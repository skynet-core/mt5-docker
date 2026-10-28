// SPDX-License-Identifier: GPL-2.0-or-later
// Validate readiness at buffer publication, descriptor ownership and child env.
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <stdlib.h>
#include <sys/poll.h>
#include <unistd.h>

int main(int argc, char **argv) {
  assert(argc == 2);
  setenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD", "caller-preload.so", 1);
  void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(library);
  const char *preload = std::getenv("LD_PRELOAD");
  assert(preload && std::strcmp(preload, "caller-preload.so") == 0);
  const char *originalPreload = std::getenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  assert(!originalPreload);
  using Duplicate = int (*)(void *, void *);
  auto symbol = static_cast<Duplicate *>(
      dlsym(library, "epoxy_eglDupNativeFenceFDANDROID"));
  assert(symbol);
  int original = -1;
  const int descriptor = (*symbol)(nullptr, &original);
  assert(descriptor >= 0 && descriptor == original);
  pollfd fence{descriptor, POLLIN, 0};
  const bool ready = poll(&fence, 1, 0) == 1 && fence.revents == POLLIN;
  const bool nvidia =
      std::strcmp(std::getenv("FAKE_EGL_VENDOR"), "NVIDIA") == 0;
  assert(ready == nvidia);
  close(descriptor);
  dlclose(library);
}
