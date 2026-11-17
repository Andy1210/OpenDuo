#!/usr/bin/env bash
# Build the camera stack's Debian packages for the phone (docs/hardware/camera.md)
# into out/camera-debs/:
#
#   libcamera 0.7.2: Debian sid's packaging (0.7.2-1) of the upstream release,
#     rebuilt for trixie (as trixie-backports did with 0.7.1: without the
#     TensorFlow Lite build dependency) with camera/libcamera/patches/ on top
#     (the AWB statistics fix from 0.8, the IMX481 / IMX563 sensor helpers,
#     properties and tuning files): version 0.7.2-1~duo2.N.
#     trixie's 0.4.0 has the AWB black level bug that tints every picture
#     green, and no sensor helper for our sensors.
#   pipewire 1.4.2: trixie's own source, unchanged, rebuilt against that
#     libcamera (libcamera has no stable ABI: the camera plugin of trixie's
#     build links libcamera 0.4): version 1.4.2-1+duo2.N. Every binary
#     package of the source is rebuilt (they depend on each other's exact
#     version); the phone gets the ones it has.
#
#   camera/build-debs.sh            # ~15 min on the build machine
#   JOBS=n camera/build-debs.sh     # default 12
#
# Cross-built in a trixie container (camera/Dockerfile). The sources come from
# deb.debian.org and are checked against the hashes below. Nothing here is
# a vendor file: the packages go into the image (scripts/build-images.sh) and
# onto a running phone (camera/install-debs.sh).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
OUT=$R/out/camera-debs
W=$R/out/camera-debs-build
JOBS=${JOBS:-12}
LC_REV=0.7.2-1~duo2.3
PW_REV=1.4.2-1+duo2.1
POOL=http://deb.debian.org/debian/pool/main

# file, sha256 (from the signed .dsc files of libcamera 0.7.2-1 and pipewire 1.4.2-1)
SOURCES="libc/libcamera/libcamera_0.7.2.orig.tar.gz e27f5670fe9443eebc8e2f24235cd20b533324ef481a3684d34c78f1c3a12afb
libc/libcamera/libcamera_0.7.2-1.debian.tar.xz 437475ac9a4266ce481430a6d7868fa3c94f2e3107729713b3df4fa10c1b0d7e
p/pipewire/pipewire_1.4.2.orig.tar.bz2 aa9098001e1bc2f7742140727e19bcd57812b686840923afda2be6c227b88bf0
p/pipewire/pipewire_1.4.2-1.debian.tar.xz 2c9fd6e1c5507a5fd063ea97ae5b2d4a28910a4c8a5a6d97923cc5ab5204fd81"

mkdir -p "$W/src"
# a run that failed inside the container may have left root-owned files
if [ -n "$(find "$W/libcamera" "$W/pipewire" ! -user "$(id -u)" -print -quit 2>/dev/null)" ]; then
    docker run --rm -v "$W":/w debian:trixie rm -rf /w/libcamera /w/pipewire
