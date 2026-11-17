#!/bin/bash
# Install the battery usage collector on the phone over the network
# (scripts/lib/remote.sh) and restart it. A freshly built image has it
# already (rootfs/install-components.sh runs the same install-files.sh).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage battery)
duo2_ssh "rm -rf $stage && mkdir -p $stage/duo2battery"
duo2_scp "$here"/duo2-battery-stats "$here"/duo2-battery-stats.service "$here"/duo2-battery-stats.sleep \
    "$here"/install-files.sh "$DUO2_HOST:$stage/"
duo2_scp "$here"/duo2battery/*.py "$DUO2_HOST:$stage/duo2battery/"
duo2_ssh "set -eu
sudo sh $stage/install-files.sh $DUO2_USER
sudo systemctl daemon-reload
sudo systemctl restart duo2-battery-stats.service
rm -rf $stage"
