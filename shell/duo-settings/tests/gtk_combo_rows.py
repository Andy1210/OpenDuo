"""Every page's combo rows under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_combo_rows.py): each page opened in a window as wide as the ones
Settings gets on the phone, with the phone's font (Cantarell 11) and GTK's
default one; the value beside a combo row's title must never be cut short
(a value of up to main.VALUE_CHARS characters), and no page may make the
window wider than the panel. Pictures to $OUT: the Hotspot page at 538 px
(the Band row that read "5…") and the Pen page at 448 px.

The widths: one panel upright at the five scales (1344 px / 2, 2.25, 2.5,
2.75, 3: 672, 597, 538, 489, 448 logical px; from 640 px up the list and the
page sit side by side), one panel on its side at 300 % and 250 % (631, 757),
and wide (broadway's screen: 1024)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)  # gtk_battery_page's helpers (python3 -I adds no script directory)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
from gtk_battery_page import render, spin  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")
WIDTHS = (448, 489, 538, 597, 631, 672, 757, 1024)
FONTS = ("Cantarell 11", "Sans 10")  # the phone's (gsettings via the portal); GTK's default
PICTURES = {("HotspotPage", 538): "combo-hotspot-538.png", ("PenPage", 448): "combo-pen-448.png"}


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def cut_values(page, limit):
    """(row title, value) of every shown combo row whose value is cut
    short, or not shown at all (value None)."""
    out = []
    for row in walk(page):
        if not (isinstance(row, Adw.ComboRow) and row.get_mapped()):
            continue
        if row.get_selected_item() is None:
            continue  # nothing to show (an empty list: no sound devices here)
        shown = False
        for label in walk(row):
            if (
                isinstance(label, Gtk.Label)
                and label.get_mapped()
                and label.get_width() > 0  # (a list item not laid out yet)
                and not label.has_css_class("title")
                and not label.has_css_class("subtitle")
            ):
                if len(label.get_label()) <= limit and label.get_layout().is_ellipsized():
                    out.append((row.get_title(), label.get_label()))
                shown = True
        if not shown:
            out.append((row.get_title(), None))
    return out


def settle(page, limit_ms=4000):
    """Until every shown combo row is laid out (broadway takes up to a
    second after a page opens)."""
    for _ in range(limit_ms // 100):
        spin(100)
        rows = [w for w in walk(page) if isinstance(w, Adw.ComboRow) and w.get_mapped()]
        if all(r.get_width() > 0 for r in rows) and all(
            label.get_width() > 0
            for r in rows
            for label in walk(r)
            if isinstance(label, Gtk.Label) and label.has_css_class("title")
        ):
            spin(100)
            return


def main():
    from duosettings import main as dmain

    app = Adw.Application(
        application_id="org.duo2os.SettingsComboTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    st = {}

    def activate(a):
        css = Gtk.CssProvider()
        css.load_from_string(dmain.CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        st["app"] = a

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(300)
    failures = []
    rows = 0
    for font in FONTS:
        Gtk.Settings.get_default().set_property("gtk-font-name", font)
        for width in WIDTHS:
            win = dmain.Window(st["app"])
            win.set_default_size(width, 709)
            win.present()
            spin(500)
            for page in win.pages:
                try:
                    win.open(page)
                except Exception as e:  # noqa: BLE001  (a page whose service is not here)
                    print("skipped %s: %s" % (page.get_tag(), e))
                    continue
                settle(page)
                rows += sum(1 for w in walk(page) if isinstance(w, Adw.ComboRow) and w.get_mapped())
                for title, value in cut_values(page, dmain.VALUE_CHARS):
                    failures.append(
                        "%s, %d px, %s: %r %s"
                        % (
                            font,
                            width,
                            page.get_tag(),
                            title,
                            "shows no value" if value is None else "reads cut short (%r)" % value,
                        )
                    )
                if win.get_width() > width:
                    failures.append(
                        "%s, %d px, %s: the window grew to %d px"
                        % (font, width, page.get_tag(), win.get_width())
                    )
                name = PICTURES.get((page.get_tag(), width))
                if name and font == FONTS[0]:
                    render(win, os.path.join(OUT, name))
            win.destroy()
            spin(150)
    for f in failures:
        print("FAIL", f)
    print(
        "combo rows: %d looked at, %s"
        % (rows, "ok" if not failures else "%d failures" % len(failures))
    )
    return 1 if failures or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
