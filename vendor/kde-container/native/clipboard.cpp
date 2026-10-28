// SPDX-License-Identifier: GPL-2.0-or-later
// Separate display connections keep clipboard ownership independent of focus.
#include "ext-data-control-v1-client.h"
#include "wlr-data-control-unstable-v1-client.h"
#include <QByteArray>
#include <QClipboard>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMimeData>
#include <QObject>
#include <QProcess>
#include <QProcessEnvironment>
#include <QSocketNotifier>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QtTypes>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <functional>
#include <map>
#include <memory>
#include <qcontainerfwd.h>
#include <qtenvironmentvariables.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/poll.h>
#include <unistd.h>
#include <utility>
#include <wayland-client-core.h>
#include <wayland-client-protocol.h>
#include <wayland-util.h>

namespace {
using Data = std::map<QString, QByteArray>;
constexpr qsizetype maxBytes = 64LL * 1024 * 1024;
constexpr qsizetype maxWireBytes = maxBytes * 2;
bool sessionLocal(const QString &type) {
  return type == "application/vnd.portal.filetransfer" ||
         type == "application/x-kde-source-id" ||
         type == "application/x-kde-onlyReplaceEmpty" ||
         // Old clipboard history may retain the bridge's former UUID marker.
         type == "application/x-plasma6-clipboard-owner";
}

QStringList transferFormats(const QStringList &formats) {
  QStringList result;
  const bool png = formats.contains("image/png");
  for (const auto &type : formats) {
    if (sessionLocal(type))
      continue;
    // Qt expands one QImage into every installed image encoder. Fetching them
    // all can stall on codecs or exceed the selection limit for a screenshot.
    // PNG preserves pixels and alpha; retain explicit vector data separately.
    if (png && (type == "application/x-qt-image" ||
                (type.startsWith("image/") && type != "image/png" &&
                 type != "image/svg+xml")))
      continue;
    result.append(type);
  }
  return result;
}

[[noreturn]] void fail(const char *message, int status = 1) {
  std::fprintf(stderr, "Plasma clipboard: %s\n", message);
  std::exit(status);
}

bool transfer(int fd, QByteArray &bytes, bool writing) {
  if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0)
    return false;
  QElapsedTimer timer;
  timer.start();
  qsizetype offset = 0;
  while (timer.elapsed() < 5000) {
    if (writing && offset == bytes.size())
      return true;
    pollfd pending{fd, static_cast<short>(writing ? POLLOUT : POLLIN), 0};
    const int remaining = std::max(0, 5000 - static_cast<int>(timer.elapsed()));
    const int ready = poll(&pending, 1, remaining);
    if (ready < 0 && errno == EINTR)
      continue;
    if (ready <= 0)
      return false;
    if (writing) {
      const auto count = write(fd, bytes.constData() + offset,
                               static_cast<size_t>(bytes.size() - offset));
      if (count > 0)
        offset += count;
      else if (count < 0 && errno != EINTR && errno != EAGAIN)
        return false;
    } else {
      std::array<char, 65536> block{};
      const auto count = read(fd, block.data(), block.size());
      if (count == 0)
        return true;
      if (count > 0) {
        if (bytes.size() + count > maxBytes)
          return false;
        bytes.append(block.data(), count);
      } else if (errno != EINTR && errno != EAGAIN) {
        return false;
      }
    }
  }
  return false;
}

void normalize(Data &data) {
  // Portal transfer keys belong to the source session bus, not the destination.
  for (auto it = data.begin(); it != data.end();) {
    if (sessionLocal(it->first))
      it = data.erase(it);
    else
      ++it;
  }
  const QString plain = QStringLiteral("text/plain");
  const QString utf8 = QStringLiteral("text/plain;charset=utf-8");
  if (data.count(plain) && !data.count(utf8))
    data[utf8] = data.at(plain);
  if (data.count(utf8) && !data.count(plain))
    data[plain] = data.at(utf8);
}

QByteArray frame(const QJsonObject &message) {
  return QJsonDocument(message).toJson(QJsonDocument::Compact) + '\n';
}

void consume(QByteArray &buffer, const QByteArray &input,
             const std::function<void(const QJsonObject &)> &handler) {
  buffer += input;
  if (buffer.size() > maxWireBytes)
    fail("clipboard message exceeds 128 MiB");
  qsizetype end;
  while ((end = buffer.indexOf('\n')) >= 0) {
    const auto message = QJsonDocument::fromJson(buffer.left(end));
    buffer.remove(0, end + 1);
    if (!message.isObject())
      fail("invalid clipboard message");
    handler(message.object());
  }
}

