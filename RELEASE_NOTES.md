# v0.5.0 — Platform experience and disposable demonstration

## Platform information architecture

- Replaced the authenticated root dashboard with a public research landing
  page, independent capability and research-plan routes, and a horizontal
  public navigation.
- Separated project-space and usage/account pages from the in-project
  scientific workspace.
- Added a project-only collapsible navigation rail grouped into data
  collection, composition science, governance and analysis.
- Added responsive project portfolio, project overview, usage and demo-state
  interfaces with accessible focus behavior and reduced-motion handling.
- Public routes render independently of authentication. Protected platform and
  workspace routes remain behind the unresolved-session gate, preventing the
  earlier project-content flash.

## Disposable Render demo

- Added `dietary-recall demo-serve`, which generates a deterministic synthetic
  legacy fixture, migrates it through schema v4 and creates a separate
  credential store from an environment secret.
- Added a two-service Render Blueprint: free Uvicorn API plus independently
  built SvelteKit static frontend with same-origin `/api` rewrites.
- Added repository protections for databases, raw exports, secrets and local
  environment files, plus a build-time safety check.
- Demo changes reset on API restart by design. The real PhD, normalized
  research and authentication databases are neither read nor committed.

## Scientific preservation

- No schema migration is required from v0.4.4.
- Legacy parity, Research Core, Validated Research, governance and analytics
  behavior remain unchanged.
- Offline interface fallback metrics are now zero rather than simulated study
  counts, avoiding presentation of invented research totals.

# v0.4.4 — Authentication UX and routing polish

## Login presentation

- Rebuilt the sign-in screen as a responsive two-panel research-platform
  experience with vertically aligned, full-width fields, explicit labels,
  consistent control heights, accessible focus states, password visibility,
  clear error feedback and a single full-width action.
- Replaced the ambiguous "platform operator" instruction. The interface now
  explains that users sign in with the account created during installation and
  identifies `auth-set-password` only for the person who configured the local
  platform.
- Added shared form-control rules so data-entry fields across project routes
  use the same spacing, sizing, alignment and disabled/focus behavior.

## Authentication routing

- Corrected the layout branch that rendered the authenticated project shell
  while session status was unresolved.
- Added a neutral blocking session gate. The dashboard, navigation and project
  content cannot render until authentication succeeds.
- Unauthenticated protected routes now remain behind the gate until the login
  redirect finishes; authenticated visits to `/login` are similarly redirected
  without exposing the sign-in screen.
- Sign-out hides protected content immediately and uses history replacement.

## Preservation

- The v0.4.3 Uvicorn/SvelteKit service split is unchanged.
- No database schema, research record, credential, subscription or scientific
  calculation behavior changes in this release.

# v0.4.3 — Separated Uvicorn API and SvelteKit services

## Service-boundary hotfix

- The Python process is now an API-only ASGI application started by Uvicorn.
  It no longer resolves routes, serves files, or applies HTML Content Security
  Policy for SvelteKit.
- The SvelteKit application runs as a dedicated Vite service with `npm run dev`
  and proxies `/api` to the backend. The proxy target is validated and can be
  changed with `DIETARY_RECALL_API_PROXY_TARGET`.
- Added `dietary-recall platform-api` as the primary backend command.
  `platform-serve` remains a temporary API-only compatibility alias; `--ui-dir`
  has been removed.
- Authentication cookies and CSRF continue through the development proxy.
  Direct cross-origin deployments must explicitly list each allowed origin.
- Added API-only, OpenAPI, cookie and CSRF boundary regression tests. No
  database schema or research-data migration is required.

## Windows SQLite lifecycle hotfix

- Repository, authentication and compatibility-workspace SQLite context
  managers now close their file handles after commit or rollback. This avoids
  `WinError 32` during temporary-database cleanup and prevents long-running
  platform processes from retaining completed request connections.
- Added commit, rollback and post-context close regression coverage.

## Delivered

- Separate credential/session SQLite store; PBKDF2-HMAC-SHA256 password hashing at 600,000 iterations, opaque revocable server-side sessions, idle/absolute expiry, login throttling and append-only authentication audit.
- Same-origin authenticated SvelteKit flow with HttpOnly/SameSite cookies, CSRF tokens, secure-cookie production mode, explicit local insecure mode and standard response security headers.
- Stripe Checkout/subscription and Paystack initialize/subscription adapters without a new Python dependency.
- Raw-body webhook signature verification, timestamp replay protection for Stripe, SHA-512 verification for Paystack, append-only provider-event ledger and idempotent entitlement updates.
- Operator-only CLI price mapping/manual grants; UI checkout can never provision its own entitlement.
- Specialist profiles with independent credential verification, review cases, append-only decisions and guarded approval effects.
- Licensed dataset packages with ZIP path safety, compressed/uncompressed limits, manifest/file SHA-256 verification, explicit permitted-use/redistribution fields, preview validation and atomic quota-aware commit.
- Reference-only metadata for the Nigeria Food Database 2019; no licensed composition rows are redistributed.
- Cohort recall-day and participant recorded-day-mean descriptives with canonical-unit conversion, immutable input snapshot hash, group-size suppression, minimum-day filtering and explicit scientific warnings.
- Laboratory median/MAD anomaly screening that records review flags without changing evidence.
- Food-match logistic triage trained only on reviewed decisions, deterministic five-fold evaluation, candidate governance and specialist approval before inference. Scores reorder review work only; automatic merging is prohibited.
- SvelteKit routes for login, licensed datasets, scientific review, billing checkout and research analytics.

## Preserved

All v0.1 legacy audit, lookup, calculations, Java-object recovery, CSV export and read-only protections remain. All v0.2 normalized data management/import/audit/version functionality remains. All v0.3 project spaces, memberships, freemium quota ledger, ontology, provenance, matching, recipes and retention remain.

## Verification

- Python suite includes the complete v0.4 feature suite and API service-boundary tests.
- SvelteKit type checking and the production build succeed; local development is served by Vite on port 5173.
- Authenticated API smoke: unauthenticated requests rejected, credential login succeeds, protected project summary succeeds, missing CSRF rejected, logout revokes the session.
- Real immutable archive: 141 foods, 863 participants and 7,755 components migrated into schema version 4; source SHA-256 unchanged at `9526c7baaf6c01d6bd7b4d85ef462ccc872a963b7cb74268826310cd6ec65359`.

## Intentionally not claimed

- TLS termination, email delivery, password recovery, MFA, secret rotation, database replication and infrastructure monitoring are deployment-operator responsibilities.
- Stripe/Paystack products and webhook endpoints must be configured in the operator's provider accounts; no live credentials are included.
- Specialist status is a workflow control, not an automated verification of professional registration.
- Licensed Nigerian data must be acquired lawfully by the deploying institution.
- Cohort outputs are unweighted descriptive statistics, not nationally representative estimates or usual-intake models. Dietary-pattern clustering and clinical prediction remain out of scope until design and validation protocols are approved.
