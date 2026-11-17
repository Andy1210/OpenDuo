#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-ab-check: does the stock HAL's rule turn the pen id of the digitizer's
AutoBond report (0x6e) into the pen's Bluetooth identity address? Diagnosis
only, read-only: the digitizer's hidraw is read (no report is written), the
bond is read from BlueZ's storage, and nothing but yes / no is printed (no
pen id, no address, no key).

    sudo pen-ab-check.py --key FILE [--seconds N]

The rule (vendor.surface.touchpen@1.3-service, AutoBondDevice::
convertPenIdToBTMac): d = SHA-1(K || the 4 id bytes of 0x6e in reverse
order), the static address (most significant byte first) = d[5] | 0xC0,
d[4], d[3], d[2], d[1], d[0]. K is 16 bytes of the HAL's .data, read from
the phone's own stock image (FILE: root's, never in git or in a log).
"""

import argparse
import glob
import hashlib
import os
import select
import time


def static_from_pen_id(key, pen_id_report):
    """pen_id_report: 0x6e's bytes 1..4 as they come. Returns the address MSB first."""
    d = hashlib.sha1(key + bytes(reversed(pen_id_report))).digest()
    return bytes([d[5] | 0xC0, d[4], d[3], d[2], d[1], d[0]])


def autobond_address(static_msb, host_msb):
    """The pen's per-host address: static XOR host, the top two bits 11."""
    ab = bytearray(a ^ b for a, b in zip(static_msb, host_msb))
    ab[0] |= 0xC0
    return bytes(ab)


def bonded_pens():
    """[(adapter MSB, identity MSB)] of the bonded Surface pens (static identity)."""
    out = []
    for info in glob.glob("/var/lib/bluetooth/*/*/info"):
        try:
            text = open(info).read()
        except OSError:
            continue
        if "Surface" not in text or "Pen" not in text or "AddressType=static" not in text:
            continue
        dev = os.path.basename(os.path.dirname(info))
        adapter = os.path.basename(os.path.dirname(os.path.dirname(info)))
        out.append(
            (
                bytes(int(x, 16) for x in adapter.split(":")),
                bytes(int(x, 16) for x in dev.split(":")),
            )
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--seconds", type=int, default=120)
    a = ap.parse_args()
    key = open(a.key, "rb").read()
    if len(key) != 16:
        raise SystemExit("the key is not 16 bytes")
    nodes = glob.glob("/sys/bus/hid/devices/001C:045E:0C1D.*/hidraw/hidraw*")
    if not nodes:
        raise SystemExit("no digitizer hidraw")
    pens = bonded_pens()
    print("bonded Surface pens with a static identity: %d" % len(pens), flush=True)
    fd = os.open("/dev/" + os.path.basename(nodes[0]), os.O_RDONLY | os.O_NONBLOCK)
    seen = {}
    end = time.time() + a.seconds
    try:
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                buf = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if len(buf) < 12 or buf[0] != 0x6E:
                continue
            pid = bytes(buf[1:5])
            if pid in seen:
                continue
            s = static_from_pen_id(key, pid)
            variants = {
                "the HAL's rule": s,
                "its byte-reversed form": s[::-1],
                "the id not reversed": static_from_pen_id(key, pid[::-1]),
            }
            hits = [n for n, v in variants.items() for _, ident in pens if v == ident]
            seen[pid] = hits
            print(
                "%s 0x6e: pen %d: %s"
                % (
                    time.strftime("%H:%M:%S"),
                    len(seen),
                    ("the bond's identity address = " + ", ".join(hits))
                    if hits
                    else "no bonded pen's identity matches",
                ),
                flush=True,
            )
    finally:
        os.close(fd)
    if not seen:
        print("no 0x6e came")


if __name__ == "__main__":
    main()
