# MetaTrader 5 containers

Two Linux **amd64** images are maintained:

| Image | Dockerfile | Purpose |
| --- | --- | --- |
| `debian-13` | [Dockerfile.debian-13](Dockerfile.debian-13) | MT5 with KDE/KWin, all GUI modes, WebView2, and the read-only Python MCP server |
| `headless` | [headless/Dockerfile](headless/Dockerfile) | MT5 with its native MCP server, Wine and Xvfb; custom INI autologin and one authenticated host endpoint |

Debian 13 is the conservative KDE baseline selected from the project's recorded
results. It uses pinned WineHQ 11.20 and KWin 6.8.2 for Wayland, with Debian's KWin
for X11. [Known issues](KNOWN_ISSUES.md) records the remaining limitations.
Fedora, Ubuntu, Arch and the alternate distro-KWin build have been retired.

For the smallest deployment, start with the [headless guide](headless/README.md).
It has no KDE, systemd, WebView2 or Windows Python. Wine and the terminal still
make it a gigabyte-scale image; it is not a small native Linux executable.

## KDE image

Install Docker and [`uv`](https://docs.astral.sh/uv/) on an x86-64 Linux host.
Run the launcher as your desktop user when using a nested display.

```sh
docker build --target mt5 -f Dockerfile.debian-13 -t mt5:debian-13 .
[ -e .env ] || cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Set `MT5_ACCOUNT`, `MT5_PASSWORD` and `MT5_SERVER` in `.env`. Passwords can be
quoted and contain literal dollar signs. `MT5_ENV_FILE=/absolute/path/account.env`
selects a private file elsewhere. Credentials are used only at runtime and are
excluded from Git and the build context. EA live trading is disabled by default.

```sh
DISPLAY_BACKEND=x11 DISPLAY_MODE=headless bin/mt5-run -d
```

The launcher defaults to `IMAGE_TAG=mt5:debian-13`, `CONTAINER_NAME=mt5` and
`MT5_VOLUME=mt5-data`. To use a published build, set
`IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:debian-13`. Publication is gated by the
GUI and broker tests; inspect the image's revision or pin its digest.

### Display modes

Both `DISPLAY_BACKEND=wayland` and `DISPLAY_BACKEND=x11` support every mode:

| `DISPLAY_MODE` | Access |
| --- | --- |
| `headless` | Private output, no host desktop required |
| `nested` | A window on the host Wayland or X11 desktop |
| `vnc` | VNC at `127.0.0.1:5900` |
| `web` | noVNC at `http://127.0.0.1:6080/vnc.html` |

Stop the previous container before switching modes. For example:

```sh
DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
DISPLAY_BACKEND=wayland DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=wayland bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=x11 bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=vnc VNC_PASSWORD=testpass bin/mt5-run -d
DISPLAY_BACKEND=wayland DISPLAY_MODE=web VNC_PASSWORD=testpass bin/mt5-run -d
```

`HOST_DISPLAY_BACKEND` selects the host transport independently of the container
compositor, so either compositor can nest on either host. The launcher discovers
display sockets and Xauthority. Nested clipboard synchronization supports text,
images and primary selections; a Weston host also needs its Xwayland socket and
host `DISPLAY` for clipboard exchange.

Wine DPI follows the initial Wayland scale automatically. Restart the container
after changing host scale. VNC passwords are limited to eight printable ASCII
characters; `VNC_PASSWORD_FILE` can supply a private file. `VNC_PORT`, `WEB_PORT`
and `BIND_ADDRESS` override the default loopback bindings.

The KDE image uses privileged systemd startup. On software-only hosts, KWin 6.8
needs `/dev/udmabuf`; load it with `sudo modprobe udmabuf`. NVIDIA acceleration
requires NVIDIA Container Toolkit; the launcher adds `--gpus all` when available.
Use `bin/mt5-run -d -e LIBGL_ALWAYS_SOFTWARE=1` for software rendering.
WebView2 uses its tested `--disable-gpu` policy. See [the compositor build](build/KWIN.md).

### Custom startup INI and persistence

Copy [config/mt5/config.ini](config/mt5/config.ini) to a private file and add:

```ini
[Common]
Login=12345678
Password=replace-with-account-password
Server=ICMarketsEU-Demo
```

Keep the other template sections, then launch with a matching file owner:

```sh
MT5_ENV_FILE=/dev/null MT5_CONFIG_FILE="$HOME/.config/mt5/account.ini" \
  PLASMA_UID="$(id -u)" PLASMA_GID="$(id -g)" \
  DISPLAY_BACKEND=x11 DISPLAY_MODE=headless bin/mt5-run -d
```

The launcher mounts the file read-only. Exported account settings override it;
`MT5_LOGIN` is an alias for `MT5_ACCOUNT`. The effective private UTF-16 config is
written to `C:\MT5\config.ini`. Public server definitions for `ICMarketsEU-Demo`
are bundled; other brokers may require a server host:port or their public
`Config/servers.dat` ([provenance](config/mt5/UPSTREAM)).

Each account should use its own named Wine volume. A fresh volume receives the
installed applications; an existing volume keeps its applications and data across
image upgrades. Use a new volume name to test a fresh installation. Keep the same
`PLASMA_UID`/`PLASMA_GID` when switching display modes. An empty bind mount over
`/home/plasma/.wine` cannot initialize the applications.

### MCP, health and logs

| KDE service | Host port | Launcher setting |
| --- | --- | --- |
| Read-only `mt5-mcp` | 8000 | `MT5_MCP_PORT` |
| Native MT5 MCP proxy | 22346 | `MT5_NATIVE_MCP_PORT` |
| Native MetaEditor MCP proxy | 22345 | `MT5_EDITOR_MCP_PORT` |

The package endpoint is `http://127.0.0.1:8000/mcp`. Native endpoints require
enablement and API-key configuration in the application settings. The headless
image configures its own authenticated native endpoint automatically.

```sh
docker inspect --format '{{.State.Health.Status}}' mt5
docker exec mt5 /usr/local/bin/mt5-healthcheck
docker exec mt5 journalctl -b --no-pager -u plasma-session -u mt5-mcp
docker stop mt5
```

Healthy requires the connected terminal, package MCP and KDE session. Stopping
the launcher's `--rm` container preserves its named volume.

## Validation

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python build/lint.py

# Small image: actual native MCP, authentication, config loading and persistence.
python3 tests/headless_smoke.py --image mt5:headless
# Also require exact-account broker autologin on fresh start, restart and replacement.
# Export MT5_ACCOUNT, MT5_PASSWORD and MT5_SERVER privately before running:
python3 tests/headless_smoke.py --image mt5:headless --broker --output artifacts/headless-broker

# KDE display matrix: both compositors and both nested host transports.
docker build --target runtime-test -f Dockerfile.debian-13 -t mt5:runtime-test-debian-13 .
tests/display-matrix.sh
# Actual broker/MCP queries, expert compilation and backtests in all eight modes.
MT5_ENV_FILE="$PWD/.env" tests/matrix.sh
```

The KDE CI gate additionally runs real MT5 input, scaled nested pointer, startup
autologin-input and Market painting suites. These retain screenshots and input
evidence. Their dummy autologin cases are distinct from real broker acceptance.
The headless broker test uses native MCP read-only queries; it never submits
trades and retains only account-free pass/fail evidence.

[CI documentation](.github/README.md) covers secrets, non-publishing broker runs,
image publication and artifacts. [Native tests](tests/native/README.md) exercise
the retained KDE compatibility fixes. The vendored runtime and copied build
helpers retain their [pinned upstream source ledger](build/upstream-sources.json).

### Source coverage

The source gate requires 94% executable-line coverage per maintained Python,
Bash and C++ source file. Test helpers and fixtures are linted and executed but
excluded from production coverage counts. Results are tied to source hashes.

<!-- coverage-report:start -->
The next successful default-branch run publishes the consolidated source report.
<!-- coverage-report:end -->
