import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from dietary_recall.analytics import AnalyticsService, MatchingModelService
from dietary_recall.billing import BillingService
from dietary_recall.governance import GovernanceService
from dietary_recall.licensed_ingestion import LicensedDatasetService
from dietary_recall.research_core import initialize_research_database
from dietary_recall.security import AuthError, CredentialStore, PBKDF2_ITERATIONS
from dietary_recall.v03_schema import DEFAULT_PROJECT_UID, upgrade_platform_database
from dietary_recall.v04_schema import upgrade_v03_to_v04
from dietary_recall.validated_research import PlatformRepository
from test_research_platform import make_legacy_fixture


class V04PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        legacy = self.root / "legacy.db"
        v02 = self.root / "v02.db"
        self.v03 = self.root / "v03.db"
        self.v04 = self.root / "v04.db"
        make_legacy_fixture(legacy)
        initialize_research_database(legacy, v02)
        upgrade_platform_database(v02, self.v03)
        self.source_hash = hashlib.sha256(self.v03.read_bytes()).hexdigest()
        upgrade_v03_to_v04(self.v03, self.v04)
        self.repo = PlatformRepository(self.v04)

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_first_schema_and_authentication_boundary(self):
        self.assertEqual(self.source_hash, hashlib.sha256(self.v03.read_bytes()).hexdigest())
        with self.repo.connect() as con:
            self.assertEqual(4, con.execute("PRAGMA user_version").fetchone()[0])
            user_uid = con.execute("SELECT user_uid FROM app_users ORDER BY created_at LIMIT 1").fetchone()[0]
        auth = CredentialStore.initialize(self.root / "auth.db")
        auth.set_password("test@example.test", user_uid, "A-strong-fixture-password")
        session = auth.login("test@example.test", "A-strong-fixture-password", user_agent="fixture")
        self.assertEqual(PBKDF2_ITERATIONS, auth.status()["password_iterations"])
        self.assertEqual("test@example.test", auth.authenticate(session.session_token, user_agent="fixture").email)
        auth.verify_csrf(session, session.csrf_token)
        with self.assertRaises(AuthError):
            auth.verify_csrf(session, "wrong")
        auth.logout(session.session_token)
        with self.assertRaises(AuthError):
            auth.authenticate(session.session_token, user_agent="fixture")

    def test_signed_billing_webhook_is_idempotent(self):
        service = BillingService(self.repo)
        price = service.register_price(DEFAULT_PROJECT_UID, {"provider": "stripe", "external_price_id": "price_fixture", "display_name": "Research group", "import_rows_limit": 500, "calculation_runs_limit": 500}, "test")
        event = {"id": "evt_fixture", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_fixture", "status": "active", "customer": "cus_fixture", "metadata": {"project_uid": DEFAULT_PROJECT_UID}, "items": {"data": [{"price": {"id": "price_fixture"}}]}}}}
        raw = json.dumps(event, separators=(",", ":")).encode()
        now = int(time.time())
        secret = "whsec_fixture"
        signature = hmac.new(secret.encode(), f"{now}.".encode() + raw, hashlib.sha256).hexdigest()
        result = service.process_webhook("stripe", raw, f"t={now},v1={signature}", secret, now=now)
        self.assertEqual("active", result["subscription_status"])
        self.assertTrue(service.process_webhook("stripe", raw, f"t={now},v1={signature}", secret, now=now)["idempotent"])
        usage = self.repo.usage_summary(DEFAULT_PROJECT_UID, "test")
        self.assertEqual(500, usage["import_rows"]["limit"])
        with self.repo.connect() as con:
            self.assertEqual(price["price_mapping_uid"], con.execute("SELECT price_mapping_uid FROM billing_subscriptions").fetchone()[0])

    def test_independent_specialist_review_gate(self):
        governance = GovernanceService(self.repo)
        self.repo.add_member(DEFAULT_PROJECT_UID, {"email": "specialist@example.test", "display_name": "Specialist", "project_role": "analyst"}, "test")
        specialist = governance.register_specialist(DEFAULT_PROJECT_UID, {"email": "specialist@example.test", "specialization": "food_composition", "credentials": "PhD food composition; fixture registry"}, "specialist@example.test")
        with self.assertRaises(PermissionError):
            governance.verify_specialist(DEFAULT_PROJECT_UID, specialist["specialist_uid"], {}, "specialist@example.test")
        governance.verify_specialist(DEFAULT_PROJECT_UID, specialist["specialist_uid"], {}, "test")
        nutrient = self.repo.list_canonical_nutrients()[0]
        case = governance.submit_case(DEFAULT_PROJECT_UID, {"target_type": "canonical_nutrient", "target_uid": nutrient["canonical_nutrient_uid"], "evidence": {"citation": "fixture evidence"}}, "test")
        decided = governance.decide_case(DEFAULT_PROJECT_UID, case["review_case_uid"], {"decision": "approved", "rationale": "Evidence and unit semantics independently checked."}, "specialist@example.test")
        self.assertEqual("approved", decided["status"])
        with self.repo.connect() as con:
            self.assertEqual("reviewed", con.execute("SELECT ontology_status FROM canonical_nutrients WHERE canonical_nutrient_uid=?", (nutrient["canonical_nutrient_uid"],)).fetchone()[0])

    def _licensed_zip(self, release_uid, source_uid, nutrient):
        foods = b"source_release_uid,source_food_code,food_name,country_code\n" + f"{release_uid},NG-1,Licensed Nigerian fixture,NG\n".encode()
        components = b"source_release_uid,source_food_code,canonical_code,value,unit\n" + f"{release_uid},NG-1,{nutrient['canonical_code']},12.5,{nutrient['canonical_unit']}\n".encode()
        manifest = {"schema_version": 1, "dataset_code": "NG-LIC-FIXTURE", "source_release_uid": release_uid, "conflict_policy": "reject", "license": {"accepted": True, "source_uid": source_uid, "name": "Fixture research licence", "text_sha256": "a" * 64, "permitted_use": "research_only", "redistribution_permitted": False}, "files": [{"path": "external_foods.csv", "entity_type": "external_foods", "sha256": hashlib.sha256(foods).hexdigest()}, {"path": "external_components.csv", "entity_type": "external_components", "sha256": hashlib.sha256(components).hexdigest()}]}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("external_foods.csv", foods)
            archive.writestr("external_components.csv", components)
        return stream.getvalue()

    def test_licensed_package_stages_then_commits_atomically(self):
        source = self.repo.create_source(DEFAULT_PROJECT_UID, {"source_code": "LIC-FIX", "source_name": "Licensed fixture", "source_type": "food_composition_table"}, "test")
        release = self.repo.add_source_release(DEFAULT_PROJECT_UID, source["source_uid"], {"release_label": "2026"}, "test")
        nutrient = self.repo.list_canonical_nutrients()[0]
        service = LicensedDatasetService(self.repo)
        staged = service.stage(DEFAULT_PROJECT_UID, self._licensed_zip(release["source_release_uid"], source["source_uid"], nutrient), "fixture.zip", "test")
        self.assertEqual((2, 0, "staged"), (staged["valid_count"], staged["invalid_count"], staged["status"]))
        self.assertEqual(0, self.repo.usage_summary(DEFAULT_PROJECT_UID, "test")["import_rows"]["used"])
        committed = service.commit(DEFAULT_PROJECT_UID, staged["dataset_package_uid"], "test")
        self.assertEqual("committed", committed["status"])
        self.assertEqual(2, self.repo.usage_summary(DEFAULT_PROJECT_UID, "test")["import_rows"]["used"])
        with self.repo.connect() as con:
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM external_food_component_values WHERE value=12.5").fetchone()[0])

    def test_descriptive_and_qc_analytics_are_saved_without_source_mutation(self):
        original = self.repo.list_project_participants(DEFAULT_PROJECT_UID, "test")[0]
        participants = [original]
        for index in range(4):
            participants.append(self.repo.create_project_participant(DEFAULT_PROJECT_UID, {"participant_code": f"P-{index}", "gender_code": "1"}, "test"))
        food = self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")[0]
        for index, participant in enumerate(participants):
            self.repo.create_project_recall(DEFAULT_PROJECT_UID, {"participant_uid": participant["participant_uid"], "recall_date": f"2026-08-{index+1:02d}", "status": "complete", "items": [{"food_uid": food["food_uid"], "amount_g": 100 + index}]}, "test")
        analytics = AnalyticsService(self.repo)
        cohort = analytics.run_cohort(DEFAULT_PROJECT_UID, {"analysis_mode": "recall_day", "group_by": "overall", "minimum_group_size": 5}, "test")
        self.assertTrue(cohort["metrics"])
        self.assertTrue(all(not item["suppressed"] for item in cohort["metrics"]))

        nutrient = self.repo.list_nutrients()[0]
        for index, value in enumerate([1, 2, 3, 4, 5, 100]):
            experiment = self.repo.create_project_experiment(DEFAULT_PROJECT_UID, {"experiment_code": f"EXP-{index}", "status": "complete"}, "test")
            self.repo.add_experiment_result(experiment["experiment_uid"], {"nutrient_code": nutrient["nutrient_code"], "value": value, "unit": nutrient["unit"]}, "test")
        before = [row["value"] for row in self.repo.get_experiment(self.repo.list_project_experiments(DEFAULT_PROJECT_UID, "test")[0]["experiment_uid"])["results"]]
        anomaly = analytics.run_anomalies(DEFAULT_PROJECT_UID, {"threshold": 3.5}, "test")
        self.assertGreaterEqual(anomaly["flag_count"], 1)
        after = [row["value"] for row in self.repo.get_experiment(self.repo.list_project_experiments(DEFAULT_PROJECT_UID, "test")[0]["experiment_uid"])["results"]]
        self.assertEqual(before, after)
        with self.assertRaises(ValueError):
            MatchingModelService(self.repo).train(DEFAULT_PROJECT_UID, "test")

    def test_matching_model_prioritizes_only_after_approval(self):
        source = self.repo.create_source(DEFAULT_PROJECT_UID, {"source_code": "ML-FIX", "source_name": "Matching fixture", "source_type": "regional_table"}, "test")
        release = self.repo.add_source_release(DEFAULT_PROJECT_UID, source["source_uid"], {"release_label": "1"}, "test")
        food_uid = self.repo.list_project_foods(DEFAULT_PROJECT_UID, "test")[0]["food_uid"]
        with self.repo.connect() as con:
            for index in range(21):
                external_uid = f"xfood_ml_{index}"
                con.execute("INSERT INTO external_foods(external_food_uid,project_uid,source_release_uid,source_food_code,food_name,country_code) VALUES (?,?,?,?,?,'NG')", (external_uid, DEFAULT_PROJECT_UID, release["source_release_uid"], f"ML-{index}", f"Fixture {index}"))
                status = "candidate" if index == 20 else ("accepted" if index < 10 else "rejected")
                feature = {"sequence": 0.9 if index < 10 else 0.2, "token_jaccard": 0.8 if index < 10 else 0.1, "exact": index < 5}
                con.execute("INSERT INTO food_match_candidates(match_uid,project_uid,research_food_uid,external_food_uid,match_method,score,feature_json,review_status) VALUES (?,?,?,?,?,?,?,?)", (f"match_ml_{index}", DEFAULT_PROJECT_UID, food_uid, external_uid, "fixture", 0.85 if index < 10 else 0.25, json.dumps(feature), status))
        service = MatchingModelService(self.repo)
        model = service.train(DEFAULT_PROJECT_UID, "test")
        self.assertEqual("candidate", model["review_status"])
        with self.assertRaises(PermissionError):
            service.apply(DEFAULT_PROJECT_UID, model["matching_model_uid"], "test")
        with self.repo.connect() as con:
            con.execute("UPDATE matching_models SET review_status='approved' WHERE matching_model_uid=?", (model["matching_model_uid"],))
        applied = service.apply(DEFAULT_PROJECT_UID, model["matching_model_uid"], "test")
        self.assertEqual(1, applied["prioritized"])
        with self.repo.connect() as con:
            score = con.execute("SELECT triage_score FROM food_match_candidates WHERE match_uid='match_ml_20'").fetchone()[0]
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 1)


if __name__ == "__main__":
    unittest.main()
