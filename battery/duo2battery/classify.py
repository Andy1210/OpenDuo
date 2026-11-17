"""Which app a process belongs to.

Every app of the phone runs as the same user, so the user id says nothing.
A key per process, decided once when the collector first sees it:

  app:<desktop id>      launched from a desktop file: GLib puts
                        GIO_LAUNCHED_DESKTOP_FILE in the app's environment
                        (duo-shell launches through GDesktopAppInfo), and the
                        app's own children inherit it; a Flatpak app by its
                        /.flatpak-info
  android:<package>     an Android app in Waydroid's container (its command
                        line is the package name); android:system the rest of
                        the container and Waydroid itself
  system:<part>         the phone's own parts, by name (table below); the
                        kernel's threads and its interrupt time are
                        system:kernel
  exe:<name>            anything else of a user: Duo Settings finds the
                        desktop file whose Exec runs <name> (GNOME Calls in
                        the background is exe:gnome-calls)

Only the one environment variable is read from /proc/PID/environ, and
nothing of it is kept but the desktop file's name.
"""

import os
import re

PF_KTHREAD = 0x00200000

INTERPRETERS = {"python3", "python", "sh", "bash", "dash", "perl", "linker64", "env"}
for _v in range(8, 20):
    INTERPRETERS.add("python3.%d" % _v)

SYSTEM_NAMES = {
    "duo-wc": "system:compositor",
    "duo-shell": "system:shell",
    "duo-lock": "system:shell",
    "squeekboard": "system:shell",
    "phosh-osk-stub": "system:shell",
    "pipewire": "system:audio",
    "pipewire-pulse": "system:audio",
    "wireplumber": "system:audio",
    "ModemManager": "system:telephony",
    "qmi-proxy": "system:telephony",
    "rmtfs": "system:telephony",
    "tqftpserv": "system:telephony",
    "qrtr-ns": "system:telephony",
    "duo2-wwan": "system:telephony",
    "duo2-sim": "system:telephony",
    "duo2-modem-lpm": "system:telephony",
    "duo2-callaudio": "system:telephony",
    "callaudiod": "system:telephony",
    "mmsdtng": "system:telephony",
    "duo2-touchd": "system:touch",
    "hexagonrpcd": "system:sensors",
    "duo2-ssc": "system:sensors",
    "duo-posed": "system:sensors",
    "iio-sensor-proxy": "system:sensors",
    "duo2-battery-stats": "system:batterystats",
    "sshd": "system:remote",
    "sshd-session": "system:remote",
    "sshd-auth": "system:remote",
    "waydroid": "android:system",
    "lxc-start": "android:system",
    "lxc-monitord": "android:system",
    "dbus-daemon": "system:session",
    "dconf-service": "system:session",
    "gvfsd": "system:session",
    "xdg-desktop-portal": "system:session",
    "xdg-desktop-portal-gtk": "system:session",
    "xdg-permission-store": "system:session",
    "xdg-document-portal": "system:session",
    "gnome-keyring-daemon": "system:session",
    "mpris-proxy": "system:session",
    "feedbackd": "system:session",
    "at-spi-bus-launcher": "system:session",
    "at-spi2-registryd": "system:session",
    "(sd-pam)": "system:session",
}
SYSTEM_PREFIXES = (
    ("evolution-", "system:session"),
    ("gvfsd-", "system:session"),
    ("gvfs-", "system:session"),
)
SYSTEM_PATHS = (("/usr/local/lib/duo-wc/", "system:shell"),)
# Keys a child takes over from its parent (a shell under ssh is the remote
# login's, a helper an app starts is the app's).
INHERITED = ("app:", "android:", "system:remote", "exe:")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+(:[A-Za-z0-9_.]+)?$")
DESKTOP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def proc_name(comm, argv):
    """The program's name: the script's for an interpreter, else argv[0]'s
    basename (comm is cut at 15 characters)."""
    if argv:
        first = os.path.basename(argv[0])
        if first in INTERPRETERS:
            args = argv[1:]
            for i, a in enumerate(args):
                if a == "-c":  # a command string, not a script
                    break
                if a == "-m" and i + 1 < len(args):
                    return args[i + 1], argv[0]
                if a.startswith("-"):
                    continue
                return os.path.basename(a), a
            return first, argv[0]
        return first or comm, argv[0]
    return comm, ""


def system_key(name, path):
    if name in SYSTEM_NAMES:
        return SYSTEM_NAMES[name]
    for prefix, key in SYSTEM_PREFIXES:
        if name.startswith(prefix):
            return key
    for prefix, key in SYSTEM_PATHS:
        if path.startswith(prefix):
            return key
    return None


def desktop_key(path):
    base = os.path.basename(path or "")
    if base.endswith(".desktop"):
        base = base[:-8]
    return "app:" + base if base and DESKTOP_RE.match(base) else None


