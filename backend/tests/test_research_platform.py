import hashlib
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from dietary_recall.constants import TABLE_FIELDS
from dietary_recall.imports import commit_import, read_xlsx_rows, stage_import
from dietary_recall.research_core import ResearchRepository, initialize_research_database


def make_legacy_fixture(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE Food(id INTEGER PRIMARY KEY, Food_ID INTEGER, Food_Name TEXT, Food_Description TEXT)")
        con.execute("INSERT INTO Food VALUES (1, 7, 'Fixture food', 'test evidence')")
        con.execute(
            "CREATE TABLE Person(id INTEGER PRIMARY KEY, Usercode INTEGER, Surname TEXT, Firstname TEXT, Age REAL, Gender TEXT, Activity_Level TEXT, Life_ID INTEGER, Weight_KG REAL, Height_CM REAL, BMI REAL)"
        )
        con.execute("INSERT INTO Person VALUES (1, 101, 'Study', 'Participant', 30, '1', '2', 4, 60, 165, 22.0)")
        for table, fields in TABLE_FIELDS.items():
            definitions = ",".join(f'"{field}" REAL' for field in fields)
            con.execute(f'CREATE TABLE "{table}"(id INTEGER PRIMARY KEY, Food_ID INTEGER, {definitions})')
            values = [10.0] + [None] * (len(fields) - 1)
            placeholders = ",".join("?" for _ in range(2 + len(fields)))
            con.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', [1, 7, *values])
        con.commit()
    finally:
        con.close()


def minimal_xlsx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Foods" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>food_name</t></is></c><c r="B1" t="inlineStr"><is><t>category</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>XLSX food</t></is></c><c r="B2" t="inlineStr"><is><t>fixture</t></is></c></row></sheetData></worksheet>',
        )
    return stream.getvalue()


class ResearchPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.legacy = self.root / "legacy.db"
        self.research = self.root / "research.db"
        make_legacy_fixture(self.legacy)
        self.legacy_sha = hashlib.sha256(self.legacy.read_bytes()).hexdigest()
        initialize_research_database(self.legacy, self.research)
        self.repo = ResearchRepository(self.research)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_preserves_source_and_missingness(self):
        summary = self.repo.summary()
        self.assertEqual(1, summary["foods"])
        self.assertEqual(1, summary["participants"])
        self.assertEqual(sum(map(len, TABLE_FIELDS.values())), summary["component_values"])
        self.assertEqual(len(TABLE_FIELDS), summary["reported_components"])
        self.assertEqual(self.legacy_sha, hashlib.sha256(self.legacy.read_bytes()).hexdigest())
        food = self.repo.list_foods()[0]
        components = self.repo.get_food(food["food_uid"])["components"]
        self.assertTrue(any(row["value"] is None and row["value_status"] == "missing" for row in components))

    def test_versioned_crud_and_recall_snapshot(self):
        food = self.repo.create_food({"food_name": "New research food"}, "test")
        self.assertEqual(8, food["legacy_food_id"])
        changed = self.repo.update_food(food["food_uid"], {"description": "v2"}, "test")
        self.assertEqual(2, changed["version"])
        nutrient = self.repo.list_nutrients()[0]
        self.repo.upsert_component(food["food_uid"], {"nutrient_code": nutrient["nutrient_code"], "value": 12.0}, "test")
        participant = self.repo.create_participant({"participant_code": "R-002"}, "test")
        recall = self.repo.create_recall({"participant_uid": participant["participant_uid"], "items": [{"food_uid": food["food_uid"], "amount_g": 50}]}, "test")
        self.assertEqual(1, len(recall["results"]))
        self.assertAlmostEqual(6.0, recall["results"][0]["value"])
        self.assertEqual("research_core_current", recall["results"][0]["calculation_mode"])

    def test_blank_food_ids_allocate_sequentially_and_conflict_is_atomic(self):
        csv_payload = b"legacy_food_id,food_name,description,preparation_method,category,source_scope\n,Batch A,,,,research_core\n,Batch B,,,,research_core\n"
        staged = stage_import(self.repo, csv_payload, "foods.csv", "foods", actor="test")
        result = commit_import(self.repo, staged["batch_uid"], "test")
        self.assertEqual(2, result["inserted"])
        ids = sorted(row["legacy_food_id"] for row in self.repo.list_foods(limit=10))
        self.assertEqual([7, 8, 9], ids)

        before = self.repo.summary()["foods"]
        conflict = b"legacy_food_id,food_name\n,Unique before rollback\n,Batch A\n"
        staged = stage_import(self.repo, conflict, "conflict.csv", "foods", conflict_policy="reject", actor="test")
        with self.assertRaises(ValueError):
            commit_import(self.repo, staged["batch_uid"], "test")
        self.assertEqual(before, self.repo.summary()["foods"])

    def test_audit_log_is_append_only(self):
        audit_uid = self.repo.list_audit(1)[0]["audit_uid"]
        with self.repo.connect() as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("DELETE FROM audit_log WHERE audit_uid=?", (audit_uid,))
        with self.repo.connect() as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE audit_log SET actor='changed' WHERE audit_uid=?", (audit_uid,))

    def test_xlsx_value_reader(self):
        rows = read_xlsx_rows(minimal_xlsx(), "Foods")
        self.assertEqual([{"food_name": "XLSX food", "category": "fixture"}], rows)


if __name__ == "__main__":
    unittest.main()
