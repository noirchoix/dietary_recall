# v0.5.1 verification record

Verified on 2026-09-28 (Africa/Lagos).

## Backend

```bash
cd backend
uv run --with pytest python -m pytest -q
```

Result: 47 passed and 1 optional real Java-serialized golden fixture skipped
because `DIETARY_RECALL_TEST_DB` was not configured.

Coverage includes:

- the project-scoped direct composition preview and its API boundary;
- 150 g scaling of the synthetic sample through both current and legacy paths;
- explicit missingness and rejection of zero, NaN and infinite weights;
- paid-entitlement and quota-override self-provisioning refusal;
- enforcement of the advertised per-plan active-project count;
- API-only, authentication, cookie and CSRF boundaries;
- Render rewrite order and application-shell/asset cache policy; and
- prior migration, provenance, review, analytics and safety behavior.

## Frontend

```bash
cd frontend
npm ci
npm run check
npm run build
```

Result: SvelteKit found 0 errors and 0 warnings. The adapter-static production
build completed successfully and includes the composition-calculator route.

## Repository safety

```bash
python backend/scripts/verify_repository_safety.py
```

Result: no tracked databases, raw tables, secrets or local environment files.

## Synthetic smoke result

The v0.5.1 demo fixture creates four explicitly illustrative foods, eight
synthetic participants, a schema-v4 research database and a separate PBKDF2
credential store. For synthetic food 1002, a 150 g portion calculates 10.8 g
protein and 3.15 mg iron in both the preserved legacy path and Research Core
preview. Unstored values remain missing.

## Deployment qualification

This verifies the source patch, not the still-deployed v0.5.0 Render services.
A deployment is complete only after both services are rebuilt from the same
commit and `/api/health` reports version `0.5.1`. Cold starts and synthetic
record resets remain properties of the free demonstration configuration.

The immutable real-archive result is unchanged: 141 foods, 863 participants,
7,755 component values and schema version 4, with source SHA-256 unchanged at
`9526c7baaf6c01d6bd7b4d85ef462ccc872a963b7cb74268826310cd6ec65359`.
