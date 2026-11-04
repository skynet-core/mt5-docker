/* Compile the actual DDX callback, replacing only its event-queue boundary. */
#undef NDEBUG
#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "xwayland-input.c"

static DeviceIntPtr observed_device;
static int observed_type, observed_buttons, observed_flags, observed_calls;
static double observed_x, observed_y;

void __wrap_QueuePointerEvents(DeviceIntPtr device, int type, int buttons,
                               int flags, const ValuatorMask *mask);

void __wrap_QueuePointerEvents(DeviceIntPtr device, int type, int buttons,
                               int flags, const ValuatorMask *mask) {
  assert(valuator_mask_isset(mask, 0));
  assert(valuator_mask_isset(mask, 1));
  observed_device = device;
  observed_type = type;
  observed_buttons = buttons;
  observed_flags = flags;
  observed_x = valuator_mask_get_double(mask, 0);
  observed_y = valuator_mask_get_double(mask, 1);
  observed_calls++;
}

int main(void) {
  const double scales[] = {1, 1.15, 1.2, 1.25, 4.0 / 3.0, 1.5, 2};
  const wl_fixed_t points[][2] = {{0, 0},     {1, 255},       {64, 192},
                                  {256, 512}, {32767, 24577}, {319999, 239999},
                                  {-1, -255}, {-1025, 513}};
  const int origins[][2] = {{0, 0}, {-37, 53}};
  unsigned cases = 0;

  for (unsigned s = 0; s < sizeof(scales) / sizeof(scales[0]); s++) {
    for (int global = 1; global <= 2; global++) {
      for (unsigned o = 0; o < sizeof(origins) / sizeof(origins[0]); o++) {
        for (int relative = 0; relative <= 1; relative++) {
          for (unsigned p = 0; p < sizeof(points) / sizeof(points[0]); p++) {
            struct xwl_screen screen = {0};
            struct xwl_window window = {0};
            struct xwl_seat seat = {0};
            WindowRec toplevel = {0};
            DeviceIntRec absolute_device = {0}, relative_device = {0};
            screen.global_surface_scale = global;
            window.viewport_scale_x = scales[s] / global;
            window.viewport_scale_y = scales[s] / global;
            window.toplevel = &toplevel;
            toplevel.drawable.x = origins[o][0];
            toplevel.drawable.y = origins[o][1];
            seat.xwl_screen = &screen;
            seat.focus_window = &window;
            seat.pointer = &absolute_device;
            seat.relative_pointer = &relative_device;
            seat.pending_pointer_event.has_absolute = TRUE;
            seat.pending_pointer_event.has_relative = relative;
            seat.pending_pointer_event.x = points[p][0];
            seat.pending_pointer_event.y = points[p][1];

            /* Public protocol units are exact multiples of 1/256.
             * Check the complete affine mapping at the real queue
             * boundary, including the real int/double setters. */
            const double expected_x =
                origins[o][0] +
                (double)points[p][0] / 256 * global * window.viewport_scale_x;
            const double expected_y =
                origins[o][1] +
                (double)points[p][1] / 256 * global * window.viewport_scale_y;
            observed_calls = 0;
            dispatch_absolute_motion(&seat);
            if (fabs(observed_x - expected_x) > 1e-9 ||
                fabs(observed_y - expected_y) > 1e-9) {
              fprintf(stderr,
                      "raw=(%d,%d) global=%d viewport=(%.9g,%.9g) "
                      "origin=(%d,%d) observed=(%.17g,%.17g) "
                      "expected=(%.17g,%.17g)\n",
                      points[p][0], points[p][1], global,
                      window.viewport_scale_x, window.viewport_scale_y,
                      origins[o][0], origins[o][1], observed_x, observed_y,
                      expected_x, expected_y);
              assert(
                  !"actual Xwayland callback lost absolute pointer precision");
            }
            assert(observed_calls == 1);
            assert(observed_type == MotionNotify);
            assert(observed_buttons == 0);
            assert(observed_device ==
                   (relative ? &relative_device : &absolute_device));
            assert(observed_flags == (POINTER_ABSOLUTE | POINTER_SCREEN |
                                      (relative ? POINTER_NORAW : 0)));
            cases++;
          }
        }
      }
    }
  }
  printf("actual Xwayland absolute callback: %u cases passed\n", cases);
  return 0;
}
