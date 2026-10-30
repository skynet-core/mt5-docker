// SPDX-License-Identifier: GPL-2.0-or-later
// A private Wayland server sends enter and clicks without an intervening motion.
// No compositor, host display, input device, or clipboard is opened.
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>
#include <wayland-client-protocol.h>
#include <wayland-server-protocol.h>

// The fixture implements only the protocol requests exercised on this socket.
extern const wl_interface lockedInterface, relativeInterface;
const wl_interface *lockTypes[] = {
    &lockedInterface, &wl_surface_interface, &wl_pointer_interface,
    &wl_region_interface, nullptr};
const wl_message constraintRequests[] = {
    {"destroy", "", nullptr}, {"lock_pointer", "noo?ou", lockTypes}};
const wl_message destroyRequest[] = {{"destroy", "", nullptr}};
const wl_message lockEvents[] = {
    {"locked", "", nullptr}, {"unlocked", "", nullptr}};
const wl_interface lockedInterface{
    "zwp_locked_pointer_v1", 1, 1, destroyRequest, 2, lockEvents};
const wl_interface constraintsInterface{
    "zwp_pointer_constraints_v1", 1, 2, constraintRequests, 0, nullptr};
const wl_interface *relativeTypes[] = {&relativeInterface, &wl_pointer_interface};
const wl_message relativeRequests[] = {
    {"destroy", "", nullptr}, {"get_relative_pointer", "no", relativeTypes}};
const wl_message relativeEvents[] = {{"relative_motion", "uuffff", nullptr}};
const wl_interface relativeInterface{
    "zwp_relative_pointer_v1", 1, 1, destroyRequest, 1, relativeEvents};
const wl_interface relativeManagerInterface{
    "zwp_relative_pointer_manager_v1", 1, 2, relativeRequests, 0, nullptr};

