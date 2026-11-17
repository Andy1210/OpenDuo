"""Settings > Android apps (pure, tests/test_settings.py).

"Keep Android apps running in the background" is KEEP_RUNNING in
~/.config/duo2/waydroid.conf (over /etc/duo2/waydroid.conf, the system's
default). Two readers, both in android/: duo2-waydroid-idle (the session is
not stopped without an Android window) and the container manager's
hardware_manager.py as duo2-waydroid-patch changes it (Android's suspend
request does not freeze the container). Both read the file again at every
check, so the switch works at once, without a restart.

What the page shows of Android now comes from the container's cgroup
(LXC's lxc.payload.waydroid): absent = not running, `frozen 1` in its
cgroup.events = paused by Waydroid (no notifications)."""

import os

from . import conf

SYSTEM_CONF = "/etc/duo2/waydroid.conf"
KEY = "KEEP_RUNNING"
CGROUP = "/sys/fs/cgroup/lxc.payload.waydroid"
HEADER = (
    "# duo2-os: Android apps (Waydroid), the user's settings over /etc/duo2/waydroid.conf\n"
    "# (Duo Settings > Android apps; IDLE_MIN may be set here too)\n"
)


def user_conf_path():
    # ~/.config, not $XDG_CONFIG_HOME: the container manager (root) finds the
    # file from the user's home directory
    return os.path.join(os.path.expanduser("~"), ".config", "duo2", "waydroid.conf")


def keep_running(system_path=SYSTEM_CONF, user_path=None):
    value = None
    for path in (system_path, user_path or user_conf_path()):
        got = conf.read(path).get(KEY)
        if got is not None:
            value = got.strip("\"'")
    return conf.is_on(value)


def set_keep_running(on, path=None):
    path = path or user_conf_path()
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        text = HEADER
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(conf.set_key(text, KEY, "yes" if on else "no"))
    os.replace(path + ".tmp", path)


def frozen(events_text):
    for line in events_text.splitlines():
        key, _, value = line.partition(" ")
        if key == "frozen":
            return value.strip() == "1"
    return False


def state(cgroup=CGROUP):
    """'stopped', 'paused' or 'running'."""
    if not os.path.isdir(cgroup):
        return "stopped"
    try:
        with open(os.path.join(cgroup, "cgroup.events")) as f:
            return "paused" if frozen(f.read()) else "running"
    except OSError:
        return "running"
