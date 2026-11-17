"""The DNS relay against a stand-in resolver on the loopback (high ports, no
root): UDP and TCP queries come back answered, the limits hold."""

import os
import socket
import struct
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    from gi.repository import GLib

    from duo2hotspot import dnsrelay
except ImportError:
    GLib = None


def free_port(kind=socket.SOCK_DGRAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Resolver:
    """Answers every query with b"ANS:" + the query (UDP and TCP)."""

    def __init__(self):
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.port = self.udp.getsockname()[1]
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("127.0.0.1", self.port))
        self.tcp.listen(4)
        self.stop = False
        self.threads = [
            threading.Thread(target=self._udp, daemon=True),
            threading.Thread(target=self._tcp, daemon=True),
        ]
        for t in self.threads:
            t.start()

    def _udp(self):
        self.udp.settimeout(0.2)
        while not self.stop:
            try:
                q, peer = self.udp.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            self.udp.sendto(b"ANS:" + q, peer)

    def _tcp(self):
        self.tcp.settimeout(0.2)
        while not self.stop:
            try:
                c, _ = self.tcp.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with c:
                n = struct.unpack("!H", c.recv(2))[0]
                q = b""
                while len(q) < n:
                    q += c.recv(n - len(q))
                a = b"ANS:" + q
                c.sendall(struct.pack("!H", len(a)) + a)

    def close(self):
        self.stop = True
        for t in self.threads:
            t.join(1)
        self.udp.close()
        self.tcp.close()


def pump(until, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until():
        ctx.iteration(False)
        time.sleep(0.005)


@unittest.skipIf(GLib is None, "no PyGObject")
class Relay(unittest.TestCase):
    def setUp(self):
        self.res = Resolver()
        self.port = free_port()
        self.relay = dnsrelay.Relay(
            ["127.0.0.1"],
            port=self.port,
            upstream=("127.0.0.1", self.res.port),
            log=lambda *a: None,
        )

    def tearDown(self):
        self.relay.close()
        self.res.close()

    def test_udp(self):
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.settimeout(0.01)
        c.sendto(b"\x12\x34query", ("127.0.0.1", self.port))
        got = []

        def answered():
            try:
                got.append(c.recv(4096))
            except TimeoutError:
                pass
            return bool(got)

        pump(answered)
        c.close()
        self.assertEqual(got, [b"ANS:\x12\x34query"])
        pump(lambda: not self.relay.udp)
        self.assertEqual(self.relay.udp, {})

    def test_tcp(self):
        out = {}

        def client():
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as c:
                c.sendall(struct.pack("!H", 6) + b"\x00\x01tcpq")
                n = struct.unpack("!H", c.recv(2))[0]
                data = b""
                while len(data) < n:
                    data += c.recv(n - len(data))
                out["a"] = data

        t = threading.Thread(target=client, daemon=True)
        t.start()
        pump(lambda: "a" in out)
        t.join(1)
        self.assertEqual(out.get("a"), b"ANS:\x00\x01tcpq")
        pump(lambda: not self.relay.tcp)
        self.assertEqual(self.relay.tcp, {})

    def test_bounded(self):
        old = dnsrelay.MAX_UDP
        dnsrelay.MAX_UDP = 0
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.settimeout(0.01)
            c.sendto(b"q", ("127.0.0.1", self.port))
            got = []

            def answered():
                try:
                    got.append(c.recv(10))
                except TimeoutError:
                    pass
                return bool(got)

            pump(answered, 0.3)
            c.close()
            self.assertEqual(got, [])
        finally:
            dnsrelay.MAX_UDP = old

    def test_close_frees_the_port(self):
        self.relay.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", self.port))
        s.close()
        self.relay = dnsrelay.Relay([], log=lambda *a: None)


if __name__ == "__main__":
    unittest.main()
