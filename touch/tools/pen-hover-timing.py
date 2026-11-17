#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-hover-timing: how evenly the pen's reports reach the system while it
hovers and writes, and whether the pen's Bluetooth link coming and going
lines up with gaps. Diagnosis only, read-only: the two tablet devices of
duo2-touchd ("Surface Duo 2 Pen Left/Right") are read without a grab, and
the pen's Bluetooth HID device is only looked for in sysfs.

    sudo pen-hover-timing.py [--seconds N] [--out FILE] [--start-on-pen S --started FILE]
                             [--no-libinput]

With --start-on-pen, the measurement starts when the pen first comes into
range (at most S seconds of waiting), and FILE is written then: a wrapper
can start other captures on the same cue.

Two kinds of interval, per mode (hover, touch):
  - positions: between frames that carry a new position, what moves the
    cursor (before duo2-touchd sent MSC_TIMESTAMP every frame, nearly every
    frame did: the kernel drops a frame with no changed value);
  - frames: between any two frames, what libinput's proximity timer sees: a
    pen in range that sends nothing for 50 ms is taken out of proximity by
    libinput until it has once seen the pen really leave.
And libinput's own view: `libinput debug-events` on each pen device (a fresh
libinput context, so its proximity timer is armed as after a boot) is read
for its proximity events only; the ones the device did not send are the
ones libinput made up. --no-libinput leaves that out.

