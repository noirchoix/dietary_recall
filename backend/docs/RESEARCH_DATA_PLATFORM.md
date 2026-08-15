# Research Data Platform v0.2

> Historical contract: this document describes the v0.2 Research Core preserved inside v0.4. For the validated-composition layer see `VALIDATED_NIGERIAN_FOOD_LAYER.md`; for deployment and governed analytics see `DEPLOYMENT_V04.md`.

## Purpose

Version 0.2 is the writable data-management layer that sits between the recovered Java application and future scientific inference. It preserves the stable v0.1 compatibility layer and creates a normalized, provenance-aware SQLite database for new research.

The architectural rule is simple: `Nutrients.db` is evidence; the research-core database is the writable system of record; validated standards and governed analytics remain separate additive layers.

## Layer contract

| Layer | Status | Writable | Purpose |
|---|---|---:|---|
| `legacy_compatibility` | available | No | Reproduce/review recoverable Java behavior and historical exports. |
| `research_core` | active | Yes | Manage normalized research source events, measurements, imports, versions and audit. |
| `validated_research` | planned | No | Later standards-backed nutrient semantics, food matching, recipes/retention and validated adequacy. |

No standards value is silently merged into v0.2 Research Core.

## Core entities

| Entity | Stable key | Legacy link | Versioned | Notes |
|---|---|---|---:|---|
| Food | `food_uid` | `legacy_food_id` | Yes | New compatibility IDs allocate from `MAX+1` under an immediate SQLite write lock. |
| Food component | `component_value_uid` | exact legacy table/column definition | Per-row counter | Missing values remain `NULL`; provenance is explicit. |
| Participant | `participant_uid` | legacy physical row + `Usercode` | Yes | Participant identity is distinct from application users. |
| Application user | `user_uid` | none | Yes | Role registry only; no password column exists. |
| Experiment | `experiment_uid` | optional Food link | Yes | Sample/method/lab/date/status/provenance. |
| Experiment result | `result_uid` | nutrient definition | Audit event | Supports replicate, LOD, LOQ, uncertainty and QC status. |
| Recall | `recall_uid` | participant + foods | Yes | Stores row-level food/gram events and derived composition snapshot hash. |
| Import batch | `batch_uid` | source SHA-256 | Immutable outcome | Stage/validate first; commit is atomic. |

## Recall calculation

Research Core uses the migrated composition basis exactly as stored: values are per 100 g and a recall item contributes `amount_g × component_value / 100`. Only non-missing composition values enter a total. Each derived result stores a hash of the exact component value/version snapshot used.

This is a reproducible research calculation, not the legacy recommendation engine and not a standards-backed adequacy interpretation. The interactive platform does not provide personalized weight-loss or calorie-restriction guidance.

## Batch import

Accepted inputs are UTF-8 CSV and flat `.xlsx` sheets up to 25 MiB. Supported entity types are:

1. `foods`
2. `participants`
3. `app_users`
4. `experiments`
5. `components`
6. `experiment_results`

The workflow is `stage → validate → review → commit`. Staging records SHA-256, source filename, normalized row JSON and validation errors but does not mutate research entities. A batch with any invalid row cannot commit. Commit takes an immediate SQLite write lock and either completes all rows or rolls the entire batch back.

Conflict policies are `reject`, `insert_only` and `upsert`. `upsert` creates version/audit history for mutable master records rather than silently replacing evidence.

Use `templates/research_import_templates.xlsx`, or download the exact CSV header for an entity from `/api/templates/csv/{entity}`.

## Audit and versions

`audit_log` is append-only at the database level: update/delete triggers abort attempts. Audit rows retain actor, source, entity, import-batch link, before/after JSON and operation detail.

`entity_versions` stores immutable JSON snapshots for versioned Food, Participant, Application User, Experiment and Recall entities. Normal data-entry edits increment the corresponding entity version before adding its snapshot.

## API surface

`platform-api` exposes JSON endpoints for summary/layers, foods/components, participants, users, experiments/results, recalls, nutrients, imports/templates and audit history. It is an API-only Uvicorn service; the separate SvelteKit development process proxies `/api`. The `X-Research-Actor` request header becomes the audit actor label. This is provenance metadata, not authentication.

The v0.2 local app deliberately does not invent a password system. If the platform is later deployed for multiple researchers, authentication/authorization should be attached outside the research database and mapped to the `app_users` role registry.

## Future inference contract

The SvelteKit Inference page lists candidate analysis lanes but does not run fabricated science. Suitable next lanes after curation include cohort descriptives, food-contribution analysis, composition similarity, anomaly/QC detection and exploratory pattern discovery. Standards-backed nutrient adequacy, recipe yield/retention and external food matching belong in `validated_research` first.
