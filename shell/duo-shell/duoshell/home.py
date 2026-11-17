"""The home screen: what a panel shows with no app on it.

A wlr-layer-shell surface on the BACKGROUND layer of each panel (below every
window: it shows where no window is, so "home" is duo-wc setting the panel's
windows aside, a long swipe up from the bottom edge). Pages of app icons
(Adw.Carousel: swipe sideways; dots below), the first one with the widgets
above its icons: the clock and the date, the weather (GNOME Weather's first
place) and the battery. The dock stands at the bottom (dock.py, mode
"home").

- **tap** an icon: the app starts on this panel;
- **touch and hold** an icon (or the empty page): edit mode. The icons
  show a remove badge, and one can be carried to another place, to another
  page (held at the page's left or right edge, the page turns; past the last
  page a new one starts) and is put down where the finger lets go. "Done",
  Back (a side swipe) or Escape end it. Apps are added from the app grid
  (touch and hold an app there: "Add to home screen");
- **swipe up** on the page (not from the edge): the app grid.

The layout is homemodel.py's (pure), saved in ~/.config/duo-shell/home.json.
"""

import gi

gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import apps as applist  # noqa: E402
from . import deskmodel, homemodel, status, timefmt, wc  # noqa: E402
from .i18n import _  # noqa: E402
from .wallpicture import WallPicture  # noqa: E402

DOCK_RESERVE = 92
# The status bar's height (bar.py): the home surface covers the whole panel
# (its wallpaper under the bar too) and keeps this much free at the top.
BAR_HEIGHT = 48
LONG_PRESS_MS = 450
PICK_SLOP = 10
FLIP_MS = 550
ICON = 56


