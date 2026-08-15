"""Copy-first v0.4 deployment, governance and analytics schema.

The historical Java SQLite database is never a valid input to the low-level
schema function. Public helpers either copy a v0.3 platform or initialize a
new v0.3 platform from the immutable legacy snapshot before applying v0.4.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .v03_schema import (
    DEFAULT_PROJECT_UID,
    apply_v03_schema,
    initialize_v03_research_database,
)


PLATFORM_SCHEMA_VERSION = 4


V04_SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS billing_price_mappings (
    price_mapping_uid TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK(provider IN ('stripe','paystack','manual')),
    external_price_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    plan_code TEXT NOT NULL DEFAULT 'paid' REFERENCES plan_definitions(plan_code),
    import_rows_limit INTEGER NOT NULL CHECK(import_rows_limit > 100),
    calculation_runs_limit INTEGER NOT NULL CHECK(calculation_runs_limit > 100),
    amount_minor INTEGER CHECK(amount_minor IS NULL OR amount_minor > 0),
    currency TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, external_price_id)
);

CREATE TABLE IF NOT EXISTS billing_checkout_sessions (
    checkout_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    price_mapping_uid TEXT NOT NULL REFERENCES billing_price_mappings(price_mapping_uid),
    provider TEXT NOT NULL,
    purchaser_email TEXT NOT NULL,
    external_checkout_id TEXT,
    external_customer_id TEXT,
    status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','redirected','completed','expired','failed')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    billing_subscription_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    price_mapping_uid TEXT NOT NULL REFERENCES billing_price_mappings(price_mapping_uid),
    provider TEXT NOT NULL CHECK(provider IN ('stripe','paystack','manual')),
    external_subscription_id TEXT NOT NULL,
    external_customer_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('trialing','active','past_due','paused','canceled','expired')),
    current_period_end TEXT,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0 CHECK(cancel_at_period_end IN (0,1)),
    provider_payload_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, external_subscription_id)
);
CREATE INDEX IF NOT EXISTS billing_subscriptions_project_idx ON billing_subscriptions(project_uid,status);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
    billing_event_uid TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK(provider IN ('stripe','paystack')),
    external_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    signature_verified INTEGER NOT NULL CHECK(signature_verified=1),
    processing_status TEXT NOT NULL CHECK(processing_status IN ('processed','ignored','failed')),
    detail_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, external_event_id)
);

CREATE TRIGGER IF NOT EXISTS billing_webhook_events_no_update
BEFORE UPDATE ON billing_webhook_events BEGIN
  SELECT RAISE(ABORT, 'billing_webhook_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS billing_webhook_events_no_delete
BEFORE DELETE ON billing_webhook_events BEGIN
  SELECT RAISE(ABORT, 'billing_webhook_events is append-only');
END;

CREATE TABLE IF NOT EXISTS specialist_profiles (
    specialist_uid TEXT PRIMARY KEY,
    user_uid TEXT NOT NULL UNIQUE REFERENCES app_users(user_uid),
    specialization TEXT NOT NULL CHECK(specialization IN ('food_composition','nutritional_biochemistry','dietary_assessment','analytical_chemistry')),
    credentials TEXT NOT NULL,
    organization TEXT,
    country_code TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified','suspended','expired')),
    verified_by TEXT,
    verified_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scientific_review_cases (
    review_case_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN ('canonical_nutrient','nutrient_mapping','validated_value','retention_factor','source_release','matching_model')),
    target_uid TEXT NOT NULL,
    proposed_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','withdrawn')),
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    UNIQUE(project_uid,target_type,target_uid,status)
);
CREATE INDEX IF NOT EXISTS scientific_review_queue_idx ON scientific_review_cases(project_uid,status,target_type);

CREATE TABLE IF NOT EXISTS scientific_review_decisions (
    decision_uid TEXT PRIMARY KEY,
    review_case_uid TEXT NOT NULL UNIQUE REFERENCES scientific_review_cases(review_case_uid) ON DELETE CASCADE,
    specialist_uid TEXT NOT NULL REFERENCES specialist_profiles(specialist_uid),
    decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
    rationale TEXT NOT NULL,
    evidence_checked_json TEXT NOT NULL DEFAULT '{}',
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS scientific_review_decisions_no_update
BEFORE UPDATE ON scientific_review_decisions BEGIN
  SELECT RAISE(ABORT, 'scientific_review_decisions is append-only');
END;
CREATE TRIGGER IF NOT EXISTS scientific_review_decisions_no_delete
BEFORE DELETE ON scientific_review_decisions BEGIN
  SELECT RAISE(ABORT, 'scientific_review_decisions is append-only');
END;

CREATE TABLE IF NOT EXISTS source_license_acceptances (
    license_acceptance_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    source_uid TEXT NOT NULL REFERENCES data_sources(source_uid),
    license_name TEXT NOT NULL,
    license_url TEXT,
    license_text_sha256 TEXT NOT NULL,
    permitted_use TEXT NOT NULL CHECK(permitted_use IN ('research_only','noncommercial','commercial','internal_only','custom')),
    redistribution_permitted INTEGER NOT NULL CHECK(redistribution_permitted IN (0,1)),
    expires_at TEXT,
    accepted_by TEXT NOT NULL,
    accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    UNIQUE(project_uid,source_uid,license_text_sha256)
);

CREATE TABLE IF NOT EXISTS dataset_packages (
    dataset_package_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    source_release_uid TEXT NOT NULL REFERENCES source_releases(source_release_uid),
    license_acceptance_uid TEXT NOT NULL REFERENCES source_license_acceptances(license_acceptance_uid),
    dataset_code TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    valid_count INTEGER NOT NULL,
    invalid_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged' CHECK(status IN ('staged','committed','rejected')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    committed_at TEXT,
    UNIQUE(project_uid,dataset_code,package_sha256)
);

CREATE TABLE IF NOT EXISTS dataset_package_rows (
    dataset_package_row_uid TEXT PRIMARY KEY,
    dataset_package_uid TEXT NOT NULL REFERENCES dataset_packages(dataset_package_uid) ON DELETE CASCADE,
    file_order INTEGER NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('external_foods','external_components','retention_factors')),
    source_path TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    normalized_json TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('valid','invalid')),
    errors_json TEXT NOT NULL DEFAULT '[]',
    committed_entity_uid TEXT,
    UNIQUE(dataset_package_uid,source_path,row_number)
);

CREATE TABLE IF NOT EXISTS cohort_analysis_runs (
    analysis_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    analysis_mode TEXT NOT NULL CHECK(analysis_mode IN ('recall_day','participant_recorded_mean')),
    group_by TEXT NOT NULL CHECK(group_by IN ('overall','gender_code','life_id','activity_level')),
    minimum_group_size INTEGER NOT NULL CHECK(minimum_group_size >= 5),
    minimum_days INTEGER NOT NULL CHECK(minimum_days >= 1),
    status TEXT NOT NULL CHECK(status IN ('complete','exploratory')),
    config_json TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    excluded_count INTEGER NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cohort_metric_results (
    analysis_uid TEXT NOT NULL REFERENCES cohort_analysis_runs(analysis_uid) ON DELETE CASCADE,
    group_key TEXT NOT NULL,
    canonical_nutrient_uid TEXT NOT NULL REFERENCES canonical_nutrients(canonical_nutrient_uid),
    observation_count INTEGER NOT NULL,
    suppressed INTEGER NOT NULL CHECK(suppressed IN (0,1)),
    mean_value REAL,
    sample_sd REAL,
    minimum_value REAL,
    q1_value REAL,
    median_value REAL,
    q3_value REAL,
    maximum_value REAL,
    unit TEXT NOT NULL REFERENCES unit_definitions(unit_code),
    PRIMARY KEY(analysis_uid,group_key,canonical_nutrient_uid)
);

CREATE TABLE IF NOT EXISTS anomaly_detection_runs (
    anomaly_run_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    source_entity TEXT NOT NULL CHECK(source_entity IN ('experiment_result','recall_day')),
    method TEXT NOT NULL DEFAULT 'median_mad_v1',
    threshold REAL NOT NULL CHECK(threshold > 0),
    input_snapshot_hash TEXT NOT NULL,
    evaluated_count INTEGER NOT NULL,
    flag_count INTEGER NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS anomaly_flags (
    anomaly_flag_uid TEXT PRIMARY KEY,
    anomaly_run_uid TEXT NOT NULL REFERENCES anomaly_detection_runs(anomaly_run_uid) ON DELETE CASCADE,
    source_record_uid TEXT NOT NULL,
    canonical_nutrient_uid TEXT REFERENCES canonical_nutrients(canonical_nutrient_uid),
    legacy_nutrient_code TEXT,
    observed_value REAL NOT NULL,
    unit TEXT NOT NULL,
    robust_z_score REAL NOT NULL,
    flag_reason TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(review_status IN ('unreviewed','confirmed','dismissed')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT
);

CREATE TABLE IF NOT EXISTS matching_models (
    matching_model_uid TEXT PRIMARY KEY,
    project_uid TEXT NOT NULL REFERENCES research_projects(project_uid) ON DELETE CASCADE,
    model_type TEXT NOT NULL DEFAULT 'logistic_triage_v1',
    feature_schema_json TEXT NOT NULL,
    coefficients_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    training_snapshot_hash TEXT NOT NULL,
    training_count INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'candidate' CHECK(review_status IN ('candidate','approved','rejected','retired')),
    trained_by TEXT NOT NULL,
    reviewed_by TEXT,
    review_rationale TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT
);
"""


