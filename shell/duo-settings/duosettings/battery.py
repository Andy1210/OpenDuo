"""Battery usage: the collector's report (battery/, duo2-battery-stats) turned
into what the Battery page shows. Pure apart from `load_report` and the
desktop file lookup the caller passes in (tested)."""

import json
import os
import time

REPORT = "/run/duo2-battery/report.json"

SYSTEM = {
    "comp:screen": ("Screen", "video-display-symbolic"),
    "comp:idle": ("Idle, screen off", "night-light-symbolic"),
    "comp:standby": ("Standby (suspended)", "weather-clear-night-symbolic"),
    "comp:modem": ("Mobile network", "network-cellular-symbolic"),
    "comp:wifi": ("Wi-Fi", "network-wireless-symbolic"),
    "comp:bluetooth": ("Bluetooth", "bluetooth-symbolic"),
    "comp:dsp": ("Signal processors (DSPs)", "audio-card-symbolic"),
    "system:compositor": ("Display compositor", "computer-symbolic"),
    "system:shell": ("Shell and keyboard", "view-app-grid-symbolic"),
    "system:kernel": ("Kernel", "applications-system-symbolic"),
    "system:services": ("System services", "system-run-symbolic"),
    "system:session": ("Desktop services", "preferences-system-details-symbolic"),
    "system:audio": ("Sound", "audio-speakers-symbolic"),
    "system:telephony": ("Phone services", "phone-symbolic"),
    "system:touch": ("Touch input", "input-touchpad-symbolic"),
    "system:sensors": ("Sensors and rotation", "rotation-allowed-symbolic"),
    "system:batterystats": ("Battery usage statistics", "battery-symbolic"),
    "system:remote": ("Remote login (ssh)", "network-server-symbolic"),
    "android:system": ("Android system (Waydroid)", "phone-symbolic"),
}
APP_ICON = "application-x-executable-symbolic"
DSP_NAMES = {"adsp": "Audio DSP", "cdsp": "Compute DSP", "slpi": "Sensor DSP"}
CLUSTERS = ("small", "medium", "big")


def panels_lit(base="/sys/class/drm"):
    """Is any panel on (the DRM connectors' dpms; True when unknown)?"""
    try:
        found = False
        for name in os.listdir(base):
            if "-DSI-" not in name:
                continue
            found = True
            with open(os.path.join(base, name, "dpms")) as f:
                if f.read().strip() == "On":
                    return True
        return not found
    except OSError:
        return True


def load_report(path=REPORT, text=None):
    """The report dict, or None (no collector, or not readable)."""
    try:
        if text is None:
            with open(path) as f:
                text = f.read()
        rep = json.loads(text)
        return rep if isinstance(rep, dict) and rep.get("version") == 1 else None
    except (OSError, ValueError):
        return None


def merge_entries(lists, include_charger=False):
    """{key: entry} summed over the battery list (and the charger's)."""
    out = {}
    for cls in ("battery", "charger") if include_charger else ("battery",):
        for e in lists.get(cls) or []:
            m = out.setdefault(
                e["key"], {"key": e["key"], "mah": 0.0, "cpu_mah": 0.0, "gpu_mah": 0.0, "extra": {}}
            )
            m["mah"] += e.get("mah") or 0.0
            m["cpu_mah"] += e.get("cpu_mah") or 0.0
            m["gpu_mah"] += e.get("gpu_mah") or 0.0
            for k, v in (e.get("extra") or {}).items():
                m["extra"][k] = m["extra"].get(k, 0.0) + v
    return out


def resolve(entries, lookup):
    """Give every entry a name, an icon and a section ('apps' / 'system'),
    folding entries that are the same app (a desktop file's own key and the
    program it runs) into one.

    lookup(kind, value) -> (desktop id, name, icon) or None, with kind
    'id' (a desktop id), 'exe' (a program name) or 'android' (a package)."""
    out = {}
    for key, e in entries.items():
        kind, _, value = key.partition(":")
        if key in SYSTEM:
            name, icon = SYSTEM[key]
            ident, section = key, "system"
        elif kind == "app":
            hit = lookup("id", value)
            ident, name, icon = hit if hit else (value, value.rsplit(".", 1)[-1], APP_ICON)
            section = "apps"
        elif kind == "exe":
            hit = lookup("exe", value)
            ident, name, icon = hit if hit else ("exe:" + value, value, APP_ICON)
            section = "apps"
        elif kind == "android":
            hit = lookup("android", value)
            ident, name, icon = hit if hit else ("android:" + value, value, APP_ICON)
            section = "apps"
        else:
            ident, name, icon, section = key, key, APP_ICON, "system"
        r = out.setdefault(
            ident,
            {
                "id": ident,
                "name": name,
                "icon": icon or APP_ICON,
                "section": section,
                "keys": [],
                "mah": 0.0,
                "cpu_mah": 0.0,
                "gpu_mah": 0.0,
                "extra": {},
            },
        )
        r["keys"].append(key)
        for f in ("mah", "cpu_mah", "gpu_mah"):
            r[f] += e.get(f) or 0.0
        for k, v in e["extra"].items():
            r["extra"][k] = r["extra"].get(k, 0.0) + v
    return out


