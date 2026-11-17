"""The Updates page under GTK 4 + libadwaita (tests/run-gtk.sh
tests/gtk_updates_page.py): laid out at one panel's size (538 x 757) against
a stand-in for duo2-updates (no sudo, no apt): never checked, updates found
(OpenDuo's, Debian's, the changes), Install started and running, the restart
asked for, the battery too low, the testing channel only in developer mode,
the settings' switches sent to the helper. Pictures to $OUT."""

import copy
import json
import os
import sys
import time

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
NOW = time.time()

BASE = {
    "ok": True,
    "conf": {
        "auto_install": "off",
        "notify": "on",
        "channel": "stable",
        "check_on_mobile_data": "off",
    },
    "developer_mode": False,
    "installed": "0.1.0-dev",
    "job": None,
    "last": None,
    "reboot_required": False,
    "reboot_packages": [],
    "battery": 80,
    "on_charger": False,
    "may_install": None,
    "kernel": {
        "running": {"release": "7.3.4-duo2-g3ba7db140", "entry": None, "state": "known-good"},
        "kernels": [],
    },
}
FOUND = {
    "time": NOW - 600,
    "ours": [
        {
            "name": "openduo-duo-wc",
            "old": "0.1.0~dev+20261116233800.g0e1a4ac",
            "new": "0.1.0~dev+20261117120000.gabcdef1",
            "security": False,
        },
        {
            "name": "openduo-kernel-surface-duo2-7.3.5-duo2-gaaaa111",
            "old": None,
            "new": "7.3.5+20261117100000.gaaaa111",
            "security": False,
        },
    ],
    "debian": [
        {"name": "openssl", "old": "3.5.1-1", "new": "3.5.1-1+deb13u1", "security": True},
        {"name": "curl", "old": "8.14.1-2", "new": "8.14.1-2+deb13u1", "security": False},
    ],
    "removed": [],
    "refuse": [],
    "changes": [
        {
            "version": "Unreleased",
            "date": "",
            "text": "### Fixed\n- The dock on both panels\n\n### Added\n- System updates from Settings",
        }
    ],
    "count": 4,
}


class Helper:
    """duo2-updates, made up: answers the page's commands from `state`."""

    def __init__(self):
        self.state = copy.deepcopy(BASE)
        self.calls = []

    def __call__(self, page, args, done=None):
        self.calls.append(list(args))
        if args == ["status"]:
            reply = copy.deepcopy(self.state)
        elif args == ["check"]:
            self.state["job"] = {"job": "check", "state": "running", "step": "apt-get update"}
            reply = {"ok": True, "job": "check"}
        elif args == ["install"]:
            if self.state["may_install"]:
                reply = {"ok": False, "code": "power", "error": self.state["may_install"]}
            else:
                self.state["job"] = {"job": "install", "state": "running", "step": "snapshot"}
                reply = {"ok": True, "job": "install"}
        elif args[:1] == ["set"]:
            key, value = args[1], args[2]
            if key == "channel" and value == "testing" and not self.state["developer_mode"]:
                reply = {
                    "ok": False,
                    "code": "value",
                    "error": "the testing channel needs developer mode",
                }
            else:
                field = {
                    "auto": "auto_install",
                    "notify": "notify",
                    "mobile-data": "check_on_mobile_data",
                    "channel": "channel",
                }[key]
                self.state["conf"][field] = value
                reply = {"ok": True, "conf": self.state["conf"]}
        else:
            reply = {"ok": False, "error": "unknown"}
        if done:
            done(json.loads(json.dumps(reply)))


