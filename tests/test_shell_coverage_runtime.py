"""Execute shell runtime paths with private files and intercepted process handoffs."""

import json
import os
import shlex
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from shell_coverage import copy_shell

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "vendor/kde-container/libexec"
TRACE = Path(__file__).with_name("shell_trace.sh")
ISOLATION_BOUNDARIES = (
    'mkdir() { record mkdir "$@"; [[ ${TEST_FAIL:-} != mkdir ]] || return 37; command mkdir "$@"; }\n'
    'mount() { record mount "$@"; if [[ ${TEST_FAIL:-} == mount || ( ${TEST_FAIL:-} == bind && $1 == --bind ) ]]; then return 37; fi; }\n'
)


class ShellRuntimeTests(unittest.TestCase):
    def run_source(
        self,
        source,
        *,
        environment=None,
        before="",
        after="",
        replacements=(),
        setup=None,
        arguments=(),
        relative_name="source",
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(source)
            pairs = [(old, str(root / new)) for old, new in replacements]
            script = copy_shell(source, root / relative_name, pairs)
            if setup:
                setup(root)
            bootstrap = root / "environment.sh"
            bootstrap.write_text(
                f"source {shlex.quote(str(TRACE))}\n"
                "record() { { flock -x 9; printf '%s\\0' \"$@\" >&9; printf '\\n' >&9; } 9>> \"$TEST_LOG\"; }\n"
                "exec() {\n"
                '  record "$@"\n'
                '  TEST_CAPTURED_PRELOAD=${LD_PRELOAD:-} LD_PRELOAD= python3 -c \'import json,os; keys="DISPLAY XAUTHORITY WAYLAND_DISPLAY PATH LD_LIBRARY_PATH QT_PLUGIN_PATH QML_IMPORT_PATH QML2_IMPORT_PATH PLASMA_KWIN_BINARY PLASMA_KWIN_INPUT XDG_SESSION_TYPE PLASMA_APP_LD_LIBRARY_PATH PLASMA_APP_QML2_IMPORT_PATH PLASMA_KWIN_ORIGINAL_LD_PRELOAD".split(); data={key:os.environ[key] for key in keys if key in os.environ}; data["LD_PRELOAD"]=os.environ["TEST_CAPTURED_PRELOAD"]; json.dump(data,open(os.environ["TEST_ENV_LOG"],"w"))\'\n'
                '  exit "${TEST_EXEC_STATUS:-0}"\n'
                "}\n"
                "info() { :; }\n"
                "die() { printf '%s\\n' \"$*\" >&2; exit 23; }\n"
                'runuser() { record runuser "$@"; return "${TEST_CONFIGURE_STATUS:-0}"; }\n'
                "kbuildsycoca6() { record kbuildsycoca6; }\n" + before
            )
            settings = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("MT5_", "PLASMA_", "VNC_", "HOST_"))
                and key
                not in {
                    "LD_PRELOAD",
                    "LD_LIBRARY_PATH",
                    "QT_PLUGIN_PATH",
                    "QML_IMPORT_PATH",
                    "QML2_IMPORT_PATH",
                }
            }
            for key in ("MT5_SHELL_COVERAGE_DIR", "MT5_SHELL_COVERAGE_MAP"):
                if key in os.environ:
                    settings[key] = os.environ[key]
            settings.update(
                {
                    "BASH_ENV": str(bootstrap),
                    "TEST_LOG": str(root / "commands"),
                    "TEST_ENV_LOG": str(root / "environment.json"),
                    "TEST_ROOT": str(root),
                    "DISPLAY_BACKEND": "wayland",
                    "DISPLAY_MODE": "headless",
                    "DISPLAY_WIDTH": "800",
                    "DISPLAY_HEIGHT": "600",
                    "VNC_MAX_FPS": "60",
                    "HOST_DISPLAY_BACKEND": "wayland",
                    "HOST_WAYLAND_DISPLAY": str(root / "host-wayland"),
                    "HOST_DISPLAY": ":41",
                    "HOST_XAUTHORITY": str(root / "host-auth"),
                    "PLASMA_X11_DISPLAY": ":100",
                    "PLASMA_SOFTWARE_DISPLAY": ":110",
                    "DISPLAY": ":100",
                    "XAUTHORITY": str(root / "auth"),
                    "WAYLAND_DISPLAY": "wayland-test",
                    "XDG_RUNTIME_DIR": str(root),
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={root}/bus",
                    "TARGET_APP": "",
                }
            )
            settings.update(environment or {})
            if after:
                command = [
                    "bash",
                    "-c",
                    f"source {shlex.quote(str(script))}\n{after}",
                    "shell-test",
                    *arguments,
                ]
            else:
                command = ["bash", str(script), *arguments]
            result = subprocess.run(
                command, env=settings, capture_output=True, text=True, check=False
            )
            log = root / "commands"
            commands = (
                [
                    [os.fsdecode(value) for value in line.split(b"\0") if value]
                    for line in log.read_bytes().splitlines()
                ]
                if log.exists()
                else []
            )
            captured = root / "environment.json"
            final_environment = (
                json.loads(captured.read_text()) if captured.exists() else {}
            )
            files = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and path not in (script, bootstrap, log, captured)
            }
            return result, commands, final_environment, files

    def test_concurrent_fake_commands_keep_argument_records_atomic(self):
        payload = "argument with spaces " * 512
        result, commands, _, _ = self.run_source(
            ROOT / "init.d/modules/450-mt5.sh",
            environment={"TEST_RECORD_PAYLOAD": payload},
            after='for index in {1..32}; do record "$index" "$TEST_RECORD_PAYLOAD" & done; wait',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(commands), 32)
        self.assertEqual(
            {tuple(command) for command in commands},
            {(str(index), payload) for index in range(1, 33)},
        )

    def test_isolated_startup_masks_peripherals_after_oci_and_preserves_requested_gpu(
        self,
    ):
        for gpu, character, arguments in (
            (None, None, ()),
            ("0", True, ("/custom/init", "literal argument", "--flag")),
            ("1", True, ("/custom/init", "literal argument", "--flag")),
            ("1", False, ()),
        ):
            with self.subTest(gpu=gpu, character=character):

                def setup(root, character=character):
                    (root / "dev").mkdir()
                    (root / "dev/hidraw1").write_text("not a character device")
                    if character:
                        (root / "dev/hidraw0").symlink_to("/dev/null")

                result, commands, _, _ = self.run_source(
                    ROOT / "tests/isolated-entrypoint.sh",
                    environment={} if gpu is None else {"MT5_TEST_GPU": gpu},
                    arguments=arguments,
                    replacements=(("/dev", "dev"),),
                    before=ISOLATION_BOUNDARIES,
                    setup=None if character is None else setup,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ["snd", "input", "bus/usb"]
                if gpu != "1":
                    expected.append("dri")
                mounts = [command for command in commands if command[0] == "mount"]
                directories = [command for command in mounts if command[1] == "-t"]
                self.assertEqual(
                    [command[-1].split("/dev/", 1)[1] for command in directories],
                    expected,
                )
                self.assertTrue(
                    all(
                        command[1:-1]
                        == ["-t", "tmpfs", "-o", "nosuid,nodev,mode=755", "tmpfs"]
                        for command in directories
                    )
                )
                bindings = [command for command in mounts if command[1] == "--bind"]
                self.assertEqual(len(bindings), int(bool(character)))
                if character:
                    self.assertTrue(bindings[0][2].endswith("/dev/null"))
                    self.assertTrue(bindings[0][3].endswith("/dev/hidraw0"))
                self.assertEqual(
                    [command[0] for command in commands],
                    ["mkdir", "mount"] * len(expected)
                    + ["mount"] * int(bool(character))
                    + ["/init.d/init.sh"],
                )
                self.assertEqual(
                    commands[-1],
                    ["/init.d/init.sh", *(arguments or ("/usr/sbin/init", "--system"))],
                )

    def test_isolated_startup_rejects_invalid_gpu_flags_before_touching_devices(self):
        result, commands, _, _ = self.run_source(
            ROOT / "tests/isolated-entrypoint.sh",
            environment={"MT5_TEST_GPU": "true"},
            replacements=(("/dev", "dev"),),
            before=ISOLATION_BOUNDARIES,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("MT5_TEST_GPU must be 0 or 1", result.stderr)
        self.assertEqual(commands, [])

    def test_isolated_chart_fixture_uses_owned_parents_and_copy_failures_stop_init(
        self,
    ):
        for failure in ("", "directories", "copy", "gid"):
            with self.subTest(failure=failure):

                def setup(root):
                    (root / "fixture").mkdir()
                    (root / "fixture/ChartInput.ex5").write_bytes(
                        b"compiled chart fixture"
                    )

                result, commands, _, files = self.run_source(
                    ROOT / "tests/isolated-entrypoint.sh",
                    environment={"TEST_FAIL": failure},
                    replacements=(
                        ("/dev", "dev"),
                        ("/run/mt5-test", "fixture"),
                        ("/home/plasma/.wine", "wine"),
                    ),
                    before=ISOLATION_BOUNDARIES
                    + 'id() { record id "$@"; [[ $* == "-g plasma" && ${TEST_FAIL:-} != gid ]] || return 37; printf "2442\\n"; }\n'
                    'install() { record install "$@"; '
                    "if [[ ${TEST_FAIL:-} == directories && $1 == -d || ${TEST_FAIL:-} == copy && $1 == -m ]]; then return 37; fi; "
                    'if [[ $1 == -d ]]; then command mkdir -p "${@: -3}"; else command cp "${@: -2}"; fi; }\n',
                    setup=setup,
                )
                self.assertEqual(result.returncode, 37 if failure else 0, result.stderr)
                installs = [command for command in commands if command[0] == "install"]
                self.assertIn(["id", "-g", "plasma"], commands)
                if failure == "gid":
                    self.assertEqual(installs, [])
                    self.assertNotIn(
                        "/init.d/init.sh", [command[0] for command in commands]
                    )
                    continue
                self.assertEqual(installs[0][1:6], ["-d", "-o", "plasma", "-g", "2442"])
                self.assertEqual(len(installs[0][-3:]), 3)
                if failure:
                    self.assertNotIn(
                        "/init.d/init.sh", [command[0] for command in commands]
                    )
                else:
                    self.assertEqual(
                        installs[1][1:7], ["-m", "0644", "-o", "plasma", "-g", "2442"]
                    )
                    self.assertEqual(
                        files["wine/drive_c/MT5/MQL5/Experts/ChartInput.ex5"],
                        b"compiled chart fixture",
                    )

    def test_isolated_startup_fails_closed_when_device_masking_or_init_fails(self):
        def setup(root):
            (root / "dev").mkdir()
            (root / "dev/hidraw0").symlink_to("/dev/null")

        for failure in ("mkdir", "mount", "bind", "exec"):
            with self.subTest(failure=failure):
                result, commands, _, _ = self.run_source(
                    ROOT / "tests/isolated-entrypoint.sh",
                    environment={
                        "TEST_FAIL": failure,
                        "TEST_EXEC_STATUS": "47" if failure == "exec" else "0",
                    },
                    replacements=(("/dev", "dev"),),
                    before=ISOLATION_BOUNDARIES,
                    setup=setup,
                )
                self.assertEqual(result.returncode, 47 if failure == "exec" else 37)
                if failure == "exec":
                    self.assertEqual(commands[-1][0], "/init.d/init.sh")
                else:
                    self.assertEqual(
                        commands[-1][0], "mkdir" if failure == "mkdir" else "mount"
                    )
                    self.assertNotIn(
                        "/init.d/init.sh", [command[0] for command in commands]
                    )

    def test_private_xservers_select_the_requested_transport(self):
        for mode, host, binary, option in (
            ("headless", "wayland", "Xvfb", "-screen"),
            ("vnc", "x11", "Xvfb", "-screen"),
            ("nested", "wayland", "Xwayland", "-geometry"),
            ("nested", "x11", "Xephyr", "-screen"),
        ):
            with self.subTest(mode=mode, host=host):
                result, commands, environment, _ = self.run_source(
                    RUNTIME / "plasma-xserver",
                    environment={"DISPLAY_MODE": mode, "HOST_DISPLAY_BACKEND": host},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(commands[-1][0]).name, binary)
                self.assertIn(option, commands[-1])
                self.assertIn("-nolisten", commands[-1])
                if binary == "Xwayland":
                    self.assertTrue(
                        environment["WAYLAND_DISPLAY"].endswith("host-wayland")
                    )
                    self.assertIn("-shm", commands[-1])
                elif binary == "Xephyr":
                    self.assertEqual(environment["DISPLAY"], ":41")

    def test_software_parent_stays_private_and_disables_tcp(self):
        result, commands, _, _ = self.run_source(RUNTIME / "plasma-software-display")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands[-1][:2], ["/usr/bin/Xvfb", ":110"])
        self.assertIn("800x600x24", commands[-1])
        self.assertIn("-nolisten", commands[-1])

    def test_vnc_native_and_software_capture_use_different_input_paths(self):
        for backend, software in (("wayland", "0"), ("wayland", "1"), ("x11", "0")):
            with self.subTest(backend=backend, software=software):
                result, commands, environment, _ = self.run_source(
                    RUNTIME / "plasma-vnc",
                    environment={
                        "DISPLAY_BACKEND": backend,
                        "PLASMA_SOFTWARE_CAPTURE": software,
                    },
                    before="cat() { printf 'testpass'; }\n",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if backend == "wayland" and software == "0":
                    self.assertEqual(commands[-1][0], "/usr/bin/krfb-virtualmonitor")
                    self.assertEqual(environment["PLASMA_KWIN_INPUT"], "1")
                    self.assertIn("800x600", commands[-1])
                else:
                    self.assertEqual(commands[-1][0], "/usr/bin/x11vnc")
                    self.assertIn("17", commands[-1])
                    if software == "1":
                        self.assertEqual(environment["DISPLAY"], ":110")
                        self.assertNotIn("WAYLAND_DISPLAY", environment)
                        self.assertIn("-nomodtweak", commands[-1])
                        self.assertIn("xtest-paced.so", environment["LD_PRELOAD"])

    def test_kiosk_and_desktop_sessions_keep_their_supported_entrypoints(self):
        def setup(root):
            for name in ("wait", "target"):
                helper = root / name
                helper.write_text(f'#!/bin/bash\nrecord {name} "$@"\n')
                helper.chmod(0o755)

        for backend, kiosk, expected in (
            ("wayland", "", "/usr/bin/startplasma-wayland"),
            ("x11", "", "/usr/bin/startplasma-x11"),
            ("wayland", "true", "/usr/bin/kwin_wayland_wrapper"),
        ):
            with self.subTest(backend=backend, kiosk=kiosk):
                result, commands, _, _ = self.run_source(
                    RUNTIME / "plasma-session",
                    environment={"DISPLAY_BACKEND": backend, "TARGET_APP": kiosk},
                    replacements=(
                        ("/usr/local/libexec/plasma-wait", "wait"),
                        ("/usr/local/libexec/plasma-target-app", "target"),
                    ),
                    setup=setup,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands[-1][0], expected)
                if kiosk:
                    self.assertEqual(commands[0], ["target", "--configure"])

    def test_missing_vnc_password_does_not_start_an_unprotected_server(self):
        result, commands, _, _ = self.run_source(
            RUNTIME / "plasma-vnc", before="cat() { return 1; }\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(commands, [])

    def test_private_compositor_overlay_preserves_the_application_library_environment(
        self,
    ):
        def setup(root):
            (root / "private/bin").mkdir(parents=True)
            (root / "private/bin/kwin_wayland").touch(mode=0o755)
            target = root / "target"
            target.write_text('#!/bin/bash\nrecord target "$@"\n')
            target.chmod(0o755)

        result, commands, environment, _ = self.run_source(
            RUNTIME / "plasma-session",
            setup=setup,
            replacements=(
                ("/opt/kwin-6.8.2", "private"),
                ("/usr/local/libexec/plasma-target-app", "target"),
            ),
            environment={
                "TARGET_APP": "application",
                "LD_LIBRARY_PATH": "/original/lib",
                "QML2_IMPORT_PATH": "/original/qml",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(commands[-1][0].endswith("/private/bin/kwin_wayland_wrapper"))
        self.assertTrue(
            environment["PLASMA_KWIN_BINARY"].endswith("/private/bin/kwin_wayland")
        )
        self.assertTrue(
            environment["LD_LIBRARY_PATH"].endswith("/private/lib:/original/lib")
        )
        self.assertEqual(environment["PLASMA_APP_LD_LIBRARY_PATH"], "/original/lib")
        self.assertEqual(environment["PLASMA_APP_QML2_IMPORT_PATH"], "/original/qml")
        self.assertNotIn("QML2_IMPORT_PATH", environment)

    def test_x11_kiosk_propagates_app_status_and_stops_its_compositor(self):
        def setup(root):
            os.mkfifo(root / "ready")
            programs = {
                "wait": 'record wait "$@"\n',
                "target": 'if [[ ${1:-} == --configure ]]; then record configure; exit 0; fi\nrecord application\nprintf ready > "$TEST_ROOT/ready"\nexit "${TEST_APP_STATUS:-0}"\n',
                "kwin": 'record compositor "$@"\ntrap \'printf stopped > "$TEST_ROOT/stopped"; exit 0\' TERM\nunset -f exec\nexec 3<>"$TEST_ROOT/ready"\nread -r -u 3 ready || :\nread -r -t 10 -u 3 unused || :\n',
            }
            for name, body in programs.items():
                path = root / name
                path.write_text("#!/bin/bash\n" + body)
                path.chmod(0o755)

        for status in (0, 7):
            with self.subTest(status=status):
                result, commands, _, files = self.run_source(
                    RUNTIME / "plasma-session",
                    setup=setup,
                    replacements=(
                        ("/usr/local/libexec/plasma-wait", "wait"),
                        ("/usr/local/libexec/plasma-target-app", "target"),
                        ("/usr/bin/kwin_x11", "kwin"),
                    ),
                    environment={
                        "DISPLAY_BACKEND": "x11",
                        "TARGET_APP": "application",
                        "TEST_APP_STATUS": str(status),
                    },
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(["compositor", "--replace"], commands)
                self.assertIn(["application"], commands)
                self.assertEqual(files["stopped"], b"stopped")

    def test_mt5_launch_publishes_only_private_display_metadata_before_wine(self):
        def setup(root):
            helper = root / "dpi"
            helper.write_text(
                "#!/bin/bash\nrecord dpi\n[[ ! -e $TEST_ROOT/mt5-environment.json ]]\n"
            )
            helper.chmod(0o755)

        before = r"""exec() {
record "$@"
python3 -c 'import os,stat; assert stat.S_IMODE(os.stat(os.environ["TEST_ROOT"]+"/mt5-environment.json").st_mode)==0o600'
exit 0
}
"""
        result, commands, _, files = self.run_source(
            ROOT / "bin/mt5-launch",
            before=before,
            setup=setup,
            replacements=(
                ("/home/plasma/.wine", "prefix"),
                ("/run/plasma/mt5-environment.json", "mt5-environment.json"),
                ("/usr/local/libexec/mt5-dpi", "dpi"),
            ),
            environment={"MT5_PASSWORD": "never-publish-this"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands[0], ["dpi"])
        self.assertEqual(
            commands[-1],
            [
                "wine",
                r"C:\MT5\terminal64.exe",
                "/portable",
                r"/config:C:\MT5\config.ini",
            ],
        )
        metadata = json.loads(files["mt5-environment.json"])
        self.assertEqual(metadata["DISPLAY"], ":100")
        self.assertEqual(metadata["WINEDEBUG"], "-all")
        self.assertNotIn("MT5_PASSWORD", metadata)
        self.assertNotIn(b"never-publish-this", files["mt5-environment.json"])

    def test_mt5_launch_keeps_metadata_unpublished_until_json_is_complete(self):
        for previous in (None, '{"DISPLAY":":previous"}'):
            with self.subTest(previous=previous):

                def setup(root, previous=previous):
                    if previous is not None:
                        (root / "mt5-environment.json").write_text(previous)
                    helper = root / "dpi"
                    helper.write_text("#!/bin/sh\nexit 0\n")
                    helper.chmod(0o755)
                    (root / "sitecustomize.py").write_text(
                        """import json
import os
from pathlib import Path
root = Path(os.environ['TEST_ROOT'])
published = root / 'mt5-environment.json'
previous = published.read_bytes() if published.exists() else None
original_dump = json.dump
def checked_dump(data, stream, *args, **kwargs):
    if 'WINEPREFIX' in data:
        stream.write('{')
        stream.flush()
        current = published.read_bytes() if published.exists() else None
        assert current == previous, 'Readers observed incomplete display metadata'
        stream.seek(0)
        stream.truncate()
        (root / 'publication-checked').write_text('checked')
    return original_dump(data, stream, *args, **kwargs)
json.dump = checked_dump
"""
                    )

                result, _, _, files = self.run_source(
                    ROOT / "bin/mt5-launch",
                    setup=setup,
                    before='export PYTHONPATH="$TEST_ROOT"\n',
                    replacements=(
                        ("/home/plasma/.wine", "prefix"),
                        ("/run/plasma/mt5-environment.json", "mt5-environment.json"),
                        ("/usr/local/libexec/mt5-dpi", "dpi"),
                    ),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(files["publication-checked"], b"checked")
                self.assertEqual(
                    json.loads(files["mt5-environment.json"])["DISPLAY"], ":100"
                )

    def test_mt5_launch_stops_before_mt5_when_dpi_initialization_fails(self):
        def setup(root):
            helper = root / "dpi"
            helper.write_text("#!/bin/bash\nrecord dpi\nexit 37\n")
            helper.chmod(0o755)

        result, commands, _, files = self.run_source(
            ROOT / "bin/mt5-launch",
            setup=setup,
            replacements=(
                ("/usr/local/libexec/mt5-dpi", "dpi"),
                ("/run/plasma/mt5-environment.json", "mt5-environment.json"),
            ),
        )
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(commands, [["dpi"]])
        self.assertNotIn("mt5-environment.json", files)

    def test_mt5_host_arguments_preserve_private_configuration_and_secret_names(self):
        for account_file, automatic, config in (
            (False, False, False),
            (True, False, True),
            (False, True, False),
        ):
            with self.subTest(
                account_file=account_file, automatic=automatic, config=config
            ):

                def setup(root, automatic=automatic):
                    if automatic:
                        (root / ".env").write_text("MT5_PASSWORD=synthetic\n")
                    (root / "config with spaces.ini").write_text("private-config")

                result, commands, _, _ = self.run_source(
                    ROOT / "bin/mt5-run",
                    relative_name="bin/mt5-run",
                    setup=setup,
                    before='MT5_CONFIG_FILE="$TEST_ROOT/config with spaces.ini"\n'
                    if config
                    else "",
                    environment={
                        "MT5_ENV_FILE": "/dev/null" if account_file else "",
                        "MT5_ACCOUNT": "12345",
                        "MT5_LOGIN": "12345",
                        "MT5_PASSWORD": "never-log-this",
                        "MT5_SERVER": "Synthetic-Demo",
                    },
                    arguments=("--some-docker-option",),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                command = commands[-1]
                self.assertEqual(command[0] == "uv", account_file or automatic)
                self.assertIn("MT5_PASSWORD", command)
                self.assertNotIn("never-log-this", command)
                self.assertIn("--some-docker-option", command)
                if config:
                    self.assertTrue(
                        any(
                            "config with spaces.ini,target=/run/secrets/mt5-config.ini,readonly"
                            in value
                            for value in command
                        )
                    )

    def test_host_x11_aliases_are_private_and_do_not_replace_an_existing_socket(self):
        for display, existing in (("", False), (":41", False), (":41", True)):
            with self.subTest(display=display, existing=existing):

                def setup(root, existing=existing):
                    (root / "host-X11").mkdir()
                    (root / "locks").mkdir()
                    with socket.socket(
                        socket.AF_UNIX, socket.SOCK_STREAM
                    ) as connection:
                        connection.bind(str(root / "host-X11/X41"))
                    if existing:
                        (root / "client-X11").mkdir()
                        with socket.socket(
                            socket.AF_UNIX, socket.SOCK_STREAM
                        ) as connection:
                            connection.bind(str(root / "client-X11/X41"))

                result, _, _, files = self.run_source(
                    RUNTIME / "plasma-host-display",
                    setup=setup,
                    environment={"HOST_DISPLAY": display},
                    replacements=(
                        ("/tmp/.X11-unix", "client-X11"),
                        ("/run/host/X11", "host-X11"),
                        ("/tmp/.X", "locks/.X"),
                    ),
                    after='[[ -L "$TEST_ROOT/client-X11/X41" ]] && readlink "$TEST_ROOT/client-X11/X41" || true',
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if display:
                    self.assertEqual(files["locks/.X41-lock"].strip(), b"1")
                    self.assertEqual(bool(result.stdout), not existing)
                else:
                    self.assertNotIn("locks/.X41-lock", files)

    def test_nested_viewer_versions_disable_their_own_clipboard_and_shortcuts(self):
        def setup(root):
            viewer = root / "viewer"
            viewer.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$TEST_VIEWER_HELP"\nexit "${TEST_HELP_STATUS:-0}"\n'
            )
            viewer.chmod(0o755)

        for help_text, status, option in (
            ("ShortcutModifiers", 0, "-ShortcutModifiers="),
            ("MenuKey", 1, "-MenuKey="),
        ):
            result, commands, environment, _ = self.run_source(
                RUNTIME / "plasma-nested-x11",
                setup=setup,
                replacements=(("/usr/bin/vncviewer", "viewer"),),
                environment={
                    "TEST_VIEWER_HELP": help_text,
                    "TEST_HELP_STATUS": str(status),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(option, commands[-1])
            self.assertIn("-AcceptClipboard=0", commands[-1])
            self.assertIn("-SendClipboard=0", commands[-1])
            self.assertNotIn("WAYLAND_DISPLAY", environment)

    def test_kwin_adapter_aliases_sockets_and_selects_native_or_backing_outputs(self):
        def setup(root):
            wait = root / "wait"
            wait.write_text('#!/bin/bash\nrecord wait "$@"\n')
            wait.chmod(0o755)

        for mode, host, software, relay, nvidia, expected in (
            ("nested", "wayland", "0", "0", "0", "--wayland-display"),
            ("nested", "x11", "0", "0", "0", "--x11-display"),
            ("nested", "x11", "0", "1", "0", "--virtual"),
            ("headless", "none", "1", "0", "0", "--x11-display"),
            ("headless", "none", "0", "0", "1", "--virtual"),
        ):
            with self.subTest(mode=mode, host=host, software=software):
                result, commands, environment, _ = self.run_source(
                    RUNTIME / "plasma-backend/kwin_wayland",
                    setup=setup,
                    replacements=(("/usr/local/libexec/plasma-wait", "wait"),),
                    environment={
                        "DISPLAY_MODE": mode,
                        "HOST_DISPLAY_BACKEND": host,
                        "PLASMA_SOFTWARE_CAPTURE": software,
                        "PLASMA_SOFTWARE_NESTED": relay,
                        "PLASMA_NVIDIA_SYNC": nvidia,
                        "PLASMA_KWIN_BINARY": "/synthetic/private/kwin",
                    },
                    arguments=("--socket", "wayland-1"),
                    before="LD_PRELOAD=/original/preload.so\n",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands[-1][0], "/synthetic/private/kwin")
                self.assertIn(expected, commands[-1])
                self.assertNotIn("--no-lockscreen", commands[-1])
                if nvidia == "1":
                    self.assertIn(
                        "egl-fence-wait.so:/original/preload.so",
                        environment["LD_PRELOAD"],
                    )

    def test_kwin_adapter_rejects_a_conflicting_real_socket_name(self):
        result, commands, _, _ = self.run_source(
            RUNTIME / "plasma-backend/kwin_wayland",
            environment={"PLASMA_NVIDIA_SYNC": "0"},
            arguments=("--socket", "wayland-2"),
            setup=lambda root: (root / "wayland-0").write_text("occupied"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("already in use", result.stderr)
        self.assertEqual(commands, [])

    def test_pointer_enter_shim_stays_on_native_wayland_nesting_and_keeps_original_preloads(
        self,
    ):
        def setup(root):
            wait = root / "wait"
            wait.write_text('#!/bin/bash\nrecord wait "$@"\n')
            wait.chmod(0o755)

        for mode, host, relay, capture, pointer in (
            ("nested", "wayland", "0", "0", True),
            ("nested", "wayland", "0", "1", True),
            ("nested", "wayland", "1", "0", False),
            ("nested", "x11", "0", "0", False),
            ("nested", "x11", "1", "0", False),
            ("headless", "none", "0", "1", False),
            ("vnc", "wayland", "0", "0", False),
            ("web", "wayland", "0", "0", False),
        ):
            for nvidia in ("0", "1"):
                for original in ("", "/user/one.so:/user/two.so"):
                    with self.subTest(
                        mode=mode,
                        host=host,
                        relay=relay,
                        nvidia=nvidia,
                        original=original,
                    ):
                        result, commands, environment, _ = self.run_source(
                            RUNTIME / "plasma-backend/kwin_wayland",
                            setup=setup,
                            replacements=(("/usr/local/libexec/plasma-wait", "wait"),),
                            environment={
                                "DISPLAY_MODE": mode,
                                "HOST_DISPLAY_BACKEND": host,
                                "PLASMA_SOFTWARE_NESTED": relay,
                                "PLASMA_SOFTWARE_CAPTURE": capture,
                                "PLASMA_NVIDIA_SYNC": nvidia,
                            },
                            before=f"LD_PRELOAD={shlex.quote(original)}\n",
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(commands[-1][0], "/usr/bin/kwin_wayland")
                        expected = []
                        if nvidia == "1":
                            expected.append("/usr/local/lib/plasma6/egl-fence-wait.so")
                        if pointer:
                            expected.append(
                                "/usr/local/lib/plasma6/wayland-pointer-enter.so"
                            )
                        if original:
                            expected.append(original)
                        self.assertEqual(environment["LD_PRELOAD"], ":".join(expected))
                        if pointer or nvidia == "1":
                            # Each native constructor restores this before KWin starts Wine/Xwayland.
                            self.assertEqual(
                                environment["PLASMA_KWIN_ORIGINAL_LD_PRELOAD"], original
                            )
                        else:
                            self.assertNotIn(
                                "PLASMA_KWIN_ORIGINAL_LD_PRELOAD", environment
                            )

    def test_mt5_init_reports_failed_configuration(self):
        module = ROOT / "init.d/modules/450-mt5.sh"
        for status in (0, 7):
            with self.subTest(status=status):
                result, commands, _, _ = self.run_source(
                    module,
                    environment={"TEST_CONFIGURE_STATUS": str(status)},
                    after="module_mt5_enabled; module_mt5_run",
                )
                self.assertEqual(result.returncode, 0 if status == 0 else 23)
                self.assertEqual(
                    commands,
                    [
                        [
                            "runuser",
                            "-u",
                            "plasma",
                            "--",
                            "/usr/local/libexec/mt5-configure",
                        ]
                    ],
                )
                if status:
                    self.assertIn("Cannot configure MetaTrader 5", result.stderr)


if __name__ == "__main__":
    unittest.main()
