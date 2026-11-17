#!/usr/bin/env bash
# Put the touch runtime (out/duo2-touch/, built from the phone's OWN stock
# images by scripts/build-touch-runtime.sh) on a running phone as
# /opt/duo2-touch (scripts/lib/remote.sh), then start duo2-touchd.
#
# The image carries everything else (duo2-touchd.service, enabled, with
# ConditionPathExists=/opt/duo2-touch/duo2-touchd; duo2-touch-calibration,
# which copies the panel alignment from the phone's sfpd partition at the
# service's start; the udev rule; the system-sleep hook that has the daemon
# power the digitizer off across a suspend; /etc/duo2/touch-calibration and
# /etc/duo2/touch-mode, /etc/duo2/pen.conf, the libinput quirk for the pen
# devices, duo2-pend for the pen's Bluetooth side). The runtime itself is Microsoft / Google code that
# may not be redistributed, so it is never in the image or in git: a
# post-install step. This script also puts the image's versions of the
# service, the udev rule, the sleep hook and the calibration on a phone
# installed earlier (the calibration, the mode and the pen settings only if
# the phone has none: they may hold this unit's own refined numbers and
# choices).
#
#   scripts/build-touch-runtime.sh && scripts/install-touch-runtime.sh
#   scripts/install-touch-runtime.sh --daemon out/duo2-touchd
#       only a new duo2-touchd (built with the NDK as build-touch-runtime.sh
#       does) into the runtime the phone has, with the files above
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
T=$R/out/duo2-touch
daemon=
if [ "${1:-}" = --daemon ]; then
    daemon=${2:?--daemon needs the duo2-touchd binary}
    [ -x "$daemon" ] || { echo "no executable $daemon" >&2; exit 1; }
else
    [ -x "$T/duo2-touchd" ] && [ -f "$T/lib64/libsurfacetouch_c1.so" ] ||
        { echo "no $T: run scripts/build-touch-runtime.sh first" >&2; exit 1; }
fi

# Our files go through touch/install-files.sh, the touch package's own
# installer (one list for both), from a staged copy of the component.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
"$R/scripts/lib/stage-components.sh" "$stage" touch
tar -czf - -C "$stage/touch" . | duo2_ssh 'set -eu
t=$(mktemp -d); trap "rm -rf \"\$t\"" EXIT
tar -C "$t" -xzf -
sudo sh "$t/install-files.sh"
sudo systemctl daemon-reload
sudo systemctl enable duo2-pend.service >/dev/null 2>&1
sudo systemctl restart duo2-pend.service
sudo udevadm control --reload'

if [ -n "$daemon" ]; then
    duo2_ssh 'set -eu
[ -d /opt/duo2-touch ] || { echo "no /opt/duo2-touch on the phone: install the whole runtime first" >&2; exit 1; }
t=$(mktemp); trap "rm -f \"\$t\"" EXIT
cat > "$t"
sudo install -m 0755 -o root -g root "$t" /opt/duo2-touch/duo2-touchd.new
sudo mv /opt/duo2-touch/duo2-touchd.new /opt/duo2-touch/duo2-touchd
sudo systemctl restart duo2-touchd.service
echo "duo2-touchd: $(systemctl is-active duo2-touchd)"' < "$daemon"
    exit 0
fi

tar -C "$T" -czf - . | duo2_ssh 'set -eu
t=$(mktemp -d); trap "rm -rf \"\$t\"" EXIT
tar -C "$t" -xzf -
sudo rm -rf /opt/duo2-touch.new
sudo cp -r "$t" /opt/duo2-touch.new
sudo chown -R root:root /opt/duo2-touch.new
sudo chmod 755 /opt/duo2-touch.new
sudo rm -rf /opt/duo2-touch.old
[ -e /opt/duo2-touch ] && sudo mv /opt/duo2-touch /opt/duo2-touch.old
sudo mv /opt/duo2-touch.new /opt/duo2-touch
sudo rm -rf /opt/duo2-touch.old
sudo systemctl restart duo2-touchd.service
echo "duo2-touchd: $(systemctl is-active duo2-touchd), $(ls /opt/duo2-touch/lib64 | wc -l) libraries"'
