#!/usr/bin/python3
"""Synthetic touches on the phone, for testing without fingers (run as root).

    sudo tools/touch.py left tap 0.5 0.5
    sudo tools/touch.py right swipe 0.5 0.99 0.5 0.6 [ms]
    sudo tools/touch.py right swipehold 0.5 0.995 0.5 0.75 [ms] [hold-ms]
                                   (a swipe that stops and stays down: the overview)
    sudo tools/touch.py left hold 0.5 0.5 [ms]
    sudo tools/touch.py left liftdrag 0.25 0.5 0.5 0.1 [ms] [hold-ms] [end-hold-ms]
                                   (hold still first, then drag: picking up an overview card)
    sudo tools/touch.py left taps 0.1,0.9 0.2,0.9 ...      (several taps in a row)
    sudo tools/touch.py left dtap 0.5 0.08                  (a quick double tap)
    sudo tools/touch.py right tapdrag 0.5 0.3 0.7 0.3 [ms] [gap-ms]
                                   (a tap, then down again gap-ms (120) later and
                                    moved to x1 y1 over ms (400): a touchpad's
                                    tap-and-drag)
    sudo tools/touch.py left cross 0.5 0.07 0.985 0.4 right 0.015 0.42 0.4 0.4 [gap-ms] [hold-ms] [ms]
                                   (one drag over both panels: down on the first at
                                    x0 y0, after hold-ms (0: at once) to x1 y1, lift; gap-ms
                                    later down on the second at x2 y2, to x3 y3, lift;
                                    a negative gap puts the second down BEFORE the lift)

Positions are fractions of the panel (0..1, x right, y down, the panel's own
unrotated frame). The tool clones the duo2-touchd device of that panel
("Surface Duo 2 Touchscreen Left/Right") with evemu-device: the clone has the
same name, so udev gives it the same WL_OUTPUT and duo-wc maps it to the same
panel, and writes the touch into it event by event with the planned timing
(evemu-play delivered a whole tap at once, a zero-length touch no finger
makes, which GTK sometimes ignores). The clone is removed at the end. Needs
evemu-tools.
"""

import os
import re
import struct
import subprocess
import sys
import tempfile
import time

NAMES = {"left": "Surface Duo 2 Touchscreen Left", "right": "Surface Duo 2 Touchscreen Right"}
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14A


def find_device(name):
    for entry in sorted(os.listdir("/sys/class/input")):
        if not entry.startswith("event"):
            continue
        try:
            with open("/sys/class/input/%s/device/name" % entry) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + entry
        except OSError:
            continue
    raise SystemExit("no input device named %r" % name)


def ranges(description):
    """(max_x, max_y) of the MT position axes from an evemu description."""
    found = {}
    for line in description.splitlines():
        m = re.match(r"A: ([0-9a-f]+) (-?\d+) (-?\d+)", line)
        if m:
            found[int(m.group(1), 16)] = int(m.group(3))
    return found.get(ABS_MT_POSITION_X, found.get(ABS_X)), found.get(
        ABS_MT_POSITION_Y, found.get(ABS_Y)
    )


class Recording:
    def __init__(self, max_x, max_y):
        self.max_x, self.max_y = max_x, max_y
        self.t = 0.0
        self.lines = []
        self.tracking = 100

    def ev(self, kind, code, value):
        self.lines.append("E: %.6f %04x %04x %d" % (self.t, kind, code, value))

    def syn(self, dt_ms):
        self.ev(EV_SYN, 0, 0)
        self.t += dt_ms / 1000.0

    def pos(self, x, y):
        px, py = int(round(x * self.max_x)), int(round(y * self.max_y))
        self.ev(EV_ABS, ABS_MT_POSITION_X, px)
        self.ev(EV_ABS, ABS_MT_POSITION_Y, py)
        self.ev(EV_ABS, ABS_X, px)
        self.ev(EV_ABS, ABS_Y, py)

    def down(self, x, y, dt_ms=8):
        self.tracking += 1
        self.ev(EV_ABS, ABS_MT_SLOT, 0)
        self.ev(EV_ABS, ABS_MT_TRACKING_ID, self.tracking)
        self.pos(x, y)
        self.ev(EV_KEY, BTN_TOUCH, 1)
        self.syn(dt_ms)

    def move(self, x, y, dt_ms=8):
        self.pos(x, y)
        self.syn(dt_ms)

    def up(self, dt_ms=8):
        self.ev(EV_ABS, ABS_MT_TRACKING_ID, -1)
        self.ev(EV_KEY, BTN_TOUCH, 0)
        self.syn(dt_ms)

    def text(self):
        return "\n".join(self.lines) + "\n"


