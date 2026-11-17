"""The Hotspot page under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_hotspot_page.py): built from a made-up state (two devices, beside
the WiFi connection), laid out at one panel's size (538 x 757), its text
checked, the password shown and forgotten again, "Your own password" (Save
only for a password the helper takes, Enter saves, the password on the
helper's stdin), and the helper's words for the switches; pictures to $OUT
(the page, the password shown, the own-password dialog, its lower half).
The helper is a stand-in: nothing runs."""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)  # gtk_battery_page's helpers (python3 -I adds no script directory)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from gtk_battery_page import render, spin, texts  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")
NOW = time.time()
STATE = {
    "state": "on",
    "client_count": 2,
    "use_5g": True,
    "idle_off_at": None,
    "wifi": {
        "iface": "ap0",
        "band": "5",
        "channel": 36,
        "width": 80,
        "how": "shared",
        "client": "kept",
        "ssid": "Surface Duo 2 0042",
        "security": "wpa2-wpa3",
    },
    "usb": None,
    "uplink": {"iface": "qmapmux0.0", "ipv6": True},
    "clients": [
        {"via": "wifi", "name": "Pixel-7", "ip": "10.15.43.12", "since": NOW - 600},
        {"via": "wifi", "name": None, "ip": "10.15.43.13", "since": NOW - 60},
    ],
    "settings": {
        "ssid": "Surface Duo 2 0042",
        "band": "5",
        "security": "wpa2-wpa3",
        "wifi_client": "keep",
        "idle_off_min": 10,
        "use_5g": True,
        "never_cut_wifi": True,
    },
}
PASSWORD = "abcd-efgh-jkmn"


def main():
    from duosettings import hotspot, hotspotpage
    from duosettings import main as dmain

    calls = []

    def fake_run(argv, done=None):
        words = argv[3:]
        calls.append(words)
        if words == ["password"]:
            reply = {"ok": True, "password": PASSWORD}
        elif words[:1] == ["status"]:
            reply = dict(STATE, ok=True, shared_bytes={"out": 1234567, "in": 34500000})
        else:
            reply = {"ok": True, "state": STATE}
        GLib.idle_add(lambda: done(True, json.dumps(reply)) and False)

    hotspotpage.run_async = fake_run
    stdin_calls = []

    def fake_run_with_input(argv, text, done=None):
        stdin_calls.append((argv[3:], text))
        GLib.idle_add(lambda: done(0, json.dumps({"ok": True})) and False)

    hotspotpage.run_with_input = fake_run_with_input
    hotspot.read_state = lambda path=None: json.loads(json.dumps(STATE))
    hotspotpage.hs.read_state = hotspot.read_state

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
    if not win.open_tag("hotspot"):
        failures.append("duo-settings hotspot does not open the page")
    page = win.current
    spin(1500)
    render(win, os.path.join(OUT, "hotspot-page.png"))
    t = texts(win)
    for want in (
        "WiFi hotspot",
        "On, 5 GHz, channel 36",
        "beside your WiFi connection",
        "Network name",
        "Surface Duo 2 0042",
        "Password",
        "Band",
        "5 GHz",
        "Security",
        "Connected devices",
        "Pixel-7",
        "10.15.43.12 · WiFi · 10 min",
        "Device",
        "USB tethering",
        "If WiFi cannot stay on",
        "Development mode: WiFi is never switched off.",
        "Use 5G for the hotspot",
        "Shared this time",
        "1.2 MB sent, 34.5 MB received",
    ):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    if any(PASSWORD in (s or "") for s in t):
        failures.append("the password is shown before Show")
    if page.client.get_sensitive():
        failures.append("the WiFi-client choice is changeable in development mode")
    # Show: the password; hidden page: forgotten
    page.toggle_password()
    spin(300)
    if page.pw_row.get_subtitle() != PASSWORD:
        failures.append("Show did not show the password: %r" % page.pw_row.get_subtitle())
    render(win, os.path.join(OUT, "hotspot-page-password.png"))
    page.hidden()
    if page.password is not None or PASSWORD in (page.pw_row.get_subtitle() or ""):
        failures.append("the password is kept after the page closed")
    # Your own password: the dialog, Save only for a password the helper
    # takes, the password on the helper's stdin after the grant's empty line
    d = page.own_password()
    spin(600)
    if not d.get_mapped():
        failures.append("Your own password: no dialog")
    if d.get_response_enabled("ok"):
        failures.append("Your own password: Save before a password")
    entry = d.get_extra_child().get_first_child()
    for text, ok, hint in (
        ("short", False, "8 to 63 characters"),
        ("pässwörd12", False, "no accents"),
        ("x" * 64, False, "At most 63"),
        ("my own pass 1", True, "have to join again"),
    ):
        entry.set_text(text)
        spin(50)
        if d.get_response_enabled("ok") != ok or hint not in d.get_body():
            failures.append(
                "Your own password %r: Save %s, %r"
                % (text, d.get_response_enabled("ok"), d.get_body())
            )
    spin(600)
    render(d, os.path.join(OUT, "hotspot-own-password.png"))
    closed = []
    d.connect("closed", lambda _d: closed.append(1))
    entry.emit("entry-activated")  # Enter: as the Save button
    spin(300)
    if not closed:
        failures.append("Your own password: the dialog stays after Enter")
    if stdin_calls != [(["password", "--set"], "\nmy own pass 1\n")]:
        failures.append("Your own password: the helper got %r" % stdin_calls)
    if entry.get_text():
        failures.append("Your own password: the entry keeps the password")
    # the switches' words
    calls.clear()
    page.switch.set_active(False)
    spin(200)
    page.set_band(1)
    spin(200)
    page.set_idle(0)
    spin(200)
    page.set_5g(False)
    spin(200)
    for want in (
        ["off"],
        ["set", "band", "2.4"],
        ["set", "idle-off", "0"],
        ["set", "use-5g", "no"],
    ):
        if want not in calls:
            failures.append("not asked of the helper: %r (%r)" % (want, calls))
    # the lower half (More): scrolled to the end, as a finger would
    content = page.prefs
    while content is not None and not isinstance(content, Gtk.ScrolledWindow):
        content = content.get_first_child()
    if content is not None:
        adj = content.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        spin(300)
        render(win, os.path.join(OUT, "hotspot-page-more.png"))
    for f in failures:
        print("FAIL", f)
    print("hotspot page: %s" % ("ok" if not failures else "%d failures" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
