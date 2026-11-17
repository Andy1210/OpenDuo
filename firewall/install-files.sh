#!/bin/sh
# Put the firewall in place, as root, on the phone (install.sh runs it there)
# or in the image (rootfs/install-components.sh under qemu): one list for both.
# It is enabled; it starts with the next boot, or now with `systemctl start
# duo-firewall` (install.sh does that only with --start).
#
#   install-files.sh     (run from this directory)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
install -d -m 0755 /etc/duo-firewall
install -m 0644 firewall.nft /etc/duo-firewall/firewall.nft
install -m 0644 duo-firewall.service /etc/systemd/system/duo-firewall.service
install -d -m 0755 /etc/systemd/resolved.conf.d
install -m 0644 resolved-no-llmnr.conf /etc/systemd/resolved.conf.d/90-duo-no-llmnr.conf
systemctl enable duo-firewall.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/system/sysinit.target.wants &&
      ln -sf /etc/systemd/system/duo-firewall.service /etc/systemd/system/sysinit.target.wants/; }
