"""DNS for the hotspot's devices: the phone's addresses on the hotspot answer
on port 53 by passing each query to systemd-resolved's stub (127.0.0.53)
and the answer back (tests/test_dnsrelay.py).

No packet is parsed: a UDP query goes out on a socket of its own and its
first answer comes back; over TCP one length-prefixed query per connection.
Bounded: at most MAX_UDP queries and MAX_TCP connections at a time, each
dropped after TIMEOUT s. The sockets bind with IP_FREEBIND (the hotspot's
addresses may still be on their way when it starts).
"""

import socket
import struct
import time

from gi.repository import GLib

UPSTREAM = ("127.0.0.53", 53)
TIMEOUT = 5.0
MAX_UDP = 256
MAX_TCP = 32
IP_FREEBIND = 15
IPV6_FREEBIND = 78


def _bind(family, kind, addr, port):
    s = socket.socket(family, kind)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        s.setsockopt(socket.IPPROTO_IPV6, IPV6_FREEBIND, 1)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    else:
        s.setsockopt(socket.IPPROTO_IP, IP_FREEBIND, 1)
    s.bind((addr, port))
    s.setblocking(False)
    if kind == socket.SOCK_STREAM:
        s.listen(16)
    return s


class Relay:
    def __init__(self, addrs, port=53, upstream=UPSTREAM, log=print):
        """addrs: the addresses to answer on (IPv4 and IPv6 strings)."""
        self.upstream = upstream
        self.port = port
        self.log = log
        self.socks = []  # (socket, watch id)
        self.udp = {}  # upstream fd -> (upstream socket, listener, peer, deadline, watch)
        self.tcp = {}  # fd -> connection state
        self.timer = 0
        for a in addrs:
            fam = socket.AF_INET6 if ":" in a else socket.AF_INET
            for kind, cb in (
                (socket.SOCK_DGRAM, self._udp_query),
                (socket.SOCK_STREAM, self._tcp_accept),
            ):
                try:
                    s = _bind(fam, kind, a, port)
                except OSError as e:
                    self.log(
                        "duo2-hotspot: DNS on %s:%d (%s): %s"
                        % (a, port, "udp" if kind == socket.SOCK_DGRAM else "tcp", e)
                    )
                    continue
                w = GLib.io_add_watch(
                    s.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, lambda _fd, _c, s=s, cb=cb: cb(s)
                )
                self.socks.append((s, w))

    def close(self):
        for s, w in self.socks:
            GLib.source_remove(w)
            s.close()
        self.socks = []
        for fd in list(self.udp):
            self._udp_drop(fd)
        for fd in list(self.tcp):
            self._tcp_drop(fd)
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def _arm(self):
        if not self.timer and (self.udp or self.tcp):
            self.timer = GLib.timeout_add(1000, self._expire)

    def _expire(self):
        now = time.monotonic()
        for fd, q in list(self.udp.items()):
            if q[3] < now:
                self._udp_drop(fd)
        for fd, c in list(self.tcp.items()):
            if c["deadline"] < now:
                self._tcp_drop(fd)
        if self.udp or self.tcp:
            return True
        self.timer = 0
        return False

    # -- UDP ---------------------------------------------------------------------

    def _udp_query(self, lsock):
        try:
            data, peer = lsock.recvfrom(4096)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return True
        if not data or len(self.udp) >= MAX_UDP:
            return True
        try:
            up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            up.setblocking(False)
            up.connect(self.upstream)
            up.send(data)
        except OSError:
            up.close()
            return True
        w = GLib.io_add_watch(
            up.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
            lambda fd, _c: self._udp_answer(fd),
        )
        self.udp[up.fileno()] = (up, lsock, peer, time.monotonic() + TIMEOUT, w)
        self._arm()
        return True

    def _udp_answer(self, fd):
        q = self.udp.get(fd)
        if q is None:
            return False
        up, lsock, peer = q[0], q[1], q[2]
        try:
            answer = up.recv(65535)
            lsock.sendto(answer, peer)
        except OSError:
            pass
        self._udp_drop(fd, watch_gone=True)
        return False

    def _udp_drop(self, fd, watch_gone=False):
        q = self.udp.pop(fd, None)
        if q is None:
            return
        if not watch_gone:
            GLib.source_remove(q[4])
        q[0].close()

    # -- TCP ---------------------------------------------------------------------

    def _tcp_accept(self, lsock):
        try:
            conn, _peer = lsock.accept()
        except OSError:
            return True
        if len(self.tcp) >= MAX_TCP:
            conn.close()
            return True
        conn.setblocking(False)
        c = {
            "conn": conn,
            "buf": b"",
            "up": None,
            "upbuf": b"",
            "deadline": time.monotonic() + TIMEOUT,
            "watch": 0,
            "upwatch": 0,
        }
        c["watch"] = GLib.io_add_watch(
            conn.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
            lambda fd, _cond: self._tcp_read(fd),
        )
        self.tcp[conn.fileno()] = c
        self._arm()
        return True

    def _tcp_read(self, fd):
        c = self.tcp.get(fd)
        if c is None:
            return False
        try:
            chunk = c["conn"].recv(65537 - len(c["buf"]))
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            chunk = b""
        if not chunk:
            c["watch"] = 0
            self._tcp_drop(fd)
            return False
        c["buf"] += chunk
        if len(c["buf"]) < 2:
            return True
        need = struct.unpack("!H", c["buf"][:2])[0] + 2
        if len(c["buf"]) < need:
            return True
        query = c["buf"][:need]
        try:
            up = socket.create_connection(self.upstream, timeout=TIMEOUT)
            up.sendall(query)
            up.setblocking(False)
        except OSError:
            c["watch"] = 0
            self._tcp_drop(fd)
            return False
        c["up"] = up
        c["upwatch"] = GLib.io_add_watch(
            up.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
            lambda _fd, _cond, fd=fd: self._tcp_answer(fd),
        )
        c["watch"] = 0
        return False  # one query per connection

    def _tcp_answer(self, fd):
        c = self.tcp.get(fd)
        if c is None:
            return False
        try:
            chunk = c["up"].recv(65537)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            chunk = b""
        if chunk:
            c["upbuf"] += chunk
            if len(c["upbuf"]) < 2 or len(c["upbuf"]) < struct.unpack("!H", c["upbuf"][:2])[0] + 2:
                return True
            try:
                c["conn"].setblocking(True)
                c["conn"].settimeout(2.0)
                c["conn"].sendall(c["upbuf"])
            except OSError:
                pass
        c["upwatch"] = 0
        self._tcp_drop(fd)
        return False

    def _tcp_drop(self, fd):
        c = self.tcp.pop(fd, None)
        if c is None:
            return
        for key, sock in (("watch", "conn"), ("upwatch", "up")):
            if c[key]:
                GLib.source_remove(c[key])
            if c[sock] is not None:
                try:
                    c[sock].close()
                except OSError:
                    pass
