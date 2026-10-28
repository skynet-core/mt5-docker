"""Validate a running MT5 image and keep evidence even when a check fails."""

import argparse
import asyncio
import contextlib
import json
import os
import re
import shlex
import subprocess
import time
import traceback
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from PIL import Image

MT5 = "/home/plasma/.wine/drive_c/MT5"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def tool_data(result):
    require(not result.isError, str(result.content))
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [part.text for part in result.content if part.type == "text"]
    require(texts, "MCP tool returned no data")
    try:
        return json.loads("\n".join(texts))
    except ValueError:
        return {"text": "\n".join(texts)}


def compilation_ok(log, binary):
    return bool(re.search(r"\b0 errors?[,;]", log, re.IGNORECASE)) and bool(binary)


def kwin_version(support):
    match = re.search(
        r"^[ \t]*KWin[ \t]+version:[ \t]*(\S+)",
        support,
        re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1) if match else None


def display_profile(session):
    return {
        key: value
        for line in session.splitlines()
        for item in shlex.split(line, comments=True)
        for key, separator, value in [item.partition("=")]
        if separator and key in ("DISPLAY_BACKEND", "DISPLAY_MODE")
    }


def public_diagnostics(evidence):
    # Error strings and MCP responses can contain account data.
    return {
        key: evidence[key]
        for key in (
            "case",
            "passed",
            "phase",
            "failure_type",
            "image_id",
            "display_backend",
            "display_mode",
            "kwin_version",
            "mcp_state",
        )
        if key in evidence
    }


