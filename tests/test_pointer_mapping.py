"""Pointer regressions use independent geometry and private synthetic images."""

import contextlib
import io
import itertools
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import mt5_input
from PIL import Image, ImageDraw


class ResizedTransport:
    """Model the parent compositor and Win32 readback on opposite wire ends."""

    def __init__(self, failure=None, *, viewport_anchor=None):
        self.failure = failure
        self.viewport_anchor = viewport_anchor
        self.desktop = [0, 0, 1000, 750]
        self.position = [0, 0]
        self.menus = False
        self.menu_mode = False
        self.capture_owner = self.menu_owner = None
        self.escapes = 0
        self.commands = []
        self.captures = 0
        self.output = {
            "origin": {"x": -100, "y": 20},
            "scale": 1.25,
            "logical": {"width": 1600, "height": 1000},
        }
        self.cursor = {"x": -100, "y": 20}
        self.window = {
            "frame": {"x": 0, "y": -36, "width": 800, "height": 636},
            "client": {"x": 0, "y": 0, "width": 800, "height": 600},
        }

    def place_window(self, *geometry):
        if geometry != (
            (280, 80, 880, 676) if self.viewport_anchor else (280, 80, 1100, 820)
        ):
            raise AssertionError(geometry)
        x, y, width, height = geometry
        self.window = {
            "frame": {"x": x, "y": y, "width": width, "height": height},
            "client": {"x": x, "y": y + 36, "width": width, "height": height - 36},
        }
        return self.window

    def state(self, _expected=""):
        return {
            "cursor": dict(self.cursor),
            "desktop": self.desktop.copy(),
            "pointer": self.position.copy(),
            "file_rect": [
                self.desktop[0] + 10,
                self.desktop[1] + 10,
                self.desktop[0] + 50,
                self.desktop[1] + 30,
            ],
            "menus": [123] if self.menus else [],
            "menu_mode": None if self.failure == "menu-query" else self.menu_mode,
            "capture": self.capture_owner,
            "menu_owner": self.menu_owner,
        }

    def pointer(self, x, y, buttons=0):
        self.commands.append((x, y, buttons))
        self.cursor = {"x": x / 1.25 - 100, "y": y / 1.25 + 20}
        rectangle = self.window["client"]
        if self.viewport_anchor:
            rectangle = {
                "x": rectangle["x"]
                + (
                    (rectangle["width"] - 800) / 2
                    if self.viewport_anchor == "center"
                    else 0
                ),
                "y": rectangle["y"]
                + (
                    (rectangle["height"] - 600) / 2
                    if self.viewport_anchor == "center"
                    else 0
                ),
                "width": 800,
                "height": 600,
            }
        self.position = [
            self.desktop[0]
            + (self.cursor["x"] - rectangle["x"]) * 1000 / rectangle["width"],
            self.desktop[1]
            + (self.cursor["y"] - rectangle["y"]) * 750 / rectangle["height"],
        ]
        if buttons and self.failure != "click":
            self.menus = (
                self.desktop[0] + 10 <= self.position[0] <= self.desktop[0] + 50
                and self.desktop[1] + 10 <= self.position[1] <= self.desktop[1] + 30
            )
            self.menu_mode = self.menus
            self.capture_owner = self.menu_owner = 100 if self.menus else None
            self.escapes = 0

    def key(self, value, down):
        self.commands.append(("key", value, down))
        if value == 0xFF1B and down:
            self.escapes += 1
            if self.failure == "escape" or (
                self.failure == "second-escape" and self.escapes == 2
            ):
                return
            if self.menus:
                self.menus = False
            else:
                self.menu_mode = False
                self.capture_owner = self.menu_owner = None

    def screenshot(self):
        return Image.new("RGB", (2000, 1250), (40, 60, 80))

    def capture(self, _runtime, _parent, _session, destination, cursor):
        self.captures += 1
        image = self.screenshot()
        if self.captures % 3 == 0 and (
            self.failure == "scene-never"
            or (self.failure == "scene-second" and self.captures == 3)
        ):
            ImageDraw.Draw(image).point((1990, 1240), fill="red")
        if cursor and self.failure != "cursor":
            x = round((self.cursor["x"] + 100) * 1.25)
            y = round((self.cursor["y"] - 20) * 1.25)
            ImageDraw.Draw(image).polygon(
                [(x, y), (x, y + 20), (x + 15, y + 15)], fill="white"
            )
        image.save(destination)
        return {"method": "kwin-screenshot2", "include_cursor": cursor}


