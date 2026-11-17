#!/bin/sh
# Put duo2-os's power pieces in place, as root, on the phone (install.sh runs
# it there) or in the image (rootfs/install-components.sh under qemu):
#
#   duo2-try-netwatch   a duo2-try boot without WiFi reboots (to the known-good kernel)
#   duo2-autosleep      suspend when both panels are dark; HELD by
#                       /etc/duo2/autosleep-hold, which the first install creates;
#                       duo2-autosleep-switch on|off|status is its switch (the GUI
#                       user may switch it and its charging hold through sudo),
#                       hold 2h a timed hold for remote work (root)
#   duo2-push-poke      duo2-autosleep's sync_hook: Waydroid's Play services
#                       check in after a WiFi sync wake (off unless configured)
#   duo2-usb-gadget     system-sleep hook: the USB gadget unbound across a suspend
#   duo2-charge-hold    the battery charge hold (Duo Settings: the GUI user may
#                       switch it through sudo), set again at every boot
#   duo2-pen-charger    the pen charger (the Pen Cover's wireless power for the
#                       Slim Pen) by the stock rule, while switched on in Duo
#                       Settings (its helper runs `duo2-pen-charger on|off`);
#                       needs the kernel's pen-charger output, else unsupported
#   duo2-rtc-time       the wall clock at boot from the PMIC RTC + the offset
#                       saved after NTP synchronizations; /dev/rtc = that RTC
#   duo2-torch-off      the torch (the camera's flash LED as a light) off before
#                       every suspend (system-sleep hook) and at every shutdown
#                       (duo2-torch-off.service); 91-duo2-torch.rules gives its
#                       brightness to the video group (duo-shell's quick menu)
#
#   install-files.sh USER     (USER = the GUI user)
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

enable_unit() { # unit: systemctl enable, or by hand under qemu
    systemctl enable "$1" >/dev/null 2>&1 ||
        { install -d /etc/systemd/system/multi-user.target.wants &&
          ln -sf "/etc/systemd/system/$1" /etc/systemd/system/multi-user.target.wants/; }
}

install -m 0755 duo2-try-netwatch /usr/local/sbin/duo2-try-netwatch
install -m 0644 duo2-try-netwatch.service /etc/systemd/system/duo2-try-netwatch.service
enable_unit duo2-try-netwatch.service

install -d /etc/duo2
if [ ! -e /etc/duo2/autosleep.conf ]; then
    # first install: automatic sleep starts held (the owner switches it on)
    install -m 0644 autosleep-hold /etc/duo2/autosleep-hold
    sed "s/^user = .*/user = $U/" autosleep.conf >/etc/duo2/autosleep.conf
    chmod 0644 /etc/duo2/autosleep.conf
fi
sed "s/^user = .*/user = $U/" autosleep.conf >/etc/duo2/autosleep.conf.dist
chmod 0644 /etc/duo2/autosleep.conf.dist
# a key the installed settings do not have yet: appended with its comment
# (the values already there stay the owner's)
awk 'function key(l) { sub(/[ \t]*=.*/, "", l); return l }
     NR == FNR { if ($0 ~ /^[a-z_]+[ \t]*=/) have[key($0)] = 1; next }
     /^#/ { block = block $0 "\n"; next }
     /^[a-z_]+[ \t]*=/ { if (!(key($0) in have)) printf "%s%s\n", block, $0 }
     { block = "" }' /etc/duo2/autosleep.conf /etc/duo2/autosleep.conf.dist >/etc/duo2/autosleep.conf.new
if [ -s /etc/duo2/autosleep.conf.new ]; then
    cat /etc/duo2/autosleep.conf.new >>/etc/duo2/autosleep.conf
fi
rm -f /etc/duo2/autosleep.conf.new
install -m 0755 duo2-autosleep /usr/local/sbin/duo2-autosleep
install -m 0755 duo2-push-poke /usr/local/sbin/duo2-push-poke
install -m 0755 duo2-autosleep-switch /usr/local/sbin/duo2-autosleep-switch
rule=$(mktemp)
S=/usr/local/sbin/duo2-autosleep-switch
echo "$U ALL=(root) NOPASSWD: $S on, $S off, $S charging on, $S charging off" >"$rule"
visudo -cqf "$rule"
install -m 0440 "$rule" /etc/sudoers.d/duo2-autosleep
rm -f "$rule"
install -m 0644 duo2-autosleep.service /etc/systemd/system/duo2-autosleep.service
enable_unit duo2-autosleep.service
install -D -m 0755 usb-gadget.sleep /usr/lib/systemd/system-sleep/duo2-usb-gadget
install -D -m 0644 duo2-qrtr.conf /etc/modprobe.d/duo2-qrtr.conf

install -m 0755 duo2-charge-hold /usr/local/sbin/duo2-charge-hold
install -m 0644 duo2-charge-hold.service /etc/systemd/system/duo2-charge-hold.service
enable_unit duo2-charge-hold.service
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/duo2-charge-hold on, /usr/local/sbin/duo2-charge-hold off" >"$rule"
visudo -cqf "$rule"
install -m 0440 "$rule" /etc/sudoers.d/duo2-charge-hold
rm -f "$rule"

install -m 0755 duo2-pen-charger /usr/local/sbin/duo2-pen-charger
install -m 0644 duo2-pen-charger.service /etc/systemd/system/duo2-pen-charger.service
enable_unit duo2-pen-charger.service

install -m 0755 duo2-torch-off /usr/local/sbin/duo2-torch-off
install -D -m 0755 duo2-torch-off /usr/lib/systemd/system-sleep/duo2-torch
install -m 0644 duo2-torch-off.service /etc/systemd/system/duo2-torch-off.service
enable_unit duo2-torch-off.service
install -D -m 0644 91-duo2-torch.rules /etc/udev/rules.d/91-duo2-torch.rules

install -m 0755 duo2-rtc-time /usr/local/sbin/duo2-rtc-time
install -m 0644 duo2-rtc-time.service duo2-rtc-time-save.service duo2-rtc-time-save.path \
    duo2-rtc-time-save.timer /etc/systemd/system/
install -D -m 0644 60-duo2-rtc.rules /etc/udev/rules.d/60-duo2-rtc.rules
for u in duo2-rtc-time.service:sysinit.target duo2-rtc-time-save.path:paths.target \
         duo2-rtc-time-save.timer:timers.target; do
    systemctl enable "${u%%:*}" >/dev/null 2>&1 ||
        { install -d "/etc/systemd/system/${u#*:}.wants" &&
          ln -sf "/etc/systemd/system/${u%%:*}" "/etc/systemd/system/${u#*:}.wants/"; }
done
