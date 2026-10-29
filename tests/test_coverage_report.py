"""Coverage evidence must match current sources and pass every file independently."""

import contextlib
import hashlib
import importlib.util
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "coverage_report_under_test", ROOT / "build/coverage_report.py"
)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class SourceIdentityTests(unittest.TestCase):
    def test_inventory_includes_tracked_untracked_and_extensionless_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            sources = {
                "source.py": "print(1)\n",
                "driver": "#!/usr/bin/python3\nprint(1)\n",
                "host-env": "# /// script\nprint(1)\n",
                "script": "#!/bin/bash\ntrue\n",
                "native.cpp": "int main() {return 0;}\n",
            }
            for name, body in sources.items():
                (root / name).write_text(body)
            (root / "README.md").write_text("documentation\n")
            (root / ".gitignore").write_text("ignored.py\n")
            (root / "ignored.py").write_text("ignored=1\n")
            subprocess.run(["git", "add", "source.py"], cwd=root, check=True)
            self.assertEqual(
                REPORT.inventory(root),
                {
                    "source.py": "python",
                    "driver": "python",
                    "host-env": "python",
                    "script": "shell",
                    "native.cpp": "c++",
                },
            )
            before = REPORT.manifest(root)
            self.assertEqual(set(before), set(sources))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(before))
            REPORT.verify_sources([manifest], root)
            (root / "source.py").write_text("print(2)\n")
            with self.assertRaisesRegex(ValueError, "changed after coverage"):
                REPORT.verify_sources([manifest], root)
            with self.assertRaisesRegex(ValueError, "manifest is required"):
                REPORT.verify_sources([], root)

    def test_manifest_set_changes_and_missing_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"old.py": "hash"}))
            with (
                patch.object(REPORT, "manifest", return_value={"new.py": "hash"}),
                self.assertRaisesRegex(ValueError, "changed after coverage"),
            ):
                REPORT.verify_sources([path])


class PythonEvidenceTests(unittest.TestCase):
    def test_real_coverage_data_files_merge_and_unexecuted_lines_stay_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_text("first=1\nsecond=2\nthird=3\n")
            inputs = []
            for number, lines in enumerate(({1}, {2})):
                path = root / f"input-{number}"
                data = CoverageData(basename=str(path))
                data.add_lines({str(source): lines})
                data.write()
                inputs.append(path)
            merged = root / "combined"
            REPORT.combine_python(inputs, merged)
            data = CoverageData(basename=str(merged))
            data.read()
            self.assertEqual(set(data.lines(str(source))), {1, 2})
            metric = REPORT.python_metrics(root, ["source.py"], merged)["source.py"]
            self.assertEqual(set(metric["executable_lines"]), {1, 2, 3})
            self.assertEqual(metric["covered_lines"], [1, 2])
            unrun = root / "unrun.py"
            unrun.write_text("untested=1\n")
            missing = REPORT.python_metrics(root, ["unrun.py"], merged)["unrun.py"]
            self.assertEqual(missing["covered_lines"], [])

    def test_python_exclusions_are_rejected_instead_of_improving_percentages(self):
        coverage = Mock()
        coverage.analysis2.return_value = ("source.py", [1, 2], [2], [2], "2")
        with (
            patch.object(REPORT, "Coverage", return_value=coverage),
            self.assertRaisesRegex(ValueError, "excluded executable lines"),
        ):
            REPORT.python_metrics(
                Path("/private"), ["source.py"], Path("/private/data")
            )


class NativeEvidenceTests(unittest.TestCase):
    def test_native_reports_merge_owned_sources_and_ignore_external_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "file": "/source/native.cpp",
                                "lines": [
                                    {"line_number": 1, "count": 0},
                                    {"line_number": 2, "count": 3},
                                ],
                            },
                            {
                                "file": str(root / "other.cpp"),
                                "lines": [{"line_number": 1, "count": 1}],
                            },
                            {
                                "file": "/third-party/generated.cpp",
                                "lines": [
                                    {
                                        "line_number": 1,
                                        "count": 0,
                                        "gcovr/excluded": True,
                                    }
                                ],
                            },
                        ]
                    }
                )
            )
            second.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "file": "./native.cpp",
                                "lines": [{"line_number": 1, "count": 2}],
                            }
                        ]
                    }
                )
            )
            owned = {"native.cpp": "c++", "other.cpp": "c++"}
            metrics = REPORT.native_metrics([first, second], root, owned=owned)
            self.assertEqual(
                metrics["native.cpp"],
                {"executable_lines": {1, 2}, "covered_lines": {1, 2}},
            )
            self.assertEqual(set(metrics), set(owned))
            with patch.object(REPORT, "inventory", return_value=owned):
                self.assertEqual(REPORT.native_metrics([first, second], root), metrics)

    def test_exclusions_and_invalid_native_counts_fail_closed(self):
        for line in (
            {"line_number": 1, "count": 0, "gcovr/excluded": True},
            *({"line_number": 1, "count": value} for value in (-1, 1.5, True, "1")),
        ):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "gcov.json"
                path.write_text(
                    json.dumps({"files": [{"file": "native.cpp", "lines": [line]}]})
                )
                with self.assertRaisesRegex(ValueError, "excluded|Invalid native"):
                    REPORT.native_metrics([path], owned={"native.cpp": "c++"})


