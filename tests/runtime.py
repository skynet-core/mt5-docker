"""Boot compositor profiles with a test marker and inspect real framebuffers."""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

from clipboard import configuration
from clipboard import validate as validate_clipboard
from display_matrix import case_path, check_screenshot, profiles
from PIL import Image, ImageChops, ImageFilter
from rfb import RFBClient


def execute(*arguments, check=True):
    return subprocess.run(arguments, capture_output=True, check=check)


def collect(runtime, container, output):
    for filename, command in (
        ("container.log", ["logs", container]),
        (
            "services.log",
            [
                "exec",
                container,
                "journalctl",
                "-b",
                "--no-pager",
                "-u",
                "plasma-*.service",
            ],
        ),
        ("units.log", ["exec", container, "systemctl", "--failed", "--no-pager"]),
    ):
        result = execute(runtime, *command, check=False)
        (output / filename).write_bytes(result.stdout + result.stderr)


def ready(runtime, container):
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        result = execute(
            runtime, "exec", container, "/usr/local/bin/plasma-healthcheck", check=False
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Compositor profile did not become healthy within 110 seconds")


def remote(runtime, container, mode):
    port = 6080 if mode == "web" else 5900
    address = execute(runtime, "port", container, f"{port}/tcp").stdout.decode().strip()
    published = int(address.rsplit(":", 1)[1])
    if mode == "web":
        with urllib.request.urlopen(
            f"http://127.0.0.1:{published}/vnc.html", timeout=10
        ) as response:
            if response.status != 200:
                raise RuntimeError("noVNC page was not served")
    deadline = time.monotonic() + 30
    while True:
        try:
            return RFBClient(published, web=mode == "web")
        except (OSError, RuntimeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def screenshot(client, path):
    deadline = time.monotonic() + 25
    while True:
        image = client.screenshot()
        colors = image.resize((160, 100)).getcolors(16000) or []
        marker = sum(
            count
            for count, pixel in colors
            if max(abs(a - b) for a, b in zip(pixel, (18, 210, 178), strict=True)) < 15
        )
        image.save(path)
        if marker > 50:
            return marker
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Remote framebuffer did not show the test application's green marker"
            )
        time.sleep(0.25)


def capture_metadata(stdout):
    for line in stdout.decode(errors="replace").splitlines():
        try:
            metadata = json.loads(line)
        except ValueError:
            continue
        if isinstance(metadata, dict) and metadata.get("method") in (
            "kwin-screenshot2",
            "x11-root",
        ):
            return metadata
    raise RuntimeError("Capture helper did not return valid JSON metadata on stdout")


def capture_output(runtime, container, session, output):
    """Capture the compositor's real output without starting a remote server."""
    deadline = time.monotonic() + 25
    while True:
        result = execute(
            runtime,
            "exec",
            "--user=plasma",
            container,
            "env",
            *[f"{key}={value}" for key, value in session.items()],
            "/usr/local/bin/plasma-ui-fixture",
            "--capture-output",
            "/tmp/display-output.png",
            check=False,
        )
        with (output / "capture.log").open("ab") as log:
            log.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        # Docker's archive API can miss files inside a container's /tmp tmpfs.
        (output / "screenshot.png").write_bytes(
            execute(runtime, "exec", container, "cat", "/tmp/display-output.png").stdout
        )
        try:
            marker = check_screenshot(output / "screenshot.png")
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    metadata = capture_metadata(result.stdout)
    return {"marker_pixels": marker, "capture_method": metadata["method"]}


def input_points(image):
    """Locate the fixture's banner, then its editor and Save button."""
    rgb = image.convert("RGB")

    def bounds(color):
        difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color))
        mask = difference.point(lambda value: 255 if value < 15 else 0)
        red, green, blue = mask.split()
        # Ignore subpixel text edges that happen to resemble a control color.
        mask = ImageChops.multiply(ImageChops.multiply(red, green), blue)
        box = mask.filter(ImageFilter.MinFilter(5)).getbbox()
        if not box:
            raise RuntimeError(f"Cannot locate GUI control color {color}")
        return box

    left, _top, _right, bottom = bounds((18, 210, 178))
    left_button, top_button, right_button, bottom_button = bounds((36, 88, 180))
    return (left + 80, bottom + 60), (
        (left_button + right_button) // 2,
        (top_button + bottom_button) // 2,
    )


