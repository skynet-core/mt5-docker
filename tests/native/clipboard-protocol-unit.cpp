// SPDX-License-Identifier: GPL-2.0-or-later
// Real endpoint/controller code is exercised against the Wayland C API
// boundary.
#include "fake-wayland.h"
#include <QCoreApplication>
#include <QGuiApplication>
#include <QProcess>
#include <QTemporaryDir>
#include <QTimer>
#include <cassert>
#include <csignal>
#include <functional>
#include <unistd.h>

QString scenario;
std::function<void()> startup;
std::vector<QByteArray> forwarded;
extern "C" void __gcov_dump();
extern "C" void __gcov_reset();

class ControlledProcess : public QProcess {
public:
  void start(const QString &program, const QStringList &arguments,
             OpenMode mode = ReadWrite) {
    QProcess::start(scenario == "bridge-start-failed"
                        ? "/nonexistent-native-clipboard-worker"
                        : program,
                    arguments, mode);
  }

protected:
  qint64 writeData(const char *data, qint64 size) override {
    forwarded.emplace_back(data, size);
    return scenario == "bridge-write-failed" ? -1
                                             : QProcess::writeData(data, size);
  }
};
#define QProcess ControlledProcess
#define main clipboard_application_main
#include "../../vendor/kde-container/native/clipboard.cpp"
#undef main
#undef QProcess

void installStartup() {
  if (startup)
    QTimer::singleShot(0, startup);
}

void privateEnvironment(const QString &runtime) {
  qputenv("XDG_RUNTIME_DIR", runtime.toUtf8());
  qputenv("QT_QPA_PLATFORM", "offscreen");
  qputenv("QT_QPA_PLATFORMTHEME", "generic");
  qputenv("DBUS_SESSION_BUS_ADDRESS",
          "unix:path=/nonexistent-private-clipboard-unit-bus");
  qunsetenv("DISPLAY");
  qunsetenv("XAUTHORITY");
}

int main(int argc, char **argv) {
  std::signal(SIGPIPE, SIG_IGN);
  QTemporaryDir runtime;
  assert(runtime.isValid());
  privateEnvironment(runtime.path());
  const bool worker = argc == 3 && std::strcmp(argv[1], "--endpoint") == 0;
  scenario = worker ? qEnvironmentVariable("NATIVE_CLIPBOARD_SCENARIO")
                    : QString::fromLocal8Bit(argv[1]);
  const unsigned protocol = scenario == "wlr-v1"             ? 1
                            : scenario == "wlr-v2"           ? 2
                            : scenario == "prefer-ext-first" ? 5
                                                             : 4;
  fake::reset("clipboard-" + scenario.toStdString(), protocol);
  if (worker) {
    if (scenario == "bridge-invalid-message") {
      std::puts("{\"mode\":9,\"data\":{}}");
      std::fflush(stdout);
      sleep(1);
      return 0;
    }
    startup = [] {
      if (scenario == "bridge-worker-zero")
        QTimer::singleShot(30, [] { QCoreApplication::quit(); });
      QTimer::singleShot(200, [] {
        __gcov_dump();
        __gcov_reset();
      });
    };
    qAddPreRoutine(installStartup);
    const int result = clipboard_application_main(argc, argv);
    fake::reset("done");
    return result;
  }
  if (scenario.startsWith("bridge")) {
    qputenv("NATIVE_CLIPBOARD_SCENARIO", scenario.toUtf8());
    qputenv("DISPLAY_BACKEND", "wayland");
    qputenv("HOST_DISPLAY_BACKEND", "wayland");
    qputenv("HOST_WAYLAND_DISPLAY", "/private/unit-host");
    qputenv("WAYLAND_DISPLAY", "/private/unit-child");
    qputenv("HOST_DISPLAY", ":4242");
    startup = [] { QTimer::singleShot(700, [] { QCoreApplication::quit(); }); };
    qAddPreRoutine(installStartup);
    int count = 1;
    char *parameters[]{argv[0], nullptr};
    const int result = clipboard_application_main(count, parameters);
    assert(result ==
           (scenario == "bridge" || scenario == "bridge-fallback" ? 0 : 1));
    if (scenario == "bridge" || scenario == "bridge-fallback") {
      int host = 0, child = 0;
      std::array<QByteArray, 2> previous;
      for (const auto &bytes : forwarded) {
        const auto object = QJsonDocument::fromJson(bytes).object();
        const int mode = object.value("mode").toInt();
        const auto data = QJsonDocument(object.value("data").toObject())
                              .toJson(QJsonDocument::Compact);
        assert(previous.at(mode) != data);
        previous.at(mode) = data;
        const auto encoded = object.value("data")
                                 .toObject()
                                 .value("text/plain")
                                 .toString()
                                 .toLatin1();
        const auto text = QByteArray::fromBase64(encoded);
        host += text == "private-host";
        child += text == "private-child";
      }
      std::fprintf(stderr, "Bridge payloads: host=%d child=%d\n", host, child);
      assert(child >= 1 && child <= 2);
      if (scenario == "bridge")
        assert(host >= 1 && host <= 2);
    }
    fake::reset("done");
    return 0;
  }
  if (scenario.startsWith("endpoint") || scenario.startsWith("read-") ||
      scenario == "stdin-failed") {
    const bool x11 = scenario == "endpoint-x11";
    int descriptors[2];
    assert(pipe(descriptors) == 0);
    assert(dup2(descriptors[0], STDIN_FILENO) >= 0);
    close(descriptors[0]);
    const auto input = frame(
        {{"mode", 0}, {"data", QJsonObject{{"text/plain", "dW5pdCB0ZXh0"}}}});
    assert(write(descriptors[1], input.constData(), input.size()) ==
           input.size());
    close(descriptors[1]);
    QByteArray option("--endpoint"), backend(x11 ? "x11" : "wayland");
    char *parameters[]{argv[0], option.data(), backend.data(), nullptr};
    int count = 3;
    const int result = clipboard_application_main(count, parameters);
    assert(result == 0);
    fake::reset("done");
    return 0;
  }
  QCoreApplication app(argc, argv);
  {
    WaylandEndpoint endpoint;
    if (scenario == "finished")
      fake::finished();
    if (scenario == "dispatch-failed") {
      fake::notify();
      QCoreApplication::processEvents();
      assert(false);
    }
    if (scenario == "unknown-offer")
      fake::offer(0, {{"text/plain", "unknown"}}, false);
    if (scenario == "pipe-failed")
      fake::offer(0, {{"text/plain", "test"}});
    if (scenario == "oversize-offer")
      fake::offer(0, {{"application/x-bytes", std::string(maxBytes + 1, 'x')}});
    else {
      fake::offer(0, {{"text/plain", "Unicode \xce\xbb"}, {"TARGETS", "bad"}});
      if (protocol >= 2)
        fake::offer(1, {{"text/plain;charset=utf-8", "primary"}});
      endpoint.set(0, {{"text/plain", "source content"}});
      assert(fake::request(0, "text/plain") == "source content");
      assert(fake::request(0, "application/x-missing").empty());
      fake::request(0, "text/plain", true);
      fake::offer(0, {{"text/plain", "self offer must not be read"}});
      fake::cancel(0);
      endpoint.set(0, {});
      endpoint.set(1, {{"text/plain", "primary source"}});
      if (protocol >= 2) {
        assert(fake::request(1, "text/plain") == "primary source");
        fake::cancel(1);
      }
      endpoint.set(1, {});
    }
  }
  fake::reset("done");
  return 0;
}
