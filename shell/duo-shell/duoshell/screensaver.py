"""org.freedesktop.ScreenSaver on the session bus: apps' "keep the screen
on", passed to duo-wc as its idle hold (`set_idle_hold`; the rules in
screensavermodel.py and compositor/duo-wc/src/idle.rs).

Served at /org/freedesktop/ScreenSaver (Chromium, the portal's GTK backend)
and /ScreenSaver (Firefox, VLC): Inhibit / UnInhibit, GetActive /
GetActiveTime / ActiveChanged (the screen blanked or locked), SetActive
(refused: only the power key and the idle timeout switch the screen off).
An inhibitor whose connection leaves the bus ends with it.

GTK4 apps ask the Inhibit portal, but only if the portal already runs when
they start (GTK reads the portal's interface version without starting it),
so the frontend is started here once the name is ours. Its GTK backend
finds no GNOME session manager and passes idle inhibits on to this
interface.
"""

import random

from gi.repository import Gio, GLib

from . import screensavermodel as model
from . import wc

BUS_NAME = "org.freedesktop.ScreenSaver"
PATHS = ("/org/freedesktop/ScreenSaver", "/ScreenSaver")
IFACE = "org.freedesktop.ScreenSaver"
PORTAL = "org.freedesktop.portal.Desktop"
DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")

INTROSPECTION = """
<node>
  <interface name="org.freedesktop.ScreenSaver">
    <method name="Inhibit">
      <arg type="s" name="application_name" direction="in"/>
      <arg type="s" name="reason_for_inhibit" direction="in"/>
      <arg type="u" name="cookie" direction="out"/>
    </method>
    <method name="UnInhibit">
      <arg type="u" name="cookie" direction="in"/>
    </method>
    <method name="GetActive">
      <arg type="b" name="active" direction="out"/>
    </method>
    <method name="GetActiveTime">
      <arg type="u" name="seconds" direction="out"/>
    </method>
    <method name="SetActive">
      <arg type="b" name="active" direction="in"/>
      <arg type="b" name="changed" direction="out"/>
    </method>
    <signal name="ActiveChanged">
      <arg type="b" name="active"/>
    </signal>
  </interface>
</node>
"""


def log(*args):
    print("duo-shell: screensaver:", *args, flush=True)


def process_name(pid):
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


