"""The report Duo Settings shows (/run/duo2-battery/report.json): since the
last full charge, Android's choice. The window starts where the phone was
last unplugged at 90 % or more (Android resets its battery statistics on
that unplug); without one in the store, at the oldest data. The usage list
counts the time on battery (what drained the battery); the time on the
charger is estimated separately ("charger")."""

import bisect
import json
import time

from . import store

FULL_LEVEL = 90
GAP_S = 600  # samples further apart: the collector was not running, unless
# the phone was suspended in between (bridged_by)
MAX_POINTS = 360


def window_start(samples):
    """samples: [(t, level, charger)] by time. -> (start, reason)."""
    start, reason = (samples[0][0], "oldest") if samples else (time.time(), "empty")
    for (_t0, l0, c0), (t1, l1, c1) in zip(samples, samples[1:]):
        if c0 and not c1 and max(l0 or 0, l1 or 0) >= FULL_LEVEL:
            start, reason = t1, "full"
    return start, reason


def bridged_by(periods):
    """A test for two samples further apart than GAP_S: the collector ran
    through the gap if the phone was suspended in all of it but GAP_S (the
    sleep hook samples right before and after every suspend, and the
    collector stores the suspend as a period). A phone asleep all night has
    samples hours apart; one that died or hung in a suspend has no period."""
    sus = sorted((a, b) for a, b, w in periods if w == "suspend")
    starts = [a for a, _b in sus]

    def bridged(t0, t1):
        if t1 - t0 <= GAP_S:
            return True
        covered = 0.0
        last = t0
        for a, b in sus[max(0, bisect.bisect_right(starts, t0) - 1) :]:
            if a >= t1:
                break
            a, b = max(a, last), min(b, t1)
            if b > a:
                covered += b - a
                last = b
        return t1 - t0 - covered <= GAP_S

    return bridged


def _near(bridged, t0, t1):
    return t1 - t0 <= GAP_S if bridged is None else bridged(t0, t1)


def runs(samples, pred, bridged=None):
    """[(t0, t1)] where pred(sample) holds over consecutive samples."""
    out = []
    cur = None
    for s, nxt in zip(samples, samples[1:] + [None]):
        if pred(s):
            if cur is None:
                cur = s[0]
            near = nxt is not None and _near(bridged, s[0], nxt[0])
            end = nxt[0] if near else s[0]
            if nxt is None or not pred(nxt) or not near:
                out.append((cur, end))
                cur = None
    return out


