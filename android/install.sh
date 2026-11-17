#!/bin/bash
# Install Waydroid (Android apps) on the phone over the network
# (scripts/lib/remote.sh): repo.waydro.id (pinned, rootfs/overlay/etc/apt),
# the packages (no recommends), install-files.sh on the phone. A freshly built
# image has all of this (rootfs/Dockerfile, rootfs/install-components.sh);
# a package or file added here belongs there too.
#
# The Android images and `waydroid init` are NOT here (no images in git or in
# the image): docs/hardware/android.md, quick start.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"

packages="waydroid lxc dnsmasq-base nftables apparmor"

tar -C "$here/../rootfs/overlay" -cf - etc/apt/sources.list.d/waydroid.sources \
    etc/apt/preferences.d/waydroid usr/share/keyrings/waydroid.asc |
    duo2_ssh 'sudo tar --no-same-owner -C / -xf -'
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage android)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
duo2_ssh "set -eu
missing=
for p in $packages; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
sudo sh $stage/install-files.sh $DUO2_USER
rm -rf $stage
sudo systemctl daemon-reload
if [ -d /sys/kernel/security/apparmor ]; then sudo apparmor_parser -r -W /etc/apparmor.d/lxc-duo2-waydroid; fi
echo \"waydroid-container: \$(systemctl is-enabled waydroid-container 2>/dev/null) / \$(systemctl is-active waydroid-container)\""
duo2_user "systemctl --user daemon-reload"
