"""The "Starting Android apps..." notice (the rules: androidstartmodel.py).

A pill on the overlay layer at the bottom of the panel an Android app was
launched on while Android was not running, above the dock, a spinner and
the text; its input region is empty (touches pass through). It goes when
the app's window is there, after androidstartmodel.NOTICE_S, or when the
screen goes dark. Whether the Waydroid session runs is watched on the
session bus (its name, id.waydro.Session), so the tap itself asks nothing.
"""

import time

import gi

gi.require_foreign("cairo")  # python3-gi-cairo: Gdk.Surface.set_input_region

import cairo  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import androidstartmodel as model  # noqa: E402
from .i18n import _  # noqa: E402

TICK_MS = 250
# Above the dock (64 px) on the home screen.
BOTTOM_MARGIN = 96


def log(*args):
    print("duo-shell:", *args, flush=True)


class StartNotice(Gtk.Window):
    """One panel's notice."""

    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.add_css_class("duo-android-start")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-android-start")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(self, LayerShell.Edge.BOTTOM, BOTTOM_MARGIN)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(self, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("android-start-pill")
        self.spinner = Gtk.Spinner()
        label = Gtk.Label(label=_("Starting Android apps…"))
        label.set_wrap(True)
        box.append(self.spinner)
        box.append(label)
        self.set_child(box)
        self.connect("realize", self._see_through)

    def _see_through(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show_notice(self):
        self.spinner.set_spinning(True)
        self.present()

    def hide_notice(self):
        self.spinner.set_spinning(False)
        self.set_visible(False)


class AndroidStart:
    """The notices of the panels. `monitor_named(name)` gives a panel's
    Gdk.Monitor (None if there is none), `windows()` duo-wc's window list
    as last heard."""

    def __init__(self, app, monitor_named, windows):
        self.app = app
        self.monitor_named = monitor_named
        self.windows = windows
        self.model = model.Starting()
        self.notices = {}
        self.timer = None
        # True / False once the bus has answered; None until then (taken as
        # running: no notice rather than a wrong one).
        self.running = None
        Gio.bus_watch_name(
            Gio.BusType.SESSION,
            model.SESSION_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._appeared,
            self._vanished,
        )

    def _appeared(self, *_args):
        self.running = True

    def _vanished(self, *_args):
        self.running = False

    def launching(self, output, desktop_id):
        """An app is about to be launched on `output`: the expect_window
        timeout to ask for (ms; None: duo-wc's default), and the notice
        shown when Android has to start first."""
        ms = self.model.launch(output, desktop_id, self.running, time.monotonic())
        if output in self.model.notices:
            notice = self.notices.get(output)
            monitor = self.monitor_named(output)
            if notice is None and monitor is not None:
                notice = StartNotice(self.app, monitor)
                self.notices[output] = notice
            if notice is not None:
                log(
                    "Android is starting: the notice on %s until %s is there"
                    % (output, self.model.notices[output]["app_id"])
                )
                notice.show_notice()
            if self.timer is None:
                self.timer = GLib.timeout_add(TICK_MS, self._tick)
        return ms

    def _tick(self):
        for output in self.model.tick(self.windows(), time.monotonic()):
            log("Android start notice on %s done" % output)
            notice = self.notices.get(output)
            if notice is not None:
                notice.hide_notice()
        if self.model.pending():
            return True
        self.timer = None
        return False

    def hide_all(self):
        """The screen went dark: no notice is left over it."""
        self.model.drop()
        for notice in self.notices.values():
            notice.hide_notice()

    def drop_monitor(self, name):
        self.model.drop(name)
        notice = self.notices.pop(name, None)
        if notice is not None:
            notice.destroy()
