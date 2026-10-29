"""Exercise GPU driver boundaries and output policy without accessing real GPUs."""

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

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    source = ROOT / "vendor/kde-container/libexec" / name
    loader = importlib.machinery.SourceFileLoader(
        "coverage_" + name.replace("-", "_"), str(source)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"DISPLAY_BACKEND": "x11"}):
        loader.exec_module(module)
    return module


GPU = load("plasma-gpu")
REMOTE = load("plasma-remote-output")


class GraphicsDriver:
    def __init__(self, failure=None, renderer="Test GPU", vendor="NVIDIA Corporation"):
        names = (
            "gbm_create_device",
            "gbm_device_destroy",
            "eglGetPlatformDisplay",
            "eglInitialize",
            "eglTerminate",
            "eglBindAPI",
            "eglChooseConfig",
            "eglCreateContext",
            "eglDestroyContext",
            "eglMakeCurrent",
            "eglGetProcAddress",
            "eglGetError",
        )
        self.functions = {name: Mock(return_value=1) for name in names}
        self.functions["eglGetError"].return_value = 0x3003
        buffers = {
            0x1F00: ctypes.create_string_buffer(vendor.encode()),
            0x1F01: ctypes.create_string_buffer(renderer.encode()),
        }

        @ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint)
        def get_string(name):
            if (failure == "GL_VENDOR" and name == 0x1F00) or (
                failure == "GL_RENDERER" and name == 0x1F01
            ):
                return 0
            return ctypes.addressof(buffers[name])

        self.callback = get_string
        self.functions["eglGetProcAddress"].return_value = ctypes.cast(
            get_string, ctypes.c_void_p
        ).value

        def choose(_display, _attributes, _config, _capacity, count):
            count._obj.value = 0 if failure == "no_configs" else 1
            return 0 if failure == "eglChooseConfig" else 1

        self.functions["eglChooseConfig"].side_effect = choose
        if failure in self.functions and failure != "eglChooseConfig":
            self.functions[failure].return_value = 0
        self.egl = SimpleNamespace(
            **{
                name: method
                for name, method in self.functions.items()
                if name.startswith("egl")
            }
        )
        self.gbm = SimpleNamespace(
            **{
                name: method
                for name, method in self.functions.items()
                if name.startswith("gbm")
            }
        )

    def library(self, name):
        return {"libEGL.so.1": self.egl, "libgbm.so.1": self.gbm}[name]


