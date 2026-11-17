#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-bt-trace: what the Surface pen and the phone say over Bluetooth, with
every address replaced by a role, so the output can be shared.

    pen-bt-trace.py capture [--seconds N] [--dir DIR]   btmon + the digitizer's
                                                        AutoBond reports, then analyze
    pen-bt-trace.py analyze FILE [--all]                a btsnoop (btmon -w) file
    pen-bt-trace.py state                               BlueZ's bond, the adapter

Run as root (the bond's IRK and the HCI monitor). The raw btsnoop file holds
link keys (LE Start Encryption): it stays in a root-only directory on the
phone (default /run/duo2-penbt) and is never copied off; only this script's
output is.

Roles: HOST (the adapter's public address), HOST-RND (a random address the
host set), PEN (the bond's identity address), PEN-RPA (a resolvable private
address that resolves with the pen's IRK), PEN-AB (the pen's per-host
AutoBond address for HOST: identity XOR host, top bits 11), ZERO, and R1,
R2, ... for everything else (with their kind: public / static / RPA / NRPA).
"""

import argparse
import datetime
import glob
import os
import select
import signal
import socket
import struct
import subprocess
import threading
import time

BTSNOOP_EPOCH_DELTA = 0x00DCDDB30F2F8000  # btsnoop µs (year 0) -> Unix µs
TRACE_DIR = "/run/duo2-penbt"


# --- addresses -> roles ---------------------------------------------------------------------


def aes_ecb(key: bytes, block: bytes) -> bytes:
    """One AES-128 block through the kernel (AF_ALG): no Python crypto package."""
    alg = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
    try:
        alg.bind(("skcipher", "ecb(aes)"))
        alg.setsockopt(socket.SOL_ALG, socket.ALG_SET_KEY, key)
        op, _ = alg.accept()
        try:
            op.sendmsg_afalg([block], op=socket.ALG_OP_ENCRYPT)
            return op.recv(16)
        finally:
            op.close()
    finally:
        alg.close()


def rpa_resolves(irk_be: bytes, addr_be: bytes) -> bool:
    """addr_be MSB first (as printed): prand = the top 3 bytes, hash = the low 3."""
    if len(addr_be) != 6 or (addr_be[0] >> 6) != 0b01:
        return False
    ct = aes_ecb(irk_be, bytes(13) + addr_be[:3])
    return ct[13:] == addr_be[3:]


def kind(addr_be: bytes, atype: int) -> str:
    if atype in (0, 2):
        return "public"
    top = addr_be[0] >> 6
    return {0b11: "static", 0b01: "RPA", 0b00: "NRPA"}.get(top, "random?")


def clock(x):
    """A wall-clock time as HH:MM:SS."""
    return datetime.datetime.fromtimestamp(x).strftime("%H:%M:%S")


class Roles:
    def __init__(self, host=None, pen=None, pen_irks=(), extra=None):
        self.host = host  # bytes, MSB first
        self.pen = pen
        self.pen_irks = list(pen_irks)
        self.pen_ab = None
        if host and pen:
            ab = bytearray(p ^ h for p, h in zip(pen, host))
            ab[0] |= 0xC0
            self.pen_ab = bytes(ab)
        self.fixed = dict(extra or {})
        self.host_random = set()
        self.others = {}
        self.cache = {}

    def name(self, addr_be: bytes, atype: int = 1) -> str:
        key = (addr_be, atype in (0, 2))
        if key in self.cache:
            return self.cache[key]
        n = self._name(addr_be, atype)
        self.cache[key] = n
        return n

    def _name(self, a, atype):
        if a == bytes(6):
            return "ZERO"
        if a == b"\xff" * 6:
            return "FF:FF:FF:FF:FF:FF"
        if a in self.fixed:
            return self.fixed[a]
        if self.host and a == self.host:
            return "HOST" + ("" if atype in (0, 2) else "(as random?)")
        if a in self.host_random:
            return "HOST-RND(%s)" % kind(a, 1)
        if self.pen and a == self.pen:
            return "PEN"
        if self.pen_ab and a == self.pen_ab:
            return "PEN-AB"
        if self.pen and a == self.pen[::-1]:
            return "PEN(byte-reversed)"
        if atype not in (0, 2):
            for i, irk in enumerate(self.pen_irks):
                try:
                    if rpa_resolves(irk, a):
                        return "PEN-RPA" if i == 0 else "PEN-RPA(irk order %d)" % i
                except OSError:
                    pass
        if a not in self.others:
            self.others[a] = "R%d(%s)" % (len(self.others) + 1, kind(a, atype))
        return self.others[a]


def btmgmt(*args):
    """btmgmt hangs with /dev/null (or no tty) as stdin: give it an empty pipe."""
    return subprocess.run(
        ["btmgmt", *args], input="", capture_output=True, text=True, timeout=10
    ).stdout


def bluez_roles() -> Roles:
    """The adapter's public address and the Surface pen's bond (BlueZ's files)."""
    host = None
    out = btmgmt("info")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("addr "):
            host = bytes(int(x, 16) for x in line.split()[1].split(":"))
            break
    pen, irks = None, []
    for info in glob.glob("/var/lib/bluetooth/*/*/info"):
        text = open(info).read()
        if "Surface" not in text:
            continue
        pen = bytes(int(x, 16) for x in os.path.basename(os.path.dirname(info)).split(":"))
        sect = None
        for line in text.splitlines():
            if line.startswith("["):
                sect = line.strip("[]")
            elif sect == "IdentityResolvingKey" and line.startswith("Key="):
                k = bytes.fromhex(line[4:].strip())
                irks = [k[::-1], k]  # BlueZ writes it LSB first; try both
        break
    return Roles(host, pen, irks)


# --- btsnoop / HCI --------------------------------------------------------------------------


def read_btsnoop(path):
    with open(path, "rb") as f:
        hdr = f.read(16)
        if hdr[:8] != b"btsnoop\0":
            raise SystemExit("%s: not a btsnoop file" % path)
        datalink = struct.unpack(">I", hdr[12:16])[0]
        if datalink != 2001:
            raise SystemExit("%s: datalink %d, expected 2001 (btmon -w)" % (path, datalink))
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                return
            orig, incl, flags, _drops, ts = struct.unpack(">IIIIq", rec)
            data = f.read(incl)
            if len(data) < incl:
                return
            yield (ts - BTSNOOP_EPOCH_DELTA) / 1e6, flags >> 16, flags & 0xFFFF, data


def le_addr(b):
    return bytes(b[::-1])


ADDR_TYPES = {0: "public", 1: "random", 2: "public-id", 3: "random-id", 0xFF: "anon"}
OWN_TYPES = {0: "public", 1: "random", 2: "RPA-or-public", 3: "RPA-or-random"}
SCAN_FILTER = {
    0: "accept all",
    1: "accept list",
    2: "all + directed to RPA",
    3: "accept list + directed to RPA",
}
DISC_REASONS = {
    0x08: "supervision timeout",
    0x13: "remote user terminated",
    0x16: "local host terminated",
    0x3E: "failed to establish",
    0x22: "LL response timeout",
    0x3B: "unacceptable conn params",
    0x05: "auth failure",
    0x06: "PIN/key missing",
    0x15: "remote power off",
    0x14: "remote low resources",
    0x1F: "unspecified",
    0x28: "instant passed",
    0x3D: "MIC failure",
}
ATT_OPS = {
    0x01: "Error Rsp",
    0x02: "Exchange MTU Req",
    0x03: "Exchange MTU Rsp",
    0x04: "Find Info Req",
    0x05: "Find Info Rsp",
    0x06: "Find By Type Value Req",
    0x07: "Find By Type Value Rsp",
    0x08: "Read By Type Req",
    0x09: "Read By Type Rsp",
    0x0A: "Read Req",
    0x0B: "Read Rsp",
    0x0C: "Read Blob Req",
    0x0D: "Read Blob Rsp",
    0x0E: "Read Multiple Req",
    0x0F: "Read Multiple Rsp",
    0x10: "Read By Group Type Req",
    0x11: "Read By Group Type Rsp",
    0x12: "Write Req",
    0x13: "Write Rsp",
    0x16: "Prepare Write Req",
    0x17: "Prepare Write Rsp",
    0x18: "Execute Write Req",
    0x19: "Execute Write Rsp",
    0x1B: "Notification",
    0x1D: "Indication",
    0x1E: "Confirmation",
    0x52: "Write Cmd",
    0xD2: "Signed Write Cmd",
}
SMP_OPS = {
    0x01: "Pairing Request",
    0x02: "Pairing Response",
    0x03: "Pairing Confirm",
    0x04: "Pairing Random",
    0x05: "Pairing Failed",
    0x06: "Encryption Info",
    0x07: "Central Identification",
    0x08: "Identity Info",
    0x09: "Identity Address Info",
    0x0A: "Signing Info",
    0x0B: "Security Request",
    0x0C: "Pairing Public Key",
    0x0D: "DHKey Check",
    0x0E: "Keypress",
}


def adv_type_ext(t):
    if t & 0x10:  # legacy PDU
        legacy = {
            0x13: "ADV_IND",
            0x15: "ADV_DIRECT_IND",
            0x12: "ADV_SCAN_IND",
            0x10: "ADV_NONCONN_IND",
            0x1B: "SCAN_RSP(ADV_IND)",
            0x1A: "SCAN_RSP(ADV_SCAN_IND)",
        }
        return legacy.get(t & 0x1F, "legacy 0x%02x" % t)
    bits = [
        n for b, n in ((0, "conn"), (1, "scan"), (2, "directed"), (3, "scan-rsp")) if t & (1 << b)
    ]
    return "EXT[" + ",".join(bits or ["nonconn"]) + "]"


def ad_summary(data):
    out, i = [], 0
    while i < len(data):
        ln = data[i]
        if ln == 0 or i + 1 + ln > len(data):
            break
        t, v = data[i + 1], data[i + 2 : i + 1 + ln]
        if t == 0x01:
            out.append("flags=%02x" % v[0] if v else "flags")
        elif t in (0x08, 0x09):
            out.append("name=%r" % v.decode("utf-8", "replace"))
        elif t == 0x19 and len(v) >= 2:
            out.append("appearance=%04x" % struct.unpack("<H", v[:2]))
        elif t in (0x02, 0x03):
            out.append(
                "uuid16="
                + ",".join(
                    "%04x" % struct.unpack("<H", v[j : j + 2]) for j in range(0, len(v) - 1, 2)
                )
            )
        elif t == 0xFF and len(v) >= 2:
            out.append("mfr=%04x+%d:%s" % (struct.unpack("<H", v[:2])[0], len(v) - 2, v[2:5].hex()))
        elif t == 0x16 and len(v) >= 2:
            out.append("svcdata=%04x+%d" % (struct.unpack("<H", v[:2])[0], len(v) - 2))
        elif t == 0x0A and v:
            out.append("txpwr=%d" % struct.unpack("b", v[:1]))
        else:
            out.append("ad%02x+%d" % (t, len(v)))
        i += 1 + ln
    return " ".join(out)


class Analyzer:
    def __init__(self, roles: Roles, show_all=False):
        self.r = roles
        self.all = show_all
        self.handles = {}
        self.pending = {}  # opcode -> description awaiting status
        self.counts = {}
        self.lines = []
        self.last_seen = {}
        self.sources = {}

    def emit(self, t, text):
        stamp = datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
        self.lines.append("%s %s" % (stamp, text))
        print("%s %s" % (stamp, text), flush=True)

    def who(self, a_le, atype):
        return self.r.name(le_addr(a_le), atype)

    def interesting(self, name):
        return name.startswith(("PEN", "HOST"))

    # -- commands
    def command(self, t, d):
        if len(d) < 3:
            return
        op, plen = struct.unpack("<HB", d[:3])
        p = d[3 : 3 + plen]
        ogf, ocf = op >> 10, op & 0x3FF
        txt = None
        if op == 0x2005 and len(p) >= 6:
            a = le_addr(p[:6])
            if a != self.r.host and a not in (self.r.pen,):
                self.r.host_random.add(a)
                self.r.cache.clear()
            txt = "LE Set Random Address %s" % self.r.name(a, 1)
        elif op == 0x200B and len(p) >= 7:
            typ, iv, win, own, fp = struct.unpack("<BHHBB", p[:7])
            txt = "LE Set Scan Params %s int %.2f ms win %.2f ms own %s filter %s" % (
                "active" if typ else "passive",
                iv * 0.625,
                win * 0.625,
                OWN_TYPES.get(own, own),
                SCAN_FILTER.get(fp, fp),
            )
        elif op == 0x200C and len(p) >= 2:
            txt = "LE Set Scan Enable %s dup-filter %d" % ("ON" if p[0] else "off", p[1])
        elif op == 0x2041 and len(p) >= 3:
            own, fp, phys = p[0], p[1], p[2]
            parts, j = [], 3
            for ph, nm in ((0, "1M"), (2, "Coded")):
                if phys & (1 << ph) and j + 5 <= len(p):
                    typ, iv, win = struct.unpack("<BHH", p[j : j + 5])
                    parts.append(
                        "%s %s %.2f/%.2f ms"
                        % (nm, "active" if typ else "passive", iv * 0.625, win * 0.625)
                    )
                    j += 5
            txt = "LE Set Ext Scan Params own %s filter %s [%s]" % (
                OWN_TYPES.get(own, own),
                SCAN_FILTER.get(fp, fp),
                "; ".join(parts),
            )
        elif op == 0x2042 and len(p) >= 2:
            txt = "LE Set Ext Scan Enable %s dup-filter %d" % ("ON" if p[0] else "off", p[1])
        elif op == 0x200D and len(p) >= 25:
            fp, ptype = p[4], p[5]
            own = p[12]
            txt = "LE Create Conn to %s (%s) own %s initiator-filter %s" % (
                self.who(p[6:12], ptype),
                ADDR_TYPES.get(ptype, ptype),
                OWN_TYPES.get(own, own),
                "accept list" if fp else "peer",
            )
        elif op == 0x2043 and len(p) >= 10:
            fp, own, ptype = p[0], p[1], p[2]
            txt = "LE Ext Create Conn to %s (%s) own %s initiator-filter %s phys %02x" % (
                self.who(p[3:9], ptype),
                ADDR_TYPES.get(ptype, ptype),
                OWN_TYPES.get(own, own),
                "accept list" if fp else "peer",
                p[9],
            )
        elif op == 0x200E:
            txt = "LE Create Conn Cancel"
        elif op == 0x2010:
            txt = "LE Clear Accept List"
        elif op in (0x2011, 0x2012) and len(p) >= 7:
            txt = "LE %s Accept List: %s (%s)" % (
                "Add to" if op == 0x2011 else "Remove from",
                self.who(p[1:7], p[0]),
                ADDR_TYPES.get(p[0], p[0]),
            )
        elif op == 0x2027 and len(p) >= 39:
            txt = "LE Add to Resolving List: %s (%s), local IRK %s" % (
                self.who(p[1:7], p[0]),
                ADDR_TYPES.get(p[0], p[0]),
                "zero" if p[23:39] == bytes(16) else "set",
            )
        elif op == 0x2028 and len(p) >= 7:
            txt = "LE Remove from Resolving List: %s" % self.who(p[1:7], p[0])
        elif op == 0x2029:
            txt = "LE Clear Resolving List"
        elif op == 0x202D and p:
            txt = "LE Set Address Resolution %s" % ("ON" if p[0] else "off")
        elif op == 0x204E and len(p) >= 8:
            txt = "LE Set Privacy Mode %s: %s" % (
                self.who(p[1:7], p[0]),
                "device" if p[7] else "network",
            )
        elif op == 0x2013 and len(p) >= 10:
            h, mn, mx, lat, to = struct.unpack("<HHHHH", p[:10])
            txt = "LE Connection Update h%d int %.2f-%.2f ms lat %d timeout %d ms" % (
                h,
                mn * 1.25,
                mx * 1.25,
                lat,
                to * 10,
            )
        elif op == 0x2020 and len(p) >= 10:
            h, mn, mx, lat, to = struct.unpack("<HHHHH", p[:10])
            txt = "LE Remote Conn Param Reply h%d int %.2f-%.2f ms lat %d timeout %d ms" % (
                h,
                mn * 1.25,
                mx * 1.25,
                lat,
                to * 10,
            )
        elif op == 0x2021 and len(p) >= 3:
            txt = "LE Remote Conn Param NEG Reply h%d reason %02x" % (
                struct.unpack("<H", p[:2])[0],
                p[2],
            )
        elif op == 0x2019:
            txt = "LE Enable Encryption h%d (key not shown)" % struct.unpack("<H", p[:2])[0]
        elif op == 0x201A:
            txt = "LE LTK Request Reply (key not shown)"
        elif op == 0x201B:
            txt = "LE LTK Request NEG Reply"
        elif op == 0x0406 and len(p) >= 3:
            txt = "Disconnect h%d reason %02x %s" % (
                struct.unpack("<H", p[:2])[0],
                p[2],
                DISC_REASONS.get(p[2], ""),
            )
        elif op == 0x2032 and len(p) >= 7:
            txt = "LE Set PHY h%d all %02x tx %02x rx %02x" % (
                struct.unpack("<H", p[:2])[0],
                p[2],
                p[3],
                p[4],
            )
        elif op == 0x2022 and len(p) >= 6:
            txt = "LE Set Data Length h%d" % struct.unpack("<H", p[:2])[0]
        elif ogf == 0x08 and ocf in (0x06, 0x08, 0x09, 0x0A, 0x36, 0x37, 0x38, 0x39, 0x35):
            txt = None  # advertising set up: not ours to care about
        if txt:
            self.pending[op] = txt
            self.emit(t, "> " + txt)

    # -- events
    def event(self, t, d):
        if len(d) < 2:
            return
        code, plen = d[0], d[1]
        p = d[2 : 2 + plen]
        if code == 0x0E and len(p) >= 4:
            op = struct.unpack("<H", p[1:3])[0]
            if op in self.pending:
                st = p[3]
                if st:
                    self.emit(t, "< status %02x for: %s" % (st, self.pending[op]))
                del self.pending[op]
        elif code == 0x0F and len(p) >= 4:
            op = struct.unpack("<H", p[2:4])[0]
            if op in self.pending:
                if p[0]:
                    self.emit(t, "< status %02x for: %s" % (p[0], self.pending[op]))
                del self.pending[op]
        elif code == 0x05 and len(p) >= 4:
            st, h, reason = p[0], struct.unpack("<H", p[1:3])[0], p[3]
            self.emit(
                t,
                "< Disconnect Complete h%d %s reason %02x %s"
                % (h, self.handles.get(h, "?"), reason, DISC_REASONS.get(reason, "")),
            )
        elif code in (0x08, 0x59) and len(p) >= 4:
            self.emit(
                t,
                "< Encryption Change h%d %s status %02x enabled %d"
                % (
                    struct.unpack("<H", p[1:3])[0],
                    self.handles.get(struct.unpack("<H", p[1:3])[0], "?"),
                    p[0],
                    p[3],
                ),
            )
        elif code == 0x3E and p:
            self.le_meta(t, p[0], p[1:])

    def le_meta(self, t, sub, p):
        if sub in (0x01, 0x0A, 0x29) and len(p) >= 11:
            st, h, role, ptype = p[0], struct.unpack("<H", p[1:3])[0], p[3], p[4]
            peer = self.who(p[5:11], ptype)
            extra = ""
            j = 11
            if sub in (0x0A, 0x29) and len(p) >= 23:
                lrpa, prpa = le_addr(p[11:17]), le_addr(p[17:23])
                extra = " local-RPA %s peer-RPA %s" % (self.r.name(lrpa, 1), self.r.name(prpa, 1))
                j = 23
            if len(p) >= j + 6:
                iv, lat, to = struct.unpack("<HHH", p[j : j + 6])
                extra += " int %.2f ms lat %d timeout %d ms" % (iv * 1.25, lat, to * 10)
            if st == 0:
                self.handles[h] = peer
            self.emit(
                t,
                "< LE Connection Complete status %02x h%d %s peer %s (%s)%s"
                % (
                    st,
                    h,
                    "central" if role == 0 else "peripheral",
                    peer,
                    ADDR_TYPES.get(ptype, ptype),
                    extra,
                ),
            )
        elif sub == 0x03 and len(p) >= 9:
            st, h, iv, lat, to = struct.unpack("<BHHHH", p[:9])
            self.emit(
                t,
                "< LE Conn Update Complete h%d status %02x int %.2f ms lat %d timeout %d ms"
                % (h, st, iv * 1.25, lat, to * 10),
            )
        elif sub == 0x06 and len(p) >= 10:
            h, mn, mx, lat, to = struct.unpack("<HHHHH", p[:10])
            self.emit(
                t,
                "< LE Remote Conn Param Request h%d int %.2f-%.2f ms lat %d timeout %d ms"
                % (h, mn * 1.25, mx * 1.25, lat, to * 10),
            )
        elif sub == 0x0C and len(p) >= 5:
            self.emit(
                t,
                "< LE PHY Update h%d status %02x tx %d rx %d"
                % (struct.unpack("<H", p[1:3])[0], p[0], p[3], p[4]),
            )
        elif sub == 0x05:
            self.emit(t, "< LE LTK Request (not shown)")
        elif sub == 0x0D and p:
            n, j = p[0], 1
            for _ in range(n):
                if j + 24 > len(p):
                    break
                et = struct.unpack("<H", p[j : j + 2])[0]
                atype = p[j + 2]
                a = le_addr(p[j + 3 : j + 9])
                pphy, rssi = p[j + 9], struct.unpack("b", p[j + 13 : j + 14])[0]
                dtype, da = p[j + 16], le_addr(p[j + 17 : j + 23])
                dl = p[j + 23]
                data = p[j + 24 : j + 24 + dl]
                j += 24 + dl
                self.adv(
                    t,
                    adv_type_ext(et),
                    a,
                    atype,
                    da if da != bytes(6) else None,
                    dtype,
                    rssi,
                    data,
                    "1M" if pphy == 1 else ("Coded" if pphy == 3 else str(pphy)),
                )
        elif sub == 0x02 and p:
            n, j = p[0], 1
            legacy = {
                0: "ADV_IND",
                1: "ADV_DIRECT_IND",
                2: "ADV_SCAN_IND",
                3: "ADV_NONCONN_IND",
                4: "SCAN_RSP",
            }
            for _ in range(n):
                if j + 9 > len(p):
                    break
                et, atype, a, dl = p[j], p[j + 1], le_addr(p[j + 2 : j + 8]), p[j + 8]
                data = p[j + 9 : j + 9 + dl]
                rssi = (
                    struct.unpack("b", p[j + 9 + dl : j + 10 + dl])[0]
                    if j + 10 + dl <= len(p)
                    else 0
                )
                j += 10 + dl
                self.adv(t, legacy.get(et, str(et)), a, atype, None, 0, rssi, data, "1M")
        elif sub == 0x0B and p:
            n, j = p[0], 1
            for _ in range(n):
                if j + 16 > len(p):
                    break
                et, atype, a = p[j], p[j + 1], le_addr(p[j + 2 : j + 8])
                dtype, da, rssi = (
                    p[j + 8],
                    le_addr(p[j + 9 : j + 15]),
                    struct.unpack("b", p[j + 15 : j + 16])[0],
                )
                j += 16
                self.adv(t, "DIRECT-REPORT", a, atype, da, dtype, rssi, b"", "1M")

    def adv(self, t, typ, a, atype, da, dtype, rssi, data, phy):
        src = self.r.name(a, atype)
        tgt = self.r.name(da, dtype) if da else None
        summ = ad_summary(data)
        key = (src, typ, tgt, phy)
        self.counts[key] = self.counts.get(key, 0) + 1
        st = self.sources.setdefault(
            src,
            {
                "first": t,
                "last": t,
                "n": 0,
                "rssi": -127,
                "types": set(),
                "targets": set(),
                "data": set(),
                "bursts": [],
            },
        )
        if not st["bursts"] or t - st["last"] > 2.0:
            st["bursts"].append(t)
        st["last"] = t
        st["n"] += 1
        st["rssi"] = max(st["rssi"], rssi)
        st["types"].add("%s/%s" % (typ, phy))
        if tgt:
            st["targets"].add(tgt)
        if summ and len(st["data"]) < 3:
            st["data"].add(summ)
        notable = (
            self.interesting(src)
            or tgt is not None
            or "Surface" in summ
            or "mfr=0006" in summ
            or "uuid16=1812" in summ
            or self.all
        )
        if not notable:
            return
        # print each notable source/type at most once a second
        last = self.last_seen.get(key, 0)
        if t - last < 1.0 and not self.all:
            return
        self.last_seen[key] = t
        self.emit(
            t,
            "  adv %s %s from %s%s rssi %d %s"
            % (phy, typ, src, (" -> " + tgt) if tgt else "", rssi, summ),
        )

    # -- ACL
    def acl(self, t, d, tx):
        if len(d) < 8:
            return
        hf, ln = struct.unpack("<HH", d[:4])
        h, pb = hf & 0x0FFF, (hf >> 12) & 0x3
        if pb == 0x01:  # continuation fragment
            return
        l2len, cid = struct.unpack("<HH", d[4:8])
        pl = d[8:]
        who = self.handles.get(h, "h%d" % h)
        arrow = "host->%s" % who if tx else "%s->host" % who
        if cid == 0x0004 and pl:
            op = pl[0]
            name = ATT_OPS.get(op, "ATT 0x%02x" % op)
            info = ""
            if op in (0x08, 0x10) and len(pl) >= 7:
                s, e = struct.unpack("<HH", pl[1:5])
                u = pl[5:]
                info = " %04x-%04x type %s" % (
                    s,
                    e,
                    ("%04x" % struct.unpack("<H", u[:2])) if len(u) == 2 else u[::-1].hex(),
                )
            elif op in (0x0A, 0x12, 0x52, 0x1B, 0x1D, 0x0C, 0x04) and len(pl) >= 3:
                info = " handle %04x len %d" % (struct.unpack("<H", pl[1:3])[0], len(pl) - 3)
            elif op == 0x01 and len(pl) >= 5:
                info = " for %02x handle %04x error %02x" % (
                    pl[1],
                    struct.unpack("<H", pl[2:4])[0],
                    pl[4],
                )
            elif op in (0x02, 0x03) and len(pl) >= 3:
                info = " mtu %d" % struct.unpack("<H", pl[1:3])
            elif op == 0x06 and len(pl) >= 7:
                info = " %04x-%04x type %04x" % struct.unpack("<HHH", pl[1:7])
            # notifications are many: count them, print the first of each handle per 10 s
            if op == 0x1B:
                key = ("ntf", who, pl[1:3])
                last = self.last_seen.get(key, 0)
                self.counts[key] = self.counts.get(key, 0) + 1
                if t - last < 10:
                    return
                self.last_seen[key] = t
            self.emit(t, "  ATT %s %s%s" % (arrow, name, info))
        elif cid == 0x0005 and len(pl) >= 4:
            code, slen = pl[0], struct.unpack("<H", pl[2:4])[0]
            body = pl[4 : 4 + slen]
            if code == 0x12 and len(body) >= 8:
                mn, mx, lat, to = struct.unpack("<HHHH", body[:8])
                self.emit(
                    t,
                    "  L2CAP %s Conn Param Update Req int %.2f-%.2f ms lat %d timeout %d ms"
                    % (arrow, mn * 1.25, mx * 1.25, lat, to * 10),
                )
            elif code == 0x13 and len(body) >= 2:
                self.emit(
                    t,
                    "  L2CAP %s Conn Param Update Rsp %s"
                    % (arrow, "accepted" if body[0] == 0 else "REJECTED"),
                )
            else:
                self.emit(t, "  L2CAP %s signal code %02x" % (arrow, code))
        elif cid == 0x0006 and pl:
            op = pl[0]
            info = ""
            if op in (0x01, 0x02) and len(pl) >= 7:
                info = " io %d oob %d auth %02x maxkey %d init-dist %02x resp-dist %02x" % tuple(
                    pl[1:7]
                )
            elif op == 0x05 and len(pl) >= 2:
                info = " reason %02x" % pl[1]
            elif op == 0x0B and len(pl) >= 2:
                info = " auth %02x" % pl[1]
            elif op == 0x09 and len(pl) >= 8:
                info = " %s" % self.who(pl[2:8], pl[1])
            self.emit(t, "  SMP %s %s%s" % (arrow, SMP_OPS.get(op, "0x%02x" % op), info))
        elif self.all:
            self.emit(t, "  L2CAP %s cid %04x len %d" % (arrow, cid, l2len))

    def note(self, t, d, user=False):
        try:
            if user:
                # priority(1) ident_len(1) ident ... message
                il = d[1]
                ident = d[2 : 2 + il].rstrip(b"\0").decode("utf-8", "replace")
                msg = d[2 + il :].rstrip(b"\0").decode("utf-8", "replace")
                if ident in ("bluetoothd", "kernel"):
                    self.emit(t, "  log %s: %s" % (ident, self.mask_text(msg)))
            else:
                self.emit(
                    t, "  note: %s" % self.mask_text(d.rstrip(b"\0").decode("utf-8", "replace"))
                )
        except (IndexError, UnicodeDecodeError):
            pass

    def mask_text(self, s):
        import re

        def repl(m):
            a = bytes(int(x, 16) for x in m.group(0).replace("_", ":").split(":"))
            return self.r.name(a, 1)

        return re.sub(r"([0-9A-Fa-f]{2}[:_]){5}[0-9A-Fa-f]{2}", repl, s)

    def run(self, path):
        for t, _idx, op, d in read_btsnoop(path):
            if op == 2:
                self.command(t, d)
            elif op == 3:
                self.event(t, d)
            elif op in (4, 5):
                self.acl(t, d, op == 4)
            elif op == 12:
                self.note(t, d)
            elif op == 13:
                self.note(t, d, user=True)
        print("--- advertising seen (source, type, target, phy: count) ---")
        for key, n in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            if key[0] == "ntf":
                print("notifications %s handle %s: %d" % (key[1], key[2][::-1].hex(), n))
            elif self.interesting(key[0]) or key[2] is not None or self.all:
                print(
                    "%s %s%s %s: %d"
                    % (key[0], key[1], (" -> " + key[2]) if key[2] else "", key[3], n)
                )
        print("--- near sources (best RSSI >= -70 dBm, or the pen's), by first sighting ---")
        for src, st in sorted(self.sources.items(), key=lambda kv: kv[1]["first"]):
            if st["rssi"] < -70 and not self.interesting(src) and not st["targets"]:
                continue
            print(
                "%s: %d reports, best %d dBm, bursts at %s, %s%s %s"
                % (
                    src,
                    st["n"],
                    st["rssi"],
                    ",".join(clock(b) for b in st["bursts"][:12]),
                    ",".join(sorted(st["types"])),
                    (" -> " + ",".join(sorted(st["targets"]))) if st["targets"] else "",
                    " | ".join(sorted(st["data"])),
                )
            )
        others = sum(
            n
            for k, n in self.counts.items()
            if k[0] != "ntf" and not self.interesting(k[0]) and k[2] is None
        )
        print("other devices' adverts: %d reports from %d addresses" % (others, len(self.r.others)))


# --- the digitizer's AutoBond reports --------------------------------------------------------


def digitizer_hidraw():
    for p in glob.glob("/sys/bus/hid/devices/001C:045E:0C1D.*/hidraw/hidraw*"):
        return "/dev/" + os.path.basename(p)
    return None


def watch_digitizer(roles: Roles, stop: threading.Event, log):
    """Read-only copy of the digitizer's input reports: 0x6e (a pen in range:
    id, address, info), 0x58 (pen tail). Nothing is written."""
    dev = digitizer_hidraw()
    if not dev:
        log("digitizer: no hidraw")
        return
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    seen_ids = {}
    last = {}
    try:
        while not stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                buf = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not buf:
                continue
            rid = buf[0]
            now = time.time()
            if rid == 0x6E and len(buf) >= 12:
                pid = bytes(buf[1:5])
                if pid not in seen_ids:
                    seen_ids[pid] = "PENID%d" % (len(seen_ids) + 1)
                addr_lsb = bytes(buf[5:11])
                a = roles.name(addr_lsb[::-1], 1)
                info = buf[11]
                key = (pid, addr_lsb, info)
                if now - last.get(key, 0) < 2:
                    continue
                last[key] = now
                flags = [
                    n
                    for b, n in (
                        (0, "public-addr"),
                        (1, "paired-with-host"),
                        (2, "autobond-capable"),
                    )
                    if info & (1 << b)
                ]
                log(
                    "digitizer 0x6e pen in range: id %s addr %s info %02x [%s] (%d bytes)"
                    % (seen_ids[pid], a, info, ",".join(flags), len(buf))
                )
            elif rid == 0x58 and len(buf) >= 3:
                log("digitizer 0x58 pen tail %02x %02x" % (buf[1], buf[2]))
    finally:
        os.close(fd)


def user_channel_scan(seconds, log, roles: Roles, stop: threading.Event):
    """An unfiltered passive scan on the controller itself, for diagnosis: BlueZ's
    background scan only lets its accept list through. The adapter is powered
    off through mgmt, opened as an HCI user channel (exclusive), scanned with
    own address = the public one (so directed adverts to HOST are reported),
    filter policy 0x02 (all, plus directed adverts to any RPA), 1M + Coded,
    no duplicate filter; then closed and powered on again. btmon records it."""
    import ctypes

    btmgmt("power", "off")
    time.sleep(1)
    libc = ctypes.CDLL(None, use_errno=True)

    class SockaddrHci(ctypes.Structure):
        _fields_ = [
            ("family", ctypes.c_ushort),
            ("dev", ctypes.c_ushort),
            ("channel", ctypes.c_ushort),
        ]

    fd = libc.socket(31, 3 | 0o2000000, 1)  # AF_BLUETOOTH, SOCK_RAW|CLOEXEC, BTPROTO_HCI
    if fd < 0:
        log("user channel: socket: %s" % os.strerror(ctypes.get_errno()))
        return
    try:
        sa = SockaddrHci(31, 0, 1)  # hci0, HCI_CHANNEL_USER
        if libc.bind(fd, ctypes.byref(sa), ctypes.sizeof(sa)) < 0:
            log("user channel: bind: %s" % os.strerror(ctypes.get_errno()))
            return

        def cmd(op, params=b"", wait=True):
            os.write(fd, b"\x01" + struct.pack("<HB", op, len(params)) + params)
            if not wait:
                return b""
            end = time.time() + 3
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.2)
                if not r:
                    continue
                pkt = os.read(fd, 1100)
                if pkt[:1] == b"\x04" and pkt[1] == 0x0E and struct.unpack("<H", pkt[4:6])[0] == op:
                    return pkt[6:]
                if pkt[:1] == b"\x04" and pkt[1] == 0x0F and struct.unpack("<H", pkt[5:7])[0] == op:
                    return pkt[3:4]
            log("user channel: no reply to %04x" % op)
            return None

        rp = cmd(0x1009)  # Read BD_ADDR
        if rp and len(rp) >= 7:
            log("user channel: controller address %s" % roles.name(rp[1:7][::-1], 0))
        for op, p in (
            (0x0C01, struct.pack("<Q", 0x20001FFFFFFFFFFF)),  # event mask + LE meta
            (0x2001, struct.pack("<Q", 0x141F)),  # + direct / ext adv reports
            (
                0x2041,
                bytes([0x00, 0x02, 0x05, 0x00])
                + struct.pack("<HH", 0x60, 0x30)
                + bytes([0x00])
                + struct.pack("<HH", 0x60, 0x30),
            ),
            (0x2042, bytes([0x01, 0x00]) + struct.pack("<HH", 0, 0)),
        ):
            rp = cmd(op, p)
            if not rp or rp[0] != 0:
                log("user channel: command %04x status %s" % (op, rp[:1].hex() if rp else "none"))
        log("user channel: unfiltered passive scan for %d s" % seconds)
        end = time.time() + seconds
        while time.time() < end and not stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                os.read(fd, 1100)  # btmon has it
        cmd(0x2042, bytes([0x00, 0x00]) + struct.pack("<HH", 0, 0))
    finally:
        os.close(fd)
        time.sleep(1)
        out = btmgmt("power", "on")
        log(
            "user channel closed; power on: %s"
            % (out.strip().splitlines() or ["?"])[-1].split(":")[0]
        )


def le_discovery(seconds, log, stop: threading.Event):
    """BlueZ stays up: the kernel's LE discovery (btmgmt find -l, ~10 s a round, again
    until the time is up) scans actively without the accept list, from a random
    (non-resolvable) own address, so every undirected advert near the phone is
    reported, while the pen's reconnection works as ever (the kernel checks its
    pending LE connections on every advert). Directed adverts to the host's public
    address are not seen this way (the rawscan sees them, with Bluetooth off)."""
    end = time.time() + seconds
    rounds = 0
    log("LE discovery (BlueZ up) for %d s" % seconds)
    while time.time() < end and not stop.is_set():
        try:
            subprocess.run(
                ["btmgmt", "find", "-l"], input="", capture_output=True, text=True, timeout=15
            )
        except subprocess.TimeoutExpired:
            pass
        rounds += 1
    btmgmt("stop-find", "-l")
    log("LE discovery done: %d rounds" % rounds)


def cmd_capture(args):
    os.makedirs(args.dir, mode=0o700, exist_ok=True)
    os.chmod(args.dir, 0o700)
    trace = os.path.join(args.dir, "trace-%s.btsnoop" % time.strftime("%H%M%S"))
    roles = bluez_roles()
    events = []

    def log(s):
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        events.append("%s %s" % (stamp, s))
        print("%s %s" % (stamp, s), flush=True)

    btmon = subprocess.Popen(
        ["btmon", "-w", trace], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    stop = threading.Event()
    th = threading.Thread(target=watch_digitizer, args=(roles, stop, log), daemon=True)
    th.start()
    log("capturing for %d s into %s (btmon pid %d)" % (args.seconds, trace, btmon.pid))
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    end = time.time() + args.seconds
    if args.rawscan:
        time.sleep(1)
        user_channel_scan(args.rawscan, log, roles, stop)
    elif args.discover:
        le_discovery(args.discover, log, stop)
    try:
        while time.time() < end and not stop.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    btmon.send_signal(signal.SIGINT)
    try:
        btmon.wait(5)
    except subprocess.TimeoutExpired:
        btmon.kill()
    th.join(2)
    os.chmod(trace, 0o600)
    log("--- HCI timeline ---")
    Analyzer(roles, args.all).run(trace)


def cmd_analyze(args):
    Analyzer(bluez_roles(), args.all).run(args.file)


def cmd_state(args):
    roles = bluez_roles()
    print("host public address known:", roles.host is not None)
    print(
        "pen bond:",
        "yes" if roles.pen else "no",
        "kind",
        kind(roles.pen, 1) if roles.pen else "-",
        "IRK",
        "yes" if roles.pen_irks else "no",
    )
    for line in btmgmt("info").splitlines():
        if "current settings" in line:
            print(line.strip())
    if roles.pen:
        a = ":".join("%02X" % b for b in roles.pen)
        out = subprocess.run(["bluetoothctl", "info", a], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if any(
                k in line
                for k in ("Paired", "Bonded", "Trusted", "Connected", "WakeAllowed", "Blocked")
            ):
                print(line.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--seconds", type=int, default=120)
    c.add_argument("--dir", default=TRACE_DIR)
    c.add_argument("--all", action="store_true")
    c.add_argument(
        "--rawscan",
        type=int,
        default=0,
        metavar="S",
        help="first S seconds: BlueZ off, an unfiltered scan of our own (diagnosis)",
    )
    c.add_argument(
        "--discover",
        type=int,
        default=0,
        metavar="S",
        help="first S seconds: the kernel's LE discovery, BlueZ up (undirected adverts)",
    )
    a = sub.add_parser("analyze")
    a.add_argument("file")
    a.add_argument("--all", action="store_true")
    sub.add_parser("state")
    args = ap.parse_args()
    {"capture": cmd_capture, "analyze": cmd_analyze, "state": cmd_state}[args.cmd](args)


if __name__ == "__main__":
    main()
