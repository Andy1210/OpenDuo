"""The closed phone's ambient screens (peek, glance): one black overlay
surface per panel with a strip of information on one edge (where and what:
ambientmodel.py). duo-wc shows nothing but these (`duo-ambient`) while its
state says `ambient`, and ignores touches then; the surface takes none
either (an empty input region).

While a call rings in, the line says who calls instead (a handset, the
contact's name or the number, the time): the shell's state carries it as
`incoming_call` (callsmodel.strip_caller), never logged.
"""

import math

import cairo
from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import ambientmodel, status, svgicon, timefmt
from .widgets import icon_image


class AmbientView(Gtk.Window):
    """One panel's peek / glance screen.

    Made when the panel appears and kept MAPPED (duo-wc draws `duo-ambient`
    surfaces only while it shows an ambient screen, and lets every touch
    through), with one drawing area: showing a strip is a redraw, not a
    new surface. A new layer surface took seconds (its configure, GTK's
    first frame, the widgets) while the phone was being opened; duo-wc
    keeps the panel black until this surface has drawn for the screen
    (render::Ambient), so the redraw is all that stands between the
    posture and the strip."""

    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.shown = None
        self.data = None
        self.add_css_class("duo-ambient")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-ambient")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.connect("realize", self._see_through)
        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_can_target(False)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self.present()

    def _see_through(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show_plan(self, plan, state):
        """Draw the strip for `plan` (ambientmodel.plan) with the shell's
        `state` (battery, notifications); None: nothing to show (the
        surface stays, duo-wc does not draw it)."""
        if plan is None:
            self.shown = None
            self.data = None
            if self.get_child() is not self.area:
                self.set_child(self.area)
            return
        side = plan["edge"] in ("left", "right")
        if plan["kind"] == "peek" and plan.get("style") != "line":
            # The old column look (peek_style = column), widgets.
            self._show_column(plan, state, side)
            return
        now = GLib.DateTime.new_now_local()
        percent, _charge = state.get("battery", (None, None))
        # The time, then the weekday and the full date in the session's
        # language (the owner, 2026-11-08); shorter forms of the date, and
        # the time alone, for a strip with no room (_draw_glance picks).
        text = ambientmodel.strip_lines(
            now.format(timefmt.hm()) or "",
            [now.format(f) or "" for f in timefmt.full_date_formats()],
        )
        marker, icons = ambientmodel.notes_marker(
            plan.get("notes", "off"), state.get("notifications")
        )
        caller = state.get("incoming_call")
        if plan["kind"] == "ruler":
            data = ("ruler", plan)
        elif caller:
            # A call rings in: who calls, then the time (no date, battery or
            # notifications: the one thing that matters now).
            data = ("call", (plan, caller, now.format(timefmt.hm()) or ""))
        else:
            data = ("line", (plan, text, state.get("battery", (None, None)), marker, icons))
        if self.get_child() is not self.area:
            self.set_child(self.area)
        if data != self.data or plan != self.shown:
            self.data = data
            self.shown = plan
        # Always a fresh picture: duo-wc shows the panel black until this
        # surface commits one for the ambient screen that just started.
        self.area.queue_draw()

    def _draw(self, area, ctx, w, h):
        if self.data is None:
            ctx.set_source_rgb(0, 0, 0)
            ctx.paint()
            return
        kind, data = self.data
        if kind == "ruler":
            _draw_ruler(area, ctx, w, h, data)
        elif kind == "call":
            _draw_call(ctx, w, h, data)
        else:
            _draw_glance(area, ctx, w, h, data)

    def _show_column(self, plan, state, side):
        strip = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL if side else Gtk.Orientation.HORIZONTAL, spacing=10
        )
        strip.add_css_class("strip")
        strip.add_css_class(plan["kind"])
        strip.set_halign(Gtk.Align.CENTER)
        strip.set_valign(Gtk.Align.CENTER)
        now = GLib.DateTime.new_now_local()
        if side:
            # Narrow and tall: the hours over the minutes.
            clock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for part in (now.format(timefmt.hm().split(":")[0]), now.format("%M")):
                label = Gtk.Label(label=part)
                label.add_css_class("clock")
                clock.append(label)
        else:
            clock = Gtk.Label(label=now.format(timefmt.hm()))
            clock.add_css_class("clock")
        strip.append(clock)
        percent, charge = state.get("battery", (None, None))
        battery = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL if side else Gtk.Orientation.HORIZONTAL, spacing=2
        )
        battery.set_halign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(status.battery_icon(percent, charge))
        image.set_pixel_size(18 if plan["big"] else 14)
        battery.append(image)
        if percent is not None:
            label = Gtk.Label(label="%d%%" % percent)
            label.add_css_class("percent")
            battery.append(label)
        strip.append(battery)
        if plan["notifications"]:
            # The newest notifications' apps (notifystore.Notification).
            for note in (state.get("notifications") or [])[:4]:
                strip.append(icon_image(note.icon(), 22))
        if plan["big"]:
            strip.add_css_class("big")
        # The strip on its edge, the rest black.
        holder = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL if side else Gtk.Orientation.VERTICAL
        )
        holder.set_hexpand(True)
        holder.set_vexpand(True)
        band = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if side else Gtk.Orientation.HORIZONTAL)
        # The band is exactly `width` across (set explicitly: an expanding
        # child would otherwise make it share the panel with the filler).
        if side:
            band.set_size_request(plan["width"], -1)
            band.set_vexpand(True)
            band.set_hexpand(False)
            strip.set_vexpand(True)
            strip.set_hexpand(False)
        else:
            band.set_size_request(-1, plan["width"])
            band.set_hexpand(True)
            band.set_vexpand(False)
            strip.set_hexpand(True)
            strip.set_vexpand(False)
        band.append(strip)
        filler = Gtk.Box()
        filler.set_hexpand(True)
        filler.set_vexpand(True)
        if plan["edge"] in ("left", "top"):
            holder.append(band)
            holder.append(filler)
        else:
            holder.append(filler)
            holder.append(band)
        self.set_child(holder)
        self.shown = plan


