# GitHub Actions

[`build-and-publish.yml`](workflows/build-and-publish.yml) uses standard free
`ubuntu-latest` runners. Each distribution gets its own runner and build cache.
New runs cancel superseded runs for the same branch or tag, freeing hosted
runners for the current commit.

## Checks and image tests

Every push and pull request runs locked `uv` dependency installation, unit tests,
Ruff, ShellCheck, Cppcheck, Dockerfile generation checks and actionlint. A four-image matrix
then builds the production image and runs:

1. An installation smoke test: Wine/WebView2/MT5 payloads, Windows Python
   dependencies and running KWin, with no account credentials or network access.
   Actual MT5 windows and charts are checked by the broker acceptance suite.
2. A complete display matrix against the final production image: Wayland and
   X11 × headless/VNC/web/nested, with nested mode repeated through both host
   transports. The four image jobs cover 40 cases. Every case saves a verified
   `screenshot.png`, including real headless compositor captures. Nested/VNC/web
   cases send mouse and keyboard input, save a unique token from the GUI, and
   verify the saved file. Nested cases also exchange text and PNG clipboard data.
   Tests reproduce delayed capture startup where PipeWire is used; X11 capture
   records that phase as inapplicable and still verifies authenticated frames.
3. Real MT5 mouse and keyboard checks in eight GUI profiles per image. Mixed-case
   text and Ctrl+A replacement must match read-only Win32 edit-control readback;
   mouse clicks must close Cancel and open File, and Escape must close the menu.
   Web cases use an actual Chromium/noVNC session. Private displays, internal
   networks, empty credentials and fresh Wine volumes isolate every case.
4. Twelve advanced MT5 pointer profiles per image: two child compositors × both
   nested host transports × scales 1, 1.25 and 2. They move and resize windows, including server-decorated native Wayland
   windows, repeat entry/click checks and retain cursor captures against an Arch
   parent built from the same commit. Its `pointer-parent` target extends `runtime-test` with
   only `breeze`, so it retains the current patched KRFB framebuffer bridge
   and stock KWin window decorations. Each distribution remains the tested MT5
   child; the existing eight-profile input suite still uses its matching parent.
   This test image adds no dependencies to published images and has no Wine stage.
5. Both input suites repeat with `--startup autologin`: twenty additional profiles
   per image use a dummy login on an internal network, without broker credentials.
   No startup dialog or menu interaction may refresh Wine's window geometry before
   the first chart click. Screenshot-located expert buttons must report their exact
   chart-local click coordinates and object names through MT5's `OnChartEvent`.
   The expert compiles once in a separate disposable, network-disabled container
   before test sessions start. Production images contain no test expert or compiler
   dependency. Publishing therefore requires 58 profiles per distribution.

Before image jobs, the source job requires at least **94% executable-line coverage
for every maintained Python, Bash and C++ source**, including tests and maintained
vendored code. Python/C++ branch reports are separate. Bash traces contain only
paths and line numbers, never command text or credentials. A dedicated Fedora
test image builds and runs native coverage without adding test dependencies to
published images. The compiler-audited declaration-only header is shown as N/A
and pinned by source hash; other missing or empty measurements fail. The combined
per-file table and raw/JSON/XML/HTML profiles are uploaded as `source-coverage`,
including after a failed check. Source hashes prevent merging stale profiles.

## Coverage site and README report

