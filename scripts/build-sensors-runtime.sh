#!/usr/bin/env bash
# Assemble the files hexagonrpcd serves to the SLPI (the sensor DSP) from the
# phone's OWN stock images: out/duo2-sensors/.
#
# The SLPI runs Qualcomm's Sensor Core (SSC). At start its sensor process
# (sensorspd) reads, over FastRPC "apps_std" file calls answered by
# hexagonrpcd on /dev/fastrpc-sdsp:
#   /vendor/etc/sensors/sns_reg_config     which registry to use, its version
#   /vendor/etc/sensors/config/*.json      the sensor driver configuration
#   /mnt/vendor/persist/sensors/registry/registry/*
#                                          the registry Android generated from
#                                          the json files ON THIS PHONE (factory
#                                          calibration: magnetometer per posture,
#                                          hall, placement matrices)
#   /usr/lib/qcom/adsp/ (= the dsp partition's sdsp/)  FastRPC skeletons
#   /mnt/vendor/persist/sensors/registry/sns_reg_version  the config version the
#                                          registry was generated from
#   /sys/devices/soc0/{hw_platform,platform_subtype_id,...}, Microsoft's
#   /sys/devices/platform/soc/soc:surface_util/surface_product_id/..., and
#   Android properties (ro.build.version.release) over the sns_registry
#   FastRPC interface: the json files select themselves by these.
# hexagonrpcd (with sensors/patches/hexagonrpc) maps these Android paths onto
# one directory (its -R root):
#   sensors/sns_reg.conf, sensors/sns_reg_version, sensors/config/,
#   sensors/registry/, sensors/properties, dsp/sdsp/, socinfo/, sysfs-platform/
# It serves read-only, so the SLPI cannot rewrite the registry: it has to be
# the phone's own, with the same config version as the vendor's sns_reg_config.
#
# The downstream socinfo values and the product id are not in mainline sysfs.
# They are the ones THIS registry was generated with (read off it): an
# MTP-only config (sns_basic_gestures) is in it -> hw_platform MTP; the
# magnetometer orientation is the v2 one -> board subtype 8..52 (the MP board
# is 12, its DTBO overlay); txc_0 and no lps22hh -> surface_product_id 2;
# stock Android 12 -> ro.build.version.release 12. soc_id and revision are
# mainline's (415, 2.1).
#
# Nothing assembled here may be redistributed (Qualcomm / Microsoft files and
# per-device calibration): the repository carries only this recipe; every user
# builds the tree from their own phone's images.
#
#   scripts/build-sensors-runtime.sh
#   IMG=<dir with vendor.img dsp.img> PERSIST=<persist.img of THIS phone> \
#       scripts/build-sensors-runtime.sh
#
# PERSIST is the raw `persist` partition (ext4), saved before the bring-up
# (the installation steps). The tree is installed on the phone by
# scripts/install-sensors.sh.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=${IMG:-$R/firmware/ota-2023.501.541/img}
PERSIST=${PERSIST:-$R/firmware/partitions/persist.img}
OUT=$R/out/duo2-sensors

for f in "$IMG/vendor.img" "$IMG/dsp.img" "$PERSIST"; do
    [ -e "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

tmp=$(mktemp -d "$R/out/duo2-sensors.new.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
pdir=$(cd "$(dirname "$PERSIST")" && pwd)
docker run --rm -u "$(id -u):$(id -g)" -v "$IMG":/img:ro -v "$pdir":/p:ro -v "$tmp":/w duo2-build bash -c '
set -e
command -v debugfs >/dev/null || { echo "duo2-build lacks debugfs"; exit 1; }
mkdir -p /w/x/vendor /w/x/persist /w/x/dsp
debugfs -R "rdump /etc/sensors /w/x/vendor" /img/vendor.img >/dev/null 2>&1
debugfs -R "rdump /sdsp /w/x/dsp" /img/dsp.img >/dev/null 2>&1
debugfs -R "rdump /sensors/registry /w/x/persist" /p/'"$(basename "$PERSIST")"' >/dev/null 2>&1
'
x=$tmp/x
for d in "$x/vendor/sensors/config" "$x/persist/registry/registry" "$x/dsp/sdsp"; do
    [ -d "$d" ] || { echo "extraction failed: no $d" >&2; exit 1; }
done
# The registry must belong to the vendor's configuration (hexagonrpcd is
# read-only, a mismatch would make the SLPI try to rebuild it and fail).
# The SLPI reads the vendor's config and the registry's sns_reg_version, not
# the copy of the config Android keeps beside the registry, which Android does
# not always refresh when it rebuilds the registry (issue #1): an identical
# copy passes, otherwise the versions must agree (as stock/duo2stock.py).
sns_version() { tr '\0' '\n' <"$1" | sed -n 's/^[[:space:]]*[Vv]ersion[[:space:]]*=[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' | head -n 1; }
if ! cmp -s "$x/vendor/sensors/sns_reg_config" "$x/persist/registry/sns_reg_config"; then
    want=$(sns_version "$x/vendor/sensors/sns_reg_config")
    have=$(sns_version "$x/persist/registry/sns_reg_version")
    if [ -z "$want" ] || [ "$want" != "$have" ]; then
        echo "persist registry was built from another sns_reg_config than the vendor image's" \
            "(config version ${want:-none}, registry version ${have:-none})" >&2
        exit 1
    fi
    echo "the registry's copy of sns_reg_config is older; the registry is version $have, as the vendor's config"
fi

mkdir -p "$tmp/t/sensors" "$tmp/t/dsp"
cp -r "$x/vendor/sensors/config" "$tmp/t/sensors/config"
cp "$x/vendor/sensors/sns_reg_config" "$tmp/t/sensors/sns_reg.conf"
cp "$x/persist/registry/sns_reg_version" "$tmp/t/sensors/sns_reg_version"
cp -r "$x/persist/registry/registry" "$tmp/t/sensors/registry"
cp -r "$x/dsp/sdsp" "$tmp/t/dsp/sdsp"

# Device facts for the json selectors (see the header).
s=$tmp/t/socinfo
mkdir -p "$s"
printf '415\n' >"$s/soc_id"
printf '2.1\n' >"$s/revision"
printf 'MTP\n' >"$s/hw_platform"
printf '12\n' >"$s/platform_subtype_id"
printf 'Unknown\n' >"$s/platform_subtype"
printf '65536\n' >"$s/platform_version"
p=$tmp/t/sysfs-platform/soc/soc:surface_util/surface_product_id
mkdir -p "$p"
printf '2\n' >"$p/surface_product_id"
printf 'ro.build.version.release=12\n' >"$tmp/t/sensors/properties"
chmod -R u=rwX,go=rX "$tmp/t"

rm -rf "$OUT"
mv "$tmp/t" "$OUT"
echo "sensor runtime: $(find "$OUT/sensors/config" -type f | wc -l) config files," \
     "$(find "$OUT/sensors/registry" -type f | wc -l) registry entries," \
     "$(find "$OUT/dsp" -type f | wc -l) dsp files, $(du -sh "$OUT" | cut -f1) in $OUT"
