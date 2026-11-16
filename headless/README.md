# Standalone headless MT5

This amd64 image runs MT5 under Wine and Xvfb, with MT5's native MCP server. It does not use KDE, systemd, WebView2, or the separate `mt5-mcp` Python server. The image accepts a custom MT5 startup configuration at `/config.ini`.

Build it from the repository root:

```sh
docker build -f headless/Dockerfile -t mt5:headless .
```

Copy `config/mt5/config.ini` to a private file outside the checkout and add your
account under its existing `[Common]` section, keeping the other sections:

```ini
[Common]
Login=12345678
Password=replace-with-account-password
Server=ICMarketsEU-Demo
KeepPrivate=1
NewsEnable=0
```

Start it with that configuration. The file must be readable by container user
1000 (for example, owned by UID 1000 with mode `0600`). The named volume preserves
the MT5 installation, Wine prefix, and MCP token across container replacement.

```sh
docker run -d --name mt5-headless \
  -p 127.0.0.1:22346:22347 \
  --mount type=bind,source="$HOME/.config/mt5/account.ini",target=/config.ini,readonly \
  --mount type=volume,source=mt5-headless-data,target=/home/mt5 \
  mt5:headless
```

The container creates a different MCP token on first startup and stores it in `/home/mt5/.mcp-token` with mode `0600`. Retrieve it as the container owner:

```sh
export MT5_NATIVE_MCP_TOKEN="$(docker exec mt5-headless cat /home/mt5/.mcp-token)"
```

Connect your MCP client to `http://127.0.0.1:22346/mcp` using the header `Authorization: Bearer <token>`. MT5 listens internally on loopback port 22346 without native authentication; an authenticated proxy exposes it on container port 22347. The native MCP server can expose trading operations, so keep the token private and publish the port only to trusted clients. Processes inside the container can also access the internal server directly.

A configuration can include account credentials; the image reads the mounted
file directly and does not bake it into the image. MT5 automatically logs in
using `Login`, `Password` and `Server`. Public definitions for `ICMarketsEU-Demo`
are bundled; other brokers may require a host:port or their public
`Config/servers.dat` in the volume's `MT5` directory. Keep separate volumes per
account. A retained volume keeps its installed terminal when the image changes.

This image runs unprivileged and needs no host display, GPU or systemd. Only the
authenticated MCP proxy port is exposed. Wine and the official MT5 binaries
dominate its size; the installer-only 32-bit Wine packages and caches stay out of
the final image. MetaEditor and Strategy Tester binaries remain available for
MT5's native MCP tools.

Run the isolated smoke test after building:

```sh
python3 tests/headless_smoke.py --image mt5:headless
```

The account-free test uses a dummy configuration and a dedicated bridge network. It verifies MT5
loaded the file, authenticates to the native MCP server through the host port,
discovers and calls a tool, rejects missing and unrelated tokens, and checks
restart and volume persistence. Results and logs are retained in
`artifacts/headless-smoke/`. Use a fresh `--output` directory when repeating it.

For **real autologin acceptance**, privately export `MT5_ACCOUNT`, `MT5_PASSWORD`
and `MT5_SERVER`, then run:

```sh
python3 tests/headless_smoke.py --image mt5:headless --broker \
  --output artifacts/headless-broker
```

This mounts a temporary custom INI and verifies the exact account/server plus a
current-session authorization and connected state using native MCP. It repeats
after restart and container replacement. No login API call or trade is submitted.
Broker evidence contains only account-free assertions. CI requires this check
before publishing `ghcr.io/skynet-core/mt5-docker:headless`; see [CI setup](../.github/README.md).
