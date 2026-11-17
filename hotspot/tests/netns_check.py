#!/usr/bin/python3
"""The hotspot's forwarding, firewall and DNS, end to end in network
namespaces of an ordinary user (no root, nothing of the machine touched):

    unshare -rn python3 tests/netns_check.py

Four namespaces stand in for the phone (this one), a device on the hotspot
(behind ap0, a veth), the home WiFi network (behind wlan0, the phone's
default route) and the mobile network (behind qmapmux0.0, a raw-IP TUN like
rmnet, pumped to a TUN in the carrier's namespace). Both "networks" answer
on 198.51.100.1 and 2001:db8:ffff::1 and say who they are and which source
address they saw. The daemon's own apply_shared() sets up the table, the
policy routing and the switches, with android/firewall.nft (Waydroid's
table) loaded too. Checked: the device reaches the internet over MOBILE
data (not the WiFi that is the default route), masqueraded (IPv4 and NAT66);
it reaches neither the home LAN nor the phone's own services; nothing
reaches it unasked; DNS on the hotspot address answers; without mobile data
nothing leaks out over WiFi; Waydroid's table needs the mark.

The kernel here may be older than the phone's: IPv6 force_forwarding (6.17)
is then missing and the IPv6 global switch is set in this namespace instead.
"""

import fcntl
import importlib.machinery
import importlib.util
import ipaddress
import os
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from gi.repository import GLib  # noqa: E402

from duo2hotspot import conf, dnsrelay, netplan, state  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(
        "%s %s%s" % ("ok  " if ok else "FAIL", name, (": " + detail) if detail and not ok else ""),
        flush=True,
    )
    if not ok:
        FAILS.append(name)


def sh(*argv, ns=None, check_rc=True, input=None):
    cmd = (["nsenter", "-t", str(ns), "-n"] if ns else []) + [str(a) for a in argv]
    p = subprocess.run(cmd, capture_output=True, text=True, input=input)
    if check_rc and p.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), p.stderr.strip()))
    return p.stdout


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


TUNSETIFF = 0x400454CA
IFF_TUN, IFF_NO_PI = 0x0001, 0x1000


def tun(name):
    fd = os.open("/dev/net/tun", os.O_RDWR)
    fcntl.ioctl(fd, TUNSETIFF, struct.pack("16sH", name.encode(), IFF_TUN | IFF_NO_PI))
    return fd


def pump(a, b, stop):
    while not stop.is_set():
        r, _, _ = select.select([a, b], [], [], 0.2)
        for fd in r:
            try:
                pkt = os.read(fd, 65535)
                os.write(b if fd == a else a, pkt)
            except OSError:
                pass


ECHO = r"""
import socket, sys, threading
name = sys.argv[1]
addrs = sys.argv[2:] or ["0.0.0.0", "::"]
def sock(fam, kind):
    s = socket.socket(fam, kind)
    if fam == socket.AF_INET6:
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    return s
def serve(fam, addr):
    s = sock(fam, socket.SOCK_DGRAM)
    s.bind((addr, 7))
    while True:
        d, peer = s.recvfrom(2048)
        s.sendto(("%s saw %s" % (name, peer[0])).encode(), peer)
def tcp(fam, addr):
    s = sock(fam, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((addr, 7))
    s.listen(4)
    while True:
        c, peer = s.accept()
        c.sendall(("%s tcp saw %s" % (name, peer[0])).encode())
        c.close()
for addr in addrs:
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    threading.Thread(target=serve, args=(fam, addr), daemon=True).start()
    threading.Thread(target=tcp, args=(fam, addr), daemon=True).start()
threading.Event().wait()
"""

ASK = r"""
import socket, sys
fam = socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET
proto = sys.argv[3] if len(sys.argv) > 3 else "udp"
try:
    if proto == "udp":
        s = socket.socket(fam, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.sendto(b"hi", (sys.argv[1], int(sys.argv[2])))
        print(s.recv(200).decode())
    else:
        s = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1.5)
        print(s.recv(200).decode() or "connected")
except socket.timeout:
    print("timeout")
except OSError as e:
    print("error %s" % e.errno)
"""


