"""Clipboard runner tests use synthetic streams and private endpoint identities."""

import base64
import contextlib
import io
import json
import queue
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import clipboard

ROOT = Path(__file__).resolve().parents[1]


class ClipboardProbeTests(unittest.TestCase):
    def test_probe_serializes_content_reads_errors_and_closes_worker(self):
        message = {
            "mode": 0,
            "data": {"text/plain": base64.b64encode(b"private test").decode()},
        }
        process = SimpleNamespace(
            stdin=io.StringIO(),
            stdout=io.StringIO(json.dumps(message) + "\ninvalid json\n"),
            poll=Mock(return_value=None),
            wait=Mock(side_effect=[subprocess.TimeoutExpired("probe", 5), 0]),
            kill=Mock(),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(clipboard.subprocess, "Popen", return_value=process) as start,
        ):
            probe = clipboard.Probe(
                "docker",
                "private-child",
                {"DISPLAY_BACKEND": "wayland", "WAYLAND_DISPLAY": "wayland-test"},
                Path(directory) / "probe.log",
            )
            probe.reader.join(1)
            probe.set({"text/plain": b"private test"})
            self.assertEqual(json.loads(process.stdin.getvalue()), message)
            self.assertEqual(
                probe.wait({"text/plain": b"private test"}),
                {"text/plain": b"private test"},
            )
            with self.assertRaises(ValueError):
                probe.wait({"text/plain": b"private test"})
            probe.close()
            process.kill.assert_called_once()
            self.assertTrue(probe.log.closed)
            arguments = start.call_args.args[0]
            self.assertEqual(
                arguments[:5],
                ["docker", "exec", "-i", "--user=plasma", "private-child"],
            )
            self.assertEqual(arguments[-2:], ["--endpoint", "wayland"])

    def test_wait_ignores_other_modes_and_payloads_then_accepts_current_selection(self):
        probe = object.__new__(clipboard.Probe)
        probe.process = Mock()
        probe.process.poll.return_value = None
        probe.messages = Mock()

        def encoded(value):
            return base64.b64encode(value).decode()

        probe.messages.get.side_effect = [
            queue.Empty,
            {"mode": 1, "data": {"text/plain": encoded(b"expected")}},
            {"mode": 0, "data": {"text/plain": encoded(b"old")}},
            {"mode": 0, "data": {"text/plain": encoded(b"expected")}},
        ]
        with patch.object(clipboard.time, "monotonic", return_value=0):
            self.assertEqual(
                probe.wait({"text/plain": b"expected"}), {"text/plain": b"expected"}
            )
        self.assertEqual(probe.messages.get.call_count, 4)

    def test_wait_detects_exited_probe_and_bounded_timeout(self):
        probe = object.__new__(clipboard.Probe)
        probe.process = Mock()
        probe.process.poll.return_value = 23
        probe.messages = Mock()
        probe.messages.get.side_effect = queue.Empty
        with (
            patch.object(clipboard.time, "monotonic", return_value=0),
            self.assertRaisesRegex(RuntimeError, "probe exited"),
        ):
            probe.wait({"text/plain": b"test"})
        with (
            patch.object(clipboard.time, "monotonic", side_effect=[0, 21]),
            self.assertRaisesRegex(RuntimeError, "within 20 seconds"),
        ):
            probe.wait({"text/plain": b"test"})

    def test_broken_input_pipe_does_not_prevent_worker_cleanup(self):
        probe = object.__new__(clipboard.Probe)
        probe.process = Mock()
        probe.process.stdin.close.side_effect = BrokenPipeError
        probe.reader = Mock()
        probe.log = Mock()
        probe.close()
        probe.process.wait.assert_called_once_with(timeout=5)
        probe.reader.join.assert_called_once_with(timeout=2)
        probe.log.close.assert_called_once()
        probe.process.kill.assert_not_called()

    def test_socket_identification_rejects_remote_x11_and_uses_requested_end(self):
        configuration = {
            "XDG_RUNTIME_DIR": "/private/runtime",
            "WAYLAND_DISPLAY": "wayland-test",
            "HOST_WAYLAND_DISPLAY": "/private/parent-wayland",
            "DISPLAY": ":123.0",
            "HOST_DISPLAY": ":124",
        }
        with patch.object(clipboard, "run", return_value=b"5:10\n") as run:
            for backend, host, path in (
                ("wayland", False, "/private/runtime/wayland-test"),
                ("wayland", True, "/private/parent-wayland"),
                ("x11", False, "/tmp/.X11-unix/X123"),
                ("x11", True, "/tmp/.X11-unix/X124"),
            ):
                self.assertEqual(
                    clipboard.socket_identity(
                        "docker", "private-child", backend, configuration, host=host
                    ),
                    b"5:10",
                )
                self.assertEqual(run.call_args.args[-1], path)
            for display in ("remote:0", ":bad"):
                with (
                    self.subTest(display=display),
                    self.assertRaisesRegex(ValueError, "local X11"),
                ):
                    clipboard.socket_identity(
                        "docker",
                        "private-child",
                        "x11",
                        {**configuration, "DISPLAY": display},
                    )


