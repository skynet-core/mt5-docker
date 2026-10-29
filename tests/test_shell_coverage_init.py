"""Init modules validate identities/displays using private trees and command doubles."""

import errno
import json
import shlex
import socket
import unittest
from pathlib import Path

import test_shell_coverage_runtime as runtime_tests

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "vendor/kde-container/init.d/modules"

IDENTITY = r"""
id() {
    case "$*" in
        -u) printf '%s\n' "${TEST_CURRENT_UID:-0}";;
        '-u plasma'|'-g plasma') printf '1000\n';;
        '-gn plasma') printf 'plasma\n';;
    esac
}
getent() {
    if [[ $1 == passwd ]]; then
        [[ -n ${TEST_OWNER:-} ]] || return 2
        printf '%s:x:2000:2000::/home/test:/bin/bash\n' "$TEST_OWNER"
    else
        [[ ${TEST_GROUP_EXISTS:-1} == 1 ]] || return 2
        printf 'render:x:2000:\n'
    fi
}
groupmod() { record groupmod "$@"; }
groupadd() { record groupadd "$@"; }
usermod() { record usermod "$@"; }
chown() { record chown "$@"; }
find() { record find "$@"; }
visudo() { record visudo "$@"; return "${TEST_VISUDO_STATUS:-0}"; }
chpasswd() { local value; read -r value; [[ $value == "plasma:${TEST_EXPECTED_PASSWORD:-synthetic}" ]] || return 1; record password-accepted; return "${TEST_PASSWORD_STATUS:-0}"; }
"""

DISPLAY_COMMANDS = (
    IDENTITY
    + r"""
unset HOST_DISPLAY HOST_XAUTHORITY HOST_WAYLAND_DISPLAY
case ${TEST_HOST:-none} in
    wayland) HOST_WAYLAND_DISPLAY=$TEST_ROOT/host/wayland;;
    x11) HOST_DISPLAY=:41; HOST_XAUTHORITY=$TEST_ROOT/host-authority;;
esac
command() {
    if [[ ${1:-} == -v && ${2:-} == kwin_x11 ]]; then [[ ${TEST_X11_AVAILABLE:-1} == 1 ]];
    elif [[ ${1:-} == -v && ${2:-} == startplasma-x11 ]]; then [[ ${TEST_DESKTOP_AVAILABLE:-1} == 1 ]];
    else builtin command "$@"; fi
}
stat() { if [[ $2 == %a ]]; then printf '%s\n' "${TEST_DEVICE_MODE:-660}"; elif [[ $2 == %g ]]; then printf '2000\n'; else /usr/bin/stat "$@"; fi; }
ldconfig() {
    if [[ ${TEST_ALLOCATOR:-0} == 1 ]]; then
        printf 'libnvidia-allocator.so.1 (libc6,x86-64) => %s/libs/allocator.so\nlibgbm.so.1 (libc6,x86-64) => %s/libs/libgbm.so\n' "$TEST_ROOT" "$TEST_ROOT"
    fi
}
runuser() {
    record runuser "$@"
    if [[ ${@: -1} == --validate ]]; then return "${TEST_VALIDATE_STATUS:-0}"; fi
    printf '{"devices":[{"hardware":true,"vendor":"%s","renderer":"synthetic"}]}\n' "${TEST_GPU_VENDOR:-AMD}"
    return "${TEST_GPU_STATUS:-0}"
}
xauth() {
    record xauth "$@"
    if [[ $* == *nlist* ]]; then printf '0000deadbeef\n';
    elif [[ $* == *nmerge* ]]; then cat >/dev/null; fi
}
mcookie() { printf '0123456789abcdef0123456789abcdef\n'; }
x11vnc() { record x11vnc "$@"; }
python3() {
    if [[ ${1:-} == - ]]; then
        shift
        command python3 -c $'import errno,json,os,sys\ndef capability(path,name):\n    with open(os.path.join(os.environ["TEST_ROOT"],"capabilities.jsonl"),"a") as stream: stream.write(json.dumps([path,name])+"\\n")\n    number=int(os.environ.get("TEST_CAP_REMOVE_ERROR",str(errno.ENODATA)))\n    if number: raise OSError(number,os.strerror(number),path)\nos.removexattr=capability\nexec(compile(sys.stdin.read(),"<capability-fixture>","exec"))' "$@"
    else command python3 "$@"; fi
}
"""
)


