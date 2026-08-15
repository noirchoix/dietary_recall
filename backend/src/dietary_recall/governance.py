"""Specialist credential and scientific-review workflow for v0.4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .research_core import _json, _uid
from .validated_research import ADMIN_ROLES, PlatformRepository


REVIEW_ROLES = {"owner", "admin", "analyst"}
SPECIALIZATIONS = {
    "food_composition",
    "nutritional_biochemistry",
    "dietary_assessment",
    "analytical_chemistry",
}
TARGET_TABLES = {
    "canonical_nutrient": ("canonical_nutrients", "canonical_nutrient_uid"),
    "nutrient_mapping": ("nutrient_mappings", "mapping_uid"),
    "validated_value": ("validated_food_component_values", "validated_value_uid"),
    "retention_factor": ("retention_factors", "retention_factor_uid"),
    "source_release": ("source_releases", "source_release_uid"),
    "matching_model": ("matching_models", "matching_model_uid"),
}


class GovernanceService:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository

    def list_specialists(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT s.*,u.email,u.display_name FROM specialist_profiles s JOIN app_users u USING(user_uid) ORDER BY s.created_at DESC"
            )]

    def register_specialist(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        specialization = str(data.get("specialization") or "")
        credentials = str(data.get("credentials") or "").strip()
        email = str(data.get("email") or actor).strip().casefold()
        if specialization not in SPECIALIZATIONS or not credentials:
            raise ValueError("A supported specialization and credential summary are required")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            user = con.execute("SELECT * FROM app_users WHERE lower(email)=? AND active=1", (email,)).fetchone()
            if user is None:
                raise KeyError("Active platform user not found")
            if email != actor.casefold():
                self.repository._membership(con, project_uid, actor, ADMIN_ROLES)
            uid = _uid("spec")
            con.execute(
                "INSERT INTO specialist_profiles(specialist_uid,user_uid,specialization,credentials,organization,country_code) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(user_uid) DO UPDATE SET specialization=excluded.specialization,credentials=excluded.credentials,organization=excluded.organization,country_code=excluded.country_code,status='pending',verified_by=NULL,verified_at=NULL,updated_at=CURRENT_TIMESTAMP",
                (uid, user["user_uid"], specialization, credentials, data.get("organization"), data.get("country_code") or "NG"),
            )
            result = dict(con.execute("SELECT s.*,u.email,u.display_name FROM specialist_profiles s JOIN app_users u USING(user_uid) WHERE s.user_uid=?", (user["user_uid"],)).fetchone())
            self.repository._audit(con, "submit", "specialist_profile", result["specialist_uid"], actor, after=result)
            return result

    def verify_specialist(self, project_uid: str, specialist_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        status = str(data.get("status") or "verified")
        if status not in {"verified", "suspended", "expired"}:
            raise ValueError("status must be verified, suspended or expired")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor, ADMIN_ROLES)
            before = con.execute("SELECT s.*,u.email FROM specialist_profiles s JOIN app_users u USING(user_uid) WHERE specialist_uid=?", (specialist_uid,)).fetchone()
            if before is None:
                raise KeyError("Specialist profile not found")
            if before["email"].casefold() == actor.casefold():
                raise PermissionError("A specialist cannot verify their own credentials")
            con.execute(
                "UPDATE specialist_profiles SET status=?,verified_by=?,verified_at=CASE WHEN ?='verified' THEN CURRENT_TIMESTAMP ELSE verified_at END,expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE specialist_uid=?",
                (status, actor, status, data.get("expires_at"), specialist_uid),
            )
            result = dict(con.execute("SELECT s.*,u.email,u.display_name FROM specialist_profiles s JOIN app_users u USING(user_uid) WHERE specialist_uid=?", (specialist_uid,)).fetchone())
            self.repository._audit(con, "verify", "specialist_profile", specialist_uid, actor, before=dict(before), after=result, detail={"notes": data.get("notes")})
            return result

    @staticmethod
    def _target(con: Any, target_type: str, target_uid: str, project_uid: str) -> dict[str, Any]:
        if target_type not in TARGET_TABLES:
            raise ValueError("Unsupported review target type")
        table, key = TARGET_TABLES[target_type]
        row = con.execute(f"SELECT * FROM {table} WHERE {key}=?", (target_uid,)).fetchone()
        if row is None:
            raise KeyError("Review target not found")
        value = dict(row)
        if "project_uid" in value and value["project_uid"] != project_uid:
            raise KeyError("Review target is not in this project")
        if target_type == "source_release":
            source = con.execute("SELECT project_uid FROM data_sources WHERE source_uid=?", (value["source_uid"],)).fetchone()
            if source and source[0] not in {None, project_uid}:
                raise KeyError("Review target is not visible in this project")
        return value

    def list_cases(self, project_uid: str, actor: str, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "approved", "rejected", "withdrawn", "all"}:
            raise ValueError("Invalid review status")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            where = "c.project_uid=?" + ("" if status == "all" else " AND c.status=?")
            params = (project_uid,) if status == "all" else (project_uid, status)
            return [dict(row) for row in con.execute(
                f"SELECT c.*,d.decision,d.rationale,d.specialist_uid FROM scientific_review_cases c LEFT JOIN scientific_review_decisions d USING(review_case_uid) WHERE {where} ORDER BY c.submitted_at DESC",
                params,
            )]

    def submit_case(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        target_type = str(data.get("target_type") or "")
        target_uid = str(data.get("target_uid") or "")
        evidence = data.get("evidence") or {}
        if not target_uid or not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("target_uid and non-empty evidence are required")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "contributor", "analyst"})
            target = self._target(con, target_type, target_uid, project_uid)
            uid = _uid("review")
            con.execute(
                "INSERT INTO scientific_review_cases(review_case_uid,project_uid,target_type,target_uid,proposed_json,evidence_json,submitted_by) VALUES (?,?,?,?,?,?,?)",
                (uid, project_uid, target_type, target_uid, _json(data.get("proposed") or {}), _json(evidence), actor),
            )
            result = dict(con.execute("SELECT * FROM scientific_review_cases WHERE review_case_uid=?", (uid,)).fetchone())
            self.repository._audit(con, "submit", "scientific_review_case", uid, actor, before=target, after=result)
            return result

    def decide_case(self, project_uid: str, review_case_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        decision = str(data.get("decision") or "")
        rationale = str(data.get("rationale") or "").strip()
        if decision not in {"approved", "rejected"} or len(rationale) < 10:
            raise ValueError("approved/rejected and a substantive rationale are required")
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, REVIEW_ROLES)
            profile = con.execute(
                "SELECT s.*,u.email FROM specialist_profiles s JOIN app_users u USING(user_uid) WHERE lower(u.email)=lower(?) AND s.status='verified' AND (s.expires_at IS NULL OR s.expires_at>CURRENT_TIMESTAMP)",
                (actor,),
            ).fetchone()
            if profile is None:
                raise PermissionError("A current, independently verified specialist profile is required")
            case = con.execute("SELECT * FROM scientific_review_cases WHERE review_case_uid=? AND project_uid=? AND status='pending'", (review_case_uid, project_uid)).fetchone()
            if case is None:
                raise KeyError("Pending review case not found")
            if case["submitted_by"].casefold() == actor.casefold():
                raise PermissionError("The submitter cannot decide their own review case")
            before = self._target(con, case["target_type"], case["target_uid"], project_uid)
            proposed = json.loads(case["proposed_json"] or "{}")
            if decision == "approved":
                self._apply_approval(con, case["target_type"], case["target_uid"], proposed)
            else:
                self._apply_rejection(con, case["target_type"], case["target_uid"])
            if case["target_type"] == "matching_model":
                con.execute("UPDATE matching_models SET reviewed_by=?,review_rationale=? WHERE matching_model_uid=?", (actor, rationale, case["target_uid"]))
            con.execute("UPDATE scientific_review_cases SET status=?,decided_at=CURRENT_TIMESTAMP WHERE review_case_uid=?", (decision, review_case_uid))
            con.execute(
                "INSERT INTO scientific_review_decisions(decision_uid,review_case_uid,specialist_uid,decision,rationale,evidence_checked_json) VALUES (?,?,?,?,?,?)",
                (_uid("decision"), review_case_uid, profile["specialist_uid"], decision, rationale, _json(data.get("evidence_checked") or {})),
            )
            after = self._target(con, case["target_type"], case["target_uid"], project_uid)
            self.repository._audit(con, "scientific_review", case["target_type"], case["target_uid"], actor, before=before, after=after, detail={"decision": decision, "review_case_uid": review_case_uid})
            con.commit()
            return dict(con.execute("SELECT c.*,d.decision,d.rationale,d.specialist_uid FROM scientific_review_cases c JOIN scientific_review_decisions d USING(review_case_uid) WHERE c.review_case_uid=?", (review_case_uid,)).fetchone())

    @staticmethod
    def _apply_approval(con: Any, target_type: str, target_uid: str, proposed: Mapping[str, Any]) -> None:
        if target_type == "canonical_nutrient":
            allowed = {key: proposed[key] for key in ("display_name", "infoods_tag", "chemical_form", "notes") if key in proposed}
            assignments = [f"{key}=?" for key in allowed] + ["ontology_status='reviewed'"]
            con.execute(f"UPDATE canonical_nutrients SET {','.join(assignments)} WHERE canonical_nutrient_uid=?", (*allowed.values(), target_uid))
        elif target_type == "nutrient_mapping":
            con.execute("UPDATE nutrient_mappings SET mapping_status='reviewed',mapping_notes=COALESCE(?,mapping_notes) WHERE mapping_uid=?", (proposed.get("mapping_notes"), target_uid))
        elif target_type == "validated_value":
            con.execute("UPDATE validated_food_component_values SET validation_status='validated' WHERE validated_value_uid=?", (target_uid,))
        elif target_type == "retention_factor":
            con.execute("UPDATE retention_factors SET scientific_review_status='reviewed' WHERE retention_factor_uid=?", (target_uid,))
        elif target_type == "source_release":
            con.execute("UPDATE source_releases SET scientific_review_status='reviewed' WHERE source_release_uid=?", (target_uid,))
        elif target_type == "matching_model":
            con.execute("UPDATE matching_models SET review_status='approved',reviewed_at=CURRENT_TIMESTAMP WHERE matching_model_uid=?", (target_uid,))

    @staticmethod
    def _apply_rejection(con: Any, target_type: str, target_uid: str) -> None:
        if target_type == "nutrient_mapping":
            con.execute("UPDATE nutrient_mappings SET mapping_status='rejected' WHERE mapping_uid=?", (target_uid,))
        elif target_type == "validated_value":
            con.execute("UPDATE validated_food_component_values SET validation_status='rejected' WHERE validated_value_uid=?", (target_uid,))
        elif target_type == "retention_factor":
            con.execute("UPDATE retention_factors SET scientific_review_status='rejected' WHERE retention_factor_uid=?", (target_uid,))
        elif target_type == "source_release":
            con.execute("UPDATE source_releases SET scientific_review_status='rejected' WHERE source_release_uid=?", (target_uid,))
        elif target_type == "matching_model":
            con.execute("UPDATE matching_models SET review_status='rejected',reviewed_at=CURRENT_TIMESTAMP WHERE matching_model_uid=?", (target_uid,))