class Stream:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line


class ClipboardWorker:
    def __init__(self, workers):
        self.stdout = Stream()
        self.stdin = self
        self.workers = workers
        self.closed = False
        workers.append(self)

    def write(self, line):
        for worker in self.workers:
            if not worker.closed:
                worker.stdout.lines.put(line)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self.stdout.lines.put(None)

    def poll(self):
        return 0 if self.closed else None

    def wait(self, timeout):
        return 0


class ClipboardEntrypointTests(unittest.TestCase):
    def run_case(self, output, *, host="wayland", fallback=False):
        parent = {
            "DISPLAY_MODE": "vnc",
            "DISPLAY_BACKEND": "wayland",
            "XDG_RUNTIME_DIR": "/private/runtime",
            "WAYLAND_DISPLAY": "wayland-test",
            "DISPLAY": ":123",
            "XAUTHORITY": "/private/auth",
        }
        child = {
            "DISPLAY_MODE": "nested",
            "DISPLAY_BACKEND": "wayland",
            "HOST_DISPLAY_BACKEND": host,
            "HOST_WAYLAND_DISPLAY": "/private/parent-wayland",
            "HOST_DISPLAY": ":123",
            "XDG_RUNTIME_DIR": "/private/inner",
            "WAYLAND_DISPLAY": "wayland-test",
        }
        workers = []
        calls = []

        def execute(arguments, **_options):
            calls.append(arguments)
            if arguments[3:5] == ["python3", "-c"]:
                if "shlex" in arguments[-1]:
                    payload = parent if arguments[2] == "private-parent" else child
                else:
                    payload = {"DISPLAY": ":123", "XAUTHORITY": "/private/auth"}
                return SimpleNamespace(stdout=json.dumps(payload).encode(), stderr=b"")
            if "stat" in arguments:
                return SimpleNamespace(stdout=b"5:10\n", stderr=b"")
            return SimpleNamespace(stdout=b"private service log\n", stderr=b"")

        arguments = [
            "clipboard",
            "--parent",
            "private-parent",
            "--container",
            "private-child",
            "--case",
            "test",
            "--output",
            str(output),
        ]
        if fallback:
            arguments.append("--parent-x11-clipboard")
        with (
            patch.object(sys, "argv", arguments),
            patch("subprocess.run", side_effect=execute),
            patch(
                "subprocess.Popen",
                side_effect=lambda *_args, **_options: ClipboardWorker(workers),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(ROOT / "tests/clipboard.py"), run_name="__main__")
        self.assertEqual(len(workers), 4)
        self.assertTrue(all(worker.closed for worker in workers))
        result = json.loads((output / "test/results.json").read_text())
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["checks"],
            {
                "host-to-container": ["unicode-text", "png-with-alpha"],
                "container-to-host": ["unicode-text", "png-with-alpha"],
            },
        )
        for direction in ("host-to-container", "container-to-host"):
            self.assertIn("漢字", (output / "test" / f"{direction}.txt").read_text())
            self.assertTrue(
                (output / "test" / f"{direction}.png")
                .read_bytes()
                .startswith(b"\x89PNG")
            )
        return result, calls

    def test_wayland_xwayland_and_weston_fallback_endpoints(self):
        for host, fallback, expected in (
            ("wayland", False, "wayland"),
            ("x11", False, "x11"),
            ("wayland", True, "x11"),
        ):
            with (
                self.subTest(host=host, fallback=fallback),
                tempfile.TemporaryDirectory() as directory,
            ):
                result, calls = self.run_case(
                    Path(directory), host=host, fallback=fallback
                )
                self.assertEqual(result["parent_endpoint"], expected)
                self.assertTrue(
                    all(
                        call[2] in ("private-parent", "private-child") for call in calls
                    )
                )


if __name__ == "__main__":
    unittest.main()
