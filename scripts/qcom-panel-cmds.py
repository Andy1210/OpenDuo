#!/usr/bin/env python3
"""Decode a downstream Qualcomm DSI panel command sequence into mainline C.

    scripts/qcom-panel-cmds.py PANEL.dts NODE PROPERTY [TIMING]

PANEL.dts is decompiled device tree source (dtc -O dts), NODE the panel node
name (e.g. qcom,mdss_dsi_elgin_dsi0_c3_cmd_mp), PROPERTY a command property
(qcom,mdss-dsi-on-command, ...), TIMING the timing node (default timing@0) for
properties that live there.

The downstream format is a byte stream of packets:
    dtype last vc ack wait_ms dlen_hi dlen_lo payload[dlen]
written either as a byte string [..] or as 32-bit big-endian cells <..>.
Output: mipi_dsi_*_multi() calls (Linux 6.14+ DSI helpers), one per packet,
with the wait as mipi_dsi_msleep(). PPS (0x0a) and compression-mode (0x07)
packets are reported, not emitted: the driver sends its own drm_dsc_config.
"""

import re
import sys

DCS_NAMES = {
    0x01: "soft reset",
    0x10: "enter sleep mode",
    0x11: "exit sleep mode",
    0x13: "normal display mode on",
    0x28: "display off",
    0x29: "display on",
    0x2A: "column address",
    0x2B: "page address",
    0x2C: "write memory start",
    0x34: "tear off",
    0x35: "tear on",
    0x36: "address mode",
    0x3A: "pixel format",
    0x44: "tear scanline",
    0x51: "brightness",
    0x53: "write control display",
    0x55: "write power save",
    0x5E: "CABC min brightness",
    0x9D: "compression mode",
}
DCS_TYPES = {0x05, 0x15, 0x39}
GENERIC_TYPES = {0x03, 0x13, 0x23, 0x29}


def block_of(text, header):
    i = text.find(header)
    if i < 0:
        sys.exit(f"node not found: {header}")
    depth = 0
    for j in range(text.index("{", i), len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i : j + 1]
    sys.exit("unbalanced braces")


def prop_bytes(block, prop):
    m = re.search(re.escape(prop) + r"\s*=\s*(\[[^\]]*\]|<[^>]*>)\s*;", block)
    if not m:
        sys.exit(f"property not found: {prop}")
    v = m.group(1)
    if v.startswith("["):
        return bytes(int(x, 16) for x in v[1:-1].split())
    out = b""
    for cell in v[1:-1].split():
        out += int(cell, 0).to_bytes(4, "big")
    return out


def decode(data):
    i = 0
    while i + 7 <= len(data):
        dtype, last, vc, ack, wait = data[i : i + 5]
        dlen = int.from_bytes(data[i + 5 : i + 7], "big")
        payload = data[i + 7 : i + 7 + dlen]
        i += 7 + dlen
        yield dtype, wait, payload
    if i != len(data):
        # cell-encoded streams are padded to a multiple of four bytes
        if any(data[i:]):
            sys.exit(f"trailing bytes: {data[i:].hex()}")


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    text = open(sys.argv[1]).read()
    node = block_of(text, sys.argv[2] + " {")
    timing = sys.argv[4] if len(sys.argv) > 4 else "timing@0"
    src = node
    if sys.argv[3] not in node.split(timing + " {")[0]:
        src = block_of(node, timing + " {")
    for dtype, wait, payload in decode(prop_bytes(src, sys.argv[3])):
        hexes = ", ".join(f"0x{b:02x}" for b in payload)
        if dtype in DCS_TYPES:
            name = DCS_NAMES.get(payload[0]) if payload else None
            note = f" /* {name} */" if name else ""
            print(f"\tmipi_dsi_dcs_write_seq_multi(&dsi_ctx, {hexes});{note}")
        elif dtype in GENERIC_TYPES:
            print(f"\tmipi_dsi_generic_write_seq_multi(&dsi_ctx, {hexes});")
        elif dtype == 0x0A:
            print(f"\t/* PPS packet ({len(payload)} bytes) left to the driver's drm_dsc_config */")
        elif dtype == 0x07:
            print(f"\t/* compression mode {hexes} left to mipi_dsi_compression_mode_multi() */")
        else:
            print(f"\t/* UNKNOWN dtype 0x{dtype:02x}: {hexes} */")
        if wait:
            print(f"\tmipi_dsi_msleep(&dsi_ctx, {wait});")


if __name__ == "__main__":
    main()
