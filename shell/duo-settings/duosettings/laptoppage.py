"""Settings > Laptop mode: one screen a desktop (windows that float, a
taskbar, a pointer), the other a keyboard and a touchpad. duo-wc does it
(compositor/duo-wc README "Laptop mode"); this page switches it through the
helper (`duo-settings-helper laptop ...`, duo-wc's admin socket) and reads
duo-wc's state for what is on now. duo-wc keeps the two settings itself."""

from gi.repository import Adw, Gtk

from . import system
from .main import Page, _, run_async, set_quietly, switch_row

PANELS = (("DSI-1", _("Left")), ("DSI-2", _("Right")))


class LaptopPage(Page):
    icon = "computer-symbolic"
    title = _("Laptop mode")

    def build(self):
        g = self.group(
            None,
            _(
                "One screen becomes a desktop: windows you can move, resize and put side by side, a "
                "taskbar and a pointer, and it stays a touch screen. The other screen becomes a "
                "keyboard with a touchpad. Hold the keyboard key at its top right to leave."
            ),
        )
        self.on = switch_row(_("Laptop mode"), on_toggle=self.toggle)
        g.add(self.on)
        g = self.group(_("Automatically"))
        self.auto = switch_row(
            _("Turn on in the laptop posture"),
            _(
                "One half lying flat, the other standing up. It turns off again when the phone "
                "leaves that posture"
            ),
            on_toggle=lambda on: self.helper(
                ["auto", "on" if on else "off"], _("Could not save the setting")
            ),
        )
        g.add(self.auto)
        g = self.group(
            _("Keyboard screen"),
            _(
                "Turned sideways like a laptop, the keyboard is always on the lower screen. "
                "Held upright, it goes here:"
            ),
        )
        row = Adw.PreferencesRow()
        row.set_activatable(False)
        box = Gtk.Box(
            spacing=6, homogeneous=True, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8
        )
        self.panel_buttons = {}
        self._quiet = False
        first = None
        for key, label in PANELS:
            b = Gtk.ToggleButton(label=label)
            b.set_size_request(-1, 48)
            if first:
                b.set_group(first)
            first = first or b
            b.connect(
                "toggled",
                lambda btn, k=key: (
                    btn.get_active()
                    and not self._quiet
                    and self.helper(["keys", k], _("Could not save the setting"))
                ),
            )
            box.append(b)
            self.panel_buttons[key] = b
        row.set_child(box)
        g.add(row)
        self.now = Adw.ActionRow(title=_("Now"))
        g.add(self.now)

    def shown(self):
        self.every(2, self.refresh)

    def refresh(self):
        lt = system.wc_state().get("laptop") or {}
        set_quietly(self.on, bool(lt.get("on")))
        set_quietly(self.auto, bool(lt.get("auto")))
        self._quiet = True
        self.panel_buttons.get(lt.get("keys_pref"), self.panel_buttons["DSI-2"]).set_active(True)
        self._quiet = False
        if lt.get("on"):
            self.now.set_subtitle(
                _("Desktop on the left screen, keyboard on the right")
                if lt.get("desk") == "DSI-1"
                else _("Desktop on the right screen, keyboard on the left")
            )
        elif not lt:
            self.now.set_subtitle(_("This display server has no laptop mode"))
        else:
            self.now.set_subtitle(_("Off"))

    def toggle(self, on):
        self.helper(
            ["on" if on else "off"],
            _("Laptop mode needs both screens in use")
            if on
            else _("Could not turn laptop mode off"),
        )

    def helper(self, args, failed):
        def done(ok, _o):
            if not ok:
                self.toast(failed)
            self.refresh()

        run_async(["sudo", "-n", system.HELPER, "laptop"] + args, done)