def ask(ns, addr, port=7, proto="udp"):
    return sh("python3", "-c", ASK, addr, port, proto, ns=ns, check_rc=False).strip()


def dns_ask(ns, addr):
    code = r"""
import socket, sys
s = socket.socket(socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(1.5)
q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"
try:
    s.sendto(q, (sys.argv[1], 53)); print(s.recv(512)[:2].hex())
except OSError as e:
    print("error %s" % e)
"""
    return sh("python3", "-c", code, addr, ns=ns, check_rc=False).strip()


def main():
    if os.geteuid() != 0:
        print("run it as: unshare -rn python3 %s" % sys.argv[0])
        return 2
    tmp = tempfile.mkdtemp()
    procs, stop = [], threading.Event()
    try:
        return run(tmp, procs, stop)
    finally:
        stop.set()
        for p in procs:
            p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def run(tmp, procs, stop):
    sh("ip", "link", "set", "lo", "up")
    client, home, carrier = new_ns(), new_ns(), new_ns()
    procs += [client, home, carrier]
    for p in (client, home, carrier):
        sh("ip", "link", "set", "lo", "up", ns=p.pid)

    # the device on the hotspot
    sh("ip", "link", "add", "ap0", "type", "veth", "peer", "name", "eth0", "netns", client.pid)
    # the home WiFi: the phone's default route
    sh("ip", "link", "add", "wlan0", "type", "veth", "peer", "name", "eth0", "netns", home.pid)
    sh("ip", "addr", "add", "192.0.2.50/24", "dev", "wlan0")
    sh("ip", "link", "set", "wlan0", "up")
    sh("ip", "route", "add", "default", "via", "192.0.2.1", "metric", "1024")
    sh("ip", "addr", "add", "192.0.2.1/24", "dev", "eth0", ns=home.pid)
    sh("ip", "link", "set", "eth0", "up", ns=home.pid)
    sh("ip", "addr", "add", "198.51.100.1/32", "dev", "lo", ns=home.pid)
    sh("ip", "-6", "addr", "add", "2001:db8:ffff::1/128", "dev", "lo", ns=home.pid)
    # a leak would come back: the home network routes the hotspot's subnet to the phone
    sh("ip", "route", "add", "10.15.43.0/24", "via", "192.0.2.50", ns=home.pid)

    # the mobile network: raw IP (TUN), as rmnet
    up_fd = tun("qmapmux0.0")
    box = {}

    def carrier_side():
        os.setns(os.open("/proc/%d/ns/net" % carrier.pid, os.O_RDONLY), os.CLONE_NEWNET)
        box["fd"] = tun("carrier0")
        box["ready"] = True
        pump(up_fd, box["fd"], stop)

    threading.Thread(target=carrier_side, daemon=True).start()
    for _ in range(100):
        if box.get("ready"):
            break
        time.sleep(0.02)
    sh("ip", "addr", "add", "100.64.1.2/32", "dev", "qmapmux0.0")
    sh("ip", "-6", "addr", "add", "2001:db8:1:2::5/64", "dev", "qmapmux0.0", "nodad")
    sh("ip", "link", "set", "qmapmux0.0", "up")
    sh(
        "ip", "route", "add", "default", "dev", "qmapmux0.0", "metric", "4096"
    )  # duo2-wwan's backup route
    sh("ip", "link", "set", "carrier0", "up", ns=carrier.pid)
    sh("ip", "route", "add", "100.64.1.2/32", "dev", "carrier0", ns=carrier.pid)
    sh("ip", "-6", "addr", "add", "2001:db8:1:2::1/64", "dev", "carrier0", "nodad", ns=carrier.pid)
    sh("ip", "addr", "add", "198.51.100.1/32", "dev", "lo", ns=carrier.pid)
    sh("ip", "-6", "addr", "add", "2001:db8:ffff::1/128", "dev", "lo", ns=carrier.pid)

    for name, p in (("mobile", carrier), ("home", home)):
        # bound to the "internet" addresses: replies come from where the query went
        procs.append(
            subprocess.Popen(
                [
                    "nsenter",
                    "-t",
                    str(p.pid),
                    "-n",
                    "python3",
                    "-c",
                    ECHO,
                    name,
                    "198.51.100.1",
                    "2001:db8:ffff::1",
                ]
            )
        )
    # the phone's own services: an "sshd" on 22
    procs.append(subprocess.Popen(["python3", "-c", ECHO.replace("7))", "22))"), "phone-ssh"]))

    # systemd-resolved's stub, played on 127.0.0.53
    import socket as so

    stub = so.socket(so.AF_INET, so.SOCK_DGRAM)
    sh("ip", "addr", "add", "127.0.0.53/8", "dev", "lo")
    stub.bind(("127.0.0.53", 53))

    def stub_serve():
        while not stop.is_set():
            r, _, _ = select.select([stub], [], [], 0.2)
            if r:
                q, peer = stub.recvfrom(512)
                stub.sendto(q[:2] + b"\x81\x80" + q[4:], peer)

    threading.Thread(target=stub_serve, daemon=True).start()

    # the daemon's shared part, for a WiFi device on ap0
    loader = importlib.machinery.SourceFileLoader(
        "duo2_hotspotd", os.path.join(ROOT, "duo2-hotspotd")
    )
    spec = importlib.util.spec_from_loader("duo2_hotspotd", loader)
    d = importlib.util.module_from_spec(spec)
    loader.exec_module(d)
    state.STATE = os.path.join(tmp, "hotspot.json")
    state.WANT = os.path.join(tmp, "want.json")
    d.SAVED = os.path.join(tmp, "saved.json")
    d.WAYDROID_NFT = os.path.join(os.path.dirname(ROOT), "android", "firewall.nft")

    class T(d.Hotspot):
        def inhibit(self):
            return None

    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    hs = T(relay_factory=lambda addrs: dnsrelay.Relay(addrs, log=print))
    ula = conf.ula_from_seed(bytes.fromhex("0102030405"))
    down = netplan.downstream("ap0", ipaddress.ip_network("10.15.43.0/24"), ula, 1)
    sh("ip", "addr", "add", "10.15.43.1/24", "dev", "ap0")
    sh("ip", "-6", "addr", "add", "%s/64" % down["addr6"], "dev", "ap0", "nodad")
    sh("ip", "link", "set", "ap0", "up")
    sh("ip", "addr", "add", "10.15.43.12/24", "dev", "eth0", ns=client.pid)
    sh("ip", "-6", "addr", "add", "fd01:203:405:1::12/64", "dev", "eth0", "nodad", ns=client.pid)
    sh("ip", "link", "set", "eth0", "up", ns=client.pid)
    sh("ip", "route", "add", "default", "via", "10.15.43.1", ns=client.pid)
    sh("ip", "-6", "route", "add", "default", "via", str(down["addr6"]), ns=client.pid)
    # Waydroid's table, as after an Android session
    sh("nft", "-f", d.WAYDROID_NFT)
    if not os.path.exists("/proc/sys/net/ipv6/conf/ap0/force_forwarding"):
        print(
            "note: no IPv6 force_forwarding in this kernel (%s): the namespace's global switch instead"
            % os.uname().release
        )
        for path, v in (
            ("/proc/sys/net/ipv6/conf/qmapmux0.0/accept_ra", "2"),
            ("/proc/sys/net/ipv6/conf/all/forwarding", "1"),
        ):
            with open(path, "w") as f:
                f.write(v)
    hs.wifi = {
        "down": down,
        "hold": None,
        "cd": {"band": "5", "chan": 36, "width": 80},
        "how": "own",
    }
    # IPv4 forwarding off everywhere (a new namespace inherits the machine's
    # switches): only the daemon's per-device switches may make it work
    with open("/proc/sys/net/ipv4/conf/all/forwarding", "w") as f:
        f.write("0")
    with open("/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding") as f:
        fwd_before = f.read().strip()
    hs.apply_shared()
    time.sleep(0.3)

    # before: what forwarding the phone does not do
    r = ask(client.pid, "198.51.100.1")
    check("device -> internet over MOBILE data, masqueraded", r == "mobile saw 100.64.1.2", r)
    r = ask(client.pid, "198.51.100.1", proto="tcp")
    check("TCP too", r == "mobile tcp saw 100.64.1.2", r)
    r = ask(client.pid, "2001:db8:ffff::1")
    check(
        "IPv6 over mobile data, NAT66 to the phone's mobile address",
        r == "mobile saw 2001:db8:1:2::5",
        r,
    )
    r = ask(client.pid, "192.0.2.1")
    check("device -/-> home LAN", "saw" not in r, r)
    r = ask(client.pid, "10.15.43.1", 22, "tcp")
    check("device -/-> the phone's ssh (hotspot address)", "saw" not in r, r)
    r = ask(client.pid, "192.0.2.50", 22, "tcp")
    check("device -/-> the phone's ssh (WiFi address)", "saw" not in r, r)
    r = dns_ask(client.pid, "10.15.43.1")
    check("DNS on the hotspot address (IPv4)", r == "1234", r)
    r = dns_ask(client.pid, str(down["addr6"]))
    check("DNS on the hotspot address (IPv6)", r == "1234", r)
    procs.append(
        subprocess.Popen(
            [
                "nsenter",
                "-t",
                str(client.pid),
                "-n",
                "python3",
                "-c",
                ECHO.replace("7))", "7))"),
                "device",
            ]
        )
    )
    time.sleep(0.3)
    r = ask(home.pid, "10.15.43.12")
    check("home LAN -/-> device", "saw" not in r, r)
    sh("ip", "route", "add", "10.15.43.0/24", "dev", "carrier0", ns=carrier.pid)
    r = ask(carrier.pid, "10.15.43.12")
    check("mobile network -/-> device (unasked)", "saw" not in r, r)

    # Waydroid's table without the exception: the hotspot's traffic is dropped
    with open(d.WAYDROID_NFT) as f:
        old = f.read().replace("\t\tmeta mark & 0x10000000 == 0x10000000 accept\n", "")
    sh("nft", "-f", "-", input=old)
    r = ask(client.pid, "198.51.100.1")
    check("(control) Waydroid's old table drops it", "saw" not in r, r)
    sh("nft", "-f", d.WAYDROID_NFT)
    r = ask(client.pid, "198.51.100.1")
    check("Waydroid's table with the mark lets it through", r == "mobile saw 100.64.1.2", r)

    # mobile data gone: nothing over WiFi
    sh("ip", "link", "set", "qmapmux0.0", "down")
    hs.after_change = lambda: None
    hs.apply_shared()
    r = ask(client.pid, "198.51.100.1")
    check("no mobile data: no way out over WiFi", "saw" not in r, r)
    r = ask(client.pid, "2001:db8:ffff::1")
    check("no mobile data: no IPv6 way out either", "saw" not in r, r)
    sh("ip", "link", "set", "qmapmux0.0", "up")
    sh("ip", "route", "replace", "default", "dev", "qmapmux0.0", "metric", "4096")
    hs.apply_shared()
    r = ask(client.pid, "198.51.100.1")
    check("mobile data back: over it again", r == "mobile saw 100.64.1.2", r)

    # the end: nothing of it stays
    hs.wifi = None
    hs.teardown_shared()
    out = sh("nft", "list", "tables")
    check("table removed", "duo2_hotspot" not in out, out)
    rules = sh("ip", "rule", "show")
    check("rules removed", "7240" not in rules, rules)
    with open("/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding") as f:
        check("forwarding switches back", f.read().strip() == fwd_before)
    r = ask(client.pid, "198.51.100.1")
    check("after the end: the device is cut off", "saw" not in r, r)
    loop.quit()
    print("%d failed" % len(FAILS) if FAILS else "all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