def plan(rec, action, args):
    f = [float(a) for a in args if re.match(r"^-?[0-9.]+$", a)]
    if action == "tap":
        rec.down(f[0], f[1], 60)
        rec.up(150)
    elif action == "hold":
        ms = f[2] if len(f) > 2 else 800
        rec.down(f[0], f[1], ms)
        rec.up(150)
    elif action == "swipe":
        x0, y0, x1, y1 = f[:4]
        ms = f[4] if len(f) > 4 else 250
        steps = max(4, int(ms / 12))
        rec.down(x0, y0, 12)
        for i in range(1, steps + 1):
            k = i / steps
            rec.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 12)
        rec.up(150)
    elif action == "swipehold":
        x0, y0, x1, y1 = f[:4]
        ms = f[4] if len(f) > 4 else 250
        hold = f[5] if len(f) > 5 else 600
        steps = max(4, int(ms / 12))
        rec.down(x0, y0, 12)
        for i in range(1, steps + 1):
            k = i / steps
            rec.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 12)
        # Still down, not moving: no events at all for a while.
        rec.t += hold / 1000.0
        rec.up(150)
    elif action == "liftdrag":
        x0, y0, x1, y1 = f[:4]
        ms = f[4] if len(f) > 4 else 300
        hold = f[5] if len(f) > 5 else 600
        end_hold = f[6] if len(f) > 6 else 0
        steps = max(4, int(ms / 12))
        rec.down(x0, y0, hold)
        for i in range(1, steps + 1):
            k = i / steps
            rec.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 12)
        rec.t += end_hold / 1000.0
        rec.up(150)
    elif action == "dtap":
        # A double tap: 50 ms down, 120 ms up, 50 ms down.
        rec.down(f[0], f[1], 50)
        rec.up(120)
        rec.down(f[0], f[1], 50)
        rec.up(150)
    elif action == "tapdrag":
        x0, y0, x1, y1 = f[:4]
        ms = f[4] if len(f) > 4 else 400
        gap = f[5] if len(f) > 5 else 120
        steps = max(4, int(ms / 12))
        rec.down(x0, y0, 60)
        rec.up(gap)
        rec.down(x0, y0, 40)
        for i in range(1, steps + 1):
            k = i / steps
            rec.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 12)
        rec.up(150)
    elif action == "taps":
        for pair in args:
            x, y = (float(v) for v in pair.split(","))
            rec.down(x, y, 60)
            rec.up(350)
    else:
        raise SystemExit(__doc__)


def play(node, lines):
    """Write the recording's events into the device node, sleeping between
    them as their times say (struct input_event: timeval, type, code, value;
    the kernel stamps the time itself)."""
    start = time.monotonic()
    with open(node, "wb", buffering=0) as dev:
        for line in lines:
            _e, t, kind, code, value = line.split()
            due = start + float(t)
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            dev.write(struct.pack("qqHHi", 0, 0, int(kind, 16), int(code, 16), int(value)))


def clone_device(panel):
    """An evemu clone of a panel's touchscreen: (process, node, max_x, max_y, desc)."""
    source = find_device(NAMES[panel])
    description = subprocess.run(
        ["evemu-describe", source], check=True, capture_output=True, text=True
    ).stdout
    max_x, max_y = ranges(description)
    with tempfile.NamedTemporaryFile("w", suffix=".desc", delete=False) as d:
        d.write(description)
        desc_path = d.name
    clone = subprocess.Popen(["evemu-device", desc_path], stdout=subprocess.PIPE, text=True)
    line = clone.stdout.readline()
    m = re.search(r"(/dev/input/event\d+)", line)
    if not m:
        clone.terminate()
        raise SystemExit("evemu-device said: %r" % line)
    return clone, m.group(1), max_x, max_y, desc_path


