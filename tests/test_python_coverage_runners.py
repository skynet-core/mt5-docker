"""Runner contracts retain diagnostics and remove only their disposable resources."""

import contextlib
import importlib.machinery
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    loader = importlib.machinery.SourceFileLoader(
        "coverage_" + name.replace("-", "_"), str(ROOT / "tests" / name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class WestonRunnerTests(unittest.TestCase):
    def test_wrapper_forwards_failure_policy_and_joins_diagnostics(self):
        weston = load("weston.py")
        with patch.object(
            weston.subprocess,
            "run",
            return_value=SimpleNamespace(stdout=b"test diagnostic"),
        ) as execute:
            self.assertEqual(
                weston.docker("logs", "private-test", check=False), b"test diagnostic"
            )
        execute.assert_called_once_with(
            ["docker", "logs", "private-test"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_private_parent_waits_then_checks_both_nested_backends_and_cleans_up(self):
        weston = load("weston.py")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            calls = []

            def docker(*arguments, **_options):
                calls.append(arguments)
                return b"weston-private-test\n"

            def publish_sockets(_delay):
                (output / "parent/runtime/wayland-0").touch()
                (output / "parent/x11/X0").touch()

            with (
                patch.object(weston, "docker", side_effect=docker),
                patch.object(weston, "start") as start,
                patch.object(weston, "collect") as collect,
                patch.object(weston, "validate_clipboard") as validate,
                patch.object(weston.time, "sleep", side_effect=publish_sockets),
            ):
                weston.main(
                    SimpleNamespace(
                        output=str(output),
                        parent_image="private-parent-image",
                        image="private-child-image",
                    )
                )
            self.assertEqual(
                [call.args[3] for call in start.call_args_list], ["wayland", "x11"]
            )
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(validate.call_count, 2)
            self.assertTrue(
                all(
                    call.args[0].parent_x11_clipboard
                    for call in validate.call_args_list
                )
            )
            self.assertTrue(json.loads((output / "summary.json").read_text())["passed"])
            self.assertFalse(list((output / "parent/runtime").iterdir()))
            self.assertFalse(list((output / "parent/x11").iterdir()))
            run = calls[0]
            self.assertIn("--network", run)
            self.assertEqual(run[run.index("--network") + 1], "none")
            removed = [call[-1] for call in calls if call[:2] == ("rm", "-f")]
            self.assertEqual(len(removed), 3)
            with self.assertRaises(FileExistsError):
                weston.main(SimpleNamespace(output=str(output)))

    def test_missing_display_and_failed_collection_still_remove_parent_and_child(self):
        weston = load("weston.py")
        for fail_collection in (False, True):
            with (
                self.subTest(fail_collection=fail_collection),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "evidence"
                calls = []

                def docker(
                    *arguments,
                    output=output,
                    calls=calls,
                    fail_collection=fail_collection,
                    **_options,
                ):
                    calls.append(arguments)
                    if arguments[0] == "run" and fail_collection:
                        (output / "parent/runtime/wayland-0").touch()
                        (output / "parent/x11/X0").touch()
                    return b"private diagnostic"

                with (
                    patch.object(weston, "docker", side_effect=docker),
                    patch.object(weston, "start"),
                    patch.object(weston, "validate_clipboard"),
                    patch.object(
                        weston,
                        "collect",
                        side_effect=RuntimeError("synthetic collection failure"),
                    ),
                    patch.object(weston.time, "monotonic", side_effect=[0, 31]),
                    self.assertRaises(RuntimeError),
                ):
                    weston.main(
                        SimpleNamespace(
                            output=str(output),
                            parent_image="private-parent-image",
                            image="private-child-image",
                        )
                    )
                removed = [call[-1] for call in calls if call[:2] == ("rm", "-f")]
                self.assertTrue(any("parent" in name for name in removed))
                if fail_collection:
                    self.assertTrue(any("wayland" in name for name in removed))
                self.assertTrue((output / "parent/weston.log").is_file())

    def test_cli_runs_the_same_private_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cli"

            def execute(command, **_options):
                if command[1] == "run":
                    (output / "parent/runtime/wayland-0").touch()
                    (output / "parent/x11/X0").touch()
                return SimpleNamespace(
                    stdout=b"private weston", stderr=b"", returncode=0
                )

            with (
                patch.object(sys, "argv", ["weston", "--output", str(output)]),
                patch("subprocess.run", side_effect=execute),
                patch("runtime.start"),
                patch("runtime.collect"),
                patch("clipboard.validate"),
            ):
                runpy.run_path(str(ROOT / "tests/weston.py"), run_name="__main__")
            self.assertTrue(json.loads((output / "summary.json").read_text())["passed"])


class SmokeRunnerTests(unittest.TestCase):
    def run_case(self, output, *, failure=None, cli=False):
        smoke = load("ci-smoke.py")
        calls = []
        attempts = 0

        def execute(command, **options):
            nonlocal attempts
            calls.append(command)
            operation = command[1]
            if operation == "stop" and failure == "stop-timeout":
                raise subprocess.TimeoutExpired(command, options["timeout"])
            if operation == "inspect":
                body = b"sha256:private-test-image\n"
            elif "/usr/local/bin/plasma-healthcheck" in command:
                attempts += 1
                return SimpleNamespace(
                    stdout=b"",
                    stderr=b"",
                    returncode=1 if attempts == 1 or failure == "readiness" else 0,
                )
            elif "test" in command and "-s" in command:
                return SimpleNamespace(
                    stdout=b"",
                    stderr=b"",
                    returncode=1 if attempts == 1 or failure == "readiness" else 0,
                )
            elif command[-1] == smoke.PAYLOAD_CHECK:
                if failure == "payload":
                    return SimpleNamespace(
                        stdout=b"", stderr=b"synthetic missing payload", returncode=23
                    )
                body = json.dumps(
                    {"terminal64.exe": 256, "msedgewebview2.exe": 256}
                ).encode()
            elif "--version" in command:
                body = b"wine-11.20\n"
            elif "supportInformation" in command:
                body = (
                    b"unknown support"
                    if failure == "version"
                    else b"KWin version: 6.8.2\n"
                )
            elif command[-1] == smoke.WINDOWS_CHECK:
                if failure == "windows-timeout":
                    raise subprocess.TimeoutExpired(
                        command,
                        options["timeout"],
                        output=b"partial Windows import",
                        stderr=b"synthetic timeout",
                    )
                body = (
                    b"Windows imports complete\n"
                    + json.dumps(
                        {"mt5_mcp_version": "0.6.3", "mcp_server_created": True}
                    ).encode()
                    + b"\n"
                )
            elif operation == "logs" and failure == "windows-timeout":
                raise subprocess.TimeoutExpired(command, 30)
            else:
                body = b"private diagnostic\n"
            return SimpleNamespace(stdout=body, stderr=b"", returncode=0)

        with (
            patch.object(smoke.subprocess, "run", side_effect=execute),
            patch.object(smoke.time, "sleep"),
            patch.object(
                smoke.time,
                "monotonic",
                side_effect=[0, 121] if failure == "readiness" else None,
            )
            if failure == "readiness"
            else patch.object(smoke.time, "monotonic", return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            if cli:
                with patch.object(
                    sys, "argv", ["smoke", "private-image", "--output", str(output)]
                ):
                    runpy.run_path(str(ROOT / "tests/ci-smoke.py"), run_name="__main__")
            else:
                smoke.main(SimpleNamespace(output=str(output), image="private-image"))
        return calls

    def test_offline_smoke_keeps_payload_versions_and_cleanup_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "smoke"
            calls = self.run_case(output)
            evidence = json.loads((output / "results.json").read_text())
            self.assertTrue(evidence["passed"])
            self.assertEqual(evidence["kwin_version"], "6.8.2")
            self.assertTrue(evidence["windows"]["mcp_server_created"])
            self.assertEqual(evidence["network"], "none")
            run = calls[0]
            self.assertEqual(run[run.index("--network") + 1], "none")
            self.assertIn(
                "type=bind,source=/dev/null,target=/etc/systemd/system/mt5-mcp.service,readonly",
                run,
            )
            self.assertEqual([call[1] for call in calls[-3:]], ["stop", "rm", "volume"])
            self.assertTrue((output / "services.log").is_file())
            self.assertTrue((output / "windows.log").is_file())

    def test_failure_stages_record_error_and_attempt_disposable_cleanup(self):
        for failure in ("payload", "version", "readiness", "windows-timeout"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "failed"
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    self.run_case(output, failure=failure)
                evidence = json.loads((output / "results.json").read_text())
                self.assertFalse(evidence["passed"])
                self.assertTrue(evidence["error"])
                self.assertTrue((output / "failed-units.log").is_file())
                if failure == "windows-timeout":
                    self.assertIn(
                        "partial Windows import", (output / "stdout.log").read_text()
                    )

    def test_entrypoint_runs_the_same_offline_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cli"
            self.run_case(output, cli=True)
            self.assertTrue(json.loads((output / "results.json").read_text())["passed"])

    def test_stop_timeout_still_forces_container_and_volume_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = self.run_case(Path(directory) / "smoke", failure="stop-timeout")
        self.assertEqual([call[1] for call in calls[-3:]], ["stop", "rm", "volume"])


if __name__ == "__main__":
    unittest.main()
