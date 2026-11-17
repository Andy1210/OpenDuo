#!/usr/bin/env bash
# Build the duo2-os kernel in the duo2-build container.
#
#   scripts/build-kernel.sh [config|dtbs|all]
#
#   config  only (re)generate out/linux/.config: arm64 defconfig + the
#           kernel/config/duo2.config fragment, then olddefconfig
#   dtbs    config, then only the DTBs (BRING-UP variants included), for a
#           DT-only try (duo2-try --same-kernel --dtb NAME) from whatever
#           src/linux has checked out. The Image and the modules in out/linux
#           are not rebuilt, and the release string stays the one of that
#           Image (the suffix is read back from out/linux/.duo2-ksuffix), so a
#           later duo2-try of the Image still finds its modules.
#   all     (default) config, then Image, dtbs and modules
#
# Kernel tree: src/linux (branch duo2: the 7.3.y stable tag it is based on +
# kernel/patches, exported with git format-patch v7.3.4..duo2), build dir: out/linux
# (O=..., KBUILD_OUTPUT), O may be overridden for a scratch build, and SRC=
# (relative to the repository) builds another tree, e.g. a git worktree of
# src/linux: SRC=src/linux-voice O=out/linux-voice (duo2-try then takes it
# with DUO2_KSRC= and DUO2_KOUT=).
# JOBS defaults to 10 (the build machine's budget; adjust as needed).
#
# Every build gets its own release string, 7.3.4-duo2-g<commit>[-dirty]
# (LOCALVERSION, override with KSUFFIX=...), so its modules land in their own
# /lib/modules/<release> and a kernel under test never overwrites the modules
# of the known-good one (see duo2-try).
# EXTRA_CONFIG=path (relative to the repository) merges a second fragment after
# duo2.config, for a debug build of the same code (give it its own KSUFFIX,
# e.g. -g<commit>-dbg: the plain suffix is the known-good kernel's).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
WHAT=${1:-all}
O=${O:-out/linux}
SRC=${SRC:-src/linux}
JOBS=${JOBS:-10}
EXTRA_CONFIG=${EXTRA_CONFIG:-}
if [ -n "$EXTRA_CONFIG" ] && [ ! -f "$R/$EXTRA_CONFIG" ]; then
    echo "no $R/$EXTRA_CONFIG" >&2; exit 2
fi
if [ -z "${KSUFFIX:-}" ]; then
    KSUFFIX=-g$(git -C "$R/$SRC" rev-parse --short=9 HEAD)
    git -C "$R/$SRC" diff --quiet HEAD || KSUFFIX=$KSUFFIX-dirty
    if [ "$WHAT" = dtbs ] && [ -f "$R/$O/.duo2-ksuffix" ]; then
        KSUFFIX=$(cat "$R/$O/.duo2-ksuffix")
    fi
fi
case $WHAT in config|dtbs|all) ;; *) echo "usage: $0 [config|dtbs|all]" >&2; exit 2 ;; esac

docker run --rm -u "$(id -u):$(id -g)" -e HOME=/work/out/home -v "$R":/work duo2-build bash -c "
set -euo pipefail
cd /work/$SRC
M='make -s ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- O=/work/$O LOCALVERSION=$KSUFFIX'
mkdir -p /work/$O
# modules_install recomputes the release: later steps pass the same suffix
printf '%s\\n' '$KSUFFIX' > /work/$O/.duo2-ksuffix
\$M defconfig
ARCH=arm64 scripts/kconfig/merge_config.sh -m -O /work/$O /work/$O/.config /work/kernel/config/duo2.config ${EXTRA_CONFIG:+/work/$EXTRA_CONFIG} >/dev/null
\$M olddefconfig
# every fragment line must have survived (a typo or a missing dependency
# silently drops it)
grep -hE '^(CONFIG_|# CONFIG_)' /work/kernel/config/duo2.config ${EXTRA_CONFIG:+/work/$EXTRA_CONFIG} | while read -r l; do
    grep -qxF \"\$l\" /work/$O/.config || echo \"fragment line not in .config: \$l\"
done
[ '$WHAT' = config ] && exit 0
# The BRING-UP ONLY DTB variants are not in the dts Makefile (they never go
# upstream): build them by name.
EXTRA=\$(cd arch/arm64/boot/dts && ls qcom/sm8350-microsoft-surface-duo2-*.dts 2>/dev/null | sed 's/\\.dts\$/.dtb/' | tr '\\n' ' ')
if [ '$WHAT' = dtbs ]; then
    \$M -j$JOBS dtbs \$EXTRA
    echo \"dtbs from \$(git -c safe.directory=/work/$SRC rev-parse --short=9 HEAD) (release string kept: \$(cat /work/$O/include/config/kernel.release))\"
    exit 0
fi
\$M -j$JOBS Image dtbs modules \$EXTRA
echo \"kernel \$(cat /work/$O/include/config/kernel.release)\"
"
