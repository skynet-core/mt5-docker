"""Validate upstream display policies and the MT5 caller configuration."""

import configparser
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_mt5_images_request_maximization_for_normal_windows(self):
        root = Path(__file__).resolve().parents[1]
        for distro in ("fedora-44", "debian-13", "ubuntu-26.04", "archlinux"):
            with self.subTest(distro=distro):
                recipe = (root / f"Dockerfile.{distro}").read_text()
                self.assertIn("TARGET_APP=/usr/local/bin/mt5-launch", recipe)
                self.assertIn("TARGET_APP_WINDOW_MODE=maximized", recipe)

    def test_window_policy_is_generic_and_applies_only_to_normal_windows(self):
        for mode in (None, "fullscreen", "maximized"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                environment = {"XDG_CONFIG_HOME": directory}
                if mode is not None:
                    environment["TARGET_APP_WINDOW_MODE"] = mode
                with patch.dict(os.environ, environment, clear=True):
                    APP.configure()
                config = configparser.ConfigParser()
                config.read(Path(directory) / "kwinrulesrc")
                rule = config["1"]
                self.assertEqual(rule["types"], "1")
                self.assertEqual(rule["noborder"], "true")
                self.assertEqual(rule["noborderrule"], "2")
                if mode == "maximized":
                    self.assertNotIn("fullscreen", rule)
                    for axis in ("horiz", "vert"):
                        self.assertEqual(rule[f"maximize{axis}"], "true")
                        self.assertEqual(rule[f"maximize{axis}rule"], "2")
                else:
                    self.assertEqual(rule["fullscreen"], "true")
                    self.assertEqual(rule["fullscreenrule"], "2")
                    self.assertNotIn("maximizehoriz", rule)
                    self.assertNotIn("maximizevert", rule)

    def test_invalid_window_policy_is_rejected_by_the_real_validation_entrypoint(self):
        for mode in ("borderless", "", "maximized\n"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [str(SOURCE), "--validate"],
                    env={
                        **os.environ,
                        "TARGET_APP": "/bin/true",
                        "TARGET_APP_WINDOW_MODE": mode,
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("TARGET_APP_WINDOW_MODE", result.stderr)

    def test_x11_viewer_passes_shortcut_keys_through_old_and_new_versions(self):
        helper = SOURCE.parent / "plasma-nested-x11"
        with tempfile.TemporaryDirectory() as directory:
            viewer = Path(directory) / "viewer"
            viewer.write_text(
                '#!/bin/bash\nif [[ $1 == --help ]]; then printf "%s\\n" "$VIEWER_HELP"; '
                'else printf "%s\\n" "$@"; printf "DISPLAY=%s\\n" "$DISPLAY"; '
                'printf "SOFTWARE=%s\\n" "$LIBGL_ALWAYS_SOFTWARE"; fi\n'
            )
            viewer.chmod(0o755)
            for help_text, expected, software in (
                ("MenuKey", "-MenuKey=", "0"),
                ("MenuKey", "-MenuKey=", "1"),
                ("ShortcutModifiers", "-ShortcutModifiers=", "0"),
                ("ShortcutModifiers", "-ShortcutModifiers=", "1"),
            ):
                with self.subTest(version=help_text, software=software):
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            helper.read_text().replace(
                                "/usr/bin/vncviewer", str(viewer)
                            ),
                        ],
                        env={
                            **os.environ,
                            "VIEWER_HELP": help_text,
                            "LIBGL_ALWAYS_SOFTWARE": software,
                            "HOST_DISPLAY_BACKEND": "x11",
                            "HOST_DISPLAY": ":42",
                            "HOST_XAUTHORITY": "/tmp/host-auth",
                            "DISPLAY_WIDTH": "800",
                            "DISPLAY_HEIGHT": "600",
                        },
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    self.assertIn(expected, result.stdout.splitlines())
                    self.assertIn("DISPLAY=unix/:42", result.stdout.splitlines())
                    self.assertIn("SOFTWARE=" + software, result.stdout.splitlines())
                    self.assertIn("-AcceptClipboard=0", result.stdout.splitlines())
                    self.assertIn("127.0.0.1::5900", result.stdout.splitlines())

    def test_software_x11_relay_uses_virtual_kwin_without_changing_native_paths(self):
        adapter = SOURCE.parent / "plasma-backend/kwin_wayland"
        with tempfile.TemporaryDirectory() as directory:
            compositor = Path(directory) / "kwin"
            compositor.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            compositor.chmod(0o755)
            for mode, host, relay, expected, absent in (
                ("nested", "x11", "1", "--virtual", "--x11-display"),
                ("nested", "x11", "0", "--x11-display", "--virtual"),
                ("nested", "wayland", "0", "--wayland-display", "--virtual"),
                ("nested", "wayland", "1", "--virtual", "--wayland-display"),
                ("headless", "none", "0", "--virtual", "--x11-display"),
            ):
                with self.subTest(mode=mode, host=host, relay=relay):
                    result = subprocess.run(
                        ["/bin/bash", str(adapter)],
                        env={
                            **os.environ,
                            "DISPLAY_MODE": mode,
                            "HOST_DISPLAY_BACKEND": host,
                            "PLASMA_SOFTWARE_NESTED": relay,
                            "PLASMA_SOFTWARE_CAPTURE": "0",
                            "PLASMA_NVIDIA_SYNC": "0",
                            "PLASMA_KWIN_BINARY": str(compositor),
                            "HOST_DISPLAY": ":42",
                            "HOST_XAUTHORITY": "/tmp/host-auth",
                            "HOST_WAYLAND_DISPLAY": "/tmp/host-wayland",
                            "DISPLAY_WIDTH": "800",
                            "DISPLAY_HEIGHT": "600",
                        },
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    self.assertIn(expected, result.stdout.splitlines())
                    self.assertNotIn(absent, result.stdout.splitlines())

    def test_nested_presentation_relay_preserves_renderer_and_older_compositor_paths(
        self,
    ):
        module = (SOURCE.parents[1] / "init.d/modules/400-plasma.sh").read_text()
        start = module.index(
            "\tif [[ $backend == wayland", module.index("software_capture=1")
        )
        end = module.index("\tif [[ $backend == x11 && $mode == nested", start)
        for backend, software, mode, host, opengl, relay, capture in (
            ("wayland", "1", "nested", "wayland", "1", "1", "0"),
            ("wayland", "1", "nested", "x11", "1", "1", "0"),
            ("wayland", "0", "nested", "wayland", "1", "1", "0"),
            ("wayland", "0", "nested", "x11", "1", "1", "0"),
            ("wayland", "0", "nested", "wayland", "0", "0", "0"),
            ("wayland", "0", "nested", "x11", "0", "0", "0"),
            ("wayland", "1", "nested", "wayland", "0", "1", "1"),
            ("wayland", "1", "nested", "x11", "0", "0", "0"),
            ("wayland", "1", "headless", "none", "1", "0", "0"),
            ("wayland", "1", "vnc", "none", "1", "0", "0"),
            ("wayland", "1", "web", "none", "1", "0", "0"),
            ("x11", "1", "nested", "wayland", "1", "0", "0"),
        ):
            with self.subTest(
                backend=backend, software=software, mode=mode, host=host, opengl=opengl
            ):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -euo pipefail\nsoftware_nested=0\nsoftware_capture=0\n"
                        + module[start:end]
                        + '\nprintf "%s\n" "$software_nested" "$software_capture" "$software" "$render_node"',
                    ],
                    env={
                        **os.environ,
                        "backend": backend,
                        "software": software,
                        "mode": mode,
                        "host_backend": host,
                        "opengl_software": opengl,
                        "render_node": "/dev/dri/renderD128" if software == "0" else "",
                    },
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(
                    result.stdout.splitlines(),
                    [
                        relay,
                        capture,
                        software,
                        "/dev/dri/renderD128" if software == "0" else "",
                    ],
                )

    def test_debian_nvidia_remote_fallback_does_not_override_source_compositor(self):
        for overlay, backend, mode, distro, version, vendor, expected in (
            (False, "wayland", "vnc", "debian", "13", "NVIDIA Corporation", True),
            (False, "wayland", "web", "debian", "13", "NVIDIA Corporation", True),
            (True, "wayland", "vnc", "debian", "13", "NVIDIA Corporation", False),
            (False, "wayland", "headless", "debian", "13", "NVIDIA Corporation", False),
            (False, "wayland", "nested", "debian", "13", "NVIDIA Corporation", False),
            (False, "x11", "vnc", "debian", "13", "NVIDIA Corporation", False),
            (False, "wayland", "vnc", "ubuntu", "26.04", "NVIDIA Corporation", False),
            (False, "wayland", "vnc", "debian", "13", "AMD", False),
        ):
            with (
                self.subTest(
                    overlay=overlay,
                    backend=backend,
                    mode=mode,
                    distro=distro,
                    vendor=vendor,
                ),
                patch.dict(os.environ, {"TARGET_APP": "test-app"}),
                patch.object(GPU.Path, "is_file", return_value=overlay),
                patch.object(
                    GPU.platform,
                    "freedesktop_os_release",
                    return_value={"ID": distro, "VERSION_ID": version},
                ),
            ):
                self.assertEqual(
                    GPU.requires_software(
                        backend, mode, {"hardware": True, "vendor": vendor}
                    ),
                    expected,
                )

    def test_installed_overlay_does_not_disable_the_full_desktop_software_workaround(
        self,
    ):
        with (
            patch.dict(os.environ, {"TARGET_APP": ""}),
            patch.object(GPU.Path, "is_file", return_value=True),
            patch.object(
                GPU.platform,
                "freedesktop_os_release",
                return_value={"ID": "debian", "VERSION_ID": "13"},
            ),
        ):
            self.assertTrue(
                GPU.requires_software(
                    "wayland", "vnc", {"hardware": True, "vendor": "NVIDIA Corporation"}
                )
            )

    def test_capture_policy_cli_uses_the_selected_usable_gpu_from_json(self):
        nvidia = {
            "device": "/dev/dri/renderD128",
            "hardware": True,
            "vendor": "NVIDIA Corporation",
        }
        amd = {"device": "/dev/dri/renderD129", "hardware": True, "vendor": "AMD"}
        failed = {**nvidia, "hardware": False, "error": "EGL initialization failed"}
        for devices, expected in (
            ([amd, nvidia], 0),
            ([failed, amd], 1),
            ([failed], 1),
        ):
            selected = GPU.select_device(devices)
            for overlay, target, status in (
                (False, "", expected),
                (True, "", expected),
                (True, "fixture", 1),
            ):
                with (
                    self.subTest(devices=devices, overlay=overlay, target=target),
                    patch.dict(os.environ, {"TARGET_APP": target}),
                    patch.object(Path, "is_file", return_value=overlay),
                    patch(
                        "platform.freedesktop_os_release",
                        return_value={"ID": "debian", "VERSION_ID": "13"},
                    ),
                    patch.object(
                        sys,
                        "argv",
                        [
                            str(SOURCE.parent / "plasma-gpu"),
                            "--requires-software",
                            "wayland",
                            "vnc",
                        ],
                    ),
                    patch.object(
                        sys,
                        "stdin",
                        io.StringIO(
                            json.dumps({"devices": devices, "selected": selected})
                        ),
                    ),
                    self.assertRaises(SystemExit) as result,
                ):
                    runpy.run_path(
                        str(SOURCE.parent / "plasma-gpu"), run_name="__main__"
                    )
                self.assertEqual(result.exception.code, status)
        for malformed in ("", "broken", "{}", "[]", '{"selected": "NVIDIA"}'):
            with (
                self.subTest(malformed=malformed),
                patch.object(
                    sys,
                    "argv",
                    [
                        str(SOURCE.parent / "plasma-gpu"),
                        "--requires-software",
                        "wayland",
                        "vnc",
                    ],
                ),
                patch.object(sys, "stdin", io.StringIO(malformed)),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(SOURCE.parent / "plasma-gpu"), run_name="__main__")
            self.assertEqual(result.exception.code, 2)

    def test_init_probes_before_policy_and_propagates_policy_errors(self):
        module = (SOURCE.parents[1] / "init.d/modules/400-plasma.sh").read_text()
        start = module.index(
            "\tif [[ $software == 0 ]]", module.index("local kwin_overlay=0")
        )
        end = module.index("\tchmod 644 /etc/plasma/gpu.json", start)
        for forced, detected, policy, software, calls, status in (
            (0, 0, 0, 1, 2, 0),
            (0, 0, 1, 0, 2, 0),
            (0, 1, 0, 1, 1, 0),
            (1, 0, 0, 1, 0, 0),
            (0, 0, 2, None, 2, 7),
        ):
            with (
                self.subTest(forced=forced, detected=detected, policy=policy),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                report = root / "gpu.json"
                snapshot = (
                    json.dumps(
                        {
                            "devices": [{"hardware": True}],
                            "selected": {"hardware": True, "vendor": "AMD"},
                        }
                    )
                    + "\n"
                )
                (root / "snapshot").write_text(snapshot)
                script = (
                    module[start:end]
                    .replace("/usr/local/libexec/plasma-gpu", "gpu")
                    .replace("/etc/plasma/gpu.json", str(report))
                )
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        'set -euo pipefail\nsoftware=$1; backend=wayland; mode=vnc\ninfo() { :; }\ndie() { exit 7; }\nrunuser() { shift 3; "$@"; }\ngpu() {\n    printf \'%s\\n\' "$*" >>"$QA_ROOT/calls"\n    if [[ $# == 0 ]]; then cat "$QA_ROOT/snapshot"; return "$QA_DETECTED"; fi\n    cmp -s "$QA_ROOT/snapshot" - || return 2\n    return "$QA_POLICY"\n}\n'
                        + "check_policy() {\n"
                        + script
                        + '\n}\ncheck_policy\nprintf "%s\n" "$software"',
                        "check",
                        str(forced),
                    ],
                    env={
                        **os.environ,
                        "QA_ROOT": directory,
                        "QA_DETECTED": str(detected),
                        "QA_POLICY": str(policy),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(
                    len((root / "calls").read_text().splitlines())
                    if (root / "calls").exists()
                    else 0,
                    calls,
                )
                if software is not None:
                    self.assertEqual(result.stdout.strip(), str(software))
                    if forced:
                        self.assertEqual(
                            json.loads(report.read_text()),
                            {"devices": [], "forced_software": True},
                        )
                    else:
                        self.assertEqual(report.read_text(), snapshot)

    def test_matching_kscreen_uses_private_qt_without_changing_application_environment(
        self,
    ):
        environment = {
            "TARGET_APP": "test-app",
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

    def test_full_desktop_keeps_distro_kscreen_when_private_stack_is_installed(self):
        environment = {
            "PATH": "/usr/bin",
            "TARGET_APP": "",
            "LD_LIBRARY_PATH": "/custom/lib",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(Path, "is_file", return_value=True),
            patch.object(REMOTE.subprocess, "run") as run,
        ):
            REMOTE.kscreen("-j", timeout=5)
            self.assertEqual(run.call_args.args[0], ["kscreen-doctor", "-j"])
            self.assertEqual(run.call_args.kwargs["env"], environment)

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
            intercept.write_text(
                f"source {shlex.quote(str(SOURCE.parents[3] / 'tests/shell_trace.sh'))}\n"
                'exec() { printf "%s\\n" "$@"; }\n'
            )
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
