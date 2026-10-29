"""Shell instrumentation must preserve behavior without recording command values."""

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).with_name("shell_trace.sh").resolve()


class ShellTraceTests(unittest.TestCase):
    def run_shell(self, program, *, enabled=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.sh"
            target.write_text(program)
            output = root / "trace"
            result = subprocess.run(
                ["bash", str(target)],
                env={
                    **os.environ,
                    "BASH_ENV": str(HOOK),
                    "MT5_SHELL_COVERAGE_DIR": str(output) if enabled else "",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            records = []
            raw = b""
            for path in sorted(output.glob("*.trace")):
                content = path.read_bytes()
                raw += content
                fields = content.split(b"\0")
                self.assertEqual(fields.pop(), b"")
                self.assertEqual(len(fields) % 2, 0)
                records += [
                    (os.fsdecode(source), int(line))
                    for source, line in zip(fields[::2], fields[1::2], strict=True)
                ]
                if inherited := os.environ.get("MT5_SHELL_COVERAGE_DIR"):
                    destination = Path(inherited)
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination / path.name)
            return result, records, raw, str(target)

    def test_locations_capture_functions_branches_and_subshells_without_values(self):
        result, records, raw, target = self.run_shell(
            "secret='never-record-this-password'\n"
            "if [[ $secret == unknown ]]; then\n"
            "    printf 'unreachable\\n'\n"
            "else\n"
            "    value=$(printf 'substitution')\n"
            "fi\n"
            "work() { printf '%s\\n' \"$value\"; }\n"
            "work\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "substitution\n")
        hits = {line for source, line in records if source == target}
        self.assertTrue({1, 2, 5, 7, 8} <= hits)
        self.assertNotIn(3, hits)
        self.assertNotIn(b"never-record-this-password", raw)
        self.assertNotIn(b"printf", raw)

    def test_exit_status_errexit_and_exit_traps_are_preserved(self):
        result, _, _, _ = self.run_shell(
            "false\nstatus=$?\nprintf 'status=%s\\n' \"$status\"\n"
            "trap 'printf exit-trap' EXIT\nset -e\nfalse\nprintf unreachable\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "status=1\nexit-trap")

    def test_disabled_collection_has_no_side_effects(self):
        result, records, raw, _ = self.run_shell(
            "set -euo pipefail\nprintf 'normal\\n'\n", enabled=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "normal\n")
        self.assertEqual(records, [])
        self.assertEqual(raw, b"")

    def test_reloading_its_own_hook_is_safe_and_profiles_initialization(self):
        result, records, _, _ = self.run_shell(
            f"source {shlex.quote(str(HOOK))}\n"
            "__mt5_shell_coverage_record\n"
            "printf 'reloaded\\n'\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "reloaded\n")
        self.assertTrue(any(source == str(HOOK) for source, _ in records))

    def test_existing_debug_and_return_traps_are_rejected_clearly(self):
        for trap in ("DEBUG", "RETURN"):
            with self.subTest(trap=trap):
                body = "__mt5_shell_coverage_record; :" if trap == "DEBUG" else ":"
                result, _, _, _ = self.run_shell(
                    f"trap {shlex.quote(body)} {trap}\n"
                    f"source {shlex.quote(str(HOOK))}\n"
                    "printf 'must-not-run\\n'\n"
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("must-not-run", result.stdout)
                self.assertIn("existing DEBUG or RETURN trap", result.stderr)

    def test_failed_coverage_output_is_fatal_instead_of_silently_losing_hits(self):
        result, _, _, _ = self.run_shell(
            "safe_output=$__mt5_shell_coverage_output\n"
            "safe_debug() { local previous=$__mt5_shell_coverage_output; "
            "__mt5_shell_coverage_output=$safe_output; __mt5_shell_coverage_record; "
            "__mt5_shell_coverage_output=$previous; }\n"
            "trap safe_debug DEBUG\n"
            "__mt5_shell_coverage_output=/dev/null/not-a-directory\n"
            "__mt5_shell_coverage_record\n"
            "printf 'must-not-run\\n'\n"
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("must-not-run", result.stdout)
        self.assertIn("Cannot write shell coverage locations", result.stderr)

    def test_failed_directory_creation_is_fatal(self):
        result, _, _, _ = self.run_shell(
            "MT5_SHELL_COVERAGE_DIR=$0\n"
            f"source {shlex.quote(str(HOOK))}\n"
            "printf 'must-not-run\\n'\n"
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("must-not-run", result.stdout)
        self.assertIn("Cannot create shell coverage directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
