"""duo2-battery-stats' pure parts: python3 -m unittest discover -s tests
(from battery/)."""

import os
import random
import sqlite3
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))

from duo2battery import classify, model, report, sources, store  # noqa: E402
from duo2battery.profile import Profile, interpolate, parse_power_profile  # noqa: E402

# A made-up table in power_profile.xml's shape (not the phone's figures).
TABLE = """<?xml version="1.0" encoding="utf-8"?>
<device name="Android">
  <item name="screen.on">100</item>
  <item name="screen.full">300</item>
  <array name="cpu.clusters.cores"><value>4</value><value>3</value><value>1</value></array>
  <array name="cpu.core_speeds.cluster0"><value>300000</value><value>1800000</value></array>
  <array name="cpu.core_power.cluster0"><value>10</value><value>40</value></array>
  <array name="cpu.core_speeds.cluster1"><value>700000</value><value>2400000</value></array>
  <array name="cpu.core_power.cluster1"><value>30</value><value>300</value></array>
  <array name="cpu.core_speeds.cluster2"><value>800000</value><value>2800000</value></array>
  <array name="cpu.core_power.cluster2"><value>50</value><value>600</value></array>
  <item name="cpu.active">10</item>
  <item name="wifi.controller.idle">3</item>
  <item name="wifi.controller.rx">100</item>
  <item name="wifi.controller.tx">200</item>
  <array name="radio.on"><value>80</value><value>10</value></array>
  <item name="radio.active">150</item>
  <item name="modem.controller.idle">5</item>
  <item name="bluetooth.on">1</item>
  <item name="bluetooth.active">100</item>
  <item name="dsp.audio">20</item>
  <item name="battery.typical.capacity">4000</item>
</device>
"""


def profile():
    return Profile(parse_power_profile(TABLE), [], [[0, 1, 2, 3], [4, 5, 6], [7]])


class Profiles(unittest.TestCase):
    def test_table(self):
        p = profile()
        self.assertEqual(p.screen(), (100.0, 300.0))
        self.assertEqual(p.radio_on(), 10.0)
        self.assertEqual(p.capacity_mah(), 4000.0)
        self.assertEqual(p.cpu_ma(0, 300000), 10)
        self.assertAlmostEqual(p.cpu_ma(0, 1050000), 25)  # interpolated
        self.assertEqual(p.cpu_ma(2, 3000000), 600)  # clamped
        self.assertEqual(p.summary()["sources"]["cpu.cluster1"], "power_profile")

    def test_energy_model_fallback(self):
        em = [([0, 1, 2, 3], [(300000, 38500), (1800000, 154000)])]
        p = Profile({}, em, [[0, 1, 2, 3], [4, 5, 6], [7]])
        self.assertAlmostEqual(p.cpu_ma(0, 300000), 10.0)  # 38.5 mW / 3.85 V
        self.assertEqual(p.sources["cpu.cluster0"], "energy model")
        self.assertEqual(p.sources["cpu.cluster1"], "assumed")
        self.assertGreater(p.value("screen.on"), 0)
        self.assertEqual(p.sources["screen.on"], "assumed")

    def test_interpolate(self):
        self.assertIsNone(interpolate([], 1))
        self.assertEqual(interpolate([(0, 0), (10, 100)], 2.5), 25)


class Clusters(unittest.TestCase):
    def test_rates_weight_the_speeds(self):
        p = profile()
        rates = model.cluster_rates(p, [{300000: 3.0, 1800000: 1.0}, {}, {2800000: 5.0}])
        self.assertAlmostEqual(rates[0], (3 * 10 + 1 * 40) / 4.0 + 10)
        self.assertAlmostEqual(rates[2], 600 + 10)
        self.assertGreater(rates[1], 10)  # no stats: a middle speed

    def test_rows_keep_their_totals_and_columns_follow_busy(self):
        rows = [(10.0, [1, 0, 0]), (6.0, [0, 0, 1]), (4.0, [1, 1, 0])]
        busy = [12.0, 2.0, 6.0]
        out = model.split_clusters(rows, busy)
        for (total, _p), r in zip(rows, out):
            self.assertAlmostEqual(sum(r), total, places=6)
        cols = [sum(r[c] for r in out) for c in range(3)]
        for c in range(3):
            self.assertAlmostEqual(cols[c], busy[c], delta=0.05)
        self.assertGreater(out[1][2], 5.5)  # the big-core thread stays big

    def test_wrong_priors_are_pulled_towards_the_measured_clusters(self):
        # two processes both last seen on the big core, but the big core was
        # busy only 1 s: most of their time ran on the small cores
        out = model.split_clusters([(5.0, [0, 0, 1]), (5.0, [0, 0, 1])], [9.0, 0.0, 1.0])
        self.assertAlmostEqual(sum(r[2] for r in out), 1.0, delta=0.05)
        self.assertAlmostEqual(sum(r[0] for r in out), 9.0, delta=0.05)

    def test_nothing_measured(self):
        out = model.split_clusters([(2.0, [0, 1, 0])], [0, 0, 0])
        self.assertAlmostEqual(sum(out[0]), 2.0)
        self.assertEqual(model.split_clusters([], [1, 2, 3]), [])


