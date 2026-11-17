"""hostapd's control socket (a UNIX datagram socket per interface): requests
(STATUS, STA-FIRST / STA-NEXT) and the attached event stream
(AP-STA-CONNECTED ...). The same protocol as wpa_supplicant's."""

import itertools
import os
import select
import socket

_count = itertools.count()


class Ctrl:
    def __init__(self, server_path, local_dir, timeout=2.0):
        self.server = server_path
        self.local = os.path.join(local_dir, "ctl-%d-%d" % (os.getpid(), next(_count)))
        try:
            os.unlink(self.local)
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.local)
        os.chmod(self.local, 0o600)
        self.sock.settimeout(timeout)
        self.sock.connect(self.server)

    def request(self, cmd):
        """The reply to one command (events that arrive meanwhile are
        skipped: they start with '<')."""
        self.sock.send(cmd.encode())
        while True:
            reply = self.sock.recv(16384).decode("utf-8", "replace")
            if not reply.startswith("<"):
                return reply

    def stations(self):
        """Every station hostapd has (STA-FIRST, STA-NEXT ...)."""
        from .wifi import parse_sta

        out = []
        st = parse_sta(self.request("STA-FIRST"))
        while st is not None and len(out) < 64:
            out.append(st)
            st = parse_sta(self.request("STA-NEXT " + st["mac"]))
        return out

    def fileno(self):
        return self.sock.fileno()

    def recv_event(self):
        """One waiting message, or None: never waits. The socket has a
        timeout (for request()), and with one Python's recv waits for it
        even with MSG_DONTWAIT, then raises TimeoutError: that ended the
        daemon's event watch at the first event, and the devices on the
        hotspot were never listed. So: a zero-timeout poll first."""
        try:
            if not select.select([self.sock], [], [], 0)[0]:
                return None
            return self.sock.recv(16384).decode("utf-8", "replace")
        except (BlockingIOError, InterruptedError, TimeoutError):
            return None

    def close(self):
        try:
            self.sock.close()
        finally:
            try:
                os.unlink(self.local)
            except OSError:
                pass
