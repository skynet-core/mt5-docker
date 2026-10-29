// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the actual compatibility source against controlled Wayland ABI
// boundaries; a separate socketpair integration test uses the real libraries.
#include <array>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <string>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <vector>
#include <wayland-client-protocol.h>

extern "C" void __gcov_dump();

bool scenario(const char *name) {
  const char *selected = std::getenv("NATIVE_CASE");
  return selected && std::strcmp(selected, name) == 0;
}

extern "C" [[noreturn]] void abort() noexcept {
  __gcov_dump();
  _Exit(99);
}

extern "C" int setenv(const char *name, const char *value, int replace) noexcept {
  if (scenario("restore-failed") && std::strcmp(name, "LD_PRELOAD") == 0)
    return -1;
  using Function = int (*)(const char *, const char *, int);
  const auto original =
      reinterpret_cast<Function>(dlvsym(RTLD_NEXT, "setenv", "GLIBC_2.2.5"));
  return original(name, value, replace);
}

extern "C" int clock_gettime(clockid_t clock, timespec *now) noexcept {
  assert(clock == CLOCK_MONOTONIC);
  if (scenario("clock-failed"))
    return -1;
  *now = {12, 345000000};
  return 0;
}

struct Proxy {
  const char *name = nullptr;
  uint32_t version = 11;
  void *data = nullptr;
  const void *implementation = nullptr;
  wl_dispatcher_func_t dispatcher = nullptr;
};

extern "C" const char *wl_proxy_get_class(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->name;
}
extern "C" void *wl_proxy_get_user_data(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->data;
}
extern "C" uint32_t wl_proxy_get_version(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->version;
}

int addDispatcher(wl_proxy *proxy, wl_dispatcher_func_t dispatcher,
                  const void *implementation, void *data) {
  auto *state = reinterpret_cast<Proxy *>(proxy);
  if (state->implementation)
    return -1;
  state->dispatcher = dispatcher;
  state->implementation = implementation;
  state->data = data;
  return 0;
}
int addListener(wl_proxy *proxy, void (**implementation)(void), void *data) {
  auto *state = reinterpret_cast<Proxy *>(proxy);
  state->implementation = implementation;
  state->data = data;
  return -7;
}

extern "C" void *dlsym(void *library, const char *name) noexcept {
  assert(library == RTLD_NEXT);
  if (std::strcmp(name, "wl_proxy_add_dispatcher") == 0)
    return scenario("dispatcher-missing") ? nullptr
        : reinterpret_cast<void *>(addDispatcher);
  assert(std::strcmp(name, "wl_proxy_add_listener") == 0);
  return scenario("listener-missing") ? nullptr
      : reinterpret_cast<void *>(addListener);
}

#include "../../vendor/kde-container/compat/wayland-pointer-enter.cpp"

