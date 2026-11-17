#!/usr/bin/env bash
# Rebuild only the ESP (out/images/esp.simg): systemd-boot from the rootfs
# tarball, the kernel Image + DTB from out/linux, the initramfs (twice: the
# boot entry's and the rescue entry's copy), and the loader entries.
# Usage: OPTIONS="..." scripts/build-esp.sh
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
O=$R/out
KVER=$(cat "$O/linux/include/config/kernel.release")
OPTIONS=${OPTIONS:-"fw_devlink.sync_state=timeout mem_sleep_default=s2idle panic=30 console=tty0 quiet"}

"$R/scripts/build-initrd.sh"

echo "== ESP"
docker run --rm --platform linux/amd64 -e LANG=C.UTF-8 -e DEBIAN_FRONTEND=noninteractive \
    -v "$O":/out -v "$O/linux/arch/arm64/boot":/kboot:ro debian:trixie bash -c "
set -e
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends dosfstools mtools android-sdk-libsparse-utils >/dev/null 2>&1
cd /out
tar -xf rootfs.tar -C /tmp ./usr/lib/systemd/boot/efi/systemd-bootaa64.efi 2>/dev/null || tar -xf rootfs.tar -C /tmp usr/lib/systemd/boot/efi/systemd-bootaa64.efi
rm -f images/esp.img
mkfs.fat -F 32 -S 4096 -n ESP -C images/esp.img 1048576 >/dev/null
export MTOOLS_SKIP_CHECK=1
mmd -i images/esp.img ::/EFI ::/EFI/BOOT ::/loader ::/loader/entries ::/duo2
mcopy -i images/esp.img /tmp/usr/lib/systemd/boot/efi/systemd-bootaa64.efi ::/EFI/BOOT/BOOTAA64.EFI
# No 'default': systemd-boot then boots the first entry in sort order, and a
# duo2-try entry (version 99, boot-counted) or a packaged kernel's (version
# 10.N, maint/duo2-boot) sorts before duo2.conf (version 1) until its tries
# are used up. A 'default' pattern would pick a 'bad' entry too.
printf 'timeout 1\nconsole-mode keep\n' > /tmp/loader.conf
ent() { # file title sort-key initrd dtb extra-options
    printf 'title   %s\nsort-key %s\nversion 1\nlinux   /duo2/Image\ninitrd  /duo2/%s\ndevicetree /duo2/%s\noptions %s\n' \"\$2\" \"\$3\" \"\$4\" \"\$5\" \"\$6 $OPTIONS\" > /tmp/\$1
}
EC=''  # bring-up only: 'earlycon=efifb keep_bootcon ignore_loglevel' (duo2-try --append)
# A seamless boot (docs/hardware/boot.md): fbcon is never bound
# to the panels (it maps every console to fb1, which does not exist), so the
# UEFI picture stays until duo-wc's first frame and no console text shows,
# also not at shutdown. Only for duo2.conf: the rescue shell keeps its text.
SEAMLESS='fbcon=map:1 vt.global_cursor_default=0'
BR='rw fw_devlink=permissive arm-smmu.disable_bypass=0 printk.devkmsg=on hung_task_panic=1 softlockup_panic=1 nmi_watchdog=panic'
# Each entry has its own copy of the one initramfs (scripts/build-initrd.sh):
# duo2-promote renews duo2.conf's, the rescue entry's stays as it is.
ent duo2.conf 'duo2-os ($KVER)' duo2 initrd-boot.img sm8350-microsoft-surface-duo2-bringup-j.dtb \"duo2.boot=root \$BR \$EC \$SEAMLESS\"
ent rescue.conf 'duo2-os rescue shell (USB)' rescue initrd.img sm8350-microsoft-surface-duo2-bringup.dtb \"\$BR \$EC\"
mcopy -i images/esp.img /tmp/loader.conf ::/loader/loader.conf
mcopy -i images/esp.img /tmp/duo2.conf /tmp/rescue.conf ::/loader/entries/
mcopy -i images/esp.img /kboot/Image /out/initrd.img ::/duo2/
mcopy -i images/esp.img /out/initrd.img ::/duo2/initrd-boot.img
mcopy -i images/esp.img /kboot/dts/qcom/sm8350-microsoft-surface-duo2.dtb /kboot/dts/qcom/sm8350-microsoft-surface-duo2-bringup.dtb /kboot/dts/qcom/sm8350-microsoft-surface-duo2-bringup-j.dtb ::/duo2/
mdir -i images/esp.img -/ :: | grep -E 'BOOTAA64|Image|initrd|dtb|conf'
img2simg images/esp.img images/esp.simg
chown -R $(id -u):$(id -g) /out/images
"
ls -la "$O/images/esp.simg"
