"""The labels on laptop mode's keyboard panel.

duo-wc draws the key caps and the touchpad on the keyboard panel and takes
every touch there (laptopmodel); this see-through surface, the only one
duo-wc shows on that panel (namespace `duo-laptop-keys`), puts each key's
label on its cap. Its input region is empty: the keys are duo-wc's.
"""

import gi

gi.require_foreign("cairo")  # python3-gi-cairo: Gdk.Surface.set_input_region

import cairo  # noqa: E402
from gi.repository import Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import laptopmodel  # noqa: E402

NAMESPACE = "duo-laptop-keys"

# Label colours: the main text nearly white, the corner ones dimmed, the
# layout key a little blue (duo-wc tints its cap); in the list of layouts
# the configured ones white, the others dimmed.
MAIN = (0.93, 0.94, 0.96)
CORNER = (0.60, 0.63, 0.68)
ACCENT = (0.80, 0.88, 1.0)
COLOURS = {"layout": ACCENT, "menu-active": (1.0, 1.0, 1.0), "menu": MAIN, "menu-add": CORNER}


class LaptopKeys(Gtk.Window):
    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.labels = []
        self.shown = None
        self.add_css_class("duo-laptop-keys")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, NAMESPACE)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        # The whole panel, in its own frame (the caps are given in it).
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.connect("realize", self._see_through)
        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_can_target(False)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)

    def _see_through(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show_labels(self, state):
        sig = laptopmodel.signature(state)
        if sig == self.shown and self.get_visible():
            return
        self.shown = sig
        self.labels = laptopmodel.labels(state) + laptopmodel.menu_labels(state)
        self.area.queue_draw()
        self.present()

    def hide_labels(self):
        self.shown = None
        self.labels = []
        if self.get_visible():
            self.set_visible(False)

    def _draw(self, _area, ctx, _w, _h):
        from gi.repository import Pango, PangoCairo

        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)

        def layout_of(text, px, bold=True):
            layout = PangoCairo.create_layout(ctx)
            font = Pango.FontDescription.from_string("Cantarell Bold" if bold else "Cantarell")
            font.set_absolute_size(max(6, px) * Pango.SCALE)
            layout.set_font_description(font)
            layout.set_text(text, -1)
            return layout

        for label in self.labels:
            if label.main:
                px = label.h * label.size
                layout = layout_of(label.main, px)
                _ink, logical = layout.get_pixel_extents()
                # Words never wider than the cap.
                room = label.w - 8
                if logical.width > room > 0:
                    layout = layout_of(label.main, px * room / logical.width)
                    _ink, logical = layout.get_pixel_extents()
                ctx.set_source_rgb(*COLOURS.get(label.kind, MAIN))
                ctx.move_to(
                    label.x + (label.w - logical.width) / 2,
                    label.y + (label.h - logical.height) / 2,
                )
                PangoCairo.show_layout(ctx, layout)
            if label.corner:
                layout = layout_of(label.corner, label.h * 0.24, bold=False)
                ctx.set_source_rgb(*CORNER)
                ctx.move_to(label.x + 4, label.y + 1)
                PangoCairo.show_layout(ctx, layout)
            if label.third:
                # What AltGr types: small at the bottom right.
                layout = layout_of(label.third, label.h * 0.24, bold=False)
                _ink, logical = layout.get_pixel_extents()
                ctx.set_source_rgb(*ACCENT)
                ctx.move_to(
                    label.x + label.w - logical.width - 4, label.y + label.h - logical.height - 1
                )
                PangoCairo.show_layout(ctx, layout)


def sync(shell, state, make):
    """Show the labels on laptop mode's keyboard panel, hide them
    elsewhere. `make(name)` gives a panel's LaptopKeys (made when first
    needed)."""
    keys = laptopmodel.keys_panel(state)
    for name, surface in list(shell.laptop_keys.items()):
        if name != keys:
            surface.hide_labels()
    if keys:
        surface = make(keys)
        if surface is not None:
            surface.show_labels(state)


__all__ = ["LaptopKeys", "sync", "NAMESPACE"]