class Endpoint {
public:
  virtual ~Endpoint() = default;
  virtual void set(int mode, Data data) = 0;

  void publish(int mode, Data data) {
    const bool first = initial.at(mode);
    initial.at(mode) = false;
    normalize(data);
    QJsonObject formats;
    qsizetype size = 0;
    for (const auto &[type, bytes] : data) {
      size += bytes.size();
      if (size > maxBytes) {
        std::fputs("Plasma clipboard: selection exceeds 64 MiB; skipped\n",
                   stderr);
        return;
      }
      formats.insert(type, QString::fromLatin1(bytes.toBase64()));
    }
    const auto output =
        frame({{"mode", mode}, {"initial", first}, {"data", formats}});
    if (std::fwrite(output.constData(), 1, static_cast<size_t>(output.size()),
                    stdout) != static_cast<size_t>(output.size()) ||
        std::fflush(stdout) != 0)
      fail("clipboard controller disconnected");
  }

  void apply(const QJsonObject &message) {
    const int mode = message.value("mode").toInt(-1);
    if (mode < 0 || mode > 1 || !message.value("data").isObject())
      fail("invalid clipboard selection");
    Data data;
    const auto formats = message.value("data").toObject();
    qsizetype size = 0;
    for (auto it = formats.begin(); it != formats.end(); ++it) {
      const auto decoded = QByteArray::fromBase64Encoding(
          it.value().toString().toLatin1(),
          QByteArray::AbortOnBase64DecodingErrors);
      if (!decoded)
        fail("invalid clipboard encoding");
      size += decoded.decoded.size();
      if (size > maxBytes)
        fail("clipboard selection exceeds 64 MiB");
      data.emplace(it.key(), decoded.decoded);
    }
    normalize(data);
    initial.at(mode) = false;
    set(mode, std::move(data));
  }

protected:
  std::array<bool, 2> initial{true, true};
};

class X11Endpoint : public Endpoint {
public:
  X11Endpoint() : clipboard(QGuiApplication::clipboard()) {
    QObject::connect(clipboard, &QClipboard::changed, clipboard,
                     [this](QClipboard::Mode mode) { changed(mode); });
    QTimer::singleShot(0, clipboard, [this] {
      changed(QClipboard::Clipboard);
      changed(QClipboard::Selection);
    });
  }

  void set(int mode, Data data) override {
    auto mime = std::make_unique<QMimeData>();
    for (const auto &[type, bytes] : data)
      mime->setData(type, bytes);
    clipboard->setMimeData(mime.release(), static_cast<QClipboard::Mode>(mode));
  }

private:
  void changed(QClipboard::Mode mode) {
    if (mode != QClipboard::Clipboard && mode != QClipboard::Selection)
      return;
    if ((mode == QClipboard::Clipboard && clipboard->ownsClipboard()) ||
        (mode == QClipboard::Selection && clipboard->ownsSelection()))
      return;
    Data data;
    if (const auto *mime = clipboard->mimeData(mode)) {
      qsizetype size = 0;
      for (const auto &type : transferFormats(mime->formats())) {
        auto bytes = mime->data(type);
        size += bytes.size();
        if (size > maxBytes) {
          std::fputs("Plasma clipboard: selection exceeds 64 MiB; skipped\n",
                     stderr);
          return;
        }
        data[type] = std::move(bytes);
      }
    }
    publish(mode, std::move(data));
  }
  QClipboard *clipboard;
};

// These two protocols intentionally share request/event ordering. Use their
// generated interface tables, including the correct object names on the wire.
class WaylandEndpoint : public Endpoint {
  struct Protocol {
    const wl_interface *manager;
    const wl_interface *device;
    const wl_interface *source;
  } protocol{};
  struct Offer {
    explicit Offer(wl_proxy *object) : object(object) {}
    wl_proxy *object;
    QStringList formats;
    ~Offer() { destroy(object); }
  };
  struct Source {
    Source(WaylandEndpoint *owner, wl_proxy *object, int mode, Data data)
        : owner(owner), object(object), mode(mode), data(std::move(data)) {}
    WaylandEndpoint *owner;
    wl_proxy *object;
    int mode;
    Data data;
    ~Source() { destroy(object); }
  };
  wl_display *display = nullptr;
  wl_registry *registry = nullptr;
  wl_seat *seat = nullptr;
  wl_proxy *manager = nullptr;
  wl_proxy *device = nullptr;
  std::unique_ptr<QSocketNotifier> notifier;
  std::map<wl_proxy *, std::unique_ptr<Offer>> offers;
  std::array<std::unique_ptr<Source>, 2> sources;
  uint32_t managerName = 0;
  uint32_t managerVersion = 0;
  bool primary = false;

