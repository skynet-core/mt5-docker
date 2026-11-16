# Known issues

## KDE baseline: Debian 13

Debian 13 is retained because it has a stable package base, pinned WineHQ 11.20,
and no observed Market startup stall in the recorded local workload. KWin 6.8.2
is retained for Wayland GPU/remote support; X11 uses Debian's packaged compositor.
All headless, nested, VNC and web modes remain available on both backends.

This is the conservative choice from the existing evidence, not a claim that
every GUI regression is resolved. The previous [hosted run](https://github.com/skynet-core/mt5-docker/actions/runs/41773657771)
failed Debian's scaled nested-Wayland input gate. X11 headless or VNC is the
recommended operating mode when scaled nesting is unnecessary.

### Initial characters lost in scaled nested Wayland

The company-search edit lost an initial prefix at 125% scaling in the recorded
Debian run despite correct focus, pointer calibration and startup DPI. The cause
downstream of input delivery remains unresolved. The original input assertions
remain release gates.

### Market validation scope

Market tests check the bundled WebView2 shell, DOM, painting and transport pixels.
They do not prove live catalog retrieval, MQL5 login, downloads or purchases.
The production WebView2 software-rendering policy remains necessary.

## Headless native MCP

MT5's native API-key storage format is not documented. The launcher currently
uses the tested `ApiKey=x` setting to disable native loopback authentication and
puts a bearer-authenticated nginx proxy on the published port. The internal
listener is accessible to processes inside the container. Smoke tests check
missing, incorrect, case-altered and other-container tokens are rejected.
An upstream change to this setting must pass those real MCP tests before release.

Native MCP includes trading and file-management tools. Treat its token as an
account credential and keep the default host loopback binding. The validation
suite only calls read-only tools. The KDE package MCP endpoint is read-only.

MT5's official installer is mutable. Builds record its checksum, and acceptance
records the actual native MCP version and exact image ID. Persistent volumes
retain installed terminal versions across image replacement. Public broker
definitions are bundled for `ICMarketsEU-Demo`; another broker may need its own
server cache or explicit server address.
