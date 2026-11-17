"""The taskbar of a desk (an external display, laptop mode's desk panel):
what a desk has instead of the phone's status bar and dock (deskmodel.py).

A wlr-layer-shell surface on the top layer along the whole bottom edge,
with an exclusive zone of its height (the desk's windows stay above it),
keyboard none. Left to right:

- the **app menu** button (deskmenu.py: a search that types at once, every
  app);
- the **apps**: the pinned ones (dock.json, the dock's), then the windows of
  this desk that are not pinned; a dot under a running one, the front
  window's app lit. A click starts an app that has no window here, brings
  its window to the front, and sets it aside when it already is in front
  (deskmodel.tap_action); a right click or a long press pins or unpins it;
- the **tray** (right): WiFi, the battery, the time and the date; a click
  opens the quick menu on this desk.
"""

from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import apps, deskmodel, dockmodel, penscroll, status, timefmt, wc
from .i18n import _

HEIGHT = 48
LONG_PRESS_S = 0.6


class Taskbar(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.shown = None
        self.toast_timer = None
        self.add_css_class("duo-taskbar")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-taskbar")
        LayerShell.set_layer(self, LayerShell.Layer.TOP)
        penscroll.install(self)  # the pen drags the lists like a finger
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, HEIGHT)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.set_default_size(-1, HEIGHT)

        self.menu_button = Gtk.Button()
        self.menu_button.add_css_class("start")
        self.menu_button.set_child(Gtk.Image.new_from_icon_name("view-app-grid-symbolic"))
        self.menu_button.set_tooltip_text(_("Apps"))
        self.menu_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Apps")])
        self.menu_button.set_can_focus(False)
        self.menu_button.connect("clicked", lambda _b: shell.toggle_desk_menu(self.connector()))

        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.row.set_valign(Gtk.Align.CENTER)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_width(True)
        scroller.set_hexpand(True)
        scroller.set_child(self.row)

        start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        start.append(self.menu_button)
        start.append(scroller)
        start.set_hexpand(True)

        # The tray: one button, the quick menu.
        self.wifi = Gtk.Image.new_from_icon_name("network-wireless-offline-symbolic")
        self.battery = Gtk.Image.new_from_icon_name("battery-missing-symbolic")
        self.percent = Gtk.Label()
        self.percent.add_css_class("percent")
        self.clock = Gtk.Label()
        self.clock.add_css_class("clock")
        self.date = Gtk.Label()
        self.date.add_css_class("date")
        when = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        when.set_valign(Gtk.Align.CENTER)
        when.append(self.clock)
        when.append(self.date)
        tray_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for widget in (self.wifi, self.battery, self.percent, when):
            tray_box.append(widget)
        self.tray = Gtk.Button()
        self.tray.add_css_class("tray")
        self.tray.set_child(tray_box)
        self.tray.set_can_focus(False)
        self.tray.update_property([Gtk.AccessibleProperty.LABEL], [_("Quick settings")])
        self.tray.connect("clicked", lambda _b: shell.toggle_quick(self.monitor))

        self.toast = Gtk.Label()
        self.toast.add_css_class("toast")
        self.toast.set_halign(Gtk.Align.CENTER)
        self.toast.set_valign(Gtk.Align.CENTER)
        self.toast.set_visible(False)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.append(start)
        bar.append(self.tray)
        bar.set_size_request(-1, HEIGHT)
        overlay = Gtk.Overlay()
        overlay.set_child(bar)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

    def connector(self):
        return self.monitor.get_connector()

    def _windows(self):
        st = self.shell.wc_state or {}
        return dockmodel.windows_on(st.get("windows"), self.connector(), st)

    def populate(self, force=False):
        index, infos = self.shell.app_index()
        windows = self._windows()
        entries = dockmodel.items(self.shell.pinned, windows, index)
        shown = deskmodel.taskbar_signature(entries, windows)
        if not force and shown == self.shown and self.row.get_first_child() is not None:
            return
        self.shown = shown
        child = self.row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.row.remove(child)
            child = nxt
        pinned_done = False
        for item in entries:
            if not item.pinned and not pinned_done:
                pinned_done = True
                if self.row.get_first_child() is not None:
                    sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                    sep.add_css_class("sep")
                    self.row.append(sep)
            front = bool(item.app_id) and deskmodel.is_front_app(windows, item.app_id)
            self.row.append(self._app_button(item, infos.get(item.desktop_id), front))

    def _app_button(self, item, info, front):
        button = Gtk.Button()
        button.add_css_class("task")
        if front:
            button.add_css_class("front")
        button.set_can_focus(False)
        box = Gtk.Overlay()
        image = Gtk.Image()
        if info is not None and info.get_icon() is not None:
            image.set_from_gicon(info.get_icon())
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(28)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        dot = Gtk.Box()
        dot.add_css_class("dot")
        dot.set_halign(Gtk.Align.CENTER)
        dot.set_valign(Gtk.Align.END)
        dot.set_opacity(1.0 if item.running else 0.0)
        box.set_child(image)
        box.add_overlay(dot)
        button.set_child(box)
        name = apps.info_label(info, item.app_id or "")
        button.set_tooltip_text(name)
        button.update_property([Gtk.AccessibleProperty.LABEL], [name])
        held = {"at": None}
        down = Gtk.GestureClick()
        down.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        down.connect("pressed", lambda *_a: held.__setitem__("at", GLib.get_monotonic_time()))
        button.add_controller(down)
        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", lambda *_a: self.pin(item))
        button.add_controller(secondary)
        button.connect("clicked", lambda _b: self._clicked(item, info, held))
        return button

    def _clicked(self, item, info, held):
        at, held["at"] = held["at"], None
        if at is not None and (GLib.get_monotonic_time() - at) / 1e6 >= LONG_PRESS_S:
            self.pin(item)
        else:
            self.tap(item, info)

    def tap(self, item, info):
        action, window = deskmodel.tap_action(self._windows(), item.app_id)
        if action == "activate":
            self._wc("activate_window", window=window)
        elif action == "minimize":
            self._wc("minimize_window", window=window)
        else:
            self.shell.desk_launch(self.connector(), info, self.get_display())

    def pin(self, item):
        if not item.desktop_id:
            self.show_toast(_("Cannot pin this app"))
            return
        pinned = self.shell.toggle_pin(item.desktop_id)
        self.show_toast(_("Pinned") if pinned else _("Unpinned"))

    def _wc(self, name, **args):
        try:
            wc.request(name, **args)
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: taskbar: %s: %s" % (name, e), flush=True)

    def show_toast(self, text):
        self.toast.set_label(text)
        self.toast.set_visible(True)
        if self.toast_timer is not None:
            GLib.source_remove(self.toast_timer)
        self.toast_timer = GLib.timeout_add(1400, self._toast_done)

    def _toast_done(self):
        self.toast_timer = None
        self.toast.set_visible(False)
        return False

    def update(self, st):
        """The tray from the shell's state (main.Shell.state, as the bar's)."""
        now = GLib.DateTime.new_now_local()
        self.clock.set_label(now.format(timefmt.hm()) or "")
        self.date.set_label(now.format("%x") or "")
        wifi = st.get("wifi") or {}
        self.wifi.set_from_icon_name(status.wifi_icon(wifi))
        percent, state = st.get("battery") or (None, None)
        self.battery.set_from_icon_name(status.battery_icon(percent, state))
        self.percent.set_label(deskmodel.tray_battery(st.get("battery")))
