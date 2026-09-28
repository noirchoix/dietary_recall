import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from dietary_recall.db import LegacyRepository
from dietary_recall.demo_runtime import initialize_demo_workspace
from dietary_recall.legacy import FoodPortion, calculate_foods
from dietary_recall.security import CredentialStore
from dietary_recall.v03_schema import DEFAULT_PROJECT_UID
from dietary_recall.validated_research import PlatformRepository


class DemoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "runtime" / "demo"

    def tearDown(self):
        self.tmp.cleanup()

    def test_bootstrap_contains_only_synthetic_records_and_separate_credentials(self):
        result = initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026")
        self.assertEqual("demo@dietary-recall.local", result.email)
        self.assertNotEqual(result.legacy_database, result.research_database)
        self.assertNotEqual(result.research_database, result.credential_store)
        with closing(sqlite3.connect(result.legacy_database)) as con:
            food_names = [row[0] for row in con.execute("SELECT Food_Name FROM Food ORDER BY Food_ID")]
            self.assertEqual(4, len(food_names))
            self.assertTrue(all("Illustrative" in name for name in food_names))
        with closing(sqlite3.connect(result.research_database)) as con:
            self.assertEqual(4, con.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("ephemeral_synthetic_demo", con.execute("SELECT value FROM research_meta WHERE key='deployment_mode'").fetchone()[0])
            owner = con.execute("SELECT email,display_name FROM app_users WHERE user_uid='user_local_platform_owner'").fetchone()
            self.assertEqual(("demo@dietary-recall.local", "Demo Researcher"), owner)
        status = CredentialStore(result.credential_store).status()
        self.assertEqual(1, status["active_credentials"])
        self.assertFalse(status["plaintext_passwords_stored"])

    def test_synthetic_calculations_scale_real_seed_rows_and_keep_missingness_explicit(self):
        result = initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026")
        legacy = calculate_foods(LegacyRepository(result.legacy_database), [FoodPortion(1002, 150)])
        self.assertAlmostEqual(10.8, legacy.totals["Basic_Components"]["Protein_g"])
        self.assertAlmostEqual(3.15, legacy.totals["Minerals"]["Iron_mg"])

        repo = PlatformRepository(result.research_database)
        food = next(
            row for row in repo.list_project_foods(DEFAULT_PROJECT_UID, result.email)
            if row["legacy_food_id"] == 1002
        )
        preview = repo.calculate_food_portions(
            DEFAULT_PROJECT_UID,
            [{"food_uid": food["food_uid"], "grams": 150}],
            result.email,
        )
        protein = next(row for row in preview["results"] if row["display_name"] == "Protein")
        iron = next(row for row in preview["results"] if row["display_name"] == "Iron")
        self.assertAlmostEqual(10.8, protein["value"])
        self.assertAlmostEqual(3.15, iron["value"])
        self.assertFalse(preview["saved"])
        self.assertGreater(preview["items"][0]["missing_component_count"], 0)
        self.assertNotIn("Vitamin A IU", {row["display_name"] for row in preview["results"]})

    def test_composition_preview_rejects_zero_nonfinite_and_cross_project_inputs(self):
        result = initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026")
        repo = PlatformRepository(result.research_database)
        food = repo.list_project_foods(DEFAULT_PROJECT_UID, result.email)[0]
        for grams in (0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                repo.calculate_food_portions(
                    DEFAULT_PROJECT_UID,
                    [{"food_uid": food["food_uid"], "grams": grams}],
                    result.email,
                )

    def test_reset_discards_prior_demo_mutations(self):
        first = initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026")
        with closing(sqlite3.connect(first.research_database)) as con:
            con.execute("INSERT OR REPLACE INTO research_meta(key,value) VALUES ('temporary_demo_marker','present')")
            con.commit()
        second = initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026", reset=True)
        with closing(sqlite3.connect(second.research_database)) as con:
            marker = con.execute("SELECT value FROM research_meta WHERE key='temporary_demo_marker'").fetchone()
        self.assertIsNone(marker)

    def test_keep_existing_refuses_partial_workspace(self):
        self.root.mkdir(parents=True)
        (self.root / "Nutrients-demo-synthetic.db").touch()
        with self.assertRaises(ValueError):
            initialize_demo_workspace(self.root, "Synthetic-demo-passphrase-2026", reset=False)


if __name__ == "__main__":
    unittest.main()
