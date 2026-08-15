"""v0.3 project platform and validated food-composition services."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .research_core import ResearchRepository, _json, _uid
from .v03_schema import DEFAULT_OWNER_UID, DEFAULT_PROJECT_UID, PLATFORM_SCHEMA_VERSION


READ_ROLES = {"owner", "admin", "contributor", "analyst", "viewer"}
WRITE_ROLES = {"owner", "admin", "contributor"}
CURATE_ROLES = {"owner", "admin", "analyst"}
ADMIN_ROLES = {"owner", "admin"}


class QuotaExceededError(ValueError):
    """Raised before a metered transaction when a project allowance is full."""

    def __init__(self, metric: str, used: int, limit: int, requested: int):
        self.metric = metric
        self.used = used
        self.limit = limit
        self.requested = requested
        super().__init__(f"{metric} quota exceeded: {used} used + {requested} requested > {limit} monthly limit")


def _period_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")


def _normalized_food_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    stop = {"prepared", "cooked", "fresh", "food", "dish"}
    return " ".join(word for word in words if word not in stop)


class PlatformRepository(ResearchRepository):
    """Project-scoped repository layered over the v0.2 Research Core.

    ``X-Research-Actor`` is an auditable local-mode identity selector, not a
    replacement for deployment authentication.  Production deployments should
    map a verified identity to the same membership checks.
    """

    def __init__(self, path: str | Path):
        super().__init__(path)
        with self.connect() as con:
            version = int(con.execute("PRAGMA user_version").fetchone()[0])
            if version < PLATFORM_SCHEMA_VERSION:
                raise ValueError("Database is v0.2; run dietary-recall platform-upgrade before using v0.3")

    @staticmethod
    def _actor_user(con: sqlite3.Connection, actor: str) -> sqlite3.Row:
        normalized = str(actor or "").strip().casefold()
        if normalized in {"", "local-researcher", "migration", "test"}:
            row = con.execute("SELECT * FROM app_users WHERE user_uid=?", (DEFAULT_OWNER_UID,)).fetchone()
            if row is None:
                row = con.execute("SELECT * FROM app_users ORDER BY active DESC,created_at LIMIT 1").fetchone()
        else:
            row = con.execute("SELECT * FROM app_users WHERE lower(email)=? AND active=1", (normalized,)).fetchone()
        if row is None:
            raise PermissionError("Actor is not a registered platform identity")
        return row

    @classmethod
    def _membership(cls, con: sqlite3.Connection, project_uid: str, actor: str, roles: set[str] = READ_ROLES) -> dict[str, Any]:
        user = cls._actor_user(con, actor)
        row = con.execute(
            "SELECT m.*,u.email,u.display_name,p.project_name,p.project_code "
            "FROM project_memberships m JOIN app_users u USING(user_uid) JOIN research_projects p USING(project_uid) "
            "WHERE m.project_uid=? AND m.user_uid=? AND m.status='active' AND p.status='active'",
            (project_uid, user["user_uid"]),
        ).fetchone()
        if row is None or row["project_role"] not in roles:
            raise PermissionError("The actor does not have the required project role")
        return dict(row)

    def default_project_uid(self) -> str:
        return DEFAULT_PROJECT_UID

    def list_projects(self, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            user = self._actor_user(con, actor)
            return [dict(row) for row in con.execute(
                "SELECT p.*,m.project_role,m.status membership_status,s.plan_code "
                "FROM project_memberships m JOIN research_projects p USING(project_uid) "
                "JOIN project_subscriptions s USING(project_uid) "
                "WHERE m.user_uid=? AND m.status='active' ORDER BY p.updated_at DESC",
                (user["user_uid"],),
            )]

    def create_project(self, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        name = str(data.get("project_name") or "").strip()
        if not name:
            raise ValueError("project_name is required")
        plan = str(data.get("plan_code") or "student")
        if plan not in {"student", "independent", "paid"}:
            raise ValueError("plan_code must be student, independent or paid")
        import_override = data.get("import_rows_override")
        calculation_override = data.get("calculation_runs_override")
        if plan != "paid" and (import_override not in (None, "") or calculation_override not in (None, "")):
            raise ValueError("Quota overrides are available only on the paid plan")
        if import_override not in (None, "") and int(import_override) <= 100:
            raise ValueError("Paid import override must be greater than 100")
        if calculation_override not in (None, "") and int(calculation_override) <= 100:
            raise ValueError("Paid calculation override must be greater than 100")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            user = self._actor_user(con, actor)
            definition = con.execute("SELECT * FROM plan_definitions WHERE plan_code=? AND active=1", (plan,)).fetchone()
            uid = _uid("proj")
            code = str(data.get("project_code") or _slug(name)[:28] or uuid.uuid4().hex[:8])
            con.execute(
                "INSERT INTO research_projects(project_uid,project_code,project_name,description,country_code,created_by) VALUES (?,?,?,?,?,?)",
                (uid, code, name, data.get("description"), data.get("country_code") or "NG", actor),
            )
            con.execute(
                "INSERT INTO project_subscriptions(project_uid,plan_code,import_rows_override,calculation_runs_override) VALUES (?,?,?,?)",
                (uid, plan, int(import_override) if import_override not in (None, "") else None, int(calculation_override) if calculation_override not in (None, "") else None),
            )
            con.execute(
                "INSERT INTO project_memberships(membership_uid,project_uid,user_uid,project_role,status) VALUES (?,?,?,?,?)",
                (_uid("mem"), uid, user["user_uid"], "owner", "active"),
            )
            project = self._one(con, "SELECT * FROM research_projects WHERE project_uid=?", (uid,))
            self._audit(con, "create", "project", uid, actor, after=project, detail={"plan_code": plan})
            con.commit()
            return self.project_context(uid, actor)

    def project_context(self, project_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            membership = self._membership(con, project_uid, actor)
            project = self._one(con, "SELECT * FROM research_projects WHERE project_uid=?", (project_uid,))
            project["membership"] = membership
            project["usage"] = self._usage_summary(con, project_uid)
            project["member_count"] = con.execute("SELECT COUNT(*) FROM project_memberships WHERE project_uid=? AND status='active'", (project_uid,)).fetchone()[0]
            return project

    def list_members(self, project_uid: str, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT m.*,u.email,u.display_name,u.active user_active FROM project_memberships m JOIN app_users u USING(user_uid) WHERE m.project_uid=? ORDER BY CASE m.project_role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,u.display_name",
                (project_uid,),
            )]

    def add_member(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        email = str(data.get("email") or "").strip().casefold()
        display = str(data.get("display_name") or "").strip()
        role = str(data.get("project_role") or "contributor")
        if "@" not in email:
            raise ValueError("valid email is required")
        if role not in {"admin", "contributor", "analyst", "viewer"}:
            raise ValueError("project_role must be admin, contributor, analyst or viewer")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, ADMIN_ROLES)
            usage = self._usage_summary(con, project_uid)
            member_count = con.execute("SELECT COUNT(*) FROM project_memberships WHERE project_uid=? AND status='active'", (project_uid,)).fetchone()[0]
            if member_count >= usage["max_members"]:
                raise QuotaExceededError("members", int(member_count), int(usage["max_members"]), 1)
            user = con.execute("SELECT * FROM app_users WHERE lower(email)=?", (email,)).fetchone()
            if user is None:
                if not display:
                    raise ValueError("display_name is required when creating a new contributor identity")
                user_uid = _uid("user")
                con.execute("INSERT INTO app_users(user_uid,email,display_name,role) VALUES (?,?,?,?)", (user_uid, email, display, "researcher"))
                user = con.execute("SELECT * FROM app_users WHERE user_uid=?", (user_uid,)).fetchone()
                self._version(con, "user", dict(user), actor)
            existing = con.execute("SELECT * FROM project_memberships WHERE project_uid=? AND user_uid=?", (project_uid, user["user_uid"])).fetchone()
            if existing:
                con.execute("UPDATE project_memberships SET project_role=?,status='active',updated_at=CURRENT_TIMESTAMP WHERE membership_uid=?", (role, existing["membership_uid"]))
                membership_uid = existing["membership_uid"]
            else:
                membership_uid = _uid("mem")
                con.execute("INSERT INTO project_memberships(membership_uid,project_uid,user_uid,project_role,status) VALUES (?,?,?,?,?)", (membership_uid, project_uid, user["user_uid"], role, "active"))
            result = self._one(con, "SELECT m.*,u.email,u.display_name FROM project_memberships m JOIN app_users u USING(user_uid) WHERE membership_uid=?", (membership_uid,))
            self._audit(con, "upsert", "project_membership", membership_uid, actor, after=result)
            con.commit()
            return result

    def update_membership(self, project_uid: str, membership_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        role = data.get("project_role")
        status = data.get("status")
        if role is not None and role not in {"admin", "contributor", "analyst", "viewer"}:
            raise ValueError("Owner transfer is not supported by this local release")
        if status is not None and status not in {"active", "suspended", "removed"}:
            raise ValueError("status must be active, suspended or removed")
        with self.connect() as con:
            self._membership(con, project_uid, actor, ADMIN_ROLES)
            before = self._one(con, "SELECT * FROM project_memberships WHERE membership_uid=? AND project_uid=?", (membership_uid, project_uid))
            if before["project_role"] == "owner":
                raise ValueError("The project owner cannot be changed or removed in this release")
            values: list[Any] = []
            assignments: list[str] = []
            if role is not None:
                assignments.append("project_role=?")
                values.append(role)
            if status is not None:
                assignments.append("status=?")
                values.append(status)
            if assignments:
                con.execute(f"UPDATE project_memberships SET {','.join(assignments)},updated_at=CURRENT_TIMESTAMP WHERE membership_uid=?", (*values, membership_uid))
            after = self._one(con, "SELECT * FROM project_memberships WHERE membership_uid=?", (membership_uid,))
            self._audit(con, "update", "project_membership", membership_uid, actor, before=before, after=after)
            return after

    @staticmethod
    def _usage_summary(con: sqlite3.Connection, project_uid: str, period: str | None = None) -> dict[str, Any]:
        period = period or _period_key()
        row = con.execute(
            "SELECT p.plan_code,d.display_name,d.monthly_import_rows,d.monthly_calculation_runs,d.max_projects,d.max_members,d.paid,p.import_rows_override,p.calculation_runs_override "
            "FROM project_subscriptions p JOIN plan_definitions d USING(plan_code) WHERE p.project_uid=?",
            (project_uid,),
        ).fetchone()
        if row is None:
            raise KeyError("Project subscription not found")
        used = {metric: 0 for metric in ("import_rows", "calculation_runs")}
        for item in con.execute("SELECT metric,COALESCE(SUM(quantity),0) used FROM usage_events WHERE project_uid=? AND period_key=? GROUP BY metric", (project_uid, period)):
            used[item["metric"]] = int(item["used"])
        import_limit = int(row["import_rows_override"] or row["monthly_import_rows"])
        calc_limit = int(row["calculation_runs_override"] or row["monthly_calculation_runs"])
        return {
            "period": period,
            "plan_code": row["plan_code"],
            "plan_name": row["display_name"],
            "paid": bool(row["paid"]),
            "max_projects": int(row["max_projects"]),
            "max_members": int(row["max_members"]),
            "import_rows": {"used": used["import_rows"], "limit": import_limit, "remaining": max(0, import_limit - used["import_rows"])},
            "calculation_runs": {"used": used["calculation_runs"], "limit": calc_limit, "remaining": max(0, calc_limit - used["calculation_runs"])},
        }

    def usage_summary(self, project_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return self._usage_summary(con, project_uid)

    @classmethod
    def consume_usage_in_transaction(
        cls,
        con: sqlite3.Connection,
        project_uid: str,
        metric: str,
        quantity: int,
        actor: str,
        reference_type: str,
        reference_uid: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if metric not in {"import_rows", "calculation_runs"} or quantity <= 0:
            raise ValueError("Invalid usage event")
        summary = cls._usage_summary(con, project_uid)
        bucket = summary[metric]
        if bucket["used"] + quantity > bucket["limit"]:
            raise QuotaExceededError(metric, bucket["used"], bucket["limit"], quantity)
        con.execute(
            "INSERT INTO usage_events(usage_uid,project_uid,metric,quantity,period_key,reference_type,reference_uid,actor,detail_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (_uid("usage"), project_uid, metric, quantity, summary["period"], reference_type, reference_uid, actor, _json(detail or {})),
        )

    def _assert_record(self, con: sqlite3.Connection, project_uid: str, entity_type: str, entity_uid: str) -> None:
        row = con.execute("SELECT 1 FROM project_records WHERE project_uid=? AND entity_type=? AND entity_uid=?", (project_uid, entity_type, entity_uid)).fetchone()
        if row is None:
            raise KeyError(f"{entity_type} record not found in project")

    def _link_record(self, project_uid: str, entity_type: str, entity_uid: str, actor: str) -> None:
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
            con.execute("INSERT INTO project_records(project_uid,entity_type,entity_uid,linked_by) VALUES (?,?,?,?)", (project_uid, entity_type, entity_uid, actor))

    def project_summary(self, project_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            counts = {}
            for label, entity_type in (("foods", "food"), ("participants", "participant"), ("experiments", "experiment"), ("recalls", "recall"), ("imports", "import_batch")):
                counts[label] = con.execute("SELECT COUNT(*) FROM project_records WHERE project_uid=? AND entity_type=?", (project_uid, entity_type)).fetchone()[0]
            counts.update({
                "canonical_nutrients": con.execute("SELECT COUNT(*) FROM canonical_nutrients WHERE active=1").fetchone()[0],
                "external_foods": con.execute("SELECT COUNT(*) FROM external_foods WHERE project_uid=?", (project_uid,)).fetchone()[0],
                "food_matches": con.execute("SELECT COUNT(*) FROM food_match_candidates WHERE project_uid=? AND review_status='accepted'", (project_uid,)).fetchone()[0],
                "recipes": con.execute("SELECT COUNT(*) FROM recipes WHERE project_uid=? AND status!='archived'", (project_uid,)).fetchone()[0],
                "calculation_runs": con.execute("SELECT COUNT(*) FROM recipe_calculation_runs WHERE project_uid=?", (project_uid,)).fetchone()[0],
                "audit_events": con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                "platform_mode": "research_platform",
                "legacy_compatibility": "available_read_only",
                "validated_research": "active_provenance_required",
                "schema_version": int(con.execute("PRAGMA user_version").fetchone()[0]),
                "usage": self._usage_summary(con, project_uid),
            })
            return counts

    def list_project_foods(self, project_uid: str, actor: str = "local-researcher", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            params: list[Any] = [project_uid]
            where = "pr.project_uid=? AND pr.entity_type='food' AND f.active=1"
            if query:
                where += " AND (f.food_name LIKE ? OR CAST(f.legacy_food_id AS TEXT)=?)"
                params += [f"%{query}%", query]
            params.append(max(1, min(limit, 1000)))
            return [dict(row) for row in con.execute(
                "SELECT f.*,SUM(CASE WHEN c.value IS NOT NULL THEN 1 ELSE 0 END) reported_components,COUNT(c.component_value_uid) total_components "
                "FROM project_records pr JOIN research_foods f ON f.food_uid=pr.entity_uid LEFT JOIN food_component_values c ON c.food_uid=f.food_uid "
                f"WHERE {where} GROUP BY f.food_uid ORDER BY f.food_name LIMIT ?",
                params,
            )]

    def get_project_food(self, project_uid: str, food_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            self._assert_record(con, project_uid, "food", food_uid)
            validated = [dict(row) for row in con.execute(
                "SELECT v.*,n.canonical_code,n.display_name,n.component_class,s.source_name,r.release_label "
                "FROM validated_food_component_values v JOIN canonical_nutrients n USING(canonical_nutrient_uid) "
                "LEFT JOIN source_releases r USING(source_release_uid) LEFT JOIN data_sources s USING(source_uid) "
                "WHERE v.project_uid=? AND v.research_food_uid=? ORDER BY n.component_class,n.display_name,v.created_at DESC",
                (project_uid, food_uid),
            )]
            accepted_match = con.execute(
                "SELECT m.*,e.food_name external_food_name,e.source_food_code,s.source_name,r.release_label "
                "FROM food_match_candidates m JOIN external_foods e USING(external_food_uid) JOIN source_releases r USING(source_release_uid) JOIN data_sources s USING(source_uid) "
                "WHERE m.project_uid=? AND m.research_food_uid=? AND m.review_status='accepted'",
                (project_uid, food_uid),
            ).fetchone()
        food = super().get_food(food_uid)
        food["validated_components"] = validated
        food["accepted_external_match"] = dict(accepted_match) if accepted_match else None
        return food

    def create_validated_component(self, project_uid: str, food_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        nutrient_uid = str(data.get("canonical_nutrient_uid") or "")
        evidence = str(data.get("evidence_class") or "study_measured")
        status = str(data.get("validation_status") or "provisional")
        if evidence not in {"study_measured", "nigerian_regional", "external_matched", "recipe_calculated", "transparent_imputation", "missing"}:
            raise ValueError("evidence_class is not recognized")
        if status not in {"provisional", "reviewed", "validated", "rejected"}:
            raise ValueError("validation_status is not recognized")
        value = data.get("value")
        value = None if value in (None, "") else float(value)
        if evidence == "missing" and value is not None:
            raise ValueError("missing evidence must not contain a numeric value")
        if evidence != "missing" and value is None:
            raise ValueError("a numeric value is required unless evidence_class is missing")
        release_uid = data.get("source_release_uid") or None
        if evidence in {"nigerian_regional", "external_matched", "transparent_imputation"} and not release_uid:
            raise ValueError("source_release_uid is required for this evidence class")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            membership = self._membership(con, project_uid, actor, WRITE_ROLES | {"analyst"})
            if status != "provisional" and membership["project_role"] not in CURATE_ROLES:
                raise PermissionError("Only an owner, admin or analyst may mark evidence reviewed/validated/rejected")
            self._assert_record(con, project_uid, "food", food_uid)
            nutrient = self._one(con, "SELECT * FROM canonical_nutrients WHERE canonical_nutrient_uid=? AND active=1", (nutrient_uid,))
            unit = str(data.get("unit") or nutrient["canonical_unit"])
            self._convert(con, 0.0 if value is None else value, unit, nutrient["canonical_unit"])
            if release_uid:
                self._one(con, "SELECT r.source_release_uid FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=? AND (s.project_uid=? OR s.project_uid IS NULL)", (release_uid, project_uid))
            uid = _uid("vval")
            con.execute(
                "INSERT INTO validated_food_component_values(validated_value_uid,project_uid,research_food_uid,canonical_nutrient_uid,value,unit,basis,evidence_class,validation_status,source_release_uid,source_record_uid,provenance_json,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, food_uid, nutrient_uid, value, unit, data.get("basis") or "per_100g_edible_portion", evidence, status, release_uid, data.get("source_record_uid"), _json(data.get("provenance") or {}), actor),
            )
            result = self._one(con, "SELECT * FROM validated_food_component_values WHERE validated_value_uid=?", (uid,))
            self._audit(con, "create", "validated_food_component", uid, actor, after=result)
            con.commit()
            return result

    def create_project_food(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
        result = super().create_food(data, actor)
        self._link_record(project_uid, "food", result["food_uid"], actor)
        return result

    def list_project_participants(self, project_uid: str, actor: str = "local-researcher", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            params: list[Any] = [project_uid]
            where = "pr.project_uid=? AND pr.entity_type='participant' AND p.active=1"
            if query:
                where += " AND (p.participant_code LIKE ? OR p.surname LIKE ? OR p.firstname LIKE ?)"
                params += [f"%{query}%"] * 3
            params.append(max(1, min(limit, 1000)))
            return [dict(row) for row in con.execute(f"SELECT p.* FROM project_records pr JOIN participants p ON p.participant_uid=pr.entity_uid WHERE {where} ORDER BY p.participant_code LIMIT ?", params)]

    def create_project_participant(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
        result = super().create_participant(data, actor)
        self._link_record(project_uid, "participant", result["participant_uid"], actor)
        return result

    def list_project_experiments(self, project_uid: str, actor: str = "local-researcher", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            params: list[Any] = [project_uid]
            where = "pr.project_uid=? AND pr.entity_type='experiment' AND e.active=1"
            if query:
                where += " AND (e.experiment_code LIKE ? OR e.sample_code LIKE ? OR f.food_name LIKE ?)"
                params += [f"%{query}%"] * 3
            params.append(max(1, min(limit, 1000)))
            return [dict(row) for row in con.execute(f"SELECT e.*,f.food_name FROM project_records pr JOIN experiments e ON e.experiment_uid=pr.entity_uid LEFT JOIN research_foods f USING(food_uid) WHERE {where} ORDER BY e.created_at DESC LIMIT ?", params)]

    def create_project_experiment(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
            if data.get("food_uid"):
                self._assert_record(con, project_uid, "food", str(data["food_uid"]))
        result = super().create_experiment(data, actor)
        self._link_record(project_uid, "experiment", result["experiment_uid"], actor)
        return result

    def list_project_recalls(self, project_uid: str, actor: str = "local-researcher", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT r.*,p.participant_code,COUNT(i.recall_item_uid) item_count FROM project_records pr JOIN recalls r ON r.recall_uid=pr.entity_uid JOIN participants p USING(participant_uid) LEFT JOIN recall_items i USING(recall_uid) WHERE pr.project_uid=? AND pr.entity_type='recall' GROUP BY r.recall_uid ORDER BY r.created_at DESC LIMIT ?",
                (project_uid, max(1, min(limit, 1000))),
            )]

    def create_project_recall(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
            self._assert_record(con, project_uid, "participant", str(data.get("participant_uid") or ""))
            for item in data.get("items") or []:
                self._assert_record(con, project_uid, "food", str(item.get("food_uid") or ""))
        result = super().create_recall(data, actor)
        self._link_record(project_uid, "recall", result["recall_uid"], actor)
        return result

    def list_units(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM unit_definitions WHERE active=1 ORDER BY quantity_dimension,unit_code")]

    @staticmethod
    def _convert(con: sqlite3.Connection, value: float, from_unit: str, to_unit: str) -> float:
        if from_unit == to_unit:
            return float(value)
        source = con.execute("SELECT * FROM unit_definitions WHERE unit_code=? AND active=1", (from_unit,)).fetchone()
        target = con.execute("SELECT * FROM unit_definitions WHERE unit_code=? AND active=1", (to_unit,)).fetchone()
        if source is None or target is None:
            raise ValueError("Unknown unit")
        if source["quantity_dimension"] != target["quantity_dimension"]:
            raise ValueError(f"Incompatible unit dimensions: {from_unit} and {to_unit}")
        if source["factor_to_dimension_base"] is None or target["factor_to_dimension_base"] is None:
            raise ValueError(f"No generic conversion is defined between {from_unit} and {to_unit}")
        return float(value) * float(source["factor_to_dimension_base"]) / float(target["factor_to_dimension_base"])

    def convert_unit(self, value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
        with self.connect() as con:
            result = self._convert(con, float(value), from_unit, to_unit)
        return {"input_value": float(value), "from_unit": from_unit, "to_unit": to_unit, "value": result}

    def list_canonical_nutrients(self, query: str = "") -> list[dict[str, Any]]:
        with self.connect() as con:
            params: list[Any] = []
            where = "WHERE n.active=1"
            if query:
                where += " AND (n.display_name LIKE ? OR n.canonical_code LIKE ? OR n.infoods_tag LIKE ?)"
                params += [f"%{query}%"] * 3
            return [dict(row) for row in con.execute(
                f"SELECT n.*,COUNT(m.mapping_uid) mapping_count FROM canonical_nutrients n LEFT JOIN nutrient_mappings m USING(canonical_nutrient_uid) {where} GROUP BY n.canonical_nutrient_uid ORDER BY n.component_class,n.display_name",
                params,
            )]

    def list_sources(self, project_uid: str, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT s.*,COUNT(r.source_release_uid) release_count FROM data_sources s LEFT JOIN source_releases r USING(source_uid) WHERE s.project_uid=? OR s.project_uid IS NULL GROUP BY s.source_uid ORDER BY s.project_uid IS NULL,s.source_name",
                (project_uid,),
            )]

    def list_source_releases(self, project_uid: str, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT r.*,s.source_code,s.source_name,s.source_type,s.project_uid FROM source_releases r JOIN data_sources s USING(source_uid) WHERE s.project_uid=? OR s.project_uid IS NULL ORDER BY r.created_at DESC",
                (project_uid,),
            )]

    def create_source(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        code = str(data.get("source_code") or "").strip()
        name = str(data.get("source_name") or "").strip()
        source_type = str(data.get("source_type") or "manual")
        if not code or not name:
            raise ValueError("source_code and source_name are required")
        if source_type not in {"study_analysis", "regional_table", "food_composition_table", "retention_table", "literature", "manual"}:
            raise ValueError("source_type is not recognized")
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
            uid = _uid("src")
            con.execute(
                "INSERT INTO data_sources(source_uid,project_uid,source_code,source_name,source_type,publisher,source_url,citation,license_notes) VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, code, name, source_type, data.get("publisher"), data.get("source_url"), data.get("citation"), data.get("license_notes")),
            )
            source = self._one(con, "SELECT * FROM data_sources WHERE source_uid=?", (uid,))
            self._audit(con, "create", "data_source", uid, actor, after=source)
            return source

    def add_source_release(self, project_uid: str, source_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        label = str(data.get("release_label") or "").strip()
        if not label:
            raise ValueError("release_label is required")
        with self.connect() as con:
            self._membership(con, project_uid, actor, WRITE_ROLES)
            source = self._one(con, "SELECT * FROM data_sources WHERE source_uid=? AND (project_uid=? OR project_uid IS NULL)", (source_uid, project_uid))
            uid = _uid("rel")
            con.execute(
                "INSERT INTO source_releases(source_release_uid,source_uid,release_label,release_date,retrieved_at,sha256,schema_notes,imported_by) VALUES (?,?,?,?,?,?,?,?)",
                (uid, source_uid, label, data.get("release_date"), data.get("retrieved_at"), data.get("sha256"), data.get("schema_notes"), actor),
            )
            release = self._one(con, "SELECT * FROM source_releases WHERE source_release_uid=?", (uid,))
            self._audit(con, "create", "source_release", uid, actor, after=release, detail={"source_code": source["source_code"]})
            return release

    def list_external_foods(self, project_uid: str, actor: str = "local-researcher", query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            params: list[Any] = [project_uid]
            where = "f.project_uid=?"
            if query:
                where += " AND (f.food_name LIKE ? OR f.local_name LIKE ? OR f.source_food_code LIKE ?)"
                params += [f"%{query}%"] * 3
            params.append(max(1, min(limit, 1000)))
            return [dict(row) for row in con.execute(
                f"SELECT f.*,s.source_name,r.release_label,COUNT(v.external_value_uid) component_count FROM external_foods f JOIN source_releases r USING(source_release_uid) JOIN data_sources s USING(source_uid) LEFT JOIN external_food_component_values v USING(external_food_uid) WHERE {where} GROUP BY f.external_food_uid ORDER BY f.food_name LIMIT ?",
                params,
            )]

    def create_external_food(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        name = str(data.get("food_name") or "").strip()
        code = str(data.get("source_food_code") or "").strip()
        release_uid = str(data.get("source_release_uid") or "")
        if not name or not code or not release_uid:
            raise ValueError("food_name, source_food_code and source_release_uid are required")
        components = list(data.get("components") or [])
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, WRITE_ROLES)
            self._one(con, "SELECT r.* FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=? AND (s.project_uid=? OR s.project_uid IS NULL)", (release_uid, project_uid))
            uid = _uid("xfood")
            con.execute(
                "INSERT INTO external_foods(external_food_uid,project_uid,source_release_uid,source_food_code,food_name,local_name,scientific_name,food_group,country_code,preparation_state,edible_portion_percent,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, release_uid, code, name, data.get("local_name"), data.get("scientific_name"), data.get("food_group"), data.get("country_code") or "NG", data.get("preparation_state"), data.get("edible_portion_percent"), _json(data.get("provenance") or {})),
            )
            for component in components:
                nutrient_uid = str(component.get("canonical_nutrient_uid") or "")
                nutrient = self._one(con, "SELECT * FROM canonical_nutrients WHERE canonical_nutrient_uid=? AND active=1", (nutrient_uid,))
                unit = str(component.get("unit") or nutrient["canonical_unit"])
                value = component.get("value")
                if value not in (None, ""):
                    self._convert(con, float(value), unit, nutrient["canonical_unit"])
                con.execute(
                    "INSERT INTO external_food_component_values(external_value_uid,external_food_uid,canonical_nutrient_uid,value,unit,basis,value_status,analytical_method,uncertainty,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_uid("xval"), uid, nutrient_uid, None if value in (None, "") else float(value), unit, component.get("basis") or "per_100g_edible_portion", component.get("value_status") or ("missing" if value in (None, "") else "reported"), component.get("analytical_method"), component.get("uncertainty"), _json(component.get("provenance") or {})),
                )
            food = self._one(con, "SELECT * FROM external_foods WHERE external_food_uid=?", (uid,))
            self._audit(con, "create", "external_food", uid, actor, after=food, detail={"component_count": len(components)})
            con.commit()
            return food

    def generate_food_matches(self, project_uid: str, research_food_uid: str | None = None, actor: str = "local-researcher", minimum_score: float = 0.35) -> dict[str, Any]:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, WRITE_ROLES)
            foods_sql = "SELECT f.* FROM project_records pr JOIN research_foods f ON f.food_uid=pr.entity_uid WHERE pr.project_uid=? AND pr.entity_type='food' AND f.active=1"
            params: list[Any] = [project_uid]
            if research_food_uid:
                foods_sql += " AND f.food_uid=?"
                params.append(research_food_uid)
            foods = list(con.execute(foods_sql, params))
            externals = list(con.execute("SELECT * FROM external_foods WHERE project_uid=?", (project_uid,)))
            created = 0
            for food in foods:
                left = _normalized_food_name(food["food_name"])
                left_tokens = set(left.split())
                scored: list[tuple[float, sqlite3.Row, dict[str, Any]]] = []
                for external in externals:
                    candidate_names = [external["food_name"], external["local_name"]]
                    right = max((_normalized_food_name(name or "") for name in candidate_names), key=lambda value: difflib.SequenceMatcher(None, left, value).ratio(), default="")
                    right_tokens = set(right.split())
                    sequence = difflib.SequenceMatcher(None, left, right).ratio()
                    union = left_tokens | right_tokens
                    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
                    exact = 1.0 if left and left == right else 0.0
                    score = max(exact, 0.65 * sequence + 0.35 * jaccard)
                    if score >= minimum_score:
                        scored.append((score, external, {"normalized_research": left, "normalized_external": right, "sequence": sequence, "token_jaccard": jaccard, "exact": bool(exact)}))
                for score, external, features in sorted(scored, key=lambda item: item[0], reverse=True)[:10]:
                    match_uid = _uid("match")
                    con.execute(
                        "INSERT INTO food_match_candidates(match_uid,project_uid,research_food_uid,external_food_uid,match_method,score,feature_json) VALUES (?,?,?,?,?,?,?) "
                        "ON CONFLICT(project_uid,research_food_uid,external_food_uid) DO UPDATE SET match_method=excluded.match_method,score=excluded.score,feature_json=excluded.feature_json",
                        (match_uid, project_uid, food["food_uid"], external["external_food_uid"], "normalized_name_v1", score, _json(features)),
                    )
                    created += 1
            self._audit(con, "generate", "food_match_candidates", research_food_uid, actor, detail={"candidate_writes": created, "minimum_score": minimum_score})
            con.commit()
            return {"candidate_writes": created, "research_foods_scanned": len(foods), "external_foods_scanned": len(externals), "method": "normalized_name_v1", "automatic_merge": False}

    def list_food_matches(self, project_uid: str, actor: str = "local-researcher", status: str = "candidate") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT m.*,r.food_name research_food_name,e.food_name external_food_name,e.local_name,s.source_name FROM food_match_candidates m JOIN research_foods r ON r.food_uid=m.research_food_uid JOIN external_foods e USING(external_food_uid) JOIN source_releases rel USING(source_release_uid) JOIN data_sources s USING(source_uid) WHERE m.project_uid=? AND m.review_status=? ORDER BY m.score DESC",
                (project_uid, status),
            )]

    def review_food_match(self, project_uid: str, match_uid: str, decision: str, notes: str | None, actor: str = "local-researcher") -> dict[str, Any]:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("decision must be accepted or rejected")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, CURATE_ROLES)
            before = self._one(con, "SELECT * FROM food_match_candidates WHERE match_uid=? AND project_uid=?", (match_uid, project_uid))
            if decision == "accepted":
                con.execute("UPDATE food_match_candidates SET review_status='superseded' WHERE project_uid=? AND research_food_uid=? AND review_status='accepted' AND match_uid<>?", (project_uid, before["research_food_uid"], match_uid))
            con.execute("UPDATE food_match_candidates SET review_status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,review_notes=? WHERE match_uid=?", (decision, actor, notes, match_uid))
            after = self._one(con, "SELECT * FROM food_match_candidates WHERE match_uid=?", (match_uid,))
            self._audit(con, "review", "food_match", match_uid, actor, before=before, after=after, detail={"automatic_merge": False})
            con.commit()
            return after

    def create_retention_factor(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        fraction = float(data.get("retention_fraction"))
        if not 0 <= fraction <= 1:
            raise ValueError("retention_fraction must be between 0 and 1")
        with self.connect() as con:
            self._membership(con, project_uid, actor, CURATE_ROLES)
            self._one(con, "SELECT canonical_nutrient_uid FROM canonical_nutrients WHERE canonical_nutrient_uid=?", (data.get("canonical_nutrient_uid"),))
            self._one(con, "SELECT r.source_release_uid FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=? AND (s.project_uid=? OR s.project_uid IS NULL)", (data.get("source_release_uid"), project_uid))
            uid = _uid("ret")
            con.execute(
                "INSERT INTO retention_factors(retention_factor_uid,project_uid,source_release_uid,cooking_method_code,food_group,canonical_nutrient_uid,retention_fraction,factor_status,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, data.get("source_release_uid"), data.get("cooking_method_code"), data.get("food_group"), data.get("canonical_nutrient_uid"), fraction, data.get("factor_status") or "reported", data.get("notes")),
            )
            result = self._one(con, "SELECT * FROM retention_factors WHERE retention_factor_uid=?", (uid,))
            self._audit(con, "create", "retention_factor", uid, actor, after=result)
            return result

    def list_retention_factors(self, project_uid: str, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT f.*,n.canonical_code,n.display_name,n.canonical_unit,s.source_name,r.release_label "
                "FROM retention_factors f JOIN canonical_nutrients n USING(canonical_nutrient_uid) "
                "JOIN source_releases r USING(source_release_uid) JOIN data_sources s USING(source_uid) "
                "WHERE f.project_uid=? ORDER BY f.cooking_method_code,f.food_group,n.display_name",
                (project_uid,),
            )]

    def list_recipes(self, project_uid: str, actor: str = "local-researcher") -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT r.*,COUNT(i.recipe_ingredient_uid) ingredient_count,MAX(c.created_at) last_calculated_at FROM recipes r LEFT JOIN recipe_ingredients i USING(recipe_uid) LEFT JOIN recipe_calculation_runs c USING(recipe_uid) WHERE r.project_uid=? AND r.status!='archived' GROUP BY r.recipe_uid ORDER BY r.updated_at DESC",
                (project_uid,),
            )]

    def create_recipe(self, project_uid: str, data: Mapping[str, Any], actor: str = "local-researcher") -> dict[str, Any]:
        name = str(data.get("recipe_name") or "").strip()
        method = str(data.get("cooking_method_code") or "").strip()
        final_weight = float(data.get("final_cooked_weight_g") or 0)
        ingredients = list(data.get("ingredients") or [])
        if not name or not method or final_weight <= 0 or not ingredients:
            raise ValueError("recipe_name, cooking_method_code, positive final_cooked_weight_g and ingredients are required")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, WRITE_ROLES)
            uid = _uid("recipe")
            code = str(data.get("recipe_code") or f"REC-{uuid.uuid4().hex[:8].upper()}")
            con.execute(
                "INSERT INTO recipes(recipe_uid,project_uid,recipe_code,recipe_name,description,cooking_method_code,final_cooked_weight_g,servings,status,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, code, name, data.get("description"), method, final_weight, data.get("servings"), data.get("status") or "draft", actor),
            )
            for index, ingredient in enumerate(ingredients, start=1):
                research_uid = ingredient.get("research_food_uid")
                external_uid = ingredient.get("external_food_uid")
                if bool(research_uid) == bool(external_uid):
                    raise ValueError("Each ingredient must reference exactly one research or external food")
                if research_uid:
                    self._assert_record(con, project_uid, "food", str(research_uid))
                    food = self._one(con, "SELECT food_name,category FROM research_foods WHERE food_uid=?", (research_uid,))
                    default_name, default_group = food["food_name"], food["category"]
                else:
                    food = self._one(con, "SELECT food_name,food_group FROM external_foods WHERE external_food_uid=? AND project_uid=?", (external_uid, project_uid))
                    default_name, default_group = food["food_name"], food["food_group"]
                con.execute(
                    "INSERT INTO recipe_ingredients(recipe_ingredient_uid,recipe_uid,ingredient_order,research_food_uid,external_food_uid,ingredient_name,input_weight_g,edible_fraction,food_group,notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_uid("ring"), uid, index, research_uid, external_uid, ingredient.get("ingredient_name") or default_name, float(ingredient.get("input_weight_g") or 0), float(ingredient.get("edible_fraction") or 1), ingredient.get("food_group") or default_group, ingredient.get("notes")),
                )
            recipe = self._one(con, "SELECT * FROM recipes WHERE recipe_uid=?", (uid,))
            self._audit(con, "create", "recipe", uid, actor, after=recipe, detail={"ingredient_count": len(ingredients)})
            con.commit()
            return self.get_recipe(project_uid, uid, actor)

    def get_recipe(self, project_uid: str, recipe_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            recipe = self._one(con, "SELECT * FROM recipes WHERE recipe_uid=? AND project_uid=?", (recipe_uid, project_uid))
            recipe["ingredients"] = [dict(row) for row in con.execute("SELECT * FROM recipe_ingredients WHERE recipe_uid=? ORDER BY ingredient_order", (recipe_uid,))]
            recipe["calculations"] = [dict(row) for row in con.execute("SELECT * FROM recipe_calculation_runs WHERE recipe_uid=? ORDER BY created_at DESC", (recipe_uid,))]
            return recipe

    def _ingredient_values(self, con: sqlite3.Connection, project_uid: str, ingredient: sqlite3.Row) -> list[dict[str, Any]]:
        if ingredient["external_food_uid"]:
            return [dict(row) | {"evidence": "external_source", "validation_status": "provisional"} for row in con.execute(
                "SELECT v.external_value_uid value_uid,v.canonical_nutrient_uid,v.value,v.unit,n.canonical_unit,n.ontology_status FROM external_food_component_values v JOIN canonical_nutrients n USING(canonical_nutrient_uid) WHERE v.external_food_uid=? AND v.value IS NOT NULL",
                (ingredient["external_food_uid"],),
            )]
        research_uid = ingredient["research_food_uid"]
        selected: dict[str, dict[str, Any]] = {}
        for row in con.execute(
            "SELECT v.validated_value_uid value_uid,v.canonical_nutrient_uid,v.value,v.unit,n.canonical_unit,n.ontology_status,v.evidence_class evidence,v.validation_status "
            "FROM validated_food_component_values v JOIN canonical_nutrients n USING(canonical_nutrient_uid) "
            "WHERE v.project_uid=? AND v.research_food_uid=? AND v.value IS NOT NULL AND v.validation_status!='rejected' "
            "ORDER BY CASE v.validation_status WHEN 'validated' THEN 0 WHEN 'reviewed' THEN 1 ELSE 2 END,CASE v.evidence_class WHEN 'study_measured' THEN 0 WHEN 'nigerian_regional' THEN 1 WHEN 'external_matched' THEN 2 WHEN 'recipe_calculated' THEN 3 ELSE 4 END,v.created_at DESC",
            (project_uid, research_uid),
        ):
            selected.setdefault(row["canonical_nutrient_uid"], dict(row))
        for row in con.execute(
            "SELECT c.component_value_uid value_uid,m.canonical_nutrient_uid,c.value,c.unit,n.canonical_unit,n.ontology_status,'legacy_reported' evidence,'provisional' validation_status "
            "FROM food_component_values c JOIN nutrient_mappings m ON m.source_system='legacy_phd' AND m.source_nutrient_code=c.nutrient_code JOIN canonical_nutrients n USING(canonical_nutrient_uid) "
            "WHERE c.food_uid=? AND c.value IS NOT NULL AND m.mapping_status!='rejected'",
            (research_uid,),
        ):
            selected.setdefault(row["canonical_nutrient_uid"], dict(row))
        accepted = con.execute("SELECT external_food_uid FROM food_match_candidates WHERE project_uid=? AND research_food_uid=? AND review_status='accepted'", (project_uid, research_uid)).fetchone()
        if accepted:
            for row in con.execute(
                "SELECT v.external_value_uid value_uid,v.canonical_nutrient_uid,v.value,v.unit,n.canonical_unit,n.ontology_status,'accepted_external_match' evidence,'reviewed' validation_status FROM external_food_component_values v JOIN canonical_nutrients n USING(canonical_nutrient_uid) WHERE v.external_food_uid=? AND v.value IS NOT NULL",
                (accepted[0],),
            ):
                selected.setdefault(row["canonical_nutrient_uid"], dict(row))
        return list(selected.values())

    def calculate_recipe(self, project_uid: str, recipe_uid: str, policy: str = "strict", actor: str = "local-researcher") -> dict[str, Any]:
        if policy not in {"strict", "best_available"}:
            raise ValueError("policy must be strict or best_available")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._membership(con, project_uid, actor, WRITE_ROLES | {"analyst"})
            recipe = self._one(con, "SELECT * FROM recipes WHERE recipe_uid=? AND project_uid=? AND status!='archived'", (recipe_uid, project_uid))
            ingredients = list(con.execute("SELECT * FROM recipe_ingredients WHERE recipe_uid=? ORDER BY ingredient_order", (recipe_uid,)))
            totals: dict[str, float] = defaultdict(float)
            units: dict[str, str] = {}
            lineage: dict[str, list[dict[str, Any]]] = defaultdict(list)
            retention_states: dict[str, set[str]] = defaultdict(set)
            warnings: list[str] = []
            all_reviewed = True
            snapshot: list[dict[str, Any]] = [{"recipe_uid": recipe_uid, "version": recipe["version"], "final_cooked_weight_g": recipe["final_cooked_weight_g"]}]
            missing_factors: list[str] = []
            for ingredient in ingredients:
                edible_g = float(ingredient["input_weight_g"]) * float(ingredient["edible_fraction"])
                values = self._ingredient_values(con, project_uid, ingredient)
                if not values:
                    warnings.append(f"No composition values for ingredient: {ingredient['ingredient_name']}")
                    all_reviewed = False
                for value in values:
                    nutrient_uid = value["canonical_nutrient_uid"]
                    canonical_value = self._convert(con, float(value["value"]), value["unit"], value["canonical_unit"])
                    factor = con.execute(
                        "SELECT * FROM retention_factors WHERE project_uid=? AND cooking_method_code=? AND canonical_nutrient_uid=? AND (food_group=? OR food_group IS NULL) ORDER BY food_group IS NULL,CASE factor_status WHEN 'analytical' THEN 0 WHEN 'reported' THEN 1 WHEN 'imputed_by_source' THEN 2 ELSE 3 END LIMIT 1",
                        (project_uid, recipe["cooking_method_code"], nutrient_uid, ingredient["food_group"]),
                    ).fetchone()
                    if factor is None:
                        if policy == "strict":
                            missing_factors.append(f"{ingredient['ingredient_name']} / {nutrient_uid}")
                            continue
                        fraction = 1.0
                        factor_uid = None
                        factor_status = "assumed_no_factor"
                        all_reviewed = False
                        warnings.append(f"Retention assumed 1.0 for {ingredient['ingredient_name']} / {nutrient_uid}")
                    else:
                        fraction = float(factor["retention_fraction"])
                        factor_uid = factor["retention_factor_uid"]
                        factor_status = factor["factor_status"]
                        if factor_status in {"imputed_by_source", "project_assumption"}:
                            all_reviewed = False
                    amount = canonical_value * edible_g / 100.0 * fraction
                    totals[nutrient_uid] += amount
                    units[nutrient_uid] = value["canonical_unit"]
                    retention_states[nutrient_uid].add(factor_status)
                    item = {
                        "ingredient_uid": ingredient["recipe_ingredient_uid"],
                        "ingredient_name": ingredient["ingredient_name"],
                        "edible_weight_g": edible_g,
                        "composition_value_uid": value["value_uid"],
                        "composition_per_100g": canonical_value,
                        "evidence": value["evidence"],
                        "retention_factor_uid": factor_uid,
                        "retention_fraction": fraction,
                        "retained_amount": amount,
                    }
                    lineage[nutrient_uid].append(item)
                    snapshot.append(item)
                    if value["validation_status"] not in {"reviewed", "validated"} or value["ontology_status"] != "reviewed":
                        all_reviewed = False
            if missing_factors:
                raise ValueError("Strict calculation requires explicit retention factors: " + "; ".join(missing_factors[:20]))
            if not totals:
                raise ValueError("Recipe has no calculable nutrient values")
            calculation_uid = _uid("calc")
            self.consume_usage_in_transaction(con, project_uid, "calculation_runs", 1, actor, "recipe", recipe_uid, {"policy": policy})
            validation_status = "validated" if policy == "strict" and all_reviewed else "exploratory"
            if validation_status == "exploratory":
                warnings.append("Result is exploratory until nutrient mappings, source values and factors complete scientific review.")
            snapshot_hash = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
            con.execute(
                "INSERT INTO recipe_calculation_runs(calculation_uid,project_uid,recipe_uid,recipe_version,policy,validation_status,input_snapshot_hash,warnings_json,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                (calculation_uid, project_uid, recipe_uid, recipe["version"], policy, validation_status, snapshot_hash, _json(sorted(set(warnings))), actor),
            )
            final_weight = float(recipe["final_cooked_weight_g"])
            for nutrient_uid, total in totals.items():
                con.execute(
                    "INSERT INTO recipe_calculation_results(calculation_uid,canonical_nutrient_uid,total_recipe_value,per_100g_value,unit,retention_status,lineage_json) VALUES (?,?,?,?,?,?,?)",
                    (calculation_uid, nutrient_uid, total, total / final_weight * 100.0, units[nutrient_uid], ",".join(sorted(retention_states[nutrient_uid])), _json(lineage[nutrient_uid])),
                )
            self._audit(con, "calculate", "recipe", recipe_uid, actor, detail={"calculation_uid": calculation_uid, "policy": policy, "validation_status": validation_status, "nutrient_count": len(totals), "input_snapshot_hash": snapshot_hash})
            con.commit()
            return self.get_calculation(project_uid, calculation_uid, actor)

    def get_calculation(self, project_uid: str, calculation_uid: str, actor: str = "local-researcher") -> dict[str, Any]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            run = self._one(con, "SELECT c.*,r.recipe_name,r.recipe_code,r.final_cooked_weight_g FROM recipe_calculation_runs c JOIN recipes r USING(recipe_uid) WHERE c.calculation_uid=? AND c.project_uid=?", (calculation_uid, project_uid))
            run["warnings"] = json.loads(run.pop("warnings_json"))
            run["results"] = [dict(row) for row in con.execute(
                "SELECT v.*,n.canonical_code,n.display_name,n.component_class FROM recipe_calculation_results v JOIN canonical_nutrients n USING(canonical_nutrient_uid) WHERE v.calculation_uid=? ORDER BY n.component_class,n.display_name",
                (calculation_uid,),
            )]
            return run

    def list_project_imports(self, project_uid: str, actor: str = "local-researcher", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute("SELECT * FROM import_batches WHERE project_uid=? ORDER BY created_at DESC LIMIT ?", (project_uid, max(1, min(limit, 1000))))]

    def list_project_audit(self, project_uid: str, actor: str = "local-researcher", limit: int = 200) -> list[dict[str, Any]]:
        """Return audit events attributable to the selected workspace."""
        with self.connect() as con:
            self._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT a.* FROM audit_log a "
                "LEFT JOIN project_records pr ON pr.entity_type=a.entity_type AND pr.entity_uid=a.entity_uid "
                "WHERE pr.project_uid=? "
                "OR (a.entity_type='project' AND a.entity_uid=?) "
                "OR EXISTS(SELECT 1 FROM project_memberships m WHERE m.membership_uid=a.entity_uid AND m.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM data_sources s WHERE s.source_uid=a.entity_uid AND s.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=a.entity_uid AND s.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM external_foods e WHERE e.external_food_uid=a.entity_uid AND e.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM food_match_candidates m WHERE m.match_uid=a.entity_uid AND m.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM retention_factors f WHERE f.retention_factor_uid=a.entity_uid AND f.project_uid=?) "
                "OR EXISTS(SELECT 1 FROM recipes r WHERE r.recipe_uid=a.entity_uid AND r.project_uid=?) "
                "ORDER BY a.created_at DESC,a.rowid DESC LIMIT ?",
                (project_uid, project_uid, project_uid, project_uid, project_uid, project_uid, project_uid, project_uid, project_uid, max(1, min(limit, 1000))),
            )]
