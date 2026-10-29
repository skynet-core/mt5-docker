"""Health must prove a connected MT5 session and check each required transport."""

import io
import json
import os
import runpy
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def response(body, session=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.headers = {"Mcp-Session-Id": session} if session else {}
    result.read.return_value = body
    return result


class Mt5HealthTests(unittest.TestCase):
    def run_health(self, result, *, stream=False, session=True, protocol_error=False):
        if protocol_error:
            payload = {"error": {"code": -32603, "message": "synthetic rejection"}}
        else:
            payload = {"result": result}
        body = json.dumps(payload).encode()
        if stream:
            body = b"event: message\ndata: " + body + b"\n\n"
        replies = [
            response(b'{"result":{}}', "private-test-session" if session else None),
            response(b""),
            response(body),
        ]
        if session:
            replies.append(response(b""))
        self.requests = []

        def request(message, **_options):
            self.requests.append(message)
            return replies.pop(0)

        with (
            patch("subprocess.run") as desktop,
            patch("urllib.request.urlopen", side_effect=request),
        ):
            runpy.run_path(str(ROOT / "bin/mt5-healthcheck"))
        desktop.assert_called_once_with(
            ["/usr/local/bin/plasma-healthcheck"], check=True
        )
        self.assertFalse(replies)

    def test_session_handshake_connected_query_and_cleanup(self):
        self.run_health(
            {"structuredContent": {"success": True, "data": {"connected": True}}}
        )
        self.assertEqual(
            [request.get_method() for request in self.requests],
            ["POST", "POST", "POST", "DELETE"],
        )
        messages = [json.loads(request.data) for request in self.requests[:3]]
        self.assertEqual(
            [message["method"] for message in messages],
            ["initialize", "notifications/initialized", "tools/call"],
        )
        self.assertEqual(
            messages[2]["params"],
            {"name": "mt5_query", "arguments": {"operation": "terminal_info"}},
        )
        for request in self.requests[1:]:
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["mcp-session-id"], "private-test-session")
            self.assertEqual(headers["mcp-protocol-version"], "2025-03-26")

    def test_sse_and_text_results_are_supported_without_session(self):
        self.run_health(
            {
                "content": [
                    {"text": json.dumps({"success": True, "data": {"connected": True}})}
                ]
            },
            stream=True,
            session=False,
        )
        self.assertEqual(len(self.requests), 3)
        self.assertFalse(
            any(request.get_method() == "DELETE" for request in self.requests)
        )

    def test_disconnected_failed_and_tool_error_results_are_unhealthy(self):
        for tool_error, success, connected in (
            (False, True, False),
            (False, False, True),
            (True, True, True),
        ):
            with self.subTest(
                tool_error=tool_error, success=success, connected=connected
            ):
                with self.assertRaisesRegex(AssertionError, "MT5 is disconnected"):
                    self.run_health(
                        {
                            "isError": tool_error,
                            "structuredContent": {
                                "success": success,
                                "data": {"connected": connected},
                            },
                        }
                    )
                self.assertEqual(self.requests[-1].get_method(), "DELETE")

    def test_protocol_error_releases_the_session(self):
        with self.assertRaisesRegex(RuntimeError, "MCP returned an error"):
            self.run_health({}, protocol_error=True)
        self.assertEqual(self.requests[-1].get_method(), "DELETE")

    def test_unhealthy_desktop_is_rejected_before_http(self):
        with (
            patch(
                "subprocess.run", side_effect=subprocess.CalledProcessError(1, "probe")
            ),
            patch("urllib.request.urlopen") as http,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            runpy.run_path(str(ROOT / "bin/mt5-healthcheck"))
        http.assert_not_called()


class DesktopHealthTests(unittest.TestCase):
    def probe(self, *, mode="headless", backend="wayland", extra=None, failed=None):
        environment = {
            "DISPLAY_BACKEND": backend,
            "DISPLAY_MODE": mode,
            "CLIPBOARD_SYNC": "0",
            "XDG_RUNTIME_DIR": "/run/private-test",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/private-test/bus",
            **(extra or {}),
        }
        config = "".join(f'{key}="{value}"\n' for key, value in environment.items())
        calls = []

        def execute(command, **options):
            calls.append((command, options))
            if failed in command:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(returncode=0)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.open", return_value=io.StringIO(config)),
            patch(
                "pwd.getpwnam",
                return_value=SimpleNamespace(
                    pw_uid=1234, pw_gid=2345, pw_name="plasma"
                ),
            ),
            patch("os.getgrouplist", return_value=[2345, 3456]),
            patch("subprocess.run", side_effect=execute),
        ):
            runpy.run_path(str(ROOT / "vendor/kde-container/bin/plasma-healthcheck"))
        return calls

    def test_headless_checks_each_unit_and_only_private_desktop(self):
        calls = self.probe()
        self.assertEqual(
            [command[-1] for command, _ in calls],
            ["plasma-wayland-headless.target", "plasma-session.service", "desktop"],
        )
        probe = calls[-1][1]
        self.assertEqual(probe["user"], 1234)
        self.assertEqual(probe["group"], 2345)
        self.assertEqual(probe["extra_groups"], [2345, 3456])
        self.assertEqual(probe["env"]["XDG_RUNTIME_DIR"], "/run/private-test")
        self.assertEqual(probe["env"]["PLASMA_WAIT_TIMEOUT"], "0")

    def test_remote_profiles_check_capture_and_their_own_transport(self):
        for mode, transports in (
            ("vnc", ["desktop", "vnc"]),
            ("web", ["desktop", "vnc", "web"]),
        ):
            with self.subTest(mode=mode):
                calls = self.probe(mode=mode)
                commands = [command for command, _ in calls]
                self.assertIn(
                    ["/usr/local/libexec/plasma-remote-output", "--check"], commands
                )
                self.assertEqual(
                    [
                        command[-1]
                        for command in commands
                        if "plasma-wait" in command[0]
                    ],
                    transports,
                )
                self.assertIn(
                    ["systemctl", "is-active", "--quiet", "plasma-vnc.service"],
                    commands,
                )
                self.assertEqual(
                    any(command[-1] == "plasma-web.service" for command in commands),
                    mode == "web",
                )

    def test_nested_clipboard_and_software_relay_are_individually_required(self):
        calls = self.probe(
            mode="nested", extra={"CLIPBOARD_SYNC": "1", "PLASMA_SOFTWARE_NESTED": "1"}
        )
        units = [command[-1] for command, _ in calls if command[0] == "systemctl"]
        self.assertIn("plasma-clipboard.service", units)
        self.assertIn("plasma-nested-x11.service", units)
        self.assertIn("plasma-vnc.service", units)
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(
                mode="nested",
                extra={"CLIPBOARD_SYNC": "1"},
                failed="plasma-clipboard.service",
            )


if __name__ == "__main__":
    unittest.main()
