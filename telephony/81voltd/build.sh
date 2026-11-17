#!/bin/sh
# Build 81voltd for the phone in Docker (aarch64, Debian trixie): -> out/81voltd/
# (sbin/81voltd, the man page, the license), installed by ../install-files.sh.
#
#   telephony/81voltd/build.sh       the pinned upstream commit + patches/
#
# Upstream: gitlab.postmarketos.org/modem/81voltd (moved from
# gitlab.com/flamingradian/81voltd), GPL-2.0-or-later. The source is fetched at
# a fixed commit (1.2.0 and its fix for undecodable QRTR packets) and checked;
# our patches (patches/, telephony/README.md "VoLTE") are applied on top.
# C23 (gnu2x): the source uses true/false without <stdbool.h> (Debian's
# forky/sid GCC 15 defaults to C23; trixie's GCC 14 to C17).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
VOLTD_URL=${VOLTD_URL:-https://gitlab.postmarketos.org/modem/81voltd}
VOLTD_COMMIT=${VOLTD_COMMIT:-a7794dd6c8ac216a97dc5a931edab2dfc46eca2a}
work=$repo/out/81voltd-build
out=$repo/out/81voltd
image=duo2-81voltd-cross:trixie
mkdir -p "$work"
if [ ! -d "$work/upstream/.git" ]; then
    rm -rf "$work/upstream"
    git clone -q "$VOLTD_URL" "$work/upstream"
fi
git -C "$work/upstream" cat-file -e "$VOLTD_COMMIT^{commit}" 2>/dev/null ||
    git -C "$work/upstream" fetch -q origin
# A fresh tree at the pinned commit, then the patches.
rm -rf "$work/src"
git -C "$work/upstream" worktree prune
git -C "$work/upstream" worktree add -q --detach "$work/src" "$VOLTD_COMMIT"
got=$(git -C "$work/src" rev-parse HEAD)
[ "$got" = "$VOLTD_COMMIT" ] || { echo "81voltd is at $got, expected $VOLTD_COMMIT" >&2; exit 1; }
git -C "$work/src" -c user.name=duo2-os -c user.email=duo2-os@localhost am -q "$here"/patches/*.patch
docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$here" >/dev/null
docker run --rm --user "$(id -u):$(id -g)" -v "$work:/w" -w /w "$image" sh -euc '
cat > /w/cross.txt <<T
[binaries]
c = '"'"'aarch64-linux-gnu-gcc'"'"'
ar = '"'"'aarch64-linux-gnu-ar'"'"'
strip = '"'"'aarch64-linux-gnu-strip'"'"'
pkg-config = '"'"'pkg-config'"'"'
[properties]
pkg_config_libdir = ['"'"'/usr/lib/aarch64-linux-gnu/pkgconfig'"'"', '"'"'/usr/share/pkgconfig'"'"']
[host_machine]
system = '"'"'linux'"'"'
cpu_family = '"'"'aarch64'"'"'
cpu = '"'"'aarch64'"'"'
endian = '"'"'little'"'"'
T
rm -rf /w/build /w/stage
meson setup /w/build /w/src --cross-file /w/cross.txt --prefix=/usr/local --bindir=sbin \
    --buildtype=release -Dc_std=gnu2x >/w/meson-setup.log 2>&1 || { tail -20 /w/meson-setup.log; exit 1; }
ninja -C /w/build >/w/ninja.log 2>&1 || { tail -40 /w/ninja.log; exit 1; }
grep -i warning /w/ninja.log || true
DESTDIR=/w/stage meson install --quiet -C /w/build --strip >/dev/null
'
rm -rf "$out" && mkdir -p "$out/sbin" "$out/share/doc/81voltd" "$out/share/man/man1"
cp -a "$work/stage/usr/local/sbin/81voltd" "$out/sbin/"
cp -a "$work/stage/usr/local/share/man/man1/81voltd.1" "$out/share/man/man1/" 2>/dev/null ||
    find "$work/stage" -name '81voltd.1*' -exec cp -a {} "$out/share/man/man1/" \;
cp -a "$work/src/LICENSE" "$work/src/README.md" "$out/share/doc/81voltd/"
{
    echo "81voltd for duo2-os: upstream $VOLTD_URL"
    echo "commit $VOLTD_COMMIT (version 1.2.0 + 'qvd-server: handle qrtr decoding errors')"
    echo "with the patches of duo2-os telephony/81voltd/patches:"
    for p in "$here"/patches/*.patch; do echo "  $(basename "$p")"; done
} > "$out/share/doc/81voltd/README.duo2"
find "$out" -type f | sed "s|$out/||" | sort
file "$out/sbin/81voltd"
