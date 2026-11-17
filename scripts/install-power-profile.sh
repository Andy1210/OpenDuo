#!/usr/bin/env bash
# Put the power profile (out/power-profile/power_profile.xml, extracted from
# the phone's OWN stock images by scripts/build-power-profile.sh) on a
# running phone as /opt/duo2-power-profile/power_profile.xml and restart the
# battery usage collector, which reads it at its start. Microsoft's file: a
# post-install step, never in the image or in git.
#
#   scripts/build-power-profile.sh && scripts/install-power-profile.sh
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
P=$R/out/power-profile/power_profile.xml
[ -f "$P" ] || { echo "no $P: run scripts/build-power-profile.sh first" >&2; exit 1; }
duo2_ssh 'set -eu
t=$(mktemp); trap "rm -f \"$t\"" EXIT
cat >"$t"
sudo install -D -m 0644 "$t" /opt/duo2-power-profile/power_profile.xml
sudo systemctl try-restart duo2-battery-stats.service 2>/dev/null || true' <"$P"