fi
echo "== sources"
while read -r path sum; do
    f=$W/src/${path##*/}
    if ! echo "$sum  $f" | sha256sum -c --quiet 2>/dev/null; then
        curl -sfL -o "$f" "$POOL/$path"
        echo "$sum  $f" | sha256sum -c --quiet || { echo "checksum mismatch: $f" >&2; exit 1; }
    fi
done <<<"$SOURCES"

entry() {   # package version text...
    local pkg=$1 ver=$2; shift 2
    printf '%s (%s) trixie; urgency=medium\n\n' "$pkg" "$ver"
    for l in "$@"; do printf '  * %s\n' "$l"; done
    printf '\n -- duo2-os <duo2-os@localhost>  %s\n\n' "$(date -R)"
}

echo "== libcamera $LC_REV"
rm -rf "$W/libcamera" && mkdir -p "$W/libcamera"
tar -C "$W/libcamera" -xzf "$W/src/libcamera_0.7.2.orig.tar.gz"
LS=$(ls -d "$W"/libcamera/libcamera-*)
tar -C "$LS" -xJf "$W/src/libcamera_0.7.2-1.debian.tar.xz"
cp "$R"/camera/libcamera/patches/*.patch "$LS/debian/patches/"
(cd "$R/camera/libcamera/patches" && for f in *.patch; do echo "$f"; done) >>"$LS/debian/patches/series"
sed -i '/libtensorflow-lite-dev/d' "$LS/debian/control"
sed -i 's|^\(\s*\)libgstreamer1.0-dev,|\1libegl-dev,\n\1libgles-dev,\n\1libgstreamer1.0-dev,|' "$LS/debian/control"
grep -q '^ *libegl-dev,' "$LS/debian/control" || { echo "control edit failed" >&2; exit 1; }
# no qcam (Qt), no Python bindings, no tests: the rules enable them whatever
# the build profiles say; the python3-libcamera package goes with them
# shellcheck disable=SC2016 # $(QCAM) is make's, in debian/rules
sed -i 's|-Dpycamera=enabled|-Dpycamera=disabled|; s|-Dqcam=$(QCAM)|-Dqcam=disabled|; s|-Dtest=true|-Dtest=false|' "$LS/debian/rules"
if ! grep -q -- '-Dpycamera=disabled' "$LS/debian/rules" || ! grep -q -- '-Dqcam=disabled' "$LS/debian/rules"; then
    echo "rules edit failed" >&2
    exit 1
fi
awk '/^Package: python3-libcamera$/ { skip = 1 } /^$/ { skip = 0 } !skip' "$LS/debian/control" >"$LS/debian/control.new"
mv "$LS/debian/control.new" "$LS/debian/control"
rm -f "$LS/debian/python3-libcamera.install"
{ entry libcamera "$LC_REV" \
    "Rebuild for trixie (duo2-os): without libtensorflow-lite-dev, like 0.7.1-1~bpo13+1." \
    "Without qcam, the Python bindings (python3-libcamera) and the tests." \
    "Build-depend on EGL and GLES (the software ISP's GPU path; qt6-base-dev brought them)." \
    "Patches from camera/libcamera/patches: AWB statistics without saturated pixels (from 0.8)," \
    "  IMX481 and IMX563 sensor helpers, properties and tuning files, a faster soft AGC," \
    "  the soft IPA takes the configured mode's sensor information."
  cat "$LS/debian/changelog"; } >"$LS/debian/changelog.new"
mv "$LS/debian/changelog.new" "$LS/debian/changelog"

echo "== pipewire $PW_REV"
rm -rf "$W/pipewire" && mkdir -p "$W/pipewire"
tar -C "$W/pipewire" -xjf "$W/src/pipewire_1.4.2.orig.tar.bz2"
PS=$(ls -d "$W"/pipewire/pipewire-*)
tar -C "$PS" -xJf "$W/src/pipewire_1.4.2-1.debian.tar.xz"
cp "$R"/camera/pipewire/patches/*.patch "$PS/debian/patches/"
(cd "$R/camera/pipewire/patches" && for f in *.patch; do echo "$f"; done) >>"$PS/debian/patches/series"
{ entry pipewire "$PW_REV" \
    "Rebuild against libcamera $LC_REV (duo2-os): the camera plugin links libcamera 0.7." \
    "spa-libcamera-build-with-libcamera-0.7.patch: its API changes (Model, FrameBuffer planes)."
  cat "$PS/debian/changelog"; } >"$PS/debian/changelog.new"
mv "$PS/debian/changelog.new" "$PS/debian/changelog"

echo "== build image (camera/Dockerfile)"
CTX=$W/ctx
rm -rf "$CTX" && mkdir -p "$CTX/libcamera/debian" "$CTX/pipewire/debian"
cp "$R/camera/Dockerfile" "$CTX/"
cp "$LS/debian/control" "$LS/debian/changelog" "$CTX/libcamera/debian/"
cp "$PS/debian/control" "$PS/debian/changelog" "$CTX/pipewire/debian/"
docker build -q -t duo2-camera-debs "$CTX" >/dev/null

echo "== cross-build (log: $W/build.log)"
docker run --rm -v "$W":/w -e JOBS="$JOBS" -e HU="$(id -u):$(id -g)" duo2-camera-debs bash -c '
set -euo pipefail
# the build runs as root (it installs the new libcamera for pipewire): give
# the tree back to the caller however it ends
trap "chown -R \"\$HU\" /w/libcamera /w/pipewire" EXIT
export DEB_BUILD_OPTIONS="nocheck nodoc parallel=$JOBS"
cd /w/libcamera/libcamera-*
dpkg-buildpackage -a arm64 -b -uc -us -Pcross,nocheck,nodoc,pkg.libcamera.noqt,pkg.libcamera.nopython
cd /w/libcamera
# pipewire builds against these
apt-get install -y -qq --allow-downgrades ./libcamera0.7_*_arm64.deb ./libcamera-dev_*_arm64.deb >/dev/null
cd /w/pipewire/pipewire-*
dpkg-buildpackage -a arm64 -b -uc -us -Pcross,nocheck,nodoc
' >"$W/build.log" 2>&1 || { tail -40 "$W/build.log" >&2; echo "build failed, log: $W/build.log" >&2; exit 1; }

rm -rf "$OUT" && mkdir -p "$OUT"
cp "$W"/libcamera/*.deb "$W"/pipewire/*.deb "$OUT/"
ls -1 "$OUT"