def gui_input(runtime, container, client, output):
    """Type a unique token and click Save; only the UI may create typed.txt."""
    expected = f"Display input AaZz 0123!? {uuid.uuid4().hex}"
    (output / "expected.txt").write_text(expected)
    execute(runtime, "exec", container, "rm", "-f", "/tmp/typed.txt")
    image = client.screenshot()
    editor, save = input_points(image)
    client.click(*editor)
    client.chord(0xFFE3, ord("a"))
    client.text(expected)
    client.click(*save)
    deadline = time.monotonic() + 15
    while True:
        result = execute(
            runtime, "exec", container, "cat", "/tmp/typed.txt", check=False
        )
        if result.returncode == 0 and result.stdout == expected.encode():
            (output / "typed.txt").write_bytes(result.stdout)
            break
        if time.monotonic() >= deadline:
            (output / "input-error.log").write_bytes(result.stdout + result.stderr)
            screenshot(client, output / "input-failure.png")
            raise RuntimeError(
                "Mouse/keyboard input did not save the expected GUI text"
            )
        time.sleep(0.2)
    screenshot(client, output / "screenshot.png")
    return {
        "passed": True,
        "method": "rfb",
        "expected": "expected.txt",
        "saved": "typed.txt",
        "screenshot": "screenshot.png",
        "editor_click": editor,
        "save_click": save,
    }


def session_identity(session, backend, mode, host):
    actual = {
        key: session.get(key)
        for key in ("DISPLAY_BACKEND", "DISPLAY_MODE", "HOST_DISPLAY_BACKEND")
    }
    expected = {
        "DISPLAY_BACKEND": backend,
        "DISPLAY_MODE": mode,
        "HOST_DISPLAY_BACKEND": host or "none",
    }
    if actual != expected:
        raise RuntimeError(
            f"Actual display session {actual!r} differs from {expected!r}"
        )
    return actual


def headless_services(runtime, container):
    states = {}
    for unit in ("plasma-vnc.service", "plasma-web.service"):
        result = execute(
            runtime, "exec", container, "systemctl", "is-active", unit, check=False
        )
        states[unit] = result.stdout.decode().strip()
        if states[unit] != "inactive":
            raise RuntimeError(
                f"Headless display unexpectedly started {unit}: {states[unit]}"
            )
    return states


def capture_startup(runtime, container, mode, output, session):
    """Request pixels before the capture producer can deliver its first frame."""
    if session.get("PLASMA_SOFTWARE_CAPTURE") == "1":
        return {"capture_startup_skipped": "X11 capture does not use PipeWire"}
    execute(runtime, "exec", container, "systemctl", "stop", "plasma-vnc.service")

    def producer(signal):
        execute(
            runtime,
            "exec",
            container,
            "systemctl",
            "kill",
            f"--signal={signal}",
            "--kill-whom=main",
            "plasma-pipewire.service",
        )

    client = None
    producer("STOP")
    try:
        execute(
            runtime,
            "exec",
            container,
            "systemctl",
            "start",
            "--no-block",
            "plasma-vnc.service",
        )
        client = remote(runtime, container, mode)
        image = client.screenshot()
        image.save(output / "capture-before-first-frame.png")
        colors = image.getcolors(image.width * image.height) or []
        black = sum(count for count, pixel in colors if pixel == (0, 0, 0))
        if black < image.width * image.height * 0.95:
            raise RuntimeError("Initial capture framebuffer was not initialized")
        producer("CONT")
        return {
            "delayed_capture_marker_pixels": screenshot(
                client, output / "capture-after-first-frame.png"
            )
        }
    finally:
        producer("CONT")
        if client:
            client.close()


