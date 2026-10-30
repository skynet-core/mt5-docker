"""Wine must receive the settled session DPI before its first MT5 process."""

import configparser
import importlib.machinery
import importlib.util
import os
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

SOURCE = Path(__file__).resolve().parents[1] / "libexec/mt5-dpi"
LOADER = importlib.machinery.SourceFileLoader("mt5_dpi", str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
DPI = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(DPI)


class DisplayDpiTests(unittest.TestCase):
    def snapshot(self, support, config=None, *, default_directory=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_directory = root / ".config" if default_directory else root
            config_directory.mkdir(exist_ok=True)
            if config is not None:
                (config_directory / "kwinrc").write_text(config)
            environment = {"XDG_CONFIG_HOME": str(root)}
            if default_directory:
                environment = {}
            with (
                patch.dict(DPI.os.environ, environment, clear=True),
                patch.object(DPI.Path, "home", return_value=root),
                patch.object(
                    DPI.subprocess,
                    "run",
                    return_value=SimpleNamespace(stdout=support),
                ) as execute,
            ):
                result = DPI.scale_snapshot()
            self.assertEqual(execute.call_args_list[0].args[0], ["xdpyinfo"])
            self.assertEqual(execute.call_args.args[0][0], "qdbus-qt6")
            self.assertEqual(
                execute.call_args.kwargs["env"]["QT_QPA_PLATFORM"], "offscreen"
            )
            return result

    def test_native_and_fractional_scales_come_from_running_session(self):
        for scale in (1, 1.25, 1.5, 2):
            with self.subTest(scale=scale):
                self.assertEqual(
                    self.snapshot(
                        f"Screen 0:\nScale: {scale}\n", f"[Xwayland]\nScale={scale}\n"
                    ),
                    ((scale,), scale),
                )

    def test_compositor_upscaling_keeps_wine_at_96_despite_scaled_output(self):
        self.assertEqual(
            self.snapshot("Scale: 2\n", "[Other]\nSetting=100%\n"), ((2,), 1)
        )
        self.assertEqual(self.snapshot("Scale: 1\n"), ((1,), 1))

    def test_default_config_directory_and_multiple_outputs(self):
        self.assertEqual(
            self.snapshot(
                "Scale: 1\nScale: 2\n", "[Xwayland]\nScale=2\n", default_directory=True
            ),
            ((1, 2), 2),
        )

    def test_dword_boundary_values_are_accepted(self):
        for value in (1, 0xFFFFFFFF):
            scale = value / 96
            with self.subTest(dpi=value):
                snapshot = self.snapshot("Scale: 1\n", f"[Xwayland]\nScale={scale}\n")
                self.assertEqual(round(snapshot[1] * 96), value)

    def test_missing_invalid_or_out_of_range_scale_is_rejected(self):
        for support, config in (
            ("No outputs", None),
            ("Scale: nope\n", None),
            ("Scale: 0\n", None),
            ("Scale: nan\n", None),
            ("Scale: inf\n", None),
            ("Scale: 2\n", "[Xwayland]\nScale=0\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=-1\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=nan\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=inf\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=nope\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=0.001\n"),
            ("Scale: 2\n", "[Xwayland]\nScale=1e308\n"),
        ):
            with (
                self.subTest(support=support, config=config),
                self.assertRaises(ValueError),
            ):
                self.snapshot(support, config)
        with self.assertRaises(configparser.Error):
            self.snapshot("Scale: 2\n", "invalid configuration")

    def wait(self, samples, *, advance=0.1):
        now = [0.0]
        last = samples[-1]
        samples = iter(samples)

        def snapshot():
            value = next(samples, last)
            if isinstance(value, Exception):
                raise value
            return value

        def sleep(_delay):
            now[0] += advance

        with (
            patch.dict(DPI.os.environ, {"DISPLAY_BACKEND": "wayland"}),
            patch.object(DPI, "scale_snapshot", side_effect=snapshot) as observe,
            patch.object(DPI.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(DPI.time, "sleep", side_effect=sleep),
        ):
            result = DPI.display_dpi()
        return result, observe.call_count

    def test_transient_initial_scale_and_later_changes_restart_settling(self):
        result, calls = self.wait([((1,), 1)] * 4 + [((2,), 2)])
        self.assertEqual(result, 192)
        self.assertGreaterEqual(calls, 10)
        result, calls = self.wait([((1,), 1)] * 4 + [((2,), 1)])
        self.assertEqual(result, 96)
        self.assertGreaterEqual(calls, 10)
        for scale, expected in ((1, 96), (1.25, 120), (1.5, 144), (2, 192)):
            with self.subTest(scale=scale):
                self.assertEqual(self.wait([((scale,), scale)])[0], expected)

    def test_transient_display_command_and_config_failures_are_retried(self):
        for error in (
            OSError("not ready"),
            ValueError("invalid scale"),
            configparser.Error("incomplete write"),
            subprocess.CalledProcessError(1, "qdbus-qt6"),
            subprocess.TimeoutExpired("xdpyinfo", 2),
        ):
            with self.subTest(error=error):
                result, calls = self.wait([((2,), 2)] * 4 + [error, ((2,), 2)])
                self.assertEqual(result, 192)
                self.assertGreaterEqual(calls, 11)

    def test_unready_display_times_out_without_silently_selecting_96(self):
        with self.assertRaisesRegex(RuntimeError, "display scale"):
            self.wait([OSError("not ready")], advance=31)

    def test_real_x11_uses_96_and_does_not_read_stale_wayland_settings(self):
        with (
            patch.dict(DPI.os.environ, {"DISPLAY_BACKEND": "x11"}),
            patch.object(DPI, "scale_snapshot") as observe,
        ):
            self.assertEqual(DPI.display_dpi(), 96)
        observe.assert_not_called()

    def test_registry_is_written_synchronously_in_the_inherited_prefix(self):
        for existing_session in (False, True):
            with (
                self.subTest(existing_session=existing_session),
                patch.object(DPI.Path, "exists", return_value=existing_session),
                patch.object(DPI, "display_dpi", return_value=120),
                patch.object(DPI.subprocess, "run") as execute,
                patch("builtins.print") as output,
            ):
                DPI.main()
            expected = [
                call(
                    [
                        "wine",
                        "reg",
                        "add",
                        r"HKCU\Control Panel\Desktop",
                        "/v",
                        "LogPixels",
                        "/t",
                        "REG_DWORD",
                        "/d",
                        "120",
                        "/f",
                    ],
                    check=True,
                    timeout=30,
                )
            ]
            if not existing_session:
                expected.append(call(["wineserver", "-w"], check=True, timeout=30))
            self.assertEqual(execute.call_args_list, expected)
            output.assert_called_once_with("MT5 Wine startup DPI: 120", flush=True)

    def test_bootstrap_must_finish_without_killing_wine_clients(self):
        with (
            patch.object(DPI.Path, "exists", return_value=False),
            patch.object(DPI, "display_dpi", return_value=192),
            patch.object(
                DPI.subprocess,
                "run",
                side_effect=[None, subprocess.TimeoutExpired("wineserver", 30)],
            ) as execute,
            patch("builtins.print") as output,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            DPI.main()
        self.assertEqual(execute.call_args.args[0], ["wineserver", "-w"])
        output.assert_not_called()

    def test_failed_registry_update_is_fatal(self):
        for error in (
            subprocess.CalledProcessError(1, "wine"),
            subprocess.TimeoutExpired("wine", 30),
        ):
            with (
                self.subTest(error=error),
                patch.object(DPI, "display_dpi", return_value=192),
                patch.object(DPI.subprocess, "run", side_effect=error),
                patch("builtins.print") as output,
                self.assertRaises(type(error)),
            ):
                DPI.main()
            output.assert_not_called()

    def test_installed_entrypoint_runs_the_same_initialization(self):
        with (
            patch.dict(os.environ, {"DISPLAY_BACKEND": "x11"}),
            patch("subprocess.run", Mock()) as execute,
            patch("builtins.print"),
        ):
            runpy.run_path(str(SOURCE), run_name="__main__")
        self.assertEqual(execute.call_args_list[0].args[0][-2:], ["96", "/f"])
