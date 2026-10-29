# Native source coverage

These tests compile the maintained C++ files themselves. Qt key/button events
drive the editor/benchmark, clipboard tests exercise real framing, encoding,
size limits and descriptor I/O, and protocol tests assert Wayland requests,
RFB button/key translation, clipboard forwarding and duplicate suppression.
Dependency failures are injected at the EGL, syscall, Qt capture and Wayland C
API boundaries. They are unit tests; production-image MT5 display/input tests
separately verify real compositor and network transports.

The native image's existing `input-source` packages provide GCC/gcov, CMake,
Ninja, Qt 6 Core/Gui/Widgets/DBus/Test, KDE CoreAddons, Wayland client headers,
xkbcommon and libvncserver headers. `KRFB_SOURCE_DIR` must name the same pinned
KRFB tree used by that image; only its event interface classes are compiled.
Fetched KRFB files, generated protocol/moc files and system headers are outside
the maintained-source coverage filter. Owned test C++ is included.

Append `Dockerfile.fragment` to a generated Dockerfile and build/load its
`native-coverage` target. The fragment reuses `input-source` and the pinned `uv`
stage and installs locked Python dev dependencies only in that test image.
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

Local equivalent, with those development packages already installed:

```sh
cmake -S tests/native -B artifacts/coverage/native/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug -DKRFB_SOURCE_DIR=/path/to/pinned/krfb
cmake --build artifacts/coverage/native/build --parallel 2
ctest --test-dir artifacts/coverage/native/build --output-on-failure --parallel 2
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

`fake-wayland.h` declares the test protocol API and aggregate fields; its default
initialization contract is checked by the input tests. GCC emits no executable
lines for this header, so the source inventory records it as having no executable
lines rather than inventing a coverage percentage.

The X11 root-capture unit case requires `NATIVE_TEST_DISPLAY` to identify a
private Xvfb display and otherwise reports a skip. It never discovers a user
display. All other GUI cases use Qt's isolated offscreen platform, and the
coverage gate still includes the unexecuted X11 branch. Each protocol test
has a 30-second timeout; the controller's workers flush counters before its
normal forced teardown.