class ShellInitTests(unittest.TestCase):
    run_source = runtime_tests.ShellRuntimeTests.run_source

    def user(self, **environment):
        return self.run_source(
            MODULES / "300-plasma-user.sh",
            before=IDENTITY,
            replacements=(("/etc/sudoers.d", "sudoers"), ("/home/plasma", "home")),
            after="module_plasma_user_enabled; module_plasma_user_run",
            environment=environment,
            setup=lambda root: (root / "password").write_text("synthetic\n"),
        )

    def test_identity_defaults_passwords_and_uid_gid_changes(self):
        for values, sudo, changed, password in (
            ({}, "plasma ALL=(ALL) ALL\n", False, False),
            (
                {
                    "PLASMA_UID": "2000",
                    "PLASMA_GID": "3000",
                    "TEST_GROUP_EXISTS": "0",
                    "PLASMA_SUDO_NOPASSWD": "true",
                    "PLASMA_PASSWORD": "synthetic",
                },
                "plasma ALL=(ALL) NOPASSWD: ALL\n",
                True,
                True,
            ),
            (
                {
                    "PLASMA_UID": "2000",
                    "PLASMA_GID": "3000",
                    "TEST_GROUP_EXISTS": "1",
                    "TEST_OWNER": "plasma",
                },
                "plasma ALL=(ALL) ALL\n",
                True,
                False,
            ),
        ):
            with self.subTest(values=values):
                result, commands, _, files = self.user(**values)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(files["sudoers/90-plasma"].decode(), sudo)
                self.assertEqual(
                    any(command[0] == "usermod" for command in commands), changed
                )
                self.assertEqual(["password-accepted"] in commands, password)
                if changed:
                    self.assertTrue(any(command[0] == "find" for command in commands))

    def test_identity_rejects_invalid_inputs_and_failed_mutations(self):
        for values, message in (
            ({"TEST_CURRENT_UID": "1000"}, "Run PID 1 as root"),
            ({"PLASMA_SUDO_NOPASSWD": "invalid"}, "must be true or false"),
            ({"PLASMA_PASSWORD": ""}, "nonempty single line"),
            ({"PLASMA_PASSWORD": "bad\npassword"}, "nonempty single line"),
            ({"PLASMA_UID": "0"}, "nonzero numeric"),
            ({"PLASMA_GID": "4294967295"}, "nonzero numeric"),
            ({"PLASMA_UID": "other"}, "nonzero numeric"),
            ({"TEST_OWNER": "somebody"}, "already belongs"),
            ({"TEST_VISUDO_STATUS": "1"}, "Invalid sudoers"),
            (
                {"PLASMA_PASSWORD": "synthetic", "TEST_PASSWORD_STATUS": "1"},
                "Could not set",
            ),
        ):
            with self.subTest(message=message, values=values):
                result, _, _, _ = self.user(**values)
                self.assertEqual(result.returncode, 23)
                self.assertIn(message, result.stderr)

    def test_password_file_is_checked_and_takes_precedence(self):
        for exists in (True, False):
            result, commands, _, _ = self.run_source(
                MODULES / "300-plasma-user.sh",
                before=IDENTITY
                + f'PLASMA_PASSWORD_FILE="$TEST_ROOT/{"password" if exists else "missing"}"\n',
                setup=lambda root: (root / "password").write_text("synthetic\n"),
                replacements=(("/etc/sudoers.d", "sudoers"), ("/home/plasma", "home")),
                after="module_plasma_user_run",
                environment={"PLASMA_PASSWORD": "ignored"},
            )
            self.assertEqual(result.returncode, 0 if exists else 23, result.stderr)
            self.assertEqual(["password-accepted"] in commands, exists)

    def display_setup(self, root, *, host="none", modern=False, private_binary=True):
        (root / "libs").mkdir()
        (root / "libs/allocator.so").touch()
        (root / "libs/libgbm.so").touch()
        (root / "plasma").mkdir()
        if modern:
            (root / "plasma/opengl-software-capture").touch()
        (root / "host-authority").write_text("synthetic-cookie")
        (root / "host/X11").mkdir(parents=True)
        if host != "none":
            path = root / ("host/wayland" if host == "wayland" else "host/X11/X41")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.bind(str(path))
        (root / "dev/dri").mkdir(parents=True)
        (root / "dev/dri/renderD999").symlink_to("/dev/null")
        binary = root / (
            "private/bin/kwin_wayland" if private_binary else "distro-kwin"
        )
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()
        gpu = root / "gpu"
        gpu.write_text('#!/bin/bash\nexit "${TEST_REQUIRE_SOFTWARE:-1}"\n')
        gpu.chmod(0o755)

    def display(
        self,
        *,
        host="none",
        modern=False,
        private_binary=True,
        extra="",
        repeat=False,
        **environment,
    ):
        return self.run_source(
            MODULES / "400-plasma.sh",
            replacements=(
                ("/run/host", "host"),
                ("/etc/plasma", "plasma"),
                ("/tmp/.X11-unix", "x11"),
                ("/etc/systemd", "systemd"),
                ("/dev/dri", "dev/dri"),
                ("/dev/nvidia", "dev/nvidia"),
                ("/dev/udmabuf", "dev/udmabuf"),
                ("/usr/local/libexec/plasma-gpu", "gpu"),
                ("/opt/kwin-6.8.2", "private"),
                ("/usr/bin/kwin_wayland", "distro-kwin"),
            ),
            setup=lambda root: self.display_setup(
                root, host=host, modern=modern, private_binary=private_binary
            ),
            before=DISPLAY_COMMANDS + extra,
            environment={"TEST_HOST": host, "VNC_PASSWORD": "testpass", **environment},
            after="module_plasma_enabled; module_plasma_run"
            + ("; module_plasma_run" if repeat else ""),
        )

    def test_display_profiles_write_the_actual_backend_transport_and_capture_policy(
        self,
    ):
        for backend, mode, host, software, modern in (
            ("auto", "headless", "none", "0", False),
            ("wayland", "vnc", "none", "1", False),
            ("wayland", "web", "none", "1", True),
            ("wayland", "nested", "x11", "1", True),
            ("wayland", "nested", "wayland", "0", True),
            ("x11", "nested", "x11", "0", False),
            ("auto", "nested", "x11", "0", False),
        ):
            with self.subTest(backend=backend, mode=mode, host=host, software=software):
                result, commands, _, files = self.display(
                    host=host,
                    modern=modern,
                    DISPLAY_BACKEND=backend,
                    DISPLAY_MODE=mode,
                    LIBGL_ALWAYS_SOFTWARE=software,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                session = dict(
                    shlex.split(line)[0].split("=", 1)
                    for line in files["plasma/session.env"].decode().splitlines()
                )
                self.assertEqual(session["DISPLAY_MODE"], mode)
                self.assertEqual(session["HOST_DISPLAY_BACKEND"], host)
                expected_backend = (
                    "wayland"
                    if backend == "auto" and host == "none"
                    else host
                    if backend == "auto"
                    else backend
                )
                self.assertEqual(session["DISPLAY_BACKEND"], expected_backend)
                self.assertEqual(
                    session["PLASMA_SOFTWARE_NESTED"],
                    str(
                        int(
                            mode == "nested"
                            and host == "x11"
                            and software == "1"
                            and modern
                        )
                    ),
                )
                if mode != "nested":
                    self.assertEqual(session["CLIPBOARD_SYNC"], "0")
                if mode in ("vnc", "web"):
                    self.assertTrue(
                        any(
                            command[:2] == ["x11vnc", "-storepasswd"]
                            for command in commands
                        )
                    )

    def test_gpu_detection_fallback_nvidia_sync_and_allocator_are_scoped(self):
        for values, private_binary, expected in (
            ({"TEST_REQUIRE_SOFTWARE": "0"}, True, "1"),
            ({"TEST_GPU_STATUS": "1"}, True, "1"),
            (
                {
                    "TEST_GPU_VENDOR": "NVIDIA",
                    "TEST_ALLOCATOR": "1",
                    "TEST_GROUP_EXISTS": "0",
                },
                True,
                "0",
            ),
            ({"TEST_GPU_VENDOR": "NVIDIA", "TEST_DEVICE_MODE": "666"}, False, "0"),
        ):
            with self.subTest(values=values, private_binary=private_binary):
                result, _, _, files = self.display(
                    host="wayland",
                    DISPLAY_MODE="nested",
                    DISPLAY_BACKEND="wayland",
                    private_binary=private_binary,
                    **values,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                session = dict(
                    shlex.split(line)[0].split("=", 1)
                    for line in files["plasma/session.env"].decode().splitlines()
                )
                self.assertEqual(session["LIBGL_ALWAYS_SOFTWARE"], expected)
                if values.get("TEST_GPU_VENDOR") == "NVIDIA":
                    self.assertEqual(session["PLASMA_NVIDIA_SYNC"], "1")

    def test_nested_preload_capabilities_are_independent_of_nvidia_egl_sync(self):
        for host, backend, mode, vendor, software, remove, nvidia in (
            ("wayland", "wayland", "nested", "AMD", "0", True, "0"),
            ("wayland", "wayland", "nested", "AMD", "1", True, "0"),
            ("wayland", "wayland", "nested", "NVIDIA", "1", True, "0"),
            ("x11", "wayland", "nested", "NVIDIA", "0", True, "1"),
            ("x11", "wayland", "nested", "AMD", "0", False, "0"),
            ("x11", "wayland", "nested", "NVIDIA", "1", False, "0"),
            ("wayland", "x11", "nested", "NVIDIA", "0", False, "0"),
            ("wayland", "wayland", "headless", "NVIDIA", "0", False, "0"),
        ):
            with self.subTest(
                host=host, backend=backend, mode=mode, vendor=vendor, software=software
            ):
                result, _, _, files = self.display(
                    host=host,
                    DISPLAY_BACKEND=backend,
                    DISPLAY_MODE=mode,
                    LIBGL_ALWAYS_SOFTWARE=software,
                    TEST_GPU_VENDOR=vendor,
                    TEST_CAP_REMOVE_ERROR="0" if remove else str(errno.EPERM),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [
                    json.loads(line)
                    for line in files.get("capabilities.jsonl", b"").splitlines()
                ]
                self.assertEqual(len(calls), int(remove))
                if remove:
                    self.assertTrue(calls[0][0].endswith("/private/bin/kwin_wayland"))
                    self.assertEqual(calls[0][1], "security.capability")
                session = dict(
                    shlex.split(line)[0].split("=", 1)
                    for line in files["plasma/session.env"].decode().splitlines()
                )
                self.assertEqual(session["PLASMA_NVIDIA_SYNC"], nvidia)

    def test_capability_removal_is_idempotent_for_private_and_distro_kwin(self):
        for private_binary in (False, True):
            for error in (0, errno.ENODATA):
                with self.subTest(private_binary=private_binary, error=error):
                    result, _, _, files = self.display(
                        host="wayland",
                        DISPLAY_MODE="nested",
                        LIBGL_ALWAYS_SOFTWARE="1",
                        private_binary=private_binary,
                        repeat=True,
                        TEST_CAP_REMOVE_ERROR=str(error),
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = [
                        json.loads(line)
                        for line in files["capabilities.jsonl"].splitlines()
                    ]
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0], calls[1])
                    expected = (
                        "/private/bin/kwin_wayland"
                        if private_binary
                        else "/distro-kwin"
                    )
                    self.assertTrue(calls[0][0].endswith(expected))
                    self.assertEqual(calls[0][1], "security.capability")

    def test_capability_errors_fail_before_publishing_a_session_environment(self):
        for software in ("0", "1"):
            with self.subTest(software=software):
                result, _, _, files = self.display(
                    host="wayland",
                    DISPLAY_MODE="nested",
                    LIBGL_ALWAYS_SOFTWARE=software,
                    TEST_CAP_REMOVE_ERROR=str(errno.EPERM),
                )
                self.assertEqual(result.returncode, 23)
                self.assertIn("Cannot prepare nested KWin preloads", result.stderr)
                self.assertIn("Operation not permitted", result.stderr)
                self.assertNotIn("plasma/session.env", files)
                self.assertEqual(len(files["capabilities.jsonl"].splitlines()), 1)

    def test_invalid_display_requests_fail_before_starting_services(self):
        for values, message in (
            ({"TEST_CURRENT_UID": "1"}, "Run PID 1 as root"),
            ({"DISPLAY_MODE": "unknown"}, "Invalid DISPLAY_MODE"),
            ({"DISPLAY_BACKEND": "unknown"}, "Invalid DISPLAY_BACKEND"),
            ({"TARGET_APP": "app", "TEST_VALIDATE_STATUS": "1"}, "Invalid TARGET_APP"),
            ({"VNC_MAX_FPS": "61"}, "VNC_MAX_FPS"),
            ({"CLIPBOARD_SYNC": "2"}, "CLIPBOARD_SYNC"),
            ({"LIBGL_ALWAYS_SOFTWARE": "2"}, "LIBGL_ALWAYS_SOFTWARE"),
            ({"DISPLAY_WIDTH": "9"}, "Display dimensions"),
            ({"DISPLAY_MODE": "nested"}, "nested mode requires"),
            (
                {"DISPLAY_BACKEND": "x11", "TEST_X11_AVAILABLE": "0"},
                "no usable KWin X11",
            ),
            (
                {"DISPLAY_MODE": "vnc", "VNC_PASSWORD": "too-long-password"},
                "printable ASCII",
            ),
        ):
            with self.subTest(values=values):
                result, _, _, _ = self.display(**values)
                self.assertEqual(result.returncode, 23)
                self.assertIn(message, result.stderr)

    def test_session_writer_escapes_literals_and_rejects_multiline_values(self):
        for value, expected in ((r'a\b"c', 0), ("a\nb", 23), ("a\rb", 23)):
            result, _, _, _ = self.run_source(
                MODULES / "400-plasma.sh",
                before=IDENTITY,
                after='plasma_write_env KEY "$TEST_VALUE"',
                environment={"TEST_VALUE": value},
            )
            self.assertEqual(result.returncode, expected)
            if not expected:
                self.assertEqual(shlex.split(result.stdout), ["KEY=" + value])

    def test_display_discovery_accepts_relative_wayland_and_private_socket_aliases(
        self,
    ):
        for host, extra, values, expected in (
            (
                "wayland",
                "unset HOST_WAYLAND_DISPLAY WAYLAND_DISPLAY DISPLAY\n",
                {},
                "wayland",
            ),
            (
                "wayland",
                'HOST_WAYLAND_DISPLAY=wayland\nHOST_XDG_RUNTIME_DIR="$TEST_ROOT/host"\nunset DISPLAY\n',
                {"TARGET_APP": "application"},
                "wayland",
            ),
            (
                "x11",
                "unset HOST_DISPLAY DISPLAY HOST_WAYLAND_DISPLAY WAYLAND_DISPLAY\n",
                {},
                "x11",
            ),
            ("x11", "unset DISPLAY\n", {"TEST_DESKTOP_AVAILABLE": "0"}, "wayland"),
        ):
            with self.subTest(host=host, extra=extra):
                result, _, _, files = self.display(
                    host=host,
                    DISPLAY_MODE="nested",
                    DISPLAY_BACKEND="auto",
                    extra=extra,
                    **values,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                session = dict(
                    shlex.split(line)[0].split("=", 1)
                    for line in files["plasma/session.env"].decode().splitlines()
                )
                self.assertEqual(session["DISPLAY_BACKEND"], expected)
                if values.get("TARGET_APP"):
                    self.assertEqual(session["QT_QPA_PLATFORMTHEME"], "generic")

    def test_missing_host_sockets_and_gpu_capability_failures_are_explicit(self):
        for host, extra, values, message in (
            (
                "none",
                'HOST_WAYLAND_DISPLAY="$TEST_ROOT/missing"\n',
                {},
                "not a mounted socket",
            ),
            ("none", "HOST_DISPLAY=:88\n", {}, "mounted local X11 socket"),
            (
                "wayland",
                "",
                {"TEST_GPU_VENDOR": "NVIDIA", "TEST_CAP_REMOVE_ERROR": "1"},
                "Cannot prepare nested KWin preloads",
            ),
        ):
            with self.subTest(message=message):
                result, _, _, _ = self.display(
                    host=host, DISPLAY_MODE="nested", extra=extra, **values
                )
                self.assertEqual(result.returncode, 23)
                self.assertIn(message, result.stderr)

    def test_password_files_and_existing_remote_outputs_are_materialized(self):
        for exists in (True, False):
            extra = 'VNC_PASSWORD_FILE="$TEST_ROOT/vnc-password"\ntouch "$TEST_ROOT/plasma/vnc-existing-output"\n'
            if exists:
                extra += 'printf filepass > "$VNC_PASSWORD_FILE"\n'
            result, _, _, files = self.display(DISPLAY_MODE="vnc", extra=extra)
            self.assertEqual(result.returncode, 0 if exists else 23, result.stderr)
            if exists:
                self.assertEqual(files["plasma/vnc-password"], b"filepass")
                self.assertIn(
                    b'PLASMA_VNC_EXISTING_OUTPUT="1"', files["plasma/session.env"]
                )
            else:
                self.assertIn("VNC_PASSWORD_FILE is not readable", result.stderr)


if __name__ == "__main__":
    unittest.main()
