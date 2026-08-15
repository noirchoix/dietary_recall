"""Small dependency-free ASGI routing primitives used by the platform API.

Uvicorn owns the HTTP server. This module only maps ASGI requests to the
platform's existing service methods, keeping the backend independent of any
frontend file-serving framework.
"""

from __future__ import annotations

import inspect
import json
import re
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs


Endpoint = Callable[..., Awaitable["Response"]]
Middleware = Callable[["Request", Callable[["Request"], Awaitable["Response"]]], Awaitable["Response"]]


class Headers(dict[str, str]):
    """Case-insensitive request headers."""

    def __init__(self, values: list[tuple[bytes, bytes]]):
        super().__init__((key.decode("latin-1").casefold(), value.decode("latin-1")) for key, value in values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return super().get(key.casefold(), default)


class Request:
    def __init__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]]):
        self.scope = scope
        self._receive = receive
        self._body: bytes | None = None
        self.headers = Headers(scope.get("headers", []))
        query = parse_qs(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True)
        self.query_params = {key: values[-1] for key, values in query.items()}
        cookie = SimpleCookie()
        cookie.load(self.headers.get("cookie", "") or "")
        self.cookies = {key: morsel.value for key, morsel in cookie.items()}
        client = scope.get("client")
        self.client = SimpleNamespace(host=client[0]) if client else None

    async def body(self) -> bytes:
        if self._body is not None:
            return self._body
        chunks: list[bytes] = []
        more = True
        while more:
            message = await self._receive()
            if message.get("type") == "http.disconnect":
                break
            if message.get("type") != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more = bool(message.get("more_body", False))
        self._body = b"".join(chunks)
        return self._body


