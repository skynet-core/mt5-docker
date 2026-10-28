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

## Build and run

```sh
docker build -f Dockerfile.fedora-44 -t mt5:fedora-44 .
cp .env.example .env
chmod 600 .env
# Edit .env to set a real account, password and broker server.
bin/mt5-run
```

Use the matching Dockerfile and `IMAGE_TAG` for another distribution. The host launcher inherits the systemd base's privileged-container setup. It publishes services on localhost and creates the named `mt5-data` Wine volume. Use a separate volume for each image/account; a fresh Docker named volume is populated from the image's installed Wine prefix. An empty bind mount over the prefix does not contain MT5.

Debian and Ubuntu compile KWin 6.8.2 with Qt 6.11.2 and the required KDE Frameworks 6.30 libraries from pinned source archives. The first build takes longer; separate cached phases retain completed dependencies when later components change. See [build/KWIN.md](build/KWIN.md) for the versions and dependency choices. KWin 6.8 is Wayland-only; X11 modes retain their separate X11 compositor.

For a smaller build on a constrained runner, add `--build-arg KWIN_VARIANT=distro` to the Debian or Ubuntu build command. This skips the private Qt/KF/KWin compilation and uses the distribution's KWin packages. The GitHub Actions pipeline uses this variant on standard free runners; its published Debian/Ubuntu images use distribution KWin versions. When validating that variant with `tests/matrix.sh`, set `KWIN_VARIANT=distro` as well.

Debian and Ubuntu use pinned WineHQ 11.20 packages from their own release repositories, with a verified signing key and signed APT metadata. The current MT5 build 6292 aborts during startup under their distribution Wine 10 packages. Fedora and Arch use their distribution Wine packages.

Account settings are materialized at `C:\MT5\config.ini` as a private UTF-16 file. Passwords are passed at runtime, never as build arguments. The host launcher uses `uv` and dotenv parsing, including quoted passwords and literal dollar signs. `.env` is excluded from Git and the Docker build context. Live EA trading is disabled by default; Strategy Tester can still test the supplied expert.

The image includes the public server definitions needed to resolve the confirmed `ICMarketsEU-Demo` name on first boot. This cache contains no account data; its source and checksum are recorded in [config/mt5/UPSTREAM](config/mt5/UPSTREAM). For another broker, use its host:port address or seed its public `Config/servers.dat` in the Wine volume.

You can instead copy `config/mt5/config.ini` to a private file, add `Login`, `Password`, and `Server` to `[Common]`, and run `MT5_ENV_FILE=/dev/null MT5_CONFIG_FILE=/absolute/path/config.ini bin/mt5-run`. The file must be readable by the container's `plasma` UID. Environment account settings override the INI, including settings loaded from `.env`; `/dev/null` skips that file. `MT5_ENV_FILE` can also select an environment file outside this checkout. `MT5_LOGIN` is accepted as an alias for `MT5_ACCOUNT`.

## Displays

Choose `DISPLAY_BACKEND=wayland` or `x11` and one of these modes:

| Mode | Behavior |
| --- | --- |
| `headless` | Private KWin output; no host display required |
| `nested` | A window on the host's mounted Wayland or X11 display |
| `vnc` | VNC on localhost:5900 |
| `web` | noVNC at http://localhost:6080 |

```sh
DISPLAY_BACKEND=wayland DISPLAY_MODE=nested bin/mt5-run
DISPLAY_BACKEND=x11 DISPLAY_MODE=nested HOST_DISPLAY_BACKEND=x11 bin/mt5-run
DISPLAY_BACKEND=wayland DISPLAY_MODE=vnc VNC_PASSWORD=testpass bin/mt5-run
DISPLAY_BACKEND=wayland DISPLAY_MODE=web VNC_PASSWORD=testpass bin/mt5-run
```

The launcher discovers the host Wayland/X11 socket and Xauthority cookie. `HOST_DISPLAY_BACKEND=wayland|x11` selects the host transport separately from the container compositor. A Weston host renders through its Wayland socket; clipboard exchange also requires its Xwayland socket and host `DISPLAY`. `CLIPBOARD_SYNC=1` is the default for nested sessions and bridges Unicode text, images, MIME data and primary selections bidirectionally. VNC/noVNC use their remote clipboard support.

VNC passwords are limited to eight printable ASCII characters by the protocol. `VNC_PASSWORD_FILE` mounts a private password file. `VNC_PORT`, `WEB_PORT`, and `BIND_ADDRESS` control the host binding; the default address is `127.0.0.1`.

