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
  if (!scenario || std::strncmp(scenario, "xtest-missing", 13) != 0)
    provider = dlopen(argv[2], RTLD_NOW | RTLD_GLOBAL);
  using Key = Bool (*)(Display *, unsigned, Bool, unsigned long);
  using Motion = Bool (*)(Display *, int, int, int, unsigned long);
  auto key = reinterpret_cast<Key>(dlsym(shim, "XTestFakeKeyEvent"));
  auto button = reinterpret_cast<Key>(dlsym(shim, "XTestFakeButtonEvent"));
  auto motion = reinterpret_cast<Motion>(dlsym(shim, "XTestFakeMotionEvent"));
  assert(key && button && motion);
  auto *display = reinterpret_cast<Display *>(0x1234);
  if (scenario && std::strcmp(scenario, "xtest-missing-button") == 0)
    button(display, 1, True, 0);
  if (scenario && std::strcmp(scenario, "xtest-missing-motion") == 0)
    motion(display, -1, 200, 300, 0);
  assert(key(display, 42, True, 0) == True);
  using Observe = unsigned long (*)(unsigned);
  auto observe = reinterpret_cast<Observe>(dlsym(provider, "observed"));
  assert(observe);
  const char *software = std::getenv("PLASMA_SOFTWARE_CAPTURE");
  const bool paced = software && std::strcmp(software, "1") == 0;
  assert(observe(0) == 42 && observe(1) == True &&
         observe(2) == (paced ? 5 : 0));
  assert(observe(3) == 0x1234 && observe(6) == 0);
  assert(key(display, 40, False, 12) == False);
  assert(observe(0) == 40 && observe(1) == False && observe(2) == 12);
  assert(button(display, 3, True, 17) == True);
  assert(observe(0) == 3 && observe(1) == True && observe(2) == 17);
  assert(observe(3) == 0x1234 && observe(5) == 0x1234 && observe(6) == 1);
  assert(button(display, 2, False, 0) == False && observe(6) == 1);
  assert(observe(0) == 2 && observe(1) == False && observe(2) == 0);
  assert(motion(display, -1, 321, -70, 19) == True);
  assert(observe(0) == static_cast<unsigned long>(-1));
  assert(observe(1) == 321 && observe(2) == static_cast<unsigned long>(-70));
  assert(observe(3) == 0x1234 && observe(4) == 19 && observe(6) == 2);
  assert(motion(display, 2, -12, 640, 0) == False && observe(6) == 2);
  assert(observe(0) == 2 && observe(1) == static_cast<unsigned long>(-12));
  assert(observe(2) == 640 && observe(4) == 0);
  dlclose(provider);
  dlclose(shim);
}
