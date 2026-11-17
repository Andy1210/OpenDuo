"""Settings > About, like Android's "About phone": a short page with four
sub-pages (Status, Software information, Hardware information, Legal).

Values are never selectable (a tap must not put a text cursor in a row):
a long press on a value copies it, with a toast. Data: duosettings/about.py.
The identifiers (IMEI, serial number...) come from root's helper
(`duo-settings-helper identity`) and are shown in the app only."""

import threading
import traceback

from gi.repository import Adw, GLib, Gtk

from . import about, system
from .main import Page, _
from .safetimer import keep_running_after


def info_row(window, label, value):
    """A label over its value; long press copies the value. Not selectable,
    not focusable: nothing in it takes a tap."""
    row = Adw.ActionRow(use_markup=False, title=label, subtitle=value)
    row.add_css_class("property")
    row.set_activatable(False)
    row.set_focusable(False)
    for name in ("set_title_selectable", "set_subtitle_selectable"):
        getattr(row, name, lambda _x: None)(False)
    press = Gtk.GestureLongPress()
    press.set_touch_only(False)

    def copy(_g, _x, _y):
        text = value if isinstance(value, str) else str(value)
        row.get_clipboard().set(text)
        window.toast(_("Copied"))

    press.connect("pressed", copy)
    row.add_controller(press)
    row._value = value
    return row


