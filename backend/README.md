# Dietary Recall Analysis — Python Research Platform v0.5.1

v0.5 retains the v0.4 scientific and API schema while adding a deterministic
synthetic demonstration runtime. `dietary-recall demo-serve` creates disposable
legacy, research and credential databases without reading the PhD snapshot.
It is the only SQLite mode intended for the Render free-tier Blueprint.

This package contains four additive stages of the Dietary Recall Analysis PhD archive migration. v0.1 compatibility/export, v0.2 Research Core and v0.3 project/provenance/recipe functionality remain intact. v0.4 adds production authentication boundaries, provider-backed subscriptions, specialist review, licensed dataset packages and governed analytics. Historical databases remain immutable research evidence.

Create the latest database with `dietary-recall research-init SOURCE_DB RESEARCH_DB`. Upgrade v0.2 with `dietary-recall platform-upgrade SOURCE_V02 TARGET_V04`, or v0.3 with `dietary-recall platform-upgrade-v03 SOURCE_V03 TARGET_V04`. Every path is copy-first and refuses to modify the source.

The implementation deliberately separates two concepts:

- `legacy_compatibility`: implemented here. It reproduces historical Java behavior, including documented quirks, so old results can be explained and tested.
- `validated_research`: active from v0.3. It adds canonical nutrient/unit semantics, explicit external-source releases, reviewed food matches, validated evidence values, recipes, yield and retention.
- `governed_analytics`: active in v0.4. It adds suppressed descriptives, review-only QC flags and specialist-approved candidate prioritization; it does not infer nutrient chemistry or usual intake.

The v0.2 Research Core provides normalized records, imports, audit and versions. v0.3 adds project/provenance functionality, and v0.4 additionally provides separate authentication, billing webhooks, specialist review, licensed packages and governed analytics.

- project workspaces and role-based memberships for research groups;
- monthly Student 50/50, Independent 100/100 and paid configurable upload/calculation entitlements;
- canonical nutrient ontology and dimension-safe unit conversion;
- source/release/external-food provenance and evidence-separated composition values;
- candidate-only food matching with mandatory curator review;
- recipe ingredients, edible fractions, final cooked yield and retention-factor calculations with lineage;
- expanded CSV/XLSX imports for the validated layer;
- a Svelte 5 + SvelteKit project platform rather than a single-study dashboard.

No FAO/INFOODS or retention value is bundled or silently mixed into a historical calculation. The seeded external-source entries are reference metadata only. See `docs/VALIDATED_NIGERIAN_FOOD_LAYER.md` for the scientific contract.

## What is implemented

| Original capability | Python migration |
|---|---|
| SQLite database access | Read-only `LegacyRepository`; source opened with SQLite `mode=ro` + `query_only` |
| Participant lookup | Implemented |
| Food lookup | Implemented |
| Per-food composition retrieval | Implemented across Basic, Vitamins, Minerals, Poly Fats, Other Nutrients and Toxicants |
| Portion calculation | Implemented with the Java 100 g reference formula |
| Daily nutrient totals | Implemented |
| Legacy DRI selection | Implemented |
| Activity multiplier behavior | Implemented exactly as observed in Java |
| Legacy adequacy percentages | Implemented and explicitly labelled compatibility output |
| Daily/person vector averaging | Implemented as recorded-row average, matching Java semantics |
| Java serialized Daily_Records | Decoded by a read-only Java bridge and normalized to JSON/CSV |
| Java serialized Total_Average | Decoded by the same bridge |
| Historical-vs-recomputed validation | Implemented as a golden-master comparison |
| CSV export | Full raw-table export plus normalized research CSV |
| Data-quality/EDA audit | Implemented as deterministic JSON |
| Editable research workspace | Implemented as a verified database copy with new normalized recall tables |
| Participant/food/notes write service | Implemented only against a verified working copy |
| New recall storage | Implemented in Python-owned normalized tables; legacy Java BLOBs are not overwritten |
| Platform services | ASGI/Uvicorn API process plus an independent SvelteKit/Vite frontend process |
| Legacy plaintext login | Replaced by a separate salted password/session store; historical passwords remain redacted from exports |
| Historical body-weight auxiliary records | Preserved in raw data, but not exposed as an active recommendation feature |

## Important compatibility behavior

The following are intentional because they were found in the Java application. They are **not** assertions of scientific correctness:

