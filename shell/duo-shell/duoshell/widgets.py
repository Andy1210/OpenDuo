"""Small GTK pieces the shell's surfaces share."""

from gi.repository import Gio, GLib, Graphene, Gtk

from . import swipe as swipelogic


class Swipeable(Gtk.Box):
    """A box that follows a finger sideways (and, if `up`, upwards) and is
    dismissed when let go far enough (`on_dismiss()`); a touch that hardly
    moved is a tap (`on_tap()`). The picture moves (a snapshot translation),
    the layout does not. A vertical drag that is not a dismissal is given up,
    so a scrolled list around it still scrolls."""

    def __init__(self, on_dismiss, on_tap=None, up=False, **kwargs):
        super().__init__(**kwargs)
        self.on_dismiss = on_dismiss
        self.on_tap = on_tap
        self.up = up
        self.dx = 0.0
        self.dy = 0.0
        self.axis = None
        drag = Gtk.GestureDrag()
        drag.set_touch_only(False)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)

    def _update(self, gesture, dx, dy):
        if self.axis is None:
            self.axis = swipelogic.decide(dx, dy, self.up)
            if self.axis == "deny":
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
            if self.axis is not None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self.axis == "x":
            self.dx, self.dy = dx, 0.0
        elif self.axis == "y":
            self.dx, self.dy = 0.0, min(0.0, dy)
        else:
            return
        self.set_opacity(swipelogic.opacity(self.dx, self.dy, self.get_width() or 300))
        self.queue_draw()

    def _end(self, _gesture, dx, dy):
        axis, self.axis = self.axis, None
        verdict = swipelogic.release(axis, dx, dy, self.get_width() or 300, self.get_height() or 80)
        self.dx = self.dy = 0.0
        self.set_opacity(1.0)
        self.queue_draw()
        if verdict == "dismiss":
            self.on_dismiss()
        elif verdict == "tap" and self.on_tap is not None:
            self.on_tap()

    def do_snapshot(self, snapshot):
        if self.dx or self.dy:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(self.dx, self.dy))
            Gtk.Box.do_snapshot(self, snapshot)
            snapshot.restore()
        else:
            Gtk.Box.do_snapshot(self, snapshot)


def icon_image(icon, size, fallback="dialog-information-symbolic"):
    """A Gtk.Image for a notifystore icon tuple."""
    image = None
    if icon is not None:
        kind, value = icon
        if kind == "file" and GLib.file_test(value, GLib.FileTest.EXISTS):
            image = Gtk.Image.new_from_file(value)
        elif kind == "name":
            image = Gtk.Image.new_from_icon_name(value)
        elif kind == "desktop":
            name = value if value.endswith(".desktop") else value + ".desktop"
            try:
                info = Gio.DesktopAppInfo.new(name)
            except TypeError:
                info = None
            if info is not None and info.get_icon() is not None:
                image = Gtk.Image.new_from_gicon(info.get_icon())
    if image is None:
        image = Gtk.Image.new_from_icon_name(fallback)
    image.set_pixel_size(size)
    return image


def icon_button(icon, label, callback, css=None):
    button = Gtk.Button()
    button.set_child(Gtk.Image.new_from_icon_name(icon))
    button.set_tooltip_text(label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.set_focus_on_click(False)
    button.set_can_focus(False)
    if css:
        button.add_css_class(css)
    button.connect("clicked", lambda _b: callback())
    return button
