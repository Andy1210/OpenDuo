"""duo-shell: the status bars, app grid, quick menu, notifications, dock
and volume keys of duo2-os, one process.

One status bar per panel (GDK monitor = duo-wc output, by connector name);
an app grid, a quick menu, a notification banner and a dock per panel, made
when first needed. Everything the bars show is read here, on timers that
stop while the screen is dark (duo-wc's event stream says when), so the
shell costs nothing while the phone sleeps. The notification daemon
(org.freedesktop.Notifications) lives here too.
"""

import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import (  # noqa: E402
    ambient,
    apps,
    deskmodel,
    displaymodel,
    dockmodel,
    homemodel,
    keyboardmodel,
    laptopkeys,
    launchermodel,
    lockpad,
    lockstore,
    lockwatch,
    notifystore,
    oskpolicy,
    overviewlabels,
    overviewmodel,
    status,
    system,
    trust,
    wc,
)  # noqa: E402
from .androidstart import AndroidStart  # noqa: E402
from .bar import StatusBar  # noqa: E402
from .confirm import ConfirmSheet  # noqa: E402
from .dbus import Bluetooth, Keyboard  # noqa: E402
from .deskmenu import DeskMenu  # noqa: E402
from .displayprompt import DisplayPrompt  # noqa: E402
from .dock import Dock, app_index  # noqa: E402
from .home import HomeScreen  # noqa: E402
from .launcher import AppGrid, watch_apps  # noqa: E402
from .notifications import NotificationCenter  # noqa: E402
from .quickmenu import QuickMenu  # noqa: E402
from .safetimer import keep_running  # noqa: E402
from .simpin import SimPinDialog  # noqa: E402
from .taskbar import Taskbar  # noqa: E402
from .volumeosd import VolumeKeys, VolumeOsd  # noqa: E402
from .wallpaperview import WallpaperWatch  # noqa: E402
from .weather import Weather  # noqa: E402

APP_ID = "org.duo2os.Shell"
BATTERY_EVERY_S = 30
WIFI_EVERY_S = 10


def log(*args):
    print("duo-shell:", *args, flush=True)


