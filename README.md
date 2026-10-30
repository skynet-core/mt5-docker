# MetaTrader 5 on KWin and systemd

Four amd64 images extend the corresponding [`skynet-core/systemd`](https://github.com/skynet-core/systemd/tree/1a4a0ca746a21cfff521bb1906cfe6e66ae9f578) bases:

| Dockerfile | Base image | Default local Wayland compositor |
| --- | --- | --- |
| `Dockerfile.fedora-44` | `ghcr.io/skynet-core/systemd:fedora-44` | Distribution KWin |
| `Dockerfile.debian-13` | `ghcr.io/skynet-core/systemd:debian-13` | KWin 6.8.2 |
| `Dockerfile.ubuntu-26.04` | `ghcr.io/skynet-core/systemd:ubuntu-26.04` | KWin 6.8.2 |
| `Dockerfile.archlinux` | `ghcr.io/skynet-core/systemd:archlinux` | Distribution KWin |

The images install Wine, Microsoft's Evergreen WebView2 runtime, the official MT5 terminal/MetaEditor/Strategy Tester, and Windows Python for `mt5-mcp`. MT5 fills the display under KWin as the unprivileged `plasma` user. Wine uses X11, through Xwayland in Wayland sessions. Systemd remains PID 1. Plasma desktop applications and the desktop shell are omitted.

The display runtime is vendored from [`skynet-core/kde-container` at `782714f`](https://github.com/skynet-core/kde-container/tree/782714fb841737b884ea263ada5108ae3754be85). Its native clipboard bridge, patched KRFB capture/input, GPU probe, software fallback, and scoped NVIDIA synchronization fix are retained. See [the source record](vendor/kde-container/UPSTREAM) for local changes.

See [Resolved challenges and root causes](#resolved-challenges-and-root-causes) for the installation, rendering, input and CI problems addressed during development.

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

The startup regression repeats these profiles with `--startup autologin`. It uses a dummy login on an internal network, so the terminal skips the company-selection dialog without reaching a broker. That dialog previously refreshed Wine's window geometry and hid an offset in the first chart click. A test expert draws flat colored targets; the test locates them in screenshots and checks their actual MT5 chart-local click coordinates and object names. Compilation runs in a separate disposable container before the tested session starts. No menu, dialog or MetaEditor window may repair the tested session before its first chart click.

```sh
uv run --locked python tests/mt5_input.py \
  --image ghcr.io/skynet-core/mt5-docker:archlinux --case archlinux \
  --output artifacts/mt5-autologin-input --startup autologin
```

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

CI also repeats all twelve scaled profiles with `--startup autologin`, retaining their evidence under `artifacts/autologin-pointer-matrix/`. The two startup variants add twenty profiles per image. Their expert, compiler output, input CSV and screenshots remain test artifacts and add no dependencies to the published runtime.

On hosts without a GPU, older KWin versions use QPainter, which cannot provide the ScreenShot2 API. Wayland headless sessions then render through a private Xvfb display; the screenshot captures that actual KWin output, with VNC and web services still disabled. Capture metadata records this backing display, and validation checks its private session configuration. Failed runs print the affected profiles and errors in the CI log while retaining the complete evidence; successful cases keep their individual pass results.

KWin 6.8 requires DRI3 for direct nesting on X11. In software mode, its Wayland session instead renders through a private, authenticated RFB connection to an X11 viewer. KWin still runs the kiosk application; the viewer displays its output and forwards input, while the existing clipboard relay handles text and images. The RFB listener binds only to container loopback. Builds retain only the viewer executable and missing shared libraries, leaving the VNC server packages in the builder.

`--gpu` requires NVIDIA Container Toolkit and records both the EGL probe and KWin's actual renderer. Overlay cases also record the compositor's loaded Qt libraries and reject mixed distro/overlay Qt paths. KWin 6.8's software Wayland backend needs the host's `/dev/udmabuf` device; load the host module with `sudo modprobe udmabuf` if necessary. The privileged launcher exposes that device to the container.

## Per-file source coverage

Coverage reports include application, container runtime and build code. Tests,
test helpers and native test fixtures/benchmarks are excluded from coverage
counts and detailed pages. They continue to run and are checked by the linters.
CI retains hashes of all source inputs, including tests, to detect stale results.

<!-- coverage-report:start -->
[Full HTML coverage report](https://skynet-core.github.io/mt5-docker/) · [tested commit `3ce53566306c`](https://github.com/skynet-core/mt5-docker/commit/3ce53566306c50cf7bc26b8db793a5dc0bb568f8) · [CI run](https://github.com/skynet-core/mt5-docker/actions/runs/41002120479)

Required: **94% for every executable source file**. Branch coverage is available in the detailed reports.

| Language | Files | Minimum line coverage | Required | Passed | Audited N/A |
| --- | ---: | ---: | ---: | ---: | ---: |
| Python | 13 | 98.26% | 94% | 13/13 | 0 |
| Bash | 29 | 94.74% | 94% | 29/29 | 0 |
| C++ | 6 | 96.65% | 94% | 6/6 | 0 |

<details>
<summary>All 48 source files</summary>

| Source | Language | Covered / executable | Line coverage | Missing lines or audit |
| --- | --- | ---: | ---: | --- |
| [bin/mt5-healthcheck](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/bin/mt5-healthcheck) | Python | 32/32 | 100.00% |  |
| [bin/mt5-launch](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/bin/mt5-launch) | Bash | 6/6 | 100.00% |  |
| [bin/mt5-run](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/bin/mt5-run) | Bash | 14/14 | 100.00% |  |
| [build/arch-kwin-x11](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/arch-kwin-x11) | Bash | 15/15 | 100.00% |  |
| [build/ci-broker-env](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/ci-broker-env) | Bash | 13/13 | 100.00% |  |
| [build/collect-artifacts](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/collect-artifacts) | Bash | 18/19 | 94.74% | [26](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/collect-artifacts#L26) |
| [build/coverage\_report.py](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/coverage_report.py) | Python | 123/123 | 100.00% |  |
| [build/create-user](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/create-user) | Bash | 18/18 | 100.00% |  |
| [build/install-mt5](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/install-mt5) | Bash | 74/74 | 100.00% |  |
| [build/install-packages](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/install-packages) | Bash | 27/27 | 100.00% |  |
| [build/install-winehq](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/install-winehq) | Bash | 12/12 | 100.00% |  |
| [build/kwin-6.8.2](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-6.8.2) | Bash | 30/30 | 100.00% |  |
| [build/kwin-phases/collect.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/collect.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/frameworks.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/frameworks.sh) | Bash | 5/5 | 100.00% |  |
| [build/kwin-phases/kwin.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/kwin.sh) | Bash | 7/7 | 100.00% |  |
| [build/kwin-phases/platform.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/platform.sh) | Bash | 15/15 | 100.00% |  |
| [build/kwin-phases/qtbase.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/qtbase.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtdeclarative.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/qtdeclarative.sh) | Bash | 3/3 | 100.00% |  |
| [build/kwin-phases/qtmodules.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/kwin-phases/qtmodules.sh) | Bash | 3/3 | 100.00% |  |
| [build/lint.py](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/lint.py) | Python | 49/49 | 100.00% |  |
| [build/normalize-runtime](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/normalize-runtime) | Bash | 6/6 | 100.00% |  |
| [build/publish\_coverage.py](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/publish_coverage.py) | Python | 173/173 | 100.00% |  |
| [build/render-dockerfiles.py](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/build/render-dockerfiles.py) | Python | 40/40 | 100.00% |  |
| [init.d/modules/450-mt5.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/init.d/modules/450-mt5.sh) | Bash | 2/2 | 100.00% |  |
| [libexec/host-env](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/libexec/host-env) | Python | 15/15 | 100.00% |  |
| [libexec/mt5-configure](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/libexec/mt5-configure) | Python | 32/32 | 100.00% |  |
| [libexec/mt5-mcp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/libexec/mt5-mcp) | Python | 6/6 | 100.00% |  |
| [vendor/kde-container/bin/plasma-healthcheck](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/bin/plasma-healthcheck) | Python | 31/31 | 100.00% |  |
| [vendor/kde-container/bin/plasma6-run](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/bin/plasma6-run) | Bash | 77/77 | 100.00% |  |
| [vendor/kde-container/compat/egl-fence-wait.cpp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/compat/egl-fence-wait.cpp) | C++ | 35/35 | 100.00% |  |
| [vendor/kde-container/compat/wayland-pointer-enter.cpp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/compat/wayland-pointer-enter.cpp) | C++ | 100/100 | 100.00% |  |
| [vendor/kde-container/compat/xtest-paced.cpp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/compat/xtest-paced.cpp) | C++ | 6/6 | 100.00% |  |
| [vendor/kde-container/init.d/modules/300-plasma-user.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/init.d/modules/300-plasma-user.sh) | Bash | 38/38 | 100.00% |  |
| [vendor/kde-container/init.d/modules/400-plasma.sh](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/init.d/modules/400-plasma.sh) | Bash | 180/181 | 99.45% | [162](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/init.d/modules/400-plasma.sh#L162) |
| [vendor/kde-container/libexec/plasma-backend/kwin\_wayland](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-backend/kwin_wayland) | Bash | 34/34 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-gpu](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-gpu) | Python | 104/104 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-host-display](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-host-display) | Bash | 9/9 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-nested-x11](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-nested-x11) | Bash | 8/8 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-remote-output](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-remote-output) | Python | 50/50 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-session](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-session) | Bash | 39/39 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-software-display](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-software-display) | Bash | 2/2 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-target-app](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-target-app) | Python | 113/115 | 98.26% | [179](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-target-app#L179), [180](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-target-app#L180) |
| [vendor/kde-container/libexec/plasma-vnc](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-vnc) | Bash | 13/13 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-wait](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-wait) | Python | 40/40 | 100.00% |  |
| [vendor/kde-container/libexec/plasma-xserver](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/libexec/plasma-xserver) | Bash | 9/9 | 100.00% |  |
| [vendor/kde-container/native/clipboard.cpp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp) | C++ | 404/418 | 96.65% | [74](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L74), [94](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L94), [113](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L113), [114](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L114), [118](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L118), [232](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L232), [235](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L235), [243](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L243), [245](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L245), [423](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L423), [455](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L455), [456](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L456), [559](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L559), [607](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/clipboard.cpp#L607) |
| [vendor/kde-container/native/framebuffer-geometry.h](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/framebuffer-geometry.h) | C++ | 5/5 | 100.00% |  |
| [vendor/kde-container/native/kwin-input.cpp](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/kwin-input.cpp) | C++ | 141/145 | 97.24% | [61](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/kwin-input.cpp#L61), [72](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/kwin-input.cpp#L72), [235](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/kwin-input.cpp#L235), [262](https://github.com/skynet-core/mt5-docker/blob/3ce53566306c50cf7bc26b8db793a5dc0bb568f8/vendor/kde-container/native/kwin-input.cpp#L262) |

</details><!-- coverage-report:end -->

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

The [2026-10-30 acceptance run](https://github.com/skynet-core/mt5-docker/actions/runs/40967817657) tested source commit `222c6ed` and passed all four image builds, tests and GHCR publications on standard GitHub runners. Each image passed 38 profiles: ten Qt display cases, eight real MT5 input cases, twelve scaled nested cases and eight broker/MCP/expert-backtest cases, for 152 hosted profiles. Mutable and immutable image tags were independently matched to the exact tested OCI image configurations.

Supplemental isolated runs passed 252 actual MT5 GUI profiles and 2,040 physical cursor measurements, including AMD/NVIDIA Wayland rendering and a controlled old-versus-fixed startup-order regression. Local evidence is indexed in `artifacts/ci/40967817657/INDEX.md`, with detailed suites in `artifacts/mt5-input-final/`, `artifacts/pointer-matrix-final/`, `artifacts/amd-input-final/` and `artifacts/startup-order-final/`. The earlier rendering runs retain their original source/image identities; the later startup fix has separate overlay and CI evidence. X11/Xvfb renderer results are recorded as measured and can be software despite GPU access.

The source gate passed 331 unit tests, 84 native tests and one optional private-X11 native capture skip. All 119 executable files exceeded 94% line coverage; the declaration-only header is explicitly N/A. The [coverage publication](https://github.com/skynet-core/mt5-docker/actions/runs/40986172562) deployed the [live report](https://skynet-core.github.io/mt5-docker/) and generated this README's coverage block. All 99 public files matched the audited report, and browser checks verified the home, Python and C++ reports.

The earlier 2026-10-28 run passed all 32 MT5 display combinations and six additional NVIDIA remote cases. Debian and Ubuntu used KWin 6.8.2 in both GPU VNC and web cases. Each case verified login, package MCP queries, expert compilation, a fresh backtest report, and chart/application capture. Nested clipboard checks passed with both host transports and Weston 15.0.1.

The local evidence index is `artifacts/VALIDATION.md`, with exact image IDs, per-case logs, screenshots and reports. Artifacts and account files are excluded from Git and image builds. Native MT5 MCP API-key authentication was not checked; the validated package endpoint is port 8000. Optional forecasting dependencies are omitted from the images.

## CI and publication

The [GitHub Actions pipeline](.github/workflows/build-and-publish.yml) builds and tests all four images on standard free Ubuntu runners. Default-branch pushes and release tags publish the exact tested images to GHCR after broker acceptance passes. Configure the three broker test secrets before the first publishing run; [CI setup](.github/README.md) covers triggers, tags, coverage, resource limits and retained artifacts.

Add `--capture-startup` to reproduce the KRFB startup regression: the test pauses PipeWire, authenticates VNC and requests initialized pixels before the first captured frame, then resumes capture and requires the real test window. Screenshots from both phases are retained.

The earlier compositor baseline is indexed in [artifacts/runtime/INDEX.md](artifacts/runtime/INDEX.md): 40 software profiles and 20 NVIDIA Wayland profiles, with exact tested versions, service logs, renderer details and framebuffer screenshots retained per case. KWin 6.8.2 acceptance runs have separate evidence directories. These checks cover the minimal compositor runtime; MT5 login and expert tests have their own evidence.

## Resolved challenges and root causes

This summary records confirmed causes and implemented corrections. Several symptoms had independent causes: a black window could come from driver access, monitor setup or early capture; a moving cursor did not prove that clicks reached the same coordinates. Runtime fixes and validation fixes are listed separately. The implementation details are recorded in [the vendored runtime changes](vendor/kde-container/UPSTREAM), [the KWin build notes](build/KWIN.md) and [CI documentation](.github/README.md).

### Installation, account configuration and MCP

| Challenge or symptom | Cause | Correction |
| --- | --- | --- |
| Current MT5 aborts on Debian/Ubuntu's distribution Wine | The recorded MT5 build 6292 did not start correctly under their Wine 10 packages. | Install pinned WineHQ 11.20 packages from the matching release repository and verify installed MT5 executables and real startup. This is a version-specific compatibility result, not a claim that every Wine 10 application is unsupported. |
| `wineserver` is missing from PATH on Ubuntu 26.04 | The package installs it below the multiarch library directory rather than at the expected executable path. | Discover the supported package locations, link the actual executable into `/usr/local/bin` and fail the installation check if it is unavailable. |
| WebView2 installation fails or leaves a build apparently complete | Its x64 bootstrapper is itself 32-bit. Cold Wine RPC/service startup and leftover updater processes also interfere with installers; status alone does not establish that the runtime exists. | Enable the required i386/WoW64 support, keep bootstrap services alive, stop stale Wine service graphs between installers, allow one clean WebView2 retry, and require an accepted status plus a nonempty installed payload. Verify terminal, editor and tester payloads too. See [the installer](build/install-mt5). |
| Linux dependency resolution or Dependabot reports no compatible MetaTrader5 wheel | The native SDK supplies Windows wheels. Linux-only requirements generation could also drop the dependencies needed inside Wine. | Keep the Windows platform marker in the input and compiled requirements, compile with `--universal`, and check universal/Linux/Windows resolution separately. Windows Python still installs and imports the real SDK; Linux development does not install its Windows extension. |
| Windows numerical dependencies fail to import on legacy Wine 10.0 | Its UCRT implementation lacks complex-math exports used by NumPy/SciPy wheels. | Retain a checksum-verified UCRT fallback scoped to `python.exe` for that legacy path, and run actual Windows import/numerical checks during the build. Current Debian/Ubuntu images use Wine 11.20 for MT5 compatibility. |
| An MCP listener exists but terminal queries fail or hit unexpected rate limits | In the validated `mt5-mcp==0.6.3`/SDK combination, IPC tools need the server thread. Its CLI registers worker-thread tools and initializes the ASGI application with the default rate limit before applying parsed CLI settings. | [The package launcher](libexec/mt5-mcp) keeps those tools on the server thread and explicitly configures rate limiting. Acceptance requires a real MCP handshake and connected-terminal query, not just an open port. |
| A fresh prefix cannot resolve `ICMarketsEU-Demo` by name | The generic MT5 installation lacks that broker's public server definitions. | Seed the public, checksum-recorded `servers.dat` exported from a credential-free broker installation. Keep account credentials separate; custom brokers can supply their address or public definitions. See [broker provenance](config/mt5/UPSTREAM). |
| Broker validation uses a placeholder or changes prefix ownership between modes | CI could receive the literal `MT5_SERVER` placeholder; inconsistent UID/GID settings could make a reused Wine volume inaccessible. | Validate account/server settings, resolve only that known placeholder to the confirmed broker, preserve custom servers, and use one prefix owner across the matrix. Fail missing credentials before expensive publishing builds. |
| Quoted passwords or account files behave differently between launch methods | Shell expansion, INI encoding, file ownership and existing volume contents differ from ordinary command-line strings. | Use dotenv parsing through `uv`, write a private UTF-16 MT5 INI, mount custom INIs read-only and pass credentials only at runtime. Separate image/account volumes; pulling an image does not replace applications in an existing prefix. |
| Package MCP and native application MCP endpoints are confused or collide | MT5 and MetaEditor own their internal loopback listeners; the package server is a different service. | Expose the package endpoint on port 8000 and use systemd socket proxies for the native application ports. Keep package validation separate from the optional native API-key check. See [MCP validation](#mcp-and-expert-validation). |

### Rendering, nested input and clipboard

| Challenge or symptom | Cause | Correction |
| --- | --- | --- |
| The visible cursor aligns, but the first chart click lands about a title bar away after automatic login | Forced KWin fullscreen left Wine's nonclient input geometry stale. In the controlled 96-DPI case, rendered chart controls and Win32 geometry differed by 4×30 pixels. Opening and dismissing the company-selection or Options dialog refreshed the geometry, masking this in previous input tests. | Use borderless maximization specifically for `mt5-launch`; MT5 still fills the kiosk display. Other kiosk apps retain their fullscreen policy. Regress with a dummy offline login and screenshot-located targets whose exact coordinates are reported by MT5 itself, before any dialog or menu interaction. |
| Pointer moves but the next click hits an old position, especially on entering a 200% nested window | KWin did not apply the coordinates carried by `wl_pointer.enter` before a subsequent click. | A scoped [Wayland compatibility library](vendor/kde-container/compat/wayland-pointer-enter.cpp) forwards enter followed by motion when the pointer is unlocked, preserving the original listener data and all twelve current callbacks. Real protocol and GUI tests cover immediate entry/click behavior. |
| A grabbed nested pointer jumps back when a click changes subsurface focus | During a pointer lock, the host's absolute position stays frozen while relative motion moves KWin's cursor. Synthesizing motion from a later enter event reset that relative position. | Track active locks per pointer and display; preserve the original enter callback and suppress only its added absolute motion while locked. Resume normal entry synchronization after unlock, and clear tracking on proxy destruction or display disconnect. Unit, real-protocol and isolated MT5 tests cover the sequence. |
| VNC/nested absolute input drifts with display scaling | Captured framebuffer pixels were treated as compositor logical coordinates. | KRFB publishes its actual capture dimensions; [the input plugin](vendor/kde-container/native/kwin-input.cpp) maps those pixels to logical output geometry, including its origin. Tests independently compare physical pointer positions before and after resize. |
| Compatibility fixes silently fail on AMD or software rendering | KWin executable file capabilities make the dynamic loader ignore `LD_PRELOAD`; the problem is not specific to NVIDIA. | Remove conflicting capabilities for Wayland-on-Wayland nesting regardless of GPU vendor. Keep NVIDIA synchronization conditional on actual NVIDIA use, and restore the application's preload environment after compositor startup. |
| Native Wayland nesting shows a black window on an NVIDIA host | DRI device access alone did not supply NVIDIA's container driver libraries needed for frame exchange with the host compositor. | Automatically request Docker NVIDIA driver injection when the host driver and runtime are available. Explicit GPU/runtime/CDI/software choices take precedence. |
| Nested NVIDIA frames are incomplete or rendering races appear | KWin can publish a raw buffer before the host can observe completion of GPU rendering. | Retain the compositor-scoped EGL fence synchronization fix. It does not force NVIDIA handling on AMD/software paths or inject private compositor libraries into applications. |
| Debian software nested mode produces a black frame and MT5 repeatedly exits 69 | Wayland-on-X11 software nesting creates a KRFB monitor, but Wine launched while output configuration removed another output. Wine received `BadRROutput` / `RRGetOutputInfo`. | [The kiosk supervisor](vendor/kde-container/libexec/plasma-target-app) now waits for the same bounded remote-monitor readiness used by VNC/web before launching software-nested applications. Commit `222c6ed` and controlled delayed-monitor tests cover the startup order. |
| Copy/paste fails across independent nested sessions or transfers strange X11 data | Display sockets do not bridge clipboard ownership. Portal transfer keys, X11 atoms and selection-negotiation targets belong to their original session/server. | Use the bidirectional Wayland/X11 relay, prefer `ext_data_control_v1`, fall back to `wlr-data-control`, and filter portal-local keys plus `TARGETS`, `MULTIPLE`, `TIMESTAMP` and `SAVE_TARGETS`. Weston fallback also mounts its Xwayland socket. |
| Older Debian GPU remote capture fails; upgrading KWin introduces library/protocol conflicts | KWin 6.3's NVIDIA capture path needs a fallback. KWin 6.8 requires newer Qt/KF and changes the output-device registry protocol. | Build KWin 6.8.2 with its matching stack under `/opt/kwin-6.8.2`; scope private paths to KWin and matching KScreen. Keep applications/KRFB/clipboard helpers on distro Qt. Retain software capture for the older distro variant. |
| X11 sessions disappear from current Arch packages | Its repositories no longer supply `kwin-x11`; KWin 6.8 is Wayland-only. | Build the separate pinned, namespaced KWin-X11 release and omit compilers from the final image. Debian/Ubuntu also retain their separate X11 compositor. |
| A minimal kiosk crashes during Qt theme initialization or cannot find compositor services | The built-in KDE theme expects optional integration packages; bypassing normal Plasma startup also skips service-cache preparation. | Use Qt's generic theme for kiosk clients and build the appropriately scoped KDE service cache without adding the full desktop shell. |
| A crashed compositor stays down despite restart settings | KWin's wrapper may return zero after exhausting its own crash retries. | Restart the session after successful exits too; an explicit systemd stop still stops it. |
| VNC crashes when a client requests pixels before the first captured frame | KRFB advertised dimensions before allocating its framebuffer; PipeWire had not delivered a frame yet. | Allocate and initialize the framebuffer before publishing it. A startup regression pauses capture and requests pixels before resuming the real window. |
| Headless screenshots or nested Wayland fail on GPU-less runners | Older QPainter sessions lack ScreenShot2; direct KWin 6.8 X11 nesting requires DRI3 unavailable on CPU-only hosts. | Capture the actual private Xvfb-backed output where required. For software nesting on X11, use an authenticated loopback RFB viewer while KWin remains the compositor. Preserve the clipboard relay and copy only needed viewer libraries into the runtime. |

Disabling `CLIPBOARD_SYNC` helped one reported Fedora comparison, but that alone did not establish the cause of its click failures. The confirmed clipboard and coordinate fixes above have separate regression evidence. Clipboard sharing also does not imply support for cross-session drag-and-drop.

### CI, test reliability and coverage publication

| Challenge or misleading result | Cause | Correction |
| --- | --- | --- |
| A passing image build or listening socket is mistaken for acceptance | Neither proves visible MT5, correct input, successful login or a completed expert test. | Require the original 38 profiles plus twenty startup-without-dialog profiles per image, actual screenshots and chart events, connected account/history checks, fresh expert compilation and Strategy Tester results. Upload per-case evidence and publish the exact tested image ID. |
| The first-chart web regression rejects a correct target click by two pixels | A beveled button's solid-color interior is asymmetric, so its screenshot bounds do not have the declared object's centre. | Draw flat rectangle targets with matching border/fill colors. Keep the original one-pixel coordinate tolerance and old-image negative control; do not calibrate input to an observed offset. |
| The cold-start guard reports a dialog while the chart remains usable | The read-only observer lists every visible same-process auxiliary window, including passive `tooltips_class32` windows. | Ignore only that tooltip class in the startup guard, retain the raw window evidence, and reject disabled main windows or every other auxiliary class before clicking. Unit cases cover tooltip-only, mixed modal, disabled and unknown-class states. |
| Debian's 125% X11-on-X11 test aborts although its input worked | `journalctl --grep` returns exit 1 with empty output before the asynchronous tagged KWin state reaches journald. | Commit `7627f49` polls only that exact empty no-match result within the existing deadline. Diagnostic/error exits remain fatal, and script cleanup is tested. This observer bug is separate from the production black-frame bug. |
| A screenshot connection unexpectedly moves the cursor to 0,0 | The test RFB client always sent a pointer release on disconnect, including read-only captures. | Release the pointer only when buttons are actually held; still release outstanding input. |
| A hidden MT5 popup is incorrectly reported as closed | One Escape hides the popup while leaving menu-bar capture active. | Send the second Escape and require the read-only `menu_mode` flag to be false before continuing. |
| A cursor screenshot appears correct despite an offset or unrelated repaint | A crop near the expected point can select unrelated differences; changing backgrounds can masquerade as a cursor. | Require identical complete OFF backgrounds and a plausible full-frame OFF/ON cursor difference. Check physical input independently. Only unstable captures may repeat; input is neither retried nor calibrated to the observed error. |
| Fractional-scale tests reject valid geometry or click a fixed viewport incorrectly | KScreen can omit `enabled`; X11 geometry is quantized; rootful X11 windows may lack server decorations; some viewers keep a fixed viewport. | Use priority as the enabled fallback, fresh acknowledged window geometry, one-device-pixel placement tolerance, explicit viewport anchoring and the appropriate decoration policy. |
| Chromium/noVNC startup fails when a Docker bridge changes | The specific `net::ERR_NETWORK_CHANGED` transport error cancels document/module requests. | Allow one narrowly classified reload and retain the original trace/failure. Mixed errors, repeated failures and ordinary GUI failures remain fatal. |
| Parallel shell tests corrupt recorded command arguments | Separate append operations interleave between child processes. | Serialize the complete test record with `flock`. |
| ShellCheck reports EXIT-trap cleanup as unreachable; some sources escape linting | Trap-only functions are called indirectly, and extension-based discovery misses extensionless/vendored scripts. | Apply narrow explained trap suppressions, fix actual findings and share maintained-source discovery across pinned Ruff, ShellCheck and Cppcheck checks. Consolidate project tests under `tests/`; retain the upstream vendored fixture layout. |
| Aggregate coverage hides untested application code or reports refer to older code | A repository average does not establish per-file coverage, and raw measurements can outlive the sources they measured. | Enforce 94% executable-line coverage per application, runtime and build source file; exclude tests and test fixtures from reports. Keep all tests running and linted, hash every source input before/after, and independently replay Python/native/Bash measurements. |
| Source builds consume hosted-runner budgets or inflate runtime images | New Qt/KF/KWin stacks need substantial compiler resources; installing the whole build environment or optional ML stack in production is unnecessary. | Use cached multi-stage builders, reclaim unused runner SDK space, limit parallel build vertices and retain a `distro` variant when the budget requires skipping compilation. Keep compilers, test fixtures and optional forecasting packages out of production. |
| Concurrent or failed CI runs publish unverified images | Rebuilding after tests changes the image being published; outdated runs can overlap newer commits. | Supersede stale branch runs, gate publication on all acceptance tests, retag the tested image instead of rebuilding, and verify both mutable and commit-specific registry tags against its configuration digest. |
| A public coverage site or README update becomes stale, exposes raw logs or triggers another build loop | Cross-workflow artifacts, concurrent branch edits and generated documentation commits need explicit boundaries. | Accept only successful trusted default-branch runs; check artifact digests and report schema; allowlist static assets. Deploy Pages, then update only README coverage markers with bounded non-force retries. Built-in-token commits use `[skip ci]`. |

The final [image workflow](https://github.com/skynet-core/mt5-docker/actions/runs/40967817657) and [coverage workflow](https://github.com/skynet-core/mt5-docker/actions/runs/40986172562) passed. Their retained artifacts contain profile-level logs, screenshots, measurements and report provenance. The local evidence paths and hardware scope are listed in [Recorded acceptance](#recorded-acceptance).
