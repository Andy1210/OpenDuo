"""Settings > Pen's pure parts (penpage.py is the page): the palm rejection
setting as duo2-touchd reads it from /etc/duo2/pen.conf, the pen's haptics
(the writing feel, its strength, the top button's click) as duo2-pend reads
them from the same file, what the pen is
doing now in words (duo-wc's `pen` state), the pen charger (power/
duo2-pen-charger: the setting in /etc/duo2/pen-charger, the transmitter's
state in the kernel's connector), the top button's actions as duo-shell
reads them from ~/.config/duo-shell/pen.conf (duoshell/penmodel.py: the same
keys and values, tests/test_pen.py checks both), which Bluetooth device is a
Surface pen, and the pairing walk (PairFlow). No GTK: tests/test_pen.py."""

import gettext
import os

_ = gettext.gettext

PEN_CONF = "/etc/duo2/pen.conf"
PEN_TEST = "/usr/local/lib/duo2-touch-tools/pen-test.py"
PALM = ("both", "panel", "off")
BUTTON = ("right_click", "off")
# duo-wc's pen_input and pen_apps (compositor/duo-wc/src/tablet.rs): which apps
# get the pen as a pen; DEFAULT_PEN_APPS is its DEFAULT_APPS.
PEN_INPUT = ("apps", "all")
DEFAULT_PEN_APPS = (
    "rnote",
    "xournalpp",
    "krita",
    "gimp",
    "inkscape",
    "mypaint",
    "org.duo2os.PenTest",
    "org.duo2os.Nibfair",
)
APP_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
OUTPUTS = {"DSI-1": _("the left screen"), "DSI-2": _("the right screen")}
CHARGER_SETTING = "/etc/duo2/pen-charger"
CHARGER_OUTPUT = "/sys/devices/platform/pen-charger"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def charger(setting=CHARGER_SETTING, output=CHARGER_OUTPUT):
    """The pen charger as the page shows it: None when this kernel has none
    (the row stays hidden), else (switched on?, transmitter on? or None).
    Both files are readable by anyone."""
    if not os.path.exists(os.path.join(output, "state")):
        return None
    text = _read(setting) or ""
    state = _read(os.path.join(output, "state"))
    return (text.split()[:1] == ["on"], {"enabled": True, "disabled": False}.get(state))


def charger_subtitle(on, transmitting):
    """The charger row's subtitle. Pure."""
    if on and transmitting is False:
        return _("Paused: the battery is below 20 % and the phone is not charging")
    return _(
        "Needs the Surface Duo 2 Pen Cover. Pauses below 20 % battery unless the phone is charging"
    )


def palm_from_conf(text):
    """The palm rejection mode in pen.conf's text (the last valid
    `palm_rejection` line, as duo2-touchd reads it), "both" without one. Pure."""
    mode = "both"
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "palm_rejection" and value in PALM + ("on",):
            mode = "both" if value == "on" else value
    return mode


# -- the pen's haptics: duo2-pend's keys in the same pen.conf ------------------------

FEELS = ("off", "ink", "pencil", "marker", "chisel", "brush")
FEEL_TITLES = {
    "off": _("Off"),
    "ink": _("Ink"),
    "pencil": _("Pencil"),
    "marker": _("Marker"),
    "chisel": _("Chisel marker"),
    "brush": _("Brush"),
}
STRENGTH_DEFAULT = 50


def haptics_from_conf(text):
    """(writing feel, strength 1..100, top button click) in pen.conf's text,
    as duo2-pend reads it: the last valid line of each, else off / 50 / off.
    Pure (tests/test_pen.py checks it against the daemon's own reader)."""
    feel, strength, click = "off", STRENGTH_DEFAULT, False
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "writing_feel" and value in FEELS:
            feel = value
        elif key == "feel_strength" and value.isdigit() and 1 <= int(value) <= 100:
            strength = int(value)
        elif key == "button_click" and value in ("on", "off"):
            click = value == "on"
    return feel, strength, click


def read_haptics(path=PEN_CONF):
    try:
        with open(path) as f:
            return haptics_from_conf(f.read())
    except OSError:
        return "off", STRENGTH_DEFAULT, False


def read_palm(path=PEN_CONF):
    try:
        with open(path) as f:
            return palm_from_conf(f.read())
    except OSError:
        return "both"


