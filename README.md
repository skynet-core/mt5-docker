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

KWin tests actual EGL/GBM rendering before selecting hardware acceleration. `bin/mt5-run -e LIBGL_ALWAYS_SOFTWARE=1` forces software rendering. For an NVIDIA host configured with NVIDIA Container Toolkit, pass `--gpus all` to `bin/mt5-run`. The nested NVIDIA fence fix is scoped to KWin; WebView2 uses software rendering. The Debian/Ubuntu source variant builds KWin 6.8.2 with a matching Qt/KF stack under `/opt/kwin-6.8.2`, enabling the normal GPU path in Wayland VNC/web modes. Debian's distro variant uses software capture for NVIDIA VNC/web with its older KWin. Native KRFB, clipboard helpers and kiosk applications retain their distro Qt libraries. Arch's removed X11 compositor is built separately from pinned KWin-X11 sources, with compilers excluded from the final image.

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

On hosts without a GPU, older KWin versions use QPainter, which cannot provide the ScreenShot2 API. Wayland headless sessions then render through a private Xvfb display; the screenshot captures that actual KWin output, with VNC and web services still disabled. Capture metadata records this backing display, and validation checks its private session configuration. Failed runs print the affected profiles and errors in the CI log while retaining the complete evidence; successful cases keep their individual pass results.

KWin 6.8 requires DRI3 for direct nesting on X11. In software mode, its Wayland session instead renders through a private, authenticated RFB connection to an X11 viewer. KWin still runs the kiosk application; the viewer displays its output and forwards input, while the existing clipboard relay handles text and images. The RFB listener binds only to container loopback. Builds retain only the viewer executable and missing shared libraries, leaving the VNC server packages in the builder.

`--gpu` requires NVIDIA Container Toolkit and records both the EGL probe and KWin's actual renderer. Overlay cases also record the compositor's loaded Qt libraries and reject mixed distro/overlay Qt paths. KWin 6.8's software Wayland backend needs the host's `/dev/udmabuf` device; load the host module with `sudo modprobe udmabuf` if necessary. The privileged launcher exposes that device to the container.

## Recorded acceptance

The 2026-10-28 run passed all 32 MT5 display combinations and six additional NVIDIA remote cases. Debian and Ubuntu used KWin 6.8.2 in both GPU VNC and web cases. Each case verified login, package MCP queries, expert compilation, a fresh backtest report, and chart/application capture. Nested clipboard checks passed with both host transports and Weston 15.0.1.

The local evidence index is `artifacts/VALIDATION.md`, with exact image IDs, per-case logs, screenshots and reports. Artifacts and account files are excluded from Git and image builds. Native MT5 MCP API-key authentication was not checked; the validated package endpoint is port 8000. Optional forecasting dependencies are omitted from the images.

## CI and publication

The [GitHub Actions pipeline](.github/workflows/build-and-publish.yml) builds and tests all four images on standard free Ubuntu runners. Default-branch pushes and release tags publish the exact tested images to GHCR after broker acceptance passes. Configure the three broker test secrets before the first publishing run; [CI setup](.github/README.md) covers triggers, tags, coverage, resource limits and retained artifacts.

Add `--capture-startup` to reproduce the KRFB startup regression: the test pauses PipeWire, authenticates VNC and requests initialized pixels before the first captured frame, then resumes capture and requires the real test window. Screenshots from both phases are retained.

The earlier compositor baseline is indexed in [artifacts/runtime/INDEX.md](artifacts/runtime/INDEX.md): 40 software profiles and 20 NVIDIA Wayland profiles, with exact tested versions, service logs, renderer details and framebuffer screenshots retained per case. KWin 6.8.2 acceptance runs have separate evidence directories. These checks cover the minimal compositor runtime; MT5 login and expert tests have their own evidence.
