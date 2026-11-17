"""Settings > Android apps: keep Android (Waydroid) running in the
background for the messengers' notifications, or let it pause and stop when
no Android window is open (android.py has the files and who reads them)."""

from gi.repository import Adw, Gio, GLib

from . import android
from .main import Page, _, set_quietly, switch_row

CONTAINER = ("id.waydro.Container", "/ContainerManager", "id.waydro.ContainerManager")


class AndroidPage(Page):
    icon = "phone-symbolic"
    title = _("Android apps")

    def build(self):
        g = self.group(
            _("In the background"),
            _(
                "Off: a few minutes after you close the last Android window, Android is paused and then "
                "stopped, and uses no battery. Its apps then get no messages and show no notifications "
                "until you open one of them again."
            ),
        )
        self.keep = switch_row(
            _("Keep Android apps running in the background"),
            _(
                "For notifications from messengers. Uses more battery: Android stays in memory and "
                "wakes the phone for its own timers, also while it sleeps"
            ),
            on_toggle=self.toggle,
        )
        g.add(self.keep)
        g = self.group(
            None, _("After a restart, Android starts with the first Android app you open.")
        )
        self.now = Adw.ActionRow(title=_("Android now"))
        g.add(self.now)

    def shown(self):
        set_quietly(self.keep, android.keep_running())
        self.every(5, self.show_state)

    def show_state(self):
        self.now.set_subtitle(
            {
                "stopped": _("Not running"),
                "paused": _("Paused: no notifications"),
                "running": _("Running"),
            }[android.state()]
        )

    def toggle(self, on):
        try:
            android.set_keep_running(on)
        except OSError as e:
            self.toast(_("Could not save: %s") % e)
            set_quietly(self.keep, android.keep_running())
            return
        if on and android.state() == "paused":
            self.unfreeze()

    def unfreeze(self):
        """Paused by Waydroid (its container manager runs, so nothing is
        activated by this): wake it now instead of at the next app."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return

        def done(b, res):
            try:
                b.call_finish(res)
            except GLib.Error:
                self.toast(_("Android wakes with the next Android app you open"))
            self.show_state()

        bus.call(*CONTAINER, "Unfreeze", None, None, Gio.DBusCallFlags.NONE, 10000, None, done)
