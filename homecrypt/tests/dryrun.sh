#!/bin/sh
# The home's encryption end to end (tests/dryrun.py) with the kernel's fscrypt
# on a loop-mounted ext4, as root in a throwaway privileged debian:trixie
# container: the loop device is detached and the image deleted at the end.
#   homecrypt/tests/dryrun.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
top=$(dirname "$here")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
docker run --rm --privileged -v "$top":/src/homecrypt:ro -v "$work":/work debian:trixie sh -c '
apt-get update -qq >/dev/null &&
    apt-get install -y -qq --no-install-recommends python3 e2fsprogs rsync dconf-cli util-linux mount passwd >/dev/null &&
    exec python3 -B /src/homecrypt/tests/dryrun.py'
