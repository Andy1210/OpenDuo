"""The Surface Pen's top button and battery: the pure rules (pen.py is the glue,
tests/test_penmodel.py the tests).

The top button comes over Bluetooth LE (the pen paired in Duo Settings >
Pen): duo-wc takes the pen's keys (compositor/duo-wc src/penbutton.rs), so
no app ever sees them, and announces each press on its event stream:
`{"event": "pen_button", "press": "click" | "double_click" | "hold",
"output": "DSI-2" | null, "locked": false}`. `output` is the panel the press is
for (the one the pen is over or just left, else the active one), null while
the screen is dark.

What a press does is in ~/.config/duo-shell/pen.conf (Duo Settings > Pen
writes it, the shell reads it at every press), `key = value`:

    click = notes            # Rnote (or the first note app installed)
    double_click = screenshot  # the panel the press is for, to ~/Pictures/Screenshots
    hold = quick_menu        # the quick menu on that panel
    # or: off

Nothing happens while the screen is dark or locked (the button does not wake
the phone, and a note app or a screenshot over the lock screen would get
round it).

The battery: BlueZ shows the pen's GATT battery as org.bluez.Battery1; at or
below LOW per cent the shell says so once, and again only after the pen was
charged (REARM per cent or more).
"""

import hashlib
import os
import time

PRESSES = ("click", "double_click", "hold")
ACTIONS = ("notes", "screenshot", "quick_menu", "off")
DEFAULTS = {"click": "notes", "double_click": "screenshot", "hold": "quick_menu"}

# The note apps, in order of preference: Rnote (Flathub), Xournal++ (Debian),
# GNOME Text Editor as the last resort (a note all the same).
NOTE_APPS = (
    "com.github.flxzt.rnote.desktop",
    "com.github.xournalpp.xournalpp.desktop",
    "org.gnome.TextEditor.desktop",
)

LOW = 10
REARM = 20

MICROSOFT_COMPANY_ID = 0x0006  # Bluetooth SIG company identifier (manufacturer data)
APPEARANCE_PEN = 0x03C7  # GAP appearance: HID, digital pen


def config_path(home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".config", "duo-shell", "pen.conf")


def parse(text):
    """The button's settings from pen.conf's text: every press with an action
    (the defaults for missing or unknown values). Pure."""
    config = dict(DEFAULTS)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in PRESSES and value in ACTIONS:
            config[key] = value
    return config


def load(path=None):
    try:
        with open(path or config_path()) as f:
            return parse(f.read())
    except OSError:
        return dict(DEFAULTS)


def decide(message, config):
    """What a `pen_button` event does: (action, output), or None (nothing:
    the screen is dark, the phone locked, the press set to off, or an
    unknown press). Pure."""
    press = message.get("press")
    output = message.get("output")
    if press not in PRESSES or not output or message.get("locked"):
        return None
    action = config.get(press, DEFAULTS[press])
    if action not in ACTIONS or action == "off":
        return None
    return action, output


def notes_app(installed):
    """The note app to open: the first of NOTE_APPS that is installed
    (`installed`: desktop ids), or None. Pure."""
    for app in NOTE_APPS:
        if app in installed:
            return app
    return None


def app_id_of(desktop_id):
    return desktop_id[: -len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id


def running_window(windows, desktop_id):
    """The id of a window of that app in duo-wc's state (its `windows`), or
    None: a running note app is brought forward, not started again. Pure."""
    app_id = app_id_of(desktop_id)
    for w in reversed(windows or []):
        if w.get("app_id") == app_id:
            return w.get("id")
    return None


def screenshot_dir(home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, "Pictures", "Screenshots")


def screenshot_path(directory, now=None, exists=os.path.exists):
    """A new file in `directory` named as GNOME names screenshots, with a
    number when one of that second is there already. Pure but for `exists`."""
    stamp = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(now))
    path = os.path.join(directory, "Screenshot from %s.png" % stamp)
    n = 2
    while exists(path):
        path = os.path.join(directory, "Screenshot from %s (%d).png" % (stamp, n))
        n += 1
    return path


# -- the pen among BlueZ's devices -------------------------------------------------


def is_surface_pen(device):
    """Whether a BlueZ device (org.bluez.Device1's properties, unpacked) is a
    Surface pen: "pen" in its name and Microsoft's by name, company id
    (manufacturer data, Swift Pair) or USB vendor (Modalias); or a nameless
    digital pen (appearance) with Microsoft's manufacturer data. Duo Settings
    has the same rule (duosettings/pen.py; its tests compare the two). Pure."""
    name = (device.get("Name") or device.get("Alias") or "").lower()
    microsoft = (
        MICROSOFT_COMPANY_ID in (device.get("ManufacturerData") or {})
        or "v045e" in (device.get("Modalias") or "").lower()
    )
    if "pen" in name:
        return "surface" in name or microsoft
    return device.get("Appearance") == APPEARANCE_PEN and microsoft


def pens(objects):
    """The paired Surface pens in BlueZ's managed objects:
    [{"path", "name", "connected", "battery" (None: unknown)}]. Pure."""
    out = []
    for path, ifaces in sorted(objects.items()):
        d = ifaces.get("org.bluez.Device1")
        if not d or not d.get("Paired") or not is_surface_pen(d):
            continue
        battery = ifaces.get("org.bluez.Battery1", {}).get("Percentage")
        out.append(
            {
                "path": path,
                "name": d.get("Alias") or d.get("Name") or "Surface Pen",
                "connected": bool(d.get("Connected")),
                "battery": int(battery) if battery is not None else None,
            }
        )
    return out


def key_of(path):
    """A device's key in the shell's small state file: a hash of its BlueZ
    path (the path carries its address, which stays out of files)."""
    return hashlib.sha256(path.encode()).hexdigest()[:16]


class BatteryNotice:
    """Low battery, said once: `update(key, percent)` is True when it is to be
    said now. `told` (keys) is kept by the caller between runs. Pure."""

    def __init__(self, told=()):
        self.told = set(told)

    def update(self, key, percent):
        if percent is None:
            return False
        if percent >= REARM:
            self.told.discard(key)
            return False
        if percent <= LOW and key not in self.told:
            self.told.add(key)
            return True
        return False


def battery_text(name, percent):
    """The notification's (summary, body). Pure."""
    return ("Pen battery low", "%s: %d %%. Charge it soon." % (name, percent))
