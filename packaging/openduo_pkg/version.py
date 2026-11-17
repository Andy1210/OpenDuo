"""The packages' versions (pure; docs/UPDATES.md "Versions").

OpenDuo's packages: VERSION with its "-" as "~" (a pre-release sorts before
the release), "+", the commit's UTC time and short hash:
    0.1.0-dev, commit 2026-11-17 00:15:03, 5b008a3 -> 0.1.0~dev+20261117001503.g5b008a3
    0.1.0                                          -> 0.1.0+20261201120000.gabcdef1
The time comes first, so a later commit is a newer package (a hash alone
does not sort); the commit's time, not the build's, so the same commit
builds the same version (reproducible). A tree with uncommitted changes
adds ".dirty" (never published).

A kernel: its own version (7.3.4) + the kernel tree's commit time and hash,
so a kernel update does not wait for an OpenDuo release:
    7.3.4-duo2-g3ba7db140, committed 2026-11-15 10:02:00 -> 7.3.4+20261115100200.g3ba7db140
"""

import re
import time

VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.]*)?$")
HASH = re.compile(r"^[0-9a-f]{7,40}$")


def stamp(epoch):
    return time.strftime("%Y%m%d%H%M%S", time.gmtime(int(epoch)))


def deb_version(version, epoch, short_hash, dirty=False):
    v = (version or "").strip()
    if not VERSION.match(v):
        raise ValueError("VERSION must be X.Y.Z or X.Y.Z-tag, not %r" % v)
    if not HASH.match(short_hash or ""):
        raise ValueError("not a commit hash: %r" % short_hash)
    out = "%s+%s.g%s" % (v.replace("-", "~", 1), stamp(epoch), short_hash)
    return out + ".dirty" if dirty else out


def kernel_release_parts(release):
    """7.3.4-duo2-g3ba7db140[-dirty] -> ("7.3.4", "3ba7db140", dirty)."""
    m = re.match(r"^(\d+\.\d+(?:\.\d+)?)-.*?-g([0-9a-f]{7,40})(-dirty)?$", release or "")
    if not m:
        raise ValueError("not an OpenDuo kernel release: %r" % release)
    return m.group(1), m.group(2), bool(m.group(3))


def kernel_version(release, epoch):
    base, h, dirty = kernel_release_parts(release)
    out = "%s+%s.g%s" % (base, stamp(epoch), h)
    return out + ".dirty" if dirty else out


def os_version(deb):
    """0.1.0~dev+20261117001503.g5b008a3 -> 0.1.0-dev."""
    return deb.split(":", 1)[-1].split("+", 1)[0].replace("~", "-")


def publishable(deb):
    return not deb.endswith(".dirty")
