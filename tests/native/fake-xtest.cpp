// SPDX-License-Identifier: GPL-2.0-or-later
#include <X11/Xlib.h>
#include <cstdint>

namespace {
unsigned long fields[8];
unsigned long flushes;
} // namespace
extern "C" Bool XTestFakeKeyEvent(Display *display, unsigned code, Bool pressed,
                                  unsigned long delay) {
  fields[0] = code;
  fields[1] = pressed;
  fields[2] = delay;
  fields[3] = reinterpret_cast<std::uintptr_t>(display);
  return pressed;
}
extern "C" Bool XTestFakeButtonEvent(Display *display, unsigned button,
                                     Bool pressed, unsigned long delay) {
  fields[0] = button;
  fields[1] = pressed;
  fields[2] = delay;
  fields[3] = reinterpret_cast<std::uintptr_t>(display);
  return pressed;
}
extern "C" Bool XTestFakeMotionEvent(Display *display, int screen, int x, int y,
                                     unsigned long delay) {
  fields[0] = screen;
  fields[1] = x;
  fields[2] = y;
  fields[3] = reinterpret_cast<std::uintptr_t>(display);
  fields[4] = delay;
  return x >= 0;
}
extern "C" int XFlush(Display *display) {
  fields[5] = reinterpret_cast<std::uintptr_t>(display);
  fields[6] = ++flushes;
  return 0;
}
extern "C" unsigned long observed(unsigned field) { return fields[field]; }
