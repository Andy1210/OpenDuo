#!/usr/bin/python3
"""A client that dies while it has the keyboard, the way that aborted duo-wc.

    duoctl exec-trusted --wayland -- python3 teardown-repro.py [layer|text|toplevel] [exit|kill]
    (as the administrator; `layer` needs the layer shell, a trusted client's.
    `text` / `toplevel` also run as an app: . /run/duo-wc/session.env first)

A raw Wayland client (the wire protocol by hand, no toolkit), so it picks its
own object ids and with them the order libwayland destroys its objects in
when it goes away (ascending id). It reuses a freed id so that its role
object (the layer surface or the xdg_toplevel) is destroyed BEFORE its
wl_surface and its wl_keyboard: the destructor of the role object moves the
keyboard focus while the focused surface and the keyboard (or the
text-input) still exist. duo-wc before 2026-11-07 sent them leave() there and
aborted (wayland-backend InvalidId, server_impl/mod.rs 1142; see
src/teardown.rs).

    layer     a 1x1 layer surface (top layer, keyboard exclusive)  (default)
    text      the same, with the keyboard destroyed first and a text-input
              enabled (the on-screen keyboard comes up): the text-input's
              leave() is the one in danger
    toplevel  an xdg_toplevel without a buffer (nothing is drawn)

    exit      close the connection and exit (default)
    kill      SIGKILL itself (no goodbye at all)

Prints "focus" when the keyboard entered its surface before it went away
("nofocus" otherwise: the lock, the overview or another layer kept it).
Nothing is drawn: no buffer is ever attached.
"""

import os
import select
import signal
import socket
import struct
import sys
import time


class Wire:
    def __init__(self):
        if os.environ.get("WAYLAND_SOCKET", "").isdigit():
            self.sock = socket.socket(fileno=int(os.environ.pop("WAYLAND_SOCKET")))
        else:
            path = os.path.join(
                os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-1")
            )
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(path)
        self.buf = b""

    def send(self, obj, opcode, *args):
        body = b""
        for arg in args:
            if isinstance(arg, str):
                data = arg.encode() + b"\0"
                body += struct.pack("=I", len(data)) + data + b"\0" * (-len(data) % 4)
            else:
                body += struct.pack("=I", arg)
        try:
            self.sock.sendall(struct.pack("=II", obj, ((8 + len(body)) << 16) | opcode) + body)
        except OSError:
            for _ in self.events(1.0):  # the error that closed it
                pass
            raise

    def events(self, timeout):
        """(object, opcode, body) of every event that arrives within timeout."""
        end = time.monotonic() + timeout
        while True:
            while len(self.buf) >= 8:
                obj, word = struct.unpack_from("=II", self.buf)
                size = word >> 16
                if len(self.buf) < size:
                    break
                body, self.buf = self.buf[8:size], self.buf[size:]
                if obj == 1 and word & 0xFFFF == 0:  # wl_display.error
                    culprit, code = struct.unpack_from("=II", body, 0)
                    raise SystemExit(
                        "protocol error on %d (code %d): %s" % (culprit, code, string(body, 8)[0])
                    )
                yield obj, word & 0xFFFF, body
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.sock], [], [], left)[0]:
                return
            # recvmsg: the keymap comes with a file descriptor, closed here.
            data, ancdata, _, _ = self.sock.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
            for level, kind, fds in ancdata:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    for i in range(len(fds) // 4):
                        os.close(struct.unpack_from("=i", fds, 4 * i)[0])
            if not data:
                return
            self.buf += data


def string(body, at):
    (n,) = struct.unpack_from("=I", body, at)
    return body[at + 4 : at + 4 + n - 1].decode(), at + 4 + n + (-n % 4)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "layer"
    end = sys.argv[2] if len(sys.argv) > 2 else "exit"
    w = Wire()
    w.send(1, 1, 2)  # wl_display.get_registry -> 2
    w.send(1, 0, 3)  # wl_display.sync -> 3
    globals_ = {}
    versions = {}
    for obj, op, body in w.events(2.0):
        if obj == 2 and op == 0:
            (name,) = struct.unpack_from("=I", body, 0)
            iface, _ = string(body, 4)
            globals_.setdefault(iface, name)
            versions[iface] = struct.unpack_from("=I", body, string(body, 4)[1])[0]
        if obj == 3 and op == 0:
            break

    def bind(iface, version, new_id):
        w.send(2, 0, globals_[iface], iface, min(version, versions[iface]), new_id)

    # 3 (the sync callback) is free again once done: reused.
    # New ids go up one by one (or reuse a freed one): 3, 4, 5, ...
    bind("wl_compositor", 4, 3)
    bind("xdg_wm_base" if mode == "toplevel" else "zwlr_layer_shell_v1", 4, 4)
    bind("wl_seat", 5, 5)
    if mode == "toplevel":
        w.send(3, 0, 6)  # two placeholder surfaces, destroyed: ids 6, 7 free
        w.send(3, 0, 7)
        w.send(6, 0)
        w.send(7, 0)
        surface = 8
        w.send(3, 0, surface)
        w.send(4, 2, 7, surface)  # xdg_wm_base.get_xdg_surface -> 7
        w.send(7, 1, 6)  # xdg_surface.get_toplevel -> 6 (destroyed first)
        w.send(6, 3, "duo-teardown-test")  # set_app_id
        keyboard = 9
        w.send(5, 1, keyboard)
        w.send(surface, 6)  # commit
    else:
        if mode == "text":
            keyboard = 6  # destroyed before the layer surface: only the text-input is left
            w.send(5, 1, keyboard)
            w.send(3, 0, 7)  # placeholder, destroyed: id 7 free
            w.send(7, 0)
            surface, layer, text_input = 8, 7, 10
            w.send(3, 0, surface)
        else:
            w.send(3, 0, 6)  # placeholder, destroyed: id 6 free
            w.send(6, 0)
            surface, layer, keyboard = 7, 6, 8
            w.send(3, 0, surface)
            w.send(5, 1, keyboard)
        # get_layer_surface(new id, surface, output: none, layer: top, namespace)
        w.send(4, 0, layer, surface, 0, 2, "duo-teardown-test")
        w.send(layer, 0, 1, 1)  # set_size 1x1
        w.send(layer, 1, 1 | 4)  # anchor top left
        w.send(layer, 4, 1)  # keyboard interactivity: exclusive
        w.send(surface, 6)  # commit

    focus = False
    for obj, op, body in w.events(2.0):
        if mode == "toplevel" and obj == 4 and op == 0:  # xdg_wm_base.ping
            w.send(4, 3, struct.unpack_from("=I", body, 0)[0])
        if obj == keyboard and op == 1:  # wl_keyboard.enter(serial, surface, keys)
            if struct.unpack_from("=I", body, 4)[0] == surface:
                focus = True
                if mode != "text":
                    break
        if mode == "text" and focus:
            break
    if mode == "text" and focus:
        bind("zwp_text_input_manager_v3", 1, 9)
        w.send(9, 1, text_input, 5)  # get_text_input(seat) -> 10
        w.send(text_input, 1)  # enable
        w.send(text_input, 7)  # commit
        w.send(1, 0, 11)  # sync
        for obj, op, _ in w.events(1.0):
            if obj == 11 and op == 0:
                break
        time.sleep(0.2)  # the input method answers
    print("focus" if focus else "nofocus", flush=True)
    if end == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    os._exit(0)


if __name__ == "__main__":
    main()
