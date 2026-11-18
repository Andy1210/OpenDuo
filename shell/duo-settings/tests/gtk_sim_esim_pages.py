"""The SIM card page's "SIM in use" and the eSIM page under GTK 4 +
libadwaita (tests/run-gtk.sh tests/gtk_sim_esim_pages.py), with stand-in
helpers (nothing runs): the SIM page shows the tray in use and switches to
the eSIM on a tap (the helper's `sim use 2`, then the slots read again);
the eSIM page marks the factory test profiles, offers the three ways to a
code (the QR rows only where GStreamer's parts are there), follows a
download's progress lines in its dialog and, once done, offers the new
profile and then the switch to the eSIM. Pictures to $OUT."""

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
TRAY = {"slot": 1, "card": True, "esim": False, "active": True, "logical": 1, "in_use": True}
ESIM = {"slot": 2, "card": True, "esim": True, "active": True, "logical": 2, "in_use": False}
PROFILES = [
    {
        "iccid": "89000000000000000060",
        "name": "Network Simulator - Milenage",
        "nickname": "",
        "provider": "Microsoft",
        "state": "disabled",
        "class": "test",
    },
    {
        "iccid": "89000000000000000056",
        "name": "Operator",
        "nickname": "",
        "provider": "Operator",
        "state": "disabled",
        "class": "operational",
    },
]


def main():
    from duosettings import esimpage, pages
    from duosettings import main as dmain

    state = {"esim_in_use": False}
    calls = []

    def slots():
        if state["esim_in_use"]:
            return [
                dict(TRAY, active=False, logical=None, in_use=False),
                dict(ESIM, logical=1, in_use=True),
            ]
        return [TRAY, ESIM]

    def fake_run(argv, done=None):
        words = argv[3:]
        calls.append(words)
        if words == ["sim", "slots"]:
            out = json.dumps(slots())
        elif words == ["sim", "use", "2"]:
            state["esim_in_use"] = True
            out = json.dumps({"ok": True, "message": "slot 2 is in use now"})
        elif words == ["info"]:
            out = json.dumps({"ok": True, "eid": "89049032000000000000000000000042"})
        elif words == ["list"]:
            out = json.dumps({"ok": True, "profiles": PROFILES})
        else:
            out = json.dumps({"ok": True})
        if done is not None:
            GLib.idle_add(lambda: done(True, out) and False)

    pages.run_async = fake_run
    esimpage.run_async = fake_run
    progress = []

    def fake_lines(argv, on_line, done):
        calls.append(argv[3:])
        steps = [
            "confirmed",
            "es9p_initiate_authentication",
            "es9p_get_bound_profile_package",
            "es10b_load_bound_profile_package",
            "checking",
        ]
        lines = [json.dumps({"progress": s}) for s in steps]
        lines.append(json.dumps({"ok": True, "profile": PROFILES[1]}))

        def feed():
            line = lines.pop(0)
            on_line(line)
            progress.append(line)
            if not lines:
                done(line)
                return False
            return True

        GLib.timeout_add(150, feed)

    esimpage.run_lines = fake_lines

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

    # -- SIM card page ----------------------------------------------------------------
    if not win.open_tag("sim"):
        failures.append("the SIM card page does not open")
    page = win.current
    spin(800)
    render(win, os.path.join(OUT, "sim-in-use.png"))
    t = texts(win)
    for want in ("SIM in use", "SIM card in the tray", "eSIM", "In use", "Off"):
        if want not in t:
            failures.append("SIM page: %r missing" % want)
    page.slot_checks["2"].set_active(True)
    spin(800)
    if ["sim", "use", "2"] not in calls:
        failures.append("a tap on the eSIM did not ask the helper to switch")
    if (
        page.slot_rows["2"].get_subtitle() != "In use"
        or page.slot_rows["1"].get_subtitle() != "Off"
    ):
        failures.append(
            "after the switch: %r / %r"
            % (page.slot_rows["1"].get_subtitle(), page.slot_rows["2"].get_subtitle())
        )
    render(win, os.path.join(OUT, "sim-switched.png"))

    # -- eSIM page ----------------------------------------------------------------------
    state["esim_in_use"] = False
    if not win.open_tag("esim"):
        failures.append("the eSIM page does not open")
    page = win.current
    spin(800)
    render(win, os.path.join(OUT, "esim-page.png"))
    t = texts(win)
    for want in ("Factory test profile", "Add a profile", "Type the code"):
        if not any(want in x for x in t):
            failures.append("eSIM page: %r missing" % want)
    page.add("lpa:1$rsp.example.com$ABC", "")
    spin(450)
    render(win, os.path.join(OUT, "esim-progress.png"))
    spin(1500)
    if len(progress) != 6:
        failures.append("the progress dialog saw %d lines" % len(progress))
    t = texts(win)
    if not any("is on the phone" in x for x in t):
        failures.append("no question to use the new profile: %r" % t[-6:])
    render(win, os.path.join(OUT, "esim-added.png"))
    if ["add", "LPA:1$rsp.example.com$ABC"] not in calls:
        failures.append("the code was not sent as normalized: %r" % calls[-3:])
    # "Use it now" -> enable -> the slots -> the switch offer
    page.enable(PROFILES[1]["iccid"])
    spin(800)
    t = texts(win)
    if not any("Use the eSIM now?" in x for x in t):
        failures.append("no offer to switch to the eSIM after switching the profile on")
    render(win, os.path.join(OUT, "esim-switch-offer.png"))

    print("\n".join(failures) if failures else "all good")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