def interval(**kw):
    iv = {
        "dt": 60.0,
        "awake_s": 60.0,
        "suspended_s": 0.0,
        "off_awake_s": 0.0,
        "lit_s": [0.0, 0.0],
        "bright_s": [0.0, 0.0],
        "tis": [{300000: 60.0}, {700000: 60.0}, {800000: 60.0}],
        "irq_s": [0, 0, 0],
        "procs": {},
        "dsp_awake_s": {},
        "wifi": None,
        "modem": None,
        "bluetooth": None,
    }
    iv.update(kw)
    return iv


class IntervalModel(unittest.TestCase):
    def test_cpu_gpu_per_app(self):
        p = profile()
        e = model.interval_model(
            interval(
                procs={
                    "app:a": {
                        "cpu_s": [36.0, 0, 0],
                        "gpu_ns": 3.6e9,
                        "gpu_cycles": int(3.6 * 840e6),
                    }
                }
            ),
            p,
        )
        self.assertAlmostEqual(e["app:a"]["parts"]["cpu"], 36 * (10 + 10) / 3600.0)
        self.assertAlmostEqual(e["app:a"]["parts"]["gpu"], 3.6 * p.gpu_ma(840e6) / 3600.0)
        self.assertEqual(e["app:a"]["extra"]["cpu0_s"], 36.0)

    def test_screen_idle_standby(self):
        p = profile()
        e = model.interval_model(
            interval(lit_s=[36.0, 0.0], bright_s=[18.0, 0.0], off_awake_s=24.0, suspended_s=360.0),
            p,
        )
        self.assertAlmostEqual(e["comp:screen"]["parts"]["screen"], (36 * 50 + 18 * 150) / 3600.0)
        self.assertAlmostEqual(e["comp:idle"]["parts"]["idle"], 24 * model.IDLE_PRIOR_MA / 3600.0)
        self.assertAlmostEqual(
            e["comp:standby"]["parts"]["standby"], 360 * model.STANDBY_PRIOR_MA / 3600.0
        )

    def test_radios_and_dsps(self):
        p = profile()
        e = model.interval_model(
            interval(
                wifi={"up_s": 60.0, "rx_bytes": 0, "tx_bytes": 0, "rx_pkts": 0, "tx_pkts": 0},
                modem={"up_s": 60.0, "awake_s": 30.0, "rx_bytes": 0, "tx_bytes": 0},
                bluetooth={"on_s": 60.0, "bytes": 0, "pkts": 0},
                dsp_awake_s={"adsp": 36.0, "slpi": 0.0},
            ),
            p,
        )
        self.assertAlmostEqual(e["comp:wifi"]["parts"]["radio"], 60 * 3 / 3600.0)
        self.assertAlmostEqual(e["comp:modem"]["parts"]["radio"], (60 * 10 + 30 * 5) / 3600.0)
        self.assertAlmostEqual(e["comp:bluetooth"]["parts"]["radio"], 60 * 1 / 3600.0)
        self.assertAlmostEqual(e["comp:dsp"]["parts"]["dsp"], 36 * 20 / 3600.0)
        # traffic costs airtime
        busy = model.interval_model(
            interval(
                wifi={"up_s": 60.0, "rx_bytes": 125e6, "tx_bytes": 0, "rx_pkts": 0, "tx_pkts": 0}
            ),
            p,
        )
        self.assertAlmostEqual(
            busy["comp:wifi"]["parts"]["radio"],
            (60 * 3 + 125e6 * 8 / model.WIFI_RATE_BPS * 100) / 3600.0,
        )


