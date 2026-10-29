"""Private compositor control checks immutable geometry, fresh state and cleanup."""

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kwin_driver


def display(scale=1, name="Virtual-1"):
    return {
        "screen": {
            "currentSize": {"width": round(1920 / scale), "height": round(1200 / scale)}
        },
        "outputs": [
            {
                "name": name,
                "enabled": True,
                "rotation": 1,
                "scale": scale,
                "pos": {"x": 0, "y": 0},
                "currentModeId": "7",
                "modes": [{"id": "7", "size": {"width": 1920, "height": 1200}}],
            },
            {"name": "disabled", "enabled": False},
        ],
    }


class Desktop:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.codes = []
        self.tags = []
        self.unloaded = []
        self.failure = None
        self.identifier = b"7\n"
        self.cursor = {"x": 13, "y": 17}
        self.window = {
            "id": "child-1",
            "caption": "Private compositor child",
            "frame": {"x": 0, "y": 0, "width": 1920, "height": 1200},
            "client": {"x": 0, "y": 0, "width": 1920, "height": 1200},
            "fullScreen": True,
            "noBorder": True,
        }
        self.windows = [self.window]
        self.maximized = True
        self.focused = False
        self.decorated = True
        self.pending = None
        self.acknowledgements = 0
        self.silent = False
        self.journal_empty_once = False
        self.invalid_state = None
        self.invalid_json = False
        self.layouts = [display()]

    def result(self, value=b""):
        return SimpleNamespace(returncode=0, stdout=value, stderr=b"")

    def __call__(self, *arguments, **_options):
        self.calls.append(arguments)
        if "python3" in arguments:
            index = arguments.index("-c")
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    arguments[index + 1],
                    str(self.root / "kiosk/kwinrulesrc"),
                ],
                check=True,
            )
            return self.result()
        if "kscreen-doctor" in arguments:
            if arguments[-1] == "--json":
                value = (
                    self.layouts.pop(0) if len(self.layouts) > 1 else self.layouts[0]
                )
                return self.result(json.dumps(value).encode())
            return self.result()
        if "journalctl" in arguments:
            if self.failure == "journal":
                raise OSError("private journal failed")
            if self.silent or self.journal_empty_once:
                self.journal_empty_once = False
                return self.result()
            payload = (
                "invalid-json"
                if self.invalid_json
                else json.dumps(
                    self.invalid_state
                    if self.invalid_state is not None
                    else self.snapshot
                )
            )
            return self.result(
                (
                    "unrelated journal line\njs: "
                    + self.tags[-1]
                    + " "
                    + payload
                    + "\n"
                ).encode()
            )
        if "loadScript" in arguments:
            self.tags.append(arguments[-1])
            self.current = (
                self.root / "runtime" / Path(arguments[-2]).name
            ).read_text()
            self.codes.append(self.current)
            if self.failure == "load":
                raise OSError("private load failed")
            return self.result(self.identifier)
        if "unloadScript" in arguments:
            self.unloaded.append(arguments[-1])
            if self.failure == "unload":
                raise OSError("private unload failed")
            return self.result(b"true\n")
        if arguments[-1] == "run":
            if self.failure == "run":
                raise OSError("private run failed")
            if "w.fullScreen=false" in self.current:
                self.window.update(fullScreen=False, noBorder=False)
                self.maximized = False
            # KWin's rectangle values are immutable; only assigning a fresh
            # object to the frameGeometry setter creates a configure request.
            assignment = re.search(r"\.frameGeometry=(\{[^;]+\});", self.current)
            if assignment and not self.window["fullScreen"] and not self.maximized:
                self.pending = json.loads(assignment[1])
                self.acknowledgements = 2
            elif self.pending is not None:
                self.acknowledgements -= 1
                if self.acknowledgements == 0:
                    self.window["frame"] = self.pending
                    self.window["client"] = {
                        **self.pending,
                        "y": self.pending["y"] + (36 if self.decorated else 0),
                        "height": self.pending["height"]
                        - (36 if self.decorated else 0),
                    }
                    self.pending = None
            self.focused |= "workspace.activeWindow=w" in self.current
            self.snapshot = {
                "cursor": copy.deepcopy(self.cursor),
                "windows": copy.deepcopy(self.windows),
            }
        return self.result()


