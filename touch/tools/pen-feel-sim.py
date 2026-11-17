#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-feel-sim: when the pen would tick, offline. Runs duo2-pend's writing-feel
timing (WritingFeel, the same code the daemon runs) over made-up strokes at the
tablet device's frame rate and prints the tick times: no pen, no phone, no
Bluetooth, nothing sent anywhere.

    pen-feel-sim.py [--feel NAME ...] [--case NAME ...] [--seed N] [--pressure P]
                    [--noise MM] [--pend PATH]

The strokes (--case, all by default):
  straight    a straight line, 60 mm at 30 mm/s
  zigzag      a zigzag as by hand, 2-3 mm legs (seeded), 120-degree turns, 40 mm at 25 mm/s
  slow-curve  a quarter circle of 20 mm radius at 4 mm/s
  fast        a straight line, 120 mm at 150 mm/s (the link's rate is the limit)

Each tick is printed as its time in ms from the touch: T the touch, A an
accent on a turn (both the feel's firmer waveform), a bare number the grain.
The summary line: ticks per mm, and the spread of the intervals between grain
ticks (cv: the standard deviation over the mean; a metronome is 0).

duo2-pend is looked for in this tree (rootfs/overlay/usr/local/sbin) and then
at /usr/local/sbin; --pend names it.
"""

import argparse
import importlib.machinery
import importlib.util
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PEND_PATHS = (
    os.path.join(HERE, "..", "..", "rootfs", "overlay", "usr", "local", "sbin", "duo2-pend"),
    "/usr/local/sbin/duo2-pend",
)
FRAME = 1 / 264.0  # s: duo2-touchd's tablet device with the tip down (pen_rules.h)
WAVE_NAMES = {0x1003: "CLICK", 0x1006: "PRESS", 0x1008: "HOVER"}


def load_pend(path=None):
    for p in [path] if path else PEND_PATHS:
        if p and os.path.exists(p):
            loader = importlib.machinery.SourceFileLoader("duo2_pend", p)
            spec = importlib.util.spec_from_loader("duo2_pend", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    sys.exit("pen-feel-sim: no duo2-pend (--pend PATH)")


def polyline(points, speed):
    """Points (mm) along a polyline at a constant speed (mm/s), one per frame."""
    out = []
    carry = 0.0
    step = speed * FRAME
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        d = carry
        while d < seg:
            out.append((x0 + (x1 - x0) * d / seg, y0 + (y1 - y0) * d / seg))
            d += step
        carry = d - seg
    out.append(points[-1])
    return out


def straight(length, speed):
    return polyline([(10.0, 10.0), (10.0 + length, 10.0)], speed)


def zigzag(length, leg, angle, speed, seed):
    """As by hand: each leg +-20 % of `leg` (a seeded generator), the turns 2 x `angle`."""
    rng = random.Random(seed)
    pts, x, y, sign, done = [(10.0, 10.0)], 10.0, 10.0, 1, 0.0
    while done < length:
        n = leg * rng.uniform(0.8, 1.2)
        x += n * math.cos(math.radians(angle))
        y += sign * n * math.sin(math.radians(angle))
        pts.append((x, y))
        sign, done = -sign, done + n
    return polyline(pts, speed)


def arc(radius, degrees, speed):
    n = max(2, int(radius * math.radians(degrees) / 0.05))
    pts = [
        (10.0 + radius * math.sin(math.radians(degrees) * i / n),
         10.0 + radius - radius * math.cos(math.radians(degrees) * i / n))
        for i in range(n + 1)
    ]  # fmt: skip
    return polyline(pts, speed)


CASES = {
    "straight": ("a straight line, 60 mm at 30 mm/s", lambda seed: straight(60.0, 30.0)),
    "zigzag": (
        "a zigzag, 2-3 mm legs, 120-degree turns, 25 mm/s",
        lambda seed: zigzag(40.0, 2.5, 60.0, 25.0, seed),
    ),
    "slow-curve": ("a quarter circle, 20 mm radius, 4 mm/s", lambda seed: arc(20.0, 90.0, 4.0)),
    "fast": ("a straight line, 120 mm at 150 mm/s", lambda seed: straight(120.0, 150.0)),
}


def run(pend, feel, positions, seed, pressure, noise):
    """-> [(ms from the touch, kind)], the stroke's length in mm."""
    w = pend.WritingFeel(seed)
    jitter = random.Random(seed)
    t0 = 100.0
    ticks = []
    for i, (x, y) in enumerate(positions):
        if noise:
            x, y = x + jitter.gauss(0.0, noise), y + jitter.gauss(0.0, noise)
        t = t0 + i * FRAME
        kind = w.step(feel, t, True, (x, y), pressure)
        if kind:
            ticks.append((round((t - t0) * 1000), kind))
    w.step(feel, t0 + len(positions) * FRAME, False, None)
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(positions, positions[1:]))
    return ticks, length


def describe(pend, ticks, length):
    marks = {pend.TOUCH: "T", pend.ACCENT: "A", pend.GRAIN: ""}
    times = " ".join("%d%s" % (ms, marks[k]) for ms, k in ticks)
    grains = [ms for ms, k in ticks if k == pend.GRAIN]
    gaps = [b - a for a, b in zip(grains, grains[1:])]
    line = "%d ticks over %.0f mm (%.2f /mm, %d accents)" % (
        len(ticks),
        length,
        len(ticks) / length if length else 0.0,
        sum(k == pend.ACCENT for _, k in ticks),
    )
    if len(gaps) >= 2:
        mean = sum(gaps) / len(gaps)
        sd = math.sqrt(sum((g - mean) ** 2 for g in gaps) / (len(gaps) - 1))
        line += ", grain intervals %d-%d ms, mean %.0f, cv %.2f" % (
            min(gaps),
            max(gaps),
            mean,
            sd / mean,
        )
    return line, times


def wrap(text, width, indent):
    out, cur = [], indent
    for word in text.split():
        if len(cur) + len(word) + 1 > width and cur.strip():
            out.append(cur)
            cur = indent
        cur += ("" if cur == indent else " ") + word
    if cur.strip():
        out.append(cur)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--feel", action="append", help="ink, pencil, marker, chisel, brush (all)")
    ap.add_argument("--case", action="append", choices=sorted(CASES), help="the strokes (all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pressure", type=float, default=None, help="0..1 of the range (none)")
    ap.add_argument("--noise", type=float, default=0.0, help="mm of position noise (sd)")
    ap.add_argument("--pend", help="duo2-pend to load")
    ap.add_argument("--width", type=int, default=100)
    args = ap.parse_args(argv)
    pend = load_pend(args.pend)
    feels = args.feel or list(pend.TEXTURES)
    for name in feels:
        if name not in pend.TEXTURES:
            ap.error("no feel %r" % name)
    for name in feels:
        f = pend.TEXTURES[name]
        print(
            "%s: grain %s every %.1f mm (irregularity %.2f), touch and turns %s, accents %s"
            % (
                name,
                WAVE_NAMES.get(f.grain, hex(f.grain)),
                f.spacing,
                f.irregularity,
                WAVE_NAMES.get(f.accent, hex(f.accent)),
                "past %d degrees" % f.turn if f.turn is not None else "none",
            )
        )
        for case in args.case or list(CASES):
            title, make = CASES[case]
            ticks, length = run(pend, f, make(args.seed), args.seed, args.pressure, args.noise)
            line, times = describe(pend, ticks, length)
            print("  %s: %s" % (title, line))
            for row in wrap(times, args.width, "    "):
                print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
