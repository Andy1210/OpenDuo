#!/bin/bash
# Install the firewall on the phone over the network (scripts/lib/remote.sh):
# nftables (apt), then install-files.sh on the phone. It is enabled for the
# next boot; with --start it is loaded now, under a dead man's switch: a
# transient timer stops it again after 10 minutes unless a NEW ssh connection
# gets in after the start (then the timer is cancelled). resolved is
# restarted for its LLMNR drop-in. A freshly built image has all of this
# (rootfs/Dockerfile, rootfs/install-components.sh).
#
#   firewall/install.sh [--start]
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
start=0
[ "${1:-}" = --start ] && start=1

# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage firewall)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
duo2_ssh "set -eu
dpkg -s nftables >/dev/null 2>&1 ||
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends nftables
sudo nft -c -f $stage/firewall.nft
sudo sh $stage/install-files.sh
rm -rf $stage
sudo systemctl daemon-reload"
[ $start = 1 ] || { echo "installed; it is loaded at the next boot (or: $0 --start)"; exit 0; }

duo2_ssh "set -eu
sudo systemctl stop duo-firewall-deadman.timer 2>/dev/null || true
sudo systemd-run --quiet --unit=duo-firewall-deadman --on-active=10min \
    /bin/systemctl stop duo-firewall.service
sudo systemctl start duo-firewall.service
sudo systemctl try-restart systemd-resolved.service"
if duo2_ssh true; then
    duo2_ssh "sudo systemctl stop duo-firewall-deadman.timer
systemctl is-active duo-firewall.service"
    echo "firewall on; a new ssh connection got in: the dead man's switch is cancelled"
else
    echo "no new ssh connection after the start: the firewall goes off within 10 minutes" >&2
    exit 1
fi
