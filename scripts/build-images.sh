#!/usr/bin/env bash
# Build the flashable images from the kernel build in out/linux:
#   out/images/linux.simg  ext4 root (GPT name "linux"), grows to the partition at first boot
#   out/images/esp.simg    FAT32 ESP: systemd-boot + kernel Image + DTBs + rescue
#                          initramfs (scripts/build-esp.sh)
# Both are Android sparse images for `fastboot flash <name> <file>`.
#
# ROOTFS_ONLY=1: only the root file system's Docker image (duo2-rootfs), for
# comparing the recipe with a phone (scripts/rootfs-compare.sh): no kernel
# modules (the kernel tree is not touched), no images.
#
# From the environment or local.env: DUO2_USER, the GUI user's name (default:
# scripts/lib/defaults.sh); DUO2_DEVELOPER_MODE=on, an image for a developer's
# own phone (Settings > Developer on: sshd enabled, the USB network up;
# default off);
# AUTHORIZED_KEYS, a public key file that may log in once ssh is on (default:
# ~/.ssh/id_ed25519.pub if there is one, else none: add keys in Settings).
#
# INSTALL_FROM=packages DEBS=DIR KOUT=DIR (scripts/build-release.sh): OpenDuo
# from its own packages (DIR: packaging/build-packages.sh's out/packages/debs,
# the kernel's included), so dpkg owns every file and updates work from the
# first boot; no firmware in the image (duo2-stock-import, stock/, brings the
# phone's own at its first boot); the kernel package's ESP files and blessed
# entry go into the ESP. KOUT is the kernel build the packages were made from
# (for the ESP's duo2.conf and rescue entries). The default, INSTALL_FROM=
# components, is the developer's image: the components' installers, the
# modules from out/linux and firmware/linux.
#
# OPENDUO_RELEASE=1 (scripts/build-release.sh): an image to publish. local.env
# is not read (it holds a developer's names and choices), and the image gets
# the default user, no ssh key and developer mode off whatever the
# environment says.
#
# ROOT_SIZE: the root image's size (default 3G; "auto": the root file
# system's size plus a quarter and 768 MiB, it grows to its partition at the
# first boot anyway).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
if [ "${OPENDUO_RELEASE:-}" = 1 ]; then
    unset DUO2_USER
    DUO2_DEVELOPER_MODE=off
    AUTHORIZED_KEYS=/dev/null
elif [ -f "$R/local.env" ]; then
    # shellcheck source=/dev/null
    . "$R/local.env"
fi
# shellcheck source=lib/defaults.sh
. "$R/scripts/lib/defaults.sh"
DUO2_USER=${DUO2_USER:-$DUO2_USER_DEFAULT}
DUO2_DEVELOPER_MODE=${DUO2_DEVELOPER_MODE:-off}
case $DUO2_DEVELOPER_MODE in on | off) ;; *) echo "DUO2_DEVELOPER_MODE: on or off" >&2; exit 2 ;; esac
O=$R/out
IMG=$O/images
ROOT_SIZE=${ROOT_SIZE:-3G}
INSTALL_FROM=${INSTALL_FROM:-components}
TAG=${ROOTFS_TAG:-duo2-rootfs}
case $INSTALL_FROM in components | packages) ;; *) echo "INSTALL_FROM: components or packages" >&2; exit 2 ;; esac
if [ "$INSTALL_FROM" = packages ]; then
    : "${DEBS:?INSTALL_FROM=packages needs DEBS (the directory with the packages)}"
    KOUT=$(cd "${KOUT:-$O/linux}" && pwd)
    KVER=$(cat "$KOUT/include/config/kernel.release")
    ls "$DEBS"/openduo-surface-duo2_*.deb "$DEBS"/openduo-kernel-surface-duo2-"$KVER"_*.deb >/dev/null ||
        { echo "$DEBS lacks the metapackage or the kernel $KVER" >&2; exit 1; }
    MODS=$O/release-no-modules
    rm -rf "$MODS"; mkdir -p "$MODS/lib/modules" "$IMG"
    echo "== kernel $KVER (its package)"
elif [ "${ROOTFS_ONLY:-}" = 1 ]; then
    MODS=$O/rootfs-only-modules
    rm -rf "$MODS"; mkdir -p "$MODS/lib/modules"
else
KVER=$(cat "$O/linux/include/config/kernel.release")
MODS=$O/modroot
mkdir -p "$IMG"
echo "== kernel $KVER"

echo "== modules"
rm -rf "$O/modroot"
# modules_install recomputes the release: the build's suffix again (read in
# the container, where the repository is /work).
docker run --rm -u "$(id -u):$(id -g)" -v "$R":/work duo2-build bash -c \
    'cd /work/src/linux && make -s ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- O=/work/out/linux \
     LOCALVERSION=$(cat /work/out/linux/.duo2-ksuffix 2>/dev/null) INSTALL_MOD_PATH=/work/out/modroot INSTALL_MOD_STRIP=1 modules_install'
rm -f "$O/modroot/lib/modules/$KVER/build" "$O/modroot/lib/modules/$KVER/source"
du -sh "$O/modroot/lib/modules/$KVER"
fi

echo "== rootfs (arm64 under qemu)"
CTX=$O/rootfs-ctx
rm -rf "$CTX"; mkdir -p "$CTX"
cp -a "$R/rootfs/Dockerfile" "$R/rootfs/overlay" "$CTX/"
# The SSH keys that may log in (once Developer mode switches ssh on): a build
# input, never committed; none is fine (Settings > Developer adds keys).
AUTHORIZED_KEYS=${AUTHORIZED_KEYS:-$HOME/.ssh/id_ed25519.pub}
if [ -r "$AUTHORIZED_KEYS" ]; then
    cp "$AUTHORIZED_KEYS" "$CTX/authorized_keys"