def now_text(pen):
    """What the pen is doing, for the page (duo-wc's `pen` state). Pure."""
    if not pen:
        return _("This display server has no pen support")
    if not pen.get("in_range"):
        return _("Not near the screen")
    where = OUTPUTS.get(pen.get("output"), pen.get("output") or "?")
    if pen.get("tool") == "eraser":
        return (_("Erasing on %s") if pen.get("tip") else _("The eraser end is near %s")) % where
    if pen.get("tip"):
        return _("Writing on %s") % where
    return _("Hovering over %s") % where


# -- the top button (Bluetooth): duo-shell's pen.conf ------------------------------

PRESSES = ("click", "double_click", "hold")
ACTIONS = ("notes", "screenshot", "quick_menu", "off")
DEFAULTS = {"click": "notes", "double_click": "screenshot", "hold": "quick_menu"}
PRESS_TITLES = {"click": _("Click"), "double_click": _("Double click"), "hold": _("Press and hold")}
ACTION_TITLES = {
    "notes": _("Open Rnote"),
    "screenshot": _("Screenshot"),
    "quick_menu": _("Quick menu"),
    "off": _("Nothing"),
}


def button_conf_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "pen.conf")


def buttons_from_conf(text):
    """Every press's action in pen.conf's text, as duo-shell reads it (the
    defaults for missing or unknown values). Pure."""
    config = dict(DEFAULTS)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in PRESSES and value in ACTIONS:
            config[key] = value
    return config


def buttons_conf_text(config):
    """pen.conf's text for `config` (every press). Pure."""
    lines = [
        "# The pen's top button: notes | screenshot | quick_menu | off",
        "# (duo-shell reads it at every press; Duo Settings > Pen writes it)",
    ]
    for press in PRESSES:
        action = config.get(press, DEFAULTS[press])
        lines.append("%s = %s" % (press, action if action in ACTIONS else DEFAULTS[press]))
    return "\n".join(lines) + "\n"


def read_buttons(path=None):
    try:
        with open(path or button_conf_path()) as f:
            return buttons_from_conf(f.read())
    except OSError:
        return dict(DEFAULTS)


def set_button(press, action, path=None):
    if press not in PRESSES or action not in ACTIONS:
        raise ValueError((press, action))
    path = path or button_conf_path()
    config = read_buttons(path)
    config[press] = action
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".new", "w") as f:
        f.write(buttons_conf_text(config))
    os.replace(path + ".new", path)


# -- the pen over Bluetooth ---------------------------------------------------------

MICROSOFT_COMPANY_ID = 0x0006  # Bluetooth SIG company identifier (manufacturer data)
APPEARANCE_PEN = 0x03C7  # GAP appearance: HID, digital pen


def is_surface_pen(device):
    """Whether a BlueZ device (org.bluez.Device1's properties, unpacked) is a
    Surface pen: "pen" in its name and Microsoft's by name, company id
    (manufacturer data, Swift Pair) or USB vendor (Modalias); or a nameless
    digital pen (appearance) with Microsoft's manufacturer data. The shell
    has the same rule (duoshell/penmodel.py). Pure."""
    name = (device.get("Name") or device.get("Alias") or "").lower()
    microsoft = (
        MICROSOFT_COMPANY_ID in (device.get("ManufacturerData") or {})
        or "v045e" in (device.get("Modalias") or "").lower()
    )
    if "pen" in name:
        return "surface" in name or microsoft
    return device.get("Appearance") == APPEARANCE_PEN and microsoft


def battery_text(percent):
    return _("Battery %d %%") % percent


def device_subtitle(dev):
    """A Bluetooth device row's line: connected or not, and its battery. Pure."""
    text = _("Connected") if dev.get("connected") else _("Not connected")
    if dev.get("connected") and dev.get("battery") is not None:
        text = "%s · %s" % (text, battery_text(dev["battery"]))
    return text


def pen_status(pen):
    """The Pen page's line for a paired pen (bluetooth.Bluez.devices' dict). Pure."""
    if pen.get("connected"):
        if pen.get("battery") is not None:
            return _("Connected · %s") % battery_text(pen["battery"])
        return _("Connected")
    return _("Not connected: press the top button to connect it")


