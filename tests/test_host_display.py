"""Keep explicitly mounted nested parents separate from the launcher's host."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HostDisplayCases:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.host = self.directory / "host-wayland"
        self.private = self.directory / "private-wayland"
        self.x11 = self.directory / "private-X11"
        self.x11.mkdir()
        for path in (self.host, self.private, self.x11 / "X42"):
            connection = socket.socket(socket.AF_UNIX)
            self.addCleanup(connection.close)
            connection.bind(str(path))
        self.cookie = self.directory / "private-authority"
        self.cookie.touch()
        self.engines = {}
        for name in ("docker", "podman"):
            engine = self.directory / name
            engine.write_text(
                f"#!{sys.executable}\nimport json, sys\n"
                "assert sys.argv[1] == 'run'\n"
                "print(json.dumps(sys.argv[1:]))\n"
            )
            engine.chmod(0o755)
            self.engines[name] = engine
        uv = self.directory / "uv"
        uv.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            'assert sys.argv[1:3] == ["run", "--script"]\n'
            "os.execv(sys.executable, [sys.executable, *sys.argv[3:]])\n"
        )
        uv.chmod(0o755)

    def launch(self, *arguments, host="wayland", missing=False, engine="docker"):
        return subprocess.run(
            [str(self.launcher), "--env", "LIBGL_ALWAYS_SOFTWARE=1", *arguments],
            env={
                **{
                    key: os.environ[key]
                    for key in (
                        "BASH_ENV",
                        "MT5_SHELL_COVERAGE_DIR",
                        "MT5_SHELL_COVERAGE_MAP",
                    )
                    if key in os.environ
                },
                "PATH": str(self.directory) + os.pathsep + os.environ["PATH"],
                "HOME": str(self.directory),
                "RUNTIME": str(self.engines[engine]),
                "IMAGE_TAG": "mt5:private-display-test",
                "MT5_ENV_FILE": "/dev/null",
                "DISPLAY_MODE": "nested",
                "DISPLAY_BACKEND": "wayland",
                "HOST_DISPLAY_BACKEND": host,
                "XDG_RUNTIME_DIR": str(self.directory),
                "WAYLAND_DISPLAY": str(self.directory / "missing")
                if missing
                else str(self.host),
                "DISPLAY": ":4242",
                "XAUTHORITY": str(self.cookie),
                "CLIPBOARD_SYNC": "1",
                "PLASMA_UID": "1000",
                "PLASMA_GID": "1000",
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def assert_private(self, result, expected, engine):
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        mounts = [
            arguments[index + 1] if option == "--mount" else option[8:]
            for index, option in enumerate(arguments)
            if option == "--mount" or option.startswith("--mount=")
        ]
        self.assertEqual(mounts, [*self.default_mounts, *expected])
        self.assertEqual(arguments[-1], "mt5:private-display-test")
        for port in self.default_ports:
            self.assertIn(port, arguments)
        self.assertEqual(
            arguments.count(
                "--userns=host" if engine == "docker" else "--userns=keep-id"
            ),
            1,
        )
        return arguments

    def test_private_wayland_mount_does_not_require_host_socket(self):
        for engine in self.engines:
            for field in ("target", "destination", "dst"):
                for equals in (False, True):
                    with self.subTest(engine=engine, field=field, equals=equals):
                        mount = (
                            f"type=bind,source={self.private},"
                            f"{field}=/run/host/wayland,readonly"
                        )
                        options = ["--mount=" + mount] if equals else ["--mount", mount]
                        options += ["-e", "HOST_WAYLAND_DISPLAY=/run/host/wayland"]
                        result = self.launch(*options, missing=True, engine=engine)
                        self.assert_private(result, [mount], engine)

    def test_private_x11_mount_does_not_import_real_host_displays(self):
        for engine in self.engines:
            for host in ("auto", "x11"):
                with self.subTest(engine=engine, host=host):
                    mounts = [
                        f"type=bind,source={self.x11},target=/run/host/X11,readonly",
                        f"type=bind,source={self.cookie},target=/run/host/Xauthority,readonly",
                    ]
                    arguments = self.assert_private(
                        self.launch(
                            "--mount",
                            mounts[0],
                            "--mount=" + mounts[1],
                            "-e",
                            "HOST_DISPLAY=:42",
                            "-e",
                            "HOST_XAUTHORITY=/run/host/Xauthority",
                            host=host,
                            engine=engine,
                        ),
                        mounts,
                        engine,
                    )
                    self.assertIn("HOST_DISPLAY=:42", arguments)
                    self.assertNotIn("HOST_DISPLAY=:4242", arguments)
                    self.assertNotIn(
                        "HOST_WAYLAND_DISPLAY=/run/host/wayland", arguments
                    )

    def test_private_wayland_and_x11_mounts_are_not_duplicated(self):
        mounts = [
            f"type=bind,source={self.private},target=/run/host/wayland,readonly",
            f"type=bind,source={self.x11},target=/run/host/X11,readonly",
        ]
        self.assert_private(
            self.launch("--mount", mounts[0], "--mount", mounts[1], host="auto"),
            mounts,
            "docker",
        )

    def test_forced_wayland_missing_socket_still_rejects_without_display_mount(self):
        for options in (
            (),
            (
                "--mount",
                f"type=bind,source={self.private},target=/run/host/wayland-other",
            ),
            ("--mount", "type=volume,source=unused,target=/run/host/wayland"),
            ("--env", "UNRELATED=type=bind,target=/run/host/wayland"),
        ):
            with self.subTest(options=options):
                result = self.launch(*options, missing=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires a host Wayland socket", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_ordinary_wayland_discovery_still_mounts_the_real_selected_socket(self):
        mount = f"type=bind,source={self.host},target=/run/host/wayland,readonly"
        result = self.launch("-e", "CLIPBOARD_SYNC=0")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assertIn(mount, arguments)
        self.assertIn("HOST_WAYLAND_DISPLAY=/run/host/wayland", arguments)


class PlasmaHostDisplayTests(HostDisplayCases, unittest.TestCase):
    launcher = ROOT / "vendor/kde-container/bin/plasma6-run"
    default_mounts = ()
    default_ports = ()


class MT5HostDisplayTests(HostDisplayCases, unittest.TestCase):
    launcher = ROOT / "bin/mt5-run"
    default_mounts = ("type=volume,source=mt5-data,target=/home/plasma/.wine",)
    default_ports = (
        "127.0.0.1:8000:8000",
        "127.0.0.1:22346:22347",
        "127.0.0.1:22345:22349",
    )


if __name__ == "__main__":
    unittest.main()
