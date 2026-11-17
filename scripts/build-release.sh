#!/usr/bin/env bash
# Build the images OpenDuo publishes for the Surface Duo 2 (docs/INSTALL.md):
#
#   out/release/openduo-VERSION-surface-duo2-rootfs.img.xz   the root file system
#       (ext4, PARTLABEL=linux; it grows to its partition at the first boot)
#   out/release/openduo-VERSION-surface-duo2-esp.img.xz      the ESP (systemd-boot,
#       the kernel, its device trees, the initramfs, the boot entries)
#   out/release/*.img, *.simg                                the same uncompressed, and
#       as Android sparse images (for a `fastboot flash` by hand)
#   out/release/openduo-VERSION-surface-duo2.packages        what is installed (dpkg -l)
#   out/release/install-openduo.sh, udev-rules.sh            the installer and the USB
#                                                            permission helper (Linux)
#   out/release/SHA256SUMS                                   every file above
#
# and scripts/install-openduo.sh puts them on a phone. OpenDuo is installed in
# the image from its own packages (packaging/build-packages.sh, the kernel's
# included), so dpkg owns every file and Settings > Updates works from the
# first boot, against the repository the keyring package names
# (OPENDUO_APT_URL; default https://<git user.name>.github.io/OpenDuo/apt, the
# release workflow's default for the GitHub repository).
#
# The image carries nothing that may not be redistributed and nothing
# personal, and this script checks it before it compresses anything:
#   - no firmware, no touch runtime, no sensor configuration, no camera table,
#     no power profile: duo2-stock-import (stock/) reads them from the phone's
#     own stock partitions at its first boot;
#   - no ssh key, developer mode off (no sshd, no USB network), the default
#     user (scripts/lib/defaults.sh) with a locked password and no sudo; the
#     lock screen has no PIN until one is set in Settings;
#   - no ssh host key, an empty machine-id (both made at the first boot);
#   - local.env is not read (OPENDUO_RELEASE=1).
#
#   scripts/build-release.sh
#   KROOT=DIR KOUT=out/linux-final scripts/build-release.sh   (in a worktree: the
#       kernel build and its tree, src/linux, live in the main checkout)
#
# The built parts are taken as they are (rebuild them after a source change:
# scripts/lib/stage-components.sh names each one and its build script);
# out/camera-debs must hold the camera packages (camera/build-debs.sh). Heavy:
# the root file system is built under qemu (about an hour). JOBS (default 10)
# caps the compression threads. SKIP_BUILD=1 takes the packages and images an
# earlier run built (out/packages, out/images, out/rootfs*.tar) and only checks
# and packs them, under that build's name.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
O=$R/out
REL=$O/release
JOBS=${JOBS:-10}
export KROOT=${KROOT:-$R}
export KOUT=${KOUT:-out/linux}
export KSRC=${KSRC:-src/linux}
g="git -C $R"

if [ "${SKIP_BUILD:-}" != 1 ] && [ -n "$($g status --porcelain --untracked-files=no)" ] && [ "${ALLOW_DIRTY:-}" != 1 ]; then
    echo "build-release: uncommitted changes: commit first (or ALLOW_DIRTY=1 for a test build)" >&2
    exit 1
fi
version=$(tr -d ' \t\r\n' <"$R/VERSION")
hash=$($g rev-parse --short=7 HEAD)
if [ "${SKIP_BUILD:-}" = 1 ]; then
    # the commit the packages were built from (their version ends in .g<hash>)
    deb=$(ls "$O"/packages/debs/openduo-release_*.deb)
    hash=$(basename "$deb" | sed -n 's/.*\.g\([0-9a-f]\{7,\}\)\(\.dirty\)\{0,1\}_all\.deb$/\1/p')
    [ -n "$hash" ] || { echo "build-release: no build to take (SKIP_BUILD=1)" >&2; exit 1; }
fi
# A tagged release is named by its version alone; anything else carries the commit.
if [ "$($g tag --points-at HEAD | grep -cx "v$version" || true)" = 1 ]; then
    name=openduo-$version-surface-duo2
else
    name=openduo-$version-g$hash-surface-duo2
fi
if [ -z "${OPENDUO_APT_URL:-}" ]; then
    owner=$($g config user.name || true)
    [ -n "$owner" ] || { echo "build-release: set OPENDUO_APT_URL (no git user.name to derive it from)" >&2; exit 2; }
    OPENDUO_APT_URL=https://$(printf '%s' "$owner" | tr '[:upper:]' '[:lower:]').github.io/OpenDuo/apt
fi
case $OPENDUO_APT_URL in https://*) ;; *) echo "build-release: OPENDUO_APT_URL must be https://" >&2; exit 2 ;; esac
[ -f "$KROOT/$KOUT/include/config/kernel.release" ] ||
    { echo "build-release: no kernel build at $KROOT/$KOUT (KROOT, KOUT)" >&2; exit 1; }
kver=$(cat "$KROOT/$KOUT/include/config/kernel.release")
echo "== $name (kernel $kver, updates from $OPENDUO_APT_URL)"
echo "   disk: $(df -h "$O" | awk 'NR == 2 {print $4 " free"}'), memory: $(free -g | awk '/^Mem/ {print $7 " GiB available"}')"

export OPENDUO_RELEASE=1 OPENDUO_APT_URL
if [ "${SKIP_BUILD:-}" != 1 ]; then
    echo "== packages"
    "$R/packaging/build-packages.sh"

    echo "== images (from the packages)"
    INSTALL_FROM=packages DEBS=$O/packages/debs KOUT=$KROOT/$KOUT ROOT_SIZE=${ROOT_SIZE:-auto} \
        ROOTFS_TAG=duo2-rootfs-release "$R/scripts/build-images.sh"
fi

echo "== checks"
# The repository's URL is public on purpose (the keyring package's sources).
python3 -I "$R/scripts/lib/release-audit.py" "$O/rootfs.tar" "$O/rootfs-esp.tar" \
    "$(sed -n 's/^DUO2_USER_DEFAULT=//p' "$R/scripts/lib/defaults.sh")" "${OPENDUO_APT_URL#https://}"

echo "== $REL"
rm -rf "$REL"
mkdir -p "$REL"
for p in rootfs:linux esp:esp; do
    IFS=: read -r what img <<<"$p"
    cp --reflink=auto "$O/images/$img.img" "$REL/$name-$what.img"
    cp --reflink=auto "$O/images/$img.simg" "$REL/$name-$what.simg"
    xz -T"$JOBS" -6 -k "$REL/$name-$what.img"
done
tar -xOf "$O/rootfs.tar" ./var/lib/dpkg/status 2>/dev/null >"$O/release-status" ||
    tar -xOf "$O/rootfs.tar" var/lib/dpkg/status >"$O/release-status"
awk '/^Package: /{p=$2} /^Version: /{v=$2} /^Status: install ok installed/{s=1} /^$/{if (s) print p, v; s=0}' \
    "$O/release-status" | sort >"$REL/$name.packages"
rm -f "$O/release-status"
# The installer goes with the images (it needs nothing else from the
# repository), and so does the helper that gives a Linux user USB access.
install -m 0755 "$R/scripts/install-openduo.sh" "$REL/install-openduo.sh"
install -m 0755 "$R/scripts/udev-rules.sh" "$REL/udev-rules.sh"
(cd "$REL" && sha256sum -- *.img *.img.xz *.simg *.packages install-openduo.sh udev-rules.sh >SHA256SUMS)
chmod -R go-w "$REL"
ls -la "$REL"
echo "== done: $REL ($(grep -c '^openduo-' "$REL/$name.packages") OpenDuo packages)"
