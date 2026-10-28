"""Quoted passwords and literal dollars must survive the host launch boundary."""

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "libexec/host-env"
loader = importlib.machinery.SourceFileLoader("host_env", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
HOST_ENV = importlib.util.module_from_spec(spec)
loader.exec_module(HOST_ENV)


class HostEnvironmentTests(unittest.TestCase):
    def test_dotenv_quotes_and_literal_dollars(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "MT5_ACCOUNT=123456\nMT5_PASSWORD='space # % ${UNSET} ü'\n"
                'MT5_SERVER="Broker-Demo"\n'
            )
            values = HOST_ENV.environment(path)
            self.assertEqual(values["MT5_PASSWORD"], "space # % ${UNSET} ü")
            self.assertEqual(values["MT5_SERVER"], "Broker-Demo")
            self.assertEqual(values["MT5_ACCOUNT"], "123456")


if __name__ == "__main__":
    unittest.main()