- SQL `NULL` nutrient values become `0` inside `legacy_compatibility`, matching Java `ResultSet.getFloat()` without `wasNull()`.
- Food composition is treated as per 100 g and intake is `eaten_weight_g × composition / 100`.
- The activity multiplier is applied to all legacy recommendations.
- The legacy Vitamin A adequacy selection uses the `Vitamin_A_IU_IU` intake field against a reference field labelled `mcg`.
- Thiamin, riboflavin and choline intake are hard-coded to zero by the observed recommendation-selection logic.
- Total fibre in that logic is `Dietary_Fibre_g + Soluble_Fibre_g`.
- Copper is converted from mg to mcg for the legacy adequacy mapping.
- Day/person averages divide by the number of recorded rows, not by seven calendar days.

The normalized export does **not** apply the `NULL → 0` transformation. SQL `NULL` remains a blank value with `value_status=missing`.

## Project layout

```text
dietary_recall_python/
├── pyproject.toml
├── README.md
├── src/dietary_recall/
│   ├── audit.py           # deterministic descriptive/data-quality audit
│   ├── cli.py             # command-line application
│   ├── constants.py       # legacy schema/vector mappings
│   ├── db.py              # immutable SQLite boundary
│   ├── export.py          # raw + normalized CSV export
│   ├── legacy.py          # Java-compatible nutrient/DRI calculations
│   ├── imports.py         # CSV/XLSX staging, validation and atomic commit
│   ├── api_app.py         # ASGI application and Uvicorn startup
│   ├── asgi_runtime.py    # typed request/response routing boundary
│   ├── platform_server.py # compatibility imports; no static-file server
│   ├── security.py        # separate credential store and opaque sessions
│   ├── billing.py         # Stripe/Paystack adapters and entitlement projection
│   ├── governance.py      # specialist profiles and scientific decisions
│   ├── licensed_ingestion.py # checksummed licensed dataset packages
│   ├── analytics.py       # cohort EDA, QC flags and match triage
│   ├── research_core.py   # normalized repository + legacy-to-core seed
│   ├── research_schema.py # v0.2 SQLite schema, audit/versioning constraints
│   ├── v03_schema.py      # projects, entitlements, ontology, sources, recipes
│   ├── v04_schema.py      # auth-adjacent, billing, review and analytics records
│   ├── validated_research.py # project-scoped services and calculation engine
│   ├── serialization.py   # Java BLOB evidence/decoder interface
│   ├── server.py          # local research UI/API
│   ├── validation.py      # Java-vs-Python golden-master comparison
│   └── workspace.py       # editable verified-copy service
├── tools/
│   └── LegacyBlobDecoder.java
├── tests/
└── artifacts/newest_snapshot/
    ├── raw_tables/
    ├── normalized/
    ├── snapshot_audit.json
    ├── legacy_parity_validation.csv
    ├── legacy_parity_summary.json
    └── export_manifest.json
```

`artifacts/` is a local verification output and is intentionally excluded from the distributable ZIP because raw exports can contain direct participant identifiers. Regenerate it from the preserved database only inside an approved research environment.

The sibling `frontend/` directory contains the Svelte 5 + SvelteKit application. Exact CSV headers for all core and validated-layer entity types are available from `/api/templates/csv/{entity_type}`; flat XLSX sheets use the documented sheet names.

## v0.4 quick start

From `backend/`, install/update the package and initialize a new research-core database from the immutable legacy snapshot:

```bash
python -m pip install -e .
dietary-recall research-init data/Nutrients.db data/Nutrients-research-v04.db
```

Run two independent development services. In terminal 1:

```bash
cd backend
dietary-recall platform-api data/Nutrients-research-v04.db \
  --legacy-db data/Nutrients.db
```

In terminal 2:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The backend on port 8765 returns API JSON only.
The `--legacy-db` option is read-only and exists only for explicit compatibility
calculations; all new platform writes go to `Nutrients-research-v04.db`.

## Installation

The platform API is an ASGI application served by Uvicorn, which is installed
with the package.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Java 11+ is only needed to decode the historical `ObjectOutputStream` BLOBs. The decoder uses the archived application JAR and SQLite JDBC JAR as serialization/runtime dependencies; it does not modify them.

## Common commands

With the package installed:

```bash
dietary-recall audit /path/to/Nutrients.db
dietary-recall foods /path/to/Nutrients.db rice
dietary-recall calculate /path/to/Nutrients.db 127:450 197:50
```

