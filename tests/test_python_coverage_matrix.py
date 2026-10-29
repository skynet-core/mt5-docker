"""Check display-runner success, failure and evidence contracts at I/O boundaries."""

import contextlib
import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import runtime
from display_matrix import profiles
from PIL import Image


def result(stdout=b"", stderr=b"", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def marker():
    return Image.new("RGB", (800, 600), (18, 210, 178))


class RuntimeBoundaryTests(unittest.TestCase):
    def test_start_uses_private_device_wrapper_and_explicit_cpu_or_gpu_policy(self):
        for gpu in (False, True):
            with (
                self.subTest(gpu=gpu),
                tempfile.TemporaryDirectory() as directory,
                patch.object(runtime, "execute", return_value=result()) as execute,
                patch.object(runtime, "ready"),
            ):
                runtime.start(
                    "docker",
                    "private:image",
                    "private-child",
                    "wayland",
                    "web",
                    Path(directory),
                    gpu=gpu,
                )
            arguments = execute.call_args.args
            self.assertIn("--entrypoint=/usr/local/bin/mt5-test-init", arguments)
            wrapper = (
                Path(runtime.__file__).with_name("isolated-entrypoint.sh").resolve()
            )
            self.assertIn(
                f"type=bind,source={wrapper},target=/usr/local/bin/mt5-test-init,readonly",
                arguments,
            )
            self.assertIn(f"MT5_TEST_GPU={int(gpu)}", arguments)
            self.assertIn(f"LIBGL_ALWAYS_SOFTWARE={int(not gpu)}", arguments)
            self.assertEqual(arguments[-1], "private:image")

    def test_cli_help_exposes_public_transport_options(self):
        with (
            patch.object(sys, "argv", ["runtime", "--help"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as status,
        ):
            runpy.run_path(str(Path(runtime.__file__)), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        for option in (
            "--image",
            "--fixture",
            "--gpu",
            "--backends",
            "--capture-startup",
        ):
            self.assertIn(option, output.getvalue())

    def test_command_and_logs_preserve_stdout_stderr_even_on_failure(self):
        with patch.object(
            runtime.subprocess, "run", return_value=result(b"out", b"err", 1)
        ) as run:
            self.assertEqual(
                runtime.execute("tool", "argument", check=False).returncode, 1
            )
            run.assert_called_once_with(
                ("tool", "argument"), capture_output=True, check=False
            )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "execute", return_value=result(b"out", b"err", 1)),
        ):
            output = Path(directory)
            runtime.collect("docker", "private-child", output)
            for name in ("container.log", "services.log", "units.log"):
                self.assertEqual((output / name).read_bytes(), b"outerr")

    def test_readiness_retries_and_has_a_deadline(self):
        with (
            patch.object(
                runtime, "execute", side_effect=[result(returncode=1), result()]
            ) as execute,
            patch.object(runtime.time, "monotonic", return_value=0),
            patch.object(runtime.time, "sleep") as sleep,
        ):
            runtime.ready("docker", "private-child")
            self.assertEqual(execute.call_count, 2)
            sleep.assert_called_once_with(0.5)
        with (
            patch.object(runtime, "execute", return_value=result(returncode=1)),
            patch.object(runtime.time, "monotonic", side_effect=[0, 0, 111]),
            patch.object(runtime.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "110 seconds"),
        ):
            runtime.ready("docker", "private-child")

    def test_vnc_web_transport_retries_and_rejects_failed_http(self):
        client = Mock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        for mode in ("vnc", "web"):
            with (
                self.subTest(mode=mode),
                patch.object(
                    runtime, "execute", return_value=result(b"127.0.0.1:45678\n")
                ),
                patch.object(
                    runtime, "RFBClient", side_effect=[OSError("not ready"), client]
                ) as connect,
                patch.object(runtime.urllib.request, "urlopen", return_value=response),
                patch.object(runtime.time, "monotonic", return_value=0),
                patch.object(runtime.time, "sleep"),
            ):
                self.assertIs(runtime.remote("docker", "private-child", mode), client)
                self.assertEqual(connect.call_args.kwargs, {"web": mode == "web"})
        response.status = 503
        with (
            patch.object(runtime, "execute", return_value=result(b"127.0.0.1:45678")),
            patch.object(runtime.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(RuntimeError, "noVNC page"),
        ):
            runtime.remote("docker", "private-child", "web")
        with (
            patch.object(runtime, "execute", return_value=result(b"127.0.0.1:45678")),
            patch.object(runtime, "RFBClient", side_effect=RuntimeError("unreachable")),
            patch.object(runtime.time, "monotonic", side_effect=[0, 31]),
            self.assertRaisesRegex(RuntimeError, "unreachable"),
        ):
            runtime.remote("docker", "private-child", "vnc")

    def test_framebuffer_waits_for_marker_and_keeps_black_failure_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            client = Mock()
            client.screenshot.side_effect = [Image.new("RGB", (800, 600)), marker()]
            with (
                patch.object(runtime.time, "monotonic", return_value=0),
                patch.object(runtime.time, "sleep"),
            ):
                self.assertGreater(
                    runtime.screenshot(client, output / "visible.png"), 50
                )
            client.screenshot.side_effect = None
            client.screenshot.return_value = Image.new("RGB", (800, 600))
            with (
                patch.object(runtime.time, "monotonic", side_effect=[0, 26]),
                self.assertRaisesRegex(RuntimeError, "green marker"),
            ):
                runtime.screenshot(client, output / "failure.png")
            self.assertTrue((output / "failure.png").is_file())

    def test_gui_save_requires_input_through_the_client_and_retains_failure_evidence(
        self,
    ):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                state = {"typed": None, "saved": None}
                client = Mock()
                client.screenshot.return_value = marker()
                client.text.side_effect = lambda value, state=state: state.__setitem__(
                    "typed", value
                )

                def click(x, y, state=state, fail=fail):
                    if (x, y) == (20, 20) and not fail:
                        state["saved"] = state["typed"]

                client.click.side_effect = click

                def execute(*arguments, state=state, **_options):
                    if "cat" in arguments:
                        return result(
                            state["saved"].encode() if state["saved"] else b"waiting",
                            b"diagnostic",
                            0 if state["saved"] else 1,
                        )
                    return result()

                def capture(_client, path):
                    marker().save(path)
                    return 16000

                with (
                    patch.object(runtime, "execute", side_effect=execute),
                    patch.object(
                        runtime, "input_points", return_value=((10, 10), (20, 20))
                    ),
                    patch.object(runtime, "screenshot", side_effect=capture),
                    patch.object(runtime.time, "monotonic", side_effect=[0, 0, 16]),
                    patch.object(runtime.time, "sleep"),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "did not save"):
                            runtime.gui_input("docker", "private-child", client, output)
                        self.assertTrue((output / "input-error.log").is_file())
                        self.assertTrue((output / "input-failure.png").is_file())
                    else:
                        self.assertTrue(
                            runtime.gui_input(
                                "docker", "private-child", client, output
                            )["passed"]
                        )
                        self.assertEqual(
                            (output / "typed.txt").read_bytes(),
                            (output / "expected.txt").read_bytes(),
                        )
                client.chord.assert_called_once_with(0xFFE3, ord("a"))

    def test_capture_retry_and_backing_method_mismatch(self):
        pixels = io.BytesIO()
        marker().save(pixels, format="PNG")
        metadata = json.dumps({"method": "kwin-screenshot2"}).encode()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                side_effect=[
                    result(metadata),
                    result(pixels.getvalue()),
                    result(metadata),
                    result(pixels.getvalue()),
                ],
            ),
            patch.object(
                runtime,
                "check_screenshot",
                side_effect=[ValueError("early blank"), 16000],
            ),
            patch.object(runtime.time, "monotonic", return_value=0),
            patch.object(runtime.time, "sleep"),
        ):
            self.assertEqual(
                runtime.capture_output("docker", "private-child", {}, Path(directory))[
                    "marker_pixels"
                ],
                16000,
            )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                side_effect=[result(metadata), result(pixels.getvalue())],
            ),
            patch.object(runtime, "check_screenshot", return_value=16000),
            self.assertRaisesRegex(RuntimeError, "wrong display API"),
        ):
            runtime.capture_output(
                "docker",
                "private-child",
                {
                    "DISPLAY_BACKEND": "wayland",
                    "DISPLAY_MODE": "headless",
                    "PLASMA_SOFTWARE_CAPTURE": "1",
                    "PLASMA_SOFTWARE_DISPLAY": ":110",
                },
                Path(directory),
            )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                side_effect=[result(metadata), result(pixels.getvalue())],
            ),
            patch.object(
                runtime, "check_screenshot", side_effect=ValueError("still blank")
            ),
            patch.object(runtime.time, "monotonic", side_effect=[0, 26]),
            self.assertRaisesRegex(ValueError, "still blank"),
        ):
            runtime.capture_output("docker", "private-child", {}, Path(directory))

    def test_capture_startup_resumes_producer_and_closes_client_on_failure(self):
        for good in (False, True):
            with self.subTest(good=good), tempfile.TemporaryDirectory() as directory:
                client = Mock()
                client.screenshot.return_value = (
                    Image.new("RGB", (20, 20)) if good else marker()
                )
                with (
                    patch.object(runtime, "execute") as execute,
                    patch.object(runtime, "remote", return_value=client),
                    patch.object(runtime, "screenshot", return_value=50),
                ):
                    if good:
                        self.assertEqual(
                            runtime.capture_startup(
                                "docker", "private-child", "vnc", Path(directory), {}
                            )["delayed_capture_marker_pixels"],
                            50,
                        )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "not initialized"):
                            runtime.capture_startup(
                                "docker", "private-child", "vnc", Path(directory), {}
                            )
                self.assertIn("--signal=CONT", execute.call_args.args)
                client.close.assert_called_once()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "execute") as execute,
            patch.object(runtime, "remote", side_effect=RuntimeError("no transport")),
            self.assertRaisesRegex(RuntimeError, "no transport"),
        ):
            runtime.capture_startup(
                "docker", "private-child", "vnc", Path(directory), {}
            )
        self.assertIn("--signal=CONT", execute.call_args.args)

    def test_start_failure_logs_error_and_gpu_application_options_are_preserved(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime, "execute", return_value=result(b"", b"failed start", 23)
            ),
            patch.object(runtime, "ready") as ready,
            self.assertRaisesRegex(RuntimeError, "Container start failed"),
        ):
            runtime.start(
                "docker",
                "image",
                "private-child",
                "wayland",
                "headless",
                Path(directory),
            )
        ready.assert_not_called()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "execute", return_value=result()) as execute,
            patch.object(runtime, "ready"),
        ):
            runtime.start(
                "docker",
                "image",
                "private-child",
                "wayland",
                "web",
                Path(directory),
                gpu=True,
                application="/private/application",
                dimensions=(1920, 1080),
            )
            command = execute.call_args.args
            self.assertIn("--gpus", command)
            self.assertIn("TARGET_APP=/private/application", command)
            self.assertIn("DISPLAY_WIDTH=1920", command)
            self.assertIn("DISPLAY_HEIGHT=1080", command)
            self.assertIn("127.0.0.1::6080", command)

    def test_private_xwayland_cookie_and_headless_units(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                runtime,
                "execute",
                side_effect=[
                    result(b'{"DISPLAY":":123","XAUTHORITY":"/private/auth"}'),
                    result(b"private test cookie"),
                ],
            ),
        ):
            display, path = runtime.parent_x11(
                "docker", "private-parent", Path(directory)
            )
            self.assertEqual(display, ":123")
            self.assertEqual(path.read_bytes(), b"private test cookie")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.object(runtime, "execute", return_value=result(b"inactive\n")):
            self.assertEqual(
                len(runtime.headless_services("docker", "private-child")), 2
            )