def android_key(argv, comm):
    first = argv[0] if argv else comm
    if PACKAGE_RE.match(first or ""):
        return "android:" + first.split(":", 1)[0]
    return "android:system"


def classify(info, parent_key=None):
    """The key of a process from what was read about it:

    info: kthread (bool), uid, comm, argv [str], cgroup (str),
          desktop (GIO_LAUNCHED_DESKTOP_FILE or None), flatpak (app id or None)
    parent_key: the parent's key (already classified) or None
    """
    if info.get("kthread"):
        return "system:kernel"
    argv = info.get("argv") or []
    comm = info.get("comm") or ""
    if "lxc.payload" in (info.get("cgroup") or ""):
        return android_key(argv, comm)
    if info.get("flatpak"):
        return "app:" + info["flatpak"]
    name, path = proc_name(comm, argv)
    if name == "systemd":  # PID 1, or a user's service manager
        return "system:services" if (info.get("uid") or 0) < 1000 else "system:session"
    key = system_key(name, path) or system_key(comm, path)
    if key:
        return key
    if info.get("desktop"):
        key = desktop_key(info["desktop"])
        if key:
            return key
    if parent_key and parent_key.startswith(INHERITED):
        return parent_key
    if (info.get("uid") or 0) >= 1000 and name:
        return "exe:" + name
    return "system:services"


def window_key(app_id):
    """The key a duo-wc window's app id counts its foreground time to."""
    if not app_id:
        return None
    if app_id.startswith("waydroid."):
        return android_key([app_id[len("waydroid.") :]], "")
    if app_id.lower() == "waydroid":
        return "android:system"
    return "app:" + app_id if DESKTOP_RE.match(app_id) else None


SYSTEM_DESKTOP_DIRS = (
    "/usr/local/share/applications",
    "/usr/share/applications",
    "/var/lib/flatpak/exports/share/applications",
)


def exec_program(text):
    """The program a desktop file's Exec runs (its basename), from the
    file's text: `env VAR=x prog %u` gives prog."""
    exec_line = None
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Desktop Entry]" and line.startswith("Exec="):
            exec_line = line[5:]
            break
    if not exec_line:
        return None
    words = exec_line.replace('"', " ").split()
    while words and (words[0] == "env" or "=" in words[0]):
        words.pop(0)
    return os.path.basename(words[0]) if words else None


def window_aliases(app_id, dirs=SYSTEM_DESKTOP_DIRS):
    """The process keys a window's app may run under: app:<id>, and exe:<the
    program its desktop file runs> when the file is a system one (an app
    started by D-Bus or from a terminal has no desktop file variable)."""
    key = window_key(app_id)
    if not key:
        return set()
    out = {key}
    if key.startswith("app:"):
        for d in dirs:
            try:
                with open(os.path.join(d, key[4:] + ".desktop")) as f:
                    prog = exec_program(f.read(65536))
            except (OSError, UnicodeDecodeError):
                continue
            if prog and prog not in ("flatpak",):
                out.add("exe:" + prog)
            break
    return out


# -- reading a process (as root; the collector) ------------------------------------


def read_info(pid, stat):
    """What classify() needs, read from /proc (None when the process is gone).
    `stat` is the parsed /proc/PID/stat."""
    base = "/proc/%d" % pid
    info = {
        "kthread": bool(stat["flags"] & PF_KTHREAD) or pid == 2 or stat["ppid"] == 2,
        "comm": stat["comm"],
    }
    if info["kthread"]:
        return info
    try:
        with open(base + "/cmdline", "rb") as f:
            raw = f.read(4096)
        info["argv"] = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a][:16]
        with open(base + "/status") as f:
            for line in f:
                if line.startswith("Uid:"):
                    info["uid"] = int(line.split()[1])
                    break
        with open(base + "/cgroup") as f:
            info["cgroup"] = f.read(512)
    except (OSError, ValueError, IndexError):
        return None
    if "lxc.payload" in info.get("cgroup", ""):
        return info
    info["desktop"] = read_environ_var(base + "/environ", b"GIO_LAUNCHED_DESKTOP_FILE=")
    info["flatpak"] = read_flatpak_id(base + "/root/.flatpak-info")
    return info


def read_environ_var(path, prefix):
    try:
        with open(path, "rb") as f:
            data = f.read(256 * 1024)
    except OSError:
        return None
    for item in data.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix) :].decode("utf-8", "replace")[:512]
    return None


def read_flatpak_id(path):
    try:
        with open(path) as f:
            text = f.read(8192)
    except OSError:
        return None
    return parse_flatpak_info(text)


def parse_flatpak_info(text):
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Application]" and line.startswith("name="):
            v = line[5:].strip()
            return v if DESKTOP_RE.match(v) else None
    return None
