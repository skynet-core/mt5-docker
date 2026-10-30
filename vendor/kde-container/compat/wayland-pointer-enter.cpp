// SPDX-License-Identifier: GPL-2.0-or-later
// Nested KWin consumes motion coordinates but ignores the position on enter.
// Deliver that position before a click, including after window moves/scaling.
#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <mutex>
#include <time.h>
#include <vector>
#include <wayland-client-protocol.h>
#include <wayland-version.h>

namespace {
[[noreturn]] void fail(const char *message) {
  std::fprintf(stderr, "Plasma Wayland pointer: %s\n", message);
  std::abort();
}

// The loader already mapped this library. Keep it out of KWin's children,
// sharing the saved caller environment with the NVIDIA synchronization shim.
__attribute__((constructor)) void restoreChildEnvironment() {
  const char *previous = std::getenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  if (previous) {
    if (*previous) {
      if (setenv("LD_PRELOAD", previous, 1) != 0)
        fail("cannot restore the application preload environment");
    } else {
      unsetenv("LD_PRELOAD");
    }
    unsetenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  }
}

template <typename Function> Function resolve(const char *name) {
  const auto original = reinterpret_cast<Function>(dlsym(RTLD_NEXT, name));
  if (!original)
    fail("cannot resolve the Wayland listener API");
  return original;
}

struct PointerLock {
  wl_proxy *proxy = nullptr;
  wl_display *display = nullptr;
  uint32_t pointer = 0;
  uint32_t surface = 0;
  bool active = false;
};
struct Locks {
  std::mutex mutex;
  std::vector<PointerLock> objects;
};
Locks &locks() {
  static Locks value;
  return value;
}
bool pointerLocked(wl_proxy *pointer) {
  auto &state = locks();
  const std::lock_guard<std::mutex> guard(state.mutex);
  return std::any_of(state.objects.begin(), state.objects.end(),
                     [pointer](const PointerLock &lock) {
                       return lock.active &&
                           lock.display == wl_proxy_get_display(pointer) &&
                           lock.pointer == wl_proxy_get_id(pointer);
                     });
}
void forgetProxy(wl_proxy *proxy) {
  auto &state = locks();
  const std::lock_guard<std::mutex> guard(state.mutex);
  state.objects.erase(
      std::remove_if(state.objects.begin(), state.objects.end(),
                     [proxy](const PointerLock &lock) {
                       return lock.proxy == proxy ||
                           (lock.display == wl_proxy_get_display(proxy) &&
                            (lock.pointer == wl_proxy_get_id(proxy) ||
                             lock.surface == wl_proxy_get_id(proxy)));
                     }),
      state.objects.end());
}
int dispatchLock(const void *implementation, void *target, uint32_t opcode,
                 const wl_message *, wl_argument *) {
  if (opcode > 1)
    return -1;
  auto *proxy = static_cast<wl_proxy *>(target);
  {
    auto &state = locks();
    const std::lock_guard<std::mutex> guard(state.mutex);
    for (auto &lock : state.objects) {
      if (lock.proxy == proxy)
        lock.active = opcode == 0;
    }
  }
  using Callback = void (*)(void *, wl_proxy *);
  const auto *callbacks = static_cast<void (*const *)(void)>(implementation);
  const auto callback = reinterpret_cast<Callback>(callbacks[opcode]);
  if (callback)
    callback(wl_proxy_get_user_data(proxy), proxy);
  return 0;
}

int dispatchPointer(const void *implementation, void *target, uint32_t opcode,
                    const wl_message *message, wl_argument *arguments) {
  const auto *listener =
      static_cast<const wl_pointer_listener *>(implementation);
  auto *pointer = static_cast<wl_pointer *>(target);
  // Fetch the current value: wl_proxy_set_user_data remains supported after
  // registration, and wl_proxy_get_listener still sees the original listener.
  void *data = wl_proxy_get_user_data(static_cast<wl_proxy *>(target));
  switch (opcode) {
  case 0: {
    if (listener->enter)
      listener->enter(data, pointer, arguments[0].u,
                      reinterpret_cast<wl_surface *>(arguments[1].o),
                      arguments[2].f, arguments[3].f);
    // A grab's host position stays frozen while relative motion moves KWin's
    // software cursor; a subsurface enter must not reset that relative position.
    if (listener->motion && !pointerLocked(static_cast<wl_proxy *>(target))) {
      timespec now{};
      if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        fail("cannot read the monotonic pointer clock");
      const auto milliseconds = static_cast<uint32_t>(
          static_cast<uint64_t>(now.tv_sec) * 1000 + now.tv_nsec / 1000000);
      listener->motion(data, pointer, milliseconds, arguments[2].f,
                       arguments[3].f);
    }
    break;
  }
  case 1:
    if (listener->leave)
      listener->leave(data, pointer, arguments[0].u,
                      reinterpret_cast<wl_surface *>(arguments[1].o));
    break;
  case 2:
    if (listener->motion)
      listener->motion(data, pointer, arguments[0].u, arguments[1].f,
                       arguments[2].f);
    break;
  case 3:
    if (listener->button)
      listener->button(data, pointer, arguments[0].u, arguments[1].u,
                       arguments[2].u, arguments[3].u);
    break;
  case 4:
    if (listener->axis)
      listener->axis(data, pointer, arguments[0].u, arguments[1].u,
                     arguments[2].f);
    break;
  case 5:
    if (listener->frame)
      listener->frame(data, pointer);
    break;
  case 6:
    if (listener->axis_source)
      listener->axis_source(data, pointer, arguments[0].u);
    break;
  case 7:
    if (listener->axis_stop)
      listener->axis_stop(data, pointer, arguments[0].u, arguments[1].u);
    break;
  case 8:
    if (listener->axis_discrete)
      listener->axis_discrete(data, pointer, arguments[0].u, arguments[1].i);
    break;
#ifdef WL_POINTER_AXIS_VALUE120_SINCE_VERSION
  case 9:
    if (listener->axis_value120)
      listener->axis_value120(data, pointer, arguments[0].u, arguments[1].i);
    break;
#endif
#ifdef WL_POINTER_AXIS_RELATIVE_DIRECTION_SINCE_VERSION
  case 10:
    if (listener->axis_relative_direction)
      listener->axis_relative_direction(data, pointer, arguments[0].u,
                                        arguments[1].u);
    break;
#endif
  case 11: {
    // Debian's system headers can be older than KWin's private Wayland SDK.
    // add_listener's ABI is a function-pointer table, so the v11 slot remains
    // usable without a compile-time wl_pointer_listener::warp declaration.
    if (wl_proxy_get_version(static_cast<wl_proxy *>(target)) < 11 ||
        !message || std::strcmp(message->name, "warp") != 0 ||
        std::strcmp(message->signature, "11ff") != 0)
      return -1;
    using Warp = void (*)(void *, wl_pointer *, wl_fixed_t, wl_fixed_t);
    const auto *callbacks = static_cast<void (*const *)(void)>(implementation);
    const auto warp = reinterpret_cast<Warp>(callbacks[11]);
    if (warp)
      warp(data, pointer, arguments[0].f, arguments[1].f);
    break;
  }
  default:
    return -1;
  }
  return 0;
}
} // namespace

