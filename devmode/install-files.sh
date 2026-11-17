#!/bin/sh
# Put Developer mode in place, as root, on the phone (install.sh runs it there)
# or in the image (rootfs/install-components.sh under qemu): one list for both.
#
#   install-files.sh USER     (USER = the GUI user, who gets the sudoers rule;
#                             run from this directory, with check-personal-data
#                             staged next to it as check_personal_data.py, and
#                             telephony/debug/imsd-logger as imsd-logger)
#
# /etc/duo-devmode.conf is written only when there is none: the phone's own
# switches are never reset by an update. The image passes DEVELOPER_MODE:
# off (devmode.conf, all off) or on (an image for a developer's own phone:
# the switches on, sshd enabled). Without DEVELOPER_MODE (install.sh on a
# phone from before Developer mode) the file says what that phone already
# does: on, and sshd is left as it is.
#
# The developer tools (devtools.list) are put in their payload and installed
# only while Developer mode is on: the last step here, so run it after the
# components whose tools it lists (rootfs/install-components.sh does).
set -eu
U=$1
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
L=/usr/local/lib/duo-devmode
rm -rf "$L.new"
install -d "$L.new/duodevmode" "$L.new/bugreport.d"
install -m 0644 duodevmode/*.py "$L.new/duodevmode/"
install -m 0644 bugreport.d/*.conf "$L.new/bugreport.d/"
if [ -f check_personal_data.py ]; then
    install -m 0644 check_personal_data.py "$L.new/check_personal_data.py"
else
    install -m 0644 ../scripts/check-personal-data "$L.new/check_personal_data.py"
fi
rm -rf "$L.old"
[ -d "$L" ] && mv "$L" "$L.old"
mv "$L.new" "$L"
rm -rf "$L.old"
install -m 0755 duo-devmode /usr/local/sbin/duo-devmode
# devtools.list and the lists beside it (devtools-*.list), as one
{
    cat devtools.list
    for f in devtools-*.list; do
        [ ! -f "$f" ] || cat "$f"
    done
} >"$L/devtools.list"
chmod 0644 "$L/devtools.list"
install -m 0644 duo-devmode-sync.service /etc/systemd/system/duo-devmode-sync.service
systemctl enable duo-devmode-sync.service >/dev/null 2>&1 ||
    { install -d /etc/systemd/system/multi-user.target.wants &&
      ln -sf /etc/systemd/system/duo-devmode-sync.service /etc/systemd/system/multi-user.target.wants/; }
# The VoLTE bring-up logger: a developer tool like the others (devtools.list).
for f in imsd-logger ../telephony/debug/imsd-logger; do
    if [ -f "$f" ]; then
        install -m 0755 "$f" /usr/local/sbin/duo2-imsd-logger
        break
    fi
done

if [ ! -e /etc/duo-devmode.conf ]; then
    case ${DEVELOPER_MODE:-} in
    off)
        install -m 0644 devmode.conf /etc/duo-devmode.conf
        ;;
    on | "")
        sed 's/= off$/= on/' devmode.conf >/etc/duo-devmode.conf
        chmod 0644 /etc/duo-devmode.conf
        [ -z "${DEVELOPER_MODE:-}" ] || systemctl enable ssh.service >/dev/null 2>&1 || true
        ;;
    *)
        echo "DEVELOPER_MODE: on or off" >&2
        exit 2
        ;;
    esac
fi

# Root actions: exactly this tool, for the desktop user (it asks the user in
# the shell for what opens the phone).
rule=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/duo-devmode" >"$rule"
visudo -cqf "$rule"
install -m 0440 "$rule" /etc/sudoers.d/duo-devmode
rm -f "$rule"

# The developer tools follow the switch (and again at every boot).
/usr/local/sbin/duo-devmode devtools sync >/dev/null
