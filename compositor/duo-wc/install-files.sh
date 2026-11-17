#!/bin/sh
# Put duo-wc's files in place, as root: in the image (rootfs/install-components.sh
# under qemu) and on the phone from its package (packaging/, docs/UPDATES.md):
# one list for both. install.sh (the network installer from the build machine)
# does the same by hand, plus the restart.
#
#   install-files.sh USER     (run from the staged directory:
#                             scripts/lib/stage-components.sh duo-wc)
#
#   /usr/local/bin/duo-wc, /usr/local/bin/duoctl
#   /usr/local/lib/duo-wc/{session,osk,hello}, tools/, xwayland/Xwayland
#   /etc/duo-wc/duo-wc.conf      only when absent (the owner's edits stay);
#                                the shipped one is always duo-wc.conf.dist
#   /etc/systemd/system/duo-wc.service (for USER)
#   /etc/systemd/logind.conf.d/50-duo-wc-power-key.conf
#
# Nothing is started or restarted here: a running compositor keeps the old
# binary until the next start (the package asks for a restart of the phone).
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
install -m 0755 duo-wc /usr/local/bin/duo-wc.new
mv -f /usr/local/bin/duo-wc.new /usr/local/bin/duo-wc
install -m 0755 duoctl /usr/local/bin/duoctl
install -d /usr/local/lib/duo-wc/tools /etc/duo-wc
install -m 0755 session osk hello /usr/local/lib/duo-wc/
install -m 0755 touch.py key.py toplevels.py /usr/local/lib/duo-wc/tools/
install -D -m 0755 xwayland-wrapper /usr/local/lib/duo-wc/xwayland/Xwayland
[ -e /etc/duo-wc/duo-wc.conf ] || install -m 0644 duo-wc.conf /etc/duo-wc/duo-wc.conf
install -m 0644 duo-wc.conf /etc/duo-wc/duo-wc.conf.dist
install -D -m 0644 logind-power-key.conf /etc/systemd/logind.conf.d/50-duo-wc-power-key.conf
sed "s/@USER@/$U/" duo-wc.service >/etc/systemd/system/duo-wc.service.new
chmod 0644 /etc/systemd/system/duo-wc.service.new
mv -f /etc/systemd/system/duo-wc.service.new /etc/systemd/system/duo-wc.service
# seatd hands duo-wc the panels and the input devices (the video group: the
# DRM nodes)
if getent group video >/dev/null && ! id -nG "$U" 2>/dev/null | tr ' ' '\n' | grep -qx video; then
    usermod -aG video "$U"
fi
