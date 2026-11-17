#!/usr/bin/env bash
# systemd-boot's configuration on a new ESP, into DIR: loader.conf and the two
# hand-managed entries, duo2.conf (the known-good kernel) and rescue.conf (the
# rescue initramfs, a shell over USB).
#
#   scripts/lib/esp-entries.sh DIR KVER
#   OPTIONS="..." scripts/lib/esp-entries.sh DIR KVER
#
# Used by scripts/build-esp.sh (the ESP image) and scripts/build-images.sh
# (an image installed from the packages: the kernel package's postinst copies
# duo2.conf's options into its own entry, docs/UPDATES.md).
set -euo pipefail
D=${1:?usage: esp-entries.sh DIR KVER}
KVER=${2:?usage: esp-entries.sh DIR KVER}
OPTIONS=${OPTIONS:-"fw_devlink.sync_state=timeout mem_sleep_default=s2idle panic=30 console=tty0 quiet"}
mkdir -p "$D"

# No 'default': systemd-boot then boots the first entry in sort order, and a
# duo2-try entry (version 99, boot-counted) or a packaged kernel's (version
# 10.N, maint/duo2-boot) sorts before duo2.conf (version 1) until its tries
# are used up. A 'default' pattern would pick a 'bad' entry too.
printf 'timeout 1\nconsole-mode keep\n' >"$D/loader.conf"

ent() { # file title sort-key initrd dtb extra-options
    printf 'title   %s\nsort-key %s\nversion 1\nlinux   /duo2/Image\ninitrd  /duo2/%s\ndevicetree /duo2/%s\noptions %s\n' \
        "$2" "$3" "$4" "$5" "$6 $OPTIONS" >"$D/$1"
}
EC='' # bring-up only: 'earlycon=efifb keep_bootcon ignore_loglevel' (duo2-try --append)
# A seamless boot (docs/hardware/boot.md): fbcon is never bound
# to the panels (it maps every console to fb1, which does not exist), so the
# UEFI picture stays until duo-wc's first frame and no console text shows,
# also not at shutdown. Only for duo2.conf: the rescue shell keeps its text.
SEAMLESS='fbcon=map:1 vt.global_cursor_default=0'
BR='rw fw_devlink=permissive arm-smmu.disable_bypass=0 printk.devkmsg=on hung_task_panic=1 softlockup_panic=1 nmi_watchdog=panic'
# Each entry has its own copy of the one initramfs (scripts/build-initrd.sh):
# duo2-promote renews duo2.conf's, the rescue entry's stays as it is.
ent duo2.conf "duo2-os ($KVER)" duo2 initrd-boot.img sm8350-microsoft-surface-duo2-bringup-j.dtb \
    "duo2.boot=root $BR $EC $SEAMLESS"
ent rescue.conf 'duo2-os rescue shell (USB)' rescue initrd.img sm8350-microsoft-surface-duo2-bringup.dtb \
    "$BR $EC"
