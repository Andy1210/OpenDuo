"""The quick menu: pulled down from a panel's status bar.

A wlr-layer-shell surface on the overlay layer covering its panel (status bar
included: it has its own header), keyboard none (nothing to type, the
window's text field keeps the focus). Top to bottom:

- tiles, three to a row (two took half a portrait panel; a label too long
  for its tile wraps to a second line, ellipsized only past that): WiFi
  (the radio), Mobile data (duo2-wwan), Hotspot (hotspot/:
  shares mobile data over WiFi; the devices on it), Bluetooth (BlueZ; a
  radio rfkill-blocked by airplane mode is freed first), Airplane mode
  (WiFi, Bluetooth and the modem off; WiFi and Bluetooth can come back on
  one by one), Location (location.py: geoclue's agent), Sound (haptics.py:
  a tap goes to the next of the four sound modes, sound and vibration /
  sound only / vibration only / silent; feedbackd's profile and strength),
  Do not disturb (dnd.py: no banners, notifications and calls silent but
  for the exceptions, alarms ring), Flashlight (torchmodel.py: the camera's
  flash LED as a torch; read again every second while the menu shows, so
  it follows a switch-off from elsewhere), Auto-rotate
  (duo-posed's auto_rotate; shown as "sensors off" while duo-posed only logs), Keyboard (sm.puri.OSK0), Hinge gap (duo-wc),
  Panels, Laptop mode (duo-wc's set_laptop: one panel a desk, the other a
  keyboard and a touchpad; laptopmodel.tile), Windows (duo-wc's task overview);
- brightness (both panels), only where /sys/class/backlight has the panels;
- Settings (Duo Settings), Screen off, Restart, Power off (the last two ask
  first). There is no pose switch: the two panels are one canvas, a window
  is carried to one panel, a half or both, and the poses come from the
  sensors;
- the notifications, newest first: swipe one sideways to dismiss it, tap it
  for its default action; "Clear all".

A long press on a tile with a Settings page (quickpages.py: WiFi, Bluetooth,
Hotspot, Mobile data -> SIM card ...) closes the menu and opens Duo Settings
on that page, also on a greyed tile (Mobile data without a SIM); a short
press is the tile's toggle.

Opened by a swipe down from the top edge (duo-wc's gesture, also over a
fullscreen app), dragging down on the status bar or tapping its status
icons, or by root's OpenQuickMenu on the system bus (shellbus.py: tests and
screenshots without a touch); it slides down on a spring. Closed by the arrow at the top, the
handle at the bottom, a swipe up from the bottom edge, Back (a side swipe),
or the screen going off; it slides back up.
"""

from gi.repository import Gio, GLib, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from . import (
    displaymodel,
    dndmodel,
    hapticsmodel,
    hotspot,
    hotspotmodel,
    laptopmodel,
    mobilemodel,
    penscroll,
    quickpages,
    system,
    timefmt,
    torchmodel,
    wc,
)
from .i18n import _, ngettext
from .notifications import card

CLOSE_MS = 170
COLUMNS = 3  # tiles in a row
TORCH_POLL_MS = 1000


