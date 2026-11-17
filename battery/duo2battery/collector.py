"""The collector: duo2-battery-stats.service (root, capability-bounded: see
the unit). A sample a minute, a cheap look at the screen every 10 s, the
store written every 10 minutes (of CLOCK_BOOTTIME) and right before every
suspend.

No timer of ours wakes the phone: every wait is a select() timeout, which
runs on CLOCK_MONOTONIC and stands still while the phone is suspended. The
time a sample interval spent suspended is CLOCK_BOOTTIME - CLOCK_MONOTONIC
(the standby), and the systemd sleep hook asks for a sample right before a
suspend (and the store written) and right after it, so that interval is
clean, and a phone that never comes back from a suspend (an empty battery,
a hang) has lost nothing but that suspend. The store's deadline is on
CLOCK_BOOTTIME: a phone that sleeps most of the time is awake a few minutes
an hour, and a monotonic 10 minutes came hours late (2026-11-09: a night
unstored).

Signals: SIGUSR1 = sample, store and write the report now (Duo Settings,
through duo-settings-helper battery refresh); SIGUSR2 = clear the usage
history (the same helper); SIGHUP = sample now (the sleep hook, after a
suspend); SIGRTMIN+1 = sample and store now (the sleep hook, before a
suspend); SIGTERM = sample, store, exit.
"""

import json
import os
import resource
import select
import signal
import sys
import time

from . import classify, model, report, sources, store
from .profile import Profile

POLL_S = 10
SAMPLE_S = 60
FLUSH_S = 600
GROUP_MAH = 10.0
GROUP_MAX_S = 1800
RUN_DIR = "/run/duo2-battery"
REPORT = RUN_DIR + "/report.json"
SAMPLED = RUN_DIR + "/sampled"
AIRPLANE = "/var/lib/duo2/airplane"
BROKERS = {"seatd", "systemd-logind"}
TICK = float(sources.CLK_TCK)
STORE_SIG = signal.SIGRTMIN + 1


def log(*a):
    print("duo2-battery-stats:", *a, file=sys.stderr, flush=True)


def clocks():
    return (
        time.time(),
        time.clock_gettime(time.CLOCK_BOOTTIME),
        time.clock_gettime(time.CLOCK_MONOTONIC),
    )


def mono_boot():
    return time.clock_gettime(time.CLOCK_MONOTONIC), time.clock_gettime(time.CLOCK_BOOTTIME)


class Timers:
    """When the 10 s look, the sample and the store are due: the look and
    the sample on CLOCK_MONOTONIC (they need not run while the phone
    sleeps: the sleep hook samples around every suspend), the store on
    CLOCK_BOOTTIME (the time asleep counts)."""

    def __init__(self, mono, boot):
        self.sampled(mono)
        self.stored(boot)

    def sampled(self, mono):
        self.poll, self.sample = mono + POLL_S, mono + SAMPLE_S

    def polled(self, mono):
        self.poll = mono + POLL_S

    def stored(self, boot):
        self.flush = boot + FLUSH_S

    def timeout(self, mono, boot):
        return max(0.0, min(self.poll - mono, self.sample - mono, self.flush - boot))

    def due(self, mono, boot):
        """("sample" | "poll" | None, whether the store is due)."""
        step = "sample" if mono >= self.sample else "poll" if mono >= self.poll else None
        return step, boot >= self.flush


class Proc:
    __slots__ = ("start", "key", "total", "child", "threads", "ppid", "comm", "read_at")

    def __init__(self, start, key, total, child, ppid, comm):
        self.start, self.key, self.total, self.child, self.ppid, self.comm = (
            start,
            key,
            total,
            child,
            ppid,
            comm,
        )
        self.threads = {}
        self.read_at = None