struct Server {
  wl_display *display = nullptr;
  wl_resource *surface = nullptr;
  wl_resource *pointer = nullptr;
  wl_resource *lock = nullptr;
  wl_resource *relative = nullptr;
  uint32_t version = 5;
};
void destroyResource(wl_client *, wl_resource *resource) {
  wl_resource_destroy(resource);
}
void createSurface(wl_client *client, wl_resource *compositor, uint32_t id) {
  auto *server = static_cast<Server *>(wl_resource_get_user_data(compositor));
  static const auto implementation = [] {
    struct wl_surface_interface value{};
    value.destroy = destroyResource;
    return value;
  }();
  server->surface = wl_resource_create(client, &wl_surface_interface, 1, id);
  assert(server->surface);
  wl_resource_set_implementation(server->surface, &implementation, server, nullptr);
}
void bindCompositor(wl_client *client, void *data, uint32_t, uint32_t id) {
  static const auto implementation = [] {
    struct wl_compositor_interface value{};
    value.create_surface = createSurface;
    return value;
  }();
  auto *resource = wl_resource_create(client, &wl_compositor_interface, 1, id);
  assert(resource);
  wl_resource_set_implementation(resource, &implementation, data, nullptr);
}
void createPointer(wl_client *client, wl_resource *seat, uint32_t id) {
  auto *server = static_cast<Server *>(wl_resource_get_user_data(seat));
  static const struct wl_pointer_interface implementation{nullptr, destroyResource};
  server->pointer = wl_resource_create(client, &wl_pointer_interface,
                                       wl_resource_get_version(seat), id);
  assert(server->pointer);
  wl_resource_set_implementation(server->pointer, &implementation, server, nullptr);
}
void bindSeat(wl_client *client, void *data, uint32_t, uint32_t id) {
  static const struct wl_seat_interface implementation{
      createPointer, nullptr, nullptr, destroyResource};
  auto *resource = wl_resource_create(
      client, &wl_seat_interface, static_cast<Server *>(data)->version, id);
  assert(resource);
  wl_resource_set_implementation(resource, &implementation, data, nullptr);
  wl_seat_send_capabilities(resource, WL_SEAT_CAPABILITY_POINTER);
  wl_seat_send_name(resource, "isolated-pointer-test");
}
void createLock(wl_client *client, wl_resource *manager, uint32_t id,
                wl_resource *surface, wl_resource *pointer, wl_resource *region,
                uint32_t lifetime) {
  auto *server = static_cast<Server *>(wl_resource_get_user_data(manager));
  assert(surface == server->surface && pointer == server->pointer && !region);
  assert(lifetime == 2);
  server->lock = wl_resource_create(client, &lockedInterface, 1, id);
  static void (*const implementation[])(void) = {
      reinterpret_cast<void (*)(void)>(destroyResource)};
  wl_resource_set_implementation(server->lock, implementation, server, nullptr);
}
void createRelative(wl_client *client, wl_resource *manager, uint32_t id,
                    wl_resource *pointer) {
  auto *server = static_cast<Server *>(wl_resource_get_user_data(manager));
  assert(pointer == server->pointer);
  server->relative = wl_resource_create(client, &relativeInterface, 1, id);
  static void (*const implementation[])(void) = {
      reinterpret_cast<void (*)(void)>(destroyResource)};
  wl_resource_set_implementation(server->relative, implementation, server, nullptr);
}
void bindConstraints(wl_client *client, void *data, uint32_t, uint32_t id) {
  auto *resource = wl_resource_create(client, &constraintsInterface, 1, id);
  static void (*const implementation[])(void) = {
      reinterpret_cast<void (*)(void)>(destroyResource),
      reinterpret_cast<void (*)(void)>(createLock)};
  wl_resource_set_implementation(resource, implementation, data, nullptr);
}
void bindRelative(wl_client *client, void *data, uint32_t, uint32_t id) {
  auto *resource = wl_resource_create(client, &relativeManagerInterface, 1, id);
  static void (*const implementation[])(void) = {
      reinterpret_cast<void (*)(void)>(destroyResource),
      reinterpret_cast<void (*)(void)>(createRelative)};
  wl_resource_set_implementation(resource, implementation, data, nullptr);
}
int commands(int descriptor, uint32_t, void *data) {
  auto *server = static_cast<Server *>(data);
  char command;
  assert(read(descriptor, &command, 1) == 1);
  if (command == 'Q') {
    wl_display_terminate(server->display);
  } else if (command == 'L' || command == 'U') {
    assert(server->lock && server->relative);
    if (command == 'L') {
      wl_pointer_send_motion(server->pointer, 101,
          wl_fixed_from_double(100), wl_fixed_from_double(100));
      wl_resource_post_event(server->lock, 0);
      wl_resource_post_event(server->relative, 0, 0u, 102u,
          wl_fixed_from_double(17), wl_fixed_from_double(-9),
          wl_fixed_from_double(17), wl_fixed_from_double(-9));
      wl_pointer_send_leave(server->pointer, 21, server->surface);
    } else {
      wl_resource_post_event(server->lock, 1);
    }
    // Lock freezes the host coordinate while its relative cursor has moved.
    wl_pointer_send_enter(server->pointer, 22, server->surface,
        wl_fixed_from_double(command == 'L' ? 100 : 200),
        wl_fixed_from_double(command == 'L' ? 100 : 150));
    wl_pointer_send_button(server->pointer, 23, 103, 0x110,
                           WL_POINTER_BUTTON_STATE_PRESSED);
    wl_pointer_send_button(server->pointer, 24, 104, 0x110,
                           WL_POINTER_BUTTON_STATE_RELEASED);
    wl_pointer_send_frame(server->pointer);
    wl_display_flush_clients(server->display);
  } else {
    assert(command == 'E' && server->surface && server->pointer);
    const std::pair<double, double> points[] = {{37.25, 81.5}, {361.75, 19.25}};
    for (const auto &point : points) {
      wl_pointer_send_enter(server->pointer, 17, server->surface,
                            wl_fixed_from_double(point.first),
                            wl_fixed_from_double(point.second));
      // Deliberately no wl_pointer.motion: the next physical event is a click.
      wl_pointer_send_button(server->pointer, 18, 99, 0x110,
                             WL_POINTER_BUTTON_STATE_PRESSED);
      wl_pointer_send_button(server->pointer, 19, 100, 0x110,
                             WL_POINTER_BUTTON_STATE_RELEASED);
      wl_pointer_send_frame(server->pointer);
      if (point == points[0])
        wl_pointer_send_leave(server->pointer, 20, server->surface);
    }
#ifdef WL_POINTER_WARP_SINCE_VERSION
    if (server->version >= WL_POINTER_WARP_SINCE_VERSION) {
      wl_pointer_send_warp(server->pointer, wl_fixed_from_double(77.5),
                           wl_fixed_from_double(91.25));
      wl_pointer_send_frame(server->pointer);
    }
#endif
    wl_display_flush_clients(server->display);
  }
  return 0;
}

