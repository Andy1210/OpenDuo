#!/bin/sh
# Build lpac for the phone in Docker (aarch64, Debian trixie): -> out/lpac/
# (bin/lpac and the license texts), installed by ./install.sh.
#
#   telephony/lpac/build.sh            the pinned release (LPAC_REF)
#
# The source is fetched at a fixed tag and checked against its commit.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
LPAC_REF=${LPAC_REF:-v2.3.0}
LPAC_COMMIT=${LPAC_COMMIT:-c2fcf5e4b21c712d54e35a11da2ad9ad134fb821}
work=$repo/out/lpac-build
out=$repo/out/lpac
image=duo2-lpac-cross:trixie
mkdir -p "$work" "$out"
if [ ! -d "$work/src/.git" ]; then
    rm -rf "$work/src"
    git clone -q --depth 1 --branch "$LPAC_REF" https://github.com/estkme-group/lpac "$work/src"
fi
got=$(git -C "$work/src" rev-parse HEAD)
[ "$got" = "$LPAC_COMMIT" ] || { echo "lpac $LPAC_REF is $got, expected $LPAC_COMMIT" >&2; exit 1; }
docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$here" >/dev/null
docker run --rm --user "$(id -u):$(id -g)" -v "$work:/w" -w /w "$image" sh -euc '
cat > /w/toolchain.cmake <<T
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
T
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
rm -rf /w/build && mkdir /w/build && cd /w/build
cmake -S /w/src -B . -DCMAKE_TOOLCHAIN_FILE=/w/toolchain.cmake -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DLPAC_WITH_APDU_PCSC=OFF -DLPAC_WITH_APDU_AT=OFF -DLPAC_WITH_APDU_QMI=ON \
    -DLPAC_WITH_APDU_QMI_QRTR=ON -DLPAC_WITH_APDU_MBIM=OFF -DLPAC_WITH_HTTP_CURL=ON >/dev/null
make -j"${JOBS:-6}" >/dev/null
make DESTDIR=/w/stage install >/dev/null
'
rm -rf "$out" && mkdir -p "$out"
cp -a "$work/stage/usr/local/." "$out/"
mkdir -p "$out/share/doc/lpac" && cp -a "$work/src/LICENSES" "$work/src/REUSE.toml" "$out/share/doc/lpac/"
find "$out" -type f | sed "s|$out/||" | sort
file "$out/bin/lpac"
