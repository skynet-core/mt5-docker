"""Source discovery must include scripts that have no filename extension."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "source_lint", Path(__file__).resolve().parents[1] / "build/lint.py"
)
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


class SourceLintTests(unittest.TestCase):
    def test_extensions_shebangs_and_inline_python_metadata(self):
        cases = [
            ("module.sh", "echo ok\n", "shell"),
            ("module.py", "print('ok')\n", "python"),
            ("module.cpp", "int main() {}\n", "c++"),
            ("module.hpp", "#pragma once\n", "c++"),
            ("script", "#!/bin/sh\n", "shell"),
            ("script", "#!/usr/bin/env -S bash -eu\n", "shell"),
            ("script", "#!/usr/bin/python3\n", "python"),
            ("script", "#!/usr/bin/env -S python3.12 -u\n", "python"),
            ("script", "# /// script\n# requires-python = '>=3.12'\n", "python"),
            ("README.md", "Documentation\n", None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for name, contents, expected in cases:
                with self.subTest(name=name, contents=contents):
                    path = Path(directory) / name
                    path.write_text(contents)
                    self.assertEqual(LINT.language(path), expected)

    def test_discovery_includes_new_and_vendored_sources_but_ignores_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("artifacts/\n.env\n")
            for name in (
                "tracked.py",
                "new file.py",
                "tests/test_runtime.py",
                "vendor/kde-container/test/helper.py",
                "artifacts/generated.py",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("pass\n")
            vendor = root / "vendor/helper"
            vendor.parent.mkdir(exist_ok=True)
            vendor.write_text("#!/usr/bin/python3\n")
            (root / ".env").write_text("# /// script\n")
            subprocess.run(["git", "add", "tracked.py"], cwd=root, check=True)
            self.assertEqual(
                LINT.sources(root),
                {
                    "shell": [],
                    "python": [
                        "./new file.py",
                        "./tests/test_runtime.py",
                        "./tracked.py",
                        "./vendor/helper",
                        "./vendor/kde-container/test/helper.py",
                    ],
                    "c++": [],
                },
            )

    def test_coverage_excludes_test_sources_without_excluding_build_tools(self):
        for name in (
            "tests/test_runtime.py",
            "./tests/display-matrix.sh",
            "vendor/kde-container/test/helper.cpp",
            "vendor/kde-container/native/ui-fixture.cpp",
            "vendor/kde-container/native/vnc-benchmark.cpp",
        ):
            with self.subTest(name=name):
                self.assertFalse(LINT.coverage_source(name))
        for name in (
            "build/coverage_report.py",
            "build/test-runner.py",
            "vendor/kde-container/native/clipboard.cpp",
            "vendor/kde-container/compat/wayland-pointer-enter.cpp",
        ):
            with self.subTest(name=name):
                self.assertTrue(LINT.coverage_source(name))

    def test_lint_failure_still_checks_other_languages_and_fails_the_command(self):
        with (
            patch.object(
                LINT,
                "sources",
                return_value={
                    "shell": ["./a.sh"],
                    "python": ["./b.py"],
                    "c++": ["./c.cpp"],
                },
            ),
            patch.object(
                LINT.subprocess,
                "run",
                side_effect=lambda command, **kwargs: SimpleNamespace(
                    returncode=int(command == ["shellcheck", "./a.sh"])
                ),
            ) as run,
        ):
            self.assertEqual(LINT.main(), 1)
        self.assertEqual(run.call_args_list[-1].args[0][-1], "./c.cpp")


if __name__ == "__main__":
    unittest.main()