No position is stored: only intervals, per-report motion statistics
(millimetres, as magnitudes), proximity changes and link changes, with
wall-clock times (they match the journal and pen-bt-trace's timeline).
libinput's output (which has positions) is read line by line and only the
proximity events are kept, as times.
"""

import argparse
import datetime
import fcntl
import glob
import math
import os
import select
import shutil
import signal
import struct
import subprocess
import threading
import time

EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0, 1, 3, 4
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH = 0x140, 0x141, 0x14A
ABS_X, ABS_Y = 0, 1
EVENT = struct.Struct("llHHi")  # struct input_event on a 64-bit kernel
LIBINPUT_TIMEOUT_MS = 50  # evdev-tablet.c FORCED_PROXOUT_TIMEOUT


def eviocgabs(code):
    return (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + code)


def stamp(t):
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]


def pct(v, p):
    if not v:
        return float("nan")
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def pen_devices():
    out = []
    for d in sorted(glob.glob("/sys/class/input/event*")):
        try:
            name = open(d + "/device/name").read().strip()
        except OSError:
            continue
        if name.startswith("Surface Duo 2 Pen"):
            out.append(("/dev/input/" + os.path.basename(d), name))
    return out


class Link(threading.Thread):
    """The pen's Bluetooth HID device present or not, every 5 ms."""

    def __init__(self, stop):
        super().__init__(daemon=True)
        self.stop = stop
        self.changes = []  # (time, up)

    def run(self):
        last = None
        while not self.stop.is_set():
            up = bool(glob.glob("/sys/bus/hid/devices/0005:045E:0C0F.*"))
            if up != last:
                self.changes.append((time.time(), up))
                last = up
            time.sleep(0.005)


def libinput_proximity(line):
    """'in' / 'out' for a `libinput debug-events` proximity line, else None."""
    if "TABLET_TOOL_PROXIMITY" not in line:
        return None
    if "proximity-out" in line:
        return "out"
    if "proximity-in" in line:
        return "in"
    return None


class Libinput(threading.Thread):
    """`libinput debug-events --device PATH`: the times of its proximity events."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.changes = []  # (time, "in" | "out")
        self.proc = subprocess.Popen(
            ["libinput", "debug-events", "--device", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def run(self):
        for line in self.proc.stdout:
            state = libinput_proximity(line)
            if state:
                self.changes.append((time.time(), state))

    def stop(self):
        # debug-events flushes after every dispatch and ends cleanly on SIGINT
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class Track:
    """One pen device's frames (pure: fed (time, type, code, value) events)."""

    def __init__(self, res_x=1, res_y=1):
        self.res = {ABS_X: res_x, ABS_Y: res_y}  # units per mm
        self.prox = False
        self.touch = False
        self.x = self.y = None
        self.pos_dirty = False
        self.last_pos_t = None  # the last frame with a new position
        self.last_frame_t = None  # the last frame of any kind
        self.prev = []  # the last two positions in mm (for the second difference)
        self.iv = {"hover": [], "touch": []}  # between new positions, ms
        self.fiv = {"hover": [], "touch": []}  # between any frames, ms
        self.jerk = {"hover": [], "touch": []}
        self.step = {"hover": [], "touch": []}
        self.still = {"hover": 0, "touch": 0}  # frames without a new position
        self.gaps = []  # (time, ms, mode): positions more than 20 ms apart
        self.frame_gaps = []  # (time, ms, mode): frames more than 50 ms apart
        self.prox_changes = []

    def event(self, t, typ, code, val):
        if typ == EV_KEY and code in (BTN_TOOL_PEN, BTN_TOOL_RUBBER):
            p = bool(val)
            if p != self.prox:
                self.prox = p
                self.prox_changes.append((t, p))
                self.last_pos_t = self.last_frame_t = None
                self.prev = []
        elif typ == EV_KEY and code == BTN_TOUCH:
            self.touch = bool(val)
        elif typ == EV_ABS and code == ABS_X:
            self.x = val
            self.pos_dirty = True
        elif typ == EV_ABS and code == ABS_Y:
            self.y = val
            self.pos_dirty = True
        elif typ == EV_SYN and code == 0:
            self.frame(t)

    def feed(self, data):
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            sec, usec, typ, code, val = EVENT.unpack_from(data, off)
            self.event(sec + usec / 1e6, typ, code, val)

    def frame(self, t):
        if not self.prox or self.x is None:
            self.pos_dirty = False
            return
        mode = "touch" if self.touch else "hover"
        if self.last_frame_t is not None:
            ms = (t - self.last_frame_t) * 1000
            self.fiv[mode].append(ms)
            if ms > LIBINPUT_TIMEOUT_MS:
                self.frame_gaps.append((t, ms, mode))
        self.last_frame_t = t
        if not self.pos_dirty:
            self.still[mode] += 1
            return
        self.pos_dirty = False
        if self.last_pos_t is not None:
            ms = (t - self.last_pos_t) * 1000
            self.iv[mode].append(ms)
            if ms > 20:
                self.gaps.append((t, ms, mode))
        self.last_pos_t = t
        pos = (self.x / self.res[ABS_X], self.y / self.res[ABS_Y])
        if self.prev:
            a = self.prev[-1]
            self.step[mode].append(math.hypot(pos[0] - a[0], pos[1] - a[1]))
        if len(self.prev) == 2:
            a, b = self.prev
            self.jerk[mode].append(math.hypot(pos[0] - 2 * b[0] + a[0], pos[1] - 2 * b[1] + a[1]))
        self.prev = (self.prev + [pos])[-2:]

    def clear(self):
        """Forget the statistics (the waiting for the pen is not measured)."""
        for k in self.iv:
            self.iv[k].clear()
            self.fiv[k].clear()
            self.jerk[k].clear()
            self.step[k].clear()
            self.still[k] = 0
        self.gaps.clear()
        self.frame_gaps.clear()

    def report(self, near_link=lambda t: ""):
        lines = []
        for mode in ("hover", "touch"):
            iv, fiv = self.iv[mode], self.fiv[mode]
            if iv:
                n, total = len(iv), sum(iv)
                lines.append(
                    "  %s: %d new positions, %.1f s, rate %.0f/s; interval ms p50 %.2f p90 %.2f"
                    " p99 %.2f max %.1f; >12 ms %d, >20 ms %d, >50 ms %d"
                    % (
                        mode,
                        n,
                        total / 1000,
                        n / (total / 1000) if total else 0,
                        pct(iv, 50),
                        pct(iv, 90),
                        pct(iv, 99),
                        max(iv),
                        sum(x > 12 for x in iv),
                        sum(x > 20 for x in iv),
                        sum(x > 50 for x in iv),
                    )
                )
            if fiv:
                lines.append(
                    "  %s frames (libinput's timer): %d, %d without a new position; interval ms"
                    " p99 %.2f max %.1f; >%d ms %d"
                    % (
                        mode,
                        len(fiv),
                        self.still[mode],
                        pct(fiv, 99),
                        max(fiv),
                        LIBINPUT_TIMEOUT_MS,
                        sum(x > LIBINPUT_TIMEOUT_MS for x in fiv),
                    )
                )
            st, jk = self.step[mode], self.jerk[mode]
            if jk:
                lines.append(
                    "  %s motion: step mm p50 %.3f p90 %.3f p99 %.3f max %.2f;"
                    " second difference mm p50 %.3f p90 %.3f p99 %.3f max %.2f"
                    % (
                        mode,
                        pct(st, 50),
                        pct(st, 90),
                        pct(st, 99),
                        max(st),
                        pct(jk, 50),
                        pct(jk, 90),
                        pct(jk, 99),
                        max(jk),
                    )
                )
        for t, ms, mode in self.frame_gaps[:20]:
            lines.append(
                "  frame gap %s %.1f ms (%s): libinput's timer runs out" % (stamp(t), ms, mode)
            )
        if len(self.frame_gaps) > 20:
            lines.append("  ... %d frame gaps in all" % len(self.frame_gaps))
        for t, ms, mode in self.gaps[:60]:
            lines.append("  gap %s %.1f ms (%s), %s" % (stamp(t), ms, mode, near_link(t)))
        if len(self.gaps) > 60:
            lines.append("  ... %d gaps in all" % len(self.gaps))
        return lines


def libinput_summary(changes, device_changes):
    """libinput's proximity events against the device's own: (ins, outs, made-up outs)."""
    ins = sum(s == "in" for _, s in changes)
    outs = sum(s == "out" for _, s in changes)
    real_outs = sum(not p for _, p in device_changes)
    return ins, outs, max(0, outs - real_outs)


class Dev:
    def __init__(self, path, name):
        self.path, self.name = path, name.replace("Surface Duo 2 ", "")
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        res = {}
        for c in (ABS_X, ABS_Y):
            buf = bytearray(24)
            fcntl.ioctl(self.fd, eviocgabs(c), buf)
            r = struct.unpack("6i", buf)[5]
            res[c] = r if r > 0 else 1
        self.track = Track(res[ABS_X], res[ABS_Y])
        self.libinput = None

    def read(self):
        try:
            self.track.feed(os.read(self.fd, 24 * 64))
        except BlockingIOError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--out", default="/run/duo2-penbt/hover.txt")
    ap.add_argument("--start-on-pen", type=int, default=0)
    ap.add_argument("--started")
    ap.add_argument("--no-libinput", action="store_true")
    a = ap.parse_args()
    devs = [Dev(p, n) for p, n in pen_devices()]
    if not devs:
        raise SystemExit("no pen tablet devices")
    if not a.no_libinput and shutil.which("libinput"):
        for d in devs:  # before the pen comes: a fresh context, as after a boot
            d.libinput = Libinput(d.path)
            d.libinput.start()
    if a.start_on_pen:
        until = time.time() + a.start_on_pen
        while time.time() < until and not any(d.track.prox for d in devs):
            r, _, _ = select.select([d.fd for d in devs], [], [], 0.5)
            for d in devs:
                if d.fd in r:
                    d.read()
        if not any(d.track.prox for d in devs):
            for d in devs:
                if d.libinput:
                    d.libinput.stop()
            raise SystemExit("the pen never came into range")
        for d in devs:
            d.track.clear()
    if a.started:
        with open(a.started, "w") as f:
            f.write(stamp(time.time()) + "\n")
    stop = threading.Event()
    link = Link(stop)
    link.start()
    t0 = time.time()
    end = t0 + a.seconds
    by_fd = {d.fd: d for d in devs}
    while time.time() < end:
        r, _, _ = select.select(list(by_fd), [], [], 0.5)
        for fd in r:
            by_fd[fd].read()
    stop.set()
    link.join(1)
    for d in devs:
        if d.libinput:
            d.libinput.stop()
            d.libinput.join(1)

    lines = ["pen-hover-timing %s .. %s" % (stamp(t0), stamp(end))]
    downs = [t for t, up in link.changes if not up]
    ups = [t for t, up in link.changes if up]
    lines.append(
        "Bluetooth HID link: %d down, %d up (%s)"
        % (len(downs), len(ups), " ".join(("+" if u else "-") + stamp(t) for t, u in link.changes))
    )

    def near_link(t):
        d = [t - x for x, _ in link.changes if -0.2 <= t - x <= 1.5]
        return ("link change %.0f ms before" % (min(d) * 1000)) if d else "no link change near"

    for d in devs:
        tr = d.track
        lines.append(
            "== %s (%s), resolution %d/%d units per mm"
            % (d.name, d.path, tr.res[ABS_X], tr.res[ABS_Y])
        )
        for t, p in tr.prox_changes:
            lines.append("  %s proximity %s" % (stamp(t), "in" if p else "out"))
        if d.libinput:
            ins, outs, made_up = libinput_summary(
                [c for c in d.libinput.changes if c[0] >= t0],
                [c for c in tr.prox_changes if c[0] >= t0],
            )
            lines.append(
                "  libinput: %d proximity in, %d out; %d outs the device did not send"
                % (ins, outs, made_up)
            )
        lines += tr.report(near_link)
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(a.out), mode=0o700, exist_ok=True)
    with open(a.out, "w") as f:
        f.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
