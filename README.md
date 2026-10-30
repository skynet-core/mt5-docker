# MetaTrader 5 on KWin and systemd

Four amd64 images extend the corresponding [`skynet-core/systemd`](https://github.com/skynet-core/systemd/tree/1a4a0ca746a21cfff521bb1906cfe6e66ae9f578) bases:

| Dockerfile | Base image | Default local Wayland compositor |
| --- | --- | --- |
| `Dockerfile.fedora-44` | `ghcr.io/skynet-core/systemd:fedora-44` | Distribution KWin |
| `Dockerfile.debian-13` | `ghcr.io/skynet-core/systemd:debian-13` | KWin 6.8.2 |
| `Dockerfile.ubuntu-26.04` | `ghcr.io/skynet-core/systemd:ubuntu-26.04` | KWin 6.8.2 |
| `Dockerfile.archlinux` | `ghcr.io/skynet-core/systemd:archlinux` | Distribution KWin |

The images install Wine, Microsoft's Evergreen WebView2 runtime, the official MT5 terminal/MetaEditor/Strategy Tester, and Windows Python for `mt5-mcp`. MT5 runs fullscreen under KWin as the unprivileged `plasma` user. Wine uses X11, through Xwayland in Wayland sessions. Systemd remains PID 1. Plasma desktop applications and the desktop shell are omitted.

The display runtime is vendored from [`skynet-core/kde-container` at `782714f`](https://github.com/skynet-core/kde-container/tree/782714fb841737b884ea263ada5108ae3754be85). Its native clipboard bridge, patched KRFB capture/input, GPU probe, software fallback, and scoped NVIDIA synchronization fix are retained. See [the source record](vendor/kde-container/UPSTREAM) for local changes.

## Setup

Use a Linux x86-64 host with Docker and `uv` installed. The `.env` launcher needs Python 3.12 or newer, which `uv` can manage. Run the launcher as a regular user with Docker access; use your desktop user's terminal for nested displays so it can discover the display sockets and credentials. The containers use the inherited privileged systemd setup.

```sh
git clone https://github.com/skynet-core/mt5-docker.git
cd mt5-docker || exit 1
[ -e .env ] || cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Create `.env` once; keep an existing account file when updating the checkout. Set these values to your trading account:

```dotenv
MT5_ACCOUNT=12345678
MT5_PASSWORD='replace-with-account-password'
MT5_SERVER=ICMarketsEU-Demo
```

The examples below run one container at a time because they use the same host ports. Each image gets its own container name and persistent Wine volume. Set launcher options such as `IMAGE_TAG`, `CONTAINER_NAME` and `MT5_VOLUME` in the command environment, as shown; `.env` supplies the account settings.

## Run published images

Choose one of these examples. Each pulls the image and starts a detached Wayland headless session; no host display is required. See [Displays](#displays) to select nested, VNC, web or X11 instead.

### Fedora 44

```sh
docker pull ghcr.io/skynet-core/mt5-docker:fedora-44
IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:fedora-44 \
  CONTAINER_NAME=mt5-fedora-44 MT5_VOLUME=mt5-fedora-44-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Debian 13

```sh
docker pull ghcr.io/skynet-core/mt5-docker:debian-13
IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:debian-13 \
  CONTAINER_NAME=mt5-debian-13 MT5_VOLUME=mt5-debian-13-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Ubuntu 26.04

```sh
docker pull ghcr.io/skynet-core/mt5-docker:ubuntu-26.04
IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:ubuntu-26.04 \
  CONTAINER_NAME=mt5-ubuntu-26.04 MT5_VOLUME=mt5-ubuntu-26.04-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Arch Linux

```sh
docker pull ghcr.io/skynet-core/mt5-docker:archlinux
IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:archlinux \
  CONTAINER_NAME=mt5-archlinux MT5_VOLUME=mt5-archlinux-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

Published tags are `fedora-44`, `debian-13`, `ubuntu-26.04` and `archlinux`; there is no shared `latest` tag. For a fixed build, use `ghcr.io/skynet-core/mt5-docker:sha-<full-commit>-<distribution>` instead. For Debian/Ubuntu, CI records the selected KWin variant in the `io.skynet.kwin.variant` image label:

```sh
docker image inspect --format '{{index .Config.Labels "io.skynet.kwin.variant"}}' \
  ghcr.io/skynet-core/mt5-docker:debian-13
```

## Build locally

Run these commands from the repository root. Choose one build/run pair; stop an existing container with the same name before starting its replacement. These local tags are separate from the published GHCR tags.

### Fedora 44

```sh
docker build --target mt5 -f Dockerfile.fedora-44 -t mt5:fedora-44 .
IMAGE_TAG=mt5:fedora-44 CONTAINER_NAME=mt5-fedora-44 MT5_VOLUME=mt5-fedora-44-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Debian 13 with KWin 6.8.2

```sh
docker build --target mt5 --build-arg KWIN_VARIANT=source \
  -f Dockerfile.debian-13 -t mt5:debian-13 .
IMAGE_TAG=mt5:debian-13 CONTAINER_NAME=mt5-debian-13 MT5_VOLUME=mt5-debian-13-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Ubuntu 26.04 with KWin 6.8.2

```sh
docker build --target mt5 --build-arg KWIN_VARIANT=source \
  -f Dockerfile.ubuntu-26.04 -t mt5:ubuntu-26.04 .
IMAGE_TAG=mt5:ubuntu-26.04 CONTAINER_NAME=mt5-ubuntu-26.04 MT5_VOLUME=mt5-ubuntu-26.04-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Arch Linux

```sh
docker build --target mt5 -f Dockerfile.archlinux -t mt5:archlinux .
IMAGE_TAG=mt5:archlinux CONTAINER_NAME=mt5-archlinux MT5_VOLUME=mt5-archlinux-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

### Smaller Debian and Ubuntu builds

Fedora and Arch use their distribution KWin. `KWIN_VARIANT=source` is the Debian/Ubuntu default. It compiles KWin 6.8.2, Qt 6.11.2 and the required KDE Frameworks 6.30 libraries from pinned archives. The first build needs substantial time, memory and disk; separate cached stages retain completed dependencies. KWin 6.8 is Wayland-only, so X11 modes use a separate compositor. See [build/KWIN.md](build/KWIN.md) for the source stack.

Use `KWIN_VARIANT=distro` to skip that stack and install the distribution's KWin instead. These examples use separate tags and volumes:

```sh
docker build --target mt5 --build-arg KWIN_VARIANT=distro \
  -f Dockerfile.debian-13 -t mt5:debian-13-distro .
IMAGE_TAG=mt5:debian-13-distro CONTAINER_NAME=mt5-debian-13-distro \
  MT5_VOLUME=mt5-debian-13-distro-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

```sh
docker build --target mt5 --build-arg KWIN_VARIANT=distro \
  -f Dockerfile.ubuntu-26.04 -t mt5:ubuntu-26.04-distro .
IMAGE_TAG=mt5:ubuntu-26.04-distro CONTAINER_NAME=mt5-ubuntu-26.04-distro \
  MT5_VOLUME=mt5-ubuntu-26.04-distro-data \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

CI selects its variant through `KWIN_VARIANT` in [the workflow](.github/workflows/build-and-publish.yml); the current checkout selects `source`. Inspect the label of a pulled image to check the variant of that published build.

Debian and Ubuntu use pinned WineHQ 11.20 packages from their own release repositories, with a verified signing key and signed APT metadata. The recorded MT5 build 6292 aborts during startup under their distribution Wine 10 packages. Fedora and Arch use their distribution Wine packages.

## Account configuration and persistence

Account settings are materialized at `C:\MT5\config.ini` as a private UTF-16 file. Passwords are passed at runtime, never as build arguments. The host launcher uses `uv` and dotenv parsing, including quoted passwords and literal dollar signs. `.env` is excluded from Git and the Docker build context. `MT5_ENV_FILE=/absolute/path/account.env` selects an account file outside the checkout. Live EA trading is disabled by default; Strategy Tester can still test the supplied expert.

The image includes the public server definitions needed to resolve `ICMarketsEU-Demo` on first boot. This cache contains no account data; its source and checksum are recorded in [config/mt5/UPSTREAM](config/mt5/UPSTREAM). For another broker, use its host:port address or seed its public `Config/servers.dat` in the Wine volume.

A fresh named volume is populated with the image's installed Wine prefix. Use a separate `MT5_VOLUME` for each image/account. Pulling or rebuilding an image does not replace an existing volume's installed applications. Use a new volume name for a fresh prefix; retain the previous volume to keep its data. An empty bind mount over `/home/plasma/.wine` does not contain MT5.

To supply your own INI, copy the template to a private file outside the repository:

```sh
mkdir -p "$HOME/.config/mt5"
[ -e "$HOME/.config/mt5/account.ini" ] || cp config/mt5/config.ini "$HOME/.config/mt5/account.ini"
chmod 600 "$HOME/.config/mt5/account.ini"
${EDITOR:-vi} "$HOME/.config/mt5/account.ini"
```

Add `Login`, `Password` and `Server` under `[Common]`, keeping the remaining template sections:

```ini
[Common]
Login=12345678
Password=replace-with-account-password
Server=ICMarketsEU-Demo
```

Then select the file when launching any image:

```sh
MT5_ENV_FILE=/dev/null MT5_CONFIG_FILE="$HOME/.config/mt5/account.ini" \
  IMAGE_TAG=mt5:fedora-44 CONTAINER_NAME=mt5-fedora-44 MT5_VOLUME=mt5-fedora-44-data \
  PLASMA_UID="$(id -u)" PLASMA_GID="$(id -g)" \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
```

The source INI must be readable by the container's `plasma` UID; the launcher mounts it read-only. Environment account settings override the INI, including any exported `MT5_ACCOUNT`, `MT5_LOGIN`, `MT5_PASSWORD` and `MT5_SERVER`. `MT5_ENV_FILE=/dev/null` skips the checkout's `.env`. `MT5_LOGIN` is accepted as an alias for `MT5_ACCOUNT`.

## Displays

Choose `DISPLAY_BACKEND=wayland` or `x11` and one of these modes:

| Mode | Behavior |
| --- | --- |
| `headless` | Private KWin output; no host display required |
| `nested` | A window on the host's mounted Wayland or X11 display |
| `vnc` | VNC on localhost:5900 |
| `web` | noVNC at http://localhost:6080 |

The following modes work with every image above. Select its image, container and volume once; replace the Fedora values with your chosen image's values. Stop an existing container before changing its display mode.

```sh
export IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:fedora-44
export CONTAINER_NAME=mt5-fedora-44
export MT5_VOLUME=mt5-fedora-44-data
# Keep the same Wine prefix owner when switching between headless and nested.
PLASMA_UID=$(id -u)
PLASMA_GID=$(id -g)
export PLASMA_UID PLASMA_GID
```

Run **one** of these alternatives from your desktop user's terminal. For a host without a desktop, use headless, VNC or web; they do not require host display sockets.

```sh
# Headless: choose the container compositor.
DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=headless bin/mt5-run -d

# Nested on a Wayland host, including Weston.
DISPLAY_BACKEND=wayland DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=wayland bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=wayland bin/mt5-run -d

# Nested on an X11 host.
DISPLAY_BACKEND=wayland DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=x11 bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=x11 bin/mt5-run -d

# VNC: connect your VNC client to 127.0.0.1:5900 using the selected password.
DISPLAY_BACKEND=wayland DISPLAY_MODE=vnc VNC_PASSWORD=testpass bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=vnc VNC_PASSWORD=testpass bin/mt5-run -d

# Web: open http://127.0.0.1:6080/vnc.html and enter the selected password.
DISPLAY_BACKEND=wayland DISPLAY_MODE=web VNC_PASSWORD=testpass bin/mt5-run -d
DISPLAY_BACKEND=x11 DISPLAY_MODE=web VNC_PASSWORD=testpass bin/mt5-run -d
```

The launcher discovers the host Wayland/X11 socket and Xauthority cookie. `HOST_DISPLAY_BACKEND=wayland|x11` selects the host transport separately from the container compositor. A Weston host renders through its Wayland socket; clipboard exchange also requires its Xwayland socket and host `DISPLAY`. `CLIPBOARD_SYNC=1` is the default for nested sessions and bridges Unicode text, images, MIME data and primary selections bidirectionally. VNC/noVNC use their remote clipboard support.

VNC passwords are limited to eight printable ASCII characters by the protocol. `VNC_PASSWORD_FILE` mounts a private password file. `VNC_PORT`, `WEB_PORT`, and `BIND_ADDRESS` control the host binding; the default address is `127.0.0.1`.

The startup GPU probe tests EGL/GBM rendering on available devices; KWin selects its rendering GPU separately. `bin/mt5-run -e LIBGL_ALWAYS_SOFTWARE=1` requests Mesa software rendering. CPU regression tests also hide physical DRI devices before init: KWin/Wine can enumerate those devices even with this variable set.

Docker native Wayland nesting automatically adds `--gpus all` when the host NVIDIA driver and NVIDIA Container Toolkit runtime are available. This supplies the driver libraries needed to exchange frames with an NVIDIA host compositor; access to an AMD GPU alone can otherwise leave the nested window black. Explicit GPU, runtime, CDI and software-rendering arguments take precedence. For other modes on an NVIDIA host configured with NVIDIA Container Toolkit, pass `--gpus all` to `bin/mt5-run`.

The nested NVIDIA fence fix is scoped to KWin; WebView2 uses software rendering. The Debian/Ubuntu source variant builds KWin 6.8.2 with a matching Qt/KF stack under `/opt/kwin-6.8.2`, enabling the normal GPU path in Wayland VNC/web modes. Debian's distro variant uses software capture for NVIDIA VNC/web with its older KWin. Native KRFB, clipboard helpers and kiosk applications retain their distro Qt libraries. Arch's removed X11 compositor is built separately from pinned KWin-X11 sources, with compilers excluded from the final image.

```sh
# With IMAGE_TAG, CONTAINER_NAME and MT5_VOLUME selected as above:
DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d -e LIBGL_ALWAYS_SOFTWARE=1
DISPLAY_BACKEND=wayland DISPLAY_MODE=headless bin/mt5-run -d --gpus all
```

KWin 6.8 software sessions need `/dev/udmabuf` on the host. If it is absent, load the module before starting a container:

```sh
sudo modprobe udmabuf
```

## Health, logs and stopping

The examples use `-d` to run in the background. Omit it to attach to the container. Without explicit options, the launcher uses `IMAGE_TAG=mt5:fedora-44`, `CONTAINER_NAME=mt5`, `MT5_VOLUME=mt5-data` and headless mode.

Select the container you started, then inspect its health and logs. First boot includes Wine startup and broker synchronization; `healthy` requires the actual connected terminal and MCP service.

```sh
export CONTAINER_NAME=mt5-fedora-44
docker ps --filter "name=$CONTAINER_NAME"
docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME"
docker exec "$CONTAINER_NAME" /usr/local/bin/mt5-healthcheck
docker logs --tail 100 "$CONTAINER_NAME"
docker exec "$CONTAINER_NAME" journalctl -b --no-pager \
  -u plasma-session.service -u mt5-mcp.service -u plasma-clipboard.service
```

Stop the container when finished:

```sh
docker stop "$CONTAINER_NAME"
```

The launcher uses `--rm`, so stopping removes the container while retaining its named Wine volume. Start it again by repeating its launch command with the same `MT5_VOLUME`.

All service bindings default to `127.0.0.1`:

| Service | Host port | Launcher setting |
| --- | --- | --- |
| Package MCP | 8000 | `MT5_MCP_PORT` |
| Native MT5 MCP | 22346 | `MT5_NATIVE_MCP_PORT` |
| Native MetaEditor MCP | 22345 | `MT5_EDITOR_MCP_PORT` |
| VNC, in `vnc` mode | 5900 | `VNC_PORT` |
| noVNC, in `web` mode | 6080 | `WEB_PORT` |

To run Ubuntu alongside the default-port Fedora example, give it separate ports as well as its own name and volume:

```sh
IMAGE_TAG=ghcr.io/skynet-core/mt5-docker:ubuntu-26.04 \
  CONTAINER_NAME=mt5-ubuntu-26.04 MT5_VOLUME=mt5-ubuntu-26.04-data \
  MT5_MCP_PORT=8001 MT5_NATIVE_MCP_PORT=22348 MT5_EDITOR_MCP_PORT=22350 \
  DISPLAY_BACKEND=wayland DISPLAY_MODE=web WEB_PORT=6081 VNC_PASSWORD=testpass \
  bin/mt5-run -d
```

Open http://127.0.0.1:6081/vnc.html for this container; its package MCP endpoint is http://127.0.0.1:8001/mcp.

## MCP and expert validation

[`mt5-mcp==0.6.3`](https://github.com/Cloudmeru/MetaTrader-5-MCP-Server/tree/b4ded2c9806e0ce290ff7e2af6004913e71bf67e) runs in Windows Python inside the same Wine prefix. Its read-only MCP endpoint is `http://127.0.0.1:8000/mcp`. It queries the actual MT5 terminal through the MetaTrader5 IPC package. The health check requires a successful MCP handshake, a connected terminal query, and a healthy KWin kiosk.

The image installs the dependencies needed for query and technical-analysis tools. Forecasting dependencies (Prophet, XGBoost, scikit-learn) are omitted to keep the runtime smaller. The full requested package is a locked development dependency managed by `uv`:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python build/lint.py
uv run --locked python tests/validate.py --container mt5-fedora-44 --case fedora-wayland-headless
MT5_ENV_FILE="$PWD/.env" tests/matrix.sh
```

The validator checks the exact account/server and downloaded history through `mt5-mcp`, compiles `expert/AlgoSamurai.mq5` with MetaEditor, runs Strategy Tester with a unique report, and retrieves the chart screenshot, MT5 window screenshot, report, parsed statistics, compiler output and service/terminal/tester logs. It prints each evidence directory under `artifacts/`. Backtests use local agents with cloud and remote agents disabled.

`tests/matrix.sh` runs all four images across both compositors and all four modes, authenticates VNC/web connections, and captures their remote framebuffer. Pass distro names to select images, for example `tests/matrix.sh fedora-44`. Nested cases require an available host display. The separate `tests/clipboard.py` exchanges text and PNG data with a disposable parent display, checks that its sockets belong to that parent, and saves bidirectional evidence without changing the user's clipboard.

The matrix expects local `mt5:<distribution>` image tags. If you built the Debian/Ubuntu distro variant under those canonical tags, also set `KWIN_VARIANT=distro` when running `tests/matrix.sh` so it checks the selected compositor variant.

MT5's separate native MCP endpoint can be reached on host port 22346, and MetaEditor's on 22345. These are systemd socket proxies to the applications' internal loopback listeners; they do not bind MT5's own ports. Native API keys and enablement are configured in MT5's MCP settings. Pass `MT5_MCP_TOKEN` and `--native-url http://127.0.0.1:22346/mcp` to additionally check that endpoint. The default validator uses MT5's documented [`/config` interface](https://www.metatrader5.com/en/terminal/help/start_advanced/start) to run the test without requiring a native MCP key.

Builds fail if the Windows installers do not leave the required MT5 and WebView2 executables. Multi-stage builds retain native runtime plugins and leave compilation tools out. Native sources, package manifests and `uv.lock` are checked in; mutable upstream distro packages and Evergreen installers are downloaded during the build, with installer hashes recorded in the build log.

## Compositor regression tests

The `runtime-test` target adds the upstream Qt test window to the minimal compositor runtime. It omits MT5 and Wine, and its test binary is excluded from the final `mt5` image.

```sh
docker build --target runtime-test -f Dockerfile.fedora-44 -t mt5:runtime-test-fedora-44 .
uv run --locked python tests/runtime.py --image mt5:runtime-test-fedora-44 --case fedora-44-runtime
uv run --locked python tests/runtime.py --image mt5:runtime-test-fedora-44 --case fedora-44-gpu --backends wayland --gpu
```

Use the corresponding Dockerfile and tag for Debian, Ubuntu or Arch. The default matrix checks ten profiles: both compositors in headless, VNC and web modes, plus nested mode through each host transport. Every profile renders the GUI fixture and saves `screenshot.png`. Nested, VNC and web profiles also click, type and save a unique token through the real display input path, then compare the file written by the GUI. Nested profiles exchange Unicode text and PNG images in both directions. Disposable parent displays keep the user's actual clipboard untouched.

Run the complete display matrix against the four production images with:

```sh
# Build matching test tools without recompiling the private KWin stack.
for distro in fedora-44 debian-13 ubuntu-26.04 archlinux; do
  docker build -f "Dockerfile.$distro" --target runtime-test \
    --build-arg KWIN_VARIANT=distro -t "mt5:runtime-test-$distro" .
done
tests/display-matrix.sh
```

This covers **40 cases**: four images × two compositors × headless/VNC/web plus nested on both Wayland and X11 hosts. Pass distribution names to select a subset, such as `tests/display-matrix.sh debian-13 ubuntu-26.04`. For a single custom image, set `IMAGE_TAG` and `FIXTURE_IMAGE`. The isolated parent defaults to the tested production image, keeping its Wayland capabilities compatible; `PARENT_IMAGE` can select a compatible lightweight parent. CI uses the matching distro fixture image as parent. `OUTPUT_DIR` selects a fresh evidence directory; the default is `artifacts/display-matrix/<timestamp>-<pid>/`.

Each run contains an `INDEX.md` and `index.json`, followed by `<distribution>/<backend>/<mode>/`; nested cases add `on-wayland/` or `on-x11/`. Each case retains its screenshot, result metadata, startup/container/service logs and GUI input files. Headless captures use the compositor screenshot API or X11 root pixels, with VNC/web services disabled. The index fails if any expected profile, screenshot, input proof or required log is missing. This display suite uses a test GUI; the separate MT5 suite above verifies actual account login, MT5 charts and expert backtests.

The additional MT5 input suite drives the **real terminal** in eight GUI profiles per image: Wayland/X11 × VNC, web, nested on Wayland and nested on X11. It clicks the company-search edit, types mixed-case text, replaces the nonempty value with Ctrl+A, checks the exact text through read-only Win32 APIs, clicks Cancel, opens File with the mouse and closes it with Escape. Web input travels through Chromium and noVNC. Each case uses an internal network, a fresh Wine volume, empty account credentials and a private parent display.

```sh
uv run --locked playwright install --with-deps --only-shell chromium
for distro in fedora-44 debian-13 ubuntu-26.04 archlinux; do
  uv run --locked python tests/mt5_input.py \
    --image "ghcr.io/skynet-core/mt5-docker:$distro" --case "$distro"
done
```

Evidence is under `artifacts/mt5-input/<distribution>/<backend>/<mode>/`, with `on-wayland/` or `on-x11/` below nested cases. It includes startup, keyboard, menu and final screenshots, expected/observed text, read-only GUI state, the last input phase and service/compositor logs. Web cases also retain a browser trace. `results.json` fails when any expected case or input assertion fails. Add `--gpu` for a separate NVIDIA hardware run; Wayland uses NVIDIA while the X11/Xvfb profiles use llvmpipe. Standard CI hides physical DRI devices inside the test namespace to exercise software rendering. Both tiers hide host audio, input and USB devices before init.

The pointer regression adds **12 nested profiles per image**: both compositors and host transports at 100%, 125% and 200% display scale. A private Arch KWin parent provides real window decorations and cursor capture on software runners. Tests move and resize the window, compare physical MT5 pointer coordinates against independent window geometry, and click immediately after re-entry without an intervening movement. Cursor checks compare stable OFF/ON/OFF screenshots and inspect the full difference bounds; all capture attempts are retained. No observed cursor offset is used to adjust input.

```sh
mkdir -p artifacts/pointer-parent
cat Dockerfile.archlinux tests/Dockerfile.pointer-parent.fragment \
  > artifacts/pointer-parent/Dockerfile
docker build -f artifacts/pointer-parent/Dockerfile --target pointer-parent \
  -t mt5:pointer-parent .
for distro in fedora-44 debian-13 ubuntu-26.04 archlinux; do
  uv run --locked python tests/mt5_input.py \
    --image "ghcr.io/skynet-core/mt5-docker:$distro" --parent-image mt5:pointer-parent \
    --case "$distro" --output artifacts/pointer-matrix \
    --modes nested --pointer-scales 1 1.25 2
done
```

Results, coordinate comparisons, cursor captures and per-case logs are saved under `artifacts/pointer-matrix/<distribution>/scale-<percent>/<backend>/nested/on-<transport>/`. CI requires both the eight GUI profiles and all twelve scaled profiles before publishing each image. The native tests also exercise the Wayland enter-and-click sequence over a real private protocol connection, including the failing behavior without the compatibility fix.

On hosts without a GPU, older KWin versions use QPainter, which cannot provide the ScreenShot2 API. Wayland headless sessions then render through a private Xvfb display; the screenshot captures that actual KWin output, with VNC and web services still disabled. Capture metadata records this backing display, and validation checks its private session configuration. Failed runs print the affected profiles and errors in the CI log while retaining the complete evidence; successful cases keep their individual pass results.

KWin 6.8 requires DRI3 for direct nesting on X11. In software mode, its Wayland session instead renders through a private, authenticated RFB connection to an X11 viewer. KWin still runs the kiosk application; the viewer displays its output and forwards input, while the existing clipboard relay handles text and images. The RFB listener binds only to container loopback. Builds retain only the viewer executable and missing shared libraries, leaving the VNC server packages in the builder.

`--gpu` requires NVIDIA Container Toolkit and records both the EGL probe and KWin's actual renderer. Overlay cases also record the compositor's loaded Qt libraries and reject mixed distro/overlay Qt paths. KWin 6.8's software Wayland backend needs the host's `/dev/udmabuf` device; load the host module with `sudo modprobe udmabuf` if necessary. The privileged launcher exposes that device to the container.

## Per-file source coverage

<!-- coverage-report:start -->
[Full HTML coverage report](https://skynet-core.github.io/mt5-docker/) · [tested commit `222c6ed645da`](https://github.com/skynet-core/mt5-docker/commit/222c6ed645da920b9a6ccbd2357d2da37294118b) · [CI run](https://github.com/skynet-core/mt5-docker/actions/runs/40967817657)

Required: **94% for every executable source file**. Branch coverage is available in the detailed reports.

| Language | Files | Minimum line coverage | Required | Passed | Audited N/A |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python | 65 | 95.24% | 94% | 65/65 | 0 |
| Bash | 33 | 94.74% | 94% | 33/33 | 0 |
| C++ | 22 | 95.21% | 94% | 22/22 | 1 |

<details>
<summary>All 120 source files</summary>

| Source | Language | Covered / executable | Line coverage | Missing lines or audit |
| --- | --- | ---: | ---: | --- |
| [bin/mt5-healthcheck](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/bin/mt5-healthcheck) | Python | 32/32 | 100.00% |  |
| [bin/mt5-launch](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/bin/mt5-launch) | Bash | 6/6 | 100.00% |  |
| [bin/mt5-run](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/bin/mt5-run) | Bash | 14/14 | 100.00% |  |
| [build/arch-kwin-x11](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/arch-kwin-x11) | Bash | 15/15 | 100.00% |  |
| [build/ci-broker-env](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/ci-broker-env) | Bash | 13/13 | 100.00% |  |
| [build/collect-artifacts](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/collect-artifacts) | Bash | 18/19 | 94.74% | [26](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/collect-artifacts#L26) |
| [build/coverage\_report.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/coverage_report.py) | Python | 123/123 | 100.00% |  |
| [build/create-user](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/create-user) | Bash | 18/18 | 100.00% |  |
| [build/install-mt5](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/install-mt5) | Bash | 74/74 | 100.00% |  |
| [build/install-packages](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/install-packages) | Bash | 27/27 | 100.00% |  |
| [build/install-winehq](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/install-winehq) | Bash | 12/12 | 100.00% |  |
| [build/kwin-6.8.2](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-6.8.2) | Bash | 30/30 | 100.00% |  |
| [build/kwin-phases/collect.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/collect.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/frameworks.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/frameworks.sh) | Bash | 5/5 | 100.00% |  |
| [build/kwin-phases/kwin.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/kwin.sh) | Bash | 7/7 | 100.00% |  |
| [build/kwin-phases/platform.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/platform.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/qtbase.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/qtbase.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtdeclarative.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/qtdeclarative.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtmodules.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/kwin-phases/qtmodules.sh) | Bash | 3/3 | 100.00% |  |
| [build/lint.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/lint.py) | Python | 49/49 | 100.00% |  |
| [build/normalize-runtime](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/normalize-runtime) | Bash | 6/6 | 100.00% |  |
| [build/publish\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/publish_coverage.py) | Python | 173/173 | 100.00% |  |
| [build/render-dockerfiles.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/build/render-dockerfiles.py) | Python | 40/40 | 100.00% |  |
| [init.d/modules/450-mt5.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/init.d/modules/450-mt5.sh) | Bash | 2/2 | 100.00% |  |
| [libexec/host-env](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/libexec/host-env) | Python | 15/15 | 100.00% |  |
| [libexec/mt5-configure](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/libexec/mt5-configure) | Python | 32/32 | 100.00% |  |
| [libexec/mt5-mcp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/libexec/mt5-mcp) | Python | 6/6 | 100.00% |  |
| [tests/capture-vnc.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/capture-vnc.py) | Python | 25/25 | 100.00% |  |
| [tests/ci-smoke.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/ci-smoke.py) | Python | 72/72 | 100.00% |  |
| [tests/clipboard.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/clipboard.py) | Python | 125/128 | 97.66% | [258](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/clipboard.py#L258), [259](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/clipboard.py#L259), [260](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/clipboard.py#L260) |
| [tests/close-terminal.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/close-terminal.py) | Python | 31/31 | 100.00% |  |
| [tests/display-matrix.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display-matrix.sh) | Bash | 52/52 | 100.00% |  |
| [tests/display\_matrix.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py) | Python | 152/159 | 95.60% | [86](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L86), [173](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L173), [175](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L175), [180](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L180), [181](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L181), [217](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L217), [284](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/display_matrix.py#L284) |
| [tests/isolated-entrypoint.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/isolated-entrypoint.sh) | Bash | 13/13 | 100.00% |  |
| [tests/kwin\_driver.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/kwin_driver.py) | Python | 112/112 | 100.00% |  |
| [tests/matrix.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/matrix.sh) | Bash | 66/66 | 100.00% |  |
| [tests/mt5\_input.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/mt5_input.py) | Python | 469/469 | 100.00% |  |
| [tests/mt5\_ui\_probe.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/mt5_ui_probe.py) | Python | 94/94 | 100.00% |  |
| [tests/native/benchmark-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/benchmark-unit.cpp) | C++ | 26/26 | 100.00% |  |
| [tests/native/clipboard-protocol-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-protocol-unit.cpp) | C++ | 140/143 | 97.90% | [84](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-protocol-unit.cpp#L84), [85](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-protocol-unit.cpp#L85), [161](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-protocol-unit.cpp#L161) |
| [tests/native/clipboard-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp) | C++ | 134/140 | 95.71% | [37](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L37), [76](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L76), [107](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L107), [157](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L157), [159](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L159), [168](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/clipboard-unit.cpp#L168) |
| [tests/native/compat-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/compat-unit.cpp) | C++ | 59/60 | 98.33% | [24](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/compat-unit.cpp#L24) |
| [tests/native/fake-wayland.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp) | C++ | 280/287 | 97.56% | [51](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L51), [52](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L52), [74](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L74), [169](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L169), [217](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L217), [235](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L235), [366](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.cpp#L366) |
| [tests/native/fake-wayland.h](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-wayland.h) | C++ | 0/0 | N/A | Declarations only; GCC emits no executable lines; input-unit tests the member defaults |
| [tests/native/fake-xtest.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fake-xtest.cpp) | C++ | 9/9 | 100.00% |  |
| [tests/native/fixture-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp) | C++ | 139/146 | 95.21% | [60](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L60), [61](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L61), [76](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L76), [77](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L77), [96](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L96), [104](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L104), [122](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/fixture-unit.cpp#L122) |
| [tests/native/input-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/input-unit.cpp) | C++ | 139/142 | 97.89% | [22](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/input-unit.cpp#L22), [47](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/input-unit.cpp#L47), [75](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/input-unit.cpp#L75) |
| [tests/native/pointer-enter-integration.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-integration.cpp) | C++ | 163/166 | 98.19% | [121](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-integration.cpp#L121), [178](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-integration.cpp#L178), [223](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-integration.cpp#L223) |
| [tests/native/pointer-enter-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-unit.cpp) | C++ | 176/179 | 98.32% | [25](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-unit.cpp#L25), [164](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-unit.cpp#L164), [168](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/pointer-enter-unit.cpp#L168) |
| [tests/native/run\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/run_coverage.py) | Python | 24/24 | 100.00% |  |
| [tests/native/xtest-unit.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/xtest-unit.cpp) | C++ | 20/21 | 95.24% | [11](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/native/xtest-unit.cpp#L11) |
| [tests/rfb.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/rfb.py) | Python | 113/113 | 100.00% |  |
| [tests/runtime.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/runtime.py) | Python | 317/317 | 100.00% |  |
| [tests/shell\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/shell_coverage.py) | Python | 93/93 | 100.00% |  |
| [tests/shell\_trace.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/shell_trace.sh) | Bash | 19/19 | 100.00% |  |
| [tests/test\_ci\_broker\_env.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_ci_broker_env.py) | Python | 32/33 | 96.97% | [59](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_ci_broker_env.py#L59) |
| [tests/test\_clipboard\_validation.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_clipboard_validation.py) | Python | 65/66 | 98.48% | [134](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_clipboard_validation.py#L134) |
| [tests/test\_collect\_artifacts.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_collect_artifacts.py) | Python | 26/27 | 96.30% | [51](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_collect_artifacts.py#L51) |
| [tests/test\_coverage\_report.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_coverage_report.py) | Python | 223/224 | 99.55% | [451](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_coverage_report.py#L451) |
| [tests/test\_display\_matrix.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_display_matrix.py) | Python | 184/185 | 99.46% | [317](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_display_matrix.py#L317) |
| [tests/test\_display\_runtime.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_display_runtime.py) | Python | 123/124 | 99.19% | [306](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_display_runtime.py#L306) |
| [tests/test\_host\_env.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_host_env.py) | Python | 20/21 | 95.24% | [31](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_host_env.py#L31) |
| [tests/test\_host\_gpu.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_host_gpu.py) | Python | 83/84 | 98.81% | [227](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_host_gpu.py#L227) |
| [tests/test\_install\_mt5.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_install_mt5.py) | Python | 58/59 | 98.31% | [192](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_install_mt5.py#L192) |
| [tests/test\_kwin\_driver.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_kwin_driver.py) | Python | 337/339 | 99.41% | [97](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_kwin_driver.py#L97), [618](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_kwin_driver.py#L618) |
| [tests/test\_lint.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_lint.py) | Python | 39/40 | 97.50% | [86](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_lint.py#L86) |
| [tests/test\_matrix.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_matrix.py) | Python | 27/28 | 96.43% | [84](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_matrix.py#L84) |
| [tests/test\_mt5\_configure.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_mt5_configure.py) | Python | 70/71 | 98.59% | [105](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_mt5_configure.py#L105) |
| [tests/test\_mt5\_input.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_mt5_input.py) | Python | 608/609 | 99.84% | [1310](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_mt5_input.py#L1310) |
| [tests/test\_native\_coverage\_runner.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_native_coverage_runner.py) | Python | 84/85 | 98.82% | [154](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_native_coverage_runner.py#L154) |
| [tests/test\_pointer\_mapping.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_pointer_mapping.py) | Python | 489/490 | 99.80% | [47](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_pointer_mapping.py#L47) |
| [tests/test\_publish\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_publish_coverage.py) | Python | 192/193 | 99.48% | [455](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_publish_coverage.py#L455) |
| [tests/test\_python\_coverage\_cli.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_cli.py) | Python | 139/140 | 99.29% | [242](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_cli.py#L242) |
| [tests/test\_python\_coverage\_clipboard.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_clipboard.py) | Python | 139/141 | 98.58% | [182](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_clipboard.py#L182), [287](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_clipboard.py#L287) |
| [tests/test\_python\_coverage\_graphics.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_graphics.py) | Python | 145/146 | 99.32% | [385](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_graphics.py#L385) |
| [tests/test\_python\_coverage\_health.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_health.py) | Python | 103/104 | 99.04% | [223](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_health.py#L223) |
| [tests/test\_python\_coverage\_matrix.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_matrix.py) | Python | 238/239 | 99.58% | [606](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_matrix.py#L606) |
| [tests/test\_python\_coverage\_rfb.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_rfb.py) | Python | 116/117 | 99.15% | [206](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_rfb.py#L206) |
| [tests/test\_python\_coverage\_runners.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_runners.py) | Python | 159/160 | 99.38% | [304](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_runners.py#L304) |
| [tests/test\_python\_coverage\_runtime.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_runtime.py) | Python | 217/219 | 99.09% | [265](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_runtime.py#L265), [491](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_runtime.py#L491) |
| [tests/test\_python\_coverage\_validate.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_validate.py) | Python | 261/263 | 99.24% | [83](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_validate.py#L83), [538](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_validate.py#L538) |
| [tests/test\_python\_coverage\_windows.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_windows.py) | Python | 87/88 | 98.86% | [184](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_python_coverage_windows.py#L184) |
| [tests/test\_runtime.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_runtime.py) | Python | 85/86 | 98.84% | [208](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_runtime.py#L208) |
| [tests/test\_runtime\_requirements.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_runtime_requirements.py) | Python | 20/21 | 95.24% | [48](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_runtime_requirements.py#L48) |
| [tests/test\_shell\_coverage\_build.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_build.py) | Python | 128/129 | 99.22% | [375](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_build.py#L375) |
| [tests/test\_shell\_coverage\_init.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_init.py) | Python | 155/156 | 99.36% | [511](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_init.py#L511) |
| [tests/test\_shell\_coverage\_launcher.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_launcher.py) | Python | 71/72 | 98.61% | [190](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_launcher.py#L190) |
| [tests/test\_shell\_coverage\_matrix.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_matrix.py) | Python | 65/66 | 98.48% | [188](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_matrix.py#L188) |
| [tests/test\_shell\_coverage\_report.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_report.py) | Python | 100/101 | 99.01% | [176](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_report.py#L176) |
| [tests/test\_shell\_coverage\_runtime.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_runtime.py) | Python | 277/278 | 99.64% | [682](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_runtime.py#L682) |
| [tests/test\_shell\_coverage\_toolchains.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_toolchains.py) | Python | 62/63 | 98.41% | [216](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_toolchains.py#L216) |
| [tests/test\_shell\_coverage\_trace.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_trace.py) | Python | 73/74 | 98.65% | [136](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_shell_coverage_trace.py#L136) |
| [tests/test\_validate.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_validate.py) | Python | 62/63 | 98.41% | [145](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/test_validate.py#L145) |
| [tests/validate.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/validate.py) | Python | 290/290 | 100.00% |  |
| [tests/weston.py](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/tests/weston.py) | Python | 58/58 | 100.00% |  |
| [vendor/kde-container/bin/plasma-healthcheck](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/bin/plasma-healthcheck) | Python | 31/31 | 100.00% |  |
| [vendor/kde-container/bin/plasma6-run](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/bin/plasma6-run) | Bash | 77/77 | 100.00% |  |
| [vendor/kde-container/compat/egl-fence-wait.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/compat/egl-fence-wait.cpp) | C++ | 35/35 | 100.00% |  |
| [vendor/kde-container/compat/wayland-pointer-enter.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/compat/wayland-pointer-enter.cpp) | C++ | 100/100 | 100.00% |  |
| [vendor/kde-container/compat/xtest-paced.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/compat/xtest-paced.cpp) | C++ | 6/6 | 100.00% |  |
| [vendor/kde-container/init.d/modules/300-plasma-user.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/init.d/modules/300-plasma-user.sh) | Bash | 38/38 | 100.00% |  |
| [vendor/kde-container/init.d/modules/400-plasma.sh](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/init.d/modules/400-plasma.sh) | Bash | 180/181 | 99.45% | [162](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/init.d/modules/400-plasma.sh#L162) |
| [vendor/kde-container/libexec/plasma-backend/kwin\_wayland](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-backend/kwin_wayland) | Bash | 34/34 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-gpu](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-gpu) | Python | 104/104 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-host-display](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-host-display) | Bash | 9/9 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-nested-x11](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-nested-x11) | Bash | 8/8 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-remote-output](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-remote-output) | Python | 50/50 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-session](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-session) | Bash | 39/39 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-software-display](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-software-display) | Bash | 2/2 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-target-app](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-target-app) | Python | 113/115 | 98.26% | [179](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-target-app#L179), [180](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-target-app#L180) |
| [vendor/kde-container/libexec/plasma-vnc](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-vnc) | Bash | 13/13 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-wait](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-wait) | Python | 40/40 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-xserver](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/libexec/plasma-xserver) | Bash | 9/9 | 100.00% |  |
| [vendor/kde-container/native/clipboard.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp) | C++ | 404/418 | 96.65% | [74](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L74), [94](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L94), [113](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L113), [114](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L114), [118](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L118), [232](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L232), [235](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L235), [243](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L243), [245](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L245), [423](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L423), [455](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L455), [456](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L456), [559](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L559), [607](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/clipboard.cpp#L607) |
| [vendor/kde-container/native/framebuffer-geometry.h](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/framebuffer-geometry.h) | C++ | 5/5 | 100.00% |  |
| [vendor/kde-container/native/kwin-input.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/kwin-input.cpp) | C++ | 141/145 | 97.24% | [61](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/kwin-input.cpp#L61), [72](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/kwin-input.cpp#L72), [235](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/kwin-input.cpp#L235), [262](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/kwin-input.cpp#L262) |
| [vendor/kde-container/native/ui-fixture.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/ui-fixture.cpp) | C++ | 171/175 | 97.71% | [50](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/ui-fixture.cpp#L50), [51](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/ui-fixture.cpp#L51), [65](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/ui-fixture.cpp#L65), [98](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/ui-fixture.cpp#L98) |
| [vendor/kde-container/native/vnc-benchmark.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/native/vnc-benchmark.cpp) | C++ | 46/46 | 100.00% |  |
| [vendor/kde-container/test/egl-fence-stub.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/test/egl-fence-stub.cpp) | C++ | 17/17 | 100.00% |  |
| [vendor/kde-container/test/egl-fence-test.cpp](https://github.com/skynet-core/mt5-docker/blob/222c6ed645da920b9a6ccbd2357d2da37294118b/vendor/kde-container/test/egl-fence-test.cpp) | C++ | 21/21 | 100.00% |  |

</details>
<!-- coverage-report:end -->

CI requires **at least 94% executable-line coverage for each maintained Python, C++ and Bash source file**, including test code, helpers, extensionless scripts and maintained code under `vendor/kde-container`. An uncovered file fails even if the repository average is high. Python and C++ branch coverage is reported separately. Fetched dependencies and generated compiler output are outside the maintained-source inventory.

Python uses `coverage.py`, native tests compile the actual C++ with GCC coverage, and Bash uses `shfmt` command locations plus a metadata-only DEBUG trace. The shell trace records source paths and line numbers; it does not record commands, environment values or credentials. Source hashes reject coverage from changed revisions. The declaration-only `tests/native/fake-wayland.h` is listed as N/A with its compiler audit and an exact source hash; edits require re-audit and cannot silently inherit that classification.

```sh
go install mvdan.cc/sh/v3/cmd/shfmt@v3.14.1
export PATH="$(go env GOPATH)/bin:$PATH"
mkdir -p artifacts/coverage/python artifacts/coverage/shell
export COVERAGE_FILE="$PWD/artifacts/coverage/python/.coverage"
export MT5_SHELL_COVERAGE_DIR="$PWD/artifacts/coverage/shell"
export MT5_SHELL_COVERAGE_MAP="$MT5_SHELL_COVERAGE_DIR/maps.jsonl"
uv run --locked python build/coverage_report.py --record-sources artifacts/coverage/sources.json
BASH_ENV="$PWD/tests/shell_trace.sh" uv run --locked coverage run --parallel-mode -m unittest discover -s tests -v
uv run --locked coverage combine --keep
```

Build and run the separate [native coverage image](tests/native/README.md), then enforce the combined gate:

```sh
uv run --locked python build/coverage_report.py \
  --source-manifest artifacts/coverage/sources.json \
  --python-data "$COVERAGE_FILE" \
  --shell-traces "$MT5_SHELL_COVERAGE_DIR" --shell-map "$MT5_SHELL_COVERAGE_MAP" \
  --native-report artifacts/coverage/native/native.json \
  --output artifacts/coverage/report
```

The per-file table is `artifacts/coverage/report/INDEX.md`; raw profiles, JSON, XML and HTML reports are retained by CI. Native fault tests use controlled dependency boundaries; the production-image MT5 input and broker suites provide the compositor, browser, login and backtest checks.

## Recorded acceptance

The 2026-10-28 run passed all 32 MT5 display combinations and six additional NVIDIA remote cases. Debian and Ubuntu used KWin 6.8.2 in both GPU VNC and web cases. Each case verified login, package MCP queries, expert compilation, a fresh backtest report, and chart/application capture. Nested clipboard checks passed with both host transports and Weston 15.0.1.

The local evidence index is `artifacts/VALIDATION.md`, with exact image IDs, per-case logs, screenshots and reports. Artifacts and account files are excluded from Git and image builds. Native MT5 MCP API-key authentication was not checked; the validated package endpoint is port 8000. Optional forecasting dependencies are omitted from the images.

## CI and publication

The [GitHub Actions pipeline](.github/workflows/build-and-publish.yml) builds and tests all four images on standard free Ubuntu runners. Default-branch pushes and release tags publish the exact tested images to GHCR after broker acceptance passes. Configure the three broker test secrets before the first publishing run; [CI setup](.github/README.md) covers triggers, tags, coverage, resource limits and retained artifacts.

Add `--capture-startup` to reproduce the KRFB startup regression: the test pauses PipeWire, authenticates VNC and requests initialized pixels before the first captured frame, then resumes capture and requires the real test window. Screenshots from both phases are retained.

The earlier compositor baseline is indexed in [artifacts/runtime/INDEX.md](artifacts/runtime/INDEX.md): 40 software profiles and 20 NVIDIA Wayland profiles, with exact tested versions, service logs, renderer details and framebuffer screenshots retained per case. KWin 6.8.2 acceptance runs have separate evidence directories. These checks cover the minimal compositor runtime; MT5 login and expert tests have their own evidence.
