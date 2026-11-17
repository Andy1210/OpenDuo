#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# pen-drop-capture.sh: why the Surface pen drops its Bluetooth link when it comes near the
# screen. Diagnosis only: nothing is written to the pen or to BlueZ. Run as root on the phone,
# by hand, while the owner brings the pen to the screen and away again every ~10 s:
#
#   sudo systemd-run --wait --collect -p MemoryMax=300M -p RuntimeMaxSec=600 \
#       /usr/local/lib/duo2-touch-tools/pen-drop-capture.sh [SECONDS [RAWSCAN]]
#
# SECONDS (default 180): btmon + the digitizer's AutoBond reports (pen-bt-trace.py capture)
# + the journal of duo-wc, the kernel, bluetoothd, seatd, duo2-pend and duo2-touchd. The pen's
# HID report descriptor and the udev view of its input devices are kept on its first link.
# RAWSCAN (default 0: none): then RAWSCAN s of pen-bt-trace.py's unfiltered raw scan, which
# powers Bluetooth OFF for RAWSCAN-10 s (on again after): only with the owner's go.
#
# Everything stays in /run/duo2-penbt (root, 0700); the btsnoop holds link keys: read the
# timeline (phaseA.txt, every address a role) and delete the directory afterwards.
set -u
D=/run/duo2-penbt
TOOLS=$(dirname "$(readlink -f "$0")")
A=${1:-180}
B=${2:-0}
(umask 077; mkdir -p "$D")
chmod 700 "$D"
cd "$D" || exit 1
rm -f ./done marks phaseA.txt phaseB.txt journal.txt pen.rdesc udev.txt
journalctl -f -n0 -o short-precise -t duo-wc -t kernel -t bluetoothd -t seatd -t python3 \
    -t linker64 -u duo2-pend > $D/journal.txt 2>&1 &
J=$!
python3 -I "$TOOLS/pen-bt-trace.py" capture --seconds "$A" --dir $D > $D/phaseA.txt 2>&1 &
P=$!
echo "$(date +%T) capture start" > $D/marks
t=0
while kill -0 $P 2>/dev/null; do
    if [ ! -s $D/pen.rdesc ]; then
        for d in /sys/bus/hid/devices/0005:045E:0C0F.*; do
            [ -e "$d/report_descriptor" ] || continue
            cp "$d/report_descriptor" $D/pen.rdesc
            for e in "$d"/input/input*; do
                echo "== $(cat "$e/name")"
                for ev in "$e"/event*; do
                    [ -e "$ev" ] && udevadm info -q property "$ev" | grep -E "DEVNAME|ID_INPUT|LIBINPUT"
                done
            done > $D/udev.txt 2>&1
            echo "$(date +%T) the pen's HID device, $t s in" >> $D/marks
        done
    fi
    sleep 1
    t=$((t + 1))
done
echo "$(date +%T) capture end" >> $D/marks
if [ "$B" -gt 0 ]; then
    python3 -I "$TOOLS/pen-bt-trace.py" capture --seconds "$B" --rawscan $((B - 10)) --dir $D \
        > $D/phaseB.txt 2>&1
    echo "$(date +%T) raw scan end" >> $D/marks
fi
kill $J
echo "done" > "$D/done"
