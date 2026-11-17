#!/usr/bin/python3
"""List the toplevels duo-wc announces on ext-foreign-toplevel-list-v1.

    duoctl exec-trusted --wayland -- tools/toplevels.py [seconds]

The list is for trusted clients only (docs/hardware/security.md): run it
as the administrator (duo2admin) through `duoctl exec-trusted --wayland`,
which hands it a trusted connection in WAYLAND_SOCKET. On the public socket
(`. /run/duo-wc/session.env; tools/toplevels.py`, an app's view) the global
is not there: it says so.

A raw Wayland wire-protocol client (no libraries): binds the list, prints each
toplevel's identifier, app id and title as they arrive, and any change for the
given number of seconds (default: just the current list).
"""

import os
import select
import socket
import struct
import sys
import time


def string(s):
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)


def message(obj, opcode, payload=b""):
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload


def read_string(payload, at):
    (n,) = struct.unpack_from("<I", payload, at)
    s = payload[at + 4 : at + 4 + n - 1].decode(errors="replace")
    return s, at + 4 + ((n + 3) // 4) * 4


def main():
    watch = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    if os.environ.get("WAYLAND_SOCKET", "").isdigit():
        sock = socket.socket(fileno=int(os.environ.pop("WAYLAND_SOCKET")))
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/duo-wc")
        path = os.path.join(runtime, os.environ.get("WAYLAND_DISPLAY", "wayland-1"))
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(path)
    next_id = [2]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1

    registry = new_id()
    sync = new_id()
    sock.sendall(
        message(1, 1, struct.pack("<I", registry)) + message(1, 0, struct.pack("<I", sync))
    )

    lst = None
    handles = {}
    buf = b""
    synced = False
    deadline = None
    while True:
        timeout = None if deadline is None else max(0.0, deadline - time.time())
        if deadline is not None and timeout == 0.0:
            break
        r, _, _ = select.select([sock], [], [], timeout)
        if not r:
            break
        data = sock.recv(65536)
        if not data:
            break
        buf += data
        while len(buf) >= 8:
            obj, word = struct.unpack_from("<II", buf)
            size, opcode = word >> 16, word & 0xFFFF
            if len(buf) < size:
                break
            payload, buf = buf[8:size], buf[size:]
            if obj == registry and opcode == 0:
                (name,) = struct.unpack_from("<I", payload)
                iface, at = read_string(payload, 4)
                if iface == "ext_foreign_toplevel_list_v1" and lst is None:
                    lst = new_id()
                    sock.sendall(
                        message(
                            registry,
                            0,
                            struct.pack("<I", name) + string(iface) + struct.pack("<II", 1, lst),
                        )
                    )
                    s2 = new_id()
                    sock.sendall(message(1, 0, struct.pack("<I", s2)))
                    sync = s2
            elif obj == sync and opcode == 0:
                if lst is None:
                    print("no ext_foreign_toplevel_list_v1 global")
                    return 1
                if not synced:
                    synced = True
                    s3 = new_id()
                    sock.sendall(message(1, 0, struct.pack("<I", s3)))
                    sync = s3
                else:
                    deadline = time.time() + watch
            elif obj == lst and opcode == 0:
                (handle,) = struct.unpack_from("<I", payload)
                handles[handle] = {}
            elif obj in handles:
                h = handles[obj]
                if opcode == 0:
                    print("closed  %s" % h.get("identifier"))
                elif opcode == 1:
                    print(
                        "toplevel %-34s app_id=%-28s title=%s"
                        % (h.get("identifier"), h.get("app_id"), h.get("title"))
                    )
                elif opcode in (2, 3, 4):
                    value, _ = read_string(payload, 0)
                    h[{2: "title", 3: "app_id", 4: "identifier"}[opcode]] = value
            elif obj == 1 and opcode == 0:
                print("protocol error:", payload)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
