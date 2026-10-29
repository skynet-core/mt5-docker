"""Check installed Windows components and running KWin without account credentials."""

import argparse
import contextlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

PAYLOAD_CHECK = r"""
import json
from pathlib import Path
root = Path('/home/plasma/.wine/drive_c')
files = {path.name.lower(): path for path in (root / 'MT5').iterdir()}
payloads = {}
for name in ('terminal64.exe', 'metaeditor64.exe', 'metatester64.exe'):
    path = files[name]
    assert path.is_file() and path.stat().st_size > 0, name
    payloads[name] = path.stat().st_size
webview = next(path for path in root.rglob('*') if path.name.lower() == 'msedgewebview2.exe')
assert webview.is_file() and webview.stat().st_size > 0
payloads['msedgewebview2.exe'] = webview.stat().st_size
print(json.dumps(payloads))
"""

WINDOWS_CHECK = r"""
import json
from importlib import import_module
from importlib.metadata import version
print('Windows imports: starting', flush=True)
# The build gate checks COM before startup; MT5/MCP uses the SDK and Win32 APIs.
for name in ('MetaTrader5', 'mt5_mcp', 'matplotlib', 'fastmcp', 'win32api',
             'pywintypes', 'win32gui'):
    print('Import: ' + name, flush=True)
    import_module(name)
    print('Imported: ' + name, flush=True)
from scipy.special import ndtr
from mt5_mcp.mcp_server import create_mcp_server
assert version('mt5-mcp') == '0.6.3'
assert ndtr(0) == 0.5
print('Windows imports: complete', flush=True)
print('MCP factory: starting', flush=True)
create_mcp_server(rate_limit_per_minute=0)
print('MCP factory: complete', flush=True)
print(json.dumps({'mt5_mcp_version': version('mt5-mcp'),
    'metatrader5_version': version('MetaTrader5'), 'mcp_server_created': True}))
"""

WINE_ENVIRONMENT = r"""
import json, os, sys
with open('/run/plasma/mt5-environment.json') as stream:
    environment = {**os.environ, **json.load(stream)}
print('Wine Windows Python: starting', flush=True)
os.execvpe('wine', ['wine', r'C:\Python\python.exe', '-c', sys.argv[1]], environment)
"""


def main(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    container = "mt5-ci-smoke-" + uuid.uuid4().hex[:12]
    volume = container + "-wine"
    evidence = {
        "image": args.image,
        "passed": False,
        "network": "none",
        "backend": "x11",
        "scope": "installation-dependencies-and-compositor",
    }

    def docker(*arguments, check=True, timeout=30):
        with (output / "stdout.log").open("ab") as stream:
            stream.write(("$ docker " + json.dumps(arguments) + "\n").encode())
        try:
            result = subprocess.run(
                ["docker", *arguments],
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            with (output / "stdout.log").open("ab") as stream:
                stream.write((error.stdout or b"") + (error.stderr or b""))
            raise
        with (output / "stdout.log").open("ab") as stream:
            stream.write(result.stdout + result.stderr)
        if check and result.returncode:
            raise RuntimeError(f"Docker {arguments[0]} failed; see stdout.log")
        return result

    try:
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--no-healthcheck",
            # An offline import/window smoke must not initialize account IPC.
            "--mount",
            "type=bind,source=/dev/null,target=/etc/systemd/system/mt5-mcp.service,readonly",
            "--privileged",
            "--cgroupns=private",
            "--network",
            "none",
            "--pids-limit=8192",
            "--shm-size=256m",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/tmp:mode=1777",
            "--mount",
            f"type=volume,source={volume},target=/home/plasma/.wine",
            "-e",
            "DISPLAY_BACKEND=x11",
            "-e",
            "DISPLAY_MODE=headless",
            "-e",
            "LIBGL_ALWAYS_SOFTWARE=1",
            "-e",
            "SYSTEMD_MASKED=NetworkManager.service,systemd-udev-trigger.service",
            args.image,
            timeout=120,
        )
        evidence["image_id"] = (
            docker("inspect", "--format", "{{.Image}}", container)
            .stdout.decode()
            .strip()
        )
        deadline = time.monotonic() + 120
        while True:
            ready = (
                docker(
                    "exec", container, "/usr/local/bin/plasma-healthcheck", check=False
                ).returncode
                == 0
            )
            environment = (
                docker(
                    "exec",
                    container,
                    "test",
                    "-s",
                    "/run/plasma/mt5-environment.json",
                    check=False,
                ).returncode
                == 0
            )
            if ready and environment:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Compositor/display environment did not become ready within 120 seconds"
                )
            time.sleep(1)
        evidence["payloads"] = json.loads(
            docker("exec", container, "python3", "-c", PAYLOAD_CHECK).stdout
        )
        evidence["wine_version"] = (
            docker("exec", "-u", "plasma", container, "wine", "--version")
            .stdout.decode()
            .strip()
        )
        support = docker(
            "exec",
            "-u",
            "plasma",
            "-e",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/plasma/bus",
            "-e",
            "QT_QPA_PLATFORM=offscreen",
            "-e",
            "QT_QPA_PLATFORMTHEME=generic",
            container,
            "qdbus-qt6",
            "org.kde.KWin",
            "/KWin",
            "supportInformation",
        ).stdout.decode(errors="replace")
        (output / "compositor-support.log").write_text(support)
        match = re.search(r"^KWin version:\s*(\S+)", support, re.MULTILINE)
        if not match:
            raise RuntimeError("Running KWin did not report its version")
        evidence["kwin_version"] = match.group(1)
        windows = docker(
            "exec",
            "-u",
            "plasma",
            container,
            "python3",
            "-c",
            WINE_ENVIRONMENT,
            WINDOWS_CHECK,
            timeout=120,
        )
        (output / "windows.log").write_bytes(windows.stdout + windows.stderr)
        evidence["windows"] = json.loads(windows.stdout.decode().splitlines()[-1])
        evidence["passed"] = True
    except Exception as error:
        evidence["error"] = str(error)
        raise
    finally:
        for name, arguments in (
            ("container.log", ("logs", container)),
            ("services.log", ("exec", container, "journalctl", "-b", "--no-pager")),
            (
                "failed-units.log",
                ("exec", container, "systemctl", "--failed", "--no-pager"),
            ),
        ):
            with contextlib.suppress(subprocess.TimeoutExpired):
                result = docker(*arguments, check=False)
                (output / name).write_bytes(result.stdout + result.stderr)
        for arguments in (
            ("stop", "--timeout", "10", container),
            ("rm", "-f", container),
            ("volume", "rm", volume),
        ):
            with contextlib.suppress(subprocess.TimeoutExpired):
                docker(*arguments, check=False)
        (output / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"MT5 component smoke evidence: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Final MT5 image tag or immutable image ID")
    parser.add_argument("--output", default="artifacts/ci-smoke")
    main(parser.parse_args())
