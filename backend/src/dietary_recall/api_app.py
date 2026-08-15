"""ASGI application for the API-only Dietary Recall Research Platform service."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .analytics import AnalyticsService, MatchingModelService
from .asgi_runtime import ASGIApplication, Request, Response
from .billing import BillingError, BillingService
from .db import LegacyRepository
from .governance import GovernanceService
from .imports import commit_import, csv_template, stage_import_base64
from .legacy import FoodPortion, calculate_foods
from .licensed_ingestion import LicensedDatasetService
from .research_core import _uid
from .security import AuthError, CredentialStore
from .validated_research import PlatformRepository, QuotaExceededError


API_VERSION = "0.5.0"
MAX_REQUEST_BYTES = 36 * 1024 * 1024
LOCAL_ORIGIN_REGEX = r"^http://(?:127\.0\.0\.1|localhost)(?::\d+)?$"


def _json_response(value: Any, status_code: int = 200, headers: Mapping[str, str] | None = None) -> Response:
    return Response(
        content=json.dumps(value, ensure_ascii=False, default=str),
        status_code=status_code,
        media_type="application/json",
        headers=dict(headers or {}),
    )


def _error_response(status_code: int, exc: Exception | str) -> Response:
    return _json_response(
        {
            "error": str(exc),
            "error_type": type(exc).__name__ if isinstance(exc, Exception) else "error",
        },
        status_code,
    )


async def _json_object(request: Request) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_REQUEST_BYTES:
                raise ValueError("Request exceeds the 36 MiB API limit")
        except ValueError as exc:
            if str(exc).startswith("Request exceeds"):
                raise
            raise ValueError("Invalid Content-Length header") from exc
    raw = await request.body()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Request exceeds the 36 MiB API limit")
    try:
        value = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must contain valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON request body must be an object")
    return value


def _remote_address(request: Request) -> str | None:
    return request.client.host if request.client else None


def create_app(
    research_db: str | Path,
    *,
    legacy_db: str | Path | None = None,
    auth_db: str | Path | None = None,
    secure_cookies: bool = False,
    allow_insecure_auth: bool = False,
    host: str = "127.0.0.1",
    allowed_origins: set[str] | None = None,
) -> ASGIApplication:
    """Create an API-only ASGI application.

    The application deliberately has no frontend directory or static-file
    mount. SvelteKit is a separate service and proxies ``/api`` in development.
    """
    if auth_db and not secure_cookies and not (
        allow_insecure_auth and host in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError(
            "Authentication requires secure cookies behind HTTPS; "
            "use --allow-insecure-auth only for localhost development"
        )

    repository = PlatformRepository(research_db)
    legacy_repository = LegacyRepository(legacy_db) if legacy_db else None
    credential_store = CredentialStore(auth_db) if auth_db else None
    billing = BillingService(repository)
    governance = GovernanceService(repository)
    datasets = LicensedDatasetService(repository)
    analytics = AnalyticsService(repository)
    matching_models = MatchingModelService(repository)
    configured_origins = sorted(set(allowed_origins or set()))
    deployment_mode = os.environ.get("DIETARY_RECALL_DEPLOYMENT_MODE", "standard").strip() or "standard"

    app = ASGIApplication(
        title="Dietary Recall Research Platform API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.frontend_serving = False
    app.state.research_database = str(Path(research_db).resolve())
    app.state.authentication_required = credential_store is not None
    app.state.deployment_mode = deployment_mode

    if configured_origins:
        app.configure_cors(
            allow_origins=configured_origins,
            allow_credentials=credential_store is not None,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "X-Research-Actor", "X-Project-UID", "X-CSRF-Token"],
        )
    elif credential_store is None:
        app.configure_cors(
            allow_origin_regex=LOCAL_ORIGIN_REGEX,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "X-Research-Actor", "X-Project-UID", "X-CSRF-Token"],
        )

    cookie_name = "__Host-dietary_recall_session" if secure_cookies else "dietary_recall_session"

    def session_token(request: Request) -> str | None:
        return request.cookies.get(cookie_name)

    def session(request: Request):
        if credential_store is None:
            return None
        return credential_store.authenticate(
            session_token(request), user_agent=request.headers.get("user-agent")
        )

    def actor(request: Request) -> str:
        if credential_store is not None:
            return session(request).email
        return request.headers.get("X-Research-Actor", "local-researcher")[:200]

    def project(request: Request) -> str:
        return request.headers.get("X-Project-UID", repository.default_project_uid())[:200]

    def require_csrf(request: Request) -> None:
        if credential_store is not None:
            credential_store.verify_csrf(session(request), request.headers.get("X-CSRF-Token"))

    @app.middleware("http")
    async def api_boundary(request: Request, call_next):
        try:
            response = await call_next(request)
        except KeyError as exc:
            response = _error_response(404, exc)
        except QuotaExceededError as exc:
            response = _error_response(429, exc)
        except (ValueError, BillingError, json.JSONDecodeError) as exc:
            response = _error_response(400, exc)
        except AuthError as exc:
            response = _error_response(401, exc)
        except PermissionError as exc:
            response = _error_response(403, exc)
        except sqlite3.IntegrityError as exc:
            response = _error_response(409, exc)
        except Exception as exc:  # Keep the v0.4 JSON error contract.
            response = _error_response(500, exc)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/", include_in_schema=False)
    async def api_root() -> Response:
        return _json_response(
            {
                "service": "dietary-recall-research-platform-api",
                "version": API_VERSION,
                "frontend_served": False,
                "health": "/api/health",
                "openapi": "/api/openapi.json",
            }
        )

    @app.get("/api/health", tags=["system"])
    async def health() -> Response:
        return _json_response(
            {
                "ok": True,
                "service": "dietary-recall-research-platform-api",
                "version": API_VERSION,
                "mode": "api_only",
                "deployment_mode": deployment_mode,
                "frontend_served": False,
            }
        )

    @app.get("/api/config", tags=["system"])
    async def config() -> Response:
        return _json_response(
            {
                "version": API_VERSION,
                "authentication_required": credential_store is not None,
                "service_mode": "api_only",
                "deployment_mode": deployment_mode,
                "ephemeral_demo": deployment_mode == "ephemeral_synthetic_demo",
                "demo_email": (
                    os.environ.get("DIETARY_RECALL_DEMO_EMAIL", "demo@dietary-recall.local")
                    if deployment_mode == "ephemeral_synthetic_demo"
                    else None
                ),
                "billing_providers": {
                    "stripe": bool(os.environ.get("DIETARY_RECALL_STRIPE_SECRET_KEY")),
                    "paystack": bool(os.environ.get("DIETARY_RECALL_PAYSTACK_SECRET_KEY")),
                },
            }
        )

    @app.get("/api/{api_path:path}", tags=["platform"])
    async def get_api(api_path: str, request: Request) -> Response:
        path = f"/api/{api_path}".rstrip("/")
        parts = [part for part in path.split("/") if part]
        query = request.query_params
        if path == "/api/auth/status":
            if credential_store is None:
                return _json_response(
                    {
                        "authenticated": True,
                        "development_mode": True,
                        "actor": actor(request),
                        "csrf_token": None,
                        "deployment_mode": deployment_mode,
                    }
                )
            try:
                current = session(request)
                return _json_response(
                    {
                        "authenticated": True,
                        "development_mode": False,
                        "actor": current.email,
                        "csrf_token": current.csrf_token,
                        "deployment_mode": deployment_mode,
                        "expires_at": current.expires_at,
                    }
                )
            except AuthError:
                return _json_response(
                    {
                        "authenticated": False,
                        "development_mode": False,
                        "deployment_mode": deployment_mode,
                    },
                    401,
                )
        if path == "/api/projects":
            return _json_response(repository.list_projects(actor(request)))
        if path == "/api/project":
            return _json_response(repository.project_context(project(request), actor(request)))
        if path == "/api/summary":
            return _json_response(repository.project_summary(project(request), actor(request)))
        if path == "/api/layers":
            return _json_response(
                [
                    {"id": "legacy_compatibility", "status": "available", "writable": False, "description": "Recoverable Java-compatible calculation behavior"},
                    {"id": "research_core", "status": "active", "writable": True, "description": "Versioned normalized research records"},
                    {"id": "validated_research", "status": "active", "writable": True, "description": "Canonical units, explicit provenance, reviewed matches, recipes and retention"},
                    {"id": "governed_analytics", "status": "active", "writable": True, "description": "Suppressed cohort descriptives, review-only QC flags and specialist-approved match triage"},
                ]
            )
        if path == "/api/foods":
            return _json_response(repository.list_project_foods(project(request), actor(request), query.get("q", ""), int(query.get("limit", "100"))))
        if path == "/api/foods/next-id":
            actor(request)
            return _json_response({"next_legacy_food_id": repository.next_legacy_food_id()})
        if len(parts) == 3 and parts[:2] == ["api", "foods"]:
            return _json_response(repository.get_project_food(project(request), parts[2], actor(request)))
        if path == "/api/participants":
            return _json_response(repository.list_project_participants(project(request), actor(request), query.get("q", ""), int(query.get("limit", "100"))))
        if path in {"/api/users", "/api/members"}:
            return _json_response(repository.list_members(project(request), actor(request)))
        if path == "/api/experiments":
            return _json_response(repository.list_project_experiments(project(request), actor(request), query.get("q", ""), int(query.get("limit", "100"))))
        if len(parts) == 3 and parts[:2] == ["api", "experiments"]:
            project_uid, current_actor = project(request), actor(request)
            with repository.connect() as con:
                repository._membership(con, project_uid, current_actor)
                repository._assert_record(con, project_uid, "experiment", parts[2])
            return _json_response(repository.get_experiment(parts[2]))
        if path == "/api/recalls":
            return _json_response(repository.list_project_recalls(project(request), actor(request), int(query.get("limit", "100"))))
        if len(parts) == 3 and parts[:2] == ["api", "recalls"]:
            project_uid, current_actor = project(request), actor(request)
            with repository.connect() as con:
                repository._membership(con, project_uid, current_actor)
                repository._assert_record(con, project_uid, "recall", parts[2])
            return _json_response(repository.get_recall(parts[2]))
        if path == "/api/nutrients":
            actor(request)
            return _json_response(repository.list_nutrients())
        if path == "/api/ontology":
            actor(request)
            return _json_response(repository.list_canonical_nutrients(query.get("q", "")))
        if path == "/api/units":
            actor(request)
            return _json_response(repository.list_units())
        if path == "/api/sources":
            return _json_response(repository.list_sources(project(request), actor(request)))
        if path == "/api/source-releases":
            return _json_response(repository.list_source_releases(project(request), actor(request)))
        if path == "/api/external-foods":
            return _json_response(repository.list_external_foods(project(request), actor(request), query.get("q", ""), int(query.get("limit", "200"))))
        if path == "/api/matches":
            return _json_response(repository.list_food_matches(project(request), actor(request), query.get("status", "candidate")))
        if path == "/api/retention-factors":
            return _json_response(repository.list_retention_factors(project(request), actor(request)))
        if path == "/api/recipes":
            return _json_response(repository.list_recipes(project(request), actor(request)))
        if len(parts) == 3 and parts[:2] == ["api", "recipes"]:
            return _json_response(repository.get_recipe(project(request), parts[2], actor(request)))
        if len(parts) == 3 and parts[:2] == ["api", "calculations"]:
            return _json_response(repository.get_calculation(project(request), parts[2], actor(request)))
        if path == "/api/usage":
            return _json_response(repository.usage_summary(project(request), actor(request)))
        if path == "/api/audit":
            return _json_response(repository.list_project_audit(project(request), actor(request), int(query.get("limit", "200"))))
        if path == "/api/imports":
            return _json_response(repository.list_project_imports(project(request), actor(request), int(query.get("limit", "100"))))
        if path == "/api/billing":
            return _json_response(billing.billing_summary(project(request), actor(request)))
        if path == "/api/billing/prices":
            return _json_response(billing.list_prices(actor(request), project(request)))
        if path == "/api/specialists":
            return _json_response(governance.list_specialists(project(request), actor(request)))
        if path == "/api/reviews":
            return _json_response(governance.list_cases(project(request), actor(request), query.get("status", "pending")))
        if path == "/api/datasets":
            return _json_response(datasets.list_packages(project(request), actor(request)))
        if path == "/api/licenses":
            return _json_response(datasets.list_licenses(project(request), actor(request)))
        if len(parts) == 3 and parts[:2] == ["api", "datasets"]:
            return _json_response(datasets.get_package(project(request), parts[2], actor(request)))
        if path == "/api/analytics/cohort":
            return _json_response(analytics.list_cohort_runs(project(request), actor(request)))
        if len(parts) == 4 and parts[:3] == ["api", "analytics", "cohort"]:
            return _json_response(analytics.get_cohort_run(project(request), parts[3], actor(request)))
        if path == "/api/analytics/anomalies":
            return _json_response(analytics.list_anomaly_runs(project(request), actor(request)))
        if len(parts) == 4 and parts[:3] == ["api", "analytics", "anomalies"]:
            return _json_response(analytics.get_anomaly_run(project(request), parts[3], actor(request)))
        if path == "/api/analytics/matching-models":
            return _json_response(matching_models.list_models(project(request), actor(request)))
        if len(parts) == 4 and parts[:3] == ["api", "templates", "csv"]:
            actor(request)
            entity = parts[3]
            return Response(
                content=csv_template(entity),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{entity}_import_template.csv"'},
            )
        return _error_response(404, "API route not found")

    @app.post("/api/{api_path:path}", tags=["platform"])
    async def post_api(api_path: str, request: Request) -> Response:
        path = f"/api/{api_path}".rstrip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["api", "billing", "webhooks"]:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Request exceeds the 36 MiB API limit")
            provider = parts[3]
            secret = (
                os.environ.get("DIETARY_RECALL_STRIPE_WEBHOOK_SECRET")
                if provider == "stripe"
                else os.environ.get("DIETARY_RECALL_PAYSTACK_WEBHOOK_SECRET")
                if provider == "paystack"
                else None
            )
            if not secret:
                raise BillingError("Webhook provider is not configured")
            signature = request.headers.get("Stripe-Signature") if provider == "stripe" else request.headers.get("X-Paystack-Signature")
            return _json_response(billing.process_webhook(provider, raw, signature or "", secret))

        body = await _json_object(request)
        if path == "/api/auth/login":
            if credential_store is None:
                raise ValueError("Production authentication is not enabled")
            current = credential_store.login(
                str(body.get("email") or ""),
                str(body.get("password") or ""),
                remote_address=_remote_address(request),
                user_agent=request.headers.get("user-agent"),
            )
            response = _json_response(
                {
                    "authenticated": True,
                    "actor": current.email,
                    "csrf_token": current.csrf_token,
                    "expires_at": current.expires_at,
                }
            )
            response.set_cookie(
                cookie_name,
                current.session_token or "",
                path="/",
                secure=secure_cookies,
                httponly=True,
                samesite="strict",
            )
            return response
        if path == "/api/auth/logout":
            require_csrf(request)
            if credential_store is not None:
                credential_store.logout(
                    session_token(request),
                    remote_address=_remote_address(request),
                    user_agent=request.headers.get("user-agent"),
                )
            response = _json_response({"authenticated": False})
            response.delete_cookie(cookie_name, path="/", secure=secure_cookies, httponly=True, samesite="strict")
            return response

        current_actor, project_uid = actor(request), project(request)
        require_csrf(request)
        if path == "/api/projects":
            return _json_response(repository.create_project(body, current_actor), 201)
        if path == "/api/foods":
            return _json_response(repository.create_project_food(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "foods"] and parts[3] == "components":
            repository.get_project_food(project_uid, parts[2], current_actor)
            return _json_response(repository.upsert_component(parts[2], body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "foods"] and parts[3] == "validated-components":
            return _json_response(repository.create_validated_component(project_uid, parts[2], body, current_actor), 201)
        if path == "/api/participants":
            return _json_response(repository.create_project_participant(project_uid, body, current_actor), 201)
        if path in {"/api/users", "/api/members"}:
            return _json_response(repository.add_member(project_uid, body, current_actor), 201)
        if path == "/api/experiments":
            return _json_response(repository.create_project_experiment(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "experiments"] and parts[3] == "results":
            with repository.connect() as con:
                repository._membership(con, project_uid, current_actor, {"owner", "admin", "contributor"})
                repository._assert_record(con, project_uid, "experiment", parts[2])
            return _json_response(repository.add_experiment_result(parts[2], body, current_actor), 201)
        if path == "/api/recalls":
            return _json_response(repository.create_project_recall(project_uid, body, current_actor), 201)
        if path == "/api/imports/stage":
            return _json_response(stage_import_base64(repository, body, current_actor, project_uid), 201)
        if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "commit":
            return _json_response(commit_import(repository, parts[2], current_actor))
        if path == "/api/units/convert":
            return _json_response(repository.convert_unit(float(body.get("value")), str(body.get("from_unit")), str(body.get("to_unit"))))
        if path == "/api/sources":
            return _json_response(repository.create_source(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "sources"] and parts[3] == "releases":
            return _json_response(repository.add_source_release(project_uid, parts[2], body, current_actor), 201)
        if path == "/api/external-foods":
            return _json_response(repository.create_external_food(project_uid, body, current_actor), 201)
        if path == "/api/matches/generate":
            return _json_response(repository.generate_food_matches(project_uid, body.get("research_food_uid"), current_actor, float(body.get("minimum_score", 0.35))))
        if len(parts) == 4 and parts[:2] == ["api", "matches"] and parts[3] == "review":
            return _json_response(repository.review_food_match(project_uid, parts[2], str(body.get("decision")), body.get("notes"), current_actor))
        if path == "/api/retention-factors":
            return _json_response(repository.create_retention_factor(project_uid, body, current_actor), 201)
        if path == "/api/recipes":
            return _json_response(repository.create_recipe(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "recipes"] and parts[3] == "calculate":
            return _json_response(repository.calculate_recipe(project_uid, parts[2], str(body.get("policy") or "strict"), current_actor), 201)
        if path == "/api/billing/checkout":
            return _json_response(
                billing.create_checkout(
                    project_uid,
                    body,
                    current_actor,
                    stripe_api_key=os.environ.get("DIETARY_RECALL_STRIPE_SECRET_KEY"),
                    paystack_secret_key=os.environ.get("DIETARY_RECALL_PAYSTACK_SECRET_KEY"),
                ),
                201,
            )
        if path == "/api/specialists":
            return _json_response(governance.register_specialist(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "specialists"] and parts[3] == "verify":
            return _json_response(governance.verify_specialist(project_uid, parts[2], body, current_actor))
        if path == "/api/reviews":
            return _json_response(governance.submit_case(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "reviews"] and parts[3] == "decide":
            return _json_response(governance.decide_case(project_uid, parts[2], body, current_actor))
        if path == "/api/datasets/stage":
            return _json_response(datasets.stage_base64(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "datasets"] and parts[3] == "commit":
            return _json_response(datasets.commit(project_uid, parts[2], current_actor))
        if path == "/api/analytics/cohort":
            return _json_response(analytics.run_cohort(project_uid, body, current_actor), 201)
        if path == "/api/analytics/anomalies":
            return _json_response(analytics.run_anomalies(project_uid, body, current_actor), 201)
        if len(parts) == 4 and parts[:2] == ["api", "anomaly-flags"] and parts[3] == "review":
            return _json_response(analytics.review_anomaly(project_uid, parts[2], body, current_actor))
        if path == "/api/analytics/matching-models/train":
            return _json_response(matching_models.train(project_uid, current_actor), 201)
        if len(parts) == 5 and parts[:3] == ["api", "analytics", "matching-models"] and parts[4] == "apply":
            return _json_response(matching_models.apply(project_uid, parts[3], current_actor))
        if path == "/api/legacy/calculate":
            if legacy_repository is None:
                raise ValueError("Legacy database was not configured for this server")
            portions = [FoodPortion(int(item["food_id"]), float(item["grams"])) for item in body.get("foods", [])]
            result = calculate_foods(legacy_repository, portions)
            with repository.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                repository._membership(con, project_uid, current_actor, {"owner", "admin", "contributor", "analyst"})
                repository.consume_usage_in_transaction(
                    con,
                    project_uid,
                    "calculation_runs",
                    1,
                    current_actor,
                    "legacy_calculation",
                    _uid("legacycalc"),
                    {"portion_count": len(portions)},
                )
                con.commit()
            return _json_response({"mode": "legacy_compatibility", "portions": [asdict(item) for item in result.portions], "totals": result.totals})
        return _error_response(404, "API route not found")

    @app.put("/api/{api_path:path}", tags=["platform"])
    async def put_api(api_path: str, request: Request) -> Response:
        path = f"/api/{api_path}".rstrip("/")
        parts = [part for part in path.split("/") if part]
        body = await _json_object(request)
        current_actor, project_uid = actor(request), project(request)
        require_csrf(request)
        if len(parts) == 3 and parts[:2] == ["api", "foods"]:
            repository.get_project_food(project_uid, parts[2], current_actor)
            return _json_response(repository.update_food(parts[2], body, current_actor))
        if len(parts) == 3 and parts[:2] == ["api", "participants"]:
            with repository.connect() as con:
                repository._membership(con, project_uid, current_actor, {"owner", "admin", "contributor"})
                repository._assert_record(con, project_uid, "participant", parts[2])
            return _json_response(repository.update_participant(parts[2], body, current_actor))
        if len(parts) == 3 and parts[:2] in (["api", "users"], ["api", "members"]):
            return _json_response(repository.update_membership(project_uid, parts[2], body, current_actor))
        if len(parts) == 3 and parts[:2] == ["api", "experiments"]:
            with repository.connect() as con:
                repository._membership(con, project_uid, current_actor, {"owner", "admin", "contributor"})
                repository._assert_record(con, project_uid, "experiment", parts[2])
            return _json_response(repository.update_experiment(parts[2], body, current_actor))
        return _error_response(404, "API route not found")

    return app


def serve_api(
    research_db: str | Path,
    *,
    legacy_db: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    auth_db: str | Path | None = None,
    secure_cookies: bool = False,
    allow_insecure_auth: bool = False,
    allowed_origins: set[str] | None = None,
    env_file: str | Path | None = None,
    log_level: str = "info",
) -> None:
    """Run the API application with Uvicorn. The frontend is never started here."""
    import uvicorn

    app = create_app(
        research_db,
        legacy_db=legacy_db,
        auth_db=auth_db,
        secure_cookies=secure_cookies,
        allow_insecure_auth=allow_insecure_auth,
        host=host,
        allowed_origins=allowed_origins,
    )
    print(f"Dietary Recall Research Platform API: http://{host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        env_file=str(env_file) if env_file else None,
        log_level=log_level,
        server_header=False,
    )
