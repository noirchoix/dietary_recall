# Dietary Recall Research Platform v0.5.0

v0.5 turns the research dashboard into a routed platform experience. Public
landing, capability and research-plan pages are separate from authenticated
project spaces. The collapsible scientific navigation appears only after a
project is opened. A deterministic synthetic runtime supports a lightweight
Render demonstration without committing the historical, research or
credential SQLite databases.

Version 0.4 completed the first deployable governance and analytics layer on top of the frozen Java-compatible migration. v0.5 preserves that backend contract and adds the platform information architecture plus a synthetic demo deployment mode.

The historical `Nutrients.db` remains immutable evidence. Every initialization and upgrade is copy-first, and the release archive intentionally excludes participant data, databases and generated raw exports.

## Lightweight demonstration

`render.yaml` defines a free Python API service and an independently deployed
SvelteKit static site. The API runs `dietary-recall demo-serve`, which creates
synthetic records and separate credentials under ephemeral storage. It never
loads the PhD research files. See
`backend/docs/RENDER_DEMO_DEPLOYMENT.md` for the data decision, exact Blueprint
steps, expected cold starts/resets and the later production promotion path.

## Capability layers

| Layer | Status | Boundary |
|---|---|---|
| `legacy_compatibility` | Preserved | Java-equivalent calculations and original quirks; read-only source |
| `research_core` | Preserved | Normalized foods, participants, experiments, recalls, import/audit/version records |
| `validated_research` | Preserved | Ontology, units, sources, candidate matches, evidence values, recipes and retention |
| `deployment_governance` | New | Credentials/sessions, CSRF, billing webhooks, specialist review and licensed packages |
| `governed_analytics` | New | Suppressed descriptives, review-only anomaly flags and approved match triage |

Project spaces remain the unit of collaboration and quota. Student projects receive 50 committed import rows and 50 calculations per month; Independent Researcher projects receive 100/100. Provider or operator-managed paid mappings must grant both limits above 100. Staging and failed transactions consume nothing.

## Quick start from the immutable legacy database

```bash
cd backend
python -m pip install -e .
dietary-recall research-init /path/to/Nutrients.db ./data/Nutrients-research-v04.db
```

Start the backend API in terminal 1:

```bash
cd backend
dietary-recall platform-api ./data/Nutrients-research-v04.db \
  --legacy-db /path/to/Nutrients.db
```

Start the SvelteKit frontend in terminal 2:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The backend at `http://127.0.0.1:8765`
is API-only; it never serves SvelteKit files. Vite proxies `/api` during local
development. Local mode keeps the explicit `X-Research-Actor` development
identity used by earlier releases. Do not expose local mode to a network.

## Enable authenticated operation

Credentials live in a separate SQLite file and passwords are requested interactively so they do not enter shell history:

```bash
dietary-recall auth-init ./secrets/dietary-recall-auth.db
dietary-recall auth-set-password \
  ./secrets/dietary-recall-auth.db \
  ./data/Nutrients-research-v04.db \
  owner@local.research
```

For a localhost-only development check:

```bash
dietary-recall platform-api ./data/Nutrients-research-v04.db \
  --legacy-db /path/to/Nutrients.db \
  --auth-db ./secrets/dietary-recall-auth.db \
  --allow-insecure-auth
```

Production must terminate HTTPS at a trusted reverse proxy and use `--secure-cookies`. See `backend/docs/DEPLOYMENT_V04.md` for proxy, environment, webhook and backup requirements.

## Upgrade without overwriting prior releases

```bash
# v0.3 → v0.4
dietary-recall platform-upgrade-v03 old-v03.db new-v04.db

# v0.2 → v0.4
dietary-recall platform-upgrade old-v02.db new-v04.db
```

The command refuses an existing target and verifies that the source checksum is unchanged.

## Scientific and analytical boundaries

- No external Nigerian composition values are bundled. The seeded Nigeria Food Database record is reference metadata only.
- A licensed ZIP must include a checksummed manifest, explicit licence acceptance and declared CSVs. Every row is revalidated inside the final transaction.
- Ontology terms, mappings, validated values, retention factors, source releases and matching models remain provisional until an independently verified specialist decides a review case.
- Cohort output suppresses groups below the configured threshold (minimum 5). Participant recorded-day means are explicitly not usual-intake estimates.
- Median/MAD flags never modify or reject laboratory results.
- The logistic matching model trains only from reviewed food-match decisions, records deterministic five-fold metrics and only prioritizes candidates after specialist approval. It never merges foods or predicts nutrient chemistry.
- This is research software, not personalized clinical, diagnostic or diet advice.

## Verification snapshot

- Python: the full suite plus API-only boundary, cookie and CSRF integration tests pass; one optional real Java-serialized golden case is skipped when `DIETARY_RECALL_TEST_DB` is not configured.
- SvelteKit: `npm run check` completes with zero errors and 120 accessibility warnings in compact markup; the static production build succeeds.
- Real archive migration: 141 foods, 863 participants and 7,755 component records; schema version 4.
- Immutable source SHA-256 before and after migration: `9526c7baaf6c01d6bd7b4d85ef462ccc872a963b7cb74268826310cd6ec65359`.
