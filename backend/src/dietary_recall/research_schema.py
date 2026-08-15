"""Normalized research-core schema for the v0.2 Research Data Platform.

The schema is additive: it never modifies the legacy Nutrients.db evidence.
Every mutable research entity has a stable UUID, version counter and audit
trail.  Legacy numeric IDs are retained only as compatibility/provenance keys.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_foods (
    food_uid TEXT PRIMARY KEY,
    legacy_food_id INTEGER UNIQUE,
    food_name TEXT NOT NULL,
    description TEXT,
    preparation_method TEXT,
    category TEXT,
    source_scope TEXT NOT NULL DEFAULT 'research_core',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS research_foods_name_idx ON research_foods(food_name);

CREATE TABLE IF NOT EXISTS nutrient_definitions (
    nutrient_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    unit TEXT NOT NULL,
    component_group TEXT NOT NULL,
    component_kind TEXT NOT NULL CHECK(component_kind IN ('nutrient','toxicant')),
    legacy_table TEXT,
    legacy_column TEXT,
    scientific_status TEXT NOT NULL DEFAULT 'legacy_definition',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    UNIQUE(legacy_table, legacy_column)
);

CREATE TABLE IF NOT EXISTS food_component_values (
    component_value_uid TEXT PRIMARY KEY,
    food_uid TEXT NOT NULL REFERENCES research_foods(food_uid),
    nutrient_code TEXT NOT NULL REFERENCES nutrient_definitions(nutrient_code),
    value REAL,
    unit TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT 'per_100g',
    value_status TEXT NOT NULL DEFAULT 'reported',
    source_type TEXT NOT NULL DEFAULT 'research_core',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(food_uid, nutrient_code)
);
CREATE INDEX IF NOT EXISTS food_component_values_food_idx ON food_component_values(food_uid);
CREATE INDEX IF NOT EXISTS food_component_values_nutrient_idx ON food_component_values(nutrient_code);

CREATE TABLE IF NOT EXISTS participants (
    participant_uid TEXT PRIMARY KEY,
    participant_code TEXT NOT NULL UNIQUE,
    legacy_person_row_id INTEGER,
    legacy_usercode INTEGER,
    surname TEXT,
    firstname TEXT,
    age_years REAL,
    gender_code TEXT,
    activity_level TEXT,
    life_id INTEGER,
    weight_kg REAL,
    height_cm REAL,
    bmi_legacy REAL,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS participants_legacy_usercode_idx ON participants(legacy_usercode);
CREATE INDEX IF NOT EXISTS participants_name_idx ON participants(surname, firstname);

CREATE TABLE IF NOT EXISTS app_users (
    user_uid TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'researcher' CHECK(role IN ('admin','researcher','data_entry','analyst','viewer')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_uid TEXT PRIMARY KEY,
    experiment_code TEXT NOT NULL UNIQUE,
    food_uid TEXT REFERENCES research_foods(food_uid),
    sample_code TEXT,
    preparation_method TEXT,
    analytical_method TEXT,
    laboratory TEXT,
    experiment_date TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','in_progress','complete','curated','archived')),
    notes TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS experiments_food_idx ON experiments(food_uid);

CREATE TABLE IF NOT EXISTS experiment_results (
    result_uid TEXT PRIMARY KEY,
    experiment_uid TEXT NOT NULL REFERENCES experiments(experiment_uid) ON DELETE CASCADE,
    nutrient_code TEXT NOT NULL REFERENCES nutrient_definitions(nutrient_code),
    value REAL,
    unit TEXT NOT NULL,
    replicate_number INTEGER,
    lod REAL,
    loq REAL,
    uncertainty REAL,
    qc_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(qc_status IN ('unreviewed','accepted','flagged','rejected')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS experiment_results_experiment_idx ON experiment_results(experiment_uid);

CREATE TABLE IF NOT EXISTS recalls (
    recall_uid TEXT PRIMARY KEY,
    participant_uid TEXT NOT NULL REFERENCES participants(participant_uid),
    recall_date TEXT,
    day_name TEXT,
    meal_context TEXT,
    calculation_mode TEXT NOT NULL DEFAULT 'research_core_current',
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','complete','archived')),
    notes TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS recalls_participant_idx ON recalls(participant_uid);

CREATE TABLE IF NOT EXISTS recall_items (
    recall_item_uid TEXT PRIMARY KEY,
    recall_uid TEXT NOT NULL REFERENCES recalls(recall_uid) ON DELETE CASCADE,
    item_order INTEGER NOT NULL,
    food_uid TEXT NOT NULL REFERENCES research_foods(food_uid),
    amount_g REAL NOT NULL CHECK(amount_g >= 0),
    meal_label TEXT,
    notes TEXT,
    UNIQUE(recall_uid, item_order)
);

CREATE TABLE IF NOT EXISTS recall_results (
    recall_uid TEXT NOT NULL REFERENCES recalls(recall_uid) ON DELETE CASCADE,
    nutrient_code TEXT NOT NULL REFERENCES nutrient_definitions(nutrient_code),
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    calculation_mode TEXT NOT NULL,
    composition_snapshot_hash TEXT NOT NULL,
    PRIMARY KEY(recall_uid, nutrient_code)
);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_uid TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_format TEXT NOT NULL,
    sheet_name TEXT,
    conflict_policy TEXT NOT NULL DEFAULT 'reject' CHECK(conflict_policy IN ('reject','insert_only','upsert')),
    status TEXT NOT NULL DEFAULT 'staged' CHECK(status IN ('staged','committed','rejected','failed')),
    row_count INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    invalid_count INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS import_rows (
    import_row_uid TEXT PRIMARY KEY,
    batch_uid TEXT NOT NULL REFERENCES import_batches(batch_uid) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    normalized_json TEXT,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('valid','invalid')),
    errors_json TEXT NOT NULL DEFAULT '[]',
    commit_action TEXT,
    committed_entity_uid TEXT,
    UNIQUE(batch_uid, row_number)
);

CREATE TABLE IF NOT EXISTS entity_versions (
    version_uid TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_uid TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_uid, version_number)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_uid TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_uid TEXT,
    actor TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'application',
    import_batch_uid TEXT,
    before_json TEXT,
    after_json TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS audit_log_created_idx ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx ON audit_log(entity_type, entity_uid);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""


def initialize_schema(path: str | Path) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target)
    try:
        con.executescript(SCHEMA_SQL)
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, description) VALUES (?, ?)",
            (SCHEMA_VERSION, "Initial normalized Research Data Platform schema"),
        )
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    finally:
        con.close()

