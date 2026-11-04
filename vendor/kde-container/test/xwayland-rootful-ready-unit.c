/* Exercise the actual rootful callbacks, not a copy of the readiness policy. */
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "xwayland-window.c"

static WindowPtr expected_window;
static unsigned publications;
static CARD32 observed_numerator;
static int surface_token, xdg_token, output_token, frame_token, state_token;
static int content_width, content_height;
static int expected_content_width, expected_content_height;
static unsigned mode_requests;

Atom __wrap_MakeAtom(const char *, unsigned, Bool);
int __wrap_dixChangeWindowProperty(ClientPtr, WindowPtr, Atom, Atom, int, int,
                                   unsigned long, const void *, Bool);
Bool __wrap_xwl_randr_add_modes_fixed(struct xwl_output *, int, int);
_X_EXPORT uint32_t __wrap_wl_proxy_get_version(struct wl_proxy *);
_X_EXPORT struct wl_proxy *
__wrap_wl_proxy_marshal_flags(struct wl_proxy *, uint32_t,
                              const struct wl_interface *, uint32_t, uint32_t,
                              ...);
#ifdef XWL_HAS_LIBDECOR
_X_EXPORT bool __wrap_libdecor_configuration_get_content_size(
    struct libdecor_configuration *, struct libdecor_frame *, int *, int *);
_X_EXPORT struct libdecor_state *__wrap_libdecor_state_new(int, int);
_X_EXPORT void __wrap_libdecor_frame_commit(struct libdecor_frame *,
                                            struct libdecor_state *,
                                            struct libdecor_configuration *);
_X_EXPORT void __wrap_libdecor_state_free(struct libdecor_state *);
#endif

Atom __wrap_MakeAtom(const char *name, unsigned length, Bool create) {
  const char expected[] = "_XWAYLAND_ROOTFUL_SCALE";
  assert(length == sizeof(expected) - 1);
  assert(!memcmp(name, expected, length));
  assert(create);
  return 71;
}

int __wrap_dixChangeWindowProperty(ClientPtr client, WindowPtr window,
                                   Atom atom, Atom type, int format, int mode,
                                   unsigned long length, const void *value,
                                   Bool send_event) {
  assert(client == serverClient);
  assert(window == expected_window);
  assert(atom == 71 && type == XA_CARDINAL);
  assert(format == 32 && mode == PropModeReplace && length == 1 && send_event);
  observed_numerator = *(const CARD32 *)value;
  publications++;
  return Success;
}

Bool __wrap_xwl_randr_add_modes_fixed(struct xwl_output *output, int width,
                                      int height) {
  assert(output && width > 0 && height > 0);
  mode_requests++;
  /* Stop at mode allocation; keep the actual callback and resize arithmetic. */
  return FALSE;
}

uint32_t __wrap_wl_proxy_get_version(struct wl_proxy *proxy) {
  assert(proxy == (struct wl_proxy *)&surface_token ||
         proxy == (struct wl_proxy *)&xdg_token);
  return 1;
}

struct wl_proxy *
__wrap_wl_proxy_marshal_flags(struct wl_proxy *proxy, uint32_t opcode,
                              const struct wl_interface *interface,
                              uint32_t version, uint32_t flags, ...) {
  (void)interface;
  assert(version == 1 && flags == 0);
  assert((proxy == (struct wl_proxy *)&surface_token && opcode == 6) ||
         (proxy == (struct wl_proxy *)&xdg_token && opcode == 4));
  return NULL;
}

#ifdef XWL_HAS_LIBDECOR
bool __wrap_libdecor_configuration_get_content_size(
    struct libdecor_configuration *configuration, struct libdecor_frame *frame,
    int *width, int *height) {
  (void)configuration;
  assert(frame == (struct libdecor_frame *)&frame_token);
  if (!content_width)
    return false;
  *width = content_width;
  *height = content_height;
  return true;
}

struct libdecor_state *__wrap_libdecor_state_new(int width, int height) {
  assert(width == expected_content_width && height == expected_content_height &&
         "Initial configure must not scale existing pixels twice");
  return (struct libdecor_state *)&state_token;
}

void __wrap_libdecor_frame_commit(
    struct libdecor_frame *frame, struct libdecor_state *state,
    struct libdecor_configuration *configuration) {
  (void)configuration;
  assert(frame == (struct libdecor_frame *)&frame_token);
  assert(state == (struct libdecor_state *)&state_token);
}

void __wrap_libdecor_state_free(struct libdecor_state *state) {
  assert(state == (struct libdecor_state *)&state_token);
}
#endif

struct fixture {
  struct xwl_screen screen;
  struct xwl_window window;
  struct xwl_output output;
  ScreenRec xscreen;
  WindowPtr root;
};

static void setup(struct fixture *state, int global, int numerator) {
  memset(state, 0, sizeof(*state));
  publications = 0;
  observed_numerator = 0;
  content_width = content_height = 0;
  expected_content_width = 1280;
  expected_content_height = 960;
  mode_requests = 0;
  state->screen.screen = &state->xscreen;
  state->screen.width = 1280;
  state->screen.height = 960;
  state->screen.global_surface_scale = global;
  state->screen.hidpi = TRUE;
  state->screen.fixed_output = &state->output;
  dixInitScreenSpecificPrivates(&state->xscreen);
  state->root = dixAllocateScreenObjectWithPrivates(&state->xscreen, WindowRec,
                                                    PRIVATE_WINDOW);
  assert(state->root);
  state->root->drawable.pScreen = &state->xscreen;
  state->xscreen.root = state->root;
  state->window.xwl_screen = &state->screen;
  state->window.toplevel = state->root;
  state->window.surface = (struct wl_surface *)&surface_token;
  state->window.fractional_scale_numerator = numerator;
  xorg_list_init(&state->window.xwl_output_list);
  xorg_list_init(&state->screen.output_list);
  dixSetPrivate(&state->root->devPrivates, &xwl_window_private_key,
                &state->window);
  expected_window = state->root;
}

