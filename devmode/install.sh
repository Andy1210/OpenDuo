#!/bin/bash
# Install Developer mode on the phone over the network (scripts/lib/remote.sh):
# install-files.sh on the phone, with check-personal-data for the bug
# report's masking and the VoLTE logger for the developer tools. Nothing is switched: /etc/duo-devmode.conf is kept, and a
# phone from before Developer mode gets one that says what it does already
# (on: ssh and the USB network stay). A freshly built image has all of this
# (rootfs/Dockerfile, rootfs/install-components.sh).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"

# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage devmode)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
tar -C "$here" --exclude=tests --exclude=__pycache__ --mode=go-w -cf - . | duo2_ssh "tar -C $stage --no-same-owner -xf -"
duo2_scp "$here/../scripts/check-personal-data" "$DUO2_HOST:$stage/check_personal_data.py"
duo2_scp "$here/../telephony/debug/imsd-logger" "$DUO2_HOST:$stage/imsd-logger"
duo2_ssh "set -eu
sudo sh $stage/install-files.sh $DUO2_USER
rm -rf $stage
sudo /usr/local/sbin/duo-devmode status"
