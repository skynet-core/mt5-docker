// SPDX-License-Identifier: GPL-2.0-or-later
#include <X11/Xlib.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>

extern "C" void __gcov_dump();
extern "C" [[noreturn]] void abort() noexcept {
  __gcov_dump();
  _Exit(99);
}

int main(int argc, char **argv) {
  assert(argc == 3);
  void *shim = dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL);
  assert(shim);
  const char *scenario = std::getenv("NATIVE_CASE");
  void *provider = nullptr;
  if (!scenario || std::strcmp(scenario, "xtest-missing") != 0)
    provider = dlopen(argv[2], RTLD_NOW | RTLD_GLOBAL);
  using Key = Bool (*)(Display *, unsigned, Bool, unsigned long);
  auto key = reinterpret_cast<Key>(dlsym(shim, "XTestFakeKeyEvent"));
  assert(key && key(nullptr, 42, True, 0) == True);
  using Observe = unsigned long (*)(unsigned);
  auto observe = reinterpret_cast<Observe>(dlsym(provider, "observed"));
  assert(observe);
  assert(observe(0) == 42 && observe(1) == True && observe(2) == 5);
  assert(key(nullptr, 40, False, 12) == False);
  assert(observe(0) == 40 && observe(1) == False && observe(2) == 12);
  dlclose(provider);
  dlclose(shim);
}
