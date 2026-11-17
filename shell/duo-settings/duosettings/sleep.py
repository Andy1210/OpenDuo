"""Settings > Battery > Sleep (pure, tests/test_settings.py).

"Stay awake while charging" is stay_awake_charging in
/etc/duo2/autosleep.conf, duo2-autosleep's (power/): on external power (a
charger, a computer's USB port) the phone does not go to sleep, the panels
still go dark. On by default (the daemon's default when the key is
missing). The page changes it through `sudo -n duo2-autosleep-switch
charging on|off` (the sudoers rule allows the GUI user exactly those and
on / off); the daemon reads the file again by itself.

Under it the page says when something else holds the automatic sleep: the
switch's hold file (automatic sleep off) or a timed hold for remote work
(`duo2-autosleep-switch hold 2h`, its end in /etc/duo2/autosleep-hold-until).
All three files are readable by anyone."""

import os
import time

from . import conf

CONF = "/etc/duo2/autosleep.conf"
HOLD = "/etc/duo2/autosleep-hold"
HOLD_UNTIL = "/etc/duo2/autosleep-hold-until"
SWITCH = "/usr/local/sbin/duo2-autosleep-switch"
KEY = "stay_awake_charging"
HOLD_MAX_S = 24 * 3600 + 300  # an end further ahead is not trusted (as the daemon)


def stay_awake_charging(path=None):
    return conf.is_on(conf.read(path or CONF).get(KEY), default=True)


def switch_argv(on):
    return ["sudo", "-n", SWITCH, "charging", "on" if on else "off"]


def hold_until(path=None, now=None):
    """The end of a timed hold in force (epoch seconds), or None."""
    try:
        with open(path or HOLD_UNTIL) as f:
            first = f.readline().split()
        end = float(first[0])
    except (OSError, IndexError, ValueError):
        return None
    left = end - (time.time() if now is None else now)
    return end if 0 < left <= HOLD_MAX_S else None


def held(hold_path=None, until_path=None, now=None):
    """('off', None): automatic sleep switched off; ('until', end): a timed
    hold; (None, None): neither."""
    if os.path.exists(hold_path or HOLD):
        return "off", None
    end = hold_until(until_path, now)
    if end is not None:
        return "until", end
    return None, None
