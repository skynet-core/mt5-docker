"""Chart startup must expose native click errors before any dialog repairs them."""

import contextlib
import io
import itertools
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import mt5_input
from PIL import Image, ImageDraw


class ChartInputTests(unittest.TestCase):
    def image(self):
        image = Image.new("RGB", (800, 600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((400, 100, 515, 139), fill=mt5_input.CHART_TARGETS[0][1])
        draw.rectangle((550, 300, 665, 339), fill=mt5_input.CHART_TARGETS[1][1])
        return image

    def test_targets_are_found_in_pixels_without_window_geometry(self):
        image = self.image()
        self.assertEqual(
            mt5_input.chart_target(image, (203, 41, 97)), (400, 100, 516, 140)
        )
        for rectangle in (None, (4, 5, 5, 6), (0, 0, 200, 100)):
            with self.subTest(rectangle=rectangle):
                blank = Image.new("RGB", (800, 600), "white")
                if rectangle:
                    ImageDraw.Draw(blank).rectangle(rectangle, fill=(203, 41, 97))
                with self.assertRaisesRegex(RuntimeError, "absent"):
                    mt5_input.chart_target(blank, (203, 41, 97))

    def test_target_extent_limit_uses_declared_parent_scale_and_rounds_up(self):
        for scale, width, height in (
            (1, 120, 44),
            (1.25, 150, 55),
            (2, 240, 88),
            (1.01, 122, 45),
            (0.5, 120, 44),
        ):
            with self.subTest(scale=scale):
                self.assertEqual(
                    mt5_input.chart_target(self.image(), (203, 41, 97), scale),
                    (400, 100, 516, 140),
                )
                for extra_width, extra_height in ((0, 0), (1, 0), (0, 1)):
                    image = Image.new("RGB", (800, 600), "white")
                    bounds = (
                        10,
                        10,
                        10 + width + extra_width,
                        10 + height + extra_height,
                    )
                    ImageDraw.Draw(image).rectangle(
                        (10, 10, bounds[2] - 1, bounds[3] - 1), fill=(203, 41, 97)
                    )
                    if extra_width or extra_height:
                        with self.assertRaisesRegex(RuntimeError, "absent"):
                            mt5_input.chart_target(image, (203, 41, 97), scale)
                    else:
                        self.assertEqual(
                            mt5_input.chart_target(image, (203, 41, 97), scale), bounds
                        )

    def test_compiler_uses_disposable_offline_unprivileged_prefix_and_verifies_outputs(
        self,
    ):
        for failure in (None, "exit", "empty", "errors", "missing"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "compile"

                def compile_command(
                    *arguments, failure=failure, output=output, **options
                ):
                    self.assertEqual(arguments[:3], ("docker", "run", "--rm"))
                    self.assertIn("--network=none", arguments)
                    self.assertIn("--user=plasma", arguments)
                    self.assertNotIn("--privileged", arguments)
                    self.assertTrue(
                        any(
                            str(value).endswith("target=/ChartInput.mq5,readonly")
                            for value in arguments
                        )
                    )
                    self.assertEqual(output.stat().st_mode & 0o777, 0o777)
                    self.assertFalse(options["check"])
                    if failure != "missing":
                        (output / "ChartInput.ex5").write_bytes(
                            b"" if failure == "empty" else b"fresh compiled fixture"
                        )
                        (output / "ChartInput.log").write_text(
                            "Result: 2 errors, 0 warnings,"
                            if failure == "errors"
                            else "Result: 0 errors, 0 warnings,",
                            encoding="utf-16",
                        )
                    return subprocess.CompletedProcess(
                        arguments,
                        2 if failure == "exit" else 0,
                        b"compiler stdout",
                        b"compiler stderr",
                    )

                with patch.object(mt5_input, "execute", side_effect=compile_command):
                    if failure:
                        with self.assertRaises(
                            (RuntimeError, OSError, subprocess.CalledProcessError)
                        ):
                            mt5_input.compile_chart("docker", "private-image", output)
                    else:
                        binary, config = mt5_input.compile_chart(
                            "docker", "private-image", output
                        )
                        self.assertTrue(binary.read_bytes())
                        self.assertIn("Expert=ChartInput.ex5", config.read_text())
                        self.assertIn("AllowLiveTrading=0", config.read_text())
                        hashes = json.loads((output / "sha256.json").read_text())
                        self.assertEqual(len(hashes["source"]), 64)
                        self.assertEqual(len(hashes["binary"]), 64)
                self.assertEqual(
                    (output / "compiler.log").read_bytes(),
                    b"compiler stdoutcompiler stderr",
                )
        subprocess.run(
            ["bash", "-n"], input=mt5_input.CHART_COMPILE, text=True, check=True
        )
        subprocess.run(
            ["shellcheck", "--shell=bash", "-"],
            input=mt5_input.CHART_COMPILE,
            text=True,
            check=True,
        )

    def test_event_reader_handles_initial_missing_file_and_preserves_raw_records(self):
        for status, stdout, stderr in (
            (1, b"", b"No such file or directory"),
            (0, b"event;name;x;y\nREADY;;0;0\n", b""),
            (0, b"", b""),
            (0, b"\xef\xbb\xbf", b""),
            (0, b"unexpected;columns\nREADY;0\n", b""),
            (1, b"", b"Permission denied"),
        ):
            with (
                self.subTest(status=status, stderr=stderr),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory)
                with patch.object(
                    mt5_input,
                    "execute",
                    return_value=subprocess.CompletedProcess(
                        [], status, stdout, stderr
                    ),
                ):
                    if stderr == b"Permission denied":
                        with self.assertRaises(subprocess.CalledProcessError):
                            mt5_input.chart_events("docker", "private", output)
                    elif stdout.startswith(b"unexpected"):
                        with self.assertRaisesRegex(RuntimeError, "CSV header"):
                            mt5_input.chart_events("docker", "private", output)
                    else:
                        rows = mt5_input.chart_events("docker", "private", output)
                        self.assertEqual(len(rows), int(stdout.startswith(b"event;")))
                self.assertEqual((output / "chart-input.csv").read_bytes(), stdout)

    def test_chart_input_checks_actual_events_before_any_menu_or_dialog_input(self):
        for failure in (
            None,
            "offset",
            "missing-event",
            "wrong-target",
            "dialog",
            "disabled",
            "not-ready",
            "malformed-event",
            "missing-target",
            "tooltip",
            "tooltip-and-dialog",
            "disabled-tooltip",
            "unknown-dialog",
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory)
                client = Mock()
                client.screenshot.return_value = self.image()
                if failure == "missing-target":
                    client.screenshot.return_value = Image.new("RGB", (800, 600))
                probe = Mock()
                tooltip = {
                    "hwnd": 20,
                    "class": "tooltips_class32",
                    "rect": [1, 2, 30, 40],
                }
                dialog = {"hwnd": 21, "class": "#32770", "rect": [1, 2, 300, 400]}
                unknown = {"hwnd": 22, "class": "OtherWindow", "rect": [1, 2, 300, 400]}
                probe.state.return_value = {
                    "main": 10,
                    "main_enabled": failure not in ("disabled", "disabled-tooltip"),
                    "dialogs": {
                        "dialog": [dialog],
                        "tooltip": [tooltip],
                        "tooltip-and-dialog": [tooltip, dialog],
                        "disabled-tooltip": [tooltip],
                        "unknown-dialog": [unknown],
                    }.get(failure, []),
                }
                ready_calls = [0]

                def events(
                    *_args, failure=failure, ready_calls=ready_calls, client=client
                ):
                    ready_calls[0] += 1
                    if failure == "not-ready" or ready_calls[0] == 1:
                        return []
                    rows = [{"event": "READY"}]
                    for index in range(client.click.call_count):
                        name, _, point = mt5_input.CHART_TARGETS[index]
                        if failure == "missing-event":
                            continue
                        rows.append(
                            {
                                "event": "OBJECT_CLICK",
                                "name": "other" if failure == "wrong-target" else name,
                                "x": "broken"
                                if failure == "malformed-event"
                                else str(point[0] + (5 if failure == "offset" else 0)),
                                "y": str(point[1]),
                            }
                        )
                    return rows

                with (
                    patch.object(mt5_input, "chart_events", side_effect=events),
                    patch.object(
                        mt5_input.time, "monotonic", side_effect=itertools.count()
                    ),
                    patch.object(mt5_input.time, "sleep"),
                ):
                    if failure not in (None, "tooltip"):
                        with self.assertRaises((RuntimeError, ValueError)):
                            mt5_input.autologin_input(
                                "docker", "private", client, probe, output
                            )
                    else:
                        result = mt5_input.autologin_input(
                            "docker", "private", client, probe, output
                        )
                        self.assertTrue(result["passed"])
                        self.assertEqual(len(result["targets"]), 2)
                        self.assertEqual(
                            client.click.call_args_list,
                            [call(458, 120), call(608, 320)],
                        )
                        self.assertTrue((output / "startup.png").is_file())
                        self.assertEqual(
                            len(json.loads((output / "chart-input.json").read_text())),
                            2,
                        )
                client.chord.assert_not_called()
                client.key.assert_not_called()
                client.text.assert_not_called()
                if failure in (
                    "dialog",
                    "disabled",
                    "not-ready",
                    "missing-target",
                    "tooltip-and-dialog",
                    "disabled-tooltip",
                    "unknown-dialog",
                ):
                    client.click.assert_not_called()
                if failure in ("offset", "missing-event", "wrong-target"):
                    self.assertEqual(client.click.call_count, 1)
                    evidence = json.loads((output / "chart-input.json").read_text())
                    self.assertFalse(evidence[0]["passed"])

    def test_ready_event_waits_for_actual_painted_targets_before_first_click(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            client = Mock()
            client.screenshot.side_effect = [
                Image.new("RGB", (1600, 1200)),
                self.image().resize((1600, 1200), Image.Resampling.NEAREST),
                self.image().resize((1600, 1200), Image.Resampling.NEAREST),
                self.image().resize((1600, 1200), Image.Resampling.NEAREST),
            ]
            probe = Mock()
            probe.state.return_value = {"main": 10, "main_enabled": True, "dialogs": []}
            ready = [{"event": "READY"}]
            red = {
                "event": "OBJECT_CLICK",
                "name": "ChartInput-red",
                "x": "100",
                "y": "62",
            }
            blue = {
                "event": "OBJECT_CLICK",
                "name": "ChartInput-blue",
                "x": "270",
                "y": "62",
            }

            def click(*_point):
                self.assertEqual(
                    client.screenshot.call_count, 2 + client.click.call_count - 1
                )

            client.click.side_effect = click
            with (
                patch.object(
                    mt5_input,
                    "chart_events",
                    side_effect=[ready, ready, [*ready, red], [*ready, red, blue]],
                ),
                patch.object(mt5_input.time, "sleep"),
            ):
                self.assertTrue(
                    mt5_input.autologin_input(
                        "docker", "private", client, probe, output, target_scale=2
                    )["passed"]
                )
            self.assertEqual(
                client.click.call_args_list, [call(916, 240), call(1216, 640)]
            )
            attempts = json.loads((output / "startup-attempts.json").read_text())
            self.assertFalse(attempts[0]["targets_visible"])
            self.assertTrue(attempts[1]["targets_visible"])
            self.assertTrue((output / attempts[0]["capture"]).is_file())

    def test_autologin_main_compiles_once_before_children_and_uses_existing_matrix(
        self,
    ):
        for scaled in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                order, sessions = [], {}
                compiled = (
                    Path(directory) / "ChartInput.ex5",
                    Path(directory) / "autologin.ini",
                )
                parent, child, probe, driver = Mock(), Mock(), Mock(), Mock()
                driver.output_scale.side_effect = lambda scale: {"scale": scale}

                def start(
                    _runtime,
                    _image,
                    name,
                    backend,
                    mode,
                    _output,
                    extra,
                    order=order,
                    sessions=sessions,
                    **options,
                ):
                    order.append("parent" if options.get("sleep") else "child")
                    sessions[name] = {
                        "DISPLAY_BACKEND": backend,
                        "DISPLAY_MODE": mode,
                        "HOST_DISPLAY_BACKEND": "wayland"
                        if "HOST_WAYLAND_DISPLAY=/run/host/wayland" in extra
                        else "x11"
                        if "HOST_XAUTHORITY=/run/host/Xauthority" in extra
                        else "none",
                    }
                    if not options.get("sleep"):
                        self.assertIn("MT5_ACCOUNT=1", extra)
                        self.assertIn("MT5_PASSWORD=isolated-test-only", extra)
                        self.assertTrue(
                            any(
                                "target=/etc/mt5/autologin.ini,readonly" in item
                                for item in extra
                            )
                        )
                        self.assertTrue(
                            any(
                                "target=/run/mt5-test/ChartInput.ex5,readonly" in item
                                for item in extra
                            )
                        )
                        self.assertTrue(
                            any(
                                item.startswith("--network=mt5-input-private-")
                                for item in extra
                            )
                        )

                args = SimpleNamespace(
                    output=directory,
                    case="autologin",
                    runtime="docker",
                    image="private",
                    parent_image="private-parent" if scaled else None,
                    pointer_scales=[1, 1.25, 2] if scaled else None,
                    gpu=False,
                    backends=["wayland", "x11"],
                    modes=["nested"] if scaled else ["vnc"],
                    startup="autologin",
                )
                with (
                    patch.object(
                        mt5_input,
                        "compile_chart",
                        side_effect=lambda *_args, order=order, compiled=compiled: (
                            order.append("compile") or compiled
                        ),
                    ) as compiler,
                    patch.object(mt5_input, "start", side_effect=start),
                    patch.object(
                        mt5_input,
                        "execute",
                        return_value=SimpleNamespace(stdout=b"support", stderr=b""),
                    ),
                    patch.object(
                        mt5_input,
                        "connect",
                        side_effect=[parent] if scaled else [parent, child, child],
                    ),
                    patch.object(mt5_input, "KWinDriver", return_value=driver),
                    patch.object(mt5_input, "NestedClient") as mapper,
                    patch.object(mt5_input, "resized_input") as resized,
                    patch.object(
                        mt5_input,
                        "parent_x11",
                        return_value=(":3", Path(directory) / "cookie"),
                    ),
                    patch.object(
                        mt5_input,
                        "configuration",
                        side_effect=lambda _r, n, sessions=sessions: sessions[n],
                    ),
                    patch.object(mt5_input, "Probe", return_value=probe),
                    patch.object(
                        mt5_input, "autologin_input", return_value={"passed": True}
                    ) as autologin,
                    patch.object(mt5_input, "exercise") as dialog,
                    patch.object(mt5_input, "collect"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(mt5_input.main(args), 0)
                self.assertEqual(
                    order, ["compile", "parent", *(["child"] * (12 if scaled else 2))]
                )
                compiler.assert_called_once()
                self.assertEqual(autologin.call_count, 12 if scaled else 2)
                self.assertEqual(
                    [call.kwargs["target_scale"] for call in autologin.call_args_list],
                    [1] * 4 + [1.25] * 4 + [2] * 4 if scaled else [1, 1],
                )
                self.assertTrue(
                    all(
                        call.args[2] is (parent if scaled else child)
                        for call in autologin.call_args_list
                    )
                )
                driver.place_window.assert_not_called()
                mapper.assert_not_called()
                resized.assert_not_called()
                if scaled:
                    self.assertEqual(driver.output_scale.call_count, 12)
                dialog.assert_not_called()
                self.assertTrue(
                    json.loads(
                        (Path(directory) / "autologin/results.json").read_text()
                    )["passed"]
                )

    def test_cli_exposes_autologin_without_changing_default_dialog(self):
        with (
            patch.object(sys, "argv", ["mt5-input", "--help"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(Path(mt5_input.__file__)), run_name="__main__")
        self.assertIn("--startup {dialog,autologin}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
