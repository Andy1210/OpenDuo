"""Settings > About under GTK 4 + libadwaita (tests/run-gtk.sh), built from
MADE-UP values only (DUO_SETTINGS_FAKE_ABOUT=1: no real identifier is read,
drawn or saved), at one panel's size (538 x 757): the four sub-pages, the
values not selectable, a long press copying the value (clipboard + toast),
back navigation leaving no row lit. Pictures go to $OUT."""

import os
import sys

os.environ["DUO_SETTINGS_FAKE_ABOUT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
from gtk_battery_page import OUT, render, spin, texts  # noqa: E402


def walk(widget, kind, out=None):
    out = [] if out is None else out
    if isinstance(widget, kind):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        walk(child, kind, out)
        child = child.get_next_sibling()
    return out


def main():
    from duosettings import main as dmain

    app = Adw.Application(
        application_id="org.duo2os.SettingsTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    state = {}

    def activate(a):
        css = Gtk.CssProvider()
        css.load_from_string(dmain.CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = dmain.Window(a)
        win.set_default_size(538, 757)
        win.present()
        state["win"] = win

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(800)
    win = state["win"]
    failures = []
    page = next(p for p in win.pages if p.get_tag() == "AboutPage")
    win.open(page)
    spin(800)
    render(win, os.path.join(OUT, "about-root.png"))
    t = texts(win)
    for want in ("Device name", "Status", "Software information", "Hardware information", "Legal"):
        if not any(want in (s or "") for s in t):
            failures.append("root missing: " + want)
    wants = {
        "Status": (
            "SIM 1",
            "Example Mobile",
            "IMEI",
            "000000000000000",
            "Serial number",
            "Up time",
        ),
        "Software information": (
            "OpenDuo version",
            "Kernel version",
            "Modem firmware",
            "Last security update",
            "Graphics driver",
        ),
        "Hardware information": (
            "Processor",
            "CPU cores",
            "Design capacity",
            "Panels",
            "Camera sensors",
        ),
        "Legal": ("Open-source licences", "Where the licences are"),
    }
    for title, sub, sp in page.subpages:
        page.nav.push(sp)
        spin(1200)
        render(win, os.path.join(OUT, "about-%s.png" % title.split()[0].lower()))
        t = texts(win)
        for want in wants[title]:
            if not any(want in (s or "") for s in t):
                failures.append("%s missing: %s" % (title, want))
        # nothing selectable: a tap must not show a cursor
        for lab in walk(sp, Gtk.Label):
            if lab.get_selectable():
                failures.append("%s: a selectable label (%r)" % (title, lab.get_label()[:20]))
        rows = [r for r in walk(sp, Adw.ActionRow) if getattr(r, "_value", None)]
        if title == "Status" and rows:
            # long press copies the value and says so
            seen = []
            win.toast = lambda text, _s=seen: _s.append(text)
            from gi.repository import Gtk as G

            press = next(
                c for c in rows[0].observe_controllers() if isinstance(c, G.GestureLongPress)
            )
            press.emit("pressed", 5.0, 5.0)
            spin(300)
            clip = []
            rows[0].get_clipboard().read_text_async(
                None, lambda c, r: clip.append(c.read_text_finish(r))
            )
            spin(500)
            if seen != ["Copied"]:
                failures.append("no toast after a long press: %r" % seen)
            if clip != [rows[0]._value]:
                failures.append("the clipboard has %r, wanted the value" % clip)
        if title == "Legal":
            sp.licences.fill(
                sp.licences.items or __import__("duosettings.about", fromlist=["x"]).fake_licences()
            )
            page.nav.push(sp.licences)
            spin(1200)
            render(win, os.path.join(OUT, "about-licences.png"))
            if not any("example-lib" in (s or "") for s in texts(win)):
                failures.append("licence list empty")
            sp.licences.search.set_text("gpl")
            spin(300)
            page.nav.pop()
            spin(600)
        page.nav.pop()
        spin(700)
    # back to the list of pages (collapsed): no row selected or lit
    if win.split.get_collapsed():
        win.split.set_show_content(False)
        spin(800)
        if win.list.get_selected_row() is not None:
            failures.append("a row is still selected after going back")
        render(win, os.path.join(OUT, "about-back.png"))
        if win.get_focus() is not None:
            failures.append("a widget keeps the focus after going back: %r" % win.get_focus())
    else:
        failures.append("the split is not collapsed at 538 px")
    for f in failures:
        print("FAIL", f)
    print("ok" if not failures else "%d failures" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
