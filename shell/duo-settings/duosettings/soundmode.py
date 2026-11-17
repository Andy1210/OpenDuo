"""The sound mode's rule (pure, tests/test_settings.py), the same as the
quick menu's Sound tile (duo-shell's hapticsmodel.py, kept in step): four
states as feedbackd's `profile` and `max-haptic-strength`.

    Sound and vibration   profile full    strength = the chosen one
    Sound only            profile full    strength 0
    Vibration only        profile quiet   strength = the chosen one
    Silent                profile silent  strength 0
"""

SOUND_VIBRATION, SOUND, VIBRATION, SILENT = "sound+vibration", "sound", "vibration", "silent"
MODES = (SOUND_VIBRATION, SOUND, VIBRATION, SILENT)


def mode_of(profile, strength):
    """The sound mode feedbackd's two settings make (any other profile is
    feedbackd's default, full)."""
    vibrates = float(strength) > 0.0
    if profile == "silent":
        return SILENT
    if profile == "quiet":
        return VIBRATION if vibrates else SILENT
    return SOUND_VIBRATION if vibrates else SOUND


def settings_for(mode, remembered):
    """(profile, max-haptic-strength) for a mode; `remembered` is the
    strength chosen (vibration.json)."""
    strength = min(1.0, max(0.1, float(remembered)))
    return {
        SOUND_VIBRATION: ("full", strength),
        SOUND: ("full", 0.0),
        VIBRATION: ("quiet", strength),
        SILENT: ("silent", 0.0),
    }[mode]


def vibrates(mode):
    return mode in (SOUND_VIBRATION, VIBRATION)


def with_vibration(mode, on):
    """The mode after the Vibration switch is set: the sound stays as it is."""
    if mode in (SOUND_VIBRATION, SOUND):
        return SOUND_VIBRATION if on else SOUND
    return VIBRATION if on else SILENT
