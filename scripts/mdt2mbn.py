#!/usr/bin/env python3
"""Join a split Qualcomm firmware (name.mdt + name.bNN) into one name.mbn.

    python3 -I scripts/mdt2mbn.py <in/name.mdt> <out/name.mbn>

The .mdt holds the ELF header, the program headers and the hash segment; each
loadable segment i with a file size lives in name.b<i:02d>. The joined image is
the ELF with every segment placed at its p_offset, which is what the mainline
remoteproc / GPU zap loaders read (same result as linux-msm's pil-squasher).
"""

import os
import struct
import sys


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    mdt, out = sys.argv[1], sys.argv[2]
    base = mdt[:-4]
    hdr = open(mdt, "rb").read()
    if hdr[:4] != b"\x7fELF":
        sys.exit(f"{mdt}: not an ELF")
    is64 = hdr[4] == 2
    if is64:
        (phoff,) = struct.unpack_from("<Q", hdr, 0x20)
        phentsize, phnum = struct.unpack_from("<HH", hdr, 0x36)
    else:
        (phoff,) = struct.unpack_from("<I", hdr, 0x1C)
        phentsize, phnum = struct.unpack_from("<HH", hdr, 0x2A)
    # Only the ELF header + program headers are taken as-is: in a .mdt the hash
    # segment is packed right behind them, not at its own p_offset.
    image = bytearray(hdr[: phoff + phnum * phentsize])
    for i in range(phnum):
        off = phoff + i * phentsize
        if is64:
            _, _, p_offset, _, _, p_filesz = struct.unpack_from("<IIQQQQ", hdr, off)
        else:
            _, p_offset, _, _, p_filesz = struct.unpack_from("<IIIII", hdr, off)
        if p_filesz == 0:
            continue
        seg = f"{base}.b{i:02d}"
        if os.path.exists(seg):
            data = open(seg, "rb").read()
            if len(data) != p_filesz:
                sys.exit(f"{seg}: {len(data)} bytes, program header says {p_filesz}")
        elif p_offset == 0 and p_filesz <= len(image):
            continue  # segment 0 = the headers themselves
        elif i < 2 and len(hdr) >= len(image) + p_filesz:
            data = hdr[len(image) : len(image) + p_filesz]  # hash segment packed in the .mdt
        else:
            sys.exit(f"{seg}: missing")
        end = p_offset + p_filesz
        if len(image) < end:
            image.extend(b"\0" * (end - len(image)))
        image[p_offset:end] = data
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(image)
    print(f"{os.path.basename(out)}: {phnum} segments, {len(image)} bytes")


if __name__ == "__main__":
    main()
