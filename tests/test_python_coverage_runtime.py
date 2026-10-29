"""Readiness and kiosk lifecycle tests use private state and owned child processes."""

import configparser
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    source = ROOT / "vendor/kde-container/libexec" / name
    loader = importlib.machinery.SourceFileLoader(
        "coverage_" + name.replace("-", "_"), str(source)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


WAIT = load("plasma-wait")
APP = load("plasma-target-app")


class ReadinessTests(unittest.TestCase):
    def test_remote_protocols_reject_wrong_or_failed_servers(self):
        for kind, payload, expected in (
            ("vnc", b"RFB 003.008\n", True),
            ("vnc", b"HTTP/1.0 200 OK\r\n", False),
            ("web", b"HTTP/1.0 200 OK\r\n\r\n", True),
            ("web", b"HTTP/1.0 503 Busy\r\n\r\n", False),
        ):
            with self.subTest(kind=kind, payload=payload):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.recv.return_value = payload
                with patch.object(
                    WAIT.socket, "create_connection", return_value=connection
                ) as connect:
                    self.assertEqual(WAIT.ready(kind), expected)
                connect.assert_called_once_with(
                    ("127.0.0.1", 5900 if kind == "vnc" else 6080), timeout=1
                )
                if kind == "web":
                    connection.sendall.assert_called_once_with(
                        b"GET /vnc.html HTTP/1.0\r\nHost: localhost\r\n\r\n"
                    )
        with patch.object(
            WAIT.socket,
            "create_connection",
            side_effect=OSError("synthetic unavailable socket"),
        ):
            self.assertFalse(WAIT.ready("vnc"))

    def test_wayland_connects_only_to_private_runtime_socket(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": "/private/runtime",
                    "WAYLAND_DISPLAY": "wayland-test",
                },
                clear=True,
            ),
            patch.object(WAIT.socket, "socket", return_value=connection),
        ):
            self.assertTrue(WAIT.ready("wayland"))
        connection.connect.assert_called_once_with("/private/runtime/wayland-test")
        connection.settimeout.assert_called_once_with(1)

    def test_x11_desktop_and_kiosk_readiness_require_the_right_process(self):
        with patch.object(
            WAIT.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ):
            self.assertTrue(WAIT.ready("x11"))
        with patch.object(
            WAIT.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 2)
        ):
            self.assertFalse(WAIT.ready("x11"))
        for kind, target, owner, app_status, expected, service in (
            ("desktop", "", True, 0, True, "org.kde.plasmashell"),
            ("desktop", "wine", True, 0, True, "org.kde.KWin"),
            ("desktop", "wine", True, 1, False, "org.kde.KWin"),
            ("desktop", "wine", False, 0, False, "org.kde.KWin"),
            ("compositor", "", True, 0, True, "org.kde.KWin"),
        ):
            with self.subTest(
                kind=kind, target=target, owner=owner, app_status=app_status
            ):
                replies = [
                    SimpleNamespace(
                        returncode=0,
                        stdout=b"boolean true" if owner else b"boolean false",
                    ),
                    SimpleNamespace(returncode=app_status),
                ]
                with (
                    patch.dict(os.environ, {"TARGET_APP": target}, clear=True),
                    patch.object(WAIT.subprocess, "run", side_effect=replies) as run,
                ):
                    self.assertEqual(WAIT.ready(kind), expected)
                self.assertIn("string:" + service, run.call_args_list[0].args[0])
                self.assertEqual(
                    run.call_count, 2 if kind == "desktop" and target and owner else 1
                )
        with self.assertRaisesRegex(ValueError, "Unknown probe"):
            WAIT.ready("unknown")

    def test_cli_waits_boundedly_and_reports_timeout(self):
        source = ROOT / "vendor/kde-container/libexec/plasma-wait"
        for returncode, expected in (
            (0, None),
            (1, "Timed out waiting for Plasma x11"),
        ):
            with (
                self.subTest(returncode=returncode),
                patch.object(sys, "argv", [str(source), "x11"]),
                patch.dict(os.environ, {"PLASMA_WAIT_TIMEOUT": "1"}, clear=True),
                patch(
                    "subprocess.run",
                    return_value=SimpleNamespace(returncode=returncode),
                ),
                patch("time.monotonic", side_effect=[0, 0, 2]),
                patch("time.sleep") as sleep,
            ):
                if expected:
                    with self.assertRaisesRegex(SystemExit, expected):
                        runpy.run_path(str(source), run_name="__main__")
                    sleep.assert_called_once_with(0.25)
                else:
                    runpy.run_path(str(source), run_name="__main__")
                    sleep.assert_not_called()


