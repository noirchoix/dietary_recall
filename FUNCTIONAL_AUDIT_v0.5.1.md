# Dietary Recall demo functional audit and remediation

**Audit date:** 2026-09-28 (Africa/Lagos)  
**Baseline:** commit `745e82c`, public demo release v0.5.0  
**Patched candidate:** v0.5.1  
**Inputs reviewed:** the deployable `dietary_recall-main` archive, the current
platform archive and its v0.3–v0.5 history/backups.

## Executive conclusion

The repository contains a substantial research platform, but the v0.5 demo
surface displaced its simplest original purpose: select a food, enter grams
and obtain scaled composition. The calculation code was present, yet the
public route emphasized platform breadth, the only modern workflow saved a
participant recall, and the preserved legacy endpoint failed in the synthetic
demo because its tables omitted a queried column.

The patch restores the direct calculation path and corrects the clearest
misrepresentations. It is a deployable demo candidate, not a declaration that
the product is clinically validated or operationally production-ready.

Using a transparent score of **1 for full, 0.5 for partial and 0 for absent**
across the 14 original capabilities below, the baseline satisfied 10/14
weighted capability points (71%). The patch raises this to 11/14 (79%) by
repairing legacy demo parity and exposing direct calculation. The remaining
three weighted points are mainly averages, adequacy/reference-intake output,
exports and charts—not cosmetic UI work.

## Original goal and recurring functionality

The original goal was to preserve the legacy research evidence, reproduce its
Java calculation behavior in Python, and add a separate normalized and
validated research layer. The core arithmetic is intentionally simple:

`calculated amount = grams consumed × stored value per 100 g ÷ 100`

It does not infer chemistry from a food name. Missing vitamins or sparse
macronutrients must remain missing, not become zero. The wider workflow covers
participants, daily recalls, nutrient and cadmium/lead totals, participant and
group summaries, reference-intake comparison, visual summaries and export.

| # | Original capability | Baseline v0.5.0 | Patched v0.5.1 | Evidence / boundary |
|---:|---|---|---|---|
| 1 | Immutable legacy source and audit trail | Full | Full | Copy-first migration, hashes, audit/version records |
| 2 | Legacy Java-compatible calculation | Partial | Full | Code existed, but demo returned 500; fixture schema repaired and parity tested |
| 3 | Food + arbitrary grams calculation | Partial | Full | Previously recall-only/no direct UI; now a non-saving multi-food preview |
| 4 | Participant management | Full | Full | Project-scoped participant routes and synthetic participants |
| 5 | Daily recall capture | Full | Full | Recall entry, item weights and stored results |
| 6 | Nutrient and toxicant totals | Full | Full | Component groups include nutrients and toxicants; depends on stored values |
| 7 | Day and participant averages | Partial | Partial | Recorded-day means exist in analytics; not a complete usual-intake workflow |
| 8 | Group/cohort averages | Partial | Partial | Governed descriptives exist and suppress small groups; data-dependent |
| 9 | Reference-intake/adequacy comparison | Partial | Partial | Preserved in legacy/Python logic, not exposed as a complete current UI/API flow |
| 10 | Charts and visual summaries | Absent | Absent | No dedicated chart workflow in the demo |
| 11 | CSV/Excel export | Partial | Partial | CSV/normalized export exists outside the primary UI; no complete Excel/UI flow |
| 12 | Missingness and provenance | Full | Full | NULL is distinct from zero; units, basis, source, version and snapshots retained |
| 13 | Foods and laboratory experiments | Full | Full | Project-scoped records and results with provenance |
| 14 | Normalized imports and versioning | Full | Full | Staged validation, audit history and licensed-package controls |

## What the v0.5 interface silenced or replaced

| Original user need | What occupied the foreground | Effect |
|---|---|---|
| Calculate a known food at N grams | Public platform landing, capability taxonomy and plan cards | The most useful action was not directly available |
| Inspect calculation assumptions | Governance and scientific-layer jargon | The simple per-100 g formula was technically present but hard to find |
| Understand available data | Broad checkmarked capability claims | Setup-dependent and data-dependent functions appeared immediately usable |
| Use the research archive | Synthetic platform showcase | The demo could imply comprehensive composition where values were illustrative |
| Review evidence independently | Single-account review UI with decision controls | The UI implied completion although self-verification and self-review are prohibited |
| Collaborate with contributors | Membership records | Adding a member did not create credentials, which the interface did not explain |

The overarching UI did not delete the core backend; it reduced discoverability
and overstated readiness. That distinction matters because the correct remedy
is to restore the workflow and label dependencies, not discard the normalized,
provenance and governance work.

## Specific discrepancies and their disposition

