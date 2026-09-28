import hashlib
import tempfile
import unittest
from pathlib import Path

from dietary_recall.imports import commit_import, stage_import
from dietary_recall.research_core import initialize_research_database
from dietary_recall.v03_schema import DEFAULT_PROJECT_UID, upgrade_platform_database
from dietary_recall.validated_research import PlatformRepository, QuotaExceededError
from test_research_platform import make_legacy_fixture


class V03PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.legacy = self.root / "legacy.db"
        self.v02 = self.root / "research-v02.db"
        self.v03 = self.root / "research-v03.db"
        make_legacy_fixture(self.legacy)
        initialize_research_database(self.legacy, self.v02)
        self.v02_sha = hashlib.sha256(self.v02.read_bytes()).hexdigest()
        upgrade_platform_database(self.v02, self.v03)
        self.repo = PlatformRepository(self.v03)

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_first_upgrade_and_default_workspace(self):
        self.assertEqual(self.v02_sha, hashlib.sha256(self.v02.read_bytes()).hexdigest())
        summary = self.repo.project_summary(DEFAULT_PROJECT_UID, "test")
        self.assertEqual(3, summary["schema_version"])
        self.assertEqual(1, summary["foods"])
        self.assertEqual("active_provenance_required", summary["validated_research"])
        self.assertGreater(summary["canonical_nutrients"], 40)
        self.assertAlmostEqual(1000.0, self.repo.convert_unit(1, "mg", "mcg")["value"])
        with self.assertRaises(ValueError):
            self.repo.convert_unit(1, "IU", "mcg")

    def test_project_records_are_isolated(self):
        project = self.repo.create_project({"project_name": "Student study", "project_code": "STUDENT-1", "plan_code": "student"}, "test")
        created = self.repo.create_project_food(project["project_uid"], {"food_name": "Project-only food"}, "test")
        default_names = {row["food_name"] for row in self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")}
        new_names = {row["food_name"] for row in self.repo.list_project_foods(project["project_uid"], "test")}
        self.assertNotIn(created["food_name"], default_names)
        self.assertIn(created["food_name"], new_names)

    def test_paid_project_cannot_be_self_provisioned(self):
        with self.assertRaises(ValueError):
            self.repo.create_project(
                {"project_name": "Unverified paid project", "plan_code": "paid"},
                "test",
            )

    def test_project_count_limit_is_enforced_per_plan(self):
        self.repo.create_project(
            {"project_name": "Allowed student project", "project_code": "STUDENT-LIMIT-1", "plan_code": "student"},
            "test",
        )
        with self.assertRaises(QuotaExceededError):
            self.repo.create_project(
                {"project_name": "Excess student project", "project_code": "STUDENT-LIMIT-2", "plan_code": "student"},
                "test",
            )
        with self.assertRaises(ValueError):
            self.repo.create_project(
                {
                    "project_name": "Unverified override",
                    "plan_code": "independent",
                    "import_rows_override": 1000,
                },
                "test",
            )

    def test_import_quota_is_atomic_and_staging_is_free(self):
        project = self.repo.create_project({"project_name": "Quota study", "project_code": "QUOTA-1", "plan_code": "student"}, "test")
        rows = ["legacy_food_id,food_name"] + [f",Quota food {index}" for index in range(51)]
        staged = stage_import(self.repo, ("\n".join(rows) + "\n").encode(), "foods.csv", "foods", actor="test", project_uid=project["project_uid"])
        self.assertEqual(0, self.repo.usage_summary(project["project_uid"], "test")["import_rows"]["used"])
        with self.assertRaises(QuotaExceededError):
            commit_import(self.repo, staged["batch_uid"], "test")
        self.assertEqual(0, self.repo.usage_summary(project["project_uid"], "test")["import_rows"]["used"])
        self.assertEqual(0, len(self.repo.list_project_foods(project["project_uid"], "test")))

        staged = stage_import(self.repo, b"legacy_food_id,food_name\n,Allowed A\n,Allowed B\n", "foods.csv", "foods", actor="test", project_uid=project["project_uid"])
        result = commit_import(self.repo, staged["batch_uid"], "test")
        self.assertEqual(2, result["inserted"])
        self.assertEqual(2, self.repo.usage_summary(project["project_uid"], "test")["import_rows"]["used"])

    def test_matching_requires_review_and_does_not_overwrite_components(self):
        source = self.repo.create_source(DEFAULT_PROJECT_UID, {"source_code": "TEST-FCT", "source_name": "Test composition table", "source_type": "regional_table"}, "test")
        release = self.repo.add_source_release(DEFAULT_PROJECT_UID, source["source_uid"], {"release_label": "2026"}, "test")
        nutrient = self.repo.list_canonical_nutrients()[0]
        self.repo.create_external_food(DEFAULT_PROJECT_UID, {
            "source_release_uid": release["source_release_uid"],
            "source_food_code": "FX-1",
            "food_name": "Fixture food",
            "components": [{"canonical_nutrient_uid": nutrient["canonical_nutrient_uid"], "value": 99, "unit": nutrient["canonical_unit"]}],
        }, "test")
        research_food = self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")[0]
        before = len(self.repo.get_project_food(DEFAULT_PROJECT_UID, research_food["food_uid"], "test")["components"])
        generated = self.repo.generate_food_matches(DEFAULT_PROJECT_UID, research_food["food_uid"], "test")
        self.assertGreater(generated["candidate_writes"], 0)
        match = self.repo.list_food_matches(DEFAULT_PROJECT_UID, "test")[0]
        self.repo.review_food_match(DEFAULT_PROJECT_UID, match["match_uid"], "accepted", "fixture review", "test")
        after = len(self.repo.get_project_food(DEFAULT_PROJECT_UID, research_food["food_uid"], "test")["components"])
        self.assertEqual(before, after)

    def test_contributor_can_enter_provisional_but_cannot_self_validate(self):
        self.repo.add_member(DEFAULT_PROJECT_UID, {"email": "contributor@example.test", "display_name": "Fixture Contributor", "project_role": "contributor"}, "test")
        food = self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")[0]
        nutrient = self.repo.list_canonical_nutrients()[0]
        created = self.repo.create_validated_component(DEFAULT_PROJECT_UID, food["food_uid"], {
            "canonical_nutrient_uid": nutrient["canonical_nutrient_uid"],
            "value": 4.2,
            "unit": nutrient["canonical_unit"],
            "evidence_class": "study_measured",
            "validation_status": "provisional",
        }, "contributor@example.test")
        self.assertEqual("provisional", created["validation_status"])
        self.assertEqual(1, len(self.repo.get_project_food(DEFAULT_PROJECT_UID, food["food_uid"], "test")["validated_components"]))
        with self.assertRaises(PermissionError):
            self.repo.create_validated_component(DEFAULT_PROJECT_UID, food["food_uid"], {
                "canonical_nutrient_uid": nutrient["canonical_nutrient_uid"],
                "value": 4.3,
                "unit": nutrient["canonical_unit"],
                "evidence_class": "study_measured",
                "validation_status": "validated",
            }, "contributor@example.test")

    def test_recipe_yield_retention_and_calculation_quota(self):
        food = self.repo.create_project_food(DEFAULT_PROJECT_UID, {"food_name": "Recipe ingredient"}, "test")
        legacy_nutrient = self.repo.list_nutrients()[0]
        self.repo.upsert_component(food["food_uid"], {"nutrient_code": legacy_nutrient["nutrient_code"], "value": 10.0}, "test")
        with self.repo.connect() as con:
            mapping = con.execute("SELECT canonical_nutrient_uid FROM nutrient_mappings WHERE source_nutrient_code=?", (legacy_nutrient["nutrient_code"],)).fetchone()[0]
        source = self.repo.create_source(DEFAULT_PROJECT_UID, {"source_code": "RET-TEST", "source_name": "Retention fixture", "source_type": "retention_table"}, "test")
        release = self.repo.add_source_release(DEFAULT_PROJECT_UID, source["source_uid"], {"release_label": "1"}, "test")
        self.repo.create_retention_factor(DEFAULT_PROJECT_UID, {"source_release_uid": release["source_release_uid"], "cooking_method_code": "boiled", "canonical_nutrient_uid": mapping, "retention_fraction": 0.8, "factor_status": "analytical"}, "test")
        recipe = self.repo.create_recipe(DEFAULT_PROJECT_UID, {
            "recipe_code": "REC-TEST",
            "recipe_name": "Yield fixture",
            "cooking_method_code": "boiled",
            "final_cooked_weight_g": 80,
            "ingredients": [{"research_food_uid": food["food_uid"], "input_weight_g": 100, "edible_fraction": 1}],
        }, "test")
        result = self.repo.calculate_recipe(DEFAULT_PROJECT_UID, recipe["recipe_uid"], "strict", "test")
        self.assertEqual("exploratory", result["validation_status"])
        self.assertAlmostEqual(8.0, result["results"][0]["total_recipe_value"])
        self.assertAlmostEqual(10.0, result["results"][0]["per_100g_value"])
        self.assertEqual(1, self.repo.usage_summary(DEFAULT_PROJECT_UID, "test")["calculation_runs"]["used"])

    def test_validated_layer_csv_pipeline_keeps_provenance(self):
        def import_one(filename, entity_type, content):
            staged = stage_import(self.repo, content.encode(), filename, entity_type, actor="test", project_uid=DEFAULT_PROJECT_UID)
            self.assertEqual(1, staged["valid_count"], staged["preview"])
            return commit_import(self.repo, staged["batch_uid"], "test")

        import_one("sources.csv", "data_sources", "source_code,source_name,source_type,citation\nCSV-FCT,CSV fixture,regional_table,Fixture citation\n")
        import_one("releases.csv", "source_releases", "source_code,release_label,retrieved_at\nCSV-FCT,2026,2026-08-09\n")
        import_one("foods.csv", "external_foods", "source_code,release_label,source_food_code,food_name,country_code\nCSV-FCT,2026,NGA-1,CSV Nigerian food,NG\n")
        nutrient = self.repo.list_canonical_nutrients()[0]
        import_one(
            "external-values.csv",
            "external_components",
            f"source_code,release_label,source_food_code,canonical_code,value,unit,analytical_method\nCSV-FCT,2026,NGA-1,{nutrient['canonical_code']},12.5,{nutrient['canonical_unit']},fixture method\n",
        )
        import_one(
            "retention.csv",
            "retention_factors",
            f"source_code,release_label,cooking_method_code,canonical_code,retention_fraction,factor_status\nCSV-FCT,2026,boiled,{nutrient['canonical_code']},0.75,reported\n",
        )
        research_food = self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")[0]
        import_one(
            "validated.csv",
            "validated_components",
            f"food_uid,canonical_code,value,unit,evidence_class,validation_status,source_code,release_label,source_record_uid\n{research_food['food_uid']},{nutrient['canonical_code']},9.5,{nutrient['canonical_unit']},nigerian_regional,reviewed,CSV-FCT,2026,row-1\n",
        )
        with self.repo.connect() as con:
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM external_food_component_values WHERE value=12.5").fetchone()[0])
            record = con.execute("SELECT * FROM validated_food_component_values WHERE source_record_uid='row-1'").fetchone()
            self.assertEqual("nigerian_regional", record["evidence_class"])
            self.assertEqual("reviewed", record["validation_status"])
        self.assertEqual(1, len(self.repo.list_retention_factors(DEFAULT_PROJECT_UID, "test")))
        self.assertEqual(6, self.repo.usage_summary(DEFAULT_PROJECT_UID, "test")["import_rows"]["used"])


if __name__ == "__main__":
    unittest.main()
