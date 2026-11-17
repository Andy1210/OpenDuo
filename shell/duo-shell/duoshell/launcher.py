"""The app grid (the app drawer): every installed app, with a search field,
as a grid of icons or as an alphabetical list with letter headers and an
A-Z index down the right edge (launchermodel.py; the toggle in the header,
or Duo Settings).

Touch and hold an app: add it to this panel's home screen, or pin it to
the dock (or unpin it).

A wlr-layer-shell surface on the top layer of one panel, anchored to all four
edges (it covers the panel below its status bar), keyboard on demand (the
search field takes text; tapping it brings up the on-screen keyboard through
text-input-v3). It opens on the panel it was called from (the bar's button, or
a swipe up from that panel's bottom edge); tapping an app tells duo-wc to put
the next window on this panel (`expect_window`) and starts the app.
"""

from gi.repository import Gdk, Gio, GLib, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from . import apps as applist
from . import launchermodel, penscroll, wc
from .i18n import _

# The A-Z index keeps this far from the panel's right edge: a touch that
# starts in duo-wc's edge strip (20 px) would be held back as a possible
# back swipe.
INDEX_EDGE_GAP = 22


class Tile(Gtk.FlowBoxChild):
    def __init__(self, app, on_launch, on_hold=None):
        super().__init__()
        self.app = app
        button = Gtk.Button()
        button.add_css_class("tile")
        button.set_can_focus(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        image = Gtk.Image()
        if app.icon is not None:
            image.set_from_gicon(app.icon)
        else:
            image.set_from_icon_name("application-x-executable")
        label = Gtk.Label(label=app.short)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(2)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(11)
        label.set_justify(Gtk.Justification.CENTER)
        box.append(image)
        box.append(label)
        button.set_child(box)
        button.update_property([Gtk.AccessibleProperty.LABEL], [app.name])
        self.held = False
        button.connect("clicked", lambda _b: self._clicked(on_launch))
        if on_hold is not None:
            # Touch and hold: the app's menu (add to home, pin to the dock).
            hold = Gtk.GestureLongPress()
            hold.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            hold.connect("pressed", lambda g, _x, _y: self._held(g, on_hold))
            button.add_controller(hold)
        self.button = button
        self.set_child(button)

    def _clicked(self, on_launch):
        if self.held:
            self.held = False
            return
        on_launch(self.app)

    def _held(self, gesture, on_hold):
        self.held = True
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        on_hold(self)


class ListRow(Gtk.ListBoxRow):
    """One app in the list view: icon and name; tap launches, hold opens
    the app's menu (like a grid tile)."""

    def __init__(self, app, on_launch, on_hold=None):
        super().__init__()
        self.app = app
        self.letter = launchermodel.letter_of(app.short)
        self.set_activatable(False)
        self.set_selectable(False)
        button = Gtk.Button()
        button.add_css_class("list-app")
        button.set_can_focus(False)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        image = Gtk.Image()
        if app.icon is not None:
            image.set_from_gicon(app.icon)
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(36)
        label = Gtk.Label(label=app.name, xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        row.append(image)
        row.append(label)
        button.set_child(row)
        button.update_property([Gtk.AccessibleProperty.LABEL], [app.name])
        self.held = False
        button.connect("clicked", lambda _b: self._clicked(on_launch))
        if on_hold is not None:
            hold = Gtk.GestureLongPress()
            hold.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            hold.connect("pressed", lambda g, _x, _y: self._held(g, on_hold))
            button.add_controller(hold)
        self.button = button
        self.set_child(button)

    def _clicked(self, on_launch):
        if self.held:
            self.held = False
            return
        on_launch(self.app)

    def _held(self, gesture, on_hold):
        self.held = True
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        on_hold(self)


class HeaderRow(Gtk.ListBoxRow):
    """A letter over its section of the list."""

    def __init__(self, letter):
        super().__init__()
        self.app = None
        self.letter = letter
        self.set_activatable(False)
        self.set_selectable(False)
        self.set_can_focus(False)
        label = Gtk.Label(label=letter, xalign=0)
        label.add_css_class("list-letter")
        self.set_child(label)


class AppGrid(Gtk.Window):
    def __init__(self, application, shell, monitor):
        super().__init__(application=application)
        self.shell = shell
        self.monitor = monitor
        self.add_css_class("duo-launcher")
        self.set_decorated(False)

        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-launcher")
        LayerShell.set_layer(self, LayerShell.Layer.TOP)
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
        self.search.connect("activate", self._launch_first)
        self.search.connect("stop-search", lambda _e: self.close_grid())
        # Not focusable until it is tapped: GTK would focus the first
        # focusable widget when the grid maps (the keyboard flashing up).
        self._search_focusable(False)
        tap = Gtk.GestureClick()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", self._search_tapped)
        self.search.add_controller(tap)

        close = Gtk.Button()
        close.add_css_class("close")
        close.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close.set_tooltip_text(_("Close"))
        close.update_property([Gtk.AccessibleProperty.LABEL], [_("Close")])
        close.set_can_focus(False)
        close.connect("clicked", lambda _b: self.close_grid())

        # Grid <-> list (launchermodel; Duo Settings has it too).
        self.view_button = Gtk.Button()
        self.view_button.add_css_class("close")
        self.view_button.set_can_focus(False)
        self.view_button.connect(
            "clicked",
            lambda _b: self.set_view("grid" if self.view == "list" else "list", save=True),
        )

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("search-row")
        row.append(self.search)
        row.append(self.view_button)
        row.append(close)

        self.flow = Gtk.FlowBox()
        self.flow.set_homogeneous(True)
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_min_children_per_line(3)
        self.flow.set_max_children_per_line(8)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_filter_func(self._filter)
        self.flow.set_sort_func(self._sort)

        self.empty = Gtk.Label(label=_("No apps found"))
        self.empty.add_css_class("empty")
        self.empty.set_visible(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.flow)
        content.append(self.empty)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_kinetic_scrolling(True)
        scroll.set_child(content)
        self.scroll = scroll

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.add_named(scroll, "grid")
        self.stack.add_named(self._list_view(), "list")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(row)
        box.append(self.stack)
        self.set_child(box)
        self.view = None
        self.set_view(launchermodel.read_view())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        self.query = ""
        self.populate()

    def connector(self):
        return self.monitor.get_connector()

    # -- the list view ------------------------------------------------------------

    def _list_view(self):
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("app-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_filter_func(self._list_filter)
        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroll.set_vexpand(True)
        self.list_scroll.set_hexpand(True)
        self.list_scroll.set_kinetic_scrolling(True)
        self.list_scroll.set_child(self.listbox)
        self.list_headers, self.list_order, self.list_letters = {}, [], set()
        self.list_empty = Gtk.Label(label=_("No apps found"))
        self.list_empty.add_css_class("empty")
        self.list_empty.set_valign(Gtk.Align.START)
        self.list_empty.set_visible(False)

        # The A-Z index: tap or scrub, the list jumps to the letter.
        self.index = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, homogeneous=True)
        self.index.add_css_class("az-index")
        self.index.set_margin_end(INDEX_EDGE_GAP)
        self.index_labels = {}
        for letter in launchermodel.INDEX:
            label = Gtk.Label(label=letter)
            label.add_css_class("az-letter")
            label.set_vexpand(True)
            self.index.append(label)
            self.index_labels[letter] = label
        scrub = Gtk.GestureDrag()
        scrub.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scrub.connect("drag-begin", self._scrub_begin)
        scrub.connect("drag-update", self._scrub_update)
        scrub.connect("drag-end", self._scrub_end)
        self.index.add_controller(scrub)
        self.scrub_from = 0.0

        # The big letter by the finger while scrubbing.
        self.bubble = Gtk.Label()
        self.bubble.add_css_class("az-bubble")
        self.bubble.set_halign(Gtk.Align.END)
        self.bubble.set_valign(Gtk.Align.START)
        self.bubble.set_margin_end(72)
        self.bubble.set_can_target(False)
        self.bubble.set_visible(False)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        lists = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lists.set_hexpand(True)
        lists.append(self.list_empty)
        lists.append(self.list_scroll)
        row.append(lists)
        row.append(self.index)
        overlay = Gtk.Overlay()
        overlay.set_child(row)
        overlay.add_overlay(self.bubble)
        return overlay

    def set_view(self, view, save=False):
        """`grid` or `list`; `save`: remembered (launcher.conf)."""
        if view not in launchermodel.VIEWS:
            view = "grid"
        if save:
            try:
                launchermodel.write_view(view)
            except OSError as e:
                print("duo-shell: launcher view: %s" % e, flush=True)
        if view == self.view:
            return
        self.view = view
        self.stack.set_visible_child_name(view)
        self.view_button.set_child(
            Gtk.Image.new_from_icon_name(
                "view-grid-symbolic" if view == "list" else "view-list-symbolic"
            )
        )
        tip = _("Show as a grid") if view == "list" else _("Show as a list")
        self.view_button.set_tooltip_text(tip)
        self.view_button.update_property([Gtk.AccessibleProperty.LABEL], [tip])

    def _list_filter(self, row):
        if row.app is None:
            # Letters only while not searching (the matches are few).
            return not self.query.strip() and row.letter in self.list_letters
        return applist.matches(self.query, row.app.fields)

    def _scrub_begin(self, gesture, _x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.scrub_from = y
        self._scrub_to(y)

    def _scrub_update(self, _gesture, _dx, dy):
        self._scrub_to(self.scrub_from + dy)

    def _scrub_end(self, _gesture, _dx, _dy):
        self.bubble.set_visible(False)
        for label in self.index_labels.values():
            label.remove_css_class("active")

    def _scrub_to(self, y):
        height = self.index.get_height()
        letter = launchermodel.letter_at(y, height)
        target = launchermodel.section_for(letter, self.list_order)
        for name, label in self.index_labels.items():
            if name == letter:
                label.add_css_class("active")
            else:
                label.remove_css_class("active")
        self.bubble.set_label(letter or "")
        size = 64
        self.bubble.set_margin_top(int(max(0, min(max(0, height - size), y - size / 2))))
        self.bubble.set_visible(letter is not None)
        header = self.list_headers.get(target)
        if header is None or self.query.strip():
            return
        ok, bounds = header.compute_bounds(self.listbox)
        if ok:
            adj = self.list_scroll.get_vadjustment()
            adj.set_value(
                max(adj.get_lower(), min(bounds.get_y(), adj.get_upper() - adj.get_page_size()))
            )

    def populate(self):
        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flow.remove(child)
            child = nxt
        apps = applist.installed_apps()
        for app in apps:
            self.flow.append(Tile(app, self.launch, self.app_menu))
        # The list: a header over each letter's section.
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        by_short = {}
        for app in apps:
            by_short.setdefault(app.short, []).append(app)
        self.list_headers = {}
        self.list_order = []
        for letter, names in launchermodel.sections([a.short for a in apps]):
            header = HeaderRow(letter)
            self.listbox.append(header)
            self.list_headers[letter] = header
            self.list_order.append(letter)
            seen = set()
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                for app in by_short.get(name, []):
                    self.listbox.append(ListRow(app, self.launch, self.app_menu))
        self.list_letters = set(self.list_order)
        for letter, label in self.index_labels.items():
            if letter in self.list_letters:
                label.remove_css_class("dim")
            else:
                label.add_css_class("dim")

    def _filter(self, child):
        return applist.matches(self.query, child.app.fields)

    def _sort(self, a, b):
        ka = (applist.rank(self.query, a.app.name), applist.sort_key(a.app.name))
        kb = (applist.rank(self.query, b.app.name), applist.sort_key(b.app.name))
        return (ka > kb) - (ka < kb)

    def _search_changed(self, entry):
        self.query = entry.get_text()
        self.flow.invalidate_filter()
        self.flow.invalidate_sort()
        self.listbox.invalidate_filter()
        none = self._first_visible() is None
        self.empty.set_visible(none)
        self.list_empty.set_visible(none)
        # The index jumps between letters: not while searching.
        self.index.set_visible(not self.query.strip())

    def _first_visible(self):
        i = 0
        while True:
            child = self.flow.get_child_at_index(i)
            if child is None:
                return None
            if child.get_child_visible() and self._filter(child):
                return child
            i += 1

    def _launch_first(self, _entry):
        child = self._first_visible()
        if child is not None:
            self.launch(child.app)

    def _key(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close_grid()
            return True
        return False

    def open_grid(self):
        self.remove_css_class("closing")
        self.set_view(launchermodel.read_view())
        self.search.set_text("")
        self.query = ""
        self.flow.invalidate_filter()
        self.listbox.invalidate_filter()
        self.empty.set_visible(False)
        self.list_empty.set_visible(False)
        self.index.set_visible(True)
        self.scroll.get_vadjustment().set_value(0)
        self.list_scroll.get_vadjustment().set_value(0)
        # Nothing focused: the keyboard comes up only when the search field
        # is tapped, not every time the grid opens. Before it maps (a focus
        # still on the field would enable its text input as the grid takes
        # the keyboard) and once more after (GTK focuses on map).
        self._unfocus()
        self.present()
        GLib.idle_add(self._unfocus)
        self.shell.grid_changed()

    def _unfocus(self):
        """The search field loses the focus and takes none until tapped.
        A hidden grid kept the field focused (setting it unfocusable does
        not move the focus away): the next open brought the keyboard up for
        a moment, until the idle unfocus took it down again."""
        self._search_focusable(False)
        self.set_focus(None)
        return False

    def _search_focusable(self, on):
        self.search.set_focusable(on)
        delegate = self.search.get_delegate()
        if delegate is not None:
            delegate.set_focusable(on)

    def _search_tapped(self, _gesture, _n, _x, _y):
        self._search_focusable(True)
        delegate = self.search.get_delegate()
        (delegate or self.search).grab_focus()

    def close_grid(self):
        """Sinks away (CSS, 160 ms), then goes."""
        if self.get_visible() and not self.has_css_class("closing"):
            self.add_css_class("closing")
            # The keyboard goes with the grid, and the field stays unfocused
            # for the next open.
            self._unfocus()
            GLib.timeout_add(160, self._closed)

    def _closed(self):
        self.remove_css_class("closing")
        if self.get_visible():
            self._unfocus()
            self.set_visible(False)
            self.shell.grid_changed()
        return False

    def toggle(self):
        if self.get_visible() and not self.has_css_class("closing"):
            self.close_grid()
        else:
            self.open_grid()

    def app_menu(self, tile):
        """An app held in the grid: add it to this panel's home screen, pin
        or unpin it in the dock."""
        app = tile.app
        popover = Gtk.Popover()
        popover.add_css_class("duo-menu")
        popover.set_has_arrow(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        on_home = any(
            app.app_id in page for page in self.shell.home_layout.get(self.connector(), [])
        )

        def done(action):
            popover.popdown()
            action()

        if not on_home:
            box.append(
                _menu_button(
                    _("Add to home screen"),
                    "go-home-symbolic",
                    lambda: done(lambda: self._add_home(app)),
                )
            )
        pinned = app.app_id in self.shell.pinned
        box.append(
            _menu_button(
                _("Unpin from the dock") if pinned else _("Pin to the dock"),
                "view-pin-symbolic",
                lambda: done(lambda: self.shell.toggle_pin(app.app_id)),
            )
        )
        popover.set_child(box)
        popover.set_parent(tile.button)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _add_home(self, app):
        self.shell.add_to_home(app.app_id, self.connector())
        print(
            "duo-shell: %s added to the home screen of %s" % (app.app_id, self.connector()),
            flush=True,
        )

    def launch(self, app):
        output = self.connector()
        if output:
            try:
                wc.request("expect_window", output=output)
            except (OSError, RuntimeError, ValueError) as e:
                print("duo-shell: expect_window: %s" % e, flush=True)
        context = self.get_display().get_app_launch_context()
        try:
            app.info.launch([], context)
            print("duo-shell: launched %s on %s" % (app.app_id, output), flush=True)
        except GLib.Error as e:
            print("duo-shell: launching %s failed: %s" % (app.app_id, e.message), flush=True)
        self.close_grid()


def _menu_button(label, icon, callback):
    button = Gtk.Button()
    button.add_css_class("menu-item")
    button.set_can_focus(False)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(20)
    row.append(image)
    row.append(Gtk.Label(label=label, xalign=0))
    button.set_child(row)
    button.connect("clicked", lambda _b: callback())
    return button


def watch_apps(callback):
    """Call `callback` when the set of installed apps changes."""
    monitor = Gio.AppInfoMonitor.get()
    monitor.connect("changed", lambda _m: callback())
    return monitor
