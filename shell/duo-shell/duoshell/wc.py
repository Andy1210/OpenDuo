"""duo-wc's control socket: one-off requests, and the event stream.

The connection is a trusted one from the session's trust factory
(trust.py: every request allowed), else $DUO_WC_SOCKET / /run/duo-wc/duo-wc.sock
(the user's socket: the state and the event stream only; the shell started
by hand). One JSON object per line. `subscribe` keeps the
connection open and duo-wc sends `{"event": ...}` lines on it (state changes,
edge swipes); this module reads them from the GLib main loop (no threads)
and reconnects when the compositor restarts.
"""

import json
import os
import socket

from . import trust


def socket_path():
    if os.environ.get("DUO_WC_SOCKET"):
        return os.environ["DUO_WC_SOCKET"]
    return "/run/duo-wc/duo-wc.sock"


def connect(timeout=2.0):
    """A control connection: trusted when the session gave us its factory."""
    if trust.available():
        return trust.control_socket(timeout)
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(socket_path())
    except OSError:
        s.close()
        raise
    return s


def request(name, timeout=2.0, sock=None, **args):
    """Send one request, return its `ok` value; raises OSError / RuntimeError.
    `sock`: a connection made beforehand (connect(), on the main thread: the
    trust factory is one socket), used and closed here."""
    s = sock if sock is not None else connect(timeout)
    try:
        s.sendall((json.dumps(dict(id=1, request=name, **args)) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    reply = json.loads(buf.split(b"\n")[0] or b"{}")
    if "error" in reply:
        raise RuntimeError("duo-wc %s: %s" % (name, reply["error"]))
    return reply.get("ok")


class LineBuffer:
    """Bytes in, complete JSON lines out (pure; tests/test_wc.py)."""

    def __init__(self, limit=1 << 20):
        self.buf = b""
        self.limit = limit

    def feed(self, data):
        self.buf += data
        out = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        if len(self.buf) > self.limit:
            self.buf = b""
        return out


class Subscription:
    """The event stream, read in the GLib main loop. `on_message(obj)` gets
    the subscribe reply (`{"id":..,"ok":{"state":..}}`) and every event."""

    def __init__(self, on_message, retry_s=2):
        self.on_message = on_message
        self.retry_s = retry_s
        self.sock = None
        self.watch = None
        self.lines = LineBuffer()

    def start(self):
        from gi.repository import GLib

        self.stop()
        try:
            s = connect(2.0)
            s.sendall(b'{"id":1,"request":"subscribe"}\n')
            s.setblocking(False)
        except OSError as e:
            print("duo-shell: no event stream (%s); again in %d s" % (e, self.retry_s), flush=True)
            GLib.timeout_add_seconds(self.retry_s, self._retry)
            return
        self.sock = s
        self.lines = LineBuffer()
        self.watch = GLib.io_add_watch(
            s.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._readable,
        )

    def _retry(self):
        self.start()
        return False

    def stop(self):
        from gi.repository import GLib

        if self.watch is not None:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _readable(self, _fd, condition):
        from gi.repository import GLib

        data = b""
        if condition & GLib.IOCondition.IN:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                return True
            except OSError:
                data = b""
        if not data:
            self.watch = None
            self.sock.close()
            self.sock = None
            print("duo-shell: event stream closed; again in %d s" % self.retry_s, flush=True)
            GLib.timeout_add_seconds(self.retry_s, self._retry)
            return False
        for message in self.lines.feed(data):
            try:
                self.on_message(message)
            except Exception as e:  # a handler bug must not end the stream
                print("duo-shell: event handler failed: %r" % (e,), flush=True)
        return True
