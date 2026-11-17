"""The Lock screen page's data encryption under GTK 4 + libadwaita
(tests/run-gtk.sh tests/gtk_lock_page.py): a PIN set, the home plain; the
switch asks first (the warnings, the recovery key's check box), then the
PIN, and hands it to `duo-lock-helper home enable recovery` on stdin; the
recovery key is shown once; a restart is offered. Then the encrypted
state: removing the PIN is refused with an explanation, the change PIN
dialog takes the recovery key. Pictures to $OUT. duo-lock-helper is a
stand-in: nothing runs."""

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
KEY = "123456-234567-345678-456789-567890-678901"


def dialog_of(win):
    d = win.get_visible_dialog()
    spin(200)
    return d


def main():
    from duosettings import main as dmain
    from duosettings import pages, system

    calls = []
    home = {"state": "plain", "progress": 0, "detail": "", "recovery": "no", "supported": True}
    system.lock_policy = lambda path=None: {
        "pin": "yes",
        "lock_on_blank": "on",
        "lock_on_close": "on",
    }
    system.home_state = lambda *a, **k: dict(home)

    def fake_input(argv, text, done=None):
        calls.append((argv[3:], text))
        out = ""
        if argv[3:5] == ["home", "enable"]:
            home.update(state="encrypt-pending", recovery="yes")
            out = "recovery %s\n" % KEY
        GLib.idle_add(lambda: done(0, out) and False)

    def fake_run(argv, done=None):
        calls.append((argv[3:], None))
        if done:
            GLib.idle_add(lambda: done(True, "") and False)

    pages.run_with_input = fake_input
    pages.run_async = fake_run

    app = Adw.Application(
        application_id="org.duo2os.SettingsLockTest", flags=Gio.ApplicationFlags.NON_UNIQUE
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
    if not win.open_tag("lock"):
        failures.append("duo-settings lock does not open the page")
    page = win.current
    spin(800)
    t = texts(win)
    for want in ("Data encryption", "Encrypt my data", "Off"):
        if not any(want == (s or "") for s in t):
            failures.append("missing: " + want)
    if page.enc_pending.get_visible() or page.recovery_row.get_visible():
        failures.append("the pending / recovery rows show with the home plain")
    render(win, os.path.join(OUT, "lock-page.png"))

    # The switch: the warnings first, the switch stays off until it is done.
    page.enc.set_active(True)
    spin(400)
    d = dialog_of(win)
    if d is None or "Encrypt your data" not in (d.get_heading() or ""):
        failures.append("no question before encrypting")
    else:
        body = d.get_body()
        for want in ("forget the PIN", "recovery key", "free space", "8 digits"):
            if want not in body:
                failures.append("the question does not say: " + want)
        spin(400)
        render(win, os.path.join(OUT, "lock-encrypt-ask.png"))
        if page.enc.get_active():
            failures.append("the switch went on before anything was done")
        d.emit("response", "ok")
        spin(400)
    d = dialog_of(win)
    if d is None or "PIN" not in (d.get_heading() or ""):
        failures.append("no PIN dialog")
    else:
        entry = d.get_extra_child().get_first_child()
        entry.set_text("2468")
        spin(100)
        d.emit("response", "ok")
        spin(600)
    if (["home", "enable", "recovery"], "2468\n") not in calls:
        failures.append("the PIN did not go to home enable on stdin: %r" % calls[-2:])
    d = dialog_of(win)
    if d is None or "recovery key" not in (d.get_heading() or "").lower():
        failures.append("the recovery key is not shown")
    else:
        shown = d.get_extra_child().get_label()
        if "123456 234567 345678" not in shown or "678901" not in shown:
            failures.append("the recovery key reads %r" % shown)
        spin(400)
        render(win, os.path.join(OUT, "lock-recovery-key.png"))
        d.emit("response", "ok")
        spin(400)
    if (["wc-conf", "session_lock", "on"], None) not in calls:
        failures.append("duo-wc's lock was not switched on: %r" % calls)
    d = dialog_of(win)
    if d is None or "Restart" not in (d.get_heading() or ""):
        failures.append("no restart offered")
    else:
        d.emit("response", "cancel")
        spin(300)
    page.shown()
    spin(300)
    if not page.enc_pending.get_visible() or not page.enc.get_active():
        failures.append("the waiting change is not shown")
    render(win, os.path.join(OUT, "lock-page-pending.png"))

    # Encrypted: the PIN cannot be removed, a change takes the recovery key.
    home.update(state="encrypted", progress=100)
    page.shown()
    spin(300)
    page.remove_pin()
    spin(300)
    d = dialog_of(win)
    if d is None or "protects your data" not in (d.get_heading() or ""):
        failures.append("removing the PIN is not explained")
    else:
        d.emit("response", "ok")
        spin(300)
    page.change_pin()
    spin(300)
    d = dialog_of(win)
    if d is None:
        failures.append("no change PIN dialog")
    else:
        box = d.get_extra_child()
        rows = []
        child = box.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        rows[0].set_text(KEY.replace("-", ""))
        rows[1].set_text("97531")
        rows[2].set_text("97531")
        spin(100)
        if not d.get_response_enabled("ok"):
            failures.append("the recovery key is not taken as the current PIN")
        d.emit("response", "cancel")
        spin(200)
    for f in failures:
        print("FAIL:", f)
    print("ok" if not failures else "%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