struct Event {
  uint32_t opcode;
  void *data;
  wl_pointer *pointer;
  std::array<int64_t, 4> values;
};
std::vector<Event> events;
void record(uint32_t opcode, void *data, wl_pointer *pointer,
            std::array<int64_t, 4> values = {}) {
  events.push_back({opcode, data, pointer, values});
}
void entered(void *data, wl_pointer *pointer, uint32_t serial, wl_surface *surface,
             wl_fixed_t x, wl_fixed_t y) {
  record(0, data, pointer, {serial, reinterpret_cast<intptr_t>(surface), x, y});
}
void left(void *data, wl_pointer *pointer, uint32_t serial, wl_surface *surface) {
  record(1, data, pointer, {serial, reinterpret_cast<intptr_t>(surface)});
}
void moved(void *data, wl_pointer *pointer, uint32_t time, wl_fixed_t x,
           wl_fixed_t y) {
  record(2, data, pointer, {time, x, y});
}
void button(void *data, wl_pointer *pointer, uint32_t serial, uint32_t time,
            uint32_t code, uint32_t state) {
  record(3, data, pointer, {serial, time, code, state});
}
void axis(void *data, wl_pointer *pointer, uint32_t time, uint32_t direction,
          wl_fixed_t value) {
  record(4, data, pointer, {time, direction, value});
}
void frame(void *data, wl_pointer *pointer) { record(5, data, pointer); }
void source(void *data, wl_pointer *pointer, uint32_t value) {
  record(6, data, pointer, {value});
}
void stop(void *data, wl_pointer *pointer, uint32_t time, uint32_t direction) {
  record(7, data, pointer, {time, direction});
}
void discrete(void *data, wl_pointer *pointer, uint32_t direction, int32_t value) {
  record(8, data, pointer, {direction, value});
}
#ifdef WL_POINTER_AXIS_VALUE120_SINCE_VERSION
void value120(void *data, wl_pointer *pointer, uint32_t direction, int32_t value) {
  record(9, data, pointer, {direction, value});
}
#endif
#ifdef WL_POINTER_AXIS_RELATIVE_DIRECTION_SINCE_VERSION
void relative(void *data, wl_pointer *pointer, uint32_t direction, uint32_t value) {
  record(10, data, pointer, {direction, value});
}
#endif
void warped(void *data, wl_pointer *pointer, wl_fixed_t x, wl_fixed_t y) {
  record(11, data, pointer, {x, y});
}
struct CurrentListener {
  wl_pointer_listener base{};
#ifndef WL_POINTER_WARP_SINCE_VERSION
  void (*warp)(void *, wl_pointer *, wl_fixed_t, wl_fixed_t) = nullptr;
#endif
};
static_assert(sizeof(CurrentListener) == 12 * sizeof(void (*)(void)),
              "New core pointer callbacks require dispatcher coverage");

void verifyChildEnvironment(const std::string &expected) {
  int descriptors[2];
  assert(pipe(descriptors) == 0);
  const pid_t child = fork();
  assert(child >= 0);
  if (child == 0) {
    assert(dup2(descriptors[1], STDOUT_FILENO) == STDOUT_FILENO);
    close(descriptors[0]);
    close(descriptors[1]);
    __gcov_dump();
    execl("/usr/bin/python3", "python3", "-c",
          "import os; print(os.getenv('LD_PRELOAD', '')); "
          "print(os.getenv('PLASMA_KWIN_ORIGINAL_LD_PRELOAD', 'MISSING'))",
          nullptr);
    _Exit(127);
  }
  close(descriptors[1]);
  std::string output;
  char buffer[1024];
  ssize_t count;
  while ((count = read(descriptors[0], buffer, sizeof(buffer))) > 0)
    output.append(buffer, static_cast<size_t>(count));
  assert(count == 0);
  close(descriptors[0]);
  int result;
  assert(waitpid(child, &result, 0) == child && result == 0);
  assert(output == expected + "\nMISSING\n");
}

