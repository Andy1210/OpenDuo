#!/bin/sh
# Let the users of this Linux computer reach a Surface Duo 2 over USB:
# its bootloader (fastboot, USB vendor 045e) and the WOA recovery that
# install-openduo.sh starts (adb, USB vendor 18d1). Without these rules adb
# and fastboot say "no permissions" (docs/INSTALL.md).
#
#   sudo sh udev-rules.sh           write the rules, then unplug the phone
#                                   and plug it in again
#   sudo sh udev-rules.sh --remove  take them away again
set -eu

# OPENDUO_UDEV_RULES: another file (the tests)
RULES=${OPENDUO_UDEV_RULES:-/etc/udev/rules.d/51-surface-duo.rules}

if [ "$(id -u)" != 0 ]; then
	echo "udev-rules.sh: run it as root: sudo sh $0" >&2
	exit 1
fi

reload() {
	udevadm control --reload
	udevadm trigger --subsystem-match=usb
}

if [ "${1:-}" = --remove ]; then
	rm -f "$RULES"
	reload
	echo "removed $RULES"
	exit 0
fi

# GROUP="plugdev" where the group exists (Debian, Ubuntu); uaccess gives the
# user at the computer access everywhere else.
group=
if getent group plugdev >/dev/null 2>&1; then
	group=' GROUP="plugdev",'
fi
mkdir -p "$(dirname "$RULES")"
{
	echo "# Surface Duo 2: the bootloader (045e) and the WOA recovery (18d1), for adb"
	echo "# and fastboot without root (OpenDuo, scripts/udev-rules.sh)"
	for v in 045e 18d1; do
		echo "SUBSYSTEM==\"usb\", ATTR{idVendor}==\"$v\", MODE=\"0660\",$group TAG+=\"uaccess\""
	done
} >"$RULES.tmp"
chmod 0644 "$RULES.tmp"
mv "$RULES.tmp" "$RULES"
reload
echo "wrote $RULES"
echo "Now unplug the phone and plug it in again."
echo "If adb still says \"no permissions\": adb kill-server (this stops adb"
echo "for every phone on this computer), or unplug and replug once more."
