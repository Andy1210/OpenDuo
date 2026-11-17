"""The dock: pinned and running apps along the bottom of a panel.

A wlr-layer-shell surface on the overlay layer, anchored to the bottom.
Three ways it shows (main.Shell decides):

- **peek**: a short swipe up from the panel's bottom edge (duo-wc's
  `edge_swipe` with kind `short`) shows it on that panel for a few seconds;
- **with the overview**: on every panel the task overview is open on (duo-wc
  keeps room for it at the bottom), until the overview closes;
- **home**: standing at the bottom of a panel that shows the home screen
  (duo-wc's state names those: `home`);
- **taskbar**: standing with an exclusive zone on a desk (an external
  display, laptop mode's desk panel: dockmodel.is_desk), listing that
  desk's windows; in span it is not used (the owner wants the dock in span
  only when pulled up, 2026-11-07). Never on laptop mode's keyboard panel.

Never over a fullscreen window (a video): duo-wc names those panels in its
state (`fullscreen`), and hides the dock there itself too.

A long swipe up is the app grid, a swipe that holds is the overview (both
duo-wc's gestures), so the three do not compete. Tap an app: a running one
comes to the front (duo-wc's activate_window), another starts on this panel.
Hold an app (LONG_PRESS_S or more) and let go: pin or unpin it (timed on
the button's own click). The first button opens the overview, the last the
app grid.

The row is rebuilt only when what it shows changes (dockmodel.signature): a
row rebuilt while a finger goes down has no buttons in place yet (GTK lays
the new ones out at the next frame), so the press lands on the row and the
tap is lost. That was the "lost first tap": duo-wc raised the window under
the dock when the dock was touched, the window list changed, and the dock
rebuilt itself under the finger (both are fixed).
"""

from gi.repository import Gio, GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import apps, dockmodel, wc
from .i18n import _
from .widgets import icon_button

DOCK_HEIGHT = 64
LONG_PRESS_S = 0.6
MARGIN = 8
PEEK_MS = 5000


def app_index():
    """desktop id -> (StartupWMClass, executable basename), for matching
    windows to apps; and desktop id -> AppInfo."""
    index, infos = {}, {}
    for info in Gio.AppInfo.get_all():
        desktop_id = info.get_id()
        if not desktop_id:
            continue
        wm_class = info.get_startup_wm_class() if hasattr(info, "get_startup_wm_class") else None
        exe = info.get_executable()
        index[desktop_id] = (wm_class, GLib.path_get_basename(exe) if exe else None)
        infos[desktop_id] = info
    return index, infos


