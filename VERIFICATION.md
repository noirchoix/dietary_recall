# v0.5.0 verification record

Verified on 2026-08-14 (Africa/Lagos).

## Python

```bash
PYTHONPATH=backend/src python -m unittest discover -s backend/tests -v
```

Result: 42 tests ran, 41 passed and 1 optional real Java-serialized golden
fixture was skipped because `DIETARY_RECALL_TEST_DB` was not configured.

New coverage verifies:

- public, platform and project-workspace routing boundaries;
- grouped and collapsible project navigation;
- deterministic synthetic demo bootstrap;
- separation of legacy, research and credential demo databases;
- removal of earlier demo mutations on reset;
- refusal of an incomplete keep-existing workspace;
- Render frontend/backend service separation; and
- repository database/secret exclusion rules.

## SvelteKit

```bash
cd frontend
npm run check
npm run build
```

Result: zero type/compiler errors and a successful adapter-static production
build. There are 104 inherited accessibility warnings in older compact
scientific form routes; the new public, project-space and workspace-shell files
introduce no compiler errors. The warnings remain scheduled for route-by-route
form modernization.

## Repository safety

```bash
python backend/scripts/verify_repository_safety.py
```

Result: no database, raw-table, secrets or local environment file is included
in the deployable source tree.

## Synthetic demo contract

The bootstrap test creates:

- four explicitly illustrative food records;
- eight synthetic participants;
- a schema-v4 Research Platform database;
- `demo@dietary-recall.local` as the demo account; and
- an independent PBKDF2 credential store with no plaintext password.

The test then writes a marker to the research database, starts the demo again,
and verifies that the marker is absent. This confirms the intended free-tier
reset behavior.

## Preserved real-archive result

No v0.5 database schema migration is introduced. The earlier immutable archive
verification remains: 141 foods, 863 participants, 7,755 component values and
schema version 4, with source SHA-256 unchanged at
`9526c7baaf6c01d6bd7b4d85ef462ccc872a963b7cb74268826310cd6ec65359`.
