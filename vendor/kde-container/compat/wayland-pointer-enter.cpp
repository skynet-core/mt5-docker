// SPDX-License-Identifier: GPL-2.0-or-later
// Nested KWin consumes motion coordinates but ignores the position on enter.
// Deliver that position before a click, including after window moves/scaling.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <time.h>
#include <wayland-client-protocol.h>

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

int dispatchPointer(const void *implementation, void *target, uint32_t opcode,
                    const wl_message *message, wl_argument *arguments) {
  const auto *listener = static_cast<const wl_pointer_listener *>(implementation);
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
    if (listener->motion) {
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
    if (wl_proxy_get_version(static_cast<wl_proxy *>(target)) < 11 || !message ||
        std::strcmp(message->name, "warp") != 0 ||
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

extern "C" int wl_proxy_add_listener(wl_proxy *proxy, void (**implementation)(void),
                                      void *data) {
  if (std::strcmp(wl_proxy_get_class(proxy), "wl_pointer") == 0) {
    static const auto original =
        resolve<decltype(&wl_proxy_add_dispatcher)>("wl_proxy_add_dispatcher");
    return original(proxy, dispatchPointer, implementation, data);
  }
  static const auto original =
      resolve<decltype(&wl_proxy_add_listener)>("wl_proxy_add_listener");
  return original(proxy, implementation, data);
}
