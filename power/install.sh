#!/bin/bash
# Install the power pieces on the phone over the network (scripts/lib/remote.sh).
# A freshly built image has them already (rootfs/install-components.sh runs
# the same install-files.sh). Services that run are restarted only when asked:
#   install.sh [--restart]
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage power)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
tar -C "$here" --exclude=tests --exclude=__pycache__ -cf - . | duo2_ssh "tar -C $stage -xf -"
duo2_ssh "set -eu
sudo sh $stage/install-files.sh $DUO2_USER
sudo systemctl daemon-reload
# duo2-torch-off.service must be started to be stopped at shutdown (it does
# nothing else); the torch's rule applied to a LED already there
sudo systemctl start duo2-torch-off.service
sudo udevadm control --reload
sudo udevadm trigger --action=add --subsystem-match=leds --sysname-match='*:flash'
rm -rf $stage"
if [ "${1:-}" = --restart ]; then
    duo2_ssh "sudo systemctl restart duo2-autosleep.service; sudo systemctl try-restart duo2-try-netwatch.service"
fi
