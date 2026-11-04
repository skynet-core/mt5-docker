// SPDX-License-Identifier: GPL-2.0-or-later
// Keep x11vnc input responsive while transfer-rate calibration polls RFB.
#include <X11/Xlib.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
  // KWin's private X11 input backend needs distinct Shift/Ctrl transitions.
  const char *software = std::getenv("PLASMA_SOFTWARE_CAPTURE");
  return original(
      display, keycode, pressed,
      software && std::strcmp(software, "1") == 0 && delay < 5 ? 5 : delay);
}

extern "C" Bool XTestFakeButtonEvent(Display *display, unsigned int button,
                                     Bool pressed, unsigned long delay) {
  using Function = Bool (*)(Display *, unsigned int, Bool, unsigned long);
  static const auto original =
      reinterpret_cast<Function>(dlsym(RTLD_NEXT, "XTestFakeButtonEvent"));
  if (!original) {
    std::fputs("Plasma VNC: cannot resolve XTestFakeButtonEvent\n", stderr);
    std::abort();
  }
  const Bool result = original(display, button, pressed, delay);
  if (result)
    XFlush(display);
  return result;
}

extern "C" Bool XTestFakeMotionEvent(Display *display, int screen, int x, int y,
                                     unsigned long delay) {
  using Function = Bool (*)(Display *, int, int, int, unsigned long);
  static const auto original =
      reinterpret_cast<Function>(dlsym(RTLD_NEXT, "XTestFakeMotionEvent"));
  if (!original) {
    std::fputs("Plasma VNC: cannot resolve XTestFakeMotionEvent\n", stderr);
    std::abort();
  }
  const Bool result = original(display, screen, x, y, delay);
  if (result)
    XFlush(display);
  return result;
}
