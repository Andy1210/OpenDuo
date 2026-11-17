"""The torch: the rear camera's flash LED lit as a light (the quick menu's
Flashlight tile).

The kernel drives it as an LED class device (leds-qcom-flash, PM8350C's
flash module; `white:flash`): `brightness` 0 is off, `max_brightness` is the
whole torch current, which the device tree caps at the stock limit (2 x
200 mA). Only `brightness` is the video group's (power/91-duo2-torch.rules;
the session's user is in it): the flash strobe stays root's.

Elsewhere: duo2-autosleep keeps the phone awake while it is lit (as Android
does, the light stays on with the panels dark); a suspend that happens
anyway, a shutdown and the empty-battery power-off switch it off
(power/duo2-torch-off). Nothing here notices those: the quick menu reads the
LED again whenever it shows it (every second while open).

Pure apart from the reads and the one write; tests/test_torchmodel.py.
"""

import os

LEDS = "/sys/class/leds"
# LED_FUNCTION_TORCH, then LED_FUNCTION_FLASH: a flash LED lights as a torch
# through its brightness too.
SUFFIXES = (":torch", ":flash")
ICON_ON = "duo-torch-symbolic"
ICON_OFF = "duo-torch-off-symbolic"


def find(root=LEDS):
    """The torch's LED class directory, or None: the first LED named
    <colour>:torch, else the first <colour>:flash."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for suffix in SUFFIXES:
        for name in names:
            if name.endswith(suffix):
                return os.path.join(root, name)
    return None


def _int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def read(led, access=os.access):
    """{"brightness", "max", "writable"} of the LED, or None (no LED, or
    unreadable)."""
    if not led:
        return None
    b = _int(os.path.join(led, "brightness"))
    m = _int(os.path.join(led, "max_brightness"))
    if b is None or m is None or m <= 0:
        return None
    return {"brightness": b, "max": m, "writable": access(os.path.join(led, "brightness"), os.W_OK)}


def is_on(state):
    return bool(state) and state["brightness"] > 0


def tile(state):
    """(sensitive, on, label) of the quick menu's tile; the label is one of
    "No flashlight", "No access", "On", "Off" (the caller translates)."""
    if state is None:
        return False, False, "No flashlight"
    on = is_on(state)
    if not state["writable"]:
        return False, on, "No access"
    return True, on, "On" if on else "Off"


def level(state, on):
    """The brightness a switch writes: the whole torch current (the stock
    limit, max_brightness) or 0."""
    return state["max"] if on else 0


def write(led, value):
    """Set the brightness; True when the kernel took it."""
    try:
        with open(os.path.join(led, "brightness"), "w") as f:
            f.write("%d\n" % value)
        return True
    except OSError as e:
        print("duo-shell: torch %s <- %d: %s" % (led, value, e), flush=True)
        return False
