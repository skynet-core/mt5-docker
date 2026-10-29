"""Launcher option tests never start Docker, Podman, or a user desktop client."""

import socket
import unittest
from pathlib import Path

import test_shell_coverage_runtime as runtime_tests

SOURCE = Path(__file__).resolve().parents[1] / "vendor/kde-container/bin/plasma6-run"


class ShellLauncherTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source

    def launch(self, *, extra="", socket_exists=True, auth_exists=True, **environment):
        def setup(root):
            if socket_exists:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.bind(str(root / "wayland-test"))
            (root / "host-X11").mkdir()
            (root / "home").mkdir()
            (root / "home/.Xauthority").write_text("synthetic-home-cookie")
            (root / "private password").write_text("synthetic-password")
            if auth_exists:
                (root / "auth").write_text("synthetic-cookie")
            for engine in ("docker", "podman"):
                runtime = root / engine
                runtime.write_text(
                    "#!/bin/bash\nif [[ $1 == info ]]; then printf nvidia; else exit 99; fi\n"
                )
                runtime.chmod(0o755)

        return self.run_source(
            SOURCE,
            setup=setup,
            replacements=(("/tmp/.X11-unix", "host-X11"),),
            before=(
                'RUNTIME="$TEST_ROOT/${TEST_ENGINE:-docker}"\nHOME="$TEST_ROOT/home"\n'
                "id() { printf 1000; }\n"
                'test() { if [[ $* == "-d /sys/module/nvidia" ]]; then return 1; else builtin test "$@"; fi; }\n'
                + extra
            ),
            environment={
                "TEST_ENGINE": "docker",
                "HOST_DISPLAY_BACKEND": "auto",
                "IMAGE_TAG": "image:fixture",
                "CLIPBOARD_SYNC": "0",
                **environment,
            },
        )

    def test_credentials_are_forwarded_by_name_and_password_files_are_readonly(self):
        for file in (False, True):
            result, commands, _, _ = self.launch(
                extra='PLASMA_PASSWORD_FILE="$TEST_ROOT/private password"\n'
                if file
                else "",
                PLASMA_PASSWORD="never-print-this",
                PLASMA_SUDO_NOPASSWD="false",
                TARGET_APP="application --flag",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = commands[-1]
            self.assertEqual(arguments[-1], "image:fixture")
            self.assertIn("TARGET_APP=application --flag", arguments)
            self.assertIn("PLASMA_SUDO_NOPASSWD", arguments)
            self.assertNotIn("never-print-this", arguments)
            if file:
                self.assertTrue(
                    any(
                        "private password,target=/run/secrets/plasma-password,readonly"
                        in value
                        for value in arguments
                    )
                )
            else:
                self.assertIn("PLASMA_PASSWORD", arguments)

    def test_identity_and_rootless_flags_follow_runtime_and_explicit_overrides(self):
        for mode, engine, explicit in (
            ("headless", "docker", True),
            ("nested", "docker", False),
            ("headless", "podman", False),
            ("nested", "podman", True),
        ):
            result, commands, _, _ = self.launch(
                DISPLAY_MODE=mode,
                TEST_ENGINE=engine,
                **({"PLASMA_UID": "2100", "PLASMA_GID": "2200"} if explicit else {}),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = commands[-1]
            self.assertIn(f"PLASMA_UID={'2100' if explicit else '1000'}", arguments)
            self.assertIn(f"PLASMA_GID={'2200' if explicit else '1000'}", arguments)
            self.assertEqual("--systemd=always" in arguments, engine == "podman")
            self.assertEqual(
                "--userns=host" in arguments, mode == "nested" and engine == "docker"
            )

    def test_nested_transport_discovery_uses_only_private_sockets_and_cookies(self):
        for extra, auth_exists, expected_auth in (
            ("", True, "/auth,target=/run/host/Xauthority,readonly"),
            (
                'WAYLAND_DISPLAY="$TEST_ROOT/wayland-test"\n',
                False,
                "/home/.Xauthority,target=/run/host/Xauthority,readonly",
            ),
            (
                "unset WAYLAND_DISPLAY\n",
                True,
                "/auth,target=/run/host/Xauthority,readonly",
            ),
        ):
            with self.subTest(extra=extra, auth_exists=auth_exists):
                result, commands, _, _ = self.launch(
                    DISPLAY_MODE="nested", extra=extra, auth_exists=auth_exists
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                arguments = commands[-1]
                self.assertTrue(
                    any(
                        "target=/run/host/wayland,readonly" in value
                        for value in arguments
                    )
                )
                self.assertTrue(any(expected_auth in value for value in arguments))

    def test_vnc_and_web_bind_the_requested_port_and_password_source(self):
        for mode, file, port in (
            ("vnc", False, "5900"),
            ("web", True, "6080"),
            ("vnc", True, "0"),
            ("web", False, "0"),
        ):
            result, commands, _, _ = self.launch(
                DISPLAY_MODE=mode,
                VNC_PASSWORD="synthetic",
                VNC_PORT=port,
                WEB_PORT=port,
                extra='VNC_PASSWORD_FILE="$TEST_ROOT/private password"\n'
                if file
                else "",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = commands[-1]
            container_port = "6080" if mode == "web" else "5900"
            self.assertIn(
                f"127.0.0.1:{'' if port == '0' else port}:{container_port}", arguments
            )
            self.assertNotIn("synthetic", arguments)
            if file:
                self.assertTrue(
                    any(
                        "target=/run/secrets/vnc-password,readonly" in value
                        for value in arguments
                    )
                )
            else:
                self.assertIn("VNC_PASSWORD", arguments)

    def test_invalid_runtime_transport_and_missing_secrets_fail_before_container_run(
        self,
    ):
        for values, extra, socket_exists, message in (
            ({"TEST_ENGINE": "unsupported"}, "", True, "RUNTIME must name"),
            (
                {"DISPLAY_MODE": "nested", "HOST_DISPLAY_BACKEND": "wrong"},
                "",
                True,
                "Invalid HOST_DISPLAY_BACKEND",
            ),
            (
                {"DISPLAY_MODE": "nested", "HOST_DISPLAY_BACKEND": "wayland"},
                "",
                False,
                "requires a host Wayland socket",
            ),
            ({"DISPLAY_MODE": "vnc"}, "unset VNC_PASSWORD\n", True, "Set VNC_PASSWORD"),
        ):
            with self.subTest(message=message):
                result, commands, _, _ = self.launch(
                    extra=extra, socket_exists=socket_exists, **values
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
