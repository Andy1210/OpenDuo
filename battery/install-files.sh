#!/bin/sh
# Put the battery usage collector in place, as root, on the phone (install.sh
# runs it there) or in the image (rootfs/install-components.sh under qemu).
#
#   install-files.sh USER     (USER = the GUI user, who may read the store)
#
# Microsoft's power figures are not part of this: they come from the
# owner's own stock images (scripts/build-power-profile.sh,
# scripts/install-power-profile.sh); without them the collector uses the
# kernel's energy model and its own assumptions.
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo2-battery
rm -rf "$L.new"
install -d "$L.new/duo2battery"
install -m 0755 duo2-battery-stats "$L.new/"
install -m 0644 duo2battery/*.py "$L.new/duo2battery/"
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"

# Who may read the usage history: the GUI user (and root). Personal data:
# it stays on the phone.
getent group duo2-battery >/dev/null || groupadd --system duo2-battery
id -nG "$U" 2>/dev/null | tr ' ' '\n' | grep -qx duo2-battery || usermod -aG duo2-battery "$U"

install -m 0644 duo2-battery-stats.service /etc/systemd/system/duo2-battery-stats.service
install -D -m 0755 duo2-battery-stats.sleep /usr/lib/systemd/system-sleep/duo2-battery-stats
systemctl enable duo2-battery-stats.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/system/multi-user.target.wants &&
      ln -sf /etc/systemd/system/duo2-battery-stats.service /etc/systemd/system/multi-user.target.wants/; }
