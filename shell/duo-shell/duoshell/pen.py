"""The Surface Pen's top button and battery in the shell (the rules:
penmodel.py).

- `PenButton.pressed(message)`: duo-wc's `pen_button` event -> what Duo
  Settings > Pen set for that press (pen.conf): the note app on the press's
  panel (a running one brought forward), a screenshot of that panel (duo-wc's
  `screenshot` on the session's trusted connection, written to
  ~/Pictures/Screenshots, a notification that opens it), or the quick menu.
- `PenBattery.update(pens)`: the paired pens' battery (penmodel.pens, from
  dbus.Bluetooth) -> "Pen battery low" once at or below penmodel.LOW per cent;
  what was said is kept in ~/.cache/duo-shell/pen-battery (hashed keys), so a
  restarted shell does not say it again.
"""

import json
import os
import threading

from gi.repository import Gdk, Gio, GLib

from . import penmodel, wc


def log(*args):
    print("duo-shell: pen:", *args, flush=True)


class PenButton:
    def __init__(self, shell):
        self.shell = shell

    def pressed(self, message):
        decided = penmodel.decide(message, penmodel.load())
        if decided is None:
            log(
                "%s: nothing (output %s, locked %s)"
                % (message.get("press"), message.get("output"), bool(message.get("locked")))
            )
            return
        action, output = decided
        log("%s on %s: %s" % (message.get("press"), output, action))
        if action == "notes":
            self.open_notes(output)
        elif action == "screenshot":
            self.screenshot(output)
        elif action == "quick_menu":
            self.shell.open_quick(output)

    # -- the note app -------------------------------------------------------------
    def open_notes(self, output):
        installed = {i.get_id() for i in Gio.AppInfo.get_all() if i.get_id()}
        desktop_id = penmodel.notes_app(installed)
        if desktop_id is None:
            self.shell.notifications.post(
                "No note app", "Install Rnote from Software.", icon="document-edit-symbolic"
            )
            return
        window = penmodel.running_window((self.shell.wc_state or {}).get("windows"), desktop_id)
        if window is not None:
            try:
                wc.request("activate_window", window=window)
                return
            except (OSError, RuntimeError, ValueError) as e:
                log("activate_window: %s" % e)
        info = Gio.DesktopAppInfo.new(desktop_id)
        if info is None:
            return
        self.shell.desk_launch(output, info, Gdk.Display.get_default())

    # -- a screenshot ---------------------------------------------------------------
    def screenshot(self, output):
        directory = penmodel.screenshot_dir()
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            log("screenshot: %s" % e)
            return
        path = penmodel.screenshot_path(directory)
        try:
            # The trusted connection is made here, on the main thread (the
            # session's trust factory is one socket); the wait is the thread's.
            sock = wc.connect(20.0)
        except OSError as e:
            self.screenshot_done(path, str(e))
            return

        def take():
            # duo-wc reads the panel back on its loop and writes the PNG in a
            # thread of its own; the answer comes when the file is there.
            try:
                wc.request("screenshot", sock=sock, output=output, path=path)
                err = None
            except (OSError, RuntimeError, ValueError) as e:
                err = str(e)
            GLib.idle_add(self.screenshot_done, path, err)

        threading.Thread(target=take, name="pen-screenshot", daemon=True).start()

    def screenshot_done(self, path, err):
        if err is not None:
            log("screenshot failed: %s" % err)
            self.shell.notifications.post(
                "Screenshot failed", err, icon="applets-screenshooter-symbolic"
            )
            return False
        log("screenshot: %s" % os.path.basename(path))
        uri = Gio.File.new_for_path(path).get_uri()

        def opened(_key):
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error as e:
                log("opening the screenshot: %s" % e.message)

        self.shell.notifications.post(
            "Screenshot saved",
            "Pictures/Screenshots/" + os.path.basename(path),
            actions=("default", "Open"),
            on_action=opened,
            icon="applets-screenshooter-symbolic",
        )
        return False


class PenBattery:
    def __init__(self, shell, path=None):
        self.shell = shell
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".cache", "duo-shell", "pen-battery"
        )
        self.notice = penmodel.BatteryNotice(self._load())
        self.nids = {}

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f).get("told", [])
        except (OSError, ValueError, AttributeError):
            return []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w") as f:
                json.dump({"told": sorted(self.notice.told)}, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError as e:
            log("battery state: %s" % e)

    def update(self, pens):
        before = set(self.notice.told)
        for pen in pens:
            key = penmodel.key_of(pen["path"])
            if self.notice.update(key, pen["battery"]):
                summary, body = penmodel.battery_text(pen["name"], pen["battery"])
                log("battery low (%d %%)" % pen["battery"])
                self.nids[key] = self.shell.notifications.post(
                    summary,
                    body,
                    icon="battery-caution-symbolic",
                    replaces_id=self.nids.get(key, 0),
                )
        if self.notice.told != before:
            self._save()