class Shell:
    def __init__(self, app):
        self.app = app
        self.bars = {}
        self.grids = {}
        self.quicks = {}
        self.docks = {}
        self.overview_labels = {}
        # Laptop mode's key labels, by panel (laptopkeys).
        self.laptop_keys = {}
        # A desk's taskbar and app menu, by output (deskmodel).
        self.taskbars = {}
        self.desk_menus = {}
        # "Desktop or Mirror?" for a display plugged in, by panel (displayprompt).
        self.display_prompts = {}
        self.volume_osds = {}
        self.ambient_views = {}
        self.homes = {}
        self.confirms = {}
        self.lit = True
        self.timers = []
        self.wc_state = None
        self._app_index = None
        self.state = {
            "battery": (None, None),
            "wifi": {"state": None},
            "bluetooth": None,
            "keyboard": False,
            "osk_output": None,
            "grid_open_on": None,
            "overview": False,
            "notifications": [],
        }
        self.osk_history = oskpolicy.History()
        self.battery_path = status.find_battery()
        self.wifi_iface = status.find_wifi_iface()
        log("battery %s, wifi %s" % (self.battery_path, self.wifi_iface))

        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(os.path.dirname(__file__), "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        # The shell's own symbolic icons (icons/hicolor/symbolic/apps beside
        # the code: the flashlight, which Adwaita has none of; an icon straight
        # in a search path would not be recoloured as a symbolic one).
        Gtk.IconTheme.get_for_display(display).add_search_path(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
        )
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        self.monitors = display.get_monitors()
        self.monitors.connect("items-changed", lambda *_a: GLib.idle_add(self.sync_monitors))

        # The pen's top button (duo-wc's pen_button) and battery (pen.py).
        from .pen import PenBattery, PenButton

        self.pen_button = PenButton(self)
        self.pen_battery = PenBattery(self)
        self.bluetooth = Bluetooth(self.bluetooth_changed, self.pen_battery_changed)
        self.keyboard = Keyboard(self.keyboard_changed)
        self.apps_monitor = watch_apps(self.apps_changed)
        self.pinned = dockmodel.load_pinned(dockmodel.config_path(), set(self.app_index()[0]))
        self.home_layout = homemodel.load(
            homemodel.config_path(), ["DSI-1", "DSI-2"], set(self.app_index()[0])
        )
        self.weather = None
        self.weather = Weather(self.weather_changed)
        self.notifications = NotificationCenter(app, self, self.notifications_changed)
        # duo2-touchd's last-resort fallback (split: the outer edges out of
        # reach): a notification with "Try again" while its note exists.
        self.touch_nid = 0
        self.touch_monitor = None
        try:
            self.touch_monitor = Gio.File.new_for_path(system.TOUCH_FALLBACK).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
            self.touch_monitor.connect("changed", lambda *_a: self.touch_fallback_changed())
        except GLib.Error:
            pass
        GLib.timeout_add_seconds(
            3, self.touch_fallback_changed
        )  # a note from before the shell started
        self.volume_keys = VolumeKeys(self.volume_osd_for)
        # "Starting Android apps..." while an Android app waits for Android
        # to start (androidstart), and the longer launch hint it needs.
        self.android_start = AndroidStart(
            app, self.monitor_named, lambda: (self.wc_state or {}).get("windows")
        )
        # A volume key while a call rings aloud silences it (callsmodel.RingKeys).
        from .callsmodel import RingKeys

        self.ring_keys = RingKeys()
        # The mobile network (ModemManager): the bar's signal and operator,
        # the quick menu's data tile, the SIM PIN dialog.
        self.sim_dialogs = {}
        self.sim_later = False
        self.state["mobile"] = {"present": False}
        self.state["airplane"] = system.read_airplane()
        self.airplane_monitor = None
        try:
            self.airplane_monitor = Gio.File.new_for_path(system.AIRPLANE).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
            self.airplane_monitor.connect("changed", lambda *_a: self.airplane_changed())
        except GLib.Error:
            pass
        self.mobile = None
        try:
            from .mobile import Mobile

            self.mobile = Mobile(self.mobile_changed)
        except (ImportError, ValueError) as e:
            log("no ModemManager GI (%s): no mobile network in the shell" % e)

        self.wallpaper = None
        self.sync_monitors()
        self.wallpaper = WallpaperWatch(self)
        self.wallpaper.apply()
        self.read_battery()
        self.read_wifi()
        self.start_timers()

        # Vibration: the quick menu's switch, notifications vibrate (haptics.py).
        self.haptics = None
        try:
            from .haptics import Haptics

            self.haptics = Haptics(self.refresh_quicks)
        except (ImportError, ValueError) as e:
            log("no haptics (%s)" % e)

        # Do not disturb: the switch, Calls' ringer silenced (dnd.py); the
        # bar's icon and the quick menu's tile follow it.
        self.dnd = None
        try:
            from .dnd import Dnd

            self.dnd = Dnd(self.dnd_changed)
            self.state["dnd"] = self.dnd.on
        except (ImportError, ValueError) as e:
            log("no do not disturb (%s)" % e)

        # geoclue's agent: the Location switch, which apps may know where the
        # phone is (location.py).
        self.location = None
        try:
            from .location import LocationAgent

            self.location = LocationAgent(self, self.refresh_quicks, self.location_use_changed)
        except (ImportError, ValueError) as e:
            log("no location agent (%s)" % e)

        # The proximity sensor during calls on the earpiece (proximity.py).
        self.proximity = None
        try:
            from .proximity import Proximity

            self.proximity = Proximity(self)
        except (ImportError, ValueError) as e:
            log("no proximity handling (%s)" % e)

        # A Bluetooth or USB output that connects becomes the output
        # (audioroute.py, audioroutemodel.py).
        self.audio_route = None
        try:
            from .audioroute import AudioRoute

            self.audio_route = AudioRoute()
        except (ImportError, ValueError) as e:
            log("no audio routing (%s)" % e)

        # Incoming calls light the screen; the lock screen shows the call
        # (calls.py, callsmodel.py).
        self.lock_channel = None
        self.lock_watch = lockwatch.RelockWatch()
        GLib.timeout_add_seconds(2, self.watch_lock)
        self.calls = None
        try:
            from .calls import CallWatch

            self.calls = CallWatch(self)
        except (ImportError, ValueError, GLib.Error) as e:
            log("no call handling (%s)" % e)

        # Apps' "keep the screen on" (org.freedesktop.ScreenSaver: Chromium,
        # Firefox, GTK apps through the portal) as duo-wc's idle hold
        # (screensaver.py).
        self.screensaver = None
        try:
            from .screensaver import ScreenSaver

            self.screensaver = ScreenSaver(self)
        except (ImportError, ValueError, GLib.Error) as e:
            log("no screen saver service (%s)" % e)

        # The administrator's remote (root only, on the system bus): the
        # quick menu opened and closed without a touch (shellbus.py).
        self.shellbus = None
        try:
            from .shellbus import ShellBus

            self.shellbus = ShellBus(
                self.open_quick, self.close_quicks, lambda: list(self.bars), log
            )
        except (ImportError, ValueError, GLib.Error) as e:
            log("no system bus remote (%s)" % e)

        # The system's keyboard layouts, sent to duo-wc (keyboardmodel).
        self.input_sources = None
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(keyboardmodel.SCHEMA, True) is not None:
            self.input_sources = Gio.Settings.new(keyboardmodel.SCHEMA)
            self.input_sources.connect(
                "changed::" + keyboardmodel.KEY, lambda *_a: self.sync_keyboard_layouts()
            )

        self.events = wc.Subscription(self.on_event)
        self.events.start()
        # The Android clipboard (duo-clipboard-bridge), if Waydroid is there.
        self.clipboard_bridge = None
        start_clipboard_bridge(self)

    # -- panels ------------------------------------------------------------------

    def sync_monitors(self):
        present = {}
        for i in range(self.monitors.get_n_items()):
            monitor = self.monitors.get_item(i)
            name = monitor.get_connector()
            if name:
                present[name] = monitor
        for name in list(self.bars):
            if name not in present or self.bars[name].monitor is not present[name]:
                log("panel gone: %s" % name)
                self.bars.pop(name).destroy()
                for table in (
                    self.grids,
                    self.quicks,
                    self.docks,
                    self.overview_labels,
                    self.laptop_keys,
                    self.taskbars,
                    self.desk_menus,
                    self.display_prompts,
                    self.volume_osds,
                    self.ambient_views,
                    self.homes,
                    self.confirms,
                    self.sim_dialogs,
                ):
                    surface = table.pop(name, None)
                    if surface is not None:
                        surface.destroy()
                self.notifications.drop_monitor(name)
                self.android_start.drop_monitor(name)
        for name, monitor in sorted(present.items()):
            if name not in self.bars:
                log("status bar on %s" % name)
                bar = StatusBar(self.app, self, monitor)
                self.bars[name] = bar
                bar.update(self.state)
                bar.present()
            if name not in self.homes:
                if name not in self.home_layout:
                    self.home_layout[name] = [[]]
                home = HomeScreen(self.app, self, monitor)
                self.homes[name] = home
                home.present()
                if self.wallpaper is not None:
                    self.wallpaper.schedule(100)
            # The peek / glance surface, made now and kept: showing it is a
            # redraw (ambient.py), so the strip is there as the phone opens.
            self.ambient_for(name)
        # A display plugged in is a desk from its first frame.
        self.sync_desks()
        return False

    # -- the home screen -------------------------------------------------------------

    def set_home_layout(self, layout):
        """A new home layout (home.py, the app grid's "Add to home screen"):
        saved, and every home screen shows it."""
        self.home_layout = layout
        try:
            homemodel.save(homemodel.config_path(), layout)
        except OSError as e:
            log("home: cannot save the layout: %s" % e)
        for home in self.homes.values():
            home.populate()

    def add_to_home(self, desktop_id, name):
        self.set_home_layout(homemodel.add(self.home_layout, name, desktop_id))

    def weather_changed(self):
        for home in getattr(self, "homes", {}).values():
            home.update_widgets()

    def end_home_editing(self, name):
        home = self.homes.get(name)
        if home is not None and home.editing:
            home.set_editing(False)
            return True
        return False

    def grid_for(self, monitor):
        name = monitor.get_connector()
        grid = self.grids.get(name)
        if grid is None:
            grid = AppGrid(self.app, self, monitor)
            self.grids[name] = grid
        return grid

    def toggle_grid(self, monitor):
        self.grid_for(monitor).toggle()

    def open_grid_on(self, name):
        bar = self.bars.get(name)
        if bar is None:
            return
        grid = self.grid_for(bar.monitor)
        if not grid.get_visible() or grid.has_css_class("closing"):
            grid.open_grid()

    def grid_changed(self):
        open_on = [n for n, g in self.grids.items() if g.get_visible()]
        self.state["grid_open_on"] = open_on[0] if open_on else None
        self.refresh()
        self.sync_docks()

    def apps_changed(self):
        self._app_index = None
        for grid in self.grids.values():
            grid.populate()
        self.refresh_docks(force=True)
        for home in self.homes.values():
            home.populate()

    def question_sheet(self):
        """The question sheet on the panel the user looks at, for a question
        of the shell's own (ConfirmSheet.ask_question); None while the
        screen is dark."""
        st = self.wc_state or {}
        if not self.lit or not (st.get("lit") or []):
            return None
        name = self.focus_output()
        if name not in (st.get("lit") or []):
            name = st["lit"][0]
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        sheet = self.confirms.get(name)
        if sheet is None:
            sheet = ConfirmSheet(self.app, monitor)
            self.confirms[name] = sheet
        return sheet

    def monitor_named(self, name):
        bar = self.bars.get(name)
        return bar.monitor if bar is not None else None

    def focus_output(self):
        """Where the user is looking: the panel of the keyboard focus, else
        the active one, else the first."""
        st = self.wc_state or {}
        for name in (st.get("keyboard_output"), st.get("active_output")):
            if name in self.bars:
                return name
        return sorted(self.bars)[0] if self.bars else None

    # -- volume keys -----------------------------------------------------------------

    def volume_osd_for(self, name):
        """The volume display of the panel duo-wc named (the active one), or
        of the panel the user looks at if it named one the shell has no bar
        on."""
        if name not in self.bars:
            name = self.focus_output()
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        osd = self.volume_osds.get(name)
        if osd is None:
            osd = VolumeOsd(self.app, monitor)
            self.volume_osds[name] = osd
        return osd

    # -- quick menu ----------------------------------------------------------------

    def quick_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        quick = self.quicks.get(name)
        if quick is None:
            quick = QuickMenu(self.app, self, monitor)
            self.quicks[name] = quick
        return quick

    def open_quick(self, name):
        quick = self.quick_for(name)
        if quick is not None and (not quick.get_visible() or quick.closing is not None):
            quick.open_menu()

    def close_quicks(self):
        for quick in self.quicks.values():
            quick.close_menu()

    def toggle_quick(self, monitor):
        quick = self.quick_for(monitor.get_connector())
        if quick is not None:
            quick.toggle()

    def quick_changed(self):
        pass

    def location_use_changed(self):
        self.state["location_in_use"] = bool(self.location is not None and self.location.in_use)
        self.refresh()

    def refresh_quicks(self):
        for quick in self.quicks.values():
            if quick.get_visible():
                quick.refresh()

    def notifications_changed(self):
        self.state["notifications"] = self.notifications.store.bar_icons()
        self.refresh()
        for quick in self.quicks.values():
            if quick.get_visible():
                quick.refresh_notifications()

    def toggle_overview(self):
        try:
            wc.request("overview", action="toggle")
        except (OSError, RuntimeError, ValueError) as e:
            log("overview: %s" % e)

    # -- dock ------------------------------------------------------------------------

    def app_index(self):
        if self._app_index is None:
            self._app_index = app_index()
        return self._app_index

    def dock_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        dock = self.docks.get(name)
        if dock is None:
            dock = Dock(self.app, self, monitor)
            self.docks[name] = dock
        return dock

    def toggle_pin(self, desktop_id):
        self.pinned = dockmodel.toggle_pin(self.pinned, desktop_id)
        try:
            dockmodel.save_pinned(dockmodel.config_path(), self.pinned)
        except OSError as e:
            log("dock: cannot save the pinned apps: %s" % e)
        self.refresh_docks()
        return desktop_id in self.pinned

    def refresh_docks(self, force=False):
        for dock in self.docks.values():
            if dock.get_visible():
                dock.populate(force)
        for taskbar in self.taskbars.values():
            if taskbar.get_visible():
                taskbar.populate(force)

    def dock_used(self, dock):
        if dock.mode == "peek":
            dock.hide_dock()

    def sync_docks(self):
        """Which docks show, from duo-wc's state (dockmodel.wanted): the dock
        on every panel the overview is on; a peeking dock goes when its
        panel goes dark or a fullscreen window (a video) comes to its front."""
        st = self.wc_state or {}
        for name in self.bars:
            dock = self.docks.get(name)
            want = dockmodel.wanted(st, name)
            grid = self.grids.get(name)
            if want == "home" and grid is not None and grid.get_visible():
                want = None  # the app grid covers the home screen
            sim = self.sim_dialogs.get(name)
            if sim is not None and sim.get_visible():
                want = None  # the SIM PIN dialog is over everything
            taskbar = self.taskbars.get(name)
            if want == "taskbar":
                # A desk: its own taskbar (taskbar.py), not the phone's dock.
                if dock is not None and dock.mode is not None:
                    dock.hide_dock()
                taskbar = self.taskbar_for(name)
                if taskbar is not None and not taskbar.get_visible():
                    log("taskbar on %s" % name)
                    taskbar.update(self.state)
                    taskbar.populate(force=True)
                    taskbar.present()
                continue
            if taskbar is not None and taskbar.get_visible():
                log("taskbar on %s: away" % name)
                taskbar.set_visible(False)
                menu = self.desk_menus.get(name)
                if menu is not None:
                    menu.close_menu()
            if want is not None:
                dock = self.dock_for(name)
                if dock.mode != want:
                    dock.show_dock(want)
            elif dock is not None and dock.mode in ("taskbar", "overview", "home"):
                dock.hide_dock()
            elif dock is not None and dock.mode == "peek" and not dockmodel.may_peek(st, name):
                dock.hide_dock()
            elif (
                dock is not None and sim is not None and sim.get_visible() and dock.mode is not None
            ):
                dock.hide_dock()

    # -- the overview's card labels ---------------------------------------------------

    def labels_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        surface = self.overview_labels.get(name)
        if surface is None:
            surface = overviewlabels.OverviewLabels(self.app, monitor)
            self.overview_labels[name] = surface
        return surface

    def sync_overview_labels(self):
        """Titles and icons on duo-wc's overview cards (it draws no text)."""
        index, infos = self.app_index()
        names = {d: apps.info_label(i) for d, i in infos.items()}
        per_output = overviewmodel.labels(self.wc_state, index, names)
        overviewlabels.sync(self, per_output, self.labels_for)

    # -- laptop mode's keyboard panel ---------------------------------------------------

    def laptop_keys_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        surface = self.laptop_keys.get(name)
        if surface is None:
            surface = laptopkeys.LaptopKeys(self.app, monitor)
            self.laptop_keys[name] = surface
        return surface

    def sync_laptop(self):
        """The labels on laptop mode's keyboard panel (duo-wc draws the caps)."""
        laptopkeys.sync(self, self.wc_state, self.laptop_keys_for)

    # -- a desk: an external display, laptop mode's desk panel (deskmodel) -------------

    def sync_desks(self):
        """Each output's home surface and status bar as a desk or as a
        phone's panel: a desk shows the wallpaper and a taskbar (sync_docks),
        no status bar; a panel that stops being a desk gets its home screen
        and bar back as they were."""
        st = self.wc_state or {}
        for name, bar in self.bars.items():
            desk = deskmodel.is_desk(st, name)
            home = self.homes.get(name)
            if home is not None:
                home.set_desk(desk)
            if deskmodel.shows_bar(st, name):
                if not bar.get_visible():
                    log("status bar on %s: back" % name)
                    bar.update(self.state)
                    bar.present()
            elif bar.get_visible():
                log("status bar on %s: away (a desk)" % name)
                bar.set_visible(False)
            if desk:
                grid = self.grids.get(name)
                if grid is not None and grid.get_visible():
                    grid.close_grid()
            else:
                menu = self.desk_menus.get(name)
                if menu is not None:
                    menu.close_menu()
        self.sync_docks()

    def taskbar_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        taskbar = self.taskbars.get(name)
        if taskbar is None:
            taskbar = Taskbar(self.app, self, monitor)
            self.taskbars[name] = taskbar
        return taskbar

    def desk_menu_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        menu = self.desk_menus.get(name)
        if menu is None:
            menu = DeskMenu(self.app, self, monitor)
            self.desk_menus[name] = menu
        return menu

    def toggle_desk_menu(self, name):
        menu = self.desk_menu_for(name)
        if menu is None:
            return
        quick = self.quicks.get(name)
        if quick is not None and quick.get_visible():
            quick.close_menu()
        menu.toggle()

    def desk_menu_changed(self):
        pass

    def expect_window(self, output, desktop_id=None):
        """Tell duo-wc that the window of the app about to be launched
        (`desktop_id`) opens on `output`. An Android app launched while
        Android is not running gets the "Starting Android apps" notice there
        and a hint that waits for Android to start (androidstart)."""
        args = {}
        ms = self.android_start.launching(output, desktop_id)
        if ms:
            args["timeout_ms"] = ms
        try:
            wc.request("expect_window", output=output, **args)
        except (OSError, RuntimeError, ValueError) as e:
            log("expect_window: %s" % e)

    def desk_launch(self, name, info, display):
        """An app started from a desk (its taskbar, its app menu): its
        window opens on that desk."""
        if info is None:
            return
        self.expect_window(name, info.get_id())
        try:
            info.launch([], display.get_app_launch_context())
            log("desk %s launched %s" % (name, info.get_id()))
        except GLib.Error as e:
            log("launching %s failed: %s" % (info.get_id(), e.message))

    def desk_settings(self, name, action, display):
        """Duo Settings on a desk, at a page (`action`: the desktop file's
        action, wallpaper) or its first."""
        info = Gio.DesktopAppInfo.new("org.duo2os.Settings.desktop")
        if info is None:
            return
        try:
            wc.request("expect_window", output=name)
        except (OSError, RuntimeError, ValueError) as e:
            log("expect_window: %s" % e)
        context = display.get_app_launch_context()
        try:
            if action and action in (info.list_actions() or []):
                info.launch_action(action, context)
            else:
                info.launch([], context)
        except GLib.Error as e:
            log("Settings: %s" % e.message)

    def sync_display_prompt(self):
        """The question about a display plugged in (displaymodel.asking), on
        the panel in use; gone once answered (here, in the quick menu, by
        duoctl) or unplugged."""
        st = self.wc_state or {}
        display = displaymodel.asking(st)
        panel = displaymodel.prompt_panel(st) if display is not None else None
        for prompt in self.display_prompts.values():
            if prompt.get_visible() and (display is None or prompt.output != display.get("output")):
                prompt.close_prompt()
        if display is None or panel is None:
            return
        if any(
            p.get_visible() and p.output == display.get("output")
            for p in self.display_prompts.values()
        ):
            return
        monitor = self.monitor_named(panel)
        if monitor is None:
            return
        prompt = self.display_prompts.get(panel)
        if prompt is None:
            prompt = DisplayPrompt(self.app, self, monitor)
            self.display_prompts[panel] = prompt
        log("display %s plugged in: asking on %s" % (display.get("output"), panel))
        prompt.ask(display)

    def leave_laptop(self):
        try:
            wc.request("set_laptop", laptop="off")
        except (OSError, RuntimeError, ValueError) as e:
            log("leave laptop mode: %s" % e)

    # -- the closed phone: peek / glance strips, the lock ----------------------------

    def ambient_for(self, name):
        monitor = self.monitor_named(name)
        if monitor is None:
            return None
        view = self.ambient_views.get(name)
        if view is None:
            view = ambient.AmbientView(self.app, monitor)
            self.ambient_views[name] = view
        return view

    def sync_ambient(self):
        """duo-wc shows the peek or glance screen: the strips (ambient.py),
        with the battery read now (the bars' timers stop during it) and the
        clock kept by a minute timer of its own."""
        st = self.wc_state or {}
        if st.get("ambient"):
            self.state["battery"] = status.read_battery(self.battery_path)
        ambient.sync(self, st, self.ambient_for)
        if any(v.shown for v in self.ambient_views.values()):
            if getattr(self, "ambient_tick", None) is None:
                now = GLib.DateTime.new_now_local()
                self.ambient_tick = GLib.timeout_add(
                    (60 - now.get_second()) * 1000 - now.get_microsecond() // 1000 + 50,
                    self._ambient_minute,
                )
        elif getattr(self, "ambient_tick", None) is not None:
            GLib.source_remove(self.ambient_tick)
            self.ambient_tick = None

    def _ambient_minute(self):
        self.ambient_tick = None
        self.sync_ambient()
        return False

    def posture_changed(self, st):
        """duo-posed told duo-wc how the phone is folded: folded closed, it
        locks (lockstore.lock_on_close, on by default; a swipe unlocks)."""
        if st.get("posture") == "closed" and lockstore.lock_on_close(lockstore.read_policy()):
            self.lock_now("folded closed")

    # -- what the bars show --------------------------------------------------------

    # -- the mobile network, the SIM PIN --------------------------------------------

    def touch_fallback_changed(self):
        on = os.path.exists(system.TOUCH_FALLBACK)
        if on and not self.touch_nid:
            log("touch: duo2-touchd fell back to its split mode")
            self.touch_nid = self.notifications.post(
                "Touch screen in basic mode",
                "The screen edges do not respond to touch. Try again restarts the touch driver.",
                actions=("retry", "Try again"),
                on_action=lambda _key: system.helper("touch", "retry"),
                icon="input-touchscreen-symbolic",
                hints={"resident": True, "urgency": notifystore.URGENCY_CRITICAL},
            )
        elif not on and self.touch_nid:
            self.notifications.close(self.touch_nid, notifystore.CLOSED_BY_CALL)
            self.touch_nid = 0
        return False

    def airplane_changed(self):
        airplane = system.read_airplane()
        if airplane != self.state.get("airplane"):
            self.state["airplane"] = airplane
            log("airplane mode %s" % ("on" if airplane else "off"))
            self.refresh()

    def dnd_changed(self):
        self.state["dnd"] = self.dnd.on
        self.refresh()

    def mobile_changed(self):
        if self.mobile is None:
            return
        self.state["mobile"] = self.mobile.info()
        self.refresh()
        self.sim_prompt_check()
        if self.lock_channel is not None:
            # The lock's emergency numbers follow the SIM and the network.
            self.call_card_changed()

    def emergency_context(self):
        if self.mobile is None:
            return {"sim_present": False}
        return self.mobile.emergency_context()

    def dial_emergency(self, number, why):
        """An emergency call (the lock screen's emergency keypad, the SIM PIN
        dialog's): the number checked once more, then ModemManager
        (mobile.Mobile.dial; telephony/README.md "Emergency calls")."""
        from . import emergencymodel

        if not emergencymodel.dialable(number, self.emergency_context()):
            log("emergency call to %s refused: not an emergency number here" % number)
            return False
        if self.mobile is None:
            log("emergency call to %s: no ModemManager GI" % number)
            return False
        log("emergency call to %s (%s)" % (number, why))
        self.mobile.dial(number)
        return True

    def sim_prompt_check(self, output=None, force=False):
        """The SIM waits for its PIN or PUK: the dialog on the active panel
        (or `output`), unless the screen is off, the session is locked (the
        lock screen first: duo-wc shows nothing of the shell over it) or the
        user said "Not now" since the screen came on (`force`: the bar's or
        the quick menu's SIM tap)."""
        from . import mobilemodel

        request = mobilemodel.pin_request(self.state.get("mobile"))
        st = self.wc_state or {}
        if request is None:
            shown = any(d.get_visible() for d in self.sim_dialogs.values())
            for dialog in self.sim_dialogs.values():
                dialog.hide_dialog()
            self.sim_later = False
            if shown:
                self.sync_docks()
            return
        if force:
            self.sim_later = False
        if not self.lit or st.get("locked") or self.sim_later:
            return
        name = output or st.get("active_output") or next(iter(sorted(self.bars)), None)
        monitor = self.monitor_named(name)
        if monitor is None:
            return
        for other, dialog in self.sim_dialogs.items():
            if other != name:
                dialog.hide_dialog()
        dialog = self.sim_dialogs.get(name)
        if dialog is None:
            dialog = SimPinDialog(self.app, self, monitor)
            self.sim_dialogs[name] = dialog
        dialog.show_request(request)
        self.sync_docks()

    def sim_prompt_later(self):
        """ "Not now": hidden until the screen comes on again (or the SIM
        icon is tapped)."""
        self.sim_later = True
        for dialog in self.sim_dialogs.values():
            dialog.hide_dialog()
        self.sync_docks()

    def refresh(self):
        for bar in self.bars.values():
            bar.update(self.state)
        for taskbar in self.taskbars.values():
            if taskbar.get_visible():
                taskbar.update(self.state)
        for home in self.homes.values():
            home.update_widgets()
        self.refresh_quicks()
        if any(v.shown for v in self.ambient_views.values()):
            self.sync_ambient()  # the clock, the battery

    @keep_running
    def read_battery(self):
        self.state["battery"] = status.read_battery(self.battery_path)
        self.refresh()
        return True

    @keep_running
    def read_wifi(self):
        self.state["wifi"] = status.read_wifi(self.wifi_iface)
        self.refresh()
        return True

    def tick_clock(self):
        try:
            self.refresh()
        finally:
            # Again at the start of the next minute, whatever refresh did:
            # an exception there must not stop the clock for good.
            now = GLib.DateTime.new_now_local()
            self.timers = [t for t in self.timers if t != self.clock_timer]
            self.clock_timer = GLib.timeout_add(
                (60 - now.get_second()) * 1000 - now.get_microsecond() // 1000 + 50,
                self.tick_clock,
            )
            self.timers.append(self.clock_timer)
        return False

    def start_timers(self):
        self.stop_timers()
        self.timers = [
            GLib.timeout_add_seconds(BATTERY_EVERY_S, self.read_battery),
            GLib.timeout_add_seconds(WIFI_EVERY_S, self.read_wifi),
        ]
        self.clock_timer = None
        self.tick_clock()

    def stop_timers(self):
        for timer in self.timers:
            GLib.source_remove(timer)
        self.timers = []
        self.clock_timer = None

    def bluetooth_changed(self):
        self.state["bluetooth"] = self.bluetooth.powered
        self.refresh()

    def pen_battery_changed(self, pens):
        if getattr(self, "notifications", None) is None:
            # BlueZ answered before the notification center is up (at start).
            GLib.timeout_add(500, lambda: self.pen_battery_changed(pens) and False)
            return
        self.pen_battery.update(pens)

    def keyboard_changed(self):
        self.state["keyboard"] = bool(self.keyboard.visible)
        self.note_osk()
        self.refresh()

    def toggle_keyboard(self, connector):
        """The bar's keyboard button: hides the keyboard if it is shown on
        this panel, otherwise shows it (duo-wc puts it on the panel of the
        keyboard focus, which the tap on this bar just moved here)."""
        hide = oskpolicy.button_hides(self.osk_history, GLib.get_monotonic_time() / 1e6, connector)
        if not hide:
            # The keyboard comes up on THIS panel: it goes where the focus is.
            try:
                wc.request("focus_output", output=connector)
            except (OSError, RuntimeError, ValueError) as e:
                log("focus_output %s: %s" % (connector, e))
        self.keyboard.set_visible(not hide)

    # -- the keyboard layouts (keyboardmodel) -------------------------------------------

    def keyboard_sources(self):
        if self.input_sources is None:
            return []
        return self.input_sources.get_value(keyboardmodel.KEY).unpack()

    def sync_keyboard_layouts(self):
        """The system's layouts to duo-wc (a plugged keyboard, laptop mode)."""
        sources = self.keyboard_sources()
        if not keyboardmodel.needs_push(sources, self.wc_state):
            return
        args = keyboardmodel.request_args(sources)
        try:
            wc.request("set_keyboard", **args)
            log("keyboard layouts to duo-wc: %s" % args["layouts"])
        except (OSError, RuntimeError, ValueError) as e:
            log("set_keyboard %s: %s" % (args, e))

    def keyboard_layout_added(self, message):
        """A layout picked on laptop mode's keyboard: into the system's list."""
        if self.input_sources is None:
            return
        new = keyboardmodel.with_added(
            self.keyboard_sources(), message.get("layout"), message.get("variant") or ""
        )
        if new is not None:
            self.input_sources.set_value(keyboardmodel.KEY, GLib.Variant("a(ss)", new))
            log("keyboard layout added to the system's list: %s" % message.get("layout"))

    # -- duo-wc's events ------------------------------------------------------------

    def on_event(self, message):
        if "ok" in message and isinstance(message["ok"], dict) and "state" in message["ok"]:
            st = message["ok"]["state"]
            self.wc_state = st
            # duo-wc came up LOCKED (it was locked when it went down, black
            # until a lock client takes over): that is us, first of all.
            if st.get("locked"):
                self.restore_lock()
            elif os.path.exists(lockpad.HOME_LOCKED):
                # The home is encrypted and its key not given yet (homecrypt/):
                # the first unlock is the lock screen's PIN.
                self.lock_now("the encrypted home waits for its PIN")
            self.screen_state(st)
            self.osk_state(st)
            self.state["overview"] = bool(st.get("overview"))
            self.sync_desks()
            self.sync_display_prompt()
            self.sync_overview_labels()
            self.sync_laptop()
            self.sync_ambient()
            self.sync_keyboard_layouts()
            self.refresh()
            if self.proximity is not None:
                self.proximity.wc_changed()
            if self.screensaver is not None:
                self.screensaver.wc_connected(st)
            return
        event = message.get("event")
        if event == "state":
            st = message["state"]
            self.wc_state = st
            changed = message.get("changed", [])
            if self.screensaver is not None and ("power" in changed or "locked" in changed):
                self.screensaver.wc_state(st)
            # The peek / glance strip first: the phone is being opened, the
            # panel is waking, nothing else is seen (and the bars' screen-on
            # work waits until a real screen is on, screen_state).
            if any(c in changed for c in ("ambient", "power", "rotation")):
                self.sync_ambient()
            if "locked" in changed and not st.get("locked"):
                self.lock_ended()
                self.sim_prompt_check()
            if "locked" in changed and st.get("locked"):
                # duo-wc locked by the rules itself (a fold, a blank: it
                # does not wait for us): duo-lock takes the lock over.
                self.restore_lock()
            if "power" in changed or "ambient" in changed:
                self.screen_state(st)
            if "osk" in changed:
                self.osk_state(st)
            if "overview" in changed:
                self.state["overview"] = bool(st.get("overview"))
                if not st.get("overview"):
                    for sheet in self.confirms.values():
                        if (
                            sheet.confirm_id is None and sheet.callback is None
                        ):  # a force-close question
                            sheet.close_sheet()
                if st.get("overview"):
                    # The overview covers the panels: a grid or a quick menu
                    # would sit under it.
                    for grid in self.grids.values():
                        grid.close_grid()
                    for quick in self.quicks.values():
                        quick.close_menu()
                    for menu in self.desk_menus.values():
                        menu.close_menu()
                self.refresh()
            if any(
                c in changed for c in ("pose", "power", "overview", "fullscreen", "home", "laptop")
            ):
                self.sync_docks()
            if "laptop" in changed:
                self.sync_laptop()
                self.sync_desks()
                self.refresh_docks(force=True)
            if "displays" in changed:
                # A display plugged, unplugged, or switched (desk / mirror).
                self.sync_desks()
                self.sync_display_prompt()
                self.refresh_quicks()
            if any(c in changed for c in ("overview", "windows", "power")):
                self.sync_overview_labels()
            if "windows" in changed:
                self.refresh_docks()
            if any(c in changed for c in ("pose", "hinge_gap", "overview")):
                self.refresh_quicks()
            if any(c in changed for c in ("pose", "hinge_gap", "rotation")) and self.wallpaper:
                # The home screens cut their part of the picture as they
                # draw: the layout is all they need (and a new size fetches
                # it by itself, before its first frame).
                self.wallpaper.layout_changed()
            if "posture" in changed:
                self.posture_changed(st)
            if self.proximity is not None and any(
                c in changed for c in ("windows", "power", "proximity", "pose")
            ):
                self.proximity.wc_changed()
        elif event == "edge_swipe":
            self.edge_swipe(message)
        elif event == "window_front":
            self.window_front(message)
        elif event == "force_close_request":
            self.ask_force_close(message)
        elif event == "confirm_request":
            self.ask_confirm(message)
        elif event == "confirm_closed":
            for sheet in self.confirms.values():
                sheet.closed_elsewhere(message.get("id"))
        elif event == "volume_key":
            # Never delivered to an app: the volume is the shell's. No panel
            # named = the screen is dark: change it, show nothing. While a
            # call rings aloud a press silences it instead (the volume stays).
            key, pressed = message.get("key"), message.get("state") == "pressed"
            if self.ring_keys.takes(key, pressed, self.calls is not None and self.calls.loud()):
                if pressed:
                    self.calls.silence("volume key")
                return
            self.volume_keys.key_event(key, pressed, message.get("output"))
        elif event == "pen_button":
            # The pen's top button (Bluetooth): never an app's key. What it
            # does is Duo Settings > Pen's (pen.conf, penmodel.py).
            self.pen_button.pressed(message)
        elif event == "keyboard_layout_added":
            self.keyboard_layout_added(message)
        elif event == "ring_silence":
            # duo-wc's power key, pressed while a call rang aloud: silenced
            # (the screen stayed as it was; duo-wc's ring is silent now).
            if self.calls is not None:
                self.calls.silence("power key")

    def edge_swipe(self, message):
        """duo-wc's edge gestures (see the README's gesture map)."""
        edge = message.get("edge")
        kind = message.get("kind", "long")
        output = message.get("output")
        quick = self.quicks.get(output)
        shade = quick is not None and quick.get_visible()
        if edge == "top":
            log("swipe down on %s: quick menu" % output)
            self.open_quick(output)
            return
        if edge in ("left", "right"):
            if message.get("to") in ("shell", "none"):
                self.back_on(output)
            return
        if edge != "bottom":
            return
        if shade:
            # The quick menu is pulled down: a swipe up closes it.
            log("swipe up (%s) on %s: quick menu closes" % (kind, output))
            quick.close_menu()
            return
        if kind == "hold":
            return  # duo-wc opened its task overview (the docks follow its state)
        if kind == "home":
            # duo-wc set the panel's windows aside: the home screen shows.
            grid = self.grids.get(output)
            if grid is not None:
                grid.close_grid()
            log("swipe up (long) on %s: home" % output)
            return
        if kind == "short":
            if not dockmodel.may_peek(self.wc_state, output):
                log("swipe up (short) on %s: no dock over a fullscreen window" % output)
                return
            dock = self.dock_for(output)
            if dock is not None and dock.mode is None:
                log("swipe up (short) on %s: dock" % output)
                dock.show_dock("peek")
            return
        if deskmodel.is_desk(self.wc_state, output):
            # A desk has the app menu, not the phone's app grid.
            log("swipe up (%s) on %s: the desk's app menu" % (kind, output))
            self.toggle_desk_menu(output)
            return
        # A long swipe on a panel that shows home already: the app grid.
        log("swipe up (%s) on %s: app grid" % (kind, output))
        self.open_grid_on(output)

    def window_front(self, message):
        """A window came to the front by itself (a new one, an app's
        activation): the app grid and the quick menu on its panels make
        way. They are layer surfaces above the windows, and a window that
        opened on a panel with the grid open sat under it (the owner,
        2026-11-12: Text Editor under the app grid)."""
        for output in launchermodel.front_outputs(message):
            menu = self.desk_menus.get(output)
            if menu is not None and menu.get_visible():
                log("window %s in front on %s: app menu closes" % (message.get("window"), output))
                menu.close_menu()
            grid = self.grids.get(output)
            if grid is not None and grid.get_visible():
                log("window %s in front on %s: app grid closes" % (message.get("window"), output))
                grid.close_grid()
            quick = self.quicks.get(output)
            if quick is not None and quick.get_visible():
                log("window %s in front on %s: quick menu closes" % (message.get("window"), output))
                quick.close_menu()

    def ask_force_close(self, message):
        """An overview card's force-close button: ask first (unsaved data is
        lost), on that panel."""
        name = message.get("output")
        monitor = self.monitor_named(name)
        if monitor is None:
            return
        sheet = self.confirms.get(name)
        if sheet is None:
            sheet = ConfirmSheet(self.app, monitor)
            self.confirms[name] = sheet
        _index, infos = self.app_index()
        desktop_id = dockmodel.match_app(message.get("app_id"), _index)
        info = infos.get(desktop_id) if desktop_id else None
        label = apps.info_label(info, message.get("title") or message.get("app_id"))
        sheet.ask(message.get("window"), label)

    def ask_confirm(self, message):
        """duo-wc asks the user before an action only the session may take
        (Duo Settings' restart of the display): the question on the active
        panel; only its yes lets duo-wc do it."""
        name = message.get("output") or next(iter(sorted(self.bars)), None)
        monitor = self.monitor_named(name)
        if monitor is None:
            return
        sheet = self.confirms.get(name)
        if sheet is None:
            sheet = ConfirmSheet(self.app, monitor)
            self.confirms[name] = sheet
        log("duo-wc asks the user: %s (%s)" % (message.get("action"), message.get("id")))
        sheet.ask_action(message.get("id"), message.get("action"), message.get("detail"))

    def back_on(self, output):
        """Back on a panel where a shell surface is up: the topmost closes
        (the SIM PIN dialog: "Not now"; the quick menu, the app grid, the
        home screen's edit mode)."""
        sim = self.sim_dialogs.get(output)
        if sim is not None and sim.get_visible():
            self.sim_prompt_later()
            return
        quick = self.quicks.get(output)
        if quick is not None and quick.get_visible():
            quick.close_menu()
            return
        grid = self.grids.get(output)
        if grid is not None and grid.get_visible():
            grid.close_grid()
            return
        self.end_home_editing(output)

    def wake_for_call(self):
        """A call rings in: the screen lights by itself (Calls' window, or
        the lock screen's call card), as a phone does; a closed phone shows
        the glance strip."""
        self.wake_for_alarm("incoming call")

    def incoming_caller_changed(self, caller):
        """Who calls (callsmodel.strip_caller), or None once no call rings:
        the glance and peek strips say it while it rings, the clock again
        after. Never logged."""
        self.state["incoming_call"] = caller
        if (self.wc_state or {}).get("ambient"):
            self.sync_ambient()

    def call_card_changed(self):
        """The call the lock screen shows changed (or the speaker did), or
        the emergency numbers it may dial."""
        if self.lock_channel is not None and self.calls is not None:
            from . import emergencymodel

            message = dict(self.calls.message())
            message["emergency"] = emergencymodel.offer(self.emergency_context())
            self.lock_channel.send(message)

    def lock_action(self, line):
        """A button on the lock screen's call card, or its emergency keypad
        (calls.LockChannel)."""
        if self.calls is None:
            return
        from . import callsmodel, emergencymodel

        number = emergencymodel.parse_request(line, self.emergency_context())
        if number is not None:
            self.dial_emergency(number, "lock screen")
            return
        parsed = callsmodel.parse_action(line, set(self.calls.calls))
        if parsed is not None:
            self.calls.act(*parsed)

    def start_lock(self):
        """duo-lock, with the channel for its call card; the card that is
        up now goes to it at once."""
        channel = None
        if self.calls is not None:
            from .calls import LockChannel

            try:
                channel = LockChannel(self.lock_action)
            except OSError as e:
                log("no call card on the lock screen: %s" % e)
        proc = start_lock(channel)
        if channel is not None:
            channel.child_started()
            if proc is None:
                channel.close()
                channel = None
        if self.lock_channel is not None:
            self.lock_channel.close()
        self.lock_channel = channel
        self.call_card_changed()
        return proc

    def open_alarms(self, output):
        """A tap on a clock: GNOME Clocks on its Alarms page, on `output`
        (alarmsmodel.py says how)."""
        from . import alarmsmodel

        windows = (self.wc_state or {}).get("windows")
        ringing = any(
            n.urgency == notifystore.URGENCY_CRITICAL
            and (n.desktop_entry or "").lower() in notifystore.ALARM_APPS
            for n in self.notifications.store.items
        )
        step = alarmsmodel.plan(windows, ringing)
        log("clock tapped on %s: Clocks' alarms (%s)" % (output, step))
        if step == "front":
            try:
                wc.request("activate_window", app_id=alarmsmodel.APP_ID)
            except (OSError, RuntimeError, ValueError) as e:
                log("activate Clocks: %s" % e)
            return
        if step == "close":
            try:
                wc.request("close_window", app_id=alarmsmodel.APP_ID)
            except (OSError, RuntimeError, ValueError) as e:
                log("close Clocks' window: %s" % e)
                return
            deadline = GLib.get_monotonic_time() + int(alarmsmodel.CLOSE_WAIT_S * 1e6)

            def closed():
                if alarmsmodel.clocks_windows((self.wc_state or {}).get("windows")):
                    if GLib.get_monotonic_time() < deadline:
                        return True
                    log("Clocks' window did not close: left as it is")
                    return False
                GLib.timeout_add(
                    int(alarmsmodel.SETTLE_S * 1000), lambda: self.launch_alarms(output) and False
                )
                return False

            GLib.timeout_add(100, closed)
            return
        self.launch_alarms(output)

    def launch_alarms(self, output):
        from . import alarmsmodel

        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(alarmsmodel.STATE_SCHEMA, True) is not None:
            settings = Gio.Settings.new(alarmsmodel.STATE_SCHEMA)
            settings.set_string(alarmsmodel.PAGE_KEY, alarmsmodel.PAGE)
            Gio.Settings.sync()
        try:
            info = Gio.DesktopAppInfo.new(alarmsmodel.DESKTOP_ID)
        except TypeError:
            info = None
        if info is None:
            log("no %s: Clocks is not installed" % alarmsmodel.DESKTOP_ID)
            return
        try:
            wc.request("expect_window", output=output)
        except (OSError, RuntimeError, ValueError) as e:
            log("expect_window: %s" % e)
        monitor = self.monitor_named(output)
        display = monitor.get_display() if monitor is not None else Gdk.Display.get_default()
        try:
            info.launch([], display.get_app_launch_context())
        except GLib.Error as e:
            log("cannot start Clocks: %s" % e.message)

    def wake_for_alarm(self, why):
        """An urgent notification (an alarm, a critical one) lights the
        screen, as a phone does; a closed phone shows the glance strip."""
        st = self.wc_state or {}
        if st.get("lit") and not st.get("ambient"):
            return
        try:
            if st.get("posture") == "closed":
                log("%s: the phone is closed: glance" % why)
                wc.request("ambient", action="glance")
            else:
                log("%s: waking the screen" % why)
                wc.request("set_power", output="all", power="on")
        except (OSError, RuntimeError, ValueError) as e:
            log("wake: %s" % e)

    def osk_state(self, st):
        self.state["osk_output"] = st.get("osk_output")
        # Put away by duo-wc (a tap beside the text field, its oskleave.rs):
        # the keyboard still says visible, but nothing shows; the button
        # brings it back (focus_output) rather than hiding it.
        self.state["osk_away"] = bool(st.get("osk_away"))
        self.note_osk()
        self.refresh()

    def note_osk(self):
        shown = bool(self.keyboard.visible) and not self.state.get("osk_away")
        self.osk_history.note(GLib.get_monotonic_time() / 1e6, shown, self.state.get("osk_output"))

    def screen_state(self, st):
        # An ambient screen (peek, glance) is not the screen on: only its
        # strip shows, the bars, the home and their timers wait.
        # A panel the proximity sensor holds dark during a call (at an ear)
        # is not "the screen off": no lock, the bars keep their timers.
        lit = bool(st.get("lit") or st.get("proximity")) and not st.get("ambient")
        if lit == self.lit:
            return
        self.lit = lit
        if lit:
            log("screen on: timers running")
            self.read_battery()
            self.read_wifi()
            self.bluetooth.refresh()
            self.start_timers()
            self.weather.refresh()
            self.notifications.screen_lit()
            self.sim_later = False
            self.sim_prompt_check()
        else:
            log("screen off: timers stopped")
            self.stop_timers()
            for quick in self.quicks.values():
                quick.close_menu()
            for dock in self.docks.values():
                if dock.mode == "peek":
                    dock.hide_dock()
            for banner in self.notifications.banners.values():
                banner.hide_banner()
            for osd in self.volume_osds.values():
                osd.hide_osd()
            self.android_start.hide_all()
            self.volume_keys.screen_off()
            for dialog in self.sim_dialogs.values():
                dialog.hide_dialog()
            self.lock_if_wanted()

    def lock_ended(self):
        """duo-wc says the session is no longer locked. A lock ended for the
        client (`duoctl unlock`) is told so and duo-lock exits by itself;
        an older one that does not would keep the shell from ever locking
        again: it is ended after 3 s."""
        proc = getattr(self, "lock_proc", None)
        if proc is None or proc.poll() is not None:
            return

        def reap():
            if proc.poll() is None:
                log("duo-lock still runs after the unlock: ending it")
                proc.terminate()
                GLib.timeout_add(1000, lambda: (proc.poll() is None and proc.kill()) and False)
            return False

        GLib.timeout_add(3000, reap)

    def lock_if_wanted(self):
        """The screen went off: lock it, if the owner set a PIN and asked
        for it (lockstore.lock_on_blank; neither is there by default)."""
        if lockstore.lock_on_blank(lockstore.read_policy()):
            self.lock_now("screen off")

    def restore_lock(self):
        """Start duo-lock for a session duo-wc holds locked from before its
        restart (lock_now would see "locked" and do nothing)."""
        if getattr(self, "lock_proc", None) is not None and self.lock_proc.poll() is None:
            return
        log("duo-wc is locked from before its restart: duo-lock")
        self.lock_proc = self.start_lock()

    @keep_running
    def watch_lock(self):
        """duo-lock died (a crash, a kill) while duo-wc still says locked: the
        screen stays black with nothing to unlock it. Start it again; duo-wc
        lets a new lock client take over a dead one. Only ever SHOWS the lock
        screen: nothing here unlocks (lockwatch.py)."""
        proc = getattr(self, "lock_proc", None)
        alive = proc is not None and proc.poll() is None
        locked = bool((self.wc_state or {}).get("locked"))
        if self.lock_watch.tick(time.monotonic(), locked, alive):
            log("duo-lock is gone and duo-wc is still locked: starting it again")
            self.lock_proc = self.start_lock()
        return True

    def lock_now(self, why):
        """Start duo-lock, unless the session is locked or it runs already.
        It needs duo-wc's session_lock on (else it exits: nothing locks)."""
        if (self.wc_state or {}).get("locked"):
            return
        if getattr(self, "lock_proc", None) is not None and self.lock_proc.poll() is None:
            return
        log("%s: locking (duo-lock)" % why)
        self.lock_proc = self.start_lock()


def start_clipboard_bridge(shell, delay_s=1):
    """duo-clipboard-bridge (the Android clipboard), when Waydroid is
    installed: with a trusted Wayland connection of its own (only trusted
    clients see ext-data-control); started again when it exits, waiting up
    to a minute while it keeps failing."""
    import subprocess

    exe = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "duo-clipboard-bridge"
    )
    if not (os.path.isdir("/usr/lib/waydroid") and os.access(exe, os.X_OK)):
        return
    try:
        fd = trust.wayland_fd()
    except OSError as e:
        log("no clipboard bridge: no trusted Wayland connection (%s)" % e)
        return
    try:
        proc = subprocess.Popen([exe], pass_fds=(fd,), env=trust.child_env(fd))
    except OSError as e:
        log("cannot start the clipboard bridge: %s" % e)
        return
    finally:
        os.close(fd)
    started = GLib.get_monotonic_time()

    def exited(_pid, status):
        ran = (GLib.get_monotonic_time() - started) / 1e6
        again = 1 if ran > 60 else min(60, delay_s * 2)
        log("clipboard bridge exited (%s) after %d s; again in %d s" % (status, ran, again))
        GLib.timeout_add_seconds(again, lambda: start_clipboard_bridge(shell, again) and False)

    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, exited)
    shell.clipboard_bridge = proc


