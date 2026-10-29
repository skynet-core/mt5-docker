"""Broker validation contracts use fake MCP boundaries and real private evidence files."""

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import validate as validator
from PIL import Image


def reply(payload=b"", *, returncode=0, stderr=b""):
    return SimpleNamespace(stdout=payload, stderr=stderr, returncode=returncode)


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class PackageSession:
    def __init__(self, driver):
        self.driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_error):
        return False

    async def initialize(self):
        return SimpleNamespace(
            model_dump=lambda **_options: {"protocolVersion": "2025-03-26"}
        )

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name="mt5_query", inputSchema={"type": "object"})]
        )

    async def call_tool(self, name, arguments):
        self.driver.queries.append((name, arguments))
        operation = arguments["operation"]
        success = True
        if operation == "terminal_info":
            value = {"connected": self.driver.failure != "disconnected"}
        elif operation == "account_info":
            value = {
                "login": 999 if self.driver.failure == "wrong-account" else 12345678,
                "server": "Wrong-Demo"
                if self.driver.failure == "wrong-server"
                else "Example-Demo",
            }
        elif operation == "copy_rates_from_pos":
            value = [] if self.driver.failure == "rates" else [{"time": 1}] * 10
        elif operation == "copy_rates_range":
            self.driver.history_calls += 1
            start = int(
                datetime.strptime(self.driver.args.start, "%Y.%m.%d")
                .replace(tzinfo=UTC)
                .timestamp()
            )
            if self.driver.failure == "history-timeout":
                value = []
            elif self.driver.history_retry and self.driver.history_calls == 1:
                success = False
                value = None
            elif self.driver.history_retry and self.driver.history_calls == 2:
                value = []
            else:
                value = [{"time": start}]
        else:
            raise AssertionError(operation)
        return SimpleNamespace(
            isError=False,
            structuredContent={"success": success, "data": value},
            content=[],
        )


