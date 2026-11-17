#!/bin/sh
# Run a widget test under GTK 4 (Debian trixie, as on the phone) with a
# broadway display, in a container (tests/gtk.Dockerfile):
#
#   tests/run-gtk.sh tests/gtk_quick_tiles.py [OUT_DIR]
#
# OUT_DIR (default /tmp/duo-shell-gtk) gets the pictures the test saves.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
top=$(dirname "$here")
out=${2:-/tmp/duo-shell-gtk}
mkdir -p "$out"
docker build -q -t duo-shell-gtk -f "$here/gtk.Dockerfile" "$here" >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e OUT=/out -v "$top":/src:ro -v "$out":/out \
    duo-shell-gtk sh -c '
mkdir -p /tmp/rt && chmod 700 /tmp/rt
XDG_RUNTIME_DIR=/tmp/rt gtk4-broadwayd :5 >/dev/null 2>&1 &
sleep 1
export GDK_BACKEND=broadway BROADWAY_DISPLAY=:5 XDG_RUNTIME_DIR=/tmp/rt PYTHONDONTWRITEBYTECODE=1
cd /src && exec python3 -I '"$1"
