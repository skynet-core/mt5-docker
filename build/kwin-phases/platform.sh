#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
: "${prefix:?Sourced through build/kwin-6.8.2}"
source_dir wayland
meson setup /work/wayland/build /work/wayland/source --prefix="$prefix" --libdir=lib \
	-Dtests=false -Ddocumentation=false
meson compile -C /work/wayland/build -j 16
meson install -C /work/wayland/build
rm -rf /work/wayland
source_dir wayland-protocols
meson setup /work/wayland-protocols/build /work/wayland-protocols/source \
	--prefix="$prefix" --libdir=lib -Dtests=false
meson install -C /work/wayland-protocols/build
rm -rf /work/wayland-protocols
source_dir libdrm
meson setup /work/libdrm/build /work/libdrm/source --prefix="$prefix" --libdir=lib \
	-Dtests=false -Dman-pages=disabled
meson compile -C /work/libdrm/build -j 16
meson install -C /work/libdrm/build
rm -rf /work/libdrm
