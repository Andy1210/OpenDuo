#!/usr/bin/env python3
"""Turn a `docker export` stream into a clean rootfs tarball.

    docker export <ctr> | python3 -I scripts/filter-export.py out/rootfs.tar [--esp out/esp.tar]

Drops what Docker leaves behind: /.dockerenv (systemd would think it runs in
a container and skip units) and the bind-mounted /etc/hostname, /etc/hosts,
/etc/resolv.conf (tmpfiles.d writes the real ones at first boot). Ownership,
modes, links, device nodes and PAX extended attributes are kept.

--esp FILE: what the image build put under /boot/efi (the mount point of
the ESP: an image installed from the packages has the kernel package's files
and boot entry there) goes into FILE instead, relative to the ESP's root, for
scripts/build-esp.sh; the root keeps the empty mount point.
"""

import sys
import tarfile

DROP = {".dockerenv", "etc/hostname", "etc/hosts", "etc/resolv.conf"}
ESP = "boot/efi/"


def main():
    args = sys.argv[1:]
    esp_path = None
    if len(args) == 3 and args[1] == "--esp":
        esp_path = args[2]
        args = args[:1]
    if len(args) != 1:
        sys.exit(__doc__)
    src = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
    dst = tarfile.open(args[0], "w", format=tarfile.PAX_FORMAT)
    esp = tarfile.open(esp_path, "w", format=tarfile.PAX_FORMAT) if esp_path else None
    kept = dropped = moved = 0
    for m in src:
        # Docker writes ".dockerenv" (no "./"); never strip the dot of a name.
        name = m.name.removeprefix("./").lstrip("/")
        if name in DROP:
            dropped += 1
            continue
        if esp is not None and name.startswith(ESP) and len(name.rstrip("/")) > len(ESP) - 1:
            m.name = name[len(ESP) :]
            esp.addfile(m, src.extractfile(m) if m.isreg() else None)
            moved += 1
            continue
        dst.addfile(m, src.extractfile(m) if m.isreg() else None)
        kept += 1
    dst.close()
    if esp is not None:
        esp.close()
    print(f"{kept} entries kept, {dropped} dropped, {moved} moved to the ESP", file=sys.stderr)


if __name__ == "__main__":
    main()
