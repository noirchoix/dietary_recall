import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAYOUT = PROJECT_ROOT / "frontend" / "src" / "routes" / "+layout.svelte"
LOGIN = PROJECT_ROOT / "frontend" / "src" / "routes" / "login" / "+page.svelte"
SIDEBAR = PROJECT_ROOT / "frontend" / "src" / "lib" / "components" / "WorkspaceSidebar.svelte"
CALCULATOR = PROJECT_ROOT / "frontend" / "src" / "routes" / "calculator" / "+page.svelte"


class FrontendAuthenticationBoundaryTests(unittest.TestCase):
    def test_public_shell_precedes_protected_session_gate(self):
        source = LAYOUT.read_text(encoding="utf-8")
        public_branch = source.index("{#if routeKind === 'public'}")
        protected_gate = source.index("{:else if !authReady || !auth?.authenticated}")
        authenticated_platform = source.index("{:else if routeKind === 'platform'}")
        workspace = source.index('class="workspace-shell"')
        self.assertLess(public_branch, protected_gate)
        self.assertLess(protected_gate, authenticated_platform)
        self.assertLess(authenticated_platform, workspace)
        self.assertIn("replaceState: true", source)
        self.assertNotIn('class="app-shell"', source)

    def test_platform_and_project_navigation_are_separate(self):
        layout = LAYOUT.read_text(encoding="utf-8")
        sidebar = SIDEBAR.read_text(encoding="utf-8")
        self.assertIn("['/projects', '/usage']", layout)
        self.assertIn("['/', '/features', '/pricing', '/login']", layout)
        self.assertIn("Data collection", sidebar)
        self.assertIn("Composition science", sidebar)
        self.assertIn("Composition calculator", sidebar)
        self.assertIn("Governance", sidebar)
        self.assertIn("onCollapse", sidebar)
        self.assertNotIn("Usage & plan", sidebar)

    def test_login_has_clear_account_language_and_structured_controls(self):
        source = LOGIN.read_text(encoding="utf-8")
        self.assertNotIn("platform operator", source.casefold())
        self.assertIn('for="login-email"', source)
        self.assertIn('id="login-email"', source)
        self.assertIn('for="login-password"', source)
        self.assertIn('id="login-password"', source)
        self.assertIn("auth-set-password", source)
        self.assertIn('class="login-button"', source)
        self.assertIn("/projects", source)

    def test_composition_calculator_exposes_formula_and_missingness_boundary(self):
        source = CALCULATOR.read_text(encoding="utf-8")
        self.assertIn("/api/composition/calculate", source)
        self.assertIn("stored per-100 g value", source)
        self.assertIn("Missing values are not converted to zero", source)


if __name__ == "__main__":
    unittest.main()
