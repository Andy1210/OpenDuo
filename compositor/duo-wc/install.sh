#!/bin/bash
# Install duo-wc on the phone over the network (scripts/lib/remote.sh).
#
#   ./install.sh            copy the binary, unit, session and tools; restart
#                           the service if it is running
#   ./install.sh --start    ... and start it (takes the panels from the console)
#   ./install.sh --enable   ... and start it at every boot too
#
# Build first: ./build-aarch64.sh (out/duo-wc).
#
# A freshly built image already has all of this (rootfs/install-components.sh,
# from scripts/build-images.sh): this script updates a running phone. A file
# added here belongs there too.
#
# On the phone:
#   /usr/local/bin/duo-wc, /usr/local/bin/duoctl
#   /usr/local/lib/duo-wc/duo-wc.last-good   (the binary this install replaced,
#                                    when it was running: the way back)
#   /usr/local/lib/duo-wc/{session,osk,hello}   (osk: starts the on-screen keyboard)
#   /usr/local/lib/duo-wc/tools/{touch.py,key.py,toplevels.py}   (testing without fingers)
#   /usr/local/lib/duo-wc/xwayland/Xwayland   (the wrapper duo-wc starts Xwayland
#                                    through: src/xwayland.rs)
#   /etc/duo-wc/duo-wc.conf          (only when absent: the owner's edits stay;
#                                     the shipped one is always duo-wc.conf.dist)
#   /etc/systemd/system/duo-wc.service
#   /etc/systemd/logind.conf.d/50-duo-wc-power-key.conf
#                                    (logind ignores the power key: duo-wc owns it;
#                                     logind is reloaded when this changes)
#
# Runtime packages: foot seatd libseat1 libdisplay-info2 xwayland (plus what the base image has:
# libinput10 libgbm1 libegl1 libgles2 libwayland-server0 libxkbcommon0).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"

action=
case "${1:-}" in
"") ;;
--start | --enable) action=$1 ;;
*)
    echo "usage: $0 [--start|--enable]" >&2
    exit 2
    ;;
esac

[ -x "$here/out/duo-wc" ] || {
    echo "no out/duo-wc: run ./build-aarch64.sh first" >&2
    exit 1
}

stage=$(duo2_stage duo-wc)
duo2_ssh "rm -rf $stage && mkdir -p $stage"
duo2_scp "$here/out/duo-wc" "$here/dist/duoctl" "$here/dist/session" "$here/dist/osk" "$here/dist/hello" \
    "$here/dist/duo-wc.conf" "$here/dist/duo-wc.service" "$here/dist/logind-power-key.conf" \
    "$here/dist/xwayland-wrapper" \
    "$here/tools/touch.py" "$here/tools/key.py" "$here/tools/toplevels.py" \
    "$DUO2_HOST:$stage/"

duo2_ssh "set -eu
missing=
for p in foot seatd libseat1 libdisplay-info2 xwayland; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \$missing
fi
cd $stage
# The binary being replaced, if it is running and differs, is the way back:
# /usr/local/lib/duo-wc/duo-wc.last-good (sudo install -m 0755 it over
# /usr/local/bin/duo-wc and restart the service).
sudo install -d /usr/local/lib/duo-wc
if [ -x /usr/local/bin/duo-wc ] && systemctl is-active -q duo-wc.service &&
    ! cmp -s duo-wc /usr/local/bin/duo-wc; then
    sudo cp -p /usr/local/bin/duo-wc /usr/local/lib/duo-wc/duo-wc.last-good
fi
sudo install -m 0755 duo-wc /usr/local/bin/duo-wc.new
sudo mv -f /usr/local/bin/duo-wc.new /usr/local/bin/duo-wc
sudo install -m 0755 duoctl /usr/local/bin/duoctl
sudo install -d /usr/local/lib/duo-wc /etc/duo-wc
sudo install -m 0755 session osk hello /usr/local/lib/duo-wc/
sudo install -d /usr/local/lib/duo-wc/tools
sudo install -m 0755 touch.py key.py toplevels.py /usr/local/lib/duo-wc/tools/
sudo install -D -m 0755 xwayland-wrapper /usr/local/lib/duo-wc/xwayland/Xwayland
[ -e /etc/duo-wc/duo-wc.conf ] || sudo install -m 0644 duo-wc.conf /etc/duo-wc/duo-wc.conf
sudo install -m 0644 duo-wc.conf /etc/duo-wc/duo-wc.conf.dist
logind=/etc/systemd/logind.conf.d/50-duo-wc-power-key.conf
if ! cmp -s logind-power-key.conf \$logind; then
    sudo install -d /etc/systemd/logind.conf.d
    sudo install -m 0644 logind-power-key.conf \$logind
    # Type=notify-reload: logind re-reads its config, sessions stay.
    sudo systemctl reload systemd-logind.service
fi
sed \"s/@USER@/$DUO2_USER/\" duo-wc.service | sudo tee /etc/systemd/system/duo-wc.service >/dev/null
sudo systemctl daemon-reload
id -nG $DUO2_USER | grep -qw video || sudo usermod -aG video $DUO2_USER
sudo systemctl enable --now seatd.service
case '$action' in
--enable) sudo systemctl enable duo-wc.service ;;
esac
if [ '$action' != '' ] || systemctl is-active -q duo-wc.service; then
    sudo systemctl restart duo-wc.service
fi
rm -rf $stage
/usr/local/bin/duo-wc --version
systemctl is-active duo-wc.service || true"
