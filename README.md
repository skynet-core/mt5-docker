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
[Full HTML coverage report](https://skynet-core.github.io/mt5-docker/) · [tested commit `cafaeaf1a99e`](https://github.com/skynet-core/mt5-docker/commit/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818) · [CI run](https://github.com/skynet-core/mt5-docker/actions/runs/44035170089)

Required: **94% for every executable source file**. Branch coverage is available in the detailed reports.

| Language | Files | Minimum line coverage | Required | Passed | Audited N/A |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python | 13 | 96.89% | 94% | 13/13 | 0 |
| Bash | 31 | 94.12% | 94% | 31/31 | 0 |
| C++ | 6 | 96.39% | 94% | 6/6 | 0 |

<details>
<summary>All 50 source files</summary>

| Source | Language | Covered / executable | Line coverage | Missing lines or audit |
| --- | --- | ---: | ---: | --- |
| [bin/mt5-healthcheck](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/bin/mt5-healthcheck) | Python | 32/32 | 100.00% |  |
| [bin/mt5-launch](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/bin/mt5-launch) | Bash | 7/7 | 100.00% |  |
| [bin/mt5-run](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/bin/mt5-run) | Bash | 14/14 | 100.00% |  |
| [build/ci-broker-env](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/ci-broker-env) | Bash | 13/13 | 100.00% |  |
| [build/collect-artifacts](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/collect-artifacts) | Bash | 20/21 | 95.24% | [28](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/collect-artifacts#L28) |
| [build/coverage\_report.py](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/coverage_report.py) | Python | 124/124 | 100.00% |  |
| [build/create-user](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/create-user) | Bash | 18/18 | 100.00% |  |
| [build/install-mt5](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/install-mt5) | Bash | 64/64 | 100.00% |  |
| [build/install-packages](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/install-packages) | Bash | 13/13 | 100.00% |  |
| [build/install-winehq](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/install-winehq) | Bash | 11/11 | 100.00% |  |
| [build/kwin-6.8.2](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-6.8.2) | Bash | 30/30 | 100.00% |  |
| [build/kwin-phases/collect.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/collect.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/frameworks.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/frameworks.sh) | Bash | 5/5 | 100.00% |  |
| [build/kwin-phases/kwin.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/kwin.sh) | Bash | 7/7 | 100.00% |  |
| [build/kwin-phases/platform.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/platform.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/qtbase.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/qtbase.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtdeclarative.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/qtdeclarative.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtmodules.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/kwin-phases/qtmodules.sh) | Bash | 3/3 | 100.00% |  |
| [build/lint.py](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/lint.py) | Python | 56/56 | 100.00% |  |
| [build/normalize-runtime](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/normalize-runtime) | Bash | 6/6 | 100.00% |  |
| [build/publish\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/publish_coverage.py) | Python | 194/194 | 100.00% |  |
| [build/tigervnc-viewer](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/tigervnc-viewer) | Bash | 16/17 | 94.12% | [17](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/tigervnc-viewer#L17) |
| [build/xwayland-frontend](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/build/xwayland-frontend) | Bash | 14/14 | 100.00% |  |
| [headless/start](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/headless/start) | Bash | 37/37 | 100.00% |  |
| [init.d/modules/450-mt5.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/init.d/modules/450-mt5.sh) | Bash | 2/2 | 100.00% |  |
| [libexec/host-env](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/libexec/host-env) | Python | 15/15 | 100.00% |  |
| [libexec/mt5-configure](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/libexec/mt5-configure) | Python | 32/32 | 100.00% |  |
| [libexec/mt5-dpi](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/libexec/mt5-dpi) | Python | 48/48 | 100.00% |  |
| [libexec/mt5-mcp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/libexec/mt5-mcp) | Python | 6/6 | 100.00% |  |
| [vendor/kde-container/bin/plasma-healthcheck](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/bin/plasma-healthcheck) | Python | 32/32 | 100.00% |  |
| [vendor/kde-container/bin/plasma6-run](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/bin/plasma6-run) | Bash | 88/88 | 100.00% |  |
| [vendor/kde-container/compat/egl-fence-wait.cpp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/compat/egl-fence-wait.cpp) | C++ | 35/35 | 100.00% |  |
| [vendor/kde-container/compat/wayland-pointer-enter.cpp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/compat/wayland-pointer-enter.cpp) | C++ | 178/178 | 100.00% |  |
| [vendor/kde-container/compat/xtest-paced.cpp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/compat/xtest-paced.cpp) | C++ | 26/26 | 100.00% |  |
| [vendor/kde-container/init.d/modules/300-plasma-user.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/init.d/modules/300-plasma-user.sh) | Bash | 38/38 | 100.00% |  |
| [vendor/kde-container/init.d/modules/400-plasma.sh](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/init.d/modules/400-plasma.sh) | Bash | 201/204 | 98.53% | [126](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/init.d/modules/400-plasma.sh#L126), [146](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/init.d/modules/400-plasma.sh#L146), [177](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/init.d/modules/400-plasma.sh#L177) |
| [vendor/kde-container/libexec/plasma-backend/kwin\_wayland](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-backend/kwin_wayland) | Bash | 34/34 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-gpu](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-gpu) | Python | 123/123 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-host-display](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-host-display) | Bash | 9/9 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-nested-x11](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-nested-x11) | Bash | 39/40 | 97.50% | [28](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-nested-x11#L28) |
| [vendor/kde-container/libexec/plasma-remote-output](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output) | Python | 218/225 | 96.89% | [188](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L188), [264](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L264), [283](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L283), [318](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L318), [325](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L325), [336](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L336), [441](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-remote-output#L441) |
| [vendor/kde-container/libexec/plasma-session](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-session) | Bash | 39/39 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-software-display](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-software-display) | Bash | 7/7 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-target-app](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-target-app) | Python | 120/121 | 99.17% | [44](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-target-app#L44) |
| [vendor/kde-container/libexec/plasma-vnc](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-vnc) | Bash | 18/18 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-wait](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-wait) | Python | 40/40 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-xserver](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-xserver) | Bash | 21/22 | 95.45% | [18](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/libexec/plasma-xserver#L18) |
| [vendor/kde-container/native/clipboard.cpp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp) | C++ | 401/416 | 96.39% | [74](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L74), [94](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L94), [113](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L113), [114](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L114), [118](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L118), [232](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L232), [235](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L235), [243](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L243), [245](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L245), [423](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L423), [455](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L455), [456](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L456), [537](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L537), [559](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L559), [607](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/clipboard.cpp#L607) |
| [vendor/kde-container/native/framebuffer-geometry.h](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/framebuffer-geometry.h) | C++ | 5/5 | 100.00% |  |
| [vendor/kde-container/native/kwin-input.cpp](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/kwin-input.cpp) | C++ | 140/144 | 97.22% | [62](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/kwin-input.cpp#L62), [73](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/kwin-input.cpp#L73), [236](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/kwin-input.cpp#L236), [263](https://github.com/skynet-core/mt5-docker/blob/cafaeaf1a99e9f83d1d298e2a15bb4cb3cc0a818/vendor/kde-container/native/kwin-input.cpp#L263) |

</details>
<!-- coverage-report:end -->
