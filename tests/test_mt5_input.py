"""MT5 input contracts keep observation read-only and require real input effects."""

import base64
import contextlib
import ctypes
import io
import json
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mt5_input
import mt5_ui_probe
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def visible_image():
    return Image.linear_gradient("L").resize((1280, 960)).convert("RGB")


class ObservationTests(unittest.TestCase):
    def test_focus_probe_uses_sized_read_only_gui_thread_query(self):
        query = Mock()

        def inspect(thread, pointer):
            self.assertEqual(thread, 123)
            self.assertEqual(
                pointer._obj.cbSize, ctypes.sizeof(mt5_ui_probe.GUIThreadInfo)
            )
            pointer._obj.hwndFocus = 202
            return 1

        query.side_effect = inspect
        user = SimpleNamespace(GetGUIThreadInfo=query)
        self.assertEqual(mt5_ui_probe.focused_control(123, user), 202)
        with patch.object(ctypes, "windll", SimpleNamespace(user32=user), create=True):
            self.assertEqual(mt5_ui_probe.focused_control(123), 202)
        query.side_effect = None
        query.return_value = 0
        self.assertIsNone(mt5_ui_probe.focused_control(123, user))

    def test_edit_readback_only_sends_bounded_wm_gettext(self):
        send = Mock()

        def read(_window, message, capacity, address, flags, timeout, result):
            self.assertEqual(
                (message, capacity, flags, timeout), (0x000D, 4096, 3, 2000)
            )
            buffer = ctypes.create_unicode_buffer("MT5-test-token")
            ctypes.memmove(address, buffer, ctypes.sizeof(buffer))
            result._obj.value = 14
            return 1

        send.side_effect = read
        user = SimpleNamespace(
            SendMessageTimeoutW=send, IsWindow=Mock(return_value=True)
        )
        self.assertEqual(mt5_ui_probe.edit_text(123, user), "MT5-test-token")
        with patch.object(ctypes, "windll", SimpleNamespace(user32=user), create=True):
            self.assertEqual(mt5_ui_probe.edit_text(123), "MT5-test-token")
        send.side_effect = None
        send.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "WM_GETTEXT"):
            mt5_ui_probe.edit_text(123, user)
        user.IsWindow.return_value = False
        self.assertIsNone(mt5_ui_probe.edit_text(123, user))

    def gui(self, *, main=True, menu="valid"):
        kinds = {
            100: "MetaQuotes::MetaTrader::5.00" if main else "Other",
            200: "#32770",
            300: "#32768",
            400: "OtherApp",
            500: "Hidden",
            111: "Edit",
            201: "Button",
            202: "Edit",
            203: "Edit",
            204: "Button",
            205: "Static",
            206: "Edit",
            207: "Edit",
        }
        children = {100: [111], 200: list(range(201, 208))}
        gui = Mock()
        process = Mock()
        gui.EnumWindows.side_effect = lambda callback, argument: [
            callback(window, argument) for window in (100, 200, 300, 400, 500)
        ]
        gui.EnumChildWindows.side_effect = lambda window, callback, argument: [
            callback(child, argument) for child in children.get(window, [])
        ]
        gui.IsWindowVisible.side_effect = lambda window: window not in (500, 206)
        gui.IsWindowEnabled.side_effect = lambda window: window != 207
        gui.GetClassName.side_effect = lambda window: kinds[window]
        gui.GetWindowRect.side_effect = lambda window: (window, 10, window + 20, 30)
        gui.GetWindowText.side_effect = lambda window: (
            "&Cancel" if window == 201 else "OK"
        )
        gui.GetWindowLong.side_effect = lambda window, _index: (
            0x20 if window == 203 else 0
        )
        gui.GetDlgCtrlID.side_effect = lambda window: window + 1000
        gui.GetForegroundWindow.return_value = 100
        if menu == "error":
            gui.GetMenuItemRect.side_effect = OSError("synthetic menu unavailable")
        else:
            gui.GetMenuItemRect.return_value = (menu == "valid", (2, 2, 36, 22))
        process.GetWindowThreadProcessId.side_effect = lambda window: (
            1,
            99 if window == 400 else 10,
        )
        return gui, process

    def test_snapshot_redacts_unmatched_text_and_never_reads_password_or_foreign_controls(
        self,
    ):
        for menu in ("valid", "invalid", "error"):
            with self.subTest(menu=menu):
                gui, process = self.gui(menu=menu)
                read = Mock(
                    side_effect=lambda window: (
                        "expected-token" if window == 202 else "private unrelated text"
                    )
                )
                state = mt5_ui_probe.snapshot("expected-token", gui, process, read)
                self.assertEqual(state["main"], 100)
                self.assertEqual(state["foreground"], 100)
                self.assertEqual(state["pid"], 10)
                self.assertIsNone(state["focus"])
                self.assertEqual(state["menus"], [300])
                self.assertEqual(
                    state["file_rect"],
                    (2, 2, 36, 22) if menu == "valid" else [0, 0, 34, 20],
                )
                self.assertEqual(
                    {call.args[0] for call in read.call_args_list}, {111, 202}
                )
                controls = {item["hwnd"]: item for item in state["controls"]}
                self.assertTrue(controls[201]["cancel"])
                self.assertTrue(controls[202]["matches"])
                self.assertEqual(controls[202]["text"], "expected-token")
                self.assertIsNone(controls[111]["text"])
                self.assertNotIn("private unrelated text", json.dumps(state))
                self.assertTrue({203, 204, 205, 206, 207}.isdisjoint(controls))

    def test_snapshot_handles_missing_main_and_default_platform_imports(self):
        gui, process = self.gui(main=False)
        self.assertIsNone(mt5_ui_probe.snapshot(gui=gui, process=process)["main"])
        gui, process = self.gui()
        gui_module = types.ModuleType("win32gui")
        process_module = types.ModuleType("win32process")
        api_module = types.ModuleType("win32api")
        api_module.GetCursorPos = lambda: (321, 123)
        for name in (
            "EnumWindows",
            "EnumChildWindows",
            "IsWindowVisible",
            "IsWindowEnabled",
            "GetClassName",
            "GetWindowRect",
            "GetWindowText",
            "GetWindowLong",
            "GetDlgCtrlID",
            "GetForegroundWindow",
            "GetMenuItemRect",
            "GetMenu",
        ):
            setattr(gui_module, name, getattr(gui, name))
        process_module.GetWindowThreadProcessId = process.GetWindowThreadProcessId
        with (
            patch.dict(
                sys.modules,
                {
                    "win32gui": gui_module,
                    "win32process": process_module,
                    "win32api": api_module,
                },
            ),
            patch.object(
                mt5_ui_probe, "focused_control", return_value=202
            ) as read_focus,
        ):
            state = mt5_ui_probe.snapshot(read_text=lambda _window: "")
        self.assertEqual(state["main"], 100)
        self.assertEqual(state["pointer"], (321, 123))
        self.assertEqual(state["focus"], 202)
        read_focus.assert_called_once_with(1)
        self.assertTrue(all(not item.get("matches") for item in state["controls"]))

    def test_control_destroyed_after_enumeration_is_skipped_without_observer_failure(
        self,
    ):
        gui, process = self.gui()
        state = mt5_ui_probe.snapshot(
            gui=gui,
            process=process,
            read_text=lambda window: None if window == 202 else "",
        )
        self.assertEqual({item["hwnd"] for item in state["controls"]}, {111, 201})

    def test_server_processes_json_requests_and_exits_cleanly_at_eof(self):
        source = io.StringIO('{}\n{"expected":"private-token"}\n')
        destination = io.StringIO()
        with patch.object(
            mt5_ui_probe,
            "snapshot",
            side_effect=lambda expected: {"expected": expected},
        ) as snapshot:
            mt5_ui_probe.serve(source, destination)
        self.assertEqual(
            [json.loads(line) for line in destination.getvalue().splitlines()],
            [{"expected": ""}, {"expected": "private-token"}],
        )
        self.assertEqual(snapshot.call_count, 2)
        with (
            patch.object(sys, "stdin", io.StringIO("")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(ROOT / "tests/mt5_ui_probe.py"), run_name="__main__")


class BrowserTransportTests(unittest.TestCase):
    def test_initialization_failures_release_partial_resources_and_preserve_the_cause(
        self,
    ):
        for stage in ("launch", "context", "page", "navigation", "canvas", "overlay"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                manager = Mock()
                driver = manager.start.return_value
                browser = driver.chromium.launch.return_value
                context = browser.new_context.return_value
                page = context.new_page.return_value
                canvas, overlay = Mock(), Mock()
                page.locator.side_effect = (
                    lambda selector, canvas=canvas, overlay=overlay: (
                        canvas if selector == "#noVNC_container canvas" else overlay
                    )
                )
                operation = {
                    "launch": driver.chromium.launch,
                    "context": browser.new_context,
                    "page": context.new_page,
                    "navigation": page.goto,
                    "canvas": canvas.wait_for,
                    "overlay": overlay.wait_for,
                }[stage]
                operation.side_effect = RuntimeError(f"original {stage} failure")
                browser.close.side_effect = OSError("secondary browser close failure")
                with (
                    patch.object(mt5_input, "sync_playwright", return_value=manager),
                    self.assertRaisesRegex(RuntimeError, f"original {stage} failure"),
                ):
                    mt5_input.BrowserClient(6080, Path(directory))
                driver.stop.assert_called_once_with()
                self.assertEqual(browser.close.call_count, int(stage != "launch"))
                if stage in ("navigation", "canvas", "overlay"):
                    page.screenshot.assert_called_once_with(
                        path=str(Path(directory) / "browser.png")
                    )
                    context.tracing.stop.assert_called_once_with(
                        path=str(Path(directory) / "browser-trace.zip")
                    )

    def test_failed_character_still_releases_physical_shift(self):
        client = object.__new__(mt5_input.BrowserClient)
        client.page = Mock()
        client.page.keyboard.type.side_effect = RuntimeError("input disconnected")
        with self.assertRaisesRegex(RuntimeError, "input disconnected"):
            client.text("M")
        client.page.keyboard.down.assert_called_once_with("Shift")
        client.page.keyboard.up.assert_called_once_with("Shift")

    def test_failed_keyboard_chord_releases_its_modifier(self):
        client = object.__new__(mt5_input.BrowserClient)
        client.page = Mock()
        client.page.keyboard.press.side_effect = RuntimeError("keyboard disconnected")
        with (
            patch.object(mt5_input.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "keyboard disconnected"),
        ):
            client.chord(0xFFE3, ord("s"))
        client.page.keyboard.down.assert_called_once_with("Control")
        client.page.keyboard.up.assert_called_once_with("Control")

    def test_evidence_or_browser_close_errors_still_stop_every_browser_resource(self):
        for failure in ("trace", "screenshot", "browser", "driver"):
            with self.subTest(failure=failure):
                client = object.__new__(mt5_input.BrowserClient)
                client.context, client.page, client.browser, client.driver = (
                    Mock(),
                    Mock(),
                    Mock(),
                    Mock(),
                )
                client.output = Path("/private-output")
                operations = {
                    "trace": client.context.tracing.stop,
                    "screenshot": client.page.screenshot,
                    "browser": client.browser.close,
                    "driver": client.driver.stop,
                }
                operations[failure].side_effect = OSError(f"{failure} failed")
                with self.assertRaisesRegex(OSError, f"{failure} failed"):
                    client.close()
                self.assertTrue(
                    all(operation.call_count == 1 for operation in operations.values())
                )

    def test_browser_uses_real_canvas_mouse_coordinates_and_keyboard_methods(self):
        image = visible_image()
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        manager = Mock()
        driver = manager.start.return_value
        browser = driver.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        canvas = page.locator.return_value
        transition = Mock()
        page.locator.side_effect = lambda selector: (
            canvas if selector == "#noVNC_container canvas" else transition
        )
        canvas.evaluate.side_effect = lambda expression: (
            base64.b64encode(stream.getvalue()).decode()
            if "toDataURL" in expression
            else [800, 600]
        )
        canvas.bounding_box.return_value = {
            "x": 40,
            "y": 30,
            "width": 400,
            "height": 300,
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_input, "sync_playwright", return_value=manager),
        ):
            client = mt5_input.BrowserClient(6080, Path(directory), host="172.23.0.7")
            self.assertEqual(client.screenshot().size, image.size)
            client.click(200, 400)
            page.mouse.move.assert_called_once_with(140, 230)
            page.mouse.down.assert_called_once_with()
            page.mouse.up.assert_called_once_with()
            client.key(0xFF1B, True)
            client.key(0xFF1B, False)
            client.key(ord("x"), True)
            client.chord(0xFFE3, ord("s"))
            self.assertEqual(
                page.keyboard.method_calls[-3:],
                [
                    unittest.mock.call.down("Control"),
                    unittest.mock.call.press("s", delay=20),
                    unittest.mock.call.up("Control"),
                ],
            )
            page.keyboard.reset_mock()
            client.text("Aa1!")
            self.assertEqual(
                page.keyboard.method_calls,
                [
                    unittest.mock.call.down("Shift"),
                    unittest.mock.call.type("A", delay=15),
                    unittest.mock.call.up("Shift"),
                    unittest.mock.call.type("a", delay=15),
                    unittest.mock.call.type("1", delay=15),
                    unittest.mock.call.down("Shift"),
                    unittest.mock.call.type("!", delay=15),
                    unittest.mock.call.up("Shift"),
                ],
            )
            page.goto.assert_called_once_with(
                "http://172.23.0.7:6080/vnc.html?autoconnect=true&resize=off&password=testpass"
            )
            canvas.wait_for.assert_called_once_with(state="visible", timeout=30000)
            transition.wait_for.assert_called_once_with(state="hidden", timeout=30000)
            client.close()
            context.tracing.stop.assert_called_once_with(
                path=str(Path(directory) / "browser-trace.zip")
            )
            page.screenshot.assert_called_once_with(
                path=str(Path(directory) / "browser.png")
            )
        browser.close.assert_called_once()
        driver.stop.assert_called_once()

    def test_private_network_address_selects_browser_or_raw_vnc(self):
        with (
            patch.object(
                mt5_input,
                "execute",
                return_value=SimpleNamespace(stdout=b"172.23.0.7\n"),
            ),
            patch.object(mt5_input, "BrowserClient") as browser,
            patch.object(mt5_input, "RFBClient") as vnc,
        ):
            self.assertIs(
                mt5_input.connect("docker", "private", "web", Path("/private")),
                browser.return_value,
            )
            browser.assert_called_once_with(6080, Path("/private"), host="172.23.0.7")
            self.assertIs(
                mt5_input.connect("docker", "private", "vnc", Path("/private")),
                vnc.return_value,
            )
            vnc.assert_called_once_with(5900, host="172.23.0.7")
        with (
            patch.object(
                mt5_input, "execute", return_value=SimpleNamespace(stdout=b"")
            ),
            self.assertRaisesRegex(RuntimeError, "did not assign an address"),
        ):
            mt5_input.connect("docker", "private", "vnc", Path("/private"))


class ProbeTransportTests(unittest.TestCase):
    def test_broken_worker_pipe_does_not_skip_reaping_or_log_closure(self):
        probe = object.__new__(mt5_input.Probe)
        probe.process, probe.log, probe.states = Mock(), Mock(), Mock()
        probe.process.stdin.close.side_effect = BrokenPipeError("worker exited")
        probe.process.wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]
        probe.close()
        probe.process.kill.assert_called_once_with()
        self.assertEqual(probe.process.wait.call_count, 2)
        probe.log.close.assert_called_once_with()
        probe.states.close.assert_called_once_with()

    def test_worker_wait_error_still_closes_both_logs(self):
        probe = object.__new__(mt5_input.Probe)
        probe.process, probe.log, probe.states = Mock(), Mock(), Mock()
        probe.process.wait.side_effect = OSError("worker could not be reaped")
        with self.assertRaisesRegex(OSError, "worker could not be reaped"):
            probe.close()
        probe.log.close.assert_called_once_with()
        probe.states.close.assert_called_once_with()

    def test_probe_sends_expected_tokens_records_state_and_escalates_worker_timeout(
        self,
    ):
        process = SimpleNamespace(
            stdin=io.StringIO(),
            stdout=io.StringIO('{"main":100}\n'),
            wait=Mock(side_effect=[subprocess.TimeoutExpired("probe", 10), 0]),
            kill=Mock(),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                mt5_input,
                "execute",
                side_effect=[
                    SimpleNamespace(),
                    SimpleNamespace(stdout=b'{"DISPLAY":":123"}'),
                ],
            ) as execute,
            patch.object(mt5_input.subprocess, "Popen", return_value=process) as start,
            patch.object(
                mt5_input.select, "select", return_value=([process.stdout], [], [])
            ),
        ):
            output = Path(directory)
            probe = mt5_input.Probe("docker", "private", output)
            self.assertEqual(probe.state("test-token"), {"main": 100})
            self.assertEqual(
                json.loads(process.stdin.getvalue()), {"expected": "test-token"}
            )
            self.assertEqual(
                json.loads((output / "gui-state.jsonl").read_text()), {"main": 100}
            )
            probe.close()
            self.assertTrue(probe.log.closed)
            self.assertTrue(probe.states.closed)
            process.kill.assert_called_once()
            self.assertEqual(start.call_args.args[0][-1], r"C:\MT5\mt5_ui_probe.py")
            self.assertEqual(execute.call_args_list[0].args[1], "cp")

    def test_probe_timeout_exit_and_invalid_json_are_not_success(self):
        for ready, line, error in (
            (False, "", RuntimeError),
            (True, "", RuntimeError),
            (True, "invalid\n", ValueError),
        ):
            with (
                self.subTest(ready=ready, line=line),
                tempfile.TemporaryDirectory() as directory,
            ):
                process = SimpleNamespace(
                    stdin=io.StringIO(),
                    stdout=io.StringIO(line),
                    wait=Mock(),
                    kill=Mock(),
                )
                with (
                    patch.object(
                        mt5_input,
                        "execute",
                        side_effect=[SimpleNamespace(), SimpleNamespace(stdout=b"{}")],
                    ),
                    patch.object(mt5_input.subprocess, "Popen", return_value=process),
                    patch.object(
                        mt5_input.select,
                        "select",
                        return_value=([process.stdout] if ready else [], [], []),
                    ),
                ):
                    probe = mt5_input.Probe("docker", "private", Path(directory))
                    with self.assertRaises(error):
                        probe.state()
                    probe.close()
                process.kill.assert_not_called()


