"""Settings > Keyboard: the keyboard layouts, in order (the first is in force
at the start), added and removed. The list is the system's
(`org.gnome.desktop.input-sources`, keyboard.py): the on-screen keyboard reads
it, and the shell sends it to duo-wc for a keyboard you connect and for
laptop mode's keyboard (whose layout key switches between them, and adds one
from its list)."""

from gi.repository import Adw, Gio, GLib, Gtk

from . import keyboard
from .main import Page, _


def _schema_ok():
    source = Gio.SettingsSchemaSource.get_default()
    return source is not None and source.lookup(keyboard.SCHEMA, True) is not None


class KeyboardPage(Page):
    icon = "input-keyboard-symbolic"
    title = _("Keyboard")

    def build(self):
        self.settings = Gio.Settings.new(keyboard.SCHEMA) if _schema_ok() else None
        self.list_group = self.group(
            _("Layouts"),
            _(
                "For the on-screen keyboard, a keyboard you connect and laptop mode's keyboard. The first "
                "one is used at the start; the layout key on the keyboard switches to the next."
            ),
        )
        self.rows = []
        self.add_group = self.group(_("Add a layout"))
        self.add_rows = []
        if self.settings is not None:
            self.settings.connect("changed::" + keyboard.KEY, lambda *_a: self.refresh())
        self.refresh()

    def shown(self):
        self.refresh()

    def sources(self):
        if self.settings is None:
            return []
        return self.settings.get_value(keyboard.KEY).unpack()

    def save(self, sources):
        if sources is None or self.settings is None:
            return
        self.settings.set_value(keyboard.KEY, GLib.Variant("a(ss)", sources))

    def refresh(self):
        for row in self.rows:
            self.list_group.remove(row)
        for row in self.add_rows:
            self.add_group.remove(row)
        self.rows, self.add_rows = [], []
        sources = self.sources()
        if self.settings is None:
            row = Adw.ActionRow(title=_("Keyboard layouts cannot be set here"), use_markup=False)
            self.list_group.add(row)
            self.rows.append(row)
            return
        for i, source in enumerate(sources):
            title, suggestions = keyboard.describe(source)
            row = Adw.ActionRow(title=title, use_markup=False)
            if suggestions:
                row.set_subtitle(_("With word suggestions in %s") % suggestions)
            if i == 0:
                row.set_subtitle(
                    _("In use at the start")
                    + (" · " + row.get_subtitle() if row.get_subtitle() else "")
                )
            for icon, tip, new in (
                ("go-up-symbolic", _("Move up"), keyboard.move(sources, i, -1)),
                ("go-down-symbolic", _("Move down"), keyboard.move(sources, i, 1)),
                ("list-remove-symbolic", _("Remove"), keyboard.remove(sources, i)),
            ):
                b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tip)
                b.add_css_class("flat")
                b.set_size_request(44, 44)
                b.set_sensitive(new is not None)
                b.connect("clicked", lambda _b, n=new: self.save(n))
                row.add_suffix(b)
            self.list_group.add(row)
            self.rows.append(row)
        if not keyboard.can_add(sources):
            row = Adw.ActionRow(title=_("A keyboard takes at most four layouts"), use_markup=False)
            self.add_group.add(row)
            self.add_rows.append(row)
            return
        expander = Adw.ExpanderRow(title=_("Choose a layout"), use_markup=False)
        for layout, label in keyboard.addable(sources):
            row = Adw.ActionRow(title=label, use_markup=False, activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
            row.connect(
                "activated", lambda _r, lt=layout: self.save(keyboard.add(self.sources(), lt))
            )
            expander.add_row(row)
        self.add_group.add(expander)
        self.add_rows.append(expander)
