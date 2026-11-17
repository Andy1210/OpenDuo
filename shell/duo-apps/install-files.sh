#!/bin/sh
# Put duo-apps' files in place, as root, on the phone (install.sh runs it
# there) or in the image (rootfs/install-components.sh runs it under qemu):
# one list for both.
#
#   install-files.sh USER     (run from this directory)
#
# The packages come first (install.sh, rootfs/Dockerfile).
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

# Calculator without the on-screen keyboard over its keypad.
install -d /usr/local/share/applications
if [ -f /usr/share/applications/org.gnome.Calculator.desktop ]; then
    sed -f applications/org.gnome.Calculator.desktop.sed \
        /usr/share/applications/org.gnome.Calculator.desktop \
        >/usr/local/share/applications/org.gnome.Calculator.desktop
    chmod 0644 /usr/local/share/applications/org.gnome.Calculator.desktop
fi
# The video players this phone does not use (GStreamer's 1080p path stutters
# here, see README.md): hidden if they are installed anyway.
for id in org.gnome.Showtime com.github.rafostar.Clapper; do
    if [ -f /usr/share/applications/$id.desktop ]; then
        { cat /usr/share/applications/$id.desktop; echo "NoDisplay=true"; } \
            >/usr/local/share/applications/$id.desktop
    else
        rm -f /usr/local/share/applications/$id.desktop
    fi
done

# Clocks: a plain start on D-Bus activation (alarms keep ringing after its
# window closes).
install -D -m 0644 dbus-services/org.gnome.clocks.service \
    /usr/local/share/dbus-1/services/org.gnome.clocks.service

# Defaults (GNOME Software, weather units).
install -m 0644 91_duo2-os-apps.gschema.override /usr/share/glib-2.0/schemas/
glib-compile-schemas /usr/share/glib-2.0/schemas

# The session-start hook (portals, user directories, alarm services), and
# the duo-wc session hook that starts Clocks for its alarms (it closes
# Clocks' window: the session's trust).
install -D -m 0755 session-start /usr/local/lib/duo-apps/session-start
install -D -m 0755 clocks-alarms /usr/local/lib/duo-wc/session.d/50-duo-apps-clocks
install -d /etc/systemd/user/xdg-desktop-portal-gtk.service.d
install -m 0644 systemd/duo-apps-session.path systemd/duo-apps-session.service \
    systemd/duo-calendar-alarms.service /etc/systemd/user/
install -m 0644 systemd/xdg-desktop-portal-gtk.conf \
    /etc/systemd/user/xdg-desktop-portal-gtk.service.d/duo2.conf
systemctl --global enable duo-apps-session.path >/dev/null 2>&1 ||
    ln -sf /etc/systemd/user/duo-apps-session.path /etc/systemd/user/default.target.wants/

# Qt / Kirigami apps (Angelfish): native Wayland, the phone layout, dark
# Breeze colours, without Plasma. kdeglobals is the system default (a user's
# ~/.config/kdeglobals still wins); it is made from Breeze's own colour scheme.
install -D -m 0644 qt/60-duo2-qt.conf /etc/environment.d/60-duo2-qt.conf
if [ -f /usr/share/color-schemes/BreezeDark.colors ]; then
    {
        printf '# duo2-os: dark Breeze for Qt apps (shell/duo-apps/install-files.sh,\n'
        printf '# from /usr/share/color-schemes/BreezeDark.colors).\n'
        printf '[General]\nColorScheme=BreezeDark\n\n[KDE]\nwidgetStyle=Breeze\n\n[Icons]\nTheme=Adwaita\n\n'
        sed -n '/^\[Colors:/,$p' /usr/share/color-schemes/BreezeDark.colors | sed '/^\[General\]/,/^$/d; /^\[KDE\]/,/^$/d'
    } >/etc/xdg/kdeglobals
    chmod 0644 /etc/xdg/kdeglobals
fi

# GNOME Software: PackageKit without a password prompt for the desktop user
# (install and refresh; removing asks the user in the shell first, through
# duo2-polkit-confirm and duo-wc), and the "Works well on the Duo" section.
install -D -m 0755 software/duo2-polkit-confirm /usr/local/lib/duo2/duo2-polkit-confirm
install -d -m 0755 /etc/polkit-1/rules.d
sed "s/@USER@/$U/" software/50-duo2-packagekit.rules >/etc/polkit-1/rules.d/50-duo2-packagekit.rules
chmod 0644 /etc/polkit-1/rules.d/50-duo2-packagekit.rules
install -D -m 0644 software/deployment-featured.ini /etc/xdg/gnome-software/deployment-featured.ini
install -D -m 0644 software/works-on-duo.xml /usr/share/swcatalog/xml/duo2-os-works-on-duo.xml

# Flathub for the user (a user installation: no polkit needed for Flatpaks).
if command -v flatpak >/dev/null; then
    su "$U" -s /bin/sh -c 'flatpak remote-add --user --if-not-exists flathub \
        https://dl.flathub.org/repo/flathub.flatpakrepo' ||
        echo "duo-apps: could not add Flathub (no network?); add it later as $U" >&2
fi
