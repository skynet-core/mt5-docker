// SPDX-License-Identifier: GPL-2.0-or-later
// Mock only the external Wayland C API, preserving generated protocol
// signatures.
#include "fake-wayland.h"
#include "ext-data-control-v1-client.h"
#include "fake-input-client.h"
#include "wlr-data-control-unstable-v1-client.h"
#include <QCoreApplication>
#include <QTimer>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdarg>
#include <cstring>
#include <memory>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>
#include <wayland-client-core.h>
#include <wayland-client-protocol.h>
#include <xkbcommon/xkbcommon.h>

namespace {
struct Proxy {
  const wl_interface *interface = nullptr;
  unsigned version = 0;
  void (**listener)(void) = nullptr;
  void *data = nullptr;
  int descriptor = -1;
  int roundtrips = 0;
  bool active = true;
  bool bridgeStarted = false;
  std::string name;
  std::map<std::string, std::string> formats;
  std::array<Proxy *, 2> selections{};
  ~Proxy() {
    if (descriptor >= 0)
      close(descriptor);
  }
};
std::vector<std::unique_ptr<Proxy>> objects;
std::vector<fake::Request> recorded;
std::string selected;
unsigned inputVersion;
std::vector<std::thread> writers;
struct JoinWriters {
  ~JoinWriters() {
    for (auto &writer : writers)
      if (writer.joinable())
        writer.join();
  }
} joinWriters;
bool clipboard() { return selected.rfind("clipboard-", 0) == 0; }
bool condition(const char *name) {
  return selected == name || selected == std::string("clipboard-") + name;
}
Proxy *make(const wl_interface *interface, unsigned version) {
  auto object = std::make_unique<Proxy>();
  object->interface = interface;
  object->version = version;
  auto *pointer = object.get();
  objects.push_back(std::move(object));
  return pointer;
}
template <class T> Proxy *proxy(T *object) {
  return reinterpret_cast<Proxy *>(object);
}
Proxy *latest(const char *name) {
  for (auto it = objects.rbegin(); it != objects.rend(); ++it)
    if ((*it)->active && std::strcmp((*it)->interface->name, name) == 0)
      return it->get();
  return nullptr;
}
void announce(const char *name, unsigned version) {
  auto *registry = latest("wl_registry");
  assert(registry && registry->listener);
  auto *listener =
      reinterpret_cast<const wl_registry_listener *>(registry->listener);
  listener->global(registry->data, reinterpret_cast<wl_registry *>(registry),
                   unsigned(objects.size() + 1), name, version);
}
void queueBridgeOffer(const std::string &text, int delay) {
  QTimer::singleShot(delay, [text] {
    fake::cancel(0);
    fake::offer(0, {{"text/plain", text}});
    fake::offer(0, {{"text/plain", text}});
  });
}
} // namespace

