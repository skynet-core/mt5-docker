// SPDX-License-Identifier: GPL-2.0-or-later
#include <X11/Xlib.h>

namespace {
unsigned long observedDelay;
unsigned observedCode;
Bool observedPressed;
} // namespace
extern "C" Bool XTestFakeKeyEvent(Display *, unsigned code, Bool pressed,
                                  unsigned long delay) {
  observedCode = code;
  observedPressed = pressed;
  observedDelay = delay;
  return pressed;
}
extern "C" unsigned long observed(unsigned field) {
  return field == 0   ? observedCode
         : field == 1 ? observedPressed
                      : observedDelay;
}
