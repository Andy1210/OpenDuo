#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pen test: a canvas that shows, live, what an app gets from the pen.

Duo Settings > Pen > Pen test opens it; or by hand on the phone:

    duo2_session 'python3 /usr/local/lib/duo2-touch-tools/pen-test.py > /tmp/pen.log 2>&1'

The panel at the top says what GTK gets from the pen (Wayland tablet-v2,
duo-wc's crate::tablet): the tool (pen / eraser), whether it hovers or
touches, the pressure (the bar), the tilt (in degrees, while the tip touches:
the digitizer's library reports none while hovering), the side button,
where it is and which panel's pen device it came from. On the canvas the stroke's width
follows the pressure; the eraser end draws red, a stroke with the side
button held blue. A finger that reaches the canvas shows as a grey ring and
is counted: the palm check (a hand resting on the glass while the pen is
near must not show up at all). Every event is also printed on stdout, one
line each, so it can be followed over ssh.
"""

import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

T0 = time.monotonic()
NEAR_MS = 300  # no pen event for this long: the pen has gone
BUTTONS = Gdk.ModifierType.BUTTON2_MASK | Gdk.ModifierType.BUTTON3_MASK
# GTK hands the tilt over as a fraction of the tablet-v2 range, -1..1 for
# -90..90 degrees (gdk_wayland_device_tablet_clone_tool_axes adds the axis as
# -90..90, _gdk_device_translate_axis maps it to -1..1), not in degrees.
TILT_RANGE_DEG = 90.0


def log(*args):
    print(f"{time.monotonic() - T0:8.3f}", *args, flush=True)


def axes_of(gesture):
    out = {}
    for name, use in (
        ("pressure", Gdk.AxisUse.PRESSURE),
        ("xtilt", Gdk.AxisUse.XTILT),
        ("ytilt", Gdk.AxisUse.YTILT),
        ("distance", Gdk.AxisUse.DISTANCE),
    ):
        ok, value = gesture.get_axis(use)
        if ok:
            out[name] = value
    return out


def tilt_degrees(axes):
    """The tilt (x, y) in degrees from axes_of()'s values, or None without
    both."""
    x, y = axes.get("xtilt"), axes.get("ytilt")
    if x is None or y is None:
        return None
    return x * TILT_RANGE_DEG, y * TILT_RANGE_DEG


def tilt_text(axes, touching):
    """The readout's tilt: whole degrees while the tip touches; "-" while
    hovering (duo2-touchd's library gives the tilt with the tip down only,
    so a hover's 0 says nothing) or without the axes."""
    tilt = tilt_degrees(axes)
    if tilt is None or not touching:
        return "-"
    return "%+d / %+d deg" % (round(tilt[0]), round(tilt[1]))


class Readout(Gtk.Grid):
    """The live values, label over value, in two rows."""

    FIELDS = (
        ("tool", "Tool"),
        ("state", "Pen"),
        ("tilt", "Tilt"),
        ("button", "Side button"),
        ("where", "At"),
        ("device", "Device"),
        ("fingers", "Fingers"),
        ("palm", "While near"),
    )

    def __init__(self):
        super().__init__(
            column_spacing=14, row_spacing=2, margin_start=8, margin_end=8, margin_top=4
        )
        self.values = {}
        for i, (key, title) in enumerate(self.FIELDS):
            col, row = i % 4, (i // 4) * 2
            t = Gtk.Label(label=title, xalign=0)
            t.add_css_class("dim-label")
            t.add_css_class("caption")
            v = Gtk.Label(label="-", xalign=0, width_chars=9)
            self.attach(t, col, row, 1, 1)
            self.attach(v, col, row + 1, 1, 1)
            self.values[key] = v
        self.pressure = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, margin_top=4)
        self.attach(self.pressure, 0, 4, 4, 1)

    def set(self, **values):
        for key, value in values.items():
            self.values[key].set_text(str(value))


class Canvas(Gtk.DrawingArea):
    def __init__(self, readout):
        super().__init__(hexpand=True, vexpand=True)
        self.readout = readout
        self.strokes = []  # [(rgb, [(x, y, width)])]
        self.current = None
        self.hover = None
        self.fingers = {}  # sequence -> (x, y)
        self.finger_marks = []
        self.finger_count = 0
        self.finger_near = 0  # fingers that came while the pen was near: the palm check
        self.last_pen = 0.0
        self.set_draw_func(self.draw)

        stylus = Gtk.GestureStylus()
        stylus.set_button(0)  # the tip and the side button
        stylus.connect("proximity", self.on_hover)
        stylus.connect("down", self.on_down)
        stylus.connect("motion", self.on_motion)
        stylus.connect("up", self.on_up)
        self.add_controller(stylus)

        # Fingers (not the pen: touch only).
        drag = Gtk.GestureDrag(touch_only=True)
        drag.connect("drag-begin", self.on_finger_down)
        drag.connect("drag-end", self.on_finger_up)
        self.add_controller(drag)

        # The pen leaving: proximity out, or no event for a while.
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self.on_event)
        self.add_controller(legacy)
        GLib.timeout_add(100, self.tick)

    def near(self):
        return time.monotonic() - self.last_pen < NEAR_MS / 1000

    def on_event(self, _controller, event):
        if event.get_event_type() == Gdk.EventType.PROXIMITY_OUT:
            self.gone("proximity out")
        return False

    def tick(self):
        if self.hover and not self.near() and not self.current:
            self.gone("no events")
        return True

    def gone(self, why):
        if self.hover or self.current:
            log(f"out ({why})")
        self.hover = None
        self.current = None
        self.readout.set(state="away", tool="-", tilt="-", button="-", where="-")
        self.readout.pressure.set_value(0)
        self.queue_draw()

    def pen(self, gesture, what, x, y):
        self.last_pen = time.monotonic()
        tool = gesture.get_device_tool()
        kind = tool.get_tool_type().value_nick if tool else "?"
        axes = axes_of(gesture)
        state = gesture.get_current_event_state()
        side = bool(state & BUTTONS)
        device = gesture.get_device()
        name = device.get_name() if device else "?"
        text = " ".join(f"{k}={v:.3f}" for k, v in axes.items())
        tilt = tilt_degrees(axes)
        if tilt:
            text += " tilt=%+.1f/%+.1f deg" % tilt
        log(
            f"{what} tool={kind} x={x:.1f} y={y:.1f} button={gesture.get_current_button()} side={int(side)} "
            f"device={name!r} {text}"
        )
        self.readout.set(
            tool=kind,
            state=what,
            button="pressed" if side else "-",
            tilt=tilt_text(axes, what in ("down", "touch")),
            where=f"{x:.0f}, {y:.0f}",
            device=name.replace("Surface Duo 2 ", ""),
        )
        self.readout.pressure.set_value(min(max(axes.get("pressure", 0.0), 0.0), 1.0))
        return kind, axes, side

    def width(self, axes):
        return 1 + 14 * axes.get("pressure", 0.5)

    def on_hover(self, gesture, x, y):
        self.hover = (x, y)
        self.pen(gesture, "hover", x, y)
        self.queue_draw()

    def on_down(self, gesture, x, y):
        kind, axes, side = self.pen(gesture, "down", x, y)
        self.hover = (x, y)
        if gesture.get_current_button() != 1 and axes.get("pressure", 0) <= 0:
            # The side button pressed while hovering: shown, nothing drawn.
            self.queue_draw()
            return
        rgb = (
            (0.85, 0.1, 0.1)
            if kind == "eraser"
            else (0.1, 0.3, 0.9)
            if side
            else (0.05, 0.05, 0.05)
        )
        self.current = (rgb, [(x, y, self.width(axes))])
        self.strokes.append(self.current)
        self.queue_draw()

    def on_motion(self, gesture, x, y):
        self.hover = (x, y)
        kind, axes, side = self.pen(gesture, "touch" if self.current else "hover", x, y)
        if self.current:
            self.current[1].append((x, y, self.width(axes)))
        self.queue_draw()

    def on_up(self, gesture, x, y):
        self.pen(gesture, "up", x, y)
        self.current = None
        self.queue_draw()

    def on_finger_down(self, gesture, x, y):
        near = self.near()
        self.finger_count += 1
        self.finger_near += near
        self.finger_marks.append((x, y))
        log(
            f"touch down x={x:.0f} y={y:.0f}"
            + (" WHILE THE PEN IS NEAR (palm got through)" if near else "")
        )
        self.readout.set(fingers=self.finger_count, palm=self.finger_near)
        self.queue_draw()

    def on_finger_up(self, gesture, dx, dy):
        log(f"touch up dx={dx:.0f} dy={dy:.0f}")

    def clear(self):
        self.strokes.clear()
        self.finger_marks.clear()
        self.finger_count = self.finger_near = 0
        self.readout.set(fingers=0, palm=0)
        self.queue_draw()

    def draw(self, area, cr, width, height):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_line_cap(1)  # round
        for rgb, points in self.strokes:
            cr.set_source_rgb(*rgb)
            if len(points) == 1:
                x, y, w = points[0]
                cr.arc(x, y, w / 2, 0, 6.2832)
                cr.fill()
            for (x0, y0, w0), (x1, y1, w1) in zip(points, points[1:]):
                cr.set_line_width((w0 + w1) / 2)
                cr.move_to(x0, y0)
                cr.line_to(x1, y1)
                cr.stroke()
        cr.set_line_width(2)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.7)
        for x, y in self.finger_marks:
            cr.arc(x, y, 18, 0, 6.2832)
            cr.stroke()
        if self.hover:
            cr.set_source_rgba(0.2, 0.6, 0.2, 0.6)
            cr.arc(self.hover[0], self.hover[1], 10, 0, 6.2832)
            cr.stroke()


def main():
    app = Gtk.Application(application_id="org.duo2os.PenTest")

    def activate(app):
        win = Gtk.ApplicationWindow(application=app, title="Pen test")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        readout = Readout()
        readout.set(state="away", fingers=0, palm=0)
        canvas = Canvas(readout)
        clear = Gtk.Button(label="Clear", margin_start=8, margin_top=4)
        clear.connect("clicked", lambda b: canvas.clear())
        bar = Gtk.Box(spacing=8)
        bar.append(clear)
        hint = Gtk.Label(
            label="Write, press harder, tilt while writing, hold the side button, turn the pen round.",
            xalign=0,
            wrap=True,
            hexpand=True,
        )
        hint.add_css_class("dim-label")
        bar.append(hint)
        box.append(bar)
        box.append(readout)
        box.append(canvas)
        win.set_child(box)
        win.present()
        log("pen test window up")

    app.connect("activate", activate)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
