import unittest
from pathlib import Path

from dietary_recall.demo_runtime import _safe_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DemoDeploymentTests(unittest.TestCase):
    def test_blueprint_keeps_frontend_and_backend_separate(self):
        source = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("runtime: python", source)
        self.assertIn("runtime: static", source)
        self.assertIn("dietary-recall demo-serve", source)
        self.assertIn("source: /api/*", source)
        self.assertIn("destination: /index.html", source)
        self.assertIn("sync: false", source)
        self.assertNotIn("Nutrients-research-v04.db", source)

    def test_gitignore_excludes_research_and_credential_databases(self):
        source = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.db", source)
        self.assertIn("backend/secrets/", source)
        self.assertIn("raw_tables/", source)
        self.assertIn(".env", source)

    def test_demo_root_rejects_broad_filesystem_targets(self):
        with self.assertRaises(ValueError):
            _safe_root(Path(Path.cwd().anchor))


if __name__ == "__main__":
    unittest.main()
