#!/bin/sh
# Put the home's encryption in place, as root, on the phone (install.sh runs
# it there) or in the image (rootfs/install-components.sh under qemu): one
# list for both. Nothing is encrypted by this: the user switches it on in
# Settings > Lock screen (homecrypt/README.md).
#
#   install-files.sh     (run from this directory)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo2-homecrypt
rm -rf "$L.new"
install -d "$L.new/duohomecrypt"
install -m 0644 duohomecrypt/*.py "$L.new/duohomecrypt/"
install -m 0755 duo2-alarms-de "$L.new/"
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"
install -m 0755 duo2-home-crypt /usr/local/sbin/duo2-home-crypt
install -m 0644 systemd/duo2-home-boot.service systemd/duo2-home-settle.service /etc/systemd/system/
systemctl enable duo2-home-boot.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/system/multi-user.target.wants &&
      ln -sf /etc/systemd/system/duo2-home-boot.service /etc/systemd/system/multi-user.target.wants/; }
# Not before the first unlock of an encrypted home: SMS, MMS, Android.
for unit in duo-chatty-daemon.service mmsd-tng.service duo2-waydroid-session.service; do
    install -D -m 0644 systemd/user/50-duo2-home-locked.conf "/etc/systemd/user/$unit.d/50-duo2-home-locked.conf"
done
install -m 0644 systemd/user/duo2-alarms-de.service /etc/systemd/user/duo2-alarms-de.service
systemctl --global enable duo2-alarms-de.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/user/default.target.wants &&
      ln -sf /etc/systemd/user/duo2-alarms-de.service /etc/systemd/user/default.target.wants/; }
install -d -m 0755 /var/lib/duo2-lock
if [ ! -e /var/lib/duo2-lock/home.state ]; then
    printf '# duo2-home-crypt: the home'"'"'s encryption (homecrypt/README.md); no secret\nstate = plain\n' \
        >/var/lib/duo2-lock/home.state
    chmod 0644 /var/lib/duo2-lock/home.state
fi