  static void destroy(wl_proxy *object) {
    wl_proxy_marshal_flags(object, 1, nullptr, wl_proxy_get_version(object),
                           WL_MARSHAL_FLAG_DESTROY);
  }
  static void announce(void *data, wl_registry *, uint32_t name,
                       const char *interface, uint32_t version) {
    auto *self = static_cast<WaylandEndpoint *>(data);
    if (std::strcmp(interface, "wl_seat") == 0 && !self->seat) {
      self->seat = static_cast<wl_seat *>(
          wl_registry_bind(self->registry, name, &wl_seat_interface, 1));
      static const wl_seat_listener listener{
          [](void *, wl_seat *, uint32_t) {},
          [](void *, wl_seat *, const char *) {}};
      wl_seat_add_listener(self->seat, &listener, nullptr);
    }
    if (std::strcmp(interface, "ext_data_control_manager_v1") == 0) {
      self->protocol = {&ext_data_control_manager_v1_interface,
                        &ext_data_control_device_v1_interface,
                        &ext_data_control_source_v1_interface};
      self->managerName = name;
      self->managerVersion = 1;
      self->primary = true;
    } else if (std::strcmp(interface, "zwlr_data_control_manager_v1") == 0 &&
               self->managerVersion == 0) {
      self->protocol = {&zwlr_data_control_manager_v1_interface,
                        &zwlr_data_control_device_v1_interface,
                        &zwlr_data_control_source_v1_interface};
      self->managerName = name;
      self->managerVersion = std::min(version, 2U);
      self->primary = version >= 2;
    }
  }
  static void offerFormat(void *data, wl_proxy *, const char *type) {
    static_cast<Offer *>(data)->formats.append(QString::fromUtf8(type));
  }
  static void offered(void *data, wl_proxy *, wl_proxy *object) {
    auto *self = static_cast<WaylandEndpoint *>(data);
    auto offer = std::make_unique<Offer>(object);
    static struct {
      decltype(&offerFormat) format;
    } listener{offerFormat};
    wl_proxy_add_listener(object, reinterpret_cast<void (**)(void)>(&listener),
                          offer.get());
    self->offers.emplace(object, std::move(offer));
  }
  QByteArray receive(Offer &offer, const QString &type, bool &success) {
    int descriptors[2];
    if (pipe2(descriptors, O_CLOEXEC) != 0)
      fail("cannot create clipboard transfer pipe");
    const auto name = type.toUtf8();
    wl_proxy_marshal_flags(offer.object, 0, nullptr,
                           wl_proxy_get_version(offer.object), 0,
                           name.constData(), descriptors[1]);
    close(descriptors[1]);
    wl_display_flush(display);
    QByteArray bytes;
    success = transfer(descriptors[0], bytes, false);
    close(descriptors[0]);
    return bytes;
  }
  void selected(int mode, wl_proxy *object) {
    std::unique_ptr<Offer> offer;
    if (object) {
      const auto it = offers.find(object);
      if (it == offers.end())
        fail("clipboard compositor sent an unknown offer");
      offer = std::move(it->second);
      offers.erase(it);
    }
    // Reading our own source here would block its send event on this
    // connection.
    if (sources.at(mode))
      return;
    Data data;
    if (offer) {
      bool success = true;
      qsizetype size = 0;
      for (const auto &type : transferFormats(offer->formats)) {
        auto bytes = receive(*offer, type, success);
        size += bytes.size();
        if (!success || size > maxBytes) {
          std::fputs(
              "Plasma clipboard: incomplete or oversized selection; skipped\n",
              stderr);
          return;
        }
        data[type] = std::move(bytes);
      }
    }
    publish(mode, std::move(data));
  }
  static void selection(void *data, wl_proxy *, wl_proxy *offer) {
    static_cast<WaylandEndpoint *>(data)->selected(0, offer);
  }
  static void primarySelection(void *data, wl_proxy *, wl_proxy *offer) {
    static_cast<WaylandEndpoint *>(data)->selected(1, offer);
  }
  static void finished(void *, wl_proxy *) {
    fail("clipboard seat disconnected");
  }
  static void send(void *data, wl_proxy *, const char *type, int32_t fd) {
    auto *source = static_cast<Source *>(data);
    const auto it = source->data.find(QString::fromUtf8(type));
    if (it != source->data.end()) {
      auto bytes = it->second;
      if (!transfer(fd, bytes, true))
        std::fputs(
            "Plasma clipboard: clipboard consumer did not finish reading\n",
            stderr);
    }
    close(fd);
  }
  static void cancelled(void *data, wl_proxy *) {
    auto *source = static_cast<Source *>(data);
    source->owner->sources.at(source->mode).reset();
  }

public:
  ~WaylandEndpoint() override {
    notifier.reset();
    for (auto &source : sources)
      source.reset();
    offers.clear();
    if (display)
      wl_display_disconnect(display);
  }
  WaylandEndpoint() {
    const auto name = qgetenv("WAYLAND_DISPLAY");
    display = wl_display_connect(name.constData());
    if (!display)
      fail("cannot connect to Wayland clipboard display");
    registry = wl_display_get_registry(display);
    static const wl_registry_listener listener{
        announce, [](void *, wl_registry *, uint32_t) {}};
    wl_registry_add_listener(registry, &listener, this);
    if (wl_display_roundtrip(display) < 0)
      fail("cannot enumerate Wayland clipboard protocols");
    if (!managerName || !seat)
      fail("host does not expose ext-data-control or wlr-data-control; mount "
           "its X11 socket or set CLIPBOARD_SYNC=0",
           3);
    manager = static_cast<wl_proxy *>(wl_registry_bind(
        registry, managerName, protocol.manager, managerVersion));
    std::fprintf(stderr, "Plasma clipboard: using %s\n",
                 protocol.manager->name);
    device = wl_proxy_marshal_flags(manager, 1, protocol.device, managerVersion,
                                    0, nullptr, seat);
    static struct {
      decltype(&offered) offer;
      decltype(&selection) onSelection;
      decltype(&finished) onFinished;
      decltype(&primarySelection) primary;
    } deviceListener{offered, selection, finished, primarySelection};
    wl_proxy_add_listener(
        device, reinterpret_cast<void (**)(void)>(&deviceListener), this);
    if (wl_display_roundtrip(display) < 0)
      fail("cannot initialize Wayland clipboard device");
    if (!primary)
      publish(1, {});
    notifier = std::make_unique<QSocketNotifier>(wl_display_get_fd(display),
                                                 QSocketNotifier::Read);
    QObject::connect(notifier.get(), &QSocketNotifier::activated,
                     notifier.get(), [this] {
                       if (wl_display_dispatch(display) < 0)
                         fail("Wayland clipboard display disconnected");
                       wl_display_flush(display);
                     });
  }
  void set(int mode, Data data) override {
    if (mode == 1 && !primary)
      return;
    wl_proxy *object = nullptr;
    std::unique_ptr<Source> source;
    if (!data.empty()) {
      object = wl_proxy_marshal_flags(manager, 0, protocol.source,
                                      managerVersion, 0, nullptr);
      source = std::make_unique<Source>(this, object, mode, std::move(data));
      static struct {
        decltype(&send) onSend;
        decltype(&cancelled) onCancelled;
      } listener{send, cancelled};
      wl_proxy_add_listener(
          object, reinterpret_cast<void (**)(void)>(&listener), source.get());
      for (const auto &[type, bytes] : source->data) {
        const auto name = type.toUtf8();
        wl_proxy_marshal_flags(object, 0, nullptr, wl_proxy_get_version(object),
                               0, name.constData());
      }
    }
    wl_proxy_marshal_flags(device, mode == 0 ? 0 : 2, nullptr, managerVersion,
                           0, object);
    sources.at(mode) = std::move(source);
    wl_display_flush(display);
  }
};

