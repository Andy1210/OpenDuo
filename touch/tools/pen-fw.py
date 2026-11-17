#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pen-fw: the Surface Slim Pen 2's firmware over Bluetooth, with Microsoft's
Component Firmware Update (CFU) protocol.

    pen-fw.py inspect DIR|FILE...        what a set of offers/payloads holds (no pen)
    pen-fw.py verify-cat CAT --root PEM  a Windows driver catalog: signature, chain, members
    pen-fw.py find                       the pen's hidraw node and its CFU reports
    pen-fw.py version [--fw DIR]         READ-ONLY: the pen's versions (+ the offers compared)
    pen-fw.py update --fw DIR            dry run: the plan and every packet built, nothing sent
    pen-fw.py update --fw DIR --go       the update itself: only with the owner's GO, each time
    pen-fw.py update --fw DIR --go --until-done --status FILE
                                         every newer part, one per transaction, the pen restarting
                                         in between; a dropped link waits for the next connection
                                         and starts that part again (Duo Settings > Pen > Firmware)
    pen-fw.py version --json / inspect --json DIR   the same, for programs

The pen is a BLE HID device (HID over GATT, BlueZ's hog plugin, uhid), and
its third top-level collection (vendor page 0xFF0B, usage 0x0101) is Microsoft's
standard CFU interface: feature 0x2A = GET_FIRMWARE_VERSION (60 bytes),
output 0x2D = offer, input 0x2D = offer response, output 0x2A = content,
input 0x2C = content response. The report ids are taken from the descriptor
by usage (0xC8 / 0xDC / 0xD8 / 0xC7 / 0xCC, the usages of Microsoft's Surface
devices), never assumed.

The host side follows Microsoft's own Windows CFU driver (DMF
Dmf_ComponentFirmwareUpdate.c): token 0xA0, START_ENTIRE_TRANSACTION,
START_OFFER_LIST, the offers in the INF's order, the payload in 52-byte
chunks with sequence numbers from 1, END_OFFER_LIST. On top of that, for
safety: an offer is sent only when it is newer than every version the pen
reports for that component, its files must be one of the known Microsoft
sets (SHA-256 below), the force flags are never set, and one run delivers at
most one payload. The pen checks the image itself (CRC, Microsoft's ECDSA
signature, version) and keeps running the old firmware when anything fails.

docs/hardware/touch.md has the background.
"""

import argparse
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import time

# --- the pen and its CFU interface ------------------------------------------------------------

PEN_VID, PEN_PID = 0x045E, 0x0C0F
BUS_BLUETOOTH = 0x0005
CFU_PAGE, CFU_USAGE = 0xFF0B, 0x0101
USAGE_VERSION, USAGE_OFFER_OUT, USAGE_OFFER_IN = 0xC8, 0xDC, 0xD8
USAGE_CONTENT_OUT, USAGE_CONTENT_IN = 0xC7, 0xCC

HOST_TOKEN = 0xA0  # the token of Microsoft's Windows CFU driver
SEQ_START = 1  # DMF: "Do not start at 0 due to firmware limitations"
CHUNK = 52  # data bytes in one content report (60 - 8 header)
FLAG_FIRST, FLAG_LAST = 0x80, 0x40
FORCE_FLAGS = 0xC0  # byte 1 of an offer: force-ignore-version / force-reset

INFO_START_ENTIRE, INFO_START_LIST, INFO_END_LIST = 0x00, 0x01, 0x02
CMD_NOTIFY_ON_READY = 0x01

ST_SKIP, ST_ACCEPT, ST_REJECT, ST_BUSY, ST_READY, ST_NOT_SUPPORTED = (
    0x00,
    0x01,
    0x02,
    0x03,
    0x04,
    0xFF,
)
OFFER_STATUS = {
    ST_SKIP: "SKIP",
    ST_ACCEPT: "ACCEPT",
    ST_REJECT: "REJECT",
    ST_BUSY: "BUSY",
    ST_READY: "COMMAND_READY",
    ST_NOT_SUPPORTED: "CMD_NOT_SUPPORTED",
}
REJECT_REASON = {
    0x00: "OLD_FW (same or newer already)",
    0x01: "INV_COMPONENT",
    0x02: "SWAP_PENDING",
    0x03: "MISMATCH",
    0x04: "BANK (the bank is in use)",
    0x05: "PLATFORM",
    0x06: "MILESTONE",
    0x07: "INV_PCOL_REV",
    0x08: "VARIANT",
    0xE0: "SIGN_RULE",
    0xE1: "VER_RELEASE_DEBUG",
    0xE2: "DEBUG_SAME_VERSION",
}
CONTENT_STATUS = {
    0x00: "SUCCESS",
    0x01: "ERROR_PREPARE",
    0x02: "ERROR_WRITE",
    0x03: "ERROR_COMPLETE",
    0x04: "ERROR_VERIFY",
    0x05: "ERROR_CRC",
    0x06: "ERROR_SIGNATURE",
    0x07: "ERROR_VERSION",
    0x08: "SWAP_PENDING",
    0x09: "ERROR_INVALID_ADDR",
    0x0A: "ERROR_NO_OFFER",
    0x0B: "ERROR_INVALID",
}
COMPONENTS = {0x10: "MCU", 0x11: "BLE", 0x13: "HAPTIC"}
# Microsoft's INF lists them in this order (SurfacePen0C0FFirmwareUpdate.inf); the pen
# rejects the BLE image of the bank it runs from and takes the other one.
OFFER_ORDER = ("BLE-HI", "BLE-LO", "MCU-B0", "HAPTIC")

# The firmware sets this tool will send: SHA-256 of (offer, payload), from Microsoft's
# own files (never in git). The pen verifies the image's signature anyway; this list
# makes sure a wrong, damaged or foreign file is refused before the pen sees it. A new
# Microsoft release is added here after `verify-cat` has checked its catalog.
KNOWN = {
    # Surface Duo 2 stock image 2023.501.541, vendor.img /firmware/cfu/hid/pen/0C0F/
    (
        "dd4b220b80d8e27a9cba61183758a04468bbf55d0f360225bc4878969f73aeb2",
        "4d7ba25c7fdeb86b967068a2edb9e678da14709b88642d6a13c74309358bc2fc",
    ): "Duo 2 stock BLE-HI 5.148.139",
    (
        "21d5f294302dfd056d20bd26a6ea551221f2ea656cb5f065f3cde568163033ab",
        "82e73d1131d79274357ad65354a80c5bae5ab753d8ccebe5f97e523222698181",
    ): "Duo 2 stock BLE-LO 5.148.139",
    (
        "b36924ab33adf3500e5742d35611f7e40449e02895815f6f72a0c2191107f2b8",
        "0f9c2b5321050153aa8d5702981f8b87965f51ffbc7697385ddcc31a763f1d1b",
    ): "Duo 2 stock MCU-B0 5.133.139",
    # HAPTIC 5.117.139: the same files in the stock image and in Microsoft's packages
    (
        "3219820f1b87fa146a2f693c951c6e8673b0e8f812e8ece27cae153f8b0ee284",
        "bb02a13a499dd095904a97450b2c9b8d5ee0824638068192d9230e77387c5a6f",
    ): "HAPTIC 5.117.139",
    # Microsoft "Surface Pen0C0F Firmware Update" 1.22.139.0 (06/30/2022, x64) = 1.24.7.0 (ARM64),
    # in the Surface Pro 8 (26.073) and Surface Pro 11 ARM (26.094) driver packs of 2026
    (
        "bde19726a64050f5391ff03456914273b99069ad3071cb81820325b5470ae0df",
        "83c600db049997bc26b7e9ad25ec1f290fd9e66a7aa77234ad3e41e4f2fe59ef",
    ): "Microsoft 1.22.139.0 BLE-HI 5.150.139",
    (
        "4c60bc51461a41148576a06565db2d8606f0c96f3c2a6e251fbc64a24a5ce093",
        "01ade5d9fab99ca711ee4592b84ca42ff160abe2318cb16f72ad7378ca92e349",
    ): "Microsoft 1.22.139.0 BLE-LO 5.150.139",
    (
        "6ffaf44fc1e4043aa7ced62c044570a22a97f0d841898d7164af1ff599327554",
        "26c59ca5cdc7bfb17086b2f60664081a25c9409d06d95125f361dc1b18f6c7bc",
    ): "Microsoft 1.22.139.0 MCU-B0 5.142.139",
}

# Microsoft Root Certificate Authority 2010 (www.microsoft.com/pki/certs/MicRooCerAut_2010-06-23.crt)
MS_ROOT_2010_SHA256 = "df545bf919a2439c36983b54cdfc903dfa4f37d3996d8d84b4c31eec6f3c163e"

MIN_PEN_BATTERY = 50  # percent; Microsoft publishes no number, this is ours


class CfuError(Exception):
    pass


# --- versions ---------------------------------------------------------------------------------


def fmt_version(v):
    """A CFU version word: major (31:24) . minor (23:8) . variant (7:0)."""
    return f"{v >> 24}.{(v >> 8) & 0xFFFF}.{v & 0xFF}"


def parse_version(text):
    """'5.150.139' or '0x0500968B' -> the 32-bit word."""
    text = text.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        raise ValueError(f"not a version: {text!r}")
    major, minor, variant = (int(x) for x in m.groups())
    if major > 0xFF or minor > 0xFFFF or variant > 0xFF:
        raise ValueError(f"version out of range: {text!r}")
    return (major << 24) | (minor << 8) | variant


def describe_variant(variant):
    """Microsoft's variant byte (CFU sample FwUpdate.h): signing, type, official."""
    signing = ("unsigned", "test", "attestation", "production")[variant & 3]
    kind = ("debug", "selfhost", "release", "ship")[(variant >> 2) & 3]
    return f"{'official ' if variant & 0x80 else ''}{kind}, {signing}-signed"


