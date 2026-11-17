#!/bin/sh
# Put the update / snapshot / backup tools in place, as root, on the phone
# (install.sh runs it there) or in the image (rootfs/install-components.sh
# runs it under qemu): one list for both. docs/UPDATES.md.
#
#   install-files.sh [USER]   (run from this directory; USER = the GUI user, who
#                             may run duo2-updates' Settings commands)
set -eu
U=${1:-}
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

install -d /usr/local/lib/duo2-maint/duo2maint /usr/local/share/duo2-maint
install -m 0644 duo2maint/__init__.py duo2maint/aptplan.py duo2maint/backup.py duo2maint/debver.py \
    duo2maint/snapshot.py duo2maint/system.py duo2maint/bootentry.py duo2maint/health.py \
    duo2maint/notify.py duo2maint/updates.py /usr/local/lib/duo2-maint/duo2maint/
# root's tools (the administrator runs them with sudo; the GUI user has no
# sudo, except for duo2-updates' Settings commands below)
install -m 0755 duo2-update duo2-snapshot duo2-backup duo2-boot duo2-updates /usr/local/lib/duo2-maint/
for t in duo2-update duo2-snapshot duo2-backup duo2-boot duo2-updates; do
    ln -sf /usr/local/lib/duo2-maint/$t /usr/local/sbin/$t
done
install -m 0644 backup.conf.example /usr/local/share/duo2-maint/backup.conf.example
install -d -m 0755 /etc/duo2
install -m 0644 backup.filter /etc/duo2/backup.filter

# never a Debian kernel, boot loader installer or firmware package
install -m 0644 apt/preferences.d/duo2-never /etc/apt/preferences.d/duo2-never

# the nightly backup: installed, NOT enabled (the owner sets the target first)
install -m 0644 systemd/duo2-backup.service systemd/duo2-backup.timer /etc/systemd/system/
install -d -m 0700 /var/backups/duo2

# The packaged kernels' boot entries (duo2-boot): the health checks before
# boot-complete.target, the blessing after it (both only on a boot from a
# packaged entry). Settings > Updates (duo2-updates): the daily check, the
# install, the nightly automatic install (does nothing until switched on).
[ -e /etc/duo2/boot.conf ] || install -m 0644 boot.conf /etc/duo2/boot.conf
install -m 0644 boot.conf /etc/duo2/boot.conf.dist
install -m 0644 systemd/duo2-boot-health.service systemd/duo2-boot-bless.service \
    systemd/duo2-updates-check.service systemd/duo2-updates-check.timer \
    systemd/duo2-updates-check-timer.service systemd/duo2-updates-install.service \
    systemd/duo2-updates-auto.service systemd/duo2-updates-auto.timer /etc/systemd/system/
enable_unit() { # unit target: systemctl enable, or by hand under qemu
    systemctl enable "$1" >/dev/null 2>&1 ||
        { install -d "/etc/systemd/system/$2" &&
          ln -sf "/etc/systemd/system/$1" "/etc/systemd/system/$2/"; }
}
enable_unit duo2-boot-health.service boot-complete.target.requires
enable_unit duo2-boot-bless.service multi-user.target.wants
enable_unit duo2-updates-check.timer timers.target.wants
enable_unit duo2-updates-auto.timer timers.target.wants
install -d -m 0755 /var/lib/duo2-boot /var/lib/duo2-updates
if [ -n "$U" ]; then
    rule=$(mktemp)
    T=/usr/local/sbin/duo2-updates
    echo "$U ALL=(root) NOPASSWD: $T status, $T check, $T install, $T set auto on, $T set auto off, $T set notify on, $T set notify off, $T set mobile-data on, $T set mobile-data off, $T set channel stable, $T set channel testing" >"$rule"
    visudo -cqf "$rule"
    install -m 0440 "$rule" /etc/sudoers.d/duo2-updates
    rm -f "$rule"
fi
