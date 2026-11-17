"""The estimate: what each part of the phone and each app drew over an
interval, and the calibration that makes the parts add up to what the
battery measured. Pure (no I/O); tests/test_battery.py.

Per interval (one collector sample to the next, about a minute):

  CPU      each process's CPU seconds per cluster x the cluster's current per
           busy core at the speeds it ran (cpufreq time in state over the
           interval, the same mix for every process of the cluster) + cpu.active.
           Which cluster: the kernel keeps no per-task time per CPU, so each
           thread's seconds go to the cluster it last ran on (its stat's
           `processor`), and an iterative proportional fit then moves them so
           that every cluster's total matches its measured busy time
           (/proc/stat); a process keeps its own total.
  GPU      each DRM client's busy nanoseconds (msm fdinfo) at its average
           speed (cycles / time) x an assumed GPU current.
  screen   per lit panel: half the table's screen.on + half its screen.full
           x brightness (the table has one screen; we take it as both panels).
  idle     awake with both panels off: an assumed floor (the chip, memory and
           power rails awake), learned by the calibration.
  standby  suspended: an assumed floor, learned likewise.
  Wi-Fi    interface up x controller idle + airtime from bytes and packets x
           rx / tx current (assumed rates).
  modem    in service x radio.on + the modem DSP awake (qcom_stats) x controller
           idle + airtime from bytes (+ a tail) x radio active.
  Bluetooth  powered x on + airtime from the HCI byte and packet counts x active.
  DSPs     each one's awake time (qcom_stats) x its current.

Calibration: the parts are grouped (idle, standby, screen, cpu, gpu, radio,
dsp); a ridge regression over the discharge groups learns one scale per group
(starting at 1: the table's or our assumed figures), so a wrong assumption is
corrected by what the battery measured. Then each group of intervals (about
10 mAh of measured drain) is scaled once more so that its estimates add up
exactly to the measured drain: the shown numbers always sum to what the
battery lost.
"""

import math

GROUPS = ("idle", "standby", "screen", "cpu", "gpu", "radio", "dsp")
IDLE_PRIOR_MA = 100.0  # assumed: awake, panels off (~140 mA in all, measured)
STANDBY_PRIOR_MA = 40.0  # assumed: suspended (s2idle without CX collapse)
WIFI_RATE_BPS = 100e6  # assumed effective Wi-Fi airtime rate
WIFI_PKT_S = 0.0005  # assumed radio wake per packet
MODEM_RATE_BPS = 20e6  # assumed effective LTE rate
MODEM_TAIL_S = 10.0  # assumed connected tail after traffic in an interval
BT_RATE_BPS = 1e6
BT_PKT_S = 0.000625  # one baseband slot
RIDGE = 100.0  # prior strength (mAh^2 per group: a few groups' worth)
FORGET = 0.995  # per calibration group
SCALE_MIN, SCALE_MAX = 0.2, 5.0
CHARGER_EFFICIENCY = 0.88  # assumed: USB input to the phone's own draw


# -- CPU ----------------------------------------------------------------------------