class GpuProbeTests(unittest.TestCase):
    def test_probe_distinguishes_hardware_from_software_and_closes_native_resources(
        self,
    ):
        for renderer, hardware in [
            ("Test GPU", True),
            *((name.upper(), False) for name in GPU.SOFTWARE_RENDERERS),
        ]:
            with (
                self.subTest(renderer=renderer),
                tempfile.NamedTemporaryFile() as device,
            ):
                driver = GraphicsDriver(renderer=renderer)
                with patch.object(GPU.ctypes, "CDLL", side_effect=driver.library):
                    result = GPU.probe(device.name)
                self.assertEqual(result["device"], device.name)
                self.assertEqual(result["hardware"], hardware)
                for function in (
                    "eglDestroyContext",
                    "eglTerminate",
                    "gbm_device_destroy",
                ):
                    driver.functions[function].assert_called_once()
                descriptor = driver.functions["gbm_create_device"].call_args.args[0]
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_each_failed_egl_stage_releases_only_resources_it_created(self):
        early = {"gbm_create_device", "eglGetPlatformDisplay", "eglInitialize"}
        context = {"eglMakeCurrent", "eglGetProcAddress", "GL_VENDOR", "GL_RENDERER"}
        for failure in (
            "gbm_create_device",
            "eglGetPlatformDisplay",
            "eglInitialize",
            "eglBindAPI",
            "eglChooseConfig",
            "no_configs",
            "eglCreateContext",
            "eglMakeCurrent",
            "eglGetProcAddress",
            "GL_VENDOR",
            "GL_RENDERER",
        ):
            with self.subTest(failure=failure), tempfile.NamedTemporaryFile() as device:
                driver = GraphicsDriver(failure)
                with (
                    patch.object(GPU.ctypes, "CDLL", side_effect=driver.library),
                    self.assertRaisesRegex(RuntimeError, "failed .*EGL 0x3003"),
                ):
                    GPU.probe(device.name)
                self.assertEqual(
                    driver.functions["gbm_device_destroy"].called,
                    failure != "gbm_create_device",
                )
                self.assertEqual(
                    driver.functions["eglTerminate"].called, failure not in early
                )
                self.assertEqual(
                    driver.functions["eglDestroyContext"].called, failure in context
                )
                descriptor = driver.functions["gbm_create_device"].call_args.args[0]
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_detection_continues_after_crashes_invalid_results_and_timeouts(self):
        nodes = [Path(f"/dev/dri/renderD{128 + index}") for index in range(6)]
        replies = [
            OSError("synthetic driver crash"),
            subprocess.TimeoutExpired("probe", 8),
            SimpleNamespace(stdout="not json", stderr="bad driver", returncode=23),
            SimpleNamespace(stdout="[]", stderr="", returncode=0),
            SimpleNamespace(
                stdout=json.dumps({"device": "wrong node"}), stderr="", returncode=0
            ),
            SimpleNamespace(
                stdout=json.dumps(
                    {
                        "device": str(nodes[-1]),
                        "hardware": True,
                        "vendor": "NVIDIA",
                        "renderer": "Test GPU",
                    }
                ),
                stderr="",
                returncode=0,
            ),
        ]
        with (
            patch.object(Path, "glob", return_value=nodes),
            patch.object(Path, "is_char_device", return_value=True),
            patch.object(GPU.subprocess, "run", side_effect=replies),
            contextlib.redirect_stderr(io.StringIO()) as diagnostic,
        ):
            devices = GPU.detect()
        self.assertEqual(len(devices), 6)
        self.assertTrue(devices[-1]["hardware"])
        self.assertTrue(
            all(
                not device["hardware"] and device.get("error")
                for device in devices[:-1]
            )
        )
        self.assertIn("bad driver", devices[2]["error"])
        self.assertIn("Test GPU", diagnostic.getvalue())
        with (
            patch.object(Path, "glob", return_value=nodes),
            patch.object(Path, "is_char_device", return_value=False),
            patch.object(GPU.subprocess, "run") as run,
        ):
            self.assertEqual(GPU.detect(), [])
        run.assert_not_called()

    def test_cli_reports_worker_errors_and_overall_hardware_availability(self):
        source = ROOT / "vendor/kde-container/libexec/plasma-gpu"
        with (
            patch.object(sys, "argv", [str(source), "/private/nonexistent"]),
            patch("ctypes.CDLL", side_effect=OSError("synthetic missing EGL")),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(source), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        self.assertFalse(json.loads(output.getvalue())["hardware"])
        for devices, status in (
            ([], 1),
            ([{"hardware": False}], 1),
            ([{"hardware": True}], 0),
        ):
            with (
                self.subTest(devices=devices),
                patch.object(sys, "argv", ["probe"]),
                patch.object(GPU, "detect", return_value=devices),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(GPU.main(), status)
                self.assertEqual(json.loads(output.getvalue())["devices"], devices)
        for required in (False, True):
            with (
                patch.object(
                    sys, "argv", ["probe", "--requires-software", "wayland", "vnc"]
                ),
                patch.object(GPU, "requires_software", return_value=required),
            ):
                self.assertEqual(GPU.main(), 0 if required else 1)


class RemoteOutputTests(unittest.TestCase):
    def test_output_discovery_requires_complete_ids_and_uses_priority_for_legacy_enabled(
        self,
    ):
        for items, valid in (
            ([{"name": "incomplete"}], False),
            ([{"id": 1, "name": "Virtual-Plasma", "priority": 1}], True),
        ):
            with (
                patch.object(
                    REMOTE,
                    "kscreen",
                    return_value=SimpleNamespace(stdout=json.dumps({"outputs": items})),
                ),
                self.subTest(valid=valid),
            ):
                if valid:
                    self.assertTrue(REMOTE.outputs()[0]["enabled"])
                else:
                    with self.assertRaisesRegex(ValueError, "complete output"):
                        REMOTE.outputs()

    def test_only_one_origin_monitor_is_valid_and_existing_capture_accepts_its_name(
        self,
    ):
        good = {
            "id": 1,
            "name": "Virtual-Plasma",
            "enabled": True,
            "pos": {"x": 0, "y": 0},
        }
        for items, environment, expected in (
            ([good], {}, True),
            ([], {}, False),
            ([good, good], {}, False),
            ([{**good, "name": "other"}], {}, False),
            ([{**good, "pos": {"x": 1, "y": 0}}], {}, False),
            ([{**good, "name": "other"}], {"PLASMA_SOFTWARE_CAPTURE": "1"}, True),
            ([{**good, "name": "other"}], {"PLASMA_VNC_EXISTING_OUTPUT": "1"}, True),
        ):
            with (
                self.subTest(items=items, environment=environment),
                patch.dict(os.environ, environment, clear=True),
            ):
                self.assertEqual(REMOTE.configured(items), expected)

    def invoke(self, replies, *, check=False, times=None):
        source = ROOT / "vendor/kde-container/libexec/plasma-remote-output"
        with (
            patch.dict(os.environ, {"DISPLAY_BACKEND": "wayland"}, clear=True),
            patch.object(sys, "argv", [str(source), *(["--check"] if check else [])]),
            patch.object(Path, "is_file", return_value=False),
            patch("subprocess.run", side_effect=replies) as run,
            patch("time.monotonic", side_effect=times)
            if times
            else patch("time.monotonic", return_value=0),
            patch("time.sleep"),
        ):
            runpy.run_path(str(source), run_name="__main__")
        return [call.args[0] for call in run.call_args_list]

    def test_configuration_activates_remote_origin_and_disables_only_other_active_outputs(
        self,
    ):
        remote = {
            "id": 2,
            "name": "Virtual-Plasma",
            "enabled": False,
            "pos": {"x": 20, "y": 0},
        }
        old = {"id": 1, "name": "old", "enabled": True, "pos": {"x": 0, "y": 0}}
        inactive = {"id": 3, "name": "off", "enabled": False}

        def outputs(items):
            return SimpleNamespace(stdout=json.dumps({"outputs": items}))

        commands = self.invoke(
            [
                outputs([remote, old, inactive]),
                SimpleNamespace(),
                outputs(
                    [
                        {**remote, "enabled": True, "pos": {"x": 0, "y": 0}},
                        {**old, "enabled": False},
                        inactive,
                    ]
                ),
            ]
        )
        self.assertEqual(
            commands[1],
            [
                "kscreen-doctor",
                "output.2.enable",
                "output.2.position.0,0",
                "output.2.priority.1",
                "output.1.disable",
            ],
        )
        self.assertEqual(commands[0], ["kscreen-doctor", "-j"])
        self.assertEqual(commands[-1], commands[0])

    def test_checks_fail_closed_and_polling_recovers_from_transient_errors(self):
        good = SimpleNamespace(
            stdout=json.dumps(
                {
                    "outputs": [
                        {
                            "id": 1,
                            "name": "Virtual-Plasma",
                            "enabled": True,
                            "pos": {"x": 0, "y": 0},
                        }
                    ]
                }
            )
        )
        for reply, expected in (
            (good, 0),
            (SimpleNamespace(stdout='{"outputs":[]}'), 1),
        ):
            with (
                self.subTest(expected=expected),
                self.assertRaises(SystemExit) as result,
            ):
                self.invoke([reply], check=True)
            self.assertEqual(result.exception.code, expected)
        self.assertEqual(
            len(self.invoke([subprocess.CalledProcessError(1, "kscreen"), good])), 2
        )
        with self.assertRaisesRegex(SystemExit, "Timed out configuring"):
            self.invoke([SimpleNamespace(stdout='{"outputs":[]}')], times=[0, 61])
        with self.assertRaises(SystemExit):
            self.invoke([SimpleNamespace(stdout="invalid json")], check=True)
        with self.assertRaises(SystemExit):
            self.invoke([subprocess.CalledProcessError(1, "kscreen")], times=[0, 61])


if __name__ == "__main__":
    unittest.main()