# --- offers and payloads (the files) ------------------------------------------------------------


@dataclasses.dataclass
class Offer:
    raw: bytes
    segment: int
    flags: int
    component: int
    token: int
    version: int
    compat_mask: int
    protocol: int
    bank: int
    milestone: int
    platform: int

    @classmethod
    def parse(cls, raw):
        """A 16-byte FIRMWARE_UPDATE_OFFER. Bytes 12-15 as Microsoft's CFU sample lays them out
        (ProductInfo: protocol revision bits 0-3, bank bits 4-5; milestone bits 0-3 of byte 13;
        platform id), which is what makes the pen's BLE-LO / BLE-HI banks 0 / 1."""
        raw = bytes(raw)
        if len(raw) != 16:
            raise CfuError(f"an offer is 16 bytes, this one {len(raw)}")
        if raw[2] in (0xFE, 0xFF):
            raise CfuError(f"component 0x{raw[2]:02X} is a special offer, not firmware")
        ver, mask, platform = struct.unpack_from("<IIxxH", raw, 4)
        return cls(
            raw,
            raw[0],
            raw[1],
            raw[2],
            raw[3],
            ver,
            mask,
            raw[12] & 0x0F,
            (raw[12] >> 4) & 3,
            raw[13] & 0x0F,
            platform,
        )

    def report(self, token=HOST_TOKEN):
        """The offer as sent: the file's bytes with our token; never with a force flag."""
        if self.flags & FORCE_FLAGS:
            raise CfuError("the offer file has a force flag set: refused")
        b = bytearray(self.raw)
        b[3] = token
        return bytes(b)


def parse_payload(data):
    """A CFU payload file: records of (u32 address, u8 length, data), back to back."""
    records, i = [], 0
    while i < len(data):
        if i + 5 > len(data):
            raise CfuError(f"payload: {len(data) - i} trailing bytes")
        addr, n = struct.unpack_from("<IB", data, i)
        if n == 0 or i + 5 + n > len(data):
            raise CfuError(f"payload: bad record at offset {i}")
        records.append((addr, bytes(data[i + 5 : i + 5 + n])))
        i += 5 + n
    if not records:
        raise CfuError("payload: empty")
    return records


def payload_chunks(records):
    """The content packets as Microsoft's driver builds them: up to 52 bytes each, records
    with consecutive addresses packed together, the first chunk FIRST_BLOCK, the last
    LAST_BLOCK. Returns [(flags, address, data)]."""
    chunks = []
    cur_addr, cur = None, b""
    for addr, data in records:
        pos = 0
        while pos < len(data):
            if cur and (cur_addr + len(cur) != addr + pos or len(cur) == CHUNK):
                chunks.append([0, cur_addr, cur])
                cur = b""
            if not cur:
                cur_addr = addr + pos
            take = min(CHUNK - len(cur), len(data) - pos)
            cur += data[pos : pos + take]
            pos += take
    chunks.append([0, cur_addr, cur])
    chunks[0][0] |= FLAG_FIRST
    chunks[-1][0] |= FLAG_LAST
    return [tuple(c) for c in chunks]


def check_image(records):
    """What we require of an image before it goes anywhere: one block from address 0, no
    gaps or overlaps (the pen's files are all like that)."""
    expect = 0
    for addr, data in records:
        if addr != expect:
            raise CfuError(
                f"payload: record at 0x{addr:x}, expected 0x{expect:x} (a gap or overlap)"
            )
        expect += len(data)
    return expect


def label_of(stem):
    m = re.search(r"(BLE[-_]HI|BLE[-_]LO|MCU[-_]B\d|HAPTIC)$", stem, re.I)
    return m.group(1).upper().replace("_", "-") if m else stem


