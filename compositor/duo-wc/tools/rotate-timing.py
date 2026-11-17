#!/usr/bin/env python3
"""Turn both panels like duo-posed does and print each turn's frame timing.

    sudo duo2-sensors-mode off        # or duo-posed turns them back
    rotate-timing.py [ROTATIONS...]   # default: 90 0 90 0 90 0
    sudo duo2-sensors-mode on

For each rotation the two set_rotation requests go back to back over the
admin socket (duo-posed's pair), then it waits 2 s. Afterwards it reads
duo-wc's "rotation: frame timing" lines (crate::turnlog) from the journal
and prints one row per panel and turn: frames on the panel, frames later
than 13 ms, the longest interval, missed vblanks, the longest CPU time of a
frame (and of its element build, draw and commit), frames rendered ahead.
Run as root or the administrator (the admin socket, the journal).
"""

import json
import os
import re
import socket
import subprocess
import sys
import time

SOCK = os.environ.get("DUO_WC_ADMIN_SOCKET", "/run/duo-wc/admin.sock")
FIELD = re.compile(r'(\w+)=("[^"]*"|\[[^\]]*\]|\S+)')


def request(sock, buf, payload):
    sock.sendall((json.dumps(payload) + "\n").encode())
    while b"\n" not in buf[0]:
        chunk = sock.recv(65536)
        if not chunk:
            raise RuntimeError("duo-wc closed the connection")
        buf[0] += chunk
    line, buf[0] = buf[0].split(b"\n", 1)
    reply = json.loads(line)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply


def main():
    rotations = [int(a) for a in sys.argv[1:]] or [90, 0, 90, 0, 90, 0]
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(SOCK)
    buf = [b""]
    n = 0
    for r in rotations:
        for output in ("DSI-1", "DSI-2"):
            n += 1
            request(
                sock, buf, {"id": n, "request": "set_rotation", "output": output, "rotation": r}
            )
        time.sleep(2.0)
    sock.close()
    log = subprocess.run(
        ["journalctl", "-t", "duo-wc", "--since", since, "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    print(
        "%-6s %6s %4s %7s %6s %7s %6s %6s %6s %5s"
        % (
            "panel",
            "frames",
            "late",
            "max_gap",
            "missed",
            "render",
            "build",
            "draw",
            "commit",
            "ahead",
        )
    )
    for line in log.splitlines():
        if "rotation: frame timing" not in line:
            continue
        f = {k: v.strip('"') for k, v in FIELD.findall(line.split("rotation: frame timing", 1)[1])}
        print(
            "%-6s %6s %4s %7s %6s %7s %6s %6s %6s %5s"
            % (
                f.get("output"),
                f.get("frames"),
                f.get("late"),
                f.get("max_gap_ms"),
                f.get("missed_vblanks"),
                f.get("max_render_ms"),
                f.get("max_build_ms"),
                f.get("max_draw_ms", "-"),
                f.get("max_commit_ms", "-"),
                f.get("ahead", "-"),
            )
        )


if __name__ == "__main__":
    main()
