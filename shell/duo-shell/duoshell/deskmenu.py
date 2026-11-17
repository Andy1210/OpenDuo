"""The app menu of a desk (deskmodel.py): opened by the taskbar's first
button, above it.

A wlr-layer-shell surface on the overlay layer over the whole desk above
the taskbar (its exclusive zone keeps the taskbar free and clickable),
see-through but for the menu's card at the bottom left; a click beside the
card closes it. Keyboard on demand, and the search field has the focus at
once: laptop mode's keys (or a plugged keyboard) type into it as soon as
it opens. The list is every app (apps.installed_apps, the app grid's),
filtered and ordered as the search says (deskmodel.menu_apps); Enter starts
the first, Down goes into the list, Escape closes. Below the list:
Settings, and Leave laptop mode on laptop mode's desk.
"""

from gi.repository import Gdk, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from . import apps as applist
from . import deskmodel, penscroll
from .i18n import _

CARD_WIDTH = 380
CARD_HEIGHT = 520
MARGIN = 8


class Row(Gtk.ListBoxRow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        image = Gtk.Image()
        if app.icon is not None:
            image.set_from_gicon(app.icon)
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(32)
        label = Gtk.Label(label=app.name, xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        box.append(image)
        box.append(label)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], [app.name])


class DeskMenu(Gtk.Window):
    def __init__(self, application, shell, monitor):
        super().__init__(application=application)
        self.shell = shell
        self.monitor = monitor
        self.apps = []
        self.query = ""
        self.add_css_class("duo-deskmenu")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-deskmenu")
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
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)

        self.search = Gtk.SearchEntry()
        self.search.set_hexpand(True)
        self.search.set_placeholder_text(_("Search apps"))
        self.search.update_property([Gtk.AccessibleProperty.LABEL], [_("Search apps")])
        self.search.connect("search-changed", self._search_changed)
        self.search.connect("activate", lambda _e: self.launch_top())
        self.search.connect("stop-search", lambda _e: self.close_menu())

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list.set_activate_on_single_click(True)
        self.list.connect("row-activated", lambda _l, row: self.launch(row.app))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.list)
        self.empty = Gtk.Label(label=_("No apps found"))
        self.empty.add_css_class("empty")
        self.empty.set_vexpand(True)
        self.empty.set_visible(False)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        foot.add_css_class("foot")
        self.settings = self._foot_button(
            "emblem-system-symbolic", _("Settings"), self.open_settings
        )
        self.leave = self._foot_button(
            "view-restore-symbolic", _("Leave laptop mode"), self.leave_laptop
        )
        foot.append(self.settings)
        foot.append(self.leave)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.append(self.search)
        card.append(scroll)
        card.append(self.empty)
        card.append(foot)
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.END)
        card.set_margin_start(MARGIN)
        card.set_margin_bottom(MARGIN)
        self.card = card

        # Beside the card: a click closes the menu.
        shade = Gtk.Box()
        shade.set_hexpand(True)
        shade.set_vexpand(True)
        beside = Gtk.GestureClick()
        beside.set_button(0)
        beside.connect("released", lambda *_a: self.close_menu())
        shade.add_controller(beside)
        overlay = Gtk.Overlay()
        overlay.set_child(shade)
        overlay.add_overlay(card)
        self.set_child(overlay)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def connector(self):
        return self.monitor.get_connector()

    def _foot_button(self, icon, label, callback):
        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(Gtk.Image.new_from_icon_name(icon))
        box.append(Gtk.Label(label=label))
        button.set_child(box)
        button.set_can_focus(False)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: callback())
        return button

    def _size(self):
        geometry = self.monitor.get_geometry()
        # The taskbar's room is the desk's own (its exclusive zone).
        width = min(CARD_WIDTH, max(200, geometry.width - 2 * MARGIN))
        height = min(CARD_HEIGHT, max(240, geometry.height - 48 - 2 * MARGIN))
        self.card.set_size_request(width, height)

    def populate(self):
        self.apps = applist.installed_apps()
        self._fill()

    def _fill(self):
        child = self.list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list.remove(child)
            child = nxt
        found = deskmodel.menu_apps(self.apps, self.query)
        for app in found:
            self.list.append(Row(app))
        self.empty.set_visible(not found)
        first = self.list.get_row_at_index(0)
        if first is not None:
            self.list.select_row(first)

    def _search_changed(self, entry):
        self.query = entry.get_text()
        self._fill()

    def _key(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close_menu()
            return True
        if keyval == Gdk.KEY_Down and self.search.has_focus():
            row = self.list.get_selected_row() or self.list.get_row_at_index(0)
            if row is not None:
                row.grab_focus()
            return True
        return False

    def launch_top(self):
        app = deskmodel.top_hit(self.apps, self.query)
        if app is not None:
            self.launch(app)

    def launch(self, app):
        self.shell.desk_launch(self.connector(), app.info, self.get_display())
        self.close_menu()

    def open_settings(self):
        self.close_menu()
        self.shell.desk_settings(self.connector(), None, self.get_display())

    def leave_laptop(self):
        self.close_menu()
        self.shell.leave_laptop()

    def open_menu(self):
        st = self.shell.wc_state or {}
        self.leave.set_visible(
            any(a == "leave_laptop" for a, _l in deskmodel.desk_menu(st, self.connector()))
        )
        self.query = ""
        self.search.set_text("")
        self._size()
        self.populate()
        self.present()
        self.search.grab_focus()

    def close_menu(self):
        if self.get_visible():
            self.set_visible(False)
            self.shell.desk_menu_changed()

    def toggle(self):
        if self.get_visible():
            self.close_menu()
        else:
            self.open_menu()
