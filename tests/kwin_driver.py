"""Control disposable private KWin parents and retain independent compositor state."""

import json
import math
import re
import time
import uuid
from pathlib import Path

from runtime import execute

RULES = "/home/plasma/.config/plasma-kiosk/kwinrulesrc"
WRITE_RULES = """import sys
from pathlib import Path
path=Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
path.write_text('[General]\\ncount=0\\nrules=\\n')
"""
STATE = """function rect(r){{return{{x:r.x,y:r.y,width:r.width,height:r.height}};}}
{body}
console.warn({tag}+' '+JSON.stringify({{
cursor:{{x:workspace.cursorPos.x,y:workspace.cursorPos.y}},
windows:workspace.stackingOrder.filter(w=>w.normalWindow).map(w=>({{
id:w.internalId.toString(),caption:w.caption,frame:rect(w.frameGeometry),
client:rect(w.clientGeometry),fullScreen:w.fullScreen,noBorder:w.noBorder
}}))}}));
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def rectangle(value):
    return (
        isinstance(value, dict)
        and all(number(value.get(key)) for key in ("x", "y", "width", "height"))
        and value["width"] > 0
        and value["height"] > 0
    )


def output_layout(data):
    outputs = [
        item
        for item in data["outputs"]
        if item.get("enabled", item.get("priority", 0) > 0) is True
    ]
    require(len(outputs) == 1, "Private parent must have exactly one enabled output")
    output = outputs[0]
    require(
        output.get("rotation", 1) == 1 and output.get("transform", 0) == 0,
        "Transformed private outputs are unsupported",
    )
    name, scale = output.get("name"), output.get("scale")
    require(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name),
        "Invalid private output name",
    )
    require(number(scale) and scale > 0, "Invalid private output scale")
    modes = [
        mode
        for mode in output["modes"]
        if str(mode["id"]) == str(output["currentModeId"])
    ]
    require(len(modes) == 1, "Private output has no unique current mode")
    size, logical, origin = (
        modes[0]["size"],
        data["screen"]["currentSize"],
        output["pos"],
    )
    require(
        all(
            number(value.get(key)) and value[key] > 0
            for value in (size, logical)
            for key in ("width", "height")
        ),
        "Invalid private output dimensions",
    )
    require(
        all(number(origin.get(key)) for key in ("x", "y")),
        "Invalid private output origin",
    )
    return {
        "name": name,
        "scale": scale,
        "mode": {
            "id": modes[0]["id"],
            "width": size["width"],
            "height": size["height"],
        },
        "origin": {key: origin[key] for key in ("x", "y")},
        "logical": {key: logical[key] for key in ("width", "height")},
    }


class KWinDriver:
    """Use only with test-owned parent containers and their shared runtime directory."""

    def __init__(self, runtime, container, output):
        self.runtime, self.container = runtime, container
        self.directory = Path(output)
        self.shared = self.directory / "runtime"
        self.shared.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output = None
        self.require_decoration = True

    def command(self, *arguments, wayland=False):
        return execute(
            self.runtime,
            "exec",
            "--user=plasma",
            self.container,
            "env",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/plasma/bus",
            "XDG_RUNTIME_DIR=/run/plasma",
            "QT_QPA_PLATFORM=" + ("wayland" if wayland else "offscreen"),
            "QT_QPA_PLATFORMTHEME=generic",
            *(["WAYLAND_DISPLAY=wayland-0"] if wayland else []),
            *arguments,
        )

    def dbus(self, *arguments):
        return self.command("qdbus-qt6", "org.kde.KWin", *arguments)

    def clear_rules(self):
        execute(
            self.runtime,
            "exec",
            "--user=plasma",
            self.container,
            "python3",
            "-c",
            WRITE_RULES,
            RULES,
        )
        self.dbus("/KWin", "reconfigure")

    def output_scale(self, scale):
        require(
            number(scale) and scale > 0,
            "Requested private scale must be positive and finite",
        )
        initial = json.loads(
            self.command("kscreen-doctor", "--json", wayland=True).stdout
        )
        (self.directory / f"kscreen-initial-{uuid.uuid4().hex}.json").write_text(
            json.dumps(initial, indent=2) + "\n"
        )
        name = output_layout(initial)["name"]
        self.command("kscreen-doctor", f"output.{name}.scale.{scale:g}", wayland=True)
        deadline = time.monotonic() + 15
        while True:
            data = json.loads(
                self.command("kscreen-doctor", "--json", wayland=True).stdout
            )
            layout = output_layout(data)
            require(
                layout["name"] == name,
                "Private output changed during scale configuration",
            )
            if math.isclose(layout["scale"], scale, abs_tol=1e-6):
                self.output = layout
                (
                    self.directory / f"kscreen-confirmed-{uuid.uuid4().hex}.json"
                ).write_text(json.dumps(data, indent=2) + "\n")
                return layout
            require(
                time.monotonic() < deadline, "Private output scale was not acknowledged"
            )
            time.sleep(0.1)

    def state(self, body=""):
        tag = "MT5_PRIVATE_STATE_" + uuid.uuid4().hex
        path = self.shared / (tag + ".js")
        path.write_text(STATE.format(body=body, tag=json.dumps(tag)))
        try:
            identifier = int(
                self.dbus(
                    "/Scripting", "loadScript", "/run/plasma/" + path.name, tag
                ).stdout.strip()
            )
            require(identifier >= 0, "Private KWin script failed to load")
            self.dbus(f"/Scripting/Script{identifier}", "run")
            deadline = time.monotonic() + 10
            while True:
                log = execute(
                    self.runtime,
                    "exec",
                    self.container,
                    "journalctl",
                    "-u",
                    "plasma-session.service",
                    "--no-pager",
                    "-o",
                    "cat",
                    "-g",
                    tag,
                ).stdout.decode()
                line = next(
                    (
                        line.split(tag + " ", 1)[1]
                        for line in log.splitlines()
                        if tag + " " in line
                    ),
                    None,
                )
                if line is not None:
                    data = json.loads(line)
                    require(
                        isinstance(data, dict)
                        and isinstance(data.get("cursor"), dict)
                        and all(number(data["cursor"].get(key)) for key in ("x", "y"))
                        and isinstance(data.get("windows"), list),
                        "Invalid private KWin state",
                    )
                    for window in data["windows"]:
                        require(
                            isinstance(window.get("id"), str)
                            and window["id"]
                            and isinstance(window.get("caption"), str)
                            and rectangle(window.get("frame"))
                            and rectangle(window.get("client"))
                            and type(window.get("fullScreen")) is bool
                            and type(window.get("noBorder")) is bool,
                            "Invalid private child window state",
                        )
                    (self.directory / (tag + ".json")).write_text(
                        json.dumps(data, indent=2) + "\n"
                    )
                    return data
                require(
                    time.monotonic() < deadline,
                    "Private KWin script produced no tagged state",
                )
                time.sleep(0.1)
        finally:
            try:
                self.dbus("/Scripting", "unloadScript", tag)
            finally:
                path.unlink(missing_ok=True)

    def place_window(self, x, y, width, height):
        target = {"x": x, "y": y, "width": width, "height": height}
        require(rectangle(target), "Invalid requested private window rectangle")
        initial = self.state()
        require(
            len(initial["windows"]) == 1,
            "Private parent must contain exactly one normal child window",
        )
        identifier = initial["windows"][0]["id"]
        select = f"const windows=workspace.stackingOrder.filter(w=>w.normalWindow);if(windows.length!==1||windows[0].internalId.toString()!=={json.dumps(identifier)})throw Error('Private child changed');const w=windows[0];"
        self.state(
            select + "w.fullScreen=false;w.setMaximize(false,false);w.noBorder=false;"
        )
        self.state(
            select
            + "w.frameGeometry="
            + json.dumps(target)
            + ";workspace.activeWindow=w;"
        )
        deadline = time.monotonic() + 15
        tolerance = 1 / self.output["scale"] + 1e-6 if self.output else 1e-4
        while True:
            data = self.state()
            require(
                len(data["windows"]) == 1 and data["windows"][0]["id"] == identifier,
                "Private child changed during placement",
            )
            window = data["windows"][0]
            if (
                not window["fullScreen"]
                and not window["noBorder"]
                and all(
                    math.isclose(window["frame"][key], value, abs_tol=tolerance)
                    for key, value in target.items()
                )
            ):
                if self.require_decoration:
                    require(
                        window["client"]["y"] > window["frame"]["y"]
                        and window["frame"]["height"] > window["client"]["height"],
                        "Private child has no visible server decoration",
                    )
                (self.directory / f"placement-{uuid.uuid4().hex}.json").write_text(
                    json.dumps({"requested": target, "window": window}, indent=2) + "\n"
                )
                return window
            require(
                time.monotonic() < deadline,
                "Private child did not acknowledge requested geometry",
            )
            time.sleep(0.1)
