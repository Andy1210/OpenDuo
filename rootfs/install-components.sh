#!/bin/bash
# Install duo2-os's own components into the root filesystem while the image is
# built (rootfs/Dockerfile runs it under qemu; scripts/build-images.sh stages
# its input in components/). It puts the same files in the same places as the
# per-component network installers do on a running phone:
#
#   compositor/duo-wc/install.sh     its own install-files.sh (shared: run here too):
#                                    duo-wc, duoctl, session, osk, hello, tools,
#                                    /etc/duo-wc/duo-wc.conf(.dist), duo-wc.service
#   shell/duo-shell/install.sh       its own install-files.sh (shared: run here too):
#                                    duo-shell, duo-lock, the generated protocols,
#                                    the gschema override, portals.conf, the sudoers rule,
#                                    the shell's system bus policy
#   shell/duo-apps/install.sh        its own install-files.sh (shared: run here too)
#   shell/duo-settings/install.sh    likewise (Duo Settings, its root helper and sudoers rule)
#   shell/duo-screencast/install.sh  likewise (screen sharing: the portal backend's launcher,
#                                    chooser and screenshot gate; its masked user unit)
#   battery/install.sh               likewise (the battery usage collector, its unit, sleep
#                                    hook and group; the power profile is the owner's own)
#   power/install.sh                 its own install-files.sh (shared: run here too):
#                                    duo2-autosleep (held by /etc/duo2/autosleep-hold),
#                                    duo2-try-netwatch, the USB gadget sleep hook
#   telephony/install.sh             its own install-files.sh (shared: run here too): the
#                                    modem hook, duo2-sim, duo2-wwan, the polkit rule,
#                                    Calls and Chatty in the background
#   android/install.sh               its own install-files.sh (shared: run here too): the
#                                    Waydroid patches and hardening, on-demand session,
#                                    idle stop, the Android apps' location bridge (no
#                                    Android image: docs/hardware/android.md)
#   hotspot/install.sh               its own install-files.sh (shared: run here too): the
#                                    hotspot's helper, daemon and unit (not enabled: it runs
#                                    only while sharing), /etc/duo2/hotspot.conf, sudoers rule
#   maint/install.sh                 its own install-files.sh (shared: run here too):
#                                    duo2-update, duo2-snapshot, duo2-backup (timer not
#                                    enabled), the apt pin against kernel / firmware packages
#   firewall/install.sh              its own install-files.sh (shared: run here too): the
#                                    default-deny firewall (duo-firewall.service, enabled)
#                                    and resolved without LLMNR / mDNS
#   homecrypt/install.sh             its own install-files.sh (shared: run here too): the
#                                    home's optional encryption (duo2-home-crypt, its boot
#                                    and first-unlock units, the user drop-ins, the alarms'
#                                    copy for before the first unlock)
#   devmode/install.sh               its own install-files.sh (shared: run here LAST):
#                                    Settings > Developer's root tool duo-devmode, the bug
#                                    report's masking, /etc/duo-devmode.conf (all off, or
#                                    DEVELOPER_MODE=on), its sudoers rule, the developer
#                                    tools' payload (installed only with Developer mode on)
#   sensors/install-files.sh         the FREE half only (scripts/install-sensors.sh
#                                    brings the stock half): hexagonrpcd (patched), libssc,
#                                    iio-sensor-proxy 3.9 (out/duo2-sensors-bin), duo-posed,
#                                    the drop-ins, udev and polkit rules, duo-posed.service,
#                                    duo-brightness, its unit and /etc/duo2/brightness.conf
#
# The staged directories come from scripts/lib/stage-components.sh, and the
# OpenDuo packages run the same install-files.sh on the phone (packaging/,
# docs/UPDATES.md): a file a component installs belongs in its install-files.sh.
# What comes from the owner's own phone (the sensors' stock half, the touch
# runtime, the factory addresses, WiFi) is NOT installed here (see
# CONTRIBUTING.md and docs/INSTALL.md).
#
#   install-components.sh DIR USER
set -euo pipefail
C=$1
U=$2
cd "$C"

# The GUI user, for the packages' installers on the phone later (packaging/).
install -d -m 0755 /etc/duo2
printf '%s\n' "$U" >/etc/duo2/gui-user
chmod 0644 /etc/duo2/gui-user

echo "== duo-wc"
sh duo-wc/install-files.sh "$U"

echo "== duo-shell"
sh duo-shell/install-files.sh "$U"

echo "== duo-apps"
sh duo-apps/install-files.sh "$U"

echo "== duo-settings"
sh duo-settings/install-files.sh "$U"

echo "== duo-screencast (screen sharing through the portal)"
sh duo-screencast/install-files.sh

echo "== battery usage statistics"
sh battery/install-files.sh "$U"

echo "== power (automatic sleep, held; the try-kernel WiFi watchdog; the wall clock from the PMIC RTC)"
sh power/install-files.sh "$U"

echo "== telephony"
sh telephony/install-files.sh "$U"

echo "== boot splash (Plymouth)"
sh plymouth/install-files.sh

echo "== android (Waydroid)"
sh android/install-files.sh "$U"

echo "== updates, snapshots, backup (maint)"
sh maint/install-files.sh "$U"

echo "== mobile hotspot and USB tethering"
sh hotspot/install-files.sh "$U"

echo "== firewall (default deny; resolved without LLMNR)"
sh firewall/install-files.sh

echo "== the home's encryption (Settings > Lock screen > Encrypt my data; off until the user asks)"
sh homecrypt/install-files.sh

echo "== sensors (the free half), duo-posed, duo-brightness"
sh sensors/install-files.sh

# Last: it takes the developer tools the components above installed into its
# payload, and leaves them installed only with Developer mode on.
echo "== developer mode (Settings > Developer: ssh, keys, USB network, touch log, developer tools, bug report)"
DEVELOPER_MODE=${DEVELOPER_MODE:-off} sh devmode/install-files.sh "$U"
