#!/usr/bin/python3 -I
"""Capture the display state after every wake, for a wake whose picture does
not reach a panel (2026-11-08: the left panel stayed dark after the power
key while duo-wc had flipped its first frames).

Runs ON THE PHONE as root (debugfs), until killed or the time limit:

    sudo python3 -I wake-capture.py OUTDIR [--regs] [--limit S]

It follows duo-wc's event stream (the admin socket) and, each time panels
come on (`lit` gains a panel), saves 0.7 s and 3 s later:
  - /sys/kernel/debug/dri/0/state (the atomic state: planes, SSPPs, CRTCs),
  - encoder-N/status (interface, TE/vsync and underrun counters),
  - the connectors' dpms and the backlights' levels,
  - with --regs, and only while BOTH connectors are on: the display register
    snapshot (/sys/kernel/debug/dri/0/kms: DPU blocks, DSI controllers and
    PHYs; the devcoredump path). Never with a panel off: the snapshot reads
    the DSI PHYs without powering them.
Files: OUTDIR/NNN-HHMMSS-<lit>-<after>.txt, and OUTDIR/events.log (each
state change with its time). Read-only: it writes nothing but OUTDIR.
"""

import json
import os
import socket
import sys
import threading
import time

ADMIN = os.environ.get("DUO_WC_ADMIN_SOCKET", "/run/duo-wc/admin.sock")
DRI = os.environ.get("WAKE_CAPTURE_DRI", "/sys/kernel/debug/dri/0")
AFTER = (0.7, 3.0)


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError as e:
        return "(%s: %s)\n" % (path, e)


def connectors_on():
    out = {}
    for name in ("DSI-1", "DSI-2"):
        out[name] = read("/sys/class/drm/card0-%s/dpms" % name).strip()
    return out


def capture(outdir, n, lit, after, regs, log):
    time.sleep(after)
    stamp = time.strftime("%H%M%S")
    path = os.path.join(outdir, "%03d-%s-%s-%.1fs.txt" % (n, stamp, "+".join(lit) or "none", after))
    dpms = connectors_on()
    parts = [
        "# wake %d, lit %s, %.1f s after, %s\n" % (n, lit, after, time.strftime("%H:%M:%S")),
        "# dpms %s\n" % dpms,
    ]
    for b in sorted(os.listdir("/sys/class/backlight")):
        parts.append(
            "# backlight %s %s\n" % (b, read("/sys/class/backlight/%s/brightness" % b).strip())
        )
    parts.append("== state\n" + read(DRI + "/state"))
    for e in sorted(x for x in os.listdir(DRI) if x.startswith("encoder-")):
        parts.append("== %s/status\n%s\n" % (e, read("%s/%s/status" % (DRI, e))))
    if regs:
        if all(v == "On" for v in dpms.values()):
            parts.append("== kms\n" + read(DRI + "/kms"))
        else:
            parts.append("== kms: skipped, a connector is off (%s)\n" % dpms)
    with open(path, "w") as f:
        f.write("".join(parts))
    log("captured %s" % os.path.basename(path))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    outdir = argv[1]
    regs = "--regs" in argv
    limit = 1800.0
    if "--limit" in argv:
        limit = float(argv[argv.index("--limit") + 1])
    os.makedirs(outdir, exist_ok=True)
    events = open(os.path.join(outdir, "events.log"), "a", buffering=1)

    def log(msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        events.write(line + "\n")
        print(line, flush=True)

    deadline = time.monotonic() + limit
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(ADMIN)
    sock.sendall(b'{"id":1,"request":"subscribe"}\n')
    sock.settimeout(5.0)
    buf = b""
    lit = None
    n = 0
    log("started (regs=%s, limit %d s)" % (regs, limit))
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            continue
        if not chunk:
            log("duo-wc closed the stream")
            return 1
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            state = (msg.get("ok") or {}).get("state") if "ok" in msg else msg.get("state")
            if not isinstance(state, dict) or "lit" not in state:
                continue
            now = sorted(state.get("lit") or [])
            if now != lit:
                log(
                    "lit %s -> %s (ambient %s, posture %s)"
                    % (lit, now, state.get("ambient"), state.get("posture"))
                )
                if lit is not None and set(now) - set(lit):
                    n += 1
                    for after in AFTER:
                        threading.Thread(
                            target=capture, args=(outdir, n, now, after, regs, log), daemon=True
                        ).start()
                lit = now
    log("time limit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
