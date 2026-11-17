"""Do not disturb's exceptions for calls (pure, tests/test_settings.py
checks them against duo-shell's dndmodel.py, which reads the same file):
~/.config/duo-shell/dnd.json, {"repeat_callers": bool, "contacts": bool}.
The switch itself is GNOME's org.gnome.desktop.notifications show-banners
(off = do not disturb), which duo-shell follows (dnd.py)."""

import json
import os

DEFAULTS = {"repeat_callers": True, "contacts": False}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "dnd.json")


def load(path=None):
    prefs = dict(DEFAULTS)
    try:
        with open(path or config_path()) as f:
            data = json.load(f)
        for key in DEFAULTS:
            if isinstance(data.get(key), bool):
                prefs[key] = data[key]
    except (OSError, ValueError, AttributeError):
        pass
    return prefs


def save(prefs, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {key: bool(prefs.get(key, value)) for key, value in DEFAULTS.items()}
    with open(path + ".tmp", "w") as f:
        json.dump(out, f)
        f.write("\n")
    os.replace(path + ".tmp", path)
