#!/usr/bin/python3
"""The firewall end to end, in network namespaces of an ordinary user (no
root, nothing of the machine touched):

    unshare -rn python3 tests/netns_check.py

This namespace stands in for the phone; one namespace each, behind a veth,
for the WiFi network (wlan0), the computer on the USB cable (usb0), a device
on the hotspot (ap0), Android (waydroid0) and the mobile network
(qmapmux0.0). The phone's tables are loaded as on the phone: firewall.nft,
Android's (android/firewall.nft) and the hotspot's (hotspot/: its own
generator). The phone listens on ssh's port, a stand-in for any other
service (LLMNR's 5355), DNS and the DHCP server and client ports. Checked:
ssh only from the WiFi and the USB network, nothing else unasked from the
WiFi or the mobile network, DHCP and DNS only for the hotspot's devices and
Android (and the DHCP server only on USB), replies to what the phone asked
for from everywhere, ping answered.
"""

import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TOP, "hotspot"))

from duo2hotspot import netplan  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(
        "%s %s%s" % ("ok  " if ok else "FAIL", name, (": " + detail) if detail and not ok else ""),
        flush=True,
    )
    if not ok:
        FAILS.append(name)


def sh(*argv, ns=None, input=None, check_rc=True, timeout=20):
    cmd = (["nsenter", "-t", str(ns), "-n"] if ns else []) + [str(a) for a in argv]
    p = subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=timeout)
    if check_rc and p.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), p.stderr.strip()))
    return p


def new_ns():
    p = subprocess.Popen(["unshare", "-n", "sleep", "600"])
    mine = os.readlink("/proc/self/ns/net")
    for _ in range(100):
        try:
            if os.readlink("/proc/%d/ns/net" % p.pid) != mine:
                return p
        except OSError:
            pass
        time.sleep(0.02)
    raise RuntimeError("no namespace")


# name: (the phone's address, the peer's address)
LINKS = {
    "wlan0": ("192.0.2.1", "192.0.2.2"),
    "usb0": ("10.15.19.1", "10.15.19.10"),
    "ap0": ("10.15.43.1", "10.15.43.50"),
    "waydroid0": ("192.168.240.1", "192.168.240.112"),
    "qmapmux0.0": ("198.51.100.2", "198.51.100.1"),
}
V6 = {"wlan0": ("2001:db8:1::1", "2001:db8:1::2")}

PEER_TCP = """
import socket, sys
s = socket.socket(socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET)
s.settimeout(1.5)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    print("open")
except OSError as e:
    print("closed %s" % e)
"""

PEER_UDP = """
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
if len(sys.argv) > 3:
    s.bind(("0.0.0.0", int(sys.argv[3])))
s.sendto(b"hello", (sys.argv[1], int(sys.argv[2])))
"""

PEER_SERVE = """
import socket, sys, threading
t = socket.socket(); t.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
t.bind(("0.0.0.0", 7000)); t.listen(4)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("0.0.0.0", 7001))
def udp():
    while True:
        d, a = u.recvfrom(100)
        u.sendto(b"answer", a)
threading.Thread(target=udp, daemon=True).start()
print("ready", flush=True)
while True:
    c, _ = t.accept(); c.sendall(b"hi"); c.close()
"""


