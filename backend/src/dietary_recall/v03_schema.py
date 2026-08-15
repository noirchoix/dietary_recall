"""Additive v0.3 schema for collaborative projects and validated food data.

The migration never opens or modifies the historical ``Nutrients.db``.  It is
applied only to a normalized Research Data Platform database.  Public upgrade
helpers copy a v0.2 database first and then migrate the copy.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any


PLATFORM_SCHEMA_VERSION = 3
DEFAULT_PROJECT_UID = "proj_legacy_phd_research"
DEFAULT_PROJECT_CODE = "LEGACY-PHD"
DEFAULT_OWNER_UID = "user_local_platform_owner"
DEFAULT_OWNER_EMAIL = "owner@local.research"


V03_SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS plan_definitions (
    plan_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    monthly_import_rows INTEGER NOT NULL CHECK(monthly_import_rows > 0),
    monthly_calculation_runs INTEGER NOT NULL CHECK(monthly_calculation_runs > 0),
    max_projects INTEGER NOT NULL CHECK(max_projects > 0),
    max_members INTEGER NOT NULL CHECK(max_members > 0),
    paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS research_projects (
    project_uid TEXT PRIMARY KEY,
    project_code TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    description TEXT,
    country_code TEXT NOT NULL DEFAULT 'NG',
    research_domain TEXT NOT NULL DEFAULT 'dietary_recall',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_subscriptions (
    project_uid TEXT PRIMARY KEY REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    plan_code TEXT NOT NULL REFERENCES plan_definitions(plan_code),
    import_rows_override INTEGER,
    calculation_runs_override INTEGER,
    starts_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ends_at TEXT,
    CHECK(import_rows_override IS NULL OR import_rows_override > 100),
    CHECK(calculation_runs_override IS NULL OR calculation_runs_override > 100)
);

CREATE TABLE IF NOT EXISTS project_memberships (
    membership_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    user_uid TEXT NOT NULL REFERENCES app_users(user_uid),
    project_role TEXT NOT NULL CHECK(project_role IN ('owner','admin','contributor','analyst','viewer')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('invited','active','suspended','removed')),
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_uid, user_uid)
);
CREATE INDEX IF NOT EXISTS project_memberships_user_idx ON project_memberships(user_uid, status);

CREATE TABLE IF NOT EXISTS project_records (
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_uid TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(project_uid, entity_type, entity_uid),
    UNIQUE(entity_type, entity_uid)
);
CREATE INDEX IF NOT EXISTS project_records_entity_idx ON project_records(entity_type, entity_uid);

CREATE TABLE IF NOT EXISTS usage_events (
    usage_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    metric TEXT NOT NULL CHECK(metric IN ('import_rows','calculation_runs')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    period_key TEXT NOT NULL,
    reference_type TEXT,
    reference_uid TEXT,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS usage_events_period_idx ON usage_events(project_uid, metric, period_key);

CREATE TRIGGER IF NOT EXISTS usage_events_no_update
BEFORE UPDATE ON usage_events
BEGIN
  SELECT RAISE(ABORT, 'usage_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS usage_events_no_delete
BEFORE DELETE ON usage_events
BEGIN
  SELECT RAISE(ABORT, 'usage_events is append-only');
END;

CREATE TABLE IF NOT EXISTS unit_definitions (
    unit_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    quantity_dimension TEXT NOT NULL,
    factor_to_dimension_base REAL,
    dimension_base_unit TEXT NOT NULL,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS canonical_nutrients (
    canonical_nutrient_uid TEXT PRIMARY KEY,
    canonical_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    component_class TEXT NOT NULL CHECK(component_class IN ('energy','macronutrient','vitamin','mineral','fatty_acid','bioactive','toxicant','other')),
    canonical_unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    quantity_dimension TEXT NOT NULL,
    infoods_tag TEXT,
    chemical_form TEXT,
    notes TEXT,
    ontology_status TEXT NOT NULL DEFAULT 'provisional' CHECK(ontology_status IN ('provisional','reviewed','deprecated')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE INDEX IF NOT EXISTS canonical_nutrients_infoods_idx ON canonical_nutrients(infoods_tag);

CREATE TABLE IF NOT EXISTS nutrient_aliases (
    alias_uid TEXT PRIMARY KEY,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    alias_text TEXT NOT NULL,
    alias_scope TEXT NOT NULL DEFAULT 'general',
    UNIQUE(canonical_nutrient_uid, alias_text, alias_scope)
);

CREATE TABLE IF NOT EXISTS nutrient_mappings (
    mapping_uid TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_nutrient_code TEXT NOT NULL,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    expression_unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    mapping_status TEXT NOT NULL DEFAULT 'provisional' CHECK(mapping_status IN ('provisional','reviewed','rejected')),
    mapping_notes TEXT,
    UNIQUE(source_system, source_nutrient_code)
);

CREATE TABLE IF NOT EXISTS data_sources (
    source_uid TEXT PRIMARY KEY,
    project_uid TEXT REFERENCES research_projects(project_uid),
    source_code TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('study_analysis','regional_table','food_composition_table','retention_table','literature','manual')),
    publisher TEXT,
    source_url TEXT,
    citation TEXT,
    license_notes TEXT,
    source_status TEXT NOT NULL DEFAULT 'active' CHECK(source_status IN ('active','reference_only','retired')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_uid, source_code)
);

CREATE TABLE IF NOT EXISTS source_releases (
    source_release_uid TEXT PRIMARY KEY,
    source_uid TEXT NOT NULL REFERENCES data_sources(source_uid) ON DELETE CASCADE,
    release_label TEXT NOT NULL,
    release_date TEXT,
    retrieved_at TEXT,
    sha256 TEXT,
    schema_notes TEXT,
    imported_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_uid, release_label)
);

CREATE TABLE IF NOT EXISTS external_foods (
    external_food_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    source_release_uid TEXT NOT NULL REFERENCES source_releases(source_release_uid),
    source_food_code TEXT NOT NULL,
    food_name TEXT NOT NULL,
    local_name TEXT,
    scientific_name TEXT,
    food_group TEXT,
    country_code TEXT,
    preparation_state TEXT,
    edible_portion_percent REAL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_release_uid, source_food_code)
);
CREATE INDEX IF NOT EXISTS external_foods_project_name_idx ON external_foods(project_uid, food_name);

CREATE TABLE IF NOT EXISTS external_food_component_values (
    external_value_uid TEXT PRIMARY KEY,
    external_food_uid TEXT NOT NULL REFERENCES external_foods(external_food_uid) ON DELETE CASCADE,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    value REAL,
    unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    basis TEXT NOT NULL DEFAULT 'per_100g_edible_portion',
    value_status TEXT NOT NULL DEFAULT 'reported' CHECK(value_status IN ('reported','trace','missing','estimated','calculated')),
    analytical_method TEXT,
    uncertainty REAL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(external_food_uid, canonical_nutrient_uid)
);

CREATE TABLE IF NOT EXISTS food_match_candidates (
    match_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    research_food_uid TEXT NOT NULL REFERENCES research_foods(food_uid),
    external_food_uid TEXT NOT NULL REFERENCES external_foods(external_food_uid),
    match_method TEXT NOT NULL,
    score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
    feature_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL DEFAULT 'candidate' CHECK(review_status IN ('candidate','accepted','rejected','superseded')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_uid, research_food_uid, external_food_uid)
);
CREATE INDEX IF NOT EXISTS food_match_review_idx ON food_match_candidates(project_uid, review_status, score DESC);

CREATE TABLE IF NOT EXISTS validated_food_component_values (
    validated_value_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    research_food_uid TEXT NOT NULL REFERENCES research_foods(food_uid),
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    value REAL,
    unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    basis TEXT NOT NULL DEFAULT 'per_100g_edible_portion',
    evidence_class TEXT NOT NULL CHECK(evidence_class IN ('study_measured','nigerian_regional','external_matched','recipe_calculated','transparent_imputation','missing')),
    validation_status TEXT NOT NULL DEFAULT 'provisional' CHECK(validation_status IN ('provisional','reviewed','validated','rejected')),
    source_release_uid TEXT REFERENCES source_releases(source_release_uid),
    source_record_uid TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS validated_values_lookup_idx ON validated_food_component_values(project_uid, research_food_uid, canonical_nutrient_uid, validation_status);

CREATE TABLE IF NOT EXISTS retention_factors (
    retention_factor_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    source_release_uid TEXT NOT NULL REFERENCES source_releases(source_release_uid),
    cooking_method_code TEXT NOT NULL,
    food_group TEXT,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    retention_fraction REAL NOT NULL CHECK(retention_fraction >= 0 AND retention_fraction <= 1),
    factor_status TEXT NOT NULL DEFAULT 'reported' CHECK(factor_status IN ('analytical','reported','imputed_by_source','project_assumption')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS retention_lookup_idx ON retention_factors(project_uid, cooking_method_code, food_group, canonical_nutrient_uid);

CREATE TABLE IF NOT EXISTS recipes (
    recipe_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    recipe_code TEXT NOT NULL,
    recipe_name TEXT NOT NULL,
    description TEXT,
    cooking_method_code TEXT NOT NULL,
    final_cooked_weight_g REAL NOT NULL CHECK(final_cooked_weight_g > 0),
    servings REAL CHECK(servings IS NULL OR servings > 0),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','validated','archived')),
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_uid, recipe_code)
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_ingredient_uid TEXT PRIMARY KEY,
    recipe_uid TEXT NOT NULL REFERENCES recipes(recipe_uid) ON DELETE CASCADE,
    ingredient_order INTEGER NOT NULL,
    research_food_uid TEXT REFERENCES research_foods(food_uid),
    external_food_uid TEXT REFERENCES external_foods(external_food_uid),
    ingredient_name TEXT NOT NULL,
    input_weight_g REAL NOT NULL CHECK(input_weight_g > 0),
    edible_fraction REAL NOT NULL DEFAULT 1 CHECK(edible_fraction > 0 AND edible_fraction <= 1),
    food_group TEXT,
    notes TEXT,
    CHECK((research_food_uid IS NOT NULL AND external_food_uid IS NULL) OR (research_food_uid IS NULL AND external_food_uid IS NOT NULL)),
    UNIQUE(recipe_uid, ingredient_order)
);

CREATE TABLE IF NOT EXISTS recipe_calculation_runs (
    calculation_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    recipe_uid TEXT NOT NULL REFERENCES recipes(recipe_uid),
    recipe_version INTEGER NOT NULL,
    policy TEXT NOT NULL CHECK(policy IN ('strict','best_available')),
    validation_status TEXT NOT NULL CHECK(validation_status IN ('validated','exploratory','failed')),
    input_snapshot_hash TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recipe_calculation_results (
    calculation_uid TEXT NOT NULL REFERENCES recipe_calculation_runs(calculation_uid) ON DELETE CASCADE,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    total_recipe_value REAL NOT NULL,
    per_100g_value REAL NOT NULL,
    unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    retention_status TEXT NOT NULL,
    lineage_json TEXT NOT NULL,
    PRIMARY KEY(calculation_uid, canonical_nutrient_uid)
);
"""