class HomeScreen(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.editing = False
        self.press = None
        self.carry = None
        self.flip_timer = None
        self.pages = []
        self.add_css_class("duo-home")
        self.set_decorated(False)

        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-home")
        LayerShell.set_layer(self, LayerShell.Layer.BACKGROUND)
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        # -1: no other surface's exclusive zone moves it. With 0 the
        # on-screen keyboard's zone pushed the icons and the wallpaper up;
        # now only the dock (its own surface) stands above the keyboard. The
        # status bar's room is kept by hand (BAR_HEIGHT).
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        self.done = Gtk.Button(label=_("Done"))
        self.done.add_css_class("home-done")
        self.done.set_halign(Gtk.Align.END)
        self.done.set_can_focus(False)
        self.done.connect("clicked", lambda _b: self.set_editing(False))
        self.done_reveal = Gtk.Revealer()
        self.done_reveal.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.done_reveal.set_transition_duration(180)
        self.done_reveal.set_child(self.done)

        self.carousel = Adw.Carousel()
        self.carousel.set_vexpand(True)
        self.carousel.set_allow_long_swipes(False)
        self.carousel.set_spacing(0)
        self.dots = Adw.CarouselIndicatorDots()
        self.dots.set_carousel(self.carousel)
        self.dots.add_css_class("home-dots")

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.add_css_class("home-main")
        main.append(self.carousel)
        main.append(self.dots)
        room = Gtk.Box()
        room.set_size_request(-1, DOCK_RESERVE)
        main.append(room)
        self.main = main

        self.floating = Gtk.Fixed()
        self.floating.set_can_target(False)
        overlay = Gtk.Overlay()
        overlay.set_child(main)
        # "Done" floats over the page (top right): entering the edit mode must
        # not move a single icon.
        self.done_reveal.set_halign(Gtk.Align.END)
        self.done_reveal.set_valign(Gtk.Align.START)
        self.done_reveal.set_margin_top(10)
        self.done_reveal.set_margin_end(14)
        overlay.add_overlay(self.done_reveal)
        # The Bing picture's "i": what the picture is (only while it shows).
        self.info = Gtk.Button()
        self.info.add_css_class("home-info")
        info_label = Gtk.Label(label="i")
        info_label.add_css_class("home-info-i")
        self.info.set_child(info_label)
        self.info.set_halign(Gtk.Align.END)
        self.info.set_valign(Gtk.Align.END)
        self.info.set_margin_end(14)
        self.info.set_margin_bottom(DOCK_RESERVE + 2)
        self.info.set_can_focus(False)
        self.info.set_visible(False)
        self.info.update_property([Gtk.AccessibleProperty.LABEL], [_("About this picture")])
        self.info.connect("clicked", lambda _b: self.show_caption())
        self.caption = None
        overlay.add_overlay(self.info)
        overlay.add_overlay(self.floating)
        overlay.set_margin_top(BAR_HEIGHT)
        # The wallpaper under everything (none = the stylesheet's gradient),
        # and a dimming veil so the clock and the icons stay readable.
        # The whole picture, this panel's part of it cut as it is drawn
        # (wallpicture.py): right at a rotation or a pose change.
        self.wall = WallPicture(monitor.get_connector())
        self.wall.set_visible(False)
        self.veil = Gtk.Box()
        self.veil.add_css_class("home-veil")
        self.veil.set_can_target(False)
        self.veil.set_visible(False)
        self.veil_css = Gtk.CssProvider()
        self.veil.get_style_context().add_provider(
            self.veil_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        base = Gtk.Overlay()
        base.set_child(self.wall)
        base.add_overlay(self.veil)
        base.add_overlay(overlay)
        base.set_measure_overlay(overlay, True)
        self.set_child(base)
        # On a desk (deskmodel: an external display, laptop mode's desk
        # panel) only the wallpaper shows: the page, its icons and widgets
        # are a phone's (set_desk). A right click or a long press on it there
        # opens the desk's menu.
        self.content = overlay
        self.base = base
        self.desk = False
        # (Off, PropagationPhase.NONE, on the phone's home screen: its own
        # touch and hold is the edit mode.)
        desk_click = Gtk.GestureClick()
        desk_click.set_button(3)
        desk_click.connect("pressed", lambda _g, _n, x, y: self._desk_pressed(x, y))
        desk_hold = Gtk.GestureLongPress()
        desk_hold.connect("pressed", lambda _g, x, y: self._desk_pressed(x, y))
        self.desk_gestures = (desk_click, desk_hold)
        for gesture in self.desk_gestures:
            gesture.set_propagation_phase(Gtk.PropagationPhase.NONE)
            base.add_controller(gesture)

        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        main.add_controller(drag)
        self.drag = drag

        self.header = None
        # A panel on its side lays the pages out again (homemodel.grid_shape:
        # the widgets beside the first page's icons, the others 7 x 3); the
        # monitor's geometry changes before the surface's new size comes.
        self.landscape = self._is_landscape()
        self._sync_orientation_class()
        monitor.connect("notify::geometry", self._geometry_changed)
        self.populate()

    def set_wallpaper(self, texture, dim, mode="same"):
        """The picture (a Gdk.Texture of the whole picture, this panel's part
        cut as it is drawn, `mode` same or span) and the per cent of black over
        it; None = the default gradient."""
        if texture is None:
            self.wall.set_picture(None, mode)
            self.wall.set_visible(False)
            self.veil.set_visible(False)
            return
        self.wall.set_picture(texture, mode)
        self.wall.set_visible(True)
        self.veil_css.load_from_string(
            ".home-veil { background-color: rgba(0, 0, 0, %.2f); }"
            % (max(0, min(100, dim)) / 100.0)
        )
        self.veil.set_visible(dim > 0)

    def connector(self):
        return self.monitor.get_connector()

    # -- a desk ---------------------------------------------------------------------

    def set_desk(self, on):
        """A desk shows the wallpaper only; back to the home screen as it was
        (its page, its icons) when it is a phone's panel again."""
        if on == self.desk:
            return
        self.desk = on
        if on:
            if self.editing:
                self.set_editing(False)
            self.add_css_class("desk")
        else:
            self.remove_css_class("desk")
        self.content.set_visible(not on)
        for gesture in self.desk_gestures:
            gesture.set_propagation_phase(
                Gtk.PropagationPhase.BUBBLE if on else Gtk.PropagationPhase.NONE
            )
        print(
            "duo-shell: home of %s: %s" % (self.connector(), "desk" if on else "home screen"),
            flush=True,
        )

    def _desk_pressed(self, x, y):
        if not self.desk:
            return
        st = self.shell.wc_state or {}
        popover = Gtk.Popover()
        popover.add_css_class("duo-menu")
        popover.set_has_arrow(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        labels = {
            "settings": _("Settings"),
            "wallpaper": _("Wallpaper"),
            "leave_laptop": _("Leave laptop mode"),
        }
        for action, _key in deskmodel.desk_menu(st, self.connector()):
            item = Gtk.Button(label=labels[action])
            item.add_css_class("menu-item")
            item.set_can_focus(False)
            item.get_child().set_xalign(0)

            def chosen(_b, action=action):
                popover.popdown()
                self.desk_action(action)

            item.connect("clicked", chosen)
            box.append(item)
        popover.set_child(box)
        popover.set_parent(self.base)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def desk_action(self, action):
        if action == "leave_laptop":
            self.shell.leave_laptop()
        elif action == "wallpaper":
            self.shell.desk_settings(self.connector(), "wallpaper", self.get_display())
        else:
            self.shell.desk_settings(self.connector(), None, self.get_display())

    def _is_landscape(self):
        geometry = self.monitor.get_geometry()
        return geometry.width > geometry.height

    def _sync_orientation_class(self):
        if self.landscape:
            self.add_css_class("landscape")
        else:
            self.remove_css_class("landscape")

    def _geometry_changed(self, *_args):
        landscape = self._is_landscape()
        if landscape == self.landscape:
            return
        self.landscape = landscape
        self._sync_orientation_class()
        print(
            "duo-shell: home of %s: %s"
            % (self.connector(), "landscape" if landscape else "portrait"),
            flush=True,
        )
        self.populate()

    # -- the Bing picture's caption -------------------------------------------------

    def set_caption(self, caption):
        """wallpaper.bing_caption of the picture shown, or None (no "i")."""
        self.caption = caption
        self.info.set_visible(caption is not None and not self.editing)

    def show_caption(self):
        """A card above the "i": the title, what the picture is and whose,
        and "Learn more" (Bing's page about it, in the browser)."""
        caption = self.caption
        if caption is None:
            return
        popover = Gtk.Popover()
        popover.add_css_class("duo-menu")
        popover.add_css_class("home-caption")
        popover.set_has_arrow(False)
        popover.set_position(Gtk.PositionType.TOP)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("caption-box")
        if caption["title"]:
            title = Gtk.Label(label=caption["title"], xalign=0)
            title.add_css_class("caption-title")
            title.set_wrap(True)
            title.set_max_width_chars(30)
            box.append(title)
        if caption["text"]:
            text = Gtk.Label(label=caption["text"], xalign=0)
            text.add_css_class("caption-text")
            text.set_wrap(True)
            text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.set_max_width_chars(30)
            box.append(text)
        if caption["link"]:
            more = Gtk.Button(label=_("Learn more"))
            more.add_css_class("suggested-action")
            more.add_css_class("caption-more")
            more.set_halign(Gtk.Align.END)
            more.set_can_focus(False)

            def learn(_b):
                popover.popdown()
                self.open_link(caption["link"])

            more.connect("clicked", learn)
            box.append(more)
        popover.set_child(box)
        popover.set_parent(self.info)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def open_link(self, link):
        """The link in the default browser, opened on this panel."""
        output = self.connector()
        try:
            wc.request("expect_window", output=output)
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: expect_window: %s" % e, flush=True)
        try:
            Gio.AppInfo.launch_default_for_uri(link, self.get_display().get_app_launch_context())
            print("duo-shell: opened the picture's page on %s" % output, flush=True)
        except GLib.Error as e:
            print("duo-shell: cannot open %s: %s" % (link, e.message), flush=True)

    # -- building -------------------------------------------------------------------

    def _layout(self):
        return self.shell.home_layout.get(self.connector()) or [[]]

    def populate(self, keep_page=True):
        current = int(round(self.carousel.get_position())) if keep_page else 0
        while self.carousel.get_n_pages():
            self.carousel.remove(self.carousel.get_nth_page(0))
        self.pages = []
        _index, infos = self.shell.app_index()
        for p, apps in enumerate(self._layout()):
            columns, _rows = homemodel.grid_shape(p, self.landscape)
            beside = p == 0 and self.landscape
            page = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL if beside else Gtk.Orientation.VERTICAL
            )
            page.add_css_class("home-page")
            page.set_hexpand(True)
            if p == 0:
                page.append(self._header(stacked=beside))
            grid = Gtk.Grid()
            grid.set_hexpand(True)
            grid.set_column_homogeneous(True)
            grid.set_row_homogeneous(True)
            grid.add_css_class("home-grid")
            grid.set_vexpand(True)
            grid.set_valign(Gtk.Align.FILL)
            cells = []
            for i in range(homemodel.capacity(p)):
                app = apps[i] if i < len(apps) else None
                cell = self._cell(app, infos.get(app) if app else None)
                grid.attach(cell, i % columns, i // columns, 1, 1)
                cells.append((app, cell))
            page.append(grid)
            self.carousel.append(page)
            self.pages.append((grid, cells))
        if self.editing:
            # A page to carry icons onto.
            page = Gtk.Box()
            page.add_css_class("home-page")
            page.set_hexpand(True)
            hint = Gtk.Label(label=_("Drop here for a new page"))
            hint.add_css_class("home-hint")
            hint.set_vexpand(True)
            hint.set_hexpand(True)
            page.append(hint)
            self.carousel.append(page)
            self.pages.append((None, []))
        current = max(0, min(current, self.carousel.get_n_pages() - 1))
        if self.carousel.get_n_pages():
            GLib.idle_add(self._scroll_to, current)
        self.update_widgets()

    def _scroll_to(self, index):
        if index < self.carousel.get_n_pages():
            self.carousel.scroll_to(self.carousel.get_nth_page(index), False)
        return False

    def _cell(self, app, info):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("home-cell")
        box.set_valign(Gtk.Align.CENTER)
        if app is None:
            box.set_size_request(-1, 96)
            return box
        icon_box = Gtk.Overlay()
        image = Gtk.Image()
        if info is not None and info.get_icon() is not None:
            image.set_from_gicon(info.get_icon())
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(ICON)
        image.add_css_class("home-icon")
        icon_box.set_child(image)
        icon_box.set_halign(Gtk.Align.CENTER)
        if self.editing:
            badge = Gtk.Button()
            badge.add_css_class("home-badge")
            badge.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            badge.set_can_focus(False)
            badge.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove from home screen")])
            badge.connect("clicked", lambda _b: self.remove_app(app))
            icon_box.add_overlay(badge)
        name = applist.info_label(info, app)
        # The short name ("Firefox ESR", not "Firefox ESR Web Browser"), on
        # up to two lines.
        generic = info.get_generic_name() if hasattr(info, "get_generic_name") else None
        short = applist.short_name(info.get_name(), name, generic) if info is not None else app
        label = Gtk.Label(label=short or name)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(2)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(10)
        # As wide at its minimum as at its natural size: a wrapping label is
        # otherwise taller at its minimum width (more lines) than at its
        # natural one, the page's minimum height came out larger than its
        # natural height, and GTK warned at every layout ("Trying to measure
        # HomeScreen for height of 686, but it needs at least 707").
        label.set_width_chars(10)
        label.set_justify(Gtk.Justification.CENTER)
        label.add_css_class("home-label")
        box.append(icon_box)
        box.append(label)
        box.update_property([Gtk.AccessibleProperty.LABEL], [name])
        click = Gtk.GestureClick()
        click.connect("released", lambda g, n, x, y: self._tapped(app, info))
        box.add_controller(click)
        if self.editing:
            box.add_css_class("editing")
        return box

    def _header(self, stacked=False):
        """The clock, the date and the two cards: side by side under the
        clock, or (stacked: beside the icons on a panel on its side) one
        under the other."""
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header.add_css_class("home-header")
        if stacked:
            header.set_valign(Gtk.Align.START)
        self.time = Gtk.Label(xalign=0)
        self.time.add_css_class("home-time")
        self.date = Gtk.Label(xalign=0)
        self.date.add_css_class("home-date")
        # The clock widget: a tap opens Clocks' alarms on this panel.
        clock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clock.set_halign(Gtk.Align.START)
        clock.append(self.time)
        clock.append(self.date)
        clock.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Opens the alarms")])
        tap = Gtk.GestureClick()
        tap.connect("released", lambda *_a: self._clock_tapped())
        clock.add_controller(tap)
        header.append(clock)

        cards = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL if stacked else Gtk.Orientation.HORIZONTAL,
            spacing=10 if stacked else 12,
            homogeneous=True,
        )
        cards.add_css_class("home-cards")

        self.weather_icon = Gtk.Image()
        self.weather_icon.set_pixel_size(30)
        self.weather_main = Gtk.Label(xalign=0)
        self.weather_main.add_css_class("card-main")
        self.weather_sub = Gtk.Label(xalign=0)
        self.weather_sub.add_css_class("card-sub")
        self.weather_sub.set_ellipsize(Pango.EllipsizeMode.END)
        weather = self._card(
            self.weather_icon,
            self.weather_main,
            self.weather_sub,
            lambda: self.launch_card("weather"),
        )

        self.battery_icon = Gtk.Image()
        self.battery_icon.set_pixel_size(30)
        self.battery_main = Gtk.Label(xalign=0)
        self.battery_main.add_css_class("card-main")
        self.battery_sub = Gtk.Label(xalign=0)
        self.battery_sub.add_css_class("card-sub")
        battery = self._card(
            self.battery_icon,
            self.battery_main,
            self.battery_sub,
            lambda: self.launch_card("battery"),
        )
        cards.append(weather)
        cards.append(battery)
        header.append(cards)
        self.header = header
        return header

    def _card(self, icon, main, sub, on_tap):
        card = Gtk.Button()
        card.add_css_class("home-card")
        card.set_can_focus(False)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        text.append(main)
        text.append(sub)
        row.append(icon)
        row.append(text)
        card.set_child(row)
        card.connect("clicked", lambda _b: on_tap())
        return card

    def update_widgets(self):
        """The clock, the weather and the battery (from the shell's state)."""
        if self.header is None:
            return
        now = GLib.DateTime.new_now_local()
        self.time.set_label(now.format(timefmt.hm()) or "")
        self.date.set_label(now.format(timefmt.date_long()) or "")
        weather = self.shell.weather
        if weather is not None and weather.place_set:
            main, sub = weather.lines
            self.weather_main.set_label(main)
            self.weather_sub.set_label(sub or _("Weather"))
            self.weather_icon.set_from_icon_name(weather.icon)
        else:
            self.weather_main.set_label(_("Weather"))
            self.weather_sub.set_label(_("Add a place"))
            self.weather_icon.set_from_icon_name("weather-few-clouds-symbolic")
        percent, state = self.shell.state.get("battery") or (None, None)
        self.battery_icon.set_from_icon_name(status.battery_icon(percent, state))
        self.battery_main.set_label(_("%d%%") % percent if percent is not None else "--")
        charging = state in ("Charging", "Full")
        self.battery_sub.set_label(_("Charging") if charging else _("Battery"))

    # -- launching ------------------------------------------------------------------

    def _tapped(self, app, info):
        if self.editing or self.carry is not None:
            return
        self.launch_info(app, info)

    def _clock_tapped(self):
        if self.editing or self.carry is not None:
            return
        self.shell.open_alarms(self.connector())

    def launch_card(self, card):
        """A widget tapped: its app, on its page when it has one
        (homemodel.CARDS: the battery card opens Settings' Battery page)."""
        desktop_id, action = homemodel.CARDS[card]
        _index, infos = self.shell.app_index()
        self.launch_info(desktop_id, infos.get(desktop_id), action)

    def launch_info(self, app, info, action=None):
        if info is None:
            return
        self.shell.expect_window(self.connector(), app)
        context = self.get_display().get_app_launch_context()
        try:
            if (
                action
                and isinstance(info, Gio.DesktopAppInfo)
                and action in (info.list_actions() or [])
            ):
                # The desktop file's action (e.g. `duo-settings battery`),
                # with the same launch context: the window comes here.
                info.launch_action(action, context)
            else:
                info.launch([], context)
            print(
                "duo-shell: home launched %s%s on %s"
                % (app, " (%s)" % action if action else "", self.connector()),
                flush=True,
            )
        except GLib.Error as e:
            print("duo-shell: launching %s failed: %s" % (app, e.message), flush=True)

    # -- editing --------------------------------------------------------------------

    def set_editing(self, on):
        if on == self.editing:
            return
        self.editing = on
        self.info.set_visible(self.caption is not None and not on)
        self.carousel.set_interactive(not on)
        self.done_reveal.set_reveal_child(on)
        if on:
            self.add_css_class("editing")
        else:
            self.remove_css_class("editing")
        self.populate()

    def remove_app(self, app):
        self.shell.set_home_layout(homemodel.remove(self.shell.home_layout, app))

    def _hit(self, x, y):
        """(page, index, app, cell) under a point of `main`, or None."""
        page = int(round(self.carousel.get_position()))
        if page >= len(self.pages):
            return None
        grid, cells = self.pages[page]
        for i, (app, cell) in enumerate(cells):
            ok, bounds = cell.compute_bounds(self.main)
            if ok and bounds.contains_point(_point(x, y)):
                return page, i, app, cell
        return None

    def _begin(self, gesture, x, y):
        self.press = {"x": x, "y": y, "hit": self._hit(x, y), "moved": False, "timer": None}
        if not self.editing:
            self.press["timer"] = GLib.timeout_add(LONG_PRESS_MS, self._long_press, gesture)

    def _long_press(self, gesture):
        press = self.press
        if press is None:
            return False
        press["timer"] = None
        if press["moved"]:
            return False
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        press["moved"] = True  # no tap on the release
        # What was under the finger when it went down (the page is built
        # anew for the edit mode).
        hit = press["hit"]
        self.set_editing(True)
        if hit is not None and hit[2] is not None:
            self._pick_up(hit[2], press["x"], press["y"])
        return False

    def _update(self, gesture, dx, dy):
        press = self.press
        if press is None:
            return
        x, y = press["x"] + dx, press["y"] + dy
        if self.carry is not None:
            self._carry_to(x, y)
            return
        far = (dx * dx + dy * dy) ** 0.5 > PICK_SLOP
        if not far:
            return
        if not press["moved"]:
            press["moved"] = True
            if press["timer"] is not None:
                GLib.source_remove(press["timer"])
                press["timer"] = None
            hit = press["hit"]
            if self.editing and hit is not None and hit[2] is not None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self._pick_up(hit[2], press["x"], press["y"])
                self._carry_to(x, y)
                return
        if not self.editing and dy < -90 and abs(dy) > 2 * abs(dx):
            # Swipe up on the page: the app grid.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.press = None
            self.shell.open_grid_on(self.connector())

    def _end(self, gesture, dx, dy):
        press, self.press = self.press, None
        if press is not None and press.get("timer"):
            GLib.source_remove(press["timer"])
        if self.carry is not None:
            self._drop()

    def _pick_up(self, app, x, y):
        _index, infos = self.shell.app_index()
        info = infos.get(app)
        image = Gtk.Image()
        if info is not None and info.get_icon() is not None:
            image.set_from_gicon(info.get_icon())
        else:
            image.set_from_icon_name("application-x-executable")
        image.set_pixel_size(ICON + 12)
        image.add_css_class("home-floating")
        self.floating.put(image, x - (ICON + 12) / 2, y - (ICON + 12) / 2)
        self.carry = {"app": app, "widget": image, "slot": None}
        self._hide_carried()
        print("duo-shell: home: carrying %s" % app, flush=True)

    def _carry_to(self, x, y):
        carry = self.carry
        self.floating.move(carry["widget"], x - (ICON + 12) / 2, y - (ICON + 12) / 2)
        width = self.main.get_width()
        flip = homemodel.edge_flip(x, width)
        if flip and self.flip_timer is None:
            self.flip_timer = GLib.timeout_add(FLIP_MS, self._flip, flip)
        elif not flip and self.flip_timer is not None:
            GLib.source_remove(self.flip_timer)
            self.flip_timer = None
        page = int(round(self.carousel.get_position()))
        if page >= len(self.pages) or self.pages[page][0] is None:
            carry["slot"] = (page, 0)
            return
        grid, _cells = self.pages[page]
        ok, bounds = grid.compute_bounds(self.main)
        if not ok:
            return
        columns, rows = homemodel.grid_shape(page, self.landscape)
        slot = homemodel.slot_at(
            x - bounds.get_x(),
            y - bounds.get_y(),
            bounds.get_width(),
            bounds.get_height(),
            columns,
            rows,
        )
        if (page, slot) != carry["slot"]:
            carry["slot"] = (page, slot)
            layout = homemodel.move(
                self.shell.home_layout, carry["app"], self.connector(), page, slot
            )
            self.shell.home_layout = layout
            self.populate()
            self._hide_carried()

    def _hide_carried(self):
        for _grid, cells in self.pages:
            for app, cell in cells:
                if self.carry is not None and app == self.carry["app"]:
                    cell.set_opacity(0.0)

    def _flip(self, way):
        self.flip_timer = None
        page = int(round(self.carousel.get_position())) + way
        if 0 <= page < self.carousel.get_n_pages():
            self.carousel.scroll_to(self.carousel.get_nth_page(page), True)
        return False

    def _drop(self):
        carry, self.carry = self.carry, None
        if self.flip_timer is not None:
            GLib.source_remove(self.flip_timer)
            self.flip_timer = None
        self.floating.remove(carry["widget"])
        slot = carry.get("slot")
        if slot is not None:
            page, index = slot
            layout = homemodel.move(
                self.shell.home_layout, carry["app"], self.connector(), page, index
            )
            self.shell.set_home_layout(layout)
        else:
            self.shell.set_home_layout(self.shell.home_layout)


def _point(x, y):
    from gi.repository import Graphene

    return Graphene.Point().init(x, y)