extern "C" int wl_proxy_add_listener(wl_proxy *proxy,
                                     void (**implementation)(void),
                                     void *data) {
  const char *name = wl_proxy_get_class(proxy);
  const bool pointer = std::strcmp(name, "wl_pointer") == 0;
  if (pointer || std::strcmp(name, "zwp_locked_pointer_v1") == 0) {
    static const auto original =
        resolve<decltype(&wl_proxy_add_dispatcher)>("wl_proxy_add_dispatcher");
    return original(proxy, pointer ? dispatchPointer : dispatchLock,
                     implementation, data);
  }
  static const auto original =
      resolve<decltype(&wl_proxy_add_listener)>("wl_proxy_add_listener");
  return original(proxy, implementation, data);
}

extern "C" wl_proxy *wl_proxy_marshal_array_flags(
    wl_proxy *proxy, uint32_t opcode, const wl_interface *interface,
    uint32_t version, uint32_t flags, wl_argument *arguments) {
  // Generated variadic requests also pass through this packed-argument API.
  static const auto original = resolve<decltype(&wl_proxy_marshal_array_flags)>(
      "wl_proxy_marshal_array_flags");
  const bool creatingLock = interface &&
      std::strcmp(interface->name, "zwp_locked_pointer_v1") == 0 &&
      std::strcmp(wl_proxy_get_class(proxy), "zwp_pointer_constraints_v1") == 0 &&
      opcode == 1;
  if (flags & WL_MARSHAL_FLAG_DESTROY)
    forgetProxy(proxy);
  auto *created = original(proxy, opcode, interface, version, flags, arguments);
  if (created && creatingLock) {
    auto &state = locks();
    const std::lock_guard<std::mutex> guard(state.mutex);
    state.objects.push_back({
        created, wl_proxy_get_display(created),
        wl_proxy_get_id(reinterpret_cast<wl_proxy *>(arguments[2].o)),
        wl_proxy_get_id(reinterpret_cast<wl_proxy *>(arguments[1].o))});
  }
  return created;
}

