// SPDX-License-Identifier: GPL-2.0-or-later
// The UI is driven through actual Qt key/button events on an offscreen display.
#include <QApplication>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusUnixFileDescriptor>
#include <QFile>
#include <QImage>
#include <QLabel>
#include <QPushButton>
#include <QScreen>
#include <QTemporaryDir>
#include <QTemporaryFile>
#include <QTest>
#include <QTextEdit>
#include <QTimer>
#include <cassert>
#include <functional>
#include <unistd.h>

QString scenario;
std::function<void()> driver;
bool applicationReady = false;

class FixtureApplication : public QApplication {
public:
  FixtureApplication(int &argc, char **argv) : QApplication(argc, argv) {
    applicationReady = true;
    if (driver)
      QTimer::singleShot(30, driver);
  }
};

// These dependency responses exercise the capture code's trust boundaries.
QScreen *QGuiApplication::primaryScreen() {
  return applicationReady && scenario == "capture-no-screen"
             ? nullptr
             : screens().value(0);
}

class ControlledTemporaryFile : public QTemporaryFile {
public:
  bool open() {
    return scenario != "capture-temp-failed" && QTemporaryFile::open();
  }
};

class ControlledGuiApplication : public QGuiApplication {
public:
  static QString platformName() {
    return scenario == "capture-cursor-x11" ? "xcb"
                                            : QGuiApplication::platformName();
  }
};

QDBusMessage QDBusConnection::call(const QDBusMessage &message, QDBus::CallMode,
                                   int) const {
  if (message.interface() != "org.kde.KWin.ScreenShot2" ||
      message.member() != "CaptureArea")
    return message.createErrorReply("org.freedesktop.DBus.Error.ServiceUnknown",
                                    "Private unit bus");
  if (scenario == "capture-error")
    return message.createErrorReply("org.kde.Test.Denied", "Synthetic denial");
  const auto arguments = message.arguments();
  assert(arguments.size() == 6);
  assert(arguments.at(4).toMap().value("include-cursor").toBool() ==
         (scenario == "capture-cursor"));
  auto descriptor = arguments.at(5).value<QDBusUnixFileDescriptor>();
  QImage pixels(64, 48, QImage::Format_ARGB32);
  pixels.fill(QColor(18, 210, 178));
  if (scenario != "capture-incomplete")
    assert(write(descriptor.fileDescriptor(), pixels.constBits(),
                 pixels.sizeInBytes()) == pixels.sizeInBytes());
  const QVariantMap metadata{
      {"width", scenario == "capture-invalid-size" ? -1 : pixels.width()},
      {"height", pixels.height()},
      {"stride", pixels.bytesPerLine()},
      {"format", int(pixels.format())}};
  return message.createReply(QVariantList{metadata});
}

#define QApplication FixtureApplication
#define QTemporaryFile ControlledTemporaryFile
#define QGuiApplication ControlledGuiApplication
#define main fixture_application_main
#include "../../vendor/kde-container/native/ui-fixture.cpp"
#undef main
#undef QApplication
#undef QTemporaryFile
#undef QGuiApplication

QPushButton *button(QWidget *window, const QString &text) {
  for (auto *candidate : window->findChildren<QPushButton *>())
    if (candidate->text().startsWith(text))
      return candidate;
  assert(false);
  return nullptr;
}

bool status(QWidget *window, const QString &text) {
  for (auto *label : window->findChildren<QLabel *>())
    if (label->text().startsWith(text))
      return true;
  return false;
}