def main():
    from duosettings import main as dmain
    from duosettings import updatespage

    helper = Helper()
    updatespage.UpdatesPage.helper = lambda self, args, done=None: helper(self, args, done)
    informed = []
    updatespage.inform = lambda win, head, body: informed.append(body)
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

    def has(*wants, absent=()):
        t = texts(st["win"])
        for w in wants:
            if not any(w in (s or "") for s in t):
                failures.append("missing: %s" % w)
        for w in absent:
            if any(w in (s or "") for s in t):
                failures.append("shown but should not be: %s" % w)

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(800)
    win = st["win"]
    if not win.open_tag("updates"):
        failures.append("duo-settings updates does not open the page")
    page = win.current
    spin(500)
    has(
        "Updates",
        "Not checked yet",
        "Never checked",
        "Check now",
        "Install at night",
        "Tell me when updates are ready",
    )
    if page.dev_group.get_visible():
        failures.append("the testing channel is offered without developer mode")
    if page.install_row.get_visible():
        failures.append("Install shown with nothing found")
    if page.auto.get_active() or not page.notify.get_active():
        failures.append("the switches do not show the settings (auto off, notify on)")
    render(win, os.path.join(OUT, "updates-never.png"))

    page.check_button.emit("clicked")
    spin(300)
    if helper.calls[-2:] != [["check"], ["status"]]:
        failures.append("Check now: %r" % helper.calls[-2:])
    has("Looking for updates")
    if page.check_button.get_sensitive():
        failures.append("Check now pressable while a check runs")

    helper.state["job"] = {"job": "check", "state": "done", "finished": NOW}
    helper.state["last"] = copy.deepcopy(FOUND)
    page.refresh()
    spin(300)
    has(
        "4 updates are ready",
        "Checked today",
        "Install the updates",
        "2 from OpenDuo, 2 from Debian (1 security)",
        "openduo-duo-wc",
        "0.1.0-dev (20261116) → 0.1.0-dev (20261117)",
        "Debian packages",
        "What's new",
        "In development",
    )
    if not (page.install_row.get_visible() and page.install_button.get_sensitive()):
        failures.append("Install not offered")
    render(win, os.path.join(OUT, "updates-found.png"))
    for row in page.dynamic:
        if isinstance(row[1], Adw.ExpanderRow):
            row[1].set_expanded(True)
    spin(300)
    has("The dock on both panels", "curl, openssl")
    render(win, os.path.join(OUT, "updates-found-open.png"))

    helper.state["may_install"] = "the battery is at 30 %"
    page.refresh()
    spin(300)
    if page.install_button.get_sensitive():
        failures.append("Install pressable at 30 % off the charger")
    has("Connect the charger, or wait until the battery is at 50 %")
    helper.state["may_install"] = None
    page.refresh()
    spin(300)
    page.install_button.emit("clicked")
    spin(300)
    if ["install"] not in helper.calls:
        failures.append("Install did not call the helper")
    has("Installing: taking a snapshot")
    render(win, os.path.join(OUT, "updates-installing.png"))

    helper.state["job"] = {
        "job": "install",
        "state": "done",
        "finished": NOW,
        "reboot_required": True,
    }
    helper.state["reboot_required"] = True
    helper.state["last"] = dict(FOUND, ours=[], debian=[], changes=[], count=0, time=NOW)
    page.refresh()
    spin(300)
    has("Updates installed. Restart the phone to finish.", "Restart to finish the update")
    if page.install_row.get_visible():
        failures.append("Install shown after the install")
    render(win, os.path.join(OUT, "updates-restart.png"))

    page.auto.set_active(True)
    spin(200)
    if ["set", "auto", "on"] not in helper.calls or helper.state["conf"]["auto_install"] != "on":
        failures.append("the night switch did not reach the helper")
    helper.state["developer_mode"] = True
    page.refresh()
    spin(300)
    if not page.dev_group.get_visible():
        failures.append("no testing channel in developer mode")
    page.testing.set_active(True)
    spin(200)
    if ["set", "channel", "testing"] not in helper.calls:
        failures.append("the testing switch did not reach the helper")
    render(win, os.path.join(OUT, "updates-developer.png"))

    helper.state["kernel"] = {
        "running": {"release": "7.3.5-duo2-gaaaa111", "state": "trying"},
        "kernels": [{"release": "7.3.5-duo2-gaaaa111", "state": "trying", "tries_left": 2}],
    }
    page.refresh()
    spin(300)
    has("The new kernel 7.3.5-duo2-gaaaa111 is being checked")
    page.hidden()
    for f in failures:
        print("FAIL", f)
    print("updates page: %s" % ("ok" if not failures else "%d failures" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