@dataclasses.dataclass
class Pair:
    label: str
    offer_path: str
    payload_path: str
    offer: Offer
    payload: bytes
    known: str  # the KNOWN entry's name, "" if the files are not known

    @property
    def records(self):
        return parse_payload(self.payload)


def load_pairs(paths):
    """Offer/payload pairs from directories or files, in Microsoft's INF order."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))]
        else:
            files.append(p)
    offers = {f[: -len(".offer.bin")]: f for f in files if f.endswith(".offer.bin")}
    if not offers:
        raise CfuError("no *.offer.bin in " + ", ".join(paths))
    pairs = []
    for stem, opath in offers.items():
        ppath = stem + ".payload.bin"
        if not os.path.exists(ppath):
            raise CfuError(f"{opath}: no {os.path.basename(ppath)} next to it")
        with open(opath, "rb") as f:
            oraw = f.read()
        with open(ppath, "rb") as f:
            praw = f.read()
        key = (hashlib.sha256(oraw).hexdigest(), hashlib.sha256(praw).hexdigest())
        pairs.append(
            Pair(
                label_of(os.path.basename(stem)),
                opath,
                ppath,
                Offer.parse(oraw),
                praw,
                KNOWN.get(key, ""),
            )
        )
    order = {name: i for i, name in enumerate(OFFER_ORDER)}
    pairs.sort(key=lambda p: (order.get(p.label, len(order)), p.label))
    return pairs


def plan_offers(pairs, current):
    """Which pairs to offer, given the pen's versions ({component: [versions]}): only those
    strictly newer than every version the pen reports for that component. Returns
    [(pair, reason)] for all pairs; reason is None for the ones to offer."""
    out = []
    for p in pairs:
        have = current.get(p.offer.component)
        if not have:
            out.append((p, "the pen reports no such component"))
        elif p.offer.version <= max(have):
            out.append((p, f"not newer than the pen's {fmt_version(max(have))}"))
        else:
            out.append((p, None))
    return out


# --- the reports --------------------------------------------------------------------------------


def build_info(code, token=HOST_TOKEN):
    """FIRMWARE_UPDATE_OFFER - Information (component 0xFF)."""
    return bytes([code, 0, 0xFF, token]) + bytes(12)


def build_command(code, token=HOST_TOKEN):
    """FIRMWARE_UPDATE_OFFER - Extended command (component 0xFE), e.g. NOTIFY_ON_READY."""
    return bytes([code, 0, 0xFE, token]) + bytes(12)


def build_content(seq, flags, addr, data, size=60):
    """FIRMWARE_UPDATE_CONTENT: flags, length, u16 sequence, u32 address, data (padded)."""
    if not 0 < len(data) <= CHUNK:
        raise CfuError(f"content: {len(data)} data bytes")
    b = struct.pack("<BBHI", flags, len(data), seq, addr) + data
    return b + bytes(size - len(b))


def parse_offer_response(b):
    """-> (token, reject reason, status)."""
    if len(b) < 13:
        raise CfuError(f"offer response: {len(b)} bytes")
    return b[3], b[8], b[12]


def parse_content_response(b):
    """-> (sequence, status)."""
    if len(b) < 5:
        raise CfuError(f"content response: {len(b)} bytes")
    return struct.unpack_from("<H", b, 0)[0], b[4]


@dataclasses.dataclass
class Component:
    version: int
    bank: int
    vendor: int  # the 4 vendor bits next to the bank (Microsoft: milestone)
    component: int
    platform: int  # the vendor 16 bits (Microsoft: platform id)


def parse_version_response(b):
    """GET_FIRMWARE_VERSION (the 60 bytes after the report id): header (count bits 0-7,
    protocol version bits 24-27, extension bit 31), then 8 bytes per component."""
    if len(b) < 4:
        raise CfuError(f"version response: {len(b)} bytes")
    (header,) = struct.unpack_from("<I", b, 0)
    count, protocol, ext = header & 0xFF, (header >> 24) & 0x0F, header >> 31
    if count > (len(b) - 4) // 8:
        raise CfuError(f"version response: {count} components do not fit in {len(b)} bytes")
    comps = []
    for i in range(count):
        ver, prop, cid, vendor = struct.unpack_from("<IBBH", b, 4 + 8 * i)
        comps.append(Component(ver, prop & 3, prop >> 4, cid, vendor))
    return {"count": count, "protocol": protocol, "extension": ext, "components": comps}


def versions_by_component(resp):
    out = {}
    for c in resp["components"]:
        out.setdefault(c.component, []).append(c.version)
    return out


# --- the HID report descriptor ------------------------------------------------------------------


def parse_report_descriptor(desc):
    """The main items of a report descriptor: [{kind, id, usages, bits, path}] where usages
    are full 32-bit usages and path the usages of the enclosing collections."""
    g = {"page": 0, "size": 0, "count": 0, "id": 0}
    stack, path, fields = [], [], []
    usages, umin = [], None
    i = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            if i + 1 >= len(desc):
                raise CfuError("descriptor: truncated long item")
            i += 3 + desc[i + 1]
            continue
        size = (0, 1, 2, 4)[b & 3]
        kind, tag = (b >> 2) & 3, b >> 4
        if i + 1 + size > len(desc):
            raise CfuError("descriptor: truncated item")
        val = int.from_bytes(desc[i + 1 : i + 1 + size], "little")
        i += 1 + size
        if kind == 0:  # main
            if tag in (8, 9, 11):
                fields.append(
                    {
                        "kind": {8: "input", 9: "output", 11: "feature"}[tag],
                        "id": g["id"],
                        "usages": list(usages),
                        "bits": g["size"] * g["count"],
                        "path": tuple(path),
                    }
                )
            elif tag == 10:
                path.append(usages[0] if usages else 0)
            elif tag == 12:
                if path:
                    path.pop()
            usages, umin = [], None
        elif kind == 1:  # global
            if tag == 0:
                g["page"] = val
            elif tag == 7:
                g["size"] = val
            elif tag == 8:
                g["id"] = val
            elif tag == 9:
                g["count"] = val
            elif tag == 10:
                stack.append(dict(g))
            elif tag == 11 and stack:
                g = stack.pop()
        elif kind == 2:  # local
            full = val if size == 4 else (g["page"] << 16) | val
            if tag == 0:
                usages.append(full)
            elif tag == 1:
                umin = full
            elif tag == 2 and umin is not None:
                usages += range(umin, full + 1)
                umin = None
    return fields


def find_cfu_reports(desc):
    """{role: (report id, byte length)} for the CFU collection, by usage."""
    want = {
        ("feature", USAGE_VERSION): "version",
        ("output", USAGE_OFFER_OUT): "offer_out",
        ("input", USAGE_OFFER_IN): "offer_in",
        ("output", USAGE_CONTENT_OUT): "content_out",
        ("input", USAGE_CONTENT_IN): "content_in",
    }
    tlc = (CFU_PAGE << 16) | CFU_USAGE
    fields = [f for f in parse_report_descriptor(desc) if tlc in f["path"]]
    out = {}
    for f in fields:
        if not f["usages"] or f["usages"][0] >> 16 != CFU_PAGE:
            continue
        role = want.get((f["kind"], f["usages"][0] & 0xFFFF))
        if role:
            nbytes = (
                sum(x["bits"] for x in fields if x["kind"] == f["kind"] and x["id"] == f["id"]) // 8
            )
            out[role] = (f["id"], nbytes)
    missing = sorted(set(want.values()) - set(out))
    if missing:
        raise CfuError("no CFU collection (0xFF0B/0x0101) with " + ", ".join(missing))
    return out


# --- a Windows driver catalog (.cat): PKCS#7 signedData over a CTL ------------------------------


def der_read(b, i=0):
    """One DER TLV at i -> (tag, value start, value end)."""
    tag = b[i]
    n = b[i + 1]
    j = i + 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4:
            raise CfuError("DER: unsupported length")
        n = int.from_bytes(b[j : j + k], "big")
        j += k
    if j + n > len(b):
        raise CfuError("DER: truncated")
    return tag, j, j + n


def der_children(b, start, end):
    out, i = [], start
    while i < end:
        tag, s, e = der_read(b, i)
        out.append((tag, i, s, e))
        i = e
    return out


def der_octets(b, start, end, out):
    """Every OCTET STRING value under b[start:end] (recursively)."""
    for tag, _, s, e in der_children(b, start, end):
        if tag == 0x04:
            out.append(bytes(b[s:e]))
        if tag & 0x20:
            der_octets(b, s, e, out)
    return out


OID_SHA1 = bytes.fromhex("2b0e03021a")
OID_SHA256 = bytes.fromhex("608648016503040201")
OID_MESSAGE_DIGEST = bytes.fromhex("2a864886f70d010904")


def verify_catalog(cat, root_der):
    """Check a catalog the way Windows does: the signed attributes' messageDigest is the
    hash of the CTL's content octets (PKCS#7, RFC 2315 9.3: OpenSSL's CMS code hashes the
    whole TLV and fails on these), the signature over the attributes, and the chain to the
    given root. Returns {"signer", "chain", "digests"}: the digests are every hash listed
    in the signed CTL. Needs python3-cryptography."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    tag, s, e = der_read(cat)
    ci = der_children(cat, s, e)
    _, _, s0, e0 = ci[1]  # [0] EXPLICIT
    _, _, sd_s, sd_e = der_children(cat, s0, e0)[0]  # SignedData
    sd = der_children(cat, sd_s, sd_e)
    encap = der_children(cat, sd[2][2], sd[2][3])
    content_tlv = der_children(cat, encap[1][2], encap[1][3])[0]
    content = cat[content_tlv[2] : content_tlv[3]]  # the CTL's content octets
    certs = (
        [
            x509.load_der_x509_certificate(bytes(cat[i:e2]))
            for _, i, _, e2 in der_children(cat, sd[3][2], sd[3][3])
        ]
        if sd[3][0] == 0xA0
        else []
    )
    signer_infos = sd[-1]
    si = der_children(cat, signer_infos[2], signer_infos[3])[0]
    fields = der_children(cat, si[2], si[3])
    sid = der_children(cat, fields[1][2], fields[1][3])
    issuer_der = bytes(cat[sid[0][1] : sid[0][3]])
    serial = int.from_bytes(cat[sid[1][2] : sid[1][3]], "big", signed=True)
    _, _, a_s, a_e = der_children(cat, fields[2][2], fields[2][3])[0]
    alg_oid = bytes(cat[a_s:a_e])
    algs = {OID_SHA1: (hashlib.sha1, hashes.SHA1), OID_SHA256: (hashlib.sha256, hashes.SHA256)}
    if alg_oid not in algs:
        raise CfuError(f"catalog: digest algorithm {alg_oid.hex()} not supported")
    hfn, hcls = algs[alg_oid]
    attrs = fields[3]
    if attrs[0] != 0xA0:
        raise CfuError("catalog: no signed attributes")
    md = None
    for _, _, a_s, a_e in der_children(cat, attrs[2], attrs[3]):
        oid_t, val_t = der_children(cat, a_s, a_e)
        if bytes(cat[oid_t[2] : oid_t[3]]) == OID_MESSAGE_DIGEST:
            _, _, d_s, d_e = der_children(cat, val_t[2], val_t[3])[0]
            md = bytes(cat[d_s:d_e])
    if md != hfn(bytes(content)).digest():
        raise CfuError("catalog: the content does not match the signed digest")
    signed = b"\x31" + bytes(cat[attrs[1] + 1 : attrs[3]])
    sig = bytes(cat[fields[5][2] : fields[5][3]])
    signer = next(
        (c for c in certs if c.serial_number == serial and c.issuer.public_bytes() == issuer_der),
        None,
    )
    if signer is None:
        raise CfuError("catalog: the signer's certificate is not in it")
    key = signer.public_key()
    root = x509.load_der_x509_certificate(root_der)
    chain, cert = [signer], signer
    try:
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, signed, padding.PKCS1v15(), hcls())
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(sig, signed, ec.ECDSA(hcls()))
        else:
            raise CfuError("catalog: unsupported key")
        for _ in range(8):
            if cert.issuer == root.subject:
                cert.verify_directly_issued_by(root)
                chain.append(root)
                break
            nxt = next((c for c in certs if c.subject == cert.issuer and c is not cert), None)
            if nxt is None:
                raise CfuError(f"catalog: no issuer for {cert.subject.rfc4514_string()}")
            cert.verify_directly_issued_by(nxt)
            chain.append(nxt)
            cert = nxt
        else:
            raise CfuError("catalog: the chain does not reach the root")
    except InvalidSignature:
        raise CfuError(f"catalog: a bad signature ({cert.subject.rfc4514_string()})") from None
    digests = {d for d in der_octets(content, 0, len(content), []) if len(d) in (20, 32)}
    return {"signer": signer, "chain": chain, "digests": digests}


