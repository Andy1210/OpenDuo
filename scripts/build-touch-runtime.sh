#!/usr/bin/env bash
# Assemble the Surface Duo 2 touch runtime (out/duo2-touch/) from the
# phone's OWN stock images.
#
# The digitizer streams raw heat frames; Microsoft's heat processor,
# libsurfacetouch_c1.so from the vendor image, turns them into contacts. It
# is an Android (bionic) library, so it runs with the stock linker64 and the
# stock runtime libraries, resolved the way Android's linker resolves a
# vendor library: the vendor image first, then the VNDK apex of the vendor's
# VNDK version, then the system image, with bionic from its bootstrap
# copies. duo2-touchd (the host process) is built with the Android NDK.
#
# Nothing assembled here may be redistributed: the repository carries only
# this recipe; every user builds the runtime from their own phone's images.
# The approach and duo2-touchd come from luma-duo2 (none24smu, Apache-2.0),
# see docs/hardware/touch.md.
#
#   scripts/build-touch-runtime.sh
#   IMG=<dir with vendor.img system.img system_ext.img> NDK=<android-ndk> \
#   TOUCHD_SRC=<dir with duo2-touchd.cpp digitizer_power.h pen_rules.h autobond.h devgate.h liblog-stderr.c> scripts/build-touch-runtime.sh
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=${IMG:-$R/firmware/ota-2023.501.541/img}
NDK=${NDK:-$R/src/ndk/android-ndk-r27c}
SRC=${TOUCHD_SRC:-$R/touch}
W=$R/out/touch-stock
OUT=$R/out/duo2-touch
CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang

for f in "$IMG/vendor.img" "$IMG/system.img" "$IMG/system_ext.img" "$CC" \
         "$SRC/duo2-touchd.cpp" "$SRC/digitizer_power.h" "$SRC/pen_rules.h" "$SRC/autobond.h" "$SRC/devgate.h" "$SRC/liblog-stderr.c"; do
    [ -e "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

echo "== extract the stock runtime"
rm -rf "$W"; mkdir -p "$W"
docker run --rm -u "$(id -u):$(id -g)" -v "$IMG":/img:ro -v "$W":/w duo2-build bash -c '
set -e
command -v debugfs >/dev/null || { echo "duo2-build lacks debugfs"; exit 1; }
mkdir -p /w/vendor /w/system /w/vndk /w/apex
debugfs -R "dump /build.prop /w/vendor/build.prop" /img/vendor.img >/dev/null 2>&1
debugfs -R "dump /default.prop /w/vendor/default.prop" /img/vendor.img >/dev/null 2>&1
debugfs -R "rdump /lib64 /w/vendor" /img/vendor.img >/dev/null 2>&1
debugfs -R "rdump /system/lib64 /w/system" /img/system.img >/dev/null 2>&1
mkdir -p /w/system/bin
debugfs -R "dump /system/bin/bootstrap/linker64 /w/system/bin/linker64" /img/system.img >/dev/null 2>&1
# ro.vndk.version where the vendor states it, else its SDK level (30 = v30)
V=$(cat /w/vendor/build.prop /w/vendor/default.prop 2>/dev/null | sed -n "s/^ro.vndk.version=//p" | head -1)
[ -n "$V" ] || V=$(sed -n "s/^ro.vendor.build.version.sdk=//p" /w/vendor/build.prop)
echo "vendor VNDK version: $V"
debugfs -R "dump /apex/com.android.vndk.v$V.apex /w/apex/vndk.apex" /img/system_ext.img >/dev/null 2>&1
cd /w/apex && python3 -I -c "import zipfile; zipfile.ZipFile(\"vndk.apex\").extract(\"apex_payload.img\", \".\")"
debugfs -R "rdump /lib64 /w/vndk" /w/apex/apex_payload.img >/dev/null 2>&1
rm -rf /w/apex
'
for d in "$W/vendor/lib64" "$W/system/lib64" "$W/vndk/lib64" "$W/system/lib64/bootstrap"; do
    [ -d "$d" ] || { echo "extraction failed: no $d" >&2; exit 1; }
done

echo "== build duo2-touchd and the liblog shim (NDK)"
# the same build as the touch package's (touch/build-touchd.sh)
NDK=$NDK TOUCHD_SRC=$SRC TOUCHD_OUT=$W/build "$R/touch/build-touchd.sh" >/dev/null

echo "== assemble"
rm -rf "$OUT"; mkdir -p "$OUT/lib64"
install -m 0755 "$W/system/bin/linker64" "$OUT/linker64"
install -m 0755 "$W/build/duo2-touchd" "$OUT/duo2-touchd"
install -m 0644 "$W/build/liblog-stderr.so" "$OUT/liblog-stderr.so"
install -m 0644 "$W/vendor/lib64/c1/libsurfacetouch_c1.so" "$OUT/lib64/"

find_lib() {
    local n=$1 d
    for d in "$W/system/lib64/bootstrap" "$W/vendor/lib64" "$W/vndk/lib64" "$W/system/lib64"; do
        [ -f "$d/$n" ] && { printf '%s' "$d/$n"; return 0; }
    done
    return 1
}

# the dependency closure, to a fixed point
while :; do
    added=0
    for f in "$OUT"/lib64/*.so "$OUT/duo2-touchd"; do
        for n in $(readelf -d "$f" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
            [ -f "$OUT/lib64/$n" ] && continue
            src=$(find_lib "$n") || { echo "no $n in the stock images (needed by $(basename "$f"))" >&2; exit 1; }
            install -m 0644 "$src" "$OUT/lib64/"
            added=1
        done
    done
    [ "$added" = 0 ] && break
done
echo "touch runtime: $(find "$OUT/lib64" -mindepth 1 -maxdepth 1 | wc -l) libraries, $(du -sh "$OUT" | cut -f1) in $OUT"
