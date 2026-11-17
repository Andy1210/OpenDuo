"""Who may know where the phone is (pure): the Location switch and the
per-app answers, as the shell's geoclue agent (location.py) and Duo
Settings' Privacy page read and write them.

The file is ~/.config/duo-shell/location.json:

    {"enabled": true, "apps": {"org.gnome.Weather": true, "firefox": false}}

`enabled` is the quick menu's Location switch (off: geoclue answers no to
everyone and its sources, the GNSS engine too, stop); `apps` the answers
the user gave to "Allow <app> to use your location?", by desktop id. An app
not listed is asked about the first time it asks geoclue. A Flatpak asks
through xdg-desktop-portal, which asks the user itself: what it was told
(the portal's permission store) counts as the answer.

Like every per-user file this one can be written by any app of the user
(the one-user model, docs/hardware/security.md): it decides what the
user is asked, not what an app could do anyway.
"""

import json
import os

# GClueAccuracyLevel.
NONE, COUNTRY, CITY, NEIGHBORHOOD, STREET, EXACT = 0, 1, 4, 5, 6, 8

DEFAULT = {"enabled": True, "apps": {}}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "location.json")


def parse(text):
    """The store from the file's text: a broken or partial file is the
    default (on, nobody asked yet), unknown keys are dropped."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {"enabled": True, "apps": {}}
    if not isinstance(data, dict):
        return {"enabled": True, "apps": {}}
    apps = data.get("apps")
    apps = {str(k): bool(v) for k, v in apps.items()} if isinstance(apps, dict) else {}
    return {"enabled": bool(data.get("enabled", True)), "apps": apps}


def load(path=None):
    try:
        with open(path or config_path()) as f:
            return parse(f.read())
    except OSError:
        return {"enabled": True, "apps": {}}


def save(store, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {
                "enabled": bool(store.get("enabled", True)),
                "apps": dict(sorted(store.get("apps", {}).items())),
            },
            f,
            indent=1,
        )
        f.write("\n")
    os.replace(tmp, path)


def max_level(store):
    """The agent's MaxAccuracyLevel: everything while on, nothing while off."""
    return EXACT if store.get("enabled", True) else NONE


def decide(store, desktop_id, requested):
    """What to answer geoclue's AuthorizeApp: ("allow", level),
    ("deny", 0) or ("ask", 0) (the user decides)."""
    if not store.get("enabled", True):
        return ("deny", NONE)
    if not desktop_id:
        return ("deny", NONE)
    answer = store.get("apps", {}).get(desktop_id)
    if answer is True:
        return ("allow", min(int(requested), EXACT) if requested else EXACT)
    if answer is False:
        return ("deny", NONE)
    return ("ask", NONE)


def answer(store, desktop_id, allowed):
    """The store with the user's answer for an app."""
    apps = dict(store.get("apps", {}))
    apps[desktop_id] = bool(allowed)
    return {"enabled": store.get("enabled", True), "apps": apps}


def forget(store, desktop_id):
    apps = dict(store.get("apps", {}))
    apps.pop(desktop_id, None)
    return {"enabled": store.get("enabled", True), "apps": apps}


def set_enabled(store, on):
    return {"enabled": bool(on), "apps": dict(store.get("apps", {}))}


def portal_granted(permissions):
    """xdg-desktop-portal's location permission for an app (the
    permission store's strings: ["EXACT", "<timestamp>"], ["NONE"], ...)
    as an answer: True, False, or None (not asked there)."""
    if not permissions:
        return None
    level = str(permissions[0]).upper()
    if level in ("", "NONE"):
        return False
    return True


def app_label(desktop_id, names):
    """The name to show for an app: its .desktop name, else the id."""
    return names.get(desktop_id) or desktop_id
