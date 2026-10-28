#!/usr/bin/env bash
# Sourced by build/kwin-6.8.2 inside its matching cached build stage.
: "${prefix:?Sourced through build/kwin-6.8.2}"
# KWin 6.8 publishes a new output registry which distro KScreen cannot discover.
build_kde libkscreen
test -f "$prefix/lib/libKF6Screen.so.6.8.2"
"$prefix/bin/kscreen-doctor" --help >/dev/null
install -d /out/root/opt
cp -a "$prefix" /out/root/opt/
runtime=/out/root$prefix
rm -rf "$runtime/include" "$runtime/lib/cmake" "$runtime/lib/pkgconfig" \
	"$runtime/mkspecs" "$runtime/doc" "$runtime/share/doc" \
	"$runtime/share/man" "$runtime/modules" "$runtime/share/ECM" \
	"$runtime/share/wayland-protocols" "$runtime/share/plasma-wayland-protocols" \
	"$runtime/share/vulkan/registry"
find "$runtime" -type f \( -name '*.a' -o -name '*.prl' \) -delete
# Ubuntu ships a newer display-info SONAME; keep this compositor's ABI private.
cp -L /usr/lib/x86_64-linux-gnu/libdisplay-info.so.2 "$runtime/lib/libdisplay-info.so.2"
test -x "$runtime/bin/kwin_wayland"
test -x "$runtime/bin/kwin_wayland_wrapper"
test -x "$runtime/bin/kbuildsycoca6"
test -x "$runtime/bin/kscreen-doctor"
[[ -z $(getcap "$runtime/bin/kwin_wayland") ]]