else
    echo "no AUTHORIZED_KEYS ($AUTHORIZED_KEYS): the image has no ssh key" >&2
    : >"$CTX/authorized_keys"
fi
cp -a "$MODS/lib/modules" "$CTX/modules"
mkdir -p "$CTX/debs" "$CTX/esp-entries" "$CTX/components"
if [ "$INSTALL_FROM" = packages ]; then
    # No firmware: it is never in a published image (duo2-stock-import).
    mkdir -p "$CTX/firmware"
    cp -a "$DEBS"/*.deb "$CTX/debs/"
    "$R/scripts/lib/esp-entries.sh" "$CTX/esp-entries" "$KVER"
else
    cp -a "$R/firmware/linux" "$CTX/firmware"
fi

# duo2-os's own components, built here (not on the phone) and installed by
# rootfs/install-components.sh: duo-wc (compositor/duo-wc/build-aarch64.sh),
# the free half of the sensor stack (scripts/build-sensors.sh, arm64 under
# qemu, ~10 min); built when missing, otherwise taken as they are (rebuild
# them first after a source change).
if [ "$INSTALL_FROM" = components ]; then
    [ -x "$R/compositor/duo-wc/out/duo-wc" ] || JOBS=${JOBS:-10} "$R/compositor/duo-wc/build-aarch64.sh"
    [ -x "$O/duo2-sensors-bin/usr/local/bin/hexagonrpcd" ] || "$R/scripts/build-sensors.sh"
    [ -x "$O/lpac/bin/lpac" ] || "$R/telephony/lpac/build.sh"
fi
# The camera stack's packages (camera/build-debs.sh: libcamera 0.7.2 with the
# sensors' helpers and tuning, pipewire rebuilt against it), installed by the
# Dockerfile over trixie's.
ls "$O"/camera-debs/libcamera0.7_*.deb >/dev/null 2>&1 || "$R/camera/build-debs.sh"
mkdir -p "$CTX/camera-debs"
find "$O/camera-debs" -name "*.deb" ! -name "*-dbgsym_*" -exec cp -a -t "$CTX/camera-debs/" {} +
# The components as their install-files.sh read them (the same staging as
# the packages': scripts/lib/stage-components.sh).
[ "$INSTALL_FROM" = packages ] || "$R/scripts/lib/stage-components.sh" "$CTX/components"
cp -a "$R/rootfs/install-components.sh" "$R/rootfs/install-os-release.sh" "$CTX/"
# The build stamp (/etc/duo2-release): the OpenDuo version from VERSION + the build id;
# Settings > About reads it and the image's /etc/os-release is written from it.
"$R/scripts/lib/release-stamp.sh" "$R" > "$CTX/duo2-release"
# Git keeps only the executable bit: the files' other mode bits come from the
# checkout's umask (0002 here: group-writable root files in /etc). The
# image's modes are the repository's, not the build host's.
chmod -R go-w "$CTX"
docker build -q --platform linux/arm64 --build-arg DUO2_USER="$DUO2_USER" \
    --build-arg DEVELOPER_MODE="$DUO2_DEVELOPER_MODE" --build-arg INSTALL_FROM="$INSTALL_FROM" -t "$TAG" "$CTX"
[ "${ROOTFS_ONLY:-}" = 1 ] && { echo "== $TAG built (ROOTFS_ONLY)"; exit 0; }
CID=$(docker create --platform linux/arm64 "$TAG" /bin/true)
rm -f "$O/rootfs-esp.tar"
if [ "$INSTALL_FROM" = packages ]; then
    docker export "$CID" | python3 -I "$R/scripts/filter-export.py" "$O/rootfs.tar" --esp "$O/rootfs-esp.tar"
else
    docker export "$CID" | python3 -I "$R/scripts/filter-export.py" "$O/rootfs.tar"
fi
docker rm "$CID" >/dev/null
ls -la "$O/rootfs.tar"

if [ "$ROOT_SIZE" = auto ]; then
    t=$(stat -c %s "$O/rootfs.tar")
    mib=$(( (t * 5 / 4 + (768 << 20)) >> 20 ))
    ROOT_SIZE=$(( (mib + 255) / 256 * 256 ))M
fi
echo "== ext4 image ($ROOT_SIZE)"
docker run --rm --platform linux/amd64 -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 -e DEBIAN_FRONTEND=noninteractive -v "$O":/out debian:trixie bash -c "
set -e
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends e2fsprogs libarchive13 android-sdk-libsparse-utils >/dev/null
cd /out
rm -f images/linux.img
# encrypt, stable_inodes: the home's optional encryption (homecrypt/README.md:
# fscrypt, the IV_INO_LBLK_64 policy the inline crypto engine wants).
mke2fs -q -t ext4 -L linux -O encrypt,stable_inodes -d rootfs.tar images/linux.img $ROOT_SIZE
e2fsck -fn images/linux.img >/dev/null && echo 'ext4: fsck clean'

img2simg images/linux.img images/linux.simg
chown -R $(id -u):$(id -g) /out/images
"

# The ESP the phone has: rescue initramfs, duo2.conf + rescue.conf, no default
# (from the packages: also the kernel package's files and its blessed entry).
if [ "$INSTALL_FROM" = packages ]; then
    KOUT=$KOUT ESP_EXTRA_TAR=$O/rootfs-esp.tar "$R/scripts/build-esp.sh"
else
    "$R/scripts/build-esp.sh"
fi
ls -la "$IMG"
