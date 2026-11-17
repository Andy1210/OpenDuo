"""Sound and vibration on the Sound page: the sound mode (four states), the
vibration switch and its strength, as feedbackd's `profile` and
`max-haptic-strength` (GSettings org.sigxcpu.feedbackd), the same rule as
the quick menu's Sound tile (duo-shell's hapticsmodel.py, kept in step):

    Sound and vibration   profile full    strength = the chosen one
    Sound only            profile full    strength 0
    Vibration only        profile quiet   strength = the chosen one
    Silent                profile silent  strength 0

The strength chosen is kept in ~/.config/duo-shell/vibration.json, where
the shell finds it when vibration comes back on."""

import json
import os

from gi.repository import Adw, Gio, Gtk

from .main import _, combo_row, select_quietly, set_quietly, switch_row
from .soundmode import (
    MODES,
    SILENT,
    SOUND,
    SOUND_VIBRATION,
    VIBRATION,
    mode_of,
    settings_for,
    vibrates,
    with_vibration,
)

SCHEMA = "org.sigxcpu.feedbackd"
STRENGTHS = (0.6, 0.8, 1.0)


def strength_index(value):
    """The nearest of STRENGTHS (pure)."""
    return min(range(len(STRENGTHS)), key=lambda i: abs(STRENGTHS[i] - float(value)))


def store_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "vibration.json")


def load_strength(path=None):
    try:
        with open(path or store_path()) as f:
            return min(1.0, max(0.1, float(json.load(f).get("strength", 1.0))))
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0


def save_strength(value, path=None):
    path = path or store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump({"strength": float(value)}, f)
        f.write("\n")
    os.replace(path + ".tmp", path)


class VibrationGroup:
    """Adds the sound mode's and the vibration's groups to a page;
    `refresh()` when the page is shown (and whenever feedbackd's settings
    change, from the quick menu too)."""

    def __init__(self, page):
        self.settings = None
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(SCHEMA, True) is not None:
            self.settings = Gio.Settings.new(SCHEMA)
            self.settings.connect("changed", lambda *_a: self.refresh())
        g = page.group(
            _("Sound mode"), _("Calls, messages and notifications. Alarms ring in every mode.")
        )
        self.mode_rows = {}
        first = None
        texts = {
            SOUND_VIBRATION: (_("Sound and vibration"), _("The phone rings and vibrates")),
            SOUND: (_("Sound only"), _("The phone rings, no vibration")),
            VIBRATION: (_("Vibration only"), _("No ringtone or message sounds")),
            SILENT: (_("Silent"), _("No sound, no vibration")),
        }
        self._quiet_modes = False
        for mode in MODES:
            title, subtitle = texts[mode]
            row = Adw.ActionRow(use_markup=False)
            row.set_title(title)
            row.set_subtitle(subtitle)
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            if first is not None:
                check.set_group(first)
            first = first or check
            check.connect(
                "toggled",
                lambda b, m=mode: b.get_active() and not self._quiet_modes and self.set_mode(m),
            )
            row.add_prefix(check)
            row.set_activatable_widget(check)
            g.add(row)
            self.mode_rows[mode] = check
        if self.settings is None:
            g.set_sensitive(False)
        g = page.group(_("Vibration"), _("The keyboard's keys, notifications, messages and calls."))
        self.switch = switch_row(_("Vibration"), on_toggle=self.toggle)
        self.strength = combo_row(
            _("Strength"), [_("Light"), _("Medium"), _("Strong")], on_select=self.choose
        )
        g.add(self.switch)
        g.add(self.strength)
        if self.settings is None:
            g.set_sensitive(False)

    def mode(self):
        return mode_of(
            self.settings.get_string("profile"), self.settings.get_double("max-haptic-strength")
        )

    def refresh(self):
        if self.settings is None:
            return
        current = self.settings.get_double("max-haptic-strength")
        mode = self.mode()
        on = vibrates(mode)
        self._quiet_modes = True
        self.mode_rows[mode].set_active(True)
        self._quiet_modes = False
        set_quietly(self.switch, on)
        select_quietly(self.strength, strength_index(current if on else load_strength()))
        self.strength.set_sensitive(on)

    def set_mode(self, mode):
        if self.settings is None:
            return
        current = self.settings.get_double("max-haptic-strength")
        if current > 0.0:
            save_strength(current)
        profile, strength = settings_for(mode, load_strength())
        # Two plain writes: a delay()ed GSettings would hold every later write
        # (the strength row) until the next apply().
        self.settings.set_string("profile", profile)
        self.settings.set_double("max-haptic-strength", strength)
        self.refresh()

    def toggle(self, on):
        if self.settings is None:
            return
        self.set_mode(with_vibration(self.mode(), on))

    def choose(self, i):
        if self.settings is None or not 0 <= i < len(STRENGTHS):
            return
        save_strength(STRENGTHS[i])
        if self.settings.get_double("max-haptic-strength") > 0.0:
            self.settings.set_double("max-haptic-strength", STRENGTHS[i])
            # A taste of the new strength.
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                from gi.repository import GLib

                bus.call(
                    "org.sigxcpu.Feedback",
                    "/org/sigxcpu/Feedback",
                    "org.sigxcpu.Feedback",
                    "TriggerFeedback",
                    GLib.Variant(
                        "(ssa{sv}i)", ("org.duo2os.Settings", "notification-new-generic", {}, -1)
                    ),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                    None,
                )
            except Exception:  # a taste is not worth an error
                pass
