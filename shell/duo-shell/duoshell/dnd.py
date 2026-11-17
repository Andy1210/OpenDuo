"""Do not disturb (dndmodel.py says what it means): the switch (GNOME's
org.gnome.desktop.notifications show-banners, which Duo Settings and the
quick menu's tile set) and what it changes outside the shell: GNOME Calls'
ringer, silenced through feedbackd's per-app profile while it is on.
Applied whenever the switch changes and when the shell starts (a shell
that died with it on leaves the profile as the switch says)."""

from gi.repository import Gio

from . import dndmodel as model

NOTIFY_SCHEMA = "org.gnome.desktop.notifications"


def log(*args):
    print("duo-shell: dnd:", *args, flush=True)


def _schema(name):
    source = Gio.SettingsSchemaSource.get_default()
    return source is not None and source.lookup(name, True) is not None


class Dnd:
    def __init__(self, changed):
        self.changed = changed
        self.settings = Gio.Settings.new(NOTIFY_SCHEMA) if _schema(NOTIFY_SCHEMA) else None
        self.calls_profile = None
        if _schema(model.APP_SCHEMA):
            self.calls_profile = Gio.Settings.new_with_path(
                model.APP_SCHEMA, model.app_profile_path(model.CALLS_APP_ID)
            )
        else:
            log("no feedbackd per-app schema: Calls rings in do not disturb too")
        if self.settings is not None:
            self.settings.connect("changed::show-banners", lambda *_a: self._switched())
        self.apply()

    @property
    def available(self):
        return self.settings is not None

    @property
    def on(self):
        return self.settings is not None and not self.settings.get_boolean("show-banners")

    def set(self, on):
        if self.settings is not None:
            self.settings.set_boolean("show-banners", not on)

    def toggle(self):
        self.set(not self.on)

    def _switched(self):
        log("do not disturb %s" % ("on" if self.on else "off"))
        self.apply()
        self.changed()

    def apply(self):
        """Calls' ringer: silent while do not disturb is on, feedbackd's
        default (the sound mode decides) otherwise."""
        if self.calls_profile is None:
            return
        if self.on:
            if self.calls_profile.get_string("profile") != "silent":
                self.calls_profile.set_string("profile", "silent")
        elif self.calls_profile.get_user_value("profile") is not None:
            self.calls_profile.reset("profile")

    def prefs(self):
        return model.load()
