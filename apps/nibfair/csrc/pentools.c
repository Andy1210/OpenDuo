// SPDX-License-Identifier: MIT
// The pen, from the Wayland tablet protocol: which end, where, how hard, how tilted.
//
// SDL 3.2 (and 3.4) on Wayland reports every tablet tool as a pen and never
// sets the eraser flag ("FIXME: how do we know what tip is in use?" in
// SDL_waylandevents.c), although the compositor names the tool's type: duo-wc
// gives the Slim Pen's eraser end a tool of its own (type eraser). Listening
// to the tablet seat beside SDL is not enough: the compositor (Smithay's
// tablet manager) sends a tool's events to ONE of the client's instances of
// it, the first one created, and SDL's tablet seat always came first. So
// this takes the whole pen: it opens the Wayland connection itself, creates
// its tablet seat before SDL starts, and hands the connection to SDL
// (SDL_PROP_GLOBAL_VIDEO_WAYLAND_WL_DISPLAY_POINTER). Its tools are the
// first instances, so every tablet event comes here; SDL's own tablet seat
// stays silent. The events wait on a queue of their own: SDL's event pump
// reads the socket, pentools_dispatch() handles what it read for this queue
// without blocking, and each tool frame becomes one pentools_event.
#include "tablet-v2-client-protocol.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>

// What changed in a frame (pentools_event.flags).
enum {
    PT_IN = 1 << 0,        // came into range (tool_type says which end)
    PT_OUT = 1 << 1,       // left the range
    PT_MOTION = 1 << 2,    // x, y
    PT_DOWN = 1 << 3,      // the tip (or the eraser end) touched
    PT_UP = 1 << 4,        // lifted
    PT_PRESSURE = 1 << 5,  // pressure
    PT_TILT = 1 << 6,      // tilt_x, tilt_y
    PT_DISTANCE = 1 << 7,  // distance
    PT_BUTTON = 1 << 8,    // buttons (button_mask: which changed)
};

// One tool frame. Must match input.rs' PenEvent.
struct pentools_event {
    uint32_t flags;
    uint32_t time_ms;      // the compositor's (CLOCK_MONOTONIC, ms)
    uint32_t tool_type;    // ZWP_TABLET_TOOL_V2_TYPE_* (0x140 pen, 0x141 eraser, ...)
    uint32_t buttons;      // bit 0: BTN_STYLUS, 1: BTN_STYLUS2, 2: BTN_STYLUS3
    uint32_t button_mask;  // which of them changed in this frame
    float x, y;            // surface-local, logical points
    float pressure;        // 0..1
    float tilt_x, tilt_y;  // degrees
    float distance;        // 0..1
};

struct tool {
    struct zwp_tablet_tool_v2 *tool;
    uint32_t type;
    int near;
    uint32_t buttons;
    struct pentools_event pending;
    struct tool *next;
};

#define RING 512

static struct wl_display *dpy;
static struct wl_event_queue *queue;
static struct wl_registry *registry;
static struct wl_seat *seat;
static struct zwp_tablet_manager_v2 *manager;
static struct zwp_tablet_seat_v2 *tablet_seat;
static struct tool *tools;
static struct pentools_event ring[RING];
static unsigned ring_head, ring_len, dropped;

static void noop() {}

static void push(const struct pentools_event *ev) {
    if (ring_len == RING) {    // nobody reads: the oldest goes
        ring_head = (ring_head + 1) % RING;
        ring_len--;
        dropped++;
    }
    ring[(ring_head + ring_len) % RING] = *ev;
    ring_len++;
}

// --- tools ---------------------------------------------------------------------------------

static void tool_type(void *data, struct zwp_tablet_tool_v2 *t, uint32_t type) {
    (void)t;
    ((struct tool *)data)->type = type;
}

static void tool_proximity_in(void *data, struct zwp_tablet_tool_v2 *t, uint32_t serial,
                              struct zwp_tablet_v2 *tablet, struct wl_surface *surface) {
    (void)t;
    (void)serial;
    (void)tablet;
    (void)surface;
    struct tool *me = data;
    me->near = 1;
    me->pending.flags = (me->pending.flags | PT_IN) & ~PT_OUT;
}

static void tool_proximity_out(void *data, struct zwp_tablet_tool_v2 *t) {
    (void)t;
    struct tool *me = data;
    me->near = 0;
    me->pending.flags |= PT_OUT;
}

static void tool_down(void *data, struct zwp_tablet_tool_v2 *t, uint32_t serial) {
    (void)t;
    (void)serial;
    struct tool *me = data;
    me->pending.flags = (me->pending.flags | PT_DOWN) & ~PT_UP;
}

