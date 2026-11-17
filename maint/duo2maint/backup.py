"""duo2-backup's pure parts: its settings, when it may run, the rsync
command. docs/UPDATES.md "Backups".

The phone pushes (rsync over ssh, as root, with a key only root can read,
/etc/duo2/backup/): no app on the phone can read the key, and the target
can restrict it to one directory and to writing (rrsync -wo). The target
keeps the latest copy in current/ and what a run replaced or deleted in
deleted/DATE/ (no hard links needed: any file system, a NAS share too).

What goes: the GUI user's home (minus caches: backup.filter), Android's data
(INCLUDE_ANDROID: the apps' data, the Android "SD card"), and the system's
configuration (INCLUDE_SYSTEM: /etc, /var/lib/duo2*, the package lists, as
tarballs that keep owners and modes) staged in /var/backups/duo2/backup/.
Android's files belong to Android's uids: their owners and modes go in a
list (android-owners.txt) that `duo2-backup restore-owners` re-applies, so
the target need not store owners.
"""

import re

CONF = "/etc/duo2/backup.conf"
KEY_DIR = "/etc/duo2/backup"
STAGE = "/var/backups/duo2/backup"
FILTER = "/etc/duo2/backup.filter"

DEFAULTS = {
    "TARGET": "",
    "PORT": "22",
    "KEY": KEY_DIR + "/id_ed25519",
    "KNOWN_HOSTS": KEY_DIR + "/known_hosts",
    "USER": "",  # empty: the GUI user (duo-wc.service's User=)
    "INCLUDE_ANDROID": "yes",
    "INCLUDE_SYSTEM": "yes",
    "ONLY_ON_WIFI": "yes",
    "ONLY_CHARGING": "yes",
    "BWLIMIT_KIB": "0",
    # the target's key is limited by rrsync (authorized_keys_line): paths
    # then start at its directory ("/deleted/..." is inside it)
    "RESTRICTED": "yes",
}

TARGET = re.compile(
    r"^(?:([A-Za-z0-9._-]+)@)?([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):([A-Za-z0-9._/~-]*)$"
)


def parse_conf(text, gui_user=""):
    """The settings from backup.conf's text; an empty USER is `gui_user`."""
    cfg = dict(DEFAULTS)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        if key.upper() in DEFAULTS:
            cfg[key.upper()] = value.strip("\"'")
    if not cfg["USER"]:
        cfg["USER"] = gui_user or ""
    return cfg


def yes(value):
    return str(value).lower() in ("1", "yes", "true", "on")


def check_conf(cfg):
    """Problems with the settings ([] = fine)."""
    out = []
    t = cfg.get("TARGET", "")
    if not t:
        out.append("TARGET is not set (user@host:path) in %s" % CONF)
    elif not TARGET.match(t):
        out.append("TARGET %r is not user@host:path (letters, digits, . _ - / ~ only)" % t)
    if not re.match(r"^\d{1,5}$", cfg.get("PORT", "")) or not 0 < int(cfg["PORT"]) < 65536:
        out.append("PORT is not a port number")
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", cfg.get("USER", "")):
        out.append("USER is not a user name")
    if not re.match(r"^\d{1,7}$", cfg.get("BWLIMIT_KIB", "")):
        out.append("BWLIMIT_KIB is not a number")
    for key in ("KEY", "KNOWN_HOSTS"):
        if not cfg.get(key, "").startswith("/"):
            out.append("%s must be an absolute path" % key)
    return out


def destination(target):
    """TARGET's directory for the latest copy (current/ in it)."""
    host, _, path = target.rpartition(":")
    path = path.rstrip("/")
    return "%s:%s" % (host, (path + "/" if path else "") + "current/")


def ssh_command(cfg):
    return (
        "ssh -i %s -p %s -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
        " -o UserKnownHostsFile=%s -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
        % (cfg["KEY"], cfg["PORT"], cfg["KNOWN_HOSTS"])
    )


def sources(cfg, stage=STAGE):
    """rsync's sources, relative from "/./" (rsync -R keeps the rest):
    home/USER/..., and the staged system/ and android-owners.txt."""
    out = ["/./home/%s" % cfg["USER"]]
    if yes(cfg["INCLUDE_SYSTEM"]) or yes(cfg["INCLUDE_ANDROID"]):
        out.append("%s/./staged" % stage.rstrip("/"))
    return out


def android_dir(user):
    return "/home/%s/.local/share/waydroid/data" % user


def backup_dir(cfg, date):
    """Where a run moves what it replaces or deletes: TARGET/deleted/DATE.
    rrsync takes a leading "/" as its directory and refuses ".."; a plain
    login takes it relative to current/."""
    return ("/deleted/%s" if yes(cfg["RESTRICTED"]) else "../deleted/%s") % date


def rsync_argv(cfg, date, dry_run=False, filter_path=FILTER):
    argv = [
        "rsync",
        "-rlptDH",
        "-R",
        "--numeric-ids",
        "--delete",
        "--delete-excluded",
        "--backup",
        "--backup-dir=%s" % backup_dir(cfg, date),
        "--partial-dir=.rsync-partial",
        "--filter=merge %s" % filter_path,
        "--itemize-changes",
        "--stats",
        "-e",
        ssh_command(cfg),
    ]
    if not yes(cfg["INCLUDE_ANDROID"]):
        argv.append("--exclude=/home/%s/.local/share/waydroid/" % cfg["USER"])
    if int(cfg["BWLIMIT_KIB"]):
        argv.append("--bwlimit=%s" % cfg["BWLIMIT_KIB"])
    if dry_run:
        argv.append("--dry-run")
    return argv + sources(cfg) + [destination(cfg["TARGET"])]


def on_ac(supplies):
    """Charging or full: any mains / USB supply online, or a battery that
    says so. `supplies`: [{"type":..., "online":..., "status":...}]."""
    for s in supplies:
        if s.get("type") in ("Mains", "USB", "USB_PD", "USB_C") and str(s.get("online")) == "1":
            return True
        if s.get("type") == "Battery" and s.get("status") in ("Charging", "Full"):
            return True
    return False


def may_run(cfg, wifi, ac, forced):
    """None when it may run now, else why not."""
    if forced:
        return None
    if yes(cfg["ONLY_ON_WIFI"]) and not wifi:
        return "not on WiFi"
    if yes(cfg["ONLY_CHARGING"]) and not ac:
        return "not charging"
    return None


def authorized_keys_line(pubkey, target_dir):
    """The line for the target's ~/.ssh/authorized_keys: this key may only
    write into target_dir with rsync (rrsync -wo, rsync >= 3.2.4)."""
    return 'command="rrsync -wo %s",restrict %s' % (target_dir, pubkey.strip())


def parse_stats(text):
    """rsync --stats: the transferred bytes and the file counts."""
    out = {}
    for key, pat in (
        ("files", r"Number of files: ([\d,.]+)"),
        ("transferred_files", r"Number of regular files transferred: ([\d,.]+)"),
        ("sent_bytes", r"Total bytes sent: ([\d,.]+)"),
        ("total_size", r"Total file size: ([\d,.]+)"),
    ):
        m = re.search(pat, text or "")
        if m:
            out[key] = int(re.sub(r"[,.]", "", m.group(1)))
    return out
