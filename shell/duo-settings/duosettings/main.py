"""Duo Settings: the window (a list of pages, phone style on one panel, side
by side when spanned) and the widgets the pages share."""

import gettext
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import penscroll  # noqa: E402

APP_ID = "org.duo2os.Settings"
gettext.textdomain("duo-settings")
_ = gettext.gettext


def run_async(argv, done=None):
    """Run a command without blocking the window; done(ok, stdout)."""
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        )
    except GLib.Error:
        if done:
            done(False, "")
        return

    def finish(p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
            ok = p.get_successful()
        except GLib.Error:
            ok, out = False, ""
        if done:
            done(ok, out or "")

    proc.communicate_utf8_async(None, None, finish)


def run_with_input(argv, text, done=None):
    """Run a command with `text` on its stdin (a PIN never goes on a command
    line); done(exit code, stdout)."""
    try:
        proc = Gio.Subprocess.new(
            argv,
            Gio.SubprocessFlags.STDIN_PIPE
            | Gio.SubprocessFlags.STDOUT_PIPE
            | Gio.SubprocessFlags.STDERR_SILENCE,
        )
    except GLib.Error:
        if done:
            done(-1, "")
        return

    def finish(p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
            code = p.get_exit_status() if p.get_if_exited() else -1
        except GLib.Error:
            code, out = -1, ""
        if done:
            done(code, out or "")

    proc.communicate_utf8_async(text, None, finish)


class Page(Adw.NavigationPage):
    """A settings page: a header bar over a preferences page. `shown()` and
    `hidden()` follow the navigation."""

    icon = "preferences-system-symbolic"
    title = ""
    subtitle = ""

    def __init__(self, window):
        super().__init__(title=self.title, tag=self.__class__.__name__)
        self.window = window
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)
        self.prefs = Adw.PreferencesPage()
        self.toolbar.set_content(self.prefs)
        self.set_child(self.toolbar)
        self.timers = []
        self.build()

    def build(self):
        pass

    def group(self, title=None, description=None):
        g = Adw.PreferencesGroup()
        if title:
            g.set_title(title)
        if description:
            g.set_description(description)
        self.prefs.add(g)
        return g

    def every(self, seconds, fn):
        """Call fn now and every `seconds` while the page is shown."""
        fn()

        def tick():
            # An exception in a GLib timeout callback removes the timeout: the page would
            # stop following its state until it is opened again. Log it and keep going.
            try:
                fn()
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()
            return True

        self.timers.append(GLib.timeout_add_seconds(seconds, tick))

    def shown(self):
        pass

    def hidden(self):
        for t in self.timers:
            GLib.source_remove(t)
        self.timers = []

    def toast(self, text):
        self.window.toast(text)


def switch_row(title, subtitle=None, active=False, on_toggle=None):
    row = Adw.SwitchRow(use_markup=False)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_active(active)
    row._quiet = False
    if on_toggle:

        def changed(r, _p):
            if not r._quiet:
                on_toggle(r.get_active())

        row.connect("notify::active", changed)
    return row


def set_quietly(row, active):
    """Set a switch row without calling its toggle handler."""
    row._quiet = True
    row.set_active(active)
    row._quiet = False


# A value up to this many characters is never cut short beside a combo row's
# title; a longer one (a sound device's name) may ellipsize past it.
VALUE_CHARS = 24


def _value_setup(_factory, item):
    label = Gtk.Label(xalign=0, valign=Gtk.Align.CENTER)
    item.set_child(label)


def _value_bind(_factory, item):
    label = item.get_child()
    obj = item.get_item()
    text = obj.get_string() if hasattr(obj, "get_string") else str(obj or "")
    label.set_label(text)
    if len(text) <= VALUE_CHARS:
        label.set_ellipsize(Pango.EllipsizeMode.NONE)
        label.set_width_chars(-1)
        label.set_max_width_chars(-1)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_width_chars(VALUE_CHARS)
        label.set_max_width_chars(VALUE_CHARS)


def keep_value_readable(row):
    """An Adw.ComboRow shows its value in a label that may shrink to "…":
    beside a subtitle GTK gives the title column its width first, and on a
    538 px panel the Hotspot page's Band read "5…". The value beside the
    title gets a label of its own that never cuts a value of up to
    VALUE_CHARS characters (the title and the subtitle wrap instead); the
    popup's list keeps libadwaita's own (its check mark)."""
    default = row.get_factory()
    if default is not None and row.get_list_factory() is None:
        row.set_list_factory(default)
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", _value_setup)
    factory.connect("bind", _value_bind)
    row.set_factory(factory)
    return row


def combo_row(title, labels, selected=0, on_select=None, subtitle=None):
    row = Adw.ComboRow(use_markup=False, model=Gtk.StringList.new(labels))
    keep_value_readable(row)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_selected(selected)
    row._quiet = False
    if on_select:

        def changed(r, _p):
            if not r._quiet:
                on_select(r.get_selected())

        row.connect("notify::selected", changed)
    return row


def select_quietly(row, index):
    row._quiet = True
    row.set_selected(index)
    row._quiet = False


class SliderRow(Adw.PreferencesRow):
    """A row with a title and a full-width slider between two icons."""

    def __init__(self, title, low_icon, high_icon, on_change, lower=0, upper=100):
        super().__init__(title=title)
        self.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(10)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        head = Gtk.Box(spacing=6)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        head.append(label)
        self.value_label = Gtk.Label(xalign=1)
        self.value_label.add_css_class("dim-label")
        self.value_label.add_css_class("numeric")
        head.append(self.value_label)
        box.append(head)
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Image.new_from_icon_name(low_icon))
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, 1)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        line.append(self.scale)
        line.append(Gtk.Image.new_from_icon_name(high_icon))
        box.append(line)
        self.set_child(box)
        self.on_change = on_change
        self._quiet = False
        self._pending = None
        self.scale.connect("value-changed", self._changed)

    def _changed(self, scale):
        v = int(round(scale.get_value()))
        self.value_label.set_label("%d %%" % v)
        if self._quiet:
            return
        # At most one change every 80 ms while the finger moves.
        if self._pending is None:
            self._pending = GLib.timeout_add(80, self._flush)

    def _flush(self):
        self._pending = None
        self.on_change(int(round(self.scale.get_value())))
        return False

    def set_value(self, v):
        self._quiet = True
        self.scale.set_value(v)
        self.value_label.set_label("%d %%" % v)
        self._quiet = False


