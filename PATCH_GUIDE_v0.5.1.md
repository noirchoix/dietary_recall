# Applying and deploying the v0.5.1 demo patch

## Apply to commit 745e82c

```bash
git switch --detach 745e82c
git apply --check dietary-recall-v0.5.1.patch
git apply dietary-recall-v0.5.1.patch
```

Use a normal branch instead of a detached checkout when integrating the patch
into the maintained repository. The patch changes application code and demo
seed behavior but introduces no database schema migration.

## Verify locally

```bash
cd backend
PYTHONPATH=src uvx pytest -q
cd ../frontend
npm ci
npm run check
npm run build
cd ..
python backend/scripts/verify_repository_safety.py
```

Expected result: 47 backend tests pass, one optional real-archive golden test
is skipped unless `DIETARY_RECALL_TEST_DB` is configured, SvelteKit reports
zero errors and zero warnings, the static build succeeds and the repository
safety check passes.

## Deploy the Render demo

1. Commit the patched files and push the same commit to both Render services.
2. Trigger a clear-cache rebuild for the API and the static frontend.
3. Wait for `GET /api/health` to report version `0.5.1`.
4. Sign in and run the 150 g synthetic food 1002 check in **Composition
   calculator**. It should return 10.8 g protein and 3.15 mg iron.
5. Confirm the result labels missing components and says the preview is not
   saved.

The current free demo remains ephemeral. A service restart recreates the
synthetic workspace; no real research database is bundled or loaded.

## Roll back

Redeploy commit `745e82c` to both services. Because v0.5.1 has no schema
migration and the demo storage is disposable, rollback does not require a
database down-migration.