def _add_column(con: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_v04_schema(path: str | Path) -> dict[str, Any]:
    """Apply v0.4 only to an existing v0.3 Research Platform database."""
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        mode = con.execute("SELECT value FROM research_meta WHERE key='platform_mode'").fetchone()
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        if not mode or mode[0] != "research_core" or version < 3:
            raise ValueError("v0.4 migration requires a v0.3 Research Platform database")
        con.executescript(V04_SCHEMA_SQL)
        _add_column(con, "project_subscriptions", "fallback_plan_code TEXT REFERENCES plan_definitions(plan_code)")
        _add_column(con, "project_subscriptions", "billing_managed INTEGER NOT NULL DEFAULT 0 CHECK(billing_managed IN (0,1))")
        _add_column(con, "food_match_candidates", "triage_model_uid TEXT REFERENCES matching_models(matching_model_uid)")
        _add_column(con, "food_match_candidates", "triage_score REAL CHECK(triage_score IS NULL OR (triage_score >= 0 AND triage_score <= 1))")
        _add_column(con, "source_releases", "scientific_review_status TEXT NOT NULL DEFAULT 'provisional' CHECK(scientific_review_status IN ('provisional','reviewed','rejected'))")
        _add_column(con, "retention_factors", "scientific_review_status TEXT NOT NULL DEFAULT 'provisional' CHECK(scientific_review_status IN ('provisional','reviewed','rejected'))")
        con.execute(
            "UPDATE project_subscriptions SET fallback_plan_code=CASE WHEN plan_code='paid' THEN 'independent' ELSE plan_code END WHERE fallback_plan_code IS NULL"
        )
        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('platform_release','0.4.0')")
        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('production_authentication','external_credential_store_required')")
        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('licensed_ingestion','manifest_and_acceptance_required')")
        con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('analytics_policy','descriptive_first_ml_review_only')")
        con.execute(
            "INSERT OR IGNORE INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,source_url,citation,license_notes,source_status) "
            "VALUES ('src_nigeria_fdb_2019',NULL,'NIGERIA-FDB-2019','Nigeria Food Database 2019','food_composition_table','University of Ibadan','https://nigeriafooddata.ui.edu.ng/','Nigeria Food Database 2019','Reference metadata only. Content must be ingested under a documented licence acceptance.','reference_only')"
        )
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,description) VALUES (4,'Production boundary, billing, specialist review, licensed datasets, cohort EDA and governed ML')"
        )
        con.execute(f"PRAGMA user_version = {PLATFORM_SCHEMA_VERSION}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {
        "research_database": str(target),
        "schema_version": PLATFORM_SCHEMA_VERSION,
        "platform_release": "0.4.0",
        "default_project_uid": DEFAULT_PROJECT_UID,
    }


