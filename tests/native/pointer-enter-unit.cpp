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
#include <wayland-version.h>

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
  uint32_t id = 1;
  wl_display *display = reinterpret_cast<wl_display *>(0x8000);
  const wl_interface *interface = nullptr;
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
extern "C" uint32_t wl_proxy_get_id(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->id;
}
extern "C" wl_display *wl_proxy_get_display(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->display;
}
#if WAYLAND_VERSION_MAJOR > 1 || WAYLAND_VERSION_MINOR >= 24
extern "C" const wl_interface *wl_proxy_get_interface(wl_proxy *proxy) {
  return reinterpret_cast<Proxy *>(proxy)->interface;
}
#endif

wl_proxy *createdProxy = nullptr;
wl_proxy *destroyedProxy = nullptr;
wl_display *disconnectedDisplay = nullptr;
size_t forwardedCount = 0;
std::vector<wl_argument> forwarded;
wl_proxy *marshal(wl_proxy *, uint32_t, const wl_interface *, uint32_t,
                  uint32_t, wl_argument *arguments) {
  forwarded.clear();
  if (forwardedCount)
    forwarded.assign(arguments, arguments + forwardedCount);
  return createdProxy;
}
void destroy(wl_proxy *proxy) { destroyedProxy = proxy; }
void disconnect(wl_display *display) { disconnectedDisplay = display; }

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
  if (std::strcmp(name, "wl_proxy_marshal_array_flags") == 0)
    return reinterpret_cast<void *>(marshal);
  if (std::strcmp(name, "wl_proxy_destroy") == 0)
    return reinterpret_cast<void *>(destroy);
  if (std::strcmp(name, "wl_display_disconnect") == 0)
    return reinterpret_cast<void *>(disconnect);
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
void locked(void *data, wl_proxy *proxy) {
  record(100, data, reinterpret_cast<wl_pointer *>(proxy));
}
void unlocked(void *data, wl_proxy *proxy) {
  record(101, data, reinterpret_cast<wl_pointer *>(proxy));
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

  // Each lock belongs to its actual display/pointer, not every seat in KWin.
  const wl_interface lockInterface{"zwp_locked_pointer_v1", 1, 0, nullptr, 0, nullptr};
  Proxy constraints{"zwp_pointer_constraints_v1"}, surface{"wl_surface"};
  constraints.id = 10;
  surface.id = 11;
  pointer.id = 12;
  Proxy lock{"zwp_locked_pointer_v1"}, second{"zwp_locked_pointer_v1"};
  lock.id = 13;
  second.id = 14;
  wl_argument request[5]{};
  request[1].o = reinterpret_cast<wl_object *>(&surface);
  request[2].o = reinterpret_cast<wl_object *>(&pointer);
  auto create = [&](Proxy &target) {
    createdProxy = reinterpret_cast<wl_proxy *>(&target);
    assert(wl_proxy_marshal_array_flags(reinterpret_cast<wl_proxy *>(&constraints),
        1, &lockInterface, 1, 0, request) == createdProxy);
  };
  auto enterPointer = [&](Proxy &target, size_t expectedEvents) {
    events.clear();
    assert(dispatchPointer(&listener, &target, 0, nullptr, arguments) == 0);
    assert(events.size() == expectedEvents && events.front().opcode == 0);
  };
  void (*lockCallbacks[])(void) = {reinterpret_cast<void (*)(void)>(locked),
                                  reinterpret_cast<void (*)(void)>(unlocked)};
  create(lock);
  assert(locks().objects.size() == 1);
  assert(wl_proxy_add_listener(reinterpret_cast<wl_proxy *>(&lock),
                               lockCallbacks, &first) == 0);
  lock.data = &current;
  enterPointer(pointer, 2); // A requested lock is not active until confirmed.
  assert(lock.dispatcher(lockCallbacks, &lock, 0, nullptr, nullptr) == 0);
  assert(events.back().opcode == 100 && events.back().data == &current);
  enterPointer(pointer, 1);
  Proxy anotherPointer{"wl_pointer"};
  anotherPointer.id = 15;
  enterPointer(anotherPointer, 2);
  anotherPointer.id = pointer.id;
  anotherPointer.display = reinterpret_cast<wl_display *>(0x9000);
  enterPointer(anotherPointer, 2);
  assert(lock.dispatcher(lockCallbacks, &lock, 1, nullptr, nullptr) == 0);
  assert(events.back().opcode == 101 && events.back().data == &current);
  enterPointer(pointer, 2);
  assert(lock.dispatcher(lockCallbacks, &lock, 99, nullptr, nullptr) == -1);
  void (*noCallbacks[])(void) = {nullptr, nullptr};
  assert(lock.dispatcher(noCallbacks, &lock, 0, nullptr, nullptr) == 0);
  enterPointer(pointer, 1);
  create(second);
  assert(dispatchLock(noCallbacks, &second, 0, nullptr, nullptr) == 0);
  assert(lock.dispatcher(lockCallbacks, &lock, 1, nullptr, nullptr) == 0);
  enterPointer(pointer, 1); // Another active lock for this pointer survives.
  createdProxy = nullptr;
  assert(wl_proxy_marshal_array_flags(reinterpret_cast<wl_proxy *>(&second),
      0, nullptr, 1, WL_MARSHAL_FLAG_DESTROY, nullptr) == nullptr);
  enterPointer(pointer, 2);
  wl_proxy_destroy(reinterpret_cast<wl_proxy *>(&lock));
  assert(destroyedProxy == reinterpret_cast<wl_proxy *>(&lock));
  assert(locks().objects.empty());
  create(lock);
  assert(dispatchLock(noCallbacks, &lock, 0, nullptr, nullptr) == 0);
  wl_proxy_destroy(reinterpret_cast<wl_proxy *>(&surface));
  enterPointer(pointer, 2);
  create(lock);
  assert(dispatchLock(noCallbacks, &lock, 0, nullptr, nullptr) == 0);
  wl_proxy_destroy(proxy);
  enterPointer(pointer, 2);
  create(lock);
  assert(dispatchLock(noCallbacks, &lock, 0, nullptr, nullptr) == 0);
  second.display = anotherPointer.display;
  create(second);
  assert(dispatchLock(noCallbacks, &second, 0, nullptr, nullptr) == 0);
  wl_display_disconnect(pointer.display);
  assert(disconnectedDisplay == pointer.display && locks().objects.size() == 1);
  enterPointer(pointer, 2);
  enterPointer(anotherPointer, 1);
  wl_display_disconnect(anotherPointer.display);
  assert(locks().objects.empty());
  // Failed/other constructors and unrelated destructors never leave state.
  assert(dispatchLock(lockCallbacks, &lock, 0, nullptr, nullptr) == 0);
  createdProxy = nullptr;
  wl_proxy_marshal_array_flags(reinterpret_cast<wl_proxy *>(&constraints),
      1, &lockInterface, 1, 0, request);
  createdProxy = proxy;
  wl_proxy_marshal_array_flags(proxy, 1, &lockInterface, 1, 0, request);
  wl_proxy_marshal_array_flags(reinterpret_cast<wl_proxy *>(&constraints),
      0, &lockInterface, 1, 0, request);
  wl_proxy_marshal_array_flags(proxy, 0, &wl_pointer_interface, 1, 0, nullptr);
  wl_proxy_marshal_array_flags(proxy, 0, nullptr, 1, 0, nullptr);
  assert(locks().objects.empty());
#if WAYLAND_VERSION_MAJOR > 1 || WAYLAND_VERSION_MINOR >= 24
  // All public wire argument types, nullable objects and version prefixes.
  const wl_message requests[] = {{"all", "12iufs?onah", nullptr},
                                  {"empty", "", nullptr}};
  const wl_interface requestInterface{"test", 12, 2, requests, 0, nullptr};
  pointer.interface = &requestInterface;
  wl_array array{};
  auto *object = reinterpret_cast<wl_object *>(0x4444);
  forwardedCount = 8;
  assert(wl_proxy_marshal_flags(proxy, 0, nullptr, 12, 0,
      -17, 42u, wl_fixed_from_double(1.5), "value", nullptr, object,
      &array, 9) == proxy);
  assert(forwarded.size() == 8 && forwarded[0].i == -17 &&
         forwarded[1].u == 42 && forwarded[2].f == wl_fixed_from_double(1.5));
  assert(std::strcmp(forwarded[3].s, "value") == 0 && !forwarded[4].o &&
         forwarded[5].o == object && forwarded[6].a == &array &&
         forwarded[7].h == 9);
  forwardedCount = 0;
  assert(wl_proxy_marshal_flags(proxy, 1, nullptr, 12, 0) == proxy);
#endif
}
