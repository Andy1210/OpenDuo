#!/usr/bin/env bash
# Install the boot splash on a running phone (scripts/lib/remote.sh): the
# plymouth package (no recommends; its initramfs-tools dependency gets
# update_initramfs=no) and install-files.sh. Takes effect at the next boot.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"
stage=$(duo2_stage plymouth)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
duo2_ssh "set -eu
for p in plymouth plymouth-label; do dpkg -s \$p >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$p; done
sudo sh $stage/install-files.sh
sudo systemctl daemon-reload
rm -rf $stage
systemctl is-enabled duo2-plymouth-quit.service"
