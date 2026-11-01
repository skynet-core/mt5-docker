"""Run private Python entrypoints against temporary files and process boundaries."""

import asyncio
import configparser
import contextlib
import importlib.machinery
import importlib.util
import inspect
import io
import json
import os
import pwd
import runpy
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    loader = importlib.machinery.SourceFileLoader(
        "coverage_" + relative.replace("/", "_").replace("-", "_"), str(ROOT / relative)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PythonEntrypointTests(unittest.TestCase):
    def test_dotenv_entrypoint_preserves_environment_precedence_and_passes_names_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.env"
            source.write_text(
                "MT5_ACCOUNT=123\nMT5_PASSWORD='test % $ dollar'\nEMPTY\n"
            )
            with (
                patch.dict(os.environ, {"MT5_ACCOUNT": "456"}, clear=True),
                patch.object(sys, "argv", ["host-env", str(source), "docker", "run"]),
                patch("os.execvp") as execute,
            ):
                runpy.run_path(str(ROOT / "libexec/host-env"), run_name="__main__")
                execute.assert_called_once_with(
                    "docker",
                    ["docker", "run", "-e", "MT5_ACCOUNT", "-e", "MT5_PASSWORD"],
                )
                self.assertEqual(os.environ["MT5_ACCOUNT"], "456")
                self.assertEqual(os.environ["MT5_PASSWORD"], "test % $ dollar")
                self.assertNotIn("EMPTY", os.environ)

    def test_configuration_entrypoint_adds_common_and_rejects_uninitialized_volume(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "source.ini"
            source.write_text("[Experts]\nEnabled=0\n")
            destination = home / ".wine/drive_c/MT5/config.ini"
            destination.parent.mkdir(parents=True)
            current = pwd.getpwuid(os.getuid())
            account = SimpleNamespace(
                pw_dir=str(home), pw_uid=current.pw_uid, pw_gid=current.pw_gid
            )
            environment = {
                "MT5_CONFIG_FILE": str(source),
                "MT5_ACCOUNT": "123",
                "MT5_PASSWORD": "test-only-password",
                "MT5_SERVER": "Example-Demo",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("pwd.getpwnam", return_value=account),
            ):
                with self.assertRaisesRegex(
                    FileNotFoundError, "missing terminal64.exe"
                ):
                    runpy.run_path(
                        str(ROOT / "libexec/mt5-configure"), run_name="__main__"
                    )
                self.assertFalse(destination.exists())
                (destination.parent / "terminal64.exe").touch()
                runpy.run_path(str(ROOT / "libexec/mt5-configure"), run_name="__main__")
            result = configparser.ConfigParser()
            result.read(destination, encoding="utf-16")
            self.assertEqual(result["Common"]["Login"], "123")
            self.assertEqual(result["Experts"]["Enabled"], "0")

    def _exec_mcp_program(self, original_fn):
        """Run the entrypoint with a mocked mt5_mcp module. Returns (server, added tools)."""
        environment = {"DISPLAY": ":777", "WINEPREFIX": "/private/test-prefix"}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.open", return_value=io.StringIO(json.dumps(environment))),
            patch("os.execvp") as execute,
        ):
            runpy.run_path(str(ROOT / "libexec/mt5-mcp"))
            self.assertEqual(dict(os.environ), environment)
        engine, arguments = execute.call_args.args
        self.assertEqual(engine, "wine")
        self.assertEqual(arguments[:3], ["wine", r"C:\Python\python.exe", "-c"])
        program = arguments[3]

        class Tool:
            def __init__(self, name, fn):
                self.name = name
                self.fn = fn

            def model_copy(self, *, update):
                return SimpleNamespace(name=self.name, **update)

        server = SimpleNamespace(
            get_tool=AsyncMock(side_effect=lambda name: Tool(name, original_fn)),
            local_provider=SimpleNamespace(remove_tool=Mock()),
            add_tool=Mock(),
            run_async=AsyncMock(),
        )
        factory = Mock(return_value=server)
        module = types.ModuleType("mt5_mcp.mcp_server")
        module.create_mcp_server = factory
        with patch.dict(sys.modules, {"mt5_mcp.mcp_server": module}):
            exec(compile(program, "<mt5-mcp-program>", "exec"), {})
        factory.assert_called_once_with(rate_limit_per_minute=0)
        self.assertEqual(
            [call.args[0] for call in server.local_provider.remove_tool.call_args_list],
            ["mt5_query", "mt5_analyze", "mt5_execute"],
        )
        added = [call.args[0] for call in server.add_tool.call_args_list]
        # The wrappers are async: they run on the loop but push the blocking
        # tool body to the single dedicated worker thread.
        self.assertTrue(all(not tool.run_in_thread for tool in added))
        self.assertTrue(all(inspect.iscoroutinefunction(tool.fn) for tool in added))
        server.run_async.assert_awaited_once_with(
            transport="http", host="0.0.0.0", port=8000, path="/mcp"
        )
        return server, added

    def test_mcp_entrypoint_uses_private_environment_and_dispatches_tools_off_the_loop(
        self,
    ):
        def original_query(operation="terminal_info"):
            return {"success": True, "operation": operation}

        _, added = self._exec_mcp_program(original_query)
        wrapper = added[0].fn
        # The wrapper must not change the tool's parameter schema.
        self.assertEqual(inspect.signature(wrapper), inspect.signature(original_query))

        async def scenario():
            background = asyncio.create_task(wrapper("terminal_info"))
            await asyncio.sleep(0)  # let the background wrapper claim the worker
            return await asyncio.gather(
                background, wrapper("terminal_info"), return_exceptions=True
            )

        first, second = asyncio.run(scenario())
        self.assertEqual(first, {"success": True, "operation": "terminal_info"})
        self.assertIsInstance(second, RuntimeError)
        self.assertIn("busy", str(second))

    def test_mcp_entrypoint_blocking_tool_does_not_starve_the_event_loop(self):
        # Regression: while the terminal is unreachable, MT5 IPC calls block
        # for minutes. The tool body must run off the loop so the HTTP server
        # (and the healthcheck handshake) keeps responding meanwhile; the old
        # inline execution starves the loop and records no heartbeats.
        def slow_query(operation="terminal_info"):
            time.sleep(0.3)
            return "done"

        _, added = self._exec_mcp_program(slow_query)
        wrapper = added[0].fn

        async def scenario():
            heartbeats = 0

            async def heartbeat():
                nonlocal heartbeats
                while True:
                    heartbeats += 1
                    await asyncio.sleep(0.02)

            beat = asyncio.create_task(heartbeat())
            try:
                result = await wrapper("terminal_info")
            finally:
                beat.cancel()
            return result, heartbeats

        result, heartbeats = asyncio.run(scenario())
        self.assertEqual(result, "done")
        self.assertGreaterEqual(heartbeats, 5)


class DockerfileRenderingTests(unittest.TestCase):
    def test_all_generated_variants_match_their_committed_dockerfile(self):
        generator = load("build/render-dockerfiles.py")
        template = (ROOT / "Dockerfile.fedora-44").read_text()
        for distro, family in generator.DISTROS.items():
            with self.subTest(distro=distro):
                result = generator.render(template, distro, family)
                self.assertEqual(result, (ROOT / f"Dockerfile.{distro}").read_text())
                self.assertNotIn("build/packages/fedora/", result)
                if family == "ubuntu":
                    self.assertIn("build/packages/debian/input-build.txt", result)
                    self.assertIn("build/packages/ubuntu/desktop.txt", result)
                if family == "arch":
                    self.assertIn("FROM desktop-packages AS x11-build", result)
                    self.assertNotIn("ARG KWIN_VARIANT=", result)
                else:
                    self.assertIn("ARG KWIN_VARIANT=source", result)
                    self.assertIn("FROM kwin-${KWIN_VARIANT} AS kwin-selected", result)

    def test_check_reports_missing_or_stale_files_without_rewriting(self):
        generator = load("build/render-dockerfiles.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile.fedora-44").write_text(
                (ROOT / "Dockerfile.fedora-44").read_text()
            )
            stale = root / "Dockerfile.ubuntu-26.04"
            stale.write_text("keep stale bytes")
            with (
                patch.object(generator, "ROOT", root),
                patch.object(sys, "argv", ["render", "--check"]),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "Regenerate Dockerfiles:.*debian-13.*ubuntu-26.04.*archlinux",
                ):
                    generator.main()
                self.assertEqual(stale.read_text(), "keep stale bytes")
                self.assertFalse((root / "Dockerfile.debian-13").exists())
            with (
                patch.object(generator, "ROOT", root),
                patch.object(sys, "argv", ["render"]),
            ):
                generator.main()
            with (
                patch.object(generator, "ROOT", root),
                patch.object(sys, "argv", ["render", "--check"]),
            ):
                generator.main()

    def test_check_entrypoint_is_read_only(self):
        with patch.object(sys, "argv", ["render", "--check"]):
            runpy.run_path(
                str(ROOT / "build/render-dockerfiles.py"), run_name="__main__"
            )


class LintEntrypointTests(unittest.TestCase):
    def test_missing_linter_is_failure_and_other_languages_are_still_attempted(self):
        lint = load("build/lint.py")
        visited = []

        def invoke(command, **_options):
            visited.append(command)
            if command[0] == "ruff":
                raise FileNotFoundError("synthetic missing linter")
            return SimpleNamespace(returncode=0)

        with (
            patch.object(
                lint, "sources", return_value={"shell": [], "python": [], "c++": []}
            ),
            patch.object(lint.subprocess, "run", side_effect=invoke),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(lint.main(), 1)
        self.assertIn("run uv sync --locked", output.getvalue())
        self.assertTrue(any(command[0] == "shellcheck" for command in visited))
        self.assertTrue(any(command[0] == "cppcheck" for command in visited))

    def test_unknown_shebang_is_not_classified_as_python_or_shell(self):
        lint = load("build/lint.py")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "other"
            path.write_text("#!/usr/bin/env perl\n")
            self.assertIsNone(lint.language(path))

    def test_lint_entrypoint_returns_process_status(self):
        with (
            patch("subprocess.check_output", return_value=b""),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as exit_status,
        ):
            runpy.run_path(str(ROOT / "build/lint.py"), run_name="__main__")
        self.assertEqual(exit_status.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
