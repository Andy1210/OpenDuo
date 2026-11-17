#!/bin/bash
# Install telephony on the phone over the network (scripts/lib/remote.sh):
# the packages (apt, no recommends), then install-files.sh on the phone, then
# the units are (re)started. A freshly built image has all of this
# (rootfs/Dockerfile, rootfs/install-components.sh). A package or file added
# here belongs there too.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"

packages="modemmanager gir1.2-modemmanager-1.0 gnome-calls callaudiod feedbackd gnome-contacts
chatty mmsd-tng mobile-broadband-provider-info"

# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage telephony)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
# lpac, built by lpac/build.sh (not in git)
[ -x "$here/../out/lpac/bin/lpac" ] && duo2_scp -r "$here/../out/lpac" "$DUO2_HOST:$stage/lpac-bin"
duo2_ssh "set -eu
missing=
for p in ${packages//$'\n'/ }; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
sudo sh $stage/install-files.sh $DUO2_USER
rm -rf $stage
sudo systemctl daemon-reload
sudo networkctl reload
sudo systemctl restart duo2-wwan.service
echo \"ModemManager \$(systemctl is-active ModemManager), duo2-wwan \$(systemctl is-active duo2-wwan)\""
duo2_user "systemctl --user daemon-reload
systemctl --user start duo-telephony-session.path"
