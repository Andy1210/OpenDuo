# SPDX-License-Identifier: Apache-2.0
"""duo2-pend's pure parts (rootfs/overlay/usr/local/sbin/duo2-pend): BlueZ's
storage, the address-resolution flag, the AutoBond state file, the haptic
settings and reports, the writing feel's timing.  touch/tests/run.sh"""

import importlib.machinery
import importlib.util
import math
import os
import random
import socket
import struct
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "..", "rootfs", "overlay", "usr", "local", "sbin", "duo2-pend")
loader = importlib.machinery.SourceFileLoader("duo2_pend", PATH)
spec = importlib.util.spec_from_loader("duo2_pend", loader)
pend = importlib.util.module_from_spec(spec)
loader.exec_module(pend)

# placeholders, not a real device
INFO_PEN = """[General]
Name=Surface Slim Pen 2
AddressType=static
SupportedTechnologies=LE;
Trusted=true

[IdentityResolvingKey]
Key=00112233445566778899AABBCCDDEEFF

[LongTermKey]
Key=FFEEDDCCBBAA99887766554433221100
"""
INFO_MOUSE = """[General]
Name=Some Mouse
AddressType=public
SupportedTechnologies=LE;
"""
INFO_BREDR = """[General]
Name=Headphones
SupportedTechnologies=BR/EDR;
"""


class Storage(unittest.TestCase):
    def test_parse_info(self):
        self.assertEqual(pend.parse_info(INFO_PEN), ("Surface Slim Pen 2", "static", True))
        self.assertEqual(pend.parse_info(INFO_MOUSE), ("Some Mouse", "public", False))
        # an empty key is no key
        self.assertFalse(pend.parse_info("[IdentityResolvingKey]\nKey=\n")[2])

    def test_surface_pen(self):
        self.assertTrue(pend.is_surface_pen("Surface Slim Pen 2"))
        self.assertTrue(pend.is_surface_pen("Surface Pen"))
        self.assertFalse(pend.is_surface_pen("Surface Headphones"))
        self.assertFalse(pend.is_surface_pen("Pen"))

    def test_scan_storage(self):
        with tempfile.TemporaryDirectory() as d:
            adapter = "00:11:22:33:44:55"
            for dev, text in (
                ("C0:00:00:00:00:01", INFO_PEN),
                ("00:00:00:00:00:02", INFO_MOUSE),
                ("00:00:00:00:00:03", INFO_BREDR),
            ):
                os.makedirs(os.path.join(d, adapter, dev))
                with open(os.path.join(d, adapter, dev, "info"), "w") as f:
                    f.write(text)
            os.makedirs(os.path.join(d, adapter, "cache"))  # not a device
            old = pend.STORAGE
            pend.STORAGE = d
            try:
                got = pend.scan_storage(pend.addr_from_str(adapter))
            finally:
                pend.STORAGE = old
        pen = (pend.addr_from_str("C0:00:00:00:00:01"), pend.BDADDR_LE_RANDOM)
        mouse = (pend.addr_from_str("00:00:00:00:00:02"), pend.BDADDR_LE_PUBLIC)
        self.assertEqual(got, {pen: ("Surface Slim Pen 2", True), mouse: ("Some Mouse", False)})
        self.assertEqual(pen[0][0], 0x01)  # mgmt order: LSB first


class Flags(unittest.TestCase):
    def test_resolution_flag(self):
        # BlueZ 5.82's flags for the pen: remote wakeup + device privacy
        self.assertEqual(pend.flags_with_resolution(0x0F, 0x03), 0x07)
        self.assertIsNone(pend.flags_with_resolution(0x0F, 0x07))  # already set: nothing
        self.assertIsNone(pend.flags_with_resolution(0x03, 0x03))  # not supported: nothing


class AutobondState(unittest.TestCase):
    def test_text(self):
        text = pend.autobond_text(bytes([0x55, 0x44, 0x33, 0x22, 0x11, 0x00]), True, False)
        self.assertIn("host 001122334455 public\n", text)
        self.assertIn("adapter on\n", text)
        self.assertIn("pen-link down\n", text)
        text = pend.autobond_text(None, False, False)
        self.assertNotIn("host", text.replace("duo2-touchd: the AutoBond host", ""))
        self.assertIn("adapter off\n", text)

    def test_text_is_what_touchd_reads(self):
        # the line touch/autobond.h parses: "host <12 hex MSB first> public"
        text = pend.autobond_text(pend.addr_from_str("AA:BB:CC:DD:EE:FF"), True, True)
        self.assertIn("host aabbccddeeff public\n", text)
        self.assertIn("pen-link up\n", text)


