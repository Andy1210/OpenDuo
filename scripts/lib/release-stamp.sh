#!/bin/sh
# The OpenDuo build stamp (/etc/duo2-release), key=value lines on stdout:
#   release-stamp.sh [REPO]
# openduo = the VERSION file at the repository root (the one source of the
#           version: os-release, Settings > About, bug reports, OTA packages);
# build   = UTC date + git short hash, e.g. 20261116.71fc19a (a "-dirty"
#           suffix when tracked files have uncommitted changes);
# version = git describe of the repository (tag, commits since, commit).
set -eu
repo=${1:-$(cd "$(dirname "$0")/../.." && pwd)}
g="git -C $repo"
openduo=$(tr -d ' \t\r\n' <"$repo/VERSION" 2>/dev/null || true)
hash=$($g rev-parse --short=7 HEAD 2>/dev/null || echo unknown)
dirty=
[ -z "$($g status --porcelain --untracked-files=no 2>/dev/null || true)" ] || dirty=-dirty
# SOURCE_DATE_EPOCH (the packages: the commit's time) makes the stamp the
# same for every build of a commit.
d() { if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then date -u -d "@$SOURCE_DATE_EPOCH" "$@"; else date -u "$@"; fi; }
echo "openduo=${openduo:-unknown}"
echo "build=$(d +%Y%m%d).$hash$dirty"
echo "version=$($g describe --always --tags 2>/dev/null || echo unknown)"
echo "branch=$($g branch --show-current 2>/dev/null || true)"
echo "built=$(d +%Y-%m-%dT%H:%M:%SZ)"
