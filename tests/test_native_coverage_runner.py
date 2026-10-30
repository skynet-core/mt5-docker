"""Failed native tests must leave reports while report failures fail the job."""

import contextlib
import importlib.util
import io
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

SOURCE = Path(__file__).with_name("native") / "run_coverage.py"
SPEC = importlib.util.spec_from_file_location("native_coverage_runner", SOURCE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
REPORTS = ("native.json", "native.xml", "native.html")


class NativeCoverageRunnerTests(unittest.TestCase):
    def run_commands(self, outcomes=(0, 0), incomplete=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, build, output = root / "source", root / "build", root / "output"
            output.mkdir()
            previous = output / "native.tests_old.cpp.html"
            previous.write_text("old test-source coverage")
            calls = []

            def run(command, **options):
                calls.append((command, options))
                options["stdout"].write(
                    f"controlled {'CTest' if len(calls) == 1 else 'gcovr'} output\n"
                )
                if len(calls) == 2:
                    for name in REPORTS:
                        if incomplete == (name, "missing"):
                            continue
                        (output / name).write_text(
                            "" if incomplete == (name, "empty") else "report\n"
                        )
                outcome = outcomes[len(calls) - 1]
                if isinstance(outcome, Exception):
                    raise outcome
                return SimpleNamespace(returncode=outcome)

            with (
                patch.object(RUNNER, "ROOT", source),
                patch.object(RUNNER, "BUILD", build),
                patch.object(RUNNER, "OUTPUT", output),
                patch.object(RUNNER.subprocess, "run", side_effect=run),
                contextlib.redirect_stdout(io.StringIO()) as printed,
            ):
                status = RUNNER.main()
            self.assertFalse(previous.exists())
            logs = {path.name: path.read_text() for path in output.glob("*.log")}
            for _command, options in calls:
                self.assertEqual(options["cwd"], source)
                self.assertIs(options["stderr"], subprocess.STDOUT)
                self.assertFalse(options["check"])
                self.assertEqual(options["timeout"], 300)
                self.assertTrue(options["stdout"].closed)
            self.assertIn(f"Native evidence: {output}", printed.getvalue())
            self.assertEqual(
                calls[0][0],
                [
                    "ctest",
                    "--test-dir",
                    str(build),
                    "--output-on-failure",
                    "--parallel",
                    "2",
                ],
            )
            gcovr = calls[1][0]
            self.assertEqual(gcovr[:3], [sys.executable, "-m", "gcovr"])
            self.assertEqual(gcovr[gcovr.index("--root") + 1], str(source))
            self.assertEqual(gcovr[-1], str(build))
            exclusion = gcovr[gcovr.index("--exclude") + 1]
            for name in (
                "tests/native/input-unit.cpp",
                "vendor/kde-container/test/egl-fence-test.cpp",
                "vendor/kde-container/native/ui-fixture.cpp",
                "vendor/kde-container/native/vnc-benchmark.cpp",
            ):
                self.assertIsNotNone(re.match(exclusion, name))
            self.assertIsNone(
                re.match(exclusion, "vendor/kde-container/native/kwin-input.cpp")
            )
            for flag, name in (
                ("--json", "native.json"),
                ("--xml", "native.xml"),
                ("--html-details", "native.html"),
            ):
                self.assertEqual(gcovr[gcovr.index(flag) + 1], str(output / name))
            return status, logs

    def test_success_collects_all_reports_and_separate_persistent_logs(self):
        status, logs = self.run_commands()
        self.assertEqual(status, 0)
        self.assertEqual(
            logs,
            {
                "ctest.log": "controlled CTest output\n",
                "gcovr.log": "controlled gcovr output\n",
            },
        )

    def test_failed_or_unavailable_ctest_still_runs_the_reporter(self):
        for outcome in (
            8,
            OSError("ctest unavailable"),
            subprocess.TimeoutExpired("ctest", 300),
        ):
            with self.subTest(outcome=outcome):
                status, logs = self.run_commands((outcome, 0))
                self.assertEqual(status, 1)
                self.assertEqual(logs["gcovr.log"], "controlled gcovr output\n")
                if isinstance(outcome, Exception):
                    self.assertIn("ctest failed:", logs["ctest.log"])

    def test_reporter_failure_cannot_pass_even_with_nonempty_report_files(self):
        for outcome in (
            2,
            OSError("gcovr unavailable"),
            subprocess.TimeoutExpired("gcovr", 300),
        ):
            with self.subTest(outcome=outcome):
                status, logs = self.run_commands((0, outcome))
                self.assertEqual(status, 1)
                self.assertEqual(logs["ctest.log"], "controlled CTest output\n")
                if isinstance(outcome, Exception):
                    self.assertIn("gcovr failed:", logs["gcovr.log"])

    def test_every_report_is_required_and_cannot_be_empty(self):
        for name in REPORTS:
            for state in ("missing", "empty"):
                with self.subTest(name=name, state=state):
                    status, _ = self.run_commands(incomplete=(name, state))
                    self.assertEqual(status, 1)

    def test_entrypoint_propagates_failed_test_status_without_host_filesystem_access(
        self,
    ):
        with (
            patch.object(Path, "mkdir"),
            patch.object(Path, "open", mock_open()),
            patch.object(Path, "glob", return_value=[]),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
            patch.object(
                subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=8),
                    SimpleNamespace(returncode=0),
                ],
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(SOURCE), run_name="__main__")
        self.assertEqual(status.exception.code, 1)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
