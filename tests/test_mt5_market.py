"""Market acceptance requires painted browser content and matching real display pixels."""

import base64
import ctypes
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import mt5_market
import mt5_market_probe
from PIL import Image, ImageDraw


class MarketRenderingTests(unittest.TestCase):
    def frame(self):
        image = Image.new("RGB", (240, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 239, 30), fill=(230, 240, 255))
        draw.text((5, 8), "All   Most rated   Search", fill=(20, 20, 20))
        return image

    def ready(self):
        return {
            "dom": {
                "url": "file://terminal64.exe/market.html",
                "title": "Market",
                "ready": "complete",
                "visible": "visible",
                "text": "All Most rated New Free Price Up Price Down No data",
                "paint": [{"name": "first-paint"}, {"name": "first-contentful-paint"}],
            },
            "frames": {"count": 30},
        }

    def test_bundled_catalogue_ui_can_render_offline_without_external_products(self):
        self.assertTrue(mt5_market_probe.page_ready(self.ready()))
        reference = self.frame()
        result = mt5_market.compare_frame(reference, reference.copy())
        self.assertTrue(result["passed"])
        self.assertEqual(result["mean_error"], 0)
        self.assertEqual(result["painted_pixel_match"], 1)

    def test_dom_without_visible_paint_and_advancing_frames_never_passes(self):
        for field, value in (
            ("url", "about:blank"),
            ("title", "Other page"),
            ("ready", "loading"),
            ("visible", "hidden"),
            ("text", ""),
            ("paint", [{"name": "first-paint"}]),
        ):
            with self.subTest(field=field):
                result = self.ready()
                result["dom"][field] = value
                self.assertFalse(mt5_market_probe.page_ready(result))
        result = self.ready()
        result["frames"]["count"] = 0
        self.assertFalse(mt5_market_probe.page_ready(result))

    def snapshot(self, size=(741, 339), dpr=1):
        result = self.ready()
        result["dom"].update(size=list(size), dpr=dpr)
        result["native"] = {
            "desktop": [0, 0, 1280, 960],
            "main": 123,
            "browser": [506, 219, 506 + size[0], 219 + size[1]],
        }
        return result

    def observe_snapshots(self, candidates, *, timeout=False):
        targets = [
            {"url": "file://terminal64.exe/market.html", "webSocketDebuggerUrl": "page"}
        ]
        requests, doms, images, states, native_sizes = [], [], [], [], []
        native_images = []
        for candidate in candidates:
            value = candidate["value"]
            doms.extend([candidate.get("dom_before", value["dom"]), value["dom"]])
            pixels = io.BytesIO()
            self.frame().resize(candidate.get("cdp_size", value["dom"]["size"])).save(
                pixels, "PNG"
            )
            images.append(base64.b64encode(pixels.getvalue()).decode())
            states.extend(
                [
                    candidate.get("before", value["native"]),
                    value["native"],
                    candidate.get("after", value["native"]),
                ]
            )
            native_sizes.append(candidate.get("native_size"))
            native_images.append(candidate.get("native_image"))
        doms, images, states, native_sizes = (
            iter(doms),
            iter(images),
            iter(states),
            iter(native_sizes),
        )
        native_images = iter(native_images)
        socket = MagicMock()
        socket.send.side_effect = lambda text: requests.append(json.loads(text))

        def reply(**_kwargs):
            request = requests[-1]
            if request["method"] == "Runtime.evaluate":
                value = (
                    {"count": 30}
                    if request["params"].get("awaitPromise")
                    else next(doms)
                )
                result = {"result": {"value": value}}
            elif request["method"] == "Page.captureScreenshot":
                result = {"data": next(images)}
            else:
                self.assertEqual(request["method"], "Browser.getBrowserCommandLine")
                result = {"arguments": ["--disable-gpu"]}
            return json.dumps({"id": request["id"], "result": result})

        socket.recv.side_effect = reply
        connection = MagicMock()
        connection.__enter__.return_value = socket
        with tempfile.TemporaryDirectory() as directory:

            def path(value):
                return Path(directory) / value.split("\\")[-1]

            def capture(capture=False):
                state = next(states, candidates[-1]["value"]["native"])
                if capture:
                    rectangle = state["browser"]
                    size = next(native_sizes, None) or (
                        rectangle[2] - rectangle[0],
                        rectangle[3] - rectangle[1],
                    )
                    image = next(native_images, candidates[-1].get("native_image"))
                    (image if image is not None else self.frame().resize(size)).save(
                        path("market-native.png")
                    )
                return state

            with (
                patch.object(mt5_market_probe, "Path", side_effect=path),
                patch.object(mt5_market_probe, "native", side_effect=capture),
                patch.object(mt5_market_probe, "native_input") as input_event,
                patch.object(
                    mt5_market_probe.time,
                    "monotonic",
                    side_effect=[0] * (len(candidates) + 1) + [61],
                ),
                patch.object(mt5_market_probe.time, "sleep") as sleep,
                patch.object(
                    mt5_market_probe.urllib.request,
                    "urlopen",
                    side_effect=lambda url, **_kwargs: io.StringIO(
                        json.dumps(
                            targets
                            if url.endswith("/list")
                            else {"webSocketDebuggerUrl": "browser"}
                        )
                    ),
                ),
                patch("websockets.sync.client.connect", return_value=connection),
            ):
                if timeout:
                    with self.assertRaisesRegex(RuntimeError, "Market never produced"):
                        mt5_market_probe.observe()
                    result = None
                else:
                    result = mt5_market_probe.observe()
                input_event.assert_not_called()
            log = [
                json.loads(line)
                for line in path("market-cdp.jsonl").read_text().splitlines()
            ]
        return result, log, requests, sleep.call_count

    def test_observer_rejects_exact_ci_mixed_resize_then_accepts_current_snapshot(self):
        stale = self.snapshot()
        stale["native"] = self.snapshot((591, 276))["native"]
        self.assertTrue(mt5_market_probe.page_ready(stale))
        current = self.snapshot((591, 276))
        result, log, requests, sleeps = self.observe_snapshots(
            [{"value": stale}, {"value": current, "dom_before": stale["dom"]}]
        )
        self.assertEqual(result["dom"]["size"], [591, 276])
        self.assertEqual(len(log), 1)
        self.assertIn("not_ready", log[0])
        self.assertEqual(log[0]["dom"]["size"], [741, 339])
        self.assertEqual(
            sum(item["method"] == "Page.captureScreenshot" for item in requests), 2
        )
        self.assertEqual(sleeps, 1)

    def test_observer_accepts_coherent_initial_and_resized_geometry(self):
        for size in ((741, 339), (591, 276)):
            with self.subTest(size=size):
                result, log, _requests, sleeps = self.observe_snapshots(
                    [{"value": self.snapshot(size)}]
                )
                self.assertEqual(result["dom"]["size"], list(size))
                self.assertEqual(log, [])
                self.assertEqual(sleeps, 0)

    def test_observer_rejects_native_geometry_changes_across_capture(self):
        current = self.snapshot((591, 276))
        for side in ("before", "after"):
            with self.subTest(side=side):
                moved = {**current["native"], "browser": [507, 219, 1098, 495]}
                result, log, _requests, sleeps = self.observe_snapshots(
                    [{"value": current, side: moved}, {"value": current}]
                )
                self.assertEqual(result["dom"]["size"], [591, 276])
                self.assertEqual(len(log), 1)
                self.assertEqual(sleeps, 1)

    def test_observer_persistent_snapshot_mismatch_times_out_without_input(self):
        stale = self.snapshot()
        stale["native"] = self.snapshot((591, 276))["native"]
        current = self.snapshot()
        loading = Image.new("RGB", current["dom"]["size"], "white")
        ImageDraw.Draw(loading).arc((365, 164, 375, 174), 0, 270, fill="gray", width=2)
        for candidate in (
            {"value": stale},
            {"value": current, "native_image": loading},
        ):
            with self.subTest(candidate=candidate):
                result, log, requests, sleeps = self.observe_snapshots(
                    [candidate], timeout=True
                )
                self.assertIsNone(result)
                self.assertEqual(len(log), 1)
                self.assertIn("not_ready", log[0])
                self.assertNotIn(
                    "Browser.getBrowserCommandLine",
                    [item["method"] for item in requests],
                )
                self.assertEqual(sleeps, 1)
                if "native_image" in candidate:
                    self.assertIn("blank or chart bleed", log[0]["not_ready"])
                    self.assertEqual(log[0]["native"], current["native"])

    def test_observer_projects_fractional_dpr_to_nearest_physical_pixel(self):
        for dpr, physical in ((1.25, (126, 69)), (1.5, (152, 83)), (2, (202, 110))):
            with self.subTest(dpr=dpr):
                value = self.snapshot((101, 55), dpr=dpr)
                value["native"] = self.snapshot(physical)["native"]
                result, log, _requests, sleeps = self.observe_snapshots(
                    [{"value": value, "cdp_size": physical}]
                )
                self.assertEqual(result["snapshot"]["cdp_size"], list(physical))
                self.assertEqual(log, [])
                self.assertEqual(sleeps, 0)

    def test_observer_rejects_stale_native_bitmap_and_newly_hidden_dom(self):
        current = self.snapshot((591, 276))
        hidden = self.snapshot((591, 276))
        hidden["dom"]["visible"] = "hidden"
        loading = Image.new("RGB", current["dom"]["size"], "white")
        ImageDraw.Draw(loading).arc((290, 132, 300, 142), 0, 270, fill="gray", width=2)
        for candidate in (
            {"value": current, "native_size": (741, 339)},
            {"value": hidden, "dom_before": current["dom"]},
            {"value": current, "native_image": loading},
        ):
            with self.subTest(candidate=candidate):
                result, log, _requests, sleeps = self.observe_snapshots(
                    [candidate, {"value": current}]
                )
                self.assertEqual(result["dom"]["size"], [591, 276])
                self.assertEqual(len(log), 1)
                self.assertEqual(sleeps, 1)
                if "native_image" in candidate:
                    self.assertIn("blank or chart bleed", log[0]["not_ready"])
                    self.assertEqual(log[0]["native"], current["native"])

    def test_snapshot_requires_a_native_browser_and_valid_positive_dpr(self):
        for dpr in (0, -1, float("nan"), float("inf")):
            with self.subTest(dpr=dpr):
                value = self.snapshot(dpr=dpr)
                value["snapshot"] = {}
                self.assertFalse(mt5_market_probe.snapshot_ready(value))
        value = self.snapshot()
        value["snapshot"] = {}
        del value["native"]["browser"]
        self.assertFalse(mt5_market_probe.snapshot_ready(value))

    def test_blank_white_chart_bleed_and_tiled_content_are_rejected(self):
        reference = self.frame()
        chart = Image.new("RGB", reference.size, "black")
        draw = ImageDraw.Draw(chart)
        for x in range(0, chart.width, 10):
            draw.line((x, 30, x, 140), fill=(0, 255, 0))
        tiled = Image.new("RGB", reference.size, "white")
        tile = reference.resize((120, 80))
        for position in ((0, 0), (120, 0), (0, 80), (120, 80)):
            tiled.paste(tile, position)
        for observed in (Image.new("RGB", reference.size, "white"), chart, tiled):
            with (
                self.subTest(observed=observed),
                self.assertRaisesRegex(RuntimeError, "blank or chart bleed"),
            ):
                mt5_market.compare_frame(reference, observed)
        with self.assertRaisesRegex(RuntimeError, "no painted text/content"):
            mt5_market.compare_frame(
                Image.new("RGB", reference.size, "white"), reference
            )
        with self.assertRaisesRegex(RuntimeError, "dimensions disagree"):
            mt5_market.compare_frame(reference, reference.resize((120, 80)))

    def test_display_crop_uses_observed_physical_geometry(self):
        image = Image.new("RGB", (480, 320), "black")
        image.paste(self.frame(), (40, 20))
        native = {"desktop": [0, 0, 240, 160], "browser": [20, 10, 140, 90]}
        self.assertEqual(
            mt5_market.browser_crop(image, native).tobytes(), self.frame().tobytes()
        )
        for rectangle in ((-1, 10, 140, 90), (20, 10, 241, 90), (20, 10, 20, 90)):
            with (
                self.subTest(rectangle=rectangle),
                self.assertRaisesRegex(RuntimeError, "outside the visible desktop"),
            ):
                mt5_market.browser_crop(image, {**native, "browser": rectangle})

    def test_transparent_browser_reference_is_rejected_before_alpha_is_lost(self):
        reference = self.frame()
        for alpha in (0, 254):
            with self.subTest(alpha=alpha):
                transparent = reference.convert("RGBA")
                transparent.putalpha(alpha)
                with self.assertRaisesRegex(RuntimeError, "screenshot is transparent"):
                    mt5_market.compare_frame(transparent, reference)
        native = reference.convert("RGBA")
        native.putalpha(0)
        self.assertTrue(mt5_market.compare_frame(reference, native)["passed"])

    def test_uniform_black_and_gray_browser_references_are_not_painted_ui(self):
        for color in ("black", "gray"):
            with (
                self.subTest(color=color),
                self.assertRaisesRegex(RuntimeError, "no painted contrast"),
            ):
                reference = Image.new("RGB", self.frame().size, color)
                mt5_market.compare_frame(reference, reference.copy())

    def test_waits_for_actual_presentation_without_repeating_input(self):
        reference = self.frame()
        capture = Mock(
            side_effect=[Image.new("RGB", reference.size, "white"), reference]
        )
        native = {"desktop": [0, 0, 240, 160], "browser": [0, 0, 240, 160]}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_market.time, "monotonic", return_value=0),
            patch.object(mt5_market.time, "sleep") as sleep,
        ):
            result = mt5_market.presented_frame(
                reference, native, capture, Path(directory), "display"
            )
            self.assertEqual(result["attempts"], 2)
            self.assertTrue((Path(directory) / "display-attempt-1.png").is_file())
            self.assertTrue((Path(directory) / "display.png").is_file())
        self.assertEqual(capture.call_count, 2)
        sleep.assert_called_once_with(0.2)

    def test_bad_presentation_fails_with_its_screenshot_after_the_deadline(self):
        reference = self.frame()
        native = {"desktop": [0, 0, 240, 160], "browser": [0, 0, 240, 160]}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_market.time, "monotonic", side_effect=[0, 11]),
            patch.object(mt5_market.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "chart bleed"),
        ):
            try:
                mt5_market.presented_frame(
                    reference,
                    native,
                    lambda: Image.new("RGB", reference.size, "white"),
                    Path(directory),
                    "display",
                )
            finally:
                self.assertTrue((Path(directory) / "display-attempt-1.png").is_file())
        sleep.assert_not_called()

    def test_diagnostic_policy_never_adds_a_rendering_workaround(self):
        for before in (None, "--disable-gpu --preserve-custom"):
            with self.subTest(before=before):
                registry = SimpleNamespace(
                    HKEY_LOCAL_MACHINE=1,
                    KEY_READ=2,
                    KEY_WRITE=4,
                    REG_SZ=1,
                    CreateKeyEx=Mock(return_value=MagicMock()),
                    QueryValueEx=Mock(return_value=(before, 1)),
                    SetValueEx=Mock(),
                )
                if before is None:
                    registry.QueryValueEx.side_effect = FileNotFoundError
                with patch.dict(sys.modules, {"winreg": registry}):
                    result = mt5_market_probe.debug_policy()
                original = set((before or "").split())
                added = set(result["diagnostic_arguments"].split()) - original
                self.assertEqual(
                    added, {"--remote-debugging-port=9222", "--enable-automation"}
                )
                self.assertEqual(result["before"], before or "")

    def test_failed_observer_keeps_current_native_frames_and_does_not_reuse_old_ones(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                mt5_market, "wine", side_effect=RuntimeError("browser did not paint")
            ),
            patch.object(
                mt5_market,
                "execute",
                return_value=subprocess.CompletedProcess([], 1, b"", b"not created\n"),
            ) as execute,
            self.assertRaisesRegex(RuntimeError, "browser did not paint"),
        ):
            output = Path(directory) / "initial"
            try:
                mt5_market.paint(
                    SimpleNamespace(runtime="docker"),
                    "private",
                    {},
                    Mock(),
                    "vnc",
                    output,
                )
            finally:
                self.assertEqual(
                    execute.call_args_list[0].args[1:5], ("exec", "private", "rm", "-f")
                )
                copies = execute.call_args_list[1:]
                self.assertEqual(len(copies), 4)
                self.assertTrue(all(item.kwargs == {"check": False} for item in copies))
                self.assertEqual(
                    (output / "copy.log").read_bytes(), b"not created\n" * 4
                )

    def test_navigator_route_focuses_before_typing_and_uses_real_keyboard_events(self):
        client, probe = Mock(), Mock()
        navigator = {"navigator": [10, 20, 110, 220], "navigator_hwnd": 456}
        with (
            patch.object(mt5_market, "wait_state") as wait,
            patch.object(mt5_market, "control_point", return_value=(60, 120)),
        ):
            mt5_market.open_market(client, probe, navigator)
        client.click.assert_called_once_with(60, 120)
        client.text.assert_called_once_with("Market")
        self.assertEqual(
            client.key.call_args_list, [call(0xFF0D, True), call(0xFF0D, False)]
        )
        self.assertTrue(wait.call_args_list[-1].args[1]({"focus": 456}))
        self.assertFalse(wait.call_args_list[-1].args[1]({"focus": 999}))

    def test_headless_input_keeps_modifier_order_and_closes_failed_chords(self):
        compile(mt5_market.XTEST, "linux-xtest", "exec")
        client = mt5_market.HeadlessClient("docker", "private", {}, Mock())
        with patch.object(client, "input") as inject:
            client.text("Market")
        inject.assert_called_once()
        self.assertEqual(inject.call_args.args[0], "keys")
        self.assertEqual(
            json.loads(inject.call_args.args[1]),
            [
                [0xFFE1, 1],
                [109, 1],
                [109, 0],
                [0xFFE1, 0],
                [97, 1],
                [97, 0],
                [114, 1],
                [114, 0],
                [107, 1],
                [107, 0],
                [101, 1],
                [101, 0],
                [116, 1],
                [116, 0],
            ],
        )
        with patch.object(
            client, "key", side_effect=[None, RuntimeError("key failed"), None]
        ) as key:
            with self.assertRaisesRegex(RuntimeError, "key failed"):
                client.chord(0xFFE3, 0xFFC1)
        self.assertEqual(key.call_args_list[-1], call(0xFFE3, False))

    def test_batched_native_typing_preserves_all_fourteen_real_key_transitions(self):
        client = mt5_market.HeadlessClient("docker", "private", {}, Mock())
        with patch.object(client, "input") as inject:
            client.text("Market")
        events = []
        user = Mock()
        user.VkKeyScanW.side_effect = lambda code: ord(chr(code).upper())
        user.SendInput.side_effect = lambda _count, event, _size: (
            events.append((event._obj.ki.wVk, event._obj.ki.dwFlags)) or 1
        )
        with (
            patch.object(ctypes, "WinDLL", return_value=user, create=True),
            patch(
                "mt5_ui_probe.physical_desktop",
                return_value={"desktop": [0, 0, 1280, 960]},
            ),
            patch.object(mt5_market_probe.time, "sleep") as sleep,
        ):
            mt5_market_probe.native_input(*inject.call_args.args)
        self.assertEqual(
            events,
            [
                (0xA0, 0),
                (77, 0),
                (77, 2),
                (0xA0, 2),
                (65, 0),
                (65, 2),
                (82, 0),
                (82, 2),
                (75, 0),
                (75, 2),
                (69, 0),
                (69, 2),
                (84, 0),
                (84, 2),
            ],
        )
        self.assertEqual(sleep.call_args_list, [call(0.02)] * 14)

    def test_batched_backing_xtest_keeps_all_events_in_one_native_connection(self):
        client = mt5_market.HeadlessClient("docker", "private", {}, Mock())
        with patch.object(client, "input") as inject:
            client.text("Market")
        expected = json.loads(inject.call_args.args[1])
        x, xtest = Mock(), Mock()
        x.XOpenDisplay.return_value = 123
        x.XKeysymToKeycode.side_effect = lambda _display, code: (
            50 if code == 0xFFE1 else code
        )
        xtest.XTestFakeKeyEvent.return_value = 1
        with (
            patch("ctypes.util.find_library", side_effect=lambda library: library),
            patch.object(ctypes, "CDLL", side_effect=[x, xtest]),
            patch.object(sys, "argv", ["linux-xtest", *inject.call_args.args]),
            patch.object(mt5_market.time, "sleep") as sleep,
        ):
            exec(compile(mt5_market.XTEST, "linux-xtest", "exec"), {})
        x.XOpenDisplay.assert_called_once_with(None)
        x.XCloseDisplay.assert_called_once_with(123)
        self.assertEqual(
            xtest.XTestFakeKeyEvent.call_args_list,
            [
                call(123, 50 if code == 0xFFE1 else code, down, 0)
                for code, down in expected
            ],
        )
        self.assertEqual(sleep.call_args_list, [call(0.02)] * 14)

    def test_headless_qpainter_input_targets_its_actual_private_backing_display(self):
        environment = {
            "DISPLAY_BACKEND": "wayland",
            "DISPLAY": ":100",
            "XAUTHORITY": "/etc/plasma/Xauthority",
            "PLASMA_SOFTWARE_CAPTURE": "1",
            "PLASMA_SOFTWARE_DISPLAY": ":110",
        }
        client = mt5_market.HeadlessClient("docker", "private", environment, Mock())
        with patch.object(mt5_market, "execute") as execute:
            client.click(30, 40)
        self.assertIn("DISPLAY=:110", execute.call_args.args)
        self.assertNotIn("DISPLAY=:100", execute.call_args.args)
        self.assertEqual(environment["DISPLAY"], ":100")
        self.assertFalse(client.windows)

    def test_wayland_headless_uses_real_sendinput_without_enabling_remote_services(
        self,
    ):
        client = mt5_market.HeadlessClient(
            "docker",
            "private",
            {"DISPLAY_BACKEND": "wayland", "PLASMA_SOFTWARE_CAPTURE": "0"},
            Mock(),
            Path("evidence"),
        )
        with (
            patch.object(mt5_market, "wine") as wine,
            patch.object(mt5_market, "execute") as execute,
        ):
            client.click(30, 40)
        wine.assert_called_once_with(
            "docker", "private", "input", Path("evidence"), "click", 30, 40
        )
        execute.assert_not_called()

    def test_rejected_native_sendinput_is_a_failure(self):
        user = Mock()
        user.SendInput.return_value = 0
        with (
            patch.object(ctypes, "WinDLL", return_value=user, create=True),
            patch(
                "mt5_ui_probe.physical_desktop",
                return_value={"desktop": [0, 0, 1280, 960]},
            ),
            self.assertRaisesRegex(RuntimeError, "did not accept the input event"),
        ):
            mt5_market_probe.native_input("key", 0xFF0D, 1)

    def test_nested_paint_compares_child_browser_with_actual_parent_compositor(self):
        image = self.frame()
        observation = self.ready()
        observation.update(
            native={"desktop": [0, 0, 240, 160], "browser": [0, 0, 240, 160]}
        )
        observation["dom"]["size"] = [240, 160]

        def observe(_runtime, _container, _action, output):
            for filename in ("market-cdp.png", "market-native.png"):
                image.save(output / filename)
            return observation

        def capture(_runtime, _container, _session, destination):
            destination.with_suffix(".json").write_text('{"method":"x11-root"}')
            return image, {"method": "x11-root"}

        client = Mock()
        client.screenshot.return_value = image
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_market, "wine", side_effect=observe),
            patch.object(
                mt5_market,
                "execute",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ),
            patch.object(
                mt5_market, "compositor_frame", side_effect=capture
            ) as compositor,
        ):
            result = mt5_market.paint(
                SimpleNamespace(runtime="docker"),
                "child",
                {"source": "child"},
                client,
                "nested",
                Path(directory) / "initial",
                ("parent", {"source": "parent"}),
            )
        self.assertEqual(
            compositor.call_args.args[:3], ("docker", "parent", {"source": "parent"})
        )
        self.assertEqual(result["capture"]["scope"], "parent")
        self.assertTrue(result["native_paint"]["passed"])
        self.assertTrue(result["transport_paint"]["passed"])

    def test_headless_capture_uses_the_native_api_without_fixture_marker_requirements(
        self,
    ):
        image = self.frame()
        pixels = io.BytesIO()
        image.save(pixels, format="PNG")
        session = {
            "PLASMA_SOFTWARE_CAPTURE": "1",
            "PLASMA_SOFTWARE_DISPLAY": ":110",
            "WAYLAND_DISPLAY": "wayland-0",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                mt5_market,
                "execute",
                side_effect=[
                    subprocess.CompletedProcess([], 0, b'{"method":"x11-root"}', b""),
                    subprocess.CompletedProcess([], 0, pixels.getvalue(), b""),
                ],
            ) as execute,
        ):
            actual, metadata = mt5_market.compositor_frame(
                "docker", "private", session, Path(directory) / "capture.png"
            )
            self.assertEqual(actual.tobytes(), image.tobytes())
            self.assertEqual(metadata["method"], "x11-root")
            self.assertEqual(
                json.loads((Path(directory) / "capture.json").read_text()), metadata
            )
        command = execute.call_args_list[0].args
        self.assertIn("DISPLAY=:110", command)
        self.assertIn("QT_QPA_PLATFORM=xcb", command)
        self.assertFalse(any(value.startswith("WAYLAND_DISPLAY=") for value in command))
        self.assertNotIn("plasma-vnc.service", command)


if __name__ == "__main__":
    unittest.main()