class Calibrating(unittest.TestCase):
    def test_allocation_adds_up_to_the_measured_drain(self):
        p = profile()
        ivs = [
            model.interval_model(
                interval(procs={"app:a": {"cpu_s": [30, 5, 1]}}, lit_s=[60, 60], bright_s=[30, 30]),
                p,
            )
            for _ in range(5)
        ]
        cal = model.Calibration()
        alloc, summary = model.allocate(ivs, 12.5, cal)
        self.assertAlmostEqual(sum(sum(a.values()) for a in alloc), 12.5)
        # proportional: the app's share of the estimate is its share of the drain
        est = [sum(sum(e["parts"].values()) for e in iv.values()) for iv in ivs]
        app = sum(sum(iv["app:a"]["parts"].values()) for iv in ivs)
        self.assertAlmostEqual(sum(a["app:a"] for a in alloc), 12.5 * app / sum(est))
        self.assertAlmostEqual(summary["scale"], 12.5 / sum(est))
        self.assertEqual(cal.groups, 1)

    def test_without_a_measurement_the_model_stands(self):
        p = profile()
        ivs = [model.interval_model(interval(off_awake_s=60.0), p)]
        alloc, s = model.allocate(ivs, None, model.Calibration())
        self.assertAlmostEqual(alloc[0]["comp:idle"], 60 * model.IDLE_PRIOR_MA / 3600.0)
        self.assertIsNone(s["measured"])

    def test_no_data_means_the_figures_as_they_are(self):
        self.assertEqual(set(model.Calibration().scales().values()), {1.0})

    def test_learns_a_wrong_idle_floor_and_a_wrong_screen(self):
        # the truth: idle draws 2x the assumed floor, the screen 0.6x its
        # figure; cpu as the table says. Groups of varying mix + noise.
        rnd = random.Random(7)
        cal = model.Calibration()
        truth = {"idle": 2.0, "screen": 0.6, "cpu": 1.0}
        for _ in range(400):
            x = dict.fromkeys(model.GROUPS, 0.0)
            x["idle"] = rnd.uniform(0, 8)
            x["screen"] = rnd.uniform(0, 10)
            x["cpu"] = rnd.uniform(0, 4)
            y = sum(truth[g] * x[g] for g in truth) + rnd.gauss(0, 0.5)
            cal.update([x[g] for g in model.GROUPS], y)
        s = cal.scales()
        self.assertAlmostEqual(s["idle"], 2.0, delta=0.15)
        self.assertAlmostEqual(s["screen"], 0.6, delta=0.15)
        self.assertAlmostEqual(s["cpu"], 1.0, delta=0.15)
        self.assertAlmostEqual(s["gpu"], 1.0)  # never seen: the prior
        # and it survives the store
        again = model.Calibration(cal.state())
        self.assertEqual(again.scales(), s)

    def test_scales_are_bounded(self):
        cal = model.Calibration()
        for _ in range(100):
            x = [0.0] * len(model.GROUPS)
            x[model.GROUPS.index("gpu")] = 1.0
            cal.update(x, 100.0)
        self.assertEqual(cal.scales()["gpu"], model.SCALE_MAX)

    def test_solve(self):
        self.assertEqual([round(v, 9) for v in model.solve([[2, 1], [1, 3]], [3, 5])], [0.8, 1.4])
        self.assertIsNone(model.solve([[1, 1], [1, 1]], [1, 2]))


def bat(**kw):
    s = {
        "usb_online": False,
        "current_ua": -300000,
        "voltage_uv": 3900000,
        "charge_uah": 3000000,
        "usb_ua": 0,
        "usb_uv": 0,
        "status": "Discharging",
        "level": 70,
    }
    s.update(kw)
    return s