static void tool_up(void *data, struct zwp_tablet_tool_v2 *t) {
    (void)t;
    struct tool *me = data;
    me->pending.flags = (me->pending.flags | PT_UP) & ~PT_DOWN;
}

static void tool_motion(void *data, struct zwp_tablet_tool_v2 *t, wl_fixed_t x, wl_fixed_t y) {
    (void)t;
    struct tool *me = data;
    me->pending.flags |= PT_MOTION;
    me->pending.x = (float)wl_fixed_to_double(x);
    me->pending.y = (float)wl_fixed_to_double(y);
}

static void tool_pressure(void *data, struct zwp_tablet_tool_v2 *t, uint32_t p) {
    (void)t;
    struct tool *me = data;
    me->pending.flags |= PT_PRESSURE;
    me->pending.pressure = (float)p / 65535.0f;
}

static void tool_distance(void *data, struct zwp_tablet_tool_v2 *t, uint32_t d) {
    (void)t;
    struct tool *me = data;
    me->pending.flags |= PT_DISTANCE;
    me->pending.distance = (float)d / 65535.0f;
}

static void tool_tilt(void *data, struct zwp_tablet_tool_v2 *t, wl_fixed_t tx, wl_fixed_t ty) {
    (void)t;
    struct tool *me = data;
    me->pending.flags |= PT_TILT;
    me->pending.tilt_x = (float)wl_fixed_to_double(tx);
    me->pending.tilt_y = (float)wl_fixed_to_double(ty);
}

static void tool_button(void *data, struct zwp_tablet_tool_v2 *t, uint32_t serial, uint32_t button,
                        uint32_t state) {
    (void)t;
    (void)serial;
    struct tool *me = data;
    uint32_t bit;
    switch (button) {
        case 0x14b: bit = 1; break;    // BTN_STYLUS
        case 0x14c: bit = 2; break;    // BTN_STYLUS2
        case 0x149: bit = 4; break;    // BTN_STYLUS3
        default: return;
    }
    if (state == ZWP_TABLET_TOOL_V2_BUTTON_STATE_PRESSED)
        me->buttons |= bit;
    else
        me->buttons &= ~bit;
    me->pending.flags |= PT_BUTTON;
    me->pending.button_mask |= bit;
}

static void tool_frame(void *data, struct zwp_tablet_tool_v2 *t, uint32_t time) {
    (void)t;
    struct tool *me = data;
    if (me->pending.flags) {
        me->pending.time_ms = time;
        me->pending.tool_type = me->type;
        me->pending.buttons = me->buttons;
        push(&me->pending);
    }
    me->pending.flags = 0;
    me->pending.button_mask = 0;
}

static void tool_removed(void *data, struct zwp_tablet_tool_v2 *t) {
    struct tool *me = data, **p;
    if (me->near) {    // gone while in range: out of range for the game
        struct pentools_event ev = me->pending;
        ev.flags = PT_OUT;
        ev.tool_type = me->type;
        ev.time_ms = 0;
        push(&ev);
    }
    for (p = &tools; *p; p = &(*p)->next) {
        if (*p == me) {
            *p = me->next;
            break;
        }
    }
    zwp_tablet_tool_v2_destroy(t);
    free(me);
}

static const struct zwp_tablet_tool_v2_listener tool_listener = {
    .type = tool_type,
    .hardware_serial = (void *)noop,
    .hardware_id_wacom = (void *)noop,
    .capability = (void *)noop,
    .done = (void *)noop,
    .removed = tool_removed,
    .proximity_in = tool_proximity_in,
    .proximity_out = tool_proximity_out,
    .down = tool_down,
    .up = tool_up,
    .motion = tool_motion,
    .pressure = tool_pressure,
    .distance = tool_distance,
    .tilt = tool_tilt,
    .rotation = (void *)noop,
    .slider = (void *)noop,
    .wheel = (void *)noop,
    .button = tool_button,
    .frame = tool_frame,
};

// --- the tablet seat -----------------------------------------------------------------------

static void tablet_removed(void *data, struct zwp_tablet_v2 *tablet) {
    (void)data;
    zwp_tablet_v2_destroy(tablet);
}

static const struct zwp_tablet_v2_listener tablet_listener = {
    .name = (void *)noop,
    .id = (void *)noop,
    .path = (void *)noop,
    .done = (void *)noop,
    .removed = tablet_removed,
};