class Haptics(unittest.TestCase):
    def test_conf(self):
        self.assertEqual(pend.haptic_conf(""), ("off", 50, False))
        text = (
            "palm_rejection = both\nwriting_feel = pencil\nfeel_strength = 80\nbutton_click = on\n"
        )
        self.assertEqual(pend.haptic_conf(text), ("pencil", 80, True))
        # invalid values are ignored, the last valid line wins
        text = "writing_feel = ink\nwriting_feel = loud\nfeel_strength = 0\nfeel_strength = 101\nbutton_click = yes\n"
        self.assertEqual(pend.haptic_conf(text), ("ink", 50, False))
        self.assertEqual(pend.haptic_conf("writing_feel = marker # comment\n")[0], "marker")

    def test_intensity(self):
        self.assertEqual(pend.intensity(100), 255)
        self.assertEqual(pend.intensity(50), 128)
        self.assertEqual(pend.intensity(1), 3)
        self.assertEqual(pend.intensity(0), 1)

    def test_waveform_list_and_ordinals(self):
        # the Slim Pen 2's feature 0x42 (as read from the pen): tip switch, durations, waveforms
        durations = [45, 60, 60, 35, 145, 185, 1995, 1995, 1995, 1995, 1980, 1995, 1980] + [0] * 7
        waves = pend.DEFAULT_WAVEFORMS + [pend.WAVE_NONE] * 7
        report = (
            bytes([0x42])
            + struct.pack("<I", 0x000D0042)
            + struct.pack("<20H", *durations)
            + struct.pack("<20H", *waves)
        )
        got = pend.waveform_list(report)
        self.assertEqual(got, pend.DEFAULT_WAVEFORMS)
        self.assertEqual(pend.ordinal(got, pend.WAVE_CLICK), 3)
        self.assertEqual(
            [
                pend.ordinal(got, pend.FEELS[f])
                for f in ("ink", "pencil", "marker", "chisel", "brush")
            ],
            [9, 10, 11, 12, 13],
        )
        self.assertEqual(pend.ordinal(got, pend.WAVE_NONE), 1)
        self.assertIsNone(pend.ordinal(got, 0x1004))  # buzz: the pen has none
        self.assertIsNone(pend.waveform_list(report[:40]))
        self.assertIsNone(pend.waveform_list(b"\x41" + report[1:]))

    def test_the_waveform_list_as_bluez_reads_it(self):
        # read from the pen 2026-11-16: 84 bytes, the last waveform cut in half (ATT MTU)
        raw = bytes.fromhex(
            "42 42 00 0d 00 2d 00 3c 00 3c 00 23 00 91 00 b9 00 cb 07 cb 07 cb 07 cb 07 bc 07 cb 07 bc 07"
            " 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 10 06 10 07 10 08 10 09 10 0a 10 0b 10 0c 10"
            " 0d 10 0e 10 0f 10 10 10 11 10 01 10 01 10 01 10 01 10 01 10 01 10 01"
        )
        self.assertEqual(len(raw), 84)
        self.assertEqual(pend.waveform_list(raw), pend.DEFAULT_WAVEFORMS)
        self.assertEqual(pend.ordinal(pend.waveform_list(raw), pend.WAVE_HOVER), 6)

    def test_tablet_frames(self):
        # duo2-touchd's tablet device as input_event records (here through a pipe)
        rfd, wfd = os.pipe()
        try:
            os.set_blocking(rfd, False)
            t = pend.PenTablets()
            t.fds[rfd] = dict(
                pend.tablet_device("Surface Duo 2 Pen Right", rfd),  # a pipe: no axes, no clock
                scale=[0.1, 0.1],
                pressure_max=65535,
            )
            self.assertFalse(t.fds[rfd]["clock"])
            ev = lambda typ, code, val, sec=0, usec=0: struct.pack(  # noqa: E731
                "<qqHHi", sec, usec, typ, code, val
            )
            os.write(
                wfd,
                ev(pend.EV_ABS, pend.ABS_X, 100)
                + ev(pend.EV_ABS, pend.ABS_Y, 50)
                + ev(pend.EV_ABS, pend.ABS_PRESSURE, 32768)
                + ev(pend.EV_KEY, pend.BTN_TOUCH, 1)
                + ev(pend.EV_SYN, 0, 0, 5, 250000)
                + ev(pend.EV_ABS, pend.ABS_X, 130)
                + ev(pend.EV_SYN, 0, 0, 5, 254000)
                + ev(pend.EV_ABS, pend.ABS_PRESSURE, 0)
                + ev(pend.EV_KEY, pend.BTN_TOUCH, 0)
                + ev(pend.EV_SYN, 0, 0, 5, 258000),
            )
            frames = t.read(rfd, now=9.0)
            self.assertEqual([f[1] for f in frames], [True, True, False])
            self.assertEqual([f[0] for f in frames], [9.0] * 3)  # no kernel clock: read time
            self.assertAlmostEqual(frames[0][2][0], 10.0)
            self.assertAlmostEqual(frames[1][2][0], 13.0)
            self.assertAlmostEqual(frames[1][2][1], 5.0)
            self.assertAlmostEqual(frames[1][3], 0.5, places=4)
            self.assertEqual(frames[2][3], 0.0)
            self.assertEqual(t.read(rfd), [])  # nothing more: no block
            self.assertEqual(t.read(12345), [])  # not ours
            # the kernel's clock (EVIOCSCLOCKID took): each frame's own time
            t.fds[rfd]["clock"] = True
            os.write(wfd, ev(pend.EV_ABS, pend.ABS_X, 140) + ev(pend.EV_SYN, 0, 0, 7, 500000))
            self.assertEqual(t.read(rfd, now=9.0)[0][0], 7.5)
            # no pressure axis: None
            t.fds[rfd]["pressure_max"] = None
            os.write(wfd, ev(pend.EV_SYN, 0, 0, 7, 504000))
            self.assertIsNone(t.read(rfd)[0][3])
        finally:
            os.close(wfd)
            os.close(rfd)

    def test_reports(self):
        # feature 0x41: RepeatCount, Intensity, AutoTrigger, RetriggerPeriod (u16)
        self.assertEqual(pend.auto_trigger_report(9, 128), bytes([0x41, 0, 128, 9, 0, 0]))
        # output 0x41: RepeatCount, Intensity, ManualTrigger, WaveformCutoff, RetriggerPeriod (u16)
        self.assertEqual(pend.manual_report(3, 200), bytes([0x41, 0, 200, 3, 20, 0, 0]))

    def test_fw_update_mark(self):
        with tempfile.TemporaryDirectory() as d:
            mark = os.path.join(d, "fw-update")
            self.assertFalse(pend.fw_update_running(mark))
            with open(mark, "w") as f:
                f.write("%d\n" % os.getpid())
            self.assertTrue(pend.fw_update_running(mark))
            with open(mark, "w") as f:
                f.write("999999999\n")
            self.assertFalse(pend.fw_update_running(mark))