#if WAYLAND_VERSION_MAJOR > 1 || WAYLAND_VERSION_MINOR >= 24
// Some distro libraries bind their internal array call locally; pack through
// the public interface metadata so those requests use the same tracked path.
extern "C" wl_proxy *wl_proxy_marshal_flags(
    wl_proxy *proxy, uint32_t opcode, const wl_interface *interface,
    uint32_t version, uint32_t flags, ...) {
  const auto *requestInterface = wl_proxy_get_interface(proxy);
  if (opcode >= static_cast<uint32_t>(requestInterface->method_count))
    fail("invalid Wayland request opcode");
  const char *signature = requestInterface->methods[opcode].signature;
  std::vector<wl_argument> arguments;
  arguments.reserve(std::strlen(signature));
  va_list values;
  va_start(values, flags);
  for (const char *type = signature; *type; ++type) {
    if ((*type >= '0' && *type <= '9') || *type == '?')
      continue;
    wl_argument value{};
    switch (*type) {
    case 'i': value.i = va_arg(values, int32_t); break;
    case 'u': value.u = va_arg(values, uint32_t); break;
    case 'f': value.f = va_arg(values, wl_fixed_t); break;
    case 's': value.s = va_arg(values, const char *); break;
    case 'o':
    case 'n': value.o = va_arg(values, wl_object *); break;
    case 'a': value.a = va_arg(values, wl_array *); break;
    case 'h': value.h = va_arg(values, int32_t); break;
    default: fail("unknown Wayland request argument type");
    }
    arguments.push_back(value);
  }
  va_end(values);
  return wl_proxy_marshal_array_flags(proxy, opcode, interface, version, flags,
                                       arguments.data());
}
#endif

extern "C" void wl_proxy_destroy(wl_proxy *proxy) {
  forgetProxy(proxy);
  static const auto original = resolve<decltype(&wl_proxy_destroy)>(
      "wl_proxy_destroy");
  original(proxy);
}

extern "C" void wl_display_disconnect(wl_display *display) {
  {
    auto &state = locks();
    const std::lock_guard<std::mutex> guard(state.mutex);
    state.objects.erase(
        std::remove_if(state.objects.begin(), state.objects.end(),
                       [display](const PointerLock &lock) {
                         return lock.display == display;
                       }),
        state.objects.end());
  }
  static const auto original = resolve<decltype(&wl_display_disconnect)>(
      "wl_display_disconnect");
  original(display);
}
