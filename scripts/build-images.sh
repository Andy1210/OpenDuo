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
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$R/local.env" ] && . "$R/local.env"
# shellcheck source=lib/defaults.sh
. "$R/scripts/lib/defaults.sh"
DUO2_USER=${DUO2_USER:-$DUO2_USER_DEFAULT}
DUO2_DEVELOPER_MODE=${DUO2_DEVELOPER_MODE:-off}
case $DUO2_DEVELOPER_MODE in on | off) ;; *) echo "DUO2_DEVELOPER_MODE: on or off" >&2; exit 2 ;; esac
O=$R/out
IMG=$O/images
ROOT_SIZE=${ROOT_SIZE:-3G}
if [ "${ROOTFS_ONLY:-}" = 1 ]; then
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
cp -a "$R/firmware/linux" "$CTX/firmware"

# duo2-os's own components, built here (not on the phone) and installed by
# rootfs/install-components.sh: duo-wc (compositor/duo-wc/build-aarch64.sh),
# the free half of the sensor stack (scripts/build-sensors.sh, arm64 under
# qemu, ~10 min); built when missing, otherwise taken as they are (rebuild
# them first after a source change).
[ -x "$R/compositor/duo-wc/out/duo-wc" ] || JOBS=${JOBS:-10} "$R/compositor/duo-wc/build-aarch64.sh"
[ -x "$O/duo2-sensors-bin/usr/local/bin/hexagonrpcd" ] || "$R/scripts/build-sensors.sh"
[ -x "$O/lpac/bin/lpac" ] || "$R/telephony/lpac/build.sh"
# The camera stack's packages (camera/build-debs.sh: libcamera 0.7.2 with the
# sensors' helpers and tuning, pipewire rebuilt against it), installed by the
# Dockerfile over trixie's.
ls "$O"/camera-debs/libcamera0.7_*.deb >/dev/null 2>&1 || "$R/camera/build-debs.sh"
mkdir -p "$CTX/camera-debs"
find "$O/camera-debs" -name "*.deb" ! -name "*-dbgsym_*" -exec cp -a {} "$CTX/camera-debs/" +
# The components as their install-files.sh read them (the same staging as
# the packages': scripts/lib/stage-components.sh).
"$R/scripts/lib/stage-components.sh" "$CTX/components"
cp -a "$R/rootfs/install-components.sh" "$R/rootfs/install-os-release.sh" "$CTX/"
# The build stamp (/etc/duo2-release): the OpenDuo version from VERSION + the build id;
# Settings > About reads it and the image's /etc/os-release is written from it.
"$R/scripts/lib/release-stamp.sh" "$R" > "$CTX/duo2-release"
# Git keeps only the executable bit: the files' other mode bits come from the
# checkout's umask (0002 here: group-writable root files in /etc). The
# image's modes are the repository's, not the build host's.
chmod -R go-w "$CTX"
docker build -q --platform linux/arm64 --build-arg DUO2_USER="$DUO2_USER" \
    --build-arg DEVELOPER_MODE="$DUO2_DEVELOPER_MODE" -t "${ROOTFS_TAG:-duo2-rootfs}" "$CTX"
[ "${ROOTFS_ONLY:-}" = 1 ] && { echo "== ${ROOTFS_TAG:-duo2-rootfs} built (ROOTFS_ONLY)"; exit 0; }
CID=$(docker create --platform linux/arm64 duo2-rootfs /bin/true)
docker export "$CID" | python3 -I "$R/scripts/filter-export.py" "$O/rootfs.tar"
docker rm "$CID" >/dev/null
ls -la "$O/rootfs.tar"

echo "== ext4 image"
docker run --rm --platform linux/amd64 -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 -e DEBIAN_FRONTEND=noninteractive -v "$O":/out -v "$O/linux/arch/arm64/boot":/kboot:ro debian:trixie bash -c "
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

# The ESP the phone has: rescue initramfs, duo2.conf + rescue.conf, no default.
"$R/scripts/build-esp.sh"
ls -la "$IMG"
