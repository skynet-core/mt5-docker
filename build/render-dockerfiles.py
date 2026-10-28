#!/usr/bin/env python3
"""Render distro package paths and KWin compatibility markers from Fedora."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTROS = {"debian-13": "debian", "ubuntu-26.04": "ubuntu", "archlinux": "arch"}
ARCH_X11_BUILDER = """FROM desktop-packages AS x11-build
COPY build/packages/arch/x11-build.txt /build-support/packages/x11-build.txt
COPY build/arch-kwin-x11 /build-support/arch-kwin-x11
RUN /build-support/install-packages x11-build \\
    && bash /build-support/arch-kwin-x11
"""
APT_KWIN_BUILDER = """# Build once against Debian's older glibc; the private ABI also runs on Ubuntu.
FROM ghcr.io/skynet-core/systemd:debian-13 AS kwin-build-dependencies
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
COPY build/install-packages /build-support/install-packages
COPY build/packages/debian/kwin-build.txt /build-support/packages/kwin-build.txt
RUN /build-support/install-packages kwin-build
COPY build/kwin-6.8.2 build/kwin-6.8.2-sources.tsv /build-support/
FROM kwin-build-dependencies AS kwin-platform
COPY build/kwin-phases/platform.sh /build-support/kwin-phase-platform.sh
RUN /build-support/kwin-6.8.2 platform
FROM kwin-platform AS kwin-qtbase
COPY build/kwin-phases/qtbase.sh /build-support/kwin-phase-qtbase.sh
RUN /build-support/kwin-6.8.2 qtbase
FROM kwin-qtbase AS kwin-qtdeclarative
COPY build/kwin-phases/qtdeclarative.sh /build-support/kwin-phase-qtdeclarative.sh
RUN /build-support/kwin-6.8.2 qtdeclarative
FROM kwin-qtdeclarative AS kwin-qtmodules
COPY build/kwin-phases/qtmodules.sh /build-support/kwin-phase-qtmodules.sh
RUN /build-support/kwin-6.8.2 qtmodules
FROM kwin-qtmodules AS kwin-frameworks
COPY build/kwin-phases/frameworks.sh /build-support/kwin-phase-frameworks.sh
RUN /build-support/kwin-6.8.2 frameworks
FROM kwin-frameworks AS kwin-build
COPY build/packages/debian/kwin-final-build.txt /build-support/packages/kwin-final-build.txt
RUN /build-support/install-packages kwin-final-build
COPY build/kwin-vulkan-sources.tsv /build-support/kwin-vulkan-sources.tsv
RUN cat /build-support/kwin-vulkan-sources.tsv >> /build-support/kwin-6.8.2-sources.tsv
COPY build/kwin-phases/kwin.sh /build-support/kwin-phase-kwin.sh
RUN /build-support/kwin-6.8.2 kwin
FROM kwin-build AS kwin-artifacts
COPY build/kwin-client-sources.tsv /build-support/kwin-client-sources.tsv
RUN cat /build-support/kwin-client-sources.tsv >> /build-support/kwin-6.8.2-sources.tsv
COPY build/kwin-phases/collect.sh /build-support/kwin-phase-collect.sh
RUN /build-support/kwin-6.8.2 collect
"""
APT_KWIN_SELECTION = """# Select the dependency graph before building, so distro skips every source stage.
FROM kwin-artifacts AS kwin-source
RUN install -d /out/root/etc/plasma \\
    && touch /out/root/etc/plasma/opengl-software-capture
FROM desktop-packages AS kwin-distro
# DISTRO_KWIN_MARKER
# KWIN_VARIANT selects one of the two internal stages above.
# hadolint ignore=DL3006
FROM kwin-${KWIN_VARIANT} AS kwin-selected
"""


def render(template, distro, family):
    content = template.replace(
        "ARG BASE_IMAGE=ghcr.io/skynet-core/systemd:fedora-44",
        f"ARG BASE_IMAGE=ghcr.io/skynet-core/systemd:{distro}",
    ).replace("build/packages/fedora/", f"build/packages/{family}/")
    if family == "ubuntu":
        for phase in ("input-build", "vnc-build"):
            content = content.replace(
                f"build/packages/ubuntu/{phase}.txt", f"build/packages/debian/{phase}.txt"
            )
    if family in ("debian", "ubuntu"):
        content = content.replace(
            f"ARG BASE_IMAGE=ghcr.io/skynet-core/systemd:{distro}\n",
            f"ARG BASE_IMAGE=ghcr.io/skynet-core/systemd:{distro}\n"
            "ARG KWIN_VARIANT=source\n",
            1,
        )
        content = content.replace(
            "# DISTRO_WINE_PREPARE",
            "COPY build/install-winehq /build-support/install-winehq\n"
            "RUN bash /build-support/install-winehq",
        )
        selection = APT_KWIN_SELECTION.replace(
            "# DISTRO_KWIN_MARKER",
            "RUN install -d /out/root/etc/plasma \\\n"
            "    && touch /out/root/etc/plasma/vnc-existing-output"
            if family == "debian"
            else "RUN install -d /out/root",
        )
        content = content.replace(
            "# DISTRO_KWIN_BUILDER", APT_KWIN_BUILDER + selection
        )
        content = content.replace(
            "# DISTRO_KWIN_RUNTIME", "COPY --from=kwin-selected /out/root/ /"
        )
    if family == "arch":
        content = content.replace("# DISTRO_X11_BUILDER", ARCH_X11_BUILDER)
        content = content.replace(
            "# DISTRO_X11_RUNTIME",
            "COPY build/packages/arch/x11-runtime.txt /build-support/packages/x11-runtime.txt\n"
            "RUN /build-support/install-packages x11-runtime\n"
            "COPY --from=x11-build /out/root/ /\n"
            "RUN ldd /usr/bin/kwin_x11 >/tmp/kwin-x11.ldd \\\n"
            "    && ! grep -F 'not found' /tmp/kwin-x11.ldd \\\n"
            "    && rm /tmp/kwin-x11.ldd",
        )
        content = content.replace(
            "# DISTRO_RUNTIME_CONFIG",
            "# KWin 6.8 removed QPainter; use software OpenGL for capture.\nRUN touch /etc/plasma/opengl-software-capture",
        )
    return content.replace(
        "# syntax=docker/dockerfile:1\n",
        "# syntax=docker/dockerfile:1\n# Generated by build/render-dockerfiles.py; edit Dockerfile.fedora-44 and build/.\n",
        1,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    template = (ROOT / "Dockerfile.fedora-44").read_text()
    failures = []
    for distro, family in DISTROS.items():
        content = render(template, distro, family)
        path = ROOT / f"Dockerfile.{distro}"
        if options.check:
            if not path.exists() or path.read_text() != content:
                failures.append(path.name)
        else:
            path.write_text(content)
    if failures:
        raise SystemExit("Regenerate Dockerfiles: " + ", ".join(failures))


if __name__ == "__main__":
    main()
