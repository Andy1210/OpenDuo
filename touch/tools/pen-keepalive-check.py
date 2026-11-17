#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-keepalive-check: does libinput keep a still, hovering pen in proximity?

    sudo pen-keepalive-check.py [l|r]

A scripted hover through duo2-touchd's control FIFO, on the right panel by
default: a report every 8 ms for 1.5 s, the position moving only every
150 ms (the reports in between repeat it, as the heat processor's do while
the pen hovers still), then "pen out". `libinput debug-events` on that
panel's pen device (a fresh context, so its 50 ms proximity timer is armed
as after a boot) counts the proximity events. One in and one out: the pen
was kept. More: libinput made up a proximity-out at a silence (a daemon
whose repeated reports never reach the device). Diagnosis only; nothing is
written but the scripted reports, and nothing lights a dark panel.
"""

import glob
import os
import subprocess
import sys
import threading
import time

FIFO = "/run/duo2-touchd.ctl"
PERIOD_S = 0.008
MOVE_EVERY = 19  # reports per position: ~150 ms
SECONDS = 1.5


def device(panel):
    want = "Surface Duo 2 Pen " + ("Right" if panel == "r" else "Left")
    for d in sorted(glob.glob("/sys/class/input/event*")):
        try:
            if open(d + "/device/name").read().strip() == want:
                return "/dev/input/" + os.path.basename(d)
        except OSError:
            continue
    return None


def proximity(line):
    if "TABLET_TOOL_PROXIMITY" not in line:
        return None
    return "out" if "proximity-out" in line else "in" if "proximity-in" in line else None


def main():
    panel = sys.argv[1] if len(sys.argv) > 1 else "r"
    if panel not in ("l", "r"):
        raise SystemExit("usage: pen-keepalive-check.py [l|r]")
    path = device(panel)
    if not path:
        raise SystemExit("no pen device for that panel")
    proc = subprocess.Popen(
        ["libinput", "debug-events", "--device", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    events, ready = [], threading.Event()

    def read():
        for line in proc.stdout:
            if "DEVICE_ADDED" in line:
                ready.set()
            state = proximity(line)
            if state:
                events.append((time.monotonic(), state))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    if not ready.wait(5):
        proc.kill()
        raise SystemExit("libinput debug-events did not open the device")
    fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    t0 = time.monotonic()
    n = int(SECONDS / PERIOD_S)
    for i in range(n):
        fx = 0.40 + 0.01 * (i // MOVE_EVERY)
        os.write(fd, b"pen %s %.3f 0.500\n" % (panel.encode(), fx))
        time.sleep(max(0.0, t0 + (i + 1) * PERIOD_S - time.monotonic()))
    os.write(fd, b"pen out\n")
    os.close(fd)
    time.sleep(0.5)
    proc.send_signal(2)
    try:
        proc.wait(2)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(1)
    ins = sum(s == "in" for _, s in events)
    outs = sum(s == "out" for _, s in events)
    print(
        "%s: %d scripted reports, %d positions; libinput: %d proximity in, %d out%s"
        % (
            path,
            n,
            (n + MOVE_EVERY - 1) // MOVE_EVERY,
            ins,
            outs,
            " (" + ", ".join("%s +%.0f ms" % (s, (t - t0) * 1000) for t, s in events) + ")",
        )
    )
    kept = ins == 1 and outs == 1
    print(
        "kept in proximity" if kept else "libinput made up %d proximity-out(s)" % max(0, outs - 1)
    )
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
