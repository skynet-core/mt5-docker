# CI and publication

[build-and-publish.yml](workflows/build-and-publish.yml) maintains two production
images: Debian KDE (`debian-13`) and standalone native MCP (`headless`).

## Gates

Every main-branch push and pull request runs locked dependencies, unit tests, Ruff,
ShellCheck, Cppcheck, actionlint and per-source coverage. Native coverage reuses
the Debian build stages. Every maintained Python/Bash/C++ file must reach 94%
line coverage; tests and fixtures still run but do not count toward the gate.

The headless job builds the image and tests the real terminal through its
published host port: custom configuration, MCP initialization, discovery and
tool calls, rejected tokens, independent tokens, restart and volume replacement.
The account-free test uses a dedicated Docker bridge with no broker credentials.

The KDE job retains these checks:

- Installation smoke: MT5, WebView2 and Windows Python imports.
- Ten display profiles: both compositors, headless/VNC/web, and nested on both
  Wayland and X11 hosts. Rendering, input and clipboard evidence are required.
- Eight real-terminal GUI input profiles and twelve scaled nested pointer profiles.
- Both input suites repeated with dummy startup autologin (twenty profiles).
- Ten Market/WebView2 painting profiles.
- Eight real broker/MCP/expert-backtest profiles before publication.

The native test image is Debian-only. Advanced pointer tests use a digest-pinned
upstream KDE image as their decorated host; their MT5 guest is the tested Debian
production image.
The pinned KDE runtime remains covered by the original regression tests.
Standard hosted runners use software rendering; GPU validation is a local tier.
KWin needs `/dev/udmabuf`; CI loads the module before GUI tests. Source builds need
at least 25 GiB of free disk and can take substantial time on a cold cache.

## Reusing compiled dependencies

BuildKit stores every intermediate build layer in GHCR (`mode=max`), including
the compiled Qt/KWin stack, KRFB, TigerVNC and Xwayland. Unchanged stages are
pulled from the registry and their artifacts copied into the image; they are
not recompiled. Source, patch, build-command or base-image changes invalidate
the affected stages automatically.

Cache tags use `buildcache-<headless|native|debian-13>-<scope>` in the image
repository. Main has a shared `main` scope; each PR has its own `pr-<number>`
scope and also reads main. Other manual/tag builds use `sha-<commit>`.
The native-test and KDE jobs also read each other's caches to share the native
dependency builds. Fixture builds reuse the same loaded builder cache.
The KDE dependency/fixture image is cached before the MT5 installer runs, so a
later installation failure does not discard completed Qt/KWin builds.

Fork and Dependabot PRs use their read-only job token to read registry caches.
Cache writes by ordinary same-repository PRs do not publish production image
tags. Existing Actions caches are read only for migration; new large build
layers no longer compete for the repository's Actions cache quota.
Feature branches run through PR checks, avoiding duplicate push and PR builds.

## Broker secrets

Set repository Actions secrets `MT5_ACCOUNT`, `MT5_PASSWORD`, and `MT5_SERVER`
to a dedicated demo account. Missing or multiline values fail preflight.
The existing literal `MT5_SERVER` placeholder resolves to the previously
confirmed `ICMarketsEU-Demo` broker.

Publication always requires real broker acceptance for each image. For a private,
non-publishing acceptance run on a branch:

```sh
gh workflow run build-and-publish.yml --ref your-branch -f publish=false -f broker=true
```

Ordinary branch pushes and pull requests do not receive broker credentials.
The headless acceptance test mounts a temporary custom UTF-16 startup INI,
requires the exact configured account and server, and checks the terminal is
connected and authorized in the current session. It repeats after restart and
container replacement using the retained volume. It never calls a login tool
or submits a trade. Only boolean broker results are uploaded; account responses,
credentials and broker terminal logs are excluded.

## Published images

Default-branch pushes, release tags and manual `publish=true` runs publish the
exact tested image IDs without rebuilding:

- `ghcr.io/<owner>/<repository>:debian-13` and `:headless` on the default branch.
- `:sha-<full-commit>-debian-13` and `:sha-<full-commit>-headless`.
- `:v<version>-debian-13` and `:v<version>-headless` for releases.

There is no shared `latest` tag. Historical distribution tags are no longer
maintained. Failed acceptance prevents publication of the affected image.

Artifacts are retained for 14 days. `source-coverage` contains per-file reports;
`headless-smoke` contains MCP evidence and sanitized broker results;
`validation-debian-13` contains KDE screenshots, GUI input and sanitized broker
statistics. Private display sockets and Xauthority are excluded from uploads.

[coverage-publish.yml](workflows/coverage-publish.yml) accepts only successful
default-branch reports, rechecks their source hashes and coverage gate, publishes
the Pages site, and updates the README coverage block. Configure Pages to use
GitHub Actions. Historical reports remain attributed to their tested revisions.