static void cleanup(struct fixture *state) {
  if (!xorg_list_is_empty(&state->window.xwl_output_list))
    xwl_window_leave_output(&state->window, &state->output);
  dixFreeObjectWithPrivates(state->root, PRIVATE_WINDOW);
  dixFreeScreenSpecificPrivates(&state->xscreen);
}

static void configure(struct fixture *state, int decorated) {
#ifdef XWL_HAS_LIBDECOR
  if (decorated) {
    state->window.libdecor_frame = (struct libdecor_frame *)&frame_token;
    handle_libdecor_configure(state->window.libdecor_frame, NULL,
                              &state->window);
    return;
  }
#else
  assert(!decorated && "This regression requires the libdecor-enabled SDK");
#endif
  xdg_surface_handle_configure(&state->window, (struct xdg_surface *)&xdg_token,
                               41);
}

int main(void) {
  struct fixture state;
  unsigned cases = 0;
  assert(dixRegisterPrivateKey(&xwl_window_private_key, PRIVATE_WINDOW, 0));

  /* The actual unchanged-120 callback must count as the first real event.
   * Either legal arrival order must leave the property absent after one. */
  for (int decorated = 0; decorated <= 1; decorated++) {
    for (int configure_first = 0; configure_first <= 1; configure_first++) {
      setup(&state, 1, 120);
      if (configure_first)
        configure(&state, decorated);
      else
        wp_fractional_scale_preferred_scale(&state.window, NULL, 120);
      assert(publications == 0);
      if (configure_first)
        wp_fractional_scale_preferred_scale(&state.window, NULL, 120);
      else
        configure(&state, decorated);
      assert(publications == 1 && observed_numerator == 120);
      cleanup(&state);
      cases++;
    }
  }

  /* Acknowledging an already-applied fractional value exercises publication
   * without replacing or mocking the unrelated RandR resize machinery. */
  for (int numerator = 150; numerator <= 240; numerator += 90) {
    setup(&state, 1, numerator);
    configure(&state, 0);
    assert(publications == 0);
    wp_fractional_scale_preferred_scale(&state.window, NULL, numerator);
    assert(publications == 1 && observed_numerator == (CARD32)numerator);
    cleanup(&state);
    cases++;
  }

  /* Real surface-enter and real Xserver private lookup/global-scale code.
   * The output already has integer scale two, so no unrelated resize occurs. */
  for (int configure_first = 0; configure_first <= 1; configure_first++) {
    setup(&state, 2, 120);
    state.output.output = (struct wl_output *)&output_token;
    state.output.scale = 2;
    xorg_list_add(&state.output.link, &state.screen.output_list);
    if (configure_first)
      configure(&state, 0);
    else
      xwl_window_surface_enter(&state.window, state.window.surface,
                               state.output.output);
    assert(publications == 0);
    if (configure_first)
      xwl_window_surface_enter(&state.window, state.window.surface,
                               state.output.output);
    else
      configure(&state, 0);
    assert(state.screen.global_surface_scale == 2);
    assert(publications == 1 && observed_numerator == 240);
    cleanup(&state);
    cases++;
  }

  setup(&state, 1, 120);
  state.screen.rootless = TRUE;
  configure(&state, 0);
  wp_fractional_scale_preferred_scale(&state.window, NULL, 120);
  assert(publications == 0);
  cleanup(&state);
  cases++;
  const uint32_t invalid_scales[] = {0, UINT32_MAX};
  for (unsigned index = 0;
       index < sizeof(invalid_scales) / sizeof(invalid_scales[0]); index++) {
    setup(&state, 1, 120);
    configure(&state, 0);
    wp_fractional_scale_preferred_scale(&state.window, NULL,
                                        invalid_scales[index]);
    assert(publications == 0);
    assert(state.window.fractional_scale_numerator == 120);
    cleanup(&state);
    cases++;
  }
  printf("actual Xwayland rootful readiness callbacks: %u cases passed\n",
         cases);
  fflush(stdout);
#ifdef XWL_HAS_LIBDECOR
  unsigned size_cases = 0;
  const int scales[][2] = {{1, 150}, {1, 240}, {2, 120}};
  for (unsigned index = 0; index < sizeof(scales) / sizeof(scales[0]);
       index++) {
    for (int supplied_size = 0; supplied_size <= 1; supplied_size++) {
      const int global = scales[index][0];
      const int numerator = scales[index][1];
      const double scale = global * numerator / 120.0;
      setup(&state, global, numerator);
      /* The earlier host scale event already resized the physical screen. */
      state.screen.width = (int)(1280 * scale);
      state.screen.height = (int)(960 * scale);
      state.window.rootful_scale_received = TRUE;
      if (supplied_size) {
        content_width = expected_content_width = 800;
        content_height = expected_content_height = 600;
      }
      configure(&state, 1);
      assert(state.screen.width == expected_content_width * scale);
      assert(state.screen.height == expected_content_height * scale);
      assert(mode_requests == (unsigned)supplied_size);
      assert(publications == 1);
      assert(observed_numerator == (CARD32)(global * numerator));
      cleanup(&state);
      size_cases++;
    }
  }
  printf("actual Xwayland initial configure dimensions: %u cases passed\n",
         size_cases);
#endif
  return 0;
}
