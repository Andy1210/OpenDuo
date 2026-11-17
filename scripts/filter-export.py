#!/usr/bin/env python3
"""Turn a `docker export` stream into a clean rootfs tarball.

    docker export <ctr> | python3 -I scripts/filter-export.py out/rootfs.tar

Drops what Docker leaves behind: /.dockerenv (systemd would think it runs in
a container and skip units) and the bind-mounted /etc/hostname, /etc/hosts,
/etc/resolv.conf (tmpfiles.d writes the real ones at first boot). Ownership,
modes, links, device nodes and PAX extended attributes are kept.
"""

import sys
import tarfile

DROP = {".dockerenv", "etc/hostname", "etc/hosts", "etc/resolv.conf"}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
    dst = tarfile.open(sys.argv[1], "w", format=tarfile.PAX_FORMAT)
    kept = dropped = 0
    for m in src:
        # Docker writes ".dockerenv" (no "./"); never strip the dot of a name.
        name = m.name.removeprefix("./").lstrip("/")
        if name in DROP:
            dropped += 1
            continue
        dst.addfile(m, src.extractfile(m) if m.isreg() else None)
        kept += 1
    dst.close()
    print(f"{kept} entries kept, {dropped} dropped", file=sys.stderr)


if __name__ == "__main__":
    main()
