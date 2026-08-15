# Render free-tier demonstration

This deployment is deliberately a **disposable demonstration**, not the PhD
research database in production. It creates synthetic records at API startup,
migrates them through the same schema used locally, and creates a separate
credential store from a Render secret.

## Data decision

Do not commit any of these local files:

- `Nutrients.db` — historical Java/SQLite evidence; required only for explicit
  legacy parity calculations and re-migration.
- `Nutrients-research-v04.db` — the current normalized research and project
  record; this is the canonical writable local platform database.
- `dietary-recall-auth.db` — credentials and sessions; required when local
  authentication is enabled.

The demo generates replacements under `/tmp/dietary-recall-demo` at runtime:

- `Nutrients-demo-synthetic.db`
- `Nutrients-research-demo.db`
- `dietary-recall-demo-auth.db`

They contain no records from the PhD database. On the default free-tier start,
they are rebuilt and all earlier demo writes are discarded.

## Before pushing

From the repository root:

```bash
python backend/scripts/verify_repository_safety.py
cd backend
PYTHONPATH=src python -m unittest discover -s tests -v
cd ../frontend
npm ci
npm run check
npm run build
```

The safety script fails if Git tracks a database, raw table export, secrets
directory or local `.env` file. The root `.gitignore` prevents these files from
being added accidentally.

## Deploy the Blueprint

1. Push this repository to a private GitHub repository.
2. In Render, choose **New → Blueprint** and select the repository.
3. Render reads `render.yaml` and proposes:
   - `dietary-recall-npf-demo-api` — free Python/Uvicorn web service.
   - `dietary-recall-npf-demo` — static SvelteKit frontend.
4. Supply `DIETARY_RECALL_DEMO_PASSWORD` when prompted. Use at least 12
   characters and do not commit it.
5. Apply the Blueprint and wait for both services to become healthy.
6. Open `https://dietary-recall-npf-demo.onrender.com`.
7. Sign in as `demo@dietary-recall.local` with the password supplied in step 4.

The static site rewrites `/api/*` to the API service, so the browser keeps a
single public origin and the secure host-only session cookie works as intended.
The catch-all rewrite sends SvelteKit routes to `index.html`.

### If Render requires different service names

The two `onrender.com` names must stay consistent. Change all three values
together before deployment:

1. API service `name`.
2. Frontend service `name` and `DIETARY_RECALL_PUBLIC_ORIGIN`.
3. Frontend `/api/*` rewrite destination.

Do not add a strict `Content-Security-Policy: default-src 'self'` header to the
static site without first supplying SvelteKit's generated bootstrap hash or a
compatible nonce. The current security headers avoid the earlier blank-page
CSP regression.

## Expected free-tier behavior

- The Python service can sleep after inactivity and has a cold-start delay.
- A restart, redeploy or sleep cycle can remove its local SQLite changes.
- This reset behavior is acceptable only because the records are synthetic.
- The static frontend remains independently deployable from the API.

## Local preview of the same demo contract

Terminal 1:

```bash
cd backend
export DIETARY_RECALL_DEMO_PASSWORD='replace-with-at-least-12-characters'
dietary-recall demo-serve \
  --host 127.0.0.1 \
  --port 8765 \
  --allowed-origin http://127.0.0.1:5173
```

Terminal 2:

```bash
cd frontend
npm run dev
```

For localhost the command automatically allows a non-Secure cookie. Public
hosting must retain `--secure-cookies`.

## Promotion path after the demo

The demo must not be converted into production by attaching the real SQLite
files to the free service. The production step is a separate migration:

1. Keep the historical SQLite snapshot in controlled evidence storage.
2. Move current platform and identity data to durable storage with tested
   backups and restore procedures.
3. Configure a persistent production authentication/identity provider.
4. Deploy the frontend and API with production domains, secrets, monitoring,
   audit retention and privacy review.
5. Ingest licensed Nigerian composition data only under its documented licence
   and specialist review workflow.
