// SPDX-License-Identifier: GPL-2.0-or-later
// Keep regression assertions active in the production Release build.
#undef NDEBUG
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include <FL/Fl.H>
#include <FL/Fl_Window.H>
#include <FL/x.H>

#include "CConn.h"
#include "Viewport.h"
#include "parameters.h"

struct ViewerPointerEvent {
  ViewerPointerEvent(const core::Point &value, uint16_t mask)
    : position(value), buttons(mask) {}
  core::Point position;
  uint16_t buttons;
};

class CaptureViewport final : public Viewport {
public:
  using Viewport::Viewport;
  std::vector<ViewerPointerEvent> events;

protected:
  void sendPointerEvent(const core::Point &position,
                        uint16_t buttons) override {
    events.push_back({position, buttons});
  }
};

int main() {
  const char *display = std::getenv("NATIVE_TEST_DISPLAY");
  assert(display && *display);
  assert(setenv("DISPLAY", display, 1) == 0);
  fl_open_display();
  CConn connection;
  connection.server.setDimensions(640, 480);
  emulateMiddleButton.setParam(false);
  viewOnly.setParam(false);
  Fl_Window window(720, 520);
  auto *viewport = new CaptureViewport(640, 480, &connection);
  viewport->position(40, 20);
  window.end();

  struct Case {
    int x, y, state, expected_x, expected_y, buttons;
  };
  for (const Case test :
       {Case{168, 116, 0, 128, 96, 0}, Case{40, 20, FL_BUTTON1, 0, 0, 1},
        Case{168, 116, FL_BUTTON2, 128, 96, 2},
        Case{168, 116, FL_BUTTON3, 128, 96, 4},
        Case{168, 116, FL_BUTTON1 | FL_BUTTON3, 128, 96, 5},
        Case{168, 116, FL_SHIFT | FL_BUTTON1 | FL_BUTTON2 | FL_BUTTON3, 128, 96,
             7}}) {
    viewport->events.clear();
    Fl::e_x = test.x;
    Fl::e_y = test.y;
    Fl::e_state = test.state;
    std::printf(
        "FL_ENTER native(%d,%d) viewport(40,20) expected(%d,%d) buttons=%d\n",
        test.x, test.y, test.expected_x, test.expected_y, test.buttons);
    std::fflush(stdout);
    assert(viewport->handle(FL_ENTER) == 1);
    assert(viewport->events.size() == 1);
    assert(viewport->events.front().position.x == test.expected_x);
    assert(viewport->events.front().position.y == test.expected_y);
    assert(viewport->events.front().buttons == test.buttons);
  }
  std::puts("PASS: real Viewport FL_ENTER coordinates and held-button state");
}
