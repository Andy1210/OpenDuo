"""The shell is geoclue's agent: the Location switch and "Allow <app> to
use your location?" (locationmodel.py decides, from the user's answers).

geoclue (system bus, D-Bus activated, exits when idle) asks the agent of
the user whose app wants a position (`AuthorizeApp(desktop id, accuracy)`)
and caps every app of the user at its `MaxAccuracyLevel`. The shell
registers `org.duo2os.Shell` (whitelisted in /etc/geoclue/conf.d/
90-duo2.conf) whenever geoclue appears. An app asked about for the first
time gets the question on the active panel (20 s without an answer: no,
not remembered); a Flatpak through xdg-desktop-portal is answered from the
portal's permission store (the portal asked already). With the switch off
the level is NONE: geoclue refuses everyone and stops its sources (the
modem's GNSS engine with them).

The switch and the answers live in ~/.config/duo-shell/location.json
(Duo Settings' Privacy page writes it too); a change is followed through a
file monitor.

In use: geoclue's Manager.InUse (some app has a running client: a
native app, the Android apps' bridge duo2-android-location, the location
test) puts the location icon on the status bar and "In use" on the quick
menu's tile (`in_use_changed()`). Read without starting geoclue; false
while geoclue is not running.
"""

from gi.repository import Gio, GLib

from . import locationmodel as model
from .i18n import _

GC = "org.freedesktop.GeoClue2"
MANAGER_PATH = "/org/freedesktop/GeoClue2/Manager"
MANAGER = "org.freedesktop.GeoClue2.Manager"
AGENT_PATH = "/org/freedesktop/GeoClue2/Agent"
AGENT_ID = "org.duo2os.Shell"
PROPS = "org.freedesktop.DBus.Properties"
ASK_S = 20

AGENT_XML = """
<node>
  <interface name="org.freedesktop.GeoClue2.Agent">
    <property name="MaxAccuracyLevel" type="u" access="read"/>
    <method name="AuthorizeApp">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="req_accuracy_level" type="u" direction="in"/>
      <arg name="authorized" type="b" direction="out"/>
      <arg name="allowed_accuracy_level" type="u" direction="out"/>
    </method>
  </interface>
</node>
"""


def log(*args):
    print("duo-shell: location:", *args, flush=True)


def app_name(desktop_id):
    try:
        info = Gio.DesktopAppInfo.new(desktop_id + ".desktop")
    except TypeError:
        info = None
    return info.get_display_name() if info is not None else desktop_id


