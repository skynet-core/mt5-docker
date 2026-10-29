"""Exercise host GPU policy through the real launchers without a Docker daemon."""

import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = (ROOT / "vendor/kde-container/bin/plasma6-run", ROOT / "bin/mt5-run")
IMAGE = "mt5:host-gpu-test"


class HostGpuTests(unittest.TestCase):
    def launch(
        self,
        *arguments,
        launcher=LAUNCHERS[0],
        loaded=True,
        runtime="nvidia",
        info_status=0,
        engine="docker",
        backend="wayland",
        mode="nested",
        host="auto",
        environment=None,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as display,
        ):
            temporary = Path(directory)
            display.bind(str(temporary / "wayland-test"))
            authority = temporary / "Xauthority"
            authority.touch()
            commands = temporary / "commands.jsonl"
            mock = temporary / "host.sh"
            mock.write_text(
                f"source {shlex.quote(str(ROOT / 'tests/shell_trace.sh'))}\n"
                "test() {\n"
                "  if [[ ${1:-} == -d && ${2:-} == /sys/module/nvidia ]]; then\n"
                "    [[ $FAKE_NVIDIA_LOADED == 1 ]]\n"
                "  else\n"
                '    builtin test "$@"\n'
                "  fi\n"
                "}\n"
            )
            executable = temporary / engine
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                'with open(os.environ["GPU_COMMAND_LOG"], "a") as stream:\n'
                '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
                'if sys.argv[1] == "info":\n'
                '    assert sys.argv[2] == "--format"\n'
                '    print(os.environ["FAKE_GPU_RUNTIME"])\n'
                '    sys.exit(int(os.environ["FAKE_INFO_STATUS"]))\n'
                'assert sys.argv[1] == "run"\n'
                'print("fake-container")\n'
            )
            executable.chmod(0o755)
            uv = temporary / "uv"
            uv.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                'assert sys.argv[1:3] == ["run", "--script"]\n'
                "os.execv(sys.executable, [sys.executable, *sys.argv[3:]])\n"
            )
            uv.chmod(0o755)
            cleared = {
                "RUNTIME",
                "IMAGE_TAG",
                "CONTAINER_NAME",
                "DISPLAY_BACKEND",
                "DISPLAY_MODE",
                "HOST_DISPLAY_BACKEND",
                "LIBGL_ALWAYS_SOFTWARE",
                "TARGET_APP",
                "VNC_PASSWORD_FILE",
                "PLASMA_PASSWORD_FILE",
                "PLASMA_PASSWORD",
                "PLASMA_SUDO_NOPASSWD",
            }
            settings = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("MT5_", "PLASMA_")) and key not in cleared
            }
            for key in ("MT5_SHELL_COVERAGE_DIR", "MT5_SHELL_COVERAGE_MAP"):
                if key in os.environ:
                    settings[key] = os.environ[key]
            settings.update(
                {
                    "PATH": str(temporary) + os.pathsep + settings["PATH"],
                    "BASH_ENV": str(mock),
                    "FAKE_NVIDIA_LOADED": "1" if loaded else "0",
                    "FAKE_GPU_RUNTIME": runtime,
                    "FAKE_INFO_STATUS": str(info_status),
                    "GPU_COMMAND_LOG": str(commands),
                    "RUNTIME": str(executable),
                    "IMAGE_TAG": IMAGE,
                    "CONTAINER_NAME": "mt5-host-gpu-test",
                    "MT5_ENV_FILE": "/dev/null",
                    "DISPLAY_BACKEND": backend,
                    "DISPLAY_MODE": mode,
                    "HOST_DISPLAY_BACKEND": host,
                    "WAYLAND_DISPLAY": str(temporary / "wayland-test"),
                    "XDG_RUNTIME_DIR": str(temporary),
                    "DISPLAY": ":4242",
                    "XAUTHORITY": str(authority),
                    "CLIPBOARD_SYNC": "0",
                    "PLASMA_UID": "1000",
                    "PLASMA_GID": "1000",
                    "VNC_PASSWORD": "testpass",
                }
            )
            settings.update(environment or {})
            subprocess.run(
                [str(launcher), *arguments],
                env=settings,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            calls = [json.loads(line) for line in commands.read_text().splitlines()]
            runs = [call for call in calls if call[0] == "run"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0][-1], IMAGE)
            self.assertFalse(
                {"MT5_ACCOUNT", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"}
                & set(runs[0])
            )
            return runs[0], [call for call in calls if call[0] == "info"]

    def test_native_wayland_defaults_add_supported_nvidia_once(self):
        for launcher in LAUNCHERS:
            for host in ("auto", "wayland"):
                with self.subTest(launcher=launcher.name, host=host):
                    arguments, queries = self.launch(launcher=launcher, host=host)
                    self.assertEqual(arguments.count("--gpus"), 1)
                    self.assertEqual(arguments[arguments.index("--gpus") + 1], "all")
                    self.assertEqual(len(queries), 1)

    def test_unavailable_module_runtime_or_info_does_not_add_gpu_flags(self):
        for options, expected_queries in (
            ({"loaded": False}, 0),
            ({"runtime": ""}, 1),
            ({"info_status": 1}, 1),
        ):
            with self.subTest(options=options):
                arguments, queries = self.launch(**options)
                self.assertNotIn("--gpus", arguments)
                self.assertEqual(len(queries), expected_queries)

    def test_explicit_gpu_runtime_and_cdi_options_remain_authoritative(self):
        for selected in (
            ("--gpus", "device=1"),
            ("--gpus=all",),
            ("--runtime", "nvidia"),
            ("--runtime=runc",),
            ("--device", "nvidia.com/gpu=all"),
            ("--device=nvidia.com/gpu=0",),
        ):
            with self.subTest(arguments=selected):
                arguments, queries = self.launch(*selected)
                self.assertEqual(arguments[-len(selected) - 1 :], [*selected, IMAGE])
                self.assertEqual(
                    sum(
                        value == "--gpus" or value.startswith("--gpus=")
                        for value in arguments
                    ),
                    sum(
                        value == "--gpus" or value.startswith("--gpus=")
                        for value in selected
                    ),
                )
                self.assertFalse(queries)

    def test_explicit_software_values_and_forwarded_names_skip_gpu_detection(self):
        for selected, environment in (
            (("-e", "LIBGL_ALWAYS_SOFTWARE=1"), {}),
            (("--env", "LIBGL_ALWAYS_SOFTWARE=1"), {}),
            (("--env=LIBGL_ALWAYS_SOFTWARE=1",), {}),
            (("-eLIBGL_ALWAYS_SOFTWARE=1",), {}),
            (("-e", "LIBGL_ALWAYS_SOFTWARE"), {"LIBGL_ALWAYS_SOFTWARE": "1"}),
            (("--env=LIBGL_ALWAYS_SOFTWARE",), {"LIBGL_ALWAYS_SOFTWARE": "1"}),
            (("-eLIBGL_ALWAYS_SOFTWARE",), {"LIBGL_ALWAYS_SOFTWARE": "1"}),
        ):
            with self.subTest(arguments=selected):
                arguments, queries = self.launch(*selected, environment=environment)
                self.assertNotIn("--gpus", arguments)
                self.assertFalse(queries)

    def test_other_rendering_paths_do_not_query_docker_for_gpu_options(self):
        for options in (
            {"engine": "podman"},
            {"backend": "x11"},
            {"host": "x11"},
            {"mode": "headless"},
            {"mode": "vnc"},
            {"mode": "web"},
        ):
            with self.subTest(options=options):
                arguments, queries = self.launch(**options)
                self.assertNotIn("--gpus", arguments)
                self.assertFalse(queries)

    def test_non_nvidia_devices_and_hardware_requests_still_allow_auto_gpu(self):
        for selected, environment in (
            (("--device", "/dev/dri/renderD129"), {}),
            (("-e", "LIBGL_ALWAYS_SOFTWARE=0"), {}),
            (("-e", "LIBGL_ALWAYS_SOFTWARE"), {"LIBGL_ALWAYS_SOFTWARE": "0"}),
        ):
            with self.subTest(arguments=selected):
                arguments, queries = self.launch(*selected, environment=environment)
                self.assertEqual(arguments.count("--gpus"), 1)
                self.assertEqual(arguments[arguments.index("--gpus") + 1], "all")
                self.assertEqual(len(queries), 1)


if __name__ == "__main__":
    unittest.main()
