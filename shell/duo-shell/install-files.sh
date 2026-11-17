#!/bin/sh
# Put duo-shell's files in place, as root: in the image (rootfs/install-components.sh
# under qemu) and on the phone from its package (packaging/, docs/UPDATES.md):
# one list for both. install.sh (the network installer) does the same by hand,
# plus the packages and the restart of a running shell.
#
#   install-files.sh USER     (run from the staged directory:
#                             scripts/lib/stage-components.sh duo-shell)
#
#   /usr/local/lib/duo-shell/{duo-shell,duo-shell-helper,duo-lock,duo-lock-helper,
#       duo-wallpaper-bing,duo-clipboard-bridge,duoshell/,protocols/,icons/}
#   /usr/local/bin/duo-shell, /usr/local/bin/duo-lock
#   /etc/systemd/user/duo-wallpaper-bing.{service,timer} (the timer enabled globally)
#   /etc/sudoers.d/duo-shell, /var/lib/duo2-lock (the PIN store's directory)
#   /etc/xdg/xdg-desktop-portal/portals.conf
#   /etc/dbus-1/system.d/org.duo2os.Shell.conf (for USER)
#   /usr/share/glib-2.0/schemas/90_duo2-os.gschema.override (compiled)
#
# The directory is swapped whole (.new / .old): a running shell keeps its
# open modules; the session starts the new one when the old one exits.
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo-shell
rm -rf "$L.new"
install -d "$L.new/duoshell"
install -m 0755 duo-shell duo-shell-helper duo-lock duo-lock-helper duo-wallpaper-bing duo-clipboard-bridge "$L.new/"
install -m 0644 duoshell/* "$L.new/duoshell/"
install -d "$L.new/icons/hicolor/symbolic/apps"
install -m 0644 icons/hicolor/symbolic/apps/*.svg "$L.new/icons/hicolor/symbolic/apps/"
python3 -I gen-protocols.py "$L.new/protocols"
find "$L.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"
ln -sf "$L/duo-shell" /usr/local/bin/duo-shell
ln -sf "$L/duo-lock" /usr/local/bin/duo-lock
# The Bing image of the day: a user service + daily timer (it asks Bing only
# while the switch in Duo Settings is on).
install -m 0644 systemd/duo-wallpaper-bing.service systemd/duo-wallpaper-bing.timer /etc/systemd/user/
systemctl --global enable duo-wallpaper-bing.timer >/dev/null 2>&1 ||
    { install -d /etc/systemd/user/timers.target.wants &&
      ln -sf /etc/systemd/user/duo-wallpaper-bing.timer /etc/systemd/user/timers.target.wants/; }
rule=$(mktemp)
{
echo "$U ALL=(root) NOPASSWD: $L/duo-shell-helper"
# The lock's PIN store (root's, /var/lib/duo2-lock): its one door, these
# subcommands only (duoshell/lockstore.py; migrate is the installer's).
echo "$U ALL=(root) NOPASSWD: $L/duo-lock-helper verify, $L/duo-lock-helper set, $L/duo-lock-helper clear, $L/duo-lock-helper status, $L/duo-lock-helper policy *, $L/duo-lock-helper home *"
} >"$rule"
visudo -cqf "$rule"
install -m 0440 "$rule" /etc/sudoers.d/duo-shell
rm -f "$rule"
install -d -m 0755 /var/lib/duo2-lock
# An old PIN in the user's lock.conf moves to root's store (and leaves that file).
old=/home/$U/.config/duo-shell/lock.conf
if [ -f "$old" ]; then
    "$L/duo-lock-helper" migrate "$old" || echo "duo-shell: $old not migrated" >&2
fi
install -D -m 0644 portals.conf /etc/xdg/xdg-desktop-portal/portals.conf
install -d /etc/dbus-1/system.d
sed "s/@USER@/$U/" org.duo2os.Shell.conf >/etc/dbus-1/system.d/org.duo2os.Shell.conf
chmod 0644 /etc/dbus-1/system.d/org.duo2os.Shell.conf
install -m 0644 90_duo2-os.gschema.override /usr/share/glib-2.0/schemas/90_duo2-os.gschema.override
glib-compile-schemas /usr/share/glib-2.0/schemas
