"""The Android apps page under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_android_page.py): laid out at one panel's size (538 x 757), its
text checked; the switch writes KEEP_RUNNING into a made-up home's
~/.config/duo2/waydroid.conf and reads it back; the state row follows a
made-up container cgroup (stopped, paused, running) and switching on while
paused asks Waydroid's container manager to unfreeze (a stand-in: no D-Bus
call is made). Pictures to $OUT."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)  # gtk_battery_page's helpers (python3 -I adds no script directory)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402
from gtk_battery_page import render, spin, texts  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")


def main():
    home = tempfile.mkdtemp()
    os.environ["HOME"] = home
    cg = os.path.join(home, "cgroup", "lxc.payload.waydroid")
    from duosettings import android, androidpage
    from duosettings import main as dmain

    android.CGROUP = cg
    real_state = android.state
    android.state = lambda cgroup=None: real_state(cg)
    android.SYSTEM_CONF = os.path.join(home, "none")
    unfreezes = []
    androidpage.AndroidPage.unfreeze = lambda self: unfreezes.append(1)

    app = Adw.Application(
        application_id="org.duo2os.SettingsTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    failures = []
    st = {}

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
    if not win.open_tag("android"):
        failures.append("duo-settings android does not open the page")
    page = win.current
    spin(800)
    render(win, os.path.join(OUT, "android-page.png"))
    t = texts(win)
    for want in (
        "Android apps",
        "In the background",
        "Keep Android apps running in the background",
        "For notifications from messengers",
        "uses no battery",
        "Android now",
        "Not running",
        "After a restart",
    ):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    if page.keep.get_active():
        failures.append("the switch is on by default")
    path = os.path.join(home, ".config", "duo2", "waydroid.conf")
    # paused by Waydroid, then switched on: written, and an unfreeze asked
    os.makedirs(cg)
    with open(os.path.join(cg, "cgroup.events"), "w") as f:
        f.write("populated 1\nfrozen 1\n")
    page.show_state()
    if page.now.get_subtitle() != "Paused: no notifications":
        failures.append("paused state: %r" % page.now.get_subtitle())
    page.keep.set_active(True)
    spin(200)
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        text = ""
        failures.append("not written: %s" % e)
    if "KEEP_RUNNING = yes" not in text:
        failures.append("switch on did not write KEEP_RUNNING = yes: %r" % text)
    if unfreezes != [1]:
        failures.append("no unfreeze asked while paused: %r" % unfreezes)
    with open(os.path.join(cg, "cgroup.events"), "w") as f:
        f.write("populated 1\nfrozen 0\n")
    page.show_state()
    if page.now.get_subtitle() != "Running":
        failures.append("running state: %r" % page.now.get_subtitle())
    render(win, os.path.join(OUT, "android-page-on.png"))
    # the page again: the saved state; off writes no
    page.hidden()
    page.shown()
    if not page.keep.get_active():
        failures.append("the switch does not show the saved state")
    page.keep.set_active(False)
    spin(200)
    if android.keep_running(android.SYSTEM_CONF, path) is not False:
        failures.append("switch off did not write no")
    if len(unfreezes) != 1:
        failures.append("an unfreeze asked when switching off")
    page.hidden()
    for f in failures:
        print("FAIL", f)
    print("android page: %s" % ("ok" if not failures else "%d failures" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
