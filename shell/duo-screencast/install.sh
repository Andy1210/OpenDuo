#!/bin/bash
# Install screen sharing (the screen-cast portal backend, its chooser, the
# screenshot gate) on the phone over the network (scripts/lib/remote.sh):
# the packages (apt, no recommends), then install-files.sh on the phone and
# duo-shell's portals.conf. A freshly built image has all of this
# (rootfs/Dockerfile, rootfs/install-components.sh). docs/SCREENCAST.md.
#
# The backend starts with duo-wc's next session (a duo-wc restart restarts
# the session: install duo-wc first, compositor/duo-wc/install.sh).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"

packages="xdg-desktop-portal-wlr grim"

stage=$(duo2_stage duo-screencast)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
duo2_scp "$here/../duo-shell/portals.conf" "$DUO2_HOST:$stage/portals.conf"
duo2_ssh "set -eu
missing=
for p in $packages; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
sudo sh $stage/install-files.sh
sudo install -D -m 0644 $stage/portals.conf /etc/xdg/xdg-desktop-portal/portals.conf
rm -rf $stage"
duo2_user "systemctl --user daemon-reload"
echo "installed; the portal backend starts with duo-wc's next session"
