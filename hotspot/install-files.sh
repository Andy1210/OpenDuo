#!/bin/sh
# Put the hotspot's files in place, as root, on the phone (install.sh runs it
# there) or in the image (rootfs/install-components.sh runs it under qemu):
# one list for both. The package (hostapd) comes first (install.sh,
# rootfs/Dockerfile). Nothing is started or enabled: the hotspot runs only
# while the user shares (duo2-hotspot on).
#
#   install-files.sh USER   (run from this directory; USER = the GUI user)
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

L=/usr/local/lib/duo2-hotspot
install -d "$L/duo2hotspot"
install -m 0644 duo2hotspot/*.py "$L/duo2hotspot/"
install -m 0755 duo2-hotspot duo2-hotspotd /usr/local/sbin/
install -m 0644 duo2-hotspot.service /etc/systemd/system/duo2-hotspot.service
install -d /etc/duo2
install -m 0644 hotspot.conf /etc/duo2/hotspot.conf.dist
[ -e /etc/duo2/hotspot.conf ] || install -m 0644 hotspot.conf /etc/duo2/hotspot.conf
install -d -m 0700 /var/lib/duo2/hotspot
# Debian's own hostapd units stay off (ours runs hostapd with a /run config).
systemctl disable hostapd.service >/dev/null 2>&1 || true

# The WiFi country from the mobile network (README "Regulatory domain"):
# files only. Its timer is enabled by hand once the kernel takes a user's
# country (CONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS and the ath11k patches).
install -m 0755 duo2-wifi-country /usr/local/sbin/
install -m 0644 duo2-wifi-country.service duo2-wifi-country.timer /etc/systemd/system/
install -m 0644 wifi-country.conf /etc/duo2/wifi-country.conf.dist
[ -e /etc/duo2/wifi-country.conf ] || install -m 0644 wifi-country.conf /etc/duo2/wifi-country.conf
# cfg80211's own database: the kernel trusts the upstream signature only
# (net/wireless/certs), so with Debian's alternative it refuses the file
# ("malformed or signature is missing/invalid") and every country hint ends
# in a CRDA timeout. Takes effect at the next boot; the chip's own domain
# (self-managed) never depends on it.
if [ -e /lib/firmware/regulatory.db-upstream ]; then
    update-alternatives --set regulatory.db /lib/firmware/regulatory.db-upstream >/dev/null 2>&1 || true
fi

# The GUI user's door (Duo Settings, the quick menu): exactly this file.
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/duo2-hotspot" >"$rule"
if visudo -cqf "$rule"; then
    install -m 0440 "$rule" /etc/sudoers.d/duo2-hotspot
fi
rm -f "$rule"
