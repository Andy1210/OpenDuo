#!/bin/sh
# Put Nibfair's files in place, as root (install.sh runs it on the phone).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
install -D -m 0755 nibfair /usr/local/bin/nibfair
install -D -m 0644 org.duo2os.Nibfair.desktop /usr/local/share/applications/org.duo2os.Nibfair.desktop
install -D -m 0644 org.duo2os.Nibfair.svg \
    /usr/local/share/icons/hicolor/scalable/apps/org.duo2os.Nibfair.svg
# Nibfair replaced Pen Hopper (apps/pen-game, removed 2026-11-16): its files go.
# A player's own save (~/.config/pen-hopper) is left alone.
rm -rf /usr/local/lib/pen-hopper
rm -f /usr/local/bin/pen-hopper /usr/local/share/applications/org.duo2os.PenHopper.desktop \
    /usr/local/share/icons/hicolor/scalable/apps/org.duo2os.PenHopper.svg
command -v gtk-update-icon-cache >/dev/null &&
    gtk-update-icon-cache -q -t -f /usr/local/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null &&
    update-desktop-database -q /usr/local/share/applications 2>/dev/null || true
