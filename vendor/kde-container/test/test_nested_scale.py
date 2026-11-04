"""Check the public XRandR ABI boundary used for an owned nested frontend."""

import contextlib
import ctypes
import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SOURCE = Path(
    os.environ.get(
        "QA_SCALE_SOURCE",
        Path(__file__).resolve().parents[1] / "libexec/plasma-remote-output",
    )
)
LOADER = importlib.machinery.SourceFileLoader("scale_output", str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
OUTPUT = importlib.util.module_from_spec(SPEC)
with patch.dict(os.environ, {"DISPLAY_BACKEND": "x11"}, clear=True):
    LOADER.exec_module(OUTPUT)

ENVIRONMENT = {
    "DISPLAY_BACKEND": "wayland",
    "DISPLAY_MODE": "nested",
    "HOST_DISPLAY_BACKEND": "wayland",
    "PLASMA_SOFTWARE_NESTED": "1",
    "PLASMA_SOFTWARE_CAPTURE": "0",
    "PLASMA_SOFTWARE_DISPLAY": ":110",
    "DISPLAY_WIDTH": "1234",
    "DISPLAY_HEIGHT": "914",
}


class RandR:
    def __init__(
        self,
        *,
        scale=1.25,
        sizes=None,
        resources=True,
        transform=True,
        active=(7,),
        property_record="ready",
        property_type=6,
        property_format=32,
        trailing=0,
        server_record="initial",
        server_atom=True,
        server_type=6,
        server_format=32,
        server_trailing=0,
    ):
        self.scale, self.pixels = scale, [1995, 1426]
        self.initial_scale = scale
        self.property = property_record
        self.server_record = server_record
        self.sizes = sizes if sizes is not None else [[1543, 1143]]
        self.references = []
        self.active = active
        self.x11 = SimpleNamespace(
            **{
                name: Mock(return_value=1)
                for name in (
                    "XOpenDisplay",
                    "XCloseDisplay",
                    "XDefaultRootWindow",
                    "XFree",
                    "XDisplayWidth",
                    "XDisplayHeight",
                    "XInternAtom",
                    "XGetWindowProperty",
                    "XChangeProperty",
                )
            }
        )
        self.randr = SimpleNamespace(
            **{
                name: Mock(return_value=1)
                for name in (
                    "XRRGetScreenResourcesCurrent",
                    "XRRFreeScreenResources",
                    "XRRGetCrtcTransform",
                    "XRRGetScreenInfo",
                    "XRRConfigSizes",
                    "XRRFreeScreenConfigInfo",
                    "XRRSetScreenConfig",
                    "XRRGetCrtcInfo",
                    "XRRFreeCrtcInfo",
                )
            }
        )

        def atom_for(_display, name, _only_if_exists):
            if name == b"_XWAYLAND_ROOTFUL_SCALE":
                return 2 if server_atom else 0
            assert name == b"_PLASMA_CONTAINER_STARTUP_SCALE"
            return 1

        self.x11.XInternAtom.side_effect = atom_for
        self.x11.XDisplayWidth.side_effect = lambda *_args: self.pixels[0]
        self.x11.XDisplayHeight.side_effect = lambda *_args: self.pixels[1]

        def screen_resources(*_args):
            if not resources:
                return None
            value = self.randr.XRRGetScreenResourcesCurrent.restype._type_()
            value.ncrtc = value.noutput = 2
            value.crtcs = (ctypes.c_ulong * 2)(7, 8)
            self.references.append(value)
            return ctypes.pointer(value)

        def crtc_info(_display, _resources, crtc):
            value = self.randr.XRRGetCrtcInfo.restype._type_()
            if crtc in self.active:
                value.width, value.height = self.pixels
                value.mode, value.noutput = 1, 1
            self.references.append(value)
            return ctypes.pointer(value)

        def crtc_transform(_display, _crtc, result):
            if not transform:
                return 0
            value = result._obj._type_()
            for axis in range(3):
                value.currentTransform.matrix[axis][axis] = int(
                    (self.scale if axis < 2 else 1) * 65536
                )
            result._obj.contents = value
            self.references.append(value)
            return 1

        def screen_sizes(_config, count):
            count._obj.value = len(self.sizes)
            struct = self.randr.XRRConfigSizes.restype._type_
            values = (struct * len(self.sizes))()
            for value, (width, height) in zip(values, self.sizes, strict=True):
                value.width, value.height = width, height
            self.references.append(values)
            return values

        def set_config(_display, _config, _root, index, _rotation, _time):
            self.pixels = self.sizes[index]
            # RandR's old rrSetConfig fallback calls RRCrtcNotify with NULL.
            self.scale = 1
            return 0

        def get_property(
            _display,
            _root,
            _atom,
            _offset,
            _length,
            _delete,
            _requested,
            actual_type,
            actual_format,
            count,
            after,
            result,
        ):
            server = _atom == 2
            values = self.server_record if server else self.property
            if values == "initial":
                values = [round(self.initial_scale * 120)]
            if values == "ready":
                values = [
                    round(self.initial_scale * 120),
                    int(os.environ["DISPLAY_WIDTH"]),
                    int(os.environ["DISPLAY_HEIGHT"]),
                    1,
                ]
            if values is None:
                return 0
            data = (ctypes.c_ulong * len(values))(*values)
            self.references.append(data)
            actual_type._obj.value = server_type if server else property_type
            actual_format._obj.value = server_format if server else property_format
            count._obj.value = len(values)
            after._obj.value = server_trailing if server else trailing
            result._obj.value = ctypes.cast(data, ctypes.c_void_p).value
            return 0

        def change_property(
            _display, _root, _atom, property_type, format_bits, mode, data, count
        ):
            assert _atom == 1, (
                "The controller must never publish server-owned scale metadata"
            )
            assert (property_type, format_bits, mode, count) == (6, 32, 0, 4)
            self.property = list(
                ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:count]
            )
            return 1

        self.x11.XGetWindowProperty.side_effect = get_property
        self.x11.XChangeProperty.side_effect = change_property
        self.randr.XRRGetScreenResourcesCurrent.side_effect = screen_resources
        self.randr.XRRGetCrtcInfo.side_effect = crtc_info
        self.randr.XRRGetCrtcTransform.side_effect = crtc_transform
        self.randr.XRRConfigSizes.side_effect = screen_sizes
        self.randr.XRRSetScreenConfig.side_effect = set_config

    def library(self, name):
        return {"libX11.so.6": self.x11, "libXrandr.so.2": self.randr}[name]


