"""The quick menu's tiles under GTK 4 (tests/run-gtk.sh): the real Tile
widgets and tile_grid with the shell's style.css, laid out at one portrait
panel's width (538 px), every tile showing each of its status lines in turn,
in English (the shell's strings) and in a Hungarian sample (the shell has no
Hungarian catalogue yet: these are the likely translations, the long ones on
purpose). Checked: no line is cut short (ellipsized), none takes more than
two lines, every tile is at least 48 px tall and the grid fits the panel.
Pictures to $OUT: each language with every tile at its longest status.
Exit code 0 when everything holds.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")
WIDTH = 538

# (icon, English title, its status lines), in the quick menu's order.
EN = [
    (
        "network-wireless-symbolic",
        "WiFi",
        ["No WiFi device", "Off (hotspot on)", "Off", "Connected", "Not connected"],
    ),
    (
        "network-cellular-symbolic",
        "Mobile data",
        [
            "No SIM",
            "SIM locked",
            "Off",
            "On",
            "Connected",
            "Waiting for the network",
            "Airplane mode",
        ],
    ),
    (
        "network-wireless-hotspot-symbolic",
        "Hotspot",
        [
            "Not installed",
            "Airplane mode",
            "Off",
            "On",
            "No mobile data",
            "12 devices",
            "Starting…",
        ],
    ),
    ("bluetooth-active-symbolic", "Bluetooth", ["No adapter", "On", "Off"]),
    ("airplane-mode-symbolic", "Airplane mode", ["On", "Off"]),
    ("location-services-active-symbolic", "Location", ["No geoclue", "In use", "On", "Off"]),
    (
        "audio-volume-high-symbolic",
        "Sound",
        ["No feedbackd", "Sound and vibration", "Sound only", "Vibration only", "Silent"],
    ),
    ("notifications-disabled-symbolic", "Do not disturb", ["Not available", "On", "Off"]),
    ("duo-torch-off-symbolic", "Flashlight", ["No flashlight", "No access", "On", "Off"]),
    (
        "rotation-allowed-symbolic",
        "Auto-rotate",
        ["On", "Sensors only log", "Locked", "No duo-posed"],
    ),
    ("input-keyboard-symbolic", "Keyboard", ["Shown", "Hidden"]),
    ("view-dual-symbolic", "Hinge gap", ["On", "Off"]),
    ("video-display-symbolic", "Panels", ["Both", "Left only", "Right only", "No duo-posed"]),
    ("duo-laptop-symbolic", "Laptop mode", ["Not available", "Needs both panels", "On", "Off"]),
    ("focus-windows-symbolic", "Windows", ["Overview"]),
]

HU = [
    ("WiFi", ["Nincs WiFi-eszköz", "Ki (a hotspot be)", "Ki", "Csatlakozva", "Nincs csatlakozva"]),
    (
        "Mobilinternet",
        [
            "Nincs SIM",
            "SIM zárolva",
            "Ki",
            "Be",
            "Csatlakozva",
            "Várakozás a hálózatra",
            "Repülőgép-üzemmód",
        ],
    ),
    (
        "Hotspot",
        [
            "Nincs telepítve",
            "Repülőgép-üzemmód",
            "Ki",
            "Be",
            "Nincs mobilinternet",
            "12 eszköz",
            "Indul…",
        ],
    ),
    ("Bluetooth", ["Nincs adapter", "Be", "Ki"]),
    ("Repülőgép-üzemmód", ["Be", "Ki"]),
    ("Helymeghatározás", ["Nincs geoclue", "Használatban", "Be", "Ki"]),
    ("Hang", ["Nincs feedbackd", "Hang és rezgés", "Csak hang", "Csak rezgés", "Néma"]),
    ("Ne zavarjanak", ["Nem érhető el", "Be", "Ki"]),
    ("Elemlámpa", ["Nincs elemlámpa", "Nincs hozzáférés", "Be", "Ki"]),
    ("Automatikus forgatás", ["Be", "Az érzékelők csak naplóznak", "Zárolva", "Nincs duo-posed"]),
    ("Billentyűzet", ["Látható", "Rejtett"]),
    ("Zsanérrés", ["Be", "Ki"]),
    ("Panelek", ["Mindkettő", "Csak a bal", "Csak a jobb", "Nincs duo-posed"]),
    ("Laptop mód", ["Nem érhető el", "Mindkét panel kell", "Be", "Ki"]),
    ("Ablakok", ["Áttekintés"]),
]


def spin(ms):
    end = time.monotonic() + ms / 1000.0
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)


def settle(tiles, limit_ms=5000):
    """Spin until every shown line has a width, the same twice in a row
    (broadway lays out some frames after a change)."""
    last = None
    end = time.monotonic() + limit_ms / 1000.0
    while time.monotonic() < end:
        spin(100)
        now = [(t.title.get_width(), t.sub.get_width(), t.get_height()) for t in tiles]
        if now == last and all(
            tw > 0 and (sw > 0 or not t.sub.get_visible()) for (tw, sw, _h), t in zip(now, tiles)
        ):
            return True
        last = now
    return False


def render(widget, path):
    # the first snapshot after a relayout can come out empty under broadway
    Gtk.WidgetPaintable.new(widget).snapshot(
        Gtk.Snapshot(), widget.get_width(), widget.get_height()
    )
    spin(300)
    for _ in range(20):
        w, h = widget.get_width(), widget.get_height()
        snap = Gtk.Snapshot()
        bg = Gdk.RGBA()
        bg.parse("#0b0d11")
        snap.append_color(bg, Graphene.Rect().init(0, 0, w, h))
        Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            break
        spin(100)
    r = Gsk.CairoRenderer()
    r.realize_for_display(Gdk.Display.get_default())
    r.render_texture(node, None).save_to_png(path)
    r.unrealize()


def shown_layout(label):
    """The label's text laid out the way GTK shows it: at its allocated
    width, wrapping and ellipsizing as the label does (its own layout is
    also used for measuring, so its width at any moment proves nothing)."""
    layout = label.create_pango_layout(label.get_label())
    layout.set_width(label.get_width() * Pango.SCALE)
    layout.set_wrap(label.get_wrap_mode())
    layout.set_ellipsize(label.get_ellipsize())
    layout.set_height(-label.get_lines())
    return layout


def label_problems(label, what):
    layout = shown_layout(label)
    out = []
    if layout.is_ellipsized():
        out.append(
            "%s: cut short (%r; %d px for %d px)"
            % (
                what,
                label.get_label(),
                label.get_width(),
                label.create_pango_layout(label.get_label()).get_pixel_size()[0],
            )
        )
    if layout.get_line_count() > 2:
        out.append("%s: %d lines (%r)" % (what, layout.get_line_count(), label.get_label()))
    return out


def main():
    from duoshell import quickmenu, torchmodel

    Gtk.init()
    display = Gdk.Display.get_default()
    css = Gtk.CssProvider()
    css.load_from_path(os.path.join(os.path.dirname(HERE), "duoshell", "style.css"))
    Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(os.path.join(os.path.dirname(HERE), "icons"))
    failures = []
    from duoshell import laptopmodel

    for name in (torchmodel.ICON_ON, torchmodel.ICON_OFF, laptopmodel.ICON):
        icon = theme.lookup_icon(name, None, 22, 1, Gtk.TextDirection.NONE, 0)
        if icon.get_file() is None or not icon.is_symbolic():
            failures.append("icon %s not found as a symbolic icon" % name)
    if quickmenu.tile_cells(14) != [(i % 3, i // 3) for i in range(14)]:
        failures.append("tile_cells: %r" % quickmenu.tile_cells(14))

    win = Gtk.Window()
    win.add_css_class("duo-quick")
    win.set_default_size(WIDTH, 700)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    content.add_css_class("content")
    tiles = [quickmenu.Tile(icon, title, lambda: None) for icon, title, _subs in EN]
    grid = quickmenu.tile_grid(tiles)
    content.append(grid)
    win.set_child(content)
    win.present()
    spin(600)

    for lang, table in (("en", [(t, s) for _i, t, s in EN]), ("hu", HU)):
        longest = 0
        wrapped = []
        while True:
            more = False
            for tile, (title, subs) in zip(tiles, table):
                tile.title.set_label(title)
                sub = subs[min(longest, len(subs) - 1)]
                more = more or longest < len(subs) - 1
                tile.show(longest % 2 == 0, sub)
            if not settle(tiles):
                failures.append("%s: the tiles never settled (pass %d)" % (lang, longest))
            for tile, (title, _subs) in zip(tiles, table):
                for label, what in ((tile.title, "title"), (tile.sub, "status")):
                    problems = label_problems(label, "%s %s %s" % (lang, title, what))
                    failures.extend(p for p in problems if p not in failures)
                    if shown_layout(label).get_line_count() == 2:
                        w = "%s: %s" % (what, label.get_label())
                        if w not in wrapped:
                            wrapped.append(w)
                if tile.get_height() < 48:
                    failures.append("%s %s: %d px tall" % (lang, title, tile.get_height()))
            if grid.get_width() > WIDTH - 32:
                failures.append("%s: the grid is %d px wide" % (lang, grid.get_width()))
            if not more:
                break
            longest += 1
        # every tile at its longest line, for the picture
        for tile, (title, subs) in zip(tiles, table):
            tile.show(False, max(subs, key=len))
        tiles[8].show(True, table[8][1][2], torchmodel.ICON_ON)
        spin(400)
        print(
            "%s: tile %d x %d px, grid %d x %d px; two lines: %s"
            % (
                lang,
                tiles[0].get_width(),
                tiles[0].get_height(),
                grid.get_width(),
                grid.get_height(),
                "; ".join(wrapped) or "none",
            )
        )
        render(win, os.path.join(OUT, "quick-tiles-%s.png" % lang))

    for f in failures:
        print("FAIL", f)
    print("OK" if not failures else "%d failures" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
