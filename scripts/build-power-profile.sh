#!/usr/bin/env bash
# Extract the Surface Duo 2's power profile (Android's power_profile.xml:
# Microsoft's measured currents per CPU speed, screen, radios, DSPs) from the
# phone's OWN stock images into out/power-profile/power_profile.xml, for the
# battery usage estimates (battery/).
#
# Android reads it from framework-res.apk, overridden by a vendor overlay:
# on this phone the vendor image's /overlay/FrameworksResTarget.apk carries
# it (res/xml/power_profile.xml, compiled XML; scripts/axml2xml.py decodes
# it). The file is Microsoft's: it is never committed or put in the image;
# scripts/install-power-profile.sh puts it on the phone.
#
#   scripts/build-power-profile.sh
#   IMG=<dir with vendor.img system.img> scripts/build-power-profile.sh
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=${IMG:-$R/firmware/ota-2023.501.541/img}
W=$R/out/power-profile/stock
OUT=$R/out/power-profile/power_profile.xml
for f in "$IMG/vendor.img" "$IMG/system.img"; do
    [ -e "$f" ] || { echo "missing: $f" >&2; exit 1; }
done
rm -rf "$W"; mkdir -p "$W/overlay"
docker run --rm -u "$(id -u):$(id -g)" -v "$IMG":/img:ro -v "$W":/w duo2-build bash -c '
set -e
command -v debugfs >/dev/null || { echo "duo2-build lacks debugfs"; exit 1; }
for a in $(debugfs -R "ls -p /overlay" /img/vendor.img 2>/dev/null | awk -F/ "\$6 ~ /\\.apk\$/ {print \$6}"); do
    debugfs -R "dump /overlay/$a /w/overlay/$a" /img/vendor.img >/dev/null 2>&1
done
debugfs -R "dump /system/framework/framework-res.apk /w/framework-res.apk" /img/system.img >/dev/null 2>&1
'
# The vendor overlay that carries a power profile wins over framework-res.
src=
for a in "$W"/overlay/*.apk; do
    python3 -I -c 'import sys, zipfile; sys.exit(0 if "res/xml/power_profile.xml" in zipfile.ZipFile(sys.argv[1]).namelist() else 1)' "$a" 2>/dev/null &&
        { [ -z "$src" ] || { echo "more than one overlay has a power profile: $src $a" >&2; exit 1; }; src=$a; }
done
[ -n "$src" ] || src=$W/framework-res.apk
python3 -I "$R/scripts/axml2xml.py" "$src" res/xml/power_profile.xml >"$OUT.tmp"
python3 -I -c 'import sys, xml.etree.ElementTree as ET; r = ET.parse(sys.argv[1]).getroot(); assert r.tag == "device" and len(r)' "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
rm -rf "$W"
echo "power profile from $(basename "$src"): $(grep -c '<item\|<array' "$OUT") entries in $OUT"
