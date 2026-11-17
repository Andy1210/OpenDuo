"""The Privacy page: Location on / off, and which apps may use it
(privacy.py has the rules and why there are two kinds of app)."""

from gi.repository import Gio, GLib

from . import privacy
from .main import Page, _, run_async, set_quietly, switch_row
from .system import HELPER

PSTORE = (
    "org.freedesktop.impl.portal.PermissionStore",
    "/org/freedesktop/impl/portal/PermissionStore",
    "org.freedesktop.impl.portal.PermissionStore",
)


def app_name(desktop_id):
    try:
        info = Gio.DesktopAppInfo.new(desktop_id + ".desktop")
    except TypeError:
        info = None
    return info.get_display_name() if info is not None else desktop_id


class PrivacyPage(Page):
    icon = "preferences-system-privacy-symbolic"
    title = _("Privacy")

    def build(self):
        g = self.group(
            _("Location"),
            _(
                "From the mobile network and, outdoors, the satellites. "
                "Off: no app learns where the phone is, and the satellite "
                "receiver stays off."
            ),
        )
        self.switch = switch_row(
            _("Location"), _("Apps may ask where the phone is"), on_toggle=self.toggle
        )
        g.add(self.switch)
        self.apps_group = self.group(
            _("Apps"),
            _(
                "Apps installed from the store as Flatpaks are asked the first time they want the "
                "location. Other apps are not sandboxed: they may use it while Location is on, "
                "unless switched off here."
            ),
        )
        self.app_rows = []
        self.portal = {}

    def shown(self):
        self.store = privacy.load_store()
        set_quietly(self.switch, self.store["enabled"])
        self.read_portal()

    def toggle(self, on):
        self.store = privacy.load_store()
        self.store["enabled"] = bool(on)
        try:
            privacy.save_store(self.store)
        except OSError as e:
            self.toast(_("Could not save: %s") % e)
        self.fill()

    def read_portal(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.fill()
            return
        bus.call(
            *PSTORE,
            "Lookup",
            GLib.Variant("(ss)", ("location", "location")),
            GLib.VariantType("(a{sas}v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            self._portal,
        )

    def _portal(self, bus, result):
        self.portal = {}
        try:
            apps = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            apps = {}
        for app, perms in apps.items():
            self.portal[app] = bool(perms) and str(perms[0]).upper() not in ("", "NONE")
        self.fill()

    def fill(self):
        for row in self.app_rows:
            self.apps_group.remove(row)
        self.app_rows = []
        infos = []
        for info in Gio.AppInfo.get_all():
            if isinstance(info, Gio.DesktopAppInfo):
                infos.append(
                    (
                        info.get_id() or "",
                        info.get_display_name(),
                        info.get_string("X-Geoclue-Reason"),
                    )
                )
        native = privacy.location_apps(infos)
        denied = privacy.load_denied()
        rows = privacy.rows(self.store, denied, native, self.portal)
        if not rows:
            row = switch_row(_("No app has asked yet"))
            row.set_sensitive(False)
            self.apps_group.add(row)
            self.app_rows.append(row)
            return
        for app, name, allowed, kind in rows:
            sub = {
                "native": _("Not sandboxed"),
                "asked": _("Asked by the phone"),
                "portal": _("Flatpak, asked through the portal"),
            }[kind]
            row = switch_row(
                name if name != app else app_name(app),
                sub,
                active=allowed,
                on_toggle=lambda on, a=app, k=kind: self.set_app(a, k, on),
            )
            row.set_sensitive(self.store["enabled"])
            self.apps_group.add(row)
            self.app_rows.append(row)

    def set_app(self, app, kind, on):
        if kind == "native":
            denied = privacy.load_denied()
            denied = (denied - {app}) if on else (denied | {app})
            run_async(
                ["sudo", "-n", HELPER, "geoclue-deny", *sorted(denied)],
                lambda ok, _out: ok or self.toast(_("Could not change it")),
            )
        elif kind == "asked":
            self.store = privacy.load_store()
            self.store["apps"][app] = bool(on)
            privacy.save_store(self.store)
        else:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call_sync(
                    *PSTORE,
                    "SetPermission",
                    GLib.Variant(
                        "(sbssas)",
                        ("location", True, "location", app, ["EXACT" if on else "NONE", "0"]),
                    ),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
            except GLib.Error as e:
                self.toast(_("Could not change it: %s") % e.message)
