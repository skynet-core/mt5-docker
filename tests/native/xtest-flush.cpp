// SPDX-License-Identifier: GPL-2.0-or-later
#include <X11/Xlib.h>
#include <X11/extensions/XTest.h>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <thread>

int main() {
  const char *name = std::getenv("NATIVE_TEST_DISPLAY");
  assert(name && *name);
  Display *sender = XOpenDisplay(name), *receiver = XOpenDisplay(name);
  assert(sender && receiver);
  Window root = DefaultRootWindow(receiver);
  XSetWindowAttributes attributes{};
  attributes.override_redirect = True;
  attributes.event_mask =
      PointerMotionMask | ButtonPressMask | ButtonReleaseMask;
  Window window = XCreateWindow(receiver, root, 0, 0, 800, 600, 0,
                                CopyFromParent, InputOutput, CopyFromParent,
                                CWOverrideRedirect | CWEventMask, &attributes);
  XMapRaised(receiver, window);
  XSync(receiver, False);
  int event, error, major, minor;
  assert(XTestQueryExtension(sender, &event, &error, &major, &minor));
  assert(XTestFakeMotionEvent(sender, -1, 50, 50, CurrentTime));
  XSync(sender, False);
  XSync(receiver, False);
  while (XPending(receiver)) {
    XEvent ignored;
    XNextEvent(receiver, &ignored);
  }

  // The sender only injects then waits, as in x11vnc's calibration loop.
  assert(XTestFakeMotionEvent(sender, -1, 600, 400, CurrentTime));
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  assert(XTestFakeButtonEvent(sender, 1, True, CurrentTime));
  std::this_thread::sleep_for(std::chrono::milliseconds(150));
  for (int step = 1; step <= 15; ++step) {
    assert(XTestFakeMotionEvent(sender, -1, 600 - 10 * step,
                                400 + std::lround(-63.0 * step / 15),
                                CurrentTime));
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
  }
  assert(XTestFakeButtonEvent(sender, 1, False, CurrentTime));

  int pressed = 0, released = 0, held = 0;
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (std::chrono::steady_clock::now() < deadline && !released) {
    while (XPending(receiver)) {
      XEvent input;
      XNextEvent(receiver, &input);
      if (input.type == ButtonPress) {
        assert(input.xbutton.button == 1);
        ++pressed;
      } else if (input.type == ButtonRelease) {
        assert(input.xbutton.button == 1);
        ++released;
      } else if (input.type == MotionNotify &&
                 input.xmotion.state & Button1Mask) {
        ++held;
        assert(input.xmotion.x == 600 - 10 * held);
        assert(input.xmotion.y == 400 + std::lround(-63.0 * held / 15));
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  Window queryRoot, child;
  int x, y, localX, localY;
  unsigned mask;
  assert(XQueryPointer(receiver, root, &queryRoot, &child, &x, &y, &localX,
                       &localY, &mask));
  const bool delivered = pressed == 1 && released == 1 && held == 15 &&
                         x == 450 && y == 337 && !(mask & Button1Mask);
  std::fprintf(stderr,
               "XTest calibration input: press=%d held=%d release=%d "
               "pointer=%d,%d mask=%u\n",
               pressed, held, released, x, y, mask);
  XCloseDisplay(sender);
  XDestroyWindow(receiver, window);
  XCloseDisplay(receiver);
  return delivered ? 0 : 1;
}