int endpoint(QCoreApplication &app, const QString &backend) {
  std::unique_ptr<Endpoint> clipboard;
  if (backend == "wayland")
    clipboard = std::make_unique<WaylandEndpoint>();
  else
    clipboard = std::make_unique<X11Endpoint>();
  if (fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK) < 0)
    fail("cannot monitor clipboard controller");
  QSocketNotifier input(STDIN_FILENO, QSocketNotifier::Read);
  QByteArray buffer;
  QObject::connect(&input, &QSocketNotifier::activated, &app, [&] {
    std::array<char, 65536> bytes{};
    const auto count = read(STDIN_FILENO, bytes.data(), bytes.size());
    if (count == 0)
      app.quit();
    else if (count > 0)
      consume(buffer, QByteArray(bytes.data(), count),
              [&](const QJsonObject &message) { clipboard->apply(message); });
    else if (errno != EINTR && errno != EAGAIN)
      fail("clipboard controller read failed");
  });
  return app.exec();
}

int bridge(QCoreApplication &app) {
  std::array<QProcess, 2> processes;
  std::array<QByteArray, 2> buffers;
  std::array<QByteArray, 2> previous;
  std::array<bool, 2> hostReady{};
  QString hostBackend = qEnvironmentVariable("HOST_DISPLAY_BACKEND");
  auto start = [&](int side, const QString &backend) {
    auto environment = QProcessEnvironment::systemEnvironment();
    environment.remove("WAYLAND_SOCKET");
    environment.remove("SESSION_MANAGER");
    environment.insert("QT_QPA_PLATFORM",
                       backend == "x11" ? "xcb" : "offscreen");
    if (side == 0) {
      environment.insert("WAYLAND_DISPLAY",
                         qEnvironmentVariable("HOST_WAYLAND_DISPLAY"));
      environment.insert("DISPLAY", qEnvironmentVariable("HOST_DISPLAY"));
      environment.insert("XAUTHORITY", qEnvironmentVariable("HOST_XAUTHORITY"));
    }
    processes.at(side).setProcessEnvironment(environment);
    processes.at(side).setProcessChannelMode(QProcess::ForwardedErrorChannel);
    processes.at(side).start(QCoreApplication::applicationFilePath(),
                             {"--endpoint", backend});
  };
  for (int side = 0; side < 2; ++side) {
    auto &process = processes.at(side);
    QObject::connect(
        &process, &QProcess::readyReadStandardOutput, &app, [&, side] {
          consume(
              buffers.at(side), processes.at(side).readAllStandardOutput(),
              [&](const QJsonObject &message) {
                const int mode = message.value("mode").toInt(-1);
                if (mode < 0 || mode > 1 || !message.value("data").isObject())
                  fail("clipboard endpoint sent an invalid selection");
                if (side == 1 &&
                    (message.value("initial").toBool() || !hostReady.at(mode)))
                  return;
                if (side == 0)
                  hostReady.at(mode) = true;
                const auto data =
                    QJsonDocument(message.value("data").toObject())
                        .toJson(QJsonDocument::Compact);
                if (previous.at(mode) == data)
                  return;
                previous.at(mode) = data;
                if (processes.at(1 - side).write(frame(message)) < 0)
                  fail("cannot forward clipboard selection");
              });
        });
    QObject::connect(&process, &QProcess::errorOccurred, &app,
                     [&](QProcess::ProcessError error) {
                       if (error == QProcess::FailedToStart)
                         fail("cannot start clipboard endpoint");
                     });
    QObject::connect(
        &process, &QProcess::finished, &app,
        [&, side](int status, QProcess::ExitStatus exitStatus) {
          if (side == 0 && status == 3 && hostBackend == "wayland" &&
              !qEnvironmentVariableIsEmpty("HOST_DISPLAY")) {
            hostBackend = "x11";
            buffers.at(0).clear();
            std::fputs("Plasma clipboard: using host X11 clipboard fallback\n",
                       stderr);
            start(0, hostBackend);
          } else {
            app.exit(
                exitStatus == QProcess::NormalExit && status == 0 ? 1 : status);
          }
        });
  }
  start(1, qEnvironmentVariable("DISPLAY_BACKEND"));
  start(0, hostBackend);
  std::fputs(
      "Plasma clipboard: bidirectional nested clipboard sharing started\n",
      stderr);
  const int status = app.exec();
  for (auto &process : processes) {
    if (process.state() != QProcess::NotRunning) {
      process.kill();
      process.waitForFinished(1000);
    }
  }
  return status;
}
} // namespace

int main(int argc, char **argv) {
  signal(SIGPIPE, SIG_IGN);
  const bool worker = argc == 3 && std::strcmp(argv[1], "--endpoint") == 0;
  const bool x11 = worker && std::strcmp(argv[2], "x11") == 0;
  std::unique_ptr<QCoreApplication> app;
  if (x11)
    app = std::make_unique<QGuiApplication>(argc, argv);
  else
    app = std::make_unique<QCoreApplication>(argc, argv);
  if (worker)
    return endpoint(*app, QString::fromLocal8Bit(argv[2]));
  if (argc != 1)
    return 2;
  return bridge(*app);
}
