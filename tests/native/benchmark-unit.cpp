// SPDX-License-Identifier: GPL-2.0-or-later
#include <QApplication>
#include <QTemporaryDir>
#include <QTest>
#include <QTimer>
#include <cassert>

class BenchmarkApplication : public QApplication {
public:
  BenchmarkApplication(int &argc, char **argv) : QApplication(argc, argv) {
    QTimer::singleShot(80, [] {
      auto *window = QApplication::topLevelWidgets().value(0);
      assert(window);
      QTest::keyClick(window, Qt::Key_A);
      QKeyEvent repeat(QEvent::KeyPress, Qt::Key_A, Qt::NoModifier, "a", true);
      QApplication::sendEvent(window, &repeat);
      QTest::keyClick(window, Qt::Key_X);
      QTest::keyClick(window, Qt::Key_P);
      assert(!window->grab().isNull());
      QApplication::quit();
    });
  }
};

#define QApplication BenchmarkApplication
#define main benchmark_application_main
#include "../../vendor/kde-container/native/vnc-benchmark.cpp"
#undef main
#undef QApplication

int main(int argc, char **argv) {
  QTemporaryDir runtime;
  assert(runtime.isValid());
  qputenv("XDG_RUNTIME_DIR", runtime.path().toUtf8());
  qputenv("QT_QPA_PLATFORM", "offscreen");
  qputenv("QT_QPA_PLATFORMTHEME", "generic");
  qputenv("DBUS_SESSION_BUS_ADDRESS",
          "unix:path=/nonexistent-private-native-unit-bus");
  qunsetenv("DISPLAY");
  qunsetenv("WAYLAND_DISPLAY");
  qunsetenv("XAUTHORITY");
  return benchmark_application_main(argc, argv);
}