class NestedMappingTests(unittest.TestCase):
    def client(self, output, desktop=None, window=None, geometry=None):
        probe = Mock()
        probe.state.return_value = {"desktop": desktop or [-300, 40, 1200, 800]}
        driver = Mock()
        driver.output = geometry or {
            "origin": {"x": -100, "y": 20},
            "scale": 1,
        }
        client = mt5_input.NestedClient(
            Mock(),
            driver,
            probe,
            window or {"client": {"x": 140, "y": 106, "width": 600, "height": 400}},
            output,
        )
        return client

    def test_fixed_viewports_use_independent_center_and_top_left_scale_tables(self):
        for anchor, scale, viewport, framebuffer, logical in (
            ("center", 1, (240, 206, 800, 600), [540, 336], (440, 356)),
            ("center", 1.25, (320, 266, 640, 480), [725, 458], (480, 386)),
            ("center", 2, (440, 356, 400, 300), [1280, 822], (540, 431)),
            ("top-left", 1, (140, 106, 800, 600), [440, 236], (340, 256)),
            ("top-left", 1.25, (140, 106, 640, 480), [500, 258], (300, 226)),
            ("top-left", 2, (140, 106, 400, 300), [680, 322], (240, 181)),
        ):
            with (
                self.subTest(anchor=anchor, scale=scale),
                tempfile.TemporaryDirectory() as directory,
            ):
                client = self.client(
                    Path(directory),
                    desktop=[100, -50, 800, 600],
                    window={
                        "client": {"x": 140, "y": 106, "width": 1000, "height": 800}
                    },
                    geometry={"origin": {"x": -100, "y": 20}, "scale": scale},
                )
                client.viewport_anchor = anchor
                expected = dict(
                    zip(("x", "y", "width", "height"), viewport, strict=True)
                )
                self.assertEqual(client.viewport(), expected)
                self.assertEqual(
                    client.framebuffer_point(200, 150), (framebuffer, logical)
                )
                self.assertEqual(
                    client.window["client"],
                    {"x": 140, "y": 106, "width": 1000, "height": 800},
                )
                client.parent.pointer.assert_not_called()
                client.driver.state.assert_not_called()

    def test_fixed_screenshots_exclude_unused_pixels_and_clipped_clients_fail(self):
        for anchor, scale, bounds in (
            ("center", 1, (340, 186, 1140, 786)),
            ("center", 1.25, (525, 308, 1325, 908)),
            ("center", 2, (1080, 672, 1880, 1272)),
            ("top-left", 1, (240, 86, 1040, 686)),
            ("top-left", 1.25, (300, 108, 1100, 708)),
            ("top-left", 2, (480, 172, 1280, 772)),
        ):
            with (
                self.subTest(anchor=anchor, scale=scale),
                tempfile.TemporaryDirectory() as directory,
            ):
                client = self.client(
                    Path(directory),
                    desktop=[100, -50, 800, 600],
                    window={
                        "client": {"x": 140, "y": 106, "width": 1000, "height": 800}
                    },
                    geometry={"origin": {"x": -100, "y": 20}, "scale": scale},
                )
                client.viewport_anchor = anchor
                image = Image.new("RGB", (2048, 1500), "magenta")
                left, top, right, bottom = bounds
                draw = ImageDraw.Draw(image)
                draw.rectangle((left, top, right - 1, bottom - 1), fill="orange")
                draw.point((left, top), fill="red")
                draw.point((right - 1, bottom - 1), fill="yellow")
                client.parent.screenshot.return_value = image
                cropped = client.screenshot()
                self.assertEqual(cropped.size, (800, 600))
                self.assertEqual(cropped.tobytes(), image.crop(bounds).tobytes())
                self.assertEqual(cropped.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(cropped.getpixel((799, 599)), (255, 255, 0))
                for dimension, limit in (
                    ("width", 800 / scale),
                    ("height", 600 / scale),
                ):
                    original = client.window["client"][dimension]
                    client.window["client"][dimension] = limit - 1
                    with self.assertRaisesRegex(RuntimeError, "viewport is clipped"):
                        client.viewport()
                    client.window["client"][dimension] = original

    def test_explicit_scaled_moved_resized_and_nonzero_origin_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(Path(directory))
            for scale, physical in (
                (1, [300, 186]),
                (1.25, [375, 232]),
                (2, [600, 372]),
            ):
                with self.subTest(scale=scale):
                    client.driver.output["scale"] = scale
                    self.assertEqual(
                        client.framebuffer_point(120, 200), (physical, (200, 206))
                    )
            client.probe.state.return_value = {"desktop": [0, 0, 2560, 1848]}
            client.window = {
                "client": {"x": 140, "y": 106, "width": 1280, "height": 924}
            }
            client.driver.output = {"origin": {"x": 0, "y": 0}, "scale": 2}
            self.assertEqual(
                client.framebuffer_point(773, 221), ([1053, 433], (526.5, 216.5))
            )
            client.probe.state.return_value = {"desktop": [100, -50, 800, 600]}
            client.window = {
                "client": {"x": 380, "y": 140, "width": 1000, "height": 750}
            }
            client.driver.output = {"origin": {"x": -200, "y": 100}, "scale": 1.25}
            self.assertEqual(
                client.framebuffer_point(400, 300), ([1350, 519], (880, 515))
            )
            client.parent.pointer.assert_not_called()
            client.driver.state.assert_not_called()
            for desktop in ([0, 0, 0, 800], [0, 0, 1200, -1]):
                client.probe.state.return_value = {"desktop": desktop}
                with self.assertRaisesRegex(RuntimeError, "invalid physical desktop"):
                    client.desktop()

    def test_parent_readback_retries_and_rejects_constant_offsets_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(Path(directory))
            client.driver.output["scale"] = 2
            client.driver.state.side_effect = [
                {"cursor": {"x": 0, "y": 0}},
                {"cursor": {"x": 200.5, "y": 205.5}},
            ]
            with (
                patch.object(
                    mt5_input.time, "monotonic", side_effect=itertools.count()
                ),
                patch.object(mt5_input.time, "sleep") as sleep,
            ):
                client.pointer(120, 200)
            sleep.assert_called_once_with(0.1)
            client.parent.pointer.assert_called_once_with(600, 372)
            evidence = json.loads((Path(directory) / "parent-pointer.json").read_text())
            self.assertEqual(evidence[0]["parent_expected"], [200, 206])
            self.assertTrue(evidence[0]["passed"])
            client.parent.reset_mock()
            client.driver.state.side_effect = None
            client.driver.state.return_value = {"cursor": {"x": 222, "y": 269}}
            with (
                patch.object(mt5_input.time, "monotonic", side_effect=[0, 1, 6]),
                patch.object(mt5_input.time, "sleep"),
                self.assertRaisesRegex(RuntimeError, "incorrect scaled coordinates"),
            ):
                client.pointer(120, 200)
            client.parent.pointer.assert_called_once_with(600, 372)
            evidence = json.loads((Path(directory) / "parent-pointer.json").read_text())
            self.assertEqual(len(evidence), 2)
            self.assertFalse(evidence[-1]["passed"])
            self.assertEqual(evidence[-1]["parent_expected"], [200, 206])
            self.assertEqual(evidence[-1]["parent_observed"], {"x": 222, "y": 269})

    def test_click_enters_exactly_once_then_presses_at_same_point_and_delegates_keys(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(Path(directory))
            client.driver.state.return_value = {"cursor": {"x": 200, "y": 206}}
            with (
                patch.object(
                    mt5_input.time, "monotonic", side_effect=itertools.count()
                ),
                patch.object(mt5_input.time, "sleep") as sleep,
            ):
                client.click(120, 200)
            self.assertEqual(
                client.parent.pointer.call_args_list,
                [call(0, 0), call(300, 186), call(300, 186, 1), call(300, 186)],
            )
            self.assertEqual(sleep.call_args_list, [call(0.1), call(0.08)])
            client.key(0xFF1B, True)
            client.chord(0xFFE3, ord("s"))
            client.text("Private synthetic text Α")
            client.parent.key.assert_called_once_with(0xFF1B, True)
            client.parent.chord.assert_called_once_with(0xFFE3, ord("s"))
            client.parent.text.assert_called_once_with("Private synthetic text Α")

    def test_parent_screenshot_is_cropped_rebased_and_resized_to_win32_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(
                Path(directory),
                desktop=[100, -50, 10, 10],
                window={"client": {"x": -15, "y": 15, "width": 20, "height": 15}},
                geometry={"origin": {"x": -20, "y": 10}, "scale": 2},
            )
            parent = Image.new("RGB", (80, 60), "black")
            ImageDraw.Draw(parent).rectangle((10, 10, 49, 39), fill="orange")
            ImageDraw.Draw(parent).rectangle((30, 25, 49, 39), fill="red")
            client.parent.screenshot.return_value = parent
            image = client.screenshot()
            self.assertEqual(image.size, (10, 10))
            self.assertEqual(
                image.tobytes(),
                parent.crop((10, 10, 50, 40)).resize((10, 10)).tobytes(),
            )
            self.assertEqual(image.getpixel((0, 0)), (255, 165, 0))
            self.assertEqual(image.getpixel((9, 9)), (255, 0, 0))

    def test_control_centers_convert_absolute_win32_positions_to_framebuffer_pixels(
        self,
    ):
        cases = (
            ([400, 300], [-100, 50, 800, 600], [100, 150, 300, 250], (150, 75)),
            ([1000, 750], [-100, 40, 1000, 750], [-90, 50, -50, 70], (30, 20)),
            ([1200, 900], [200, -80, 800, 600], [400, 20, 600, 220], (450, 300)),
        )
        for size, desktop, rectangle, expected in cases:
            with self.subTest(desktop=desktop):
                client, probe = Mock(), Mock()
                client.screenshot.return_value = Image.new("RGB", size)
                probe.state.return_value = {"desktop": desktop}
                self.assertEqual(
                    mt5_input.control_point(client, probe, rectangle), expected
                )
                client.pointer.assert_not_called()

    def test_nested_grid_uses_win32_origins_and_cannot_calibrate_guest_offsets(self):
        for origin in ((0, 0), (-100, 40), (300, -200)):
            with (
                self.subTest(origin=origin),
                tempfile.TemporaryDirectory() as directory,
                patch.object(
                    mt5_input.time, "monotonic", side_effect=itertools.count()
                ),
                patch.object(mt5_input.time, "sleep"),
            ):
                output = Path(directory)
                transport = ResizedTransport()
                transport.desktop[:2] = origin
                transport.place_window(280, 80, 1100, 820)
                client = mt5_input.NestedClient(
                    transport, transport, transport, transport.window, output
                )
                self.assertEqual(
                    mt5_input.pointer_grid(client, transport, output)["points"], 5
                )
                points = json.loads((output / "pointer.json").read_text())
                self.assertEqual(
                    points[0]["expected"], [origin[0] + 100, origin[1] + 75]
                )
                self.assertTrue(all(point["passed"] for point in points))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_input.time, "monotonic", side_effect=itertools.count()),
            patch.object(mt5_input.time, "sleep"),
        ):
            output = Path(directory)
            transport = ResizedTransport()
            original = transport.pointer

            def offset(x, y):
                original(x, y)
                transport.position[0] += 22
                transport.position[1] += 63

            parent = SimpleNamespace(pointer=offset, screenshot=transport.screenshot)
            client = mt5_input.NestedClient(
                parent, transport, transport, transport.window, output
            )
            with self.assertRaisesRegex(RuntimeError, "MT5 pointer offset"):
                mt5_input.pointer_grid(client, transport, output)
            parent_events = json.loads((output / "parent-pointer.json").read_text())
            self.assertTrue(parent_events[-1]["passed"])
            evidence = json.loads((output / "pointer.json").read_text())
            self.assertFalse(evidence[0]["passed"])
            self.assertGreater(evidence[0]["delta"][0], 20)
            self.assertGreater(evidence[0]["delta"][1], 60)

    def test_advanced_main_runs_twelve_scaled_profiles_on_one_private_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                output=directory,
                case="scaled",
                runtime="docker",
                parent_image="decorated-parent",
                image="private-mt5",
                gpu=True,
                backends=["wayland", "x11"],
                modes=["nested"],
                pointer_scales=[1, 1.25, 2],
            )
            execute = Mock(return_value=SimpleNamespace(stdout=b"support", stderr=b""))
            for changes in ({"modes": ["nested", "vnc"]}, {"parent_image": None}):
                invalid = SimpleNamespace(**(vars(args) | changes))
                with (
                    patch.object(mt5_input, "execute", execute),
                    self.assertRaisesRegex(ValueError, "requires --modes nested"),
                ):
                    mt5_input.main(invalid)
            execute.assert_not_called()
            sessions = {}
            driver = Mock()

            def scale_output(scale):
                driver.output = {
                    "origin": {"x": -100, "y": 20},
                    "scale": scale,
                    "logical": {"width": 1920, "height": 1080},
                }
                return driver.output

            def start(
                _runtime, _image, name, backend, mode, _output, extra=(), **options
            ):
                host = (
                    "wayland"
                    if "HOST_WAYLAND_DISPLAY=/run/host/wayland" in extra
                    else "x11"
                    if "HOST_XAUTHORITY=/run/host/Xauthority" in extra
                    else "none"
                )
                sessions[name] = {
                    "DISPLAY_BACKEND": backend,
                    "DISPLAY_MODE": mode,
                    "HOST_DISPLAY_BACKEND": host,
                    "MT5_PASSWORD": "synthetic-secret-must-not-be-published",
                    "PLASMA_SOFTWARE_NESTED": str(
                        int(backend == "wayland" and host == "x11")
                    ),
                }
                if not options.get("sleep"):
                    self.assertTrue(
                        {"MT5_ACCOUNT=", "MT5_LOGIN=", "MT5_PASSWORD=", "MT5_SERVER="}
                        <= set(extra)
                    )
                    self.assertEqual(
                        options["application"], "/usr/local/bin/mt5-launch"
                    )
                    self.assertTrue(
                        any(
                            value.startswith("--network=mt5-input-private-")
                            for value in extra
                        )
                    )

            parent = Mock()
            probes = [Mock() for _ in range(12)]
            for probe in probes:
                probe.state.return_value = {"desktop": [-300, 40, 1200, 800]}
            driver.output_scale.side_effect = scale_output

            def natural_window():
                scale = driver.output["scale"]
                return {
                    "windows": [
                        {
                            "frame": {
                                "x": 7,
                                "y": 11,
                                "width": 1200 / scale,
                                "height": 800 / scale + 36,
                            }
                        }
                    ]
                }

            def placed_window(x, y, width, height):
                return {
                    "frame": {"x": x, "y": y, "width": width, "height": height},
                    "client": {
                        "x": x,
                        "y": y + 36,
                        "width": 600 if (width, height) == (1280, 996) else width,
                        "height": 400
                        if (width, height) == (1280, 996)
                        else height - 36,
                    },
                }

            driver.state.side_effect = natural_window
            driver.wait_window.side_effect = lambda: natural_window()["windows"][0]
            driver.place_window.side_effect = placed_window

            def exercise(client, probe, output):
                self.assertIsInstance(client, mt5_input.NestedClient)
                self.assertIs(client.parent, parent)
                self.assertIs(client.driver, driver)
                self.assertIs(client.probe, probe)
                self.assertEqual(client.output, output)
                self.assertEqual(
                    driver.require_decoration,
                    client.output.parts[-3:] == ("wayland", "nested", "on-wayland"),
                )
                self.assertEqual(
                    client.viewport_anchor,
                    {
                        ("wayland", "nested", "on-x11"): "center",
                        ("x11", "nested", "on-x11"): "top-left",
                    }.get(client.output.parts[-3:]),
                )
                expected = (
                    {1: [360, 286], 1.25: [420, 308], 2: [600, 372]}
                    if client.viewport_anchor
                    else {1: [300, 186], 1.25: [375, 232], 2: [600, 372]}
                )
                point, logical = client.framebuffer_point(120, 200)
                self.assertEqual(point, expected[driver.output["scale"]])
                if client.viewport_anchor:
                    expected_logical = {1: (260, 306), 1.25: (236, 266), 2: (200, 206)}
                    self.assertEqual(logical, expected_logical[driver.output["scale"]])
                else:
                    self.assertEqual(logical, (200, 206))
                return {"passed": True, "point": point}

            with (
                patch.object(mt5_input, "execute", execute),
                patch.object(mt5_input, "start", side_effect=start) as started,
                patch.object(mt5_input, "connect", return_value=parent) as connect,
                patch.object(
                    mt5_input,
                    "parent_x11",
                    return_value=(":123", Path(directory) / "private-cookie"),
                ),
                patch.object(
                    mt5_input,
                    "configuration",
                    side_effect=lambda _runtime, name: sessions[name],
                ),
                patch.object(
                    mt5_input, "KWinDriver", return_value=driver
                ) as constructor,
                patch.object(mt5_input, "Probe", side_effect=probes),
                patch.object(mt5_input, "exercise", side_effect=exercise) as exercised,
                patch.object(
                    mt5_input,
                    "resized_input",
                    return_value={"passed": True, "pointer": {"points": 5}},
                ) as resized,
                patch.object(mt5_input, "collect") as collect,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(mt5_input.main(args), 0)
            self.assertEqual(started.call_count, 13)
            self.assertEqual(started.call_args_list[0].args[1], "decorated-parent")
            self.assertEqual(
                started.call_args_list[0].kwargs["dimensions"], (3840, 2160)
            )
            self.assertTrue(started.call_args_list[0].kwargs["sleep"])
            self.assertTrue(all(item.kwargs["gpu"] for item in started.call_args_list))
            constructor.assert_called_once()
            driver.clear_rules.assert_called_once_with()
            self.assertEqual(
                driver.output_scale.call_args_list,
                [call(scale) for scale in (1, 1.25, 2) for _ in range(4)],
            )
            self.assertEqual(
                driver.place_window.call_args_list,
                [
                    invocation
                    for scale in (1, 1.25, 2)
                    for invocation in (
                        call(140, 70, 1280, 996),
                        call(140, 70, 1200 / scale, 800 / scale + 36),
                        call(140, 70, 1280, 996),
                        call(140, 70, 1200 / scale, 800 / scale + 36),
                    )
                ],
            )
            self.assertEqual(driver.wait_window.call_count, 6)
            driver.state.assert_not_called()
            connect.assert_called_once()
            parent.close.assert_called_once_with()
            self.assertTrue(all(probe.close.call_count == 1 for probe in probes))
            self.assertEqual(exercised.call_count, 12)
            self.assertEqual(resized.call_count, 12)
            self.assertEqual(collect.call_count, 13)
            report = json.loads((Path(directory) / "scaled/results.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["cases"]), 12)
            self.assertNotIn(
                "synthetic-secret-must-not-be-published", json.dumps(report)
            )
            self.assertEqual(
                {
                    (row["backend"], row["host_backend"], row["parent_output"]["scale"])
                    for row in report["cases"]
                },
                {
                    (backend, host, scale)
                    for backend in ("wayland", "x11")
                    for host in ("wayland", "x11")
                    for scale in (1, 1.25, 2)
                },
            )
            for row in report["cases"]:
                fixed = row["host_backend"] == "x11"
                scale = row["parent_output"]["scale"]
                self.assertEqual(
                    row["viewport"],
                    {
                        "x": 140,
                        "y": 106,
                        "width": 1200 / scale if fixed else 600,
                        "height": 800 / scale if fixed else 400,
                    },
                )
            for invocation in resized.call_args_list:
                self.assertEqual(invocation.args[-1].name, "resized")
                self.assertTrue((invocation.args[-1].parent / "results.json").is_file())
            for scale in (100, 125, 200):
                for backend in ("wayland", "x11"):
                    for host in ("wayland", "x11"):
                        path = (
                            Path(directory)
                            / f"scaled/scale-{scale}/{backend}/nested/on-{host}/results.json"
                        )
                        self.assertTrue(
                            json.loads(path.read_text())["resized"]["passed"]
                        )
            self.assertEqual(
                sum(item.args[1:3] == ("rm", "-fv") for item in execute.call_args_list),
                13,
            )
            self.assertEqual(
                sum(
                    item.args[1:3] == ("network", "rm")
                    for item in execute.call_args_list
                ),
                1,
            )
            self.assertTrue(
                any(
                    item.args[1:3] == ("network", "create")
                    and "--internal" in item.args
                    for item in execute.call_args_list
                )
            )


class CursorCaptureTests(unittest.TestCase):
    def test_native_capture_failure_preserves_api_diagnostics_cause_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cursor-visible.png"
            for phase, stdout, stderr in (
                (
                    "capture",
                    b"fixture stdout\n",
                    b"ScreenShot2: QPainter unsupported\xff\n",
                ),
                ("capture", None, b"ScreenShot2: capture unavailable\n"),
                ("copy", None, None),
            ):
                with self.subTest(phase=phase, stderr=stderr):
                    error = subprocess.CalledProcessError(
                        1, ["private-native-capture"], output=stdout, stderr=stderr
                    )
                    effects = (
                        [error]
                        if phase == "capture"
                        else [
                            SimpleNamespace(
                                stdout=b'{"method":"kwin-screenshot2","include_cursor":true}'
                            ),
                            error,
                        ]
                    )
                    execute = Mock(
                        side_effect=[*effects, SimpleNamespace(returncode=1)]
                    )
                    with (
                        patch.object(mt5_input, "execute", execute),
                        self.assertRaisesRegex(
                            RuntimeError, "Parent capture failed:"
                        ) as raised,
                    ):
                        mt5_input.capture_parent(
                            "docker", "private-parent", {}, destination, True
                        )
                    diagnostic = (stdout or b"") + (stderr or b"")
                    self.assertEqual(
                        destination.with_suffix(".log").read_bytes(), diagnostic
                    )
                    self.assertIn(
                        diagnostic.decode(errors="replace").strip(),
                        str(raised.exception),
                    )
                    self.assertIs(raised.exception.__cause__, error)
                    self.assertFalse(destination.exists())
                    self.assertEqual(execute.call_args.args[3:5], ("rm", "-f"))
                    self.assertEqual(execute.call_args.kwargs, {"check": False})

    def test_capture_selects_exact_api_copies_private_bytes_and_always_cleans(self):
        with tempfile.TemporaryDirectory() as directory:
            for cursor in (False, True):
                with self.subTest(cursor=cursor):
                    metadata = {"method": "kwin-screenshot2"}
                    if cursor:
                        metadata["include_cursor"] = True
                    execute = Mock(
                        side_effect=[
                            SimpleNamespace(
                                stdout=b"fixture log\n" + json.dumps(metadata).encode()
                            ),
                            SimpleNamespace(stdout=b"private-png-bytes"),
                            SimpleNamespace(returncode=1),
                        ]
                    )
                    destination = Path(directory) / "capture.png"
                    with (
                        patch.object(mt5_input, "execute", execute),
                        patch.object(
                            mt5_input.uuid,
                            "uuid4",
                            return_value=SimpleNamespace(hex="test-only"),
                        ),
                    ):
                        self.assertEqual(
                            mt5_input.capture_parent(
                                "docker",
                                "private-parent",
                                {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/private/bus"},
                                destination,
                                cursor,
                            ),
                            metadata,
                        )
                    self.assertEqual(destination.read_bytes(), b"private-png-bytes")
                    path = "/tmp/mt5-cursor-test-only.png"
                    self.assertEqual(
                        execute.call_args_list,
                        [
                            call(
                                "docker",
                                "exec",
                                "--user=plasma",
                                "private-parent",
                                "env",
                                "DBUS_SESSION_BUS_ADDRESS=unix:path=/private/bus",
                                "/usr/local/bin/plasma-ui-fixture",
                                "--capture-output-with-cursor"
                                if cursor
                                else "--capture-output",
                                path,
                            ),
                            call("docker", "exec", "private-parent", "cat", path),
                            call(
                                "docker",
                                "exec",
                                "private-parent",
                                "rm",
                                "-f",
                                path,
                                check=False,
                            ),
                        ],
                    )

    def test_capture_errors_never_skip_cleanup_or_accept_wrong_cursor_api(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "capture.png"
            cases = [
                (
                    [RuntimeError("capture failed"), SimpleNamespace(returncode=1)],
                    "capture failed",
                ),
                (
                    [SimpleNamespace(stdout=b"not json"), SimpleNamespace()],
                    "valid JSON",
                ),
                (
                    [
                        SimpleNamespace(stdout=b'{"method":"x11-root"}'),
                        SimpleNamespace(),
                    ],
                    "requested cursor mode",
                ),
                (
                    [
                        SimpleNamespace(stdout=b'{"method":"kwin-screenshot2"}'),
                        SimpleNamespace(),
                    ],
                    "requested cursor mode",
                ),
                (
                    [
                        SimpleNamespace(
                            stdout=b'{"method":"kwin-screenshot2","include_cursor":true}'
                        ),
                        RuntimeError("copy failed"),
                        SimpleNamespace(),
                    ],
                    "copy failed",
                ),
            ]
            for effects, expected in cases:
                with self.subTest(expected=expected):
                    execute = Mock(side_effect=effects)
                    with (
                        patch.object(mt5_input, "execute", execute),
                        self.assertRaisesRegex(RuntimeError, expected),
                    ):
                        mt5_input.capture_parent(
                            "docker", "private", {}, destination, True
                        )
                    self.assertEqual(execute.call_args.args[3:5], ("rm", "-f"))
                    self.assertEqual(execute.call_args.kwargs, {"check": False})
            execute = Mock(
                side_effect=[
                    SimpleNamespace(stdout=b'{"method":"kwin-screenshot2"}'),
                    SimpleNamespace(stdout=b"png"),
                    OSError("cleanup unavailable"),
                ]
            )
            with (
                patch.object(mt5_input, "execute", execute),
                self.assertRaisesRegex(OSError, "cleanup unavailable"),
            ):
                mt5_input.capture_parent("docker", "private", {}, destination, False)
            self.assertEqual(destination.read_bytes(), b"png")
            execute = Mock(
                side_effect=[
                    SimpleNamespace(stdout=b'{"method":"kwin-screenshot2"}'),
                    SimpleNamespace(stdout=b"png"),
                    SimpleNamespace(),
                ]
            )
            with (
                patch.object(mt5_input, "execute", execute),
                self.assertRaises(IsADirectoryError),
            ):
                mt5_input.capture_parent(
                    "docker", "private", {}, Path(directory), False
                )
            self.assertEqual(execute.call_args.args[3:5], ("rm", "-f"))

    def test_cursor_hotspot_requires_visible_unshifted_sprite_and_equal_dimensions(
        self,
    ):
        before = Image.new("RGB", (256, 192), (40, 40, 40))
        for expected in ([20, 30], [0, 0]):
            with self.subTest(expected=expected):
                after = before.copy()
                x, y = expected
                ImageDraw.Draw(after).polygon(
                    [(x, y), (x, y + 20), (x + 15, y + 15)], fill="white"
                )
                self.assertEqual(
                    mt5_input.cursor_hotspot(before, after, expected),
                    {
                        "expected": expected,
                        "observed_bounds_origin": expected,
                        "full_difference_bounds": [x, y, x + 16, y + 21],
                        "tolerance": 8,
                        "passed": True,
                    },
                )
        for after in (before, Image.new("RGB", before.size, (55, 55, 55))):
            with self.assertRaisesRegex(RuntimeError, "did not contain the cursor"):
                mt5_input.cursor_hotspot(before, after, [20, 30])
        after = before.copy()
        ImageDraw.Draw(after).rectangle((60, 90, 70, 110), fill="white")
        with self.assertRaisesRegex(RuntimeError, "Cursor sprite offset"):
            mt5_input.cursor_hotspot(before, after, [20, 30])
        with self.assertRaisesRegex(RuntimeError, "changed output dimensions"):
            mt5_input.cursor_hotspot(before, Image.new("RGB", (128, 128)), [20, 30])

    def test_full_frame_cursor_oracle_rejects_left_up_overlap_and_unrelated_redraw(
        self,
    ):
        before = Image.new("RGB", (300, 300), (40, 40, 40))
        overlap = before.copy()
        ImageDraw.Draw(overlap).rectangle((70, 70, 110, 110), fill="white")
        with self.assertRaisesRegex(RuntimeError, "Cursor sprite offset"):
            mt5_input.cursor_hotspot(before, overlap, [100, 100])
        unrelated = before.copy()
        draw = ImageDraw.Draw(unrelated)
        draw.polygon([(100, 100), (100, 120), (115, 115)], fill="white")
        draw.point((280, 280), fill="white")
        with self.assertRaisesRegex(RuntimeError, "did not isolate a cursor sprite"):
            mt5_input.cursor_hotspot(before, unrelated, [100, 100])
        tall = before.copy()
        ImageDraw.Draw(tall).rectangle((100, 100, 115, 250), fill="white")
        with self.assertRaisesRegex(RuntimeError, "did not isolate a cursor sprite"):
            mt5_input.cursor_hotspot(before, tall, [100, 100])

    def test_cursor_capture_preserves_each_scene_and_retries_only_unstable_background(
        self,
    ):
        for stable_on, changing_dimensions in (
            (1, False),
            (2, False),
            (2, True),
            (None, False),
        ):
            with (
                self.subTest(stable_on=stable_on, dimensions=changing_dimensions),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory)
                calls = []

                def capture(
                    runtime,
                    parent,
                    session,
                    path,
                    cursor,
                    *,
                    stable_on=stable_on,
                    changing_dimensions=changing_dimensions,
                    calls=calls,
                ):
                    self.assertEqual(
                        (runtime, parent, session),
                        ("docker", "private", {"WAYLAND_DISPLAY": "private-only"}),
                    )
                    shot = len(calls)
                    calls.append((path, cursor))
                    attempt, phase = shot // 3 + 1, shot % 3
                    image = Image.new("RGB", (160, 120), (40, 40, 40))
                    if cursor:
                        ImageDraw.Draw(image).polygon(
                            [(40, 30), (40, 50), (55, 45)], fill="white"
                        )
                    if phase == 2 and (stable_on is None or attempt < stable_on):
                        if changing_dimensions:
                            image = image.resize((161, 120))
                        else:
                            ImageDraw.Draw(image).point((150, 110), fill="red")
                    image.save(path)
                    return {"method": "kwin-screenshot2", "include_cursor": cursor}

                with patch.object(mt5_input, "capture_parent", side_effect=capture):
                    arguments = (
                        "docker",
                        "private",
                        {"WAYLAND_DISPLAY": "private-only"},
                        output,
                        (40, 30),
                        {
                            "origin": {"x": 0, "y": 0},
                            "logical": {"width": 160, "height": 120},
                        },
                    )
                    if stable_on is None:
                        with self.assertRaisesRegex(
                            RuntimeError, "did not stabilize in three"
                        ):
                            mt5_input.capture_cursor(*arguments)
                    else:
                        cursor, metadata = mt5_input.capture_cursor(*arguments)
                        self.assertEqual(cursor["attempt"], stable_on)
                        self.assertEqual(cursor["observed_bounds_origin"], [40, 30])
                        self.assertEqual(
                            cursor["full_difference_bounds"], [40, 30, 56, 51]
                        )
                        self.assertEqual(
                            cursor["scene"], f"cursor-attempt-{stable_on}/scene.json"
                        )
                        self.assertTrue(metadata["include_cursor"])
                attempts = stable_on or 3
                self.assertEqual(len(calls), attempts * 3)
                self.assertEqual(
                    [cursor for _, cursor in calls], [False, True, False] * attempts
                )
                for attempt in range(1, attempts + 1):
                    path = output / f"cursor-attempt-{attempt}"
                    scene = json.loads((path / "scene.json").read_text())
                    self.assertEqual(scene["background_stable"], attempt == stable_on)
                    self.assertEqual(len(list(path.glob("*.png"))), 3)
                    expected_bounds = (
                        None
                        if attempt == stable_on
                        else [0, 0, 160, 120]
                        if changing_dimensions
                        else [150, 110, 151, 111]
                    )
                    self.assertEqual(
                        scene["background_difference_bounds"], expected_bounds
                    )
                for name in (
                    "cursor-hidden.png",
                    "cursor-visible.png",
                    "cursor-background.png",
                ):
                    self.assertEqual((output / name).is_file(), stable_on is not None)
                    if stable_on is not None:
                        self.assertEqual(
                            (output / name).read_bytes(),
                            (
                                output / f"cursor-attempt-{stable_on}" / name
                            ).read_bytes(),
                        )

    def test_resized_window_requires_full_grid_cursor_menu_click_and_escape(self):
        for failure, origin, viewport_anchor in (
            (None, (0, 0), None),
            (None, (-100, 40), None),
            (None, (0, 0), "center"),
            (None, (-100, 40), "center"),
            (None, (0, 0), "top-left"),
            (None, (-100, 40), "top-left"),
            ("scene-second", (0, 0), None),
            ("scene-never", (0, 0), None),
            ("cursor", (0, 0), None),
            ("click", (0, 0), None),
            ("escape", (0, 0), None),
            ("second-escape", (0, 0), None),
            ("second-escape", (0, 0), "center"),
            ("second-escape", (0, 0), "top-left"),
            ("menu-query", (0, 0), None),
        ):
            with (
                self.subTest(failure=failure, viewport_anchor=viewport_anchor),
                tempfile.TemporaryDirectory() as directory,
            ):
                transport = ResizedTransport(failure, viewport_anchor=viewport_anchor)
                transport.desktop[:2] = origin
                output = Path(directory) / "resized"
                client = mt5_input.NestedClient(
                    transport,
                    transport,
                    transport,
                    transport.window,
                    Path(directory),
                    viewport_anchor=viewport_anchor,
                )

                def capture_after_input(*arguments, output=output, transport=transport):
                    self.assertTrue((output / "mouse-menu.png").is_file())
                    self.assertIn(("key", 0xFF1B, False), transport.commands)
                    self.assertFalse(transport.menus)
                    self.assertFalse(transport.menu_mode)
                    self.assertIsNone(transport.capture_owner)
                    self.assertIsNone(transport.menu_owner)
                    self.assertEqual(transport.escapes, 2)
                    return transport.capture(*arguments)

                with (
                    patch.object(
                        mt5_input.time, "monotonic", side_effect=itertools.count()
                    ),
                    patch.object(mt5_input.time, "sleep"),
                    patch.object(
                        mt5_input, "capture_parent", side_effect=capture_after_input
                    ) as capture,
                ):
                    if failure not in (None, "scene-second"):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "cursor at its input position|did not respond|did not stabilize",
                        ):
                            mt5_input.resized_input(
                                client, transport, "docker", "private", {}, output
                            )
                    else:
                        result = mt5_input.resized_input(
                            client, transport, "docker", "private", {}, output
                        )
                        self.assertTrue(result["passed"])
                        self.assertEqual(result["pointer"]["points"], 5)
                        self.assertEqual(
                            result["cursor"]["observed_bounds_origin"],
                            {
                                None: [516, 146],
                                "center": [555, 165],
                                "top-left": [505, 140],
                            }[viewport_anchor],
                        )
                        self.assertAlmostEqual(
                            result["cursor"]["expected"][0],
                            {None: 516.25, "center": 555, "top-left": 505}[
                                viewport_anchor
                            ],
                        )
                        self.assertTrue(result["capture"]["include_cursor"])
                        self.assertEqual(
                            result["cursor"]["attempt"],
                            2 if failure == "scene-second" else 1,
                        )
                        self.assertFalse(transport.menus)
                self.assertEqual(
                    json.loads((output / "window.json").read_text()), transport.window
                )
                self.assertEqual(
                    (
                        transport.window["frame"]["width"],
                        transport.window["frame"]["height"],
                    ),
                    (880, 676) if viewport_anchor else (1100, 820),
                )
                evidence = json.loads((output / "pointer.json").read_text())
                self.assertEqual(len(evidence), 5)
                self.assertTrue(all(point["passed"] for point in evidence))
                self.assertEqual(
                    evidence[0]["framebuffer"][1], evidence[1]["framebuffer"][1]
                )
                self.assertEqual(
                    evidence[1]["framebuffer"][0], evidence[2]["framebuffer"][0]
                )
                captured = failure not in (
                    "click",
                    "escape",
                    "second-escape",
                    "menu-query",
                )
                attempts = (
                    3
                    if failure == "scene-never"
                    else 2
                    if failure == "scene-second"
                    else 1
                    if captured
                    else 0
                )
                self.assertEqual(capture.call_count, attempts * 3)
                canonical = failure in (None, "scene-second")
                self.assertEqual((output / "cursor-hidden.png").is_file(), canonical)
                self.assertEqual((output / "cursor-visible.png").is_file(), canonical)
                self.assertEqual(
                    (output / "cursor-background.png").is_file(), canonical
                )
                self.assertEqual(
                    sum(
                        event[0] != "key" and event[2] == 1
                        for event in transport.commands
                    ),
                    1,
                )
                for attempt in range(1, attempts + 1):
                    self.assertEqual(
                        len(list((output / f"cursor-attempt-{attempt}").glob("*.png"))),
                        3,
                    )
                self.assertEqual(
                    (output / "mouse-menu.png").is_file(),
                    failure != "click",
                )
                if failure == "second-escape":
                    self.assertFalse(transport.menus)
                    self.assertTrue(transport.menu_mode)
                    self.assertEqual(transport.capture_owner, 100)
                    self.assertEqual(transport.menu_owner, 100)
                if failure == "menu-query":
                    self.assertFalse(transport.menu_mode)
                    self.assertIsNone(transport.state()["menu_mode"])
