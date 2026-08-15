"""Command-line entry point for the migration and compatibility layer."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .audit import snapshot_audit
from .db import LegacyRepository
from .export import export_everything
from .legacy import (
    FoodPortion,
    adequacy_percent,
    calculate_foods,
    legacy_eaten_recommendation_vector,
    legacy_recommendations,
)
from .serialization import decode_with_java
from .validation import parity_summary, validate_decoded_daily
from .workspace import initialize_workspace
from .billing import BillingService
from .security import CredentialStore
from .v04_schema import initialize_v04_from_v02, initialize_v04_research_database, upgrade_v03_to_v04
from .validated_research import PlatformRepository


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _portions(values: list[str]) -> list[FoodPortion]:
    result: list[FoodPortion] = []
    for value in values:
        try:
            food_id, grams = value.split(":", 1)
            result.append(FoodPortion(int(food_id), float(grams)))
        except (ValueError, TypeError) as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid portion {value!r}; expected FOOD_ID:GRAMS"
            ) from exc
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dietary-recall",
        description="Legacy-compatible analysis plus the collaborative v0.5 Dietary Recall Research Platform",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Run the deterministic descriptive/data-quality audit")
    audit.add_argument("db")

    foods = sub.add_parser("foods", help="Search legacy foods")
    foods.add_argument("db")
    foods.add_argument("query", nargs="?", default="")
    foods.add_argument("--limit", type=int, default=50)

    people = sub.add_parser("people", help="Search legacy participants")
    people.add_argument("db")
    people.add_argument("query", nargs="?", default="")
    people.add_argument("--limit", type=int, default=50)

    calc = sub.add_parser("calculate", help="Reproduce legacy nutrient totals from food portions")
    calc.add_argument("db")
    calc.add_argument("portions", nargs="+", help="One or more FOOD_ID:GRAMS values")
    calc.add_argument("--stage-id", type=int)
    calc.add_argument("--life-id", type=int)
    calc.add_argument("--activity", default="3")

    decode = sub.add_parser("decode", help="Decode Java-serialized daily/aggregate BLOBs")
    decode.add_argument("db")
    decode.add_argument("--helper", required=True)
    decode.add_argument("--app-jar", required=True)
    decode.add_argument("--sqlite-jar", required=True)
    decode.add_argument("--output", required=True)

    export = sub.add_parser("export", help="Export raw and normalized CSV plus audit")
    export.add_argument("db")
    export.add_argument("output")
    export.add_argument("--helper")
    export.add_argument("--app-jar")
    export.add_argument("--sqlite-jar")

    workspace = sub.add_parser("init-workspace", help="Create a verified writable copy of the legacy DB")
    workspace.add_argument("source_db")
    workspace.add_argument("working_db")

    parity = sub.add_parser("validate-parity", help="Compare Python recomputation with Java daily objects")
    parity.add_argument("db")
    parity.add_argument("--helper", required=True)
    parity.add_argument("--app-jar", required=True)
    parity.add_argument("--sqlite-jar", required=True)
    parity.add_argument("--tolerance", type=float, default=1e-3)

    serve = sub.add_parser("serve", help="Run the dependency-free local research UI/API")
    serve.add_argument("db")
    serve.add_argument("--working-db")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    research_init = sub.add_parser("research-init", help="Create the latest research platform database from an immutable legacy snapshot")
    research_init.add_argument("source_db")
    research_init.add_argument("research_db")

    upgrade = sub.add_parser("platform-upgrade", help="Copy a v0.2 Research Core database and migrate the copy to v0.4")
    upgrade.add_argument("source_research_db")
    upgrade.add_argument("target_research_db")

    upgrade_v03 = sub.add_parser("platform-upgrade-v03", help="Copy a v0.3 database and migrate the copy to v0.4")
    upgrade_v03.add_argument("source_research_db")
    upgrade_v03.add_argument("target_research_db")

    auth_init = sub.add_parser("auth-init", help="Create a separate production credential store")
    auth_init.add_argument("auth_db")
    auth_set = sub.add_parser("auth-set-password", help="Set a platform user's password without storing it in shell history")
    auth_set.add_argument("auth_db")
    auth_set.add_argument("research_db")
    auth_set.add_argument("email")
    auth_status = sub.add_parser("auth-status", help="Inspect credential/session counts without revealing secrets")
    auth_status.add_argument("auth_db")

    price = sub.add_parser("billing-register-price", help="Register a provider price/plan mapping (operator action)")
    price.add_argument("research_db")
    price.add_argument("project_uid")
    price.add_argument("provider", choices=["stripe", "paystack", "manual"])
    price.add_argument("external_price_id")
    price.add_argument("display_name")
    price.add_argument("--imports", type=int, required=True)
    price.add_argument("--calculations", type=int, required=True)
    price.add_argument("--amount-minor", type=int)
    price.add_argument("--currency")
    price.add_argument("--actor", required=True)

    grant = sub.add_parser("billing-grant", help="Grant a manual subscription (operator action)")
    grant.add_argument("research_db")
    grant.add_argument("project_uid")
    grant.add_argument("price_mapping_uid")
    grant.add_argument("external_reference")
    grant.add_argument("--actor", required=True)

    def add_api_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("research_db")
        command.add_argument("--legacy-db")
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8765)
        command.add_argument("--auth-db")
        command.add_argument("--secure-cookies", action="store_true")
        command.add_argument(
            "--allow-insecure-auth",
            action="store_true",
            help="Allow non-Secure auth cookies on localhost only",
        )
        command.add_argument(
            "--allowed-origin",
            action="append",
            default=[],
            help="Explicit browser origin for direct cross-origin API access; repeat as needed",
        )
        command.add_argument("--env-file", help="Optional backend-only environment file")
        command.add_argument(
            "--log-level",
            choices=("critical", "error", "warning", "info", "debug", "trace"),
            default="info",
        )

    platform_api = sub.add_parser(
        "platform-api",
        help="Run the v0.5 API-only service with Uvicorn",
    )
    add_api_arguments(platform_api)
    platform_serve = sub.add_parser(
        "platform-serve",
        help="Deprecated alias for platform-api; no frontend files are served",
    )
    add_api_arguments(platform_serve)

    demo_serve = sub.add_parser(
        "demo-serve",
        help="Create a disposable synthetic workspace and run the API for a public demonstration",
    )
    demo_serve.add_argument("--demo-dir", default=os.environ.get("DIETARY_RECALL_DEMO_DIR", "/tmp/dietary-recall-demo"))
    demo_serve.add_argument("--email", default=os.environ.get("DIETARY_RECALL_DEMO_EMAIL", "demo@dietary-recall.local"))
    demo_serve.add_argument("--password-env", default="DIETARY_RECALL_DEMO_PASSWORD")
    demo_serve.add_argument("--keep-existing", action="store_true", help="Reuse a complete demo workspace instead of resetting it")
    demo_serve.add_argument("--host", default="0.0.0.0")
    demo_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    demo_serve.add_argument("--secure-cookies", action="store_true")
    demo_serve.add_argument("--allowed-origin", action="append", default=[])
    demo_serve.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        _json(snapshot_audit(args.db))
        return 0
    if args.command == "foods":
        _json(LegacyRepository(args.db).search_foods(args.query, args.limit))
        return 0
    if args.command == "people":
        _json(LegacyRepository(args.db).search_people(args.query, args.limit))
        return 0
    if args.command == "calculate":
        repo = LegacyRepository(args.db)
        result = calculate_foods(repo, _portions(args.portions))
        payload: dict[str, Any] = {
            "mode": "legacy_compatibility",
            "portions": [asdict(item) for item in result.portions],
            "totals": result.totals,
        }
        if args.stage_id is not None or args.life_id is not None:
            if args.stage_id is None or args.life_id is None:
                raise SystemExit("--stage-id and --life-id must be supplied together")
            activity: int | str = int(args.activity) if str(args.activity).isdigit() else args.activity
            eaten = legacy_eaten_recommendation_vector(result)
            recommended = legacy_recommendations(repo, args.stage_id, args.life_id, activity)
            payload["legacy_eaten_selection"] = eaten
            payload["legacy_recommendations"] = recommended
            payload["legacy_adequacy_percent"] = adequacy_percent(eaten, recommended)
        _json(payload)
        return 0
    if args.command == "decode":
        records = decode_with_java(args.db, args.helper, args.app_jar, args.sqlite_jar)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        _json({"records": len(records), "output": str(output)})
        return 0
    if args.command == "export":
        decoder_args = (args.helper, args.app_jar, args.sqlite_jar)
        supplied = [value is not None for value in decoder_args]
        if any(supplied) and not all(supplied):
            raise SystemExit("--helper, --app-jar and --sqlite-jar must be supplied together")
        decoded = None
        if all(supplied):
            decoded = decode_with_java(args.db, args.helper, args.app_jar, args.sqlite_jar)
        _json(export_everything(args.db, args.output, decoded_records=decoded))
        return 0
    if args.command == "init-workspace":
        _json(initialize_workspace(args.source_db, args.working_db))
        return 0
    if args.command == "validate-parity":
        decoded = decode_with_java(args.db, args.helper, args.app_jar, args.sqlite_jar)
        results = validate_decoded_daily(LegacyRepository(args.db), decoded, args.tolerance)
        _json({"summary": parity_summary(results), "records": [asdict(row) for row in results]})
        return 0
    if args.command == "serve":
        from .server import serve

        serve(args.db, host=args.host, port=args.port, working_db=args.working_db)
        return 0
    if args.command == "research-init":
        _json(initialize_v04_research_database(args.source_db, args.research_db))
        return 0
    if args.command == "platform-upgrade":
        _json(initialize_v04_from_v02(args.source_research_db, args.target_research_db))
        return 0
    if args.command == "platform-upgrade-v03":
        _json(upgrade_v03_to_v04(args.source_research_db, args.target_research_db))
        return 0
    if args.command == "auth-init":
        _json(CredentialStore.initialize(args.auth_db).status())
        return 0
    if args.command == "auth-set-password":
        repo = PlatformRepository(args.research_db)
        email = args.email.strip().casefold()
        with repo.connect() as con:
            user = con.execute("SELECT user_uid FROM app_users WHERE lower(email)=? AND active=1", (email,)).fetchone()
        if user is None:
            raise SystemExit("Active research platform user not found")
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Confirm password: ")
        if first != second:
            raise SystemExit("Passwords do not match")
        _json(CredentialStore(args.auth_db).set_password(email, user["user_uid"], first))
        return 0
    if args.command == "auth-status":
        _json(CredentialStore(args.auth_db).status())
        return 0
    if args.command == "billing-register-price":
        service = BillingService(PlatformRepository(args.research_db))
        _json(service.register_price(args.project_uid, {"provider": args.provider, "external_price_id": args.external_price_id, "display_name": args.display_name, "import_rows_limit": args.imports, "calculation_runs_limit": args.calculations, "amount_minor": args.amount_minor, "currency": args.currency}, args.actor))
        return 0
    if args.command == "billing-grant":
        _json(BillingService(PlatformRepository(args.research_db)).grant_manual(args.project_uid, args.price_mapping_uid, args.external_reference, args.actor))
        return 0
    if args.command in {"platform-api", "platform-serve"}:
        from .api_app import serve_api

        serve_api(
            args.research_db,
            legacy_db=args.legacy_db,
            host=args.host,
            port=args.port,
            auth_db=args.auth_db,
            secure_cookies=args.secure_cookies,
            allow_insecure_auth=args.allow_insecure_auth,
            allowed_origins=set(args.allowed_origin),
            env_file=args.env_file,
            log_level=args.log_level,
        )
        return 0
    if args.command == "demo-serve":
        from .api_app import serve_api
        from .demo_runtime import initialize_demo_workspace

        password = os.environ.get(args.password_env)
        if not password:
            raise SystemExit(f"Set {args.password_env} to a demonstration password of at least 12 characters")
        workspace = initialize_demo_workspace(
            args.demo_dir,
            password,
            email=args.email,
            reset=not args.keep_existing,
        )
        os.environ["DIETARY_RECALL_DEPLOYMENT_MODE"] = "ephemeral_synthetic_demo"
        _json({**workspace.to_dict(), "password": "[FROM ENVIRONMENT]"})
        serve_api(
            workspace.research_database,
            legacy_db=workspace.legacy_database,
            host=args.host,
            port=args.port,
            auth_db=workspace.credential_store,
            secure_cookies=args.secure_cookies,
            allow_insecure_auth=not args.secure_cookies and args.host in {"127.0.0.1", "localhost", "::1"},
            allowed_origins=set(args.allowed_origin),
            log_level=args.log_level,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
