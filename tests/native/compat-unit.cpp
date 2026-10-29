// SPDX-License-Identifier: GPL-2.0-or-later
// Faults are injected at dependency boundaries; the production shims are loaded
// unchanged.
#include <X11/Xlib.h>
#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <sys/poll.h>
#include <unistd.h>

extern "C" void __gcov_dump();

bool scenario(const char *name) {
  const char *selected = std::getenv("NATIVE_CASE");
  return selected && std::strcmp(selected, name) == 0;
}

// A controlled fatal shim failure must flush its counters before this test
// exits.
extern "C" [[noreturn]] void abort() noexcept {
  __gcov_dump();
  _Exit(99);
}

extern "C" int setenv(const char *name, const char *value,
                      int replace) noexcept {
  if (scenario("restore-failed") && std::strcmp(name, "LD_PRELOAD") == 0)
    return -1;
  using Original = int (*)(const char *, const char *, int);
  const auto original =
      reinterpret_cast<Original>(dlvsym(RTLD_NEXT, "setenv", "GLIBC_2.2.5"));
  return original(name, value, replace);
}

using Proc = void (*)();
Proc missingApi(const char *) { return nullptr; }

extern "C" void *dlopen(const char *name, int flags) noexcept {
  if (scenario("egl-open") && name && std::strcmp(name, "libEGL.so.1") == 0)
    return nullptr;
  using Original = void *(*)(const char *, int);
  const auto original =
      reinterpret_cast<Original>(dlvsym(RTLD_NEXT, "dlopen", "GLIBC_2.2.5"));
  return original(name, flags);
}

extern "C" void *dlsym(void *library, const char *name) noexcept {
  if (std::strcmp(name, "eglGetProcAddress") == 0) {
    if (scenario("get-proc"))
      return nullptr;
    if (scenario("fence-api"))
      return reinterpret_cast<void *>(missingApi);
  }
  using Original = void *(*)(void *, const char *);
  const auto original =
      reinterpret_cast<Original>(dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5"));
  return original(library, name);
}

extern "C" int poll(pollfd *descriptors, nfds_t count, int timeout) {
  if (scenario("fence-error")) {
    errno = EIO;
    return -1;
  }
  static bool interrupted = false;
  if (scenario("fence-eintr") && !interrupted) {
    interrupted = true;
    errno = EINTR;
    return -1;
  }
  using Original = int (*)(pollfd *, nfds_t, int);
  const auto original =
      reinterpret_cast<Original>(dlvsym(RTLD_NEXT, "poll", "GLIBC_2.2.5"));
  return original(descriptors, count, timeout);
}

int main(int argc, char **argv) {
  assert(argc == 2);
  if (!scenario("original-absent"))
    setenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD",
           scenario("original-empty") ? "" : "caller.so", 1);
  void *shim = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(shim);
  const char *preload = std::getenv("LD_PRELOAD");
  if (scenario("original-empty"))
    assert(!preload);
  const char *previous = std::getenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  assert(!previous);
  using Duplicate = int (*)(void *, void *);
  auto symbol =
      static_cast<Duplicate *>(dlsym(shim, "epoxy_eglDupNativeFenceFDANDROID"));
  assert(symbol);
  int original = -1;
  const int descriptor = (*symbol)(nullptr, &original);
  assert(descriptor >= 0 && descriptor == original);
  close(descriptor);
  void *egl = dlopen("libEGL.so.1", RTLD_NOW | RTLD_LOCAL);
  assert(egl);
  const auto getProc =
      reinterpret_cast<Proc (*)(const char *)>(dlsym(egl, "eglGetProcAddress"));
  assert(getProc && !getProc("unknown-procedure"));
  dlclose(egl);
  dlclose(shim);
}
