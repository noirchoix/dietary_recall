"""Manifest-verified ingestion for licensed food-composition datasets."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any, Mapping

from .imports import _commit_row, _validate, read_csv_rows
from .research_core import _json, _uid
from .validated_research import PlatformRepository


MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
ENTITY_ORDER = {"external_foods": 1, "external_components": 2, "retention_factors": 3}


class LicensedDatasetService:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository

    def list_packages(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT p.*,s.source_name,r.release_label,a.license_name,a.permitted_use,a.redistribution_permitted FROM dataset_packages p JOIN source_releases r USING(source_release_uid) JOIN data_sources s USING(source_uid) JOIN source_license_acceptances a USING(license_acceptance_uid) WHERE p.project_uid=? ORDER BY p.created_at DESC",
                (project_uid,),
            )]

    def list_licenses(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute(
                "SELECT a.*,s.source_code,s.source_name FROM source_license_acceptances a JOIN data_sources s USING(source_uid) WHERE a.project_uid=? ORDER BY a.accepted_at DESC",
                (project_uid,),
            )]

    @staticmethod
    def _read_package(payload: bytes) -> tuple[dict[str, Any], list[tuple[int, str, str, list[dict[str, Any]]]], str, str]:
        if not payload or len(payload) > MAX_PACKAGE_BYTES:
            raise ValueError("Dataset package must be a non-empty ZIP no larger than 50 MiB")
        package_hash = hashlib.sha256(payload).hexdigest()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Dataset package exceeds the 100 MiB uncompressed limit")
            names: set[str] = set()
            for item in members:
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or item.is_dir():
                    if item.is_dir():
                        continue
                    raise ValueError("Unsafe path in dataset ZIP")
                if item.filename in names:
                    raise ValueError("Duplicate path in dataset ZIP")
                names.add(item.filename)
            if "manifest.json" not in names:
                raise ValueError("manifest.json is required at the ZIP root")
            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise ValueError("Unsupported dataset manifest schema_version")
            files = manifest.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("Manifest files must contain at least one CSV")
            declared = {"manifest.json"}
            parsed: list[tuple[int, str, str, list[dict[str, Any]]]] = []
            for item in files:
                if not isinstance(item, Mapping):
                    raise ValueError("Each manifest file entry must be an object")
                path = str(item.get("path") or "")
                entity = str(item.get("entity_type") or "")
                expected = str(item.get("sha256") or "").casefold()
                if entity not in ENTITY_ORDER or PurePosixPath(path).suffix.casefold() != ".csv" or path not in names:
                    raise ValueError(f"Invalid or missing declared CSV: {path}")
                if path in declared:
                    raise ValueError(f"Duplicate manifest path: {path}")
                data = archive.read(path)
                if not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"SHA-256 mismatch for {path}")
                declared.add(path)
                parsed.append((ENTITY_ORDER[entity], entity, path, read_csv_rows(data)))
            extras = names - declared
            if extras:
                raise ValueError(f"ZIP contains undeclared files: {', '.join(sorted(extras))}")
        return manifest, sorted(parsed), package_hash, hashlib.sha256(manifest_bytes).hexdigest()

    def stage_base64(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        try:
            payload = base64.b64decode(str(data.get("content_base64") or ""), validate=True)
        except Exception as exc:
            raise ValueError("content_base64 is not valid base64") from exc
        return self.stage(project_uid, payload, str(data.get("filename") or "licensed-dataset.zip"), actor)

    def stage(self, project_uid: str, payload: bytes, filename: str, actor: str) -> dict[str, Any]:
        manifest, files, package_hash, manifest_hash = self._read_package(payload)
        dataset_code = str(manifest.get("dataset_code") or "").strip()
        release_uid = str(manifest.get("source_release_uid") or "")
        license_data = manifest.get("license") or {}
        policy = str(manifest.get("conflict_policy") or "reject")
        if not dataset_code or not release_uid or policy not in {"reject", "insert_only", "upsert"}:
            raise ValueError("dataset_code, source_release_uid and a valid conflict_policy are required")
        if not isinstance(license_data, Mapping) or license_data.get("accepted") is not True:
            raise ValueError("The manifest must record explicit licence acceptance")
        license_hash = str(license_data.get("text_sha256") or "").casefold()
        permitted = str(license_data.get("permitted_use") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", license_hash):
            raise ValueError("license.text_sha256 must be a SHA-256 hex digest")
        if permitted not in {"research_only", "noncommercial", "commercial", "internal_only", "custom"}:
            raise ValueError("license.permitted_use is invalid")
        package_uid = _uid("dataset")
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "contributor"})
            release = con.execute(
                "SELECT r.*,s.source_uid,s.project_uid source_project_uid FROM source_releases r JOIN data_sources s USING(source_uid) WHERE r.source_release_uid=? AND (s.project_uid=? OR s.project_uid IS NULL)",
                (release_uid, project_uid),
            ).fetchone()
            if release is None:
                raise KeyError("Visible source release not found")
            if str(license_data.get("source_uid") or release["source_uid"]) != release["source_uid"]:
                raise ValueError("Licence source does not match the source release")
            acceptance_uid = _uid("license")
            con.execute(
                "INSERT INTO source_license_acceptances(license_acceptance_uid,project_uid,source_uid,license_name,license_url,license_text_sha256,permitted_use,redistribution_permitted,expires_at,accepted_by,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project_uid,source_uid,license_text_sha256) DO NOTHING",
                (acceptance_uid, project_uid, release["source_uid"], str(license_data.get("name") or "Documented licence"), license_data.get("url"), license_hash, permitted, int(bool(license_data.get("redistribution_permitted"))), license_data.get("expires_at"), actor, license_data.get("notes")),
            )
            acceptance = con.execute("SELECT * FROM source_license_acceptances WHERE project_uid=? AND source_uid=? AND license_text_sha256=?", (project_uid, release["source_uid"], license_hash)).fetchone()
            if acceptance is None or (acceptance["expires_at"] and acceptance["expires_at"] <= con.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]):
                raise ValueError("Licence acceptance is missing or expired")

            staged: list[dict[str, Any]] = []
            con.execute("SAVEPOINT dataset_validation")
            for order, entity, path, rows in files:
                for number, raw in enumerate(rows, start=2):
                    normalized, errors = _validate(entity, raw, con, project_uid)
                    if not errors:
                        try:
                            _commit_row(con, entity, normalized, policy, actor, package_uid, project_uid)
                        except Exception as exc:
                            errors.append(str(exc))
                    staged.append({"order": order, "entity": entity, "path": path, "row": number, "raw": raw, "errors": errors})
            con.execute("ROLLBACK TO dataset_validation")
            con.execute("RELEASE dataset_validation")
            valid = sum(not item["errors"] for item in staged)
            invalid = len(staged) - valid
            con.execute(
                "INSERT INTO dataset_packages(dataset_package_uid,project_uid,source_release_uid,license_acceptance_uid,dataset_code,source_filename,package_sha256,manifest_sha256,manifest_json,row_count,valid_count,invalid_count,status,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (package_uid, project_uid, release_uid, acceptance["license_acceptance_uid"], dataset_code, filename.split("/")[-1].split("\\")[-1], package_hash, manifest_hash, _json(manifest), len(staged), valid, invalid, "staged" if invalid == 0 else "rejected", actor),
            )
            con.executemany(
                "INSERT INTO dataset_package_rows(dataset_package_row_uid,dataset_package_uid,file_order,entity_type,source_path,row_number,normalized_json,validation_status,errors_json) VALUES (?,?,?,?,?,?,?,?,?)",
                [(_uid("drow"), package_uid, item["order"], item["entity"], item["path"], item["row"], _json(item["raw"]), "invalid" if item["errors"] else "valid", _json(item["errors"])) for item in staged],
            )
            self.repository._audit(con, "stage", "dataset_package", package_uid, actor, after={"dataset_code": dataset_code, "rows": len(staged), "valid": valid, "invalid": invalid}, detail={"package_sha256": package_hash, "license_text_sha256": license_hash})
            con.commit()
        return self.get_package(project_uid, package_uid, actor)

    def get_package(self, project_uid: str, package_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            package = con.execute("SELECT * FROM dataset_packages WHERE dataset_package_uid=? AND project_uid=?", (package_uid, project_uid)).fetchone()
            if package is None:
                raise KeyError("Dataset package not found")
            result = dict(package)
            result["rows"] = [dict(row) for row in con.execute("SELECT * FROM dataset_package_rows WHERE dataset_package_uid=? ORDER BY file_order,source_path,row_number", (package_uid,))]
            return result

    def commit(self, project_uid: str, package_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "contributor"})
            package = con.execute("SELECT * FROM dataset_packages WHERE dataset_package_uid=? AND project_uid=?", (package_uid, project_uid)).fetchone()
            if package is None or package["status"] != "staged" or package["invalid_count"]:
                raise ValueError("Only an entirely valid staged package can be committed")
            acceptance = con.execute("SELECT * FROM source_license_acceptances WHERE license_acceptance_uid=?", (package["license_acceptance_uid"],)).fetchone()
            if acceptance is None or (acceptance["expires_at"] and acceptance["expires_at"] <= con.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]):
                raise ValueError("Licence acceptance has expired")
            manifest = json.loads(package["manifest_json"])
            policy = str(manifest.get("conflict_policy") or "reject")
            self.repository.consume_usage_in_transaction(con, project_uid, "import_rows", int(package["row_count"]), actor, "dataset_package", package_uid, {"dataset_code": package["dataset_code"]})
            rows = list(con.execute("SELECT * FROM dataset_package_rows WHERE dataset_package_uid=? ORDER BY file_order,source_path,row_number", (package_uid,)))
            for row in rows:
                raw = json.loads(row["normalized_json"])
                normalized, errors = _validate(row["entity_type"], raw, con, project_uid)
                if errors:
                    raise ValueError(f"Dataset changed validation state at {row['source_path']}:{row['row_number']}: {'; '.join(errors)}")
                action, entity_uid = _commit_row(con, row["entity_type"], normalized, policy, actor, package_uid, project_uid)
                con.execute("UPDATE dataset_package_rows SET committed_entity_uid=? WHERE dataset_package_row_uid=?", (entity_uid, row["dataset_package_row_uid"]))
            con.execute("UPDATE dataset_packages SET status='committed',committed_at=CURRENT_TIMESTAMP WHERE dataset_package_uid=?", (package_uid,))
            self.repository._audit(con, "commit", "dataset_package", package_uid, actor, detail={"row_count": len(rows), "conflict_policy": policy})
            con.commit()
        return self.get_package(project_uid, package_uid, actor)

