#!/usr/bin/env bash
# Put the factory-address pieces of rootfs/overlay on a running phone
# (scripts/lib/remote.sh) and provision it:
#
#   /usr/local/sbin/duo2-factory-addr     reads sfpd (read-only) -> /etc/duo2/factory-addresses
#   /usr/local/sbin/duo2-bt-addr          + duo2-bt-addr@.service, 90-duo2-bt-addr.rules:
#                                         btmgmt public-addr on the unconfigured WCN6855
#   /usr/local/sbin/duo2-wifi-mac-trial   runtime WiFi MAC test with automatic way back
#   duo2-factory-addr.service             first-boot provisioning (fresh images)
#
#   scripts/install-factory-addr.sh          install + `duo2-factory-addr save`, and set the
#                                            BT address now; the WiFi MAC is NOT persisted
#   scripts/install-factory-addr.sh --link   ... and persist the WiFi MAC (.link drop-in,
#                                            from the next boot): only after a passed
#                                            duo2-wifi-mac-trial
#
# The addresses never leave the phone: they are read there and written to root-only
# files; every output is masked.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
mode=save
[ "${1:-}" = --link ] && mode=install

O=$R/rootfs/overlay
files=(usr/local/sbin/duo2-factory-addr usr/local/sbin/duo2-bt-addr usr/local/sbin/duo2-wifi-mac-trial
       usr/lib/udev/rules.d/90-duo2-bt-addr.rules
       etc/systemd/system/duo2-bt-addr@.service etc/systemd/system/duo2-factory-addr.service)
tmp=$(duo2_ssh 'mktemp -d')
(cd "$O" && tar cf - "${files[@]}") | duo2_ssh "tar xf - -C $tmp"
duo2_ssh "set -e
cd $tmp
for f in ${files[*]}; do
    case \$f in */sbin/*) m=755 ;; *) m=644 ;; esac
    sudo install -D -m \$m -o root -g root \"\$f\" \"/\$f\"
done
rm -rf $tmp
sudo systemctl daemon-reload
sudo udevadm control --reload
sudo systemctl enable duo2-factory-addr.service >/dev/null 2>&1
sudo /usr/local/sbin/duo2-factory-addr $mode
# The controller is already registered: start its instance by hand (later
# boots get it from udev).
for h in /sys/class/bluetooth/hci*; do
    [ -e \"\$h\" ] || continue
    readlink -f \"\$h/device/driver\" 2>/dev/null | grep -q hci_uart_qca || continue
    sudo systemctl start \"duo2-bt-addr@\${h##*/}.service\" || true
done"
