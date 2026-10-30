"""Exercise MT5 mouse and keyboard through private nested, VNC and browser sessions."""

import argparse
import base64
import contextlib
import csv
import hashlib
import io
import json
import math
import os
import re
import select
import subprocess
import time
import uuid
from pathlib import Path

from clipboard import configuration
from display_matrix import case_path, profiles
from kwin_driver import KWinDriver
from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright
from rfb import RFBClient
from runtime import (
    capture_metadata,
    collect,
    execute,
    parent_x11,
    session_identity,
    start,
)

CHART_TARGETS = (
    ("ChartInput-red", (203, 41, 97), (100, 62)),
    ("ChartInput-blue", (43, 83, 211), (270, 62)),
)
CHART_COMPILE = r"""set -euo pipefail
export DISPLAY=:99 WINEDEBUG=-all LIBGL_ALWAYS_SOFTWARE=1
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac >/out/xvfb.log 2>&1 &
display_pid=$!
trap 'kill "$display_pid" 2>/dev/null || true' EXIT
for ((attempt=0; attempt<100; attempt++)); do
    if xdpyinfo >/dev/null 2>&1; then break; fi
    sleep 0.1
done
xdpyinfo >/dev/null
mkdir -p /home/plasma/.wine/drive_c/MT5/MQL5/Experts
cp /ChartInput.mq5 /home/plasma/.wine/drive_c/MT5/MQL5/Experts/
set +e
timeout 120 wine 'C:\MT5\metaeditor64.exe' /portable '/compile:C:\MT5\MQL5\Experts\ChartInput.mq5' /log
status=$?
set -e
wineserver -k -w
[[ $status == 0 || $status == 1 ]]
cp /home/plasma/.wine/drive_c/MT5/MQL5/Experts/ChartInput.{ex5,log} /out/
"""


