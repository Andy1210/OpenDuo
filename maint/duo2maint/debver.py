"""Debian version comparison (pure): dpkg's own rules (lib/dpkg/version.c,
verrevcmp), so the checks give the answer `dpkg --compare-versions` would.

  [epoch:]upstream[-revision]; "~" sorts before everything, even the end,
  letters before other characters, digit runs compare as numbers.
"""

import re


def _order(c):
    if c == "":
        return 0
    if c.isdigit():
        return 0
    if c.isalpha() and c.isascii():
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a, b):
    i = j = 0
    la, lb = len(a), len(b)

    def ch(s, k):
        return s[k] if k < len(s) else ""

    while i < la or j < lb:
        first_diff = 0
        while (i < la and not a[i].isdigit()) or (j < lb and not b[j].isdigit()):
            ac, bc = _order(ch(a, i)), _order(ch(b, j))
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < la and a[i] == "0":
            i += 1
        while j < lb and b[j] == "0":
            j += 1
        while i < la and j < lb and a[i].isdigit() and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < la and a[i].isdigit():
            return 1
        if j < lb and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def split(version):
    """(epoch, upstream, revision)."""
    v = version.strip()
    epoch = 0
    if ":" in v:
        e, v = v.split(":", 1)
        epoch = int(e) if e.isdigit() else 0
    if "-" in v:
        upstream, revision = v.rsplit("-", 1)
    else:
        upstream, revision = v, ""
    return epoch, upstream, revision


def compare(a, b):
    """<0, 0, >0 as a sorts before, with, after b."""
    ea, ua, ra = split(a)
    eb, ub, rb = split(b)
    if ea != eb:
        return ea - eb
    r = _verrevcmp(ua, ub)
    if r:
        return r
    return _verrevcmp(ra, rb)


def newer(a, b):
    return compare(a, b) > 0


LOCAL_SUFFIX = re.compile(r"[+~]duo2\.\d+$")


def is_local(version):
    """A package we rebuilt (camera/build-debs.sh: 1.4.2-1+duo2.1,
    0.7.2-1~duo2.3)."""
    return bool(LOCAL_SUFFIX.search(version or ""))


def local_base(version):
    """The Debian version a rebuild started from: 1.4.2-1+duo2.1 -> 1.4.2-1."""
    return LOCAL_SUFFIX.sub("", version)
