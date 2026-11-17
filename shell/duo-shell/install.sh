#!/bin/bash
# Install duo-shell (status bars, app grid, quick menu, notifications, dock)
# and the on-screen keyboard on the phone over the network
# (scripts/lib/remote.sh).
#
#   ./install.sh            copy the shell, install the packages it needs,
#                           restart a running shell (the session starts it)
#
# On the phone:
#   /usr/local/lib/duo-shell/{duo-shell,duo-shell-helper,duo-lock,duoshell/,protocols/,icons/},
#   /usr/local/lib/duo-shell/duo-wallpaper-bing + the user units
#   /etc/systemd/user/duo-wallpaper-bing.{service,timer} (the Bing image of the day)
#   /usr/local/bin/duo-shell, /usr/local/bin/duo-lock (the lock screen: a
#   swipe up unlocks without a PIN; it locks nothing while duo-wc's
#   session_lock is off)
#   /usr/share/glib-2.0/schemas/90_duo2-os.gschema.override (OSK on, dark)
#   /etc/xdg/xdg-desktop-portal/portals.conf   the GTK portal backend: libadwaita
#                              apps follow the dark colour scheme through it
#   /etc/dbus-1/system.d/org.duo2os.Shell.conf  the shell's remote on the system
#                              bus (root only: the quick menu without a touch)
#   /etc/sudoers.d/duo-shell   the session's user may run duo-shell-helper as
#                              root without a password (WiFi radio, brightness,
#                              duo-posed's auto_rotate, reboot, power off)
#
# The session (compositor/duo-wc/dist/session, installed with duo-wc) starts
# duo-shell and the on-screen keyboard (phosh-osk-stub or squeekboard: its
# dist/osk) and starts them again when they exit; this script restarts the
# running shell by killing the PID the session wrote.
#
# No build step: PyGObject on the phone's own GTK4 (see README.md for why).
#
# A freshly built image already has all of this, packages included
# (rootfs/Dockerfile, rootfs/install-components.sh): this script updates a
# running phone. A file or package added here belongs there too.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"

packages="python3-gi gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0
squeekboard phosh-osk-stub libpresage-data hunspell-hu
dconf-gsettings-backend dconf-cli adwaita-icon-theme dbus-daemon at-spi2-core libglib2.0-bin
librsvg2-common libnotify-bin gir1.2-graphene-1.0 xdg-desktop-portal xdg-desktop-portal-gtk
python3-pywayland python3-cffi-backend wayland-protocols python3-cairo python3-gi-cairo
gir1.2-adw-1 gir1.2-gweather-4.0 gir1.2-pango-1.0 fonts-cantarell gir1.2-modemmanager-1.0
gir1.2-gdkpixbuf-2.0 libjxl-gdk-pixbuf gnome-backgrounds"

stage=$(duo2_stage duo-shell)
duo2_ssh "rm -rf $stage && mkdir -p $stage/duoshell"
duo2_scp "$here/duo-shell" "$here/duo-shell-helper" "$here/duo-lock" "$here/duo-lock-helper" \
    "$here/duo-wallpaper-bing" "$here/duo-clipboard-bridge" \
    "$here/systemd/duo-wallpaper-bing.service" "$here/systemd/duo-wallpaper-bing.timer" \
    "$here/90_duo2-os.gschema.override" "$here/portals.conf" "$here/org.duo2os.Shell.conf" \
    "$here/lock/gen-protocols.py" "$DUO2_HOST:$stage/"
