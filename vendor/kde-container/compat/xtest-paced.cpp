// SPDX-License-Identifier: GPL-2.0-or-later
// Scoped to software Wayland capture: preserve fast modifier transitions when
// x11vnc injects into KWin's private X11 parent. Frame/pointer delivery is
// untouched.
#include <X11/Xlib.h>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>

extern "C" Bool XTestFakeKeyEvent(Display *display, unsigned int keycode,
                                  Bool pressed, unsigned long delay) {
  using Function = Bool (*)(Display *, unsigned int, Bool, unsigned long);
  static const auto original =
      reinterpret_cast<Function>(dlsym(RTLD_NEXT, "XTestFakeKeyEvent"));
  if (!original) {
    std::fputs("Plasma VNC: cannot resolve XTestFakeKeyEvent\n", stderr);
    std::abort();
  }
  // Let the X server advance between key edges, including Shift/Ctrl chords.
  // Five milliseconds also covers coarse timer ticks and busy rootless guests.
  return original(display, keycode, pressed, delay < 5 ? 5 : delay);
}
