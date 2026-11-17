#!/usr/bin/python3
"""Read or set the host clipboard through ext-data-control, for testing the
Android clipboard bridge (needs a trusted connection: run it with
`duoctl exec-trusted --wayland -- python3 clipboard_probe.py ...` as the
administrator, from /usr/local/lib/duo-shell).

    clipboard_probe.py get          print the selection's text (or nothing)
    clipboard_probe.py set TEXT     own the selection with TEXT for 15 s
"""

import os
import select
import sys
import time

sys.path.insert(0, "/usr/local/lib/duo-shell")
from protocols.ext_data_control_v1 import ExtDataControlManagerV1  # noqa: E402
from protocols.wayland import WlSeat  # noqa: E402
from pywayland.client import Display  # noqa: E402

d = Display()
d.connect()
found = {}
reg = d.get_registry()


def on_global(r, n, i, v):
    found.setdefault(i, (r, n))


reg.dispatcher["global"] = on_global
d.roundtrip()
if "ext_data_control_manager_v1" not in found:
    print("no ext_data_control_manager_v1 (not trusted)")
    sys.exit(1)
mgr = reg.bind(found["ext_data_control_manager_v1"][1], ExtDataControlManagerV1, 1)
seat = reg.bind(found["wl_seat"][1], WlSeat, 1)
dev = mgr.get_data_device(seat)
state = {"offer": None, "mimes": {}}


def on_offer(_d, o):
    state["mimes"].setdefault(o, [])

    def mime(oo, m):
        state["mimes"][oo].append(m)

    o.dispatcher["offer"] = mime


def on_selection(_d, o):
    state["offer"] = o


dev.dispatcher["data_offer"] = on_offer
dev.dispatcher["selection"] = on_selection
d.roundtrip()
if sys.argv[1] == "get":
    o = state["offer"]
    if o is None:
        print("(no selection)")
        sys.exit(0)
    mime = next((m for m in state["mimes"].get(o, []) if m.startswith("text/plain")), None)
    r, w = os.pipe()
    o.receive(mime or "text/plain;charset=utf-8", w)
    d.flush()
    os.close(w)
    data = b""
    while select.select([r], [], [], 2)[0]:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        data += chunk
    print("selection: %r" % data.decode(errors="replace"))
else:
    text = sys.argv[2].encode()
    src = mgr.create_data_source()
    src.offer("text/plain;charset=utf-8")
    src.offer("text/plain")

    def send(_s, _m, fd):
        os.write(fd, text)
        os.close(fd)

    src.dispatcher["send"] = send

    def cancelled(_s):
        print("replaced")
        os._exit(0)

    src.dispatcher["cancelled"] = cancelled
    dev.set_selection(src)
    d.flush()
    end = time.time() + 15
    while time.time() < end:
        d.flush()
        if select.select([d.get_fd()], [], [], 0.5)[0]:
            d.dispatch(block=True)