duo2_scp "$here"/duoshell/*.py "$here"/duoshell/*.css "$DUO2_HOST:$stage/duoshell/"
duo2_ssh "mkdir -p $stage/icons"
duo2_scp "$here"/icons/hicolor/symbolic/apps/*.svg "$DUO2_HOST:$stage/icons/"

# the package list is written over several lines: one line for the remote for-loop
# shellcheck disable=SC2086 # the splitting is the point
plist=$(printf '%s ' $packages)
duo2_ssh "set -eu
missing=
for p in $plist; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    # No recommends: squeekboard would pull in feedbackd (sounds, haptics),
    # phosh-osk-stub the Noto fonts and the OSK data packager (contrib). Its
    # word data: libpresage-data (English predictions; libpresage1v5 needs
    # it anyway) and hunspell-hu (the Hungarian suggestions).
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
cd $stage
sudo rm -rf /usr/local/lib/duo-shell.new
sudo install -d /usr/local/lib/duo-shell.new/duoshell
sudo install -m 0755 duo-shell duo-shell-helper duo-lock duo-lock-helper duo-wallpaper-bing duo-clipboard-bridge /usr/local/lib/duo-shell.new/
sudo install -m 0644 duoshell/* /usr/local/lib/duo-shell.new/duoshell/
sudo install -d /usr/local/lib/duo-shell.new/icons/hicolor/symbolic/apps
sudo install -m 0644 icons/*.svg /usr/local/lib/duo-shell.new/icons/hicolor/symbolic/apps/
sudo python3 -I gen-protocols.py /usr/local/lib/duo-shell.new/protocols
sudo rm -rf /usr/local/lib/duo-shell.old
[ -d /usr/local/lib/duo-shell ] && sudo mv /usr/local/lib/duo-shell /usr/local/lib/duo-shell.old
sudo mv /usr/local/lib/duo-shell.new /usr/local/lib/duo-shell
sudo rm -rf /usr/local/lib/duo-shell.old
sudo ln -sf /usr/local/lib/duo-shell/duo-shell /usr/local/bin/duo-shell
sudo ln -sf /usr/local/lib/duo-shell/duo-lock /usr/local/bin/duo-lock
sudo install -m 0644 duo-wallpaper-bing.service duo-wallpaper-bing.timer /etc/systemd/user/
sudo systemctl --global enable duo-wallpaper-bing.timer >/dev/null 2>&1 || true
rule=\$(mktemp)
L=/usr/local/lib/duo-shell/duo-lock-helper
{
echo \"$DUO2_USER ALL=(root) NOPASSWD: /usr/local/lib/duo-shell/duo-shell-helper\"
# The lock store's one door (duoshell/lockstore.py) and the home's encryption
# (homecrypt/): these subcommands only
# (migrate is the installer's).
echo \"$DUO2_USER ALL=(root) NOPASSWD: \$L verify, \$L set, \$L clear, \$L status, \$L policy *, \$L home *\"
} >\$rule
if sudo visudo -cqf \$rule; then
    sudo install -m 0440 \$rule /etc/sudoers.d/duo-shell
else
    echo 'sudoers rule for duo-shell-helper refused by visudo; not installed' >&2
fi
rm -f \$rule
# The lock's PIN is root's (/var/lib/duo2-lock); an old one in the user's
# lock.conf moves there (and leaves that file).
sudo install -d -m 0755 /var/lib/duo2-lock
old=/home/$DUO2_USER/.config/duo-shell/lock.conf
if sudo test -f \$old; then
    sudo /usr/local/lib/duo-shell/duo-lock-helper migrate \$old || echo 'lock.conf not migrated' >&2
fi
sudo /usr/local/lib/duo-shell/duo-lock-helper status >/dev/null
sudo install -d /etc/xdg/xdg-desktop-portal
sudo install -m 0644 portals.conf /etc/xdg/xdg-desktop-portal/portals.conf
sed \"s/@USER@/$DUO2_USER/\" org.duo2os.Shell.conf >org.duo2os.Shell.conf.u
sudo install -D -m 0644 org.duo2os.Shell.conf.u /etc/dbus-1/system.d/org.duo2os.Shell.conf
sudo systemctl reload dbus
schema=/usr/share/glib-2.0/schemas/90_duo2-os.gschema.override
sudo install -m 0644 90_duo2-os.gschema.override \$schema
sudo glib-compile-schemas /usr/share/glib-2.0/schemas
rm -rf $stage
pid=\$(cat /run/duo-wc/duo-shell.pid 2>/dev/null || true)
if [ -n \"\$pid\" ] && sudo kill -0 \"\$pid\" 2>/dev/null; then
    sudo kill \"\$pid\" && echo \"restarted the running shell (pid \$pid)\"
fi"
duo2_user "systemctl --user daemon-reload 2>/dev/null || true
systemctl --user start duo-wallpaper-bing.timer 2>/dev/null || true"
