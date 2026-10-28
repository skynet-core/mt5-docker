#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
: "${prefix:?Sourced through build/kwin-6.8.2}"
build_cmake vulkan-headers -DVULKAN_HEADERS_ENABLE_TESTS=OFF -DVULKAN_HEADERS_ENABLE_MODULE=OFF
for project in kdecoration kwayland knighttime; do build_kde "$project"; done
build_kde kwin -DKWIN_BUILD_KCMS=OFF -DKWIN_BUILD_TABBOX=OFF \
	-DKWIN_BUILD_NOTIFICATIONS=OFF -DKWIN_BUILD_SCREENLOCKER=OFF \
	-DKWIN_BUILD_GLOBALSHORTCUTS=OFF -DKWIN_BUILD_RUNNERS=OFF \
	-DKWIN_BUILD_OVERVIEW=OFF -DKWIN_BUILD_ACTIVITIES=OFF \
	-DKWIN_BUILD_DECORATIONS=OFF
# File capabilities would make the loader discard scoped library paths.
if [[ -n $(getcap "$prefix/bin/kwin_wayland") ]]; then
	setcap -r "$prefix/bin/kwin_wayland"
fi
"$prefix/bin/kwin_wayland" --version | grep -F 6.8.2