Export raw + normalized data without Java decoding:

```bash
dietary-recall export /path/to/Nutrients.db ./export
```

Export including recovered Java recall objects:

```bash
dietary-recall export /path/to/Nutrients.db ./export \
  --helper tools/LegacyBlobDecoder.java \
  --app-jar '/path/to/Nutrient Calculator.jar' \
  --sqlite-jar /path/to/sqlite-jdbc-3.8.11.2.jar
```

Create an explicitly writable copy. The command refuses to overwrite the source or an existing target and verifies SHA-256 equality before adding Python-owned tables:

```bash
dietary-recall init-workspace /path/to/Nutrients.db ./working/Nutrients-working.db
```

Run the local application against the immutable source:

```bash
dietary-recall serve /path/to/Nutrients.db
```

It listens on `http://127.0.0.1:8765`. Add `--working-db ./working/Nutrients-working.db` to enable the normalized recall write endpoint. Direct participant names are intentionally omitted from the HTTP API.

## CSV outputs

### Raw fidelity track

`raw_tables/*.csv` contains one CSV per user table. Values are not normalized. SQL BLOBs use reversible `base64:<payload>` encoding. `Users.Password` is replaced by `[REDACTED_FROM_EXPORT]`; the immutable SQLite file remains the canonical evidence copy.

Raw participant tables contain historical direct identifiers and must therefore be handled as sensitive research data.

### Normalized analysis track

- `food.csv` — one row per food.
- `nutrient_definition.csv` — component/table/column/unit dictionary.
- `food_component_value_long.csv` — long-form food × component values with missingness preserved.
- `life_stage.csv` — life-stage lookup.
- `reference_intake_long.csv` — long-form legacy DRI values.
- `participant_deidentified.csv` — deterministic research keys with names excluded and legacy row/business keys retained for linkage.
- `serialized_blob_inventory.csv` — BLOB location, byte size, SHA-256 and Java-stream flag.
- `decoded_java_objects.jsonl` — recovered serialized objects when Java decoding is enabled.
- `recall_item_decoded.csv` — recovered day-by-day foods and weights.
- `legacy_daily_vector_long.csv` — recovered Java daily nutrient/recommendation vectors.

`decoded_java_objects.jsonl` is an evidence-level recovery file and can contain a historical `person_name` stored inside the Java object. Treat it as sensitive like the raw tables. The deidentified participant and recall-item CSVs do not carry participant names.

`snapshot_audit.json` adds schema, row counts, composition completeness, orphan composition rows, duplicate participant user codes, weekday BLOB counts and serialization evidence counts.

## Golden-master result on the most recent snapshot

The current archive's most recent database has SHA-256:

`9526c7baaf6c01d6bd7b4d85ef462ccc872a963b7cb74268826310cd6ec65359`

All 848 non-null day BLOBs decoded. Python recomputation against the current composition tables produced:

| Result | Daily recalls |
|---|---:|
| Match within `1e-3` | 823 |
| Recomputed but differs from stored Java total | 15 |
| Cannot recompute because a recalled Food_ID is no longer in the current Food table | 10 |
| Total decoded daily recalls | 848 |

Across recomputable daily records, 46,082 stored nutrient values were compared and 128 exceeded the tolerance. These discrepancies are retained as historical evidence in `legacy_parity_validation.csv`; the migration does not silently replace the serialized historical result.

Legacy relationship note: the Java application uses `Person.Usercode` as `Daily_Records.Person_ID`/`Total_Average.Person_ID`; it does **not** use the physical SQLite `Person.id` row key. The normalized export records both the physical row ID and this legacy business ID. Five duplicate `Usercode` groups in the newest snapshot are reported as data-quality collisions rather than silently merged.

A recovered real-world golden case (Sunday row 90) also matches the Python calculation for calcium (`421.318 mg`), copper (`2.0292 mg`), cadmium (`0.569 mcg`) and lead (`31.48 mcg`).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Set `DIETARY_RECALL_TEST_DB=/path/to/Nutrients.db` to enable the archived-snapshot golden case.

## Next analytical layer

The validated composition foundation is now implemented. The next safe extension is project-scoped cohort descriptives and quality-control reports, followed by anomaly detection and dietary-pattern models only after ontology mappings, external food matches, missingness rules and source values have completed domain review. Modeled outputs must remain a distinct evidence class and must never overwrite measured study data.
