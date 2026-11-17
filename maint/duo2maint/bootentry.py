"""The kernel's boot entries on the ESP, the pure parts (duo2-boot, docs/UPDATES.md).

A packaged kernel (openduo-kernel-surface-duo2-<release>) gets its own
systemd-boot entry, /loader/entries/openduo-<release>[+LEFT[-DONE]].conf, and
its own directory on the ESP, /openduo/<release>/ (Image, the DTB). A new one
is written with boot counting, +3: systemd-boot counts a try down before it
boots it (renaming the file on the FAT: that needs no EFI variable, which this
UEFI loses), sorts an entry with no tries left ("bad") to the end, and so boots
the previous kernel by itself. A boot that passes the health checks blesses its
entry (the counter goes from the name). Nothing here touches duo2.conf (the
hand-managed known-good kernel, scripts/duo2-promote), rescue.conf or a
duo2-try entry.

The order (systemd-boot 257: bad entries last, then sort-key, then version,
newest first): every entry here has sort-key duo2 like duo2.conf, and the
version 10.<n>, n counting the kernels installed on this phone, so the newest
installed kernel boots first while it has tries left, then the previous good
one, then duo2.conf (version 1); a duo2-try entry (version 99) goes before them
all for its one try; rescue.conf (sort-key rescue) comes after every duo2 one.
"""

import re

PREFIX = "openduo-"
SORT_KEY = "duo2"
VERSION_MAJOR = 10
TRY_VERSION = "99"  # scripts/duo2-try's entries: before every packaged kernel
TRIES = 3
ESP_DIR = "openduo"  # /boot/efi/openduo/<release>/
CMDLINE_KEY = "openduo.entry"

RELEASE = re.compile(r"^[0-9][0-9A-Za-z._-]{0,62}$")
NAME = re.compile(r"^(?P<id>[^+]+?)(?:\+(?P<left>\d+)(?:-(?P<done>\d+))?)?\.conf$")
# What duo2-try adds to an entry's options; never carried into a packaged one.
TRY_OPTIONS = re.compile(r"^(duo2\.try=.*|hung_task_timeout_secs=60|openduo\.entry=.*)$")


def valid_release(rel):
    """A kernel release that can name an entry: no "+" (the counter's mark),
    no "/" or blanks."""
    return bool(RELEASE.match(rel or ""))


def entry_id(rel):
    if not valid_release(rel):
        raise ValueError("not a usable kernel release: %r" % rel)
    return PREFIX + rel


def release_of(eid):
    return eid[len(PREFIX) :] if eid.startswith(PREFIX) else None


def parse_name(fname):
    """openduo-7.3.4-duo2-gabc+2-1.conf -> {"id", "left", "done"}; left / done
    are None for a blessed (uncounted) entry. None for a name that is not an
    entry file."""
    m = NAME.match(fname)
    if not m:
        return None
    left = m.group("left")
    done = m.group("done")
    return {
        "id": m.group("id"),
        "left": int(left) if left is not None else None,
        "done": int(done) if done is not None else (0 if left is not None else None),
    }


def file_name(eid, left=None, done=None):
    if left is None:
        return "%s.conf" % eid
    if not done:
        return "%s+%d.conf" % (eid, left)
    return "%s+%d-%d.conf" % (eid, left, done)


def state(parsed):
    """good (blessed), trying (tries left) or bad (none left)."""
    if parsed["left"] is None:
        return "good"
    return "trying" if parsed["left"] > 0 else "bad"


def blessed_name(fname):
    p = parse_name(fname)
    return file_name(p["id"]) if p else None


def bad_name(fname):
    """The name that makes systemd-boot treat the entry as used up."""
    p = parse_name(fname)
    if not p:
        return None
    done = (p["done"] or 0) + (p["left"] or 0)
    return file_name(p["id"], 0, max(done, 1))


def entry_version(seq):
    return "%d.%d" % (VERSION_MAJOR, seq)


def seq_of(version):
    m = re.match(r"^%d\.(\d+)$" % VERSION_MAJOR, (version or "").strip())
    return int(m.group(1)) if m else None


