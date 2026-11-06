# Standalone headless MT5

This amd64 image runs MT5 under Wine and Xvfb, with MT5's native MCP server. It does not use KDE, systemd, WebView2, or the separate `mt5-mcp` Python server. The image accepts a custom MT5 startup configuration at `/config.ini`.

Build it from the repository root:

```sh
docker build -f headless/Dockerfile -t mt5:headless .
```

Start it with your configuration. The file must be readable by container user 1000. The named volume preserves the MT5 installation, Wine prefix, and MCP token across container replacement.

```sh
docker run -d --name mt5-headless \
  -p 127.0.0.1:22346:22347 \
  --mount type=bind,source="$PWD/config.ini",target=/config.ini,readonly \
  --mount type=volume,source=mt5-headless-data,target=/home/mt5 \
  mt5:headless
```

The container creates a different MCP token on first startup and stores it in `/home/mt5/.mcp-token` with mode `0600`. Retrieve it as the container owner:

```sh
export MT5_NATIVE_MCP_TOKEN="$(docker exec mt5-headless cat /home/mt5/.mcp-token)"
```

Connect your MCP client to `http://127.0.0.1:22346/mcp` using the header `Authorization: Bearer <token>`. MT5 listens internally on loopback port 22346 without native authentication; an authenticated proxy exposes it on container port 22347. The native MCP server can expose trading operations, so keep the token private and publish the port only to trusted clients. Processes inside the container can also access the internal server directly.

A configuration can include account credentials; the image reads the mounted file directly and does not bake it into the image. MT5 needs valid account and broker settings for operations that require a trading connection.

Run the isolated smoke test after building:

```sh
python3 tests/headless_smoke.py --image mt5:headless
```

The test uses a dummy configuration and a private network. It verifies MT5 loaded the file, authenticates to the native MCP server, discovers and calls a tool, rejects missing and unrelated tokens, and checks restart and volume persistence. Results and logs are retained in `artifacts/headless-smoke/`.
