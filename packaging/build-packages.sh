#!/usr/bin/env bash
# Build OpenDuo's Debian packages (docs/UPDATES.md): every component of
# packaging/packages.toml, openduo-release, openduo-archive-keyring, the
# kernel (when a kernel build is there) and the metapackages, into
# out/packages/debs/. Reproducible: the version and every file's time come
# from the commit (SOURCE_DATE_EPOCH), the archives from the builder
# container (packaging/Dockerfile).
#
#   packaging/build-packages.sh [--no-kernel] [--only PKG...]
#
# The built parts are taken as they are (scripts/lib/stage-components.sh says
# which and how to build them). From the environment or local.env:
#   OPENDUO_APT_URL     the repository's URL, for the keyring package's sources
#                       (without it the keyring package is not built)
#   OPENDUO_KEYRING     the public key for it (default packaging/keyring/; the
#                       package test passes its throwaway one)
#   OPENDUO_MAINTAINER  the packages' Maintainer field
#   KOUT, KSRC          the kernel build and its tree (default out/linux, src/linux),
#                       relative to KROOT (default this checkout; a worktree
#                       names its main checkout: packaging/stage-kernel.sh)
#   ALLOW_DIRTY=1       build from uncommitted changes (the version says .dirty)
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
# OPENDUO_RELEASE=1 (scripts/build-release.sh): only the environment counts
# (local.env may point the keyring at a test repository on the LAN).
if [ "${OPENDUO_RELEASE:-}" != 1 ] && [ -f "$R/local.env" ]; then
    # shellcheck source=/dev/null
    . "$R/local.env"
fi
O=$R/out/packages
KOUT=${KOUT:-out/linux}
KSRC=${KSRC:-src/linux}
KROOT=${KROOT:-$R}
kernel=1
only=()
while [ $# -gt 0 ]; do
    case $1 in
    --no-kernel) kernel=; shift ;;
    --only) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do only+=("$1"); shift; done ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done

g="git -C $R"
hash=$($g rev-parse --short=7 HEAD)
epoch=$($g log -1 --format=%ct HEAD)
dirty=
if [ -n "$($g status --porcelain --untracked-files=no)" ]; then
    [ "${ALLOW_DIRTY:-}" = 1 ] || { echo "uncommitted changes: commit first (or ALLOW_DIRTY=1)" >&2; exit 1; }
    dirty=1
fi
P=$R/packaging/pkgtool
version=$("$P" version "$R/VERSION" "$epoch" "$hash" ${dirty:+dirty})
echo "== OpenDuo $version"

image=openduo-pkg:trixie
docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$R/packaging" >/dev/null
run() { # the builder, the repository at /work, out/packages at /out
    docker run --rm -u "$(id -u):$(id -g)" -e SOURCE_DATE_EPOCH="$epoch" -e PYTHONDONTWRITEBYTECODE=1 \
        -e OPENDUO_MAINTAINER="${OPENDUO_MAINTAINER:-}" -e OPENDUO_HOMEPAGE="${OPENDUO_HOMEPAGE:-}" \
        -v "$R":/work:ro -v "$O":/out -w /work/packaging "$image" "$@"
}
rm -rf "$O/stage" "$O/debs" "$O/.work"
mkdir -p "$O/stage" "$O/debs"

echo "== stage"
comps=$("$P" components ${only[@]:+"${only[@]}"})
# shellcheck disable=SC2086 # the list is split on purpose
"$R/scripts/lib/stage-components.sh" "$O/stage" $comps
SOURCE_DATE_EPOCH=$epoch "$R/scripts/lib/release-stamp.sh" "$R" > "$O/stage/duo2-release"

echo "== components"
B=(python3 -I /work/packaging/pkgtool build)
run "${B[@]}" components --stage /out/stage --out /out/debs --work /out/.work --version "$version" \
    --epoch "$epoch" ${only[@]:+--only "${only[@]}"}
if [ ${#only[@]} -eq 0 ]; then
    run "${B[@]}" release --stamp /out/stage/duo2-release --changelog /work/CHANGELOG.md \
        --os-release /work/rootfs/install-os-release.sh --out /out/debs --work /out/.work \
        --version "$version" --epoch "$epoch"
    if [ -n "${OPENDUO_APT_URL:-}" ]; then
        key=${OPENDUO_KEYRING:-$R/packaging/keyring/openduo-archive-keyring.asc}
        cp "$key" "$O/stage/openduo-archive-keyring.asc"
        run "${B[@]}" keyring --key /out/stage/openduo-archive-keyring.asc --url "$OPENDUO_APT_URL" \
            --out /out/debs --work /out/.work --version "$version" --epoch "$epoch"
    else
        echo "no OPENDUO_APT_URL: openduo-archive-keyring not built" >&2
    fi
fi

krel="" kver=""
if [ -n "$kernel" ] && [ ${#only[@]} -eq 0 ] && [ -f "$KROOT/$KOUT/include/config/kernel.release" ]; then
    krel=$(cat "$KROOT/$KOUT/include/config/kernel.release")
    khash=$("$P" kernel-hash "$krel")
    kepoch=$(git -C "$KROOT/$KSRC" log -1 --format=%ct "$khash" 2>/dev/null || echo "$epoch")
    kver=$("$P" kernel-version "$krel" "$kepoch")
    echo "== kernel $krel ($kver)"
    KROOT=$KROOT KSRC=$KSRC "$R/packaging/stage-kernel.sh" "$KOUT" "$O/stage/kernel"
    run "${B[@]}" kernel --release "$krel" --kversion "$kver" --modules /out/stage/kernel/lib/modules \
        --boot /out/stage/kernel/boot --config /out/stage/kernel/config --out /out/debs --work /out/.work \
        --epoch "$kepoch"
fi
if [ ${#only[@]} -eq 0 ]; then
    run "${B[@]}" meta --out /out/debs --work /out/.work --version "$version" --epoch "$epoch" \
        ${krel:+--release "$krel" --kversion "$kver"}
fi
rm -rf "$O/.work"
ls -la "$O/debs"