def start(
    runtime,
    image,
    name,
    backend,
    mode,
    output,
    extra=(),
    sleep=False,
    gpu=False,
    fixture=None,
):
    arguments = [
        runtime,
        "run",
        "-d",
        "--name",
        name,
        "--no-healthcheck",
        # systemctl mask cannot replace this custom unit's existing /etc file.
        "--mount",
        "type=bind,source=/dev/null,target=/etc/systemd/system/mt5-mcp.service,readonly",
        "--mount",
        f"type=bind,source={Path(__file__).with_name('plasma-ui-fixture.desktop').resolve()},target=/usr/share/applications/plasma-ui-fixture.desktop,readonly",
        "--privileged",
        "--cgroupns=private",
        "--pids-limit=8192",
        "--shm-size=256m",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/tmp:mode=1777",
        "-e",
        f"DISPLAY_BACKEND={backend}",
        "-e",
        f"DISPLAY_MODE={mode}",
        "-e",
        "DISPLAY_WIDTH=800",
        "-e",
        "DISPLAY_HEIGHT=600",
        "-e",
        f"LIBGL_ALWAYS_SOFTWARE={0 if gpu else 1}",
        "-e",
        f"PLASMA_UID={os.getuid() or 1000}",
        "-e",
        f"PLASMA_GID={os.getgid() or 1000}",
        "-e",
        "SYSTEMD_MASKED=NetworkManager.service,systemd-udev-trigger.service",
        "-e",
        "VNC_PASSWORD=testpass",
        "-e",
        "TARGET_APP=/usr/bin/sleep infinity"
        if sleep
        else "TARGET_APP=/usr/local/bin/plasma-ui-fixture /tmp/typed.txt",
    ]
    if gpu:
        arguments.extend(
            [
                "--gpus",
                "all",
                "-e",
                "NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display",
            ]
        )
    if mode in ("vnc", "web"):
        arguments.extend(["-p", f"127.0.0.1::{5900 if mode == 'vnc' else 6080}"])
    if fixture is not None:
        arguments.extend(
            [
                "--mount",
                f"type=bind,source={fixture},target=/usr/local/bin/plasma-ui-fixture,readonly",
            ]
        )
    arguments.extend(extra)
    arguments.append(image)
    result = execute(*arguments, check=False)
    (output / "start.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(
            f"Container start failed: {result.stderr.decode(errors='replace')}"
        )
    ready(runtime, name)


def parent_x11(runtime, parent, output):
    # Force lazy Xwayland startup using the wrapper's client cookie.
    program = """
import glob,json,os,subprocess
from pathlib import Path
number=min(int(path.name[1:]) for path in Path('/tmp/.X11-unix').glob('X[0-9]*'))
auth=glob.glob('/run/plasma/xauth_*')[0]
subprocess.run(['xdpyinfo'],env={**os.environ,'DISPLAY':f':{number}','XAUTHORITY':auth},stdout=subprocess.DEVNULL,check=True)
print(json.dumps({'DISPLAY':f':{number}','XAUTHORITY':auth}))
"""
    settings = json.loads(
        execute(runtime, "exec", parent, "python3", "-c", program).stdout
    )
    cookie = execute(runtime, "exec", parent, "cat", settings["XAUTHORITY"]).stdout
    path = output / "Xauthority"
    path.write_bytes(cookie)
    path.chmod(0o600)
    return settings["DISPLAY"], path


