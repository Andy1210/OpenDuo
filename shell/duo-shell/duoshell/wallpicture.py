"""The wallpaper on a home screen, cut when it is drawn.

The picture is decoded once (wallpaperview.py) and each panel's home screen
draws its part of it at draw time, from the layout duo-wc reports
(`get_outputs`: the panels' places in the one canvas), with the GPU doing
the scaling. So a rotation or a pose change needs no new cut: the first
frame the home screen draws at its new size is already the right part of
the picture. (It used to be cut per panel off the main thread 400 ms after
the change, and the cut popped in after duo-wc's turn animation, the
owner, 2026-11-07.) The geometry is wallpaper.py's (pure, tested).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from . import wallpaper, wc  # noqa: E402


def layout():
    """duo-wc's panels now: {name: (x, y, w, h)} in logical px ({} when it
    does not answer)."""
    try:
        return wallpaper.panels_from_outputs(wc.request("get_outputs", timeout=0.5).get("outputs"))
    except (OSError, RuntimeError, ValueError, AttributeError):
        return {}


class WallPicture(Gtk.Widget):
    """One panel's part of the wallpaper (a Gdk.Texture of the whole picture)."""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.texture = None
        self.mode = "same"
        self.panels = {}
        self.size = None
        self.set_can_target(False)

    def set_picture(self, texture, mode):
        self.texture = texture
        self.mode = mode
        self.queue_draw()

    def set_layout(self, panels):
        if panels and panels != self.panels:
            self.panels = dict(panels)
            self.queue_draw()

    def do_size_allocate(self, width, height, baseline):
        # A new size (a rotation, a pose): the layout it belongs to, now,
        # before this frame is drawn.
        if self.size != (width, height):
            self.size = (width, height)
            self.set_layout(layout())

    def do_snapshot(self, snapshot):
        tex = self.texture
        w, h = self.get_width(), self.get_height()
        if tex is None or w <= 0 or h <= 0:
            return
        x, y, dw, dh = wallpaper.draw_rect(
            self.name, self.panels, self.mode, (w, h), (tex.get_width(), tex.get_height())
        )
        snapshot.push_clip(Graphene.Rect().init(0, 0, w, h))
        snapshot.append_scaled_texture(
            tex, Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init(x, y, dw, dh)
        )
        snapshot.pop()
