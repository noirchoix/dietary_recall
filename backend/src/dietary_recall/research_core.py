"""Research Data Platform repository and legacy-to-research migration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .constants import TABLE_FIELDS
from .db import connect_writable, readonly, sha256_file
from .research_schema import SCHEMA_VERSION, initialize_schema


LEGACY_NAMESPACE = uuid.UUID("65384f71-43f9-49d7-bc0e-1e6990a1bb70")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_uid(prefix: str, source_sha: str, token: str) -> str:
    return f"{prefix}_{uuid.uuid5(LEGACY_NAMESPACE, source_sha + ':' + token).hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _unit_from_column(column: str) -> str:
    for suffix, unit in (
        ("_kcal", "kcal"), ("_mcg", "mcg"), ("_mg", "mg"),
        ("_IU", "IU"), ("_RAE", "RAE"), ("_RE", "RE"), ("_g", "g"),
    ):
        if column.endswith(suffix) or column.endswith(f"{suffix}_{unit}"):
            return unit
    return "legacy_unspecified"


def _nutrient_code(table: str, column: str) -> str:
    return "legacy:" + table.lower() + ":" + column.lower()


def _display_name(column: str) -> str:
    text = column
    for suffix in ("_kcal", "_mcg", "_mg", "_IU_IU", "_IU", "_RAE_RAE", "_RAE", "_RE_RE", "_RE", "_g"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.replace("_", " ")


class ResearchRepository:
    """Transactional CRUD service for the normalized research-core database."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        with self.connect() as con:
            row = con.execute("SELECT value FROM research_meta WHERE key='platform_mode'").fetchone()
            if row is None or row[0] != "research_core":
                raise ValueError("Not a Research Data Platform database")

    def connect(self) -> sqlite3.Connection:
        con = connect_writable(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    @staticmethod
    def _one(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict[str, Any]:
        row = con.execute(sql, tuple(params)).fetchone()
        if row is None:
            raise KeyError("Record not found")
        return dict(row)

    @staticmethod
    def _audit(
        con: sqlite3.Connection,
        action: str,
        entity_type: str,
        entity_uid: str | None,
        actor: str,
        *,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        source: str = "application",
        import_batch_uid: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        con.execute(
            "INSERT INTO audit_log(audit_uid,action,entity_type,entity_uid,actor,source,import_batch_uid,before_json,after_json,detail_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                _uid("aud"), action, entity_type, entity_uid, actor, source, import_batch_uid,
                _json(before) if before is not None else None,
                _json(after) if after is not None else None,
                _json(detail or {}),
            ),
        )

    @staticmethod
    def _version(con: sqlite3.Connection, entity_type: str, entity: Mapping[str, Any], actor: str) -> None:
        uid_field = {
            "food": "food_uid", "participant": "participant_uid", "experiment": "experiment_uid",
            "user": "user_uid", "recall": "recall_uid",
        }[entity_type]
        con.execute(
            "INSERT INTO entity_versions(version_uid,entity_type,entity_uid,version_number,snapshot_json,actor) VALUES (?,?,?,?,?,?)",
            (_uid("ver"), entity_type, entity[uid_field], int(entity.get("version", 1)), _json(entity), actor),
        )

    def summary(self) -> dict[str, Any]:
        with self.connect() as con:
            counts = {
                "foods": con.execute("SELECT COUNT(*) FROM research_foods WHERE active=1").fetchone()[0],
                "participants": con.execute("SELECT COUNT(*) FROM participants WHERE active=1").fetchone()[0],
                "experiments": con.execute("SELECT COUNT(*) FROM experiments WHERE active=1").fetchone()[0],
                "recalls": con.execute("SELECT COUNT(*) FROM recalls WHERE status!='archived'").fetchone()[0],
                "component_values": con.execute("SELECT COUNT(*) FROM food_component_values").fetchone()[0],
                "reported_components": con.execute("SELECT COUNT(*) FROM food_component_values WHERE value IS NOT NULL").fetchone()[0],
                "audit_events": con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                "imports": con.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0],
            }
            source_sha = con.execute("SELECT value FROM research_meta WHERE key='legacy_source_sha256'").fetchone()
            validated = con.execute("SELECT value FROM research_meta WHERE key='validated_research'").fetchone()
            schema_version = int(con.execute("PRAGMA user_version").fetchone()[0])
            return {
                **counts,
                "platform_mode": "research_core",
                "legacy_compatibility": "available",
                "validated_research": validated[0] if validated else "planned_separate_layer",
                "schema_version": schema_version or SCHEMA_VERSION,
                "legacy_source_sha256": source_sha[0] if source_sha else None,
            }

    def list_nutrients(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM nutrient_definitions WHERE active=1 ORDER BY component_group, display_name")]

    def next_legacy_food_id(self) -> int:
        with self.connect() as con:
            return int(con.execute("SELECT COALESCE(MAX(legacy_food_id),0)+1 FROM research_foods").fetchone()[0])

    def list_foods(self, query: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "WHERE f.active=1"
        if query:
            where += " AND (f.food_name LIKE ? OR CAST(f.legacy_food_id AS TEXT)=?)"
            params.extend([f"%{query}%", query])
        params.extend([max(1, min(limit, 1000)), max(0, offset)])
        sql = f"""
        SELECT f.*,
               SUM(CASE WHEN c.value IS NOT NULL THEN 1 ELSE 0 END) AS reported_components,
               COUNT(c.component_value_uid) AS total_components
        FROM research_foods f
        LEFT JOIN food_component_values c ON c.food_uid=f.food_uid
        {where}
        GROUP BY f.food_uid
        ORDER BY f.food_name
        LIMIT ? OFFSET ?
        """
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    def get_food(self, food_uid: str) -> dict[str, Any]:
        with self.connect() as con:
            food = self._one(con, "SELECT * FROM research_foods WHERE food_uid=?", (food_uid,))
            components = [
                dict(row)
                for row in con.execute(
                    "SELECT c.*, n.display_name, n.component_group, n.component_kind, n.legacy_column "
                    "FROM food_component_values c JOIN nutrient_definitions n USING(nutrient_code) "
                    "WHERE c.food_uid=? ORDER BY n.component_group,n.display_name",
                    (food_uid,),
                )
            ]
            food["components"] = components
            return food

    def create_food(self, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        name = str(data.get("food_name") or "").strip()
        if not name:
            raise ValueError("food_name is required")
        with self.connect() as con:
            # Serialize MAX(id)+1 allocation so two data-entry requests cannot
            # be assigned the same compatibility ID.
            con.execute("BEGIN IMMEDIATE")
            legacy_id = data.get("legacy_food_id")
            if legacy_id in (None, ""):
                legacy_id = int(con.execute("SELECT COALESCE(MAX(legacy_food_id),0)+1 FROM research_foods").fetchone()[0])
            uid = _uid("food")
            con.execute(
                "INSERT INTO research_foods(food_uid,legacy_food_id,food_name,description,preparation_method,category,source_scope) VALUES (?,?,?,?,?,?,?)",
                (
                    uid, int(legacy_id), name, data.get("description"), data.get("preparation_method"),
                    data.get("category"), data.get("source_scope") or "research_core",
                ),
            )
            food = self._one(con, "SELECT * FROM research_foods WHERE food_uid=?", (uid,))
            self._version(con, "food", food, actor)
            self._audit(con, "create", "food", uid, actor, after=food, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return food

    def update_food(self, food_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        allowed = {"food_name", "description", "preparation_method", "category", "active"}
        updates = {key: data[key] for key in data if key in allowed}
        if not updates:
            return self.get_food(food_uid)
        with self.connect() as con:
            before = self._one(con, "SELECT * FROM research_foods WHERE food_uid=?", (food_uid,))
            assignments = ",".join(f"{key}=?" for key in updates)
            con.execute(
                f"UPDATE research_foods SET {assignments},version=version+1,updated_at=CURRENT_TIMESTAMP WHERE food_uid=?",
                [*updates.values(), food_uid],
            )
            after = self._one(con, "SELECT * FROM research_foods WHERE food_uid=?", (food_uid,))
            self._version(con, "food", after, actor)
            self._audit(con, "update", "food", food_uid, actor, before=before, after=after)
            return after

    def upsert_component(self, food_uid: str, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        nutrient_code = data.get("nutrient_code")
        with self.connect() as con:
            if not nutrient_code and data.get("legacy_column"):
                row = con.execute("SELECT nutrient_code FROM nutrient_definitions WHERE legacy_column=?", (data["legacy_column"],)).fetchone()
                nutrient_code = row[0] if row else None
            if not nutrient_code:
                raise ValueError("nutrient_code or recognized legacy_column is required")
            nutrient = self._one(con, "SELECT * FROM nutrient_definitions WHERE nutrient_code=?", (nutrient_code,))
            self._one(con, "SELECT food_uid FROM research_foods WHERE food_uid=?", (food_uid,))
            before_row = con.execute("SELECT * FROM food_component_values WHERE food_uid=? AND nutrient_code=?", (food_uid, nutrient_code)).fetchone()
            before = dict(before_row) if before_row else None
            value = data.get("value")
            value = None if value in (None, "") else float(value)
            status = str(data.get("value_status") or ("missing" if value is None else "measured"))
            unit = str(data.get("unit") or nutrient["unit"])
            provenance = data.get("provenance_json") or data.get("provenance") or {}
            if isinstance(provenance, str):
                try:
                    provenance = json.loads(provenance)
                except json.JSONDecodeError:
                    provenance = {"note": provenance}
            if before:
                con.execute(
                    "UPDATE food_component_values SET value=?,unit=?,value_status=?,source_type=?,provenance_json=?,version=version+1,updated_at=CURRENT_TIMESTAMP "
                    "WHERE food_uid=? AND nutrient_code=?",
                    (value, unit, status, data.get("source_type") or "research_core", _json(provenance), food_uid, nutrient_code),
                )
            else:
                con.execute(
                    "INSERT INTO food_component_values(component_value_uid,food_uid,nutrient_code,value,unit,value_status,source_type,provenance_json) VALUES (?,?,?,?,?,?,?,?)",
                    (_uid("cmp"), food_uid, nutrient_code, value, unit, status, data.get("source_type") or "research_core", _json(provenance)),
                )
            after = self._one(con, "SELECT * FROM food_component_values WHERE food_uid=? AND nutrient_code=?", (food_uid, nutrient_code))
            self._audit(con, "upsert", "food_component", after["component_value_uid"], actor, before=before, after=after, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return after

    def list_participants(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM participants WHERE active=1"
        params: list[Any] = []
        if query:
            sql += " AND (participant_code LIKE ? OR surname LIKE ? OR firstname LIKE ? OR CAST(legacy_usercode AS TEXT)=?)"
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%", query])
        sql += " ORDER BY participant_code LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    def create_participant(self, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            code = str(data.get("participant_code") or "").strip()
            if not code:
                sequence = int(con.execute("SELECT COUNT(*)+1 FROM participants").fetchone()[0])
                code = f"RDP-{sequence:05d}"
            uid = _uid("part")
            con.execute(
                "INSERT INTO participants(participant_uid,participant_code,legacy_usercode,surname,firstname,age_years,gender_code,activity_level,life_id,weight_kg,height_cm,bmi_legacy,notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid, code, _opt_int(data.get("legacy_usercode")), data.get("surname"), data.get("firstname"),
                    _opt_float(data.get("age_years")), data.get("gender_code"), _opt_text(data.get("activity_level")),
                    _opt_int(data.get("life_id")), _opt_float(data.get("weight_kg")), _opt_float(data.get("height_cm")),
                    _opt_float(data.get("bmi_legacy")), data.get("notes"),
                ),
            )
            participant = self._one(con, "SELECT * FROM participants WHERE participant_uid=?", (uid,))
            self._version(con, "participant", participant, actor)
            self._audit(con, "create", "participant", uid, actor, after=participant, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return participant

    def update_participant(self, participant_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        allowed = {"participant_code","surname","firstname","age_years","gender_code","activity_level","life_id","weight_kg","height_cm","notes","active"}
        updates = {key: data[key] for key in data if key in allowed}
        if not updates:
            with self.connect() as con:
                return self._one(con, "SELECT * FROM participants WHERE participant_uid=?", (participant_uid,))
        with self.connect() as con:
            before = self._one(con, "SELECT * FROM participants WHERE participant_uid=?", (participant_uid,))
            assignments = ",".join(f"{key}=?" for key in updates)
            con.execute(f"UPDATE participants SET {assignments},version=version+1,updated_at=CURRENT_TIMESTAMP WHERE participant_uid=?", [*updates.values(), participant_uid])
            after = self._one(con, "SELECT * FROM participants WHERE participant_uid=?", (participant_uid,))
            self._version(con, "participant", after, actor)
            self._audit(con, "update", "participant", participant_uid, actor, before=before, after=after)
            return after

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM app_users WHERE active=1 ORDER BY display_name")]

    def create_user(self, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        email = str(data.get("email") or "").strip().lower()
        display = str(data.get("display_name") or "").strip()
        if "@" not in email or not display:
            raise ValueError("email and display_name are required")
        role = str(data.get("role") or "researcher")
        if role not in {"admin", "researcher", "data_entry", "analyst", "viewer"}:
            raise ValueError("role is not recognized")
        with self.connect() as con:
            uid = _uid("user")
            con.execute("INSERT INTO app_users(user_uid,email,display_name,role) VALUES (?,?,?,?)", (uid, email, display, role))
            user = self._one(con, "SELECT * FROM app_users WHERE user_uid=?", (uid,))
            self._version(con, "user", user, actor)
            self._audit(con, "create", "user", uid, actor, after=user, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return user

    def update_user(self, user_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        allowed = {"email", "display_name", "role", "active"}
        updates = {key: data[key] for key in data if key in allowed}
        if "email" in updates:
            updates["email"] = str(updates["email"] or "").strip().lower()
            if "@" not in updates["email"]:
                raise ValueError("valid email is required")
        if "display_name" in updates and not str(updates["display_name"] or "").strip():
            raise ValueError("display_name cannot be blank")
        if "role" in updates and updates["role"] not in {"admin", "researcher", "data_entry", "analyst", "viewer"}:
            raise ValueError("role is not recognized")
        with self.connect() as con:
            before = self._one(con, "SELECT * FROM app_users WHERE user_uid=?", (user_uid,))
            if not updates:
                return before
            assignments = ",".join(f"{key}=?" for key in updates)
            con.execute(f"UPDATE app_users SET {assignments},version=version+1,updated_at=CURRENT_TIMESTAMP WHERE user_uid=?", [*updates.values(), user_uid])
            after = self._one(con, "SELECT * FROM app_users WHERE user_uid=?", (user_uid,))
            self._version(con, "user", after, actor)
            self._audit(con, "update", "user", user_uid, actor, before=before, after=after)
            return after

    def list_experiments(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT e.*, f.food_name FROM experiments e LEFT JOIN research_foods f ON f.food_uid=e.food_uid WHERE e.active=1"
        params: list[Any] = []
        if query:
            sql += " AND (e.experiment_code LIKE ? OR e.sample_code LIKE ? OR f.food_name LIKE ?)"
            params.extend([f"%{query}%"] * 3)
        sql += " ORDER BY e.created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    def get_experiment(self, experiment_uid: str) -> dict[str, Any]:
        with self.connect() as con:
            experiment = self._one(con, "SELECT e.*,f.food_name,f.legacy_food_id FROM experiments e LEFT JOIN research_foods f USING(food_uid) WHERE experiment_uid=?", (experiment_uid,))
            experiment["results"] = [dict(row) for row in con.execute(
                "SELECT r.*,n.display_name,n.component_group FROM experiment_results r JOIN nutrient_definitions n USING(nutrient_code) WHERE r.experiment_uid=? ORDER BY r.created_at,r.replicate_number",
                (experiment_uid,),
            )]
            return experiment

    def create_experiment(self, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        with self.connect() as con:
            code = str(data.get("experiment_code") or "").strip() or f"EXP-{uuid.uuid4().hex[:8].upper()}"
            food_uid = data.get("food_uid") or self._resolve_food_uid(con, data)
            uid = _uid("exp")
            con.execute(
                "INSERT INTO experiments(experiment_uid,experiment_code,food_uid,sample_code,preparation_method,analytical_method,laboratory,experiment_date,status,notes,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid, code, food_uid, data.get("sample_code"), data.get("preparation_method"), data.get("analytical_method"), data.get("laboratory"), data.get("experiment_date"), data.get("status") or "draft", data.get("notes"), _json(data.get("provenance") or {})),
            )
            experiment = self._one(con, "SELECT * FROM experiments WHERE experiment_uid=?", (uid,))
            self._version(con, "experiment", experiment, actor)
            self._audit(con, "create", "experiment", uid, actor, after=experiment, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return experiment

    def update_experiment(self, experiment_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        allowed = {"experiment_code", "food_uid", "sample_code", "preparation_method", "analytical_method", "laboratory", "experiment_date", "status", "notes", "active"}
        updates = {key: data[key] for key in data if key in allowed}
        if "status" in updates and updates["status"] not in {"draft", "in_progress", "complete", "curated", "archived"}:
            raise ValueError("status is not recognized")
        with self.connect() as con:
            before = self._one(con, "SELECT * FROM experiments WHERE experiment_uid=?", (experiment_uid,))
            if not updates:
                return self.get_experiment(experiment_uid)
            assignments = ",".join(f"{key}=?" for key in updates)
            con.execute(f"UPDATE experiments SET {assignments},version=version+1,updated_at=CURRENT_TIMESTAMP WHERE experiment_uid=?", [*updates.values(), experiment_uid])
            after = self._one(con, "SELECT * FROM experiments WHERE experiment_uid=?", (experiment_uid,))
            self._version(con, "experiment", after, actor)
            self._audit(con, "update", "experiment", experiment_uid, actor, before=before, after=after)
            return after

    @staticmethod
    def _resolve_food_uid(con: sqlite3.Connection, data: Mapping[str, Any]) -> str | None:
        if data.get("legacy_food_id") not in (None, ""):
            row = con.execute("SELECT food_uid FROM research_foods WHERE legacy_food_id=?", (int(float(data["legacy_food_id"])),)).fetchone()
            if row:
                return str(row[0])
        if data.get("food_name"):
            row = con.execute("SELECT food_uid FROM research_foods WHERE lower(food_name)=lower(?) ORDER BY active DESC LIMIT 1", (str(data["food_name"]).strip(),)).fetchone()
            if row:
                return str(row[0])
        return None

    def add_experiment_result(self, experiment_uid: str, data: Mapping[str, Any], actor: str = "local-researcher", import_batch_uid: str | None = None) -> dict[str, Any]:
        with self.connect() as con:
            self._one(con, "SELECT experiment_uid FROM experiments WHERE experiment_uid=?", (experiment_uid,))
            nutrient_code = data.get("nutrient_code")
            if not nutrient_code and data.get("legacy_column"):
                row = con.execute("SELECT nutrient_code FROM nutrient_definitions WHERE legacy_column=?", (data["legacy_column"],)).fetchone()
                nutrient_code = row[0] if row else None
            if not nutrient_code:
                raise ValueError("nutrient_code or recognized legacy_column is required")
            nutrient = self._one(con, "SELECT * FROM nutrient_definitions WHERE nutrient_code=?", (nutrient_code,))
            uid = _uid("res")
            con.execute(
                "INSERT INTO experiment_results(result_uid,experiment_uid,nutrient_code,value,unit,replicate_number,lod,loq,uncertainty,qc_status,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid, experiment_uid, nutrient_code, _opt_float(data.get("value")), data.get("unit") or nutrient["unit"], _opt_int(data.get("replicate_number")), _opt_float(data.get("lod")), _opt_float(data.get("loq")), _opt_float(data.get("uncertainty")), data.get("qc_status") or "unreviewed", data.get("notes")),
            )
            result = self._one(con, "SELECT * FROM experiment_results WHERE result_uid=?", (uid,))
            self._audit(con, "create", "experiment_result", uid, actor, after=result, source="import" if import_batch_uid else "application", import_batch_uid=import_batch_uid)
            return result

    def list_recalls(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(
                "SELECT r.*,p.participant_code,COUNT(i.recall_item_uid) item_count FROM recalls r JOIN participants p USING(participant_uid) LEFT JOIN recall_items i USING(recall_uid) GROUP BY r.recall_uid ORDER BY r.created_at DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            )]

    def create_recall(self, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        participant_uid = str(data.get("participant_uid") or "")
        items = list(data.get("items") or [])
        if not participant_uid or not items:
            raise ValueError("participant_uid and at least one recall item are required")
        with self.connect() as con:
            self._one(con, "SELECT participant_uid FROM participants WHERE participant_uid=? AND active=1", (participant_uid,))
            uid = _uid("rec")
            con.execute(
                "INSERT INTO recalls(recall_uid,participant_uid,recall_date,day_name,meal_context,status,notes) VALUES (?,?,?,?,?,?,?)",
                (uid, participant_uid, data.get("recall_date"), data.get("day_name"), data.get("meal_context"), data.get("status") or "complete", data.get("notes")),
            )
            for index, item in enumerate(items):
                amount = float(item.get("amount_g") or 0)
                if amount < 0:
                    raise ValueError("amount_g cannot be negative")
                food_uid = str(item.get("food_uid") or "")
                self._one(con, "SELECT food_uid FROM research_foods WHERE food_uid=? AND active=1", (food_uid,))
                con.execute(
                    "INSERT INTO recall_items(recall_item_uid,recall_uid,item_order,food_uid,amount_g,meal_label,notes) VALUES (?,?,?,?,?,?,?)",
                    (_uid("rit"), uid, index, food_uid, amount, item.get("meal_label"), item.get("notes")),
                )
            self._recompute_recall(con, uid)
            recall = self._one(con, "SELECT * FROM recalls WHERE recall_uid=?", (uid,))
            self._version(con, "recall", recall, actor)
            self._audit(con, "create", "recall", uid, actor, after=recall, detail={"item_count": len(items)})
            return self.get_recall(uid, con=con)

    def _recompute_recall(self, con: sqlite3.Connection, recall_uid: str) -> None:
        totals: dict[str, float] = defaultdict(float)
        units: dict[str, str] = {}
        snapshot: list[str] = []
        rows = con.execute(
            "SELECT i.amount_g,c.nutrient_code,c.value,c.unit,c.version,c.component_value_uid "
            "FROM recall_items i JOIN food_component_values c ON c.food_uid=i.food_uid "
            "WHERE i.recall_uid=? AND c.value IS NOT NULL",
            (recall_uid,),
        )
        for row in rows:
            totals[row["nutrient_code"]] += float(row["amount_g"]) * float(row["value"]) / 100.0
            units[row["nutrient_code"]] = row["unit"]
            snapshot.append(f"{row['component_value_uid']}:{row['version']}:{row['value']}")
        snap_hash = hashlib.sha256("|".join(sorted(snapshot)).encode()).hexdigest()
        con.execute("DELETE FROM recall_results WHERE recall_uid=?", (recall_uid,))
        con.executemany(
            "INSERT INTO recall_results(recall_uid,nutrient_code,value,unit,calculation_mode,composition_snapshot_hash) VALUES (?,?,?,?,?,?)",
            [(recall_uid, code, value, units[code], "research_core_current", snap_hash) for code, value in totals.items()],
        )

    def get_recall(self, recall_uid: str, con: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns = con is None
        connection = con or self.connect()
        try:
            recall = self._one(connection, "SELECT * FROM recalls WHERE recall_uid=?", (recall_uid,))
            recall["items"] = [dict(row) for row in connection.execute(
                "SELECT i.*,f.food_name,f.legacy_food_id FROM recall_items i JOIN research_foods f USING(food_uid) WHERE i.recall_uid=? ORDER BY i.item_order", (recall_uid,)
            )]
            recall["results"] = [dict(row) for row in connection.execute(
                "SELECT r.*,n.display_name,n.component_group,n.component_kind FROM recall_results r JOIN nutrient_definitions n USING(nutrient_code) WHERE r.recall_uid=? ORDER BY n.component_group,n.display_name", (recall_uid,)
            )]
            return recall
        finally:
            if owns:
                connection.close()

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM audit_log ORDER BY created_at DESC,rowid DESC LIMIT ?", (max(1, min(limit, 1000)),))]

    def list_imports(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM import_batches ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),))]


def _opt_int(value: Any) -> int | None:
    return None if value in (None, "") else int(float(value))


def _opt_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)


def _opt_text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def initialize_research_database(source_db: str | Path, target_db: str | Path) -> dict[str, Any]:
    """Create a new research-core DB seeded from an immutable legacy snapshot."""
    source = Path(source_db).resolve()
    target = Path(target_db).resolve()
    if target.exists():
        raise FileExistsError(target)
    source_sha = sha256_file(source)
    initialize_schema(target)
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("INSERT INTO research_meta(key,value) VALUES ('platform_mode','research_core')")
        con.execute("INSERT INTO research_meta(key,value) VALUES ('legacy_source_sha256',?)", (source_sha,))
        con.execute("INSERT INTO research_meta(key,value) VALUES ('legacy_source_path',?)", (str(source),))

        for table, fields in TABLE_FIELDS.items():
            kind = "toxicant" if table == "Toxicants" else "nutrient"
            for column in fields:
                con.execute(
                    "INSERT INTO nutrient_definitions(nutrient_code,display_name,unit,component_group,component_kind,legacy_table,legacy_column) VALUES (?,?,?,?,?,?,?)",
                    (_nutrient_code(table, column), _display_name(column), _unit_from_column(column), table, kind, table, column),
                )

        food_uid_by_id: dict[int, str] = {}
        with readonly(source) as legacy:
            for row in legacy.execute("SELECT * FROM Food ORDER BY id"):
                legacy_id = int(row["Food_ID"])
                food_uid = _stable_uid("food", source_sha, f"food:{legacy_id}")
                food_uid_by_id[legacy_id] = food_uid
                con.execute(
                    "INSERT INTO research_foods(food_uid,legacy_food_id,food_name,description,source_scope) VALUES (?,?,?,?,?)",
                    (food_uid, legacy_id, row["Food_Name"], row["Food_Description"], "legacy_phd"),
                )
            for table, fields in TABLE_FIELDS.items():
                for row in legacy.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                    food_uid = food_uid_by_id.get(int(row["Food_ID"]))
                    if not food_uid:
                        continue
                    for column in fields:
                        value = row[column]
                        con.execute(
                            "INSERT INTO food_component_values(component_value_uid,food_uid,nutrient_code,value,unit,value_status,source_type,provenance_json) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                _stable_uid("cmp", source_sha, f"component:{table}:{row['id']}:{column}"),
                                food_uid, _nutrient_code(table, column), value, _unit_from_column(column),
                                "missing" if value is None else "reported", "legacy_phd",
                                _json({"legacy_table": table, "legacy_row_id": row["id"], "legacy_column": column, "source_sha256": source_sha}),
                            ),
                        )
            for row in legacy.execute("SELECT * FROM Person ORDER BY id"):
                uid = _stable_uid("part", source_sha, f"person-row:{row['id']}")
                code = f"LEGACY-{row['Usercode']}-{row['id']}"
                con.execute(
                    "INSERT INTO participants(participant_uid,participant_code,legacy_person_row_id,legacy_usercode,surname,firstname,age_years,gender_code,activity_level,life_id,weight_kg,height_cm,bmi_legacy) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uid, code, row["id"], row["Usercode"], row["Surname"], row["Firstname"], row["Age"],
                        _opt_text(row["Gender"]), _opt_text(row["Activity_Level"]), row["Life_ID"], row["Weight_KG"], row["Height_CM"], row["BMI"],
                    ),
                )
        detail = {
            "foods": con.execute("SELECT COUNT(*) FROM research_foods").fetchone()[0],
            "participants": con.execute("SELECT COUNT(*) FROM participants").fetchone()[0],
            "component_values": con.execute("SELECT COUNT(*) FROM food_component_values").fetchone()[0],
            "source_sha256": source_sha,
        }
        ResearchRepository._audit(con, "seed_from_legacy", "research_database", str(target), "migration", source="migration", detail=detail)
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        if con:
            con.close()
    if sha256_file(source) != source_sha:
        raise RuntimeError("Legacy source checksum changed during research-core initialization")
    return {"research_database": str(target), "legacy_source_sha256": source_sha, **detail, "source_unchanged": True}
