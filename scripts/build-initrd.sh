#!/usr/bin/env bash
# Build the initramfs (out/initrd.img): busybox + rescue/init. It is kernel
# independent (no modules: the boot path loads the display from the root,
# rescue/init early_display); the kernel build is only needed for its
# gen_init_cpio. Both boot entries boot it, each from its own copy on the
# ESP: try a new one with `scripts/duo2-try --same-kernel --initrd
# out/initrd.img`, and duo2-promote makes it duo2.conf's
# /duo2/initrd-boot.img. The rescue entry's /duo2/initrd.img is never
# written by the scripts: renew it by hand, only with one that has booted
# as duo2.conf's (docs/hardware/boot.md).
#
# KOUT=DIR: the kernel build whose gen_init_cpio is used (default out/linux).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
O=$R/out
KOUT=$(cd "${KOUT:-$O/linux}" && pwd)

echo "== rescue initramfs"
# busybox is not committed: fetch Debian's static arm64 build once.
if [ ! -x "$R/rescue/busybox" ]; then
    docker run --rm --platform linux/arm64 -v "$R/rescue":/out debian:trixie bash -c \
        'apt-get update -qq >/dev/null && cd /tmp && apt-get download busybox-static >/dev/null && dpkg -x busybox-static_*.deb x && install -m 755 x/usr/bin/busybox /out/busybox'
fi
cat > "$O/initramfs.list" <<EOF
dir /dev 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/null 666 0 0 c 1 3
nod /dev/kmsg 644 0 0 c 1 11
dir /bin 755 0 0
dir /sbin 755 0 0
dir /proc 755 0 0
dir /sys 755 0 0
dir /run 755 0 0
dir /tmp 1777 0 0
dir /mnt 755 0 0
dir /mnt/root 755 0 0
dir /var 755 0 0
file /bin/busybox /work/rescue/busybox 755 0 0
file /init /work/rescue/init 755 0 0
EOF
docker run --rm -u "$(id -u):$(id -g)" -v "$R":/work -v "$KOUT":/kout:ro duo2-build bash -c \
    "/kout/usr/gen_init_cpio /work/out/initramfs.list | gzip -9 > /work/out/initrd.img"
ls -la "$O/initrd.img"
