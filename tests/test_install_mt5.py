"""Transient Wine RPC failures may retry once, but missing payloads remain fatal."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "build/install-mt5"


class InstallerRecoveryTests(unittest.TestCase):
    def run_installer(self, failures, failed_status=124, payload_during_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            script = root / "install-mt5"
            script.write_text(
                SOURCE.read_text().replace("/home/plasma/.wine", str(prefix))
            )
            environment = root / "commands.sh"
            environment.write_text(
                r"""
record() { printf '%q ' "$@" >> "$TEST_LOG"; printf '\n' >> "$TEST_LOG"; }
install() {
    if [[ ${@: -1} == "$TEST_PREFIX"* ]]; then mkdir -p "${@: -1}"; fi
}
id() { printf '1000\n'; }
Xvfb() { exec /usr/bin/sleep 120; }
xdpyinfo() { return 0; }
curl() { return 0; }
sha256sum() { return 0; }
unzip() { return 0; }
uv() { return 0; }
chown() { return 0; }
rm() { return 0; }
timeout() { shift; "$@"; }
runuser() { shift 3; "$@"; }
wineserver() { record wineserver "$@"; }
wine() {
    record wine "$@"
    if [[ $1 == --version ]]; then printf 'wine-11.20\n'; fi
    if [[ $1 == /tmp/webview2.exe ]]; then
        local count=0
        if [[ -f $TEST_COUNT ]]; then count=$(< "$TEST_COUNT"); fi
        count=$((count + 1))
        printf '%s' "$count" > "$TEST_COUNT"
        if ((count <= TEST_FAILURES)); then return "$TEST_FAILED_STATUS"; fi
    fi
    if [[ $1 == /tmp/mt5setup.exe ]]; then return 1; fi
    return 0
}
find() {
    if [[ $* == *msedgewebview2.exe* ]]; then
        if (($(< "$TEST_COUNT") > TEST_FAILURES || TEST_PAYLOAD_ON_FAILURE)); then
            printf '%s/drive_c/WebView/msedgewebview2.exe\n' "$TEST_PREFIX"
        fi
    elif [[ $* == *-maxdepth* ]]; then
        printf '%s/drive_c/MT5/terminal64.exe\n' "$TEST_PREFIX"
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
            i for i, command in enumerate(commands) if command[1] == "/tmp/webview2.exe"
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
            sum(command[1] == "/tmp/webview2.exe" for command in commands), 2
        )
        self.assertFalse(any(command[1] == "/tmp/mt5setup.exe" for command in commands))

    def test_unexpected_exit_status_remains_fatal_even_with_a_payload(self):
        result, commands = self.run_installer(
            2, failed_status=5, payload_during_failure=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("status 5", result.stderr)
        self.assertFalse(any(command[1] == "/tmp/mt5setup.exe" for command in commands))


if __name__ == "__main__":
    unittest.main()
