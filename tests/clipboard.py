"""Check nested clipboard sharing against a disposable parent container."""

import argparse
import base64
import contextlib
import io
import json
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

from PIL import Image


def run(runtime, *arguments):
    return subprocess.run([runtime, *arguments], capture_output=True, check=True).stdout


def configuration(runtime, container):
    program = "import json,shlex; print(json.dumps(dict(shlex.split(line)[0].split('=',1) for line in open('/etc/plasma/session.env'))))"
    return json.loads(run(runtime, "exec", container, "python3", "-c", program))


def require_isolated_parent(parent_config, inner_config, parent_socket, inner_socket):
    if parent_config["DISPLAY_MODE"] not in ("headless", "vnc", "web"):
        raise ValueError(
            "Clipboard parent must use a private headless, VNC or web display"
        )
    if inner_config["DISPLAY_MODE"] != "nested":
        raise ValueError("Clipboard child must use nested mode")
    if not parent_socket or parent_socket != inner_socket:
        raise ValueError(
            "Child's host socket must be the isolated parent's actual socket"
        )


def socket_identity(runtime, container, backend, config, host=False):
    if backend == "wayland":
        path = (
            config["HOST_WAYLAND_DISPLAY"]
            if host
            else str(Path(config["XDG_RUNTIME_DIR"]) / config["WAYLAND_DISPLAY"])
        )
    else:
        display = config["HOST_DISPLAY"] if host else config["DISPLAY"]
        if not display.startswith(":") or not display[1:].split(".")[0].isdecimal():
            raise ValueError("Clipboard check requires a local X11 socket")
        path = "/tmp/.X11-unix/X" + display[1:].split(".")[0]
    return run(runtime, "exec", container, "stat", "-Lc", "%d:%i", "--", path).strip()


class Probe:
    def __init__(self, runtime, container, config, log):
        self.messages = queue.Queue()
        self.log = log.open("w")
        self.process = subprocess.Popen(
            [
                runtime,
                "exec",
                "-i",
                "--user=plasma",
                container,
                "env",
                *[f"{key}={value}" for key, value in config.items()],
                "/usr/local/libexec/plasma-clipboard",
                "--endpoint",
                config["DISPLAY_BACKEND"],
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError as error:
                self.messages.put(error)

    def set(self, payload):
        self.process.stdin.write(
            json.dumps(
                {
                    "mode": 0,
                    "data": {
                        key: base64.b64encode(value).decode()
                        for key, value in payload.items()
                    },
                }
            )
            + "\n"
        )
        self.process.stdin.flush()

    def wait(self, expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.25)
            except queue.Empty as error:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        "Clipboard probe exited; inspect its log"
                    ) from error
                continue
            if isinstance(message, Exception):
                raise message
            actual = {
                key: base64.b64decode(value) for key, value in message["data"].items()
            }
            if message["mode"] == 0 and all(
                actual.get(key) == value for key, value in expected.items()
            ):
                return actual
        raise RuntimeError(
            f"Clipboard did not receive {list(expected)} within 20 seconds"
        )

    def close(self):
        with contextlib.suppress(BrokenPipeError):
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.log.close()


def validate(args):
    output = Path(args.output).resolve() / args.case
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "parent": args.parent,
        "container": args.container,
        "passed": False,
        "checks": {},
    }
    probes = []
    try:
        parent_config = configuration(args.runtime, args.parent)
        inner_config = configuration(args.runtime, args.container)
        result.update(
            backend=inner_config["DISPLAY_BACKEND"],
            host=inner_config["HOST_DISPLAY_BACKEND"],
        )
        if (
            inner_config["HOST_DISPLAY_BACKEND"] == "x11"
            and parent_config["DISPLAY_BACKEND"] == "wayland"
        ):
            # The Wayland parent's private Xwayland is the child's X11 host.
            program = """
import json
from pathlib import Path
for process in Path('/proc').glob('[0-9]*'):
    try:
        if (process / 'comm').read_text().strip() == 'Xwayland':
            arguments = (process / 'cmdline').read_bytes().decode().split('\\0')
            print(json.dumps({'DISPLAY': next(a for a in arguments if a.startswith(':')),
                              'XAUTHORITY': arguments[arguments.index('-auth') + 1]}))
            break
    except FileNotFoundError:
        continue
else:
    raise SystemExit('Parent Xwayland is unavailable')
"""
            parent_config.update(
                json.loads(
                    run(args.runtime, "exec", args.parent, "python3", "-c", program)
                )
            )
            parent_config.update(DISPLAY_BACKEND="x11", QT_QPA_PLATFORM="xcb")
        host = inner_config["HOST_DISPLAY_BACKEND"]
        require_isolated_parent(
            parent_config,
            inner_config,
            socket_identity(args.runtime, args.parent, host, parent_config),
            socket_identity(
                args.runtime, args.container, host, inner_config, host=True
            ),
        )
        if getattr(args, "parent_x11_clipboard", False):
            # Weston has no data-control protocol; exercise its private Xwayland.
            require_isolated_parent(
                parent_config,
                inner_config,
                socket_identity(args.runtime, args.parent, "x11", parent_config),
                socket_identity(
                    args.runtime, args.container, "x11", inner_config, host=True
                ),
            )
            parent_config.update(DISPLAY_BACKEND="x11", QT_QPA_PLATFORM="xcb")
        result["parent_endpoint"] = parent_config["DISPLAY_BACKEND"]
        probes.append(
            Probe(args.runtime, args.parent, parent_config, output / "parent-probe.log")
        )
        probes.append(
            Probe(
                args.runtime, args.container, inner_config, output / "inner-probe.log"
            )
        )
        for sender, receiver_container, receiver_config, direction, color in (
            (
                probes[0],
                args.container,
                inner_config,
                "host-to-container",
                (18, 210, 178, 255),
            ),
            (
                probes[1],
                args.parent,
                parent_config,
                "container-to-host",
                (210, 40, 90, 128),
            ),
        ):
            # Qt XCB's former owner can miss equal-timestamp ownership events.
            # Observe each paste with a reader that has never owned the selection.
            receiver = Probe(
                args.runtime,
                receiver_container,
                receiver_config,
                output / f"{direction}-receiver.log",
            )
            probes.append(receiver)
            image = io.BytesIO()
            Image.new("RGBA", (37, 19), color).save(image, format="PNG")
            payload = {
                "text/plain": f"{direction}: Καλημέρα Привіт 漢字 🌍 {uuid.uuid4().hex}\n".encode(),
                "image/png": image.getvalue(),
            }
            sender.set(payload)
            received = receiver.wait(payload)
            (output / f"{direction}.txt").write_bytes(received["text/plain"])
            (output / f"{direction}.png").write_bytes(received["image/png"])
            result["checks"][direction] = ["unicode-text", "png-with-alpha"]
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        for probe in reversed(probes):
            probe.close()
        for label, container in (("parent", args.parent), ("inner", args.container)):
            journal = subprocess.run(
                [
                    args.runtime,
                    "exec",
                    container,
                    "journalctl",
                    "-b",
                    "--no-pager",
                    "-u",
                    "plasma-clipboard",
                    "-u",
                    "plasma-session",
                ],
                capture_output=True,
                check=False,
            )
            (output / f"{label}-services.log").write_bytes(
                journal.stdout + journal.stderr
            )
        (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"Clipboard evidence: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", default="artifacts/clipboard")
    parser.add_argument(
        "--parent-x11-clipboard",
        action="store_true",
        help="Use the isolated parent's private Xwayland clipboard (e.g. Weston)",
    )
    validate(parser.parse_args())
