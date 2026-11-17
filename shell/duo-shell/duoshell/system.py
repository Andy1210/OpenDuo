"""What the quick menu reads and switches outside duo-wc.

- WiFi: the WLAN radio's rfkill soft block (/sys/class/rfkill, readable by
  anyone; switched through duo-shell-helper, root).
- Brightness: the panels' backlight (/sys/class/backlight/*, the DSI panels'
  DCS backlight on the Duo 2; written through the helper).
- Auto-rotate: duo-posed's config file (/etc/duo-posed.conf, readable;
  changed through the helper, which restarts duo-posed). duo-posed has no
  control socket: its config is its interface.
- Reboot, power off: the helper (systemctl as root; the session has no
  logind seat, so polkit would refuse the user).
- Airplane mode: /var/lib/duo2/airplane (on / off, root's, readable;
  switched through the helper, which blocks WiFi and Bluetooth itself;
  duo2-wwan follows it with the modem).

Bluetooth is switched over D-Bus as the user (BlueZ allows it), see dbus.py.
The parsers are pure (tests/test_system.py); the rest never raises.
"""

import os
import subprocess

HELPER = "/usr/local/lib/duo-shell/duo-shell-helper"
RFKILL = "/sys/class/rfkill"
BACKLIGHT = "/sys/class/backlight"
POSED_CONF = "/etc/duo-posed.conf"
BRIGHTNESS_CONF = "/etc/duo2/brightness.conf"
AIRPLANE = "/var/lib/duo2/airplane"
TOUCH_FALLBACK = "/run/duo2-touchd.fallback"  # duo2-touchd in its last-resort split mode


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


# -- radios ----------------------------------------------------------------------


def rfkill_blocked(kind, root=RFKILL):
    """True when every radio of this kind (wlan, bluetooth) is soft- or
    hard-blocked, False when one is free, None when there is none."""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None
    found = None
    for entry in entries:
        base = os.path.join(root, entry)
        if _read(os.path.join(base, "type")) != kind:
            continue
        blocked = (
            _read(os.path.join(base, "soft")) == "1" or _read(os.path.join(base, "hard")) == "1"
        )
        if not blocked:
            return False
        found = True
    return found


def read_airplane(path=AIRPLANE):
    """Airplane mode: True when the switch file says on."""
    return (_read(path) or "").lower() == "on"


# -- backlight -------------------------------------------------------------------


def level_to_percent(level, maximum):
    """A backlight level as 1..100 per cent (pure; the helper does the
    inverse)."""
    if not maximum or maximum <= 0 or level is None:
        return None
    return max(1, min(100, int(round(level * 100.0 / maximum))))


def backlight_percent(root=BACKLIGHT):
    """The first panel's backlight in per cent, or None (no backlight)."""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None
    for entry in entries:
        try:
            level = int(_read(os.path.join(root, entry, "brightness")))
            maximum = int(_read(os.path.join(root, entry, "max_brightness")))
        except (TypeError, ValueError):
            continue
        return level_to_percent(level, maximum)
    return None


def has_backlight(root=BACKLIGHT):
    try:
        return bool(os.listdir(root))
    except OSError:
        return False


# -- duo-posed -------------------------------------------------------------------


def parse_conf(text):
    """`key = value` lines (comments and blanks skipped) as a dict (pure)."""
    conf = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        conf[key.strip()] = value.strip()
    return conf


def auto_rotate_state(conf):
    """What the auto-rotate tile shows (pure): 'on' (duo-posed turns the
    panels), 'off' (rotation locked), or 'dry' (auto_rotate is on, but
    duo-posed only logs: enabled = off, the default until the owner has
    tried it)."""
    on = lambda key, default: conf.get(key, default).lower() in ("on", "yes", "true", "1")  # noqa: E731
    if not on("auto_rotate", "on"):
        return "off"
    return "on" if on("enabled", "off") else "dry"


PANELS = ("both", "left", "right")


def panels_choice(conf):
    """duo-posed's `panels` (the user's panel choice over the sensors):
    both | left | right (pure)."""
    value = conf.get("panels", "both").strip().lower()
    return value if value in PANELS else "both"


def next_panels(current):
    """The quick menu's Panels tile cycles both -> left -> right -> both."""
    return PANELS[(PANELS.index(current) + 1) % len(PANELS)] if current in PANELS else "both"


def read_panels(path=POSED_CONF):
    text = _read(path)
    return panels_choice(parse_conf(text)) if text is not None else None


def read_auto_rotate(path=POSED_CONF):
    text = _read(path)
    if text is None:
        return None
    return auto_rotate_state(parse_conf(text))


def auto_brightness_on(conf):
    """duo-brightness's switch (pure): auto = on|off, off when not said."""
    return conf.get("auto", "off").lower() in ("on", "yes", "true", "1")


def read_auto_brightness(path=BRIGHTNESS_CONF):
    """True / False, or None when duo-brightness is not installed."""
    if not os.path.exists("/usr/local/bin/duo-brightness"):
        return None
    return auto_brightness_on(parse_conf(_read(path) or ""))


# -- the helper ------------------------------------------------------------------

GRANTED = ("wifi", "bluetooth", "mobile-data", "airplane", "touch", "reboot", "poweroff")


def grant_for(args):
    """A one-time grant from duo-wc for these helper words (the helper
    redeems it: an app without the shell's trusted connection gets none),
    or "" when they need none or none could be had."""
    if not args or str(args[0]) not in GRANTED:
        return ""
    from . import wc

    try:
        return (wc.request("grant", action=" ".join(str(a) for a in args)) or {}).get("token", "")
    except (OSError, RuntimeError, ValueError) as e:
        print("duo-shell: no grant for %s: %s" % (" ".join(str(a) for a in args), e), flush=True)
        return ""


def helper(*args, done=None):
    """Run `sudo -n duo-shell-helper ARGS` without blocking GTK, the grant
    on its standard input; `done(ok)` is called in the main loop when it
    ends."""
    from gi.repository import Gio, GLib

    argv = ["sudo", "-n", HELPER] + [str(a) for a in args]
    token = grant_for(args)
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
    except GLib.Error as e:
        print("duo-shell: %s: %s" % (" ".join(argv), e.message), flush=True)
        if done:
            done(False)
        return

    def finished(p, result):
        ok = False
        try:
            _ok, _out, err = p.communicate_utf8_finish(result)
            ok = p.get_successful()
            if not ok:
                print(
                    "duo-shell: helper %s failed: %s"
                    % (" ".join(str(a) for a in args), (err or "").strip()),
                    flush=True,
                )
        except GLib.Error as e:
            print(
                "duo-shell: helper %s failed: %s" % (" ".join(str(a) for a in args), e.message),
                flush=True,
            )
        if done:
            done(ok)

    proc.communicate_utf8_async(token + "\n", None, finished)


def helper_sync(*args):
    """The same, blocking (only for reboot / power off, where nothing waits)."""
    token = grant_for(args)
    return (
        subprocess.run(
            ["sudo", "-n", HELPER] + [str(a) for a in args],
            input=token + "\n",
            text=True,
            check=False,
        ).returncode
        == 0
    )
