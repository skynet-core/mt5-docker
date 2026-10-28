"""Check nested KWin clipboard exchange with a disposable headless Weston host.

Build the host first: docker build -f tests/Dockerfile.weston -t mt5:weston-test .
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from clipboard import validate as validate_clipboard
from runtime import collect, start


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    ).stdout


def main(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    parent = f"mt5-weston-parent-{os.getpid()}"
    child = None
    parent_output = output / "parent"
    parent_output.mkdir()
    runtime = parent_output / "runtime"
    runtime.mkdir(mode=0o700)
    x11 = parent_output / "x11"
    x11.mkdir(mode=0o1777)
    try:
        docker(
            "run",
            "-d",
            "--name",
            parent,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "plasma",
            "--mount",
            f"type=bind,source={runtime},target=/run/plasma",
            "--mount",
            f"type=bind,source={x11},target=/tmp/.X11-unix",
            "-e",
            "XDG_RUNTIME_DIR=/run/plasma",
            "-e",
            "LIBGL_ALWAYS_SOFTWARE=1",
            "--entrypoint",
            "/usr/bin/weston",
            args.parent_image,
            "--backend=headless",
            "--renderer=pixman",
            "--socket=wayland-0",
            "--xwayland",
            "--fake-seat",
            "--width=800",
            "--height=600",
            "--idle-time=0",
            "--no-config",
        )
        deadline = time.monotonic() + 30
        while not (runtime / "wayland-0").exists() or not (x11 / "X0").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Weston's private display sockets did not appear")
            time.sleep(0.1)
        (parent_output / "version.log").write_bytes(
            docker("exec", parent, "weston", "--version")
        )
        (parent_output / "wayland-info.log").write_bytes(
            docker("exec", parent, "wayland-info")
        )
        (parent_output / "xdpyinfo.log").write_bytes(
            docker("exec", parent, "env", "DISPLAY=:0", "xdpyinfo")
        )
        session = "DISPLAY_BACKEND=wayland\nDISPLAY_MODE=headless\nXDG_RUNTIME_DIR=/run/plasma\nWAYLAND_DISPLAY=wayland-0\nDISPLAY=:0\nQT_QPA_PLATFORM=offscreen\nLIBGL_ALWAYS_SOFTWARE=1\n"
        docker(
            "exec",
            "-u",
            "0",
            parent,
            "python3",
            "-c",
            "from pathlib import Path; import sys; Path('/etc/plasma/session.env').write_text(sys.argv[1])",
            session,
        )
        for backend in ("wayland", "x11"):
            case = output / f"{backend}-nested"
            case.mkdir()
            child = f"mt5-weston-{backend}-{os.getpid()}"
            try:
                start(
                    "docker",
                    args.image,
                    child,
                    backend,
                    "nested",
                    case,
                    [
                        "--mount",
                        f"type=bind,source={runtime / 'wayland-0'},target=/run/host/wayland,readonly",
                        "--mount",
                        f"type=bind,source={x11},target=/run/host/X11,readonly",
                        "-e",
                        "HOST_WAYLAND_DISPLAY=/run/host/wayland",
                        "-e",
                        "HOST_DISPLAY=:0",
                    ],
                )
                validate_clipboard(
                    SimpleNamespace(
                        runtime="docker",
                        parent=parent,
                        container=child,
                        case=f"weston-{backend}-nested",
                        output=str(output / "clipboard"),
                        parent_x11_clipboard=True,
                    )
                )
            finally:
                collect("docker", child, case)
                docker("rm", "-f", child, check=False)
                child = None
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "parent_image": args.parent_image,
                    "child_image": args.image,
                    "weston_version": (parent_output / "version.log")
                    .read_text()
                    .strip(),
                    "passed": True,
                    "checks": ["wayland-nested", "x11-nested"],
                    "clipboard": ["unicode-text", "png-with-alpha", "both-directions"],
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if child:
            docker("rm", "-f", child, check=False)
        (parent_output / "weston.log").write_bytes(docker("logs", parent, check=False))
        docker("rm", "-f", parent, check=False)
        for path in (runtime, x11):
            for socket in path.iterdir():
                socket.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-image", default="mt5:weston-test")
    parser.add_argument("--image", default="mt5:runtime-test-fedora-44")
    parser.add_argument("--output", default="artifacts/weston")
    main(parser.parse_args())
