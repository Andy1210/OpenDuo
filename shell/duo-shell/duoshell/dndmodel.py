"""Do not disturb, as data (pure, tests/test_dndmodel.py); dnd.py applies
it, calls.py asks it about a call that rings in.

The switch is GNOME's own (org.gnome.desktop.notifications show-banners =
false), set by the quick menu's tile and Duo Settings' Notifications page.
While it is on:

- notifications: no banner and no sound or vibration, except critical ones
  (an alarm, a timer); the list keeps everything (notifications.py,
  hapticsmodel.notification_event);
- calls: GNOME Calls' ringer is silenced through feedbackd's per-app
  profile (org.sigxcpu.feedbackd.application at
  /org/sigxcpu/feedbackd/application/org-gnome-calls/, profile `silent`),
  so the sound mode stays as the user set it; a call that rings in lights
  nothing and the keys need not silence it (it rings silently: it can be
  answered in Calls or on the lock screen, and Calls lists it as missed);
- the exceptions (Duo Settings, ~/.config/duo-shell/dnd.json): a caller
  who rang within the last 15 minutes rings this time (`repeat_callers`,
  on by default: someone who calls twice probably needs to), and callers
  in the address book (`contacts`, off by default: Calls' DisplayName is
  the contact's name, empty for an unknown number). Calls has favourites
  (folks' is-favourite) but does not export them on the bus, so there is no
  favourites rule. An allowed call rings through the shell's own feedbackd
  event (app id org.duo2os.Shell, the same phone-incoming-call event, so
  the chosen ringtone and the sound mode apply) and lights the screen;
- alarms ring in every case: GNOME Clocks plays them itself (and is in
  feedbackd's allow-important list).

The callers' numbers are kept in memory only (for the repeat rule), never
written or logged.
"""

import json
import os

CALLS_APP_ID = "org.gnome.Calls"
APP_SCHEMA = "org.sigxcpu.feedbackd.application"
APP_PREFIX = "/org/sigxcpu/feedbackd/application/"
REPEAT_WINDOW_S = 15 * 60
DEFAULTS = {"repeat_callers": True, "contacts": False}


def munge_app_id(app_id):
    """feedbackd's path element for an app id (fbd-feedback-manager.c's
    munge_app_id: anything but [0-9A-Za-z-] becomes '-', then lower case)."""
    ok = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-")
    return "".join(ch if ch in ok else "-" for ch in app_id).lower()


def app_profile_path(app_id=CALLS_APP_ID):
    return APP_PREFIX + munge_app_id(app_id) + "/"


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


def caller_key(call):
    """What identifies a caller for the repeat rule: the number's digits
    (and a leading '+'); None for a hidden number (nothing to repeat)."""
    raw = (call.get("id") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 3:
        return None
    return ("+" if raw.startswith("+") else "") + digits


def prune(history, now, window=REPEAT_WINDOW_S):
    for key in [k for k, t in history.items() if now - t > window]:
        del history[key]
    return history


def allowed(call, history, prefs, now, window=REPEAT_WINDOW_S):
    """Whether a call that rings in during do not disturb may ring aloud:
    "repeat" (the same number rang within `window`), "contact" (a caller
    from the address book), or None. Ask before remember() notes this
    ring."""
    key = caller_key(call)
    if prefs.get("repeat_callers") and key is not None:
        t = history.get(key)
        if t is not None and now - t <= window:
            return "repeat"
    if prefs.get("contacts") and (call.get("name") or "").strip():
        return "contact"
    return None


def remember(history, call, now):
    """Note a call that rang in (for the repeat rule). Changes `history`."""
    key = caller_key(call)
    prune(history, now)
    if key is not None:
        history[key] = now
    return history


def tile(on):
    """The quick menu's tile: (on, second line)."""
    return bool(on), ("On" if on else "Off")