int main() {
  const char *expected = std::getenv("EXPECTED_PRELOAD");
  const char *actual = std::getenv("LD_PRELOAD");
  assert(std::string(actual ? actual : "") == (expected ? expected : ""));
  const char *saved = std::getenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  assert(!saved);
  verifyChildEnvironment(expected ? expected : "");
  // Fail on newly installed core events instead of silently skipping them.
  assert(wl_pointer_interface.event_count >= 9 &&
         wl_pointer_interface.event_count <= 12);
  CurrentListener storage;
  auto &listener = storage.base;
  listener.enter = entered;
  listener.leave = left;
  listener.motion = moved;
  listener.button = button;
  listener.axis = axis;
  listener.frame = frame;
  listener.axis_source = source;
  listener.axis_stop = stop;
  listener.axis_discrete = discrete;
#ifdef WL_POINTER_AXIS_VALUE120_SINCE_VERSION
  listener.axis_value120 = value120;
#endif
#ifdef WL_POINTER_AXIS_RELATIVE_DIRECTION_SINCE_VERSION
  listener.axis_relative_direction = relative;
#endif
#ifdef WL_POINTER_WARP_SINCE_VERSION
  listener.warp = warped;
#else
  storage.warp = warped;
#endif
  auto **implementation = reinterpret_cast<void (**)(void)>(&listener);
  int first = 1, current = 2;
  Proxy other{"wl_seat"};
  assert(wl_proxy_add_listener(reinterpret_cast<wl_proxy *>(&other),
                               implementation, &first) == -7);
  assert(other.implementation == &listener && other.data == &first &&
         !other.dispatcher);
  Proxy pointer{"wl_pointer"};
  auto *proxy = reinterpret_cast<wl_proxy *>(&pointer);
  assert(wl_proxy_add_listener(proxy, implementation, &first) == 0);
  assert(pointer.implementation == &listener && pointer.data == &first &&
         pointer.dispatcher);
  assert(wl_proxy_add_listener(proxy, implementation, &current) == -1);
  assert(pointer.data == &first);
  pointer.data = &current;
  wl_argument arguments[4]{};
  arguments[0].u = 17;
  arguments[1].o = reinterpret_cast<wl_object *>(0x1234);
  arguments[2].f = wl_fixed_from_double(37.25);
  arguments[3].f = wl_fixed_from_double(81.5);
  assert(pointer.dispatcher(&listener, &pointer, 0, nullptr, arguments) == 0);
  assert(events.size() == 2 && events[0].opcode == 0 && events[1].opcode == 2);
  assert((events[0].values == std::array<int64_t, 4>{17, 0x1234,
          arguments[2].f, arguments[3].f}));
  assert((events[1].values == std::array<int64_t, 4>{12345,
          arguments[2].f, arguments[3].f}));
  for (const auto &event : events)
    assert(event.data == &current && event.pointer ==
           reinterpret_cast<wl_pointer *>(&pointer));
  events.clear();
  assert(pointer.dispatcher(&listener, &pointer, 1, nullptr, arguments) == 0);
  assert(events.size() == 1);
  assert((events.at(0).values == std::array<int64_t, 4>{17, 0x1234}));
  arguments[1].i = -120;
  arguments[2].i = -384;
  arguments[3].u = 1;
  const wl_message warpMessage{"warp", "11ff", nullptr};
  for (uint32_t opcode = 2; opcode <= 11; ++opcode) {
#ifndef WL_POINTER_AXIS_VALUE120_SINCE_VERSION
    if (opcode == 9)
      continue;
#endif
#ifndef WL_POINTER_AXIS_RELATIVE_DIRECTION_SINCE_VERSION
    if (opcode == 10)
      continue;
#endif
    assert(pointer.dispatcher(&listener, &pointer, opcode,
                              opcode == 11 ? &warpMessage : nullptr,
                              arguments) == 0);
    const auto &event = events.at(events.size() - 1);
    assert(event.opcode == opcode && event.data == &current &&
           event.pointer == reinterpret_cast<wl_pointer *>(&pointer));
    const std::array<int64_t, 4> values[] = {
        {17, -120, -384}, {17, UINT32_MAX - 119, UINT32_MAX - 383, 1},
        {17, UINT32_MAX - 119, -384}, {}, {17}, {17, UINT32_MAX - 119},
        {17, -120}, {17, -120}, {17, UINT32_MAX - 119}, {17, -120}};
    assert(event.values == values[opcode - 2]);
  }
  const size_t count = events.size();
  CurrentListener emptyStorage;
  auto &empty = emptyStorage.base;
  for (uint32_t opcode = 0; opcode <= 11; ++opcode)
    pointer.dispatcher(&empty, &pointer, opcode,
                       opcode == 11 ? &warpMessage : nullptr, arguments);
  assert(events.size() == count);
  assert(pointer.dispatcher(&listener, &pointer, 99, nullptr, arguments) == -1);
  pointer.version = 5;
  assert(pointer.dispatcher(&listener, &pointer, 11, &warpMessage, arguments) == -1);
  pointer.version = 11;
  assert(pointer.dispatcher(&listener, &pointer, 11, nullptr, arguments) == -1);
  const wl_message wrongName{"motion", "11ff", nullptr};
  const wl_message wrongSignature{"warp", "11uf", nullptr};
  assert(pointer.dispatcher(&listener, &pointer, 11, &wrongName, arguments) == -1);
  assert(pointer.dispatcher(&listener, &pointer, 11, &wrongSignature, arguments) == -1);
  // A listener that only needs enter receives it without a synthetic motion.
  empty.enter = entered;
  assert(pointer.dispatcher(&empty, &pointer, 0, nullptr, arguments) == 0);
  assert(events.size() == count + 1 && events.at(count).opcode == 0);
}
