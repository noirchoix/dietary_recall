# Validated Nutrient & Nigerian Food Composition Layer

## Scope

Version 0.3 is an additive evidence layer over the recovered Java-compatible results and the normalized v0.2 Research Core. It supports Nigerian prepared-food curation without treating an external table, a name match or a modeled value as if it were a laboratory measurement.

The three calculation lanes remain distinguishable:

| Layer | Purpose | Evidence treatment |
|---|---|---|
| `legacy_compatibility` | Explain/reproduce historical Java behavior | Read-only historical semantics, including documented quirks |
| `research_core` | Store normalized study records and measurements | Missing values preserved; mutable entities versioned |
| `validated_research` | Curate canonical nutrients, external evidence, matches, recipes and retention | Separate records, explicit review state and full calculation lineage |

## Collaboration and entitlement model

A project is the tenancy boundary. An application identity is global; its `project_memberships` row supplies the role within one project. Project records cannot be linked to another project. Existing v0.2 data migrates into `Legacy PhD Research`.

| Role | Read | Enter core/source data | Curate matches/factors | Manage members |
|---|---:|---:|---:|---:|
| Owner | Yes | Yes | Yes | Yes |
| Admin | Yes | Yes | Yes | Yes |
| Contributor | Yes | Yes | No | No |
| Analyst | Yes | No | Yes | No |
| Viewer | Yes | No | No | No |

Student projects receive 50 committed import rows and 50 calculation runs per UTC calendar month. Independent projects receive 100/100. Paid research-group projects default to 1,000/1,000 and accept administrative overrides greater than 100. The append-only `usage_events` table is the accounting record.

Import staging is unmetered. Commit begins an immediate SQLite transaction, verifies the complete allowance, adds the usage event and writes every entity row. Any error rolls back both data and usage. Recipe and explicit legacy calculations use the same atomic accounting rule.

## Nutrient ontology and units

`canonical_nutrients` separates stable code, name, component class, canonical unit, quantity dimension, INFOODS tag, chemical form and review status. `nutrient_mappings` links exact source-system nutrient identifiers to the ontology; it does not copy values.

The unit registry converts only within a compatible dimension:

$$x_{target} = x_{source}\times\frac{f_{source}}{f_{target}}$$

Mass uses grams as its dimension base; energy uses kilojoules. IU, RAE and RE remain distinct because a generic conversion would be scientifically invalid without nutrient/form-specific evidence. Unknown legacy units remain `legacy_unspecified` until reviewed.

Legacy-to-canonical mappings are seeded as `provisional`. INFOODS tags are attached only where an explicit mapping was encoded. Publication workflows should require specialist review and set ontology/mapping status accordingly.

## Provenance and food matching

External evidence is recorded in this order:

1. `data_sources` — publisher, source type, URL, citation, licence notes;
2. `source_releases` — release label/date, retrieval date, source-file SHA-256 and schema notes;
3. `external_foods` — exact source food code/name, local/scientific names, group, country and preparation state;
4. `external_food_component_values` — nutrient, value, unit, basis, analytical method, uncertainty and row provenance.

External values never update `food_component_values`. A matching run only writes scored candidates using normalized-name sequence/token features. A curator must accept or reject a candidate. One accepted match may supply otherwise absent composition evidence during recipe calculation, but it still retains the `accepted_external_match` evidence label.

For Nigerian prepared foods, matching should consider local name, ingredient pattern, preparation method, region, edible portion and water/fat changes—not name similarity alone. The current algorithm is intentionally a candidate generator, not an automatic scientific match.

## Validated values and evidence order

`validated_food_component_values` stores project-specific evidence without overwriting legacy values. Each row has an evidence class (`study_measured`, `nigerian_regional`, `external_matched`, `recipe_calculated`, `transparent_imputation` or `missing`) and a review status.

When calculating a research-food ingredient, the engine selects at most one value per canonical nutrient in this order:

1. validated/reviewed project values, preferring study measurement then Nigerian regional evidence;
2. mapped legacy reported values;
3. values from a curator-accepted external-food match.

No nutrient is derived from a food name, total energy or heavy-metal result. Heavy metals remain `toxicant`, not micronutrients.

## Recipe, yield and retention calculation

Each recipe records input ingredient weights, edible fractions, preparation method and measured final cooked weight. For ingredient $i$ and nutrient $n$:

$$W_i = input\_weight_{i,g}\times edible\_fraction_i$$

$$A_{i,n} = \frac{composition_{i,n}}{100}\times W_i\times retention_{method,group,n}$$

$$recipe\_total_n = \sum_i A_{i,n}$$

$$recipe\_per100g_n = \frac{recipe\_total_n}{final\_cooked\_weight_g}\times100$$

This is the true-retention form when cooked yield is represented by the measured final weight: nutrient retained is based on nutrient amount before cooking, while the reported post-cooking concentration uses final food weight.

`strict` policy refuses the calculation if any ingredient/nutrient lacks an explicit applicable retention factor. `best_available` uses 1.0 only as a disclosed project assumption, records warnings and labels the run exploratory. It never represents the assumption as a measured factor.

Every saved result contains total-recipe value, per-100-g value, canonical unit, retention status, ingredient-level lineage and a SHA-256 hash of the exact recipe/value/factor snapshot. One successfully saved run consumes one calculation allowance.

## Batch import types

The existing CSV/XLSX workflow now supports:

- core: `foods`, `participants`, `app_users`, `experiments`, `components`, `experiment_results`;
- validated layer: `data_sources`, `source_releases`, `external_foods`, `external_components`, `validated_components`, `retention_factors`.

Download an exact CSV header from `/api/templates/csv/{entity_type}`. For XLSX, use the corresponding sheet name shown by the platform. Import sources/releases before foods, and foods before their component values. Import identities do not automatically grant project membership; use Contributors to make that explicit.

## Standards alignment

The schema and review boundaries are designed to accommodate FAO/INFOODS food-composition identifiers, units, checking rules and food-matching documentation. Retention provenance can represent USDA Release 6 tables or another appropriately licensed source. v0.3 seeds citations/URLs only; it does not redistribute those datasets or claim that provisional legacy mappings have been validated by FAO or USDA.

Primary references:

- FAO/INFOODS standards and guidelines: https://www.fao.org/infoods/infoods/standards-guidelines/en/
- FAO/INFOODS food matching guidelines: https://www.fao.org/4/ap805e/ap805e.pdf
- FAO/INFOODS food-composition-data checking guidance: https://openknowledge.fao.org/bitstreams/61b54d2a-bc62-4f46-9026-2f28ac1eedc1/download
- USDA nutrient retention factors: https://www.ars.usda.gov/northeast-area/beltsville-md-bhnrc/beltsville-human-nutrition-research-center/methods-and-application-of-food-composition-laboratory/mafcl-site-pages/nutrient-retention-factors/
- USDA Release 6 table: https://www.ars.usda.gov/arsuserfiles/80400530/pdf/retn06.pdf

## Deployment limitations

The local HTTP server trusts `X-Research-Actor` only as an auditable identity selector. A hosted system must replace that boundary with verified authentication and map the verified identity to the same membership checks. The database enforces plan allowances but payment/checkout/webhook integration is separate. Sensitive participant data requires access control, encryption, backups, retention rules and ethics/governance appropriate to the research institution.

This release is for research food-composition analysis. It does not generate personalized dieting, weight-loss or clinical treatment recommendations.