def compile_chart(runtime, image, output):
    """Compile before test boot: MetaEditor must never refresh the failing window."""
    output.mkdir()
    # Hosted runners can use uid 1001; the disposable compiler uses plasma/1000.
    output.chmod(0o777)
    source = Path(__file__).with_name("ChartInput.mq5").resolve()
    result = execute(
        runtime,
        "run",
        "--rm",
        "--network=none",
        "--user=plasma",
        "--entrypoint=/bin/bash",
        "--mount",
        f"type=bind,source={source},target=/ChartInput.mq5,readonly",
        "--mount",
        f"type=bind,source={output},target=/out",
        image,
        "-c",
        CHART_COMPILE,
        check=False,
    )
    (output / "compiler.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    binary = output / "ChartInput.ex5"
    log = (output / "ChartInput.log").read_text(encoding="utf-16")
    if not binary.read_bytes() or not re.search(r"\b0 errors?,\s*0 warnings?,", log):
        raise RuntimeError(
            "ChartInput compilation needs a fresh binary and zero errors"
        )
    (output / "sha256.json").write_text(
        json.dumps(
            {
                "source": hashlib.sha256(source.read_bytes()).hexdigest(),
                "binary": hashlib.sha256(binary.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    config = output / "autologin.ini"
    config.write_text(
        "[Common]\nKeepPrivate=1\nNewsEnable=0\n[Experts]\nEnabled=1\n"
        "AllowLiveTrading=0\nAllowDllImport=0\n[StartUp]\n"
        "Expert=ChartInput.ex5\nSymbol=EURUSD\nPeriod=M15\n"
    )
    return binary, config


def chart_target(image, color, scale=1):
    difference = ImageChops.difference(
        image.convert("RGB"), Image.new("RGB", image.size, color)
    )
    red, green, blue = difference.split()
    bounds = (
        ImageChops.lighter(red, ImageChops.lighter(green, blue))
        .point(lambda value: 255 if value < 3 else 0)
        .getbbox()
    )
    width, height = (math.ceil(size * max(1, scale)) for size in (120, 44))
    if not bounds or not (
        20 <= bounds[2] - bounds[0] <= width and 12 <= bounds[3] - bounds[1] <= height
    ):
        raise RuntimeError("Chart input target is absent from the screenshot")
    return bounds


def chart_events(runtime, container, output):
    result = execute(
        runtime,
        "exec",
        container,
        "cat",
        "/home/plasma/.wine/drive_c/MT5/MQL5/Files/chart-input.csv",
        check=False,
    )
    (output / "chart-input.csv").write_bytes(result.stdout)
    if result.returncode and b"No such file or directory" in result.stderr:
        return []
    result.check_returncode()
    text = result.stdout.decode("utf-8-sig")
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if reader.fieldnames != ["event", "name", "x", "y"]:
        raise RuntimeError("Invalid ChartInput CSV header")
    return list(reader)


def autologin_input(runtime, container, client, probe, output, target_scale=1):
    state = wait_state(probe, lambda value: bool(value["main"]), timeout=120)
    if not state["main_enabled"] or any(
        dialog.get("class") != "tooltips_class32" for dialog in state["dialogs"]
    ):
        raise RuntimeError("Autologin regression must start without a native dialog")
    deadline = time.monotonic() + 30
    attempts = []
    while True:
        ready = any(
            row["event"] == "READY" for row in chart_events(runtime, container, output)
        )
        image = client.screenshot()
        capture = f"startup-attempt-{len(attempts) + 1}.png"
        image.save(output / capture)
        targets, reason = [], "ChartInput READY event is absent"
        try:
            targets = [
                (name, chart_target(image, color, target_scale), expected)
                for name, color, expected in CHART_TARGETS
            ]
        except RuntimeError as error:
            reason = str(error)
        attempts.append(
            {
                "capture": capture,
                "ready": ready,
                "targets_visible": bool(targets),
                "reason": None if ready and targets else reason,
            }
        )
        (output / "startup-attempts.json").write_text(
            json.dumps(attempts, indent=2) + "\n"
        )
        if ready and targets:
            image.save(output / "startup.png")
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "ChartInput did not initialize visibly without a native dialog: "
                + reason
            )
        time.sleep(0.1)
    evidence = []
    for name, bounds, expected in targets:
        point = center(bounds)
        client.click(*point)
        deadline = time.monotonic() + 5
        while True:
            events = chart_events(runtime, container, output)
            matches = [
                row
                for row in events
                if row["event"] == "OBJECT_CLICK" and row["name"] == name
            ]
            if matches:
                event = matches[-1]
                observed = [float(event[axis]) for axis in ("x", "y")]
                passed = all(
                    abs(observed[index] - expected[index]) <= 1 for index in range(2)
                )
                break
            if time.monotonic() >= deadline:
                observed, passed = None, False
                break
            time.sleep(0.1)
        client.screenshot().save(output / f"{name}.png")
        evidence.append(
            {
                "target": name,
                "bounds": bounds,
                "click": point,
                "expected_chart_coordinates": expected,
                "observed": observed,
                "passed": passed,
            }
        )
        (output / "chart-input.json").write_text(json.dumps(evidence, indent=2) + "\n")
        if not passed:
            raise RuntimeError("MT5 chart input disagrees with its visible target")
    return {
        "passed": True,
        "startup": "autologin",
        "targets": evidence,
        "oracle": "Screenshot colors and MT5 OnChartEvent; no dialog before clicks",
    }


class BrowserClient:
    def __init__(self, port, output, host="127.0.0.1"):
        self.driver = sync_playwright().start()
        self.output = output
        self.browser = self.context = self.page = None
        self.failed_requests = []
        self.network_change_reload = False
        try:
            self.browser = self.driver.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                viewport={"width": 1400, "height": 1100}
            )
            self.context.tracing.start(screenshots=True, snapshots=True)
            self.page = self.context.new_page()
            self.page.on("requestfailed", self._request_failed)
            for attempt in range(2):
                first_failure = len(self.failed_requests)
                try:
                    if attempt:
                        self.page.reload()
                    else:
                        self.page.goto(
                            f"http://{host}:{port}/vnc.html?autoconnect=true&resize=off&password=testpass"
                        )
                    self.canvas = self.page.locator("#noVNC_container canvas")
                    self.canvas.wait_for(state="visible", timeout=30000)
                    self.page.wait_for_function(
                        "document.querySelector('#noVNC_container canvas')?.width >= 800"
                    )
                    # Frames arrive before noVNC's connecting overlay fades.
                    self.page.locator("#noVNC_transition").wait_for(
                        state="hidden", timeout=30000
                    )
                    break
                except Exception:
                    failures = self.failed_requests[first_failure:]
                    # Docker bridge changes can cancel Chromium module loads.
                    if (
                        attempt
                        or not failures
                        or any(
                            failure["failure"] != "net::ERR_NETWORK_CHANGED"
                            for failure in failures
                        )
                        or not any(
                            failure["resource_type"] in ("document", "script")
                            for failure in failures
                        )
                    ):
                        raise
                    self.network_change_reload = True
        except Exception:
            with contextlib.suppress(Exception):
                self.close()
            raise

    def _request_failed(self, request):
        self.failed_requests.append(
            {
                "url": request.url.split("?", 1)[0].split("#", 1)[0],
                "failure": request.failure,
                "resource_type": request.resource_type,
            }
        )

    def screenshot(self):
        data = self.canvas.evaluate(
            "canvas => canvas.toDataURL('image/png').split(',')[1]"
        )
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")

    def pointer(self, x, y):
        box = self.canvas.bounding_box()
        width, height = self.canvas.evaluate("canvas => [canvas.width, canvas.height]")
        self.page.mouse.move(
            box["x"] + x * box["width"] / width, box["y"] + y * box["height"] / height
        )

    def click(self, x, y):
        self.pointer(x, y)
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
            (self.output / "browser-startup.json").write_text(
                json.dumps(
                    {
                        "failed_requests": self.failed_requests,
                        "network_change_reload": self.network_change_reload,
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            self._close_browser()

    def _close_browser(self):
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


class NestedClient:
    """Map independent Win32 and compositor geometries, never observed offsets."""

    def __init__(self, parent, driver, probe, window, output, viewport_anchor=None):
        self.parent, self.driver, self.probe = parent, driver, probe
        self.window, self.output = window, output
        self.viewport_anchor = viewport_anchor
        self.events = []

    def desktop(self):
        desktop = self.probe.state()["desktop"]
        if desktop[2] <= 0 or desktop[3] <= 0:
            raise RuntimeError("MT5 reported an invalid physical desktop")
        return desktop

    def viewport(self):
        client = dict(self.window["client"])
        if self.viewport_anchor:
            width, height = self.desktop()[2:]
            scale = self.driver.output["scale"]
            width, height = width / scale, height / scale
            if width > client["width"] or height > client["height"]:
                raise RuntimeError("Private relay viewport is clipped")
            # TigerVNC centres its framebuffer; Xephyr's active monitor starts at 0,0.
            if self.viewport_anchor == "center":
                client["x"] += (client["width"] - width) / 2
                client["y"] += (client["height"] - height) / 2
            client.update(width=width, height=height)
        return client

    def framebuffer_point(self, x, y):
        width, height = self.desktop()[2:]
        client = self.viewport()
        geometry = self.driver.output
        logical = (
            client["x"] + x * client["width"] / width,
            client["y"] + y * client["height"] / height,
        )
        framebuffer = [
            round((logical[index] - geometry["origin"][axis]) * geometry["scale"])
            for index, axis in enumerate(("x", "y"))
        ]
        return framebuffer, logical

    def screenshot(self):
        width, height = self.desktop()[2:]
        start, _ = self.framebuffer_point(0, 0)
        end, _ = self.framebuffer_point(width, height)
        return self.parent.screenshot().crop((*start, *end)).resize((width, height))

    def pointer(self, x, y):
        framebuffer, logical = self.framebuffer_point(x, y)
        self.parent.pointer(*framebuffer)
        deadline = time.monotonic() + 5
        while True:
            observed = self.driver.state()["cursor"]
            passed = all(
                abs(observed[axis] - logical[index]) <= 1
                for index, axis in enumerate(("x", "y"))
            )
            if passed or time.monotonic() >= deadline:
                self.events.append(
                    {
                        "client_framebuffer": [x, y],
                        "parent_framebuffer": framebuffer,
                        "parent_expected": logical,
                        "parent_observed": observed,
                        "passed": passed,
                    }
                )
                (self.output / "parent-pointer.json").write_text(
                    json.dumps(self.events, indent=2) + "\n"
                )
                if not passed:
                    raise RuntimeError(
                        "Parent RFB input used incorrect scaled coordinates"
                    )
                return
            time.sleep(0.1)

    def click(self, x, y):
        # Enter directly at the target; no interior motion may repair a stale click.
        self.parent.pointer(0, 0)
        time.sleep(0.1)
        self.pointer(x, y)
        framebuffer, _ = self.framebuffer_point(x, y)
        self.parent.pointer(*framebuffer, 1)
        time.sleep(0.08)
        self.parent.pointer(*framebuffer)

    def key(self, keysym, down):
        self.parent.key(keysym, down)

    def chord(self, modifier, key):
        self.parent.chord(modifier, key)

    def text(self, value):
        self.parent.text(value)


def capture_parent(runtime, parent, session, destination, cursor):
    path = "/tmp/mt5-cursor-" + uuid.uuid4().hex + ".png"
    try:
        result = execute(
            runtime,
            "exec",
            "--user=plasma",
            parent,
            "env",
            *[f"{key}={value}" for key, value in session.items()],
            "/usr/local/bin/plasma-ui-fixture",
            "--capture-output-with-cursor" if cursor else "--capture-output",
            path,
        )
        metadata = capture_metadata(result.stdout)
        if (
            metadata["method"] != "kwin-screenshot2"
            or bool(metadata.get("include_cursor")) != cursor
        ):
            raise RuntimeError(
                "Parent screenshot did not use the requested cursor mode"
            )
        destination.write_bytes(execute(runtime, "exec", parent, "cat", path).stdout)
        return metadata
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stdout or b"") + (error.stderr or b"")
        destination.with_suffix(".log").write_bytes(diagnostic)
        raise RuntimeError(
            "Parent capture failed: " + diagnostic.decode(errors="replace").strip()
        ) from error
    finally:
        execute(runtime, "exec", parent, "rm", "-f", path, check=False)


def cursor_hotspot(before, after, expected):
    if before.size != after.size:
        raise RuntimeError("Cursor screenshots changed output dimensions")
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    mask = difference.convert("L").point(lambda value: 255 if value > 16 else 0)
    bounds = mask.getbbox()
    if bounds is None:
        raise RuntimeError(
            "Compositor screenshot did not contain the cursor at its input position"
        )
    if bounds[2] - bounds[0] > 128 or bounds[3] - bounds[1] > 128:
        raise RuntimeError("Screenshot difference did not isolate a cursor sprite")
    tip = list(bounds[:2])
    if any(abs(tip[index] - expected[index]) > 8 for index in range(2)):
        raise RuntimeError(f"Cursor sprite offset: expected {expected}, observed {tip}")
    return {
        "expected": expected,
        "observed_bounds_origin": tip,
        "full_difference_bounds": list(bounds),
        "tolerance": 8,
        "passed": True,
    }


def capture_cursor(runtime, parent, session, output, logical, geometry):
    for attempt in range(1, 4):
        directory = output / f"cursor-attempt-{attempt}"
        directory.mkdir()
        before, after, background = [
            directory / name
            for name in (
                "cursor-hidden.png",
                "cursor-visible.png",
                "cursor-background.png",
            )
        ]
        capture_parent(runtime, parent, session, before, False)
        metadata = capture_parent(runtime, parent, session, after, True)
        capture_parent(runtime, parent, session, background, False)
        with (
            Image.open(before) as hidden,
            Image.open(after) as visible,
            Image.open(background) as check,
        ):
            bounds = (
                ImageChops.difference(
                    hidden.convert("RGB"), check.convert("RGB")
                ).getbbox()
                if hidden.size == check.size
                else (0, 0, *hidden.size)
            )
            (directory / "scene.json").write_text(
                json.dumps(
                    {
                        "background_stable": bounds is None,
                        "background_difference_bounds": bounds,
                    },
                    indent=2,
                )
                + "\n"
            )
            if bounds is not None:
                continue
            expected = [
                (logical[index] - geometry["origin"][axis])
                * visible.size[index]
                / geometry["logical"][dimension]
                for index, (axis, dimension) in enumerate(
                    (("x", "width"), ("y", "height"))
                )
            ]
            cursor = cursor_hotspot(hidden, visible, expected)
        for path in (before, after, background):
            (output / path.name).write_bytes(path.read_bytes())
        cursor.update(attempt=attempt, scene=f"{directory.name}/scene.json")
        return cursor, metadata
    raise RuntimeError("Cursor background did not stabilize in three capture attempts")


def resized_input(client, probe, runtime, parent, session, output):
    output.mkdir()
    size = (
        (client.window["frame"]["width"] + 80, client.window["frame"]["height"] + 40)
        if client.viewport_anchor
        else (1100, 820)
    )
    client.window = client.driver.place_window(280, 80, *size)
    (output / "window.json").write_text(json.dumps(client.window, indent=2) + "\n")
    pointer = pointer_grid(client, probe, output)
    state = probe.state()
    point = control_point(client, probe, state["file_rect"])
    client.click(*point)
    wait_state(probe, lambda value: bool(value["menus"]))
    client.screenshot().save(output / "mouse-menu.png")
    close_menu(client, probe)
    client.pointer(*point)
    _, logical = client.framebuffer_point(*point)
    cursor, metadata = capture_cursor(
        runtime, parent, session, output, logical, client.driver.output
    )
    return {"passed": True, "pointer": pointer, "cursor": cursor, "capture": metadata}


def wait_state(probe, predicate, expected="", timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        state = probe.state(expected)
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError("MT5 GUI state did not respond to display input")
        time.sleep(0.2)


def close_menu(client, probe):
    # Escape closes the popup first, then MT5's remaining menu-bar capture.
    for _ in range(2):
        client.key(0xFF1B, True)
        client.key(0xFF1B, False)
    wait_state(probe, lambda value: not value["menus"] and value["menu_mode"] is False)


def center(rectangle):
    return (rectangle[0] + rectangle[2]) // 2, (rectangle[1] + rectangle[3]) // 2


def control_point(client, probe, rectangle):
    x, y = center(rectangle)
    left, top, width, height = probe.state()["desktop"]
    image = client.screenshot()
    return (x - left) * image.width / width, (y - top) * image.height / height


def pointer_grid(client, probe, output):
    image = client.screenshot()
    state = probe.state()
    left, top, width, height = state["desktop"]
    if width <= 0 or height <= 0:
        raise RuntimeError("MT5 reported an invalid physical desktop")
    evidence = []
    destination = output / "pointer.json"
    # Include pure horizontal/vertical moves: stale axis metadata must fail too.
    for x, y in ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9), (0.5, 0.5)):
        framebuffer = [round(x * (image.width - 1)), round(y * (image.height - 1))]
        expected = [
            left + framebuffer[0] * width / image.width,
            top + framebuffer[1] * height / image.height,
        ]
        client.pointer(*framebuffer)
        deadline = time.monotonic() + 5
        while True:
            observed = probe.state()["pointer"]
            delta = [observed[index] - expected[index] for index in range(2)]
            matched = all(abs(value) <= 2 for value in delta)
            if matched or time.monotonic() >= deadline:
                evidence.append(
                    {
                        "framebuffer": framebuffer,
                        "expected": expected,
                        "observed": observed,
                        "delta": delta,
                        "passed": matched,
                    }
                )
                destination.write_text(json.dumps(evidence, indent=2) + "\n")
                if not matched:
                    raise RuntimeError(
                        f"MT5 pointer offset: expected {expected}, observed {observed}"
                    )
                break
            time.sleep(0.1)
    return {"passed": True, "points": len(evidence), "evidence": "pointer.json"}


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
    phase("checking physical pointer coordinates")
    pointer = pointer_grid(client, probe, output)
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
    client.click(*control_point(client, probe, edit["rect"]))
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
    client.click(*control_point(client, probe, button["rect"]))
    wait_state(
        probe,
        lambda value: (
            not any(item["hwnd"] == edit["hwnd"] for item in value["controls"])
        ),
    )
    state = wait_state(probe, lambda value: value.get("main_enabled"))
    phase("clicking File menu")
    client.click(*control_point(client, probe, state["file_rect"]))
    wait_state(probe, lambda value: bool(value["menus"]))
    client.screenshot().save(output / "mouse-menu.png")
    phase("closing File menu with Escape")
    close_menu(client, probe)
    client.screenshot().save(output / "after-input.png")
    phase("passed")
    return {
        "passed": True,
        "pointer": pointer,
        "mouse": "Company-search edit focused, Cancel closed, File menu opened",
        "keyboard": "Mixed-case text, Ctrl+A replacement and Escape reached MT5",
        "expected": "expected.txt",
        "readback": "typed.txt",
        "oracle": "Win32 WM_GETTEXT; no Win32 input injection",
    }


def main(args):
    scales = getattr(args, "pointer_scales", None)
    autologin = getattr(args, "startup", "dialog") == "autologin"
    if scales and (set(args.modes) != {"nested"} or not args.parent_image):
        raise ValueError(
            "--pointer-scales requires --modes nested and a decorated --parent-image"
        )
    root = Path(args.output).resolve() / args.case
    root.mkdir(parents=True, exist_ok=False)
    expert = (
        compile_chart(args.runtime, args.image, root / "compile") if autologin else None
    )
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
            dimensions=(3840, 2160) if scales else (1280, 960),
        )
        parent_client = connect(args.runtime, parent, "vnc", parent_output)
        host_display, auth = parent_x11(args.runtime, parent, parent_output)
        driver = KWinDriver(args.runtime, parent, parent_output) if scales else None
        if driver:
            driver.clear_rules()
        for scale, backend, mode, host in (
            (scale, *profile)
            for scale in scales or [None]
            for profile in profiles(args.backends)
        ):
            if mode not in args.modes:
                continue
            output = (
                root
                / (f"scale-{scale * 100:g}" if scale else "")
                / case_path(backend, mode, host)
            )
            output.mkdir(parents=True)
            name = f"mt5-input-{os.getpid()}-{backend}-{mode}-{host or 'none'}"
            if scale:
                name += f"-scale-{scale:g}"
            report = {
                "backend": backend,
                "mode": mode,
                "host_backend": host,
                "passed": False,
            }
            client = probe = None
            try:
                if driver:
                    report["parent_output"] = driver.output_scale(scale)
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
                if expert:
                    binary, config = expert
                    extra = [
                        f"--network={network}",
                        "-e",
                        "MT5_ACCOUNT=1",
                        "-e",
                        "MT5_LOGIN=1",
                        "-e",
                        "MT5_PASSWORD=isolated-test-only",
                        "-e",
                        "MT5_SERVER=ICMarketsEU-Demo",
                        "-e",
                        "MT5_CONFIG_FILE=/etc/mt5/autologin.ini",
                        "--mount",
                        f"type=bind,source={config},target=/etc/mt5/autologin.ini,readonly",
                        "--mount",
                        f"type=bind,source={binary},target=/run/mt5-test/ChartInput.ex5,readonly",
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
                if driver and not autologin:
                    driver.require_decoration = (
                        backend == "wayland" and host == "wayland"
                    )
                    viewport_anchor = (
                        "center"
                        if session.get("PLASMA_SOFTWARE_NESTED") == "1"
                        else "top-left"
                        if backend == "x11" and host == "x11"
                        else None
                    )
                    frame = (
                        driver.state()["windows"][0]["frame"]
                        if viewport_anchor
                        else None
                    )
                    size = (frame["width"], frame["height"]) if frame else (1280, 996)
                    window = driver.place_window(140, 70, *size)
                    report["window"] = window
                    client = NestedClient(
                        parent_client,
                        driver,
                        probe,
                        window,
                        output,
                        viewport_anchor=viewport_anchor,
                    )
                    report["viewport"] = client.viewport()
                report["input"] = (
                    autologin_input(
                        args.runtime,
                        name,
                        client,
                        probe,
                        output,
                        target_scale=scale or 1,
                    )
                    if autologin
                    else exercise(client, probe, output)
                )
                if driver and not autologin:
                    report["resized"] = resized_input(
                        client,
                        probe,
                        args.runtime,
                        parent,
                        configuration(args.runtime, parent),
                        output / "resized",
                    )
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
                    client if mode != "nested" else None,
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
    expected_cases = len(scales or [None]) * sum(
        mode in args.modes for _, mode, _ in profiles(args.backends)
    )
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
    parser.add_argument("--startup", choices=("dialog", "autologin"), default="dialog")
    parser.add_argument("--pointer-scales", nargs="+", type=float, choices=(1, 1.25, 2))
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("nested", "vnc", "web"),
        default=["nested", "vnc", "web"],
    )
    raise SystemExit(main(parser.parse_args()))
