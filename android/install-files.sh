#!/bin/sh
# Put the Waydroid (Android apps) files in place, as root, on the phone
# (install.sh runs it there) or in the image (rootfs/install-components.sh
# runs it under qemu): one list for both. The packages come first
# (install.sh, rootfs/Dockerfile). docs/hardware/android.md.
#
#   install-files.sh USER   (run from this directory; USER = the GUI user)
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

# The hardening: local patches to Waydroid (re-applied after every dpkg run),
# the LXC include (devices, capabilities, AppArmor, seccomp), the AppArmor
# profile, the firewall, the check before every container manager start.
install -m 0755 duo2-waydroid-patch duo2-waydroid-prestart duo2-waydroid-reaper duo2-waydroid-upgrade \
    /usr/local/sbin/
install -d /etc/duo2/waydroid
install -m 0644 lxc-duo2.conf /etc/duo2/waydroid/lxc.conf
install -m 0644 waydroid.seccomp /etc/duo2/waydroid/waydroid.seccomp
install -m 0644 firewall.nft /etc/duo2/waydroid/firewall.nft
[ -e /etc/duo2/waydroid.conf ] || install -m 0644 waydroid.conf /etc/duo2/waydroid.conf
rm -f /etc/apparmor.d/duo2-waydroid
install -m 0644 apparmor/lxc-duo2-waydroid /etc/apparmor.d/lxc-duo2-waydroid
install -d /etc/apt/apt.conf.d
printf '%s\n' '// duo2-os: re-apply the local Waydroid patches after every dpkg run' \
    'DPkg::Post-Invoke { "if [ -x /usr/local/sbin/duo2-waydroid-patch ] && [ -d /usr/lib/waydroid/tools ]; then /usr/local/sbin/duo2-waydroid-patch || true; fi"; };' \
    > /etc/apt/apt.conf.d/80duo2-waydroid
if [ -d /usr/lib/waydroid/tools ]; then
    /usr/local/sbin/duo2-waydroid-patch
fi

# On demand: no container manager at boot (D-Bus activation starts it), an
# idle one is stopped by the reaper.
install -D -m 0644 systemd/system/waydroid-container.service.d-duo2.conf \
    /etc/systemd/system/waydroid-container.service.d/duo2.conf
install -m 0644 systemd/system/duo2-waydroid-reaper.service systemd/system/duo2-waydroid-reaper.timer \
    systemd/system/duo2-waydroid-upgrade.service systemd/system/duo2-waydroid-upgrade.timer \
    /etc/systemd/system/
systemctl disable waydroid-container.service >/dev/null 2>&1 || true
# weekly image update (WiFi only, Android not running)
systemctl enable duo2-waydroid-reaper.timer duo2-waydroid-upgrade.timer

# The GUI user's side: the session (D-Bus activated by `waydroid app launch`,
# the Android icons' command) and its idle check.
install -d /usr/local/lib/duo2-waydroid
install -m 0755 duo2-waydroid-idle duo2-waydroid-session /usr/local/lib/duo2-waydroid/
install -m 0644 systemd/user/duo2-waydroid-session.service systemd/user/duo2-waydroid-idle.service \
    /etc/systemd/user/

# The phone's position for Android apps (docs/hardware/location.md): a bridge from
# geoclue into Android's test providers, started with the session; its
# settings, and the name Settings > Privacy lists it under ("Android apps").
install -m 0755 duo2-android-location /usr/local/lib/duo2-waydroid/
install -d /usr/local/lib/duo2-waydroid/androidloc
install -m 0644 androidloc/__init__.py androidloc/parcel.py androidloc/locmgr.py androidloc/policy.py \
    /usr/local/lib/duo2-waydroid/androidloc/
install -m 0644 systemd/user/duo2-android-location.service /etc/systemd/user/
[ -e /etc/duo2/android-location.conf ] || install -m 0644 android-location.conf /etc/duo2/android-location.conf
install -D -m 0644 duo2-android-location.desktop /usr/local/share/applications/duo2-android-location.desktop
install -D -m 0644 dbus/id.waydro.Session.service /usr/share/dbus-1/services/id.waydro.Session.service

# duo-wc's Android full screen sizes an app's task inside Android (through
# `waydroid shell`, so root): the GUI user may run this one helper, which
# checks its arguments (compositor/duo-wc/README.md "Android apps").
install -m 0755 duo2-android-task /usr/local/sbin/
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/duo2-android-task" >"$rule"
if visudo -cqf "$rule"; then
    install -m 0440 "$rule" /etc/sudoers.d/duo2-android-task
else
    echo 'sudoers rule for duo2-android-task refused by visudo; not installed' >&2
fi
rm -f "$rule"
: "$U"