def cluster_rates(profile, tis, cpu_active=None):
    """mA per busy core-second for each cluster over an interval. `tis` is
    [{khz: seconds}] per cluster: the cpufreq time in state deltas."""
    act = profile.value("cpu.active") if cpu_active is None else cpu_active
    rates = []
    for c, states in enumerate(tis):
        total = sum(states.values())
        if total > 0:
            r = sum(s * profile.cpu_ma(c, khz) for khz, s in states.items()) / total
        else:
            pts = profile.cpu_points[min(c, len(profile.cpu_points) - 1)]
            r = pts[len(pts) // 2][1]
        rates.append(r + act)
    return rates


def split_clusters(rows, busy, iterations=40, floor=0.02):
    """Spread each row's CPU seconds over the clusters.

    rows: [(total seconds, prior [weight per cluster])], busy: measured busy
    seconds per cluster. Each row keeps its total exactly; the column sums
    follow `busy` (scaled to the rows' sum) as far as the priors allow
    (iterative proportional fitting; `floor` keeps every cluster reachable)."""
    n = len(busy)
    out = []
    for total, prior in rows:
        p = [max(0.0, prior[c] if c < len(prior) else 0.0) for c in range(n)]
        s = sum(p)
        p = [v / s for v in p] if s > 0 else [1.0 / n] * n
        p = [(1 - floor) * v + floor / n for v in p]
        out.append([total * v for v in p])
    T = sum(t for t, _ in rows)
    B = sum(busy)
    if T <= 0 or B <= 0:
        return out
    target = [b * T / B for b in busy]
    for _ in range(iterations):
        for c in range(n):
            col = sum(r[c] for r in out)
            if col > 0:
                f = target[c] / col
                for r in out:
                    r[c] *= f
        for r, (total, _p) in zip(out, rows):
            s = sum(r)
            if s > 0:
                f = total / s
                for c in range(n):
                    r[c] *= f
    return out


# -- one interval ---------------------------------------------------------------------


def add_part(entries, key, group, mah, extra=None):
    e = entries.setdefault(key, {"parts": {}, "extra": {}})
    if mah:
        e["parts"][group] = e["parts"].get(group, 0.0) + mah
    for k, v in (extra or {}).items():
        e["extra"][k] = e["extra"].get(k, 0.0) + v
    return e


def interval_model(iv, profile):
    """The uncalibrated estimate of one interval: {key: {"parts": {group:
    mAh}, "extra": {name: number}}}. `iv` holds the interval's measurements
    (collector.py builds it; tests build their own)."""
    h = 1 / 3600.0
    entries = {}

    # CPU and GPU, per app
    rates = cluster_rates(profile, iv.get("tis") or [{} for _ in profile.clusters])
    procs = iv.get("procs") or {}
    for key, p in procs.items():
        cpu_s = p.get("cpu_s") or []
        mah = sum(s * rates[min(c, len(rates) - 1)] for c, s in enumerate(cpu_s)) * h
        extra = {"cpu%d_s" % c: s for c, s in enumerate(cpu_s) if s}
        if p.get("fg_s"):
            extra["fg_s"] = p["fg_s"]
        if p.get("bg_s"):
            extra["bg_s"] = p["bg_s"]
        add_part(entries, key, "cpu", mah, extra)
        if p.get("gpu_ns"):
            gs = p["gpu_ns"] / 1e9
            hz = (p.get("gpu_cycles", 0) / gs) if gs > 0 and p.get("gpu_cycles") else 0.6 * 840e6
            add_part(entries, key, "gpu", gs * profile.gpu_ma(hz) * h, {"gpu_s": gs})
    irq = iv.get("irq_s") or []
    if any(irq):
        add_part(
            entries,
            "system:kernel",
            "cpu",
            sum(s * rates[min(c, len(rates) - 1)] for c, s in enumerate(irq)) * h,
            {"irq_s": sum(irq)},
        )

    # the screen
    on, full = profile.screen()
    lit = iv.get("lit_s") or []
    bright = iv.get("bright_s") or []
    scr = sum(lit) * on / 2 * h + sum(bright) * full / 2 * h
    if scr:
        add_part(
            entries,
            "comp:screen",
            "screen",
            scr,
            {"panel%d_s" % i: s for i, s in enumerate(lit) if s},
        )

    # idle (awake, panels off) and standby (suspended)
    if iv.get("off_awake_s"):
        add_part(
            entries,
            "comp:idle",
            "idle",
            iv["off_awake_s"] * IDLE_PRIOR_MA * h,
            {"time_s": iv["off_awake_s"]},
        )
    if iv.get("suspended_s"):
        add_part(
            entries,
            "comp:standby",
            "standby",
            iv["suspended_s"] * STANDBY_PRIOR_MA * h,
            {"time_s": iv["suspended_s"]},
        )

    # Wi-Fi
    w = iv.get("wifi")
    if w and w.get("up_s"):
        rx_s = w.get("rx_bytes", 0) * 8 / WIFI_RATE_BPS + w.get("rx_pkts", 0) * WIFI_PKT_S
        tx_s = w.get("tx_bytes", 0) * 8 / WIFI_RATE_BPS + w.get("tx_pkts", 0) * WIFI_PKT_S
        mah = (
            w["up_s"] * profile.value("wifi.controller.idle")
            + rx_s * profile.value("wifi.controller.rx")
            + tx_s * profile.value("wifi.controller.tx")
        ) * h
        add_part(
            entries,
            "comp:wifi",
            "radio",
            mah,
            {"up_s": w["up_s"], "rx_bytes": w.get("rx_bytes", 0), "tx_bytes": w.get("tx_bytes", 0)},
        )

    # the modem
    m = iv.get("modem")
    if m and (m.get("up_s") or m.get("awake_s")):
        nbytes = m.get("rx_bytes", 0) + m.get("tx_bytes", 0)
        active = min(
            iv.get("awake_s", 0) or MODEM_TAIL_S,
            nbytes * 8 / MODEM_RATE_BPS + (MODEM_TAIL_S if nbytes else 0),
        )
        mah = (
            m.get("up_s", 0) * profile.radio_on()
            + m.get("awake_s", 0) * profile.value("modem.controller.idle")
            + active
            * profile.value(
                "radio.active" if profile.has("radio.active") else "modem.controller.rx"
            )
        ) * h
        add_part(
            entries,
            "comp:modem",
            "radio",
            mah,
            {
                "up_s": m.get("up_s", 0),
                "awake_s": m.get("awake_s", 0),
                "rx_bytes": m.get("rx_bytes", 0),
                "tx_bytes": m.get("tx_bytes", 0),
            },
        )

    # Bluetooth
    b = iv.get("bluetooth")
    if b and b.get("on_s"):
        bon, bact = profile.bluetooth()
        active = b.get("bytes", 0) * 8 / BT_RATE_BPS + b.get("pkts", 0) * BT_PKT_S
        add_part(
            entries,
            "comp:bluetooth",
            "radio",
            (b["on_s"] * bon + active * bact) * h,
            {"on_s": b["on_s"], "bytes": b.get("bytes", 0)},
        )

    # the DSPs
    for name, awake in (iv.get("dsp_awake_s") or {}).items():
        if awake:
            add_part(
                entries, "comp:dsp", "dsp", awake * profile.dsp_ma(name) * h, {name + "_s": awake}
            )
    return entries


def features(entries):
    """The calibration's feature vector: estimated mAh per group."""
    x = dict.fromkeys(GROUPS, 0.0)
    for e in entries.values():
        for g, v in e["parts"].items():
            x[g] += v
    return [x[g] for g in GROUPS]


# -- calibration ---------------------------------------------------------------------


def solve(a, b):
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


class Calibration:
    """A scale per group, learned from the discharge groups by ridge
    regression towards 1 (the table's / the assumed figures), with old
    groups slowly forgotten. Sufficient statistics only: no history."""

    def __init__(self, state=None):
        n = len(GROUPS)
        state = state or {}
        self.xtx = state.get("xtx") or [[0.0] * n for _ in range(n)]
        self.xty = state.get("xty") or [0.0] * n
        self.groups = state.get("groups", 0)
        self._beta = None

    def update(self, x, y):
        n = len(GROUPS)
        for i in range(n):
            self.xty[i] = self.xty[i] * FORGET + x[i] * y
            for j in range(n):
                self.xtx[i][j] = self.xtx[i][j] * FORGET + x[i] * x[j]
        self.groups += 1
        self._beta = None

    def coefficients(self):
        if self._beta is not None:
            return self._beta
        n = len(GROUPS)
        a = [[self.xtx[i][j] + (RIDGE if i == j else 0.0) for j in range(n)] for i in range(n)]
        b = [self.xty[i] + RIDGE * 1.0 for i in range(n)]
        beta = solve(a, b) or [1.0] * n
        self._beta = [min(SCALE_MAX, max(SCALE_MIN, v)) if math.isfinite(v) else 1.0 for v in beta]
        return self._beta

    def scales(self):
        return dict(zip(GROUPS, self.coefficients()))

    def state(self):
        return {"xtx": self.xtx, "xty": self.xty, "groups": self.groups}


def allocate(intervals, measured, calibration, learn=True):
    """Calibrate one group of intervals.

    intervals: [entries] (interval_model's output, one per interval);
    measured: the group's measured drain in mAh, or None (no measurement:
    while charging). Returns ([{key: mAh}] per interval, summary). With a
    measurement the allocations add up to it exactly."""
    beta = calibration.scales()
    adjusted = []
    for entries in intervals:
        adjusted.append(
            {k: sum(beta[g] * v for g, v in e["parts"].items()) for k, e in entries.items()}
        )
    prior_total = sum(sum(features(entries)) for entries in intervals)
    model_total = sum(sum(a.values()) for a in adjusted)
    scale = 1.0
    if measured is not None:
        m = max(0.0, measured)
        scale = m / model_total if model_total > 0 else 0.0
        if learn and model_total > 0 and m > 0:
            x = [0.0] * len(GROUPS)
            for entries in intervals:
                for i, v in enumerate(features(entries)):
                    x[i] += v
            calibration.update(x, m)
    out = [{k: v * scale for k, v in a.items()} for a in adjusted]
    return out, {"measured": measured, "prior": prior_total, "model": model_total, "scale": scale}


def measured_drain(kind, a, b, dt_s, frozen_est=0.0):
    """The phone's own draw over an interval in mAh (None: not measurable)
    and the new frozen-counter carry.

    battery: the charge counter's fall (1 mAh steps, updated every ~10 s); a
             counter that does not move while the current says the phone
             draws (it stops at 100 %) is bridged with the current, and that
             bridge is taken back from the counter's first move after it.
    charger: on the charger, the battery not charging (full, or the charger
             too weak): the charger's input power x an assumed efficiency,
             less what still goes into the battery (or plus what it gives).
    """
    h = dt_s / 3600.0
    if kind == "battery":
        cur = -((a.get("current_ua") or 0) + (b.get("current_ua") or 0)) / 2000.0  # mA drawn
        by_current = max(0.0, cur * h)
        if a.get("charge_uah") is None or b.get("charge_uah") is None:
            return by_current, frozen_est
        dcc = (a["charge_uah"] - b["charge_uah"]) / 1000.0
        if dcc <= 0 and by_current >= 2.0:
            return by_current, frozen_est + by_current
        if dcc > 0 and frozen_est:
            return max(0.0, dcc - frozen_est), 0.0
        return max(0.0, dcc), 0.0
    if kind == "charger":
        v = ((a.get("voltage_uv") or 0) + (b.get("voltage_uv") or 0)) / 2e6
        if v <= 0:
            return None, 0.0
        usb_mw = (
            (a.get("usb_uv") or 0) * (a.get("usb_ua") or 0)
            + (b.get("usb_uv") or 0) * (b.get("usb_ua") or 0)
        ) / 2e9
        into_bat = ((a.get("current_ua") or 0) + (b.get("current_ua") or 0)) / 2000.0
        return max(0.0, (CHARGER_EFFICIENCY * usb_mw / v - into_bat) * h), 0.0
    return None, 0.0


def interval_kind(a, b):
    """battery | charger | charging | mixed, from the samples at both ends:
    charger = plugged in with the battery not charging (full, or a weak
    charger), charging = the battery takes more than 100 mA."""

    def one(s):
        if not s.get("usb_online"):
            return "battery"
        if (s.get("current_ua") or 0) <= 100000:
            return "charger"
        return "charging"

    if a.get("level") is None or b.get("level") is None:
        return "mixed"  # the battery manager not answering (early boot)
    ka, kb = one(a), one(b)
    return ka if ka == kb else "mixed"