static void seat_tablet_added(void *data, struct zwp_tablet_seat_v2 *s,
                              struct zwp_tablet_v2 *tablet) {
    (void)data;
    (void)s;
    zwp_tablet_v2_add_listener(tablet, &tablet_listener, NULL);
}

static void seat_tool_added(void *data, struct zwp_tablet_seat_v2 *s,
                            struct zwp_tablet_tool_v2 *t) {
    (void)data;
    (void)s;
    struct tool *me = calloc(1, sizeof *me);
    if (!me) {
        zwp_tablet_tool_v2_destroy(t);
        return;
    }
    me->tool = t;
    me->next = tools;
    tools = me;
    zwp_tablet_tool_v2_add_listener(t, &tool_listener, me);
}

static void seat_pad_added(void *data, struct zwp_tablet_seat_v2 *s,
                           struct zwp_tablet_pad_v2 *pad) {
    (void)data;
    (void)s;
    zwp_tablet_pad_v2_destroy(pad);    // no pads on the phone, and none needed here
}

static const struct zwp_tablet_seat_v2_listener tablet_seat_listener = {
    .tablet_added = seat_tablet_added,
    .tool_added = seat_tool_added,
    .pad_added = seat_pad_added,
};

// --- the registry --------------------------------------------------------------------------

static const struct wl_seat_listener seat_listener = {
    .capabilities = (void *)noop,
    .name = (void *)noop,
};

static void global(void *data, struct wl_registry *r, uint32_t name, const char *iface,
                   uint32_t version) {
    (void)data;
    if (!seat && strcmp(iface, wl_seat_interface.name) == 0) {
        seat = wl_registry_bind(r, name, &wl_seat_interface, version < 2 ? version : 2);
        wl_seat_add_listener(seat, &seat_listener, NULL);
    } else if (!manager && strcmp(iface, zwp_tablet_manager_v2_interface.name) == 0) {
        manager = wl_registry_bind(r, name, &zwp_tablet_manager_v2_interface, 1);
    }
}

static const struct wl_registry_listener registry_listener = {
    .global = global,
    .global_remove = (void *)noop,
};

// --- the interface -------------------------------------------------------------------------

static void forget(void) {
    if (dpy) wl_display_disconnect(dpy);
    dpy = NULL;
    queue = NULL;
    registry = NULL;
    seat = NULL;
    manager = NULL;
    tablet_seat = NULL;
    tools = NULL;
}

// Open the Wayland connection (WAYLAND_DISPLAY) and take the tablet seat on
// it, before SDL starts. -> the wl_display for SDL, or NULL (no Wayland).
// The tablet part only where the compositor has a seat and a tablet manager
// (pentools_has_tablet).
void *pentools_connect(void) {
    if (dpy) return dpy;
    dpy = wl_display_connect(NULL);
    if (!dpy) return NULL;
    queue = wl_display_create_queue(dpy);
    struct wl_display *wrapper = wl_proxy_create_wrapper(dpy);
    if (!queue || !wrapper) goto fail;
    wl_proxy_set_queue((struct wl_proxy *)wrapper, queue);
    registry = wl_display_get_registry(wrapper);
    wl_proxy_wrapper_destroy(wrapper);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip_queue(dpy, queue) < 0) goto fail;
    if (seat && manager) {
        tablet_seat = zwp_tablet_manager_v2_get_tablet_seat(manager, seat);
        zwp_tablet_seat_v2_add_listener(tablet_seat, &tablet_seat_listener, NULL);
        // The tablets and the tools the compositor already knows.
        if (wl_display_roundtrip_queue(dpy, queue) < 0) goto fail;
    }
    return dpy;
fail:
    forget();
    return NULL;
}

// Close the connection: after SDL_Quit (SDL does not close a connection it
// was given), or when SDL chose another video driver.
void pentools_disconnect(void) { forget(); }

int pentools_has_tablet(void) { return tablet_seat != NULL; }

// Handle what SDL's event pump has read for this queue (never blocks).
void pentools_dispatch(void) {
    if (dpy) wl_display_dispatch_queue_pending(dpy, queue);
}

// The next tool frame -> 1, or 0 (none waiting).
int pentools_next(struct pentools_event *ev) {
    if (!ring_len) return 0;
    *ev = ring[ring_head];
    ring_head = (ring_head + 1) % RING;
    ring_len--;
    return 1;
}

// Frames dropped because nobody read them (the ring was full).
unsigned pentools_dropped(void) { return dropped; }

// CLOCK_MONOTONIC in ms (the compositor's event clock), for the events' age.
double pentools_monotonic_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec * 1e-6;
}
