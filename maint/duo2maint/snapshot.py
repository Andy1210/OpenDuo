"""Snapshots of the system's configuration and package state (pure parts
of duo2-snapshot and duo2-update's rollback). docs/UPDATES.md.

The root file system is ext4 (no file system snapshots), so a snapshot is
a directory under /var/backups/duo2/snapshots/ (root only, 0700):

  info.json           when, why (label), the running kernel
  packages.tsv        every package: name, architecture, version, status
  manual.txt          apt-mark showmanual (what was installed on purpose)
  holds.txt           apt-mark showhold
  etc.tar.gz          /etc
  usr-local.tar.gz    /usr/local (our components)
  opt.tar.gz          /opt (the touch runtime, the power profile)
  var-lib.tar.gz      /var/lib/duo2* (lock, touch calibration...) and BlueZ's pairings
  esp.tar.gz          the ESP (read-only mount): kernels, DTBs, initrds, loader entries
  protected.sha256    sha256 of every file under the ESP, the firmware and the
                      running kernel's modules: duo2-update compares it after an upgrade
  debs/               the packages an upgrade is about to replace, rebuilt from the
                      installed files by dpkg-repack: the way back (duo2-update rollback)

It holds secrets (WiFi passwords, ssh host keys, the lock PIN's hash,
Bluetooth link keys): it never leaves root's directory except through
duo2-backup, to the owner's own target.
"""

import re
import time

ROOT = "/var/backups/duo2/snapshots"
NAME = re.compile(r"^(\d{8}-\d{6})(?:-([a-z0-9][a-z0-9-]{0,31}))?$")

PARTS = {
    "etc": ["/etc"],
    "usr-local": ["/usr/local"],
    "opt": ["/opt"],
    "var-lib": ["/var/lib/duo2*", "/var/lib/bluetooth"],
    "esp": ["/boot/efi"],
}
RESTORABLE = ("etc", "usr-local", "opt", "var-lib")


def name_for(now, label=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    if label:
        label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:32]
    return stamp + ("-" + label if label else "")


def is_name(name):
    return bool(NAME.match(name or ""))


def to_prune(names, keep):
    """The oldest snapshots beyond `keep` (at least one stays; names sort
    by time)."""
    keep = max(1, int(keep))
    snaps = sorted(n for n in names if is_name(n))
    return snaps[:-keep] if len(snaps) > keep else []


def parse_packages(text):
    """packages.tsv / `dpkg-query -W -f '${Package}\\t${Architecture}\\t${Version}\\t${db:Status-Abbrev}\\n'`:
    {name: (arch, version)} of the installed ones ("ii", "hi", ...)."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, arch, version, status = (p.strip() for p in parts[:4])
        if len(status) >= 2 and status[1] == "i":
            out[name] = (arch, version)
    return out


def changes(before, now):
    """How the packages moved since a snapshot: {"changed": [(name, old,
    new)], "added": [(name, new)], "removed": [(name, old)]}."""
    changed, added, removed = [], [], []
    for name in sorted(set(before) | set(now)):
        b, n = before.get(name), now.get(name)
        if b and n and b[1] != n[1]:
            changed.append((name, b[1], n[1]))
        elif n and not b:
            added.append((name, n[1]))
        elif b and not n:
            removed.append((name, b[1]))
    return {"changed": changed, "added": added, "removed": removed}


def deb_name(name, version, arch):
    """dpkg-repack's file name: NAME_VERSION_ARCH.deb, the epoch dropped."""
    v = version.split(":", 1)[1] if ":" in version else version
    return "%s_%s_%s.deb" % (name, v, arch)


def parse_sums(text):
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^([0-9a-f]{64})  (.+)$", line)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def compare_sums(before, now):
    """{"changed": [...], "gone": [...], "new": [...]} paths."""
    changed = sorted(p for p in before if p in now and before[p] != now[p])
    gone = sorted(p for p in before if p not in now)
    new = sorted(p for p in now if p not in before)
    return {"changed": changed, "gone": gone, "new": new}