class Listener:
    """The phone's services: TCP ports that accept, UDP ports that count."""

    def __init__(self):
        self.got = {}
        self.socks = []

    def tcp(self, port, family=socket.AF_INET):
        s = socket.socket(family)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("::" if family == socket.AF_INET6 else "0.0.0.0", port))
        s.listen(16)
        self.socks.append(s)

        def accept():
            while True:
                try:
                    c, _ = s.accept()
                    c.close()
                except OSError:
                    return

        threading.Thread(target=accept, daemon=True).start()

    def udp(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        self.socks.append(s)

        def recv():
            while True:
                try:
                    _d, a = s.recvfrom(100)
                except OSError:
                    return
                self.got.setdefault(port, []).append(a[0])

        threading.Thread(target=recv, daemon=True).start()


def main():
    peers = {}
    try:
        sh("ip", "link", "set", "lo", "up")
        for name, (mine, theirs) in LINKS.items():
            p = new_ns()
            peers[name] = p
            sh("ip", "link", "add", name, "type", "veth", "peer", "name", "p0", "netns", p.pid)
            sh("ip", "addr", "add", mine + "/24", "dev", name)
            sh("ip", "link", "set", name, "up")
            sh("ip", "link", "set", "lo", "up", ns=p.pid)
            sh("ip", "addr", "add", theirs + "/24", "dev", "p0", ns=p.pid)
            sh("ip", "link", "set", "p0", "up", ns=p.pid)
            if name in V6:
                sh("ip", "addr", "add", V6[name][0] + "/64", "dev", name, "nodad")
                sh("ip", "addr", "add", V6[name][1] + "/64", "dev", "p0", "nodad", ns=p.pid)
        sh("nft", "-f", os.path.join(TOP, "firewall", "firewall.nft"))
        sh("nft", "-f", os.path.join(TOP, "android", "firewall.nft"))
        downs = [{"iface": "ap0", "net4": "10.15.43.0/24", "net6": None}]
        sh("nft", "-f", "-", input=netplan.nft_ruleset(downs, ["qmapmux0.0"]))

        phone = Listener()
        for port in (22, 5355, 53):
            phone.tcp(port)
        phone.tcp(2222, socket.AF_INET6)
        for port in (53, 67, 68, 5355):
            phone.udp(port)

        def tcp_from(name, port, addr=None):
            out = sh(
                "python3", "-c", PEER_TCP, addr or LINKS[name][0], port, ns=peers[name].pid
            ).stdout.strip()
            return out == "open"

        # ssh: WiFi and USB only.
        for name in LINKS:
            want = name in ("wlan0", "usb0")
            check(
                "ssh from %s %s" % (name, "open" if want else "dropped"), tcp_from(name, 22) == want
            )
        check("another service (5355) from wlan0 dropped", not tcp_from("wlan0", 5355))
        check(
            "another service (5355) from the mobile network dropped",
            not tcp_from("qmapmux0.0", 5355),
        )
        check("DNS over TCP from the hotspot open", tcp_from("ap0", 53))
        check("DNS over TCP from wlan0 dropped", not tcp_from("wlan0", 53))
        check(
            "IPv6 to another port from wlan0 dropped", not tcp_from("wlan0", 2222, V6["wlan0"][0])
        )

        # UDP: DNS and DHCP for the hotspot and Android, the DHCP server on USB.
        def udp_from(name, port, sport=None):
            argv = ["python3", "-c", PEER_UDP, LINKS[name][0], port] + ([sport] if sport else [])
            sh(*argv, ns=peers[name].pid)

        cases = [
            ("ap0", 53, True),
            ("ap0", 67, True),
            ("waydroid0", 53, True),
            ("waydroid0", 67, True),
            ("usb0", 67, True),
            ("usb0", 53, False),
            ("wlan0", 53, False),
            ("wlan0", 67, False),
            ("wlan0", 5355, False),
            ("qmapmux0.0", 53, False),
        ]
        for name, port, _want in cases:
            udp_from(name, port)
        udp_from("wlan0", 68, 67)  # a DHCP server's answer
        time.sleep(0.5)
        for name, port, want in cases:
            seen = LINKS[name][1] in phone.got.get(port, [])
            check(
                "UDP %d from %s %s" % (port, name, "reaches" if want else "dropped"), seen == want
            )
        check("a DHCP answer (67 -> 68) from wlan0 reaches", "192.0.2.2" in phone.got.get(68, []))

        # Replies to what the phone asked for.
        for name in ("wlan0", "qmapmux0.0"):
            p = subprocess.Popen(
                ["nsenter", "-t", str(peers[name].pid), "-n", "python3", "-c", PEER_SERVE],
                stdout=subprocess.PIPE,
                text=True,
            )
            p.stdout.readline()
            try:
                c = socket.create_connection((LINKS[name][1], 7000), timeout=2)
                got = c.recv(10)
                c.close()
                check("a TCP connection out over %s answered" % name, got == b"hi")
                u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                u.settimeout(2)
                u.sendto(b"q", (LINKS[name][1], 7001))
                check("a UDP query out over %s answered" % name, u.recvfrom(100)[0] == b"answer")
            except OSError as e:
                check("replies over %s" % name, False, str(e))
            finally:
                p.kill()
                p.wait()

        # Ping.
        r = sh(
            "ping", "-c", "1", "-W", "2", LINKS["wlan0"][0], ns=peers["wlan0"].pid, check_rc=False
        )
        check("ping from wlan0 answered", r.returncode == 0, r.stdout + r.stderr)
        r = sh(
            "ping",
            "-6",
            "-c",
            "1",
            "-W",
            "2",
            V6["wlan0"][0],
            ns=peers["wlan0"].pid,
            check_rc=False,
        )
        check(
            "IPv6 ping from wlan0 answered (neighbour discovery works)", r.returncode == 0, r.stderr
        )
    finally:
        for p in peers.values():
            p.kill()
            p.wait()
    print("all checks passed" if not FAILS else "%d check(s) failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