class Dock(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.mode = None
        self.timer = None
        self.toast_timer = None
        # What the row shows now (dockmodel.signature), so it is rebuilt only
        # when that changes: rebuilding under a finger loses its tap (the new
        # buttons have no place until the next frame).
        self.shown = None
        self.add_css_class("duo-dock")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-dock")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_margin(self, edge, MARGIN)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.set_default_size(-1, DOCK_HEIGHT)

        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.row.set_halign(Gtk.Align.CENTER)
        self.row.set_valign(Gtk.Align.CENTER)
        self.toast = Gtk.Label()
        self.toast.add_css_class("toast")
        self.toast.set_halign(Gtk.Align.CENTER)
        self.toast.set_valign(Gtk.Align.START)
        self.toast.set_visible(False)
        overlay = Gtk.Overlay()
        overlay.set_child(self.row)
        overlay.add_overlay(self.toast)
        overlay.set_size_request(-1, DOCK_HEIGHT)
        self.set_child(overlay)

    def connector(self):
        return self.monitor.get_connector()

    def populate(self, force=False):
        index, infos = self.shell.app_index()
        st = self.shell.wc_state or {}
        windows = dockmodel.windows_on(st.get("windows"), self.connector(), st)
        entries = dockmodel.items(self.shell.pinned, windows, index)
        shown = dockmodel.signature(entries)
        if not force and shown == self.shown and self.row.get_first_child() is not None:
            return
        self.shown = shown
        child = self.row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.row.remove(child)
            child = nxt
        self.row.append(
            icon_button("focus-windows-symbolic", _("Windows"), self.overview, "dock-tool")
        )
        pinned_done = False
        for item in entries:
            if not item.pinned and not pinned_done:
                pinned_done = True
                sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                sep.add_css_class("dock-sep")
                self.row.append(sep)
            self.row.append(self._app_button(item, infos.get(item.desktop_id)))
        self.row.append(icon_button("view-app-grid-symbolic", _("Apps"), self.apps, "dock-tool"))

    def _app_button(self, item, info):
        button = Gtk.Button()
        button.add_css_class("dock-app")
        button.set_can_focus(False)
        # The icon centred in the button (like the tool buttons' icons), the
        # running dot over the bottom padding.
        box = Gtk.Overlay()
        image = Gtk.Image()
        if info is not None and info.get_icon() is not None:
            image.set_from_gicon(info.get_icon())
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(40)
        dot = Gtk.Box()
        dot.add_css_class("dot")
        dot.set_halign(Gtk.Align.CENTER)
        dot.set_valign(Gtk.Align.END)
        dot.set_margin_bottom(1)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        dot.set_opacity(1.0 if item.running else 0.0)
        box.set_child(image)
        box.add_overlay(dot)
        button.set_child(box)
        name = apps.info_label(info, item.app_id or "")
        button.set_tooltip_text(name)
        button.update_property([Gtk.AccessibleProperty.LABEL], [name])
        # When the finger went down: the button's own "clicked" comes on the
        # release, and how long it was held says tap or hold.
        held = {"at": None}
        down = Gtk.GestureClick()
        down.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        down.connect("pressed", lambda *_a: self._pressed(held, item))
        button.add_controller(down)
        button.connect("clicked", lambda _b: self._clicked(item, info, held))
        return button

    def _pressed(self, held, item):
        held["at"] = GLib.get_monotonic_time()
        print("duo-shell: dock: pressed %s" % (item.desktop_id or item.app_id), flush=True)

    def _clicked(self, item, info, held):
        at, held["at"] = held["at"], None
        if at is not None and (GLib.get_monotonic_time() - at) / 1e6 >= LONG_PRESS_S:
            self.long_press(item)
        else:
            self.tap(item, info)

    # -- actions ----------------------------------------------------------------------

    def tap(self, item, info):
        self.keep()
        if item.running and item.app_id:
            try:
                wc.request("activate_window", app_id=item.app_id)
            except (OSError, RuntimeError, ValueError) as e:
                print("duo-shell: activate %s: %s" % (item.app_id, e), flush=True)
            self.shell.dock_used(self)
            return
        if info is None:
            return
        try:
            wc.request("expect_window", output=self.connector())
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: expect_window: %s" % e, flush=True)
        try:
            info.launch([], self.get_display().get_app_launch_context())
            print(
                "duo-shell: dock launched %s on %s" % (item.desktop_id, self.connector()),
                flush=True,
            )
        except GLib.Error as e:
            print("duo-shell: launching %s failed: %s" % (item.desktop_id, e.message), flush=True)
        self.shell.dock_used(self)

    def long_press(self, item):
        if not item.desktop_id:
            self.show_toast(_("Cannot pin this app"))
            return
        pinned = self.shell.toggle_pin(item.desktop_id)
        self.show_toast(_("Pinned") if pinned else _("Unpinned"))
        self.keep()

    def overview(self):
        try:
            wc.request("overview", action="toggle")
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: overview: %s" % e, flush=True)
        if self.mode == "peek":
            self.hide_dock()

    def apps(self):
        if self.mode == "overview":
            try:
                wc.request("overview", action="close")
            except (OSError, RuntimeError, ValueError):
                pass
        self.shell.open_grid_on(self.connector())
        if self.mode == "peek":
            self.hide_dock()

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

    # -- showing ----------------------------------------------------------------------

    def show_dock(self, mode):
        """mode: 'peek', 'overview', 'home' (standing on the home screen) or
        'taskbar'."""
        self.remove_css_class("closing")
        self.mode = mode
        if mode == "home":
            self.add_css_class("home")
        else:
            self.remove_css_class("home")
        LayerShell.set_exclusive_zone(self, DOCK_HEIGHT if mode == "taskbar" else 0)
        self.populate()
        self.present()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if mode == "peek":
            self.timer = GLib.timeout_add(PEEK_MS, self._peek_over)

    def keep(self):
        """Someone is using it: a peek lasts a while longer."""
        if self.mode == "peek":
            if self.timer is not None:
                GLib.source_remove(self.timer)
            self.timer = GLib.timeout_add(PEEK_MS, self._peek_over)

    def _peek_over(self):
        self.timer = None
        if self.mode == "peek":
            self.hide_dock()
        return False

    def hide_dock(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        peeked = self.mode == "peek"
        self.mode = None
        LayerShell.set_exclusive_zone(self, 0)
        if peeked and self.get_visible():
            # A peek sinks away (CSS, 160 ms); other modes go at once.
            self.add_css_class("closing")
            GLib.timeout_add(160, self._sunk)
        else:
            self.set_visible(False)

    def _sunk(self):
        self.remove_css_class("closing")
        if self.mode is None:
            self.set_visible(False)
        return False