class InputState:
    def __init__(self, failure=None):
        self.failure = failure
        self.startup = True
        self.menus = False
        self.focused = False
        self.selected = False
        self.buffer = ""
        self.typed = []

    def state(self, expected=""):
        controls = [
            {
                "hwnd": 111,
                "root": 100,
                "edit": True,
                "rect": [0, 900, 20, 920],
                "matches": False,
            }
        ]
        dialogs = []
        if self.startup:
            dialogs.append({"hwnd": 200, "rect": [100, 100, 600, 400]})
            controls.extend(
                [
                    {
                        "hwnd": 201,
                        "root": 200,
                        "cancel": True,
                        "rect": [400, 300, 500, 330],
                    },
                    {
                        "hwnd": 202,
                        "root": 200,
                        "edit": True,
                        "rect": [100, 350, 600, 380],
                        "matches": bool(expected) and self.buffer == expected,
                        "text": "wrong readback"
                        if self.failure == "readback"
                        else self.buffer,
                    },
                    {
                        "hwnd": 203,
                        "root": 200,
                        "edit": True,
                        "rect": [100, 100, 600, 130],
                        "matches": False,
                        "text": None,
                    },
                ]
            )
        return {
            "main": 100,
            "main_enabled": not self.startup,
            "focus": 202 if self.focused else None,
            "file_rect": [0, 0, 34, 20],
            "controls": controls,
            "dialogs": dialogs,
            "menus": [300] if self.menus else [],
        }

    def screenshot(self):
        return visible_image()

    def click(self, x, y):
        if (x, y) == (450, 315):
            self.startup = self.failure == "cancel"
        elif (x, y) == (17, 10):
            self.menus = self.failure != "click"
        elif (x, y) == (350, 365):
            self.focused = self.failure != "focus"

    def key(self, key, down):
        if key == 0xFF1B and down and self.failure != "escape":
            self.menus = False

    def chord(self, _modifier, key):
        if key == ord("a") and self.failure != "modifier":
            self.selected = True

    def text(self, value):
        self.typed.append(value)
        if self.focused and self.failure != "keyboard":
            self.buffer = value if self.selected else self.buffer + value
            self.selected = False


