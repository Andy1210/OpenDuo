#!/usr/bin/env bash
# The package test (docs/UPDATES.md "Tests"): this commit's packages and a
# next version of them, in a signed repository (stable and testing), installed
# into a Debian trixie arm64 root under qemu, then upgraded:
#
#   packaging/tests/container-test.sh
#
#   1. this commit's packages, with the kernel build in KOUT (default
#      out/linux), its keyring pointing at file:/repo;
#   2. the next version: a scratch worktree of this commit with one more
#      commit (a changed firewall comment, a CHANGELOG line), and a second
#      kernel package made from the same build under another release name;
#   3. both into a test repository signed with a throwaway key (never the
#      archive key): this version in stable, the next one in testing;
#   4. packaging/tests/inside.sh in openduo-test-base:trixie
#      (packaging/tests/Dockerfile.base): install from stable, look at the
#      files, the units, the kernel's entry; testing channel, upgrade, look
#      again (the owner's settings kept, the previous kernel kept, a bad one
#      marked and removed).
#
# Needs the built parts (scripts/lib/stage-components.sh), a kernel build
# (KROOT, KOUT: packaging/build-packages.sh) and a clean tree.
# Writes only out/package-test/.
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
T=$R/out/package-test
KOUT=${KOUT:-out/linux}
KROOT=${KROOT:-$R}
base=openduo-test-base:trixie
docker image inspect "$base" >/dev/null 2>&1 ||
    docker build --platform linux/arm64 --build-arg DEPENDS="$("$R/packaging/pkgtool" depends)" \
        -t "$base" -f "$R/packaging/tests/Dockerfile.base" "$R/packaging/tests"
[ -z "$(git -C "$R" status --porcelain --untracked-files=no)" ] || { echo "commit first" >&2; exit 1; }
rm -rf "$T"
mkdir -p "$T"

echo "== a throwaway signing key"
KEY_NAME="OpenDuo package test key (throwaway)" PUBLIC_DIR="$T/pub" "$R/packaging/signing/new-key.sh" "$T/key" >/dev/null
export OPENDUO_KEYRING=$T/pub/openduo-archive-keyring.asc GNUPGHOME=$T/key/gnupg OPENDUO_APT_URL=file:/repo

echo "== this version"
KOUT=$KOUT KROOT=$KROOT "$R/packaging/build-packages.sh" | tail -n 2
mkdir -p "$T/this"
cp -a "$R/out/packages/debs/." "$T/this/"

echo "== the next version (a scratch worktree, one more commit)"
N=$T/next
git -C "$R" worktree add -q --detach "$N" HEAD
trap 'git -C "$R" worktree remove --force "$N" 2>/dev/null || true' EXIT
for p in compositor/duo-wc/out apps/nibfair/out; do
    mkdir -p "$N/$p"
    cp -a "$R/$p/." "$N/$p/"
done
mkdir -p "$N/out"
for p in duo2-sensors-bin lpac touchd; do
    [ ! -e "$R/out/$p" ] || cp -a "$R/out/$p" "$N/out/"
done
echo "# container-test: the next version" >>"$N/firewall/firewall.nft"
sed -i 's/^## \[Unreleased\]$/## [Unreleased]\n\n### Package test\n- the next version (packaging\/tests\/container-test.sh)/' "$N/CHANGELOG.md"
git -C "$N" -c user.name=package-test -c user.email=test@example.org commit -q -a -m "package test: the next version"
"$N/packaging/build-packages.sh" --no-kernel | tail -n 2
mkdir -p "$T/next-debs"
cp -a "$N/out/packages/debs/." "$T/next-debs/"
krel=$(cat "$KROOT/$KOUT/include/config/kernel.release")
base_rel=${krel%-g*}
fake=$base_rel-gfeedc0ffe
echo "== a second kernel package: $krel's build as $fake"
S=$T/kstage
mkdir -p "$S/modules"
cp -a "$R/out/packages/stage/kernel/lib/modules/$krel" "$S/modules/$fake"
cp -a "$R/out/packages/stage/kernel/boot" "$S/boot"
cp -a "$R/out/packages/stage/kernel/config" "$S/config"
epoch=$(git -C "$N" log -1 --format=%ct)
kver=$("$R/packaging/pkgtool" kernel-version "$fake" "$epoch")
version=$("$R/packaging/pkgtool" version "$N/VERSION" "$epoch" "$(git -C "$N" rev-parse --short=7 HEAD)")
docker run --rm -u "$(id -u):$(id -g)" -e PYTHONDONTWRITEBYTECODE=1 -v "$R":/work:ro -v "$T":/t openduo-pkg:trixie \
    bash -euc "python3 -I /work/packaging/pkgtool build kernel --release $fake --kversion $kver --modules /t/kstage/modules \
        --boot /t/kstage/boot --config /t/kstage/config --out /t/next-debs --work /t/.work --epoch $epoch >/dev/null
    python3 -I /work/packaging/pkgtool build meta --release $fake --kversion $kver --version $version \
        --out /t/next-debs --work /t/.work --epoch $epoch >/dev/null
    rm -rf /t/.work"

echo "== the test repository: this version in stable, the next one in testing"
"$R/packaging/repo.sh" "$T/repo" add stable "$T"/this/*.deb >/dev/null
"$R/packaging/repo.sh" "$T/repo" add testing "$T"/next-debs/*.deb >/dev/null
"$R/packaging/repo.sh" "$T/repo" list stable | wc -l | sed 's/^/stable: packages /'
"$R/packaging/repo.sh" "$T/repo" list testing | wc -l | sed 's/^/testing: packages /'

echo "== install and upgrade in a trixie arm64 root (qemu)"
docker run --rm --platform linux/arm64 -v "$T/repo":/repo:ro -v "$R/packaging/tests":/t:ro \
    -v "$R/packaging/packages.toml":/manifest:ro "$base" bash /t/inside.sh all 2>&1 | tee "$T/inside.log"
grep -q '^== 0 failure(s)$' "$T/inside.log"