FRAME = 1 / 264.0  # s: duo2-touchd's tablet device with the tip down


def polyline(points, speed):
    """Points (mm) along a polyline at `speed` mm/s, one per frame."""
    out, carry, step = [], 0.0, speed * FRAME
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        d = carry
        while d < seg:
            out.append((x0 + (x1 - x0) * d / seg, y0 + (y1 - y0) * d / seg))
            d += step
        carry = d - seg
    return out


def straight(length, speed):
    return polyline([(0.0, 0.0), (length, 0.0)], speed)


def zigzag(length, leg, speed, turn=120.0):
    """Legs of `leg` mm, each turning by `turn` degrees from the last."""
    half = math.radians(turn / 2.0)  # each leg this far off the line's axis
    dx, dy = leg * math.cos(half), leg * math.sin(half)
    pts = [(0.0, 0.0)]
    for i in range(int(length / leg) + 1):
        pts.append((pts[-1][0] + dx, dy if i % 2 == 0 else 0.0))
    return polyline(pts, speed)


def stroke(feel, positions, seed=7, pressure=None, t0=50.0, w=None):
    """-> [(t, kind)]: the ticks of one stroke (touch, the frames, the lift)."""
    w = w or pend.WritingFeel(seed)
    ticks = []
    for i, pos in enumerate(positions):
        t = t0 + i * FRAME
        kind = w.step(feel, t, True, pos, pressure)
        if kind:
            ticks.append((t, kind))
    lift = w.step(feel, t0 + len(positions) * FRAME, False, None)
    assert lift is None
    return ticks


def cv(values):
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1)) / mean


