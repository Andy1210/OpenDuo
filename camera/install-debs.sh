#!/usr/bin/env bash
# Put the camera stack's packages (camera/build-debs.sh, out/camera-debs/) on
# the running phone: apt installs the new libcamera (0.7.2-1~duo2.N) and, of
# the rebuilt pipewire (1.4.2-1+duo2.N), the binary packages the phone already
# has; then the GUI user's PipeWire, WirePlumber and pipewire-pulse restart so
# the camera plugin loads the new libcamera (sound stops for a moment: a call
# or music in progress is cut).
#
#   camera/install-debs.sh [--no-restart]
#
# The image gets the same packages from scripts/build-images.sh.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
D=$R/out/camera-debs
. "$R/scripts/lib/remote.sh"
[ -n "$(ls "$D"/*.deb 2>/dev/null)" ] || { echo "no packages in $D: camera/build-debs.sh first" >&2; exit 1; }

tmp=$(duo2_stage camera-debs)
duo2_ssh "rm -rf $tmp && mkdir -p $tmp"
# not the debug symbols
debs=()
for f in "$D"/*.deb; do
    case $f in *-dbgsym_*) ;; *) debs+=("$f") ;; esac
done
duo2_scp "${debs[@]}" "$DUO2_HOST:$tmp/"
duo2_ssh "set -eu
cd $tmp
want=''
for f in *.deb; do
    p=\${f%%_*}
    case \$p in
        # libcamera: the library, its IPA modules, cam, the V4L2 and GStreamer layers
        libcamera0.7|libcamera-ipa|libcamera-tools|libcamera-v4l2|gstreamer1.0-libcamera) want=\"\$want ./\$f\" ;;
        # pipewire: only what the phone has (they need each other's exact version)
        *) dpkg-query -W -f='\${Status}' \"\$p\" 2>/dev/null | grep -q 'install ok installed' && want=\"\$want ./\$f\" ;;
    esac
done
echo \"== installing:\$want\"
sudo apt-get install -y --no-install-recommends \$want
dpkg-query -W libcamera0.7 libcamera-ipa libspa-0.2-libcamera pipewire 2>/dev/null
rm -rf $tmp"
[ "${1:-}" = --no-restart ] && exit 0
echo "== restarting the GUI user's PipeWire"
duo2_user 'systemctl --user daemon-reload; systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service; sleep 2; systemctl --user --no-pager --plain is-active pipewire.service wireplumber.service pipewire-pulse.service'
