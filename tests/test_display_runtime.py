"""Regressions for the local policy changes to the vendored display runtime."""

import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "vendor/kde-container/libexec/plasma-target-app"
)
LOADER = importlib.machinery.SourceFileLoader("plasma_target_app", str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
APP = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(APP)
REMOTE_LOADER = importlib.machinery.SourceFileLoader(
    "plasma_remote_output", str(SOURCE.parent / "plasma-remote-output")
)
REMOTE_SPEC = importlib.util.spec_from_loader(REMOTE_LOADER.name, REMOTE_LOADER)
REMOTE = importlib.util.module_from_spec(REMOTE_SPEC)
with patch.dict(os.environ, {"DISPLAY_BACKEND": "x11"}):
    REMOTE_LOADER.exec_module(REMOTE)
GPU_LOADER = importlib.machinery.SourceFileLoader(
    "plasma_gpu", str(SOURCE.parent / "plasma-gpu")
)
GPU_SPEC = importlib.util.spec_from_loader(GPU_LOADER.name, GPU_LOADER)
GPU = importlib.util.module_from_spec(GPU_SPEC)
GPU_LOADER.exec_module(GPU)


class RenderingPolicyTests(unittest.TestCase):
    def test_debian_nvidia_remote_fallback_does_not_override_source_compositor(self):
        vendor = Mock()
        for overlay, backend, mode, distro, version, pci, expected in (
            (False, "wayland", "vnc", "debian", "13", "0x10de", True),
            (False, "wayland", "web", "debian", "13", "0x10de", True),
            (True, "wayland", "vnc", "debian", "13", "0x10de", False),
            (False, "wayland", "headless", "debian", "13", "0x10de", False),
            (False, "wayland", "nested", "debian", "13", "0x10de", False),
            (False, "x11", "vnc", "debian", "13", "0x10de", False),
            (False, "wayland", "vnc", "ubuntu", "26.04", "0x10de", False),
            (False, "wayland", "vnc", "debian", "13", "0x1002", False),
        ):
            vendor.read_text.return_value = pci + "\n"
            with (
                self.subTest(
                    overlay=overlay, backend=backend, mode=mode, distro=distro, pci=pci
                ),
                patch.object(GPU.Path, "is_file", return_value=overlay),
                patch.object(GPU.Path, "glob", return_value=[vendor]),
                patch.object(
                    GPU.platform,
                    "freedesktop_os_release",
                    return_value={"ID": distro, "VERSION_ID": version},
                ),
            ):
                self.assertEqual(GPU.requires_software(backend, mode), expected)

    def test_matching_kscreen_uses_private_qt_without_changing_application_environment(
        self,
    ):
        environment = {
            "PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/vendor/lib",
            "QML2_IMPORT_PATH": "/usr/lib/qt6/qml",
            "WAYLAND_DISPLAY": "wayland-0",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(Path, "is_file", return_value=True),
            patch.object(REMOTE.subprocess, "run") as run,
        ):
            REMOTE.kscreen("-j", timeout=5)
            self.assertEqual(
                run.call_args.args[0], ["/opt/kwin-6.8.2/bin/kscreen-doctor", "-j"]
            )
            child = run.call_args.kwargs["env"]
            self.assertEqual(child["QT_PLUGIN_PATH"], "/opt/kwin-6.8.2/plugins")
            self.assertEqual(
                child["LD_LIBRARY_PATH"], "/opt/kwin-6.8.2/lib:/vendor/lib"
            )
            self.assertNotIn("QML2_IMPORT_PATH", child)
            self.assertEqual(dict(os.environ), environment)

    def test_distro_kscreen_keeps_its_environment(self):
        with (
            patch.object(Path, "is_file", return_value=False),
            patch.object(REMOTE.subprocess, "run") as run,
        ):
            REMOTE.kscreen("-j", timeout=5)
            self.assertEqual(run.call_args.args[0], ["kscreen-doctor", "-j"])
            self.assertEqual(run.call_args.kwargs["env"], dict(os.environ))

    def test_kiosk_x11_does_not_require_plasma_desktop(self):
        module = SOURCE.parents[1] / "init.d/modules/400-plasma.sh"
        with tempfile.TemporaryDirectory() as directory:
            for kwin, desktop, kiosk, expected in (
                (True, False, True, 0),
                (True, False, False, 1),
                (True, True, False, 0),
                (False, True, True, 1),
            ):
                for name, enabled in (("kwin_x11", kwin), ("startplasma-x11", desktop)):
                    path = Path(directory) / name
                    if enabled:
                        path.touch(mode=0o755)
                    else:
                        path.unlink(missing_ok=True)
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        'source "$1"; plasma_x11_available',
                        "check",
                        str(module),
                    ],
                    env={
                        **os.environ,
                        "PATH": directory,
                        "TARGET_APP": "wine" if kiosk else "",
                    },
                    check=False,
                )
                with self.subTest(kwin=kwin, desktop=desktop, kiosk=kiosk):
                    self.assertEqual(result.returncode, expected)

    def test_application_does_not_inherit_compositor_qt_stack(self):
        environment = {
            "PATH": "/opt/kwin-6.8.2/bin:/usr/bin",
            "PLASMA_APP_PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/opt/kwin-6.8.2/lib:/vendor/lib",
            "PLASMA_APP_LD_LIBRARY_PATH": "/vendor/lib",
            "QT_PLUGIN_PATH": "/opt/kwin-6.8.2/plugins",
            "PLASMA_APP_QT_PLUGIN_PATH": "",
            "QML_IMPORT_PATH": "/opt/kwin-6.8.2/qml",
            "PLASMA_APP_QML_IMPORT_PATH": "/custom/qml",
            "QML2_IMPORT_PATH": "/opt/kwin-6.8.2/qml",
            "PLASMA_APP_QML2_IMPORT_PATH": "",
            "XDG_CACHE_HOME": "/home/plasma/.cache/kwin-6.8.2",
            "PLASMA_APP_XDG_CACHE_HOME": "",
            "XDG_DATA_DIRS": "/opt/kwin-6.8.2/share:/usr/share",
            "PLASMA_APP_XDG_DATA_DIRS": "/usr/share",
            "PLASMA_KWIN_BINARY": "/opt/kwin-6.8.2/bin/kwin_wayland",
            "DISPLAY": ":100",
        }
        with patch.dict(os.environ, environment, clear=True):
            child = APP.application_environment()
            self.assertEqual(child["PATH"], "/usr/bin")
            self.assertEqual(child["LD_LIBRARY_PATH"], "/vendor/lib")
            self.assertEqual(child["QML_IMPORT_PATH"], "/custom/qml")
            self.assertEqual(child["XDG_DATA_DIRS"], "/usr/share")
            self.assertEqual(child["DISPLAY"], ":100")
            self.assertNotIn("QT_PLUGIN_PATH", child)
            self.assertNotIn("QML2_IMPORT_PATH", child)
            self.assertNotIn("XDG_CACHE_HOME", child)
            self.assertFalse(any(name.startswith("PLASMA_APP_") for name in child))
            self.assertNotIn("PLASMA_KWIN_BINARY", child)
            self.assertEqual(
                os.environ["LD_LIBRARY_PATH"], environment["LD_LIBRARY_PATH"]
            )

    def test_application_without_overlay_keeps_its_environment(self):
        with patch.dict(
            os.environ, {"PATH": "/usr/bin", "DISPLAY": ":100"}, clear=True
        ):
            self.assertEqual(APP.application_environment(), dict(os.environ))

    def test_application_is_resolved_in_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "native"
            overlay = Path(directory) / "overlay"
            native.mkdir()
            overlay.mkdir()
            for root in (native, overlay):
                (root / "test-app").touch(mode=0o755)
            with patch.dict(
                os.environ,
                {
                    "PATH": str(overlay),
                    "PLASMA_APP_PATH": str(native),
                    "TARGET_APP": "test-app",
                },
                clear=True,
            ):
                self.assertEqual(APP.command(), [str(native / "test-app")])

    def test_overlay_without_screenlocker_omits_unsupported_flag(self):
        adapter = SOURCE.parent / "plasma-backend/kwin_wayland"
        with tempfile.TemporaryDirectory() as directory:
            intercept = Path(directory) / "intercept-exec"
            intercept.write_text('exec() { printf "%s\\n" "$@"; }\n')
            for overlay in (False, True):
                environment = {
                    **os.environ,
                    "BASH_ENV": str(intercept),
                    "DISPLAY_MODE": "headless",
                    "DISPLAY_WIDTH": "800",
                    "DISPLAY_HEIGHT": "600",
                    "PLASMA_KWIN_BINARY": "/opt/kwin-6.8.2/bin/kwin_wayland"
                    if overlay
                    else "",
                    "PLASMA_SOFTWARE_CAPTURE": "0",
                    "PLASMA_NVIDIA_SYNC": "0",
                }
                result = subprocess.run(
                    ["/bin/bash", str(adapter)],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                arguments = result.stdout.splitlines()
                with self.subTest(overlay=overlay):
                    self.assertEqual("--no-lockscreen" in arguments, not overlay)
                    self.assertIn("--virtual", arguments)
                    self.assertEqual(
                        arguments[0],
                        environment["PLASMA_KWIN_BINARY"] or "/usr/bin/kwin_wayland",
                    )


if __name__ == "__main__":
    unittest.main()
