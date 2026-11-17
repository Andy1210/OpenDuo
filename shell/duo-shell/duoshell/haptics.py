"""Sound and vibration for the shell (hapticsmodel.py decides): the quick
menu's Sound tile (the four sound modes: feedbackd's profile and
max-haptic-strength) and the feedback for a new notification, through
feedbackd on the session bus (TriggerFeedback, as libfeedback does;
asynchronous, never raises into GTK)."""

from gi.repository import Gio, GLib

from . import hapticsmodel as model

SCHEMA = "org.sigxcpu.feedbackd"
FB = ("org.sigxcpu.Feedback", "/org/sigxcpu/Feedback", "org.sigxcpu.Feedback")
APP_ID = "org.duo2os.Shell"


def log(*args):
    print("duo-shell: haptics:", *args, flush=True)


class Haptics:
    def __init__(self, changed):
        self.changed = changed
        self.settings = None
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(SCHEMA, True) is not None:
            self.settings = Gio.Settings.new(SCHEMA)
            self.settings.connect("changed::max-haptic-strength", lambda *_a: self.changed())
            self.settings.connect("changed::profile", lambda *_a: self.changed())
        else:
            log("no feedbackd schema: no vibration switch")
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            pass

    @property
    def available(self):
        return self.settings is not None

    @property
    def on(self):
        return self.available and model.is_on(self.settings.get_double("max-haptic-strength"))

    @property
    def mode(self):
        if not self.available:
            return None
        return model.mode_of(
            self.settings.get_string("profile"), self.settings.get_double("max-haptic-strength")
        )

    def set_mode(self, mode):
        """One of hapticsmodel.MODES: feedbackd's profile and strength."""
        if not self.available or mode not in model.MODES:
            return
        current = self.settings.get_double("max-haptic-strength")
        if model.is_on(current):
            # Remember the strength the user had, for vibrating again.
            try:
                model.save_strength(current)
            except OSError:
                pass
        profile, strength = model.settings_for(mode, model.load_strength())
        log("sound mode %s (profile %s, vibration %.1f)" % (mode, profile, strength))
        # Two plain writes: a delay()ed GSettings would hold every later write
        # (the strength row) until the next apply().
        self.settings.set_string("profile", profile)
        self.settings.set_double("max-haptic-strength", strength)
        if model.vibrates(mode):
            # A taste: the phone says it will vibrate (no sound: this event
            # has none in any profile), once feedbackd has the new strength.
            GLib.timeout_add(200, lambda: self.trigger("notification-new-generic") and False)

    def cycle(self):
        mode = self.mode
        if mode is not None:
            self.set_mode(model.next_mode(mode))

    def toggle(self):
        """The vibration switch (the sound stays as it is)."""
        mode = self.mode
        if mode is not None:
            self.set_mode(model.with_vibration(mode, not model.vibrates(mode)))

    def trigger(self, event):
        if self.bus is None or not event:
            return
        self.bus.call(
            *FB,
            "TriggerFeedback",
            GLib.Variant("(ssa{sv}i)", (APP_ID, event, {}, -1)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            self._done,
            event,
        )

    def _done(self, bus, result, event):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            log("%s: %s" % (event, e.message))