class SubPage(Adw.NavigationPage):
    """One About sub-page: a header bar over groups of info rows; `load()`
    returns the groups (it may block: it runs in a thread) and `refresh`
    seconds, if set, re-reads them while the page is shown."""

    refresh = 0

    def __init__(self, window, title, load):
        super().__init__(title=title, tag=title)
        self.window = window
        self.load = load
        self.groups = []
        self.last = None
        self.timer = None
        self.busy = False
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.prefs = Adw.PreferencesPage()
        view.set_content(self.prefs)
        self.set_child(view)
        self.connect("showing", lambda _p: self.start())
        self.connect("hidden", lambda _p: self.stop())

    def start(self):
        self.update()
        if self.refresh and self.timer is None:
            self.timer = GLib.timeout_add_seconds(self.refresh, keep_running_after(self.update))

    def stop(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

    def update(self):
        if self.busy:
            return

        def work():
            try:
                groups = self.load()
            except Exception:  # noqa: BLE001 - busy must come back, or the page never updates
                traceback.print_exc()
                groups = self.last
            GLib.idle_add(self.apply, groups)

        self.busy = True
        threading.Thread(target=work, daemon=True).start()

    def apply(self, groups):
        self.busy = False
        if groups == self.last:
            return False
        self.last = groups
        self.set_groups(groups)
        return False

    def lead(self):
        """A group above the info rows (a page with a link in it), or None."""
        return None

    def set_groups(self, groups):
        for g in self.groups:
            self.prefs.remove(g)
        self.groups = []
        lead = self.lead()
        if lead is not None:
            self.prefs.add(lead)
            self.groups.append(lead)
        for title, rows in groups:
            g = Adw.PreferencesGroup()
            if title:
                g.set_title(title)
            for label, value in rows:
                g.add(info_row(self.window, label, value))
            self.prefs.add(g)
            self.groups.append(g)


class StatusPage(SubPage):
    refresh = 10

    def __init__(self, window):
        self.ident = None
        super().__init__(window, _("Status"), self.gather)

    def gather(self):
        if about.fake():
            return about.status_groups()
        modem = about.modem_info()
        if self.ident is None:
            self.ident = about.identity() or {}
        return about.status_groups(self.ident, modem)


class LicencePage(Adw.NavigationPage):
    """Every installed package with its licence(s); tap one for its copyright
    file. Built in chunks so the page opens at once."""

    def __init__(self, window):
        super().__init__(title=_("Open-source licences"), tag="licences")
        self.window = window
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search packages or licences"))
        self.search.set_margin_start(12)
        self.search.set_margin_end(12)
        self.search.set_margin_top(8)
        self.search.set_margin_bottom(8)
        box.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.set_margin_start(12)
        self.list.set_margin_end(12)
        self.list.set_margin_bottom(12)
        self.list.set_filter_func(self.match)
        self.list.connect("row-activated", lambda _l, r: self.show_text(r))
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("gpu-scroll")
        scroller.set_child(self.list)
        box.append(scroller)
        view.set_content(box)
        self.set_child(view)
        self.search.connect("search-changed", lambda _e: self.list.invalidate_filter())
        self.items = []
        self.loaded = False
        self.connect("showing", lambda _p: self.load())

    def load(self):
        if self.loaded:
            return
        self.loaded = True

        def work():
            items = about.fake_licences() if about.fake() else about.licence_packages()
            GLib.idle_add(self.fill, items)

        threading.Thread(target=work, daemon=True).start()

    def fill(self, items):
        self.items = items
        todo = list(items)

        def chunk():
            for name, lic, path in todo[:80]:
                row = Adw.ActionRow(use_markup=False, title=name, subtitle=lic)
                row.set_activatable(True)
                row.set_title_lines(1)
                row.set_subtitle_lines(2)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row._path, row._key = path, (name + " " + lic).lower()
                self.list.append(row)
            del todo[:80]
            return bool(todo)

        # One source drains the list: the first batch now, the rest every 30 ms.
        if chunk():
            GLib.timeout_add(30, chunk)
        return False

    def match(self, row):
        q = self.search.get_text().strip().lower()
        return not q or q in row._key

    def show_text(self, row):
        text = about.read_copyright(row._path) if row._path else _("Example licence text.")
        d = Adw.Dialog(title=row.get_title(), content_width=520, content_height=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        label = Gtk.Label(
            label=text or _("No copyright file."), wrap=True, xalign=0, selectable=False
        )
        label.add_css_class("monospace")
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(label)
        view.set_content(scroller)
        d.set_child(view)
        d.present(self.window)


class LegalPage(SubPage):
    def __init__(self, window, nav):
        self.nav = nav
        super().__init__(window, _("Legal"), self.gather)
        self.licences = LicencePage(window)

    def gather(self):
        return [
            (
                _("duo2-os"),
                [
                    (
                        _("Licence"),
                        _(
                            "The duo2-os components (the compositor, the shell, Settings and "
                            "the other services) have no licence file yet."
                        ),
                    )
                ],
            ),
            (
                _("Where the licences are"),
                [
                    (_("Each package"), "/usr/share/doc/PACKAGE/copyright"),
                    (
                        _("Common licences (GPL, LGPL, Apache, MPL...)"),
                        "/usr/share/common-licenses",
                    ),
                ],
            ),
        ]

    def lead(self):
        g = Adw.PreferencesGroup(title=_("Open-source software"))
        row = Adw.ActionRow(
            use_markup=False,
            title=_("Open-source licences"),
            subtitle=_("Every installed package and its licence"),
        )
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.set_activatable(True)
        row.connect("activated", lambda _r: self.nav.push(self.licences))
        g.add(row)
        return g


class AboutPage(Page):
    icon = "help-about-symbolic"
    title = _("About")

    def build(self):
        self.set_child(None)
        self.nav = Adw.NavigationView()
        root = Adw.NavigationPage(title=_("About"), tag="about-root", child=self.toolbar)
        self.nav.add(root)
        self.set_child(self.nav)
        self.subpages = [
            (
                _("Status"),
                _("Mobile network, identifiers, addresses, serial number"),
                StatusPage(self.window),
            ),
            (
                _("Software information"),
                _("Versions of OpenDuo, the kernel and the modem firmware"),
                SubPage(self.window, _("Software information"), self.software),
            ),
            (
                _("Hardware information"),
                _("Processor, memory, storage, battery, displays, cameras"),
                SubPage(self.window, _("Hardware information"), about.hardware_groups),
            ),
            (_("Legal"), _("Open-source licences"), LegalPage(self.window, self.nav)),
        ]
        name = (
            "Example Phone"
            if about.fake()
            else (
                system.read_text("/sys/devices/soc0/machine").strip() or "Microsoft Surface Duo 2"
            )
        )
        self.prefs.add(self.heading())
        dev = self.group(_("Device"))
        dev.add(info_row(self.window, _("Device name"), name))
        g = self.group()
        for title, sub, page in self.subpages:
            row = Adw.ActionRow(use_markup=False, title=title, subtitle=sub)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=page: self.nav.push(p))
            g.add(row)

    def heading(self):
        """OpenDuo, its version large, then build id, kernel, Debian base."""
        info = about.header()
        g = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        name = Gtk.Label(label=info["name"])
        name.add_css_class("title-1")
        box.append(name)
        version = Gtk.Label(label=info["version"])
        version.add_css_class("title-3")
        box.append(version)
        for line in info["lines"]:
            lab = Gtk.Label(label=line)
            lab.add_css_class("dim-label")
            box.append(lab)
        g.add(box)
        return g

    def software(self):
        from gi.repository import Adw as A

        gtk = "%d.%d.%d / %d.%d.%d" % (
            Gtk.get_major_version(),
            Gtk.get_minor_version(),
            Gtk.get_micro_version(),
            A.get_major_version(),
            A.get_minor_version(),
            A.get_micro_version(),
        )
        wc = system.wc_state()
        modem = None if about.fake() else about.modem_info()
        return about.software_groups(gtk, wc.get("version", "?"), modem)

    def hidden(self):
        super().hidden()
        self.nav.pop_to_tag("about-root")
