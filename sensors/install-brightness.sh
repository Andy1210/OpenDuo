#!/bin/bash
# Install duo-brightness on the phone (automatic brightness from the light
# sensor):
#
#   sensors/install-brightness.sh
#
#   /usr/local/bin/duo-brightness, /usr/local/lib/duo-brightness/brightnesspolicy.py
#   (duo2_ssc.py comes with duo-posed: sensors/install-posed.sh)
#   /etc/duo2/brightness.conf (only if there is none: it holds the user's switch)
#   /etc/systemd/system/duo-brightness.service, enabled and (re)started
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp -a "$R/sensors/duo-brightness/duo-brightness" "$R/sensors/duo-brightness/brightnesspolicy.py" \
      "$R/sensors/dist/brightness.conf" "$R/sensors/dist/duo-brightness.service" "$stage/"
# shellcheck disable=SC2016 # the script runs, and expands, on the phone
tar -C "$stage" -czf - . | duo2_ssh 'set -euo pipefail
t=$(mktemp -d); trap "rm -rf $t" EXIT
tar -C "$t" -xzf -
[ -e /usr/local/lib/duo-posed/duo2_ssc.py ] || { echo "no duo2_ssc.py: sensors/install-posed.sh first" >&2; exit 1; }
sudo install -d /usr/local/lib/duo-brightness /etc/duo2
sudo install -m 0644 "$t/brightnesspolicy.py" /usr/local/lib/duo-brightness/
sudo install -m 0755 "$t/duo-brightness" /usr/local/bin/
[ -e /etc/duo2/brightness.conf ] || sudo install -m 0644 "$t/brightness.conf" /etc/duo2/brightness.conf
sudo install -m 0644 "$t/duo-brightness.service" /etc/systemd/system/duo-brightness.service
sudo systemctl daemon-reload
sudo systemctl enable duo-brightness.service >/dev/null 2>&1
sudo systemctl restart duo-brightness.service
echo "duo-brightness $(systemctl is-active duo-brightness); $(grep -E "^auto" /etc/duo2/brightness.conf)"'
