#!/bin/sh
# Put duo2-stock-import in place, as root, in the image (rootfs/install-components.sh
# under qemu) or on the phone (the openduo-stock-surface-duo2 package runs it).
#
#   install-files.sh
#
# The tool reads the phone's own stock partitions at the first boot
# (duo2-stock-import.service) and writes what it finds there; it ships no
# vendor file. scripts/ holds the host recipes it shares with the
# repository's build scripts (collect-firmware.sh, mdt2mbn.py, the camera
# table and power profile decoders).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo2-stock
rm -rf "$L.new"
install -d "$L.new/scripts"
install -m 0644 duo2stock.py README.md "$L.new/"
install -m 0755 duo2-stock-import "$L.new/"
install -m 0755 scripts/collect-firmware.sh "$L.new/scripts/"
install -m 0644 scripts/mdt2mbn.py scripts/camx-sensor-tables.py scripts/camx-module-dump.py \
    scripts/axml2xml.py "$L.new/scripts/"
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"
ln -sf "$L/duo2-stock-import" /usr/local/sbin/duo2-stock-import
install -m 0644 duo2-stock-import.service /etc/systemd/system/duo2-stock-import.service
