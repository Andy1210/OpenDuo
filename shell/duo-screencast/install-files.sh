#!/bin/sh
# Put duo-screencast's files in place, as root, on the phone (install.sh runs
# it there) or in the image (rootfs/install-components.sh runs it under
# qemu): one list for both. docs/SCREENCAST.md.
#
#   install-files.sh          (run from this directory)
#
# The packages come first (install.sh, rootfs/Dockerfile):
# xdg-desktop-portal-wlr, grim. The portal backend is chosen in
# shell/duo-shell/portals.conf; duo-wc's session starts it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo-screencast

install -d -m 0755 "$L" "$L/bin" "$L/duoscreencast"
install -m 0755 portal chooser notify "$L/"
install -m 0644 xdpw.conf "$L/xdpw.conf"
install -m 0644 duoscreencast/*.py "$L/duoscreencast/"
# The screenshot gate: first in the portal's PATH, under grim's own name.
install -m 0755 grim "$L/bin/grim"
install -m 0755 duo-screencast-test /usr/local/bin/duo-screencast-test

# Started by D-Bus activation the backend would have no capture connection
# (only duo-wc's session can give it one): its user unit is masked, the
# session keeps it running instead.
install -d /etc/systemd/user
ln -sfn /dev/null /etc/systemd/user/xdg-desktop-portal-wlr.service