class WritingFeelTiming(unittest.TestCase):
    """The writing feel's ticks: only their timing (and which of two waveforms) can be
    felt, so that is what these pin down."""

    def test_the_feels(self):
        self.assertEqual(set(pend.TEXTURES), set(pend.FEELS))
        for name, f in pend.TEXTURES.items():
            for wave in (f.grain, f.accent):
                self.assertIn(wave, pend.WAVE_GAPS, name)
                self.assertIsNotNone(pend.ordinal(pend.DEFAULT_WAVEFORMS, wave), name)
            # the touch and the accents firmer than the grain (or as firm: the brush)
            self.assertGreaterEqual(pend.WAVE_GAPS[f.accent], pend.WAVE_GAPS[f.grain], name)
            self.assertGreater(f.spacing, 0)
            self.assertTrue(0 <= f.irregularity <= 1)
            self.assertTrue(f.turn is None or 0 < f.turn < 180)
        # each keeps the grain waveform it had: ink and pencil HOVER, marker and chisel
        # CLICK, brush PRESS
        self.assertEqual(
            [pend.TEXTURES[n].grain for n in ("ink", "pencil", "marker", "chisel", "brush")],
            [pend.WAVE_HOVER] * 2 + [pend.WAVE_CLICK] * 2 + [pend.WAVE_PRESS],
        )
        self.assertEqual(pend.TEXTURES["ink"].accent, pend.WAVE_CLICK)
        # never more than one report per connection event (45 ms)
        self.assertGreaterEqual(min(pend.WAVE_GAPS.values()), 0.045)
        self.assertLess(pend.TEXTURES["ink"].irregularity, pend.TEXTURES["pencil"].irregularity)

    def test_the_touch_a_tap_and_the_lift(self):
        ink = pend.TEXTURES["ink"]
        w = pend.WritingFeel(3)
        self.assertIsNone(w.step(ink, 1.0, False, (5.0, 5.0)))  # hovering
        self.assertEqual(w.step(ink, 1.01, True, (5.0, 5.0)), pend.TOUCH)
        # a short mark: 0.5 mm one way and 0.4 mm back (a sharp turn), no more ticks
        t, n = 1.01, 0
        for pos in polyline([(5.0, 5.0), (5.5, 5.0), (5.1, 5.05)], 5.0):
            t += FRAME
            n += w.step(ink, t, True, pos) is not None
        self.assertEqual(n, 0)
        self.assertLess(w.travel, pend.FEEL_STROKE_MIN)
        self.assertIsNone(w.step(ink, t + FRAME, False, (5.0, 5.0)))  # no tick at the lift
        self.assertFalse(w.down)
        # a re-touch inside the last tick's gap: none (not put off either)
        w2 = pend.WritingFeel(3)
        self.assertEqual(w2.step(ink, 2.0, True, (0.0, 0.0)), pend.TOUCH)
        w2.step(ink, 2.01, False, None)
        self.assertIsNone(w2.step(ink, 2.03, True, (0.0, 0.0)))
        self.assertIsNone(w2.step(ink, 2.2, True, (0.0, 0.0)))

    def test_held_still_is_quiet(self):
        # the digitizer's own noise (well under FEEL_JITTER) on a pen held still: no grain
        for name, f in pend.TEXTURES.items():
            pts = [(10.0 + 0.03 * math.sin(i), 10.0 + 0.03 * math.cos(i * 1.3)) for i in range(800)]
            self.assertEqual([k for _, k in stroke(f, pts)], [pend.TOUCH], name)

    def test_a_straight_line_is_no_metronome(self):
        # 200 mm at 15 mm/s: under the link's rate for every feel (no gap in the way)
        spread = {}
        for name, f in pend.TEXTURES.items():
            ticks = stroke(f, straight(200.0, 15.0))
            grains = [t for t, k in ticks if k == pend.GRAIN]
            self.assertEqual([k for _, k in ticks].count(pend.ACCENT), 0, name)  # no turn
            gaps = [b - a for a, b in zip(grains, grains[1:])]
            self.assertGreater(len(gaps), 20, name)
            spread[name] = cv(gaps)
            self.assertGreater(spread[name], 0.15, name)  # a metronome: 0
            # no interval (in frames) a quarter of them or more
            frames = [round(g / FRAME) for g in gaps]
            self.assertLess(max(frames.count(n) for n in frames), 0.25 * len(frames), name)
            # the mean spacing is the feel's (within the draw's spread)
            per_mm = len(grains) / 200.0
            self.assertAlmostEqual(per_mm * f.spacing, 1.0, delta=0.25, msg=name)
        self.assertGreater(spread["pencil"], spread["ink"])  # the most uneven, the smoothest

    def test_a_zigzag_ticks_denser_than_a_straight_line(self):
        # 12 mm/s: under the link's rate (the pencil reaches it near 20 mm/s on a zigzag,
        # then the turns still tick firmer). The pencil's grain is already dense: its
        # turns mostly make ticks firmer, the others' make them denser too.
        for name, f in pend.TEXTURES.items():
            line = stroke(f, straight(80.0, 12.0))
            zig = stroke(f, zigzag(80.0, 2.0, 12.0))
            self.assertGreater(len(zig), (1.1 if name == "pencil" else 1.3) * len(line), name)
            self.assertGreater([k for _, k in zig].count(pend.ACCENT), 25, name)
        # a gentler zigzag (60-degree turns): the pencil, marker and chisel feel it, the
        # ink and the brush do not
        for name in ("pencil", "marker", "chisel", "ink", "brush"):
            zig = stroke(pend.TEXTURES[name], zigzag(80.0, 2.5, 12.0, turn=70.0))
            accents = [k for _, k in zig].count(pend.ACCENT)
            if name in ("ink", "brush"):
                self.assertEqual(accents, 0, name)
            else:
                self.assertGreater(accents, 10, name)

    def test_accents_come_just_after_the_corners(self):
        # a staircase: ten right angles 6 mm apart at 20 mm/s
        pts = [(0.0, 0.0)]
        for i in range(10):
            pts.append(
                (pts[-1][0] + 6.0, pts[-1][1]) if i % 2 == 0 else (pts[-1][0], pts[-1][1] + 6.0)
            )
        pts.append((pts[-1][0] + 6.0, pts[-1][1]))
        corners = [50.0 + 6.0 * (i + 1) / 20.0 for i in range(10)]
        for seed in range(1, 6):  # (most corners for any seed: a grain tick just before one
            # can hold its accent back past FEEL_ACCENT_HOLD)
            ticks = stroke(pend.TEXTURES["marker"], polyline(pts, 20.0), seed=seed)
            accents = [t for t, k in ticks if k == pend.ACCENT]
            self.assertGreaterEqual(len(accents), 8, seed)
            for t in accents:  # each within 2 mm past a corner
                self.assertTrue(any(0 < t - c < 2.0 / 20.0 for c in corners), (seed, t))
            # the brush: a right angle is no accent ...
            ticks = stroke(pend.TEXTURES["brush"], polyline(pts, 20.0), seed=seed)
            self.assertNotIn(pend.ACCENT, [k for _, k in ticks])
        # ... a hairpin is
        hairpins = zigzag(60.0, 6.0, 20.0, turn=160.0)
        for seed in range(1, 6):
            ticks = stroke(pend.TEXTURES["brush"], hairpins, seed=seed)
            self.assertGreaterEqual([k for _, k in ticks].count(pend.ACCENT), 7, seed)

    def test_never_closer_than_the_gap(self):
        # a wild scribble: random headings and speeds from a crawl to 250 mm/s, the pen
        # lifted now and then
        rng = random.Random(11)
        for name, f in pend.TEXTURES.items():
            w = pend.WritingFeel(5)
            t, x, y, heading, ticks = 0.0, 50.0, 50.0, 0.0, []
            for _ in range(20000):
                t += FRAME * rng.uniform(0.5, 1.5)
                if rng.random() < 0.002:
                    w.step(f, t, False, None)
                    continue
                heading += rng.gauss(0.0, 0.5)
                v = rng.choice((2.0, 20.0, 80.0, 250.0))
                x += v * FRAME * math.cos(heading)
                y += v * FRAME * math.sin(heading)
                kind = w.step(f, t, True, (x, y), rng.random())
                if kind:
                    ticks.append((t, f.grain if kind == pend.GRAIN else f.accent))
            self.assertGreater(len(ticks), 50, name)
            for (ta, wave), (tb, _) in zip(ticks, ticks[1:]):
                self.assertGreaterEqual(tb - ta, pend.WAVE_GAPS[wave] - 1e-9, name)

    def test_a_fast_stroke_saturates_unevenly(self):
        # 150 mm/s: the gap is the limit; the ticks still uneven, about the link's rate
        pencil = pend.TEXTURES["pencil"]
        ticks = stroke(pencil, straight(150.0, 150.0))
        times = [t for t, _ in ticks]
        rate = (len(times) - 1) / (times[-1] - times[0])
        self.assertLess(rate, 1 / pend.WAVE_GAPS[pend.WAVE_HOVER])
        self.assertGreater(rate, 14.0)
        self.assertGreater(cv([b - a for a, b in zip(times, times[1:])]), 0.08)

    def test_slow_is_sparse(self):
        for name in ("ink", "marker"):
            f = pend.TEXTURES[name]
            slow = stroke(f, straight(60.0, 2.5))  # 24 s
            normal = stroke(f, straight(60.0, 15.0))
            self.assertLess(len(slow), 0.6 * len(normal), name)
            self.assertLess((len(slow) - 1) / 24.0, 0.6, name)  # ticks a second

    def test_pressure_mildly_denser(self):
        ink = pend.TEXTURES["ink"]
        self.assertEqual(pend.grain_scale(20.0, None), 1.0)
        self.assertAlmostEqual(pend.grain_scale(20.0, pend.FEEL_PRESSURE_REF), 1.0)
        self.assertEqual(pend.grain_scale(20.0, 1.0), 0.8)  # clamped
        self.assertAlmostEqual(pend.grain_scale(20.0, 0.0), 1.14)
        self.assertAlmostEqual(pend.grain_scale(4.0, None), 2.0)  # slow: wider
        self.assertAlmostEqual(pend.grain_scale(0.0, None), 16.0)
        light = stroke(ink, straight(300.0, 15.0), pressure=0.05)
        hard = stroke(ink, straight(300.0, 15.0), pressure=0.9)
        self.assertGreater(len(hard), 1.15 * len(light))
        self.assertLess(len(hard), 1.6 * len(light))

    def test_deterministic_for_a_seed(self):
        pencil = pend.TEXTURES["pencil"]
        path = zigzag(40.0, 3.0, 25.0) + straight(40.0, 30.0)
        a = stroke(pencil, path, seed=42)
        self.assertEqual(a, stroke(pencil, path, seed=42))
        self.assertNotEqual(a, stroke(pencil, path, seed=43))
        # the generator: never 0, all 32 bits, a small seed spread out
        x = pend.mix32(1)
        self.assertGreater(x, 1 << 24)
        for _ in range(1000):
            x = pend.xorshift32(x)
            self.assertTrue(0 < x < 1 << 32)


