import asyncio
import json
import tempfile
import types
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from unittest.mock import patch

from dietary_recall.api_app import API_VERSION, create_app, serve_api
from dietary_recall.research_core import initialize_research_database
from dietary_recall.security import CredentialStore
from dietary_recall.v03_schema import upgrade_platform_database
from dietary_recall.v04_schema import upgrade_v03_to_v04
from test_research_platform import make_legacy_fixture


class ApiResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.text = body.decode("utf-8")

    def json(self):
        return json.loads(self.text)


class AsgiClient:
    def __init__(self, app):
        self.app = app
        self.cookies: dict[str, str] = {}

    def request(self, method, url, payload=None, headers=None):
        return asyncio.run(self._request(method, url, payload, headers or {}))

    async def _request(self, method, url, payload, headers):
        parsed = urlsplit(url)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        request_headers = {key.casefold(): value for key, value in headers.items()}
        if payload is not None:
            request_headers.setdefault("content-type", "application/json")
        request_headers["content-length"] = str(len(body))
        if self.cookies:
            request_headers["cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("utf-8"),
            "query_string": parsed.query.encode("utf-8"),
            "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in request_headers.items()],
            "client": ("127.0.0.1", 54321),
            "server": ("testserver", 80),
        }
        received = False
        messages = []

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in start["headers"]
        }
        cookie_header = response_headers.get("set-cookie")
        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            for key, morsel in cookie.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(key, None)
                else:
                    self.cookies[key] = morsel.value
        response_body = b"".join(
            message.get("body", b"") for message in messages if message["type"] == "http.response.body"
        )
        return ApiResponse(start["status"], response_headers, response_body)

    def get(self, url, headers=None):
        return self.request("GET", url, headers=headers)

    def post(self, url, json=None, headers=None):
        return self.request("POST", url, payload=json, headers=headers)


class ApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        legacy = self.root / "legacy.db"
        v02 = self.root / "v02.db"
        v03 = self.root / "v03.db"
        self.v04 = self.root / "v04.db"
        make_legacy_fixture(legacy)
        initialize_research_database(legacy, v02)
        upgrade_platform_database(v02, v03)
        upgrade_v03_to_v04(v03, self.v04)

    def tearDown(self):
        self.tmp.cleanup()

    def test_backend_is_api_only(self):
        app = create_app(self.v04)
        self.assertFalse(app.state.frontend_serving)
        client = AsgiClient(app)
        root = client.get("/")
        self.assertEqual(200, root.status_code)
        self.assertEqual("application/json", root.headers["content-type"])
        self.assertFalse(root.json()["frontend_served"])
        self.assertNotIn("<html", root.text.casefold())

        health = client.get("/api/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual(API_VERSION, health.json()["version"])
        self.assertEqual("api_only", health.json()["mode"])

        missing = client.get("/frontend/build/index.html")
        self.assertEqual(404, missing.status_code)
        self.assertEqual("application/json", missing.headers["content-type"])
        schema = client.get("/api/openapi.json")
        self.assertEqual(200, schema.status_code)
        self.assertEqual(API_VERSION, schema.json()["info"]["version"])

    def test_backend_startup_delegates_only_to_uvicorn(self):
        calls = []
        uvicorn = types.ModuleType("uvicorn")

        def run(app, **options):
            calls.append((app, options))

        uvicorn.run = run
        with patch.dict("sys.modules", {"uvicorn": uvicorn}):
            serve_api(self.v04, host="127.0.0.1", port=8765)
        self.assertEqual(1, len(calls))
        app, options = calls[0]
        self.assertFalse(app.state.frontend_serving)
        self.assertEqual("127.0.0.1", options["host"])
        self.assertEqual(8765, options["port"])
        self.assertFalse(options["server_header"])

    def test_authenticated_cookie_and_csrf_flow_survives_vite_proxy_boundary(self):
        auth_path = self.root / "auth.db"
        auth = CredentialStore.initialize(auth_path)
        from dietary_recall.validated_research import PlatformRepository

        with PlatformRepository(self.v04).connect() as con:
            owner = con.execute("SELECT user_uid,email FROM app_users ORDER BY created_at LIMIT 1").fetchone()
        auth.set_password(owner["email"], owner["user_uid"], "Fixture-password-2026")
        app = create_app(
            self.v04,
            auth_db=auth_path,
            allow_insecure_auth=True,
            host="127.0.0.1",
        )
        client = AsgiClient(app)
        login = client.post(
            "/api/auth/login",
            json={"email": owner["email"], "password": "Fixture-password-2026"},
        )
        self.assertEqual(200, login.status_code, login.text)
        csrf = login.json()["csrf_token"]
        self.assertIn("dietary_recall_session=", login.headers["set-cookie"])
        self.assertEqual(200, client.get("/api/summary").status_code)
        self.assertEqual(401, client.post("/api/auth/logout", json={}).status_code)
        logout = client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(200, logout.status_code)
        self.assertFalse(logout.json()["authenticated"])

    def test_direct_composition_preview_scales_current_values_without_saving(self):
        auth_path = self.root / "calculator-auth.db"
        auth = CredentialStore.initialize(auth_path)
        from dietary_recall.validated_research import PlatformRepository

        with PlatformRepository(self.v04).connect() as con:
            owner = con.execute("SELECT user_uid,email FROM app_users ORDER BY created_at LIMIT 1").fetchone()
        auth.set_password(owner["email"], owner["user_uid"], "Fixture-password-2026")
        client = AsgiClient(create_app(self.v04, auth_db=auth_path, allow_insecure_auth=True, host="127.0.0.1"))
        login = client.post(
            "/api/auth/login",
            json={"email": owner["email"], "password": "Fixture-password-2026"},
        )
        csrf = login.json()["csrf_token"]
        foods = client.get("/api/foods").json()
        preview = client.post(
            "/api/composition/calculate",
            json={"items": [{"food_uid": foods[0]["food_uid"], "grams": 150}]},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual("research_core_preview", preview.json()["mode"])
        self.assertFalse(preview.json()["saved"])
        self.assertEqual("grams × stored per-100 g value ÷ 100", preview.json()["formula"])
        invalid = client.post(
            "/api/composition/calculate",
            json={"items": [{"food_uid": foods[0]["food_uid"], "grams": float("nan")}]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(400, invalid.status_code)
        malformed = client.post(
            "/api/composition/calculate",
            json={"items": {"food_uid": foods[0]["food_uid"], "grams": 100}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(400, malformed.status_code)

if __name__ == "__main__":
    unittest.main()