KWin tests actual EGL/GBM rendering before selecting hardware acceleration. `bin/mt5-run -e LIBGL_ALWAYS_SOFTWARE=1` forces software rendering. For an NVIDIA host configured with NVIDIA Container Toolkit, pass `--gpus all` to `bin/mt5-run`. The nested NVIDIA fence fix is scoped to KWin; WebView2 uses software rendering. The Debian/Ubuntu source variant builds KWin 6.8.2 with a matching Qt/KF stack under `/opt/kwin-6.8.2`, enabling the normal GPU path in Wayland VNC/web modes. Debian's distro variant uses software capture for NVIDIA VNC/web with its older KWin. Native KRFB, clipboard helpers and kiosk applications retain their distro Qt libraries. Arch's removed X11 compositor is built separately from pinned KWin-X11 sources, with compilers excluded from the final image.

## MCP and expert validation

[`mt5-mcp==0.6.3`](https://github.com/Cloudmeru/MetaTrader-5-MCP-Server/tree/b4ded2c9806e0ce290ff7e2af6004913e71bf67e) runs in Windows Python inside the same Wine prefix. Its read-only MCP endpoint is `http://127.0.0.1:8000/mcp`. It queries the actual MT5 terminal through the MetaTrader5 IPC package. The health check requires a successful MCP handshake, a connected terminal query, and a healthy KWin kiosk.

The image installs the dependencies needed for query and technical-analysis tools. Forecasting dependencies (Prophet, XGBoost, scikit-learn) are omitted to keep the runtime smaller. The full requested package is a locked development dependency managed by `uv`:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python build/lint.py
uv run --locked python tests/validate.py --container mt5 --case fedora-wayland-headless
MT5_ENV_FILE="$PWD/.env" tests/matrix.sh
```

The validator checks the exact account/server and downloaded history through `mt5-mcp`, compiles `expert/AlgoSamurai.mq5` with MetaEditor, runs Strategy Tester with a unique report, and retrieves the chart screenshot, MT5 window screenshot, report, parsed statistics, compiler output and service/terminal/tester logs. It prints each evidence directory under `artifacts/`. Backtests use local agents with cloud and remote agents disabled.

`tests/matrix.sh` runs all four images across both compositors and all four modes, authenticates VNC/web connections, and captures their remote framebuffer. Pass distro names to select images, for example `tests/matrix.sh fedora-44`. Nested cases require an available host display. The separate `tests/clipboard.py` exchanges text and PNG data with a disposable parent display, checks that its sockets belong to that parent, and saves bidirectional evidence without changing the user's clipboard.

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

`--gpu` requires NVIDIA Container Toolkit and records both the EGL probe and KWin's actual renderer. Overlay cases also record the compositor's loaded Qt libraries and reject mixed distro/overlay Qt paths. KWin 6.8's software Wayland backend needs the host's `/dev/udmabuf` device; load the host module with `sudo modprobe udmabuf` if necessary. The privileged launcher exposes that device to the container.

## Recorded acceptance

The 2026-10-28 run passed all 32 MT5 display combinations and six additional NVIDIA remote cases. Debian and Ubuntu used KWin 6.8.2 in both GPU VNC and web cases. Each case verified login, package MCP queries, expert compilation, a fresh backtest report, and chart/application capture. Nested clipboard checks passed with both host transports and Weston 15.0.1.

The local evidence index is `artifacts/VALIDATION.md`, with exact image IDs, per-case logs, screenshots and reports. Artifacts and account files are excluded from Git and image builds. Native MT5 MCP API-key authentication was not checked; the validated package endpoint is port 8000. Optional forecasting dependencies are omitted from the images.

## CI and publication

The [GitHub Actions pipeline](.github/workflows/build-and-publish.yml) builds and tests all four images on standard free Ubuntu runners. Default-branch pushes and release tags publish the exact tested images to GHCR after broker acceptance passes. Configure the three broker test secrets before the first publishing run; [CI setup](.github/README.md) covers triggers, tags, coverage, resource limits and retained artifacts.

Add `--capture-startup` to reproduce the KRFB startup regression: the test pauses PipeWire, authenticates VNC and requests initialized pixels before the first captured frame, then resumes capture and requires the real test window. Screenshots from both phases are retained.

The earlier compositor baseline is indexed in [artifacts/runtime/INDEX.md](artifacts/runtime/INDEX.md): 40 software profiles and 20 NVIDIA Wayland profiles, with exact tested versions, service logs, renderer details and framebuffer screenshots retained per case. KWin 6.8.2 acceptance runs have separate evidence directories. These checks cover the minimal compositor runtime; MT5 login and expert tests have their own evidence.