[`coverage-publish.yml`](workflows/coverage-publish.yml) publishes the
[static coverage site](https://skynet-core.github.io/mt5-docker/) after the complete
image workflow succeeds on the default branch. It accepts reports only from this
repository's successful default-branch build workflow, checks the downloaded
artifact digest and validates the per-file 94% gate again before deployment.
The site includes the full source table, sanitized coverage JSON and the Python
and native HTML reports. Bash entries link measured line gaps to the exact source
revision. Raw profiles, shell traces/maps, environment data and execution logs are
kept out of the public site.

Set the repository's **Settings → Pages → Build and deployment → Source** to
**GitHub Actions** once. Site preparation has read access; deployment has
`pages: write` and `id-token: write` in the `github-pages` environment. The README
job has `contents: write` and updates only the unique
`<!-- coverage-report:start -->` / `<!-- coverage-report:end -->` block after a
successful deployment. It regenerates that block against the latest branch before
each normal push, preserving concurrent edits elsewhere in the README. Bot commits
use the built-in `GITHUB_TOKEN` and `[skip ci]`; they do not start another image
build or cancel an active one. Coverage publication has its own concurrency group.

To republish a retained report, run **Publish source coverage** manually and supply
the successful default-branch image workflow's numeric `run_id`. Reports remain
attributed to that measured commit and run even when the README has newer edits.
The Pages actions are pinned to verified release commits. Their required setup and
permissions follow [GitHub's custom Pages workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages);
the README automation follows GitHub's
[GITHUB_TOKEN workflow-trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow).

## Running source checks locally

Run the same source linters locally with:

```sh
uv sync --locked
uv run --locked python build/lint.py
```

Ruff, ShellCheck and Cppcheck versions are locked as development dependencies.
The command lists every checked file, including vendored code, extensionless
scripts and new unignored files. Generated artifacts and virtual environments
are excluded through Git's ignore rules. Any linter finding fails the checks job.

The checks job also resolves the runtime requirements universally and for Linux
CPython 3.12, then dry-runs the Windows installation used by the image build.
MetaTrader5 ships only Windows wheels, so its `sys_platform == 'win32'` marker
must remain in both `build/mt5-mcp-requirements.in` and the compiled `.txt` file.
Keep `--universal` in the compiled file's command header: Dependabot preserves
that option when regenerating the requirements on Linux. Marker regression tests
and actual resolver checks run before any image jobs; Windows still installs the
SDK and Win32 helpers.

The matching Qt fixture is exported from the `runtime-test` target and mounted
read-only during compositor tests. Its test-only screenshot permission entry is
also mounted for headless capture. Both are excluded from the published image.
Disposable parent containers provide the nested displays and clipboard; the runner
needs no desktop session or GPU.

The workflow reclaims unused Android/.NET/GHC SDK space and requires 25 GiB free
after cleanup for the Wine image, native builders and test volumes. BuildKit runs
at most two build vertices concurrently. `KWIN_VARIANT` in the workflow selects
the Debian/Ubuntu stack; the current value `source` builds KWin 6.8.2. Set it to
`distro` to skip the private Qt/KF/KWin stages when the runner budget requires it.
The published image label records the selected variant; see
[`build/KWIN.md`](../build/KWIN.md). Arch builds its required separate X11
compositor against packaged Qt/KF.

Software rendering requires the host `udmabuf` kernel module; CI loads it before
the image tests. The pipeline fails clearly if the runner cannot provide it or
has insufficient disk. No paid or self-hosted runner is configured.

## Broker acceptance and publication

Before publishing, all eight MT5 display modes must additionally pass real broker
login, MCP queries, expert compilation, fresh Strategy Tester execution, chart
capture and authenticated VNC/web capture. These tests use an isolated Xvfb parent.
Set these repository Actions secrets to a dedicated demo account:

| Secret | Value |
| --- | --- |
| `MT5_ACCOUNT` | Numeric trading account |
| `MT5_PASSWORD` | Account password |
| `MT5_SERVER` | Broker server, for example `ICMarketsEU-Demo` |

Missing secrets fail publication before the expensive build. PRs and ordinary
branch pushes never receive broker credentials or publish application images.
The preflight rejects malformed account IDs and multiline settings. If the
server secret contains the literal placeholder `MT5_SERVER`, it uses the
confirmed `ICMarketsEU-Demo` broker name.
For a manual build without broker secrets, choose **Run workflow** and disable
`publish`; the installation and compositor tests still run.

Publishing runs on pushes to the default branch, `v*` release tags, and manual
runs with `publish` enabled. It logs in with the built-in `GITHUB_TOKEN` and the
job's `packages: write` permission. Enable Actions/package publication for the
repository; no separate registry password is needed. Each matrix job retags and
pushes its tested local image without rebuilding, so a failing test prevents
that distribution from being published.

Images use `ghcr.io/<owner>/<repository>` with distribution-qualified tags:

- `debian-13`, `ubuntu-26.04`, `fedora-44`, `archlinux` on the default branch.
- `sha-<full-commit>-<distribution>` on published commits.
- `v<version>-<distribution>` on release tags.

There is no shared `latest` tag. The GHCR package's visibility is controlled by
the repository/package settings. Intermediate layers use separate GitHub cache
scopes with `mode=max`; cache quota errors do not fail the build.

Validation artifacts are retained for 14 days. Display evidence is under
`artifacts/display-matrix/<distribution>/<backend>/<mode>/`, with `on-wayland/`
or `on-x11/` for nested sessions. Every case retains screenshot, input proof,
startup/container/service logs and metadata. `INDEX.md` and `index.json` reject
incomplete or mismatched case sets. Broker artifacts include
only chart PNGs, tester statistics and diagnostic phase/error-type records;
raw account responses, terminal logs and
application screenshots stay out of uploads. NVIDIA hardware tests remain a
separate local check because standard runners provide no NVIDIA GPU.

Account-free MT5 input evidence is under
`artifacts/mt5-input/<distribution>/<backend>/<mode>/`, with nested host transport
subfolders. It includes screenshots, expected/read-back text, GUI observation,
phase diagnostics and browser traces. Private parent sockets and Xauthority
files are excluded from uploads.

Advanced pointer evidence is retained under `artifacts/pointer-matrix/`, with
private parent sockets and Xauthority excluded. Every image job builds the
decorated Arch parent as `mt5:ci-pointer-parent`, reusing the Arch production
cache without adding another cache export. Image jobs allow up to 240 minutes
for source builds and the complete acceptance suite.

Startup-without-dialog evidence is retained under `artifacts/mt5-autologin-input/`
and `artifacts/autologin-pointer-matrix/`. It includes expert source/binary hashes,
zero-error compile logs, screenshot-discovered target bounds and raw chart events.
Private display sockets and Xauthority files are excluded from uploads.

Display failures print their case paths and causes in the job log, plus the
last 80 lines of the runner log on startup or runtime failure. A failed distro
run still fails the job while its successful cases retain their pass counts.
On older KWin software renderers, Wayland headless screenshots capture the
private Xvfb output backing KWin; VNC and web services remain disabled.
KWin 6.8 software sessions nested on X11 use a viewer of their private loopback
RFB output, preserving GUI input and the existing clipboard relay without DRI3.