class Measuring(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(model.interval_kind(bat(), bat()), "battery")
        plugged = bat(usb_online=True, current_ua=1500000, status="Charging")
        full = bat(usb_online=True, current_ua=20000, status="Full")
        self.assertEqual(model.interval_kind(plugged, plugged), "charging")
        self.assertEqual(model.interval_kind(full, full), "charger")
        self.assertEqual(model.interval_kind(bat(), plugged), "mixed")
        # plugged into a weak charger and still discharging: the charger's input counts
        weak = bat(usb_online=True, current_ua=-200000)
        self.assertEqual(model.interval_kind(weak, weak), "charger")
        # the battery manager not answering yet (early boot): nothing measured
        self.assertEqual(
            model.interval_kind(bat(level=None, current_ua=None, charge_uah=None), bat()), "mixed"
        )

    def test_charge_counter(self):
        m, carry = model.measured_drain(
            "battery", bat(charge_uah=3000000), bat(charge_uah=2995000), 60
        )
        self.assertEqual((m, carry), (5.0, 0.0))

    def test_frozen_counter_is_bridged_and_taken_back(self):
        a, b = (
            bat(charge_uah=4400000, current_ua=-300000),
            bat(charge_uah=4400000, current_ua=-300000),
        )
        m1, carry = model.measured_drain("battery", a, b, 60)
        self.assertAlmostEqual(m1, 5.0)  # 300 mA for a minute
        self.assertAlmostEqual(carry, 5.0)
        # the counter moves at last, by the whole time since it stopped
        m2, carry = model.measured_drain("battery", b, bat(charge_uah=4389000), 60, carry)
        self.assertAlmostEqual(m1 + m2, 11.0)
        self.assertEqual(carry, 0.0)

    def test_on_the_charger(self):
        full = bat(usb_online=True, usb_uv=5000000, usb_ua=440000, current_ua=0, voltage_uv=4400000)
        m, _ = model.measured_drain("charger", full, full, 3600)
        self.assertAlmostEqual(m, model.CHARGER_EFFICIENCY * 2200 / 4.4)
        self.assertEqual(model.measured_drain("charging", full, full, 60), (None, 0.0))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.connect(os.path.join(self.tmp.name, "usage.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_usage_adds_up_and_prunes(self):
        now = 1_800_000_000.0
        old = now - 8 * 86400
        u = {
            (store.bucket_of(now), "battery", "app:a"): {
                "mah": 1.0,
                "cpu_mah": 1.0,
                "extra": {"cpu0_s": 3},
            }
        }
        store.write(
            self.db,
            samples=[
                (old, 50, "Discharging", 0, -300, 3.9, 3000, 0, 0),
                (now, 49, "Discharging", 0, -300, 3.9, 2990, 0, 0),
            ],
            periods=[(old, old + 60, "screen")],
            usage=u,
            groups=[(old, old + 60, "battery", 5, 4, 4, 1.25)],
        )
        store.write(self.db, usage=u)
        row = self.db.execute("SELECT mah, extra FROM usage").fetchone()
        self.assertEqual(row[0], 2.0)
        self.assertEqual(row[1], '{"cpu0_s":6}')
        self.assertEqual(store.prune(self.db, now=now), 3)  # the old sample, period and group
        self.assertEqual(self.db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)

    def test_clear_keeps_the_calibration(self):
        store.set_meta(self.db, "calibration", {"groups": 3})
        store.write(self.db, samples=[(1.0, 50, "x", 0, 0, 0, 0, 0, 0)])
        store.clear(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM samples").fetchone()[0], 0)
        self.assertEqual(store.get_meta(self.db, "calibration"), {"groups": 3})

    def test_readonly_reader(self):
        store.write(self.db, samples=[(1.0, 50, "x", 0, 0, 0, 0, 0, 0)])
        ro = store.connect(os.path.join(self.tmp.name, "usage.db"), readonly=True)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("DELETE FROM samples")
        ro.close()


class Reporting(unittest.TestCase):
    def test_window_starts_at_the_last_full_unplug(self):
        s = [
            (0, 60, True),
            (10, 95, True),
            (20, 94, False),
            (30, 80, False),
            (40, 70, True),
            (50, 72, False),
        ]
        self.assertEqual(report.window_start(s), (20, "full"))  # the unplug at 72 % does not reset
        self.assertEqual(report.window_start([(5, 50, False)]), (5, "oldest"))

    def test_runs_and_totals(self):
        s = [(0, 90, False), (60, 89, False), (120, 88, True), (180, 90, True), (240, 89, False)]
        self.assertEqual(report.runs(s, lambda x: x[2]), [(120, 240)])
        self.assertEqual(report.battery_totals(s), (60, 1.0))
        gap = [(0, 90, False), (5000, 80, False)]  # the collector was not running
        self.assertEqual(report.battery_totals(gap), (0.0, 0.0))

    def test_a_night_asleep_counts(self):
        # The sleep hook samples right before and after every suspend: the
        # samples are hours apart, the suspends cover the gaps.
        s = [
            (0, 33, False),
            (20, 33, False),
            (3920, 31, False),
            (3935, 31, False),
            (11120, 26, False),
            (11130, 26, False),
            (40000, 4, False),
            (41000, 4, True),
        ]
        periods = [
            (21, 3919, "suspend"),
            (3936, 11119, "suspend"),
            (11131, 18000, "suspend"),
            (18100, 39990, "suspend"),
            (0, 20, "screen"),
        ]
        b = report.bridged_by(periods)
        on, drop = report.battery_totals(s, b)
        self.assertEqual((on, drop), (40000, 29.0))
        # without them: only the minutes awake
        self.assertEqual(report.battery_totals(s), (45, 0.0))
        # a suspend it never came back from (an empty battery): no period, a gap
        self.assertFalse(b(11130, 60000))
        self.assertTrue(b(11130, 18050))  # 50 s uncovered
        self.assertFalse(report.bridged_by([])(0, 601))
        self.assertTrue(report.bridged_by([])(0, 600))
        self.assertEqual(
            report.runs(
                [(0, 5, True), (5000, 9, True), (5010, 9, False)],
                lambda x: x[2],
                report.bridged_by([(10, 4995, "suspend")]),
            ),
            [(0, 5010)],
        )

    def test_the_report_shows_the_night(self):
        tmp = tempfile.TemporaryDirectory()
        db = store.connect(os.path.join(tmp.name, "u.db"))
        t0 = 1_800_000_000.0
        samples = [
            (t0 - 60, 95, "Charging", 1, 500, 4.3, 3500, 3000, 1),
            (t0, 94, "Discharging", 0, -400, 4.2, 3480, 0, 1),
        ]
        periods = []
        t, level, charge = t0, 94, 3480
        for _ in range(4):  # 2 h asleep, 15 s awake
            periods.append((t + 1, t + 7200, "suspend"))
            t += 7200
            level -= 10
            charge -= 360
            samples.append((t, level, "Discharging", 0, -170, 3.8, charge, 0, 0))
            t += 15
            samples.append((t, level, "Discharging", 0, -400, 3.8, charge - 2, 0, 0))
        store.write(db, samples=samples, periods=periods)
        rep = report.build(db, now=t + 60, profile=profile())
        self.assertEqual(rep["window"]["reason"], "full")
        self.assertEqual(rep["battery"]["on_battery_s"], round(t - t0))
        self.assertEqual(rep["battery"]["used_pct"], 40.0)
        self.assertEqual(len(rep["graph"]["suspended"]), 4)
        db.close()
        tmp.cleanup()

    def test_the_list_adds_up_to_the_drain(self):
        p = profile()
        tmp = tempfile.TemporaryDirectory()
        db = store.connect(os.path.join(tmp.name, "u.db"))
        cal = model.Calibration()
        t0 = 1_800_000_000.0
        usage = {}
        measured_total = 0.0
        samples = [(t0 - 60, 100, "Full", 1, 0, 4.4, 4400, 2000, 0)]
        for i in range(30):
            iv = model.interval_model(
                interval(
                    procs={"app:a": {"cpu_s": [20, 2, 0]}, "system:kernel": {"cpu_s": [1, 0, 0]}},
                    lit_s=[60, 0],
                    bright_s=[20, 0],
                ),
                p,
            )
            m = 3.0 + (i % 3)
            measured_total += m
            alloc, _s = model.allocate([iv], m, cal)
            for k, v in alloc[0].items():
                u = usage.setdefault(
                    (store.bucket_of(t0 + 60 * i), "battery", k),
                    {"mah": 0.0, "cpu_mah": 0.0, "gpu_mah": 0.0, "extra": {}},
                )
                u["mah"] += v
            samples.append((t0 + 60 * i, 99 - i // 10, "Discharging", 0, -300, 3.9, 4400 - i, 0, 1))
        store.write(db, samples=samples, usage=usage)
        rep = report.build(db, now=t0 + 1800, profile=p, calibration=cal)
        self.assertEqual(rep["window"]["reason"], "full")
        self.assertAlmostEqual(
            sum(e["mah"] for e in rep["entries"]["battery"]), measured_total, places=2
        )
        self.assertEqual(rep["battery"]["used_pct"], 2.0)
        self.assertEqual(rep["battery"]["capacity_mah"], 4000.0)
        db.close()
        tmp.cleanup()


class Classifying(unittest.TestCase):
    def test_keys(self):
        c = classify.classify
        self.assertEqual(c({"kthread": True}), "system:kernel")
        self.assertEqual(
            c(
                {
                    "comm": "firefox-esr",
                    "argv": ["/usr/lib/firefox-esr/firefox-esr"],
                    "uid": 1000,
                    "desktop": "/usr/share/applications/firefox-esr.desktop",
                }
            ),
            "app:firefox-esr",
        )
        self.assertEqual(
            c({"comm": "bwrap", "argv": ["bwrap"], "uid": 1000, "flatpak": "org.gnome.Maps"}),
            "app:org.gnome.Maps",
        )
        self.assertEqual(
            c(
                {
                    "comm": "ndroid.settings",
                    "argv": ["com.android.settings"],
                    "cgroup": "0::/lxc.payload.waydroid",
                }
            ),
            "android:com.android.settings",
        )
        self.assertEqual(
            c(
                {
                    "comm": "gms",
                    "argv": ["com.google.android.gms:persistent"],
                    "cgroup": "0::/lxc.payload.waydroid",
                }
            ),
            "android:com.google.android.gms",
        )
        self.assertEqual(
            c(
                {
                    "comm": "surfaceflinger",
                    "argv": ["/system/bin/surfaceflinger"],
                    "cgroup": "0::/lxc.payload.waydroid",
                }
            ),
            "android:system",
        )
        self.assertEqual(
            c(
                {
                    "comm": "duo-shell",
                    "argv": ["/usr/bin/python3", "-sE", "/usr/local/bin/duo-shell"],
                    "uid": 1000,
                }
            ),
            "system:shell",
        )
        self.assertEqual(
            c(
                {
                    "comm": "linker64",
                    "argv": ["/opt/duo2-touch/linker64", "/opt/duo2-touch/duo2-touchd"],
                    "uid": 0,
                }
            ),
            "system:touch",
        )
        self.assertEqual(
            c(
                {
                    "comm": "session",
                    "argv": ["/bin/sh", "/usr/local/lib/duo-wc/session"],
                    "uid": 1000,
                }
            ),
            "system:shell",
        )
        self.assertEqual(
            c({"comm": "gnome-calls", "argv": ["/usr/bin/gnome-calls", "--daemon"], "uid": 1000}),
            "exe:gnome-calls",
        )
        self.assertEqual(
            c({"comm": "systemd", "argv": ["/sbin/init"], "uid": 0}), "system:services"
        )
        self.assertEqual(
            c({"comm": "systemd", "argv": ["/usr/lib/systemd/systemd", "--user"], "uid": 1000}),
            "system:session",
        )
        self.assertEqual(
            c({"comm": "polkitd", "argv": ["/usr/lib/polkit-1/polkitd"], "uid": 996}),
            "system:services",
        )
        self.assertEqual(
            c(
                {
                    "comm": "evolution-alarm",
                    "argv": ["/usr/libexec/evolution-data-server/evolution-alarm-notify"],
                    "uid": 1000,
                }
            ),
            "system:session",
        )

    def test_children_inherit_an_apps_key(self):
        sh = {"comm": "bash", "argv": ["bash", "-c", "ls"], "uid": 1001}
        self.assertEqual(classify.classify(sh, "system:remote"), "system:remote")
        self.assertEqual(classify.classify(sh, "app:foot"), "app:foot")
        self.assertEqual(classify.classify(sh, "system:session"), "exe:bash")
        self.assertEqual(
            classify.proc_name("python3", ["python3", "-m", "http.server"])[0], "http.server"
        )
        # a desktop file in the environment wins over the parent
        self.assertEqual(
            classify.classify(dict(sh, desktop="/x/org.gnome.TextEditor.desktop"), "app:foot"),
            "app:org.gnome.TextEditor",
        )

    def test_window_keys(self):
        self.assertEqual(classify.window_key("org.gnome.TextEditor"), "app:org.gnome.TextEditor")
        self.assertEqual(
            classify.window_key("waydroid.com.android.settings"), "android:com.android.settings"
        )
        self.assertEqual(classify.window_key("Waydroid"), "android:system")
        self.assertIsNone(classify.window_key(None))
        self.assertIsNone(classify.window_key("bad id/../x"))

    def test_window_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "org.gnome.TextEditor.desktop"), "w") as f:
                f.write(
                    "[Desktop Entry]\nName=Text Editor\nExec=gnome-text-editor %U\n"
                    "[Desktop Action new-window]\nExec=gnome-text-editor --new-window\n"
                )
            with open(os.path.join(d, "x.desktop"), "w") as f:
                f.write("[Desktop Entry]\nExec=env GDK_BACKEND=wayland /usr/bin/xprog\n")
            self.assertEqual(
                classify.window_aliases("org.gnome.TextEditor", (d,)),
                {"app:org.gnome.TextEditor", "exe:gnome-text-editor"},
            )
            self.assertEqual(classify.window_aliases("x", (d,)), {"app:x", "exe:xprog"})
            self.assertEqual(classify.window_aliases("nofile", (d,)), {"app:nofile"})
            self.assertEqual(classify.window_aliases("waydroid.com.a.b", (d,)), {"android:com.a.b"})

    def test_flatpak_info(self):
        self.assertEqual(
            classify.parse_flatpak_info("[Application]\nname=org.gnome.Maps\nruntime=x\n"),
            "org.gnome.Maps",
        )
        self.assertIsNone(classify.parse_flatpak_info("[Runtime]\nname=org.gnome.Platform\n"))


class Parsing(unittest.TestCase):
    def test_stat_with_an_awkward_name(self):
        rest = (
            [
                "S",
                "1",
                "2",
                "3",
                "0",
                "-1",
                "4194560",
                "10",
                "0",
                "0",
                "0",
                "150",
                "50",
                "7",
                "3",
                "20",
                "0",
                "4",
                "0",
                "12345",
            ]
            + ["0"] * 16
            + ["5"]
        )
        st = sources.parse_stat("42 (we ird) (x)) " + " ".join(rest))
        self.assertEqual(
            (
                st["comm"],
                st["ppid"],
                st["utime"],
                st["stime"],
                st["cutime"],
                st["cstime"],
                st["threads"],
                st["start"],
                st["processor"],
            ),
            ("we ird) (x)", 1, 150, 50, 7, 3, 4, 12345, 5),
        )

    def test_proc_stat_and_time_in_state(self):
        st = sources.parse_proc_stat(
            "cpu  1 2 3 4 5 6 7 0 0 0\ncpu0 10 1 5 100 2 3 4 0 0 0\nintr 5\n"
        )
        self.assertEqual(st, {0: (16, 7)})
        tis = sources.parse_time_in_state("300000 100\n691200 50\n")
        self.assertAlmostEqual(tis[300000], 100 / float(sources.CLK_TCK))

    def test_qcom_stats_and_the_sleep_in_progress(self):
        st = sources.parse_qcom_stats(
            "Count: 4\nLast Entered At: 1000\nLast Exited At: 900\nAccumulated Duration: 500\n"
        )
        self.assertEqual(st, {"count": 4, "entered": 1000, "exited": 900, "sleep": 500})
        self.assertEqual(sources.sleep_ticks(st, 1600), 1100)  # asleep since 1000
        awake = dict(st, exited=1200)
        self.assertEqual(sources.sleep_ticks(awake, 1600), 500)
        self.assertIsNone(sources.parse_qcom_stats(""))

    def test_tick_clock_converges_from_below(self):
        clock = sources.TickClock()
        clock.observe(
            10.0, {"adsp": {"entered": int(10.0 * 19.2e6) + 500, "exited": 0, "sleep": 0}}
        )
        self.assertEqual(clock.now(10.0), 10.0 * 19.2e6 + 500)
        clock.observe(
            20.0, {"adsp": {"entered": 0, "exited": int(20.0 * 19.2e6) + 400, "sleep": 0}}
        )
        self.assertEqual(clock.offset, 500)  # a lower bound never goes down

    def test_drm(self):
        text = (
            "             command  tgid dev master a   uid      magic   name    id\n"
            "               seatd   548   0   y    y     0          0 <unset>    3\n"
            "           duo-shell   944 128   n    n  1000          0 <unset>    4\n"
        )
        self.assertEqual(sources.parse_drm_clients(text), {3: 548, 4: 944})
        info = "pos:\t0\ndrm-driver:\tmsm\ndrm-client-id:\t3\ndrm-engine-gpu:\t10316632 ns\ndrm-cycles-gpu:\t6792102\n"
        self.assertEqual(sources.parse_drm_fdinfo(info), (3, 10316632, 6792102))
        self.assertIsNone(sources.parse_drm_fdinfo("pos: 0\nflags: 02\n"))

    def test_drm_fds_come_from_fdinfo(self):
        # this process's own fdinfo has no DRM file
        self.assertEqual(sources.drm_fds(os.getpid()), [])
        self.assertEqual(sources.drm_fds(2**22 + 7), [])  # no such process

    def test_hci_dev_info(self):
        buf = struct.pack(
            "<H8s6sIB8sxxxIIIHHHH10I",
            0,
            b"hci0",
            b"\0" * 6,
            1,
            1,
            b"\0" * 8,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            1000,
            24,
        )
        self.assertEqual(len(buf), 92)
        self.assertEqual(
            sources.parse_hci_dev_info(buf), {"up": True, "bytes": 1024, "pkts": 5 + 6 + 7 + 8}
        )

    def test_visible_apps(self):
        state = {
            "lit": ["DSI-1"],
            "splits": [],
            "windows": [
                {"id": 1, "app_id": "a", "placement": {"output": "DSI-1"}},
                {"id": 2, "app_id": "b", "placement": {"output": "DSI-1"}},
                {"id": 3, "app_id": "c", "placement": {"output": "DSI-2"}},
                {"id": 4, "app_id": "d", "placement": {"output": "DSI-1"}, "minimized": True},
            ],
        }
        self.assertEqual(sources.visible_apps(state), {"b"})  # front of the lit panel only
        state["splits"] = [{"output": "DSI-1", "windows": [1, 2], "front": True}]
        self.assertEqual(sources.visible_apps(state), {"a", "b"})
        span = {
            "lit": ["DSI-1", "DSI-2"],
            "windows": [{"id": 1, "app_id": "s", "placement": "span"}],
        }
        self.assertEqual(sources.visible_apps(span), {"s"})
        self.assertEqual(sources.visible_apps({"lit": [], "windows": span["windows"]}), set())


class Timing(unittest.TestCase):
    """The collector's deadlines: the look and the sample on CLOCK_MONOTONIC,
    the store on CLOCK_BOOTTIME."""

    def test_a_night_asleep_is_stored_at_the_first_wake(self):
        from duo2battery import collector as col

        t = col.Timers(100.0, 100.0)
        self.assertEqual(t.due(100.0, 100.0), (None, False))
        self.assertAlmostEqual(t.timeout(100.0, 100.0), col.POLL_S)
        # 2 h asleep, 20 s awake after it: monotonic hardly moved
        mono, boot = 120.0, 7300.0
        step, store_due = t.due(mono, boot)
        self.assertEqual(step, "poll")
        self.assertTrue(store_due)
        self.assertEqual(t.timeout(mono, boot), 0.0)
        t.stored(boot)
        t.polled(mono)
        self.assertEqual(t.due(mono, boot), (None, False))
        self.assertAlmostEqual(t.timeout(mono, boot), col.POLL_S)
        # awake: a sample a minute, the store every FLUSH_S
        self.assertEqual(t.due(mono + col.SAMPLE_S, boot + col.SAMPLE_S), ("sample", False))
        self.assertEqual(t.due(mono + col.FLUSH_S, boot + col.FLUSH_S), ("sample", True))

    def test_the_store_signal_is_a_real_time_signal(self):
        import signal

        from duo2battery import collector as col

        self.assertEqual(col.STORE_SIG, signal.SIGRTMIN + 1)


class Polling(unittest.TestCase):
    """The 10 s look: awake and suspended time from the two clocks, the lit
    panels, the windows on screen (fake clocks and readers)."""

    def setUp(self):
        from duo2battery import collector

        self.collector = collector
        self.saved = (collector.clocks, sources.read_screen, sources.wc_state)
        self.now = [1000.0, 50.0, 50.0]  # wall, boottime, monotonic
        self.screen = [(False, 0.0), (False, 0.0)]
        self.state = None
        collector.clocks = lambda: tuple(self.now)
        sources.read_screen = lambda: list(self.screen)
        sources.wc_state = lambda timeout=1.0: self.state
        c = object.__new__(collector.Collector)
        c.screen = [(False, 0.0), (False, 0.0)]
        c.visible, c.visible_aliases, c.alias_cache = set(), set(), {}
        c.screen_since = None
        c.pending = {"samples": [], "periods": [], "usage": {}, "groups": []}
        c.poll_boot, c.poll_mono = 50.0, 50.0
        c.reset_acc()
        self.c = c

    def tearDown(self):
        self.collector.clocks, sources.read_screen, sources.wc_state = self.saved

    def step(self, awake, suspended=0.0):
        self.now[0] += awake + suspended
        self.now[1] += awake + suspended
        self.now[2] += awake
        self.c.poll()

    def test_suspended_time_is_standby_and_a_period(self):
        self.step(10)
        self.step(2, suspended=120)  # an rtcwake: monotonic stood still
        self.step(10)
        a = self.c.acc
        self.assertAlmostEqual(a["awake_s"], 22)
        self.assertAlmostEqual(a["suspended_s"], 120)
        self.assertAlmostEqual(a["off_awake_s"], 22)
        self.assertEqual(self.c.pending["periods"], [(1132.0 - 120, 1132.0, "suspend")])
        self.step(10, suspended=0.2)  # the clocks read a moment apart: not a suspend
        self.assertAlmostEqual(a["suspended_s"], 120)

    def test_lit_panels_brightness_and_windows(self):
        self.screen = [(True, 0.5), (False, 0.0)]
        self.state = {
            "lit": ["DSI-1"],
            "windows": [{"id": 1, "app_id": "org.example.App", "placement": {"output": "DSI-1"}}],
        }
        self.step(10)  # the state is taken now, counted from here
        self.step(10)
        self.step(10)
        self.screen = [(False, 0.0), (False, 0.0)]
        self.step(10)  # still counted lit: seen off at its end
        self.step(10)
        a = self.c.acc
        self.assertAlmostEqual(a["lit_s"][0], 30)
        self.assertAlmostEqual(a["bright_s"][0], 15)
        self.assertAlmostEqual(a["off_awake_s"], 20)
        self.assertAlmostEqual(a["fg"]["app:org.example.App"], 30)
        self.assertEqual(self.c.pending["periods"], [(1010.0, 1040.0, "screen")])


def axml(elements):
    """A minimal compiled XML (UTF-16 string pool): [(name, {attr: value},
    text or None, children)] -> bytes."""
    strings = []

    def sid(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    body = []

    def emit(el):
        name, attrs, text, children = el
        n = sid(name)
        attr_bytes = b"".join(
            struct.pack("<IIIHBBI", 0xFFFFFFFF, sid(k), sid(v), 8, 0, 3, sid(v))
            for k, v in attrs.items()
        )
        head = struct.pack("<II", 0, 0xFFFFFFFF) + struct.pack(
            "<IIHHHHHH", 0xFFFFFFFF, n, 20, 20, len(attrs), 0, 0, 0
        )
        start = head + attr_bytes
        body.append(struct.pack("<HHI", 0x0102, 16, 8 + len(start)) + start)
        if text is not None:
            cd = struct.pack("<II", 0, 0xFFFFFFFF) + struct.pack("<IHBBI", sid(text), 8, 0, 3, 0)
            body.append(struct.pack("<HHI", 0x0104, 16, 8 + len(cd)) + cd)
        for c in children:
            emit(c)
        end = struct.pack("<II", 0, 0xFFFFFFFF) + struct.pack("<II", 0xFFFFFFFF, n)
        body.append(struct.pack("<HHI", 0x0103, 16, 8 + len(end)) + end)

    for el in elements:
        emit(el)
    data = b""
    offsets = []
    for s in strings:
        offsets.append(len(data))
        data += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
    while len(data) % 4:
        data += b"\0"
    pool_head = 28 + 4 * len(strings)
    pool = struct.pack(
        "<HHIIIIII", 0x0001, 28, pool_head + len(data), len(strings), 0, 0, pool_head, 0
    )
    pool += b"".join(struct.pack("<I", o) for o in offsets) + data
    rest = pool + b"".join(body)
    return struct.pack("<HHI", 0x0003, 8, 8 + len(rest)) + rest


class Axml(unittest.TestCase):
    def test_decode(self):
        import axml2xml

        blob = axml(
            [
                (
                    "device",
                    {"name": "Android"},
                    None,
                    [
                        ("item", {"name": "screen.on"}, "12.5", []),
                        (
                            "array",
                            {"name": "radio.on"},
                            None,
                            [("value", {}, "3", []), ("value", {}, "4", [])],
                        ),
                    ],
                )
            ]
        )
        table = parse_power_profile(axml2xml.decode(blob))
        self.assertEqual(table, {"screen.on": 12.5, "radio.on": [3.0, 4.0]})
        with self.assertRaises(ValueError):
            axml2xml.decode(b"\x01\x00\x08\x00\x08\x00\x00\x00")


if __name__ == "__main__":
    unittest.main()