class TargetApplicationTests(unittest.TestCase):
    def test_invalid_command_and_chromium_fallback(self):
        for value in ("", "\n/bin/true", "/bin/true\r"):
            with (
                self.subTest(value=repr(value)),
                patch.dict(os.environ, {"TARGET_APP": value}, clear=True),
                self.assertRaises(ValueError),
            ):
                APP.command()
        with (
            patch.dict(os.environ, {"TARGET_APP": "missing-test-command"}, clear=True),
            patch.object(APP.shutil, "which", return_value=None),
            self.assertRaisesRegex(ValueError, "not available"),
        ):
            APP.command()
        with (
            patch.dict(
                os.environ,
                {"TARGET_APP": "chromium 'https://example.invalid/a b'"},
                clear=True,
            ),
            patch.object(
                APP.shutil, "which", side_effect=[None, "/usr/bin/chromium-browser"]
            ),
        ):
            self.assertEqual(
                APP.command(),
                ["/usr/bin/chromium-browser", "https://example.invalid/a b"],
            )

    def test_configure_validate_and_check_keep_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            environment = {
                "TARGET_APP": shlex.quote(sys.executable),
                "XDG_CONFIG_HOME": directory,
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(APP, "STATE", state),
            ):
                for option in ("--validate", "--configure"):
                    with patch.object(sys, "argv", ["app", option]):
                        self.assertEqual(APP.main(), 0)
                config = configparser.ConfigParser()
                config.read(Path(directory) / "kwinrulesrc")
                self.assertEqual(config["1"]["types"], "1")
                self.assertEqual(config["1"]["fullscreen"], "true")
                with patch.object(sys, "argv", ["app", "--check"]):
                    self.assertEqual(APP.main(), 1)
                    state.write_text(json.dumps({"pid": 123, "start_time": "old"}))
                    with patch.object(APP, "start_time", return_value="new"):
                        self.assertEqual(APP.main(), 1)
                    with patch.object(APP, "start_time", return_value="old"):
                        self.assertEqual(APP.main(), 0)
                for payload in ("not json", "{}", "[]"):
                    state.write_text(payload)
                    self.assertFalse(APP.check())

    def test_process_identity_handles_parentheses_and_missing_processes(self):
        with patch.object(
            Path,
            "read_text",
            return_value="123 (name with (parentheses)) "
            + " ".join(str(i) for i in range(3, 53)),
        ):
            self.assertEqual(APP.start_time(123), "22")
        with patch.object(APP.os, "killpg", side_effect=ProcessLookupError):
            APP.signal_group(SimpleNamespace(pid=123), signal.SIGTERM)

    def test_supervisor_restarts_owned_child_and_cleans_its_state_on_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            counter = root / "counter.json"
            environment_file = root / "environment.json"
            child = root / "child.py"
            child.write_text(
                "import json,os,sys,time\nfrom pathlib import Path\n"
                "counter,environment,state=map(Path,sys.argv[1:])\n"
                "attempt=json.loads(counter.read_text())['attempt']+1 if counter.exists() else 1\n"
                "counter.write_text(json.dumps({'attempt':attempt,'pid':os.getpid()}))\n"
                "environment.write_text(json.dumps(dict(os.environ)))\n"
                "while not state.exists(): time.sleep(0.01)\n"
                "if attempt==1: time.sleep(0.05);sys.exit(23)\n"
                "while True: time.sleep(10)\n"
            )
            handlers = {}
            errors = []
            original_sleep = time.sleep
            original_popen = subprocess.Popen

            def launch_owned_child(*arguments, **options):
                # Never signal a group unless this child owns its new session.
                self.assertTrue(options.get("start_new_session"))
                return original_popen(*arguments, **options)

            def stop_after_restart():
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            attempt = json.loads(counter.read_text())
                            current = json.loads(state.read_text())
                            if (
                                attempt["attempt"] == 2
                                and attempt["pid"] == current["pid"]
                            ):
                                return
                        except (OSError, ValueError):
                            pass
                        threading.Event().wait(0.01)
                    errors.append("supervisor did not restart its exited child")
                finally:
                    if signal.SIGTERM in handlers:
                        handlers[signal.SIGTERM](None, None)

            environment = {
                "TARGET_APP": shlex.join(
                    [
                        sys.executable,
                        str(child),
                        str(counter),
                        str(environment_file),
                        str(state),
                    ]
                ),
                "DISPLAY_MODE": "nested",
                "PATH": "/private/compositor-only",
                "PLASMA_APP_PATH": os.defpath,
                "LD_LIBRARY_PATH": "/private/qt",
                "PLASMA_APP_LD_LIBRARY_PATH": "",
                "PLASMA_KWIN_BINARY": "/private/kwin",
            }
            worker = threading.Thread(target=stop_after_restart)
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["app"]),
                patch.object(APP, "STATE", state),
                patch.object(APP.subprocess, "run") as prerequisite,
                patch.object(APP.subprocess, "Popen", side_effect=launch_owned_child),
                patch.object(
                    APP.signal,
                    "signal",
                    side_effect=lambda sig, handler: handlers.__setitem__(sig, handler),
                ),
                patch.object(
                    APP.time,
                    "sleep",
                    side_effect=lambda seconds: original_sleep(min(seconds, 0.01)),
                ),
                contextlib.redirect_stdout(io.StringIO()) as log,
            ):
                worker.start()
                try:
                    self.assertEqual(APP.main(), 0)
                finally:
                    worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(json.loads(counter.read_text())["attempt"], 2)
            self.assertFalse(state.exists())
            self.assertIn("restarting", log.getvalue())
            child_environment = json.loads(environment_file.read_text())
            self.assertEqual(child_environment["PATH"], os.defpath)
            self.assertNotIn("LD_LIBRARY_PATH", child_environment)
            self.assertNotIn("PLASMA_KWIN_BINARY", child_environment)
            prerequisite.assert_called_once()

    def test_remote_chromium_flags_and_force_kill_for_unresponsive_child(self):
        for url in ("", " https://example.invalid/"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                handlers = {}
                child = Mock(pid=456, returncode=0)
                child.poll.return_value = None
                child.wait.side_effect = [subprocess.TimeoutExpired("child", 10), 0]

                def sleep(_seconds, handlers=handlers):
                    if signal.SIGTERM in handlers:
                        handlers[signal.SIGTERM](None, None)

                with (
                    patch.dict(
                        os.environ,
                        {
                            "TARGET_APP": "chromium" + url,
                            "DISPLAY_MODE": "web",
                            "DISPLAY_BACKEND": "wayland",
                        },
                        clear=True,
                    ),
                    patch.object(sys, "argv", ["app"]),
                    patch.object(APP, "STATE", Path(directory) / "state.json"),
                    patch.object(APP.shutil, "which", return_value="/usr/bin/chromium"),
                    patch.object(APP, "start_time", return_value="identity"),
                    patch.object(
                        APP.subprocess,
                        "run",
                        side_effect=[
                            SimpleNamespace(returncode=0),
                            SimpleNamespace(returncode=1),
                            SimpleNamespace(returncode=0),
                        ],
                    ),
                    patch.object(APP.subprocess, "Popen", return_value=child) as spawn,
                    patch.object(
                        APP.signal,
                        "signal",
                        side_effect=lambda sig, handler, handlers=handlers: (
                            handlers.__setitem__(sig, handler)
                        ),
                    ),
                    patch.object(APP.time, "sleep", side_effect=sleep),
                    patch.object(APP.os, "killpg") as kill,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(APP.main(), 0)
                arguments = spawn.call_args.args[0]
                self.assertIn("--kiosk", arguments)
                self.assertIn("--ozone-platform=wayland", arguments)
                self.assertEqual(arguments[-1], url.strip() or "about:blank")
                self.assertIn(
                    unittest.mock.call(456, signal.SIGKILL), kill.call_args_list
                )

    def test_remote_output_timeout_never_starts_application(self):
        with (
            patch.dict(
                os.environ,
                {"TARGET_APP": sys.executable, "DISPLAY_MODE": "vnc"},
                clear=True,
            ),
            patch.object(sys, "argv", ["app"]),
            patch.object(
                APP.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=0),
                    SimpleNamespace(returncode=1),
                ],
            ),
            patch.object(APP.subprocess, "Popen") as spawn,
            patch.object(APP.time, "monotonic", side_effect=[0, 76]),
            self.assertRaisesRegex(RuntimeError, "Kiosk output did not become ready"),
        ):
            APP.main()
        spawn.assert_not_called()

    def test_cli_rejects_invalid_target_with_a_clear_error(self):
        source = ROOT / "vendor/kde-container/libexec/plasma-target-app"
        with (
            patch.dict(os.environ, {"TARGET_APP": ""}, clear=True),
            patch.object(sys, "argv", [str(source)]),
            self.assertRaisesRegex(SystemExit, "must name an executable"),
        ):
            runpy.run_path(str(source), run_name="__main__")


if __name__ == "__main__":
    unittest.main()
