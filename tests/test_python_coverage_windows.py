"""Capture CLI and Win32 shutdown contracts without a live desktop or account."""

import contextlib
import ctypes
import importlib.machinery
import importlib.util
import io
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def capture_module():
    loader = importlib.machinery.SourceFileLoader(
        "coverage_capture_vnc", str(ROOT / "tests/capture-vnc.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CaptureCliTests(unittest.TestCase):
    def test_capture_waits_for_visible_frame_and_closes_authenticated_transport(self):
        capture = capture_module()
        image = Image.new("RGB", (20, 20))
        for x in range(20):
            for y in range(20):
                image.putpixel((x, y), (x * 10, y * 10, (x + y) * 5))
        client = Mock(name="private-test-client", width=20, height=20)
        client.name = "private-test-framebuffer"
        client.screenshot.side_effect = [Image.new("RGB", (20, 20)), image]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "frame.png"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "capture",
                        "--port",
                        "45678",
                        "--password",
                        "testpass",
                        "--web",
                        "--output",
                        str(destination),
                    ],
                ),
                patch.object(capture, "RFBClient", return_value=client) as create,
                patch.object(capture.time, "monotonic", side_effect=[0, 1]),
                patch.object(capture.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                capture.main()
            create.assert_called_once_with(45678, "testpass", True)
            self.assertEqual(
                Image.open(destination).getpixel((10, 10)), image.getpixel((10, 10))
            )
            self.assertIn("captured 20x20", output.getvalue())
        client.close.assert_called_once()

    def test_blank_capture_fails_and_still_preserves_frame_and_closes(self):
        capture = capture_module()
        client = Mock()
        client.screenshot.return_value = Image.new("RGB", (20, 20))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "blank.png"
            with (
                patch.object(
                    sys,
                    "argv",
                    ["capture", "--port", "45678", "--output", str(destination)],
                ),
                patch.object(capture, "RFBClient", return_value=client),
                patch.object(capture.time, "monotonic", side_effect=[0, 31]),
                self.assertRaisesRegex(AssertionError, "Framebuffer remained blank"),
            ):
                capture.main()
            self.assertTrue(destination.is_file())
        client.close.assert_called_once()

    def test_capture_entrypoint_uses_same_transport(self):
        image = Image.new("RGB", (20, 20))
        for x in range(20):
            for y in range(20):
                image.putpixel((x, y), (x * 10, y * 10, 100))
        client = Mock()
        client.screenshot.return_value = image
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                sys,
                "argv",
                [
                    "capture",
                    "--port",
                    "45678",
                    "--output",
                    str(Path(directory) / "frame.png"),
                ],
            ),
            patch("rfb.RFBClient", return_value=client),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(ROOT / "tests/capture-vnc.py"), run_name="__main__")
        client.close.assert_called_once()


class CloseTerminalTests(unittest.TestCase):
    def run_close(self, status=0):
        user = SimpleNamespace(
            GetWindowThreadProcessId=Mock(), PostMessageW=Mock(), EnumWindows=Mock()
        )
        kernel = SimpleNamespace(
            OpenProcess=Mock(),
            QueryFullProcessImageNameW=Mock(),
            WaitForSingleObject=Mock(return_value=status),
            CloseHandle=Mock(),
        )
        user.GetWindowThreadProcessId.side_effect = lambda window, pointer: setattr(
            pointer._obj, "value", window + 100
        )
        kernel.OpenProcess.side_effect = lambda _rights, _inherit, pid: (
            0 if pid == 103 else pid + 1000
        )

        def image_name(handle, _flags, name, _size):
            if handle == 1104:
                return False
            name.value = (
                r"C:\MT5\terminal64.exe" if handle == 1101 else r"C:\Other\helper.exe"
            )
            return True

        kernel.QueryFullProcessImageNameW.side_effect = image_name

        def enumerate_windows(callback, _unused):
            for window in range(1, 5):
                self.assertTrue(callback(window, 0))

        user.EnumWindows.side_effect = enumerate_windows
        with (
            patch.object(
                ctypes,
                "windll",
                SimpleNamespace(user32=user, kernel32=kernel),
                create=True,
            ),
            patch.object(
                ctypes,
                "WINFUNCTYPE",
                side_effect=lambda *_args: lambda function: function,
                create=True,
            ),
        ):
            runpy.run_path(str(ROOT / "tests/close-terminal.py"))
        return user, kernel

    def test_only_mt5_windows_receive_close_and_all_opened_handles_are_released(self):
        user, kernel = self.run_close()
        user.PostMessageW.assert_called_once_with(1, 0x0010, 0, 0)
        kernel.WaitForSingleObject.assert_called_once_with(1101, 30000)
        self.assertEqual(
            [call.args[0] for call in kernel.CloseHandle.call_args_list],
            [1102, 1104, 1101],
        )
        self.assertEqual(kernel.OpenProcess.call_count, 4)

    def test_timed_out_mt5_shutdown_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "did not close gracefully"):
            self.run_close(status=258)


if __name__ == "__main__":
    unittest.main()
