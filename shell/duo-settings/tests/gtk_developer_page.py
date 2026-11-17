"""The Developer page under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_developer_page.py): built from a made-up duo-devmode status
(developer mode on, SSH on, one key, a WiFi and a USB address), laid out at
one panel's size (538 x 757), its text checked, the switches' and the key
dialog's words to duo-devmode checked; pictures to $OUT (the page, the page
with developer mode off). duo-devmode is a stand-in: nothing runs."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from gtk_battery_page import render, spin, texts  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")
STATUS = {
    "ok": True,
    "developer_mode": True,
    "ssh": True,
    "ssh_active": True,
    "usb_network": True,
    "account": "duo2admin",
    "keys": [
        {
            "type": "ssh-ed25519",
            "fingerprint": "SHA256:" + "A" * 43,
            "comment": "dev@example.org",
        }
    ],
    "addresses": [
        {"iface": "wlan0", "address": "192.0.2.50"},
        {"iface": "usb0", "address": "10.15.19.1"},
    ],
    "touch_log": False,
    "devtools": {"installed": 10, "listed": 10, "available": 10},
    "asks": True,
}


def main():
    from duosettings import developerpage
    from duosettings import main as dmain

    calls = []
    state = {"status": dict(STATUS)}

    def fake_run(argv, done=None):
        words = argv[3:]
        calls.append(words)
        if words == ["status"]:
            reply = state["status"]
        elif words == ["mode", "off"]:
            state["status"] = dict(STATUS, developer_mode=False, ssh=False, usb_network=False)
            reply = {"ok": True}
        else:
            reply = {"ok": True}
        GLib.idle_add(lambda: done(True, json.dumps(reply)) and False)

    def fake_input(argv, text, done=None):
        calls.append(argv[3:] + [text])
        GLib.idle_add(lambda: done(0, json.dumps({"ok": True, "added": [{}]})) and False)

    developerpage.run_async = fake_run
    developerpage.run_with_input = fake_input

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
    if not win.open_tag("developer"):
        failures.append("duo-settings developer does not open the page")
    page = win.current
    spin(1200)
    render(win, os.path.join(OUT, "developer-page.png"))
    t = texts(win)
    for want in (
        "Developer mode",
        "SSH",
        "duo2admin (the administrator, sudo without a password)",
        "ssh duo2admin@192.0.2.50",
        "ssh duo2admin@10.15.19.1",
        "Authorized keys",
        "dev@example.org",
        "USB networking",
        "Log touch positions",
        "For debugging, can reveal what you type",
        "10 of 10 installed",
        "Collect a bug report",
    ):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    if not page.ssh_group.get_sensitive():
        failures.append("the SSH group is off with developer mode on")
    # The key dialog: pasted text goes on stdin, never on the command line.
    d = page.add_key()
    spin(300)
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 test"
    box = d.get_extra_child()
    view = box.get_first_child().get_child()
    if isinstance(view, Gtk.Viewport):
        view = view.get_child()
    view.get_buffer().set_text(key)
    d.emit("response", "ok")
    spin(300)
    if ["key", "add", key] not in calls:
        failures.append("the key did not go to duo-devmode key add: %r" % calls[-3:])
    # The touch log: its own switch, asked of duo-devmode.
    page.touchlog.set_active(True)
    spin(600)
    if ["touchlog", "on"] not in calls:
        failures.append("touchlog on not asked for: %r" % calls[-3:])
    # Switching developer mode off.
    page.mode.set_active(False)
    spin(600)
    if ["mode", "off"] not in calls:
        failures.append("mode off not asked for: %r" % calls[-3:])
    if page.ssh_group.get_sensitive():
        failures.append("the SSH group stays usable with developer mode off")
    if page.debug_group.get_sensitive() or page.touchlog.get_active():
        failures.append("the touch log stays usable or on with developer mode off")
    render(win, os.path.join(OUT, "developer-page-off.png"))
    # The bug report button.
    page.report_button.emit("clicked")
    spin(300)
    if ["bugreport"] not in calls:
        failures.append("no bug report asked for")
    for f in failures:
        print("FAIL:", f)
    print("ok" if not failures else "%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
