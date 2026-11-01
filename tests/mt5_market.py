"""Verify actual Market painting via Navigator, browser diagnostics and display pixels."""

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from clipboard import configuration
from display_matrix import DISTROS, case_path, check_os_release, profiles
from mt5_input import BrowserClient, Probe, connect, control_point, wait_state
from mt5_market_probe import page_ready
from PIL import Image, ImageChops, ImageStat
from runtime import (
    capture_metadata,
    collect,
    execute,
    headless_services,
    parent_x11,
    require_gpu,
    session_identity,
    start,
)

MT5 = "/home/plasma/.wine/drive_c/MT5"
XTEST = """import ctypes,ctypes.util,json,sys,time
x=ctypes.CDLL(ctypes.util.find_library('X11')); t=ctypes.CDLL(ctypes.util.find_library('Xtst'))
x.XOpenDisplay.argtypes=[ctypes.c_char_p]; x.XOpenDisplay.restype=ctypes.c_void_p
x.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong]; x.XKeysymToKeycode.restype=ctypes.c_ubyte
x.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]; x.XCloseDisplay.argtypes=[ctypes.c_void_p]
t.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
t.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
t.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
d=x.XOpenDisplay(None)
if not d: raise RuntimeError('Cannot open the private compositor X11 display')
def key(keysym,down):
    code=x.XKeysymToKeycode(d,keysym)
    if not code: raise RuntimeError('Keysym has no native keycode')
    if not t.XTestFakeKeyEvent(d,code,down,0): raise RuntimeError('XTest key failed')
try:
    if sys.argv[1]=='key':
        key(int(sys.argv[2]),int(sys.argv[3]))
    elif sys.argv[1]=='keys':
        for keysym,down in json.loads(sys.argv[2]):
            key(keysym,down); x.XSync(d,0); time.sleep(0.02)
    else:
        if not t.XTestFakeMotionEvent(d,-1,int(sys.argv[2]),int(sys.argv[3]),0): raise RuntimeError('XTest motion failed')
        if not t.XTestFakeButtonEvent(d,1,1,0): raise RuntimeError('XTest button failed')
        x.XSync(d,0); time.sleep(0.08)
        if sys.argv[1]=='drag':
            for step in range(1,11):
                if not t.XTestFakeMotionEvent(d,-1,int(sys.argv[2])+round(int(sys.argv[4])*step/10),int(sys.argv[3])+round(int(sys.argv[5])*step/10),0): raise RuntimeError('XTest drag failed')
                x.XSync(d,0); time.sleep(0.04)
        if not t.XTestFakeButtonEvent(d,1,0,0): raise RuntimeError('XTest button release failed')
    x.XSync(d,0)
finally: x.XCloseDisplay(d)
"""


def compare_frame(reference, observed):
    if reference.size != observed.size:
        raise RuntimeError("Market browser and display crop dimensions disagree")
    if reference.convert("RGBA").getchannel("A").getextrema() != (255, 255):
        raise RuntimeError("Market browser screenshot is transparent")
    reference, observed = reference.convert("RGB"), observed.convert("RGB")
    ink = reference.convert("L").point(lambda value: 255 if value < 200 else 0)
    count = ink.histogram()[255]
    if count < 64:
        raise RuntimeError("Market browser screenshot has no painted text/content")
    if not any(ImageStat.Stat(reference).stddev):
        raise RuntimeError("Market browser screenshot has no painted contrast")
    difference = ImageChops.difference(reference, observed)
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(red, ImageChops.lighter(green, blue))
    matches = ImageChops.multiply(
        ink, maximum.point(lambda value: 255 if value <= 48 else 0)
    )
    evidence = {
        "mean_error": sum(ImageStat.Stat(difference).mean) / 3,
        "painted_pixel_match": matches.histogram()[255] / count,
    }
    if evidence["mean_error"] > 12 or evidence["painted_pixel_match"] < 0.9:
        raise RuntimeError(
            "Market display does not match the painted browser; blank or chart bleed"
        )
    return {"passed": True, **evidence}


def browser_crop(image, native):
    left, top, width, height = native["desktop"]
    rectangle = native["browser"]
    if (
        width <= 0
        or height <= 0
        or not (
            left <= rectangle[0] < rectangle[2] <= left + width
            and top <= rectangle[1] < rectangle[3] <= top + height
        )
    ):
        raise RuntimeError("Market browser is outside the visible desktop")
    return image.crop(
        tuple(
            round((value - origin) * extent / desktop)
            for value, origin, extent, desktop in zip(
                rectangle,
                (left, top, left, top),
                image.size * 2,
                (width, height) * 2,
                strict=True,
            )
        )
    )


def compositor_frame(runtime, container, session, destination):
    environment = dict(session)
    if session.get("PLASMA_SOFTWARE_CAPTURE") == "1":
        environment.update(
            DISPLAY=session["PLASMA_SOFTWARE_DISPLAY"], QT_QPA_PLATFORM="xcb"
        )
        environment.pop("WAYLAND_DISPLAY", None)
    result = execute(
        runtime,
        "exec",
        "--user=plasma",
        container,
        "env",
        *[f"{key}={value}" for key, value in environment.items()],
        "/usr/local/bin/plasma-ui-fixture",
        "--capture-output",
        "/tmp/market-display.png",
        check=False,
    )
    with (destination.parent / "capture.log").open("ab") as log:
        log.write(result.stdout + result.stderr)
    result.check_returncode()
    destination.write_bytes(
        execute(runtime, "exec", container, "cat", "/tmp/market-display.png").stdout
    )
    image = Image.open(destination).convert("RGB")
    metadata = capture_metadata(result.stdout)
    destination.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    return image, metadata


class HeadlessClient:
    def __init__(self, runtime, container, environment, capture, output=None):
        self.runtime, self.container, self.environment, self.capture = (
            runtime,
            container,
            dict(environment),
            capture,
        )
        self.output = output
        self.windows = (
            environment.get("DISPLAY_BACKEND") == "wayland"
            and environment.get("PLASMA_SOFTWARE_CAPTURE") != "1"
        )
        if environment.get("PLASMA_SOFTWARE_CAPTURE") == "1":
            self.environment["DISPLAY"] = environment["PLASMA_SOFTWARE_DISPLAY"]

    def input(self, *arguments):
        if self.windows:
            wine(self.runtime, self.container, "input", self.output, *arguments)
        else:
            execute(
                self.runtime,
                "exec",
                "--user=plasma",
                self.container,
                "env",
                *[
                    f"{key}={self.environment[key]}"
                    for key in ("DISPLAY", "XAUTHORITY")
                ],
                "python3",
                "-c",
                XTEST,
                *map(str, arguments),
            )
        time.sleep(0.02)

    def key(self, keysym, down):
        self.input("key", keysym, int(down))

    def click(self, x, y):
        self.input("click", round(x), round(y))

    def drag(self, x, y, dx, dy):
        self.input("drag", round(x), round(y), round(dx), round(dy))

    def chord(self, modifier, key):
        self.key(modifier, True)
        try:
            self.key(key, True)
            self.key(key, False)
        finally:
            self.key(modifier, False)

    def text(self, text):
        events = []
        for character in text:
            if character.isupper():
                events.append([0xFFE1, 1])
            events.extend([[ord(character.lower()), 1], [ord(character.lower()), 0]])
            if character.isupper():
                events.append([0xFFE1, 0])
        self.input("keys", json.dumps(events))

    def screenshot(self):
        return self.capture()


def wine(runtime, container, action, output, *arguments):
    environment = json.loads(
        execute(
            runtime, "exec", container, "cat", "/run/plasma/mt5-environment.json"
        ).stdout
    )
    result = subprocess.run(
        [
            runtime,
            "exec",
            "--user=plasma",
            container,
            "env",
            *[f"{key}={value}" for key, value in environment.items()],
            "wine",
            r"C:\Python\python.exe",
            r"C:\MT5\mt5_market_probe.py",
            action,
            *map(str, arguments),
        ],
        capture_output=True,
        timeout=90,
        check=False,
    )
    with (output / "windows.log").open("ab") as log:
        log.write(result.stderr)
    result.check_returncode()
    return json.loads(result.stdout)


def rendering(runtime, container, output, gpu_requested):
    session = configuration(runtime, container)
    (output / "session.json").write_text(json.dumps(session, indent=2))
    gpu = execute(runtime, "exec", container, "cat", "/etc/plasma/gpu.json").stdout
    (output / "gpu.json").write_bytes(gpu)
    support = execute(
        runtime,
        "exec",
        "--user=plasma",
        container,
        "env",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/plasma/bus",
        "QT_QPA_PLATFORM=offscreen",
        "QT_QPA_PLATFORMTHEME=generic",
        "qdbus-qt6",
        "org.kde.KWin",
        "/KWin",
        "supportInformation",
    ).stdout
    (output / "compositor.log").write_bytes(support)
    return session, require_gpu(
        json.loads(gpu), session, support.decode()
    ) if gpu_requested else {}


def presented_frame(reference, native, capture, output, label):
    deadline = time.monotonic() + 10
    attempt = 0
    while True:
        attempt += 1
        image = capture()
        image.save(output / f"{label}-attempt-{attempt}.png")
        try:
            result = compare_frame(reference, browser_crop(image, native))
            image.save(output / f"{label}.png")
            return {**result, "attempts": attempt}
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            # Presentation is asynchronous; no input is repeated while waiting.
            time.sleep(0.2)


def paint(args, name, session, client, mode, output, compositor=None):
    output.mkdir()
    filenames = (
        "market-cdp.png",
        "market-native.png",
        "market-window.png",
        "market-cdp.jsonl",
    )
    execute(
        args.runtime,
        "exec",
        name,
        "rm",
        "-f",
        *[f"{MT5}/{filename}" for filename in filenames],
    )
    try:
        observation = wine(args.runtime, name, "observe", output)
    finally:
        with (output / "copy.log").open("ab") as log:
            for filename in filenames:
                copied = execute(
                    args.runtime,
                    "cp",
                    f"{name}:{MT5}/{filename}",
                    str(output / filename),
                    check=False,
                )
                log.write(copied.stdout + copied.stderr)
    (output / "cdp.json").write_text(json.dumps(observation, indent=2))
    if not page_ready(observation):
        raise RuntimeError("Market has no positive DOM, paint and animation evidence")
    reference = Image.open(output / "market-cdp.png").copy()
    result = {
        "native": observation["native"],
        "viewport": observation["dom"]["size"],
        "frames": observation["frames"],
        "native_paint": compare_frame(
            reference, Image.open(output / "market-native.png")
        ),
    }
    capture_name, capture_session = compositor or (name, session)
    result["compositor_paint"] = presented_frame(
        reference,
        observation["native"],
        lambda: compositor_frame(
            args.runtime,
            capture_name,
            capture_session,
            output / "market-compositor-current.png",
        )[0],
        output,
        "market-compositor",
    )
    result["capture"] = json.loads(
        (output / "market-compositor-current.json").read_text()
    )
    result["capture"]["scope"] = "parent" if capture_name != name else "child"
    if mode != "headless":
        result["transport_paint"] = presented_frame(
            reference,
            observation["native"],
            client.screenshot,
            output,
            "market-transport",
        )
    return result


def open_market(client, probe, navigator):
    wait_state(
        probe,
        lambda value: (
            value.get("main_enabled") and value.get("foreground") == value.get("main")
        ),
        timeout=30,
    )
    client.click(*control_point(client, probe, navigator["navigator"]))
    wait_state(probe, lambda value: value.get("focus") == navigator["navigator_hwnd"])
    client.text("Market")
    if isinstance(client, BrowserClient):
        client.page.keyboard.press("Enter")
    else:
        client.key(0xFF0D, True)
        client.key(0xFF0D, False)


def resize_market(client, probe, native):
    frame, content = native["market"], native["market_client"]
    grip = [(frame[2] + content[2]) / 2, (frame[3] + content[3]) / 2]
    delta = [-(frame[2] - frame[0]) / 5, -(frame[3] - frame[1]) / 6]
    start = control_point(client, probe, [*grip, *grip])
    end = control_point(client, probe, [grip[0] + delta[0], grip[1] + delta[1]] * 2)
    if isinstance(client, BrowserClient):
        client.pointer(*start)
        client.page.mouse.down()
        try:
            for step in range(1, 11):
                client.pointer(
                    *(
                        start[index] + (end[index] - start[index]) * step / 10
                        for index in range(2)
                    )
                )
                time.sleep(0.04)
        finally:
            client.page.mouse.up()
    else:
        client.drag(*start, *(end[index] - start[index] for index in range(2)))


def main(args):
    root = Path(args.output).resolve() / args.case
    root.mkdir(parents=True, exist_ok=False)
    selected = [
        profile for profile in profiles(args.backends) if profile[1] in args.modes
    ]
    image_id = (
        execute(args.runtime, "image", "inspect", "--format", "{{.Id}}", args.image)
        .stdout.decode()
        .strip()
    )
    parent_id = (
        execute(
            args.runtime, "image", "inspect", "--format", "{{.Id}}", args.parent_image
        )
        .stdout.decode()
        .strip()
    )
    source = Path(__file__).resolve().parents[1]
    metadata = {
        "image": args.image,
        "image_id": image_id,
        "parent_image_id": parent_id,
        "gpu_requested": args.gpu,
        "route": "Navigator tree type-ahead Market, then Enter",
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source
        )
        .decode()
        .strip(),
        "worktree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=source)
        ),
        "source_sha256": {
            str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                Path(__file__).with_name("mt5_market_probe.py"),
                Path(__file__).with_name("mt5_ui_probe.py"),
                Path(__file__).with_name("runtime.py"),
                Path(__file__).with_name("mt5_input.py"),
            )
        },
    }
    (root / "source.json").write_text(json.dumps(metadata, indent=2))
    fixture_container = (
        execute(args.runtime, "create", args.parent_image).stdout.decode().strip()
    )
    fixture = root / "plasma-ui-fixture"
    try:
        execute(
            args.runtime,
            "cp",
            f"{fixture_container}:/usr/local/bin/plasma-ui-fixture",
            str(fixture),
        )
        fixture.chmod(0o755)
    finally:
        execute(args.runtime, "rm", "-fv", fixture_container, check=False)
    parent_output = root / "parent"
    parent_output.mkdir()
    (parent_output / "runtime").mkdir(mode=0o700)
    (parent_output / "x11").mkdir(mode=0o1777)
    parent = f"mt5-market-parent-{os.getpid()}"
    network = f"mt5-market-private-{os.getpid()}"
    parent_client = None
    reports = []
    execute(args.runtime, "network", "create", "--internal", network)
    try:
        start(
            args.runtime,
            args.parent_image,
            parent,
            "wayland",
            "vnc",
            parent_output,
            [
                f"--network={network}",
                "--mount",
                f"type=bind,source={parent_output / 'runtime'},target=/run/plasma",
                "--mount",
                f"type=bind,source={parent_output / 'x11'},target=/tmp/.X11-unix",
            ],
            sleep=True,
            gpu=args.gpu,
            fixture=fixture,
            dimensions=(1280, 960),
        )
        parent_session, _ = rendering(args.runtime, parent, parent_output, args.gpu)
        parent_client = connect(args.runtime, parent, "vnc", parent_output)
        host_display, auth = parent_x11(args.runtime, parent, parent_output)
        for backend, mode, host in selected:
            output = root / case_path(backend, mode, host)
            output.mkdir(parents=True)
            name = f"mt5-market-{os.getpid()}-{backend}-{mode}-{host or 'none'}"
            report = {
                **metadata,
                "backend": backend,
                "mode": mode,
                "host_backend": host or "none",
                "passed": False,
                "phase": "startup",
            }
            client = probe = None
            try:
                extra = [
                    f"--network={network}",
                    "-e",
                    "MT5_ACCOUNT=",
                    "-e",
                    "MT5_LOGIN=",
                    "-e",
                    "MT5_PASSWORD=",
                    "-e",
                    "MT5_SERVER=",
                    "-e",
                    "MT5_CONFIG_FILE=/etc/mt5/config.ini",
                ]
                if host == "wayland":
                    extra += [
                        "--mount",
                        f"type=bind,source={parent_output / 'runtime/wayland-0'},target=/run/host/wayland,readonly",
                        "-e",
                        "HOST_WAYLAND_DISPLAY=/run/host/wayland",
                    ]
                elif host == "x11":
                    extra += [
                        "--mount",
                        f"type=bind,source={parent_output / 'x11'},target=/run/host/X11,readonly",
                        "--mount",
                        f"type=bind,source={auth},target=/run/host/Xauthority,readonly",
                        "-e",
                        f"HOST_DISPLAY={host_display}",
                        "-e",
                        "HOST_XAUTHORITY=/run/host/Xauthority",
                    ]
                start(
                    args.runtime,
                    args.image,
                    name,
                    backend,
                    mode,
                    output,
                    extra,
                    gpu=args.gpu,
                    fixture=fixture,
                    application="/usr/local/bin/mt5-launch",
                    dimensions=(1280, 960),
                )
                actual_image = (
                    execute(args.runtime, "inspect", "--format", "{{.Image}}", name)
                    .stdout.decode()
                    .strip()
                )
                if actual_image != image_id:
                    raise RuntimeError(
                        "Market case did not use the selected immutable image"
                    )
                os_release = json.loads(
                    execute(
                        args.runtime,
                        "exec",
                        name,
                        "python3",
                        "-c",
                        "import json,platform; print(json.dumps(platform.freedesktop_os_release()))",
                    ).stdout
                )
                if not check_os_release(os_release, args.case):
                    raise RuntimeError(
                        "Market case distribution differs from requested distro"
                    )
                report["os_release"] = {
                    key: os_release[key]
                    for key in ("ID", "VERSION_ID")
                    if key in os_release
                }
                session, gpu = rendering(args.runtime, name, output, args.gpu)
                report.update(gpu)
                report["session"] = session_identity(session, backend, mode, host)
                probe = Probe(args.runtime, name, output)
                execute(
                    args.runtime,
                    "cp",
                    str(Path(__file__).with_name("mt5_market_probe.py")),
                    f"{name}:{MT5}/mt5_market_probe.py",
                )
                if mode == "headless":
                    environment = json.loads(
                        execute(
                            args.runtime,
                            "exec",
                            name,
                            "cat",
                            "/run/plasma/mt5-environment.json",
                        ).stdout
                    )
                    client = HeadlessClient(
                        args.runtime,
                        name,
                        {**environment, **session},
                        lambda name=name, session=session, output=output: (
                            compositor_frame(
                                args.runtime,
                                name,
                                session,
                                output / "headless-input.png",
                            )[0]
                        ),
                        output,
                    )
                    report["headless_input"] = {
                        "method": "Win32 SendInput" if client.windows else "XTest",
                        "display": client.environment["DISPLAY"],
                    }
                    report["headless_services"] = headless_services(args.runtime, name)
                else:
                    client = (
                        parent_client
                        if mode == "nested"
                        else connect(args.runtime, name, mode, output)
                    )
                state = wait_state(
                    probe,
                    lambda value: (
                        bool(value["main"])
                        and any(control.get("cancel") for control in value["controls"])
                    ),
                    timeout=120,
                )
                cancel = next(
                    control for control in state["controls"] if control.get("cancel")
                )
                client.click(*control_point(client, probe, cancel["rect"]))
                (output / "diagnostic-policy.json").write_text(
                    json.dumps(wine(args.runtime, name, "policy", output), indent=2)
                )
                navigator = wine(args.runtime, name, "native", output)
                client.screenshot().save(output / "before-market.png")
                report["phase"] = "open-market"
                open_market(client, probe, navigator)
                report["paint"] = {}
                compositor = (parent, parent_session) if mode == "nested" else None
                report["phase"] = "initial-paint"
                report["paint"]["initial"] = paint(
                    args, name, session, client, mode, output / "initial", compositor
                )
                report["phase"] = "resize"
                resize_market(client, probe, report["paint"]["initial"]["native"])
                report["paint"]["resized"] = paint(
                    args, name, session, client, mode, output / "resized", compositor
                )
                if (
                    report["paint"]["resized"]["viewport"]
                    == report["paint"]["initial"]["viewport"]
                ):
                    raise RuntimeError("Market ignored the real resize input")
                report["phase"] = "close-reopen"
                if isinstance(client, BrowserClient):
                    client.page.keyboard.press("Control+F4")
                else:
                    client.chord(0xFFE3, 0xFFC1)
                closed = wine(args.runtime, name, "native", output)
                (output / "closed.json").write_text(json.dumps(closed, indent=2))
                if "browser" in closed:
                    raise RuntimeError("Market did not close through keyboard input")
                open_market(client, probe, closed)
                report["paint"]["reopened"] = paint(
                    args, name, session, client, mode, output / "reopened", compositor
                )
                report.update(passed=True, phase="complete")
            except Exception as error:
                report["error"] = f"{type(error).__name__}: {error}"
                (output / "failure.log").write_text(traceback.format_exc())
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.screenshot().save(output / "failure.png")
            finally:
                execute(
                    args.runtime,
                    "cp",
                    f"{name}:{MT5}/market-cdp.jsonl",
                    str(output / "market-cdp.jsonl"),
                    check=False,
                )
                processes = execute(
                    args.runtime,
                    "exec",
                    name,
                    "ps",
                    "-eo",
                    "pid,ppid,args",
                    check=False,
                )
                (output / "processes.log").write_bytes(
                    processes.stdout + processes.stderr
                )
                for resource in (
                    probe,
                    client if mode not in ("nested", "headless") else None,
                ):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception as error:
                            report["passed"] = False
                            report.setdefault(
                                "error",
                                f"Cleanup failed: {type(error).__name__}: {error}",
                            )
                try:
                    collect(args.runtime, name, output)
                finally:
                    execute(args.runtime, "rm", "-fv", name, check=False)
                (output / "results.json").write_text(json.dumps(report, indent=2))
                reports.append(report)
                print(
                    f"Market {args.case}/{backend}/{mode}/{host}: {'PASS' if report['passed'] else report.get('error')}; evidence: {output}",
                    flush=True,
                )
    finally:
        try:
            if parent_client:
                parent_client.close()
        finally:
            try:
                collect(args.runtime, parent, parent_output)
            finally:
                execute(args.runtime, "rm", "-fv", parent, check=False)
                execute(args.runtime, "network", "rm", network, check=False)
    passed = (
        len(reports) == len(selected)
        and bool(reports)
        and all(report["passed"] for report in reports)
    )
    (root / "results.json").write_text(
        json.dumps(
            {
                **metadata,
                "passed": passed,
                "expected_cases": len(selected),
                "cases": reports,
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--parent-image",
        required=True,
        help="Matching runtime-test image with its capture helper",
    )
    parser.add_argument("--case", required=True, choices=DISTROS)
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--output", default="artifacts/mt5-market")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument(
        "--backends", nargs="+", choices=("wayland", "x11"), default=["wayland", "x11"]
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("headless", "nested", "vnc", "web"),
        default=["headless", "nested", "vnc", "web"],
    )
    raise SystemExit(main(parser.parse_args()))
