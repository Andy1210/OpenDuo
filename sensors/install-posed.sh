#!/bin/bash
# Install ONLY duo-posed and its helpers on the phone (no hexagonrpcd restart,
# no sensor runtime: scripts/install-sensors.sh does all of that):
#
#   sensors/install-posed.sh
#
#   /usr/local/bin/duo-posed, duo2-ssc, /usr/local/lib/duo-posed/*.py
#   /usr/local/bin/duo2-sensors-mode    (the sensors' quick switch: on | off | status)
#   /usr/local/bin/duo2-pose-check      (the owner's six-position check)
#   /etc/duo-posed.conf.dist            (/etc/duo-posed.conf is never overwritten)
#   /etc/systemd/system/duo-posed.service
# duo-posed is restarted if it runs.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp -a "$R/sensors/duo-posed/duo-posed" "$R/sensors/duo-posed/duo2-ssc" \
      "$R/sensors/duo-posed/duo2_ssc.py" "$R/sensors/duo-posed/posepolicy.py" \
      "$R/sensors/tools/duo2-sensors-mode" "$R/sensors/tools/duo2-pose-check" \
      "$R/sensors/dist/duo-posed.conf" "$R/sensors/dist/duo-posed.service" "$stage/"
# shellcheck disable=SC2016 # the script runs, and expands, on the phone
tar -C "$stage" -czf - . | duo2_ssh 'set -euo pipefail
t=$(mktemp -d); trap "rm -rf $t" EXIT
tar -C "$t" -xzf -
getent passwd duo-posed >/dev/null ||
    sudo useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin --comment "duo-posed (sensors drive duo-wc)" duo-posed
sudo install -d /usr/local/lib/duo-posed
sudo install -m 0644 "$t/duo2_ssc.py" "$t/posepolicy.py" /usr/local/lib/duo-posed/
sudo install -m 0755 "$t/duo-posed" "$t/duo2-ssc" "$t/duo2-sensors-mode" "$t/duo2-pose-check" /usr/local/bin/
sudo install -m 0644 "$t/duo-posed.conf" /etc/duo-posed.conf.dist
[ -e /etc/duo-posed.conf ] || sudo install -m 0644 "$t/duo-posed.conf" /etc/duo-posed.conf
sudo install -m 0644 "$t/duo-posed.service" /etc/systemd/system/duo-posed.service
sudo systemctl daemon-reload
systemctl is-active -q duo-posed.service && sudo systemctl restart duo-posed.service
echo "duo-posed $(systemctl is-active duo-posed); $(duo2-sensors-mode status | tr "\n" " ")"'