class FixtureMatrixTests(unittest.TestCase):
    def run_matrix(
        self, directory, *, failure=None, selected=None, gpu=True, fixture=False
    ):
        output = Path(directory)
        windows = {}
        clients = []
        fixture_path = None
        if fixture:
            fixture_path = output / "fixture"
            fixture_path.touch(mode=0o755)
        arguments = SimpleNamespace(
            runtime="docker",
            image="private-image",
            parent_image="private-parent-image",
            fixture=str(fixture_path) if fixture_path else None,
            output=str(output / "evidence"),
            case="matrix",
            backends=["wayland", "x11"],
            gpu=gpu,
            capture_startup=True,
        )

        def start(_runtime, _image, name, backend, mode, case, extra=(), **_options):
            host = (
                "wayland"
                if "HOST_WAYLAND_DISPLAY=/run/host/wayland" in extra
                else "x11"
                if "HOST_XAUTHORITY=/run/host/Xauthority" in extra
                else "none"
            )
            windows[name] = {
                "DISPLAY_BACKEND": backend,
                "DISPLAY_MODE": mode,
                "HOST_DISPLAY_BACKEND": host,
                "QT_QPA_PLATFORMTHEME": "unsafe" if failure == "theme" else "generic",
                "LIBGL_ALWAYS_SOFTWARE": "1" if failure == "allowed-software" else "0",
            }
            (case / "start.log").write_text("private start")

        def execute(*command, **_options):
            if command[1:3] == ("image", "inspect"):
                return result(b"sha256:private-image")
            if command[1] == "inspect":
                return result(
                    b"sha256:changed" if failure == "image" else b"sha256:private-image"
                )
            if command[1] == "exec":
                if command[-1] == "/etc/plasma/gpu.json":
                    return result(
                        json.dumps(
                            {"devices": [{"hardware": failure != "gpu"}]}
                        ).encode()
                    )
                if "test" in command:
                    return result(returncode=0)
                if "platform.freedesktop_os_release" in command[-1]:
                    return result(b'{"ID":"debian","VERSION_ID":"13"}')
                if "libraries=set()" in command[-1]:
                    return result(
                        json.dumps(
                            [
                                "/usr/lib/libQt6Core.so"
                                if failure == "libraries"
                                else "/opt/kwin-6.8.2/lib/libQt6Core.so"
                            ]
                        ).encode()
                    )
            return result()

        def parent_cookie(_runtime, _parent, path):
            auth = path / "Xauthority"
            auth.write_text("private cookie")
            return ":123", auth

        def remote(*_arguments):
            client = Mock()
            clients.append(client)
            return client

        def capture(_client, path):
            marker().save(path)
            return 16000

        def headless(_runtime, _name, _session, path):
            marker().save(path / "screenshot.png")
            return {"marker_pixels": 16000, "capture_method": "kwin-screenshot2"}

        def collect(_runtime, _name, path):
            for log in ("container.log", "services.log", "units.log"):
                (path / log).write_text("private diagnostic")

        support = (
            "KWin version: "
            + ("wrong" if failure == "version" else "6.8.2")
            + "\nQt Version: 6.11.2\nOpenGL renderer string: "
            + (
                "llvmpipe"
                if failure in ("renderer", "allowed-software")
                else "NVIDIA test GPU"
            )
            + "\n"
        )
        with (
            patch.object(runtime, "execute", side_effect=execute),
            patch.object(runtime, "start", side_effect=start),
            patch.object(
                runtime,
                "configuration",
                side_effect=lambda _runtime, name: windows[name],
            ),
            patch.object(runtime, "remote", side_effect=remote),
            patch.object(runtime, "parent_x11", side_effect=parent_cookie),
            patch.object(runtime, "screenshot", side_effect=capture),
            patch.object(runtime, "capture_output", side_effect=headless),
            patch.object(runtime, "gui_input", return_value={"passed": True}),
            patch.object(
                runtime,
                "headless_services",
                return_value={
                    "plasma-vnc.service": "inactive",
                    "plasma-web.service": "inactive",
                },
            ),
            patch.object(
                runtime,
                "capture_startup",
                return_value={"delayed_capture_marker_pixels": 16000},
            ),
            patch.object(runtime, "validate_clipboard") as clipboard,
            patch.object(runtime, "collect", side_effect=collect),
            patch.object(
                runtime.subprocess, "run", return_value=result(support.encode())
            ),
            patch.object(
                runtime,
                "profiles",
                return_value=selected if selected is not None else list(profiles()),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            if failure not in (None, "allowed-software") or selected == []:
                with self.assertRaises(SystemExit):
                    runtime.main(arguments)
            else:
                runtime.main(arguments)
        self.assertTrue(all(client.close.called for client in clients))
        saved = json.loads((output / "evidence/matrix/results.json").read_text())
        self.assertFalse((output / "evidence/matrix/parent/runtime").exists())
        self.assertFalse((output / "evidence/matrix/parent/Xauthority").exists())
        return saved, clipboard.call_count

    def test_all_profiles_preserve_matching_images_rendering_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            saved, clipboard = self.run_matrix(directory, fixture=True)
        self.assertTrue(saved["passed"])
        self.assertEqual(len(saved["cases"]), 10)
        self.assertEqual(clipboard, 4)
        self.assertTrue(all(case["hardware_rendering"] for case in saved["cases"]))

    def test_profile_failures_are_retained_without_claiming_success(self):
        for failure, message in (
            ("image", "image changed"),
            ("theme", "safe generic Qt theme"),
            ("libraries", "mixed overlay Qt"),
            ("gpu", "hardware EGL context"),
            ("version", "KWin 6.8.2"),
            ("renderer", "requested hardware renderer"),
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                saved, _ = self.run_matrix(
                    directory, failure=failure, selected=[("wayland", "vnc", None)]
                )
                self.assertFalse(saved["passed"])
                self.assertIn(message, saved["cases"][0]["error"])
                self.assertTrue(
                    (
                        Path(directory) / "evidence/matrix/wayland/vnc/error.log"
                    ).is_file()
                )

    def test_empty_profiles_fail_and_explicit_software_fallback_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            saved, _ = self.run_matrix(directory, selected=[])
            self.assertFalse(saved["passed"])
        with tempfile.TemporaryDirectory() as directory:
            saved, _ = self.run_matrix(
                directory,
                failure="allowed-software",
                selected=[("wayland", "vnc", None)],
            )
            self.assertTrue(saved["passed"])
            self.assertTrue(saved["cases"][0]["software_fallback"])
            self.assertFalse(saved["cases"][0]["hardware_rendering"])

    def test_invalid_fixture_is_rejected_before_creating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            fixture.touch(mode=0o644)
            with (
                patch.object(runtime, "execute") as execute,
                self.assertRaisesRegex(ValueError, "executable test window"),
            ):
                runtime.main(SimpleNamespace(runtime="docker", fixture=str(fixture)))
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