def downsample(points, n=MAX_POINTS):
    if len(points) <= n:
        return points
    step = len(points) / float(n)
    out = [points[int(i * step)] for i in range(n)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def battery_totals(samples, bridged=None):
    """Time on battery, the level points lost on battery."""
    on = drop = 0.0
    for (t0, l0, c0), (t1, l1, c1) in zip(samples, samples[1:]):
        if not c0 and not c1 and _near(bridged, t0, t1):
            on += t1 - t0
            if l0 is not None and l1 is not None and l1 < l0:
                drop += l0 - l1
    return on, drop


def remaining_hours(samples, capacity_mah, rows):
    """Hours left at the recent on-battery rate (None when not on battery)."""
    if not samples or samples[-1][2]:
        return None
    t_end = samples[-1][0]
    recent = [s for s in samples if s[0] >= t_end - 3600 and not s[2]]
    if len(recent) < 2 or recent[-1][0] - recent[0][0] < 900:
        return None
    drop = (recent[0][1] or 0) - (recent[-1][1] or 0)
    hours = (recent[-1][0] - recent[0][0]) / 3600.0
    if drop <= 0:
        return None
    return (recent[-1][1] or 0) / (drop / hours)


def build(db, now=None, open_periods=(), collector=None, profile=None, calibration=None, live=None):
    """The report as a dict (json.dumps-able)."""
    now = now if now is not None else time.time()
    samples = [
        (t, level, bool(c))
        for t, level, c in db.execute(
            "SELECT t, level, charger FROM samples WHERE level IS NOT NULL ORDER BY t"
        )
    ]
    start, reason = window_start(samples)
    win = [s for s in samples if s[0] >= start]
    periods = [
        (t0, t1, w)
        for t0, t1, w in db.execute(
            "SELECT t0, t1, what FROM periods WHERE t1 >= ? ORDER BY t0", (start,)
        )
    ]
    periods += [(max(t0, start), t1, w) for t0, t1, w in open_periods if t1 >= start]

    entries = {"battery": {}, "charger": {}}
    for kind, key, mah, cpu, gpu, extra in db.execute(
        "SELECT kind, key, mah, cpu_mah, gpu_mah, extra FROM usage WHERE bucket + ? > ?",
        (store.BUCKET_S, start),
    ):
        cls = "battery" if kind == "battery" else "charger"
        e = entries[cls].setdefault(
            key, {"key": key, "mah": 0.0, "cpu_mah": 0.0, "gpu_mah": 0.0, "extra": {}}
        )
        e["mah"] += mah or 0.0
        e["cpu_mah"] += cpu or 0.0
        e["gpu_mah"] += gpu or 0.0
        e["extra"] = store.merge_extra(e["extra"], json.loads(extra) if extra else {})
    lists = {}
    for cls, d in entries.items():
        lst = sorted(
            (e for e in d.values() if e["mah"] >= 0.0005 or e["extra"].get("fg_s")),
            key=lambda e: -e["mah"],
        )
        for e in lst:
            e["mah"] = round(e["mah"], 3)
            e["cpu_mah"] = round(e["cpu_mah"], 3)
            e["gpu_mah"] = round(e["gpu_mah"], 3)
            e["extra"] = {k: round(v, 2) for k, v in e["extra"].items()}
        lists[cls] = lst

    groups = list(
        db.execute("SELECT kind, measured, prior, model, scale FROM groups WHERE t1 >= ?", (start,))
    )
    cal = {}
    for cls in ("battery", "charger"):
        g = [r for r in groups if r[0] == cls and r[1] is not None and r[2]]
        ratios = sorted(r[1] / r[2] for r in g if r[2] > 0)
        cal[cls] = {
            "groups": len(g),
            "measured_mah": round(sum(r[1] for r in g), 2),
            "prior_mah": round(sum(r[2] for r in g), 2),
            "ratio": round(sum(r[1] for r in g) / sum(r[2] for r in g), 3)
            if g and sum(r[2] for r in g)
            else None,
            "ratio_p25": round(ratios[len(ratios) // 4], 3) if ratios else None,
            "ratio_median": round(ratios[len(ratios) // 2], 3) if ratios else None,
            "ratio_p75": round(ratios[(3 * len(ratios)) // 4], 3) if ratios else None,
        }
    bridged = bridged_by(periods)
    on_s, drop = battery_totals(win, bridged)
    capacity = profile.capacity_mah() if profile else None
    last = samples[-1] if samples else None
    graph_pts = downsample([(round(t), level) for t, level, _c in win if level is not None])
    return {
        "version": 1,
        "generated": round(now, 1),
        "window": {
            "start": round(start, 1),
            "end": round(now, 1),
            "reason": reason,
            "start_level": win[0][1] if win else None,
        },
        "battery": {
            "level": last[1] if last else None,
            "charger": last[2] if last else None,
            "status": (live or {}).get("status"),
            "on_battery_s": round(on_s),
            "used_pct": round(drop, 1),
            "used_mah": round(sum(e["mah"] for e in lists["battery"]), 1),
            "charger_mah": round(sum(e["mah"] for e in lists["charger"]), 1),
            "capacity_mah": capacity,
            "remaining_h": remaining_hours(samples, capacity, None),
        },
        "graph": {
            "points": graph_pts,
            "charging": [(round(a), round(b)) for a, b in runs(win, lambda s: s[2], bridged)],
            "screen": [(round(a), round(b)) for a, b, w in periods if w == "screen"],
            "suspended": [(round(a), round(b)) for a, b, w in periods if w == "suspend"],
        },
        "entries": lists,
        "calibration": dict(
            cal,
            scales=(calibration.scales() if calibration else None),
            profile=(profile.summary() if profile else None),
        ),
        "collector": collector or {},
    }