class HapticsLoop(unittest.TestCase):
    """The writing feel's pulses never wait on the pen, follow its connections and
    say why they were held back; the reads that may block run as chores."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.saved = (pend.PEN_HID_GLOB, pend.FW_MARK, pend.PEN_CONF, pend.log)
        self.lines = []
        pend.log = self.lines.append
        pend.FW_MARK = os.path.join(self.d.name, "no-mark")
        pend.PEN_CONF = os.path.join(self.d.name, "pen.conf")
        with open(pend.PEN_CONF, "w") as f:
            f.write("writing_feel = ink\nfeel_strength = 100\n")
        self.hids = os.path.join(self.d.name, "hid")
        os.mkdir(self.hids)
        pend.PEN_HID_GLOB = os.path.join(self.hids, "0005:045E:0C0F.*")
        self.node = os.path.join(self.d.name, "hidraw1")
        open(self.node, "w").close()

    def tearDown(self):
        pend.PEN_HID_GLOB, pend.FW_MARK, pend.PEN_CONF, pend.log = self.saved
        self.d.cleanup()

    def haptics(self):
        h = pend.Haptics(chores=pend.Chores(threaded=False))
        h.hidraw = lambda: self.node if h.hid else None
        h.read_conf()
        return h

    def connect(self, seq):
        os.mkdir(os.path.join(self.hids, "0005:045E:0C0F.%04X" % seq))

    def test_newest_hid(self):
        self.assertEqual(
            pend.newest_hid(
                ["/x/0005:045E:0C0F.00FF", "/x/0005:045E:0C0F.0100", "/x/0005:045E:0C0F.00A0"]
            ),
            "/x/0005:045E:0C0F.0100",
        )
        self.assertIsNone(pend.newest_hid([]))

    def test_pulses_follow_the_connections_and_say_why_not(self):
        h = self.haptics()
        wave = pend.WAVE_HOVER
        h.pulse(wave, 10.0)  # no pen yet
        self.assertEqual(h.stats.no_link, 1)
        self.connect(0x64)
        self.assertTrue(h.follow_hid(20.0))
        h.pulse(wave, 20.5)  # the link settling
        self.assertEqual(h.stats.settling, 1)
        h.pulse(wave, 21.2)
        h.pulse(wave, 21.3)
        self.assertEqual(h.stats.sent, 2)
        with open(self.node, "rb") as f:
            self.assertEqual(f.read(), pend.manual_report(6, 255) * 2)
        self.assertIn("haptics: writing feel pulses (ink) to the pen", self.lines)
        # the pen comes near the screen: the link drops and is back with a new device
        h.link_changed(False)
        h.pulse(wave, 21.4)
        self.assertEqual(h.stats.no_link, 1)
        h.link_changed(True)
        os.rmdir(os.path.join(self.hids, "0005:045E:0C0F.0064"))
        self.connect(0x65)
        self.assertTrue(h.follow_hid(21.6))
        self.assertTrue(
            any(
                l.startswith(
                    "haptics: pen link 1.6 s, writing-feel pulses: 2 sent, "
                    "1 held back while the link settled, 1 with no link"
                )
                for l in self.lines
            ),
            self.lines,
        )
        self.assertFalse(h.follow_hid(21.7))  # the same device: nothing new
        h.pulse(wave, 22.7)
        self.assertEqual(h.stats.sent, 1)
        # a write that fails: counted with its error, and the next device looked for
        os.remove(self.node)
        h.raw and h.close_raw()
        h.hidraw = lambda: os.path.join(self.d.name, "gone", "hidraw1")
        h.pulse(wave, 22.8)
        self.assertEqual(h.stats.failed, 1)
        self.assertTrue(h.stats.error)
        h.close_raw()

    def test_frames_to_pulses(self):
        # marker: the touch and the turns PRESS (ordinal 4), the grain CLICK (ordinal 3)
        with open(pend.PEN_CONF, "w") as f:
            f.write("writing_feel = marker\nfeel_strength = 100\n")
        h = self.haptics()
        h.writing = pend.WritingFeel(9)
        self.connect(0x64)
        h.follow_hid(0.0)
        frames = [(5.0 + i * FRAME, True, p, 0.4) for i, p in enumerate(straight(40.0, 20.0))]
        for fr in frames:  # each frame read as it comes
            h.feel_frames([fr], fr[0] + 0.002)
        with open(self.node, "rb") as f:
            sent = f.read()
        reports = [sent[i : i + 7] for i in range(0, len(sent), 7)]
        self.assertEqual(reports[0], pend.manual_report(4, 255))  # the touch
        self.assertGreater(len(reports), 6)
        self.assertEqual(set(reports[1:]), {pend.manual_report(3, 255)})  # the grain, no turn
        self.assertEqual((h.stats.sent, h.stats.accents), (len(reports), 1))
        # a frame read too late: its tick dropped, not sent late
        h.writing.step(pend.TEXTURES["marker"], 9.0, False, None)
        h.feel_frames([(10.0, True, (0.0, 0.0), 0.4)], 10.0 + pend.FEEL_STALE + 0.01)
        self.assertEqual(h.stats.late, 1)
        # an app's pulse just now: the writing feel's waits its turn (dropped)
        h.writing.step(pend.TEXTURES["marker"], 10.5, False, None)
        h.last_pulse = 11.0
        h.feel_frames([(11.01, True, (0.0, 0.0), 0.4)], 11.01)
        self.assertEqual(h.stats.close, 1)
        # the feel switched off: the stroke ends, nothing sent
        with open(pend.PEN_CONF, "w") as f:
            f.write("writing_feel = off\n")
        os.utime(pend.PEN_CONF, ns=(1, 1))
        h.read_conf()
        n = h.stats.sent
        h.feel_frames([(12.0 + i * FRAME, True, (i * 0.1, 0.0), 0.4) for i in range(400)], 13.6)
        self.assertEqual(h.stats.sent, n)
        self.assertFalse(h.writing.down)

    def test_no_pulse_during_a_firmware_update(self):
        h = self.haptics()
        self.connect(0x64)
        h.follow_hid(0.0)
        with open(pend.FW_MARK, "w") as f:
            f.write("%d\n" % os.getpid())
        h.pulse(pend.WAVE_HOVER, 5.0)
        self.assertEqual((h.stats.updating, h.stats.sent), (1, 0))

    def test_chores_wait_for_a_settled_quiet_link_and_back_off(self):
        h = self.haptics()
        calls, asked = [], []

        def answer(node, data, size=None):
            calls.append((data[0], size))
            asked.append(data[0])
            if size is None:
                return True, 0.01, None
            if data[0] == pend.REPORT_WAVEFORMS:
                if asked.count(pend.REPORT_WAVEFORMS) == 1:
                    return None, 5.0, OSError(5, "Input/output error")  # uhid gave up
                return (
                    bytes([0x42]) + bytes(44) + struct.pack("<3H", *pend.DEFAULT_WAVEFORMS[:3]),
                    0.01,
                    None,
                )
            return bytes([0x41, 0, 255, 1, 0, 0]), 0.01, None

        saved = pend.feature_request
        pend.feature_request = answer
        try:
            self.connect(0x64)
            h.follow_hid(100.0)
            h.ask_chores(101.0)  # not yet settled
            self.assertEqual(calls, [])
            h.writing.down = True
            h.ask_chores(104.0)  # the tip is down: never while writing
            self.assertEqual(calls, [])
            h.writing.down = False
            h.last_pulse = 103.5
            h.ask_chores(104.0)  # a pulse half a second ago
            self.assertEqual(calls, [])
            h.ask_chores(105.0)
            self.assertEqual(calls, [(pend.REPORT_WAVEFORMS, 128)])
            self.assertTrue(any("did not answer feature 0x42" in l for l in self.lines))
            self.assertIsNone(h.waves)  # the built-in list meanwhile
            self.assertGreater(h.slow_until, 0)
            calls.clear()
            os.rmdir(os.path.join(self.hids, "0005:045E:0C0F.0064"))
            self.connect(0x65)
            h.follow_hid(110.0)
            h.ask_chores(115.0)  # backing off: nothing asked of the pen
            self.assertEqual(calls, [])
            h.slow_until = 0.0
            h.chores_asked = False
            h.fw_wanted = False
            h.ask_chores(116.0)
            self.assertEqual(calls[-2:], [(pend.REPORT_WAVEFORMS, 128), (pend.REPORT_HAPTIC, 16)])
            self.assertEqual(h.waves, pend.DEFAULT_WAVEFORMS[:3])
            self.assertTrue(h.auto_checked)  # it was at NONE: nothing written
            calls.clear()
            os.rmdir(os.path.join(self.hids, "0005:045E:0C0F.0065"))
            self.connect(0x66)
            h.follow_hid(120.0)
            h.ask_chores(125.0)  # all known: the next connections ask nothing
            self.assertEqual(calls, [])
        finally:
            pend.feature_request = saved

    def test_app_pulses(self):
        h = self.haptics()
        click = pend.APP_WAVES["click"]
        h.app_pulse(click, 200, 10.0)  # no pen yet
        self.connect(0x64)
        h.follow_hid(20.0)
        h.app_pulse(click, 200, 20.5)  # the link settling
        h.app_pulse(click, 200, 21.2)
        h.app_pulse(click, 90, 21.22)  # too close to the last
        h.app_pulse(pend.APP_WAVES["success"], 255, 21.3)
        with open(self.node, "rb") as f:
            self.assertEqual(f.read(), pend.manual_report(3, 200) + pend.manual_report(7, 255))
        self.assertEqual(
            h.stats.app, {"sent": 2, "too close together": 1, "held back while the link settled": 1}
        )
        h.follow_hid(21.3)
        os.rmdir(os.path.join(self.hids, "0005:045E:0C0F.0064"))
        self.connect(0x65)
        h.follow_hid(22.0)
        self.assertTrue(
            any(
                l.startswith(
                    "haptics: pen link 2.0 s, app pulses: 2 sent, 1 too close together, "
                    "1 held back while the link settled"
                )
                for l in self.lines
            ),
            self.lines,
        )
        # a writing-feel pulse just now holds the app's back too (one pulse at a time)
        h.last_pulse = 30.0
        h.app_pulse(click, 100, 30.01)
        self.assertEqual(h.stats.app, {"too close together": 1})

    def test_app_pulse_budget(self):
        h = self.haptics()
        click = pend.APP_WAVES["click"]
        self.connect(0x64)
        h.follow_hid(10.0)
        t = 20.0
        for _ in range(40):  # one every 50 ms for 2 s: past APP_GAP, over the budget
            h.app_pulse(click, 100, t)
            t += 0.05
        sent = h.stats.app.get("sent", 0)
        # the burst, then APP_RATE a second
        self.assertLessEqual(sent, pend.APP_BURST + 2 * pend.APP_RATE + 1)
        self.assertGreaterEqual(sent, pend.APP_BURST)
        self.assertGreater(h.stats.app.get("over the budget", 0), 0)
        h.app_pulse(click, 100, t + 10.0)  # a pause refills it
        self.assertEqual(h.stats.app["sent"], sent + 1)

    def test_summary_text(self):
        st = pend.LinkStats(1.0)
        self.assertIsNone(st.summary(2.0))
        st.failed, st.error = 2, "No such device"
        self.assertEqual(
            st.summary(3.5),
            "haptics: pen link 2.5 s, writing-feel pulses: 2 failed (last error: No such device)",
        )
        st = pend.LinkStats(1.0)
        st.sent, st.accents, st.late, st.close = 20, 4, 2, 1
        self.assertEqual(
            st.summary(2.0),
            "haptics: pen link 1.0 s, writing-feel pulses: 20 sent (4 of them accents), "
            "2 dropped as late, 1 too close to another",
        )
        gap = pend.LinkStats(1.0, linked=False)
        gap.no_link = 3
        self.assertEqual(
            gap.summary(1.5), "haptics: no pen link for 0.5 s, writing-feel pulses: 3 with no link"
        )


class AppHaptics(unittest.TestCase):
    """The apps' socket: what a request may say, who may send one."""

    def test_requests(self):
        self.assertEqual(pend.app_request(b"pulse click 200"), (pend.WAVE_CLICK, 200))
        self.assertEqual(pend.app_request(b"pulse success 1\n"), (0x1009, 1))
        self.assertEqual(pend.app_request(b"pulse hover 255"), (pend.WAVE_HOVER, 255))
        for bad in (
            b"",
            b"pulse click",
            b"pulse click 0",
            b"pulse click 256",
            b"pulse ink 100",
            b"pulse eraser 100",
            b"pulse CLICK 100",
            b"pulse click -5",
            b"pulse click 1e2",
            b"buzz click 100",
            b"pulse click 100 now",
            b"pulse \xff 100",
            b"x" * 100,
        ):
            self.assertIsNone(pend.app_request(bad), bad)

    def test_conf(self):
        self.assertEqual(pend.app_conf(""), (True, None))
        self.assertEqual(pend.app_conf("app_haptics = off\n"), (False, None))
        self.assertEqual(
            pend.app_conf("app_haptics = maybe\napp_haptics_group = games # them\n"),
            (True, "games"),
        )

    def test_socket(self):
        with tempfile.TemporaryDirectory() as d:
            session = os.path.join(d, "duo-wc")
            os.mkdir(session)
            path = os.path.join(d, "duo2-pen", "haptics")
            lines = []
            saved = pend.log
            pend.log = lines.append
            try:
                a = pend.AppSocket(path, session, owner=os.getuid())
                a.look(0.0, True, None)
                st = os.stat(path)
                self.assertEqual((st.st_mode & 0o777, st.st_gid), (0o660, os.stat(session).st_gid))
                self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o711)
                c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                c.sendto(b"pulse press 120", path)
                c.sendto(b"rm -rf /", path)
                self.assertEqual(a.requests(), [(pend.WAVE_PRESS, 120), None])
                self.assertEqual(a.requests(), [])
                a.look(1.0, True, None)  # not yet time to look again
                a.look(6.0, True, None)  # all as it should be: kept
                c.sendto(b"pulse click 9", path)
                self.assertEqual(a.requests(), [(pend.WAVE_CLICK, 9)])
                # a flood: a full read, then the socket rests
                c.setblocking(False)
                sent = 0
                for _ in range(500):
                    try:
                        c.sendto(b"pulse click 9", path)
                        sent += 1
                    except BlockingIOError:
                        break
                self.assertLess(sent, 500)  # the small buffer pushes back
                self.assertEqual(len(a.requests(7.0)), pend.APP_READ_MAX)
                self.assertIsNone(a.fileno(7.0 + pend.APP_FLOOD_PAUSE / 2))
                self.assertIsNotNone(a.fileno(7.0 + pend.APP_FLOOD_PAUSE))
                while a.requests(8.0):
                    pass
                a.look(12.0, True, "no-such-group-here")
                self.assertFalse(os.path.exists(path))
                self.assertIsNone(a.fileno())
                a.look(18.0, True, None)
                self.assertTrue(os.path.exists(path))
                a.look(24.0, False, None)  # app_haptics = off
                self.assertFalse(os.path.exists(path))
                c.close()
            finally:
                pend.log = saved
            self.assertIn("haptics: app pulses off (pen.conf)", lines)


