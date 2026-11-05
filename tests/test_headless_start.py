"""Exercise headless startup and token validation with isolated process doubles."""

import unittest
from pathlib import Path

import test_shell_coverage_runtime as runtime_tests


class HeadlessStartTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source
    source = Path(__file__).resolve().parents[1] / "headless/start"

    def start(self, *, config=True, token=None, settings=False):
        def setup(root):
            home = root / "home"
            home.mkdir()
            if config:
                (root / "config.ini").write_text("[Common]\n")
            if token is not None:
                (home / ".mcp-token").write_text(token)
            if settings:
                path = home / "MT5/Config/assistant.ini"
                path.parent.mkdir(parents=True)
                path.write_text("existing")

        return self.run_source(
            self.source,
            setup=setup,
            replacements=(("/config.ini", "config.ini"), ("/tmp/mt5-nginx", "mt5-nginx")),
            before=(
                'HOME="$TEST_ROOT/home"\n'
                'nginx() { sleep 0.1; }\n'
                'xvfb-run() { sleep 0.1; }\n'
                'wineserver() { :; }\n'
            ),
        )

    def test_missing_config_and_orphaned_settings_are_rejected(self):
        result, _, _, _ = self.start(config=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mount a readable", result.stderr)
        result, _, _, _ = self.start(settings=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Existing MCP settings", result.stderr)

    def test_invalid_tokens_are_rejected(self):
        for token in ("", "bad!", "a" * 42):
            with self.subTest(token=token):
                result, _, _, _ = self.start(token=token)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid MCP token", result.stderr)

    def test_new_and_existing_tokens_start_proxy_and_terminal(self):
        for token in (None, "a" * 43):
            with self.subTest(token=token):
                result, _, _, files = self.start(token=token)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(files["home/.mcp-token"]), 43)
                self.assertIn("home/MT5/Config/assistant.ini", files)
                self.assertIn("mt5-nginx.conf", files)
