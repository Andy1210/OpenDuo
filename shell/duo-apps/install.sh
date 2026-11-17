#!/bin/bash
# Install the preinstalled apps and their duo2-os integration on the phone
# over the network (scripts/lib/remote.sh): the packages (apt, no recommends),
# then install-files.sh on the phone. A freshly built image has all of this
# (rootfs/Dockerfile, rootfs/install-components.sh). A package or file added
# here belongs there too.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"

packages="fonts-cantarell fonts-noto-color-emoji appstream apt-config-icons apt-config-icons-hidpi
gnome-clocks gnome-calculator gnome-calendar evolution-data-server gnome-weather
nautilus portfolio-filemanager loupe papers amberol celluloid mpv ffmpeg
gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-pipewire libgtk-4-media-gstreamer
sound-theme-freedesktop
angelfish qt6-wayland libqt6sql6-sqlite qml6-module-org-kde-desktop plasma-integration kde-style-breeze
gnome-software gnome-software-plugin-deb gnome-software-plugin-flatpak flatpak packagekit packagekit-tools"

stage=$(duo2_stage duo-apps)
duo2_ssh "rm -rf $stage"
duo2_scp -r "$here" "$DUO2_HOST:$stage"
# the package list is written over several lines: one line for the remote for-loop
# shellcheck disable=SC2086 # the splitting is the point
plist=$(printf '%s ' $packages)
duo2_ssh "set -eu
missing=
for p in $plist; do
    dpkg -s \$p >/dev/null 2>&1 || missing=\"\$missing \$p\"
done
if [ -n \"\$missing\" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \$missing
fi
sudo sh $stage/install-files.sh $DUO2_USER
rm -rf $stage"
duo2_user "systemctl --user daemon-reload
systemctl --user start duo-apps-session.path"