class FirmwareCache(unittest.TestCase):
    def test_versions_go_to_the_cache(self):
        with tempfile.TemporaryDirectory() as d:
            tool = os.path.join(d, "pen-fw.py")
            with open(tool, "w") as f:
                f.write(
                    "import json, sys\nassert sys.argv[1:] == ['version', '--json']\n"
                    "print(json.dumps({'read_at': 1, 'components': [{'name': 'BLE', 'version': '5.148.139', "
                    "'component': 17, 'raw': 83924107, 'bank': 1}]}))\n"
                )
            cache = os.path.join(d, "cache", "firmware.json")
            old_mark = pend.FW_MARK
            pend.FW_MARK = os.path.join(d, "no-mark")
            try:
                self.assertTrue(pend.cache_firmware_versions(tool, cache))
            finally:
                pend.FW_MARK = old_mark
            import json

            with open(cache) as f:
                self.assertEqual(json.load(f)["components"][0]["version"], "5.148.139")
            self.assertEqual(os.stat(cache).st_mode & 0o777, 0o644)
            # a failing tool: nothing written, no exception
            with open(tool, "w") as f:
                f.write("import sys\nsys.exit(1)\n")
            os.remove(cache)
            self.assertFalse(pend.cache_firmware_versions(tool, cache))
            self.assertFalse(os.path.exists(cache))
            self.assertFalse(pend.cache_firmware_versions(os.path.join(d, "none.py"), cache))


if __name__ == "__main__":
    unittest.main()