def ask(window, heading, body, ok_label, on_ok, destructive=False, extra=None, cancel_label=None):
    """A question with Cancel and `ok_label`; on_ok() when confirmed."""
    d = Adw.AlertDialog.new(heading, body)
    d.add_response("cancel", cancel_label or _("Cancel"))
    d.add_response("ok", ok_label)
    d.set_response_appearance(
        "ok",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    d.set_default_response("ok")
    d.set_close_response("cancel")
    if extra is not None:
        d.set_extra_child(extra)
    d.connect("response", lambda _d, r: on_ok() if r == "ok" else None)
    d.present(window)
    return d


def respond(dialog, response):
    """Answer an Adw.AlertDialog as its button would: it has no response()
    method (Adw.MessageDialog had one), so Enter in a dialog's entry
    emits the signal and closes it."""
    dialog.emit("response", response)
    dialog.force_close()


def inform(window, heading, body):
    """A message with one OK button."""
    d = Adw.AlertDialog.new(heading, body)
    d.add_response("ok", _("OK"))
    d.set_default_response("ok")
    d.set_close_response("ok")
    d.present(window)
    return d


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Settings"))
        self.set_default_size(538, 709)
        self.set_size_request(320, 400)
        from . import pages

        self.pages = [cls(self) for cls in pages.PAGES]
        self.current = None

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(240)
        self.split.set_max_sidebar_width(300)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        penscroll.install(self)  # the pen scrolls the pages like a finger (GTK: touch only)

        side = Adw.ToolbarView()
        side.add_top_bar(Adw.HeaderBar())
        self.list = Gtk.ListBox()
        self.list.add_css_class("navigation-sidebar")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for page in self.pages:
            row = Adw.ActionRow(use_markup=False)
            row.set_title(page.title)  # after use_markup=False: a "&" is then no markup
            if page.subtitle:
                row.set_subtitle(page.subtitle)
            icon = Gtk.Image.new_from_icon_name(page.icon)
            icon.add_css_class("settings-icon")
            row.add_prefix(icon)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.set_activatable(True)
            row._page = page
            self.list.append(row)
        self.list.connect("row-activated", lambda _l, row: self.open(row._page))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        side.set_content(scroller)
        self.sidebar = Adw.NavigationPage(title=_("Settings"), child=side)
        self.split.set_sidebar(self.sidebar)
        self.split.set_content(self.pages[0])
        self.split.connect("notify::show-content", self._show_content)
        self.split.connect("notify::collapsed", self._collapsed)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        self.connect("close-request", self._closing)

    def open(self, page):
        if self.current is not None and self.current is not page:
            self.current.hidden()
        self.split.set_content(page)
        self.split.set_show_content(True)
        if self.current is not page:
            self.current = page
            page.shown()

    def open_tag(self, tag):
        for i, page in enumerate(self.pages):
            if (
                page.get_tag().lower() == tag.lower()
                or page.get_tag().lower() == tag.lower() + "page"
            ):
                self.list.select_row(self.list.get_row_at_index(i))
                self.open(page)
                return True
        return False

    def _show_content(self, split, _p):
        # Back to the list on a phone: the page is hidden.
        if split.get_collapsed() and not split.get_show_content() and self.current is not None:
            self.current.hidden()
            self.current = None
            self.list.unselect_all()
            self.clear_highlight()

    def clear_highlight(self):
        """After a page closes on a phone no row may stay lit: the pointer a
        finger leaves behind sits where the back arrow was, and GTK hands
        the focus back to a row. A keyboard (Tab) still brings the focus ring."""
        self.set_focus(None)
        GLib.idle_add(lambda: self.set_focus(None) or False)

    def _collapsed(self, split, _p):
        # Touch layout: no hover highlight (see CSS), no selection on the list.
        if split.get_collapsed():
            self.add_css_class("touch-layout")
            self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        else:
            self.remove_css_class("touch-layout")
            self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))

    def _closing(self, _w):
        for page in self.pages:
            page.hidden()
            close = getattr(page, "close", None)
            if close:
                close()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.page_arg = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        self.page_arg = args[0] if args else None
        self.activate()
        return 0

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
        if self.page_arg:
            self.window.open_tag(self.page_arg)
            self.page_arg = None
        self.window.present()


CSS = """
.touch-layout row.activatable:hover:not(:active) { background-color: transparent; }
.touch-layout row.activatable:focus:not(:focus-visible) { background-color: transparent; }
.settings-icon { -gtk-icon-size: 20px; margin-right: 4px; }
.navigation-sidebar row { min-height: 52px; }
.wp-frame { border-radius: 14px; border: 2px solid transparent; }
.wp-default { background-image: radial-gradient(ellipse at 50% -10%, rgba(53, 132, 228, 0.55) 0%, rgba(53, 132, 228, 0.12) 45%, transparent 75%); background-color: #0d0f14; }
.wp-tile.selected .wp-frame { border-color: @accent_bg_color; }
.wp-tile { padding: 4px; }
.big-status { font-size: 1.6em; font-weight: 800; }
.code-label { font-size: 2.2em; font-weight: 800; letter-spacing: 4px; }
"""


def main():
    return App().run(sys.argv)
