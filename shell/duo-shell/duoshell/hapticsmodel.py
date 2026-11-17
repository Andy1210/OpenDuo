"""Sound and vibration (pure): the sound mode, the vibration switch, and
which feedbackd event a notification gives.

The sound mode, four states (the quick menu's Sound tile, Duo Settings'
Sound page), is two feedbackd settings (GSettings org.sigxcpu.feedbackd):
its `profile` and its `max-haptic-strength`:

    Sound and vibration   profile full    strength = the chosen one
    Sound only            profile full    strength 0
    Vibration only        profile quiet   strength = the chosen one
    Silent                profile silent  strength 0

feedbackd plays, for an event, what each profile from the current one down
to `silent` has for it (the theme's lookup): `full` = the default theme's
sounds plus this phone's vibrations (its `quiet`), `quiet` = the
vibrations, `silent` = only LEDs (the Duo has none). A strength of 0
scales every vibration to nothing. So the ringtone, the message sounds and
the vibrations follow the mode; the keyboard's keys only vibrate (the
phone's theme gives them no sound in `full`). Alarms ring in every mode
(GNOME Clocks plays them itself).

feedbackd plays the events (its theme for this phone,
/usr/share/feedbackd/themes/microsoft,surface-duo2.json, tuned for the
LRA: short pulses at its resonance). Its `max-haptic-strength` (GSettings
org.sigxcpu.feedbackd) scales every vibration: the Vibration switch sets it
to 0 (off) or back to the strength chosen in Duo Settings, kept in
~/.config/duo-shell/vibration.json ({"strength": 0.8}).

Notifications: the shell is the notification daemon, so it gives the
vibration feedbackd would get from a phone shell, by the notification's
category. Not for: an update of a shown notification, low urgency, a
`suppress-sound` hint, an incoming call (Calls rings itself), and nothing
but critical ones while do-not-disturb is on.
"""

import json
import os

STRENGTHS = (0.6, 0.8, 1.0)  # Light, Medium, Strong
DEFAULT_STRENGTH = 1.0

CATEGORY_EVENTS = {
    "im.received": "message-new-instant",
    "email.arrived": "message-new-email",
    "x-gnome.alarm": "alarm-clock-elapsed",
}
URGENCY_LOW, URGENCY_NORMAL, URGENCY_CRITICAL = 0, 1, 2


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "vibration.json")


def clamp_strength(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_STRENGTH
    return min(1.0, max(0.1, value))


def load_strength(path=None):
    try:
        with open(path or config_path()) as f:
            return clamp_strength(json.load(f).get("strength"))
    except (OSError, ValueError, AttributeError):
        return DEFAULT_STRENGTH


def save_strength(strength, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"strength": clamp_strength(strength)}, f)
        f.write("\n")
    os.replace(tmp, path)


def is_on(max_strength):
    return float(max_strength) > 0.0


def toggled(max_strength, remembered):
    """The new max-haptic-strength when the switch is flipped."""
    return 0.0 if is_on(max_strength) else clamp_strength(remembered)


# -- the sound mode ------------------------------------------------------------

SOUND_VIBRATION, SOUND, VIBRATION, SILENT = "sound+vibration", "sound", "vibration", "silent"
MODES = (SOUND_VIBRATION, SOUND, VIBRATION, SILENT)  # also the quick menu tile's order
MODE_LABELS = {
    SOUND_VIBRATION: "Sound and vibration",
    SOUND: "Sound only",
    VIBRATION: "Vibration only",
    SILENT: "Silent",
}
MODE_ICONS = {
    SOUND_VIBRATION: "audio-volume-high-symbolic",
    SOUND: "audio-volume-medium-symbolic",
    VIBRATION: "phone-symbolic",
    SILENT: "audio-volume-muted-symbolic",
}


def mode_of(profile, max_strength):
    """The sound mode feedbackd's two settings make (any other profile is
    feedbackd's default, full)."""
    vibrates = is_on(max_strength)
    if profile == "silent":
        return SILENT
    if profile == "quiet":
        return VIBRATION if vibrates else SILENT
    return SOUND_VIBRATION if vibrates else SOUND


def settings_for(mode, remembered):
    """(profile, max-haptic-strength) for a mode; `remembered` is the
    strength chosen in Duo Settings (vibration.json)."""
    strength = clamp_strength(remembered)
    return {
        SOUND_VIBRATION: ("full", strength),
        SOUND: ("full", 0.0),
        VIBRATION: ("quiet", strength),
        SILENT: ("silent", 0.0),
    }[mode]


def has_sound(mode):
    return mode in (SOUND_VIBRATION, SOUND)


def vibrates(mode):
    return mode in (SOUND_VIBRATION, VIBRATION)


def with_vibration(mode, on):
    """The mode after the Vibration switch is set: the sound stays as it
    is (silent + vibration on = vibration only, and back)."""
    if has_sound(mode):
        return SOUND_VIBRATION if on else SOUND
    return VIBRATION if on else SILENT


def next_mode(mode):
    """The quick menu's Sound tile: one tap, the next mode."""
    i = MODES.index(mode) if mode in MODES else -1
    return MODES[(i + 1) % len(MODES)]


def notification_event(category, urgency, hints, replaced, banners_on):
    """The feedbackd event for a new notification, or None."""
    hints = hints or {}
    if replaced or urgency == URGENCY_LOW:
        return None
    if hints.get("suppress-sound") or hints.get("suppress-vibration"):
        return None
    category = (category or "").strip()
    if category.startswith("call.incoming") or category == "call":
        return None
    if not banners_on and urgency != URGENCY_CRITICAL:
        return None
    if category in CATEGORY_EVENTS:
        return CATEGORY_EVENTS[category]
    if urgency == URGENCY_CRITICAL:
        return "alarm-clock-elapsed"
    return "notification-new-generic"
