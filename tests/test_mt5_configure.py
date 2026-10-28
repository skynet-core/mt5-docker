"""Exercise MT5's private startup configuration without Wine or credentials."""

import configparser
import importlib.machinery
import importlib.util
import os
import pwd
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "libexec/mt5-configure"
LOADER = importlib.machinery.SourceFileLoader("mt5_configure", str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
CONFIGURE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(CONFIGURE)


class AccountConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.source = self.home / "source.ini"
        self.source.write_text("[Common]\nKeepPrivate=1\n[Experts]\nEnabled=0\n")
        self.destination = self.home / ".wine/drive_c/MT5/config.ini"
        self.destination.parent.mkdir(parents=True)
        (self.destination.parent / "terminal64.exe").touch()
        current = pwd.getpwuid(os.getuid())
        account = SimpleNamespace(
            pw_dir=str(self.home), pw_uid=current.pw_uid, pw_gid=current.pw_gid
        )
        self.addCleanup(patch.stopall)
        patch.object(CONFIGURE.pwd, "getpwnam", return_value=account).start()
        patch.dict(
            os.environ, {"MT5_CONFIG_FILE": str(self.source)}, clear=True
        ).start()

    def test_password_roundtrip_and_private_existing_file(self):
        password = "%-#;=ü漢字🔑"
        self.destination.touch(mode=0o644)
        os.environ.update(
            MT5_ACCOUNT="12345678", MT5_PASSWORD=password, MT5_SERVER="Example-Demo"
        )
        CONFIGURE.configure()
        content = self.destination.read_bytes()
        self.assertIn(b"\r\x00\n\x00", content)
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(content.decode("utf-16"))
        self.assertEqual(config["Common"]["Login"], "12345678")
        self.assertEqual(config["Common"]["Password"], password)
        self.assertEqual(config["Common"]["Server"], "Example-Demo")
        self.assertEqual(config["Common"]["KeepPrivate"], "1")
        self.assertEqual(config["Experts"]["Enabled"], "0")
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertEqual(self.destination.stat().st_uid, os.getuid())

    def test_partial_or_injected_credentials_do_not_overwrite_config(self):
        self.destination.write_text("existing")
        valid = {
            "MT5_ACCOUNT": "123",
            "MT5_PASSWORD": "secret",
            "MT5_SERVER": "Example-Demo",
        }
        for values in (
            {"MT5_ACCOUNT": "123"},
            {"MT5_PASSWORD": "secret"},
            {"MT5_SERVER": "Example-Demo"},
            {**valid, "MT5_ACCOUNT": "１２３"},
            {**valid, "MT5_ACCOUNT": "abc"},
            {**valid, "MT5_PASSWORD": "secret\n[Experts]"},
            {**valid, "MT5_SERVER": "Example\rDemo"},
        ):
            with (
                self.subTest(values={key: key for key in values}),
                patch.dict(os.environ, values),
            ):
                with self.assertRaises(ValueError):
                    CONFIGURE.configure()
                self.assertEqual(self.destination.read_text(), "existing")

    def test_mounted_config_works_without_environment_account(self):
        self.source.write_text(
            "[Common]\nLogin=4321\nPassword=mounted%#pass\nServer=Example-Demo\n"
        )
        CONFIGURE.configure()
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.destination, encoding="utf-16")
        self.assertEqual(config["Common"]["Login"], "4321")
        self.assertEqual(config["Common"]["Password"], "mounted%#pass")

    def test_symlink_config_cannot_overwrite_another_file(self):
        target = self.home / "unrelated.ini"
        target.write_text("keep this file")
        self.destination.symlink_to(target)
        with self.assertRaises(OSError):
            CONFIGURE.configure()
        self.assertEqual(target.read_text(), "keep this file")


if __name__ == "__main__":
    unittest.main()
