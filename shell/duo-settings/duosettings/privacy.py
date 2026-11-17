"""Location privacy, the data part (pure, but for the file reads): the
Location switch and the shell agent's per-app answers
(~/.config/duo-shell/location.json, duo-shell's locationmodel.py writes the
same file), the apps that say they use the location (X-Geoclue-Reason in
their .desktop file), and the ones refused in geoclue's own config
(/etc/geoclue/conf.d/91-duo2-apps.conf, duo-settings-helper geoclue-deny).

Why two lists: geoclue asks its agent (the shell) only about sandboxed apps
(Flatpaks), so those are asked when they first want a position; an app
from a Debian package is not sandboxed and is never asked: it is allowed
while Location is on, unless refused here (geoclue's config, by its desktop
id, which a cooperative app such as GNOME Weather reports truthfully).
"""

import json
import os

GEOCLUE_APPS_CONF = "/etc/geoclue/conf.d/91-duo2-apps.conf"


def store_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "location.json")


def parse_store(text):
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return {"enabled": True, "apps": {}}
    apps = data.get("apps")
    apps = {str(k): bool(v) for k, v in apps.items()} if isinstance(apps, dict) else {}
    return {"enabled": bool(data.get("enabled", True)), "apps": apps}


def load_store(path=None):
    try:
        with open(path or store_path()) as f:
            return parse_store(f.read())
    except OSError:
        return {"enabled": True, "apps": {}}


def save_store(store, path=None):
    path = path or store_path()
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


def parse_denied(text):
    """The desktop ids refused in geoclue's config: the sections with
    allowed=false."""
    denied, section, allowed = set(), None, None
    for line in (text or "").splitlines() + ["[]"]:
        line = line.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            if section and allowed is False:
                denied.add(section)
            section, allowed = line[1:-1].strip(), None
        elif "=" in line and section:
            key, value = (x.strip() for x in line.split("=", 1))
            if key == "allowed":
                allowed = value.lower() in ("true", "1", "yes")
    return denied


def load_denied(path=GEOCLUE_APPS_CONF):
    try:
        with open(path) as f:
            return parse_denied(f.read())
    except OSError:
        return set()


def location_apps(infos):
    """The installed apps that say they use the location: [(desktop id,
    name)], by name. `infos`: [(desktop file id, name, X-Geoclue-Reason or
    None)]."""
    out = []
    for file_id, name, reason in infos:
        if reason and file_id.endswith(".desktop"):
            out.append((file_id[: -len(".desktop")], name or file_id))
    return sorted(out, key=lambda a: a[1].lower())


def rows(store, denied, native, portal):
    """The Apps list: [(desktop id, name, allowed, kind)], kind "native"
    (switch = geoclue's config), "asked" (the shell's answer) or "portal"
    (xdg-desktop-portal's permission store). `native`: location_apps();
    `portal`: {app id: allowed}."""
    names = dict(native)
    out = {}
    for app, name in native:
        out[app] = (app, name, app not in denied, "native")
    for app, allowed in sorted(store.get("apps", {}).items()):
        if app not in out:
            out[app] = (app, names.get(app, app), bool(allowed), "asked")
    for app, allowed in sorted(portal.items()):
        if app not in out:
            out[app] = (app, names.get(app, app), bool(allowed), "portal")
    return sorted(out.values(), key=lambda r: r[1].lower())
