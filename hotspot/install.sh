#!/bin/bash
# Install the hotspot on the phone over the network (scripts/lib/remote.sh):
# hostapd (apt, no recommends), then install-files.sh on the phone. Nothing
# is started: `sudo duo2-hotspot on` (or the quick menu) does that. A
# freshly built image has all of this (rootfs/Dockerfile,
# rootfs/install-components.sh). A package or file added here belongs there
# too.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"

packages="hostapd"

# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage hotspot)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
duo2_ssh "set -eu
missing=
for p in ${packages//$'\n'/ }; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
sudo sh $stage/install-files.sh $DUO2_USER
rm -rf $stage
sudo systemctl daemon-reload
echo \"hostapd \$(dpkg-query -W -f='\${Version}' hostapd), duo2-hotspot \$(systemctl is-active duo2-hotspot)\""