def upgrade_v03_to_v04(source_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Copy a v0.3 database and apply v0.4 while preserving the source."""
    source = Path(source_db).resolve()
    target = Path(target_db).resolve()
    if source == target:
        raise ValueError("Source and target must differ; upgrades are copy-first")
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    before = _sha256(source)
    shutil.copy2(source, target)
    try:
        result = apply_v04_schema(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    after = _sha256(source)
    if before != after:
        target.unlink(missing_ok=True)
        raise RuntimeError("Source v0.3 database changed during upgrade")
    return {**result, "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": True}


def initialize_v04_research_database(source_legacy_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Create the latest platform directly from an immutable legacy snapshot."""
    result = initialize_v03_research_database(source_legacy_db, target_db)
    try:
        v04 = apply_v04_schema(target_db)
    except Exception:
        Path(target_db).resolve().unlink(missing_ok=True)
        raise
    return {**result, **v04, "source_unchanged": True}


def initialize_v04_from_v02(source_v02_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Copy a v0.2 platform, apply v0.3, then v0.4 without touching v0.2."""
    source = Path(source_v02_db).resolve()
    target = Path(target_db).resolve()
    if source == target or target.exists():
        raise ValueError("Target must be a new database path")
    before = _sha256(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    try:
        apply_v03_schema(target)
        result = apply_v04_schema(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    after = _sha256(source)
    if before != after:
        target.unlink(missing_ok=True)
        raise RuntimeError("Source v0.2 database changed during upgrade")
    return {**result, "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": True}
