"""WebView2 defaults must reach elevated hosts without replacing custom policy."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SOURCE = Path(__file__).resolve().parents[1] / "bin/mt5-launch"
POLICY = SOURCE.read_text().split("<<'BROWSER'\n", 1)[1].split("\nBROWSER", 1)[0]


class WebViewPolicyTests(unittest.TestCase):
    def registry(self, value):
        registry = MagicMock(
            HKEY_LOCAL_MACHINE=0x80000002,
            KEY_QUERY_VALUE=1,
            KEY_SET_VALUE=2,
            REG_SZ=1,
        )
        if value is None:
            registry.QueryValueEx.side_effect = FileNotFoundError("not configured")
        else:
            registry.QueryValueEx.return_value = (value, registry.REG_SZ)
        return registry

    def test_missing_or_empty_app_policy_gets_the_browser_only_software_default(self):
        for value in (None, ""):
            with self.subTest(value=value):
                registry = self.registry(value)
                with patch.dict(sys.modules, {"winreg": registry}):
                    exec(POLICY, {})
                registry.CreateKeyEx.assert_called_once_with(
                    registry.HKEY_LOCAL_MACHINE,
                    r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments",
                    access=registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE,
                )
                key = registry.CreateKeyEx.return_value.__enter__.return_value
                registry.QueryValueEx.assert_called_once_with(key, "terminal64.exe")
                registry.SetValueEx.assert_called_once_with(
                    key, "terminal64.exe", 0, registry.REG_SZ, "--disable-gpu"
                )

    def test_nonempty_app_policy_is_preserved(self):
        registry = self.registry("--custom-browser-policy")
        with patch.dict(sys.modules, {"winreg": registry}):
            exec(POLICY, {})
        registry.SetValueEx.assert_not_called()

    def test_registry_access_failures_are_not_treated_as_missing_policy(self):
        registry = self.registry(None)
        registry.QueryValueEx.side_effect = PermissionError("policy access denied")
        with (
            patch.dict(sys.modules, {"winreg": registry}),
            self.assertRaisesRegex(PermissionError, "policy access denied"),
        ):
            exec(POLICY, {})
        registry.SetValueEx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
