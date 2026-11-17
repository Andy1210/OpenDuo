#!/usr/bin/env python3
"""Extract partition images from a FULL Android OTA payload.bin (stdlib only).

    python3 -I scripts/payload-extract.py <payload.bin> <out dir> [partition ...]

Handles the operations a full OTA uses (REPLACE, REPLACE_BZ, REPLACE_XZ, ZERO,
DISCARD); a delta OTA (SOURCE_*, *DIFF) is refused. Every written image is
checked against the SHA-256 in the manifest's new_partition_info.
"""

import bz2
import hashlib
import lzma
import os
import struct
import sys


def varint(buf, i):
    shift = val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if b < 0x80:
            return val, i
        shift += 7


def fields(buf):
    """Yield (field number, wire type, value) of one protobuf message."""
    i = 0
    while i < len(buf):
        key, i = varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(buf, i)
        elif wt == 1:
            v = buf[i : i + 8]
            i += 8
        elif wt == 2:
            n, i = varint(buf, i)
            v = buf[i : i + n]
            i += n
        elif wt == 5:
            v = buf[i : i + 4]
            i += 4
        else:
            raise ValueError(f"wire type {wt}")
        yield num, wt, v


def parse_partition(buf):
    p = {"name": None, "ops": [], "size": None, "hash": None}
    for num, _, v in fields(buf):
        if num == 1:
            p["name"] = v.decode()
        elif num == 8:
            op = {"type": 0, "off": 0, "len": 0, "dst": []}
            for n2, _, v2 in fields(v):
                if n2 == 1:
                    op["type"] = v2
                elif n2 == 2:
                    op["off"] = v2
                elif n2 == 3:
                    op["len"] = v2
                elif n2 == 6:
                    e = dict((n3, v3) for n3, _, v3 in fields(v2))
                    op["dst"].append((e.get(1, 0), e.get(2, 0)))
            p["ops"].append(op)
        elif num == 7:
            for n2, _, v2 in fields(v):
                if n2 == 1:
                    p["size"] = v2
                elif n2 == 2:
                    p["hash"] = v2
    return p


REPLACE, REPLACE_BZ, ZERO, DISCARD, REPLACE_XZ = 0, 1, 6, 7, 8


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, out, wanted = sys.argv[1], sys.argv[2], set(sys.argv[3:])
    os.makedirs(out, exist_ok=True)
    with open(src, "rb") as f:
        magic, version, msize = struct.unpack(">4sQQ", f.read(20))
        if magic != b"CrAU" or version != 2:
            sys.exit(f"not a v2 payload: {magic!r} v{version}")
        (sigsize,) = struct.unpack(">I", f.read(4))
        manifest = f.read(msize)
        data_start = 24 + msize + sigsize
        block = 4096
        parts = []
        for num, _, v in fields(manifest):
            if num == 3:
                block = v
            elif num == 13:
                parts.append(parse_partition(v))
        print(f"block size {block}, {len(parts)} partitions")
        if "--list" in wanted:
            for p in parts:
                print(f"{p['name']:24} {p['size'] or 0:>12}")
            return
        bad = 0
        for p in parts:
            if wanted and p["name"] not in wanted:
                continue
            path = os.path.join(out, p["name"] + ".img")
            with open(path, "wb") as o:
                for op in p["ops"]:
                    t = op["type"]
                    if t in (REPLACE, REPLACE_BZ, REPLACE_XZ):
                        f.seek(data_start + op["off"])
                        data = f.read(op["len"])
                        if t == REPLACE_BZ:
                            data = bz2.decompress(data)
                        elif t == REPLACE_XZ:
                            data = lzma.decompress(data)
                        pos = 0
                        for start, nblk in op["dst"]:
                            o.seek(start * block)
                            o.write(data[pos : pos + nblk * block])
                            pos += nblk * block
                    elif t in (ZERO, DISCARD):
                        for start, nblk in op["dst"]:
                            o.seek(start * block)
                            o.write(b"\0" * (nblk * block))
                    else:
                        sys.exit(f"{p['name']}: operation {t} needs a source image (delta OTA)")
                if p["size"] is not None:
                    o.truncate(p["size"])
            h = hashlib.sha256()
            with open(path, "rb") as r:
                for chunk in iter(lambda: r.read(1 << 22), b""):
                    h.update(chunk)
            ok = p["hash"] is None or h.digest() == p["hash"]
            bad += not ok
            print(
                f"{p['name']:24} {p['size'] or 0:>12} {'ok' if ok else 'HASH MISMATCH'}", flush=True
            )
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
