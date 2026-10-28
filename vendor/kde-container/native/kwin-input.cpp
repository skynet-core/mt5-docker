// SPDX-License-Identifier: GPL-2.0-or-later
// KRFB input for the container's KWin virtual monitor, independent of XDP
// sessions.
#include "events.h"
#include "eventsplugin.h"
#include "fake-input-client.h"
#include <rfb/rfbproto.h>

#include <KPluginFactory>
#include <QDebug>
#include <QGuiApplication>
#include <QHash>
#include <QObject>
#include <QSocketNotifier>
#include <QVariant>
#include <iterator>
#include <qcontainerfwd.h>
#include <qlogging.h>
#include <qtenvironmentvariables.h>
#include <qtmetamacros.h>
#if __has_include(<qguiapplication_platform.h>)
#include <qguiapplication_platform.h>
#endif
#include <cstdint>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client-core.h>
#include <wayland-client-protocol.h>
#include <wayland-util.h>
#include <xkbcommon/xkbcommon.h>

#include <algorithm>
#include <cstring>

class KwinInput final : public EventHandler {
public:
  KwinInput() {
    m_display = wl_display_connect(nullptr);
    if (!m_display) {
      qFatal("KRFB KWin input: cannot connect to the private Wayland display");
    }
    m_registry = wl_display_get_registry(m_display);
    static const wl_registry_listener listener = {global, globalRemove};
    wl_registry_add_listener(m_registry, &listener, this);
    if (wl_display_roundtrip(m_display) < 0 || !m_input) {
      qFatal("KRFB KWin input: fake-input v5 or newer is unavailable; check "
             "KWin and the virtual-monitor desktop permissions");
    }
    org_kde_kwin_fake_input_authenticate(m_input, "Plasma VNC",
                                         "Authenticated container VNC session");
    if (wl_display_roundtrip(m_display) < 0) {
      qFatal("KRFB KWin input: authentication roundtrip failed");
    }
    // Older KWin accepts evdev keycodes. Obtain its real keymap rather than
    // assuming that X11 keycodes or a host keyboard layout apply here.
    if (m_version < 6) {
      for (int attempt = 0; attempt < 3 && !m_keymap; ++attempt) {
        if (wl_display_roundtrip(m_display) < 0)
          break;
      }
      if (!m_keymap)
        qFatal("KRFB KWin input: compositor keyboard keymap is unavailable");
    }
    auto notifier = new QSocketNotifier(wl_display_get_fd(m_display),
                                        QSocketNotifier::Read, this);
    connect(notifier, &QSocketNotifier::activated, this, [this] {
      if (wl_display_dispatch(m_display) < 0) {
        qFatal("KRFB KWin input: Wayland connection failed");
      }
    });
    qInfo("KRFB KWin input: keyboard, buttons, scrolling and absolute pointer "
          "enabled");
  }

  ~KwinInput() override {
    // Destroying the device also releases any keys/buttons held by this client.
    org_kde_kwin_fake_input_destroy(m_input);
    if (m_keyboard)
      wl_keyboard_destroy(m_keyboard);
    if (m_seat)
      wl_seat_destroy(m_seat);
    if (m_keymap)
      xkb_keymap_unref(m_keymap);
    if (m_context)
      xkb_context_unref(m_context);
    wl_registry_destroy(m_registry);
    wl_display_flush(m_display);
    wl_display_disconnect(m_display);
  }

  void handleKeyboard(bool down, rfbKeySym key) override {
    if (m_version >= 6) {
      org_kde_kwin_fake_input_keyboard_keysym(m_input, key, down ? 1 : 0);
    } else {
      xkb_keycode_t code = m_pressed.value(key, XKB_KEYCODE_INVALID);
      if (code == XKB_KEYCODE_INVALID)
        code = keycode(key);
      if (code == XKB_KEYCODE_INVALID) {
        qWarning() << "KRFB KWin input: keysym is not in the compositor keymap:"
                   << key;
        return;
      }
      if (down)
        m_pressed.insert(key, code);
      else
        m_pressed.remove(key);
      org_kde_kwin_fake_input_keyboard_key(m_input, code - 8, down ? 1 : 0);
    }
    wl_display_flush(m_display);
  }

