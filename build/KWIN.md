# Private KWin 6.8.2 build

Debian 13 keeps its original systemd base and package
repositories. Its packaged Qt/KF versions do not meet [KWin 6.8.2's build
requirements](https://github.com/KDE/kwin/blob/v6.8.2/CMakeLists.txt). The
`kwin-*` Docker stages build its required stack into `/opt/kwin-6.8.2` using
Debian 13's glibc. The KDE image copies only the runtime payload.
The `TARGET_APP` kiosk session activates
it; applications keep their distro Qt environment.

This single build keeps normal GPU selection in NVIDIA remote modes. There is
no alternate distro-Wayland variant. X11 uses Debian's packaged KWin compositor.

| Component | Version |
| --- | --- |
| KWin, KDecoration, KWayland, KNightTime, KScreen | 6.8.2 |
| Qt | 6.11.2 |
| KDE Frameworks | 6.30.0 |
| Wayland | 1.26.0 |
| wayland-protocols | 1.49 |
| plasma-wayland-protocols | 1.23.0 |
| libdrm | 2.4.127 |
| Vulkan headers | SDK 1.4.363.0 |

Source URLs and SHA-256 checksums are recorded in
[`kwin-6.8.2-sources.tsv`](kwin-6.8.2-sources.tsv) and
[`kwin-vulkan-sources.tsv`](kwin-vulkan-sources.tsv), and
[`kwin-client-sources.tsv`](kwin-client-sources.tsv). Every downloaded archive
is verified before extraction. KWin's source is compiled without patches.
The newer Vulkan C++ headers are build dependencies; the distro Vulkan loader
and GPU drivers remain runtime dependencies.

Qt builds only qtbase, qtsvg, qtshadertools, qtdeclarative, qttools,
qt5compat and qtpositioning. In Qt 6.11, qtbase supplies WaylandClient and its
scanner; a separate QtWaylandCompositor build is unnecessary. Qt uses bundled
JPEG and omits ICU. KDE builds
the Frameworks required by KWin and KNightTime, plus PolkitQt. The KWin kiosk
build omits configuration modules, desktop overview, notifications, tabbox,
screen locker, activities, decorations and shortcut configuration.

The collector strips development headers, static libraries, SDK metadata and
documentation. The payload includes `libdisplay-info.so.2` and source license
files. KWin's file capability is removed
so its loader honors the private library paths in nested and remote modes.

Only the compositor and its cache builder use the private libraries, plugins
and QML paths. Its target application has the original environment restored.
The output selector uses the matching private `kscreen-doctor`, since KWin
6.8's new output-device registry cannot be discovered by older KScreen.
KRFB, clipboard helpers and other distro Qt clients run as separate processes
with their distro libraries. This keeps both Qt versions out of any single
process. `tests/runtime.py` records the actual loaded compositor
libraries and rejects mixed Qt paths.

Build the payload alone with:

```sh
docker build --target kwin-artifacts -f Dockerfile.debian-13 .
```

Each source phase has its own cached stage. The final `mt5` image copies only
the collected runtime tree; source archives, compilers and build tools stay
in the builder stages.