class NestedScaleTests(unittest.TestCase):
    def test_initial_identity_cannot_be_prepared_before_the_surface_acknowledgement(
        self,
    ):
        for server in ({"server_atom": False}, {"server_record": None}):
            driver = RandR(scale=1, property_record=None, **server)
            driver.pixels = [1234, 914]
            with (
                self.subTest(server=server),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
                self.assertRaises(ValueError),
            ):
                OUTPUT.frontend_scale(prepare=True)
            driver.x11.XChangeProperty.assert_not_called()
            self.assertIsNone(driver.property)

    def test_surface_acknowledgement_is_authoritative_after_identity_normalization(
        self,
    ):
        for numerator in (120, 150, 240):
            scale = numerator / 120
            pixels = [int(1234 * scale + 0.5), int(914 * scale + 0.5)]
            driver = RandR(
                scale=1, property_record=None, server_record=[numerator], sizes=[pixels]
            )
            driver.pixels = [1234, 914]
            with (
                self.subTest(numerator=numerator),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
            ):
                if numerator != 120:
                    with self.assertRaisesRegex(ValueError, "Waiting for"):
                        OUTPUT.frontend_scale(prepare=True)
                    self.assertEqual(driver.property, [numerator, 1234, 914, 0])
                self.assertEqual(OUTPUT.frontend_scale(prepare=True), scale)
                self.assertEqual(driver.property, [numerator, 1234, 914, 1])
                self.assertEqual(OUTPUT.frontend_scale(), scale)
            self.assertEqual(driver.pixels, pixels)

    def test_malformed_surface_acknowledgement_never_publishes_a_startup_latch(self):
        for options in (
            {"server_record": [0]},
            {"server_record": []},
            {"server_record": [150, 240]},
            {"server_record": [2**32 - 1]},
            {"server_type": 0},
            {"server_type": 31},
            {"server_format": 16},
            {"server_trailing": 4},
        ):
            driver = RandR(scale=1, property_record=None, **options)
            driver.pixels = [1234, 914]
            with (
                self.subTest(options=options),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
                self.assertRaises(ValueError),
            ):
                OUTPUT.frontend_scale(prepare=True)
            driver.x11.XChangeProperty.assert_not_called()

    def test_changed_surface_acknowledgement_requires_preparation_again(self):
        driver = RandR(
            scale=1,
            property_record=[150, 1234, 914, 1],
            server_record=[240],
            sizes=[[2468, 1828]],
        )
        driver.pixels = [1543, 1143]
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("ctypes.CDLL", side_effect=driver.library),
        ):
            with self.assertRaises(ValueError):
                OUTPUT.frontend_scale()
            driver.x11.XChangeProperty.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Waiting for"):
                OUTPUT.frontend_scale(prepare=True)
            self.assertEqual(OUTPUT.frontend_scale(prepare=True), 2)
        self.assertEqual(driver.property, [240, 1234, 914, 1])

    def test_nonidentity_transform_cannot_disagree_with_surface_acknowledgement(self):
        driver = RandR(scale=2, property_record=None, server_record=[150])
        driver.pixels = [1234, 914]
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("ctypes.CDLL", side_effect=driver.library),
            self.assertRaises(ValueError),
        ):
            OUTPUT.frontend_scale(prepare=True)
        driver.x11.XChangeProperty.assert_not_called()

    def test_real_controller_applies_scale_before_the_application_can_start(self):
        from test_display_runtime import APP

        class Started(Exception):
            pass

        driver = RandR(
            scale=1, property_record=[150, 1234, 914, 1], server_record=[150]
        )
        remote = {
            "id": 3,
            "name": "Virtual-Plasma",
            "enabled": True,
            "pos": {"x": 0, "y": 0},
            "scale": 1,
            "currentModeId": "1",
            "modes": [{"id": "1", "size": {"width": 1543, "height": 1143}}],
        }
        now, configuring, checks, commands = 0, False, [], []

        def run(arguments, **_options):
            if arguments == ["/usr/local/libexec/plasma-wait", "compositor"]:
                return subprocess.CompletedProcess(arguments, 0)
            if arguments == ["/usr/local/libexec/plasma-remote-output", "--check"]:
                ready = OUTPUT.configured([remote])
                checks.append(ready)
                return subprocess.CompletedProcess(arguments, 0 if ready else 1)
            self.assertEqual(arguments[0], "kscreen-doctor")
            commands.append(arguments)
            if arguments[1:] == ["-j"]:
                return SimpleNamespace(stdout=json.dumps({"outputs": [remote]}))
            for argument in arguments[1:]:
                if argument.startswith("output.3.scale."):
                    remote["scale"] = float(argument.removeprefix("output.3.scale."))
            return SimpleNamespace()

        def sleep(_seconds):
            nonlocal now, configuring
            if configuring:
                now += 31
            else:
                configuring = True
                try:
                    runpy.run_path(str(SOURCE), run_name="__main__")
                finally:
                    configuring = False

        def start(arguments, **_options):
            self.assertEqual(arguments, ["/bin/true"])
            self.assertEqual(checks, [False, True])
            self.assertEqual(remote["scale"], 1.25)
            self.assertTrue(OUTPUT.configured([remote]))
            raise Started

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {**ENVIRONMENT, "TARGET_APP": "/bin/true"}, clear=True
            ),
            patch.object(sys, "argv", [str(SOURCE)]),
            patch.object(APP, "STATE", Path(directory) / "state.json"),
            patch.object(APP.signal, "signal"),
            patch("time.monotonic", side_effect=lambda: now),
            patch("time.sleep", side_effect=sleep),
            patch("ctypes.CDLL", side_effect=driver.library),
            patch("subprocess.run", side_effect=run),
            patch("subprocess.Popen", side_effect=start) as popen,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(Started),
        ):
            APP.main()
        popen.assert_called_once()
        self.assertTrue(any("output.3.scale.1.25" in command for command in commands))

    def test_rr11_identity_reset_preserves_scale_across_new_queries_and_outer_resize(
        self,
    ):
        for scale, pixels in ((1.25, [1543, 1143]), (2, [2468, 1828])):
            driver = RandR(scale=scale, sizes=[pixels], property_record=None)
            with (
                self.subTest(scale=scale),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
            ):
                with self.assertRaisesRegex(ValueError, "Waiting for"):
                    OUTPUT.frontend_scale(prepare=True)
                self.assertEqual(driver.scale, 1)
                self.assertEqual(driver.property, [round(scale * 120), 1234, 914, 0])
                with self.assertRaisesRegex(ValueError, "not ready"):
                    OUTPUT.frontend_scale()
                self.assertEqual(OUTPUT.frontend_scale(prepare=True), scale)
                self.assertEqual(driver.property, [round(scale * 120), 1234, 914, 1])
                self.assertEqual(OUTPUT.frontend_scale(), scale)
                driver.pixels = [pixels[0] + 80, pixels[1] + 40]
                self.assertEqual(OUTPUT.frontend_scale(), scale)
                driver.property = None  # A restarted X server has no property.
                with self.assertRaisesRegex(ValueError, "not been prepared"):
                    OUTPUT.frontend_scale()

    def test_missing_pending_or_malformed_server_metadata_never_reports_ready(self):
        for options in (
            {"property_record": None},
            {"property_record": [150, 1234, 914, 0]},
            {"property_record": [150, 1234, 914]},
            {"property_record": [150, 1234, 914, 1, 0]},
            {"property_record": [150, 1235, 914, 1]},
            {"property_record": [150, 1234, 915, 1]},
            {"property_record": [0, 1234, 914, 1]},
            {"property_record": [2**32 - 1, 1234, 914, 1]},
            {"property_record": [150, 1234, 914, 2]},
            {"property_type": 31},
            {"property_type": 0},
            {"property_format": 16},
            {"trailing": 4},
        ):
            driver = RandR(**options)
            with (
                self.subTest(options=options),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
                self.assertRaises(ValueError),
            ):
                OUTPUT.frontend_scale()
            driver.x11.XChangeProperty.assert_not_called()
            self.assertEqual(
                driver.x11.XFree.call_count,
                2 if options.get("property_record", "ready") is None else 3,
            )
            driver.x11.XCloseDisplay.assert_called_once()

    def test_changed_nonidentity_hint_rejects_the_latched_startup_scale(self):
        driver = RandR(scale=2, property_record=[150, 1234, 914, 1])
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("ctypes.CDLL", side_effect=driver.library),
            self.assertRaisesRegex(ValueError, "scale changed"),
        ):
            OUTPUT.frontend_scale()

    def test_inactive_entries_are_skipped_and_exactly_one_active_output_is_required(
        self,
    ):
        for active in ((), (7,), (8,), (7, 8)):
            driver = RandR(active=active)
            with (
                self.subTest(active=active),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
            ):
                if len(active) == 1:
                    self.assertEqual(OUTPUT.frontend_scale(), 1.25)
                    self.assertEqual(
                        driver.randr.XRRGetCrtcTransform.call_args.args[1], active[0]
                    )
                else:
                    with self.assertRaisesRegex(ValueError, "one active output"):
                        OUTPUT.frontend_scale()
                    driver.randr.XRRGetCrtcTransform.assert_not_called()
            self.assertEqual(driver.randr.XRRFreeCrtcInfo.call_count, 2)
            driver.randr.XRRFreeScreenResources.assert_called_once()
            driver.x11.XCloseDisplay.assert_called_once()

    def test_frontend_open_uses_its_own_authority_and_restores_the_guest_context(self):
        for authority in (None, "/run/plasma/xauth_guest"):
            for open_failure in (False, True):
                driver = RandR()
                seen = []

                def open_display(name, *, seen=seen, open_failure=open_failure):
                    seen.append(os.environ.get("XAUTHORITY"))
                    self.assertEqual(name, b":110")
                    return (
                        0 if open_failure or seen[-1] != "/etc/plasma/Xauthority" else 1
                    )

                driver.x11.XOpenDisplay.side_effect = open_display
                environment = dict(ENVIRONMENT)
                if authority is not None:
                    environment["XAUTHORITY"] = authority
                with (
                    self.subTest(authority=authority, open_failure=open_failure),
                    patch.dict(os.environ, environment, clear=True),
                    patch("ctypes.CDLL", side_effect=driver.library),
                ):
                    if open_failure:
                        with self.assertRaisesRegex(
                            ValueError, "display is unavailable"
                        ):
                            OUTPUT.frontend_scale()
                    else:
                        self.assertEqual(OUTPUT.frontend_scale(), 1.25)
                    self.assertEqual(os.environ.get("XAUTHORITY"), authority)
                    self.assertEqual(seen, ["/etc/plasma/Xauthority"])
                if open_failure:
                    driver.x11.XCloseDisplay.assert_not_called()
                else:
                    driver.x11.XCloseDisplay.assert_called_once()

    def test_fixed_point_scale_restores_protocol_precision_before_pixel_rounding(self):
        for scale in (1.2, 1.15, 7 / 6, 4 / 3):
            driver = RandR(scale=scale)
            with (
                self.subTest(scale=scale),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
            ):
                self.assertEqual(OUTPUT.frontend_scale(), scale)
        # 1230 * 1.15 is exactly the positive half-pixel boundary 1414.5.
        # Using truncated RandR 16.16 directly would round down to 1414.
        driver = RandR(scale=1.15, sizes=[[1415, 1051]])
        with (
            patch.dict(
                os.environ, {**ENVIRONMENT, "DISPLAY_WIDTH": "1230"}, clear=True
            ),
            patch("ctypes.CDLL", side_effect=driver.library),
        ):
            with self.assertRaisesRegex(ValueError, "Waiting for"):
                OUTPUT.frontend_scale(prepare=True)
            self.assertEqual(OUTPUT.frontend_scale(prepare=True), 1.15)
        self.assertEqual(driver.pixels, [1415, 1051])

    def test_scale_cli_preserves_the_canonical_qreal_for_krfb(self):
        for scale in (1.2, 4 / 3):
            driver = RandR(scale=scale)
            output = io.StringIO()
            with (
                self.subTest(scale=scale),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch.object(sys, "argv", [str(SOURCE), "--scale"]),
                patch("ctypes.CDLL", side_effect=driver.library),
                patch(
                    "subprocess.run",
                    side_effect=AssertionError("Scale query must not run KScreen"),
                ),
                contextlib.redirect_stdout(output),
                self.assertRaises(SystemExit) as exited,
            ):
                runpy.run_path(str(SOURCE), run_name="__main__")
            self.assertEqual(exited.exception.code, 0)
            self.assertEqual(float(output.getvalue()), scale)

    def test_custom_dimensions_use_real_global_mode_and_release_every_resource(self):
        driver = RandR()
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("ctypes.CDLL", side_effect=driver.library),
        ):
            with self.assertRaisesRegex(ValueError, "Waiting for"):
                OUTPUT.frontend_scale(prepare=True)
            self.assertEqual(OUTPUT.frontend_scale(prepare=True), 1.25)
        self.assertEqual(driver.pixels, [1543, 1143])
        driver.randr.XRRSetScreenConfig.assert_called_once_with(1, 1, 1, 0, 1, 0)
        self.assertEqual(driver.x11.XCloseDisplay.call_count, 2)
        self.assertEqual(driver.randr.XRRFreeScreenResources.call_count, 2)
        self.assertEqual(driver.randr.XRRFreeCrtcInfo.call_count, 4)
        self.assertEqual(driver.x11.XFree.call_count, 6)
        driver.randr.XRRFreeScreenConfigInfo.assert_called_once_with(1)

    def test_missing_mode_is_rejected_instead_of_substituting_other_dimensions(self):
        driver = RandR(sizes=[[1280, 960]])
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch("ctypes.CDLL", side_effect=driver.library),
            self.assertRaisesRegex(ValueError, "Cannot initialize"),
        ):
            OUTPUT.frontend_scale(prepare=True)
        driver.randr.XRRSetScreenConfig.assert_not_called()
        driver.x11.XCloseDisplay.assert_called_once()
        driver.randr.XRRFreeScreenConfigInfo.assert_called_once()

    def test_missing_resources_or_transform_never_fall_back_to_scale_one(self):
        for options in (
            {"resources": False},
            {"transform": False},
            {"scale": 0},
            {"scale": 1.2345},
        ):
            driver = RandR(**options)
            with (
                self.subTest(options=options),
                patch.dict(os.environ, ENVIRONMENT, clear=True),
                patch("ctypes.CDLL", side_effect=driver.library),
                self.assertRaises(ValueError),
            ):
                OUTPUT.frontend_scale()
            driver.x11.XCloseDisplay.assert_called_once()

    def test_x11_guest_host_x11_and_legacy_capture_do_not_open_a_scaled_frontend(self):
        for key, value in (
            ("DISPLAY_BACKEND", "x11"),
            ("HOST_DISPLAY_BACKEND", "x11"),
            ("PLASMA_SOFTWARE_CAPTURE", "1"),
            ("PLASMA_SOFTWARE_NESTED", "0"),
        ):
            with (
                self.subTest(key=key),
                patch.dict(os.environ, {**ENVIRONMENT, key: value}, clear=True),
                patch("ctypes.CDLL") as library,
            ):
                self.assertEqual(OUTPUT.frontend_scale(prepare=True), 1)
                library.assert_not_called()