def ranked(rows, total=None):
    """(rows by share, the total): share = mAh / total x 100."""
    total = sum(r["mah"] for r in rows.values()) if total is None else total
    out = []
    for r in rows.values():
        r = dict(r, share=(100.0 * r["mah"] / total) if total > 0 else 0.0)
        out.append(r)
    out.sort(key=lambda r: (-r["mah"], r["name"].lower()))
    return out, total


def pct_label(share):
    if share <= 0:
        return "0 %"
    if share < 1:
        return "<1 %"
    return "%d %%" % round(share)


def duration(seconds):
    s = int(round(seconds or 0))
    if s < 60:
        return "%d s" % s
    m = s // 60
    if m < 60:
        return "%d min" % m
    h, m = divmod(m, 60)
    if h < 48:
        return "%d h %d min" % (h, m) if m else "%d h" % h
    d, h = divmod(h, 24)
    return "%d d %d h" % (d, h)


def nbytes(n):
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1000.0


def clock(t, now=None):
    """A time of day, with the weekday when it is not today."""
    now = time.time() if now is None else now
    lt, ln = time.localtime(t), time.localtime(now)
    if lt.tm_yday == ln.tm_yday and lt.tm_year == ln.tm_year:
        return time.strftime("%H:%M", lt)
    return time.strftime("%a %H:%M", lt)


def row_subtitle(r):
    """The line under an app's or a part's name."""
    x = r["extra"]
    parts = []
    if r["section"] == "apps":
        if x.get("fg_s"):
            parts.append("%s on screen" % duration(x["fg_s"]))
        cpu = sum(x.get("cpu%d_s" % c, 0.0) for c in range(3))
        if cpu >= 1:
            parts.append("CPU %s" % duration(cpu))
        if x.get("gpu_s", 0) >= 1:
            parts.append("GPU %s" % duration(x["gpu_s"]))
        return ", ".join(parts) or "Hardly any activity"
    key = r["keys"][0]
    if key == "comp:screen":
        on = sum(x.get("panel%d_s" % i, 0.0) for i in range(2))
        return "Panels lit for %s in all" % duration(on) if on else ""
    if key in ("comp:idle", "comp:standby"):
        return duration(x.get("time_s", 0))
    if key == "comp:wifi":
        return "%s received, %s sent" % (nbytes(x.get("rx_bytes")), nbytes(x.get("tx_bytes")))
    if key == "comp:modem":
        return "Modem awake %s, %s of data" % (
            duration(x.get("awake_s", 0)),
            nbytes((x.get("rx_bytes") or 0) + (x.get("tx_bytes") or 0)),
        )
    if key == "comp:bluetooth":
        return "On for %s" % duration(x.get("on_s", 0))
    if key == "comp:dsp":
        return ", ".join(
            "%s %s" % (DSP_NAMES[d], duration(x[d + "_s"])) for d in DSP_NAMES if x.get(d + "_s")
        )
    cpu = sum(x.get("cpu%d_s" % c, 0.0) for c in range(3))
    return "CPU %s" % duration(cpu) if cpu >= 1 else ""