class ValidationBoundary:
    def __init__(
        self,
        output,
        *,
        failure=None,
        native=False,
        history_retry=False,
        report_retry=False,
    ):
        self.args = SimpleNamespace(
            runtime="docker",
            container="private-mt5",
            case="test",
            output=str(output),
            url="http://private.invalid/mcp",
            native_url="http://private.invalid/native" if native else None,
            kwin_version="6.8.2",
            symbol="EURUSD",
            start="2026.09.01",
            end="2026.09.08",
            timeout=5,
        )
        self.failure = failure
        self.history_retry = history_retry
        self.report_retry = report_retry
        self.history_calls = 0
        self.report_copies = 0
        self.clock = 0
        self.commands = []
        self.queries = []
        self.connections = []
        self.close_calls = 0
        self.files = {}
        self.application = png(
            Image.linear_gradient("L").resize((640, 480)).convert("RGB")
        )
        palette = [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
            (255, 128, 0),
            (128, 0, 255),
        ]
        chart = Image.new("RGB", (64, 48))
        chart.putdata(palette * (64 * 48 // len(palette)))
        self.chart = png(chart)

    def monotonic(self):
        self.clock += 100 if self.failure == "history-timeout" else 1
        return self.clock

    @contextlib.asynccontextmanager
    async def connect(self, url, **options):
        self.connections.append((url, options))
        yield ("private-read", "private-write", None)

    def session(self, *_streams, **_options):
        return PackageSession(self)

    def report(self):
        fields = {
            "Expert": "WrongExpert"
            if self.failure == "wrong-expert"
            else "AlgoSamurai",
            "Symbol": "WRONG" if self.failure == "wrong-symbol" else "EURUSD (Euro)",
            "Total Trades": "3",
            "Bars": "0" if self.failure == "zero-bars" else "1 234",
        }
        if self.failure == "missing-results":
            fields.pop("Total Trades")
        image = "../outside.png" if self.failure == "unsafe-image" else "profit.png"
        return (
            "<table>"
            + "".join(
                f"<tr><td>{key}:</td><td>{value}</td></tr>"
                for key, value in fields.items()
            )
            + f'</table><img src="{image}">'
        )

    def execute(self, command, **_options):
        self.commands.append(command)
        operation = command[1]
        if self.failure == "docker" and operation == "inspect":
            return reply(returncode=23, stderr=b"synthetic inspect failure")
        if operation == "inspect":
            return reply(b"sha256:private-image")
        if operation == "cp":
            source, destination = command[2:4]
            if source.startswith(self.args.container + ":"):
                source = source.split(":", 1)[1]
                destination = Path(destination)
                if source.endswith(".htm"):
                    self.report_copies += 1
                    if self.failure == "missing-report" or (
                        self.report_retry and self.report_copies == 1
                    ):
                        return reply(returncode=1)
                if "/Logs" in source:
                    destination.mkdir()
                    (destination / "test.log").write_text("private test log")
                    return reply()
                destination.write_bytes(self.files[source])
                return reply()
            # Host-to-container source code/configuration transfer is recorded only.
            return reply()
        if operation == "exec":
            if command[-1] == "/etc/plasma/session.env":
                return reply(
                    b"DISPLAY_BACKEND=wayland\nDISPLAY_MODE=headless\n"
                    if self.failure != "profile"
                    else b"DISPLAY_BACKEND=bad\n"
                )
            if command[-1] == "/run/plasma/mt5-environment.json":
                return reply(
                    json.dumps(
                        {
                            "DISPLAY": ":123",
                            "WINEPREFIX": "/private/wine",
                            "MT5_PASSWORD": "do-not-forward",
                            "UNRELATED": "do-not-forward",
                        }
                    ).encode()
                )
            if command[-1] == "/run/plasma/target-app.json":
                return reply(b'{"supervisor":777}')
            if "supportInformation" in command:
                version = (
                    "wrong"
                    if self.failure in ("version", "overlay-version")
                    else "6.8.2"
                )
                return reply(
                    b"no version supplied"
                    if self.failure == "no-version"
                    else f"KWin version: {version}\n".encode()
                )
            if "test" in command:
                return reply(returncode=0)
            if "show" in command:
                return reply(
                    b"ActiveState=active\nSubState=running\nNRestarts=0\nExecMainStatus=0\n"
                )
            if "wine" in command:
                program = command[command.index("wine") + 1 :]
                if program[0] == r"C:\MT5\metaeditor64.exe":
                    stem = (
                        "AlgoSamurai"
                        if any("AlgoSamurai.mq5" in argument for argument in program)
                        else "Screenshot"
                    )
                    folder = "Experts" if stem == "AlgoSamurai" else "Scripts"
                    base = f"{validator.MT5}/MQL5/{folder}/{stem}"
                    self.files[base + ".log"] = (
                        "1 error, 0 warnings"
                        if self.failure == "compile"
                        else "0 errors, 0 warnings"
                    ).encode("utf-16")
                    self.files[base + ".ex5"] = b"private compiled test"
                elif program[0] == r"C:\MT5\terminal64.exe":
                    name = "validation-1700000000"
                    self.files[f"{validator.MT5}/{name}.htm"] = self.report().encode(
                        "utf-16"
                    )
                    self.files[f"{validator.MT5}/profit.png"] = self.chart
                    self.files[f"{validator.MT5}/MQL5/Files/validation-chart.png"] = (
                        png(Image.new("RGB", (64, 48)))
                        if self.failure == "blank-chart"
                        else self.chart
                    )
                elif "WaitForSingleObject" in program[-1]:
                    self.close_calls += 1
                    if self.failure == "close-timeout" and self.close_calls == 2:
                        raise subprocess.TimeoutExpired(command, 45)
                elif "ImageGrab.grab" in program[-1]:
                    self.files[f"{validator.MT5}/validation-window.png"] = (
                        png(Image.new("RGB", (10, 10)))
                        if self.failure == "small-application"
                        else png(Image.new("RGB", (640, 480)))
                        if self.failure == "blank-application"
                        else self.application
                    )
                return reply(b"private Wine process\n")
            if "python3" in command and "-c" in command:
                program = command[command.index("-c") + 1]
                if "('Login','Server')" in program:
                    return reply(
                        json.dumps(
                            {
                                "Login": ""
                                if self.failure == "missing-account"
                                else "bad"
                                if self.failure == "invalid-account"
                                else "12345678",
                                "Server": "Example-Demo",
                            }
                        ).encode()
                    )
                if "/proc/net/tcp" in program:
                    return reply(b"True\n")
                if "p.name.lower()=='logs'" in program:
                    return reply(b'["MQL5/Logs"]')
                return reply()
        return reply(b"private diagnostic\n")

    def run(self, *, environment=None):
        with (
            patch.dict(os.environ, environment or {}, clear=True),
            patch.object(validator.subprocess, "run", side_effect=self.execute),
            patch.object(validator, "streamablehttp_client", side_effect=self.connect),
            patch.object(validator, "ClientSession", side_effect=self.session),
            patch.object(validator.time, "time", return_value=1700000000),
            patch.object(validator.time, "monotonic", side_effect=self.monotonic),
            patch.object(validator.asyncio, "sleep", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            asyncio.run(validator.validate(self.args))


class BrokerValidationTests(unittest.TestCase):
    def test_full_success_retains_real_reports_pngs_and_private_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            boundary = ValidationBoundary(
                directory, native=True, history_retry=True, report_retry=True
            )
            boundary.run(
                environment={
                    "MT5_MCP_TOKEN": "private-test-token",
                    "MT5_ACCOUNT": "12345678",
                    "MT5_SERVER": "Example-Demo",
                }
            )
            output = Path(directory) / "test"
            evidence = json.loads((output / "results.json").read_text())
            self.assertTrue(evidence["passed"])
            self.assertEqual(evidence["phase"], "complete")
            self.assertEqual(evidence["history_ready"]["bars"], 1)
            self.assertEqual(boundary.history_calls, 3)
            self.assertEqual(boundary.report_copies, 2)
            self.assertIn("mt5_query", evidence["native_tools"])
            self.assertTrue(evidence["compiled"])
            self.assertTrue((output / "chart.png").read_bytes().startswith(b"\x89PNG"))
            self.assertTrue((output / "application.png").is_file())
            self.assertEqual(
                json.loads((output / "summary.json").read_text())["Bars"], "1 234"
            )
            self.assertTrue((output / "mt5-MQL5-Logs/test.log").is_file())
            self.assertNotIn("Password", (output / "tester.ini").read_text())
            self.assertNotIn("do-not-forward", str(boundary.commands))
            self.assertEqual(
                boundary.connections[-1][1]["headers"],
                {"Authorization": "Bearer private-test-token"},
            )
            public = json.loads((output / "diagnostics.json").read_text())
            self.assertNotIn("calls", public)
            self.assertNotIn("container", public)
            self.assertTrue(any("-CONT" in command for command in boundary.commands))

    def test_success_uses_mounted_settings_without_native_handshake_or_env_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            boundary = ValidationBoundary(directory)
            boundary.args.kwin_version = None
            boundary.run()
            evidence = json.loads((Path(directory) / "test/results.json").read_text())
            self.assertTrue(evidence["passed"])
            self.assertNotIn("native_tools", evidence)
            self.assertEqual(len(boundary.connections), 1)

    def test_failures_leave_evidence_and_never_claim_a_completed_case(self):
        failures = {
            "docker": "failed",
            "no-version": "did not report",
            "version": "does not match",
            "profile": "display profile",
            "missing-account": "verify automatic login",
            "invalid-account": "account number",
            "disconnected": "disconnected",
            "wrong-account": "different account",
            "wrong-server": "different server",
            "rates": "insufficient chart history",
            "history-timeout": "Historical M1 bars",
            "compile": "compilation failed",
            "small-application": "too small",
            "blank-application": "blank",
            "missing-report": "fresh HTML report",
            "wrong-expert": "Wrong expert",
            "wrong-symbol": "Wrong symbol",
            "missing-results": "completed tester results",
            "zero-bars": "no price history",
            "unsafe-image": "Unsafe report image",
            "blank-chart": "Chart screenshot is blank",
        }
        for failure, message in failures.items():
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                boundary = ValidationBoundary(directory, failure=failure)
                with self.assertRaisesRegex(RuntimeError, message):
                    boundary.run()
                output = Path(directory) / "test"
                evidence = json.loads((output / "results.json").read_text())
                self.assertFalse(evidence["passed"])
                self.assertTrue((output / "failure.log").is_file())
                self.assertTrue((output / "services.log").is_file())
                self.assertTrue((output / "container.log").is_file())
                public = json.loads((output / "diagnostics.json").read_text())
                self.assertEqual(public["failure_type"], "RuntimeError")
                self.assertNotIn("error", public)

    def test_invalid_backtest_dates_and_overlay_version_are_rejected(self):
        for failure in ("dates", "overlay-version"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                boundary = ValidationBoundary(directory, failure=failure)
                if failure == "dates":
                    boundary.args.end = boundary.args.start
                else:
                    boundary.args.kwin_version = None
                with self.assertRaisesRegex(
                    RuntimeError, "start must precede|installed KWin"
                ):
                    boundary.run()

    def test_backtest_close_failure_still_resumes_supervisor_and_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            boundary = ValidationBoundary(directory, failure="close-timeout")
            with self.assertRaises(subprocess.TimeoutExpired):
                boundary.run()
            self.assertTrue(any("-CONT" in command for command in boundary.commands))
            self.assertTrue(
                any(
                    command[-3:] == ["systemctl", "start", "mt5-mcp.service"]
                    for command in boundary.commands
                )
            )
            self.assertTrue(
                any(
                    command[-1].endswith("validation-1700000000.ini")
                    and "rm" in command
                    for command in boundary.commands
                )
            )

    def test_native_connection_without_token_has_no_authorization_header(self):
        with tempfile.TemporaryDirectory() as directory:
            boundary = ValidationBoundary(directory, native=True)
            boundary.run()
            self.assertIsNone(boundary.connections[-1][1]["headers"])


class ValidationUtilityTests(unittest.TestCase):
    def test_tool_content_fallback_requires_data_and_accepts_json_or_plain_text(self):
        def text(value):
            return SimpleNamespace(type="text", text=value)

        for contents, expected in (
            ([text('{"data":1}')], {"data": 1}),
            ([text("plain"), text("text")], {"text": "plain\ntext"}),
        ):
            result = SimpleNamespace(
                isError=False, structuredContent=None, content=contents
            )
            self.assertEqual(validator.tool_data(result), expected)
        with self.assertRaisesRegex(RuntimeError, "no data"):
            validator.tool_data(
                SimpleNamespace(
                    isError=False,
                    structuredContent=None,
                    content=[SimpleNamespace(type="image")],
                )
            )

    def test_text_files_support_windows_and_utf8_boms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report"
            for encoding in ("utf-16", "utf-8-sig"):
                path.write_bytes("private report text".encode(encoding))
                self.assertEqual(validator.read_text(path), "private report text")

    def test_cli_rejects_path_traversal_and_invalid_symbols_before_validation(self):
        for case, symbol in (
            ("..", "EURUSD"),
            ("bad/case", "EURUSD"),
            ("safe", "bad/symbol"),
        ):
            with (
                self.subTest(case=case, symbol=symbol),
                patch.object(
                    sys,
                    "argv",
                    [
                        "validate",
                        "--container",
                        "private",
                        "--case",
                        case,
                        "--symbol",
                        symbol,
                    ],
                ),
                self.assertRaisesRegex(RuntimeError, "Invalid"),
            ):
                validator.main()
        with (
            patch.object(sys, "argv", ["validate", "--help"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as status,
        ):
            runpy_path = Path(validator.__file__)
            import runpy

            runpy.run_path(str(runpy_path), run_name="__main__")
        self.assertEqual(status.exception.code, 0)

    def test_valid_cli_dispatches_parsed_case_to_async_validator(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "validate",
                    "--container",
                    "private",
                    "--case",
                    "safe",
                    "--symbol",
                    "EURUSD",
                ],
            ),
            patch.object(validator, "validate", new_callable=AsyncMock) as validate,
        ):
            validator.main()
        validate.assert_awaited_once()
        self.assertEqual(validate.call_args.args[0].case, "safe")
        self.assertEqual(validate.call_args.args[0].symbol, "EURUSD")


if __name__ == "__main__":
    unittest.main()