def mcp_state(docker, container):
    """Collect bounded public state without journals, arguments or RPC calls."""
    result = {}
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        status = docker(
            "exec",
            container,
            "systemctl",
            "show",
            "mt5-mcp.service",
            "--property=ActiveState,SubState,NRestarts,ExecMainStatus",
            timeout=2,
            check=False,
        )
        if status.returncode == 0:
            for line in status.stdout.decode(errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key in ("ActiveState", "SubState") and re.fullmatch(
                    r"[a-z-]+", value
                ):
                    result[key] = value
                elif key in ("NRestarts", "ExecMainStatus") and value.isdecimal():
                    result[key] = int(value)
    program = """from pathlib import Path
print(any(line.split()[1].rsplit(':', 1)[1] == '1F40' and line.split()[3] == '0A'
          for name in ('/proc/net/tcp', '/proc/net/tcp6')
          if Path(name).exists() for line in Path(name).read_text().splitlines()[1:]))
"""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        sockets = docker(
            "exec", container, "python3", "-c", program, timeout=2, check=False
        )
        if sockets.returncode == 0 and sockets.stdout.strip() in (b"True", b"False"):
            result["tcp_listening"] = sockets.stdout.strip() == b"True"
    return result


def history_ready(bars, start, end):
    return (
        isinstance(bars, list)
        and bool(bars)
        and all(start <= int(bar["time"]) <= end for bar in bars)
    )


def chart_has_content(image):
    colors = image.convert("RGB").getcolors(image.width * image.height)
    # MT5's native candle screenshots can use only 14 colors.
    return (
        len(colors) >= 8
        and sum(count for count, color in colors if max(color) - min(color) > 64)
        > image.width * image.height / 1000
    )


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = []
        self.cell = None
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == "img" and (source := dict(attrs).get("src")):
            self.images.append(source)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def report_summary(html):
    parser = ReportParser()
    parser.feed(html)
    return {
        row[index].rstrip(":"): row[index + 1]
        for row in parser.rows
        for index in range(len(row) - 1)
        if row[index].endswith(":") and row[index + 1]
    }


def report_images(html):
    parser = ReportParser()
    parser.feed(html)
    for name in parser.images:
        require(
            Path(name).name == name and name.lower().endswith((".png", ".gif", ".jpg")),
            "Unsafe report image path",
        )
    return parser.images


def read_text(path):
    data = Path(path).read_bytes()
    return data.decode(
        "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig",
        errors="replace",
    )


async def validate(args):
    output = Path(args.output).resolve() / args.case
    output.mkdir(parents=True, exist_ok=False)
    evidence = {
        "case": args.case,
        "container": args.container,
        "passed": False,
        "phase": "startup",
        "calls": {},
    }

    def save():
        (output / "results.json").write_text(
            json.dumps(evidence, indent=2, default=str)
        )
        (output / "diagnostics.json").write_text(
            json.dumps(public_diagnostics(evidence), indent=2) + "\n"
        )

    def checkpoint(phase):
        evidence["phase"] = phase
        save()

    def docker(*command, check=True, **kwargs):
        result = subprocess.run(
            [args.runtime, *command], capture_output=True, check=False, **kwargs
        )
        if check and result.returncode:
            raise RuntimeError(
                f"{args.runtime} {command[0]} failed: {result.stderr.decode(errors='replace')}"
            )
        return result

    def copy(source, destination, check=True):
        return docker("cp", f"{args.container}:{source}", str(destination), check=check)

    def wine(*command, timeout=120, check=True):
        environment = json.loads(
            docker(
                "exec", args.container, "cat", "/run/plasma/mt5-environment.json"
            ).stdout
        )
        allowed = (
            "DISPLAY",
            "XAUTHORITY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "WINEPREFIX",
            "WINEDEBUG",
            "WINEDLLOVERRIDES",
        )
        exports = [
            f"{key}={value}" for key, value in environment.items() if key in allowed
        ]
        return docker(
            "exec",
            "-u",
            "plasma",
            args.container,
            "env",
            *exports,
            "wine",
            *command,
            timeout=timeout,
            check=check,
        )

    async def call(session, operation, **arguments):
        checkpoint(operation)
        print(f"{args.case}: {operation}", flush=True)
        data = tool_data(
            await session.call_tool("mt5_query", {"operation": operation, **arguments})
        )
        evidence["calls"][operation] = data
        save()
        require(data.get("success") is True, f"{operation} failed: {data}")
        return data["data"]

    try:
        checkpoint("image-and-compositor")
        evidence["image_id"] = (
            docker("inspect", "--format", "{{.Image}}", args.container)
            .stdout.decode()
            .strip()
        )
        profile = display_profile(
            docker(
                "exec", args.container, "cat", "/etc/plasma/session.env"
            ).stdout.decode()
        )
        evidence["display_backend"] = profile.get("DISPLAY_BACKEND")
        evidence["display_mode"] = profile.get("DISPLAY_MODE")
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
            args.container,
            "qdbus-qt6",
            "org.kde.KWin",
            "/KWin",
            "supportInformation",
            timeout=15,
        ).stdout.decode(errors="replace")
        (output / "compositor-support.log").write_text(support)
        evidence["kwin_version"] = kwin_version(support)
        evidence["kwin_overlay_present"] = (
            docker(
                "exec",
                args.container,
                "test",
                "-e",
                "/opt/kwin-6.8.2/bin/kwin_wayland",
                check=False,
            ).returncode
            == 0
        )
        save()
        require(evidence["kwin_version"], "Running KWin did not report its version")
        if args.kwin_version:
            require(
                evidence["kwin_version"] == args.kwin_version,
                f"Running KWin {evidence['kwin_version']} does not match required {args.kwin_version}",
            )
        require(
            evidence["display_backend"] in ("wayland", "x11")
            and evidence["display_mode"],
            "Public session environment did not report the display profile",
        )
        if (
            evidence["display_backend"] == "wayland"
            and evidence["kwin_overlay_present"]
        ):
            require(
                evidence["kwin_version"] == "6.8.2",
                "Wayland session did not use the installed KWin 6.8.2 overlay",
            )

        checkpoint("account-configuration")
        program = "import configparser,json,sys; c=configparser.ConfigParser(interpolation=None); c.read(sys.argv[1],encoding='utf-16'); print(json.dumps({k:c.get('Common',k,fallback='') for k in ('Login','Server')}))"
        settings = json.loads(
            docker(
                "exec", args.container, "python3", "-c", program, f"{MT5}/config.ini"
            ).stdout
        )
        login = (
            os.environ.get("MT5_LOGIN")
            or os.environ.get("MT5_ACCOUNT")
            or settings["Login"]
        )
        server = os.environ.get("MT5_SERVER") or settings["Server"]
        require(
            login and server, "Set MT5_LOGIN and MT5_SERVER to verify automatic login"
        )
        require(str(login).isdigit(), "MT5_LOGIN must be an account number")

        checkpoint("mcp-initialize")
        async with (
            streamablehttp_client(args.url, timeout=timedelta(seconds=60)) as streams,
            ClientSession(
                streams[0], streams[1], read_timeout_seconds=timedelta(seconds=90)
            ) as session,
        ):
            evidence["initialize"] = (await session.initialize()).model_dump(
                mode="json"
            )
            evidence["package_tools"] = [
                tool.name for tool in (await session.list_tools()).tools
            ]
            terminal = await call(session, "terminal_info")
            require(terminal["connected"], "MT5 is disconnected from the trade server")
            account = await call(session, "account_info")
            require(
                str(account["login"]) == str(login),
                "Automatic login selected a different account",
            )
            require(
                account["server"] == server,
                "Automatic login selected a different server",
            )
            rates = await call(
                session,
                "copy_rates_from_pos",
                symbol=args.symbol,
                parameters={"timeframe": "M15", "start_pos": 0, "count": 100},
            )
            require(
                isinstance(rates, list) and len(rates) >= 10,
                "MT5 returned insufficient chart history",
            )
            start = int(
                datetime.strptime(args.start, "%Y.%m.%d")
                .replace(tzinfo=UTC)
                .timestamp()
            )
            end = int(
                datetime.strptime(args.end, "%Y.%m.%d").replace(tzinfo=UTC).timestamp()
            )
            require(start < end, "Backtest start must precede its end")
            # Model 1 needs M1 history; a first download can complete after the API returns.
            deadline = time.monotonic() + 180
            while True:
                historical = None
                try:
                    historical = await call(
                        session,
                        "copy_rates_range",
                        symbol=args.symbol,
                        parameters={
                            "timeframe": "M1",
                            "date_from": start,
                            "date_to": end,
                        },
                    )
                except RuntimeError as exc:
                    evidence["history_error"] = str(exc)
                if history_ready(historical, start, end):
                    evidence["history_ready"] = {
                        "timeframe": "M1",
                        "bars": len(historical),
                        "start": start,
                        "end": end,
                    }
                    save()
                    break
                require(
                    time.monotonic() < deadline,
                    "Historical M1 bars did not become available for the requested test dates",
                )
                await asyncio.sleep(2)

        for source, folder in (
            ("expert/AlgoSamurai.mq5", "Experts"),
            ("tests/Screenshot.mq5", "Scripts"),
        ):
            stem = Path(source).stem
            checkpoint(f"compile-{stem}")
            destination = f"{MT5}/MQL5/{folder}/{stem}"
            docker("exec", args.container, "mkdir", "-p", f"{MT5}/MQL5/{folder}")
            docker(
                "cp", str(Path(source).resolve()), f"{args.container}:{destination}.mq5"
            )
            docker(
                "exec",
                args.container,
                "rm",
                "-f",
                f"{destination}.ex5",
                f"{destination}.log",
            )
            docker("exec", args.container, "chown", "plasma:", f"{destination}.mq5")
            # MetaEditor exit codes vary under Wine; fresh binary and diagnostics are authoritative.
            wine(
                r"C:\MT5\metaeditor64.exe",
                "/portable",
                f"/compile:C:\\MT5\\MQL5\\{folder}\\{stem}.mq5",
                "/log",
                check=False,
            )
            copy(f"{destination}.log", output / f"{stem}-compile.log")
            copy(f"{destination}.ex5", output / f"{stem}.ex5")
            require(
                compilation_ok(
                    read_text(output / f"{stem}-compile.log"),
                    (output / f"{stem}.ex5").stat().st_size,
                ),
                f"{stem} compilation failed",
            )
        evidence["compiled"] = True

        # Xwayland is rootless: capture MT5's window, rather than its empty root desktop.
        checkpoint("application-screenshot")
        wine(
            r"C:\Python\python.exe",
            "-c",
            "import win32gui; from PIL import ImageGrab; windows=[]; "
            "win32gui.EnumWindows(lambda h,_: windows.append(h) if "
            "win32gui.IsWindowVisible(h) and "
            "win32gui.GetClassName(h)=='MetaQuotes::MetaTrader::5.00' else None,None); "
            "assert windows, 'No MT5 window'; "
            r"ImageGrab.grab(window=windows[0]).save(r'C:\MT5\validation-window.png')",
        )
        copy(f"{MT5}/validation-window.png", output / "application.png")
        with Image.open(output / "application.png") as image:
            require(
                image.width >= 640 and image.height >= 480, "Screenshot is too small"
            )
            require(
                len(image.convert("RGB").getcolors(image.width * image.height)) > 128,
                "Screenshot is blank",
            )
        evidence["screenshot"] = "application.png"

        if args.native_url:
            checkpoint("native-mcp")
            await inspect_native(args, evidence, save)
        await backtest(args, docker, copy, wine, output, evidence, checkpoint)
        evidence["passed"] = True
        checkpoint("complete")
    except Exception as exc:
        evidence["error"] = str(exc)
        evidence["failure_type"] = type(exc).__name__
        (output / "failure.log").write_text(traceback.format_exc())
        save()
        raise
    finally:
        evidence["mcp_state"] = mcp_state(docker, args.container)
        save()
        for filename, command in (
            ("container.log", ("logs", args.container)),
            (
                "services.log",
                ("exec", args.container, "journalctl", "-b", "--no-pager"),
            ),
        ):
            result = docker(*command, check=False)
            (output / filename).write_bytes(result.stdout + result.stderr)
        program = "import json,sys;from pathlib import Path;r=Path(sys.argv[1]);print(json.dumps([str(p.relative_to(r)) for p in r.rglob('*') if p.is_dir() and p.name.lower()=='logs']))"
        result = docker(
            "exec", args.container, "python3", "-c", program, MT5, check=False
        )
        if result.returncode == 0:
            for directory in json.loads(result.stdout):
                copy(
                    f"{MT5}/{directory}",
                    output / ("mt5-" + directory.replace("/", "-")),
                    check=False,
                )
        save()
        print(f"Evidence: {output}", flush=True)


