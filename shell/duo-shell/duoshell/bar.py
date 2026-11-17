"""The status bar: one per panel, along its top edge.

A wlr-layer-shell surface on the top layer, anchored left, top and right,
with an exclusive zone of its height (duo-wc then shrinks that panel's
windows below it). Left: the clock and the icons of the newest
notifications' apps. Right: the location icon while an app uses the position, WiFi,
Bluetooth, battery (icon and per cent),
and three buttons: the on-screen keyboard, the task overview (duo-wc's) and
the app grid.

Dragging down anywhere on the bar, or tapping its status icons or the
notification icons, opens the quick menu on this panel.
"""

from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import mobilemodel, oskpolicy, status, timefmt
from .i18n import _
from .widgets import icon_image

PULL_DOWN = 24  # px a finger drags down on the bar to open the quick menu

BAR_HEIGHT = 48


def _icon(name, css="status"):
    image = Gtk.Image.new_from_icon_name(name)
    image.add_css_class(css)
    return image


def _button(icon, label, callback):
    button = Gtk.Button()
    button.set_child(Gtk.Image.new_from_icon_name(icon))
    button.set_tooltip_text(label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.set_focus_on_click(False)
    button.set_can_focus(False)
    button.connect("clicked", lambda _b: callback())
    return button


class StatusBar(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.add_css_class("duo-bar")
        self.set_decorated(False)

        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-bar")
        LayerShell.set_layer(self, LayerShell.Layer.TOP)
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, BAR_HEIGHT)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.set_default_size(-1, BAR_HEIGHT)

        self.clock = Gtk.Label()
        self.clock.add_css_class("clock")

        self.sim_locked = False
        # The mobile network (mobilemodel.bar): only with a SIM.
        self.mobile = _icon("network-cellular-offline-symbolic")
        self.mobile.set_visible(False)
        self.operator = Gtk.Label()
        self.operator.add_css_class("operator")
        self.operator.set_visible(False)
        self.wifi = _icon("network-wireless-offline-symbolic")
        # an app knows (or is finding out) where the phone is (location.py)
        self.location = _icon("location-services-active-symbolic")
        self.location.set_tooltip_text(_("Location in use"))
        self.location.update_property([Gtk.AccessibleProperty.LABEL], [_("Location in use")])
        self.location.set_visible(False)
        # do not disturb (dnd.py): a crossed-out bell while it is on
        self.dnd = _icon("notifications-disabled-symbolic")
        self.dnd.set_tooltip_text(_("Do not disturb"))
        self.dnd.update_property([Gtk.AccessibleProperty.LABEL], [_("Do not disturb")])
        self.dnd.set_visible(False)
        self.bt = _icon("bluetooth-disabled-symbolic")
        self.battery = _icon("battery-missing-symbolic")
        self.percent = Gtk.Label()
        self.percent.add_css_class("percent")
        self.keyboard = _button(
            "input-keyboard-symbolic",
            _("Keyboard"),
            lambda: shell.toggle_keyboard(self.connector()),
        )
        self.apps = _button(
            "view-app-grid-symbolic", _("Apps"), lambda: shell.toggle_grid(self.monitor)
        )
        self.windows = _button("focus-windows-symbolic", _("Windows"), shell.toggle_overview)

        # The status icons: tapping them opens the quick menu.
        icons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        icons.add_css_class("status-icons")
        for widget in (
            self.dnd,
            self.location,
            self.mobile,
            self.wifi,
            self.bt,
            self.battery,
            self.percent,
        ):
            icons.append(widget)
        tap = Gtk.GestureClick()
        tap.connect("released", lambda *_a: self._icons_tapped())
        icons.add_controller(tap)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        right.set_valign(Gtk.Align.CENTER)
        for widget in (icons, self.keyboard, self.windows, self.apps):
            right.append(widget)
        right.set_margin_end(2)

        # The newest notifications' app icons, after the clock.
        self.notes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.notes.add_css_class("notes")
        self.notes.set_valign(Gtk.Align.CENTER)
        notes_tap = Gtk.GestureClick()
        notes_tap.connect("released", lambda *_a: shell.toggle_quick(self.monitor))
        self.notes.add_controller(notes_tap)
        self.notes_key = None
        # The clock: a tap opens Clocks' alarms on this panel.
        clock_tap = Gtk.GestureClick()
        clock_tap.connect("released", lambda *_a: shell.open_alarms(self.connector()))
        self.clock.add_controller(clock_tap)
        self.clock.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Opens the alarms")])
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        left.set_valign(Gtk.Align.CENTER)
        left.append(self.clock)
        left.append(self.operator)
        left.append(self.notes)

        bar = Gtk.CenterBox()
        bar.set_start_widget(left)
        bar.set_end_widget(right)
        bar.set_size_request(-1, BAR_HEIGHT)
        self.set_child(bar)

        # Pull down: a drag that goes down from anywhere on the bar (seen
        # before the buttons; it claims the touch only once it is a pull).
        self.pulled = False
        pull = Gtk.GestureDrag()
        pull.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pull.connect("drag-begin", lambda *_a: setattr(self, "pulled", False))
        pull.connect("drag-update", self._pull)
        self.add_controller(pull)

    def _icons_tapped(self):
        # A locked SIM: its PIN dialog; otherwise the quick menu.
        if self.sim_locked:
            self.shell.sim_prompt_check(self.connector(), force=True)
        else:
            self.shell.toggle_quick(self.monitor)

    def _pull(self, gesture, dx, dy):
        if self.pulled or dy < PULL_DOWN or abs(dx) > dy:
            return
        self.pulled = True
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.shell.open_quick(self.connector())

    def connector(self):
        return self.monitor.get_connector()

    def update(self, st):
        """Show a Shell state dict (see main.Shell.status)."""
        now = GLib.DateTime.new_now_local()
        # Translators: the status bar clock, a GLib.DateTime format.
        self.clock.set_label(now.format(timefmt.hm()) or "")

        mobile = mobilemodel.bar(st.get("mobile"), bool(st.get("airplane")))
        self.sim_locked = bool(mobile and mobile.get("locked"))
        self.mobile.set_visible(mobile is not None)
        self.operator.set_visible(bool(mobile and mobile.get("text")))
        if mobile is not None:
            self.mobile.set_from_icon_name(mobile["icon"])
            tips = {
                "Airplane mode": _("Airplane mode"),
                "SIM locked": _("SIM locked"),
                "Searching": _("Searching"),
                "Mobile network off": _("Mobile network off"),
            }
            self.mobile.set_tooltip_text(tips.get(mobile.get("tip"), mobile.get("tip") or ""))
            self.operator.set_label(mobile.get("text") or "")

        self.location.set_visible(bool(st.get("location_in_use")))
        self.dnd.set_visible(bool(st.get("dnd")))

        wifi = st.get("wifi") or {}
        self.wifi.set_from_icon_name(status.wifi_icon(wifi))
        if wifi.get("state") == "connected":
            text = wifi.get("ssid") or _("WiFi connected")
            if wifi.get("rssi") is not None:
                # Translators: WiFi network name and signal strength in dBm.
                text = _("%(ssid)s, %(rssi)d dBm") % {"ssid": text, "rssi": wifi["rssi"]}
        elif wifi.get("state") == "absent":
            text = _("No WiFi device")
        else:
            text = _("WiFi not connected")
        self.wifi.set_tooltip_text(text)

        powered = st.get("bluetooth")
        if powered is None:
            self.bt.set_from_icon_name("bluetooth-hardware-disabled-symbolic")
            self.bt.set_tooltip_text(_("No Bluetooth adapter"))
        elif powered:
            self.bt.set_from_icon_name("bluetooth-active-symbolic")
            self.bt.set_tooltip_text(_("Bluetooth on"))
        else:
            self.bt.set_from_icon_name("bluetooth-disabled-symbolic")
            self.bt.set_tooltip_text(_("Bluetooth off"))

        percent, state = st.get("battery") or (None, None)
        self.battery.set_from_icon_name(status.battery_icon(percent, state))
        if percent is None:
            self.percent.set_label("")
        else:
            # Translators: battery level in the status bar.
            self.percent.set_label(_("%d%%") % percent)
        charging = state in ("Charging", "Full")
        self.battery.set_tooltip_text(_("Charging") if charging else _("On battery"))

        if oskpolicy.button_lit(
            st.get("keyboard"), st.get("osk_away"), st.get("osk_output"), self.connector()
        ):
            self.keyboard.add_css_class("on")
        else:
            self.keyboard.remove_css_class("on")
        if st.get("grid_open_on") == self.connector():
            self.apps.add_css_class("on")
        else:
            self.apps.remove_css_class("on")
        if st.get("overview"):
            self.windows.add_css_class("on")
        else:
            self.windows.remove_css_class("on")

        notes = st.get("notifications") or []
        key = tuple((n.id, n.icon()) for n in notes)
        if key != self.notes_key:
            self.notes_key = key
            child = self.notes.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.notes.remove(child)
                child = nxt
            for n in notes:
                image = icon_image(n.icon(), 18)
                image.add_css_class("note")
                image.set_tooltip_text(n.app_name or n.summary)
                self.notes.append(image)
