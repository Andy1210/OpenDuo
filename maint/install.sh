#!/bin/bash
# Install the update / snapshot / backup tools on the phone over the network
# (scripts/lib/remote.sh). A freshly built image has them already
# (rootfs/install-components.sh runs the same install-files.sh). The two
# Debian packages they use (rsync for the backup, dpkg-repack for the way
# back of an upgrade) are installed if missing: --no-packages skips that.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
packages="rsync dpkg-repack"
[ "${1:-}" = --no-packages ] && packages=
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage maint)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
tar -C "$here" --exclude=tests --exclude=__pycache__ --mode=go-w -cf - . | duo2_ssh "tar -C $stage --no-same-owner -xf -"
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
sudo systemctl daemon-reload
rm -rf $stage
echo \"installed: \$(ls /usr/local/sbin | grep -E '^duo2-(update|snapshot|backup)\$' | paste -sd ' ')\""
