"""The Sound page's Ringtone group (ringtone.py says how it reaches
feedbackd): a list of the installed ringtones, "Your own sound…" (a file,
converted when needed), and a play button to hear the choice."""

import os
import shutil

from gi.repository import Adw, Gio, GLib, Gtk

from . import ringtone
from .main import _, keep_value_readable, run_async, select_quietly


def _schema(name):
    source = Gio.SettingsSchemaSource.get_default()
    return source is not None and source.lookup(name, True) is not None


class RingtoneGroup:
    def __init__(self, page):
        self.page = page
        self.settings = Gio.Settings.new(ringtone.SCHEMA) if _schema(ringtone.SCHEMA) else None
        self.options = []
        self.labels = None
        self.player = None
        g = page.group(_("Ringtone"), _("What calls ring with, in the sound modes with sound."))
        self.row = keep_value_readable(
            Adw.ComboRow(use_markup=False, model=Gtk.StringList.new([]))
        )
        self.row.set_title(_("Ringtone"))
        self.row._quiet = False
        self.row.connect("notify::selected", self._selected)
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_button.set_valign(Gtk.Align.CENTER)
        self.play_button.add_css_class("flat")
        self.play_button.set_tooltip_text(_("Play"))
        self.play_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Play the ringtone")])
        self.play_button.connect("clicked", lambda _b: self.play())
        self.row.add_suffix(self.play_button)
        g.add(self.row)
        own = Adw.ActionRow(use_markup=False)
        own.set_title(_("Use your own sound…"))
        own.set_subtitle(_("A music or sound file (its first minute)"))
        own.set_activatable(True)
        own.add_suffix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
        own.connect("activated", lambda _r: self.choose())
        g.add(own)
        self.own_row = own
        if self.settings is None:
            g.set_sensitive(False)

    # -- what is there -------------------------------------------------------------------

    def refresh(self):
        if self.settings is None:
            return
        self.options = ringtone.choices(own=ringtone.own_path())
        labels = [_(label) if key == "default" else label for key, label, _p in self.options]
        if labels != self.labels:
            # (never from the row's own selection handler: a new model there
            # hangs the combo row)
            self.labels = labels
            self.row._quiet = True
            self.row.set_model(Gtk.StringList.new(labels))
            self.row._quiet = False
        now = ringtone.current_path(self.settings.get_string("theme"))
        select_quietly(self.row, ringtone.selected(self.options, now))

    def _selected(self, row, _pspec):
        if row._quiet:
            return
        i = row.get_selected()
        if 0 <= i < len(self.options):
            key, _label, path = self.options[i]
            # After the row's own handling of the selection.
            GLib.idle_add(lambda: self.apply(None if key == "default" else path) and False)

    # -- the choice ----------------------------------------------------------------------

    def apply(self, path):
        """feedbackd's theme: our user theme ringing with `path`, or none
        (the default ringtone)."""
        if self.settings is None:
            return
        try:
            if path is None:
                self.settings.reset("theme")
                ringtone.remove_themes()
            else:
                name = ringtone.write_theme(path, self.settings.get_string("theme"))
                self.settings.set_string("theme", name)
                ringtone.remove_themes(keep=name)
        except OSError:
            self.page.toast(_("Could not save the ringtone"))
        self.refresh()

    def choose(self):
        flt = Gtk.FileFilter()
        flt.set_name(_("Sounds and music"))
        flt.add_mime_type("audio/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog = Gtk.FileDialog(title=_("Choose a ringtone"), filters=filters, default_filter=flt)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if path:
                self.take_own(path)

        dialog.open(self.page.window, None, done)

    def take_own(self, src):
        """Copy (or convert) the file to ~/.local/share/duo2/ringtones and
        ring with it."""
        d = ringtone.own_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            self.page.toast(_("Could not save the ringtone"))
            return
        dst = os.path.join(d, ringtone.OWN_NAME)
        tmp = dst + ".new.oga"
        if not ringtone.needs_conversion(src):
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
            except OSError:
                self.page.toast(_("Could not read that file"))
                return
            self.apply(dst)
            return
        self.page.toast(_("Preparing the ringtone…"))

        def converted(ok, _out):
            if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, dst)
                self.apply(dst)
                return
            try:
                os.unlink(tmp)
            except OSError:
                pass
            self.page.toast(_("That file has no sound that can be used"))

        run_async(ringtone.convert_argv(src, tmp), converted)

    # -- hearing it ----------------------------------------------------------------------

    def play(self):
        """Play the chosen ringtone once (a second tap stops it)."""
        if self.player is not None:
            self.stop()
            return
        i = self.row.get_selected()
        path = self.options[i][2] if 0 <= i < len(self.options) else None
        if not path:
            return
        try:
            self.player = Gio.Subprocess.new(
                ["paplay", "--property=media.role=event", path], Gio.SubprocessFlags.STDERR_SILENCE
            )
        except GLib.Error:
            self.page.toast(_("Cannot play sounds"))
            return
        self.play_button.set_icon_name("media-playback-stop-symbolic")
        self.player.wait_async(None, lambda p, r: self._played(p))

    def _played(self, proc):
        if proc is self.player:
            self.player = None
            self.play_button.set_icon_name("media-playback-start-symbolic")

    def stop(self):
        if self.player is not None:
            self.player.force_exit()
            self.player = None
            self.play_button.set_icon_name("media-playback-start-symbolic")