def paired_pens(devices):
    return [d for d in devices if d.get("pen") and d.get("paired")]


def choose_pen(devices, since):
    """The Surface pen to pair: one heard advertising since `since` (its
    `seen`, monotonic seconds), the strongest first. Pure."""
    heard = [d for d in devices if d.get("pen") and (d.get("seen") or -1) >= since]
    heard.sort(key=lambda d: -(d.get("rssi") if d.get("rssi") is not None else -200))
    return heard[0] if heard else None


class PairFlow:
    """Pairing the first Surface pen in pairing mode (the top button held
    until the light flashes). `step(devices, now)` says what to do next:
    ("pair", path), ("forget", path) (a pen already paired here that is
    pairing again: its old bond goes first, then it is paired when it is
    heard again), ("connected", path) (it is paired and connected already),
    ("timeout", None), or None (wait). `paired(err)` and `forgot(err)` report
    back. Pure (the page does the calls)."""

    TIMEOUT = 90

    def __init__(self, now):
        self.since = now
        self.state = "looking"
        self.path = None
        self.error = None

    def step(self, devices, now):
        if self.state != "looking":
            return None
        if now - self.since > self.TIMEOUT:
            self.state = "failed"
            self.error = "timeout"
            return ("timeout", None)
        pen = choose_pen(devices, self.since)
        if pen is None:
            return None
        self.path = pen["path"]
        if pen.get("paired"):
            if pen.get("connected"):
                self.state = "done"
                return ("connected", pen["path"])
            self.state = "forgetting"
            return ("forget", pen["path"])
        self.state = "pairing"
        return ("pair", pen["path"])

    def forgot(self, err):
        if err is not None:
            self.state, self.error = "failed", err
            return
        # Heard again (as a new device object) once it advertises again.
        self.state = "looking"

    def paired(self, err):
        if err is None:
            self.state = "done"
        else:
            self.state, self.error = "failed", err


# -- the pen's firmware: pen-fw.py (touch/tools), through duo2-pend and the helper ---------

FW_CACHE = (
    "/var/lib/duo2-pen/firmware.json"  # the pen's versions at its last connection (duo2-pend)
)
FW_STATUS = "/run/duo2-pen-fw/status.json"  # an update's progress (pen-fw.py --status)
PART_NAMES = {"BLE": _("Bluetooth"), "MCU": _("Pen"), "HAPTIC": _("Haptics")}
FW_FINAL = ("done", "failed", "refused", "stopped")
REFUSALS = {
    "charger": _("put the phone on its charger"),
    "battery": _("charge the pen to at least 50 %"),
    "battery-unknown": _("the pen's battery level is not known yet: try again in a moment"),
    "no-pen": _("press the pen's top button to connect it"),
    "other-devices": _("disconnect the other Bluetooth devices"),
    "busy": _("another program is using the pen: try again in a moment"),
    "bluez": _("Bluetooth did not answer"),
}


def read_json(path):
    import json

    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def short_version(v):
    """'5.148.139' -> '5.148' (the third number is Microsoft's build kind). Pure."""
    parts = str(v).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(v)


def versions_text(parts):
    """The parts' versions in one line, one per kind (BLE, MCU, haptics). Pure."""
    seen, out = set(), []
    for p in parts or []:
        name = p.get("name")
        if name not in PART_NAMES or name in seen:
            continue
        seen.add(name)
        out.append("%s %s" % (PART_NAMES[name], short_version(p.get("version"))))
    return " · ".join(out)


def newer_parts(current, available):
    """The kinds the files have newer than the pen (raw versions compared, as pen-fw.py
    does: newer than every version the pen reports for that component). Pure."""
    have = {}
    for c in current or []:
        have.setdefault(c.get("component"), []).append(c.get("raw", 0))
    out = []
    for p in available or []:
        mine = have.get(p.get("component"))
        if mine and p.get("raw", 0) > max(mine) and p.get("name") not in out:
            out.append(p.get("name"))
    return out


def fw_running(status, pid_alive=lambda pid: os.path.exists("/proc/%d" % pid)):
    """Whether an update is going on: a status not final, its process alive. Pure but for /proc."""
    if not status or status.get("state") in FW_FINAL:
        return False
    pid = status.get("pid")
    return isinstance(pid, int) and pid_alive(pid)