UNIT_ROWS = (
    ("g", "gram", "mass", 1.0, "g", None),
    ("mg", "milligram", "mass", 0.001, "g", None),
    ("mcg", "microgram", "mass", 0.000001, "g", "Preferred ASCII form used by the legacy database."),
    ("ug", "microgram", "mass", 0.000001, "g", "Alias used by some external composition tables."),
    ("µg", "microgram", "mass", 0.000001, "g", "Unicode alias."),
    ("kg", "kilogram", "mass", 1000.0, "g", None),
    ("kJ", "kilojoule", "energy", 1.0, "kJ", None),
    ("kcal", "kilocalorie", "energy", 4.184, "kJ", None),
    ("IU", "international unit", "biological_activity", None, "IU", "No generic IU-to-mass conversion; nutrient-specific evidence is required."),
    ("RAE", "retinol activity equivalent", "vitamin_a_rae", None, "RAE", "Kept distinct from RE and IU."),
    ("RE", "retinol equivalent", "vitamin_a_re", None, "RE", "Kept distinct from RAE and IU."),
    ("legacy_unspecified", "legacy unspecified", "unknown", None, "legacy_unspecified", "Cannot be converted until curated."),
)


INFOODS_TAGS = {
    "energy": "ENERC",
    "protein": "PROCNT",
    "carbohydrate_total": "CHOCDF",
    "dietary_fibre": "FIBTG",
    "total_sugars": "SUGAR",
    "fat_total": "FAT",
    "fat_saturated": "FASAT",
    "fat_monounsaturated": "FAMS",
    "fat_polyunsaturated": "FAPU",
    "cholesterol": "CHOLE",
    "water": "WATER",
    "thiamin": "THIA",
    "riboflavin": "RIBF",
    "niacin": "NIA",
    "vitamin_b6": "VITB6A",
    "vitamin_b12": "VITB12",
    "vitamin_c": "VITC",
    "vitamin_d_mass": "VITD",
    "vitamin_e": "VITE",
    "folate": "FOL",
    "vitamin_k": "VITK",
    "pantothenic_acid": "PANTAC",
    "calcium": "CA",
    "copper": "CU",
    "iron": "FE",
    "magnesium": "MG",
    "manganese": "MN",
    "phosphorus": "P",
    "potassium": "K",
    "selenium": "SE",
    "sodium": "NA",
    "zinc": "ZN",
    "alcohol": "ALC",
    "caffeine": "CAFFN",
}


