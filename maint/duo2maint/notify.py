"""A notification from root to the GUI user's session (the shell's
org.freedesktop.Notifications, over the user's bus), best effort: no session,
no notification. Used by duo2-boot and duo2-updates."""

import os
import pwd
import subprocess

from . import system


def user_env(user):
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return None, None
    rt = "/run/user/%d" % pw.pw_uid
    if not os.path.exists(os.path.join(rt, "bus")):
        return pw, None
    return pw, {
        "HOME": pw.pw_dir,
        "USER": user,
        "XDG_RUNTIME_DIR": rt,
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=%s/bus" % rt,
        "PATH": "/usr/bin:/bin",
    }


def notify_argv(app, icon, summary, body, action=None, timeout_ms=-1):
    """busctl's Notify call (pure). `action`: a default action id (the shell
    opens the app named by the desktop-entry hint)."""
    hints = ["2", "desktop-entry", "s", "org.duo2os.Settings", "urgency", "y", "1"]
    actions = ["2", action, "Open"] if action else ["0"]
    return [
        "busctl",
        "--user",
        "call",
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
        "susssasa{sv}i",
        app,
        "0",
        icon,
        summary,
        body,
        *actions,
        *hints,
        str(timeout_ms),
    ]


def send(summary, body, app="Updates", icon="software-update-available-symbolic", user=None):
    user = user or system.gui_user()
    if not user:
        return False
    pw, env = user_env(user)
    if env is None:
        return False
    try:
        p = subprocess.run(
            notify_argv(app, icon, summary, body, action="default"),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            user=pw.pw_uid,
            group=pw.pw_gid,
            extra_groups=[],
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0