def fw_status_text(status):
    """What an update is doing, for the page. Pure."""
    status = status or {}
    state = status.get("state")
    part = PART_NAMES.get(
        {"BLE-HI": "BLE", "BLE-LO": "BLE", "MCU-B0": "MCU"}.get(
            status.get("label"), status.get("label")
        ),
        "",
    )
    if state in ("starting", "checking"):
        return _("Checking the pen...")
    if state == "waiting-link":
        return _("Press the pen's top button to connect it")
    if state == "sending":
        total = status.get("total") or 0
        pct = int(100 * (status.get("sent") or 0) / total) if total else 0
        return _("Sending %(part)s %(version)s: %(pct)d %%. Keep the pen near the phone") % {
            "part": part or status.get("label", ""),
            "version": short_version(status.get("version", "")),
            "pct": pct,
        }
    if state == "interrupted":
        return _("The pen disconnected. Press its top button: the update goes on")
    if state == "restarting":
        return _(
            "The pen restarts with its new firmware. If it does not come back within a minute, "
            "press its top button"
        )
    if state == "refused":
        reasons = [
            REFUSALS.get(p.get("code"), p.get("text", "")) for p in status.get("problems") or []
        ]
        return _("Not started: %s") % "; ".join(r for r in reasons if r)
    if state == "done":
        if status.get("delivered"):
            return _("Updated to %s") % versions_text(status["delivered"])
        if status.get("message"):
            return _("The pen took no update; it keeps its firmware")
        return _("The pen's firmware is up to date")
    if state == "failed":
        if status.get("code") == "no-link":
            return _("Stopped: the pen did not connect. Press its top button and start again")
        return _(
            "The update stopped. The pen keeps working with its previous firmware; "
            "start again to finish it"
        )
    if state == "stopped":
        return _("Stopped. The pen keeps working with its previous firmware")
    return ""


def fw_fraction(status):
    """The progress bar's fraction while sending, else None. Pure."""
    if (status or {}).get("state") != "sending" or not status.get("total"):
        return None
    return max(0.0, min(1.0, (status.get("sent") or 0) / status["total"]))


def pen_components(cache, status):
    """What is in the pen now: duo2-pend's cache (read at a connection), or the versions
    a finished update read from the pen at its end, when that read is the newer one (the
    cache follows only at the pen's next connection). Pure."""
    cache, status = cache or {}, status or {}
    if (
        status.get("state") == "done"
        and status.get("versions")
        and status.get("time", 0) >= cache.get("read_at", 0)
    ):
        return status["versions"]
    return cache.get("components")


def fw_view(cache, available, status, running):
    """The Firmware rows: {current, available, can_update, status}. `available` is the
    helper's fw-available answer (None: not read yet; {} : no files). Pure."""
    components = pen_components(cache, status)
    current = versions_text(components)
    view = {"current": current or _("Not read yet: press the pen's top button to connect it")}
    parts = (available or {}).get("parts") or []
    if available is None:
        view["available"] = ""
    elif not parts or not available.get("known"):
        view["available"] = _("No firmware files on this phone")
    else:
        newer = newer_parts(components, parts)
        name = available.get("set") or ""
        line = versions_text(parts)
        if not current:
            view["available"] = _("%(set)s: %(parts)s") % {"set": name, "parts": line}
        elif newer:
            view["available"] = _("Newer: %(set)s (%(parts)s)") % {"set": name, "parts": line}
        else:
            view["available"] = _("Up to date (%s)") % name
        view["can_update"] = bool(newer) and not running
    view.setdefault("can_update", False)
    view["status"] = fw_status_text(status) if status else ""
    view["running"] = running
    return view


def pen_apps_value(text):
    """The drawing apps as typed (commas or spaces between them) -> duo-wc.conf's
    pen_apps value ("rnote, krita"), "" for none, or None when something other
    than app ids is in it (letters, digits, dots, underscores, dashes). Pure;
    duo-settings-helper checks the value the same way."""
    if text is None or len(text) > 1000:
        return None
    apps = []
    for part in text.replace(",", " ").split():
        if not set(part) <= APP_ID_CHARS:
            return None
        if part.lower() not in (a.lower() for a in apps):
            apps.append(part)
    return ", ".join(apps)