EXPLAIN = {
    "comp:screen": "From the time each panel was lit and its brightness, with Microsoft's screen figures for "
    "this phone (taken as both panels together, awake), scaled to what the battery measured.",
    "comp:idle": "The chip, memory and power rails while the phone is awake with both panels off. The power "
    "profile has no figure for this phone's awake idle: it starts as an assumption and is "
    "learned from the battery.",
    "comp:standby": "The phone suspended. Starts as an assumption and is learned from the battery.",
    "comp:modem": "From the time the modem was in service and awake (its own sleep counters) and the mobile "
    "data it moved, with Microsoft's radio figures.",
    "comp:wifi": "From the time Wi-Fi was on and the data it moved (the air time estimated from the bytes and "
    "packets), with Microsoft's Wi-Fi figures.",
    "comp:bluetooth": "From the time Bluetooth was on and the data it moved.",
    "comp:dsp": "From each DSP's awake time (its own sleep counters) at an assumed current: the audio DSP "
    "figure of the power profile for the audio and compute DSPs, a low one for the sensor DSP.",
}
EXPLAIN_APP = (
    "From the CPU time of its processes on each kind of core, at the speeds the cores ran "
    "(Microsoft's figures per speed), and its GPU time (an assumed figure), scaled to what "
    "the battery measured."
)


def explain(r):
    return EXPLAIN.get(r["keys"][0], EXPLAIN_APP)


def details(r):
    """[(title, value)] for an entry's details sheet."""
    x = r["extra"]
    out = [("Estimated use", "%.1f mAh" % r["mah"])]
    cpu = [x.get("cpu%d_s" % c, 0.0) for c in range(3)]
    if sum(cpu) > 0 or r["cpu_mah"] > 0:
        out.append(("CPU", "%s, %.1f mAh" % (duration(sum(cpu)), r["cpu_mah"])))
        for c, s in enumerate(cpu):
            out.append(("  on the %s cores" % CLUSTERS[c], duration(s)))
    if x.get("irq_s"):
        out.append(("Interrupts", duration(x["irq_s"])))
    if x.get("gpu_s") or r["gpu_mah"] > 0:
        out.append(("GPU", "%s, %.1f mAh" % (duration(x.get("gpu_s", 0)), r["gpu_mah"])))
    if r["section"] == "apps":
        out.append(("On screen", duration(x.get("fg_s", 0))))
        out.append(("Active in the background", duration(x.get("bg_s", 0))))
    key = r["keys"][0]
    if key == "comp:screen":
        for i, name in enumerate(("Left panel", "Right panel")):
            out.append((name + " lit", duration(x.get("panel%d_s" % i, 0))))
    elif key in ("comp:idle", "comp:standby"):
        out.append(("Time", duration(x.get("time_s", 0))))
    elif key == "comp:wifi":
        out += [
            ("Switched on", duration(x.get("up_s", 0))),
            ("Received", nbytes(x.get("rx_bytes"))),
            ("Sent", nbytes(x.get("tx_bytes"))),
        ]
    elif key == "comp:modem":
        out += [
            ("In service", duration(x.get("up_s", 0))),
            ("Modem awake", duration(x.get("awake_s", 0))),
            ("Received", nbytes(x.get("rx_bytes"))),
            ("Sent", nbytes(x.get("tx_bytes"))),
        ]
    elif key == "comp:bluetooth":
        out += [("Switched on", duration(x.get("on_s", 0))), ("Data", nbytes(x.get("bytes")))]
    elif key == "comp:dsp":
        out += [("%s awake" % DSP_NAMES[d], duration(x.get(d + "_s", 0))) for d in DSP_NAMES]
    return out


def summary_line(rep, include_charger=False):
    """(title, subtitle) of the usage summary."""
    b = rep.get("battery") or {}
    w = rep.get("window") or {}
    since = (
        "since the last full charge"
        if w.get("reason") == "full"
        else "since %s, when the statistics start" % clock(w.get("start", 0), rep.get("generated"))
    )
    if b.get("on_battery_s"):
        title = "%d %% used" % round(b.get("used_pct") or 0)
        sub = "%s on battery %s, %.0f mAh" % (
            duration(b["on_battery_s"]),
            since,
            b.get("used_mah") or 0,
        )
    else:
        title = "No time on battery"
        sub = since[0].upper() + since[1:]
    if include_charger and b.get("charger_mah"):
        sub += ". With %.0f mAh estimated while on the charger" % b["charger_mah"]
    return title, sub


def status_line(rep):
    """(level, state, detail) for the page's first row."""
    b = rep.get("battery") or {}
    lvl = b.get("level")
    st = b.get("status") or ""
    detail = ""
    if b.get("charger"):
        state = {"Full": "Full", "Charging": "Charging"}.get(st, "On the charger")
        detail = "Plugged in"
    else:
        state = "On battery"
        if b.get("remaining_h"):
            detail = "About %s left at the recent rate" % duration(b["remaining_h"] * 3600)
    return ("%s %%" % lvl if lvl is not None else "?"), state, detail


