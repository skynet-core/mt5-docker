"""Coverage accounts for every owned shell file and validates temporary mappings."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shell_coverage import copy_shell, executable_lines, report


class ShellCoverageReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self.source = self.root / "entry.sh"
        self.source.write_text("#!/bin/bash\nprintf /original\\n\nexit 0\n")
        self.uncovered = self.root / "uncovered"
        self.uncovered.write_text("#!/usr/bin/env bash\nprintf missed\n")
        self.traces = self.root / "traces"
        self.traces.mkdir()
        self.mapping = self.root / "mapping.jsonl"

    def trace(self, path, *lines):
        data = b"".join(
            os.fsencode(path) + b"\0" + str(line).encode() + b"\0" for line in lines
        )
        (self.traces / "bash-1.trace").write_bytes(data)

    def mapped_copy(self, destination):
        with patch.dict(os.environ, {"MT5_SHELL_COVERAGE_MAP": str(self.mapping)}):
            return copy_shell(self.source, destination, (("/original", "/fixture"),))

    def test_every_owned_file_stays_in_report_even_when_unexecuted(self):
        self.trace(self.source, 2)
        rows = {row["file"]: row for row in report(self.root, self.traces)["files"]}
        self.assertEqual(set(rows), {"entry.sh", "uncovered"})
        self.assertEqual(rows["entry.sh"]["executable_lines"], [2, 3])
        self.assertEqual(rows["entry.sh"]["covered_lines"], [2])
        self.assertEqual(rows["uncovered"]["covered_lines"], [])

    def test_ast_excludes_comments_heredoc_content_and_pure_compound_keywords(self):
        code = (
            "# comment\nexport word=test\nlocal value=1\n"
            "for value in 1 2; do\n  case $value in\n  1) ((value += 1));;\n"
            "  *) [[ $value ]];;\n  esac\ndone\n"
            "cat <<'PYTHON'\nthis_is_python_not_shell()\nPYTHON\n"
        )
        self.assertEqual(executable_lines(code), {2, 3, 4, 5, 6, 7, 10})

    def test_verified_source_copy_is_mapped_even_after_fixture_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = self.mapped_copy(Path(directory) / "copy")
            self.trace(copy, 2, 3)
            self.assertEqual(
                report(self.root, self.traces, self.mapping)["files"][0][
                    "covered_lines"
                ],
                [2, 3],
            )
        self.assertEqual(
            report(self.root, self.traces, self.mapping)["files"][0]["covered_lines"],
            [2, 3],
        )

    def test_stale_canonical_copy_and_unsafe_mapping_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = self.mapped_copy(Path(directory) / "copy")
            original_mapping = self.mapping.read_text()
            item = json.loads(original_mapping)
            for change in (
                {"source_sha256": "wrong"},
                {"copy_sha256": "wrong"},
                {"replacements": [["printf", "/fixture"]]},
                {"replacements": [["/original", "/new\nline"]]},
                {"replacements": [["/original", "/same-line; false"]]},
            ):
                with self.subTest(change=change):
                    self.mapping.write_text(json.dumps({**item, **change}) + "\n")
                    with self.assertRaises(ValueError):
                        report(self.root, self.traces, self.mapping)
            self.mapping.write_text(original_mapping)
            copy.write_text("#!/bin/bash\necho changed\n")
            with self.assertRaisesRegex(ValueError, "Executed shell fixture"):
                report(self.root, self.traces, self.mapping)

    def test_foreign_sources_are_not_credited_and_ambiguous_copies_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = self.mapped_copy(Path(directory) / "copy")
            self.trace("/outside/not-ours", 1)
            item = json.loads(self.mapping.read_text())
            with self.mapping.open("a") as stream:
                stream.write(json.dumps({**item, "source": "/outside/not-ours"}) + "\n")
            self.assertEqual(
                report(self.root, self.traces, self.mapping)["files"][0][
                    "covered_lines"
                ],
                [],
            )
            copy.unlink()
            item["source"] = str(self.uncovered)
            item["source_sha256"] = hashlib.sha256(
                self.uncovered.read_bytes()
            ).hexdigest()
            item["copy_sha256"] = item["source_sha256"]
            item["replacements"] = []
            with self.mapping.open("a") as stream:
                stream.write(json.dumps(item) + "\n")
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                report(self.root, self.traces, self.mapping)

    def test_corrupt_traces_and_out_of_range_lines_fail_instead_of_becoming_coverage(
        self,
    ):
        for content in (
            b"broken",
            b"path\0",
            os.fsencode(self.source) + b"\0not-a-line\0",
        ):
            with self.subTest(content=content):
                (self.traces / "bad.trace").write_bytes(content)
                with self.assertRaises(ValueError):
                    report(self.root, self.traces)
        (self.traces / "bad.trace").unlink()
        self.trace(self.source, 999)
        with self.assertRaisesRegex(ValueError, "outside"):
            report(self.root, self.traces)

    def test_actual_bash_array_assignment_is_attributed_to_its_ast_start(self):
        self.source.write_text(
            "#!/bin/bash\nvalues=(\n one\n two\n)\nprintf '%s\\n' \"${values[*]}\"\n"
        )
        result = subprocess.run(
            ["bash", str(self.source)],
            env={
                **os.environ,
                "BASH_ENV": str(Path(__file__).with_name("shell_trace.sh")),
                "MT5_SHELL_COVERAGE_DIR": str(self.traces),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "one two\n")
        row = report(self.root, self.traces)["files"][0]
        self.assertEqual(row["executable_lines"], [2, 6])
        self.assertEqual(row["covered_lines"], [2, 6])

    def test_multiline_assignment_does_not_credit_an_unexecuted_adjacent_command(self):
        self.source.write_text(
            "#!/bin/bash\nvalue=$(\n printf safe\n) || false\nprintf done\n"
        )
        result = subprocess.run(
            ["bash", str(self.source)],
            env={
                **os.environ,
                "BASH_ENV": str(Path(__file__).with_name("shell_trace.sh")),
                "MT5_SHELL_COVERAGE_DIR": str(self.traces),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "done")
        row = report(self.root, self.traces)["files"][0]
        self.assertEqual(row["executable_lines"], [2, 3, 4, 5])
        self.assertEqual(row["covered_lines"], [2, 5])


if __name__ == "__main__":
    unittest.main()