class PerFileGateTests(unittest.TestCase):
    def test_compiler_audited_declarations_are_listed_as_na_with_the_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "declarations.h"
            source.write_text("struct Declared;\n")
            audit = {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "reason": "Compiler audit found declarations only",
            }
            with patch.dict(REPORT.DECLARATION_ONLY, {source.name: audit}, clear=True):
                report = REPORT.evaluate({source.name: "c++"}, {}, root)
                measured = REPORT.evaluate(
                    {source.name: "c++"},
                    {source.name: {"executable_lines": [], "covered_lines": []}},
                    root,
                )
                self.assertTrue(measured["files"][0]["measured"])
            self.assertTrue(report["passed"])
            row = report["files"][0]
            self.assertFalse(row["measured"])
            self.assertIsNone(row["percent"])
            self.assertEqual((row["executable"], row["covered"]), (0, 0))
            self.assertEqual(row["declaration_only"], audit["reason"])
            output = root / "evidence"
            REPORT.write_report(report, output)
            self.assertEqual(json.loads((output / "coverage.json").read_text()), report)
            markdown = (output / "INDEX.md").read_text()
            self.assertIn("declarations.h", markdown)
            self.assertIn("| N/A | PASS |", markdown)
            self.assertIn(audit["reason"], markdown)
            self.assertNotIn("100.00%", markdown)

    def test_declaration_audit_rejects_changed_source_real_code_or_another_language(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "declarations.h"
            source.write_text("struct Declared;\n")
            audit = {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "reason": "Compiler audit",
            }
            with patch.dict(REPORT.DECLARATION_ONLY, {source.name: audit}, clear=True):
                for language, metrics in (
                    ("python", {}),
                    (
                        "c++",
                        {source.name: {"executable_lines": [1], "covered_lines": [1]}},
                    ),
                ):
                    with (
                        self.subTest(language=language, metrics=metrics),
                        self.assertRaisesRegex(ValueError, "requires re-audit"),
                    ):
                        REPORT.evaluate({source.name: language}, metrics, root)
                source.write_text("struct Changed;\n")
                with self.assertRaisesRegex(ValueError, "requires re-audit"):
                    REPORT.evaluate({source.name: "c++"}, {}, root)

    def test_unaudited_declaration_file_still_fails_when_it_is_unmeasured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unreviewed.h"
            source.write_text("struct Declared;\n")
            with patch.dict(REPORT.DECLARATION_ONLY, {}, clear=True):
                report = REPORT.evaluate({source.name: "c++"}, {}, root)
            self.assertFalse(report["passed"])
            self.assertEqual(report["files"][0]["percent"], 0)
            self.assertIsNone(report["files"][0]["declaration_only"])

    def files(self, root):
        for name in ("first.py", "second.sh", "native.cpp"):
            (root / name).write_text("statement\n" * 100)

    def test_threshold_is_per_file_and_missing_sources_cannot_be_averaged_away(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.files(root)
            owned = {"first.py": "python", "second.sh": "shell"}
            metrics = {
                "first.py": {
                    "executable_lines": range(1, 101),
                    "covered_lines": range(1, 101),
                },
                "second.sh": {
                    "executable_lines": range(1, 101),
                    "covered_lines": range(1, 94),
                },
            }
            report = REPORT.evaluate(owned, metrics, root)
            self.assertFalse(report["passed"])
            self.assertEqual(report["files"][1]["percent"], 93)
            metrics["second.sh"]["covered_lines"] = range(1, 95)
            self.assertTrue(REPORT.evaluate(owned, metrics, root)["passed"])
            report = REPORT.evaluate(owned, {"first.py": metrics["first.py"]}, root)
            self.assertFalse(report["passed"])
            self.assertFalse(report["files"][1]["measured"])
            self.assertEqual(report["files"][1]["percent"], 0)
            self.assertFalse(REPORT.evaluate({}, metrics, root)["passed"])
            self.assertFalse(
                REPORT.evaluate(
                    {"first.py": "python"},
                    {"first.py": {"executable_lines": [], "covered_lines": []}},
                    root,
                )["passed"]
            )

    def test_invalid_line_coordinates_and_outside_coverage_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.files(root)
            for positions, covered in (
                ([0], []),
                ([101], []),
                ([1.5], []),
                ([True], []),
                (["1"], []),
                ([1], [2]),
                ([1], [True]),
            ):
                with (
                    self.subTest(positions=positions, covered=covered),
                    self.assertRaisesRegex(
                        ValueError, "Invalid coverage line positions"
                    ),
                ):
                    REPORT.evaluate(
                        {"first.py": "python"},
                        {
                            "first.py": {
                                "executable_lines": positions,
                                "covered_lines": covered,
                            }
                        },
                        root,
                    )

    def test_reports_keep_missing_positions_and_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.files(root)
            report = REPORT.evaluate(
                {"first.py": "python"},
                {"first.py": {"executable_lines": [1, 2], "covered_lines": [1]}},
                root,
            )
            output = root / "report"
            REPORT.write_report(report, output)
            self.assertEqual(json.loads((output / "coverage.json").read_text()), report)
            markdown = (output / "INDEX.md").read_text()
            self.assertIn("94%", markdown)
            self.assertIn("50.00%", markdown)
            self.assertIn("FAIL", markdown)
            self.assertIn("| 2 |", markdown)


class CoverageCliTests(unittest.TestCase):
    def test_record_sources_exits_before_requiring_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested/manifest.json"
            with (
                patch.object(
                    REPORT, "manifest", return_value={"source.py": "current-hash"}
                ),
                patch.object(REPORT, "verify_sources") as verify,
            ):
                self.assertEqual(REPORT.main(["--record-sources", str(path)]), 0)
            self.assertEqual(
                json.loads(path.read_text()), {"source.py": "current-hash"}
            )
            verify.assert_not_called()

    def test_cli_combines_languages_then_returns_failure_for_any_missing_file(self):
        for shell in (False, True):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                owned = {
                    "first.py": "python",
                    "second.sh": "shell",
                    "native.cpp": "c++",
                }
                for name in owned:
                    (root / name).write_text("statement\n")

                def metric():
                    return {"executable_lines": [1], "covered_lines": [1]}

                shell_report = SimpleNamespace(
                    report=Mock(
                        return_value={"files": [{"file": "second.sh", **metric()}]}
                    )
                )
                evaluate = REPORT.evaluate
                arguments = [
                    "--output",
                    str(root / "report"),
                    "--source-manifest",
                    str(root / "manifest.json"),
                    "--python-data",
                    str(root / "python"),
                    "--native-report",
                    str(root / "native.json"),
                ]
                if shell:
                    arguments += [
                        "--shell-traces",
                        str(root / "shell"),
                        "--shell-map",
                        str(root / "shell-map.json"),
                    ]
                with (
                    patch.object(REPORT, "inventory", return_value=owned),
                    patch.object(REPORT, "verify_sources") as verify,
                    patch.object(REPORT, "combine_python") as combine,
                    patch.object(
                        REPORT, "python_metrics", return_value={"first.py": metric()}
                    ),
                    patch.object(
                        REPORT, "native_metrics", return_value={"native.cpp": metric()}
                    ),
                    patch.object(REPORT, "load_module", return_value=shell_report),
                    patch.object(
                        REPORT,
                        "evaluate",
                        side_effect=lambda owned, metrics, evaluate=evaluate, root=root: (
                            evaluate(owned, metrics, root)
                        ),
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(REPORT.main(arguments), 0 if shell else 1)
                verify.assert_called_once_with([root / "manifest.json"])
                combine.assert_called_once_with(
                    [root / "python"], root / "report/.coverage"
                )
                if shell:
                    shell_report.report.assert_called_once()
                else:
                    self.assertIn("FAIL second.sh: 0.00%", output.getvalue())
                self.assertEqual(
                    json.loads((root / "report/coverage.json").read_text())["passed"],
                    shell,
                )

    def test_entrypoint_help_lists_all_language_evidence_inputs(self):
        with (
            patch.object(sys, "argv", ["coverage-report", "--help"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(ROOT / "build/coverage_report.py"), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        for option in (
            "--source-manifest",
            "--python-data",
            "--native-report",
            "--shell-traces",
            "--record-sources",
        ):
            self.assertIn(option, output.getvalue())


if __name__ == "__main__":
    unittest.main()
