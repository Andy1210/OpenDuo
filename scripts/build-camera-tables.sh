#!/usr/bin/env bash
# The camera sensors' register tables (out/duo2-camera/), generated from the
# phone's OWN stock vendor image, and (with --install) put on a running phone
# under /lib/firmware/qcom/sm8350/microsoft/, where the imx48x driver loads
# them (the DT's firmware-name).
#
# The tables are the camera module maker's initial and mode settings
# (com.qti.sensormodule.<module>.bin in the vendor image, decoded by
# scripts/camx-sensor-tables.py). They may not be redistributed: never in git
# or in an image, a post-install step like the touch runtime.
#
#   scripts/build-camera-tables.sh [--install]
#   IMG=<dir with vendor.img> scripts/build-camera-tables.sh
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=${IMG:-$R/firmware/ota-2023.501.541/img}
W=$R/out/camera-stock
OUT=$R/out/duo2-camera
FW=/lib/firmware/qcom/sm8350/microsoft

[ -e "$IMG/vendor.img" ] || { echo "missing: $IMG/vendor.img" >&2; exit 1; }

# module in the vendor image, model, the file the DT names
MODULES="sony_uw_imx481:0x0481:imx481-uw sony_front_imx481:0x0481:imx481-front
sony_tele_imx481:0x0481:imx481-tele sony_imx563:0x0563:imx563-wide"

echo "== extract the camera module descriptions"
rm -rf "$W"; mkdir -p "$W"
docker run --rm -u "$(id -u):$(id -g)" -v "$IMG":/img:ro -v "$W":/w duo2-build bash -c '
set -e
for m in sony_uw_imx481 sony_front_imx481 sony_tele_imx481 sony_imx563; do
    debugfs -R "dump /lib64/camera/com.qti.sensormodule.$m.bin /w/$m.bin" /img/vendor.img >/dev/null 2>&1
done'

echo "== generate the tables"
rm -rf "$OUT"; mkdir -p "$OUT"
for e in $MODULES; do
    IFS=: read -r mod model name <<<"$e"
    [ -s "$W/$mod.bin" ] || { echo "extraction failed: no $mod.bin" >&2; exit 1; }
    python3 -I "$R/scripts/camx-sensor-tables.py" "$model" "$W/$mod.bin" "$OUT/$name.bin"
done
rm -rf "$W"

[ "${1:-}" = --install ] || exit 0

echo "== install on the phone ($FW)"
. "$R/scripts/lib/remote.sh"
tar -C "$OUT" -czf - . | duo2_ssh "set -eu
t=\$(mktemp -d); trap 'rm -rf \"\$t\"' EXIT
tar -C \"\$t\" -xzf -
sudo install -d $FW
sudo install -m 0644 \"\$t\"/*.bin $FW/
ls -l $FW/imx*.bin"