int main(int argc, char **argv) {
  assert(argc == 2);
  scenario = QString::fromLocal8Bit(argv[1]);
  QTemporaryDir temporary;
  assert(temporary.isValid());
  qputenv("XDG_RUNTIME_DIR", temporary.path().toUtf8());
  qputenv("DBUS_SESSION_BUS_ADDRESS",
          "unix:path=/nonexistent-private-native-unit-bus");
  qputenv("QT_QPA_PLATFORM", scenario == "capture-x11" ? "xcb" : "offscreen");
  qputenv("QT_QPA_PLATFORMTHEME", "generic");
  if (scenario == "capture-x11") {
    // The caller must provide a private Xvfb; never discover a user display.
    const QByteArray display = qgetenv("NATIVE_TEST_DISPLAY");
    if (display.isEmpty())
      return 77;
    qputenv("DISPLAY", display);
  } else {
    qunsetenv("DISPLAY");
    qunsetenv("WAYLAND_DISPLAY");
    qunsetenv("XAUTHORITY");
  }
  const bool invalidPath = scenario.endsWith("save-failed");
  const QString output =
      temporary.path() + (invalidPath ? "/missing/output.png" : "/output.png");
  QByteArray program("fixture-unit"), destination = output.toUtf8();
  QByteArray mode = scenario.startsWith("capture-") ? "--capture-output"
                    : scenario == "publish-image"   ? "--publish-image"
                                                    : "--images";
  if (scenario.startsWith("capture-cursor"))
    mode = "--capture-output-with-cursor";
  if (!scenario.startsWith("capture-")) {
    driver = [&] {
      auto *window = QApplication::topLevelWidgets().value(0);
      assert(window);
      window->activateWindow();
      if (scenario.startsWith("text")) {
        auto *editor = window->findChild<QTextEdit *>();
        assert(editor);
        editor->setFocus();
        QTest::keyClicks(editor, "Native AaZz 0123!?");
        QTest::mouseClick(button(window, "Save to host"), Qt::LeftButton);
        if (invalidPath)
          assert(status(window, "Save failed"));
        else {
          QFile file(output);
          assert(file.open(QIODevice::ReadOnly));
          assert(file.readAll() == "Native AaZz 0123!?");
          file.close();
          QTest::keyClicks(editor, " keyboard");
          QTest::keyClick(editor, Qt::Key_S, Qt::ControlModifier);
          QTest::qWait(20);
          assert(file.open(QIODevice::ReadOnly));
          assert(file.readAll() == "Native AaZz 0123!? keyboard");
        }
      } else {
        QApplication::clipboard()->clear();
        QTest::mouseClick(button(window, "Paste image"), Qt::LeftButton);
        assert(status(window, "No image on clipboard"));
        QTest::mouseClick(button(window, "Save pasted"), Qt::LeftButton);
        assert(status(window, "Image save failed"));
        QTest::mouseClick(button(window, "Copy 4K"), Qt::LeftButton);
        if (scenario == "publish-image")
          assert(!window->isVisible());
        else {
          QTest::mouseClick(button(window, "Paste image"), Qt::LeftButton);
          assert(status(window, "Image pasted"));
          QTest::mouseClick(button(window, "Save pasted"), Qt::LeftButton);
          assert(status(window, invalidPath ? "Image save failed"
                                            : "Image saved to host"));
          if (!invalidPath) {
            QImage saved(output);
            assert(saved.size() == QSize(3840, 2160));
            assert(saved.pixelColor(3000, 1500).alpha() == 128);
          }
        }
      }
      QApplication::quit();
    };
  }
  char *parameters[]{program.data(), destination.data(), mode.data(), nullptr};
  int arguments = 3;
  if (scenario == "invalid-args")
    arguments = 1;
  if (scenario.startsWith("text"))
    arguments = 2;
  if (scenario.startsWith("capture-"))
    std::swap(parameters[1], parameters[2]);
  QTemporaryFile metadata;
  assert(metadata.open());
  const int saved = dup(STDOUT_FILENO);
  assert(saved >= 0 && dup2(metadata.handle(), STDOUT_FILENO) >= 0);
  const int result = fixture_application_main(arguments, parameters);
  std::fflush(stdout);
  assert(dup2(saved, STDOUT_FILENO) >= 0);
  close(saved);
  const bool success = scenario == "capture-ok" || scenario == "capture-x11" ||
                       scenario == "capture-cursor" ||
                       !scenario.startsWith("capture-");
  assert(result == (scenario == "invalid-args" ? 2 : success ? 0 : 1));
  if (scenario == "capture-ok" || scenario == "capture-cursor") {
    assert(!QImage(output).isNull());
    assert(metadata.seek(0));
    const auto object = QJsonDocument::fromJson(metadata.readAll()).object();
    assert(object.value("method") == "kwin-screenshot2");
    assert(object.contains("include_cursor") == (scenario == "capture-cursor"));
    assert(object.value("include_cursor").toBool() ==
           (scenario == "capture-cursor"));
  }
  return 0;
}