def _scale_of(area):
    """The surface's scale (2.5 on the Duo 2's panels; 1 if unknown)."""
    try:
        native = area.get_native()
        surface = native.get_surface() if native is not None else None
        return surface.get_scale() if surface is not None else 1.0
    except AttributeError:
        return 1.0


def _strip_rect(edge, inset, width, w, h):
    """(x, y, w, h) of a strip `width` wide starting `inset` from `edge`."""
    if edge == "left":
        return inset, 0, width, h
    if edge == "right":
        return w - inset - width, 0, width, h
    if edge == "top":
        return 0, inset, w, width
    return 0, h - inset - width, w, width


def _draw_glance(area, ctx, w, h, data):
    """The time, the battery and the notification marker on one line along
    the strip, sized to it."""
    from gi.repository import Pango, PangoCairo

    plan, lines, battery, marker, icons = data
    ctx.set_source_rgb(0, 0, 0)
    ctx.paint()
    x, y, sw, sh = _strip_rect(plan["edge"], plan["inset"], plan["width"], w, h)
    standing = plan["edge"] in ("left", "right")
    thick = sw if standing else sh
    fsize = max(6, int(thick * 0.72))

    def layout_of(text, size=fsize):
        layout = PangoCairo.create_layout(ctx)
        font = Pango.FontDescription.from_string("Cantarell Bold")
        font.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(font)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(size * 0.125 * Pango.SCALE)))
        layout.set_attributes(attrs)
        layout.set_text(text, -1)
        return layout

    percent, charge = battery
    gap = max(2, fsize // 3)
    # The battery is centred on the line, so its outer edge must stay on the
    # visible part of a strip that sticks out past the panel's edge (inset < 0).
    size = max(4, int(min(thick * 0.9, thick + 2 * min(0.0, plan["inset"]) - 3)))
    # The drawn battery belongs to the peek (percent inside). The glance
    # (hinge sliver) is too thin for one: its line carries "NN%" as text.
    draw_battery = percent is not None and plan.get("battery") == "inside"
    batt_w = 0
    if draw_battery:
        batt_w = gap * 2 + _battery_extent(size, True)
    marker_w = gap * 2 + layout_of(marker.strip()).get_pixel_extents()[1].width if marker else 0
    icons_w = len(icons) * (size + gap) + gap * 2 if icons else 0
    # The longest line that fits along the strip (the panel's length past
    # `along`, the same again at the far end), whatever the rotation.
    room = (sh if standing else sw) - 2 * plan["along"]
    suffix = "" if percent is None or plan.get("battery") == "inside" else "   %d%%" % percent
    for line in lines:
        main = layout_of(line + suffix)
        _ink, logical = main.get_pixel_extents()
        if logical.width + batt_w + marker_w + icons_w <= room:
            break
    ctx.set_source_rgb(1, 1, 1)
    if standing:
        ctx.translate(x + sw / 2, y + plan["along"] + logical.width / 2)
        ctx.rotate(math.pi / 2 if plan["turn"] == 270 else -math.pi / 2)
    else:
        ctx.translate(x + plan["along"] + logical.width / 2, y + sh / 2)
    ctx.translate(-logical.width / 2, -logical.height / 2)
    PangoCairo.show_layout(ctx, main)
    left = logical.width
    if draw_battery:
        ctx.save()
        ctx.translate(left + gap * 2, (logical.height - size) / 2)
        left += batt_w
        _draw_battery(ctx, layout_of, size, percent, charge, plan.get("battery") == "inside")
        ctx.restore()
    if marker:
        text = layout_of(marker.strip())
        ctx.save()
        ctx.translate(left + gap * 2, 0)
        ctx.set_source_rgb(1, 1, 1)
        PangoCairo.show_layout(ctx, text)
        ctx.restore()
        left += gap * 2 + text.get_pixel_extents()[1].width
    if icons:
        _draw_icons(area, ctx, icons, size, gap, left + gap * 2, (logical.height - size) / 2)


def _text_layout(ctx, text, size):
    """The strips' text: Cantarell Bold, `size` px, letter spacing 0.125
    (the stock spine clock's)."""
    from gi.repository import Pango, PangoCairo

    layout = PangoCairo.create_layout(ctx)
    font = Pango.FontDescription.from_string("Cantarell Bold")
    font.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(font)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_letter_spacing_new(int(size * 0.125 * Pango.SCALE)))
    layout.set_attributes(attrs)
    layout.set_text(text, -1)
    return layout