def parse_entry(text):
    """A Boot Loader Specification entry: {key: value} (the first of each)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        out.setdefault(key, value.strip())
    return out


def clean_options(options):
    """A known-good entry's options without what a try or an earlier
    packaged entry added."""
    return " ".join(w for w in (options or "").split() if not TRY_OPTIONS.match(w))


def entry_text(rel, seq, options, initrd, dtb, os_version=""):
    eid = entry_id(rel)
    title = "OpenDuo %s (%s)" % (os_version, rel) if os_version else "OpenDuo (%s)" % rel
    lines = [
        "title   %s" % title,
        "sort-key %s" % SORT_KEY,
        "version %s" % entry_version(seq),
        "linux   /%s/%s/Image" % (ESP_DIR, rel),
    ]
    if initrd:
        lines.append("initrd  %s" % initrd)
    lines.append("devicetree /%s/%s/%s" % (ESP_DIR, rel, dtb))
    lines.append("options %s %s=%s" % (clean_options(options), CMDLINE_KEY, eid))
    return "\n".join(lines) + "\n"


def booted_entry(cmdline):
    """The packaged entry this boot came from (its openduo.entry=), or None."""
    for w in (cmdline or "").split():
        if w.startswith(CMDLINE_KEY + "="):
            return w.split("=", 1)[1] or None
    return None


def our_entries(fnames):
    """{id: (file name, parsed)} of the packaged kernels' entries."""
    out = {}
    for f in fnames:
        p = parse_name(f)
        if p and p["id"].startswith(PREFIX) and valid_release(release_of(p["id"])):
            out[p["id"]] = (f, p)
    return out


def keep_set(entries, seqs, running, keep=2):
    """Which packaged kernels stay: the running one, every one still being
    tried, the newest `keep` good ones, and the newest bad one (so the
    Updates page can say which kernel did not pass). `entries`: {id: parsed},
    `seqs`: {id: install number}."""
    keep_ids = set()
    if running:
        keep_ids.add(running)

    def newest(ids):
        return sorted(ids, key=lambda i: seqs.get(i, -1), reverse=True)

    good = [i for i, p in entries.items() if state(p) == "good"]
    trying = [i for i, p in entries.items() if state(p) == "trying"]
    bad = [i for i, p in entries.items() if state(p) == "bad"]
    keep_ids.update(trying)
    keep_ids.update(newest(good)[: max(keep, 1)])
    keep_ids.update(newest(bad)[:1])
    return keep_ids


def prune_set(entries, seqs, running, keep=2):
    """The packaged kernels whose entry and ESP files may go."""
    k = keep_set(entries, seqs, running, keep)
    return sorted(i for i in entries if i not in k)


# -- systemd-boot's order, for the tests (boot.c boot_entry_compare and
# strverscmp_improved, in the subset the entries here use) ------------------


def _vsplit(v):
    return [int(x) if x.isdigit() else x for x in re.findall(r"\d+|[A-Za-z]+", v or "")]


def version_cmp(a, b):
    sa, sb = _vsplit(a), _vsplit(b)
    for x, y in zip(sa, sb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        if isinstance(x, int):
            return 1
        if isinstance(y, int):
            return -1
        return -1 if x < y else 1
    return (len(sa) > len(sb)) - (len(sa) < len(sb))


def boot_order(entries):
    """[(file name, {sort-key, version})] in the order systemd-boot lists
    them; the first one is booted (there is no 'default' in loader.conf)."""
    import functools

    def key_cmp(a, b):
        pa, pb = parse_name(a[0]), parse_name(b[0])
        bad_a = pa is not None and pa["left"] == 0
        bad_b = pb is not None and pb["left"] == 0
        if bad_a != bad_b:
            return 1 if bad_a else -1
        ka, kb = a[1].get("sort-key"), b[1].get("sort-key")
        if bool(ka) != bool(kb):
            return -1 if ka else 1
        if ka and kb:
            if ka != kb:
                return -1 if ka < kb else 1
            r = version_cmp(a[1].get("version", ""), b[1].get("version", ""))
            if r:
                return -r
        ia = pa["id"] if pa else a[0]
        ib = pb["id"] if pb else b[0]
        return -version_cmp(ia, ib)

    return sorted(entries, key=functools.cmp_to_key(key_cmp))
