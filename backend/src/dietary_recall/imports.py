"""Reproducible CSV/XLSX staging, validation and atomic batch commit."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree as ET

from .research_core import ResearchRepository, _json, _opt_float, _opt_int, _uid


ENTITY_HEADERS: dict[str, list[str]] = {
    "foods": ["legacy_food_id", "food_name", "description", "preparation_method", "category", "source_scope"],
    "participants": ["participant_code", "legacy_usercode", "surname", "firstname", "age_years", "gender_code", "activity_level", "life_id", "weight_kg", "height_cm", "notes"],
    "app_users": ["email", "display_name", "role"],
    "experiments": ["experiment_code", "legacy_food_id", "food_name", "sample_code", "preparation_method", "analytical_method", "laboratory", "experiment_date", "status", "notes"],
    "components": ["legacy_food_id", "food_name", "nutrient_code", "legacy_column", "value", "unit", "value_status", "source_type", "provenance"],
    "experiment_results": ["experiment_code", "nutrient_code", "legacy_column", "value", "unit", "replicate_number", "lod", "loq", "uncertainty", "qc_status", "notes"],
    "data_sources": ["source_code", "source_name", "source_type", "publisher", "source_url", "citation", "license_notes"],
    "source_releases": ["source_uid", "source_code", "release_label", "release_date", "retrieved_at", "sha256", "schema_notes"],
    "external_foods": ["source_release_uid", "source_code", "release_label", "source_food_code", "food_name", "local_name", "scientific_name", "food_group", "country_code", "preparation_state", "edible_portion_percent", "provenance"],
    "external_components": ["external_food_uid", "source_food_code", "source_release_uid", "source_code", "release_label", "canonical_nutrient_uid", "canonical_code", "infoods_tag", "value", "unit", "basis", "value_status", "analytical_method", "uncertainty", "provenance"],
    "validated_components": ["food_uid", "legacy_food_id", "food_name", "canonical_nutrient_uid", "canonical_code", "infoods_tag", "value", "unit", "basis", "evidence_class", "validation_status", "source_release_uid", "source_code", "release_label", "source_record_uid", "provenance"],
    "retention_factors": ["source_release_uid", "source_code", "release_label", "cooking_method_code", "food_group", "canonical_nutrient_uid", "canonical_code", "infoods_tag", "retention_fraction", "factor_status", "notes"],
}

SHEET_NAMES = {
    "foods": "Foods",
    "participants": "Participants",
    "app_users": "App Users",
    "experiments": "Experiments",
    "components": "Food Components",
    "experiment_results": "Experiment Results",
    "data_sources": "Data Sources",
    "source_releases": "Source Releases",
    "external_foods": "External Foods",
    "external_components": "External Components",
    "validated_components": "Validated Components",
    "retention_factors": "Retention Factors",
}


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _normalize_header(key): (value.strip() if isinstance(value, str) else value)
        for key, value in row.items()
        if key is not None and _normalize_header(key)
    }


def read_csv_rows(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    return [_clean_row(row) for row in reader if any(value not in (None, "") for value in row.values())]


def _xlsx_column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def read_xlsx_rows(payload: bytes, sheet_name: str | None = None) -> list[dict[str, Any]]:
    """Read a flat .xlsx sheet using only the Python standard library.

    The importer intentionally reads values only; workbook formulas/styles are
    never treated as research provenance. Formula cells use the cached value
    stored by Excel when present.
    """
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    rel_ns = {"p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships.findall("p:Relationship", rel_ns)}
        sheets = workbook.find("m:sheets", ns)
        if sheets is None or not list(sheets):
            return []
        chosen = None
        for sheet in list(sheets):
            if sheet_name and sheet.attrib.get("name", "").casefold() == sheet_name.casefold():
                chosen = sheet
                break
        if chosen is None:
            chosen = list(sheets)[0]
        rel_id = chosen.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets.get(rel_id or "")
        if not target:
            raise ValueError("Unable to resolve XLSX worksheet")
        sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target.lstrip("/")
        sheet_root = ET.fromstring(archive.read(sheet_path))

        matrix: list[list[Any]] = []
        for row in sheet_root.findall(".//m:sheetData/m:row", ns):
            values: dict[int, Any] = {}
            for cell in row.findall("m:c", ns):
                col = _xlsx_column_index(cell.attrib.get("r", "A1"))
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    node = cell.find("m:is", ns)
                    value: Any = "" if node is None else "".join(n.text or "" for n in node.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                else:
                    node = cell.find("m:v", ns)
                    raw = "" if node is None else (node.text or "")
                    if cell_type == "s" and raw:
                        value = shared[int(raw)]
                    elif cell_type == "b":
                        value = raw == "1"
                    elif cell_type in ("str", "e"):
                        value = raw
                    elif raw == "":
                        value = ""
                    else:
                        try:
                            number = float(raw)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            value = raw
                values[col] = value
            if values:
                width = max(values) + 1
                matrix.append([values.get(index, "") for index in range(width)])
        if not matrix:
            return []
        headers = [_normalize_header(value) for value in matrix[0]]
        rows: list[dict[str, Any]] = []
        for raw_row in matrix[1:]:
            row = {header: raw_row[index] if index < len(raw_row) else "" for index, header in enumerate(headers) if header}
            if any(value not in (None, "") for value in row.values()):
                rows.append(_clean_row(row))
        return rows


def parse_rows(payload: bytes, filename: str, sheet_name: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return "csv", read_csv_rows(payload)
    if suffix == ".xlsx":
        return "xlsx", read_xlsx_rows(payload, sheet_name)
    raise ValueError("Only .csv and .xlsx imports are supported")


def _validate(
    entity_type: str,
    row: Mapping[str, Any],
    con: sqlite3.Connection,
    project_uid: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    data = dict(row)
    errors: list[str] = []
    if entity_type == "foods":
        if not str(data.get("food_name") or "").strip():
            errors.append("food_name is required")
        try:
            if data.get("legacy_food_id") not in (None, ""):
                data["legacy_food_id"] = _opt_int(data.get("legacy_food_id"))
            else:
                # Blank IDs in CSV/XLSX mean "allocate the next stable ID".
                # Leaving an empty string here would make the first imported
                # row claim "" as a unique SQLite value and make the next row
                # look like a false conflict.
                data["legacy_food_id"] = None
        except ValueError:
            errors.append("legacy_food_id must be an integer")
    elif entity_type == "participants":
        for field in ("legacy_usercode", "life_id"):
            try:
                data[field] = _opt_int(data.get(field))
            except ValueError:
                errors.append(f"{field} must be an integer")
        for field in ("age_years", "weight_kg", "height_cm"):
            try:
                data[field] = _opt_float(data.get(field))
            except ValueError:
                errors.append(f"{field} must be numeric")
    elif entity_type == "app_users":
        if "@" not in str(data.get("email") or ""):
            errors.append("valid email is required")
        if not str(data.get("display_name") or "").strip():
            errors.append("display_name is required")
        if data.get("role") and data["role"] not in {"admin", "researcher", "data_entry", "analyst", "viewer"}:
            errors.append("role is not recognized")
    elif entity_type == "experiments":
        if not data.get("experiment_code"):
            errors.append("experiment_code is required for batch imports")
        food = _resolve_food(con, data, project_uid)
        if not food:
            errors.append("legacy_food_id or food_name must resolve to an existing food")
        else:
            data["food_uid"] = food
        if data.get("status") and data["status"] not in {"draft", "in_progress", "complete", "curated", "archived"}:
            errors.append("status is not recognized")
    elif entity_type == "components":
        food = _resolve_food(con, data, project_uid)
        if not food:
            errors.append("food cannot be resolved")
        else:
            data["food_uid"] = food
        nutrient = _resolve_nutrient(con, data)
        if not nutrient:
            errors.append("nutrient_code or legacy_column is not recognized")
        else:
            data["nutrient_code"] = nutrient
        try:
            data["value"] = _opt_float(data.get("value"))
        except ValueError:
            errors.append("value must be numeric or blank")
    elif entity_type == "experiment_results":
        experiment = con.execute(
            "SELECT e.experiment_uid FROM experiments e JOIN project_records pr ON pr.entity_type='experiment' AND pr.entity_uid=e.experiment_uid WHERE e.experiment_code=? AND pr.project_uid=?",
            (data.get("experiment_code"), project_uid),
        ).fetchone() if project_uid else con.execute("SELECT experiment_uid FROM experiments WHERE experiment_code=?", (data.get("experiment_code"),)).fetchone()
        if not experiment:
            errors.append("experiment_code does not exist")
        else:
            data["experiment_uid"] = experiment[0]
        nutrient = _resolve_nutrient(con, data)
        if not nutrient:
            errors.append("nutrient_code or legacy_column is not recognized")
        else:
            data["nutrient_code"] = nutrient
        for field in ("value", "lod", "loq", "uncertainty"):
            try:
                data[field] = _opt_float(data.get(field))
            except ValueError:
                errors.append(f"{field} must be numeric or blank")
        try:
            data["replicate_number"] = _opt_int(data.get("replicate_number"))
        except ValueError:
            errors.append("replicate_number must be an integer")
    elif entity_type == "data_sources":
        if not str(data.get("source_code") or "").strip():
            errors.append("source_code is required")
        if not str(data.get("source_name") or "").strip():
            errors.append("source_name is required")
        if (data.get("source_type") or "manual") not in {"study_analysis", "regional_table", "food_composition_table", "retention_table", "literature", "manual"}:
            errors.append("source_type is not recognized")
    elif entity_type == "source_releases":
        source_uid = _resolve_source(con, data, project_uid)
        if not source_uid:
            errors.append("source_uid or source_code must resolve to a project/global source")
        else:
            data["source_uid"] = source_uid
        if not str(data.get("release_label") or "").strip():
            errors.append("release_label is required")
        sha256 = str(data.get("sha256") or "").strip()
        if sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            errors.append("sha256 must be a 64-character hexadecimal digest")
    elif entity_type == "external_foods":
        release_uid = _resolve_source_release(con, data, project_uid)
        if not release_uid:
            errors.append("source release cannot be resolved")
        else:
            data["source_release_uid"] = release_uid
        if not str(data.get("source_food_code") or "").strip():
            errors.append("source_food_code is required")
        if not str(data.get("food_name") or "").strip():
            errors.append("food_name is required")
        try:
            data["edible_portion_percent"] = _opt_float(data.get("edible_portion_percent"))
            if data["edible_portion_percent"] is not None and not 0 <= data["edible_portion_percent"] <= 100:
                errors.append("edible_portion_percent must be between 0 and 100")
        except ValueError:
            errors.append("edible_portion_percent must be numeric or blank")
    elif entity_type in {"external_components", "validated_components"}:
        nutrient_uid = _resolve_canonical_nutrient(con, data)
        if not nutrient_uid:
            errors.append("canonical nutrient cannot be resolved")
        else:
            data["canonical_nutrient_uid"] = nutrient_uid
        if entity_type == "external_components":
            external_food_uid = _resolve_external_food(con, data, project_uid)
            if not external_food_uid:
                errors.append("external food cannot be resolved")
            else:
                data["external_food_uid"] = external_food_uid
            allowed_statuses = {"reported", "trace", "missing", "estimated", "calculated"}
            if (data.get("value_status") or "reported") not in allowed_statuses:
                errors.append("value_status is not recognized")
        else:
            food_uid = _resolve_food(con, data, project_uid)
            if not food_uid:
                errors.append("research food cannot be resolved")
            else:
                data["food_uid"] = food_uid
            if (data.get("evidence_class") or "study_measured") not in {"study_measured", "nigerian_regional", "external_matched", "recipe_calculated", "transparent_imputation", "missing"}:
                errors.append("evidence_class is not recognized")
            if (data.get("validation_status") or "provisional") not in {"provisional", "reviewed", "validated", "rejected"}:
                errors.append("validation_status is not recognized")
            if any(data.get(field) not in (None, "") for field in ("source_release_uid", "source_code", "release_label")):
                release_uid = _resolve_source_release(con, data, project_uid)
                if not release_uid:
                    errors.append("source release cannot be resolved")
                else:
                    data["source_release_uid"] = release_uid
        for field in ("value", "uncertainty") if entity_type == "external_components" else ("value",):
            try:
                data[field] = _opt_float(data.get(field))
            except ValueError:
                errors.append(f"{field} must be numeric or blank")
        if nutrient_uid:
            unit = str(data.get("unit") or con.execute("SELECT canonical_unit FROM canonical_nutrients WHERE canonical_nutrient_uid=?", (nutrient_uid,)).fetchone()[0])
            data["unit"] = unit
            if not _unit_is_compatible(con, nutrient_uid, unit):
                errors.append("unit is unknown or incompatible with the canonical nutrient dimension")
    elif entity_type == "retention_factors":
        release_uid = _resolve_source_release(con, data, project_uid)
        nutrient_uid = _resolve_canonical_nutrient(con, data)
        if not release_uid:
            errors.append("source release cannot be resolved")
        else:
            data["source_release_uid"] = release_uid
        if not nutrient_uid:
            errors.append("canonical nutrient cannot be resolved")
        else:
            data["canonical_nutrient_uid"] = nutrient_uid
        if not str(data.get("cooking_method_code") or "").strip():
            errors.append("cooking_method_code is required")
        try:
            data["retention_fraction"] = float(data.get("retention_fraction"))
            if not 0 <= data["retention_fraction"] <= 1:
                errors.append("retention_fraction must be between 0 and 1")
        except (TypeError, ValueError):
            errors.append("retention_fraction must be numeric")
        if (data.get("factor_status") or "reported") not in {"analytical", "reported", "imputed_by_source", "project_assumption"}:
            errors.append("factor_status is not recognized")
    else:
        errors.append(f"unsupported entity_type {entity_type!r}")
    return data, errors


def _resolve_food(con: sqlite3.Connection, data: Mapping[str, Any], project_uid: str | None = None) -> str | None:
    project_join = ""
    project_where = ""
    project_params: list[Any] = []
    if project_uid:
        project_join = " JOIN project_records pr ON pr.entity_type='food' AND pr.entity_uid=f.food_uid"
        project_where = " AND pr.project_uid=?"
        project_params = [project_uid]
    if data.get("food_uid"):
        row = con.execute(
            f"SELECT f.food_uid FROM research_foods f{project_join} WHERE f.food_uid=?{project_where}",
            (data["food_uid"], *project_params),
        ).fetchone()
        return row[0] if row else None
    if data.get("legacy_food_id") not in (None, ""):
        try:
            legacy_id = int(float(data["legacy_food_id"]))
        except (TypeError, ValueError):
            return None
        row = con.execute(
            f"SELECT f.food_uid FROM research_foods f{project_join} WHERE f.legacy_food_id=?{project_where}",
            (legacy_id, *project_params),
        ).fetchone()
        if row:
            return row[0]
    if data.get("food_name"):
        row = con.execute(
            f"SELECT f.food_uid FROM research_foods f{project_join} WHERE lower(f.food_name)=lower(?){project_where} ORDER BY f.active DESC LIMIT 1",
            (str(data["food_name"]).strip(), *project_params),
        ).fetchone()
        return row[0] if row else None
    return None


def _resolve_nutrient(con: sqlite3.Connection, data: Mapping[str, Any]) -> str | None:
    if data.get("nutrient_code"):
        row = con.execute("SELECT nutrient_code FROM nutrient_definitions WHERE nutrient_code=?", (data["nutrient_code"],)).fetchone()
        if row:
            return row[0]
    if data.get("legacy_column"):
        row = con.execute("SELECT nutrient_code FROM nutrient_definitions WHERE legacy_column=?", (data["legacy_column"],)).fetchone()
        return row[0] if row else None
    return None


def _resolve_canonical_nutrient(con: sqlite3.Connection, data: Mapping[str, Any]) -> str | None:
    for field, column in (("canonical_nutrient_uid", "canonical_nutrient_uid"), ("canonical_code", "canonical_code"), ("infoods_tag", "infoods_tag")):
        value = str(data.get(field) or "").strip()
        if value:
            row = con.execute(f"SELECT canonical_nutrient_uid FROM canonical_nutrients WHERE {column}=? AND active=1", (value,)).fetchone()
            if row:
                return row[0]
    return None


def _resolve_source(con: sqlite3.Connection, data: Mapping[str, Any], project_uid: str | None) -> str | None:
    if data.get("source_uid"):
        row = con.execute("SELECT source_uid FROM data_sources WHERE source_uid=? AND (project_uid=? OR project_uid IS NULL)", (data["source_uid"], project_uid)).fetchone()
        if row:
            return row[0]
    if data.get("source_code"):
        row = con.execute(
            "SELECT source_uid FROM data_sources WHERE lower(source_code)=lower(?) AND (project_uid=? OR project_uid IS NULL) ORDER BY project_uid IS NULL LIMIT 1",
            (str(data["source_code"]).strip(), project_uid),
        ).fetchone()
        return row[0] if row else None
    return None


def _resolve_source_release(con: sqlite3.Connection, data: Mapping[str, Any], project_uid: str | None) -> str | None:
    if data.get("source_release_uid"):
        row = con.execute(
            "SELECT r.source_release_uid FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=? AND (s.project_uid=? OR s.project_uid IS NULL)",
            (data["source_release_uid"], project_uid),
        ).fetchone()
        if row:
            return row[0]
    source_uid = _resolve_source(con, data, project_uid)
    label = str(data.get("release_label") or "").strip()
    if source_uid and label:
        row = con.execute("SELECT source_release_uid FROM source_releases WHERE source_uid=? AND lower(release_label)=lower(?)", (source_uid, label)).fetchone()
        return row[0] if row else None
    return None


def _resolve_external_food(con: sqlite3.Connection, data: Mapping[str, Any], project_uid: str | None) -> str | None:
    if not project_uid:
        return None
    if data.get("external_food_uid"):
        row = con.execute("SELECT external_food_uid FROM external_foods WHERE external_food_uid=? AND project_uid=?", (data["external_food_uid"], project_uid)).fetchone()
        if row:
            return row[0]
    code = str(data.get("source_food_code") or "").strip()
    if not code:
        return None
    release_uid = _resolve_source_release(con, data, project_uid)
    if release_uid:
        row = con.execute("SELECT external_food_uid FROM external_foods WHERE project_uid=? AND source_release_uid=? AND source_food_code=?", (project_uid, release_uid, code)).fetchone()
    else:
        rows = list(con.execute("SELECT external_food_uid FROM external_foods WHERE project_uid=? AND source_food_code=?", (project_uid, code)))
        row = rows[0] if len(rows) == 1 else None
    return row[0] if row else None


def _unit_is_compatible(con: sqlite3.Connection, nutrient_uid: str, unit: str) -> bool:
    row = con.execute(
        "SELECT n.quantity_dimension nutrient_dimension,u.quantity_dimension unit_dimension FROM canonical_nutrients n JOIN unit_definitions u ON u.unit_code=? WHERE n.canonical_nutrient_uid=?",
        (unit, nutrient_uid),
    ).fetchone()
    return bool(row and row["nutrient_dimension"] == row["unit_dimension"])


def stage_import(
    repository: ResearchRepository,
    payload: bytes,
    filename: str,
    entity_type: str,
    *,
    sheet_name: str | None = None,
    conflict_policy: str = "reject",
    actor: str = "local-researcher",
    project_uid: str | None = None,
) -> dict[str, Any]:
    if entity_type not in ENTITY_HEADERS:
        raise ValueError(f"Unsupported entity_type {entity_type!r}")
    if conflict_policy not in {"reject", "insert_only", "upsert"}:
        raise ValueError("conflict_policy must be reject, insert_only or upsert")
    if project_uid is None and hasattr(repository, "default_project_uid"):
        project_uid = repository.default_project_uid()
    source_format, rows = parse_rows(payload, filename, sheet_name or (SHEET_NAMES.get(entity_type) if Path(filename).suffix.lower() == ".xlsx" else None))
    if not rows:
        raise ValueError("Import contains no data rows")
    batch_uid = _uid("imp")
    source_sha = hashlib.sha256(payload).hexdigest()
    with repository.connect() as con:
        if project_uid and hasattr(repository, "_membership"):
            roles = {"owner", "admin", "analyst"} if entity_type in {"validated_components", "retention_factors"} else {"owner", "admin", "contributor"}
            repository._membership(con, project_uid, actor, roles)
        staged: list[dict[str, Any]] = []
        for number, row in enumerate(rows, start=2):
            normalized, errors = _validate(entity_type, row, con, project_uid)
            status = "invalid" if errors else "valid"
            staged.append({"row_number": number, "status": status, "errors": errors, "data": normalized})
        valid = sum(item["status"] == "valid" for item in staged)
        invalid = len(staged) - valid
        columns = {row[1] for row in con.execute("PRAGMA table_info(import_batches)")}
        if "project_uid" in columns:
            con.execute(
                "INSERT INTO import_batches(batch_uid,entity_type,source_filename,source_sha256,source_format,sheet_name,conflict_policy,row_count,valid_count,invalid_count,created_by,project_uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_uid, entity_type, Path(filename).name, source_sha, source_format, sheet_name, conflict_policy, len(staged), valid, invalid, actor, project_uid),
            )
            if project_uid:
                con.execute(
                    "INSERT OR IGNORE INTO project_records(project_uid,entity_type,entity_uid,linked_by) VALUES (?,'import_batch',?,?)",
                    (project_uid, batch_uid, actor),
                )
        else:
            con.execute(
                "INSERT INTO import_batches(batch_uid,entity_type,source_filename,source_sha256,source_format,sheet_name,conflict_policy,row_count,valid_count,invalid_count,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (batch_uid, entity_type, Path(filename).name, source_sha, source_format, sheet_name, conflict_policy, len(staged), valid, invalid, actor),
            )
        con.executemany(
            "INSERT INTO import_rows(import_row_uid,batch_uid,row_number,raw_json,normalized_json,validation_status,errors_json) VALUES (?,?,?,?,?,?,?)",
            [(_uid("irow"), batch_uid, item["row_number"], _json(rows[index]), _json(item["data"]), item["status"], _json(item["errors"])) for index, item in enumerate(staged)],
        )
        ResearchRepository._audit(con, "stage", "import_batch", batch_uid, actor, source="import", import_batch_uid=batch_uid, detail={"entity_type": entity_type, "rows": len(staged), "valid": valid, "invalid": invalid, "source_sha256": source_sha})
        con.commit()
    return {"batch_uid": batch_uid, "project_uid": project_uid, "entity_type": entity_type, "filename": Path(filename).name, "row_count": len(staged), "valid_count": valid, "invalid_count": invalid, "status": "staged", "preview": staged[:25], "quota_consumed": False}


def stage_import_base64(
    repository: ResearchRepository,
    request: Mapping[str, Any],
    actor: str = "local-researcher",
    project_uid: str | None = None,
) -> dict[str, Any]:
    try:
        payload = base64.b64decode(str(request.get("content_base64") or ""), validate=True)
    except ValueError as exc:
        raise ValueError("content_base64 is invalid") from exc
    if not payload:
        raise ValueError("Uploaded file is empty")
    if len(payload) > 25 * 1024 * 1024:
        raise ValueError("Import file exceeds the 25 MiB research-platform limit")
    return stage_import(
        repository,
        payload,
        str(request.get("filename") or "upload.csv"),
        str(request.get("entity_type") or ""),
        sheet_name=request.get("sheet_name"),
        conflict_policy=str(request.get("conflict_policy") or "reject"),
        actor=actor,
        project_uid=project_uid or request.get("project_uid"),
    )


def commit_import(repository: ResearchRepository, batch_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
    with repository.connect() as con:
        # Lock before inspecting the staged batch so conflict checks, sequential
        # ID allocation and all row writes form one all-or-nothing transaction.
        con.execute("BEGIN IMMEDIATE")
        batch = con.execute("SELECT * FROM import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
        if not batch:
            raise KeyError(batch_uid)
        if batch["status"] != "staged":
            raise ValueError(f"Batch is already {batch['status']}")
        if batch["invalid_count"]:
            raise ValueError("Batch contains invalid rows; correct and re-stage before commit")
        entity_type = batch["entity_type"]
        policy = batch["conflict_policy"]
        project_uid = batch["project_uid"] if "project_uid" in batch.keys() else None
        if project_uid and hasattr(repository, "_membership"):
            roles = {"owner", "admin", "analyst"} if entity_type in {"validated_components", "retention_factors"} else {"owner", "admin", "contributor"}
            repository._membership(con, project_uid, actor, roles)
        rows = list(con.execute("SELECT * FROM import_rows WHERE batch_uid=? ORDER BY row_number", (batch_uid,)))
        actions: dict[str, int] = {"inserted": 0, "updated": 0}
        try:
            if project_uid and hasattr(repository, "consume_usage_in_transaction"):
                repository.consume_usage_in_transaction(
                    con,
                    project_uid,
                    "import_rows",
                    len(rows),
                    actor,
                    "import_batch",
                    batch_uid,
                    {"entity_type": entity_type, "source_filename": batch["source_filename"]},
                )
            for staged in rows:
                data = json.loads(staged["normalized_json"])
                action, entity_uid = _commit_row(con, entity_type, data, policy, actor, batch_uid, project_uid)
                actions[action] = actions.get(action, 0) + 1
                con.execute("UPDATE import_rows SET commit_action=?,committed_entity_uid=? WHERE import_row_uid=?", (action, entity_uid, staged["import_row_uid"]))
                entity_name = {
                    "foods": "food",
                    "participants": "participant",
                    "experiments": "experiment",
                }.get(entity_type)
                if project_uid and entity_name:
                    con.execute(
                        "INSERT OR IGNORE INTO project_records(project_uid,entity_type,entity_uid,linked_by) VALUES (?,?,?,?)",
                        (project_uid, entity_name, entity_uid, actor),
                    )
            con.execute("UPDATE import_batches SET status='committed',committed_at=CURRENT_TIMESTAMP WHERE batch_uid=?", (batch_uid,))
            ResearchRepository._audit(con, "commit", "import_batch", batch_uid, actor, source="import", import_batch_uid=batch_uid, detail=actions)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return {"batch_uid": batch_uid, "project_uid": project_uid, "status": "committed", "quota_metric": "import_rows" if project_uid else None, "quota_quantity": len(rows) if project_uid else 0, **actions}


def _commit_row(
    con: sqlite3.Connection,
    entity_type: str,
    data: Mapping[str, Any],
    policy: str,
    actor: str,
    batch_uid: str,
    project_uid: str | None = None,
) -> tuple[str, str]:
    if entity_type == "foods":
        existing = None
        scope_join = ""
        scope_where = ""
        scope_params: list[Any] = []
        if project_uid:
            scope_join = " JOIN project_records pr ON pr.entity_type='food' AND pr.entity_uid=f.food_uid"
            scope_where = " AND pr.project_uid=?"
            scope_params = [project_uid]
        if data.get("legacy_food_id") is not None:
            existing = con.execute(f"SELECT f.* FROM research_foods f{scope_join} WHERE f.legacy_food_id=?{scope_where}", (data["legacy_food_id"], *scope_params)).fetchone()
        if not existing and data.get("food_name"):
            existing = con.execute(f"SELECT f.* FROM research_foods f{scope_join} WHERE lower(f.food_name)=lower(?){scope_where} LIMIT 1", (data["food_name"], *scope_params)).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError(f"Food conflict for {data.get('food_name')!r}")
            before = dict(existing)
            con.execute("UPDATE research_foods SET food_name=?,description=?,preparation_method=?,category=?,source_scope=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE food_uid=?", (data.get("food_name"), data.get("description"), data.get("preparation_method"), data.get("category"), data.get("source_scope") or existing["source_scope"], existing["food_uid"]))
            after = dict(con.execute("SELECT * FROM research_foods WHERE food_uid=?", (existing["food_uid"],)).fetchone())
            ResearchRepository._version(con, "food", after, actor)
            ResearchRepository._audit(con, "update", "food", existing["food_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["food_uid"]
        legacy_id = data.get("legacy_food_id")
        if legacy_id is None:
            legacy_id = con.execute("SELECT COALESCE(MAX(legacy_food_id),0)+1 FROM research_foods").fetchone()[0]
        uid = _uid("food")
        con.execute("INSERT INTO research_foods(food_uid,legacy_food_id,food_name,description,preparation_method,category,source_scope) VALUES (?,?,?,?,?,?,?)", (uid, legacy_id, data.get("food_name"), data.get("description"), data.get("preparation_method"), data.get("category"), data.get("source_scope") or "research_core"))
        after = dict(con.execute("SELECT * FROM research_foods WHERE food_uid=?", (uid,)).fetchone())
        ResearchRepository._version(con, "food", after, actor)
        ResearchRepository._audit(con, "create", "food", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "participants":
        existing = con.execute(
            "SELECT p.* FROM participants p JOIN project_records pr ON pr.entity_type='participant' AND pr.entity_uid=p.participant_uid WHERE p.participant_code=? AND pr.project_uid=?",
            (data.get("participant_code"), project_uid),
        ).fetchone() if data.get("participant_code") and project_uid else (con.execute("SELECT * FROM participants WHERE participant_code=?", (data.get("participant_code"),)).fetchone() if data.get("participant_code") else None)
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError(f"Participant conflict for {data.get('participant_code')!r}")
            before = dict(existing)
            con.execute("UPDATE participants SET surname=?,firstname=?,age_years=?,gender_code=?,activity_level=?,life_id=?,weight_kg=?,height_cm=?,notes=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE participant_uid=?", (data.get("surname"),data.get("firstname"),data.get("age_years"),data.get("gender_code"),data.get("activity_level"),data.get("life_id"),data.get("weight_kg"),data.get("height_cm"),data.get("notes"),existing["participant_uid"]))
            after = dict(con.execute("SELECT * FROM participants WHERE participant_uid=?", (existing["participant_uid"],)).fetchone())
            ResearchRepository._version(con,"participant",after,actor); ResearchRepository._audit(con,"update","participant",existing["participant_uid"],actor,before=before,after=after,source="import",import_batch_uid=batch_uid)
            return "updated", existing["participant_uid"]
        sequence = con.execute("SELECT COUNT(*)+1 FROM participants").fetchone()[0]
        uid = _uid("part"); code = data.get("participant_code") or f"RDP-{sequence:05d}"
        con.execute("INSERT INTO participants(participant_uid,participant_code,legacy_usercode,surname,firstname,age_years,gender_code,activity_level,life_id,weight_kg,height_cm,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (uid,code,data.get("legacy_usercode"),data.get("surname"),data.get("firstname"),data.get("age_years"),data.get("gender_code"),data.get("activity_level"),data.get("life_id"),data.get("weight_kg"),data.get("height_cm"),data.get("notes")))
        after=dict(con.execute("SELECT * FROM participants WHERE participant_uid=?",(uid,)).fetchone()); ResearchRepository._version(con,"participant",after,actor); ResearchRepository._audit(con,"create","participant",uid,actor,after=after,source="import",import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "app_users":
        email=str(data.get("email") or "").lower(); existing=con.execute("SELECT * FROM app_users WHERE email=?",(email,)).fetchone()
        if existing:
            if policy in {"reject","insert_only"}: raise ValueError(f"User conflict for {email!r}")
            before=dict(existing); con.execute("UPDATE app_users SET display_name=?,role=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE user_uid=?",(data.get("display_name"),data.get("role") or "researcher",existing["user_uid"])); after=dict(con.execute("SELECT * FROM app_users WHERE user_uid=?",(existing["user_uid"],)).fetchone()); ResearchRepository._version(con,"user",after,actor); ResearchRepository._audit(con,"update","user",existing["user_uid"],actor,before=before,after=after,source="import",import_batch_uid=batch_uid); return "updated",existing["user_uid"]
        uid=_uid("user"); con.execute("INSERT INTO app_users(user_uid,email,display_name,role) VALUES (?,?,?,?)",(uid,email,data.get("display_name"),data.get("role") or "researcher")); after=dict(con.execute("SELECT * FROM app_users WHERE user_uid=?",(uid,)).fetchone()); ResearchRepository._version(con,"user",after,actor); ResearchRepository._audit(con,"create","user",uid,actor,after=after,source="import",import_batch_uid=batch_uid); return "inserted",uid

    if entity_type == "experiments":
        code=data.get("experiment_code"); existing=con.execute("SELECT e.* FROM experiments e JOIN project_records pr ON pr.entity_type='experiment' AND pr.entity_uid=e.experiment_uid WHERE e.experiment_code=? AND pr.project_uid=?",(code,project_uid)).fetchone() if project_uid else con.execute("SELECT * FROM experiments WHERE experiment_code=?",(code,)).fetchone()
        if existing:
            if policy in {"reject","insert_only"}: raise ValueError(f"Experiment conflict for {code!r}")
            before=dict(existing); con.execute("UPDATE experiments SET food_uid=?,sample_code=?,preparation_method=?,analytical_method=?,laboratory=?,experiment_date=?,status=?,notes=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE experiment_uid=?",(data.get("food_uid"),data.get("sample_code"),data.get("preparation_method"),data.get("analytical_method"),data.get("laboratory"),data.get("experiment_date"),data.get("status") or "draft",data.get("notes"),existing["experiment_uid"])); after=dict(con.execute("SELECT * FROM experiments WHERE experiment_uid=?",(existing["experiment_uid"],)).fetchone()); ResearchRepository._version(con,"experiment",after,actor); ResearchRepository._audit(con,"update","experiment",existing["experiment_uid"],actor,before=before,after=after,source="import",import_batch_uid=batch_uid); return "updated",existing["experiment_uid"]
        uid=_uid("exp"); con.execute("INSERT INTO experiments(experiment_uid,experiment_code,food_uid,sample_code,preparation_method,analytical_method,laboratory,experiment_date,status,notes) VALUES (?,?,?,?,?,?,?,?,?,?)",(uid,code,data.get("food_uid"),data.get("sample_code"),data.get("preparation_method"),data.get("analytical_method"),data.get("laboratory"),data.get("experiment_date"),data.get("status") or "draft",data.get("notes"))); after=dict(con.execute("SELECT * FROM experiments WHERE experiment_uid=?",(uid,)).fetchone()); ResearchRepository._version(con,"experiment",after,actor); ResearchRepository._audit(con,"create","experiment",uid,actor,after=after,source="import",import_batch_uid=batch_uid); return "inserted",uid

    if entity_type == "components":
        food_uid=data.get("food_uid"); nutrient_code=data.get("nutrient_code"); existing=con.execute("SELECT * FROM food_component_values WHERE food_uid=? AND nutrient_code=?",(food_uid,nutrient_code)).fetchone(); nutrient=con.execute("SELECT unit FROM nutrient_definitions WHERE nutrient_code=?",(nutrient_code,)).fetchone(); unit=data.get("unit") or nutrient[0]; value=data.get("value"); status=data.get("value_status") or ("missing" if value is None else "measured")
        if existing:
            if policy in {"reject","insert_only"}: raise ValueError("Component value already exists; use upsert to replace the curated value")
            before=dict(existing); con.execute("UPDATE food_component_values SET value=?,unit=?,value_status=?,source_type=?,provenance_json=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE component_value_uid=?",(value,unit,status,data.get("source_type") or "research_core",_json({"import":data.get("provenance")}),existing["component_value_uid"])); after=dict(con.execute("SELECT * FROM food_component_values WHERE component_value_uid=?",(existing["component_value_uid"],)).fetchone()); ResearchRepository._audit(con,"update","food_component",existing["component_value_uid"],actor,before=before,after=after,source="import",import_batch_uid=batch_uid); return "updated",existing["component_value_uid"]
        uid=_uid("cmp"); con.execute("INSERT INTO food_component_values(component_value_uid,food_uid,nutrient_code,value,unit,value_status,source_type,provenance_json) VALUES (?,?,?,?,?,?,?,?)",(uid,food_uid,nutrient_code,value,unit,status,data.get("source_type") or "research_core",_json({"import":data.get("provenance")}))); after=dict(con.execute("SELECT * FROM food_component_values WHERE component_value_uid=?",(uid,)).fetchone()); ResearchRepository._audit(con,"create","food_component",uid,actor,after=after,source="import",import_batch_uid=batch_uid); return "inserted",uid

    if entity_type == "experiment_results":
        uid=_uid("res"); nutrient=con.execute("SELECT unit FROM nutrient_definitions WHERE nutrient_code=?",(data.get("nutrient_code"),)).fetchone(); con.execute("INSERT INTO experiment_results(result_uid,experiment_uid,nutrient_code,value,unit,replicate_number,lod,loq,uncertainty,qc_status,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(uid,data.get("experiment_uid"),data.get("nutrient_code"),data.get("value"),data.get("unit") or nutrient[0],data.get("replicate_number"),data.get("lod"),data.get("loq"),data.get("uncertainty"),data.get("qc_status") or "unreviewed",data.get("notes"))); after=dict(con.execute("SELECT * FROM experiment_results WHERE result_uid=?",(uid,)).fetchone()); ResearchRepository._audit(con,"create","experiment_result",uid,actor,after=after,source="import",import_batch_uid=batch_uid); return "inserted",uid

    if entity_type == "data_sources":
        existing = con.execute("SELECT * FROM data_sources WHERE project_uid=? AND lower(source_code)=lower(?)", (project_uid, data.get("source_code"))).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError(f"Data source conflict for {data.get('source_code')!r}")
            before = dict(existing)
            con.execute("UPDATE data_sources SET source_name=?,source_type=?,publisher=?,source_url=?,citation=?,license_notes=? WHERE source_uid=?", (data.get("source_name"), data.get("source_type") or "manual", data.get("publisher"), data.get("source_url"), data.get("citation"), data.get("license_notes"), existing["source_uid"]))
            after = dict(con.execute("SELECT * FROM data_sources WHERE source_uid=?", (existing["source_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "data_source", existing["source_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["source_uid"]
        uid = _uid("src")
        con.execute("INSERT INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,source_url,citation,license_notes) VALUES (?,?,?,?,?,?,?,?,?)", (uid, project_uid, data.get("source_code"), data.get("source_name"), data.get("source_type") or "manual", data.get("publisher"), data.get("source_url"), data.get("citation"), data.get("license_notes")))
        after = dict(con.execute("SELECT * FROM data_sources WHERE source_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "data_source", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "source_releases":
        existing = con.execute("SELECT * FROM source_releases WHERE source_uid=? AND lower(release_label)=lower(?)", (data.get("source_uid"), data.get("release_label"))).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError(f"Source release conflict for {data.get('release_label')!r}")
            before = dict(existing)
            con.execute("UPDATE source_releases SET release_date=?,retrieved_at=?,sha256=?,schema_notes=?,imported_by=? WHERE source_release_uid=?", (data.get("release_date"), data.get("retrieved_at"), data.get("sha256"), data.get("schema_notes"), actor, existing["source_release_uid"]))
            after = dict(con.execute("SELECT * FROM source_releases WHERE source_release_uid=?", (existing["source_release_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "source_release", existing["source_release_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["source_release_uid"]
        uid = _uid("rel")
        con.execute("INSERT INTO source_releases(source_release_uid,source_uid,release_label,release_date,retrieved_at,sha256,schema_notes,imported_by) VALUES (?,?,?,?,?,?,?,?)", (uid, data.get("source_uid"), data.get("release_label"), data.get("release_date"), data.get("retrieved_at"), data.get("sha256"), data.get("schema_notes"), actor))
        after = dict(con.execute("SELECT * FROM source_releases WHERE source_release_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "source_release", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "external_foods":
        existing = con.execute("SELECT * FROM external_foods WHERE project_uid=? AND source_release_uid=? AND source_food_code=?", (project_uid, data.get("source_release_uid"), data.get("source_food_code"))).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError(f"External food conflict for {data.get('source_food_code')!r}")
            before = dict(existing)
            con.execute("UPDATE external_foods SET food_name=?,local_name=?,scientific_name=?,food_group=?,country_code=?,preparation_state=?,edible_portion_percent=?,provenance_json=? WHERE external_food_uid=?", (data.get("food_name"), data.get("local_name"), data.get("scientific_name"), data.get("food_group"), data.get("country_code") or "NG", data.get("preparation_state"), data.get("edible_portion_percent"), _json({"import": data.get("provenance")}), existing["external_food_uid"]))
            after = dict(con.execute("SELECT * FROM external_foods WHERE external_food_uid=?", (existing["external_food_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "external_food", existing["external_food_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["external_food_uid"]
        uid = _uid("xfood")
        con.execute("INSERT INTO external_foods(external_food_uid,project_uid,source_release_uid,source_food_code,food_name,local_name,scientific_name,food_group,country_code,preparation_state,edible_portion_percent,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (uid, project_uid, data.get("source_release_uid"), data.get("source_food_code"), data.get("food_name"), data.get("local_name"), data.get("scientific_name"), data.get("food_group"), data.get("country_code") or "NG", data.get("preparation_state"), data.get("edible_portion_percent"), _json({"import": data.get("provenance")})))
        after = dict(con.execute("SELECT * FROM external_foods WHERE external_food_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "external_food", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "external_components":
        existing = con.execute("SELECT * FROM external_food_component_values WHERE external_food_uid=? AND canonical_nutrient_uid=?", (data.get("external_food_uid"), data.get("canonical_nutrient_uid"))).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError("External component already exists for this food and nutrient")
            before = dict(existing)
            con.execute("UPDATE external_food_component_values SET value=?,unit=?,basis=?,value_status=?,analytical_method=?,uncertainty=?,provenance_json=? WHERE external_value_uid=?", (data.get("value"), data.get("unit"), data.get("basis") or "per_100g_edible_portion", data.get("value_status") or ("missing" if data.get("value") is None else "reported"), data.get("analytical_method"), data.get("uncertainty"), _json({"import": data.get("provenance")}), existing["external_value_uid"]))
            after = dict(con.execute("SELECT * FROM external_food_component_values WHERE external_value_uid=?", (existing["external_value_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "external_food_component", existing["external_value_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["external_value_uid"]
        uid = _uid("xval")
        con.execute("INSERT INTO external_food_component_values(external_value_uid,external_food_uid,canonical_nutrient_uid,value,unit,basis,value_status,analytical_method,uncertainty,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)", (uid, data.get("external_food_uid"), data.get("canonical_nutrient_uid"), data.get("value"), data.get("unit"), data.get("basis") or "per_100g_edible_portion", data.get("value_status") or ("missing" if data.get("value") is None else "reported"), data.get("analytical_method"), data.get("uncertainty"), _json({"import": data.get("provenance")})))
        after = dict(con.execute("SELECT * FROM external_food_component_values WHERE external_value_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "external_food_component", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "validated_components":
        existing = con.execute(
            "SELECT * FROM validated_food_component_values WHERE project_uid=? AND research_food_uid=? AND canonical_nutrient_uid=? AND evidence_class=? AND source_release_uid IS ? AND source_record_uid IS ? ORDER BY created_at DESC LIMIT 1",
            (project_uid, data.get("food_uid"), data.get("canonical_nutrient_uid"), data.get("evidence_class") or "study_measured", data.get("source_release_uid"), data.get("source_record_uid")),
        ).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError("Validated component evidence record already exists")
            before = dict(existing)
            con.execute("UPDATE validated_food_component_values SET value=?,unit=?,basis=?,validation_status=?,provenance_json=?,created_by=? WHERE validated_value_uid=?", (data.get("value"), data.get("unit"), data.get("basis") or "per_100g_edible_portion", data.get("validation_status") or "provisional", _json({"import": data.get("provenance")}), actor, existing["validated_value_uid"]))
            after = dict(con.execute("SELECT * FROM validated_food_component_values WHERE validated_value_uid=?", (existing["validated_value_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "validated_food_component", existing["validated_value_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["validated_value_uid"]
        uid = _uid("vval")
        con.execute("INSERT INTO validated_food_component_values(validated_value_uid,project_uid,research_food_uid,canonical_nutrient_uid,value,unit,basis,evidence_class,validation_status,source_release_uid,source_record_uid,provenance_json,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid, project_uid, data.get("food_uid"), data.get("canonical_nutrient_uid"), data.get("value"), data.get("unit"), data.get("basis") or "per_100g_edible_portion", data.get("evidence_class") or "study_measured", data.get("validation_status") or "provisional", data.get("source_release_uid"), data.get("source_record_uid"), _json({"import": data.get("provenance")}), actor))
        after = dict(con.execute("SELECT * FROM validated_food_component_values WHERE validated_value_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "validated_food_component", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    if entity_type == "retention_factors":
        existing = con.execute("SELECT * FROM retention_factors WHERE project_uid=? AND source_release_uid=? AND cooking_method_code=? AND food_group IS ? AND canonical_nutrient_uid=? ORDER BY created_at DESC LIMIT 1", (project_uid, data.get("source_release_uid"), data.get("cooking_method_code"), data.get("food_group") or None, data.get("canonical_nutrient_uid"))).fetchone()
        if existing:
            if policy in {"reject", "insert_only"}:
                raise ValueError("Retention factor already exists for this source/method/group/nutrient")
            before = dict(existing)
            con.execute("UPDATE retention_factors SET retention_fraction=?,factor_status=?,notes=? WHERE retention_factor_uid=?", (data.get("retention_fraction"), data.get("factor_status") or "reported", data.get("notes"), existing["retention_factor_uid"]))
            after = dict(con.execute("SELECT * FROM retention_factors WHERE retention_factor_uid=?", (existing["retention_factor_uid"],)).fetchone())
            ResearchRepository._audit(con, "update", "retention_factor", existing["retention_factor_uid"], actor, before=before, after=after, source="import", import_batch_uid=batch_uid)
            return "updated", existing["retention_factor_uid"]
        uid = _uid("ret")
        con.execute("INSERT INTO retention_factors(retention_factor_uid,project_uid,source_release_uid,cooking_method_code,food_group,canonical_nutrient_uid,retention_fraction,factor_status,notes) VALUES (?,?,?,?,?,?,?,?,?)", (uid, project_uid, data.get("source_release_uid"), data.get("cooking_method_code"), data.get("food_group") or None, data.get("canonical_nutrient_uid"), data.get("retention_fraction"), data.get("factor_status") or "reported", data.get("notes")))
        after = dict(con.execute("SELECT * FROM retention_factors WHERE retention_factor_uid=?", (uid,)).fetchone())
        ResearchRepository._audit(con, "create", "retention_factor", uid, actor, after=after, source="import", import_batch_uid=batch_uid)
        return "inserted", uid

    raise ValueError(f"Unsupported entity_type {entity_type!r}")


def csv_template(entity_type: str) -> bytes:
    if entity_type not in ENTITY_HEADERS:
        raise ValueError(entity_type)
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(ENTITY_HEADERS[entity_type])
    return stream.getvalue().encode("utf-8")