def tile_cells(count, columns=COLUMNS):
    """(column, row) of each of `count` tiles, filled row by row."""
    return [(i % columns, i // columns) for i in range(count)]


def tile_grid(tiles, columns=COLUMNS):
    """The tiles in equal columns (tests/gtk_quick_tiles.py lays this out at
    a panel's width)."""
    grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
    for tile, (col, row) in zip(tiles, tile_cells(len(tiles), columns)):
        grid.attach(tile, col, row, 1, 1)
    return grid


def tile_at(grid, x, y):
    """The tile under (x, y) in the grid's coordinates, greyed ones too."""
    w = grid.pick(x, y, Gtk.PickFlags.INSENSITIVE)
    while w is not None and w is not grid:
        if isinstance(w, Tile):
            return w
        w = w.get_parent()
    return None


def hold_opens_pages(grid, on_hold):
    """A long press on a tile with a Settings page: on_hold(tile, page).
    The gesture sits on the grid, not on each tile: a greyed (insensitive)
    tile gets no events of its own, and Mobile data without a SIM should
    still lead to the SIM page. It claims the touch when it fires, which
    cancels the tile's click; on a tile without a page it steps aside and
    the press is a tap. GTK's long-press time, as the app grid's."""
    hold = Gtk.GestureLongPress()
    hold.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def pressed(gesture, x, y):
        tile = tile_at(grid, x, y)
        page = quickpages.page_for(tile.key) if tile is not None else None
        if page is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        tile.held_at = GLib.get_monotonic_time() / 1e6
        on_hold(tile, page)

    hold.connect("pressed", pressed)
    grid.add_controller(hold)
    return hold


def _wrapping(label):
    """A tile's line: wraps (between words, inside a word only when one
    word does not fit) to at most two lines, then ellipsizes."""
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_lines(2)
    label.set_ellipsize(Pango.EllipsizeMode.END)


class Tile(Gtk.Button):
    def __init__(self, icon, title, callback, key=None):
        super().__init__()
        self.key = key  # quickpages.TILE_PAGES
        self.held_at = None
        self.add_css_class("tile")
        self.set_can_focus(False)
        self.set_focus_on_click(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.image = Gtk.Image.new_from_icon_name(icon)
        self.image.set_pixel_size(22)
        # The labels sit on the icon's centre line: the text column is
        # centred in the tile (GtkButton gives its child the tile's whole
        # height, and the column used to start at its top), and an empty
        # second line takes no room.
        box.set_valign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        # the rest of the tile: a line wraps only where the tile is too narrow
        text.set_hexpand(True)
        self.title = Gtk.Label(label=title, xalign=0)
        self.title.add_css_class("title")
        _wrapping(self.title)
        self.sub = Gtk.Label(label="", xalign=0)
        self.sub.add_css_class("sub")
        _wrapping(self.sub)
        self.sub.set_visible(False)
        text.append(self.title)
        text.append(self.sub)
        box.append(self.image)
        box.append(text)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.connect("clicked", lambda _b: self._clicked(callback))

    def _clicked(self, callback):
        # the end of a long press that opened Settings is no tap
        held, self.held_at = self.held_at, None
        if quickpages.swallow_click(held, GLib.get_monotonic_time() / 1e6):
            return
        callback()

    def show(self, on, sub, icon=None):
        if on:
            self.add_css_class("on")
        else:
            self.remove_css_class("on")
        self.sub.set_label(sub)
        self.sub.set_visible(bool(sub))
        if icon:
            self.image.set_from_icon_name(icon)


class QuickMenu(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.add_css_class("duo-quick")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-quick")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        penscroll.install(self)  # the pen drags the lists like a finger
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("sheet")
        self.sheet = outer
        self.closing = None

        # Header: time and date, the close arrow. Dragging it up closes.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("header")
        self.time = Gtk.Label(xalign=0)
        self.time.add_css_class("time")
        self.date = Gtk.Label(xalign=0)
        self.date.add_css_class("date")
        self.date.set_hexpand(True)
        close = Gtk.Button()
        close.set_child(Gtk.Image.new_from_icon_name("pan-up-symbolic"))
        close.add_css_class("round")
        close.set_can_focus(False)
        close.set_tooltip_text(_("Close"))
        close.update_property([Gtk.AccessibleProperty.LABEL], [_("Close")])
        close.connect("clicked", lambda _b: self.close_menu())
        header.append(self.time)
        header.append(self.date)
        header.append(close)
        self._drag_closes(header)
        outer.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.add_css_class("content")

        self.wifi = Tile("network-wireless-symbolic", _("WiFi"), self.toggle_wifi, "wifi")
        self.bt = Tile("bluetooth-active-symbolic", _("Bluetooth"), self.toggle_bt, "bluetooth")
        self.rotate = Tile(
            "rotation-allowed-symbolic", _("Auto-rotate"), self.toggle_rotate, "rotate"
        )
        self.keyboard = Tile(
            "input-keyboard-symbolic", _("Keyboard"), self.toggle_keyboard, "keyboard"
        )
        self.gap = Tile("view-dual-symbolic", _("Hinge gap"), self.toggle_gap, "gap")
        self.windows = Tile("focus-windows-symbolic", _("Windows"), self.open_overview, "windows")
        # Which panels are used: both (the sensors decide), or one only.
        self.panels = Tile("video-display-symbolic", _("Panels"), self.toggle_panels, "panels")
        # Laptop mode (duo-wc: one panel a desk, the other a keyboard and a touchpad).
        self.laptop = Tile(laptopmodel.ICON, _("Laptop mode"), self.toggle_laptop, "laptop")
        # A display plugged in: a desk or the phone's mirror (displaymodel;
        # only while one is connected).
        self.display = Tile("video-display-symbolic", _("Display"), self.toggle_display, "display")
        self.display.set_visible(False)
        # Mobile data (duo2-wwan honours the switch; a locked SIM: its PIN).
        self.data = Tile("network-cellular-symbolic", _("Mobile data"), self.toggle_data, "data")
        # The hotspot (duo2-hotspot; its state file, watched while shown).
        self.hotspot = Tile(hotspotmodel.ICON, _("Hotspot"), self.toggle_hotspot, "hotspot")
        self._hotspot_error = None
        self._hotspot_watch = hotspot.Watch(lambda: self.get_visible() and self.refresh())
        # Airplane mode (the helper blocks WiFi + Bluetooth, duo2-wwan the modem).
        self.airplane = Tile(
            mobilemodel.AIRPLANE_ICON, _("Airplane mode"), self.toggle_airplane, "airplane"
        )
        # Location (the shell is geoclue's agent: off, no app learns where
        # the phone is, and the GNSS engine stays off).
        self.location = Tile(
            "location-services-active-symbolic", _("Location"), self.toggle_location, "location"
        )
        # The sound mode (feedbackd's profile and strength: the ringtone, the
        # message sounds, the vibrations; a tap is the next of the four).
        self.vibration = Tile(
            hapticsmodel.MODE_ICONS[hapticsmodel.SOUND_VIBRATION],
            _("Sound"),
            self.toggle_vibration,
            "sound",
        )
        # Do not disturb (dnd.py: GNOME's show-banners, Calls' ringer).
        self.dnd = Tile(
            "notifications-disabled-symbolic", _("Do not disturb"), self.toggle_dnd, "dnd"
        )
        # The flashlight (torchmodel.py: the camera's flash LED through the
        # kernel's LED class; read every second while the menu shows).
        self.torch = Tile(torchmodel.ICON_OFF, _("Flashlight"), self.toggle_torch, "torch")
        self._torch_led = torchmodel.find()
        self._torch_timer = None
        grid = tile_grid(
            (
                self.wifi,
                self.data,
                self.hotspot,
                self.bt,
                self.airplane,
                self.location,
                self.vibration,
                self.dnd,
                self.torch,
                self.rotate,
                self.keyboard,
                self.gap,
                self.panels,
                self.laptop,
                self.windows,
                self.display,
            )
        )
        # A long press: the tile's Settings page (quickpages.py).
        hold_opens_pages(grid, lambda _tile, page: self.open_settings(page))
        content.append(grid)

        self.brightness = None
        if system.has_backlight():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("slider-row")
            icon = Gtk.Image.new_from_icon_name("display-brightness-symbolic")
            icon.set_pixel_size(22)
            self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
            self.brightness.set_hexpand(True)
            self.brightness.set_draw_value(False)
            self.brightness.update_property([Gtk.AccessibleProperty.LABEL], [_("Brightness")])
            self.brightness.connect("value-changed", self._brightness_changed)
            row.append(icon)
            row.append(self.brightness)
            # Automatic brightness (duo-brightness): the slider then sets
            # the user's offset on the light curve.
            self.auto_brightness = Gtk.ToggleButton(label=_("Auto"))
            self.auto_brightness.add_css_class("flat")
            self.auto_brightness.set_tooltip_text(_("Automatic brightness"))
            self.auto_brightness.update_property(
                [Gtk.AccessibleProperty.LABEL], [_("Automatic brightness")]
            )
            self.auto_brightness.connect("toggled", self._auto_brightness_toggled)
            row.append(self.auto_brightness)
            content.append(row)
        self._brightness_timer = None
        self._brightness_quiet = False

        power = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        power.add_css_class("power")
        for icon, label, action in (
            ("emblem-system-symbolic", _("Settings"), self.open_settings),
            ("system-lock-screen-symbolic", _("Screen off"), self.screen_off),
            ("system-reboot-symbolic", _("Restart"), lambda: self.ask("reboot")),
            ("system-shutdown-symbolic", _("Power off"), lambda: self.ask("poweroff")),
        ):
            b = Gtk.Button()
            b.set_can_focus(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            # GtkButton gives its child the whole button: centre the pair.
            box.set_valign(Gtk.Align.CENTER)
            box.set_halign(Gtk.Align.CENTER)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(22)
            box.append(img)
            box.append(Gtk.Label(label=label))
            b.set_child(box)
            b.connect("clicked", lambda _b, a=action: a())
            power.append(b)
        content.append(power)

        # The confirmation for restart / power off.
        self.confirm = Gtk.Revealer()
        confirm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        confirm_box.add_css_class("confirm")
        self.confirm_label = Gtk.Label(xalign=0)
        self.confirm_label.set_hexpand(True)
        self.confirm_label.set_wrap(True)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.set_can_focus(False)
        cancel.connect("clicked", lambda _b: self.confirm.set_reveal_child(False))
        self.confirm_go = Gtk.Button()
        self.confirm_go.add_css_class("destructive")
        self.confirm_go.set_can_focus(False)
        self.confirm_go.connect("clicked", lambda _b: self.confirmed())
        confirm_box.append(self.confirm_label)
        confirm_box.append(cancel)
        confirm_box.append(self.confirm_go)
        self.confirm.set_child(confirm_box)
        self.pending = None
        content.append(self.confirm)

        # Notifications.
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.add_css_class("list-head")
        title = Gtk.Label(label=_("Notifications"), xalign=0)
        title.set_hexpand(True)
        self.clear = Gtk.Button(label=_("Clear all"))
        self.clear.set_can_focus(False)
        self.clear.connect("clicked", lambda _b: self.shell.notifications.clear())
        head.append(title)
        head.append(self.clear)
        content.append(head)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.append(self.list)
        self.empty = Gtk.Label(label=_("No notifications"))
        self.empty.add_css_class("empty")
        content.append(self.empty)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(content)
        outer.append(scroll)

        handle = Gtk.Button()
        handle.add_css_class("handle")
        handle.set_can_focus(False)
        handle.set_child(Gtk.Image.new_from_icon_name("pan-up-symbolic"))
        handle.set_tooltip_text(_("Close"))
        handle.connect("clicked", lambda _b: self.close_menu())
        self._drag_closes(handle)
        outer.append(handle)
        self.set_child(outer)

    def _drag_closes(self, widget):
        drag = Gtk.GestureDrag()
        drag.connect("drag-end", lambda _g, _dx, dy: dy < -40 and self.close_menu())
        widget.add_controller(drag)

    def connector(self):
        return self.monitor.get_connector()

    # -- open / close ---------------------------------------------------------------

    def open_menu(self):
        if self.closing is not None:
            GLib.source_remove(self.closing)
            self.closing = None
        self.sheet.remove_css_class("closing")
        self.confirm.set_reveal_child(False)
        self.refresh()
        self.refresh_notifications()
        self.present()
        if self._torch_timer is None:
            self._torch_timer = GLib.timeout_add(TORCH_POLL_MS, self._torch_poll)
        self.shell.quick_changed()

    def open_settings(self, page=None):
        """Duo Settings (shell/duo-settings) on this panel, like the app grid
        starts an app: the next window here, then a launch with a token (a
        running Settings comes forward). With a page (a tile's long press):
        `duo-settings PAGE`, the desktop file's program with the page, with
        the same launch context, as its own actions (Battery) do."""
        info = Gio.DesktopAppInfo.new("org.duo2os.Settings.desktop")
        if info is None:
            return
        app = info
        if page:
            cmd = quickpages.commandline(info.get_executable(), page)
            try:
                app = Gio.AppInfo.create_from_commandline(
                    cmd,
                    info.get_name(),
                    Gio.AppInfoCreateFlags.SUPPORTS_STARTUP_NOTIFICATION,
                )
            except (GLib.Error, TypeError) as e:
                print("duo-shell: Settings %s: %s" % (page, e), flush=True)
                app = info
        try:
            wc.request("expect_window", output=self.monitor.get_connector())
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: expect_window: %s" % e, flush=True)
        try:
            app.launch([], self.get_display().get_app_launch_context())
            if page:
                print("duo-shell: Settings on its %s page" % page, flush=True)
        except GLib.Error as e:
            print("duo-shell: Settings: %s" % e.message, flush=True)
        self.close_menu()

    def close_menu(self):
        """Slides up (CSS), then goes."""
        if self.get_visible() and self.closing is None:
            self.sheet.add_css_class("closing")
            self.closing = GLib.timeout_add(CLOSE_MS, self._closed)

    def _closed(self):
        self.closing = None
        if self._torch_timer is not None:
            GLib.source_remove(self._torch_timer)
            self._torch_timer = None
        self.sheet.remove_css_class("closing")
        self.set_visible(False)
        self.shell.quick_changed()
        return False

    def toggle(self):
        if self.get_visible() and self.closing is None:
            self.close_menu()
        else:
            self.open_menu()

    # -- what it shows ----------------------------------------------------------------

    def refresh(self):
        st = self.shell.state
        wcs = self.shell.wc_state or {}
        now = GLib.DateTime.new_now_local()
        self.time.set_label(now.format(timefmt.hm()) or "")
        # Translators: the quick menu's date, a GLib.DateTime format.
        self.date.set_label(now.format(timefmt.date_long()) or "")

        blocked = system.rfkill_blocked("wlan")
        wifi = st.get("wifi") or {}
        hs = hotspotmodel.read_state()
        if blocked is None:
            self.wifi.show(False, _("No WiFi device"))
        elif hotspotmodel.wifi_held(hs):
            self.wifi.show(False, _("Off (hotspot on)"), "network-wireless-disabled-symbolic")
        elif blocked:
            self.wifi.show(False, _("Off"), "network-wireless-disabled-symbolic")
        elif wifi.get("state") == "connected":
            self.wifi.show(True, wifi.get("ssid") or _("Connected"), "network-wireless-symbolic")
        else:
            self.wifi.show(True, _("Not connected"), "network-wireless-symbolic")

        bt = st.get("bluetooth")
        if bt is None:
            self.bt.show(False, _("No adapter"), "bluetooth-hardware-disabled-symbolic")
        else:
            self.bt.show(
                bt,
                _("On") if bt else _("Off"),
                "bluetooth-active-symbolic" if bt else "bluetooth-disabled-symbolic",
            )

        rot = system.read_auto_rotate()
        if rot == "on":
            self.rotate.show(True, _("On"), "rotation-allowed-symbolic")
        elif rot == "dry":
            self.rotate.show(False, _("Sensors only log"), "rotation-locked-symbolic")
        elif rot == "off":
            self.rotate.show(False, _("Locked"), "rotation-locked-symbolic")
        else:
            self.rotate.show(False, _("No duo-posed"), "rotation-locked-symbolic")

        agent = getattr(self.shell, "location", None)
        if agent is None:
            self.location.show(False, _("No geoclue"), "location-services-disabled-symbolic")
        elif agent.enabled:
            self.location.show(
                True, _("In use") if agent.in_use else _("On"), "location-services-active-symbolic"
            )
        else:
            self.location.show(False, _("Off"), "location-services-disabled-symbolic")

        haptics = getattr(self.shell, "haptics", None)
        mode = haptics.mode if haptics is not None else None
        if mode is None:
            self.vibration.show(False, _("No feedbackd"))
        else:
            labels = {
                hapticsmodel.SOUND_VIBRATION: _("Sound and vibration"),
                hapticsmodel.SOUND: _("Sound only"),
                hapticsmodel.VIBRATION: _("Vibration only"),
                hapticsmodel.SILENT: _("Silent"),
            }
            self.vibration.show(
                mode != hapticsmodel.SILENT, labels[mode], hapticsmodel.MODE_ICONS[mode]
            )

        dnd = getattr(self.shell, "dnd", None)
        if dnd is None or not dnd.available:
            self.dnd.show(False, _("Not available"))
        else:
            on, _sub = dndmodel.tile(dnd.on)
            self.dnd.show(
                on,
                _("On") if on else _("Off"),
                "notifications-disabled-symbolic"
                if on
                else "preferences-system-notifications-symbolic",
            )

        self.refresh_torch()

        kb = bool(st.get("keyboard"))
        self.keyboard.show(kb, _("Shown") if kb else _("Hidden"))

        gap = wcs.get("hinge_gap", "on") == "on"
        self.gap.show(gap, _("On") if gap else _("Off"))

        self.windows.show(bool(wcs.get("overview")), _("Overview"))

        sensitive, on, label = laptopmodel.tile(wcs)
        labels = {
            "On": _("On"),
            "Off": _("Off"),
            "Needs both panels": _("Needs both panels"),
            "Not available": _("Not available"),
        }
        self.laptop.show(on, labels.get(label, label))
        self.laptop.set_sensitive(sensitive)

        display = displaymodel.tile(wcs)
        self.display.set_visible(display is not None)
        if display is not None:
            on, label = display
            self.display.show(
                on, {"Desktop": _("Desktop"), "Mirror": _("Mirror")}.get(label, label)
            )

        airplane = bool(st.get("airplane"))
        sensitive, on, sub = mobilemodel.data_tile(st.get("mobile"), airplane)
        labels = {
            "No SIM": _("No SIM"),
            "SIM locked": _("SIM locked"),
            "Off": _("Off"),
            "On": _("On"),
            "Connected": _("Connected"),
            "Waiting for the network": _("Waiting for the network"),
            "Airplane mode": _("Airplane mode"),
        }
        self.data.show(
            on,
            labels.get(sub, sub),
            "network-cellular-symbolic" if sensitive else "network-cellular-disabled-symbolic",
        )
        self.data.set_sensitive(sensitive)
        sensitive, on, label, count = hotspotmodel.tile(hs, hotspotmodel.installed(), airplane)
        labels = {
            "Not installed": _("Not installed"),
            "Airplane mode": _("Airplane mode"),
            "Off": _("Off"),
            "On": _("On"),
            "No mobile data": _("No mobile data"),
            "devices": ngettext("%d device", "%d devices", count) % count,
        }
        sub = labels.get(label, label)
        if self._hotspot_error and not on:
            sub = self._hotspot_error
        self.hotspot.show(on, sub, hotspotmodel.ICON)
        self.hotspot.set_sensitive(sensitive)
        on, sub = mobilemodel.airplane_tile(airplane)
        self.airplane.show(
            on,
            _("On") if on else _("Off"),
            mobilemodel.AIRPLANE_ICON if on else "airplane-mode-disabled-symbolic",
        )

        panels = system.read_panels()
        labels = {"both": _("Both"), "left": _("Left only"), "right": _("Right only")}
        self.panels.show(panels in ("left", "right"), labels.get(panels, _("No duo-posed")))

        if self.brightness is not None:
            percent = system.backlight_percent()
            if percent is not None and self._brightness_timer is None:
                self._brightness_quiet = True
                self.brightness.set_value(percent)
                self._brightness_quiet = False
            auto = system.read_auto_brightness()
            self.auto_brightness.set_visible(auto is not None)
            self._brightness_quiet = True
            self.auto_brightness.set_active(bool(auto))
            self._brightness_quiet = False

    def refresh_torch(self):
        if self._torch_led is None:
            self._torch_led = torchmodel.find()  # the driver came later
        sensitive, on, label = torchmodel.tile(torchmodel.read(self._torch_led))
        labels = {
            "No flashlight": _("No flashlight"),
            "No access": _("No access"),
            "On": _("On"),
            "Off": _("Off"),
        }
        self.torch.show(on, labels[label], torchmodel.ICON_ON if on else torchmodel.ICON_OFF)
        self.torch.set_sensitive(sensitive)

    def _torch_poll(self):
        if not self.get_visible():
            self._torch_timer = None
            return False
        self.refresh_torch()
        return True

    def refresh_notifications(self):
        child = self.list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list.remove(child)
            child = nxt
        center = self.shell.notifications
        items = center.store.newest_first()
        for n in items:
            self.list.append(
                card(
                    n,
                    on_dismiss=lambda nid=n.id: center.dismiss(nid),
                    on_tap=lambda nid=n.id: self._tap_notification(nid),
                    on_action=lambda key, nid=n.id: center.invoke(nid, key),
                )
            )
        self.empty.set_visible(not items)
        self.clear.set_visible(bool(items))

    def _tap_notification(self, nid):
        n = self.shell.notifications.store.get(nid)
        if n is not None and n.has_default_action():
            self.shell.notifications.invoke(nid, "default")
            self.close_menu()

    # -- actions ----------------------------------------------------------------------

    def _wc(self, name, **args):
        try:
            return wc.request(name, **args)
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: %s: %s" % (name, e), flush=True)
            return None

    def toggle_wifi(self):
        blocked = system.rfkill_blocked("wlan")
        if blocked is None:
            return
        if hotspotmodel.wifi_held(hotspotmodel.read_state()):
            # The hotspot holds the WiFi client off: ask it back (beside the
            # hotspot, or the hotspot goes) instead of switching the radio.
            hotspot.run(["wifi-client", "on"], done=lambda _r: self.refresh())
            return
        system.helper("wifi", "on" if blocked else "off", done=lambda _ok: self.refresh())

    def toggle_bt(self):
        """BlueZ powers the adapter; one rfkill-blocked (airplane mode) is
        freed first (BlueZ refuses to power it while blocked)."""
        if self.shell.state.get("bluetooth"):
            self.shell.bluetooth.set_powered(False)
        elif system.rfkill_blocked("bluetooth"):
            system.helper(
                "bluetooth", "on", done=lambda _ok: self.shell.bluetooth.set_powered(True)
            )
        else:
            self.shell.bluetooth.set_powered(True)

    def toggle_hotspot(self, words=None):
        """On / off. When the hotspot cannot run beside the phone's WiFi
        connection and the user may switch it off for it, asks first."""
        words = words or hotspotmodel.toggle_words(hotspotmodel.read_state())
        self._hotspot_error = None
        if words[0] == "on":
            self.hotspot.show(True, _("Starting…"), hotspotmodel.ICON)

        def done(reply):
            if hotspotmodel.needs_wifi_off(reply):
                self.ask("hotspot-wifi-off")
            elif not reply.get("ok") and reply.get("code") != "not-confirmed":
                self._hotspot_error = _("Not started: %s") % (reply.get("error") or "?")
            self.refresh()

        hotspot.run(words, done=done)

    def toggle_dnd(self):
        dnd = getattr(self.shell, "dnd", None)
        if dnd is not None and dnd.available:
            dnd.toggle()  # dnd.py tells the shell; the tile follows

    def toggle_airplane(self):
        """Airplane mode on / off: the helper blocks (or frees again) WiFi and
        Bluetooth, duo2-wwan follows the switch file with the modem."""
        on = not system.read_airplane()

        def done(_ok):
            self.shell.airplane_changed()
            self.shell.bluetooth.refresh()

        system.helper("airplane", "on" if on else "off", done=done)

    def toggle_torch(self):
        state = torchmodel.read(self._torch_led)
        if state is not None and state["writable"]:
            torchmodel.write(self._torch_led, torchmodel.level(state, not torchmodel.is_on(state)))
        self.refresh_torch()

    def toggle_vibration(self):
        haptics = getattr(self.shell, "haptics", None)
        if haptics is not None:
            haptics.cycle()
        self.refresh()

    def toggle_location(self):
        agent = getattr(self.shell, "location", None)
        if agent is not None:
            agent.set_enabled(not agent.enabled)
        self.refresh()

    def toggle_rotate(self):
        rot = system.read_auto_rotate()
        if rot is None:
            return
        system.helper(
            "auto-rotate", "on" if rot == "off" else "off", done=lambda _ok: self.refresh()
        )

    def toggle_data(self):
        """Mobile data on / off (duo2-wwan watches the switch); a locked SIM
        asks for its PIN instead."""
        mobile = self.shell.state.get("mobile") or {}
        if mobile.get("state") == "locked":
            self.close_menu()
            self.shell.sim_prompt_check(self.monitor.get_connector(), force=True)
            return
        if not mobile.get("present"):
            return
        system.helper(
            "mobile-data",
            "off" if mobile.get("data") == "on" else "on",
            done=lambda _ok: self.refresh(),
        )

    def toggle_panels(self):
        """Both -> left only -> right only: duo-posed keeps to it (its
        `panels`), and the pose changes now (the shell's own request)."""
        current = system.read_panels()
        if current is None:
            return
        nxt = system.next_panels(current)
        pose = {
            "both": {"pose": "dual"},
            "left": {"pose": "single", "output": "DSI-1"},
            "right": {"pose": "single", "output": "DSI-2"},
        }[nxt]

        def done(_ok):
            self._wc("set_pose", **pose)
            self.refresh()

        system.helper("panels", nxt, done=done)

    def toggle_keyboard(self):
        self.shell.keyboard.set_visible(not self.shell.state.get("keyboard"))
        self.close_menu()

    def toggle_display(self):
        """A display: desk <-> mirror; the display changes, the menu stays."""
        switch = displaymodel.toggle(self.shell.wc_state)
        if switch is not None:
            request, args = switch
            self._wc(request, **args)

    def toggle_laptop(self):
        """Laptop mode on / off; the panels change under the menu: it closes."""
        self.close_menu()
        self._wc("set_laptop", laptop="toggle")

    def toggle_gap(self):
        gap = (self.shell.wc_state or {}).get("hinge_gap", "on")
        self._wc("set_hinge_gap", gap="off" if gap == "on" else "on")

    def open_overview(self):
        self.close_menu()
        self._wc("overview", action="open")

    def screen_off(self):
        self.close_menu()
        self._wc("set_power", output="all", power="off")

    def ask(self, what):
        self.pending = what
        if what == "reboot":
            self.confirm_label.set_label(_("Restart the phone now?"))
            self.confirm_go.set_label(_("Restart"))
        elif what == "hotspot-wifi-off":
            self.confirm_label.set_label(
                _(
                    "The hotspot cannot run beside this WiFi connection. "
                    "Switch WiFi off for it? (An ssh session over WiFi drops.)"
                )
            )
            self.confirm_go.set_label(_("Switch WiFi off"))
        else:
            self.confirm_label.set_label(_("Power off the phone now?"))
            self.confirm_go.set_label(_("Power off"))
        self.confirm.set_reveal_child(True)

    def confirmed(self):
        what, self.pending = self.pending, None
        self.confirm.set_reveal_child(False)
        if what in ("reboot", "poweroff"):
            print("duo-shell: %s (quick menu)" % what, flush=True)
            system.helper(what)
        elif what == "hotspot-wifi-off":
            self.toggle_hotspot(["on", "--wifi-off"])

    def _brightness_changed(self, scale):
        if self._brightness_quiet:
            return
        # At most a few writes a second while the finger moves.
        if self._brightness_timer is None:
            self._brightness_timer = GLib.timeout_add(120, self._brightness_write)

    def _auto_brightness_toggled(self, button):
        if self._brightness_quiet:
            return
        system.helper("auto-brightness", "on" if button.get_active() else "off")

    def _brightness_write(self):
        self._brightness_timer = None
        system.helper("brightness", int(self.brightness.get_value()))
        return False