struct Client {
  wl_compositor *compositor = nullptr;
  wl_seat *seat = nullptr;
  wl_proxy *constraints = nullptr;
  wl_proxy *relativeManager = nullptr;
  bool capability = false;
  bool named = false;
  uint32_t version = 5;
};
void capabilities(void *data, wl_seat *, uint32_t value) {
  static_cast<Client *>(data)->capability = value == WL_SEAT_CAPABILITY_POINTER;
}
void seatName(void *data, wl_seat *, const char *value) {
  static_cast<Client *>(data)->named = std::strcmp(value, "isolated-pointer-test") == 0;
}
void global(void *data, wl_registry *registry, uint32_t id, const char *name,
            uint32_t) {
  auto *client = static_cast<Client *>(data);
  if (std::strcmp(name, "wl_compositor") == 0)
    client->compositor = static_cast<wl_compositor *>(
        wl_registry_bind(registry, id, &wl_compositor_interface, 1));
  if (std::strcmp(name, "wl_seat") == 0) {
    client->seat = static_cast<wl_seat *>(
        wl_registry_bind(registry, id, &wl_seat_interface, client->version));
    static const wl_seat_listener listener{capabilities, seatName};
    assert(wl_seat_add_listener(client->seat, &listener, client) == 0);
  }
  if (std::strcmp(name, constraintsInterface.name) == 0)
    client->constraints = static_cast<wl_proxy *>(
        wl_registry_bind(registry, id, &constraintsInterface, 1));
  if (std::strcmp(name, relativeManagerInterface.name) == 0)
    client->relativeManager = static_cast<wl_proxy *>(
        wl_registry_bind(registry, id, &relativeManagerInterface, 1));
}
void globalRemoved(void *, wl_registry *, uint32_t) {}
struct Position {
  std::string events;
  std::pair<double, double> position{-20, -30};
  std::vector<std::pair<double, double>> clicks;
};
void enter(void *data, wl_pointer *, uint32_t, wl_surface *, wl_fixed_t, wl_fixed_t) {
  static_cast<Position *>(data)->events += 'E';
}
void leave(void *data, wl_pointer *, uint32_t, wl_surface *) {
  static_cast<Position *>(data)->events += 'L';
}
void motion(void *data, wl_pointer *, uint32_t, wl_fixed_t x, wl_fixed_t y) {
  auto *state = static_cast<Position *>(data);
  state->events += 'M';
  state->position = {wl_fixed_to_double(x), wl_fixed_to_double(y)};
}
void button(void *data, wl_pointer *, uint32_t, uint32_t, uint32_t code,
            uint32_t value) {
  auto *state = static_cast<Position *>(data);
  assert(code == 0x110);
  state->events += value == WL_POINTER_BUTTON_STATE_PRESSED ? 'D' : 'U';
  state->clicks.push_back(state->position);
}
void frame(void *data, wl_pointer *) {
  static_cast<Position *>(data)->events += 'F';
}
void locked(void *data, wl_proxy *) {
  static_cast<Position *>(data)->events += 'K';
}
void unlocked(void *data, wl_proxy *) {
  static_cast<Position *>(data)->events += 'N';
}
void relativeMotion(void *data, wl_proxy *, uint32_t, uint32_t,
                    wl_fixed_t dx, wl_fixed_t dy, wl_fixed_t, wl_fixed_t) {
  auto *state = static_cast<Position *>(data);
  state->events += 'R';
  state->position.first += wl_fixed_to_double(dx);
  state->position.second += wl_fixed_to_double(dy);
}
#ifdef WL_POINTER_WARP_SINCE_VERSION
void warp(void *data, wl_pointer *, wl_fixed_t x, wl_fixed_t y) {
  auto *state = static_cast<Position *>(data);
  state->events += 'W';
  state->position = {wl_fixed_to_double(x), wl_fixed_to_double(y)};
}
#endif

