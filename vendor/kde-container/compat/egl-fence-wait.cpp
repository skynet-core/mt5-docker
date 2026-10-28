// SPDX-License-Identifier: GPL-2.0-or-later
// Nested KWin publishes raw buffers without a host acquire fence. Complete
// NVIDIA rendering before that handoff; Mesa keeps its normal synchronization.
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <stdlib.h>
#include <sys/poll.h>

namespace {
using Proc = void (*)();
using GetProc = Proc (*)(const char *);
using DupFence = int (*)(void *, void *);
using QueryString = const char *(*)(void *, int);

[[noreturn]] void fail(const char *message) {
  std::fprintf(stderr, "Plasma NVIDIA sync: %s\n", message);
  std::abort();
}

// The loader has already mapped this library. Restore the caller's preload
// environment now so Xwayland and session applications do not inherit it.
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

int duplicateFence(void *display, void *sync) {
  static void *egl = dlopen("libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!egl)
    fail("cannot load EGL");
  static const auto getProc =
      reinterpret_cast<GetProc>(dlsym(egl, "eglGetProcAddress"));
  if (!getProc)
    fail("cannot resolve eglGetProcAddress");
  static const auto original =
      reinterpret_cast<DupFence>(getProc("eglDupNativeFenceFDANDROID"));
  static const auto queryString =
      reinterpret_cast<QueryString>(getProc("eglQueryString"));
  if (!original || !queryString)
    fail("cannot resolve the EGL fence API");
  const int descriptor = original(display, sync);
  constexpr int eglVendor = 0x3053;
  const char *vendor = queryString(display, eglVendor);
  if (descriptor >= 0 && vendor && std::strstr(vendor, "NVIDIA")) {
    pollfd fence{descriptor, POLLIN, 0};
    int ready;
    do {
      ready = poll(&fence, 1, -1);
    } while (ready < 0 && errno == EINTR);
    if (ready != 1 || fence.revents != POLLIN)
      fail("GPU render fence failed");
    static std::atomic_flag announced = ATOMIC_FLAG_INIT;
    if (!announced.test_and_set())
      std::fputs("Plasma NVIDIA sync: completed GPU fence before nested buffer "
                 "handoff\n",
                 stderr);
  }
  // KWin retains ownership of the driver's original descriptor.
  return descriptor;
}
} // namespace

// libepoxy exposes its EGL entry points as function-pointer variables.
extern "C" {
DupFence epoxy_eglDupNativeFenceFDANDROID = duplicateFence;
}