class Collector:
    def __init__(self, db_path=store.DB_PATH, report_path=REPORT):
        self.db_path, self.report_path = db_path, report_path
        self.policies = sources.cpufreq_policies()
        clusters = [cpus for _d, cpus in self.policies] or [[0, 1, 2, 3], [4, 5, 6], [7]]
        self.ncl = len(clusters)
        self.cluster_of = {cpu: i for i, cpus in enumerate(clusters) for cpu in cpus}
        self.profile = Profile.load(clusters=clusters)
        store.ensure_dir(db_path)
        self.db = store.connect(db_path)
        self.calibration = model.Calibration(store.get_meta(self.db, "calibration"))
        self.ticks = sources.TickClock()
        self.procs = {}
        self.dead_child = {}
        self.drm = {}  # client id -> (pid, fd)
        self.drm_prev = {}  # client id -> (ns, cycles)
        self.drm_retry = {}  # client id -> when to look for its holder again
        self.drm_known = set()  # the client ids of the last scan
        self.prev = None
        self.frozen = 0.0
        self.group = None
        self.pending = {"samples": [], "periods": [], "usage": {}, "groups": []}
        self.screen = sources.read_screen()
        self.visible = set()
        self.visible_aliases = set()
        self.alias_cache = {}
        self.screen_since = None
        self.wall0, self.boot0, self.mono0 = clocks()
        self.poll_boot, self.poll_mono = self.boot0, self.mono0
        self.reset_acc()
        self.started_mono = self.mono0
        self.self_cpu0 = self.own_cpu()

    # -- the 10 s look ---------------------------------------------------------------

    def reset_acc(self):
        self.acc = {
            "lit_s": [0.0, 0.0],
            "bright_s": [0.0, 0.0],
            "off_awake_s": 0.0,
            "awake_s": 0.0,
            "suspended_s": 0.0,
            "fg": {},
            "shown": {},
        }

    def aliases(self, app_id):
        if app_id not in self.alias_cache:
            if len(self.alias_cache) > 256:
                self.alias_cache.clear()
            self.alias_cache[app_id] = classify.window_aliases(app_id)
        return self.alias_cache[app_id]

    def poll(self):
        wall, boot, mono = clocks()
        dboot, dmono = boot - self.poll_boot, mono - self.poll_mono
        susp = dboot - dmono
        if susp < 0.5:  # the two clocks read a moment apart
            susp = 0.0
        awake = max(0.0, dboot - susp)
        self.poll_boot, self.poll_mono = boot, mono
        a = self.acc
        lit_any = False
        for i, (lit, b) in enumerate(self.screen[:2]):
            if lit:
                lit_any = True
                a["lit_s"][i] += awake
                a["bright_s"][i] += awake * b
        if not lit_any:
            a["off_awake_s"] += awake
        a["awake_s"] += awake
        a["suspended_s"] += susp
        for key in self.visible:
            a["fg"][key] = a["fg"].get(key, 0.0) + awake
        for key in self.visible_aliases:
            a["shown"][key] = a["shown"].get(key, 0.0) + awake
        if susp > 5:
            self.pending["periods"].append((wall - susp, wall, "suspend"))
        # the state from now on
        self.screen = sources.read_screen()
        lit_now = any(lit for lit, _b in self.screen)
        if lit_now:
            ids = sources.visible_apps(sources.wc_state())
            self.visible = {k for k in (classify.window_key(x) for x in ids) if k}
            self.visible_aliases = set().union(*(self.aliases(x) for x in ids)) if ids else set()
            if self.screen_since is None:
                self.screen_since = wall
        else:
            self.visible = set()
            self.visible_aliases = set()
            if self.screen_since is not None:
                self.pending["periods"].append((self.screen_since, wall, "screen"))
                self.screen_since = None

    # -- processes -----------------------------------------------------------------------

    def scan_procs(self, prev_boot):
        """-> {key: {"cpu_s": [per cluster], "gpu_ns", "gpu_cycles"}} for the
        interval since the last scan; the first scan only sets the baselines."""
        first = prev_boot is None
        born_after = (prev_boot or 0) * TICK
        rows, row_keys = [], []
        seen = set()
        try:
            pids = [int(n) for n in os.listdir("/proc") if n.isdigit()]
        except OSError:
            pids = []
        for pid in pids:
            st = sources.read_pid_stat("/proc/%d/stat" % pid)
            if st is None:
                continue
            seen.add(pid)
            total = st["utime"] + st["stime"]
            child = st["cutime"] + st["cstime"]
            p = self.procs.get(pid)
            if p is None or p.start != st["start"]:
                info = classify.read_info(pid, st)
                parent = self.procs.get(st["ppid"])
                key = (
                    classify.classify(info, parent.key if parent else None)
                    if info
                    else "system:services"
                )
                new = not first and st["start"] >= born_after
                p = Proc(
                    st["start"],
                    key,
                    0 if new else total,
                    0 if new else child,
                    st["ppid"],
                    st["comm"],
                )
                self.procs[pid] = p
            d = total - p.total
            dchild = max(0, child - p.child - self.dead_child.pop(pid, 0))
            p.total, p.child, p.ppid = total, child, st["ppid"]
            if first:
                continue
            if d > 0:
                rows.append((d / TICK, self.thread_prior(pid, p, st)))
                row_keys.append(p.key)
            if dchild > 0:
                rows.append((dchild / TICK, [1.0] * self.ncl))
                row_keys.append(p.key)
        for pid in list(self.procs):
            if pid not in seen:
                p = self.procs.pop(pid)
                self.dead_child[p.ppid] = self.dead_child.get(p.ppid, 0) + p.total + p.child
        for pid in list(self.dead_child):
            if pid not in self.procs:
                del self.dead_child[pid]
        out = {}
        if not first:
            busy = self.cluster_busy
            split = model.split_clusters(rows, busy)
            for key, secs in zip(row_keys, split):
                e = out.setdefault(key, {"cpu_s": [0.0] * self.ncl})
                for c, s in enumerate(secs):
                    e["cpu_s"][c] += s
        self.scan_gpu(out, first)
        return out

    def thread_prior(self, pid, p, st):
        """Seconds per cluster of the threads that ran (each at the cluster it
        last ran on): the prior for the cluster split."""
        prior = [0.0] * self.ncl
        if st["threads"] <= 1 or st["flags"] & classify.PF_KTHREAD:
            prior[self.cluster_of.get(st["processor"], 0)] = 1.0
            return prior
        base = "/proc/%d/task" % pid
        try:
            tids = os.listdir(base)
        except OSError:
            tids = []
        threads = {}
        for t in tids:
            ts = sources.read_pid_stat("%s/%s/stat" % (base, t))
            if ts is None:
                continue
            tot = ts["utime"] + ts["stime"]
            tid = int(t)
            threads[tid] = tot
            if tid in p.threads:
                dt = tot - p.threads[tid]
            elif p.read_at is not None and ts["start"] >= p.read_at:
                dt = tot
            else:
                dt = 0
            if dt > 0:
                prior[self.cluster_of.get(ts["processor"], 0)] += dt
        p.threads = threads
        p.read_at = time.clock_gettime(time.CLOCK_BOOTTIME) * TICK
        if not any(prior):
            prior[self.cluster_of.get(st["processor"], 0)] = 1.0
        return prior

    # -- the GPU ---------------------------------------------------------------------------

    def find_drm_holders(self, wanted, openers):
        found = {}
        candidates = list(dict.fromkeys(openers))
        for _round in range(2):
            for pid in candidates:
                for fd, cid in sources.drm_fds(pid):
                    if cid in wanted:
                        found.setdefault(cid, []).append((pid, fd))
            if all(
                c in found
                and any(
                    self.procs.get(h[0]) and self.procs[h[0]].comm not in BROKERS for h in found[c]
                )
                for c in wanted
            ):
                break
            # passed on (seatd opens the card for duo-wc): every user process
            candidates = [
                pid
                for pid, pr in self.procs.items()
                if pid not in candidates and not pr.key == "system:kernel"
            ]
        out = {}
        for cid, holders in found.items():
            good = [
                h for h in holders if self.procs.get(h[0]) and self.procs[h[0]].comm not in BROKERS
            ]
            out[cid] = min(good or holders)
        return out

    def scan_gpu(self, out, first):
        clients = sources.read_drm_clients()
        for cid in list(self.drm):
            if cid not in clients:
                del self.drm[cid]
                self.drm_prev.pop(cid, None)
        mono = time.monotonic()
        for cid in list(self.drm_retry):
            if cid not in clients:
                del self.drm_retry[cid]
        wanted = {
            cid for cid in clients if cid not in self.drm and self.drm_retry.get(cid, 0) <= mono
        }
        if wanted:
            self.drm.update(self.find_drm_holders(wanted, [clients[c] for c in wanted]))
            for cid in wanted - set(self.drm):
                self.drm_retry[cid] = mono + 600  # a holder we cannot see: not every minute
        for cid, (pid, fd) in list(self.drm.items()):
            info = sources.parse_drm_fdinfo(sources.read("/proc/%d/fdinfo/%d" % (pid, fd), 4096))
            if not info or info[0] != cid:
                del self.drm[cid]  # closed or reused: found again next time
                continue
            _cid, ns, cycles = info
            if cid not in self.drm_prev:
                # a client that appeared since the last scan: all its time is
                # this interval's; one we only found now: from here on
                self.drm_prev[cid] = (
                    (0, 0) if not first and cid not in self.drm_known else (ns, cycles)
                )
            ns0, cy0 = self.drm_prev[cid]
            self.drm_prev[cid] = (ns, cycles)
            p = self.procs.get(pid)
            if first or not p or ns <= ns0:
                continue
            e = out.setdefault(p.key, {"cpu_s": [0.0] * self.ncl})
            e["gpu_ns"] = e.get("gpu_ns", 0) + (ns - ns0)
            e["gpu_cycles"] = e.get("gpu_cycles", 0) + max(0, cycles - cy0)
        self.drm_known = set(clients)

    # -- one sample ------------------------------------------------------------------------

    def snapshot(self):
        wall, boot, _mono = clocks()
        stat = sources.parse_proc_stat(sources.read("/proc/stat") or "")
        qcom = sources.read_qcom_stats()
        self.ticks.observe(boot, qcom)
        return {
            "wall": wall,
            "boot": boot,
            "bat": sources.read_battery(),
            "stat": stat,
            "tis": [
                sources.parse_time_in_state(sources.read(d + "/stats/time_in_state"))
                for d, _c in self.policies
            ],
            "qcom": qcom,
            "qnow": self.ticks.now(boot),
            "rproc": sources.remoteproc_states(),
            "wifi": sources.read_net(sources.WIFI_IF),
            "mnet": sources.read_net(sources.MODEM_IF),
            "bt": sources.read_bluetooth(),
            "airplane": (sources.read(AIRPLANE, 16) or "").strip() == "on",
        }

    def interval(self, a, b, procs):
        dt = max(0.0, b["boot"] - a["boot"])
        acc = self.acc
        busy = [0.0] * self.ncl
        irq = [0.0] * self.ncl
        for cpu, (task, irqt) in b["stat"].items():
            if cpu in a["stat"]:
                c = self.cluster_of.get(cpu, 0)
                busy[c] += max(0, task - a["stat"][cpu][0]) / TICK
                irq[c] += max(0, irqt - a["stat"][cpu][1]) / TICK
        tis = []
        for ta, tb in zip(a["tis"], b["tis"]):
            tis.append({k: max(0.0, v - ta.get(k, 0.0)) for k, v in tb.items()})
        dsp = {}
        modem_awake = 0.0
        for name in sources.SUBSYSTEMS:
            sa, sb = a["qcom"].get(name), b["qcom"].get(name)
            if not (sa and sb and a["rproc"].get(name) and b["rproc"].get(name)):
                continue
            slept = (
                sources.sleep_ticks(sb, b["qnow"]) - sources.sleep_ticks(sa, a["qnow"])
            ) / sources.QCOM_TICK_HZ
            awake = min(dt, max(0.0, dt - slept))
            if name == "modem":
                modem_awake = awake
            else:
                dsp[name] = awake
        for key, p in procs.items():
            if key in acc["fg"]:
                p["fg_s"] = acc["fg"][key]
            if key.startswith(("app:", "exe:", "android:")):
                # active in the interval, minus the time a window of it was shown
                p["bg_s"] = max(0.0, acc["awake_s"] - acc["shown"].get(key, 0.0))
        for key, fg in acc["fg"].items():
            procs.setdefault(key, {"cpu_s": [0.0] * self.ncl, "fg_s": fg})

        def net(x, y):
            if not x or not y:
                return None
            return {k: max(0, y[k] - x[k]) for k in ("rx_bytes", "tx_bytes", "rx_pkts", "tx_pkts")}

        wifi = net(a["wifi"], b["wifi"])
        if wifi is not None:
            wifi["up_s"] = dt if (a["wifi"]["up"] and b["wifi"]["up"]) else 0.0
        mnet = net(a["mnet"], b["mnet"]) or {}
        modem_up = a["rproc"].get("modem") and b["rproc"].get("modem") and not b["airplane"]
        modem = dict(mnet, up_s=dt if modem_up else 0.0, awake_s=modem_awake)
        bt = None
        if a["bt"] and b["bt"]:
            bt = {
                "on_s": dt if (a["bt"]["up"] and b["bt"]["up"]) else 0.0,
                "bytes": max(0, b["bt"]["bytes"] - a["bt"]["bytes"]),
                "pkts": max(0, b["bt"]["pkts"] - a["bt"]["pkts"]),
            }
        return {
            "dt": dt,
            "awake_s": acc["awake_s"],
            "suspended_s": acc["suspended_s"],
            "off_awake_s": acc["off_awake_s"],
            "lit_s": acc["lit_s"],
            "bright_s": acc["bright_s"],
            "tis": tis,
            "irq_s": irq,
            "busy": busy,
            "procs": procs,
            "dsp_awake_s": dsp,
            "wifi": wifi,
            "modem": modem,
            "bluetooth": bt,
        }

    def sample(self, force_close=False):
        self.poll()
        snap = self.snapshot()
        a = self.prev
        # cluster busy time is needed by scan_procs' split
        self.cluster_busy = [0.0] * self.ncl
        if a:
            for cpu, (task, _i) in snap["stat"].items():
                if cpu in a["stat"]:
                    self.cluster_busy[self.cluster_of.get(cpu, 0)] += (
                        max(0, task - a["stat"][cpu][0]) / TICK
                    )
        procs = self.scan_procs(a["boot"] if a else None)
        bat = snap["bat"]
        lit_mask = sum(1 << i for i, (lit, _b) in enumerate(self.screen) if lit)
        cur, volt = bat.get("current_ua"), bat.get("voltage_uv")
        usb_mw = (
            ((bat.get("usb_uv") or 0) * (bat.get("usb_ua") or 0) / 1e9)
            if bat.get("usb_online")
            else 0.0
        )
        if bat.get("level") is not None:
            self.pending["samples"].append(
                (
                    round(snap["wall"], 1),
                    bat.get("level"),
                    bat.get("status"),
                    int(bool(bat.get("usb_online"))),
                    cur / 1000.0 if cur is not None else None,
                    volt / 1e6 if volt is not None else None,
                    bat["charge_uah"] / 1000.0 if bat.get("charge_uah") is not None else None,
                    usb_mw,
                    lit_mask,
                )
            )
        if a is not None and snap["boot"] > a["boot"]:
            iv = self.interval(a, snap, procs)
            entries = model.interval_model(iv, self.profile)
            kind = model.interval_kind(a["bat"], bat)
            measured, self.frozen = model.measured_drain(kind, a["bat"], bat, iv["dt"], self.frozen)
            self.add(kind, measured, entries, a["wall"], snap["wall"])
        self.prev = snap
        self.reset_acc()
        if force_close:
            self.close_group()

    # -- calibration groups and the store ----------------------------------------------------

    def add(self, kind, measured, entries, t0, t1):
        cls = kind if kind in ("battery", "charger") else "charging"
        g = self.group
        if g and g["kind"] != cls:
            self.close_group()
            g = None
        if g is None:
            g = self.group = {
                "kind": cls,
                "t0": t0,
                "t1": t1,
                "measured": 0.0 if measured is not None else None,
                "intervals": [],
            }
        g["t1"] = t1
        if measured is None:
            g["measured"] = None
        elif g["measured"] is not None:
            g["measured"] += measured
        g["intervals"].append((t0, t1, entries))
        if ((g["measured"] or 0) >= GROUP_MAH) or (t1 - g["t0"] >= GROUP_MAX_S):
            self.close_group()

    def close_group(self):
        g, self.group = self.group, None
        if not g or not g["intervals"]:
            return
        alloc, summary = model.allocate(
            [e for _a, _b, e in g["intervals"]],
            g["measured"],
            self.calibration,
            learn=(g["kind"] == "battery"),
        )
        cls = "battery" if g["kind"] == "battery" else "charger"
        beta = self.calibration.scales()
        for (t0, t1, entries), shares in zip(g["intervals"], alloc):
            bucket = store.bucket_of((t0 + t1) / 2)
            for key, e in entries.items():
                u = self.pending["usage"].setdefault(
                    (bucket, cls, key), {"mah": 0.0, "cpu_mah": 0.0, "gpu_mah": 0.0, "extra": {}}
                )
                mah = shares.get(key, 0.0)
                u["mah"] += mah
                parts = e["parts"]
                adj = sum(beta[k] * v for k, v in parts.items())
                if adj > 0:
                    u["cpu_mah"] += mah * beta["cpu"] * parts.get("cpu", 0.0) / adj
                    u["gpu_mah"] += mah * beta["gpu"] * parts.get("gpu", 0.0) / adj
                u["extra"] = store.merge_extra(u["extra"], e["extra"])
        self.pending["groups"].append(
            (
                g["t0"],
                g["t1"],
                cls if g["kind"] != "charging" else "charging",
                summary["measured"],
                summary["prior"],
                summary["model"],
                summary["scale"],
            )
        )

    def flush(self):
        p = self.pending
        if self.screen_since is not None:
            # a lit stretch is stored in pieces, so a stop (or a crash) keeps it
            now = time.time()
            p["periods"].append((self.screen_since, now, "screen"))
            self.screen_since = now
        store.write(self.db, p["samples"], p["periods"], p["usage"], p["groups"])
        store.set_meta(self.db, "calibration", self.calibration.state())
        self.db.commit()
        store.prune(self.db)
        self.pending = {"samples": [], "periods": [], "usage": {}, "groups": []}

    def own_cpu(self):
        r = resource.getrusage(resource.RUSAGE_SELF)
        return r.ru_utime + r.ru_stime

    def collector_info(self):
        mono = time.clock_gettime(time.CLOCK_MONOTONIC)
        cpu = self.own_cpu() - self.self_cpu0
        up = max(1.0, mono - self.started_mono)
        return {
            "cpu_s": round(cpu, 2),
            "awake_s": round(up),
            "cpu_pct_of_core": round(100.0 * cpu / up, 3),
            "rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }

    def write_report(self):
        open_periods = [(self.screen_since, time.time(), "screen")] if self.screen_since else []
        rep = report.build(
            self.db,
            open_periods=open_periods,
            collector=self.collector_info(),
            profile=self.profile,
            calibration=self.calibration,
            live=self.prev["bat"] if self.prev else None,
        )
        tmp = self.report_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rep, f, separators=(",", ":"))
        os.replace(tmp, self.report_path)

    def clear(self):
        self.group = None
        self.pending = {"samples": [], "periods": [], "usage": {}, "groups": []}
        self.screen_since = None
        store.clear(self.db)
        self.reset_acc()
        self.poll_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        self.poll_mono = time.clock_gettime(time.CLOCK_MONOTONIC)
        self.prev = None  # the next sample is a fresh baseline
        self.scan_procs(None)

    def touch(self, path):
        try:
            with open(path, "w") as f:
                f.write("%.3f\n" % time.time())
        except OSError:
            pass


