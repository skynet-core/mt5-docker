# Known issues and image selection

Status: 2026-11-04. The issues below are deferred. No tests have been weakened or marked as expected failures, and the pending resize-test candidate has not been applied.

This snapshot covers source commit `85060b51203b67190e58e877083b0d314e54baa0` and [CI run 41773657771](https://github.com/skynet-core/mt5-docker/actions/runs/41773657771). Source checks passed, but **all four image jobs failed and all four publication steps were skipped**. The published distribution tags were not updated by this run. Inspect a published image's revision before assuming it contains these changes.

## Issues per image

Wine versions are the actual loader versions recorded by each CI image's installation smoke test.

| Image | Wine | Failure in the latest CI run | Other retained observations |
| --- | --- | --- | --- |
| `fedora-44` | 11.20 Staging | Initial characters disappear when typing in a nested Wayland session on a Wayland host at 200% scale. | The prefix loss also reproduced locally under one-core scheduling pressure. An earlier local Market run stalled in X11/VNC. A separate stress-only resize-test race is described below. |
| `debian-13` | 11.20 | The same initial-text failure in nested Wayland on a Wayland host at 125% scale. | No Market startup failure was observed in the retained local runs. CI did not reach the Market suite after its pointer/input gate failed. |
| `ubuntu-26.04` | 11.20 | The same initial-text failure in nested Wayland on a Wayland host at 200% scale. | An earlier local Market run stalled in Wayland/web. CI did not reach the Market suite after its pointer/input gate failed. |
| `archlinux` | 11.21 | Market failed to paint during a Wayland/headless cold start. | All twelve advanced pointer/input profiles passed in this CI run; nine of ten Market profiles passed. Later local passes did not establish a fix for the startup stall. |

These are observed failing configurations, not proof that other configurations cannot fail. The Wine-version difference does not establish the cause or a fix.

## Deferred issues

### KI-1: Missing initial characters in scaled nested Wayland

The company-search edit has focus and answers text queries, but its initial text loses a prefix. The screenshots show `t-seed-456` on Fedora, `T5Input-seed-456` on Debian, and `nput-seed-456` on Ubuntu instead of `MT5Input-seed-456`. Physical pointer calibration and applicable startup-DPI checks passed before typing.

A local Fedora replay emitted the full seed through the nested viewer and child KRFB input requests, yet the edit lost its first character. The remaining fault is downstream of those requests; KWin, guest Xwayland/Wine and application-side text changes have not been distinguished. No input delay, retry or coordinate correction has been added.

X11 headless and VNC are reasonable configurations to evaluate when selecting an image without depending on this particular scaled nested-Wayland path. They are not claimed to fix KI-1 or to be free of unrelated issues.

### KI-2: Intermittent Market/WebView2 startup stall

The Market frame can open while its body shows underlying charts and never produces the required browser paint evidence. The retained failures show a WebView2 browser root without renderer children and repeated diagnostic timeouts. This occurred in Arch CI and earlier local Fedora and Ubuntu workloads. The cause remains unproved.

The production `--disable-gpu` policy addresses the previously identified WebView2 rendering problem; it does not establish a fix for this separate intermittent startup stall. Successful subsequent runs remain successful observations, not a resolution of the failures.

### KI-3: Resize-test readiness race under scheduling pressure

On Fedora with one CPU, the X11 guest nested on a Wayland host can reach pointer calibration after the parent resize is acknowledged but before Wine publishes its new desktop size. The test then compares coordinates from different desktop sizes, reporting `128,100` versus `110,82`.

This is a diagnosed test-readiness race. An isolated candidate waits for the native desktop-size change before the original calibration; its regression tests pass, but it has not been applied or validated through the GUI matrices. It should not be described as a general pointer-offset defect across all images.

## Selecting one image to support

**Debian 13 with X11 headless or VNC is the first candidate for conservative single-image support.** It avoids depending on the observed scaled nested-Wayland failure path, and no Market startup failure was observed in its retained local workload. This is an operational preference based on the current evidence, not a demonstrated overall stability winner or a green CI result.

If scaled nested interaction is the priority and Market is not required, Arch has the strongest advanced-input result in this particular CI run. Its Market cold-start failure remains a reason to withhold an unrestricted stability claim.

All four images passed the latest complete local CPU, NVIDIA GPU and Market matrices: 544 profiles plus four installation smoke tests in total. Current hosted failures take precedence over any claim that validation is wholly green. Long-duration reliability has not been established.

## Validation limits

Market tests open MT5's bundled Market shell and verify DOM, animation, native-window painting, compositor output and visible transport pixels. They do not establish that live catalog retrieval, MQL5 account login, downloads or purchases work. These limits apply to every image.

Use a published image from a successful build and pin its digest for a support baseline. Recheck its recorded revision, Wine version and KWin variant; mutable tags and a retained Wine volume can differ from this source-build snapshot.
