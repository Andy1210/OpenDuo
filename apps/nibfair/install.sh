#!/bin/bash
# Install Nibfair on the phone over the network (scripts/lib/remote.sh): SDL 3
# (libsdl3-0) if missing, then install-files.sh there. Files only: no service
# is restarted. Build first: ./build-aarch64.sh (out/nibfair).
#
# The pen's buzz comes from duo2-pend's app socket (/run/duo2-pen/haptics,
# rootfs/overlay/usr/local/sbin/duo2-pend): a duo2-pend without it leaves the
# game silent in the hand, nothing else.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"
[ -x "$here/out/nibfair" ] || {
    echo "no out/nibfair: run ./build-aarch64.sh first" >&2
    exit 1
}
packages="libsdl3-0"
stage=$(duo2_stage nibfair)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
duo2_scp "$here/out/nibfair" "$here/install-files.sh" "$here/org.duo2os.Nibfair.desktop" \
    "$here/org.duo2os.Nibfair.svg" "$DUO2_HOST:$stage/"
duo2_ssh "set -eu
missing=
for p in $packages; do dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"; done
[ -z \"\$missing\" ] || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
sudo sh $stage/install-files.sh
rm -rf $stage"
