"""Raw-fidelity and normalized research CSV export."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .audit import snapshot_audit
from .constants import COMPOSITION_TABLES, DRI_FIELDS, TABLE_FIELDS
from .db import LegacyRepository, readonly, sha256_file
from .serialization import BlobEvidence, blob_inventory
from .validation import parity_summary, validate_decoded_daily


def _csv_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(value)).decode("ascii")
    return value


def _write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _unit_from_column(column: str) -> str:
    suffixes = (
        ("_kcal", "kcal"),
        ("_mcg", "mcg"),
        ("_mg", "mg"),
        ("_IU", "IU"),
        ("_RAE", "RAE"),
        ("_RE", "RE"),
        ("_g", "g"),
    )
    for suffix, unit in suffixes:
        if column.endswith(suffix) or column.endswith(suffix + "_" + unit):
            return unit
    return "legacy_unspecified"


def _participant_key(source_sha: str, row: Mapping[str, Any]) -> str:
    # Daily_Records.Person_ID is the Java application's Person.Usercode business
    # key, not the physical SQLite Person.id row key.
    token = f"{source_sha}:{row.get('Usercode')}".encode("utf-8")
    return "P-" + hashlib.sha256(token).hexdigest()[:16]


def export_raw_tables(db_path: str | Path, output_dir: str | Path) -> list[Path]:
    """Export every legacy table; BLOBs use reversible base64.

    The Users.Password field is redacted in CSV. The immutable SQLite source is
    the evidence copy for credential values, which are not needed for research.
    """
    out = Path(output_dir)
    written: list[Path] = []
    with readonly(db_path) as con:
        tables = [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            rows = list(con.execute(f'SELECT * FROM "{table}"'))
            columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
            path = out / f"{table}.csv"
            converted: list[dict[str, Any]] = []
            for sqlite_row in rows:
                row = dict(sqlite_row)
                if table == "Users" and "Password" in row:
                    row["Password"] = "[REDACTED_FROM_EXPORT]"
                converted.append(row)
            _write_csv(path, columns, converted)
            written.append(path)
    return written


def export_normalized(
    db_path: str | Path,
    output_dir: str | Path,
    decoded_records: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Export analysis-friendly, long-form CSV while preserving SQL NULL as blank."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    repo = LegacyRepository(db_path)
    source_sha = sha256_file(db_path)
    written: list[Path] = []

    with readonly(db_path) as con:
        foods = [dict(row) for row in con.execute("SELECT * FROM Food ORDER BY Food_ID")]
        path = out / "food.csv"
        _write_csv(path, ("Food_ID", "Food_Name", "Food_Weight", "Food_Description"), foods)
        written.append(path)

        definitions: list[dict[str, Any]] = []
        values: list[dict[str, Any]] = []
        food_ids = {row["Food_ID"] for row in foods}
        for table in COMPOSITION_TABLES:
            kind = "toxicant" if table == "Toxicants" else "nutrient"
            for column in TABLE_FIELDS[table]:
                definitions.append(
                    {
                        "component_kind": kind,
                        "source_table": table,
                        "legacy_column": column,
                        "unit": _unit_from_column(column),
                    }
                )
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY Food_ID'):
                for column in TABLE_FIELDS[table]:
                    values.append(
                        {
                            "Food_ID": row["Food_ID"],
                            "Food_Name": row["Food_Name"],
                            "component_kind": kind,
                            "source_table": table,
                            "legacy_column": column,
                            "unit": _unit_from_column(column),
                            "value_per_100g": row[column],
                            "value_status": "missing" if row[column] is None else "reported",
                            "matches_Food_table": int(row["Food_ID"] in food_ids),
                        }
                    )
        path = out / "nutrient_definition.csv"
        _write_csv(path, definitions[0].keys(), definitions)
        written.append(path)
        path = out / "food_component_value_long.csv"
        _write_csv(path, values[0].keys(), values)
        written.append(path)

        stages = [dict(row) for row in con.execute("SELECT * FROM Life_Stage ORDER BY Stage_ID, Life_ID")]
        path = out / "life_stage.csv"
        _write_csv(path, ("Stage_ID", "Life_ID", "Stagename", "Livename"), stages)
        written.append(path)

        ref_rows: list[dict[str, Any]] = []
        for table, fields in DRI_FIELDS.items():
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY Stage_ID, Life_ID'):
                for column in fields:
                    ref_rows.append(
                        {
                            "Stage_ID": row["Stage_ID"],
                            "Life_ID": row["Life_ID"],
                            "source_table": table,
                            "legacy_column": column,
                            "unit": _unit_from_column(column),
                            "reference_value": row[column],
                            "value_status": "missing" if row[column] is None else "reported",
                        }
                    )
        path = out / "reference_intake_long.csv"
        _write_csv(path, ref_rows[0].keys(), ref_rows)
        written.append(path)

        people = [dict(row) for row in con.execute("SELECT * FROM Person ORDER BY id")]
        participant_rows: list[dict[str, Any]] = []
        participant_keys: dict[int, str] = {}
        for row in people:
            key = _participant_key(source_sha, row)
            participant_keys[int(row["Usercode"])] = key
            participant_rows.append(
                {
                    "participant_key": key,
                    "legacy_person_row_id": row["id"],
                    "legacy_person_id": row["Usercode"],
                    "Age": row["Age"],
                    "Weight_KG": row["Weight_KG"],
                    "Height_CM": row["Height_CM"],
                    "BMI": row["BMI"],
                    "Activity_Level": row["Activity_Level"],
                    "Gender": row["Gender"],
                    "Life_ID": row["Life_ID"],
                    "Unit": row["Unit"],
                }
            )
        path = out / "participant_deidentified.csv"
        _write_csv(path, participant_rows[0].keys(), participant_rows)
        written.append(path)

    inventory = [asdict(item) for item in blob_inventory(db_path)]
    path = out / "serialized_blob_inventory.csv"
    _write_csv(path, inventory[0].keys(), inventory)
    written.append(path)

    if decoded_records is not None:
        path = out / "decoded_java_objects.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in decoded_records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        written.append(path)
        written.extend(_export_decoded_daily(out, decoded_records, participant_keys))

    return written


def _export_decoded_daily(
    out: Path,
    decoded_records: list[dict[str, Any]],
    participant_keys: Mapping[int, str],
) -> list[Path]:
    recall_rows: list[dict[str, Any]] = []
    vector_rows: list[dict[str, Any]] = []
    for record in decoded_records:
        if record.get("kind") != "daily_record" or record.get("status") != "decoded":
            continue
        obj = record.get("object", {})
        db_person_id = int(record.get("db_person_id", obj.get("person_id", -1)))
        key = participant_keys.get(db_person_id, f"legacy-person-{db_person_id}")
        ids = obj.get("food_id") or []
        names = obj.get("food_name") or []
        eaten = obj.get("eaten_food_weight") or []
        real = obj.get("real_food_weight") or []
        recommended = obj.get("recommended_food_weight") or []
        width = max(len(ids), len(names), len(eaten), len(real), len(recommended), 0)
        for index in range(width):
            recall_rows.append(
                {
                    "participant_key": key,
                    "legacy_person_id": db_person_id,
                    "day_column": record.get("column"),
                    "item_index": index,
                    "Food_ID": ids[index] if index < len(ids) else None,
                    "Food_Name": names[index] if index < len(names) else None,
                    "eaten_food_weight_g": eaten[index] if index < len(eaten) else None,
                    "real_food_weight_g": real[index] if index < len(real) else None,
                    "recommended_food_weight_g": recommended[index] if index < len(recommended) else None,
                }
            )
        for vector_name, values in obj.items():
            if not vector_name.startswith("total_eaten_") and not vector_name.startswith("recommended_"):
                continue
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                vector_rows.append(
                    {
                        "participant_key": key,
                        "legacy_person_id": db_person_id,
                        "day_column": record.get("column"),
                        "legacy_vector": vector_name,
                        "vector_index": index,
                        "value": value,
                    }
                )
    written: list[Path] = []
    recall_path = out / "recall_item_decoded.csv"
    recall_fields = (
        "participant_key",
        "legacy_person_id",
        "day_column",
        "item_index",
        "Food_ID",
        "Food_Name",
        "eaten_food_weight_g",
        "real_food_weight_g",
        "recommended_food_weight_g",
    )
    _write_csv(recall_path, recall_fields, recall_rows)
    written.append(recall_path)
    vector_path = out / "legacy_daily_vector_long.csv"
    vector_fields = (
        "participant_key",
        "legacy_person_id",
        "day_column",
        "legacy_vector",
        "vector_index",
        "value",
    )
    _write_csv(vector_path, vector_fields, vector_rows)
    written.append(vector_path)
    return written


def export_everything(
    db_path: str | Path,
    output_dir: str | Path,
    decoded_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    before = sha256_file(db_path)
    raw = export_raw_tables(db_path, output / "raw_tables")
    normalized = export_normalized(db_path, output / "normalized", decoded_records=decoded_records)
    parity_files: list[Path] = []
    if decoded_records is not None:
        parity = validate_decoded_daily(LegacyRepository(db_path), decoded_records)
        parity_csv = output / "legacy_parity_validation.csv"
        parity_rows = [
            {
                "row_id": row.row_id,
                "person_id": row.person_id,
                "day": row.day,
                "status": row.status,
                "item_count": row.item_count,
                "compared_values": row.compared_values,
                "mismatched_values": row.mismatched_values,
                "max_abs_difference": row.max_abs_difference,
                "detail": row.detail,
            }
            for row in parity
        ]
        _write_csv(parity_csv, parity_rows[0].keys(), parity_rows)
        parity_json = output / "legacy_parity_summary.json"
        with parity_json.open("w", encoding="utf-8") as handle:
            json.dump(parity_summary(parity), handle, indent=2)
        parity_files = [parity_csv, parity_json]
    audit = snapshot_audit(db_path)
    audit_path = output / "snapshot_audit.json"
    with audit_path.open("w", encoding="utf-8") as handle:
        json.dump(audit, handle, indent=2, ensure_ascii=False)
    after = sha256_file(db_path)
    if before != after:
        raise RuntimeError("Source database checksum changed during export")
    manifest = {
        "source_database": str(Path(db_path).resolve()),
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": before == after,
        "raw_files": [str(path.relative_to(output)) for path in raw],
        "normalized_files": [str(path.relative_to(output)) for path in normalized],
        "parity_files": [str(path.relative_to(output)) for path in parity_files],
        "audit_file": str(audit_path.relative_to(output)),
        "raw_null_policy": "SQL NULL -> blank CSV field",
        "raw_blob_policy": "BLOB -> base64:<payload>",
        "users_password_policy": "redacted in CSV; immutable SQLite is canonical evidence",
        "compatibility_mode": "legacy_compatibility",
        "validated_research_layer": "separate v0.3+ platform database; not mixed into this immutable legacy export",
    }
    manifest_path = output / "export_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest
