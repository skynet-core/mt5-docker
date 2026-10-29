"""Exercise MT5 mouse and keyboard through private nested, VNC and browser sessions."""

import argparse
import base64
import contextlib
import io
import json
import os
import select
import subprocess
import time
import uuid
from pathlib import Path

from clipboard import configuration
from display_matrix import case_path, profiles
from PIL import Image
from playwright.sync_api import sync_playwright
from rfb import RFBClient
from runtime import collect, execute, parent_x11, session_identity, start


class BrowserClient:
    def __init__(self, port, output, host="127.0.0.1"):
        self.driver = sync_playwright().start()
        self.output = output
        self.browser = self.context = self.page = None
        try:
            self.browser = self.driver.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                viewport={"width": 1400, "height": 1100}
            )
            self.context.tracing.start(screenshots=True, snapshots=True)
            self.page = self.context.new_page()
            self.page.goto(
                f"http://{host}:{port}/vnc.html?autoconnect=true&resize=off&password=testpass"
            )
            self.canvas = self.page.locator("#noVNC_container canvas")
            self.canvas.wait_for(state="visible", timeout=30000)
            self.page.wait_for_function(
                "document.querySelector('#noVNC_container canvas')?.width >= 800"
            )
            # The canvas receives frames before noVNC's connecting overlay fades.
            self.page.locator("#noVNC_transition").wait_for(
                state="hidden", timeout=30000
            )
        except Exception:
            with contextlib.suppress(Exception):
                self.close()
            raise

    def screenshot(self):
        data = self.canvas.evaluate(
            "canvas => canvas.toDataURL('image/png').split(',')[1]"
        )
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")

    def click(self, x, y):
        box = self.canvas.bounding_box()
        width, height = self.canvas.evaluate("canvas => [canvas.width, canvas.height]")
        self.page.mouse.move(
            box["x"] + x * box["width"] / width, box["y"] + y * box["height"] / height
        )
        time.sleep(0.1)
        self.page.mouse.down()
        time.sleep(0.08)
        self.page.mouse.up()

    def key(self, keysym, down):
        name = {0xFF1B: "Escape", 0xFFE3: "Control"}.get(keysym, chr(keysym))
        (self.page.keyboard.down if down else self.page.keyboard.up)(name)
        time.sleep(0.02)

    def chord(self, modifier, key):
        self.key(modifier, True)
        try:
            self.page.keyboard.press(chr(key), delay=20)
        finally:
            self.key(modifier, False)

    def text(self, value):
        for character in value:
            # noVNC forwards physical modifiers; uppercase key values alone
            # become lowercase through servers that inject physical keycodes.
            shifted = character in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ~!@#$%^&*()_+{}|:"<>?'
            if shifted:
                self.page.keyboard.down("Shift")
            try:
                self.page.keyboard.type(character, delay=15)
            finally:
                if shifted:
                    self.page.keyboard.up("Shift")

    def close(self):
        try:
            if self.context is not None:
                try:
                    self.context.tracing.stop(
                        path=str(self.output / "browser-trace.zip")
                    )
                finally:
                    if self.page is not None:
                        self.page.screenshot(path=str(self.output / "browser.png"))
        finally:
            try:
                if self.browser is not None:
                    self.browser.close()
            finally:
                self.driver.stop()


class Probe:
    def __init__(self, runtime, container, output):
        source = Path(__file__).with_name("mt5_ui_probe.py")
        execute(
            runtime,
            "cp",
            str(source),
            container + ":/home/plasma/.wine/drive_c/MT5/mt5_ui_probe.py",
        )
        environment = json.loads(
            execute(
                runtime, "exec", container, "cat", "/run/plasma/mt5-environment.json"
            ).stdout
        )
        self.log = (output / "probe.log").open("w")
        self.states = (output / "gui-state.jsonl").open("w")
        self.process = subprocess.Popen(
            [
                runtime,
                "exec",
                "-i",
                "--user=plasma",
                container,
                "env",
                *[f"{key}={value}" for key, value in environment.items()],
                "wine",
                r"C:\Python\python.exe",
                "-u",
                r"C:\MT5\mt5_ui_probe.py",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            bufsize=1,
        )

    def state(self, expected=""):
        self.process.stdin.write(json.dumps({"expected": expected}) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 30)[0]:
            raise RuntimeError("MT5 GUI inspector timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("MT5 GUI inspector exited; inspect probe.log")
        state = json.loads(line)
        self.states.write(json.dumps(state) + "\n")
        self.states.flush()
        return state

    def close(self):
        try:
            with contextlib.suppress(BrokenPipeError):
                self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            self.log.close()
            self.states.close()


def wait_state(probe, predicate, expected="", timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        state = probe.state(expected)
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError("MT5 GUI state did not respond to display input")
        time.sleep(0.2)


def center(rectangle):
    return (rectangle[0] + rectangle[2]) // 2, (rectangle[1] + rectangle[3]) // 2


def visible_frame(client, destination, timeout=25):
    deadline = time.monotonic() + timeout
    while True:
        image = client.screenshot()
        image.save(destination)
        colors = image.resize((160, 100)).getcolors(16000) or []
        if (
            len(colors) > 64
            and sum(count for count, pixel in colors if max(pixel) > 32) > 800
        ):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("MT5 framebuffer is black or blank")
        time.sleep(0.2)


def connect(runtime, container, mode, output):
    # Internal networks block Internet access; the host can reach their private IP.
    address = (
        execute(
            runtime,
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container,
        )
        .stdout.decode()
        .strip()
    )
    if not address:
        raise RuntimeError("Private MT5 network did not assign an address")
    if mode == "web":
        return BrowserClient(6080, output, host=address)
    return RFBClient(5900, host=address)


def exercise(client, probe, output):
    def phase(name):
        (output / "phase.txt").write_text(name + "\n")

    phase("waiting for company-search dialog")
    state = wait_state(
        probe,
        lambda value: (
            value["main"]
            and any(
                item.get("edit") and item["root"] != value["main"]
                for item in value["controls"]
            )
        ),
        timeout=120,
    )
    phase("waiting for visible framebuffer")
    visible_frame(client, output / "startup.png")
    edit = max(
        (
            item
            for item in state["controls"]
            if item.get("edit") and item["root"] != state["main"]
        ),
        key=lambda item: item["rect"][1],
    )
    expected = "MT5Input-AaZz-0123-" + uuid.uuid4().hex
    assert not any(item.get("matches") for item in probe.state(expected)["controls"])
    phase("focusing company-search edit")
    client.click(*center(edit["rect"]))
    wait_state(probe, lambda value: value.get("focus") == edit["hwnd"])
    seed = "MT5Input-seed-456"
    phase("typing initial text")
    client.text(seed)
    wait_state(
        probe,
        lambda value: any(item.get("matches") for item in value["controls"]),
        seed,
    )
    phase("replacing nonempty text with Ctrl+A")
    client.chord(0xFFE3, ord("a"))
    client.text(expected)
    state = wait_state(
        probe,
        lambda value: any(
            item["hwnd"] == edit["hwnd"] and item.get("matches")
            for item in value["controls"]
        ),
        expected,
    )
    observed = next(
        item["text"] for item in state["controls"] if item["hwnd"] == edit["hwnd"]
    )
    if observed != expected:
        raise RuntimeError("MT5 edit text did not equal keyboard input")
    (output / "expected.txt").write_text(expected)
    (output / "typed.txt").write_text(observed)
    client.screenshot().save(output / "keyboard.png")
    phase("clicking Cancel")
    button = next(
        item
        for item in state["controls"]
        if item.get("cancel") and item["root"] == edit["root"]
    )
    client.click(*center(button["rect"]))
    wait_state(
        probe,
        lambda value: (
            not any(item["hwnd"] == edit["hwnd"] for item in value["controls"])
        ),
    )
    state = wait_state(probe, lambda value: value.get("main_enabled"))
    phase("clicking File menu")
    client.click(*center(state["file_rect"]))
    wait_state(probe, lambda value: bool(value["menus"]))
    client.screenshot().save(output / "mouse-menu.png")
    phase("closing File menu with Escape")
    client.key(0xFF1B, True)
    client.key(0xFF1B, False)
    wait_state(probe, lambda value: not value["menus"])
    client.screenshot().save(output / "after-input.png")
    phase("passed")
    return {
        "passed": True,
        "mouse": "Company-search edit focused, Cancel closed, File menu opened",
        "keyboard": "Mixed-case text, Ctrl+A replacement and Escape reached MT5",
        "expected": "expected.txt",
        "readback": "typed.txt",
        "oracle": "Win32 WM_GETTEXT; no Win32 input injection",
    }


def main(args):
    root = Path(args.output).resolve() / args.case
    root.mkdir(parents=True, exist_ok=False)
    parent_output = root / "parent"
    parent_output.mkdir()
    (parent_output / "runtime").mkdir(mode=0o700)
    (parent_output / "x11").mkdir(mode=0o1777)
    parent = f"mt5-input-parent-{os.getpid()}"
    network = f"mt5-input-private-{os.getpid()}"
    reports = []
    parent_client = None
    execute(args.runtime, "network", "create", "--internal", network)
    try:
        start(
            args.runtime,
            args.parent_image or args.image,
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
            dimensions=(1280, 960),
        )
        parent_client = connect(args.runtime, parent, "vnc", parent_output)
        host_display, auth = parent_x11(args.runtime, parent, parent_output)
        for backend, mode, host in profiles(args.backends):
            if mode not in args.modes:
                continue
            output = root / case_path(backend, mode, host)
            output.mkdir(parents=True)
            name = f"mt5-input-{os.getpid()}-{backend}-{mode}-{host or 'none'}"
            report = {
                "backend": backend,
                "mode": mode,
                "host_backend": host,
                "passed": False,
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
                    "MT5_CONFIG_FILE=/etc/mt5/config.ini",
                    "-e",
                    "MT5_PASSWORD=",
                    "-e",
                    "MT5_SERVER=",
                ]
                for unit in ("mt5-native-mcp.socket", "mt5-editor-mcp.socket"):
                    extra += [
                        "--mount",
                        f"type=bind,source=/dev/null,target=/etc/systemd/system/{unit},readonly",
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
                    application="/usr/local/bin/mt5-launch",
                    dimensions=(1280, 960),
                )
                session = configuration(args.runtime, name)
                report["session"] = session_identity(session, backend, mode, host)
                support = execute(
                    args.runtime,
                    "exec",
                    "--user=plasma",
                    name,
                    "env",
                    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/plasma/bus",
                    "QT_QPA_PLATFORM=offscreen",
                    "QT_QPA_PLATFORMTHEME=generic",
                    "qdbus-qt6",
                    "org.kde.KWin",
                    "/KWin",
                    "supportInformation",
                    check=False,
                )
                (output / "compositor.log").write_bytes(support.stdout + support.stderr)
                client = (
                    parent_client
                    if mode == "nested"
                    else connect(args.runtime, name, mode, output)
                )
                probe = Probe(args.runtime, name, output)
                report["input"] = exercise(client, probe, output)
                report["transport"] = (
                    "browser-noVNC"
                    if mode == "web"
                    else "private-parent-rfb"
                    if mode == "nested"
                    else "rfb"
                )
                report["passed"] = True
            except Exception as error:
                report["error"] = f"{type(error).__name__}: {error}"
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.screenshot().save(output / "failure.png")
            finally:
                for resource in (
                    probe,
                    client if client is not parent_client else None,
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
                f"MT5 input {args.case}/{backend}/{mode}/{host}: {'PASS' if report['passed'] else report.get('error')}",
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
    expected_cases = sum(mode in args.modes for _, mode, _ in profiles(args.backends))
    passed = (
        len(reports) == expected_cases
        and bool(reports)
        and all(row["passed"] for row in reports)
    )
    (root / "results.json").write_text(
        json.dumps(
            {
                "cases": reports,
                "passed": passed,
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--parent-image")
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", default="artifacts/mt5-input")
    parser.add_argument(
        "--backends", nargs="+", choices=("wayland", "x11"), default=["wayland", "x11"]
    )
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("nested", "vnc", "web"),
        default=["nested", "vnc", "web"],
    )
    raise SystemExit(main(parser.parse_args()))
