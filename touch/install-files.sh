#!/bin/sh
# Put OUR part of the touch stack in place, as root: on the phone from its
# package (packaging/, docs/UPDATES.md) and from scripts/install-touch-runtime.sh
# (the build machine): one list for both. The image has the same files from
# rootfs/overlay (their source; scripts/lib/stage-components.sh copies them
# into files/).
#
#   install-files.sh          (run from the staged directory:
#                             scripts/lib/stage-components.sh touch)
#
#   duo2-touchd.service, the udev rule, the system-sleep hook (the digitizer
#   off across a suspend), duo2-touch-calibration, the libinput pen quirk,
#   duo2-pend + its unit (the pen's Bluetooth side), the pen and touch tools
#   (/usr/local/lib/duo2-touch-tools); /etc/duo2/touch-calibration,
#   touch-mode and pen.conf only if the phone has none (they may hold this
#   unit's own refined numbers and choices).
#   duo2-touchd (built from touch/ with the NDK, touch/build-touchd.sh), when
#   staged: kept as /usr/local/lib/duo2-touch/duo2-touchd and put into the
#   runtime, /opt/duo2-touch, when the phone has one.
#
# The runtime itself (Microsoft's heat processor and the stock libraries it
# needs) is never installed here: it may not be redistributed, every owner
# builds it from their own phone (scripts/build-touch-runtime.sh, then
# scripts/install-touch-runtime.sh). Nothing is restarted here.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/files"
install -d /usr/local/lib/duo2-touch-tools
install -m 0755 ../tools/*.py ../tools/pen-drop-capture.sh /usr/local/lib/duo2-touch-tools/
install -m 0644 etc/systemd/system/duo2-touchd.service /etc/systemd/system/
install -D -m 0644 usr/lib/udev/rules.d/90-duo2-touch.rules /usr/lib/udev/rules.d/90-duo2-touch.rules
# the hook does nothing until the daemon writes its state file
install -D -m 0755 usr/lib/systemd/system-sleep/duo2-touchd /usr/lib/systemd/system-sleep/duo2-touchd
install -m 0755 usr/local/sbin/duo2-touch-calibration /usr/local/sbin/
install -d /etc/duo2
for f in touch-calibration touch-mode pen.conf; do
    [ -e "/etc/duo2/$f" ] || install -m 0644 "etc/duo2/$f" /etc/duo2/
done
# read by libinput when duo-wc starts
install -D -m 0644 usr/share/libinput/90-duo2-pen.quirks /usr/share/libinput/90-duo2-pen.quirks
# the pen over Bluetooth (address resolution, AutoBond state, haptics): no runtime needed
install -m 0755 usr/local/sbin/duo2-pend /usr/local/sbin/
install -m 0644 etc/systemd/system/duo2-pend.service /etc/systemd/system/
if [ -f ../duo2-touchd ]; then
    install -D -m 0755 ../duo2-touchd /usr/local/lib/duo2-touch/duo2-touchd
    if [ -d /opt/duo2-touch ] && ! cmp -s ../duo2-touchd /opt/duo2-touch/duo2-touchd; then
        install -m 0755 -o root -g root ../duo2-touchd /opt/duo2-touch/duo2-touchd.new
        mv -f /opt/duo2-touch/duo2-touchd.new /opt/duo2-touch/duo2-touchd
    fi
fi