class Response:
    def __init__(
        self,
        content: str | bytes = b"",
        status_code: int = 200,
        media_type: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.body = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = status_code
        self.headers: dict[str, str] = dict(headers or {})
        if media_type:
            self.headers.setdefault("Content-Type", media_type)

    def set_cookie(
        self,
        key: str,
        value: str,
        *,
        path: str = "/",
        secure: bool = False,
        httponly: bool = False,
        samesite: str = "lax",
    ) -> None:
        parts = [f"{key}={value}", f"Path={path}", f"SameSite={samesite.capitalize()}"]
        if secure:
            parts.append("Secure")
        if httponly:
            parts.append("HttpOnly")
        self.headers["Set-Cookie"] = "; ".join(parts)

    def delete_cookie(
        self,
        key: str,
        *,
        path: str = "/",
        secure: bool = False,
        httponly: bool = False,
        samesite: str = "lax",
    ) -> None:
        self.set_cookie(key, "deleted", path=path, secure=secure, httponly=httponly, samesite=samesite)
        self.headers["Set-Cookie"] += "; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"


class ASGIApplication:
    def __init__(
        self,
        *,
        title: str,
        version: str,
        openapi_url: str = "/api/openapi.json",
        **_: Any,
    ):
        self.title = title
        self.version = version
        self.openapi_url = openapi_url
        self.state = SimpleNamespace()
        self._routes: list[tuple[str, str, Endpoint, bool]] = []
        self._middleware: Middleware | None = None
        self._allowed_origins: set[str] = set()
        self._allow_origin_pattern: re.Pattern[str] | None = None
        self._allow_credentials = False
        self._allowed_methods: list[str] = []
        self._allowed_headers: list[str] = []

    def configure_cors(
        self,
        *,
        allow_origins: list[str] | None = None,
        allow_origin_regex: str | None = None,
        allow_credentials: bool = False,
        allow_methods: list[str] | None = None,
        allow_headers: list[str] | None = None,
    ) -> None:
        self._allowed_origins = set(allow_origins or [])
        self._allow_origin_pattern = re.compile(allow_origin_regex) if allow_origin_regex else None
        self._allow_credentials = allow_credentials
        self._allowed_methods = list(allow_methods or [])
        self._allowed_headers = list(allow_headers or [])

    def middleware(self, middleware_type: str):
        if middleware_type != "http":
            raise ValueError("Only HTTP middleware is supported")

        def register(function: Middleware) -> Middleware:
            self._middleware = function
            return function

        return register

    def _route(self, method: str, path: str, include_in_schema: bool = True, **_: Any):
        def register(function: Endpoint) -> Endpoint:
            self._routes.append((method, path, function, include_in_schema))
            return function

        return register

    def get(self, path: str, **options: Any):
        return self._route("GET", path, **options)

    def post(self, path: str, **options: Any):
        return self._route("POST", path, **options)

    def put(self, path: str, **options: Any):
        return self._route("PUT", path, **options)

    def _match(self, method: str, path: str) -> tuple[Endpoint, dict[str, str]] | None:
        for route_method, route_path, endpoint, _ in self._routes:
            if route_method != method:
                continue
            if route_path == path:
                return endpoint, {}
            marker = "{api_path:path}"
            if marker in route_path:
                prefix = route_path.split(marker, 1)[0]
                if path.startswith(prefix):
                    return endpoint, {"api_path": path[len(prefix):]}
        return None

    async def _endpoint_response(self, request: Request) -> Response:
        path = request.scope.get("path", "/")
        method = request.scope.get("method", "GET").upper()
        if method == "GET" and path == self.openapi_url:
            return Response(
                json.dumps(self._openapi_document(), ensure_ascii=False),
                media_type="application/json",
            )
        matched = self._match(method, path.rstrip("/") or "/")
        if matched is None:
            return Response(
                json.dumps({"error": "Route not found", "error_type": "error"}),
                status_code=404,
                media_type="application/json",
            )
        endpoint, path_values = matched
        parameters = inspect.signature(endpoint).parameters
        if "request" in parameters:
            path_values["request"] = request
        return await endpoint(**path_values)

    def _openapi_document(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        for method, path, _, include in self._routes:
            if not include:
                continue
            display_path = path.replace("{api_path:path}", "{api_path}")
            paths.setdefault(display_path, {})[method.casefold()] = {
                "summary": "Dietary Recall Research Platform API operation",
                "responses": {"200": {"description": "Successful response"}},
            }
        return {
            "openapi": "3.1.0",
            "info": {"title": self.title, "version": self.version},
            "paths": paths,
        }

    def _origin_allowed(self, origin: str) -> bool:
        return origin in self._allowed_origins or bool(
            self._allow_origin_pattern and self._allow_origin_pattern.fullmatch(origin)
        )

    def _apply_cors(self, request: Request, response: Response) -> None:
        origin = request.headers.get("origin")
        if not origin or not self._origin_allowed(origin):
            return
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        if self._allow_credentials:
            response.headers["Access-Control-Allow-Credentials"] = "true"

    async def _send_response(self, response: Response, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        response.headers.setdefault("Content-Length", str(len(response.body)))
        headers = [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in response.headers.items()
        ]
        await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": response.body, "more_body": False})

    async def _http(self, scope: dict[str, Any], receive, send) -> None:
        request = Request(scope, receive)
        origin = request.headers.get("origin")

        async def call_next(next_request: Request) -> Response:
            if scope.get("method") == "OPTIONS" and origin and self._origin_allowed(origin):
                preflight = Response(status_code=204)
                preflight.headers["Access-Control-Allow-Methods"] = ", ".join(self._allowed_methods)
                preflight.headers["Access-Control-Allow-Headers"] = ", ".join(self._allowed_headers)
                return preflight
            return await self._endpoint_response(next_request)

        response = (
            await self._middleware(request, call_next)
            if self._middleware is not None
            else await call_next(request)
        )
        self._apply_cors(request, response)
        await self._send_response(response, send)

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        else:
            raise RuntimeError(f"Unsupported ASGI scope: {scope['type']}")
