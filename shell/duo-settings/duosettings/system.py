"""The phone's settings as data: readers, the root helpers, duo-wc's socket,
PipeWire, time, the lock's PIN. Parsers and computations are pure
(tests/test_system.py); the rest runs small commands."""

import gettext
import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess

from . import conf

_ = gettext.gettext

HELPER = "/usr/local/lib/duo-settings/duo-settings-helper"
SHELL_HELPER = "/usr/local/lib/duo-shell/duo-shell-helper"
WC_CONF = "/etc/duo-wc/duo-wc.conf"
POSED_CONF = "/etc/duo-posed.conf"
DISPLAY_CONF = "/etc/duo-settings/display.conf"
REFRESH_PARAM = "/sys/module/panel_microsoft_elgin/parameters/refresh"
BACKLIGHT = "/sys/class/backlight"

SCALES = ["2", "2.25", "2.5", "2.75", "3"]
IDLE_CHOICES = [30, 60, 120, 300, 600, 0]  # seconds, 0 = never


def run(argv, timeout=10):
    """(ok, stdout) of a command; never raises."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def generated_locales():
    """The xx_YY.UTF-8 locales built into the system, sorted."""
    ok, out = run(["locale", "-a"])
    found = set()
    for name in out.split():
        m = re.fullmatch(r"([a-z]{2,3}_[A-Z]{2})\.utf-?8", name, re.I)
        if m:
            found.add(m.group(1) + ".UTF-8")
    return sorted(found)


def helper(*args):
    return run(["sudo", "-n", HELPER] + [str(a) for a in args], timeout=30)[0]


def shell_helper(*args):
    return run(["sudo", "-n", SHELL_HELPER] + [str(a) for a in args])[0]


# -- duo-wc ---------------------------------------------------------------------


def wc_request(request, **args):
    """One request on duo-wc's control socket: its "ok" value, or None."""
    path = os.environ.get("DUO_WC_SOCKET", "/run/duo-wc/duo-wc.sock")
    msg = dict(id=1, request=request, **args)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(path)
            s.sendall((json.dumps(msg) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.decode() or "{}")
        return reply.get("ok")
    except (OSError, ValueError):
        return None


def wc_state():
    return wc_request("get_state") or {}


# -- display --------------------------------------------------------------------


def backlights():
    return sorted(glob.glob(os.path.join(BACKLIGHT, "*")))


def brightness_percent():
    """The first panel's backlight in per cent (both are set alike), or None."""
    for base in backlights():
        try:
            with open(os.path.join(base, "brightness")) as f:
                cur = int(f.read())
            with open(os.path.join(base, "max_brightness")) as f:
                top = int(f.read())
            return max(1, min(100, round(100 * cur / top))) if top else None
        except (OSError, ValueError):
            continue
    return None


def refresh_rate():
    """The refresh the panels are set to (the module parameter: 60 or 90)."""
    try:
        with open(REFRESH_PARAM) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def wc_conf():
    return conf.read(WC_CONF)


def scale_label(value):
    return {"2": "200 %", "2.25": "225 %", "2.5": "250 %", "2.75": "275 %", "3": "300 %"}.get(
        value, value
    )


def normalise_scale(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "2.5"
    best = min(SCALES, key=lambda s: abs(float(s) - v))
    return best


def idle_label(seconds, gettext=lambda s: s):
    if seconds == 0:
        return gettext("Never")
    if seconds < 60:
        return gettext("%d seconds") % seconds
    minutes = seconds // 60
    return gettext("1 minute") if minutes == 1 else gettext("%d minutes") % minutes


# -- rotation and poses ---------------------------------------------------------


def posed_conf():
    return conf.read(POSED_CONF)


# -- sound ----------------------------------------------------------------------


def parse_wpctl_volume(text):
    """'Volume: 0.25 [MUTED]' -> (25, True)."""
    m = re.search(r"Volume:\s*([0-9.]+)", text or "")
    if not m:
        return None, False
    return round(float(m.group(1)) * 100), "MUTED" in text


def volume():
    ok, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    return parse_wpctl_volume(out) if ok else (None, False)


def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    return run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%.2f" % (percent / 100)])[0]


def set_mute(muted):
    return run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"])[0]


def parse_sinks(text):
    """`pactl -f json list sinks` -> [(name, description)]."""
    try:
        sinks = json.loads(text)
    except ValueError:
        return []
    return [(s.get("name", ""), s.get("description") or s.get("name", "")) for s in sinks]


def sinks():
    ok, out = run(["pactl", "-f", "json", "list", "sinks"])
    return parse_sinks(out) if ok else []


def default_sink():
    ok, out = run(["pactl", "get-default-sink"])
    return out.strip() if ok else None


def set_default_sink(name):
    return run(["pactl", "set-default-sink", name])[0]


# -- date and time --------------------------------------------------------------


def parse_busctl_props(names, text):
    """`busctl get-property ... A B C` prints one value per line ('s "Europe/Berlin"',
    'b true') -> {name: python value}."""
    out = {}
    for name, line in zip(names, text.splitlines()):
        kind, _, value = line.partition(" ")
        if kind == "s":
            out[name] = value.strip().strip('"')
        elif kind == "b":
            out[name] = value.strip() == "true"
        else:
            out[name] = value.strip()
    return out


TIMEDATE_PROPS = ("Timezone", "NTP", "NTPSynchronized", "CanNTP")


def timedate():
    """timedated's settings. Property by property: `timedatectl show` fails
    here ("Failed to parse bus message: Input/output error": the RTC
    property cannot be read on this phone)."""
    ok, out = run(
        [
            "busctl",
            "get-property",
            "org.freedesktop.timedate1",
            "/org/freedesktop/timedate1",
            "org.freedesktop.timedate1",
        ]
        + list(TIMEDATE_PROPS)
    )
    return parse_busctl_props(TIMEDATE_PROPS, out) if ok else {}


def timezones():
    ok, out = run(["timedatectl", "list-timezones"])
    if ok and out.strip():
        return out.split()
    zones = []
    for path in glob.glob("/usr/share/zoneinfo/*/*"):
        rel = os.path.relpath(path, "/usr/share/zoneinfo")
        if rel.split("/")[0] not in ("posix", "right", "Etc") and os.path.isfile(path):
            zones.append(rel)
    return sorted(zones)


def zone_matches(zone, query):
    q = query.strip().lower().replace(" ", "_")
    return not q or q in zone.lower()


# -- the lock -------------------------------------------------------------------
# Root keeps the PIN (shell/duo-shell/duoshell/lockstore.py, docs/hardware/security.md): /var/lib/duo2-lock, through duo-lock-helper only. This page
# reads the policy file (no secret in it) and hands every change, with the
# current PIN when one is set, to the helper; it never sees a hash.

LOCK_HELPER = "/usr/local/lib/duo-shell/duo-lock-helper"
LOCK_POLICY = "/var/lib/duo2-lock/policy"
PIN_MIN, PIN_MAX = 4, 12
LOCK_OK, LOCK_WRONG, LOCK_INVALID, LOCK_WAIT, LOCK_NO_PIN = 0, 1, 2, 3, 4
LOCK_ENCRYPTED, LOCK_REFUSED = 5, 6  # the home is encrypted; refused (a reason on stdout)
LOCK_DEFAULTS = {"pin": "no", "lock_on_blank": "off", "lock_on_close": "on"}


def valid_pin(pin):
    return pin.isascii() and pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX


def parse_lock_policy(text):
    """duo-lock-helper's policy file -> {"pin": yes|no, "lock_on_blank",
    "lock_on_close": on|off} (lockstore.parse_policy's rules)."""
    policy = dict(LOCK_DEFAULTS)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (p.strip() for p in line.split("=", 1))
        if key == "pin" and value in ("yes", "no"):
            policy[key] = value
        elif key in ("lock_on_blank", "lock_on_close") and value in ("on", "off"):
            policy[key] = value
    return policy


def lock_policy(path=LOCK_POLICY):
    try:
        with open(path) as f:
            return parse_lock_policy(f.read())
    except OSError:
        return dict(LOCK_DEFAULTS)


def lock_answer(code, out):
    """What the helper's answer means for the page: (ok, problem) where
    problem is one of "wrong", "wait SECONDS", "invalid", "failed" or None."""
    if code in (LOCK_OK, LOCK_NO_PIN):
        return True, None
    if code == LOCK_WRONG:
        return False, "wrong"
    if code == LOCK_WAIT:
        parts = (out or "").split()
        return False, "wait %s" % (parts[1] if len(parts) > 1 and parts[1].isdigit() else "30")
    if code == LOCK_INVALID:
        return False, "invalid"
    if code == LOCK_ENCRYPTED:
        return False, "encrypted"
    if code == LOCK_REFUSED:
        lines = (out or "").strip().splitlines()
        return False, "refused " + lines[-1] if lines else "refused"
    return False, "failed"


# -- the home's encryption (homecrypt/README.md) ------------------------------------
# Its state is root's public file; every change goes through duo-lock-helper
# (`home enable|disable|cancel|recovery-key`, the PIN on its stdin). The page
# never sees a key.

HOME_STATE = "/var/lib/duo2-lock/home.state"
HOME_SUPPORTED = "/sys/fs/ext4/features/encryption"
HOME_STATES = ("plain", "encrypt-pending", "encrypting", "encrypted", "decrypt-pending",
               "decrypting")  # fmt: skip
RECOVERY_DIGITS = 36


def parse_home_state(text):
    """The state file -> {"state", "progress", "detail", "recovery"}
    (homecrypt's store.parse_state, the keys the page needs)."""
    st = {"state": "plain", "progress": 0, "detail": "", "recovery": "no"}
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = (p.strip() for p in line.split("=", 1))
        if key == "state" and value in HOME_STATES:
            st[key] = value
        elif key == "progress" and value.isdigit():
            st[key] = min(100, int(value))
        elif key == "recovery" and value in ("yes", "no"):
            st[key] = value
        elif key == "detail":
            st[key] = value[:300]
    return st


def home_state(path=HOME_STATE, supported=HOME_SUPPORTED):
    try:
        with open(path) as f:
            st = parse_home_state(f.read())
    except OSError:
        st = parse_home_state("")
    st["supported"] = os.path.exists(supported)
    return st


def home_view(st, has_pin):
    """What the page shows for the home's encryption: {"active" (the
    switch), "sensitive", "subtitle", "restart" (offer a restart),
    "cancel" (a change waits and can be dropped), "recovery_row"}."""
    state = st.get("state", "plain")
    v = {"active": state in ("encrypt-pending", "encrypting", "encrypted"),
         "sensitive": True, "restart": False, "cancel": False,
         "recovery_row": state in ("encrypt-pending", "encrypting", "encrypted")}  # fmt: skip
    if state == "plain":
        if not st.get("supported"):
            v.update(sensitive=False, subtitle=_("This system cannot encrypt files"))
        elif not has_pin:
            v.update(sensitive=False, subtitle=_("Set a PIN first"))
        else:
            v["subtitle"] = _("Off")
    elif state == "encrypt-pending":
        v.update(subtitle=_("Starts at the next restart"), restart=True, cancel=True)
    elif state == "encrypting":
        v.update(sensitive=False, subtitle=_("Being encrypted: %d %%") % st.get("progress", 0))
    elif state == "encrypted":
        v["subtitle"] = _("On: your data unlocks with the PIN after a restart")
    elif state == "decrypt-pending":
        v.update(subtitle=_("Turns off at the next restart"), restart=True, cancel=True)
    else:
        v.update(sensitive=False, subtitle=_("Being decrypted: %d %%") % st.get("progress", 0))
    if st.get("detail"):
        v["subtitle"] += " · " + st["detail"]
    return v


# -- about ----------------------------------------------------------------------


def os_release():
    try:
        with open("/etc/os-release") as f:
            return conf.parse(f.read().replace('"', ""))
    except OSError:
        return {}


def debian_version():
    try:
        with open("/etc/debian_version") as f:
            return f.read().strip()
    except OSError:
        return ""


def battery():
    """{capacity, status, health (per cent of design), cycles} or {}."""
    for base in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(os.path.join(base, "type")) as f:
                if f.read().strip() != "Battery":
                    continue
        except OSError:
            continue

        def read(name, base=base):
            try:
                with open(os.path.join(base, name)) as f:
                    return f.read().strip()
            except OSError:
                return None

        info = {
            "capacity": read("capacity"),
            "status": read("status"),
            "cycles": read("cycle_count"),
        }
        full = read("charge_full") or read("energy_full")
        design = read("charge_full_design") or read("energy_full_design")
        info["health"] = battery_health(full, design)
        if info["health"] is None:
            # The Duo 2's battery manager reports the state of health itself.
            soh = read("state_of_health")
            info["health"] = int(soh) if soh and soh.isdigit() and 0 < int(soh) <= 100 else None
        return info
    return {}


def battery_health(full, design):
    try:
        full, design = int(full), int(design)
    except (TypeError, ValueError):
        return None
    if design <= 0 or full <= 0:
        return None
    return round(100 * full / design)


def storage(path="/"):
    try:
        u = shutil.disk_usage(path)
        return u.used, u.total
    except OSError:
        return None, None


def memory_total():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def human_bytes(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1000.0
    return "?"


def kernel():
    return platform.release()


# -- the app drawer ---------------------------------------------------------------
# duo-shell's ~/.config/duo-shell/launcher.conf (duoshell/launchermodel.py):
# `view = grid | list`, read when the drawer opens.

DRAWER_VIEWS = ("grid", "list")


def launcher_conf_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "launcher.conf")


def drawer_view(path=None):
    v = conf.read(path or launcher_conf_path()).get("view", "grid")
    return v if v in DRAWER_VIEWS else "grid"


def set_drawer_view(view, path=None):
    if view not in DRAWER_VIEWS:
        raise ValueError(view)
    path = path or launcher_conf_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".new", "w") as f:
        f.write("# The app drawer: grid or list (duo-shell; Duo Settings writes it too)\n")
        f.write("view = %s\n" % view)
    os.replace(path + ".new", path)
