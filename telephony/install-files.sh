#!/bin/sh
# Put the telephony files in place, as root, on the phone (install.sh runs it
# there) or in the image (rootfs/install-components.sh runs it under qemu):
# one list for both. The packages come first (install.sh, rootfs/Dockerfile).
#
#   install-files.sh USER   (run from this directory; USER = the GUI user)
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

# The modem's low-power hook (before ModemManager), the SIM slots, the modem
# switch + mobile data daemon.
install -m 0755 duo2-modem-lpm duo2-sim duo2-wwan duo2-sms-storage /usr/local/sbin/
install -m 0644 systemd/duo2-modem-lpm.service systemd/duo2-wwan.service /etc/systemd/system/
install -D -m 0644 systemd/ModemManager-duo2.conf /etc/systemd/system/ModemManager.service.d/duo2.conf
install -D -m 0644 30-wwan.network /etc/systemd/network/30-wwan.network
install -d /etc/duo2
install -m 0644 wwan.conf /etc/duo2/wwan.conf.dist
[ -e /etc/duo2/wwan.conf ] || install -m 0644 wwan.conf /etc/duo2/wwan.conf
systemctl enable ModemManager.service duo2-modem-lpm.service duo2-wwan.service

# The GUI user's apps may use the modem (no logind session: polkit's "active"
# defaults never apply).
install -d -m 0755 /etc/polkit-1/rules.d
sed "s/@USER@/$U/" 50-duo2-modemmanager.rules >/etc/polkit-1/rules.d/50-duo2-modemmanager.rules
chmod 0644 /etc/polkit-1/rules.d/50-duo2-modemmanager.rules

# Calls and Chatty in the background once duo-wc's session is up.
install -d /usr/local/lib/duo2-telephony
install -m 0755 session-start /usr/local/lib/duo2-telephony/session-start

# Call audio: the voice path, call volume and mute (a user service, with the
# user's PipeWire; linger starts it at boot).
install -m 0755 duo2-callaudio /usr/local/lib/duo2-telephony/duo2-callaudio
install -m 0644 callaudio.conf /etc/duo2/callaudio.conf.dist
[ -e /etc/duo2/callaudio.conf ] || install -m 0644 callaudio.conf /etc/duo2/callaudio.conf
install -m 0644 systemd/user/duo2-callaudio.service /etc/systemd/user/
# the q6voice bring-up switches of 2026-11-08 are gone with the debug kernel
rm -f /etc/modprobe.d/duo2-q6voice.conf
install -D -m 0644 dbus/org.mobian_project.CallAudio.service \
    /usr/local/share/dbus-1/services/org.mobian_project.CallAudio.service
systemctl --global enable duo2-callaudio.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/user/default.target.wants &&
      ln -sf /etc/systemd/user/duo2-callaudio.service /etc/systemd/user/default.target.wants/; }
install -m 0644 systemd/user/duo-telephony-session.path systemd/user/duo-telephony-session.service \
    systemd/user/duo-calls-daemon.service systemd/user/duo-chatty-daemon.service /etc/systemd/user/
systemctl --global enable duo-telephony-session.path >/dev/null 2>&1 ||
    { install -d /etc/systemd/user/default.target.wants &&
      ln -sf /etc/systemd/user/duo-telephony-session.path /etc/systemd/user/default.target.wants/; }

# The eSIM (duo2-esim over lpac, built from source by lpac/build.sh into
# out/lpac; install.sh and the image bring it as lpac-bin/). The helper asks
# the user in the shell before any change; any app may read the profiles.
install -m 0755 duo2-esim /usr/local/sbin/duo2-esim
install -m 0644 tests/fake_lpac.py /usr/local/lib/duo2-telephony/fake_lpac.py
if [ -x lpac-bin/bin/lpac ]; then
    install -m 0755 lpac-bin/bin/lpac /usr/local/bin/lpac
    install -d /usr/local/share/doc/lpac
    cp -a lpac-bin/share/doc/lpac/. /usr/local/share/doc/lpac/
fi
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/duo2-esim" >"$rule"
if visudo -cqf "$rule"; then
    install -m 0440 "$rule" /etc/sudoers.d/duo2-esim
fi
rm -f "$rule"

# XTRA assistance data for the GNSS engine (docs/hardware/location.md): the
# helper and its twice-daily timer, NOT enabled (the owner decides: the
# download goes to the server the modem names).
install -m 0755 duo2-gnss-assist /usr/local/sbin/duo2-gnss-assist
install -m 0644 systemd/duo2-gnss-assist.service systemd/duo2-gnss-assist.timer /etc/systemd/system/
