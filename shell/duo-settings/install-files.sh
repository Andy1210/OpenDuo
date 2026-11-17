#!/bin/sh
# Put Duo Settings in place, as root, on the phone (install.sh runs it there)
# or in the image (rootfs/install-components.sh under qemu): one list for both.
#
#   install-files.sh USER     (run from this directory; touch-calib.py from
#                             compositor/duo-wc/tools staged next to it)
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo-settings
rm -rf "$L.new"
install -d "$L.new/duosettings"
install -m 0755 duo-settings duo-settings-helper duo-settings-qmi duo-voicemail duo-appearance "$L.new/"
install -m 0644 duosettings/*.py "$L.new/duosettings/"
[ -f touch-calib.py ] && install -m 0644 touch-calib.py "$L.new/touch-calib.py"
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"
ln -sf "$L/duo-settings" /usr/local/bin/duo-settings
ln -sf "$L/duo-voicemail" /usr/local/bin/duo-voicemail
ln -sf "$L/duo-appearance" /usr/local/bin/duo-appearance
# Settings > Appearance > Automatic: the user's timer (duo-appearance enables
# it for Automatic only).
install -m 0644 systemd/duo-appearance.service systemd/duo-appearance.timer /etc/systemd/user/

install -D -m 0644 org.duo2os.Settings.desktop /usr/local/share/applications/org.duo2os.Settings.desktop
install -D -m 0644 org.duo2os.Voicemail.desktop /usr/local/share/applications/org.duo2os.Voicemail.desktop
install -D -m 0644 org.duo2os.Settings.svg \
    /usr/local/share/icons/hicolor/scalable/apps/org.duo2os.Settings.svg
command -v gtk-update-icon-cache >/dev/null &&
    gtk-update-icon-cache -q -t -f /usr/local/share/icons/hicolor 2>/dev/null || true

# Root actions: exactly the helper, for the desktop user.
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: $L/duo-settings-helper" >"$rule"
visudo -cqf "$rule"
install -m 0440 "$rule" /etc/sudoers.d/duo-settings
rm -f "$rule"

# The refresh rate chosen in Settings, before duo-wc starts.
install -m 0644 duo-panel-refresh.service /etc/systemd/system/duo-panel-refresh.service
systemctl enable duo-panel-refresh.service >/dev/null 2>&1 || true
