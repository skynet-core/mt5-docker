// SPDX-License-Identifier: GPL-2.0-or-later
#include "fake-wayland.h"
#include <QApplication>
#include <QTemporaryDir>
#include <cassert>
#include <cstdlib>

QString platform;
QString QGuiApplication::platformName() { return platform; }
extern "C" void __gcov_dump();
extern "C" [[noreturn]] void abort() noexcept {
  __gcov_dump();
  _Exit(99);
}

#include <KPluginFactory>
// Analysis without KDE headers still checks the actual input implementation.
#ifndef K_PLUGIN_CLASS
#define K_PLUGIN_CLASS(...)
#endif
#include "../../vendor/kde-container/native/kwin-input.cpp"

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string selected = argv[1];
  QTemporaryDir runtime;
  assert(runtime.isValid());
  qputenv("XDG_RUNTIME_DIR", runtime.path().toUtf8());
  qputenv("QT_QPA_PLATFORM", "offscreen");
  qputenv("QT_QPA_PLATFORMTHEME", "generic");
  qputenv("DBUS_SESSION_BUS_ADDRESS",
          "unix:path=/nonexistent-private-input-unit-bus");
  qunsetenv("DISPLAY");
  qunsetenv("WAYLAND_DISPLAY");
  qunsetenv("XAUTHORITY");
  QApplication app(argc, argv);
  qInstallMessageHandler(
      [](QtMsgType, const QMessageLogContext &, const QString &message) {
        std::fprintf(stderr, "%s\n", qPrintable(message));
      });
  const bool legacy =
      selected == "normal-v5" || selected == "keymap-unavailable";
  fake::reset(selected, legacy ? 5 : 6);
  const fake::Request emptyRequest;
  assert(emptyRequest.opcode == 0 && emptyRequest.interface.empty() &&
         emptyRequest.values.empty() && emptyRequest.strings.empty());
  KwinEventsPlugin plugin(nullptr, {});
  qputenv("PLASMA_KWIN_INPUT", "0");
  platform = "wayland";
  assert(!plugin.eventHandler());
  qputenv("PLASMA_KWIN_INPUT", "1");
  platform = "xcb";
  assert(!plugin.eventHandler());
  platform = "wayland";
  auto input = std::unique_ptr<EventHandler>(plugin.eventHandler());
  assert(input);
  fake::global("wl_seat", 1);
  fake::capabilities(0);
  fake::capabilities(WL_SEAT_CAPABILITY_KEYBOARD);
  if (selected == "dispatch-failed") {
    fake::notify();
    for (int i = 0; i < 5; ++i)
      QCoreApplication::processEvents();
    assert(false);
  }
  input->handleKeyboard(true, 'a');
  input->handleKeyboard(false, 'a');
  input->handleKeyboard(false, 'a');
  input->handleKeyboard(true, 0xffe3); // Control_L
  input->handleKeyboard(true, 's');
  input->handleKeyboard(false, 's');
  input->handleKeyboard(false, 0xffe3);
  if (legacy) {
    fake::keyboardEvents();
    fake::keymap("valid");
    fake::keymap("wrong-format");
    fake::keymap("empty");
    fake::keymap("bad-fd");
    fake::keymap("invalid");
    input->handleKeyboard(true, 0xdeadbeef);
  }
  for (unsigned mask : {0u, 1u, 1u, 3u, 4u, 8u, 16u, 32u, 64u, 128u, 256u, 0u})
    input->handlePointer(int(mask), 123, 234);
  bool authenticated = false;
  unsigned pointers = 0;
  std::vector<std::vector<int64_t>> keys, buttons, axes;
  for (const auto &request : fake::requests()) {
    if (request.opcode == ORG_KDE_KWIN_FAKE_INPUT_AUTHENTICATE) {
      assert(request.strings.at(0) == "Plasma VNC");
      authenticated = true;
    } else if (request.opcode ==
               (legacy ? ORG_KDE_KWIN_FAKE_INPUT_KEYBOARD_KEY
                       : ORG_KDE_KWIN_FAKE_INPUT_KEYBOARD_KEYSYM)) {
      keys.push_back(request.values);
    } else if (request.opcode ==
               ORG_KDE_KWIN_FAKE_INPUT_POINTER_MOTION_ABSOLUTE) {
      assert(request.values == std::vector<int64_t>({123 * 256, 234 * 256}));
      ++pointers;
    } else if (request.opcode == ORG_KDE_KWIN_FAKE_INPUT_BUTTON) {
      buttons.push_back(request.values);
    } else if (request.opcode == ORG_KDE_KWIN_FAKE_INPUT_AXIS) {
      axes.push_back(request.values);
    }
  }
  const int64_t a = legacy ? 30 : 'a', control = legacy ? 29 : 0xffe3,
                s = legacy ? 31 : 's';
  assert(keys == std::vector<std::vector<int64_t>>({{a, 1},
                                                    {a, 0},
                                                    {a, 0},
                                                    {control, 1},
                                                    {s, 1},
                                                    {s, 0},
                                                    {control, 0}}));
  assert(buttons == std::vector<std::vector<int64_t>>({{272, 1},
                                                       {274, 1},
                                                       {272, 0},
                                                       {274, 0},
                                                       {273, 1},
                                                       {273, 0},
                                                       {275, 1},
                                                       {275, 0},
                                                       {276, 1},
                                                       {276, 0}}));
  assert(axes ==
         std::vector<std::vector<int64_t>>(
             {{0, -15 * 256}, {0, 15 * 256}, {1, -15 * 256}, {1, 15 * 256}}));
  assert(authenticated && pointers == 12);
  input.reset();
}
