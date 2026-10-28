"""Linux dependency updates must retain the Windows runtime SDK and helpers."""

import unittest
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


class RuntimeRequirementsTests(unittest.TestCase):
    def test_dependabot_preserves_universal_compilation_from_the_lock_header(self):
        header = (ROOT / "build/mt5-mcp-requirements.txt").read_text().splitlines()[1]
        self.assertIn("--universal", header)

    def test_windows_only_runtime_dependencies_keep_their_platform_markers(self):
        environment = {
            **default_environment(),
            "python_version": "3.12",
            "python_full_version": "3.12.10",
            "implementation_name": "cpython",
            "platform_python_implementation": "CPython",
        }
        for name, required in (
            ("mt5-mcp-requirements.in", {"metatrader5"}),
            ("mt5-mcp-requirements.txt", {"metatrader5", "pywin32", "pywin32-ctypes"}),
        ):
            requirements = {
                item.name.lower(): item
                for line in (ROOT / "build" / name).read_text().splitlines()
                if line and not line.startswith(("#", " ", "\t"))
                for item in [Requirement(line)]
            }
            for dependency in required:
                with self.subTest(file=name, dependency=dependency):
                    item = requirements[dependency]
                    self.assertIsNotNone(item.marker)
                    self.assertTrue(
                        item.marker.evaluate({**environment, "sys_platform": "win32"})
                    )
                    self.assertFalse(
                        item.marker.evaluate({**environment, "sys_platform": "linux"})
                    )


if __name__ == "__main__":
    unittest.main()