async def inspect_native(args, evidence, save):
    headers = (
        {"Authorization": f"Bearer {os.environ['MT5_MCP_TOKEN']}"}
        if os.environ.get("MT5_MCP_TOKEN")
        else None
    )
    async with (
        streamablehttp_client(
            args.native_url, headers=headers, timeout=timedelta(seconds=60)
        ) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        evidence["native_tools"] = {
            tool.name: tool.inputSchema for tool in (await session.list_tools()).tools
        }
        save()


async def backtest(args, docker, copy, wine, output, evidence, checkpoint):
    checkpoint("tester-configuration")
    name = f"validation-{int(time.time())}"
    config = output / "tester.ini"
    config.write_text(
        "[Tester]\nExpert=AlgoSamurai.ex5\n"
        f"Symbol={args.symbol}\nPeriod=M15\nModel=1\nOptimization=0\n"
        f"FromDate={args.start}\nToDate={args.end}\nReport={name}.htm\n"
        "ReplaceReport=1\nShutdownTerminal=1\nUseLocal=1\nUseRemote=0\nUseCloud=0\n"
        "Visual=0\nDeposit=10000\nCurrency=USD\nLeverage=100\n"
        f"[StartUp]\nScript=Screenshot.ex5\nSymbol={args.symbol}\nPeriod=M15\n"
    )
    docker("cp", str(config), f"{args.container}:{MT5}/{name}.ini")
    # Merge credentials inside the container; the host's tester.ini never contains a password.
    merge = """import configparser, os, pathlib, pwd, sys
root = pathlib.Path(sys.argv[1]); path = root / sys.argv[2]
config = configparser.ConfigParser(interpolation=None); config.optionxform = str
config.read(root / 'config.ini', encoding='utf-16'); config.read(path)
config['Experts'] = {'Enabled': '1', 'AllowLiveTrading': '0', 'AllowDllImport': '0'}
config['Tester']['Login'] = config['Common']['Login']
account = pwd.getpwnam('plasma')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC), 'w', encoding='utf-16') as stream:
    os.fchmod(stream.fileno(), 0o600); os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
    config.write(stream, space_around_delimiters=False)
"""
    docker("exec", args.container, "python3", "-c", merge, MT5, f"{name}.ini")
    state = json.loads(
        docker("exec", args.container, "cat", "/run/plasma/target-app.json").stdout
    )
    supervisor = str(state["supervisor"])
    close = Path("tests/close-terminal.py").read_text()
    docker("exec", args.container, "rm", "-f", f"{MT5}/MQL5/Files/validation-chart.png")
    docker("exec", args.container, "systemctl", "stop", "mt5-mcp.service")
    docker("exec", args.container, "kill", "-STOP", supervisor)
    try:
        checkpoint("tester-run")
        wine(r"C:\Python\python.exe", "-c", close)
        result = wine(
            r"C:\MT5\terminal64.exe",
            "/portable",
            rf"/config:C:\MT5\{name}.ini",
            timeout=args.timeout,
            check=False,
        )
        (output / "tester-process.log").write_bytes(result.stdout + result.stderr)
    finally:
        try:
            wine(r"C:\Python\python.exe", "-c", close, timeout=45, check=False)
        finally:
            docker("exec", args.container, "kill", "-CONT", supervisor, check=False)
            docker(
                "exec",
                args.container,
                "systemctl",
                "start",
                "mt5-mcp.service",
                check=False,
            )
            docker("exec", args.container, "rm", "-f", f"{MT5}/{name}.ini", check=False)
    checkpoint("tester-report")
    deadline = time.monotonic() + args.timeout
    while copy(f"{MT5}/{name}.htm", output / "report.htm", check=False).returncode:
        require(time.monotonic() < deadline, "Backtest produced no fresh HTML report")
        await asyncio.sleep(2)
    html = read_text(output / "report.htm")
    summary = report_summary(html)
    require(
        summary.get("Expert") == "AlgoSamurai",
        f"Wrong expert in report: {summary.get('Expert')}",
    )
    require(summary.get("Symbol", "").startswith(args.symbol), "Wrong symbol in report")
    require(
        "Total Trades" in summary and "Bars" in summary,
        "Report contains no completed tester results",
    )
    require(
        int(summary["Bars"].replace(" ", "")) > 0, "Backtest processed no price history"
    )
    evidence["backtest"] = summary
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    for image in report_images(html):
        copy(f"{MT5}/{image}", output / image)
    checkpoint("chart-screenshot")
    copy(f"{MT5}/MQL5/Files/validation-chart.png", output / "chart.png")
    with Image.open(output / "chart.png") as image:
        require(chart_has_content(image), "Chart screenshot is blank")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--case",
        required=True,
        help="Unique evidence directory, e.g. arch-wayland-headless",
    )
    parser.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument(
        "--kwin-version",
        help="Require this exact running compositor version from D-Bus",
    )
    parser.add_argument(
        "--native-url",
        help="Also require a native MCP handshake; use MT5_MCP_TOKEN for its API key",
    )
    parser.add_argument("--output", default="artifacts")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--start", default="2026.09.01")
    parser.add_argument("--end", default="2026.09.08")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    require(
        re.fullmatch(r"[a-zA-Z0-9_.-]+", args.case) and args.case not in (".", ".."),
        "Invalid case name",
    )
    require(re.fullmatch(r"[a-zA-Z0-9_.-]+", args.symbol), "Invalid symbol")
    asyncio.run(validate(args))


if __name__ == "__main__":
    main()
