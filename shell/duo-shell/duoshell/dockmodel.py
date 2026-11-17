"""What the dock shows: pinned apps, then running apps that are not pinned.
Pure (tests/test_dockmodel.py); dock.py draws it.

Apps are named by their desktop file id (`foot.desktop`). A window's
Wayland app id (`foot`, `org.gnome.TextEditor`) is matched to a desktop file
by id, then by StartupWMClass, then by the executable's name; a window
nothing matches still shows (by its app id, with a generic icon).

The pinned list lives in ~/.config/duo-shell/dock.json ({"pinned": [...]}).
"""

import json
import os

# Pinned when the user has not chosen yet: the first of these that exist.
DEFAULT_PINNED = [
    "foot.desktop",
    "org.gnome.Console.desktop",
    "org.gnome.TextEditor.desktop",
    "org.gnome.Nautilus.desktop",
    "firefox-esr.desktop",
    "firefox.desktop",
    "org.gnome.Epiphany.desktop",
]
MAX_DEFAULT = 4


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "dock.json")


def default_pinned(available):
    return [d for d in DEFAULT_PINNED if d in available][:MAX_DEFAULT]


def load_pinned(path, available):
    """The saved list (apps no longer installed dropped), else the defaults."""
    try:
        with open(path) as f:
            data = json.load(f)
        pinned = data.get("pinned")
        if isinstance(pinned, list):
            return [d for d in pinned if isinstance(d, str) and d in available]
    except (OSError, ValueError, AttributeError):
        pass
    return default_pinned(available)


def save_pinned(path, pinned):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump({"pinned": list(pinned)}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def toggle_pin(pinned, desktop_id):
    """The list with `desktop_id` pinned (at the end) or unpinned."""
    if desktop_id in pinned:
        return [d for d in pinned if d != desktop_id]
    return list(pinned) + [desktop_id]


def match_app(app_id, index):
    """The desktop id for a window's app id. `index` maps desktop id ->
    (StartupWMClass or None, executable basename or None)."""
    if not app_id:
        return None
    want = app_id.lower()
    for desktop_id in index:
        if desktop_id.lower() == want + ".desktop":
            return desktop_id
    for desktop_id, (wm_class, _exe) in index.items():
        if wm_class and wm_class.lower() == want:
            return desktop_id
    for desktop_id, (_wm, exe) in index.items():
        if exe and exe.lower() == want:
            return desktop_id
    return None


class Item:
    def __init__(self, desktop_id, app_id, pinned, running):
        self.desktop_id = desktop_id
        self.app_id = app_id
        self.pinned = pinned
        self.running = running

    def key(self):
        return self.desktop_id or "app:" + (self.app_id or "")

    def __repr__(self):
        return "Item(%r, %r, pinned=%r, running=%r)" % (
            self.desktop_id,
            self.app_id,
            self.pinned,
            self.running,
        )


def signature(items):
    """What a dock row shows, to compare: the same signature means the
    buttons on screen are still right (a window's title or the stacking of
    windows of one app changed nothing the dock shows)."""
    return tuple((i.desktop_id, i.app_id, i.pinned, i.running) for i in items)


def items(pinned, windows, index):
    """Dock items: every pinned app (running or not), then the running ones
    that are not pinned, newest window first. `windows` is duo-wc's list
    (back to front, dicts with app_id)."""
    running = []  # (desktop_id or None, app_id), newest first, unique
    seen = set()
    for w in reversed(windows or []):
        app_id = w.get("app_id")
        desktop_id = match_app(app_id, index)
        key = desktop_id or "app:" + (app_id or "")
        if key in seen or not app_id:
            continue
        seen.add(key)
        running.append((desktop_id, app_id))
    running_by_desktop = {d: a for d, a in running if d}
    out = [Item(d, running_by_desktop.get(d), True, d in running_by_desktop) for d in pinned]
    for desktop_id, app_id in running:
        if desktop_id and desktop_id in pinned:
            continue
        out.append(Item(desktop_id, app_id, False, True))
    return out


def is_external(output):
    """An external display (a monitor or a TV on the USB-C port), not one of
    the phone's panels (DSI-1, DSI-2)."""
    return bool(output) and not output.startswith("DSI-")


def _laptop(state):
    lt = (state or {}).get("laptop")
    return lt if isinstance(lt, dict) and lt.get("on") else None


def _mirrors(state, output):
    """Whether duo-wc uses this display as a mirror of the phone (not a
    desk: displaymodel.py)."""
    d = (state or {}).get("displays")
    for c in (d.get("connected") or []) if isinstance(d, dict) else []:
        if isinstance(c, dict) and c.get("output") == output:
            return c.get("use") == "mirror"
    return False


def is_desk(state, output):
    """A desk (windows float there, a taskbar stands): an external display
    in use as a desk (not one that mirrors the phone), or the desk panel of
    laptop mode."""
    lt = _laptop(state)
    return (is_external(output) and not _mirrors(state, output)) or (
        lt is not None and lt.get("desk") == output
    )


def is_laptop_keys(state, output):
    """Laptop mode's keyboard panel: duo-wc shows its keys there, no dock."""
    lt = _laptop(state)
    return lt is not None and lt.get("keys") == output


def windows_on(windows, output, state=None):
    """The windows a dock lists: on a desk only its own (the desk's
    taskbar), on a panel all of them."""
    windows = windows or []
    if not (is_external(output) or is_desk(state, output)):
        return windows
    return [
        w
        for w in windows
        if isinstance(w.get("placement"), dict) and w["placement"].get("output") == output
    ]


def wanted(state, output):
    """The dock a panel shows on its own, from duo-wc's state: "overview"
    while the task overview is open there (lit), "home" while the panel shows
    the home screen (no window on it), else None. In span there is no
    standing taskbar any more (the owner, 2026-11-07): the dock peeks on a
    short swipe up, as on every other pose. A desk (an external display,
    laptop mode's desk panel) has it standing as a taskbar while it is lit,
    except under a fullscreen window and while the phone is locked; laptop
    mode's keyboard panel has none."""
    state = state or {}
    if output not in (state.get("lit") or []):
        return None
    if is_laptop_keys(state, output) or _mirrors(state, output):
        return None  # duo-wc draws its keys there / the phone's panels there
    if is_desk(state, output):
        if state.get("locked") or output in (state.get("fullscreen") or []):
            return None
        return "taskbar"
    if state.get("overview"):
        return "overview"
    if output in (state.get("home") or []) and not state.get("locked"):
        return "home"
    return None


def may_peek(state, output):
    """Whether a short swipe may show the dock on a panel: lit, and no
    fullscreen window (a video) in front there; then the dock never shows."""
    state = state or {}
    if (
        output not in (state.get("lit") or [])
        or is_laptop_keys(state, output)
        or _mirrors(state, output)
    ):
        return False
    return output not in (state.get("fullscreen") or [])
