# v0.4 deployment guide

## 1. Files and trust boundaries

Keep these as separate protected assets:

1. the immutable historical `Nutrients.db` (read-only evidence);
2. the writable v0.4 research database;
3. the credential/session database;
4. deployment secrets supplied as environment variables;
5. the separately deployed SvelteKit application.

Do not place the credential store, provider secrets or research databases under the web root. Back up the two writable databases together and test restoration. Restrict filesystem access to the service identity.

## 2. Initialize or upgrade

```bash
dietary-recall research-init /evidence/Nutrients.db /data/Nutrients-research-v04.db
# or
dietary-recall platform-upgrade-v03 /data/Nutrients-research-v03.db /data/Nutrients-research-v04.db
```

Never reuse the source path as the target. Record both checksums from the command output.

## 3. Credentials

```bash
dietary-recall auth-init /secrets/dietary-recall-auth.db
dietary-recall auth-set-password /secrets/dietary-recall-auth.db /data/Nutrients-research-v04.db owner@example.org
dietary-recall auth-status /secrets/dietary-recall-auth.db
```

Adding a project contributor creates a research identity but not a password. An operator must provision that credential through `auth-set-password`. Changing a password revokes all earlier sessions.

## 4. Separate HTTPS services

Run the API behind an HTTPS reverse proxy that preserves the raw webhook
request body and does not rewrite provider signature headers:

```bash
dietary-recall platform-api /data/Nutrients-research-v04.db \
  --legacy-db /evidence/Nutrients.db \
  --auth-db /secrets/dietary-recall-auth.db \
  --secure-cookies \
  --allowed-origin https://dietary-recall.example.org \
  --host 127.0.0.1 --port 8765
```

Build and deploy the SvelteKit application as its own service. Route browser
`/api` requests to Uvicorn at the proxy/gateway layer. Do not expose the
research database, credential store, or backend environment file to the
frontend service.

The reverse proxy should set request-size/time limits, log request IDs without
bodies, expose only HTTPS, and perform API health checks at `/api/health`. The
production cookie uses the `__Host-` prefix, `Secure`, `HttpOnly`,
`SameSite=Strict` and `Path=/`. The backend root is intentionally JSON and must
not be used as the browser application URL.

## 5. Billing providers

Secrets are read only from process environment:

```text
DIETARY_RECALL_STRIPE_SECRET_KEY
DIETARY_RECALL_STRIPE_WEBHOOK_SECRET
DIETARY_RECALL_PAYSTACK_SECRET_KEY
DIETARY_RECALL_PAYSTACK_WEBHOOK_SECRET
```

Webhook routes:

```text
POST /api/billing/webhooks/stripe
POST /api/billing/webhooks/paystack
```

Register provider prices as an operator. The external ID is a Stripe recurring Price ID or Paystack Plan code:

```bash
dietary-recall billing-register-price research-v04.db PROJECT_UID stripe price_123 "Research Group" \
  --imports 1000 --calculations 1000 --actor owner@example.org

dietary-recall billing-register-price research-v04.db PROJECT_UID paystack PLN_123 "Nigeria Research Group" \
  --imports 1000 --calculations 1000 --amount-minor 2500000 --currency NGN --actor owner@example.org
```

Entitlements change only after a valid, mapped subscription webhook. Duplicate provider event IDs are acknowledged without reprocessing. `past_due` currently receives a grace entitlement; `paused`, `canceled` and `expired` revert to the stored fallback plan.

## 6. Licensed composition packages

A package is a ZIP with root `manifest.json` plus only the CSVs declared in `files`. Every CSV digest is calculated over its exact bytes. Use the example in `backend/templates/licensed_dataset_manifest.example.json`.

Allowed entity order is enforced:

1. `external_foods`;
2. `external_components`;
3. `retention_factors`.

The `source_release_uid` must already exist and be visible to the project. Staging stores validation results and licence acceptance but no composition rows and no quota use. An all-valid commit revalidates and writes every row in one transaction.

## 7. Scientific review

Specialists submit their profile, then another project owner/admin verifies it after checking credentials out of band. A verified specialist with owner/admin/analyst membership may decide review cases but cannot decide their own submission.

Do not publish provisional mappings or use a matching model until its review case is approved. Retain supporting citations, method documents and licence terms outside the app under the institution's records policy.

## 8. Analytics interpretation

- Use cohort runs as descriptive quality/exploration outputs.
- The minimum reportable group is five; institutional disclosure controls may require a higher threshold.
- `participant_recorded_mean` averages available recorded days after the minimum-days filter. It is not a usual-intake estimate.
- Median/MAD flags and matching triage scores require human review and never edit source values.
- Do not introduce imputation, adequacy claims, clustering or prediction until missingness, sampling, repeated-measure and validation protocols are approved.
