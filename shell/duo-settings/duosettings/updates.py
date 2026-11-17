"""Settings > Updates, the pure parts (tests/test_updates.py): the helper's
answers (duo2-updates status, maint/duo2-updates), what the rows say, when
Install may be pressed, the changes as headings and points.

The helper does all the work as root, in systemd units (a closed Settings
never stops an install): this page only reads its state and starts jobs."""

import json
import time

HELPER = "/usr/local/sbin/duo2-updates"


def parse(out):
    """The helper's one JSON line (the last line of its output)."""
    try:
        data = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "no answer"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}


def job_running(st):
    job = st.get("job") or {}
    return job.get("state") == "running"


def last(st):
    return st.get("last") or {}


def counts(st):
    """(ours, debian, security) of the last check."""
    lc = last(st)
    ours = lc.get("ours") or []
    deb = lc.get("debian") or []
    sec = sum(1 for i in deb if i.get("security"))
    return len(ours), len(deb), sec


def when(ts, now=None):
    """A check's time as the page says it: ("today", "14:05"), ("yesterday",
    "09:30"), ("date", "12 Nov"), or ("never", "")."""
    if not ts:
        return ("never", "")
    now = now if now is not None else time.time()
    t, n = time.localtime(ts), time.localtime(now)
    hm = "%02d:%02d" % (t.tm_hour, t.tm_min)
    if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
        return ("today", hm)
    y = time.localtime(now - 86400)
    if (t.tm_year, t.tm_yday) == (y.tm_year, y.tm_yday):
        return ("yesterday", hm)
    return ("date", "%d %s" % (t.tm_mday, time.strftime("%b", t)))


# The install's phases as the helper names them (maint/duo2-updates, its
# job record's "step"); the page translates them.
PHASES = (
    "starting",
    "plan",
    "refresh",
    "download",
    "snapshot",
    "install",
    "verify",
    "cleanup",
    "recheck",
)


def phase(step):
    """A job step as the page shows it: one of PHASES, or "" for anything
    else (an older helper's free text is not shown)."""
    return step if step in PHASES else ""


def status(st):
    """What the top row says, as (key, values) the page translates:
    ("checking",), ("installing", phase), ("failed", error), ("restart",),
    ("updates", n), ("up-to-date",), ("never",)."""
    job = st.get("job") or {}
    if job_running(st):
        return (
            ("checking",) if job.get("job") == "check" else ("installing", phase(job.get("step")))
        )
    if job.get("state") == "failed" and job.get("finished", 0) >= last(st).get("time", 0):
        return ("failed", job.get("error") or "")
    if st.get("reboot_required"):
        return ("restart",)
    if not last(st):
        return ("never",)
    ours, deb, _sec = counts(st)
    return ("updates", ours + deb) if ours + deb else ("up-to-date",)


def can_install(st):
    """(True, None) or (False, why): ("none",), ("busy",), ("power", text),
    ("kernel",), ("refused", [reasons])."""
    ours, deb, _sec = counts(st)
    if not ours + deb:
        return False, ("none",)
    if job_running(st):
        return False, ("busy",)
    if (st.get("kernel") or {}).get("running", {}).get("state") == "trying":
        return False, ("kernel",)
    refuse = last(st).get("refuse") or []
    if refuse:
        return False, ("refused", refuse)
    if st.get("may_install"):
        return False, ("power", st["may_install"])
    return True, None


def package_rows(st):
    """OpenDuo's packages one by one: [(name, "old -> new")]; a new one has
    no old version."""
    out = []
    for i in last(st).get("ours") or []:
        old, new = i.get("old"), i.get("new") or ""
        out.append((i.get("name", ""), "%s → %s" % (short(old), short(new)) if old else short(new)))
    return out


def short(version):
    """0.1.0~dev+20261117001503.g5b008a3 -> 0.1.0-dev (20261117); others as they are."""
    if not version:
        return ""
    v = version.split(":", 1)[-1]
    if "+" in v and ".g" in v.split("+", 1)[1]:
        base, build = v.split("+", 1)
        return "%s (%s)" % (base.replace("~", "-"), build.split(".", 1)[0][:8])
    return v


def debian_names(st):
    return sorted(i.get("name", "") for i in last(st).get("debian") or [])


def sections(st):
    """The changes: [{"title", "items": [(heading or None, [points])]}],
    newest first, from CHANGELOG.md's sections (Keep a Changelog)."""
    out = []
    for s in last(st).get("changes") or []:
        v = s.get("version", "")
        title = v if v.lower() != "unreleased" else "In development"
        if s.get("date"):
            title = "%s, %s" % (title, s["date"])
        groups, head, points = [], None, []
        for line in (s.get("text") or "").splitlines():
            if line.startswith("### "):
                if points or head:
                    groups.append((head, points))
                head, points = line[4:].strip(), []
            elif line.startswith(("- ", "* ")):
                points.append(line[2:].strip())
            elif line.startswith("  ") and points:
                points[-1] += " " + line.strip()
            elif line.strip() and not points and head is None:
                points.append(line.strip())
        if points or head:
            groups.append((head, points))
        out.append({"title": title, "items": groups})
    return out


def kernel_note(st):
    """("trying", release, tries left) while a new kernel is checked,
    ("failed", release) when the newest one did not pass, else None."""
    k = st.get("kernel") or {}
    run = k.get("running") or {}
    if run.get("state") == "trying":
        left = None
        for e in k.get("kernels") or []:
            if e.get("release") == run.get("release"):
                left = e.get("tries_left")
        return ("trying", run.get("release", ""), left)
    ks = k.get("kernels") or []
    if ks and ks[0].get("state") == "bad":
        return ("failed", ks[0].get("release", ""))
    return None


def poll_seconds(st):
    return 2 if job_running(st) else 30
