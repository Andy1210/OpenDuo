#!/usr/bin/env bash
# OpenDuo's apt repository: a static tree (reprepro) for GitHub Pages or any
# web server, signed with the OpenDuo key (docs/UPDATES.md "The repository").
#
#   packaging/repo.sh BASE add stable|testing DEB...   add packages, sign, export
#   packaging/repo.sh BASE promote                     testing's packages into stable
#   packaging/repo.sh BASE list [stable|testing]
#
# BASE is the repository's directory (conf/ and db/ are its state, dists/ and
# pool/ what apt reads; all of it may be published). The key: GNUPGHOME
# (default out/signing/gnupg, made by packaging/signing/new-key.sh; CI imports
# its secret OPENDUO_SIGNING_KEY into a fresh one). A .dirty version is never
# added. Runs in the builder container (packaging/Dockerfile).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
BASE=${1:?usage: repo.sh BASE add|promote|list ...}
what=${2:?usage: repo.sh BASE add|promote|list ...}
shift 2
GNUPGHOME=${GNUPGHOME:-$R/out/signing/gnupg}
mkdir -p "$BASE/conf"
B=$(cd "$BASE" && pwd)
image=openduo-pkg:trixie
docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$R/packaging" >/dev/null
run() {
    docker run --rm -u "$(id -u):$(id -g)" -e GNUPGHOME=/gnupg -v "$GNUPGHOME":/gnupg -v "$B":/repo \
        -v "$R/packaging":/packaging:ro "$@"
}
fpr=$(run "$image" gpg --batch --with-colons --list-secret-keys 2>/dev/null | awk -F: '/^fpr:/ {print $10; exit}')
[ -n "$fpr" ] || { echo "no secret key in $GNUPGHOME" >&2; exit 1; }
for suite in stable testing; do
    printf 'Origin: OpenDuo\nLabel: OpenDuo\nSuite: %s\nCodename: %s\nArchitectures: arm64\nComponents: main\nDescription: OpenDuo for the Surface Duo 2 (%s)\nSignWith: %s\n\n' \
        "$suite" "$suite" "$suite" "$fpr"
done >"$B/conf/distributions"
printf 'verbose\nbasedir /repo\n' >"$B/conf/options"
cp "${OPENDUO_KEYRING:-$R/packaging/keyring/openduo-archive-keyring.asc}" "$B/openduo-archive-keyring.asc"

case $what in
add)
    suite=${1:?usage: repo.sh BASE add stable|testing DEB...}
    shift
    case $suite in stable | testing) ;; *) echo "suite: stable or testing" >&2; exit 2 ;; esac
    [ $# -gt 0 ] || { echo "no packages" >&2; exit 2; }
    mkdir -p "$B/incoming"
    for deb in "$@"; do
        case $deb in *.dirty_*.deb | *-dbgsym_*.deb) echo "skipped: $deb"; continue ;; esac
        cp "$deb" "$B/incoming/"
    done
    # shellcheck disable=SC2016 # expanded by the container's shell
    run "$image" bash -euc '
cd /repo/incoming
for deb in *.deb; do
    [ -e "$deb" ] || continue
    reprepro -b /repo --silent includedeb "$0" "$deb"
done
reprepro -b /repo export "$0"' "$suite"
    rm -rf "$B/incoming"
    ;;
promote)
    run "$image" bash -euc 'reprepro -b /repo --silent copymatched stable testing "*"; reprepro -b /repo export stable'
    ;;
list)
    run "$image" reprepro -b /repo list "${1:-testing}"
    ;;
*)
    echo "usage: repo.sh BASE add|promote|list" >&2
    exit 2
    ;;
esac