class PrivateDriverTests(unittest.TestCase):
    def create(self, directory):
        root = Path(directory)
        desktop = Desktop(root)
        return kwin_driver.KWinDriver("docker", "private-parent", root), desktop

    def test_kscreen_without_enabled_uses_output_priority(self):
        data = display(2)
        data["outputs"][0].pop("enabled")
        data["outputs"][0]["priority"] = 1
        data["outputs"][1].pop("enabled")
        data["outputs"][1]["priority"] = 0
        self.assertEqual(kwin_driver.output_layout(data)["scale"], 2)
        data["outputs"][0]["enabled"] = False
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            kwin_driver.output_layout(data)
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.layouts = [data]
            with patch.object(kwin_driver, "execute", side_effect=desktop):
                with self.assertRaisesRegex(RuntimeError, "exactly one"):
                    driver.output_scale(2)
            saved = list(Path(directory).glob("kscreen-initial-*.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text()), data)

    def test_rules_reset_uses_actual_kiosk_path_and_private_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            with patch.object(kwin_driver, "execute", side_effect=desktop):
                driver.clear_rules()
            self.assertEqual(
                (Path(directory) / "kiosk/kwinrulesrc").read_text(),
                "[General]\ncount=0\nrules=\n",
            )
            self.assertEqual(
                desktop.calls[0][-1], "/home/plasma/.config/plasma-kiosk/kwinrulesrc"
            )
            self.assertIn("--user=plasma", desktop.calls[0])
            self.assertEqual(desktop.calls[-1][-2:], ("/KWin", "reconfigure"))
            self.assertIn(
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/plasma/bus", desktop.calls[-1]
            )

    def test_scale_waits_for_confirmation_keeps_physical_mode_and_saves_source_json(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.layouts = [display(), display(), display(1.5)]
            desktop.layouts[-1]["outputs"][0]["pos"] = {"x": 100, "y": 50}
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "sleep") as sleep,
            ):
                output = driver.output_scale(1.5)
            self.assertIs(driver.output, output)
            self.assertEqual(
                output,
                {
                    "name": "Virtual-1",
                    "scale": 1.5,
                    "mode": {"id": "7", "width": 1920, "height": 1200},
                    "origin": {"x": 100, "y": 50},
                    "logical": {"width": 1280, "height": 800},
                },
            )
            self.assertEqual(
                len(list(Path(directory).glob("kscreen-confirmed-*.json"))), 1
            )
            sleep.assert_called_once_with(0.1)
            self.assertTrue(
                any(call[-1] == "output.Virtual-1.scale.1.5" for call in desktop.calls)
            )
            self.assertTrue(
                all(
                    "QT_QPA_PLATFORM=wayland" in call
                    and "WAYLAND_DISPLAY=wayland-0" in call
                    for call in desktop.calls
                )
            )

    def test_invalid_or_unacknowledged_scale_never_updates_confirmed_output(self):
        for scale in (0, -1, True, "1.5", float("nan"), float("inf")):
            with self.subTest(scale=scale), tempfile.TemporaryDirectory() as directory:
                driver, desktop = self.create(directory)
                with (
                    patch.object(kwin_driver, "execute", side_effect=desktop),
                    self.assertRaisesRegex(RuntimeError, "scale"),
                ):
                    driver.output_scale(scale)
                self.assertFalse(desktop.calls)
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "monotonic", side_effect=[0, 16]),
                self.assertRaisesRegex(RuntimeError, "acknowledged"),
            ):
                driver.output_scale(2)
            self.assertIsNone(driver.output)
            self.assertFalse(list(Path(directory).glob("kscreen-confirmed-*.json")))
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.layouts = [display(), display(2, "Virtual-2")]
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                self.assertRaisesRegex(RuntimeError, "output changed"),
            ):
                driver.output_scale(2)

    def test_scripts_use_unique_tags_and_files_even_when_kwin_reuses_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.journal_empty_once = True
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "sleep") as sleep,
            ):
                first = driver.state()
                desktop.cursor = {"x": 321, "y": 123}
                second = driver.state()
            self.assertEqual(first["cursor"], {"x": 13, "y": 17})
            self.assertEqual(second["cursor"], {"x": 321, "y": 123})
            self.assertEqual(len(set(desktop.tags)), 2)
            self.assertEqual(desktop.tags, desktop.unloaded)
            self.assertEqual(
                sum(
                    call[-2:] == ("/Scripting/Script7", "run") for call in desktop.calls
                ),
                2,
            )
            self.assertTrue(
                all("filter(w=>w.normalWindow)" in code for code in desktop.codes)
            )
            self.assertFalse(list((Path(directory) / "runtime").glob("*.js")))
            self.assertEqual(
                len(list(Path(directory).glob("MT5_PRIVATE_STATE_*.json"))), 2
            )
            sleep.assert_called_once_with(0.1)

    def test_script_load_run_journal_or_unload_failures_remove_shared_javascript(self):
        for failure in ("load", "run", "journal", "unload"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                driver, desktop = self.create(directory)
                desktop.failure = failure
                with (
                    patch.object(kwin_driver, "execute", side_effect=desktop),
                    self.assertRaisesRegex(OSError, "private"),
                ):
                    driver.state()
                self.assertEqual(desktop.tags, desktop.unloaded)
                self.assertFalse(list((Path(directory) / "runtime").glob("*.js")))

    def test_invalid_reused_script_ids_or_payloads_cannot_be_reported_as_state(self):
        for identifier in (b"-1\n", b"not-an-id\n"):
            with (
                self.subTest(identifier=identifier),
                tempfile.TemporaryDirectory() as directory,
            ):
                driver, desktop = self.create(directory)
                desktop.identifier = identifier
                with (
                    patch.object(kwin_driver, "execute", side_effect=desktop),
                    self.assertRaises((ValueError, RuntimeError)),
                ):
                    driver.state()
                self.assertEqual(desktop.tags, desktop.unloaded)
                self.assertFalse(list((Path(directory) / "runtime").glob("*.js")))
        for payload in (
            [],
            {"cursor": {"x": True, "y": 1}, "windows": []},
            {"cursor": {"x": 0, "y": 1}, "windows": [{"id": "invalid"}]},
        ):
            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as directory,
            ):
                driver, desktop = self.create(directory)
                desktop.invalid_state = payload
                with (
                    patch.object(kwin_driver, "execute", side_effect=desktop),
                    self.assertRaisesRegex(RuntimeError, "state"),
                ):
                    driver.state()
                self.assertEqual(desktop.tags, desktop.unloaded)
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.invalid_json = True
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                self.assertRaises(ValueError),
            ):
                driver.state()
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.silent = True
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "monotonic", side_effect=[0, 11]),
                self.assertRaisesRegex(RuntimeError, "no tagged"),
            ):
                driver.state()
            self.assertEqual(desktop.tags, desktop.unloaded)

    def test_placement_assigns_fresh_rectangle_and_waits_for_configure_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "sleep") as sleep,
            ):
                window = driver.place_window(140, 70, 1280, 996)
            self.assertEqual(
                window["frame"], {"x": 140, "y": 70, "width": 1280, "height": 996}
            )
            self.assertEqual(
                window["client"], {"x": 140, "y": 106, "width": 1280, "height": 960}
            )
            self.assertFalse(window["fullScreen"])
            self.assertFalse(window["noBorder"])
            self.assertTrue(desktop.focused)
            self.assertFalse(desktop.maximized)
            sleep.assert_called_once_with(0.1)
            self.assertEqual(len(list(Path(directory).glob("placement-*.json"))), 1)

    def test_placement_rejects_missing_decorations_instead_of_calibrating_an_offset(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            desktop.decorated = False
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "sleep"),
                self.assertRaisesRegex(RuntimeError, "decoration"),
            ):
                driver.place_window(140, 70, 1280, 996)

    def test_legitimate_rootful_x11_surface_can_preserve_undecorated_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, desktop = self.create(directory)
            self.assertTrue(driver.require_decoration)
            desktop.decorated = False
            driver.require_decoration = False
            with (
                patch.object(kwin_driver, "execute", side_effect=desktop),
                patch.object(kwin_driver.time, "sleep"),
            ):
                window = driver.place_window(140, 70, 1280, 996)
            self.assertEqual(window["client"], window["frame"])
            self.assertEqual(
                window["frame"], {"x": 140, "y": 70, "width": 1280, "height": 996}
            )
            self.assertFalse(window["fullScreen"])
            self.assertFalse(window["noBorder"])
            self.assertEqual(desktop.tags, desktop.unloaded)

    def test_placement_allows_one_device_pixel_quantization_and_returns_actual_geometry(
        self,
    ):
        for offset in (-0.8, 0.8, 0.8001):
            with (
                self.subTest(offset=offset),
                tempfile.TemporaryDirectory() as directory,
            ):
                driver, _ = self.create(directory)
                driver.output = {"scale": 1.25}
                actual = {
                    key: value + offset
                    for key, value in {
                        "x": 140,
                        "y": 70,
                        "width": 1280,
                        "height": 996,
                    }.items()
                }
                window = {
                    "id": "quantized",
                    "frame": actual,
                    "client": {
                        **actual,
                        "y": actual["y"] + 36,
                        "height": actual["height"] - 36,
                    },
                    "fullScreen": False,
                    "noBorder": False,
                }
                with (
                    patch.object(driver, "state", return_value={"windows": [window]}),
                    patch.object(kwin_driver.time, "monotonic", side_effect=[0, 16]),
                ):
                    if offset > 0.8:
                        with self.assertRaisesRegex(RuntimeError, "acknowledge"):
                            driver.place_window(140, 70, 1280, 996)
                        self.assertEqual(
                            list(Path(directory).glob("placement-*.json")), []
                        )
                    else:
                        placed = driver.place_window(140, 70, 1280, 996)
                        self.assertEqual(placed["frame"], actual)
                        evidence = json.loads(
                            next(Path(directory).glob("placement-*.json")).read_text()
                        )
                        self.assertEqual(evidence["requested"]["y"], 70)
                        self.assertEqual(evidence["window"]["frame"]["y"], 70 + offset)

    def test_placement_rejects_invalid_or_nonunique_child_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, _ = self.create(directory)
            with self.assertRaisesRegex(RuntimeError, "rectangle"):
                driver.place_window(1, 2, 0, 5)
            for windows in ([], [{}, {}]):
                with (
                    self.subTest(windows=windows),
                    patch.object(driver, "state", return_value={"windows": windows}),
                    self.assertRaisesRegex(RuntimeError, "exactly one"),
                ):
                    driver.place_window(1, 2, 3, 4)
            valid = {
                "id": "first",
                "frame": {"x": 0, "y": 0, "width": 3, "height": 4},
                "client": {"x": 0, "y": 1, "width": 3, "height": 3},
                "fullScreen": False,
                "noBorder": False,
            }
            states = [{"windows": [valid]}] * 3 + [
                {"windows": [{**valid, "id": "replacement"}]}
            ]
            with (
                patch.object(driver, "state", side_effect=states),
                self.assertRaisesRegex(RuntimeError, "child changed"),
            ):
                driver.place_window(1, 2, 3, 4)
            with (
                patch.object(driver, "state", return_value={"windows": [valid]}),
                patch.object(kwin_driver.time, "monotonic", side_effect=[0, 16]),
                self.assertRaisesRegex(RuntimeError, "acknowledge"),
            ):
                driver.place_window(1, 2, 3, 4)


class LayoutTests(unittest.TestCase):
    def test_multiple_transformed_or_incomplete_output_geometry_is_rejected(self):
        variants = []
        value = display()
        value["outputs"] = []
        variants.append(value)
        value = display()
        value["outputs"] = [value["outputs"][0], copy.deepcopy(value["outputs"][0])]
        variants.append(value)
        for field, invalid in (
            ("rotation", 2),
            ("transform", 1),
            ("name", "invalid.name"),
            ("scale", True),
            ("currentModeId", "unknown"),
            ("pos", {"x": None, "y": 0}),
        ):
            value = display()
            value["outputs"][0][field] = invalid
            variants.append(value)
        value = display()
        value["outputs"][0]["modes"].append(
            copy.deepcopy(value["outputs"][0]["modes"][0])
        )
        variants.append(value)
        value = display()
        value["outputs"][0]["modes"][0]["size"]["width"] = 0
        variants.append(value)
        value = display()
        value["screen"]["currentSize"]["height"] = 0
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                kwin_driver.output_layout(value)


if __name__ == "__main__":
    unittest.main()