  // KRFB fixes this callback's parameter order.
  // NOLINTNEXTLINE(bugprone-easily-swappable-parameters)
  void handlePointer(int buttonMask, int x, int y) override {
    const auto buttonsState = static_cast<unsigned>(buttonMask);
    org_kde_kwin_fake_input_pointer_motion_absolute(
        m_input, wl_fixed_from_int(x), wl_fixed_from_int(y));
    // RFB: left, middle, right, wheel up/down/left/right, back, forward.
    static constexpr unsigned buttons[] = {272, 274, 273, 0, 0, 0, 0, 275, 276};
    for (unsigned bit = 0; bit < std::size(buttons); ++bit) {
      const bool down = buttonsState & (1u << bit);
      if (down == bool(m_buttons & (1u << bit))) {
        continue;
      }
      if (buttons[bit]) {
        org_kde_kwin_fake_input_button(m_input, buttons[bit], down ? 1 : 0);
      } else if (down) {
        const int direction = (bit == 3 || bit == 5) ? -15 : 15;
        org_kde_kwin_fake_input_axis(m_input, bit < 5 ? 0 : 1,
                                     wl_fixed_from_int(direction));
      }
    }
    m_buttons = buttonsState;
    wl_display_flush(m_display);
  }

private:
  xkb_keycode_t keycode(xkb_keysym_t symbol) const {
    for (auto code = xkb_keymap_min_keycode(m_keymap);
         code <= xkb_keymap_max_keycode(m_keymap); ++code) {
      for (xkb_layout_index_t layout = 0;
           layout < xkb_keymap_num_layouts_for_key(m_keymap, code); ++layout) {
        for (xkb_level_index_t level = 0;
             level < xkb_keymap_num_levels_for_key(m_keymap, code, layout);
             ++level) {
          const xkb_keysym_t *symbols = nullptr;
          const int count = xkb_keymap_key_get_syms_by_level(
              m_keymap, code, layout, level, &symbols);
          if (count > 0 &&
              std::find(symbols, symbols + count, symbol) != symbols + count)
            return code;
        }
      }
    }
    return XKB_KEYCODE_INVALID;
  }

  // Wayland fixes this callback's parameter types and order.
  // NOLINTNEXTLINE(bugprone-easily-swappable-parameters)
  static void keymap(void *data, wl_keyboard *, uint32_t format, int32_t fd,
                     uint32_t size) {
    auto self = static_cast<KwinInput *>(data);
    if (format == WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1 && size > 0) {
      void *memory = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
      if (memory != MAP_FAILED) {
        if (!self->m_context)
          self->m_context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
        auto map = xkb_keymap_new_from_string(
            self->m_context, static_cast<const char *>(memory),
            XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
        if (map) {
          if (self->m_keymap)
            xkb_keymap_unref(self->m_keymap);
          self->m_keymap = map;
        }
        munmap(memory, size);
      }
    }
    close(fd);
  }
  static void capabilities(void *data, wl_seat *seat, uint32_t capabilities) {
    auto self = static_cast<KwinInput *>(data);
    if (self->m_version >= 6 || self->m_keyboard ||
        !(capabilities & WL_SEAT_CAPABILITY_KEYBOARD))
      return;
    self->m_keyboard = wl_seat_get_keyboard(seat);
    static const wl_keyboard_listener listener = {
        keymap,
        [](void *, wl_keyboard *, uint32_t, wl_surface *, wl_array *) {},
        [](void *, wl_keyboard *, uint32_t, wl_surface *) {},
        [](void *, wl_keyboard *, uint32_t, uint32_t, uint32_t, uint32_t) {},
        [](void *, wl_keyboard *, uint32_t, uint32_t, uint32_t, uint32_t,
           uint32_t) {},
        [](void *, wl_keyboard *, int32_t, int32_t) {},
    };
    wl_keyboard_add_listener(self->m_keyboard, &listener, self);
  }
  static void global(void *data, wl_registry *registry, uint32_t name,
                     const char *interface, uint32_t version) {
    auto self = static_cast<KwinInput *>(data);
    if (std::strcmp(interface, "org_kde_kwin_fake_input") == 0 &&
        version >= 5) {
      self->m_version = std::min(version, 6u);
      self->m_input = static_cast<org_kde_kwin_fake_input *>(wl_registry_bind(
          registry, name, &org_kde_kwin_fake_input_interface, self->m_version));
    } else if (std::strcmp(interface, "wl_seat") == 0 && !self->m_seat) {
      self->m_seat = static_cast<wl_seat *>(
          wl_registry_bind(registry, name, &wl_seat_interface, 1));
      static const wl_seat_listener listener = {
          capabilities, [](void *, wl_seat *, const char *) {}};
      wl_seat_add_listener(self->m_seat, &listener, self);
    }
  }
  static void globalRemove(void *, wl_registry *, uint32_t) {}
  wl_display *m_display = nullptr;
  wl_registry *m_registry = nullptr;
  org_kde_kwin_fake_input *m_input = nullptr;
  uint32_t m_version = 0;
  wl_seat *m_seat = nullptr;
  wl_keyboard *m_keyboard = nullptr;
  xkb_context *m_context = nullptr;
  xkb_keymap *m_keymap = nullptr;
  QHash<xkb_keysym_t, xkb_keycode_t> m_pressed;
  unsigned m_buttons = 0;
};

class KwinEventsPlugin final : public EventsPlugin {
  Q_OBJECT
public:
  KwinEventsPlugin(QObject *parent, const QVariantList &args)
      : EventsPlugin(parent, args) {}
  EventHandler *eventHandler() override {
    if (qEnvironmentVariableIntValue("PLASMA_KWIN_INPUT") != 1 ||
        QGuiApplication::platformName() != "wayland") {
      return nullptr;
    }
    return new KwinInput;
  }
};

K_PLUGIN_CLASS(KwinEventsPlugin)
#include "kwin-input.moc"