| Finding | Impact | v0.5.1 disposition |
|---|---|---|
| Synthetic legacy tables omitted `Food_Name`, while the legacy query required it | `/api/legacy/calculate` returned 500 | Fixed and regression-tested |
| No direct composition calculator | Original task required a participant recall and created a record | Added non-saving, project-scoped calculator |
| Landing sample numbers disagreed with seeded values; text implied no participants despite eight synthetic records | Demonstrably false product evidence | Removed hard-coded metrics and clarified synthetic records |
| Dense generated component values looked comprehensive | Could be mistaken for measured/reference composition | Replaced with sparse, explicitly illustrative values |
| Successful HTML cold-start/proxy responses were parsed as JSON | Raw syntax errors and ambiguous internal failures | Added response validation, bounded retry state and a clear service message |
| Static application shell had no explicit cache boundary | Old shell and new API/assets could be mixed after deploy | Added no-cache shell and immutable fingerprinted-asset headers |
| Generic 500s returned exception text | Leaked internals while providing poor guidance | Log server-side; return a stable generic JSON error |
| Non-finite or zero weights could enter calculation paths | Invalid science output or database values | Reject NaN, infinity and non-positive gram weights |
| Component units could drift from their definition | Invalid aggregation of unlike units | Enforce unit consistency on write and recomputation |
| Capability page used universal checkmarks | Hid data and setup requirements | Added Available, Data-dependent and Setup-required states |
| Pricing acted like a live storefront | Implied purchasable/configured billing | Reframed as capacity model; removed fake start actions |
| Authenticated users could self-create `paid` projects and quota overrides | Bypassed the documented billing/operator trust boundary | Self-provisioning rejected; only verified billing/operator paths may grant it |
| Advertised per-plan project count was not enforced | Project portfolios could exceed the stated capacity | Owner project count is checked before creation |
| Review buttons appeared usable to the one demo account | Contradicted independent specialist rules | Added explicit limit and disabled decisions without a verified independent actor |
| Contributor membership looked like account provisioning | Users could expect the new member to sign in | Added an authentication-provisioning boundary notice |
| Legacy layer always reported `available` | Misstated installations without a legacy database | Layer now reports `not_configured` when absent |

## Calculation contract after the patch

- Accept 1–50 food portions per request.
- Require each food to be active and visible in the selected project.
- Require a finite gram value greater than zero.
- Scale only current, stored, non-missing values on the `per_100g` basis.
- Never convert missing values to zero.
- Sum the same component only when units match.
- Return food-level availability/missingness, component totals, the explicit
  formula and a reproducible snapshot hash.
- Do not create a recall or consume a saved-calculation allowance.
- Do not infer vitamins, minerals, clinical adequacy or disease suitability.

## Edge cases and loopholes checked

| Case | Expected result |
|---|---|
| 0, negative, NaN or infinite grams | 400-level validation error |
| Missing or unknown food | Validation/not-found error without cross-project disclosure |
| More than 50 portions | Validation error to bound work |
| Missing component value | Listed as missing; excluded from totals |
| Same component, incompatible units | Calculation rejected rather than silently summed |
| Multiple foods with the same component/unit | Values sum after per-portion scaling |
| HTML returned from `/api` during startup or bad rewrite | Clear API startup/rewrite error, not a JSON syntax trace |
| Old static shell cached after a deploy | Shell revalidated; fingerprinted assets remain immutable |
| Browser request for paid capacity | Rejected unless entitlement comes from billing/operator controls |
| Specialist decides own case or verifies own profile | Rejected by backend; UI communicates the boundary |
| Demo service restarts | Synthetic records reset by design and are never represented as durable |

## Remaining gaps before production

These are not closed by this patch:

1. Import a lawfully licensed, versioned composition dataset and complete
   scientific review; synthetic values must never be promoted as reference
   data.
2. Expose reference-intake/adequacy comparison, charts and governed exports in
   the current UI, with agreed scientific definitions.
3. Finish role provisioning and independent review with multiple real accounts,
   identity lifecycle, password recovery/MFA and organization policy.
4. Configure and verify live billing only if commercial checkout is in scope.
5. Move from disposable/free-tier SQLite operation to monitored persistent
   infrastructure with backups, restore tests, TLS, secrets rotation, alerts
   and a rollback process.
6. Add end-to-end deployment checks that verify frontend and API versions match
   after each release.
7. Validate the optional real Java-serialized golden case by setting
   `DIETARY_RECALL_TEST_DB` in a controlled environment.

## Release and rollback procedure

1. Deploy the API and static frontend from the same v0.5.1 commit.
2. Confirm `/api/health` returns `version: 0.5.1` and `mode: api_only`.
3. Sign in, open **Composition calculator**, calculate the 150 g synthetic food
   1002 check and confirm 10.8 g protein and 3.15 mg iron.
4. Confirm a missing component is reported as missing, not zero.
5. Confirm the public capability/plan pages show dependency labels and no live
   purchase claim.
6. If validation fails, redeploy the prior commit; this patch introduces no
   database schema migration and does not mutate the immutable source archive.
