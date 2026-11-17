#!/bin/sh
# The boot splash (Plymouth, theme duo2): run as root from this directory's
# copy, on the phone (install.sh) or in the image (rootfs/install-components.sh),
# after the plymouth package. docs/hardware/boot.md.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
T=/usr/share/plymouth/themes/duo2
install -d "$T"
python3 -I "$here/make-theme.py" "$T"
chmod 0644 "$T"/*.png
install -m 0644 "$here/duo2.plymouth" "$here/duo2.script" "$T/"

# plymouthd starts once card0 is there. Started earlier (3.2 s) it waits for
# the DRM devices' udev events, and msm's render node (renderD128) comes
# first: Plymouth opened it, found no KMS resources and closed it, and that
# close, the DRM device's last file, restored the fbdev's mode on both
# panels (0.39 s) before it opened card0 and drew. Started after card0 it
# lists the devices instead, card0 first (renderD128's close is then not the
# last one, and quick). The udev rule makes card0 a systemd device; without
# msm the splash starts after 20 s (JobRunningTimeoutSec), as before but late.
# docs/hardware/boot.md.
install -D -m 0644 "$here/70-duo2-card0-systemd.rules" /etc/udev/rules.d/70-duo2-card0-systemd.rules
install -d /etc/systemd/system/dev-dri-card0.device.d /etc/systemd/system/plymouth-start.service.d
printf '# duo2-os: the boot splash waits for card0 at most this long (rootfs/plymouth)\n[Unit]\nJobRunningTimeoutSec=20s\n' \
    >/etc/systemd/system/dev-dri-card0.device.d/duo2-splash.conf
printf '# duo2-os: start once msm'"'"'s KMS node is there (rootfs/plymouth)\n[Unit]\nWants=dev-dri-card0.device\nAfter=dev-dri-card0.device\n' \
    >/etc/systemd/system/plymouth-start.service.d/duo2-after-card0.conf

# The boot initramfs starts this Plymouth (from the root, in a chroot) as
# soon as msm's card0 is there, and it goes on into the root (rescue/init,
# early_display; docs/hardware/boot.md): then this unit
# has nothing to do and is skipped. Without it (an older initramfs, no msm
# in time, duo2.early-display=0) it starts here, as before.
cat >/etc/systemd/system/plymouth-start.service.d/duo2-from-initramfs.conf <<'UNIT'
# duo2-os: skipped when the initramfs's Plymouth already answers (rootfs/plymouth)
[Service]
ExecCondition=/bin/sh -c '! /usr/bin/plymouth --ping'
UNIT

# The theme, at once, at the panels' own scale (both 1344 x 1892).
install -d /etc/plymouth
cat >/etc/plymouth/plymouthd.conf <<'CONF'
# duo2-os (rootfs/plymouth/install-files.sh)
[Daemon]
Theme=duo2
ShowDelay=0
DeviceScale=1
CONF

# Plymouth shows its splash only with "splash" on the kernel command line;
# the boot entry stays as it is (no ESP write): plymouthd is given the line
# with "splash" added.
for unit in plymouth-start:boot plymouth-reboot:reboot plymouth-poweroff:shutdown \
            plymouth-halt:shutdown plymouth-kexec:shutdown; do
    name=${unit%%:*} mode=${unit#*:}
    install -d "/etc/systemd/system/$name.service.d"
    pid=""
    [ "$mode" = boot ] && pid=" --pid-file=/run/plymouth/pid"
    cat >"/etc/systemd/system/$name.service.d/duo2-splash.conf" <<UNIT
# duo2-os: the splash without "splash" in the boot entry (rootfs/plymouth)
[Service]
ExecStart=
ExecStart=/bin/sh -c 'exec /usr/sbin/plymouthd --mode=$mode$pid --attach-to-session --kernel-command-line="\$\$(cat /proc/cmdline) splash"'
UNIT
done

# duo-wc takes the panels over from the splash: plymouth lets go of DRM
# first (deactivate: the splash stays on screen); duo-wc is Type=notify, ready
# when every lit panel shows its first frame, and only then does
# duo2-plymouth-quit tell plymouth to leave, keeping the picture
# (--retain-splash): no black and no stripes between the two. Without
# plymouth the "-" lets duo-wc start as before.
install -d /etc/systemd/system/duo-wc.service.d
cat >/etc/systemd/system/duo-wc.service.d/plymouth.conf <<'UNIT'
# duo2-os: the boot splash hands the panels over (rootfs/plymouth)
[Unit]
After=plymouth-start.service plymouth-quit.service
Conflicts=plymouth-quit.service
OnFailure=plymouth-quit.service
[Service]
ExecStartPre=-+/usr/bin/plymouth deactivate
UNIT
cat >/etc/systemd/system/duo2-plymouth-quit.service <<'UNIT'
# duo2-os: the boot splash leaves once duo-wc shows its first frame on every
# lit panel (duo-wc is Type=notify), keeping its picture until then.
[Unit]
Description=Boot splash leaves after duo-wc's first frame
After=duo-wc.service
ConditionPathExists=/usr/bin/plymouth
[Service]
Type=oneshot
ExecStart=-/usr/bin/plymouth quit --retain-splash
[Install]
WantedBy=graphical.target
UNIT
systemctl enable duo2-plymouth-quit.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/system/graphical.target.wants &&
      ln -sf /etc/systemd/system/duo2-plymouth-quit.service /etc/systemd/system/graphical.target.wants/; }

# initramfs-tools comes with plymouth (a dependency); the kernel and its
# initrd live on the ESP and are ours (scripts/build-initrd.sh): nothing may
# write an initramfs.
conf=/etc/initramfs-tools/update-initramfs.conf
[ -f "$conf" ] && sed -i 's/^update_initramfs=.*/update_initramfs=no/' "$conf"
exit 0
