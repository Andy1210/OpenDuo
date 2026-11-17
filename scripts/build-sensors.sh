#!/usr/bin/env bash
# Build the free part of the sensor stack for the phone (sensors/Dockerfile:
# hexagonrpcd with our patch, libssc, iio-sensor-proxy with SSC support) and
# export it to out/duo2-sensors-bin/ (a tree under usr/local).
#
#   scripts/build-sensors.sh            # arm64 under qemu, ~10 min
#   REBUILD=1 scripts/build-sensors.sh  # ignore the docker layer cache
#   VERBOSE=1 scripts/build-sensors.sh  # hexagonrpcd logs every file call the DSP makes
#
# The stock half (registry, json config, DSP skeletons) is
# scripts/build-sensors-runtime.sh; both go onto the phone with
# scripts/install-sensors.sh.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
OUT=$R/out/duo2-sensors-bin
LOG=$R/out/build-sensors.log
args=(--progress=plain --platform linux/arm64 -t duo2-sensors)
[ "${REBUILD:-0}" = 1 ] && args+=(--no-cache)
[ "${VERBOSE:-0}" = 1 ] && args+=(--build-arg HEXAGONRPCD_VERBOSE=true)
mkdir -p "$R/out"
docker build "${args[@]}" "$R/sensors" >"$LOG" 2>&1 || { tail -40 "$LOG" >&2; echo "build failed, log: $LOG" >&2; exit 1; }
CID=$(docker create --platform linux/arm64 duo2-sensors /bin/true)
trap 'docker rm "$CID" >/dev/null' EXIT
rm -rf "$OUT"; mkdir -p "$OUT"
docker cp "$CID:/staging/." "$OUT/"
find "$OUT" -type f -printf '%P\n' | sort
