#!/bin/bash
# Put the VoLTE trial's pieces on the running phone (scripts/lib/remote.sh;
# telephony/README.md "VoLTE"): 81voltd (built by
# 81voltd/build.sh into out/81voltd) and its unit, NOT enabled; duo2-volte and
# duo2-imsa; duo2-wwan with the fix for other programs' bearers, restarted
# (data comes back in ~2 s). Nothing is started: `sudo duo2-volte start` is
# the trial. Not in the image yet.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
duo2_resolve
bin=$here/../out/81voltd
[ -x "$bin/sbin/81voltd" ] || { echo "no $bin/sbin/81voltd: run telephony/81voltd/build.sh" >&2; exit 1; }
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage volte)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
duo2_scp -r "$bin" "$here/systemd/81voltd.service" "$here/duo2-volte" "$here/duo2-imsa" \
    "$here/duo2-wwan" "$DUO2_HOST:$stage/"
duo2_ssh "set -eu
cd $stage
missing=\$(ldd 81voltd/sbin/81voltd | awk '/not found/ { print \$1 }')
[ -z \"\$missing\" ] || { echo \"81voltd needs: \$missing\" >&2; exit 1; }
if mmcli -m any --voice-list-calls 2>/dev/null | grep 'Call/' | grep -qv terminated; then
    echo 'a call is listed in ModemManager: not restarting duo2-wwan now' >&2; exit 1
fi
sudo install -m 0755 81voltd/sbin/81voltd duo2-volte duo2-imsa /usr/local/sbin/
sudo install -D -m 0644 -t /usr/local/share/doc/81voltd 81voltd/share/doc/81voltd/*
sudo install -D -m 0644 -t /usr/local/share/man/man1 81voltd/share/man/man1/81voltd.1
sudo install -m 0644 81voltd.service /etc/systemd/system/81voltd.service
sudo install -m 0755 duo2-wwan /usr/local/sbin/duo2-wwan
sudo systemctl daemon-reload
sudo systemctl restart duo2-wwan
rm -rf $stage
echo \"81voltd \$(systemctl is-enabled 81voltd 2>/dev/null) / \$(systemctl is-active 81voltd 2>/dev/null); duo2-wwan \$(systemctl is-active duo2-wwan)\""