int main(int argc, char **argv) {
  assert(argc == 1 || argc == 2);
  const std::string mode = argc == 2 ? argv[1] : "fixed";
  const bool patched = mode == "fixed" || mode == "warp";
  const bool withWarp = mode == "warp" || mode == "warp-baseline";
  assert(patched || mode == "baseline" || mode == "warp-baseline");
#ifndef WL_POINTER_WARP_SINCE_VERSION
  if (withWarp)
    return 77;
#endif
  const uint32_t version = withWarp ? 11 : 5;
  const char *preload = std::getenv("LD_PRELOAD");
  const char *saved = std::getenv("PLASMA_KWIN_ORIGINAL_LD_PRELOAD");
  assert(!preload && !saved);
  int sockets[2], mailbox[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0);
  assert(pipe(mailbox) == 0);
  Server server{wl_display_create()};
  server.version = version;
  assert(server.display);
  auto *serverClient = wl_client_create(server.display, sockets[0]);
  if (!serverClient)
    std::perror("private Wayland client");
  assert(serverClient);
  assert(wl_global_create(server.display, &wl_compositor_interface, 1, &server,
                          bindCompositor));
  assert(wl_global_create(server.display, &wl_seat_interface, version, &server, bindSeat));
  assert(wl_global_create(server.display, &constraintsInterface, 1, &server, bindConstraints));
  assert(wl_global_create(server.display, &relativeManagerInterface, 1, &server, bindRelative));
  auto *source = wl_event_loop_add_fd(wl_display_get_event_loop(server.display),
                                     mailbox[0], WL_EVENT_READABLE, commands, &server);
  assert(source);
  std::thread worker([&] { wl_display_run(server.display); });
  auto *display = wl_display_connect_to_fd(sockets[1]);
  assert(display);
  Client client;
  client.version = version;
  auto *registry = wl_display_get_registry(display);
  const wl_registry_listener registryListener{global, globalRemoved};
  assert(wl_registry_add_listener(registry, &registryListener, &client) == 0);
  assert(wl_display_roundtrip(display) >= 0 && client.compositor && client.seat);
  auto *surface = wl_compositor_create_surface(client.compositor);
  auto *pointer = wl_seat_get_pointer(client.seat);
  wl_pointer_listener listener{};
  listener.enter = enter;
  listener.leave = leave;
  listener.motion = motion;
  listener.button = button;
  listener.frame = frame;
#ifdef WL_POINTER_WARP_SINCE_VERSION
  listener.warp = warp;
#endif
  Position original, current;
  assert(wl_pointer_add_listener(pointer, &listener, &original) == 0);
  assert(wl_proxy_get_listener(reinterpret_cast<wl_proxy *>(pointer)) ==
         reinterpret_cast<void (**)(void)>(&listener));
  wl_pointer_set_user_data(pointer, &current);
  assert(wl_display_roundtrip(display) >= 0 && client.capability && client.named);
  assert(write(mailbox[1], "E", 1) == 1);
  const std::string expectedEvents =
      std::string(patched ? "EMDUFLEMDUF" : "EDUFLEDUF") + (withWarp ? "WF" : "");
  while (current.events.size() < expectedEvents.size())
    assert(wl_display_dispatch(display) >= 0);
  assert(original.events.empty());
  assert(current.events == expectedEvents);
  if (withWarp)
    assert((current.position == std::pair<double, double>{77.5, 91.25}));
  const std::vector<std::pair<double, double>> expected = patched
      ? std::vector<std::pair<double, double>>{{37.25, 81.5}, {37.25, 81.5},
                                               {361.75, 19.25}, {361.75, 19.25}}
      : std::vector<std::pair<double, double>>(4, {-20, -30});
  assert(current.clicks == expected);
  auto *lock = wl_proxy_marshal_flags(client.constraints, 1, &lockedInterface,
      1, 0, nullptr, surface, pointer, nullptr, 2u);
  auto *relative = wl_proxy_marshal_flags(client.relativeManager, 1,
      &relativeInterface, 1, 0, nullptr, pointer);
  assert(lock && relative);
  void (*lockCallbacks[])(void) = {reinterpret_cast<void (*)(void)>(locked),
                                  reinterpret_cast<void (*)(void)>(unlocked)};
  void (*relativeCallbacks[])(void) = {
      reinterpret_cast<void (*)(void)>(relativeMotion)};
  assert(wl_proxy_add_listener(lock, lockCallbacks, &original) == 0);
  assert(wl_proxy_get_listener(lock) == lockCallbacks);
  wl_proxy_set_user_data(lock, &current);
  assert(wl_proxy_add_listener(relative, relativeCallbacks, &current) == 0);
  assert(wl_display_roundtrip(display) >= 0);
  current.events.clear();
  current.clicks.clear();
  assert(write(mailbox[1], "L", 1) == 1);
  while (current.events.size() < 8)
    assert(wl_display_dispatch(display) >= 0);
  assert(current.events == "MKRLEDUF");
  assert((current.clicks == std::vector<std::pair<double, double>>(2, {117, 91})));
  assert(original.events.empty());
  current.events.clear();
  current.clicks.clear();
  assert(write(mailbox[1], "U", 1) == 1);
  const std::string unlockedEvents = patched ? "NEMDUF" : "NEDUF";
  while (current.events.size() < unlockedEvents.size())
    assert(wl_display_dispatch(display) >= 0);
  assert(current.events == unlockedEvents);
  assert((current.clicks == std::vector<std::pair<double, double>>(
      2, patched ? std::pair<double, double>{200, 150} : std::pair<double, double>{117, 91})));
  wl_proxy_marshal_flags(lock, 0, nullptr, 1, WL_MARSHAL_FLAG_DESTROY);
  wl_proxy_marshal_flags(relative, 0, nullptr, 1, WL_MARSHAL_FLAG_DESTROY);
  wl_proxy_marshal_flags(client.constraints, 0, nullptr, 1, WL_MARSHAL_FLAG_DESTROY);
  wl_proxy_marshal_flags(client.relativeManager, 0, nullptr, 1, WL_MARSHAL_FLAG_DESTROY);
  wl_pointer_release(pointer);
  wl_surface_destroy(surface);
  wl_seat_release(client.seat);
  assert(wl_display_roundtrip(display) >= 0);
  wl_compositor_destroy(client.compositor);
  wl_registry_destroy(registry);
  wl_display_disconnect(display);
  assert(write(mailbox[1], "Q", 1) == 1);
  worker.join();
  wl_event_source_remove(source);
  close(mailbox[0]);
  close(mailbox[1]);
  wl_display_destroy_clients(server.display);
  wl_display_destroy(server.display);
}