SPECIAL_CODES = {
    "Calories_kcal": "energy",
    "Protein_g": "protein",
    "Carbohydrates_g": "carbohydrate_total",
    "Dietary_Fibre_g": "dietary_fibre",
    "Soluble_Fibre_g": "soluble_fibre",
    "Total_Sugars_g": "total_sugars",
    "Monosaccharides_g": "monosaccharides",
    "Disaccharides_g": "disaccharides",
    "Other_Carbs_g": "other_carbohydrate",
    "Fat_g": "fat_total",
    "Saturated_Fat_g": "fat_saturated",
    "Mono_Fat_g": "fat_monounsaturated",
    "Poly_Fat_g": "fat_polyunsaturated",
    "Trans_Fatty_Acid_mg": "fat_trans",
    "Cholesterol_mg": "cholesterol",
    "Water_g": "water",
    "Vitamin_A_IU_IU": "vitamin_a_activity_iu",
    "Vitamin_A_RAE_RAE": "vitamin_a_rae",
    "Carotenoid_RE_RE": "carotenoids_re",
    "Retinol_RE_RE": "retinol_re",
    "BetaCarotene_mcg": "beta_carotene",
    "Vitamin_B1_mg": "thiamin",
    "Vitamin_B2_mg": "riboflavin",
    "Vitamin_B3_mg": "niacin",
    "Niacin_mg": "niacin",
    "Vitamin_B6_mg": "vitamin_b6",
    "Vitamin_B12_mcg": "vitamin_b12",
    "Vitamin_C_mg": "vitamin_c",
    "Vitamin_D_IU_IU": "vitamin_d_activity_iu",
    "Vitamin_D_mcg_mcg": "vitamin_d_mass",
    "Vitamin_E_mg": "vitamin_e",
    "Biotin_mcg": "biotin",
    "Folate_mcg": "folate",
    "Vitamin_K_mcg": "vitamin_k",
    "Panthotenic_Acid_mg": "pantothenic_acid",
    "Omega_3_Fatty_Acid_g": "omega_3_fatty_acids",
    "Omega_6_Fatty_Acid_g": "omega_6_fatty_acids",
}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _canonical_code(legacy_column: str, component_kind: str) -> str:
    code = SPECIAL_CODES.get(legacy_column)
    if code:
        return code
    stem = re.sub(r"_(?:kcal|mcg|mg|g|IU_IU|IU|RAE_RAE|RAE|RE_RE|RE)$", "", legacy_column)
    code = _slug(stem)
    return f"toxicant_{code}" if component_kind == "toxicant" else code