def quality_line(rep):
    """How far the figures alone were from the measured drain."""
    cal = rep.get("calibration") or {}
    bat = cal.get("battery") or {}
    chg = cal.get("charger") or {}
    if bat.get("ratio"):
        return (
            "Scaled to the drain the battery measured. Before scaling, the power figures came to "
            "%d %% of it (%d measurements)." % (round(100 / bat["ratio"]), bat.get("groups", 0))
        )
    if chg.get("ratio"):
        return (
            "No time on battery yet. On the charger the estimates are scaled to the charger's input "
            "power; before scaling the power figures came to %d %% of it."
            % round(100 / chg["ratio"])
        )
    return "Not yet scaled to a measurement: no time on battery yet."


def profile_line(rep):
    prof = (rep.get("calibration") or {}).get("profile") or {}
    if prof.get("file"):
        return (
            "Microsoft's power profile for this phone (CPU, screen, radios), read from its own "
            "stock images; the GPU, the DSPs and the idle draw are our assumptions, learned "
            "from the battery over time."
        )
    return (
        "The kernel's energy model for the CPU and our assumptions for the rest (no power "
        "profile installed: scripts/install-power-profile.sh)."
    )


def graph_geometry(rep, width, height, now=None):
    """Where the graph's parts go, in widget pixels:
    {"line": [(x, y)], "charging": [(x0, x1)], "screen": [(x0, x1)],
     "suspended": [(x0, x1)], "t0", "t1"}. The x axis runs from the
    window's start (at least an hour back) to now; y from 100 % (top) to 0."""
    g = rep.get("graph") or {}
    w = rep.get("window") or {}
    t1 = rep.get("generated") or now or time.time()
    t0 = min(w.get("start", t1), t1 - 3600)
    span = max(1.0, t1 - t0)

    def x(t):
        return max(0.0, min(width, (t - t0) / span * width))

    def y(level):
        return height - max(0.0, min(100.0, level)) / 100.0 * height

    return {
        "t0": t0,
        "t1": t1,
        "line": [(x(t), y(lvl)) for t, lvl in g.get("points") or [] if lvl is not None],
        "charging": [(x(a), x(b)) for a, b in g.get("charging") or []],
        "screen": [(x(a), x(b)) for a, b in g.get("screen") or []],
        "suspended": [(x(a), x(b)) for a, b in g.get("suspended") or []],
    }


INTERPRETERS = {"python3", "python", "sh", "bash", "perl", "env"}


def exec_program(commandline):
    """The program a desktop file runs, as the collector names a process:
    `env VAR=x prog`, `python3 /usr/bin/tool` -> prog, tool; a Flatpak's
    `flatpak run` has none (its processes carry the app id)."""
    words = commandline.replace('"', " ").split()
    while words and (words[0] == "env" or "=" in words[0]):
        words.pop(0)
    if not words or os.path.basename(words[0]) == "flatpak":
        return None
    first = os.path.basename(words[0])
    if first in INTERPRETERS:
        rest = [w for w in words[1:] if not w.startswith("-") and not w.startswith("%")]
        return os.path.basename(rest[0]) if rest else None
    return first


def desktop_lookup_factory(Gio):
    """lookup() for resolve() over the session's desktop files (Gio)."""
    cache = {}
    by_exe = {}
    for info in Gio.AppInfo.get_all():
        try:
            exe = exec_program(info.get_commandline() or "")
        except (TypeError, AttributeError):
            continue
        if exe and exe not in by_exe and info.get_id():
            by_exe[exe] = info

    def describe(info):
        icon = info.get_icon()
        return (
            info.get_id()[:-8] if info.get_id().endswith(".desktop") else info.get_id(),
            info.get_display_name() or info.get_name(),
            icon.to_string() if icon else None,
        )

    def lookup(kind, value):
        k = (kind, value)
        if k in cache:
            return cache[k]
        hit = None
        try:
            if kind == "id":
                info = Gio.DesktopAppInfo.new(value + ".desktop")
                hit = describe(info) if info else None
            elif kind == "exe":
                info = by_exe.get(value)
                hit = describe(info) if info else None
            elif kind == "android":
                info = Gio.DesktopAppInfo.new("waydroid.%s.desktop" % value)
                hit = describe(info) if info else None
        except (TypeError, ValueError):
            hit = None
        cache[k] = hit
        return hit

    return lookup