# --- the device -----------------------------------------------------------------------------------


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def hidiocgfeature(n):
    return _ioc(3, 0x07, n)


def find_pens(sysfs="/sys/class/hidraw"):
    """The pen's hidraw nodes: Bluetooth bus, Microsoft 045E:0C0F, with a CFU collection.
    -> [(node, reports, name)]; the node's Bluetooth address is not printed."""
    out = []
    for n in sorted(os.listdir(sysfs)) if os.path.isdir(sysfs) else []:
        dev = os.path.join(sysfs, n, "device")
        try:
            with open(os.path.join(dev, "uevent")) as f:
                ue = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
            with open(os.path.join(dev, "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        bus, vid, pid = (int(x, 16) for x in ue.get("HID_ID", "0:0:0").split(":"))
        if (bus, vid, pid) != (BUS_BLUETOOTH, PEN_VID, PEN_PID):
            continue
        try:
            out.append(("/dev/" + n, find_cfu_reports(desc), ue.get("HID_NAME", "")))
        except CfuError:
            continue
    return out


class Hidraw:
    def __init__(self, node, reports, log=print):
        self.node, self.reports, self.log = node, reports, log
        self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        os.close(self.fd)

    def get_version(self):
        rid, n = self.reports["version"]
        buf = bytearray(1 + n)
        buf[0] = rid
        got = fcntl.ioctl(self.fd, hidiocgfeature(len(buf)), buf, True)
        if got < 5:
            raise CfuError(f"GET_FIRMWARE_VERSION: {got} bytes")
        return parse_version_response(bytes(buf[1:got]))

    def drain(self):
        while select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 256)
            except BlockingIOError:
                return

    def write(self, role, data):
        rid, n = self.reports[role]
        if len(data) != n:
            raise CfuError(f"{role}: {len(data)} bytes, the report is {n}")
        os.write(self.fd, bytes([rid]) + data)

    def read(self, role, timeout):
        rid, _ = self.reports[role]
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return None
            try:
                data = os.read(self.fd, 256)
            except BlockingIOError:
                continue
            if data and data[0] == rid:
                return data[1:]


class Session:
    """One CFU transaction on an open Hidraw (the update; never used by `version`)."""

    # Microsoft's driver waits up to 90 s for any response (HIDDEVICE_RECOMMENDED_WAIT_TIMEOUT_MS);
    # the first block (the pen prepares its spare bank) and the last (it checks the whole
    # image) get that, the others 15 s. A timeout ends the run; the pen keeps its firmware.
    RESPONSE_S = 15.0
    SLOW_BLOCK_S = 90.0

    def __init__(self, dev, log=print):
        self.dev, self.log = dev, log

    def _offer_exchange(self, report, what):
        self.dev.drain()
        self.dev.write("offer_out", report)
        resp = self.dev.read("offer_in", self.RESPONSE_S)
        if resp is None:
            raise CfuError(f"{what}: no response in {self.RESPONSE_S:.0f} s")
        token, rr, status = parse_offer_response(resp)
        if token != HOST_TOKEN:
            raise CfuError(f"{what}: token 0x{token:02X}, ours is 0x{HOST_TOKEN:02X}")
        return status, rr

    def info(self, code):
        name = {
            INFO_START_ENTIRE: "START_ENTIRE_TRANSACTION",
            INFO_START_LIST: "START_OFFER_LIST",
            INFO_END_LIST: "END_OFFER_LIST",
        }[code]
        status, rr = self._offer_exchange(build_info(code), name)
        self.log(f"  {name}: {OFFER_STATUS.get(status, hex(status))}")
        if status not in (ST_ACCEPT, ST_NOT_SUPPORTED):
            raise CfuError(
                f"{name}: {OFFER_STATUS.get(status, hex(status))}, {REJECT_REASON.get(rr, hex(rr))}"
            )

    def offer(self, pair):
        status, rr = self._offer_exchange(pair.offer.report(), f"offer {pair.label}")
        if status == ST_BUSY:
            self.log(f"  offer {pair.label}: BUSY, waiting (NOTIFY_ON_READY, at most 60 s)")
            self.dev.drain()
            self.dev.write("offer_out", build_command(CMD_NOTIFY_ON_READY))
            resp = self.dev.read("offer_in", 60.0)
            if resp is None or parse_offer_response(resp)[2] != ST_READY:
                raise CfuError(f"offer {pair.label}: the pen stayed busy")
            status, rr = self._offer_exchange(pair.offer.report(), f"offer {pair.label} (again)")
        txt = OFFER_STATUS.get(status, hex(status))
        if status == ST_REJECT:
            txt += ": " + REJECT_REASON.get(rr, hex(rr))
        self.log(f"  offer {pair.label} {fmt_version(pair.offer.version)}: {txt}")
        return status

    def payload(self, pair, progress=True, on_progress=None):
        chunks = payload_chunks(pair.records)
        t0 = time.monotonic()
        for n, (flags, addr, data) in enumerate(chunks):
            seq = (SEQ_START + n) & 0xFFFF
            self.dev.write(
                "content_out",
                build_content(seq, flags, addr, data, self.dev.reports["content_out"][1]),
            )
            end = time.monotonic() + (
                self.SLOW_BLOCK_S if flags & (FLAG_FIRST | FLAG_LAST) else self.RESPONSE_S
            )
            while True:
                resp = self.dev.read("content_in", max(0.0, end - time.monotonic()))
                if resp is None:
                    raise CfuError(
                        f"content {n + 1}/{len(chunks)}: no response (the pen keeps its "
                        "old firmware; start again)"
                    )
                rseq, status = parse_content_response(resp)
                if rseq == seq:
                    break
            if status != 0:
                raise CfuError(
                    f"content {n + 1}/{len(chunks)}: {CONTENT_STATUS.get(status, hex(status))}"
                )
            if progress and (n % 200 == 0 or flags & FLAG_LAST):
                self.log(f"  {pair.label}: {n + 1}/{len(chunks)} ({time.monotonic() - t0:.0f} s)")
            if on_progress and (n % 20 == 0 or flags & FLAG_LAST):
                on_progress(pair, n + 1, len(chunks))
        return time.monotonic() - t0


def run_transaction(sess, todo, log=print, on_progress=None):
    """Microsoft's sequence, at most one payload: START_ENTIRE_TRANSACTION, START_OFFER_LIST,
    the offers until the pen accepts one, its payload, END_OFFER_LIST. -> the delivered pair
    or None. Any error raises; the pen then keeps what it runs."""
    sess.info(INFO_START_ENTIRE)
    sess.info(INFO_START_LIST)
    done = None
    for p in todo:
        if sess.offer(p) == ST_ACCEPT:
            secs = sess.payload(p, on_progress=on_progress)
            log(f"  {p.label}: delivered in {secs:.0f} s, the pen took it (image checked)")
            done = p
            break
    try:
        sess.info(INFO_END_LIST)
    except (CfuError, OSError) as e:
        if done is None:
            raise
        log(f"  END_OFFER_LIST unanswered ({e}): the pen may already be restarting")
    return done


# --- the checks before an update -------------------------------------------------------------------


def charger_online(sysfs="/sys/class/power_supply"):
    """-> the names of the online chargers (USB / mains / wireless)."""
    out = []
    for n in sorted(os.listdir(sysfs)) if os.path.isdir(sysfs) else []:
        try:
            with open(os.path.join(sysfs, n, "type")) as f:
                kind = f.read().strip()
            with open(os.path.join(sysfs, n, "online")) as f:
                online = f.read().strip()
        except OSError:
            continue
        if kind in ("USB", "Mains", "Wireless") and online == "1":
            out.append(n)
    return out


def bluez_state(objects):
    """From BlueZ's GetManagedObjects (busctl --json=short output, data[0]): the connected
    devices -> (pen, others), each {name, battery}; the pen by its PnP id 045E:0C0F."""
    pen, others = None, []
    for _, ifaces in objects.items():
        dev = ifaces.get("org.bluez.Device1")
        if not dev or not dev.get("Connected", {}).get("data"):
            continue
        name = dev.get("Name", {}).get("data", "") or dev.get("Alias", {}).get("data", "")
        modalias = dev.get("Modalias", {}).get("data", "")
        battery = ifaces.get("org.bluez.Battery1", {}).get("Percentage", {}).get("data")
        entry = {"name": name, "battery": battery, "modalias": modalias}
        if re.search(r"v045Ep0C0F", modalias, re.I):
            pen = entry
        else:
            others.append(entry)
    return pen, others


def bluez_objects():
    out = subprocess.run(
        [
            "busctl",
            "--system",
            "--json=short",
            "call",
            "org.bluez",
            "/",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    return json.loads(out)["data"][0]


def other_openers(node):
    """PIDs other than ours that have the hidraw node open."""
    real = os.path.realpath(node)
    pids = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if int(pid) == os.getpid():
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                if os.path.realpath(f"/proc/{pid}/fd/{fd}") == real:
                    pids.append(int(pid))
                    break
        except OSError:
            continue
    return pids


# --- commands ---------------------------------------------------------------------------------------


def show_pairs(pairs):
    for p in pairs:
        o = p.offer
        recs = p.records
        size = check_image(recs)
        print(
            f"{p.label:7} component 0x{o.component:02X} ({COMPONENTS.get(o.component, '?')}) "
            f"version {fmt_version(o.version)} (0x{o.version:08X}, {describe_variant(o.version & 0xFF)})"
        )
        print(
            f"        bank {o.bank}  milestone {o.milestone}  platform 0x{o.platform:04X}  "
            f"protocol {o.protocol}  compat 0x{o.compat_mask:08X}  segment {o.segment}  "
            f"flags 0x{o.flags:02X}  token 0x{o.token:02X}"
        )
        print(
            f"        payload {len(recs)} records, {size} bytes, {len(payload_chunks(recs))} packets; "
            f"{p.known or 'NOT a known Microsoft set: the update refuses it'}"
        )


def cmd_inspect(a):
    pairs = load_pairs(a.paths)
    if a.json:
        for p in pairs:
            check_image(p.records)
        # the set's name: what the known parts say before their label ("Microsoft 1.22.139.0",
        # "Duo 2 stock"); the haptic part is the same file in every set and names none
        sets = sorted(
            {
                p.known.split(p.label)[0].strip()
                for p in pairs
                if p.known and p.label in p.known and p.known.split(p.label)[0].strip()
            }
        )
        print(
            json.dumps(
                {
                    "set": sets[0] if len(sets) == 1 else None,
                    "known": all(p.known for p in pairs),
                    "parts": pairs_json(pairs),
                }
            )
        )
        return 0
    show_pairs(pairs)
    return 0


def cmd_verify_cat(a):
    with open(a.cat, "rb") as f:
        cat = f.read()
    with open(a.root, "rb") as f:
        root = f.read()
    if root.startswith(b"-----BEGIN"):
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding

        root = x509.load_pem_x509_certificate(root).public_bytes(Encoding.DER)
    fp = hashlib.sha256(root).hexdigest()
    if fp != MS_ROOT_2010_SHA256 and not a.any_root:
        print(f"the root's SHA-256 {fp} is not Microsoft Root Certificate Authority 2010's")
        return 2
    res = verify_catalog(cat, root)
    for c in res["chain"]:
        start = getattr(c, "not_valid_before_utc", None) or c.not_valid_before  # cryptography < 42
        end = getattr(c, "not_valid_after_utc", None) or c.not_valid_after
        print(f"  {c.subject.rfc4514_string()}  ({start:%Y-%m-%d} .. {end:%Y-%m-%d})")
    print("signature and chain: OK (validity dates are not checked: catalogs are timestamped)")
    bad = 0
    folder = os.path.dirname(os.path.abspath(a.cat))
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.endswith(".cat") or not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        ok = (
            hashlib.sha256(data).digest() in res["digests"]
            or hashlib.sha1(data).digest() in res["digests"]
        )
        bad += not ok
        print(f"  {'listed' if ok else 'NOT LISTED':10} {hashlib.sha256(data).hexdigest()}  {name}")
    return 1 if bad else 0


def open_pen(a):
    pens = find_pens()
    if a.hidraw:
        pens = [p for p in pens if p[0] == a.hidraw]
    if len(pens) != 1:
        raise CfuError(
            f"{len(pens)} pen(s) with a CFU interface found: pair the pen, press its top "
            "button so that it connects, then try again (or give --hidraw)"
        )
    return pens[0]


def cmd_find(a):
    pens = find_pens()
    for node, reports, name in pens:
        print(f"{node}  {name}")
        for role, (rid, n) in sorted(reports.items()):
            print(f"    {role:12} report 0x{rid:02X}, {n} bytes")
    if not pens:
        print("no pen: pair it, press the top button (it connects), then try again")
    return 0 if pens else 1


def print_versions(resp):
    print(
        f"GET_FIRMWARE_VERSION: {resp['count']} component(s), protocol {resp['protocol']}"
        f"{', extension bit set' if resp['extension'] else ''}"
    )
    for c in resp["components"]:
        print(
            f"  component 0x{c.component:02X} ({COMPONENTS.get(c.component, '?'):6}) "
            f"{fmt_version(c.version):12} (0x{c.version:08X})  bank {c.bank}  vendor {c.vendor}  "
            f"platform 0x{c.platform:04X}"
        )


def print_plan(plan):
    todo = 0
    for p, reason in plan:
        if reason is None:
            todo += 1
            print(f"  {p.label:7} {fmt_version(p.offer.version):12} OFFER (newer)")
        else:
            print(f"  {p.label:7} {fmt_version(p.offer.version):12} skip: {reason}")
    return todo


def versions_json(resp):
    """The pen's versions for programs (no serial, no address)."""
    return [
        {
            "component": c.component,
            "name": COMPONENTS.get(c.component, "0x%02X" % c.component),
            "version": fmt_version(c.version),
            "raw": c.version,
            "bank": c.bank,
        }
        for c in resp["components"]
    ]


def pairs_json(pairs):
    out = []
    for p in pairs:
        o = p.offer
        out.append(
            {
                "label": p.label,
                "component": o.component,
                "name": COMPONENTS.get(o.component, "0x%02X" % o.component),
                "version": fmt_version(o.version),
                "raw": o.version,
                "bank": o.bank,
                "known": p.known,
            }
        )
    return out


def cmd_version(a):
    node, reports, name = open_pen(a)
    dev = Hidraw(node, reports)
    try:
        resp = dev.get_version()  # a HID GET_FEATURE: reads, changes nothing
    finally:
        dev.close()
    if a.json:
        print(json.dumps({"read_at": int(time.time()), "components": versions_json(resp)}))
        return 0
    print(f"{node}  {name}")
    print_versions(resp)
    if a.fw:
        todo = print_plan(plan_offers(load_pairs(a.fw), versions_by_component(resp)))
        print("newer firmware is available" if todo else "the pen is up to date with these files")
    return 0


class SleepBlock:
    """A logind sleep inhibitor (block) for the duration: duo2-autosleep honours it, and a
    suspend would drop the Bluetooth link mid-transfer. Our own child, stopped by PID."""

    def __enter__(self):
        if not shutil.which("systemd-inhibit"):
            raise CfuError("no systemd-inhibit: the phone could fall asleep mid-transfer")
        # `cat` on our pipe: closing the pipe ends it, and systemd-inhibit with it (a signal
        # would leave the child behind)
        self.proc = subprocess.Popen(
            [
                "systemd-inhibit",
                "--what=sleep:idle",
                "--mode=block",
                "--who=pen-fw",
                "--why=pen firmware update",
                "cat",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if self.proc.poll() is not None:
            raise CfuError("systemd-inhibit refused the sleep block")
        return self

    def __exit__(self, *exc):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class PenBusy:
    """duo2-pend's mark (/run/duo2-pen/fw-update, our PID): while it is there,
    the daemon writes nothing to the pen (no haptics, no button click)."""

    MARK = "/run/duo2-pen/fw-update"

    def __enter__(self):
        os.makedirs(os.path.dirname(self.MARK), mode=0o700, exist_ok=True)
        with open(self.MARK, "w") as f:
            f.write("%d\n" % os.getpid())
        return self

    def __exit__(self, *exc):
        try:
            os.unlink(self.MARK)
        except OSError:
            pass


class StatusFile:
    """The progress of an --until-done run for Duo Settings: one JSON object, replaced as a
    whole, readable by everyone (versions and states only: no address, no serial)."""

    def __init__(self, path):
        self.path = path
        self.data = {"pid": os.getpid(), "started": int(time.time())}

    def set(self, **kw):
        for k in ("label", "version", "sent", "total", "message", "problems"):
            if (
                k not in kw
                and k in self.data
                and kw.get("state") not in (None, self.data.get("state"))
            ):
                del self.data[k]  # a new state starts clean (versions and delivered stay)
        self.data.update(kw)
        self.data["time"] = int(time.time())
        if not self.path:
            return
        d = os.path.dirname(self.path)
        os.makedirs(d, mode=0o755, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)


def wait_for(cond, timeout, sleep=time.sleep, now=time.monotonic, step=1.0):
    end = now() + timeout
    while True:
        v = cond()
        if v or now() >= end:
            return v
        sleep(step)


def run_until_done(
    pairs,
    status,
    log=print,
    wait_link=600,
    attempts=6,
    find=find_pens,
    open_dev=None,
    check=None,
    sleep=time.sleep,
    now=time.monotonic,
):
    """Every part the pen has an older version of, one payload per transaction (as Microsoft's
    driver), the pen restarting in between. A transaction that fails (the link dropped: the
    pen went to sleep or out of range; no answer) leaves the pen on its old image (the BLE
    part has two banks, the others are checked before use): this waits for the next link
    and starts that part again, at most `attempts` failures in all. A part is never
    delivered twice in one run. -> the exit code; the status file says the rest:
    starting, waiting-link, checking, refused (problems), sending (label, version, sent,
    total), interrupted, restarting, done (delivered), failed."""
    open_dev = open_dev or (lambda node, reports: Hidraw(node, reports, log))
    check = check or preflight
    failures, delivered = 0, []
    while True:
        pens = find()
        if not pens:
            status.set(state="waiting-link")
            log("waiting for the pen's link (its top button connects it)")
            pens = wait_for(find, wait_link, sleep, now)
            if not pens:
                status.set(state="failed", message="no pen link", code="no-link")
                return 4
        node, reports, _name = pens[0]
        status.set(state="checking")
        try:
            dev = open_dev(node, reports)
            try:
                resp = dev.get_version()
            finally:
                dev.close()
        except (CfuError, OSError) as e:
            failures += 1
            log(f"version read failed: {e}")
            if failures >= attempts:
                status.set(state="failed", message=str(e))
                return 1
            sleep(2)
            continue
        status.set(versions=versions_json(resp))
        done_labels = {d["label"] for d in delivered}
        todo = [
            p
            for p, reason in plan_offers(pairs, versions_by_component(resp))
            if reason is None and p.label not in done_labels
        ]
        if not todo:
            status.set(state="done", delivered=delivered)
            log("nothing newer to offer: done")
            return 0
        # BlueZ fills in the battery a moment after the link: a short wait for it
        problems = check(node)
        for _ in range(5):
            if not any(code in ("battery-unknown", "no-pen") for code, _t in problems):
                break
            sleep(1)
            problems = check(node)
        if problems:
            status.set(state="refused", problems=[{"code": c, "text": t} for c, t in problems])
            for _c, t in problems:
                log("refused: " + t)
            return 3
        try:
            dev = open_dev(node, reports)
            try:
                done = run_transaction(
                    Session(dev, log),
                    todo,
                    log,
                    on_progress=lambda p, n, total: status.set(
                        state="sending",
                        label=p.label,
                        version=fmt_version(p.offer.version),
                        sent=n,
                        total=total,
                    ),
                )
            finally:
                dev.close()
        except (CfuError, OSError) as e:
            failures += 1
            log(f"transaction failed ({failures}/{attempts}): {e}")
            if failures >= attempts:
                status.set(state="failed", message=str(e))
                return 1
            status.set(state="interrupted", message=str(e), failures=failures)
            # a dropped link: wait until the node is gone (or a minute), then for the next link
            wait_for(lambda node=node: not any(p[0] == node for p in find()), 60, sleep, now)
            continue
        if done is None:
            status.set(state="done", delivered=delivered, message="the pen accepted no offer")
            log("the pen accepted no offer")
            return 0
        delivered.append(
            {
                "label": done.label,
                "name": COMPONENTS.get(done.offer.component, "?"),
                "version": fmt_version(done.offer.version),
            }
        )
        status.set(
            state="restarting",
            delivered=delivered,
            label=done.label,
            version=fmt_version(done.offer.version),
        )
        log(f"{done.label} delivered: waiting for the pen to restart")
        # the pen restarts into the new image: its link goes and comes back (by itself or
        # with a press of the top button)
        wait_for(lambda node=node: not any(p[0] == node for p in find()), 90, sleep, now)


def preflight(node, objects=None):
    """The rules for a safe pen-firmware update, as checks. -> [(code, text)]: charger,
    bluez, no-pen, battery-unknown, battery, other-devices, busy."""
    problems = []
    if not charger_online():
        problems.append(("charger", "the phone is not on a charger"))
    try:
        pen, others = bluez_state(objects if objects is not None else bluez_objects())
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as e:
        problems.append(("bluez", f"BlueZ state unreadable ({e})"))
    else:
        if pen is None:
            problems.append(("no-pen", "BlueZ shows no connected Surface pen"))
        elif pen["battery"] is None:
            problems.append(
                ("battery-unknown", "the pen's battery level is unknown (org.bluez.Battery1)")
            )
        elif pen["battery"] < MIN_PEN_BATTERY:
            problems.append(
                (
                    "battery",
                    f"the pen's battery is {pen['battery']} % (at least {MIN_PEN_BATTERY} %)",
                )
            )
        if others:
            problems.append(
                (
                    "other-devices",
                    "other Bluetooth devices are connected: "
                    + ", ".join(o["name"] or "?" for o in others),
                )
            )
    holders = other_openers(node)
    if holders:
        problems.append(("busy", f"{node} is open in other processes: {holders}"))
    return problems


def cmd_update(a):
    pairs = load_pairs(a.fw)
    for p in pairs:
        check_image(p.records)
        p.offer.report()  # refuses a force flag
    unknown = [p.label for p in pairs if not p.known]
    if unknown:
        raise CfuError("not a known Microsoft firmware set: " + ", ".join(unknown))
    if a.until_done:
        if not a.go or not a.status or a.current:
            raise CfuError("--until-done needs --go and --status (and reads the pen itself)")
        status = StatusFile(a.status)
        log = lambda s: print(f"{datetime.datetime.now():%H:%M:%S} {s}", flush=True)  # noqa: E731
        status.set(state="starting")

        def on_term(_sig, _frame):  # systemctl stop (Duo Settings' Stop): as a Ctrl-C
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, on_term)
        try:
            with SleepBlock(), PenBusy():
                return run_until_done(pairs, status, log, wait_link=a.wait_link)
        except KeyboardInterrupt:
            status.set(state="stopped")
            raise
        except BaseException as e:
            if status.data.get("state") not in ("done", "failed", "refused"):
                status.set(state="failed", message=f"{e}"[:300])
            raise
    node = reports = None
    if a.current:
        current = {}
        for item in a.current:
            comp, ver = item.split("=", 1)
            current.setdefault(int(comp, 0), []).append(parse_version(ver))
        if a.go:
            raise CfuError("--current is for a dry run; --go reads the pen")
    else:
        node, reports, name = open_pen(a)
        dev = Hidraw(node, reports)
        try:
            resp = dev.get_version()
        finally:
            dev.close()
        print(f"{node}  {name}")
        print_versions(resp)
        current = versions_by_component(resp)
    plan = plan_offers(pairs, current)
    print("plan:")
    todo = [p for p, reason in plan if reason is None]
    print_plan(plan)
    if not todo:
        print("nothing newer to offer: no update")
        return 0
    content_size = reports["content_out"][1] if reports else 60
    for p in todo:
        chunks = payload_chunks(p.records)
        pkts = [
            build_content((SEQ_START + n) & 0xFFFF, f, ad, d, content_size)
            for n, (f, ad, d) in enumerate(chunks)
        ]
        print(
            f"  {p.label}: offer {p.offer.report().hex()} then {len(pkts)} content packets "
            f"(first {pkts[0][:8].hex()}..., last {pkts[-1][:8].hex()}...)"
        )
    print("one run delivers at most one payload: the first offer the pen accepts")
    if not a.go:
        print("dry run: nothing was sent (--go sends, with the owner's GO)")
        return 0
    problems = preflight(node)
    if problems:
        for _code, text in problems:
            print("refused: " + text)
        return 3
    log = lambda s: print(f"{datetime.datetime.now():%H:%M:%S} {s}", flush=True)  # noqa: E731
    with SleepBlock(), PenBusy():
        dev = Hidraw(node, reports, log)
        try:
            done = run_transaction(Session(dev, log), todo, log)
        finally:
            dev.close()
    if done:
        print(
            f"{done.label} {fmt_version(done.offer.version)} is in the pen and runs after its next "
            "reset (it may disconnect and come back by itself). Then: pen-fw.py version --fw ..."
        )
    else:
        print("the pen accepted no offer: nothing was written")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inspect", help="decode offers/payloads (no pen)")
    s.add_argument("paths", nargs="+")
    s.add_argument("--json", action="store_true", help="the parts and versions as JSON")
    s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("verify-cat", help="verify a Windows driver catalog and its folder's files")
    s.add_argument("cat")
    s.add_argument(
        "--root", required=True, help="Microsoft Root Certificate Authority 2010 (DER or PEM)"
    )
    s.add_argument("--any-root", action="store_true", help="accept another root (tests)")
    s.set_defaults(fn=cmd_verify_cat)
    s = sub.add_parser("find", help="the pen's hidraw node and CFU reports")
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("version", help="read-only: the pen's firmware versions")
    s.add_argument("--hidraw")
    s.add_argument("--json", action="store_true", help="the versions as JSON")
    s.add_argument("--fw", nargs="+", help="offer/payload files or folders to compare with")
    s.set_defaults(fn=cmd_version)
    s = sub.add_parser("update", help="dry run by default; --go updates")
    s.add_argument("--fw", nargs="+", required=True)
    s.add_argument("--hidraw")
    s.add_argument(
        "--current",
        action="append",
        metavar="COMPONENT=VERSION",
        help="dry run without the pen, e.g. 0x11=5.148.139 (repeatable)",
    )
    s.add_argument("--go", action="store_true", help="send it (only with the owner's GO)")
    s.add_argument(
        "--until-done",
        action="store_true",
        help="every newer part, the pen restarting in between; waits for the link and "
        "starts a part again after a dropped link (with --go and --status)",
    )
    s.add_argument("--status", help="a JSON file the progress goes to (readable by everyone)")
    s.add_argument(
        "--wait-link",
        type=int,
        default=600,
        metavar="S",
        help="with --until-done: how long to wait for the pen's link (default 600 s)",
    )
    s.set_defaults(fn=cmd_update)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (CfuError, OSError) as e:
        print(f"pen-fw: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "pen-fw: interrupted (an unfinished image is never used: the pen keeps its firmware)",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