def guarded(fn, *a, **kw):
    """Run one step; an unexpected error is logged (at most once a minute)
    and the collector goes on with the next one."""
    try:
        return fn(*a, **kw)
    except Exception:  # noqa: BLE001 - a daemon must not die of one bad read
        now = time.monotonic()
        if now - guarded.last >= 60:
            guarded.last = now
            import traceback

            log("error (going on):", traceback.format_exc().strip().splitlines()[-1])
            traceback.print_exc(file=sys.stderr)
        return None


guarded.last = -1e9


def run():
    os.umask(0o027)
    c = Collector()
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.set_blocking(r, False)
    signal.set_wakeup_fd(w)
    flags = set()
    for sig in (
        signal.SIGUSR1,
        signal.SIGUSR2,
        signal.SIGHUP,
        STORE_SIG,
        signal.SIGTERM,
        signal.SIGINT,
    ):
        signal.signal(sig, lambda s, _f: flags.add(s))
    c.sample()  # the baselines
    t = Timers(*mono_boot())
    log(
        "running: %d clusters, power figures %s"
        % (c.ncl, "from power_profile.xml" if c.profile.table else "assumed (no power_profile.xml)")
    )
    while True:
        try:
            select.select([r], [], [], t.timeout(*mono_boot()))
        except InterruptedError:
            pass
        try:
            while os.read(r, 64):
                pass
        except BlockingIOError:
            pass
        if signal.SIGTERM in flags or signal.SIGINT in flags:
            guarded(c.sample, force_close=True)
            guarded(c.flush)
            log("stopped")
            return 0
        if signal.SIGUSR2 in flags:
            flags.discard(signal.SIGUSR2)
            guarded(c.clear)
            guarded(c.write_report)
            log("usage history cleared")
        if signal.SIGUSR1 in flags:
            flags.discard(signal.SIGUSR1)
            guarded(c.sample, force_close=True)
            guarded(c.flush)
            guarded(c.write_report)
            mono, boot = mono_boot()
            t.sampled(mono)
            t.stored(boot)
            continue
        if STORE_SIG in flags:
            # a suspend comes: what is in memory goes to disk first
            flags.discard(STORE_SIG)
            flags.discard(signal.SIGHUP)
            guarded(c.sample)
            guarded(c.flush)
            c.touch(SAMPLED)
            mono, boot = mono_boot()
            t.sampled(mono)
            t.stored(boot)
            continue
        if signal.SIGHUP in flags:
            flags.discard(signal.SIGHUP)
            guarded(c.sample)
            c.touch(SAMPLED)
            t.sampled(mono_boot()[0])
            continue  # a store due after a long sleep comes next round
        mono, boot = mono_boot()
        step, store_due = t.due(mono, boot)
        if step == "sample":
            guarded(c.sample)
            t.sampled(mono)
        elif step == "poll":
            guarded(c.poll)
            t.polled(mono)
        if store_due:
            if c.group and c.group["kind"] != "battery":
                guarded(c.close_group)
            guarded(c.flush)
            t.stored(boot)


def main(argv):
    if argv[1:] == ["run"]:
        return run()
    if argv[1:2] == ["report"]:
        db = store.connect(argv[2] if len(argv) > 2 else store.DB_PATH, readonly=True)
        print(json.dumps(report.build(db, profile=Profile.load()), indent=1))
        return 0
    print("usage: duo2-battery-stats run | report [DB]", file=sys.stderr)
    return 2
