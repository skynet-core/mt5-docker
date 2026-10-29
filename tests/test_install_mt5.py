"""Transient Wine RPC failures may retry once, but missing payloads remain fatal."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from shell_coverage import copy_shell

SOURCE = Path(__file__).resolve().parents[1] / "build/install-mt5"


class InstallerRecoveryTests(unittest.TestCase):
    def run_installer(
        self,
        failures,
        failed_status=124,
        payload_during_failure=False,
        wine_version="wine-11.20",
        mt5_status=1,
        missing_executable="",
        import_status=0,
        delayed_display=False,
        wow64=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            script = root / "install-mt5"
            (root / "tmp").mkdir()
            if wow64:
                (root / "wow64").mkdir()
            copy_shell(
                SOURCE,
                script,
                (
                    ("/tmp", str(root / "tmp")),
                    ("/home/plasma/.wine", str(prefix)),
                    ("/usr/lib64/wine-wow64", str(root / "wow64")),
                ),
            )
            environment = root / "commands.sh"
            environment.write_text(
                f"source {shlex.quote(str(SOURCE.parents[1] / 'tests/shell_trace.sh'))}\n"
                + r"""
record() { printf '%q ' "$@" >> "$TEST_LOG"; printf '\n' >> "$TEST_LOG"; }
install() {
    if [[ ${@: -1} == "$TEST_PREFIX"* ]]; then mkdir -p "${@: -1}"; fi
}
id() { printf '1000\n'; }
Xvfb() { exec /usr/bin/sleep 120; }
xdpyinfo() { if [[ $TEST_DELAYED_DISPLAY == 1 && ! -e $TEST_READY ]]; then touch "$TEST_READY"; return 1; fi; return 0; }
sleep() { record sleep "$@"; }
curl() { return 0; }
sha256sum() { return 0; }
unzip() { return 0; }
uv() { return 0; }
chown() { return 0; }
cabextract() { record cabextract "$@"; }
rm() { return 0; }
timeout() { shift; "$@"; }
runuser() { shift 3; "$@"; }
wineserver() { record wineserver "$@"; }
wine() {
    record wine "$@"
    if [[ -n ${WINEARCH:-} ]]; then record arch "$WINEARCH"; fi
    if [[ $1 == --version ]]; then printf '%s\n' "$TEST_WINE_VERSION"; fi
    if [[ $1 == */webview2.exe ]]; then
        local count=0
        if [[ -f $TEST_COUNT ]]; then count=$(< "$TEST_COUNT"); fi
        count=$((count + 1))
        printf '%s' "$count" > "$TEST_COUNT"
        if ((count <= TEST_FAILURES)); then return "$TEST_FAILED_STATUS"; fi
    fi
    if [[ $1 == */mt5setup.exe ]]; then return "$TEST_MT5_STATUS"; fi
    if [[ $1 == 'C:\Python\python.exe' ]]; then return "$TEST_IMPORT_STATUS"; fi
    return 0
}
find() {
    if [[ $* == *msedgewebview2.exe* ]]; then
        if (($(< "$TEST_COUNT") > TEST_FAILURES || TEST_PAYLOAD_ON_FAILURE)); then
            printf '%s/drive_c/WebView/msedgewebview2.exe\n' "$TEST_PREFIX"
        fi
    elif [[ $* == *-maxdepth* ]]; then
        local previous= executable=
        for option in "$@"; do if [[ $previous == -iname ]]; then executable=$option; fi; previous=$option; done
        [[ $executable != "$TEST_MISSING_EXECUTABLE" ]] || return 0
        printf '%s/drive_c/MT5/%s\n' "$TEST_PREFIX" "$executable"
    fi
}
"""
            )
            log = root / "commands.log"
            result = subprocess.run(
                ["/bin/bash", str(script)],
                env={
                    **os.environ,
                    "BASH_ENV": str(environment),
                    "TEST_LOG": str(log),
                    "TEST_COUNT": str(root / "attempts"),
                    "TEST_PREFIX": str(prefix),
                    "TEST_FAILURES": str(failures),
                    "TEST_FAILED_STATUS": str(failed_status),
                    "TEST_PAYLOAD_ON_FAILURE": str(int(payload_during_failure)),
                    "TEST_WINE_VERSION": wine_version,
                    "TEST_MT5_STATUS": str(mt5_status),
                    "TEST_MISSING_EXECUTABLE": missing_executable,
                    "TEST_IMPORT_STATUS": str(import_status),
                    "TEST_DELAYED_DISPLAY": str(int(delayed_display)),
                    "TEST_READY": str(root / "ready"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            return result, [shlex.split(line) for line in log.read_text().splitlines()]

    def test_missing_runtime_retries_after_stopping_wine_and_still_checks_imports(self):
        result, commands = self.run_installer(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        attempts = [
            i
            for i, command in enumerate(commands)
            if command[1].endswith("/webview2.exe")
        ]
        self.assertEqual(len(attempts), 2)
        self.assertIn(
            ["wineserver", "-k", "-w"], commands[attempts[0] + 1 : attempts[1]]
        )
        self.assertIn(["wineserver", "-p"], commands[: attempts[0]])
        self.assertTrue(
            any(command[1] == r"C:\Python\python.exe" for command in commands)
        )

    def test_second_missing_runtime_is_fatal_before_mt5_installation(self):
        result, commands = self.run_installer(2)
        self.assertEqual(result.returncode, 1)
        self.assertIn("WebView2 installation failed after two attempts", result.stderr)
        self.assertEqual(
            sum(command[1].endswith("/webview2.exe") for command in commands), 2
        )
        self.assertFalse(
            any(command[1].endswith("/mt5setup.exe") for command in commands)
        )

    def test_unexpected_exit_status_remains_fatal_even_with_a_payload(self):
        result, commands = self.run_installer(
            2, failed_status=5, payload_during_failure=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("status 5", result.stderr)
        self.assertFalse(
            any(command[1].endswith("/mt5setup.exe") for command in commands)
        )

    def test_wine10_installs_native_ucrt_and_waits_for_x11(self):
        result, commands = self.run_installer(
            0, wine_version="wine-10.0.1", delayed_display=True, wow64=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(command[0] == "cabextract" for command in commands), 2)
        self.assertIn(["sleep", "0.25"], commands)
        self.assertIn(["arch", "wow64"], commands)
        self.assertTrue(
            any(
                "ucrtbase" in command and "native,builtin" in command
                for command in commands
            )
        )

    def test_mt5_installer_payload_and_windows_import_failures_stay_fatal(self):
        for options, status, message in (
            ({"mt5_status": 7}, 1, "MT5 installer failed: 7"),
            (
                {"missing_executable": "metaeditor64.exe"},
                1,
                "Missing MT5 executable: metaeditor64.exe",
            ),
            ({"import_status": 42}, 42, ""),
        ):
            with self.subTest(options=options):
                result, commands = self.run_installer(0, **options)
                self.assertEqual(result.returncode, status)
                if message:
                    self.assertIn(message, result.stderr)
                self.assertEqual(commands[-1], ["wineserver", "-k"])


if __name__ == "__main__":
    unittest.main()
