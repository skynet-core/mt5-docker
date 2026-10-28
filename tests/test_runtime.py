"""The GUI runner must isolate account services and use the caller's file identity."""

import ast
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import runtime
from PIL import Image, ImageDraw


class RuntimeArgumentsTests(unittest.TestCase):
    def test_wayland_qpainter_capture_uses_only_its_private_x11_backing(self):
        session = {
            "DISPLAY_BACKEND": "wayland",
            "DISPLAY_MODE": "headless",
            "PLASMA_SOFTWARE_CAPTURE": "1",
            "PLASMA_SOFTWARE_DISPLAY": ":110",
            "WAYLAND_DISPLAY": "wayland-0",
            "QT_QPA_PLATFORM": "wayland",
        }
        image = Image.new("RGB", (800, 600), "white")
        ImageDraw.Draw(image).rectangle((12, 12, 788, 56), fill=(18, 210, 178))
        pixels = io.BytesIO()
        image.save(pixels, "PNG")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                side_effect=[
                    SimpleNamespace(
                        returncode=0, stdout=b'{"method":"x11-root"}\n', stderr=b""
                    ),
                    SimpleNamespace(stdout=pixels.getvalue()),
                ],
            ) as execute,
        ):
            result = runtime.capture_output(
                "docker", "test-container", session, Path(directory)
            )
        command = execute.call_args_list[0].args
        self.assertIn("QT_QPA_PLATFORM=xcb", command)
        self.assertIn("DISPLAY=:110", command)
        self.assertFalse(any(value.startswith("WAYLAND_DISPLAY=") for value in command))
        self.assertEqual(session["QT_QPA_PLATFORM"], "wayland")
        self.assertEqual(result["capture_method"], "x11-root")
        self.assertEqual(result["headless_backing_display"], ":110")

    def test_capture_failure_preserves_exit_status_when_qt_logs_are_disabled(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b""),
            ),
            self.assertRaisesRegex(RuntimeError, "status 1: no diagnostic output"),
        ):
            runtime.capture_output("docker", "test-container", {}, Path(directory))

    def test_capture_metadata_is_an_explicit_stdout_result(self):
        self.assertEqual(
            runtime.capture_metadata(
                b'{"method":"kwin-screenshot2","width":800,"height":600}\n'
            )["method"],
            "kwin-screenshot2",
        )
        for stdout in (b"", b"irrelevant Qt diagnostic\n", b"{}\n"):
            with (
                self.subTest(stdout=stdout),
                self.assertRaisesRegex(RuntimeError, "JSON metadata on stdout"),
            ):
                runtime.capture_metadata(stdout)

    def test_python_probes_sent_to_containers_remain_valid_after_loop_edits(self):
        tree = ast.parse(Path(runtime.__file__).read_text())
        probes = [
            node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "program"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ]
        self.assertTrue(probes)
        for program in probes:
            with self.subTest(program=program):
                compile(program, "container-probe", "exec")

    def test_input_clicks_follow_visible_controls_in_offset_nested_window(self):
        image = Image.new("RGB", (800, 600))
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((90, 55, 710, 98), fill=(18, 210, 178))
        drawing.rectangle((90, 440, 710, 475), fill=(36, 88, 180))
        editor, save = runtime.input_points(image)
        self.assertEqual(editor, (172, 157))
        self.assertEqual(save, (400, 458))

    def test_input_requires_a_visible_save_button(self):
        image = Image.new("RGB", (800, 600))
        ImageDraw.Draw(image).rectangle((12, 12, 788, 56), fill=(18, 210, 178))
        with self.assertRaisesRegex(RuntimeError, "Cannot locate GUI control"):
            runtime.input_points(image)

    def test_headless_cannot_claim_rendering_through_an_active_remote_service(self):
        with (
            patch.object(
                runtime,
                "execute",
                return_value=SimpleNamespace(returncode=0, stdout=b"active\n"),
            ),
            self.assertRaisesRegex(RuntimeError, "Headless display unexpectedly"),
        ):
            runtime.headless_services("docker", "test-container")

    def test_actual_session_must_match_requested_host_transport(self):
        with self.assertRaisesRegex(RuntimeError, "differs"):
            runtime.session_identity(
                {
                    "DISPLAY_BACKEND": "wayland",
                    "DISPLAY_MODE": "nested",
                    "HOST_DISPLAY_BACKEND": "x11",
                },
                "wayland",
                "nested",
                "wayland",
            )

    def test_pipewire_startup_regression_does_not_pause_unrelated_x11_capture(self):
        with patch.object(runtime, "execute") as execute:
            report = runtime.capture_startup(
                "docker",
                "test-container",
                "vnc",
                Path("/tmp"),
                {"PLASMA_SOFTWARE_CAPTURE": "1"},
            )
        self.assertEqual(
            report, {"capture_startup_skipped": "X11 capture does not use PipeWire"}
        )
        execute.assert_not_called()

    def launch(self, uid, gid, **options):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime.os, "getuid", return_value=uid),
            patch.object(runtime.os, "getgid", return_value=gid),
            patch.object(runtime, "ready") as ready,
            patch.object(
                runtime,
                "execute",
                return_value=SimpleNamespace(
                    returncode=0, stdout=b"container\n", stderr=b""
                ),
            ) as execute,
        ):
            runtime.start(
                "docker",
                "production-image",
                "test-container",
                "wayland",
                "vnc",
                Path(directory),
                **options,
            )
            ready.assert_called_once_with("docker", "test-container")
            return execute.call_args.args

    def test_production_marker_is_readonly_and_account_services_are_disabled(self):
        arguments = self.launch(1001, 1001, fixture=Path("/tmp/fixture"))
        self.assertEqual(arguments[-1], "production-image")
        self.assertIn("--no-healthcheck", arguments)
        self.assertIn("PLASMA_UID=1001", arguments)
        self.assertIn("PLASMA_GID=1001", arguments)
        self.assertIn(
            "type=bind,source=/tmp/fixture,target=/usr/local/bin/plasma-ui-fixture,readonly",
            arguments,
        )
        self.assertIn(
            "type=bind,source=/dev/null,target=/etc/systemd/system/mt5-mcp.service,readonly",
            arguments,
        )
        self.assertIn(
            "TARGET_APP=/usr/local/bin/plasma-ui-fixture /tmp/typed.txt", arguments
        )

    def test_existing_start_calls_work_without_fixture_and_root_ids_are_nonzero(self):
        arguments = self.launch(0, 0, sleep=True)
        self.assertIn("PLASMA_UID=1000", arguments)
        self.assertIn("PLASMA_GID=1000", arguments)
        self.assertIn("TARGET_APP=/usr/bin/sleep infinity", arguments)
        self.assertFalse(
            any(
                "target=/usr/local/bin/plasma-ui-fixture" in value
                for value in arguments
            )
        )


if __name__ == "__main__":
    unittest.main()