namespace fake {
void reset(const std::string &mode, unsigned version) {
  for (auto &writer : writers)
    writer.join();
  writers.clear();
  objects.clear();
  recorded.clear();
  selected = mode;
  inputVersion = version;
}
const std::vector<Request> &requests() { return recorded; }
void global(const char *name, unsigned version) { announce(name, version); }
void keymap(const std::string &kind) {
  auto *keyboard = latest("wl_keyboard");
  assert(keyboard && keyboard->listener);
  auto *listener =
      reinterpret_cast<const wl_keyboard_listener *>(keyboard->listener);
  const int fd =
      kind == "bad-fd" ? -1 : memfd_create("native-test-keymap", MFD_CLOEXEC);
  assert(fd >= 0 || kind == "bad-fd");
  std::string text = "invalid keymap";
  if (kind == "valid") {
    auto *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    assert(context);
    auto *map = xkb_keymap_new_from_names(context, nullptr,
                                          XKB_KEYMAP_COMPILE_NO_FLAGS);
    assert(map);
    char *contents = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    assert(contents);
    text = contents;
    free(contents);
    xkb_keymap_unref(map);
    xkb_context_unref(context);
  }
  text += '\0';
  if (fd >= 0)
    assert(write(fd, text.data(), text.size()) ==
           static_cast<ssize_t>(text.size()));
  listener->keymap(keyboard->data, reinterpret_cast<wl_keyboard *>(keyboard),
                   kind == "wrong-format" ? 0
                                          : WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1,
                   fd, kind == "empty" ? 0 : uint32_t(text.size()));
}
void capabilities(unsigned flags) {
  auto *seat = latest("wl_seat");
  assert(seat && seat->listener);
  auto *listener = reinterpret_cast<const wl_seat_listener *>(seat->listener);
  listener->capabilities(seat->data, reinterpret_cast<wl_seat *>(seat), flags);
  listener->name(seat->data, reinterpret_cast<wl_seat *>(seat),
                 "private-unit-seat");
}
void keyboardEvents() {
  auto *keyboard = latest("wl_keyboard");
  assert(keyboard && keyboard->listener);
  auto *listener =
      reinterpret_cast<const wl_keyboard_listener *>(keyboard->listener);
  auto *object = reinterpret_cast<wl_keyboard *>(keyboard);
  listener->enter(keyboard->data, object, 1, nullptr, nullptr);
  listener->leave(keyboard->data, object, 1, nullptr);
  listener->key(keyboard->data, object, 1, 1, 1, 1);
  listener->modifiers(keyboard->data, object, 1, 1, 1, 1, 1);
  listener->repeat_info(keyboard->data, object, 25, 600);
}
void notify() {
  auto *display = latest("wl_display");
  assert(display);
  eventfd_t value = 1;
  assert(eventfd_write(display->descriptor, value) == 0);
}
Proxy *device() {
  for (auto it = objects.rbegin(); it != objects.rend(); ++it)
    if ((*it)->active && (std::strcmp((*it)->interface->name,
                                      "ext_data_control_device_v1") == 0 ||
                          std::strcmp((*it)->interface->name,
                                      "zwlr_data_control_device_v1") == 0))
      return it->get();
  return nullptr;
}
void offer(int mode, const std::map<std::string, std::string> &formats,
           bool known) {
  auto *control = device();
  assert(control && control->listener);
  const bool ext =
      std::strcmp(control->interface->name, "ext_data_control_device_v1") == 0;
  auto *object = make(ext ? &ext_data_control_offer_v1_interface
                          : &zwlr_data_control_offer_v1_interface,
                      1);
  object->formats = formats;
  if (known) {
    using Offered = void (*)(void *, wl_proxy *, wl_proxy *);
    reinterpret_cast<Offered>(control->listener[0])(
        control->data, reinterpret_cast<wl_proxy *>(control),
        reinterpret_cast<wl_proxy *>(object));
    for (const auto &[type, bytes] : formats) {
      using Format = void (*)(void *, wl_proxy *, const char *);
      reinterpret_cast<Format>(object->listener[0])(
          object->data, reinterpret_cast<wl_proxy *>(object), type.c_str());
    }
  }
  using Selection = void (*)(void *, wl_proxy *, wl_proxy *);
  reinterpret_cast<Selection>(control->listener[mode == 0 ? 1 : 3])(
      control->data, reinterpret_cast<wl_proxy *>(control),
      reinterpret_cast<wl_proxy *>(object));
}
std::string request(int mode, const char *type, bool fail) {
  auto *control = device();
  assert(control && control->selections.at(mode));
  auto *source = control->selections.at(mode);
  int pipes[2];
  assert(pipe(pipes) == 0);
  if (fail)
    close(pipes[0]);
  using Send = void (*)(void *, wl_proxy *, const char *, int32_t);
  reinterpret_cast<Send>(source->listener[0])(
      source->data, reinterpret_cast<wl_proxy *>(source), type, pipes[1]);
  std::string result;
  if (!fail) {
    char block[4096];
    ssize_t count;
    while ((count = read(pipes[0], block, sizeof(block))) > 0)
      result.append(block, size_t(count));
    close(pipes[0]);
  }
  return result;
}
void cancel(int mode) {
  auto *control = device();
  assert(control);
  auto *source = control->selections.at(mode);
  if (source) {
    using Cancel = void (*)(void *, wl_proxy *);
    reinterpret_cast<Cancel>(source->listener[1])(
        source->data, reinterpret_cast<wl_proxy *>(source));
    control->selections.at(mode) = nullptr;
  }
}
void finished() {
  auto *control = device();
  assert(control);
  using Finished = void (*)(void *, wl_proxy *);
  reinterpret_cast<Finished>(control->listener[2])(
      control->data, reinterpret_cast<wl_proxy *>(control));
}
} // namespace fake