def _draw_call(ctx, w, h, data):
    """An incoming call on the strip: a handset, who calls and the time on
    one line along the strip, sized to it like the clock's line
    (ambientmodel.call_line fits it to the strip's length, at any
    rotation: the length is the panel's along its edge)."""
    from gi.repository import PangoCairo

    plan, caller, clock = data
    ctx.set_source_rgb(0, 0, 0)
    ctx.paint()
    x, y, sw, sh = _strip_rect(plan["edge"], plan["inset"], plan["width"], w, h)
    standing = plan["edge"] in ("left", "right")
    thick = sw if standing else sh
    fsize = max(6, int(thick * 0.72))
    gap = max(2, fsize // 3)
    # The handset is as tall as the battery would be (its outer edge on the
    # visible part of a strip that sticks out past the panel's edge).
    size = max(4, int(min(thick * 0.9, thick + 2 * min(0.0, plan["inset"]) - 3)))
    icon_w = size + gap * 2
    room = ambientmodel.strip_room(plan, w, h) - icon_w

    def measure(text):
        return _text_layout(ctx, text, fsize).get_pixel_extents()[1].width

    layout = _text_layout(ctx, ambientmodel.call_line(caller, clock, room, measure), fsize)
    _ink, logical = layout.get_pixel_extents()
    total = icon_w + logical.width
    ctx.set_source_rgb(1, 1, 1)
    if standing:
        ctx.translate(x + sw / 2, y + plan["along"] + total / 2)
        ctx.rotate(math.pi / 2 if plan["turn"] == 270 else -math.pi / 2)
    else:
        ctx.translate(x + plan["along"] + total / 2, y + sh / 2)
    ctx.translate(-total / 2, -logical.height / 2)
    icon = svgicon.load("call-start-symbolic")
    if icon is not None:
        svgicon.draw(ctx, icon, size / 2, logical.height / 2, size)
    else:
        _draw_handset(ctx, size / 2, logical.height / 2, size)
    ctx.move_to(icon_w, 0)
    PangoCairo.show_layout(ctx, layout)


def _draw_handset(ctx, cx, cy, size):
    """A telephone handset `size` px square around (cx, cy), lifted (the
    lock screen's Answer handset, lockclient.draw_handset, fitted to a
    box)."""
    s = size / 1.35
    ctx.save()
    # The handset's shape sits up and to the left of its own centre.
    ctx.translate(cx + s * 0.2, cy - s * 0.2)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    # the grip: an arc from the earpiece to the mouthpiece
    ctx.set_line_width(s * 0.4)
    ctx.new_path()
    ctx.arc(s * 0.35, -s * 0.35, s * 0.95, math.radians(100), math.radians(170))
    ctx.stroke()
    # the two ends
    ctx.set_line_width(s * 0.5)
    for ang in (95, 175):
        a = math.radians(ang)
        ex, ey = s * 0.35 + s * 0.95 * math.cos(a), -s * 0.35 + s * 0.95 * math.sin(a)
        tx, ty = -math.sin(a), math.cos(a)  # along the arc
        nx, ny = math.cos(a), math.sin(a)  # out of the arc
        ctx.move_to(ex - tx * s * 0.08, ey - ty * s * 0.08)
        ctx.line_to(ex - nx * s * 0.18, ey - ny * s * 0.18)
        ctx.stroke()
    ctx.restore()


def _battery_width(size, inside):
    return int(size * (2.1 if inside else 1.2))


def _battery_extent(size, inside):
    """The battery's full width as drawn: the body, the terminal nub and the
    stroke's outer half (what the next item must clear)."""
    line = max(1.0, size * 0.09)
    return _battery_width(size, inside) + int(math.ceil(line)) + 1


def _draw_battery(ctx, layout_of, size, percent, charge, inside):
    """A battery outline `size` tall at the origin, filled to `percent`;
    with the number inside it for the peek. A bolt-less charge cue: the
    outline is drawn thicker-bright (kept simple at 13 px)."""
    width = _battery_width(size, inside)
    nub = max(1.0, size * 0.12)
    body = width - nub
    line = max(1.0, size * 0.09)
    ctx.set_source_rgb(1, 1, 1)
    ctx.set_line_width(line)
    ctx.rectangle(line / 2, line / 2, body - line, size - line)
    ctx.stroke()
    ctx.rectangle(body, size * 0.3, nub, size * 0.4)
    ctx.fill()
    frac = max(0.0, min(1.0, percent / 100.0))
    inner = body - line * 3
    ctx.set_source_rgba(1, 1, 1, 0.38 if inside else 1.0)
    ctx.rectangle(line * 1.5, line * 1.5, max(0.0, inner * frac), size - line * 3)
    ctx.fill()
    if inside:
        from gi.repository import PangoCairo

        label = layout_of(str(int(percent)), max(5, int(size * 0.6)))
        lw, lh = label.get_pixel_extents()[1].width, label.get_pixel_extents()[1].height
        ctx.set_source_rgb(1, 1, 1)
        ctx.move_to((body - lw) / 2, (size - lh) / 2)
        PangoCairo.show_layout(ctx, label)


def _draw_icons(area, ctx, icons, size, gap, left, top):
    for icon in icons:
        ctx.save()
        ctx.translate(left, top)
        _paint_icon(ctx, area, icon, size)
        ctx.restore()
        left += size + gap


_ICONS = {}


def _icon_paintable(area, icon, size):
    """A Gdk.Paintable for a notifystore icon tuple (theme / file / desktop
    entry), or None; cached (the strip is drawn while the phone opens: no
    icon loading then)."""
    key = (tuple(icon) if icon else None, size, int(round(_scale_of(area))) or 1)
    if key in _ICONS:
        return _ICONS[key]
    if len(_ICONS) > 64:
        _ICONS.clear()
    _ICONS[key] = paintable = _lookup_icon(area, icon, size)
    return paintable


def _lookup_icon(area, icon, size):
    from gi.repository import Gdk, Gio, Gtk

    theme = Gtk.IconTheme.get_for_display(area.get_display())
    scale = int(round(_scale_of(area))) or 1
    kind, value = icon if icon else (None, None)
    flags = Gtk.IconLookupFlags.FORCE_REGULAR
    try:
        if kind == "file":
            return Gdk.Texture.new_from_filename(value)
        if kind == "name" and theme.has_icon(value):
            return theme.lookup_icon(value, None, size, scale, Gtk.TextDirection.NONE, flags)
        if kind == "desktop":
            info = Gio.DesktopAppInfo.new(
                value if value.endswith(".desktop") else value + ".desktop"
            )
            if info is not None and info.get_icon() is not None:
                return theme.lookup_by_gicon(
                    info.get_icon(), size, scale, Gtk.TextDirection.NONE, flags
                )
    except (GLib.Error, TypeError):
        pass
    return theme.lookup_icon(
        "dialog-information-symbolic", None, size, scale, Gtk.TextDirection.NONE, 0
    )


def _paint_icon(ctx, area, icon, size):
    """Draw the icon `size` px square at the origin of `ctx` (white if it is
    a symbolic one)."""
    from gi.repository import Gdk, Gtk

    paintable = _icon_paintable(area, icon, size)
    if paintable is None:
        return
    snapshot = Gtk.Snapshot()
    if hasattr(paintable, "is_symbolic") and paintable.is_symbolic():
        white = Gdk.RGBA()
        white.red = white.green = white.blue = white.alpha = 1.0
        paintable.snapshot_symbolic(snapshot, size, size, [white, white, white, white])
    else:
        paintable.snapshot(snapshot, size, size)
    node = snapshot.to_node()
    if node is not None:
        node.draw(ctx)


def _draw_ruler(area, ctx, w, h, plan):
    """Numbered coloured bands from the hinge-side edge inwards, every
    plan["band_px"] PHYSICAL px; a legend in the middle of the panel."""
    import math

    from gi.repository import Pango, PangoCairo

    ctx.set_source_rgb(0, 0, 0)
    ctx.paint()
    scale = _scale_of(area) or 1.0
    bands = ambientmodel.ruler_bands(plan)
    standing = plan["edge"] in ("left", "right")
    for number, _name, rgb, a, b in bands:
        x, y, sw, sh = _strip_rect(plan["edge"], a / scale, (b - a) / scale, w, h)
        ctx.set_source_rgb(*rgb)
        ctx.rectangle(x, y, sw, sh)
        ctx.fill()
        # The number along the band, every 90 px, in black.
        layout = PangoCairo.create_layout(ctx)
        font = Pango.FontDescription.from_string("Cantarell Bold")
        thick = sw if standing else sh
        font.set_absolute_size(max(4, int(thick * 0.9)) * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_text(str(number), -1)
        _ink, logical = layout.get_pixel_extents()
        length = h if standing else w
        pos = 20
        while pos < length - 20:
            ctx.save()
            ctx.set_source_rgb(0, 0, 0)
            if standing:
                ctx.translate(x + sw / 2, y + pos)
                ctx.rotate(math.pi / 2)
            else:
                ctx.translate(x + pos, y + sh / 2)
            ctx.translate(-logical.width / 2, -logical.height / 2)
            PangoCairo.show_layout(ctx, layout)
            ctx.restore()
            pos += 90
    # The legend, for whoever reads the screen (not the closed phone).
    lines = ["Glance ruler: bands of %d physical px from the hinge-side edge" % plan["band_px"]]
    for number, name, _rgb, a, b in bands:
        lines.append(
            "%2d  %-8s %3d-%3d px  (%.1f-%.1f mm)" % (number, name, a, b, a / 15.75, b / 15.75)
        )
    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(Pango.FontDescription.from_string("Cantarell 11"))
    layout.set_text("\n".join(lines), -1)
    ctx.set_source_rgb(0.8, 0.8, 0.8)
    ctx.move_to(w * 0.35, h * 0.25)
    PangoCairo.show_layout(ctx, layout)


def sync(shell, state, views_for):
    """Show or hide every panel's ambient view for duo-wc's `state`."""
    conf = ambientmodel.read_conf()
    for name in list(shell.bars):
        plan = ambientmodel.plan(state, name, conf)
        view = views_for(name) if plan is not None else shell.ambient_views.get(name)
        if view is not None:
            view.show_plan(plan, shell.state)
