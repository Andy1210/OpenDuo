#!/usr/bin/env bash
# Stage a kernel build for its package (packaging/build-packages.sh): the
# modules (modules_install, stripped, as scripts/build-images.sh does), the
# Image, the device trees and the config, from a build directory.
#
#   packaging/stage-kernel.sh KOUT DEST      (KOUT relative to KROOT, the checkout
#                                            the kernel was built in, default this
#                                            one; KSRC= its tree, default src/linux)
#
# The build is mounted at /work as scripts/build-kernel.sh had it (its
# Makefiles name that path), so a worktree passes KROOT= its main checkout.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
KOUT=${1:?usage: stage-kernel.sh KOUT DEST}
DEST=${2:?usage: stage-kernel.sh KOUT DEST}
KSRC=${KSRC:-src/linux}
KROOT=${KROOT:-$R}
K=$KROOT/$KOUT
[ -f "$K/arch/arm64/boot/Image" ] || { echo "no $K/arch/arm64/boot/Image (scripts/build-kernel.sh)" >&2; exit 1; }
rm -rf "$DEST"
mkdir -p "$DEST/boot/dts/qcom" "$DEST/lib"
D=$(cd "$DEST" && pwd)
# modules_install recomputes the release: the build's own suffix again
docker run --rm -u "$(id -u):$(id -g)" -v "$KROOT":/work -v "$D":/dest duo2-build bash -c \
    "cd /work/$KSRC && make -s ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- O=/work/$KOUT \
     LOCALVERSION=\$(cat /work/$KOUT/.duo2-ksuffix 2>/dev/null) INSTALL_MOD_PATH=/dest INSTALL_MOD_STRIP=1 modules_install"
rel=$(cat "$K/include/config/kernel.release")
[ -d "$D/lib/modules/$rel" ] || { echo "modules_install made no lib/modules/$rel (another suffix?)" >&2; exit 1; }
cp -a "$K/arch/arm64/boot/Image" "$D/boot/"
cp -a "$K"/arch/arm64/boot/dts/qcom/sm8350-microsoft-surface-duo2*.dtb "$D/boot/dts/qcom/"
cp -a "$K/.config" "$D/config"
du -sh "$D/lib/modules/$rel"
