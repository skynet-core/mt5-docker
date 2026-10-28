// SPDX-License-Identifier: GPL-2.0-or-later
// Fake EGL exports a delayed fence for NVIDIA and an unsignalled Mesa fence.
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <sys/eventfd.h>
#include <thread>

namespace {
const char *vendor(void *, int) { return std::getenv("FAKE_EGL_VENDOR"); }
int duplicate(void *, void *sync) {
  const int descriptor = eventfd(0, EFD_CLOEXEC);
  *static_cast<int *>(sync) = descriptor;
  if (std::strcmp(vendor(nullptr, 0), "NVIDIA") == 0) {
    std::thread([descriptor] {
      std::this_thread::sleep_for(std::chrono::milliseconds(80));
      const eventfd_t value = 1;
      eventfd_write(descriptor, value);
    }).detach();
  }
  return descriptor;
}
} // namespace

using Proc = void (*)();
extern "C" Proc eglGetProcAddress(const char *name) {
  if (std::strcmp(name, "eglDupNativeFenceFDANDROID") == 0)
    return reinterpret_cast<Proc>(duplicate);
  if (std::strcmp(name, "eglQueryString") == 0)
    return reinterpret_cast<Proc>(vendor);
  return nullptr;
}
