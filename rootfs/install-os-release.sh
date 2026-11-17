#!/bin/sh
# Write /etc/os-release for OpenDuo from the build stamp, as root, on the
# phone or in the image:  install-os-release.sh [STAMP]  (default
# /etc/duo2-release, scripts/lib/release-stamp.sh).
#
# Debian's /etc/os-release is a symlink to /usr/lib/os-release that base-files
# owns. /usr/lib/os-release stays Debian's (what ships there is the vendor's
# default, and tools that fall back to it keep working); /etc/os-release,
# which takes priority, is a regular file of ours. dpkg-divert (no rename:
# the file is Essential) sends base-files' own copy to /etc/os-release.debian
# on an upgrade, so the symlink does not come back over ours. ID=openduo with
# ID_LIKE=debian is what the spec means for a derivative; apt and dpkg do not
# read os-release (their vendor is /etc/dpkg/origins/default, still Debian).
set -eu
stamp=${1:-/etc/duo2-release}
val() { sed -n "s/^$1=//p" "$stamp" | head -n 1; }
version=$(val openduo)
build=$(val build)
[ -n "$version" ] && [ "$version" != unknown ] || { echo "install-os-release: no openduo version in $stamp" >&2; exit 1; }
codename=trixie
full=$(cat /etc/debian_version 2>/dev/null || echo 13)
if ! dpkg-divert --list /etc/os-release | grep -q .; then
    [ ! -L /etc/os-release ] || mv -f /etc/os-release /etc/os-release.debian
    dpkg-divert --local --no-rename --divert /etc/os-release.debian --add /etc/os-release >/dev/null
fi
tmp=/etc/.os-release.new
cat >"$tmp" <<OSR
NAME="OpenDuo"
ID=openduo
ID_LIKE=debian
VERSION_ID="$version"
VERSION="$version"
BUILD_ID="$build"
PRETTY_NAME="OpenDuo $version (Debian 13 based)"
VARIANT="Surface Duo 2"
VARIANT_ID=surface-duo2
VERSION_CODENAME=$codename
DEBIAN_VERSION_FULL=$full
HOME_URL=""
OSR
chmod 0644 "$tmp"
mv -f "$tmp" /etc/os-release