class ScreenSaver:
    def __init__(self, shell):
        self.shell = shell
        self.model = model.Inhibits(first_cookie=random.randint(1, 1 << 30))
        self.conn = None
        self.active = False
        self.active_since = None  # monotonic µs
        self.renew = None
        self.failed = None
        self.node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self.owner = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE,
            self._bus_acquired,
            lambda *_a: log("name acquired"),
            lambda *_a: log("name lost (another screen saver?)"),
        )
        # A hold left by a shell before this one (it would run out by itself).
        self.push()

    # -- the bus ------------------------------------------------------------------

    def _bus_acquired(self, conn, _name):
        self.conn = conn
        for path in PATHS:
            try:
                conn.register_object(path, self.node.interfaces[0], self._call, None, None)
            except GLib.Error as e:
                log("cannot register %s: %s" % (path, e.message))
        # A connection that leaves the bus takes its inhibitors along.
        conn.signal_subscribe(
            DBUS[0],
            DBUS[2],
            "NameOwnerChanged",
            DBUS[1],
            None,
            Gio.DBusSignalFlags.NONE,
            self._owner_changed,
        )
        conn.call(
            DBUS[0],
            DBUS[1],
            DBUS[2],
            "StartServiceByName",
            GLib.Variant("(su)", (PORTAL, 0)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            25000,
            None,
            self._portal_started,
        )

    def _portal_started(self, conn, result):
        try:
            conn.call_finish(result)
            log("the portal runs (GTK apps' Inhibit reaches this service)")
        except GLib.Error as e:
            log("could not start the portal: %s" % e.message)

    def _owner_changed(self, _conn, _sender, _path, _iface, _signal, params):
        name, old, new = params.unpack()
        if new or name != old or old not in self.model.senders():
            return
        gone = self.model.drop_sender(old)
        if gone:
            log("%s left the bus: %d inhibitor(s) gone" % (old, len(gone)))
            self.push()

    def _call(self, _conn, sender, _path, _iface, method, params, invocation):
        try:
            if method == "Inhibit":
                app, reason = params.unpack()
                cookie = self.model.add(sender, app, reason)
                if cookie is None:
                    log("inhibit by %s refused: too many" % sender)
                    invocation.return_dbus_error(
                        "org.freedesktop.DBus.Error.LimitsExceeded", "too many inhibitors"
                    )
                    return
                invocation.return_value(GLib.Variant("(u)", (cookie,)))
                item = self.model.items[cookie]
                log("inhibit %d by %r (%s): %r" % (cookie, item["app"], sender, item["reason"]))
                self._ask_pid(sender, cookie)
            elif method == "UnInhibit":
                (cookie,) = params.unpack()
                if self.model.remove(cookie, sender):
                    log("uninhibit %d" % cookie)
                    self.push()
                invocation.return_value(None)
            elif method == "GetActive":
                invocation.return_value(GLib.Variant("(b)", (self.active,)))
            elif method == "GetActiveTime":
                seconds = 0
                if self.active and self.active_since is not None:
                    seconds = max(0, int((GLib.get_monotonic_time() - self.active_since) / 1e6))
                invocation.return_value(GLib.Variant("(u)", (seconds,)))
            elif method == "SetActive":
                # Not done: the screen goes off by the power key or the idle
                # timeout, not because an app asked.
                invocation.return_value(GLib.Variant("(b)", (False,)))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:  # never leave a caller hanging
            log("%s failed: %r" % (method, e))
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _ask_pid(self, sender, cookie):
        self.conn.call(
            DBUS[0],
            DBUS[1],
            DBUS[2],
            "GetConnectionUnixProcessID",
            GLib.Variant("(s)", (sender,)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            self._got_pid,
            cookie,
        )

    def _got_pid(self, conn, result, cookie):
        pid = None
        try:
            (pid,) = conn.call_finish(result).unpack()
        except GLib.Error as e:
            log("no process id for inhibitor %d: %s" % (cookie, e.message))
        if cookie not in self.model.items:
            return  # let go meanwhile
        self.model.set_pid(cookie, pid)
        if pid:
            self.model.set_fallback_name(cookie, model.fallback_name(process_name(pid)))
        self.push()

    # -- duo-wc -------------------------------------------------------------------

    def push(self):
        """Tell duo-wc the holders now; renewed while there are any."""
        holds = self._send()
        if holds and self.renew is None:
            self.renew = GLib.timeout_add_seconds(model.HOLD_RENEW_S, self._renew)
        elif not holds and self.renew is not None:
            GLib.source_remove(self.renew)
            self.renew = None

    def _renew(self):
        if not self.model.items:
            self.renew = None
            self._send()
            return False
        self._send()
        return True

    def _send(self):
        holds = self.model.holds()
        try:
            wc.request("set_idle_hold", holds=holds, ttl_ms=model.HOLD_TTL_MS)
            if self.failed:
                log("set_idle_hold works again")
                self.failed = None
        except (OSError, RuntimeError, ValueError) as e:
            if str(e) != self.failed:
                log("set_idle_hold: %s" % e)
                self.failed = str(e)
        return holds

    def wc_connected(self, state):
        """The event stream is (back) up: duo-wc may have restarted, its
        hold is gone."""
        self.wc_state(state)
        if self.model.items:
            self._send()

    def wc_state(self, state):
        """duo-wc's state changed: GetActive / ActiveChanged follow it."""
        active = model.active(state)
        if active == self.active:
            return
        self.active = active
        self.active_since = GLib.get_monotonic_time() if active else None
        if self.conn is None:
            return
        for path in PATHS:
            try:
                self.conn.emit_signal(
                    None, path, IFACE, "ActiveChanged", GLib.Variant("(b)", (active,))
                )
            except GLib.Error as e:
                log("ActiveChanged: %s" % e.message)
