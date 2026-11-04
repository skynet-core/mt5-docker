"""Exercise cancellation through the real MCP protocol and a worker process."""

import ast
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP
from mcp import McpError

ROOT = Path(__file__).resolve().parents[1]


class Mt5McpProtocolTests(unittest.TestCase):
    def test_cancelled_request_keeps_all_tools_busy_until_worker_completes(self):
        source = ast.parse((ROOT / "libexec/mt5-mcp").read_text())
        program = next(
            ast.literal_eval(node.value)
            for node in source.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "program"
                for target in node.targets
            )
        )
        server = FastMCP("MT5 protocol test")

        def original(operation: str) -> dict:
            return {"operation": operation}

        for name in ("mt5_query", "mt5_analyze", "mt5_execute"):
            server.tool(original, name=name)

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "mt5_mcp"
            package.mkdir()
            (package / "__init__.py").touch()
            (package / "mcp_tools.py").write_text(
                "import os, pathlib, time\n"
                "def mt5_query(operation):\n"
                "    if operation == 'hold':\n"
                "        pathlib.Path(os.environ['MT5_TEST_STARTED']).touch()\n"
                "        time.sleep(1.5)\n"
                "    return {'operation': operation, 'pid': os.getpid()}\n"
                "mt5_analyze = mt5_query\n"
                "mt5_execute = mt5_query\n"
            )
            started = Path(directory) / "started"

            async def scenario(**_kwargs):
                async with Client(server) as client:
                    request_id = client.session._request_id
                    first = asyncio.create_task(
                        client.call_tool(
                            "mt5_query", {"operation": "hold"}, raise_on_error=False
                        )
                    )
                    async with asyncio.timeout(5):
                        while not started.exists():
                            await asyncio.sleep(0.01)
                    self.assertTrue(await client.ping())
                    await client.cancel(request_id, "client stopped waiting")
                    with self.assertRaisesRegex(McpError, "cancelled"):
                        await first
                    for name in ("mt5_query", "mt5_analyze", "mt5_execute"):
                        result = await asyncio.wait_for(
                            client.call_tool(
                                name, {"operation": "busy"}, raise_on_error=False
                            ),
                            timeout=0.5,
                        )
                        self.assertTrue(result.is_error)
                        self.assertIn("busy", str(result.content).lower())
                    await asyncio.sleep(1.6)
                    result = await client.call_tool(
                        "mt5_query", {"operation": "recovered"}
                    )
                    self.assertEqual(result.structured_content["operation"], "recovered")

            server.run_async = scenario
            module = types.ModuleType("mt5_mcp.mcp_server")
            module.create_mcp_server = lambda **_kwargs: server
            with (
                patch.dict(sys.modules, {"mt5_mcp.mcp_server": module}),
                patch.dict(
                    os.environ,
                    {"PYTHONPATH": directory, "MT5_TEST_STARTED": str(started)},
                ),
            ):
                exec(compile(program, "<mt5-mcp-program>", "exec"), {})


if __name__ == "__main__":
    unittest.main()