def start_lock(channel=None):
    """duo-lock with a trusted Wayland connection of its own (only trusted
    clients see ext-session-lock) and, given a calls.LockChannel, its end of
    the call card's socketpair; None if it could not be started."""
    import subprocess

    try:
        fd = trust.wayland_fd()
    except OSError as e:
        log("cannot lock: no trusted Wayland connection (%s)" % e)
        return None
    env = trust.child_env(fd)
    fds = [fd]
    if channel is not None:
        env[channel.ENV] = str(channel.child_fd)
        fds.append(channel.child_fd)
    try:
        return subprocess.Popen(["duo-lock"], pass_fds=tuple(fds), env=env)
    except OSError as e:
        log("cannot start duo-lock: %s" % e)
        return None
    finally:
        os.close(fd)


def main(argv=None):
    # Children (the apps the grid starts) must not inherit the layer-shell
    # preload, if the wrapper used one, nor the trusted Wayland connection's
    # number (GTK has the display open by now: see trust.py).
    os.environ.pop("LD_PRELOAD", None)
    trust.forget_wayland_socket()
    if not trust.available():
        log(
            "no trust factory (not started by the session): the compositor "
            "refuses the privileged requests"
        )
    if not LayerShell.is_supported():
        log("the compositor has no wlr-layer-shell (or libgtk4-layer-shell was loaded too late)")
        return 1
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    holder = {}

    def activate(application):
        if "shell" not in holder:
            application.hold()
            holder["shell"] = Shell(application)

    app.connect("activate", activate)
    return app.run(argv if argv is not None else sys.argv[:1])
