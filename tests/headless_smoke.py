"""Exercise the standalone image with real MT5 and its native MCP server."""

import argparse
import hashlib
import json
import os
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


def journal_for(name):
    return docker(
        "exec", name, "sh", "-c",
        'for f in "$HOME"/MT5/logs/*.log; do '
        'iconv -f UTF-16LE -t UTF-8 "$f" 2>/dev/null; done',
        check=False,
    ).stdout


def journal_status(text):
    text = text.lower()
    return {
        "authorization_seen": "authorized on " in text,
        "authorization_failed": any("authorization" in line and "failed" in line for line in text.splitlines()),
        "connection_lost": "connection lost" in text,
        "synchronized": "terminal synchronized with " in text,
        "live_update_seen": "liveupdate" in text,
    }


def broker_config():
    values = {key: os.environ.get(key, "") for key in ("MT5_ACCOUNT", "MT5_PASSWORD", "MT5_SERVER")}
    for key, value in values.items():
        if not value or any(char in value for char in "\r\n\0"):
            raise ValueError(f"{key} must be a nonempty single-line value")
    if not values["MT5_ACCOUNT"].isascii() or not values["MT5_ACCOUNT"].isdigit():
        raise ValueError("MT5_ACCOUNT must be numeric")
    return (
        "[Common]\n"
        f"Login={values['MT5_ACCOUNT']}\nPassword={values['MT5_PASSWORD']}\nServer={values['MT5_SERVER']}\n"
        "KeepPrivate=1\nNewsEnable=0\n[Experts]\nEnabled=0\nAllowLiveTrading=0\nAllowDllImport=0\n",
        values["MT5_ACCOUNT"],
        values["MT5_SERVER"],
    )


def account_status(response, login, server):
    result = response.get("result", {})
    if result.get("isError") or "error" in response:
        raise RuntimeError("Native account query failed")
    data = result.get("structuredContent")
    if data is None:
        data = json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
    account = data.get("account", {})
    connection = data.get("terminal", {}).get("connection", {})
    state = connection.get("state")
    return {
        "account_matches": str(account.get("login")) == login,
        "server_matches": connection.get("server") == server,
        "connected": state == "authorized",
        "authorized_this_session": bool(connection.get("last_authorized_at")),
        "connection_state": state if state in (
            "disconnected", "connecting", "connected", "authorized", "unknown",
        ) else f"unexpected-{type(state).__name__}",
    }


def autologin(port, token, session, login, server, timeout):
    deadline = time.monotonic() + timeout
    while True:
        response, _ = request(
            port, token, "tools/call", session,
            {"name": "get_trading_account_info", "arguments": {}},
        )
        status = account_status(response, login, server)
        if (status["account_matches"] and status["server_matches"]
                and status["connected"] and status["authorized_this_session"]):
            return status
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Automatic broker login failed: {status}")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="mt5:headless")
    parser.add_argument("--output", default="artifacts/headless-smoke")
    parser.add_argument("--broker", action="store_true", help="Require real autologin using MT5_ACCOUNT, MT5_PASSWORD and MT5_SERVER")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    credentials = broker_config() if args.broker else None
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    nonce = uuid4().hex[:12]
    network = f"mt5-headless-test-{nonce}"
    volume = f"mt5-headless-test-{nonce}-data"
    names = [f"mt5-headless-test-{nonce}-{index}" for index in (1, 2)]
    evidence = {"image": args.image, "passed": False, "broker_required": args.broker, "cases": {}}
    created = []
    try:
        evidence["image_id"] = docker(
            "image", "inspect", args.image, "--format", "{{.Id}}"
        ).stdout.strip()
        evidence["image_size_bytes"] = int(docker(
            "image", "inspect", args.image, "--format", "{{.Size}}"
        ).stdout)
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
            account_config = config
            if credentials:
                account_config = Path(directory) / "account.ini"
                account_config.write_text(credentials[0], encoding="utf-16")
                # The enclosing temporary directory is 0700; only the bind mount exposes this file.
                account_config.chmod(0o644)

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
                    f"type=bind,source={account_config if persistent else config},target=/config.ini,readonly",
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
                startup = journal_for(name)
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
                if credentials and name == names[0]:
                    evidence["cases"]["fresh_autologin"] = autologin(
                        port, token, session, *credentials[1:], args.timeout,
                    )
            try:
                request(ports[1], tokens[0])
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("A different container's key was accepted")
            evidence["cases"]["unique_token_isolation"] = True
            docker("restart", "--time", "20", names[0])
            assert token_for(names[0]) == tokens[0]
            port = port_for(names[0])
            _, session = ready(port, tokens[0])
            evidence["cases"]["restart_preserves_mcp"] = True
            if credentials:
                evidence["cases"]["restart_autologin"] = autologin(
                    port, tokens[0], session, *credentials[1:], args.timeout,
                )
            docker("rm", "-f", names[0])
            start(names[0], persistent=True)
            assert token_for(names[0]) == tokens[0]
            port = port_for(names[0])
            _, session = ready(port, tokens[0])
            evidence["cases"]["replacement_preserves_mcp"] = True
            if credentials:
                evidence["cases"]["replacement_autologin"] = autologin(
                    port, tokens[0], session, *credentials[1:], args.timeout,
                )
        evidence["passed"] = True
    finally:
        for name in names:
            logs = docker("logs", name, check=False)
            if name in created and not args.broker:
                (output / f"{name}.log").write_text(logs.stdout + logs.stderr)
            if name in created and args.broker and not evidence["passed"]:
                evidence.setdefault("broker_journals", {})[name] = journal_status(journal_for(name))
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)
        (output / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Headless MCP {'and broker autologin' if args.broker else 'smoke'}: PASS; evidence: {output}")


if __name__ == "__main__":
    main()
