# GitHub Actions

[`build-and-publish.yml`](workflows/build-and-publish.yml) uses standard free
`ubuntu-24.04` runners. Each distribution gets its own runner and build cache.
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

Run the same source linters locally with:

```sh
uv sync --locked
uv run --locked python build/lint.py
```

Ruff, ShellCheck and Cppcheck versions are locked as development dependencies.
The command lists every checked file, including vendored code, extensionless
scripts and new unignored files. Generated artifacts and virtual environments
are excluded through Git's ignore rules. Any linter finding fails the checks job.

The matching Qt fixture is exported from the `runtime-test` target and mounted
read-only during compositor tests. Its test-only screenshot permission entry is
also mounted for headless capture. Both are excluded from the published image.
Disposable parent containers provide the nested displays and clipboard; the runner
needs no desktop session or GPU.

The workflow reclaims unused Android/.NET/GHC SDK space and requires 25 GiB free
after cleanup for the Wine image, native builders and test volumes. BuildKit runs
at most two build vertices concurrently. Debian and Ubuntu use
`--build-arg KWIN_VARIANT=distro`, which completely skips the private Qt/KF/KWin
source stages. Published hosted-CI images therefore contain their distribution
KWin versions. The default local build still compiles KWin 6.8.2; see
[`build/KWIN.md`](../build/KWIN.md). Arch still builds its required separate X11
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

Display failures print their case paths and causes in the job log, plus the
last 80 lines of the runner log on startup or runtime failure. A failed distro
run still fails the job while its successful cases retain their pass counts.
On older KWin software renderers, Wayland headless screenshots capture the
private Xvfb output backing KWin; VNC and web services remain disabled.
KWin 6.8 software sessions nested on X11 use a viewer of their private loopback
RFB output, preserving GUI input and the existing clipboard relay without DRI3.