def _component_class(group: str, kind: str, code: str) -> str:
    if kind == "toxicant":
        return "toxicant"
    if code == "energy":
        return "energy"
    if group == "Basic_Components":
        return "macronutrient"
    if group == "Vitamins":
        return "vitamin"
    if group == "Minerals":
        return "mineral"
    if group == "Poly_Fats":
        return "fatty_acid"
    if group == "Other_Nutrients":
        return "bioactive"
    return "other"


def _add_column(con: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def apply_v03_schema(path: str | Path, actor: str = "migration") -> dict[str, Any]:
    """Migrate an existing v0.2 Research Core database in place.

    This low-level function is intentionally separate from the public copy-first
    upgrade helper.  It must never be pointed at the historical Nutrients.db.
    """
    target = Path(path).resolve()
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        mode = con.execute("SELECT value FROM research_meta WHERE key='platform_mode'").fetchone()
        if not mode or mode[0] != "research_core":
            raise ValueError("v0.3 migration requires a normalized Research Data Platform database")
        con.executescript(V03_SCHEMA_SQL)
        _add_column(con, "import_batches", "project_uid TEXT REFERENCES research_projects(project_uid)")

        con.executemany(
            "INSERT OR IGNORE INTO plan_definitions(plan_code,display_name,monthly_import_rows,monthly_calculation_runs,max_projects,max_members,paid) VALUES (?,?,?,?,?,?,?)",
            (
                ("student", "Student", 50, 50, 1, 3, 0),
                ("independent", "Independent Researcher", 100, 100, 3, 8, 0),
                ("paid", "Research Group", 1000, 1000, 25, 100, 1),
            ),
        )
        con.executemany(
            "INSERT OR IGNORE INTO unit_definitions(unit_code,display_name,quantity_dimension,factor_to_dimension_base,dimension_base_unit,notes) VALUES (?,?,?,?,?,?)",
            UNIT_ROWS,
        )

        existing_owner = con.execute("SELECT user_uid,email FROM app_users ORDER BY active DESC,created_at LIMIT 1").fetchone()
        if existing_owner:
            owner_uid = str(existing_owner["user_uid"])
        else:
            owner_uid = DEFAULT_OWNER_UID
            con.execute(
                "INSERT INTO app_users(user_uid,email,display_name,role) VALUES (?,?,?,?)",
                (owner_uid, DEFAULT_OWNER_EMAIL, "Local Research Owner", "admin"),
            )
        con.execute(
            "INSERT OR IGNORE INTO research_projects(project_uid,project_code,project_name,description,created_by) VALUES (?,?,?,?,?)",
            (DEFAULT_PROJECT_UID, DEFAULT_PROJECT_CODE, "Legacy PhD Research", "Migrated Nigerian prepared-food dietary recall study workspace", actor),
        )
        con.execute(
            "INSERT OR IGNORE INTO project_subscriptions(project_uid,plan_code) VALUES (?,?)",
            (DEFAULT_PROJECT_UID, "independent"),
        )
        con.execute(
            "INSERT OR IGNORE INTO project_memberships(membership_uid,project_uid,user_uid,project_role,status) VALUES (?,?,?,?,?)",
            ("member_default_owner", DEFAULT_PROJECT_UID, owner_uid, "owner", "active"),
        )
        for row in con.execute("SELECT user_uid FROM app_users WHERE user_uid<>?", (owner_uid,)):
            con.execute(
                "INSERT OR IGNORE INTO project_memberships(membership_uid,project_uid,user_uid,project_role,status) VALUES (?,?,?,?,?)",
                (_uid("mem"), DEFAULT_PROJECT_UID, row[0], "contributor", "active"),
            )
        for entity_type, table, uid_column in (
            ("food", "research_foods", "food_uid"),
            ("participant", "participants", "participant_uid"),
            ("experiment", "experiments", "experiment_uid"),
            ("recall", "recalls", "recall_uid"),
            ("import_batch", "import_batches", "batch_uid"),
        ):
            con.execute(
                f"INSERT OR IGNORE INTO project_records(project_uid,entity_type,entity_uid,linked_by) SELECT ?,?,{uid_column},? FROM {table}",
                (DEFAULT_PROJECT_UID, entity_type, actor),
            )
        con.execute("UPDATE import_batches SET project_uid=? WHERE project_uid IS NULL", (DEFAULT_PROJECT_UID,))

        legacy_sha_row = con.execute("SELECT value FROM research_meta WHERE key='legacy_source_sha256'").fetchone()
        legacy_sha = str(legacy_sha_row[0]) if legacy_sha_row else None
        con.execute(
            "INSERT OR IGNORE INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,citation,license_notes) VALUES (?,?,?,?,?,?,?,?)",
            ("src_legacy_phd", DEFAULT_PROJECT_UID, "LEGACY-PHD", "Original PhD laboratory and dietary recall dataset", "study_analysis", "PhD research project", "Immutable Nutrients.db snapshot", "Internal research evidence; access controlled by the project."),
        )
        con.execute(
            "INSERT OR IGNORE INTO source_releases(source_release_uid,source_uid,release_label,sha256,schema_notes,imported_by) VALUES (?,?,?,?,?,?)",
            ("rel_legacy_phd_snapshot", "src_legacy_phd", "immutable-snapshot", legacy_sha, "Original SQLite source is not stored in the Research Core database.", actor),
        )
        con.execute(
            "INSERT OR IGNORE INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,source_url,citation,license_notes,source_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("src_fao_infoods_reference", None, "FAO-INFOODS", "FAO/INFOODS standards and food composition resources", "food_composition_table", "FAO", "https://www.fao.org/infoods/infoods/standards-guidelines/en/", "FAO/INFOODS Standards and Guidelines", "Reference metadata only; composition data must be imported under its applicable licence.", "reference_only"),
        )
        con.execute(
            "INSERT OR IGNORE INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,source_url,citation,license_notes,source_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("src_usda_retention_reference", None, "USDA-RETENTION-6", "USDA Table of Nutrient Retention Factors, Release 6", "retention_table", "USDA Agricultural Research Service", "https://www.ars.usda.gov/arsuserfiles/80400530/pdf/retn06.pdf", "USDA Table of Nutrient Retention Factors, Release 6 (2007)", "Reference metadata only; factors are not bundled and must be imported with provenance.", "reference_only"),
        )

        definitions = list(con.execute("SELECT * FROM nutrient_definitions ORDER BY nutrient_code"))
        for row in definitions:
            code = _canonical_code(row["legacy_column"] or row["display_name"], row["component_kind"])
            canonical_uid = f"cn_{code}"
            unit = row["unit"] if con.execute("SELECT 1 FROM unit_definitions WHERE unit_code=?", (row["unit"],)).fetchone() else "legacy_unspecified"
            dimension = con.execute("SELECT quantity_dimension FROM unit_definitions WHERE unit_code=?", (unit,)).fetchone()[0]
            component_class = _component_class(row["component_group"], row["component_kind"], code)
            con.execute(
                "INSERT OR IGNORE INTO canonical_nutrients(canonical_nutrient_uid,canonical_code,display_name,component_class,canonical_unit,quantity_dimension,infoods_tag,notes,ontology_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (canonical_uid, code, row["display_name"], component_class, unit, dimension, INFOODS_TAGS.get(code), "Seeded from the legacy ontology; requires domain review before publication.", "provisional"),
            )
            con.execute(
                "INSERT OR IGNORE INTO nutrient_mappings(mapping_uid,source_system,source_nutrient_code,canonical_nutrient_uid,expression_unit,mapping_status,mapping_notes) VALUES (?,?,?,?,?,?,?)",
                (_uid("nmap"), "legacy_phd", row["nutrient_code"], canonical_uid, unit, "provisional", "Direct legacy column mapping; no value imputation or unit conversion applied."),
            )
            con.execute(
                "INSERT OR IGNORE INTO nutrient_aliases(alias_uid,canonical_nutrient_uid,alias_text,alias_scope) VALUES (?,?,?,?)",
                (_uid("nalias"), canonical_uid, row["display_name"], "legacy_display_name"),
            )

        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('platform_release','0.3.0')")
        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('validated_research','active_provenance_required')")
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES (2,'Collaborative project workspaces, memberships, plans and usage ledger')")
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES (3,'Validated nutrient ontology, provenance, food matching, recipes, yield and retention')")
        con.execute(f"PRAGMA user_version = {PLATFORM_SCHEMA_VERSION}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"research_database": str(target), "schema_version": PLATFORM_SCHEMA_VERSION, "default_project_uid": DEFAULT_PROJECT_UID, "platform_release": "0.3.0"}


def upgrade_platform_database(source_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Copy a v0.2 Research Core DB, migrate the copy, and preserve the source."""
    source = Path(source_db).resolve()
    target = Path(target_db).resolve()
    if source == target:
        raise ValueError("Source and target must differ; v0.3 upgrades are copy-first")
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_before = source.read_bytes()
    shutil.copy2(source, target)
    try:
        result = apply_v03_schema(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if source.read_bytes() != source_before:
        target.unlink(missing_ok=True)
        raise RuntimeError("Source Research Core database changed during copy-first upgrade")
    result["source_unchanged"] = True
    return result


def initialize_v03_research_database(source_legacy_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Create the latest platform directly from an immutable legacy snapshot."""
    from .research_core import initialize_research_database

    target = Path(target_db).resolve()
    base = initialize_research_database(source_legacy_db, target)
    try:
        result = apply_v03_schema(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {**base, **result, "source_unchanged": True}
