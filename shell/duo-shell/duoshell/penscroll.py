"""The pen scrolls like a finger in our own GTK 4 windows.

GTK 4 drags a scrolled view only with a touchscreen: GtkScrolledWindow's drag,
pan, swipe and long-press gestures are all `touch_only` (gtkscrolledwindow.c),
and GtkGestureSingle drops every button or motion event whose device source is
not GDK_SOURCE_TOUCHSCREEN (gtkgesturesingle.c). A tablet tool's tip is a
button-1 press of a GDK_SOURCE_PEN device, so in every GTK 4 (and GTK 3) app a
pen drag selects or does nothing where a finger scrolls. A pen-aware app is the
exception on purpose: a canvas takes the pen itself.

Which press is the pen's: on Wayland GTK 4 gives a tablet's events the
tablet's logical pointer device, whose source is GDK_SOURCE_MOUSE
(gdkseat-wayland.c, 4.18); only the event's device TOOL says pen. So the
press is the pen's when its tool is a pen, an eraser or a brush-like tool,
or (X11) when its device's source is GDK_SOURCE_PEN. (Until 2026-11-16 this
looked at the device's source alone and never matched on Wayland.)

install(window) adds one drag gesture to a window, in the capture phase as
GTK's own touch gesture is: a pen press inside a scrolled view that can move
(not on its scrollbar, not inside a widget with the `pen-input` style class)
starts it; past the drag threshold it claims the press (the button or row
under the pen is cancelled, as with a finger) and moves the view with the pen;
a flick goes on and slows down. A widget that claims the press itself (a
slider, a text field) keeps it, and a mouse or a finger is left to GTK. The
same file is shell/duo-shell/duoshell/penscroll.py (a test keeps them equal).
"""

import math

FRICTION = 4.0  # 1/s: v(t) = v0 * e^(-FRICTION t), as GTK's kinetic scrolling
MIN_FLICK = 120.0  # px/s: slower than this at the lift is no flick
STOP_SPEED = 8.0  # px/s: the deceleration ends here
SAMPLE_WINDOW = 0.1  # s: the lift's velocity from the last 100 ms of the drag
MAX_SPEED = 6000.0  # px/s


def past_threshold(dx, dy, threshold):
    """GTK's drag threshold test (a square, as gtk_drag_check_threshold). Pure."""
    return abs(dx) > threshold or abs(dy) > threshold


def clamp_value(value, lower, upper, page):
    """An adjustment's value kept to its range. Pure."""
    return max(lower, min(value, max(lower, upper - page)))


def can_move(lower, upper, page):
    """Whether an adjustment has anywhere to go. Pure."""
    return upper - page - lower > 0.5


def velocity(samples, window=SAMPLE_WINDOW):
    """(vx, vy) in px/s of the pen from [(t s, x, y)], the last `window` seconds, capped. Pure."""
    if len(samples) < 2:
        return 0.0, 0.0
    t1, x1, y1 = samples[-1]
    first = samples[0]
    for s in samples:
        if t1 - s[0] <= window:
            first = s
            break
    t0, x0, y0 = first
    dt = t1 - t0
    if dt <= 0:
        return 0.0, 0.0
    vx, vy = (x1 - x0) / dt, (y1 - y0) / dt
    speed = math.hypot(vx, vy)
    if speed > MAX_SPEED:
        vx, vy = vx * MAX_SPEED / speed, vy * MAX_SPEED / speed
    return vx, vy


def glide(v0, t, friction=FRICTION):
    """How far a flick at v0 px/s has gone after t s, and its speed then. Pure."""
    k = math.exp(-friction * t)
    return v0 * (1.0 - k) / friction, v0 * k


PEN_TOOLS = ("pen", "eraser", "brush", "pencil", "airbrush")


def pen_press(source_nick, tool_nick):
    """Whether a press is the pen's, from its device's source and its device
    tool's type (GdkInputSource / GdkDeviceToolType nicks, None for no tool). Pure."""
    return source_nick == "pen" or tool_nick in PEN_TOOLS


def opted_out(classes_of_ancestors):
    """A widget or one of its ancestors takes the pen itself. Pure."""
    return any("pen-input" in classes for classes in classes_of_ancestors)


try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk
except (ImportError, ValueError):  # the pure part without GTK (tests)
    Gdk = GLib = Gtk = None


def _threshold(widget):
    try:
        return float(widget.get_settings().get_property("gtk-dnd-drag-threshold"))
    except (TypeError, ValueError, AttributeError):
        return 8.0