class InputEffectTests(unittest.TestCase):
    def clock(self):
        values = iter(range(10000))
        return lambda: next(values)

    def test_mt5_actions_require_menu_changes_and_exact_nonpassword_readback(self):
        state = InputState()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_input.time, "monotonic", side_effect=self.clock()),
            patch.object(mt5_input.time, "sleep"),
        ):
            output = Path(directory)
            result = mt5_input.exercise(state, state, output)
            self.assertTrue(result["passed"])
            self.assertEqual(
                (output / "typed.txt").read_bytes(),
                (output / "expected.txt").read_bytes(),
            )
            self.assertEqual(len(state.typed), 2)
            self.assertEqual(state.typed[0], "MT5Input-seed-456")
            self.assertEqual(state.buffer, (output / "expected.txt").read_text())
            self.assertFalse(state.startup)
            self.assertFalse(state.menus)
            for name in (
                "startup.png",
                "mouse-menu.png",
                "keyboard.png",
                "after-input.png",
            ):
                self.assertTrue((output / name).is_file())

    def test_dropped_keyboard_clicks_and_inconsistent_readback_fail(self):
        for failure in (
            "keyboard",
            "modifier",
            "focus",
            "cancel",
            "click",
            "escape",
            "readback",
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
                patch.object(mt5_input.time, "monotonic", side_effect=self.clock()),
                patch.object(mt5_input.time, "sleep"),
                self.assertRaisesRegex(RuntimeError, "did not respond|did not equal"),
            ):
                state = InputState(failure)
                mt5_input.exercise(state, state, Path(directory))

    def test_waiting_retries_and_rejects_unresponsive_state(self):
        probe = Mock()
        probe.state.side_effect = [{"ready": False}, {"ready": True}]
        with (
            patch.object(mt5_input.time, "monotonic", return_value=0),
            patch.object(mt5_input.time, "sleep"),
        ):
            self.assertTrue(
                mt5_input.wait_state(probe, lambda state: state["ready"], "test-token")[
                    "ready"
                ]
            )
        self.assertEqual(probe.state.call_count, 2)
        probe.state.side_effect = None
        probe.state.return_value = {"ready": False}
        with (
            patch.object(mt5_input.time, "monotonic", side_effect=[0, 21]),
            self.assertRaisesRegex(RuntimeError, "did not respond"),
        ):
            mt5_input.wait_state(probe, lambda state: state["ready"])
        self.assertEqual(mt5_input.center([10, 20, 30, 50]), (20, 35))

    def test_black_frames_cannot_count_as_visible_mt5(self):
        client = Mock()
        client.screenshot.side_effect = [Image.new("RGB", (1280, 960)), visible_image()]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_input.time, "monotonic", return_value=0),
            patch.object(mt5_input.time, "sleep"),
        ):
            mt5_input.visible_frame(client, Path(directory) / "visible.png")
        client.screenshot.side_effect = None
        client.screenshot.return_value = Image.new("RGB", (1280, 960))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(mt5_input.time, "monotonic", side_effect=[0, 26]),
            self.assertRaisesRegex(RuntimeError, "black or blank"),
        ):
            mt5_input.visible_frame(client, Path(directory) / "failure.png")


