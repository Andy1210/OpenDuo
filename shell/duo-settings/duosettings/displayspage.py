"""Settings > External display: a TV or a monitor on the USB-C port, used as
a desktop (windows on it, a taskbar, the phone's keyboard and touchpad or a
Bluetooth one) or as a mirror of the phone. duo-wc decides
(compositor/duo-wc/src/mirror.rs) and keeps the settings itself; this page
sets them through the helper (`duo-settings-helper display ...`) and reads
duo-wc's state for what is connected now. The shell asks on the phone when a
display is plugged in (unless a choice was remembered); its quick menu has a
Display tile to switch one that is connected."""

from gi.repository import Adw, Gtk

from . import system
from .main import Page, _, run_async

ON_CONNECT = (("ask", _("Ask")), ("desk", _("Desktop")), ("mirror", _("Mirror")))
MIRROR = (("active", _("The screen in use")), ("both", _("Both screens")))


class ExternalDisplayPage(Page):
    icon = "video-display-symbolic"
    title = _("External display")

    def build(self):
        self._quiet = False
        g = self.group(
            _("When a display is connected"),
            _(
                "Desktop: apps open in windows on the display, with a taskbar; the phone stays a phone. "
                "Mirror: the display shows the phone's screen."
            ),
        )
        self.on_connect = self._choices(g, ON_CONNECT, "on_connect")
        g = self.group(_("A mirror shows"), _("The screen in use follows your finger."))
        self.mirror = self._choices(g, MIRROR, "mirror")
        g = self.group(None)
        self.now = Adw.ActionRow(title=_("Now"))
        g.add(self.now)

    def _choices(self, group, choices, key):
        row = Adw.PreferencesRow()
        row.set_activatable(False)
        box = Gtk.Box(
            spacing=6, homogeneous=True, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8
        )
        buttons = {}
        first = None
        for value, label in choices:
            b = Gtk.ToggleButton(label=label)
            b.set_size_request(-1, 48)
            if first:
                b.set_group(first)
            first = first or b
            b.connect(
                "toggled",
                lambda btn, v=value: btn.get_active() and not self._quiet and self.helper([key, v]),
            )
            box.append(b)
            buttons[value] = b
        row.set_child(box)
        group.add(row)
        return buttons

    def shown(self):
        self.every(2, self.refresh)

    def refresh(self):
        d = system.wc_state().get("displays")
        if not isinstance(d, dict):
            self.now.set_subtitle(_("This display server cannot mirror"))
            return
        self._quiet = True
        self.on_connect.get(d.get("on_connect"), self.on_connect["ask"]).set_active(True)
        self.mirror.get(d.get("mirror"), self.mirror["active"]).set_active(True)
        self._quiet = False
        connected = [c for c in d.get("connected") or [] if isinstance(c, dict)]
        if not connected:
            self.now.set_subtitle(_("No display is connected"))
            return
        lines = []
        for c in connected:
            name = c.get("name") or c.get("output") or ""
            # Translators: a connected display and what it is used as.
            lines.append(
                _("%(name)s: desktop") % {"name": name}
                if c.get("use") == "desk"
                else _("%(name)s: mirror") % {"name": name}
            )
        self.now.set_subtitle(", ".join(lines))

    def helper(self, args):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            self.refresh()

        run_async(["sudo", "-n", system.HELPER, "display"] + args, done)
