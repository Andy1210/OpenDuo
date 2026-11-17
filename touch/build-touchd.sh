#!/usr/bin/env bash
# Build duo2-touchd and the liblog shim (our code: touch/) with the Android
# NDK, in a container, into out/touchd/. The touch package carries the daemon
# (packaging/, docs/UPDATES.md); scripts/build-touch-runtime.sh puts both into
# the runtime it assembles from the phone's own images.
#
#   touch/build-touchd.sh
#   NDK=<android-ndk-r27c> touch/build-touchd.sh
#
# The same NDK and sources give the same bytes: no paths, no timestamps.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
NDK=${NDK:-$R/src/ndk/android-ndk-r27c}
OUT=${TOUCHD_OUT:-$R/out/touchd}
SRC=${TOUCHD_SRC:-$R/touch}
[ -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang" ] ||
    { echo "no Android NDK at $NDK (NDK=...)" >&2; exit 1; }
for f in duo2-touchd.cpp digitizer_power.h pen_rules.h autobond.h devgate.h liblog-stderr.c; do
    [ -f "$SRC/$f" ] || { echo "missing $SRC/$f" >&2; exit 1; }
done
mkdir -p "$OUT"
docker run --rm --platform linux/amd64 -u "$(id -u):$(id -g)" -v "$NDK":/ndk:ro -v "$SRC":/src:ro -v "$OUT":/out \
    debian:trixie bash -euc '
CC=/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang
F="-O2 -Wall -Wextra -ffile-prefix-map=/src=."
"$CC++" $F -static-libstdc++ -o /out/duo2-touchd /src/duo2-touchd.cpp -ldl
"$CC" $F -shared -fPIC -o /out/liblog-stderr.so /src/liblog-stderr.c
'
ls -la "$OUT"
