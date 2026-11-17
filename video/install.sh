#!/bin/bash
# The browser video pieces on the phone over the network
# (scripts/lib/remote.sh): install-files.sh there, as root.
#
#   install.sh              REMOVE the YouTube launcher (the default since
#                           2026-11-10: not installed), its profile included
#   install.sh --launcher   install it (docs/hardware/video.md)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
arg=
case "${1:-}" in
"") ;;
--launcher) arg=--launcher ;;
*) echo "usage: $0 [--launcher]" >&2; exit 2 ;;
esac
# Staged in the administrator's home on the phone (duo2_stage), never in /tmp.
stage=$(duo2_stage video)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
tar -C "$here" --exclude=__pycache__ --exclude=web --mode=go-w -cf - . | duo2_ssh "tar -C $stage --no-same-owner -xf -"
duo2_ssh "set -eu
sudo sh $stage/install-files.sh $arg
rm -rf $stage"
if [ -z "$arg" ]; then
    # the launcher's own Chromium profile (not while it runs)
    # shellcheck disable=SC2016 # $HOME is the GUI user's, on the phone
    duo2_user 'p=$HOME/.config/duo2-youtube
if [ -L "$p/SingletonLock" ]; then echo "the launcher is running: its profile is kept" >&2; exit 1; fi
rm -rf "$p"'
fi
