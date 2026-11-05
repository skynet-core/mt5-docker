# syntax=docker/dockerfile:1
FROM debian:trixie-slim AS wine-repository
SHELL ["/bin/bash", "-e", "-u", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY build/install-winehq /tmp/install-winehq
RUN bash /tmp/install-winehq && rm /tmp/install-winehq

FROM wine-repository AS installer
RUN dpkg --add-architecture i386 && apt-get update \
    && apt-get install -y --no-install-recommends \
        winehq-devel=11.20~trixie-1 wine-devel=11.20~trixie-1 \
        wine-devel-amd64=11.20~trixie-1 wine-devel-i386:i386=11.20~trixie-1 \
        xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
    && useradd --create-home --uid 1000 mt5
USER mt5
ENV HOME=/home/mt5 WINEPREFIX=/home/mt5/.wine WINEARCH=win64 \
    WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
RUN curl --fail --location --retry 3 \
        https://download.mql5.com/cdn/web/metaquotes.ltd/mt5/mt5setup.exe \
        --output /tmp/mt5setup.exe \
    && sha256sum /tmp/mt5setup.exe \
    && mkdir -p "$WINEPREFIX" \
    && xvfb-run -a -s '-screen 0 1280x960x24 -nolisten tcp' bash -c '\
        set -eu; wineserver -p; timeout 120 wine wineboot.exe --init; \
        wine reg add "HKCU\\Software\\Wine" /v Version /d win10 /f; \
        status=0; timeout 600 wine /tmp/mt5setup.exe /auto "/path:C:\\MT5" || status=$?; \
        wineserver -k -w; \
        [[ $status == 0 || $status == 1 || $status == 124 ]]; \
        for exe in terminal64.exe metaeditor64.exe metatester64.exe; do \
            test -n "$(find "$WINEPREFIX/drive_c/MT5" -maxdepth 1 -type f -iname "$exe" -size +0c -print -quit)" || exit 1; \
        done' \
    && rm -f /tmp/mt5setup.exe "$WINEPREFIX/drive_c/MT5/Config/assistant.ini"

FROM wine-repository AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        wine-devel-amd64=11.20~trixie-1 libgnutls30t64 fonts-wine xvfb xauth nginx \
    && rm -rf /var/lib/apt/lists/* \
    && find /opt/wine-devel -type f -name '*.a' -delete \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
    && useradd --create-home --uid 1000 mt5
COPY --from=installer /opt/wine-devel/bin/wine /opt/wine-devel/bin/wineserver /opt/wine-devel/bin/
COPY --from=installer /opt/wine-devel/share/wine /opt/wine-devel/share/wine
COPY --from=installer --chown=mt5:mt5 /home/mt5/.wine/drive_c/MT5 /home/mt5/MT5
COPY headless/start /usr/local/bin/mt5-headless
USER mt5
ENV HOME=/home/mt5 WINEPREFIX=/home/mt5/.wine WINEARCH=win64 \
    WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" PATH="/opt/wine-devel/bin:${PATH}"
EXPOSE 22347
ENTRYPOINT ["/bin/bash", "/usr/local/bin/mt5-headless"]
