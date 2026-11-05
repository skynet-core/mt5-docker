"""Exercise the standalone image with real MT5 and its native MCP server."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check
    )


def request(port, token=None, method="initialize", session=None, params=None):
    params = params or (
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "headless-smoke", "version": "1"},
        }
        if method == "initialize"
        else {}
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if not method.startswith("notifications/"):
        message["id"] = 1
    body = json.dumps(message).encode()
    query = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", body, headers)
    with urllib.request.urlopen(query, timeout=5) as response:
        payload = response.read()
        return (
            json.loads(payload) if payload else {},
            response.headers.get("Mcp-Session-Id"),
        )


def ready(port, token, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        try:
            result, session = request(port, token)
            if (
                result.get("result", {}).get("serverInfo", {}).get("name")
                == "MetaTrader 5 MCP"
            ):
                assert session
                request(port, token, "notifications/initialized", session)
                return result["result"]["serverInfo"], session
        except urllib.error.HTTPError as error:
            if error.code == 401 and not error.headers.get("WWW-Authenticate"):
                raise RuntimeError("MCP proxy rejected its generated token") from error
        except (OSError, urllib.error.URLError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("MT5 native MCP did not complete initialization")
        time.sleep(1)


def port_for(name):
    return int(docker("port", name, "22347/tcp").stdout.strip().rsplit(":", 1)[1])


def token_for(name):
    deadline = time.monotonic() + 30
    while True:
        result = docker("exec", name, "cat", "/home/mt5/.mcp-token", check=False)
        if result.returncode == 0 and len(result.stdout.strip()) == 43:
            return result.stdout.strip()
        if time.monotonic() >= deadline:
            raise RuntimeError("MCP token was not created")
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="mt5:headless")
    parser.add_argument("--output", default="artifacts/headless-smoke")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    nonce = uuid4().hex[:12]
    network = f"mt5-headless-test-{nonce}"
    volume = f"mt5-headless-test-{nonce}-data"
    names = [f"mt5-headless-test-{nonce}-{index}" for index in (1, 2)]
    evidence = {"image": args.image, "passed": False, "cases": {}}
    created = []
    try:
        evidence["image_id"] = docker(
            "image", "inspect", args.image, "--format", "{{.Id}}"
        ).stdout.strip()
        missing = docker("run", "--rm", "--network", "none", args.image, check=False)
        assert missing.returncode != 0 and "Mount a readable" in missing.stderr
        evidence["cases"]["missing_config_rejected"] = True
        docker("network", "create", network)
        docker("volume", "create", volume)
        with tempfile.TemporaryDirectory(prefix="mt5-headless-config-") as directory:
            config = Path(directory) / "custom.ini"
            config.write_text(
                "[Common]\nKeepPrivate=1\nNewsEnable=0\n[Experts]\nEnabled=0\n[StartUp]\nSymbol=EURUSD\nPeriod=M15\n"
            )
            config.chmod(0o644)
            evidence["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()

            def start(name, persistent=False):
                extra = (
                    ["--mount", f"type=volume,source={volume},target=/home/mt5"]
                    if persistent
                    else []
                )
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--publish",
                    "127.0.0.1::22347",
                    "--mount",
                    f"type=bind,source={config},target=/config.ini,readonly",
                    *extra,
                    args.image,
                )

            for index, name in enumerate(names):
                start(name, persistent=index == 0)
                created.append(name)
            ports = [port_for(name) for name in names]
            tokens = [token_for(name) for name in names]
            assert all(len(t) == 43 for t in tokens) and tokens[0] != tokens[1]
            for name, port, token in zip(names, ports, tokens, strict=True):
                server, session = ready(port, token)
                startup = docker(
                    "exec",
                    name,
                    "sh",
                    "-c",
                    'for f in "$HOME"/MT5/logs/*.log; do '
                    'iconv -f UTF-16LE -t UTF-8 "$f" 2>/dev/null; done',
                ).stdout
                assert (
                    'successfully initialized from start config "Z:\\config.ini"'
                    in startup
                )
                assert (
                    docker(
                        "exec", name, "stat", "-c", "%a", "/home/mt5/.mcp-token"
                    ).stdout.strip()
                    == "600"
                )
                tools = request(port, token, "tools/list", session)[0]["result"][
                    "tools"
                ]
                assert tools and any(
                    tool["name"] == "get_workspace_info" for tool in tools
                )
                call = request(
                    port,
                    token,
                    "tools/call",
                    session,
                    {"name": "get_workspace_info", "arguments": {}},
                )[0]["result"]
                assert call.get("content") and not call.get("isError", False)
                for rejected_token in (None, "invalid-token", token.swapcase()):
                    try:
                        request(port, rejected_token)
                    except urllib.error.HTTPError as error:
                        assert error.code == 401
                    else:
                        raise AssertionError("Invalid MCP token was accepted")
                evidence["cases"][name] = {
                    "server": server,
                    "tool_count": len(tools),
                    "tool_call_succeeded": True,
                    "unauthenticated_status": 401,
                    "custom_config_loaded": True,
                }
            try:
                request(ports[1], tokens[0])
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("A different container's key was accepted")
            evidence["cases"]["unique_token_isolation"] = True
            docker("restart", "--time", "20", names[0])
            assert token_for(names[0]) == tokens[0]
            ready(port_for(names[0]), tokens[0])
            evidence["cases"]["restart_preserves_mcp"] = True
            docker("rm", "-f", names[0])
            start(names[0], persistent=True)
            assert token_for(names[0]) == tokens[0]
            ready(port_for(names[0]), tokens[0])
            evidence["cases"]["replacement_preserves_mcp"] = True
        evidence["passed"] = True
    finally:
        for name in names:
            logs = docker("logs", name, check=False)
            if name in created:
                (output / f"{name}.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)
        (output / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Headless MCP smoke: PASS; evidence: {output}")


if __name__ == "__main__":
    main()