def _scroller_at(window, x, y):
    """The scrolled view under (x, y) in window coordinates that can move, or None."""
    target = window.pick(x, y, Gtk.PickFlags.DEFAULT)
    w, classes, sw = target, [], None
    while w is not None:
        if isinstance(w, Gtk.Scrollbar):
            return None
        classes.append(w.get_css_classes())
        if sw is None and isinstance(w, Gtk.ScrolledWindow):
            h, v = w.get_hadjustment(), w.get_vadjustment()
            hp, vp = w.get_policy()
            if (
                vp != Gtk.PolicyType.NEVER
                and can_move(v.get_lower(), v.get_upper(), v.get_page_size())
            ) or (
                hp != Gtk.PolicyType.NEVER
                and can_move(h.get_lower(), h.get_upper(), h.get_page_size())
            ):
                sw = w
        w = w.get_parent()
    return None if opted_out(classes) else sw


def _nick(value):
    try:
        return value.value_nick
    except AttributeError:
        return None


def is_pen(gesture):
    """The press the gesture is handling is the pen's (see the module's text)."""
    ev = gesture.get_current_event()
    tool = ev.get_device_tool() if ev is not None else None
    dev = gesture.get_device()
    return pen_press(
        _nick(dev.get_source()) if dev is not None else None,
        _nick(tool.get_tool_type()) if tool is not None else None,
    )


class PenScroll:
    def __init__(self, window):
        self.window = window
        self.sw = None
        self.start = (0.0, 0.0)
        self.samples = []
        self.claimed = False
        self.glides = {}  # scrolled window -> tick callback id
        g = Gtk.GestureDrag()
        g.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        g.connect("drag-begin", self.begin)
        g.connect("drag-update", self.update)
        g.connect("drag-end", self.end)
        g.connect("cancel", lambda *_a: self.reset())
        window.add_controller(g)
        self.gesture = g

    def reset(self):
        self.sw, self.samples, self.claimed = None, [], False

    def begin(self, g, x, y):
        self.reset()
        if not is_pen(g):
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        sw = _scroller_at(self.window, x, y)
        if sw is None:
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.stop_glide(sw)
        self.sw = sw
        self.start = (sw.get_hadjustment().get_value(), sw.get_vadjustment().get_value())
        self.samples = [(GLib.get_monotonic_time() / 1e6, 0.0, 0.0)]

    def _move(self, adj, policy, value):
        if policy == Gtk.PolicyType.NEVER or not can_move(
            adj.get_lower(), adj.get_upper(), adj.get_page_size()
        ):
            return False
        v = clamp_value(value, adj.get_lower(), adj.get_upper(), adj.get_page_size())
        moved = abs(v - adj.get_value()) > 0.01
        adj.set_value(v)
        return moved

    def update(self, g, dx, dy):
        if self.sw is None:
            return
        self.samples.append((GLib.get_monotonic_time() / 1e6, dx, dy))
        del self.samples[:-32]
        if not self.claimed:
            if not past_threshold(dx, dy, _threshold(self.window)):
                return
            self.claimed = True
            g.set_state(Gtk.EventSequenceState.CLAIMED)
        hp, vp = self.sw.get_policy()
        self._move(self.sw.get_hadjustment(), hp, self.start[0] - dx)
        self._move(self.sw.get_vadjustment(), vp, self.start[1] - dy)

    def end(self, g, dx, dy):
        sw, claimed, samples = self.sw, self.claimed, self.samples
        self.reset()
        if sw is None or not claimed:
            return
        vx, vy = velocity(samples)
        if math.hypot(vx, vy) < MIN_FLICK:
            return
        h0, v0 = sw.get_hadjustment().get_value(), sw.get_vadjustment().get_value()
        t0 = [None]

        def tick(widget, clock):
            now = clock.get_frame_time() / 1e6
            if t0[0] is None:
                t0[0] = now
            t = now - t0[0]
            ox, sx = glide(-vx, t)
            oy, sy = glide(-vy, t)
            hp, vp = sw.get_policy()
            moved = self._move(sw.get_hadjustment(), hp, h0 + ox)
            moved = self._move(sw.get_vadjustment(), vp, v0 + oy) or moved
            if (not moved and t > 0.05) or math.hypot(sx, sy) < STOP_SPEED:
                self.glides.pop(sw, None)
                return False
            return True

        self.glides[sw] = sw.add_tick_callback(tick)

    def stop_glide(self, sw):
        tid = self.glides.pop(sw, None)
        if tid is not None:
            sw.remove_tick_callback(tid)


def install(window):
    """Let the pen scroll the window's scrolled views (once per window)."""
    if Gtk is None or getattr(window, "_pen_scroll", None) is not None:
        return None
    window._pen_scroll = PenScroll(window)
    return window._pen_scroll