class LocationAgent:
    """`changed()` is called when the switch flips (the quick menu tile)."""

    def __init__(self, shell, changed, in_use_changed=None):
        self.shell = shell
        self.changed = changed
        self.in_use_changed = in_use_changed
        self.in_use = False
        self.in_use_sub = None
        self.store = model.load()
        self.bus = None
        self.reg = None
        self.pending = {}  # desktop id -> [invocations]
        self.monitor = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as e:
            log("no system bus (%s): no location agent" % e.message)
            return
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.reg = self.bus.register_object(
            AGENT_PATH, node.interfaces[0], self.on_call, self.on_get, None
        )
        Gio.bus_watch_name_on_connection(
            self.bus, GC, Gio.BusNameWatcherFlags.NONE, self.geoclue_up, self.geoclue_down
        )
        path = model.config_path()
        try:
            self.monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.monitor.connect("changed", lambda *_a: self.reload())
        except GLib.Error:
            pass

    @property
    def enabled(self):
        return bool(self.store.get("enabled", True))

    # -- geoclue ------------------------------------------------------------------

    def geoclue_up(self, bus, _name, owner):
        bus.call(
            GC,
            MANAGER_PATH,
            MANAGER,
            "AddAgent",
            GLib.Variant("(s)", (AGENT_ID,)),
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            5000,
            None,
            self._added,
        )
        if self.in_use_sub is None:
            self.in_use_sub = bus.signal_subscribe(
                owner,
                PROPS,
                "PropertiesChanged",
                MANAGER_PATH,
                MANAGER,
                Gio.DBusSignalFlags.NONE,
                self._manager_props,
            )
        bus.call(
            GC,
            MANAGER_PATH,
            PROPS,
            "Get",
            GLib.Variant("(ss)", (MANAGER, "InUse")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            5000,
            None,
            self._in_use_read,
        )

    def geoclue_down(self, bus, _name):
        if self.in_use_sub is not None:
            bus.signal_unsubscribe(self.in_use_sub)
            self.in_use_sub = None
        self.set_in_use(False)

    def _in_use_read(self, bus, result):
        try:
            self.set_in_use(bool(bus.call_finish(result).unpack()[0]))
        except GLib.Error:
            pass

    def _manager_props(self, _conn, _sender, _path, _iface, _signal, params):
        _iface_name, changed, _invalid = params.unpack()
        if "InUse" in changed:
            self.set_in_use(bool(changed["InUse"]))

    def set_in_use(self, on):
        if on == self.in_use:
            return
        self.in_use = on
        log("in use" if on else "no longer in use")
        if self.in_use_changed is not None:
            self.in_use_changed()

    def _added(self, bus, result):
        try:
            bus.call_finish(result)
            log("geoclue agent registered (location %s)" % ("on" if self.enabled else "off"))
        except GLib.Error as e:
            log("AddAgent: %s" % e.message)

    def on_get(self, _conn, _sender, _path, _iface, prop):
        if prop == "MaxAccuracyLevel":
            return GLib.Variant("u", model.max_level(self.store))
        return None

    def on_call(self, _conn, sender, _path, _iface, method, params, inv):
        if method != "AuthorizeApp":
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        desktop_id, requested = params.unpack()
        verdict, level = model.decide(self.store, desktop_id, requested)
        if verdict != "ask":
            log("%s: %s" % (desktop_id, verdict))
            inv.return_value(GLib.Variant("(bu)", (verdict == "allow", level)))
            return
        waiting = self.pending.setdefault(desktop_id, [])
        waiting.append((inv, requested))
        if len(waiting) == 1:
            self.ask_portal(desktop_id)

    def finish(self, desktop_id, allowed, remember):
        if remember:
            self.store = model.answer(self.store, desktop_id, allowed)
            try:
                model.save(self.store)
            except OSError as e:
                log("cannot save the answer: %s" % e)
        log(
            "%s: %s%s"
            % (
                desktop_id,
                "allowed" if allowed else "denied",
                "" if remember else " (not remembered)",
            )
        )
        for inv, requested in self.pending.pop(desktop_id, []):
            level = min(int(requested), model.EXACT) if allowed else model.NONE
            inv.return_value(GLib.Variant("(bu)", (bool(allowed), level)))

    # -- the portal's answer, else the user's -------------------------------------------

    def ask_portal(self, desktop_id):
        try:
            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.ask_user(desktop_id)
            return
        session.call(
            "org.freedesktop.impl.portal.PermissionStore",
            "/org/freedesktop/impl/portal/PermissionStore",
            "org.freedesktop.impl.portal.PermissionStore",
            "Lookup",
            GLib.Variant("(ss)", ("location", "location")),
            GLib.VariantType("(a{sas}v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            2000,
            None,
            self._portal,
            desktop_id,
        )

    def _portal(self, bus, result, desktop_id):
        try:
            apps = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            apps = {}
        granted = model.portal_granted(apps.get(desktop_id))
        if granted is not None:
            log("%s: answered through the portal" % desktop_id)
            self.finish(desktop_id, granted, remember=True)
            return
        self.ask_user(desktop_id)

    def ask_user(self, desktop_id):
        name = app_name(desktop_id)
        timer = {}

        def answered(yes):
            if "id" in timer:
                GLib.source_remove(timer.pop("id"))
            if desktop_id in self.pending:
                self.finish(desktop_id, yes, remember=True)

        def too_late():
            timer.pop("id", None)
            if desktop_id in self.pending:
                sheet.close_sheet(keep_confirm=True)
                sheet.callback = None
                self.finish(desktop_id, False, remember=False)
            return False

        sheet = self.shell.question_sheet()
        if sheet is None:
            # The screen is dark: nobody to ask now.
            self.finish(desktop_id, False, remember=False)
            return
        # Translators: %s is an app's name.
        sheet.ask_question(
            _("Allow %s to use your location?") % name,
            _(
                "It can then find out where the phone is, from the mobile network "
                "and, outdoors, from satellites. You can change this in Settings, "
                "Privacy."
            ),
            _("Allow"),
            answered,
            no_label=_("Don't allow"),
        )
        timer["id"] = GLib.timeout_add_seconds(ASK_S, too_late)

    # -- the switch ------------------------------------------------------------------------

    def reload(self):
        old = model.max_level(self.store)
        self.store = model.load()
        if model.max_level(self.store) != old:
            self.level_changed()

    def set_enabled(self, on):
        self.store = model.set_enabled(model.load(), on)
        try:
            model.save(self.store)
        except OSError as e:
            log("cannot save the switch: %s" % e)
        self.level_changed()

    def level_changed(self):
        log("location %s" % ("on" if self.enabled else "off"))
        if self.bus is not None:
            self.bus.emit_signal(
                None,
                AGENT_PATH,
                PROPS,
                "PropertiesChanged",
                GLib.Variant(
                    "(sa{sv}as)",
                    (
                        "org.freedesktop.GeoClue2.Agent",
                        {"MaxAccuracyLevel": GLib.Variant("u", model.max_level(self.store))},
                        [],
                    ),
                ),
            )
        self.changed()
