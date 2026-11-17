#!/bin/bash
# Install the home's encryption on the phone over the network
# (scripts/lib/remote.sh): install-files.sh there. Nothing is encrypted and
# nothing restarts: the boot unit runs at the next start, and the user
# switches encryption on in Settings > Lock screen (homecrypt/README.md). It
# needs a kernel with CONFIG_FS_ENCRYPTION (kernel/config/duo2.config) and
# duo-shell's duo-lock-helper of the same version.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"

# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage homecrypt)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
tar -C "$here" --exclude=tests --exclude=__pycache__ --mode=go-w -cf - . | duo2_ssh "tar -C $stage --no-same-owner -xf -"
duo2_ssh "set -eu
sudo sh $stage/install-files.sh
rm -rf $stage
sudo systemctl daemon-reload
sudo /usr/local/sbin/duo2-home-crypt status"
