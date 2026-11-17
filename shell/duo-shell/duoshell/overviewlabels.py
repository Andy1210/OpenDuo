"""Titles and app icons on duo-wc's task overview cards.

duo-wc draws the overview but no text; it keeps a band above each card and
says where on its event stream (overviewmodel). One see-through surface per
panel, on the overlay layer over the whole panel, puts the app's icon and
the window's title in those bands. Its input region is empty, so every
touch still goes to the overview under it (duo-wc hit-tests overlay
surfaces by their input region).
"""

import gi

gi.require_foreign("cairo")  # python3-gi-cairo: Gdk.Surface.set_input_region

import cairo  # noqa: E402
from gi.repository import Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import overviewmodel  # noqa: E402
from .i18n import _  # noqa: E402

ICON_PX = 20


class OverviewLabels(Gtk.Window):
    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.shown = None
        self.add_css_class("duo-overview-labels")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-overview-labels")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        # The whole panel, under the status bar too: the bands are given in
        # the panel's own frame.
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.fixed = Gtk.Fixed()
        self.fixed.set_can_target(False)
        self.set_child(self.fixed)
        self.connect("realize", self._see_through)

    def _see_through(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show_labels(self, labels, infos):
        """labels: [overviewmodel.Label]; infos: desktop id -> AppInfo."""
        key = tuple(label.key() for label in labels)
        if key == self.shown and self.get_visible():
            return
        self.shown = key
        child = self.fixed.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.fixed.remove(child)
            child = nxt
        for label in labels:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.add_css_class("card-label")
            box.set_size_request(max(label.w, 1), max(label.h, 1))
            box.set_valign(Gtk.Align.CENTER)
            image = Gtk.Image()
            info = infos.get(label.desktop_id) if label.desktop_id else None
            if info is not None and info.get_icon() is not None:
                image.set_from_gicon(info.get_icon())
            else:
                image.set_from_icon_name("application-x-executable")
            image.set_pixel_size(ICON_PX)
            shown = label.text
            if label.state == "closing":
                # Translators: an overview card whose window was asked to close.
                shown = _("Closing…")
            elif label.state == "stuck":
                # Translators: an overview card whose app does not answer.
                shown = _("Not responding")
                box.add_css_class("stuck")
            text = Gtk.Label(label=shown)
            text.set_ellipsize(3)  # Pango.EllipsizeMode.END
            text.set_xalign(0.0)
            text.set_hexpand(True)
            text.set_max_width_chars(1)
            box.append(image)
            box.append(text)
            self.fixed.put(box, label.x, label.y)
        self.present()

    def hide_labels(self):
        self.shown = None
        if self.get_visible():
            self.set_visible(False)


def sync(shell, labels_by_output, make):
    """Show each panel's labels, hide the rest. `make(name)` gives a panel's
    OverviewLabels (made when first needed)."""
    _index, infos = shell.app_index()
    for name in list(shell.bars):
        labels = labels_by_output.get(name)
        if labels:
            surface = make(name)
            if surface is not None:
                surface.show_labels(labels, infos)
        else:
            surface = shell.overview_labels.get(name)
            if surface is not None:
                surface.hide_labels()


__all__ = ["OverviewLabels", "sync", "overviewmodel"]