extern "C" wl_display *wl_display_connect(const char *) {
  if (condition("connect-failed"))
    return nullptr;
  auto *display = make(&wl_display_interface, 1);
  display->descriptor = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
  assert(display->descriptor >= 0);
  display->name = qgetenv("WAYLAND_DISPLAY").constData();
  return reinterpret_cast<wl_display *>(display);
}
extern "C" void wl_display_disconnect(wl_display *display) {
  auto *object = proxy(display);
  object->active = false;
  close(object->descriptor);
  object->descriptor = -1;
}
extern "C" int wl_display_get_fd(wl_display *display) {
  if (condition("stdin-failed"))
    close(STDIN_FILENO);
  return proxy(display)->descriptor;
}
extern "C" int wl_display_flush(wl_display *) { return 0; }
extern "C" int wl_display_dispatch(wl_display *display) {
  eventfd_t value;
  eventfd_read(proxy(display)->descriptor, &value);
  return condition("dispatch-failed") ? -1 : 0;
}
extern "C" uint32_t wl_proxy_get_version(wl_proxy *object) {
  return proxy(object)->version;
}
extern "C" void wl_proxy_destroy(wl_proxy *object) {
  proxy(object)->active = false;
}
extern "C" int wl_proxy_add_listener(wl_proxy *object, void (**listener)(void),
                                     void *data) {
  auto *node = proxy(object);
  node->listener = listener;
  node->data = data;
  if (std::strcmp(node->interface->name, "wl_seat") == 0)
    fake::capabilities(WL_SEAT_CAPABILITY_KEYBOARD);
  if (std::strcmp(node->interface->name, "wl_keyboard") == 0 &&
      selected != "keymap-unavailable")
    fake::keymap("valid");
  return 0;
}
extern "C" int wl_display_roundtrip(wl_display *display) {
  const int round = ++proxy(display)->roundtrips;
  if ((condition("enumerate-failed") && round == 1) ||
      (condition("auth-failed") && round == 2) ||
      (condition("device-failed") && round == 2))
    return -1;
  if (round == 1) {
    announce("unrelated_interface", 1);
    if (clipboard()) {
      const bool host = proxy(display)->name.find("host") != std::string::npos;
      const bool unavailable = condition("protocol-unavailable") ||
                               (condition("bridge-fallback") && host);
      if (!unavailable) {
        if (inputVersion == 5)
          announce("ext_data_control_manager_v1", 1);
        if (inputVersion != 3)
          announce("zwlr_data_control_manager_v1", std::min(inputVersion, 2u));
        if (inputVersion >= 3)
          announce("ext_data_control_manager_v1", 1);
      }
    } else
      announce("org_kde_kwin_fake_input",
               selected == "input-unavailable" ? 4 : inputVersion);
    if (!condition("seat-unavailable"))
      announce("wl_seat", 1);
  } else if (clipboard() && round == 2) {
    auto *control = fake::device();
    assert(control && control->listener);
    using Selection = void (*)(void *, wl_proxy *, wl_proxy *);
    reinterpret_cast<Selection>(control->listener[1])(
        control->data, reinterpret_cast<wl_proxy *>(control), nullptr);
    if (inputVersion >= 2)
      reinterpret_cast<Selection>(control->listener[3])(
          control->data, reinterpret_cast<wl_proxy *>(control), nullptr);
    if (selected.rfind("clipboard-bridge", 0) == 0 &&
        proxy(display)->name.find("host") != std::string::npos)
      queueBridgeOffer("private-host", 80);
  }
  return 0;
}
extern "C" wl_proxy *wl_proxy_marshal_flags(wl_proxy *object, uint32_t opcode,
                                            const wl_interface *interface,
                                            uint32_t version, uint32_t flags,
                                            ...) {
  auto *node = proxy(object);
  if (flags & WL_MARSHAL_FLAG_DESTROY) {
    node->active = false;
    return nullptr;
  }
  if (interface)
    return reinterpret_cast<wl_proxy *>(make(interface, version));
  assert(opcode < static_cast<unsigned>(node->interface->method_count));
  fake::Request request{node->interface->name, opcode, {}, {}};
  va_list arguments;
  va_start(arguments, flags);
  const char *signature = node->interface->methods[opcode].signature;
  for (const char *type = signature; *type; ++type) {
    if (*type == 's')
      request.strings.emplace_back(va_arg(arguments, const char *));
    else if (*type == 'u')
      request.values.push_back(va_arg(arguments, unsigned));
    else if (*type == 'i' || *type == 'f' || *type == 'h')
      request.values.push_back(va_arg(arguments, int));
    else if (*type == 'o')
      request.values.push_back(
          reinterpret_cast<int64_t>(va_arg(arguments, wl_proxy *)));
    else
      assert(*type == '?' || (*type >= '0' && *type <= '9'));
  }
  va_end(arguments);
  const std::string type = node->interface->name;
  if (type == "ext_data_control_offer_v1" ||
      type == "zwlr_data_control_offer_v1") {
    assert(opcode == 0 && request.strings.size() == 1 &&
           request.values.size() == 1);
    const auto data = node->formats.at(request.strings[0]);
    const int descriptor = dup(int(request.values[0]));
    assert(descriptor >= 0);
    writers.emplace_back([descriptor, data] {
      size_t offset = 0;
      while (offset < data.size()) {
        const auto count =
            write(descriptor, data.data() + offset, data.size() - offset);
        if (count <= 0)
          break;
        offset += size_t(count);
      }
      close(descriptor);
    });
  } else if (type == "ext_data_control_device_v1" ||
             type == "zwlr_data_control_device_v1") {
    assert(opcode == 0 || opcode == 2);
    node->selections.at(opcode == 0 ? 0 : 1) =
        reinterpret_cast<Proxy *>(request.values.at(0));
    // The controller deliberately ignores child changes before host startup.
    // Receiving its first host selection provides a deterministic handshake.
    if (selected.rfind("clipboard-bridge", 0) == 0 && opcode == 0 &&
        !node->bridgeStarted &&
        latest("wl_display")->name.find("host") == std::string::npos) {
      node->bridgeStarted = true;
      queueBridgeOffer("private-child", 0);
    }
  }
  recorded.push_back(std::move(request));
  return nullptr;
}

extern "C" int pipe2(int descriptors[2], int flags) {
  if (condition("pipe-failed")) {
    errno = EMFILE;
    return -1;
  }
  return int(syscall(SYS_pipe2, descriptors, flags));
}
extern "C" ssize_t read(int descriptor, void *data, size_t size) {
  static bool transient = false;
  if (descriptor == STDIN_FILENO && condition("read-failed")) {
    errno = EIO;
    return -1;
  }
  if (descriptor == STDIN_FILENO && !transient &&
      (condition("read-eintr") || condition("read-eagain"))) {
    transient = true;
    errno = condition("read-eintr") ? EINTR : EAGAIN;
    return -1;
  }
  return syscall(SYS_read, descriptor, data, size);
}
