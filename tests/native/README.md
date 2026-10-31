# Native source coverage

These tests compile the maintained C++ files themselves. Qt key/button events
drive the editor/benchmark, clipboard tests exercise real framing, encoding,
size limits and descriptor I/O, and protocol tests assert Wayland requests,
RFB button/key translation, clipboard forwarding and duplicate suppression.
Dependency failures are injected at the EGL, syscall, Qt capture and Wayland C
API boundaries. They are unit tests; production-image MT5 display/input tests
separately verify real compositor and network transports.

The native image's existing `vnc-build` packages provide GCC/gcov, CMake,
Ninja, Qt 6 Core/Gui/Widgets/DBus/Test, KDE CoreAddons, Wayland client headers,
xkbcommon, FLTK 1.3, Nettle and libvncserver headers. `KRFB_SOURCE_DIR` must name the same
pinned KRFB tree used by that image. Input tests compile its event interfaces;
the capture test links its actual PipeWire framebuffer module objects.
The viewer regression links the actual patched TigerVNC Viewport, connection and
pointer handler objects, forwarding one enter event per coordinate/button case
without a network server. Crypto cases use its actual AES streams for authenticated
roundtrips/tamper rejection and check full SHA1 digests with the retained
eight-byte fingerprint and sixteen-byte AES128 key prefixes. Native test sources and the GUI fixture/benchmark are
executed and linted, but the central application/build coverage gate excludes
them according to `build/lint.py`. Fetched sources, generated files and system
headers are outside that gate too.

The framebuffer regression emits actual `frameReceived` signals for nine SPA
formats, odd-width growth/shrink, padded rows and a null image. It decodes the
result through the actual libvncserver/PW framebuffer pixel format. Real modern
frame data uses the unchanged upstream private cleanup declaration from
[`pwhelpers.h`](https://github.com/KDE/kpipewire/blob/v6.7.5/src/pwhelpers.h),
retained as `kpipewire-pwhelpers.h` with an exact SHA-256 check. CMake accepts only
the verified KPipeWire 6.7.5 and 6.3.90 private ABIs; other modern versions require
an explicit source review. The callback test runs on an isolated offscreen Qt
platform without a compositor, PipeWire service or user display.

Append `Dockerfile.fragment` to a generated Dockerfile and build/load its
`native-coverage` target. The fragment reuses `vnc-build` and the pinned `uv`
stage and installs locked Python dev dependencies only in that test image.
Its entrypoint creates a private Xvfb and exports `NATIVE_TEST_DISPLAY` for every
test, including actual X11 capture and viewer entry; no user display is discovered.
Run it with an owned host output directory bound at `/coverage`; the entrypoint
runs CTest every time and always collects JSON/Cobertura/HTML and logs, including
after test failures. It returns nonzero for failed tests, collection errors or
missing reports. Production image dependencies and targets are unchanged.

```sh
mkdir -p artifacts/coverage/native
cat Dockerfile.fedora-44 tests/native/Dockerfile.fragment > artifacts/coverage/native/Dockerfile
docker buildx build --load --target native-coverage \
  -f artifacts/coverage/native/Dockerfile -t mt5-native-coverage .
docker run --rm -v "$PWD/artifacts/coverage/native:/coverage" mt5-native-coverage
```

Local equivalent requires those development packages, Xvfb and `xvfb-run`, the
pinned fresh KRFB source and its `vnc-build` development dependencies, plus a fresh
TigerVNC 1.16.0 source tree with all three
`vendor/kde-container/patches/tigervnc-*.patch` patches applied. Prepare the KRFB
tree as in the image, and add each test fragment once:

```sh
KRFB_SOURCE_DIR=/path/to/pinned/krfb
KRFB_BUILD_DIR="$PWD/artifacts/coverage/native/krfb"
cp vendor/kde-container/native/framebuffer-geometry.h "$KRFB_SOURCE_DIR/krfb/"
for patch in krfb-low-latency krfb-existing-output krfb-initial-framebuffer krfb-loopback-relay; do
  patch --batch --fuzz=0 -d "$KRFB_SOURCE_DIR" -p1 < "vendor/kde-container/patches/$patch.patch"
done
printf '\ninclude("%s/tests/native/krfb-frame-unit.cmake")\n' "$PWD" \
  >> "$KRFB_SOURCE_DIR/framebuffers/pipewire/CMakeLists.txt"
cmake -S "$KRFB_SOURCE_DIR" -B "$KRFB_BUILD_DIR" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build "$KRFB_BUILD_DIR" --parallel 2 --target krfb-frame-unit
TIGERVNC_SOURCE_DIR=/path/to/patched/tigervnc-1.16.0
TIGERVNC_BUILD_DIR="$PWD/artifacts/coverage/native/tigervnc"
FLTK_CONFIG=$(command -v fltk-config1.3 || command -v fltk-config)
printf '\ninclude("%s/tests/native/tigervnc-enter.cmake")\n' "$PWD" \
  >> "$TIGERVNC_SOURCE_DIR/vncviewer/CMakeLists.txt"
cmake -S "$TIGERVNC_SOURCE_DIR" -B "$TIGERVNC_BUILD_DIR" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_VIEWER=ON -DBUILD_JAVA=OFF \
  -DENABLE_H264=OFF -DENABLE_NLS=OFF \
  -DFLTK_CONFIG_SCRIPT="$FLTK_CONFIG" \
  -DFLTK_INCLUDE_DIR="$("$FLTK_CONFIG" --includedir)" \
  -DCMAKE_LIBRARY_PATH=/usr/lib/fltk1.3
cmake --build "$TIGERVNC_BUILD_DIR" --parallel 2 --target viewport-enter viewer-crypto
cmake -S tests/native -B artifacts/coverage/native/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug -DKRFB_SOURCE_DIR="$KRFB_SOURCE_DIR" \
  -DKRFB_FRAME_TEST_PROGRAM="$KRFB_BUILD_DIR/bin/krfb-frame-unit" \
  -DTIGERVNC_TEST_PROGRAM="$TIGERVNC_BUILD_DIR/vncviewer/viewport-enter" \
  -DTIGERVNC_CRYPTO_TEST_PROGRAM="$TIGERVNC_BUILD_DIR/vncviewer/viewer-crypto"
cmake --build artifacts/coverage/native/build --parallel 2
xvfb-run --auto-servernum --server-args='-screen 0 800x600x24 -nolisten tcp' \
  sh -c 'export NATIVE_TEST_DISPLAY="$DISPLAY"; ctest --test-dir artifacts/coverage/native/build --output-on-failure --parallel 2'
uv run --locked gcovr --root . --filter '(vendor/kde-container|tests/native)/.*' \
  --merge-mode-functions merge-use-line-min --json artifacts/coverage/native/native.json \
  --xml artifacts/coverage/native/native.xml --html-details artifacts/coverage/native/native.html \
  artifacts/coverage/native/build
```

Start from an empty build directory, or remove old `.gcda` profiles after any
source/rebuild change, to avoid merging stale compiler stamps. gcovr must use
the same GCC/gcov version as the build. The central coverage report enforces
the 94% minimum for **each** maintained file; the aggregate is insufficient.
Branches are reported separately.

The X11 capture and viewer-entry cases require `NATIVE_TEST_DISPLAY` to identify
the private Xvfb provided by the container entrypoint or local wrapper above.
Other GUI cases use Qt's isolated offscreen platform. Each protocol test
has a 30-second timeout; the controller's workers flush counters before its
normal forced teardown.