class InputMatrixTests(unittest.TestCase):
    def test_collection_or_parent_close_errors_still_remove_private_resources(self):
        for stage in ("child-collection", "parent-collection", "parent-close"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                parent, child = Mock(), Mock()
                if stage == "parent-close":
                    parent.close.side_effect = OSError(stage)

                def collect(_runtime, name, _output, stage=stage):
                    is_parent = name.startswith("mt5-input-parent-")
                    if stage == (
                        "parent-collection" if is_parent else "child-collection"
                    ):
                        raise OSError(stage)

                args = SimpleNamespace(
                    output=directory,
                    case="cleanup",
                    runtime="docker",
                    parent_image=None,
                    image="private",
                    gpu=False,
                    backends=["wayland"],
                    modes=["vnc"],
                )
                with (
                    patch.object(
                        mt5_input,
                        "profiles",
                        return_value=[("wayland", "vnc", None)]
                        if stage == "child-collection"
                        else [],
                    ),
                    patch.object(
                        mt5_input,
                        "execute",
                        return_value=SimpleNamespace(stdout=b"support", stderr=b""),
                    ) as execute,
                    patch.object(mt5_input, "start"),
                    patch.object(mt5_input, "connect", side_effect=[parent, child]),
                    patch.object(
                        mt5_input,
                        "parent_x11",
                        return_value=(":123", Path(directory) / "private-cookie"),
                    ),
                    patch.object(
                        mt5_input,
                        "configuration",
                        return_value={
                            "DISPLAY_BACKEND": "wayland",
                            "DISPLAY_MODE": "vnc",
                            "HOST_DISPLAY_BACKEND": "none",
                        },
                    ),
                    patch.object(mt5_input, "Probe"),
                    patch.object(mt5_input, "exercise", return_value={"passed": True}),
                    patch.object(mt5_input, "collect", side_effect=collect),
                    self.assertRaisesRegex(OSError, stage),
                ):
                    mt5_input.main(args)
                removed = {
                    call.args[-1]
                    for call in execute.call_args_list
                    if call.args[1:3] == ("rm", "-fv")
                }
                self.assertTrue(
                    any(name.startswith("mt5-input-parent-") for name in removed)
                )
                self.assertEqual(len(removed), 2 if stage == "child-collection" else 1)
                self.assertEqual(
                    sum(
                        call.args[1:3] == ("network", "rm")
                        for call in execute.call_args_list
                    ),
                    1,
                )

    def test_all_requested_profiles_keep_offline_account_free_resources_and_cleanup(
        self,
    ):
        for fail in (
            False,
            True,
            "capture",
            "probe",
            "client",
            "wrong-backend",
            "wrong-mode",
            "wrong-transport",
        ):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                sessions = {}
                clients = []
                probes = []
                commands = []

                def execute(*arguments, commands=commands, **_options):
                    commands.append(arguments)
                    return SimpleNamespace(
                        stdout=b"fake compositor support\n", stderr=b""
                    )

                def start(
                    _runtime,
                    _image,
                    name,
                    backend,
                    mode,
                    output,
                    extra=(),
                    sessions=sessions,
                    fail=fail,
                    **options,
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
                        "MT5_PASSWORD": "synthetic-private-value",
                    }
                    if not options.get("sleep"):
                        if fail == "wrong-backend":
                            sessions[name]["DISPLAY_BACKEND"] = (
                                "x11" if backend == "wayland" else "wayland"
                            )
                        elif fail == "wrong-mode":
                            sessions[name]["DISPLAY_MODE"] = "headless"
                        elif fail == "wrong-transport":
                            sessions[name]["HOST_DISPLAY_BACKEND"] = (
                                "wayland" if host == "x11" else "x11"
                            )
                        self.assertEqual(
                            options["application"], "/usr/local/bin/mt5-launch"
                        )
                        self.assertTrue(
                            {"MT5_ACCOUNT=", "MT5_PASSWORD=", "MT5_SERVER="}
                            <= set(extra)
                        )
                        self.assertIn(
                            "type=bind,source=/dev/null,target=/etc/systemd/system/mt5-native-mcp.socket,readonly",
                            extra,
                        )

                def client(*_arguments, clients=clients, fail=fail):
                    value = Mock()
                    value.screenshot.return_value = visible_image()
                    if fail == "capture":
                        value.screenshot.side_effect = OSError("capture lost")
                    elif fail == "client" and clients:
                        value.close.side_effect = OSError("client close failed")
                    clients.append(value)
                    return value

                def probe(*_arguments, probes=probes, fail=fail):
                    value = Mock()
                    if fail == "probe":
                        value.close.side_effect = OSError("probe close failed")
                    probes.append(value)
                    return value

                def cookie(_runtime, _parent, path):
                    return ":123", path / "private-auth"

                args = SimpleNamespace(
                    output=directory,
                    case="matrix",
                    runtime="docker",
                    parent_image="private-parent",
                    image="private-image",
                    gpu=False,
                    backends=["wayland", "x11"],
                    modes=["nested", "vnc", "web"],
                )
                with (
                    patch.object(
                        mt5_input,
                        "execute",
                        side_effect=execute,
                    ),
                    patch.object(mt5_input, "start", side_effect=start),
                    patch.object(mt5_input, "connect", side_effect=client),
                    patch.object(mt5_input, "parent_x11", side_effect=cookie),
                    patch.object(
                        mt5_input,
                        "configuration",
                        side_effect=lambda _runtime, name, sessions=sessions: sessions[
                            name
                        ],
                    ),
                    patch.object(mt5_input, "Probe", side_effect=probe),
                    patch.object(
                        mt5_input,
                        "exercise",
                        side_effect=RuntimeError("synthetic input lost")
                        if fail in (True, "capture")
                        else None,
                        return_value={"passed": True},
                    ) as exercise,
                    patch.object(mt5_input, "collect"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(mt5_input.main(args), 1 if fail else 0)
                report = json.loads(
                    (Path(directory) / "matrix/results.json").read_text()
                )
                self.assertEqual(len(report["cases"]), 8)
                self.assertEqual(report["passed"], not fail)
                self.assertNotIn("synthetic-private-value", json.dumps(report))
                wrong_profile = isinstance(fail, str) and fail.startswith("wrong-")
                self.assertEqual(len(probes), 0 if wrong_profile else 8)
                self.assertTrue(all(value.close.called for value in probes))
                self.assertTrue(all(value.close.called for value in clients))
                self.assertTrue(
                    any(
                        command[1:3] == ("network", "create")
                        and "--internal" in command
                        for command in commands
                    )
                )
                self.assertTrue(
                    any(command[1:3] == ("network", "rm") for command in commands)
                )
                if fail is True:
                    self.assertTrue(
                        (Path(directory) / "matrix/wayland/web/failure.png").is_file()
                    )
                elif fail == "capture":
                    self.assertTrue(
                        all(
                            row["error"] == "RuntimeError: synthetic input lost"
                            for row in report["cases"]
                        )
                    )
                elif fail in ("probe", "client"):
                    self.assertTrue(
                        any(
                            "Cleanup failed:" in row.get("error", "")
                            for row in report["cases"]
                        )
                    )
                elif wrong_profile:
                    exercise.assert_not_called()
                    self.assertEqual(len(clients), 1)
                    self.assertTrue(
                        all(
                            "Actual display session" in row["error"]
                            for row in report["cases"]
                        )
                    )
                if not wrong_profile:
                    self.assertTrue(
                        all(
                            set(row["session"])
                            == {
                                "DISPLAY_BACKEND",
                                "DISPLAY_MODE",
                                "HOST_DISPLAY_BACKEND",
                            }
                            for row in report["cases"]
                        )
                    )

    def test_cli_help_exposes_only_supported_modes_and_backend_choices(self):
        with (
            patch.object(sys, "argv", ["mt5-input", "--help"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(ROOT / "tests/mt5_input.py"), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        self.assertIn("{nested,vnc,web}", output.getvalue())
        self.assertIn("{wayland,x11}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