def cross(argv):
    """cross <panel> x0 y0 x1 y1 <panel> x2 y2 x3 y3 [gap-ms] [hold-ms] [ms]"""
    first, a = argv[0], [float(v) for v in argv[2:6]]
    second, b = argv[6], [float(v) for v in argv[7:11]]
    rest = [float(v) for v in argv[11:]]
    gap = rest[0] if rest else 60
    hold = rest[1] if len(rest) > 1 else 0
    ms = rest[2] if len(rest) > 2 else 300
    if first not in NAMES or second not in NAMES or first == second:
        raise SystemExit(cross.__doc__)
    clones = [clone_device(first), clone_device(second)]
    try:
        time.sleep(1.0)
        events = []  # (time, node, line)
        recs = []
        for (_clone, node, mx, my, _), (x0, y0, x1, y1) in (
            (clones[0], a),
            (clones[1], b),
        ):
            rec = Recording(mx, my)
            recs.append((rec, node, x0, y0, x1, y1))
        rec, node, x0, y0, x1, y1 = recs[0]
        steps = max(4, int(ms / 12))
        rec.down(x0, y0, max(hold, 12))
        for i in range(1, steps + 1):
            k = i / steps
            rec.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 12)
        lift_at = rec.t
        rec.up(10)
        events += [(float(ln.split()[1]), node, ln) for ln in rec.lines]
        rec2, node2, x2, y2, x3, y3 = recs[1]
        rec2.t = lift_at + gap / 1000.0
        rec2.down(x2, y2, 12)
        for i in range(1, steps + 1):
            k = i / steps
            rec2.move(x2 + (x3 - x2) * k, y2 + (y3 - y2) * k, 12)
        rec2.up(150)
        events += [(float(ln.split()[1]), node2, ln) for ln in rec2.lines]
        events.sort(key=lambda e: e[0])
        start = time.monotonic()
        devs = {n: open(n, "wb", buffering=0) for n in (node, node2)}
        try:
            for t, n, line in events:
                delay = start + t - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                _e, _t, kind, code, value = line.split()
                devs[n].write(struct.pack("qqHHi", 0, 0, int(kind, 16), int(code, 16), int(value)))
        finally:
            for d in devs.values():
                d.close()
        time.sleep(0.3)
    finally:
        for clone, _node, _x, _y, desc in clones:
            clone.terminate()
            clone.wait()
            os.unlink(desc)


def main():
    if len(sys.argv) >= 3 and sys.argv[2] == "cross":
        return cross(sys.argv[1:])
    if len(sys.argv) < 4 or sys.argv[1] not in NAMES:
        raise SystemExit(__doc__)
    panel, action, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    source = find_device(NAMES[panel])
    description = subprocess.run(
        ["evemu-describe", source], check=True, capture_output=True, text=True
    ).stdout
    max_x, max_y = ranges(description)
    rec = Recording(max_x, max_y)
    plan(rec, action, args)

    with tempfile.NamedTemporaryFile("w", suffix=".desc", delete=False) as d:
        d.write(description)
        desc_path = d.name
    clone = subprocess.Popen(["evemu-device", desc_path], stdout=subprocess.PIPE, text=True)
    try:
        line = clone.stdout.readline()
        m = re.search(r"(/dev/input/event\d+)", line)
        if not m:
            raise SystemExit("evemu-device said: %r" % line)
        node = m.group(1)
        time.sleep(1.0)  # udev + libinput pick the new device up
        play(node, rec.lines)
        time.sleep(0.3)
    finally:
        clone.terminate()
        clone.wait()
        os.unlink(desc_path)


if __name__ == "__main__":
    main()
