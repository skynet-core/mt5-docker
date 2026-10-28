"""CI broker configuration must preserve secrets and reject environment-file injection."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build/ci-broker-env"


class BrokerEnvironmentTests(unittest.TestCase):
    def configure(self, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "environment"
            values = {
                "MT5_ACCOUNT": "12345678",
                "MT5_PASSWORD": "private $ pass # value",
                "MT5_SERVER": "Custom-Demo:443",
                **overrides,
            }
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT)],
                env={**os.environ, **values, "GITHUB_ENV": str(output)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotIn(values["MT5_PASSWORD"], result.stdout + result.stderr)
            self.assertNotIn(values["MT5_ACCOUNT"], result.stdout + result.stderr)
            return result, output.read_text() if output.exists() else ""

    def test_variable_name_placeholder_uses_the_confirmed_broker(self):
        result, environment = self.configure(MT5_SERVER="MT5_SERVER")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(environment, "MT5_SERVER=ICMarketsEU-Demo\n")
        self.assertIn("::notice::", result.stdout)

    def test_custom_server_is_preserved_without_exporting_credentials(self):
        result, environment = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(environment, "MT5_SERVER=Custom-Demo:443\n")
        self.assertEqual(result.stdout, "")

    def test_invalid_values_cannot_reach_the_github_environment_file(self):
        for values in (
            {"MT5_ACCOUNT": "not-a-number"},
            {"MT5_SERVER": ""},
            {"MT5_SERVER": "Demo\nINJECTED=value"},
            {"MT5_PASSWORD": "private\rpassword"},
        ):
            with self.subTest(values=tuple(values)):
                result, environment = self.configure(**values)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(environment, "")


if __name__ == "__main__":
    unittest.main()
