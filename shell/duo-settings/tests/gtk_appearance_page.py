"""The Appearance page under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_appearance_page.py): laid out at one panel's size, its text
checked, the choices sent to duo-appearance (a stand-in: nothing is applied),
the two times shown only for Automatic. Pictures to $OUT."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
from gtk_battery_page import render, spin, texts  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")


def main():
    os.environ["HOME"] = tempfile.mkdtemp()
    from duosettings import appearancepage
    from duosettings import main as dmain

    calls = []
    appearancepage.run_async = lambda argv, done=None: calls.append(argv)
    app = Adw.Application(
        application_id="org.duo2os.SettingsTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    failures, st = [], {}

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
        st["win"] = win

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(800)
    win = st["win"]
    if not win.open_tag("appearance"):
        failures.append("duo-settings appearance does not open the page")
    page = win.current
    spin(600)
    t = texts(win)
    for want in ("Appearance", "Style", "Dark", "stay dark", "Android apps"):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    if page.light.get_visible() or page.dark.get_visible():
        failures.append("the times show for Dark")
    render(win, os.path.join(OUT, "appearance-dark.png"))
    page.style.set_selected(2)
    spin(300)
    if not (page.light.get_visible() and page.dark.get_visible()):
        failures.append("the times do not show for Automatic")
    if not calls or calls[-1] != [
        "duo-appearance",
        "set",
        "auto",
        "--light-from",
        "07:00",
        "--dark-from",
        "19:00",
    ]:
        failures.append("Automatic sent %r" % (calls[-1:],))
    page.dark.set_selected(4)  # 20:00
    spin(200)
    if calls[-1][-1] != "20:00":
        failures.append("Dark from sent %r" % calls[-1])
    render(win, os.path.join(OUT, "appearance-auto.png"))
    page.style.set_selected(1)
    spin(200)
    if calls[-1][2] != "light" or page.light.get_visible():
        failures.append("Light: %r, times visible %s" % (calls[-1], page.light.get_visible()))
    for f in failures:
        print("FAIL", f)
    print("appearance page: %s" % ("ok" if not failures else "%d failures" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
