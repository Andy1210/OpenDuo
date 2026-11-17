"""org.duo2os.Shell on the SYSTEM bus: the administrator's remote for the
shell, for tests and screenshots that must not touch the screen.

  sudo busctl --system call org.duo2os.Shell /org/duo2os/Shell org.duo2os.Shell OpenQuickMenu s DSI-1
  sudo busctl --system call org.duo2os.Shell /org/duo2os/Shell org.duo2os.Shell CloseQuickMenu

OpenQuickMenu(output) opens that panel's quick menu the way the swipe down
from its top edge does (Shell.open_quick, the same call); CloseQuickMenu()
closes every open one the way its close arrow does.

Who may: root only. Every app runs as the GUI user, so the user's session
bus cannot tell an app from the administrator; the system bus can. Its
policy (dbus/org.duo2os.Shell.conf, installed by install.sh) lets only the
GUI user own the name and only root call it, and the shell checks the
caller's uid itself as well (`allowed`), the same rule as duo-wc's admin
socket (the administrator, duo2admin, goes through sudo). Without the
policy the name is never had: the remote is simply not there.

`decide` is pure (tests/test_shellbus.py).
"""

from gi.repository import Gio, GLib

NAME = "org.duo2os.Shell"
PATH = "/org/duo2os/Shell"
IFACE = "org.duo2os.Shell"
XML = """<node>
  <interface name="org.duo2os.Shell">
    <method name="OpenQuickMenu">
      <arg type="s" name="output" direction="in"/>
    </method>
    <method name="CloseQuickMenu"/>
  </interface>
</node>"""


def allowed(uid):
    """Root only (sudo for the administrator)."""
    return uid == 0


def decide(method, args, uid, outputs):
    """(action, output, error) for a call: action "open" / "close" or None,
    error a (D-Bus error name, message) or None."""
    if not allowed(uid):
        return None, None, ("org.freedesktop.DBus.Error.AccessDenied", "root only")
    if method == "OpenQuickMenu":
        output = args[0] if args else ""
        if output not in outputs:
            return (
                None,
                None,
                (
                    "org.freedesktop.DBus.Error.InvalidArgs",
                    "no output %r (outputs: %s)" % (output, ", ".join(sorted(outputs)) or "none"),
                ),
            )
        return "open", output, None
    if method == "CloseQuickMenu":
        return "close", None, None
    return None, None, ("org.freedesktop.DBus.Error.UnknownMethod", method)


class ShellBus:
    """Owns the name, registers the object; `open_quick(output)`,
    `close_quicks()` and `outputs()` are the shell's."""

    def __init__(self, open_quick, close_quicks, outputs, log):
        self.open_quick = open_quick
        self.close_quicks = close_quicks
        self.outputs = outputs
        self.log = log
        self.reg = None
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.owner = Gio.bus_own_name(
            Gio.BusType.SYSTEM, NAME, Gio.BusNameOwnerFlags.NONE, self._acquired, None, self._lost
        )

    def _acquired(self, conn, _name):
        try:
            self.reg = conn.register_object(PATH, self.node.interfaces[0], self._call, None, None)
        except GLib.Error as e:
            self.log("system bus %s: %s" % (PATH, e.message))

    def _lost(self, conn, _name):
        if conn is None:
            self.log("no system bus: no %s remote" % NAME)
        else:
            self.log("%s not owned on the system bus (no policy installed?): no remote" % NAME)

    def _call(self, conn, sender, _path, _iface, method, params, inv):
        def got_uid(c, result):
            try:
                uid = c.call_finish(result).unpack()[0]
            except GLib.Error as e:
                inv.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", e.message)
                return
            action, output, error = decide(method, params.unpack(), uid, set(self.outputs()))
            if error:
                self.log("%s %s from uid %d refused: %s" % (NAME, method, uid, error[1]))
                inv.return_dbus_error(*error)
                return
            self.log("%s %s%s (uid %d)" % (NAME, method, " " + output if output else "", uid))
            if action == "open":
                self.open_quick(output)
            else:
                self.close_quicks()
            inv.return_value(None)

        conn.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixUser",
            GLib.Variant("(s)", (sender,)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            got_uid,
        )