def main(args):
    runtime = args.runtime
    fixture = Path(args.fixture).resolve(strict=True) if args.fixture else None
    if fixture is not None and (
        not fixture.is_file() or not os.access(fixture, os.X_OK)
    ):
        raise ValueError("--fixture must name an executable test window")
    output = Path(args.output).resolve() / args.case
    output.mkdir(parents=True, exist_ok=False)
    image_id = (
        execute(runtime, "image", "inspect", "--format", "{{.Id}}", args.image)
        .stdout.decode()
        .strip()
    )
    parent_output = output / "parent"
    parent_output.mkdir()
    (parent_output / "runtime").mkdir(mode=0o700)
    (parent_output / "x11").mkdir(mode=0o1777)
    parent = f"mt5-runtime-parent-{os.getpid()}"
    parent_client = None
    reports = []
    try:
        start(
            runtime,
            args.parent_image or args.image,
            parent,
            "wayland",
            "vnc",
            parent_output,
            [
                "--mount",
                f"type=bind,source={parent_output / 'runtime'},target=/run/plasma",
                "--mount",
                f"type=bind,source={parent_output / 'x11'},target=/tmp/.X11-unix",
            ],
            sleep=True,
            gpu=args.gpu,
            fixture=fixture,
        )
        parent_client = remote(runtime, parent, "vnc")
        host_display, host_auth = parent_x11(runtime, parent, parent_output)
        for backend, mode, host in profiles(args.backends):
            label = f"{backend}-{mode}" + (f"-on-{host}" if host else "")
            relative = case_path(backend, mode, host)
            case = output / relative
            case.mkdir(parents=True)
            name = f"mt5-runtime-{os.getpid()}-{label}"
            report = {
                "case": label,
                "image": args.image,
                "image_id": image_id,
                "backend": backend,
                "mode": mode,
                "host_backend": host or "none",
                "evidence": relative.as_posix(),
                "screenshot": "screenshot.png",
                "passed": False,
            }
            client = None
            print(f"Checking compositor profile {args.case}/{label}", flush=True)
            try:
                extra = []
                if host == "wayland":
                    extra = [
                        "--mount",
                        f"type=bind,source={parent_output / 'runtime/wayland-0'},target=/run/host/wayland,readonly",
                        "-e",
                        "HOST_WAYLAND_DISPLAY=/run/host/wayland",
                    ]
                elif host == "x11":
                    extra = [
                        "--mount",
                        f"type=bind,source={parent_output / 'x11'},target=/run/host/X11,readonly",
                        "--mount",
                        f"type=bind,source={host_auth},target=/run/host/Xauthority,readonly",
                        "-e",
                        f"HOST_DISPLAY={host_display}",
                        "-e",
                        "HOST_XAUTHORITY=/run/host/Xauthority",
                    ]
                start(
                    runtime,
                    args.image,
                    name,
                    backend,
                    mode,
                    case,
                    extra,
                    gpu=args.gpu,
                    fixture=fixture,
                )
                report["healthy"] = True
                session = configuration(runtime, name)
                report["session"] = session_identity(session, backend, mode, host)
                program = "import json,platform; r=platform.freedesktop_os_release(); print(json.dumps({k:r[k] for k in ('ID','VERSION_ID') if k in r}))"
                report["os_release"] = json.loads(
                    execute(runtime, "exec", name, "python3", "-c", program).stdout
                )
                actual_image = (
                    execute(runtime, "inspect", "--format", "{{.Image}}", name)
                    .stdout.decode()
                    .strip()
                )
                if actual_image != image_id:
                    raise RuntimeError(
                        "Container image changed during the display matrix"
                    )
                (case / "session.json").write_text(json.dumps(session, indent=2))
                if session.get("QT_QPA_PLATFORMTHEME") != "generic":
                    raise RuntimeError(
                        "Kiosk services did not receive the safe generic Qt theme"
                    )
                gpu = json.loads(
                    execute(runtime, "exec", name, "cat", "/etc/plasma/gpu.json").stdout
                )
                (case / "gpu.json").write_text(json.dumps(gpu, indent=2))
                overlay = (
                    backend == "wayland"
                    and execute(
                        runtime,
                        "exec",
                        name,
                        "test",
                        "-x",
                        "/opt/kwin-6.8.2/bin/kwin_wayland",
                        check=False,
                    ).returncode
                    == 0
                )
                if overlay:
                    program = """
import json
from pathlib import Path
libraries=set()
for process in Path('/proc').glob('[0-9]*'):
    try:
        if (process/'comm').read_text().strip() == 'kwin_wayland':
            for line in (process/'maps').read_text().splitlines():
                if '/libQt6' in line and '.so' in line:
                    libraries.add(line.split()[-1])
    except (FileNotFoundError, ProcessLookupError):
        continue
print(json.dumps(sorted(libraries)))
"""
                    libraries = json.loads(
                        execute(runtime, "exec", name, "python3", "-c", program).stdout
                    )
                    (case / "compositor-qt-libraries.json").write_text(
                        json.dumps(libraries, indent=2)
                    )
                    if not libraries or any(
                        not path.startswith("/opt/kwin-6.8.2/lib/")
                        for path in libraries
                    ):
                        raise RuntimeError(
                            "Compositor mixed overlay Qt with distro Qt libraries"
                        )
                if args.gpu:
                    report["hardware_gpu_available"] = any(
                        device.get("hardware") for device in gpu["devices"]
                    )
                    if not report["hardware_gpu_available"]:
                        raise RuntimeError(
                            "Requested GPU could not create a hardware EGL context"
                        )
                    support = subprocess.run(
                        [
                            runtime,
                            "exec",
                            "--user=plasma",
                            name,
                            "env",
                            *[f"{key}={value}" for key, value in session.items()],
                            "qdbus-qt6",
                            "org.kde.KWin",
                            "/KWin",
                            "supportInformation",
                        ],
                        capture_output=True,
                        check=True,
                        timeout=10,
                    ).stdout.decode()
                    (case / "compositor-renderer.log").write_text(support)
                    for field, version_label in (
                        ("kwin_version", "KWin version"),
                        ("qt_version", "Qt Version"),
                    ):
                        value = re.search(
                            r"^" + version_label + r":\s*(.+)",
                            support,
                            re.MULTILINE,
                        )
                        report[field] = value.group(1) if value else None
                    if overlay and (
                        report["kwin_version"] != "6.8.2"
                        or report["qt_version"] != "6.11.2"
                    ):
                        raise RuntimeError(
                            "Overlay compositor did not report KWin 6.8.2 with Qt 6.11.2"
                        )
                    renderer = re.search(
                        r"^OpenGL renderer string:\s*(.+)", support, re.MULTILINE
                    )
                    report["renderer"] = renderer.group(1) if renderer else None
                    report["software_fallback"] = (
                        session["LIBGL_ALWAYS_SOFTWARE"] == "1"
                    )
                    report["hardware_rendering"] = bool(renderer) and not any(
                        token in renderer.group(1).lower()
                        for token in (
                            "llvmpipe",
                            "softpipe",
                            "software rasterizer",
                            "lavapipe",
                        )
                    )
                    if (
                        not report["software_fallback"]
                        and not report["hardware_rendering"]
                    ):
                        raise RuntimeError(
                            "Compositor did not use the requested hardware renderer"
                        )
                if mode == "headless":
                    report["headless_services"] = headless_services(runtime, name)
                    report["headless_private"] = True
                    report.update(capture_output(runtime, name, session, case))
                elif mode in ("vnc", "web"):
                    if args.capture_startup and backend == "wayland" and mode == "vnc":
                        report.update(
                            capture_startup(runtime, name, mode, case, session)
                        )
                    client = remote(runtime, name, mode)
                    report["marker_pixels"] = screenshot(
                        client, case / "framebuffer.png"
                    )
                    report["rfb_authenticated"] = True
                    report["capture_method"] = "rfb"
                    report["gui_input"] = gui_input(runtime, name, client, case)
                elif mode == "nested":
                    report["marker_pixels"] = screenshot(
                        parent_client, case / "parent-framebuffer.png"
                    )
                    report["capture_method"] = "parent-rfb"
                    report["gui_input"] = gui_input(runtime, name, parent_client, case)
                    validate_clipboard(
                        argparse.Namespace(
                            runtime=runtime,
                            parent=parent,
                            container=name,
                            case=label,
                            output=str(case / "clipboard"),
                        )
                    )
                report["gui_rendered"] = True
                report["passed"] = True
            # Preserve diagnostics and continue independent matrix cases.
            except Exception as error:  # noqa: BLE001
                report["error"] = f"{type(error).__name__}: {error}"
                (case / "error.log").write_text(traceback.format_exc())
                print(f"FAILED {label}: {error}", flush=True)
            finally:
                if client:
                    client.close()
                collect(runtime, name, case)
                execute(runtime, "rm", "-f", "-v", name, check=False)
                (case / "results.json").write_text(json.dumps(report, indent=2) + "\n")
                reports.append(report)
    finally:
        if parent_client:
            parent_client.close()
        collect(runtime, parent, parent_output)
        execute(runtime, "rm", "-f", "-v", parent, check=False)
        # Display sockets and test-only auth cookies are not retained as artifacts.
        for path in (parent_output / "runtime", parent_output / "x11"):
            shutil.rmtree(path)
        (parent_output / "Xauthority").unlink(missing_ok=True)
        (output / "results.json").write_text(
            json.dumps(
                {
                    "image": args.image,
                    "image_id": image_id,
                    "cases": reports,
                    "passed": bool(reports) and all(case["passed"] for case in reports),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Compositor evidence: {output}", flush=True)
    if not reports or not all(case["passed"] for case in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Runtime-test image or production image with --fixture",
    )
    parser.add_argument(
        "--fixture",
        help="Bind a distro-matched plasma-ui-fixture executable into every test container",
    )
    parser.add_argument(
        "--parent-image", help="Use an already verified minimal runtime as parent"
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Expose NVIDIA GPUs and verify KWin's actual renderer",
    )
    parser.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    parser.add_argument(
        "--capture-startup",
        action="store_true",
        help="Delay PipeWire and request pixels before its first captured frame when that capture path is active",
    )
    parser.add_argument(
        "--backends", nargs="+", choices=("wayland", "x11"), default=("wayland", "x11")
    )
    parser.add_argument("--output", default="artifacts/runtime")
    parser.add_argument("--case", required=True)
    main(parser.parse_args())
