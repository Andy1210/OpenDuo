#!/bin/bash
# Install Duo Settings on the phone over the network (scripts/lib/remote.sh):
# copy this directory and compositor/duo-wc/tools/touch-calib.py, run
# install-files.sh there. A freshly built image has it already
# (rootfs/install-components.sh runs the same install-files.sh).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"
packages="python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
stage=$(duo2_stage duo-settings)
stamp=$(mktemp)
"$here/../../scripts/lib/release-stamp.sh" > "$stamp"
duo2_scp "$stamp" "$DUO2_HOST:$stage.stamp"
rm -f "$stamp"
duo2_ssh "rm -rf $stage && mkdir -p $stage/duosettings"
duo2_scp "$here"/duo-settings "$here"/duo-settings-helper "$here"/duo-settings-qmi "$here"/duo-voicemail \
    "$here"/duo-appearance \
    "$here"/install-files.sh "$here"/org.duo2os.Settings.desktop "$here"/org.duo2os.Voicemail.desktop \
    "$here"/org.duo2os.Settings.svg "$here"/duo-panel-refresh.service \
    "$here/../../compositor/duo-wc/tools/touch-calib.py" "$here/../../rootfs/install-os-release.sh" "$DUO2_HOST:$stage/"
duo2_scp "$here"/duosettings/*.py "$DUO2_HOST:$stage/duosettings/"
duo2_ssh "mkdir -p $stage/systemd"
duo2_scp "$here"/systemd/duo-appearance.service "$here"/systemd/duo-appearance.timer "$DUO2_HOST:$stage/systemd/"
duo2_ssh "set -eu
missing=
for p in $packages; do dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"; done
[ -z \"\$missing\" ] || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
sudo install -m 0644 $stage.stamp /etc/duo2-release
sudo sh $stage/install-os-release.sh
rm -f $stage.stamp
sudo sh $stage/install-files.sh $DUO2_USER
sudo systemctl daemon-reload
rm -rf $stage"
