#!/usr/bin/env bash
# Close the QRTR hole on a running phone (docs/hardware/security.md, "QRTR"):
#
#   rootfs/overlay/etc/systemd/system/duo-wc.service.d/20-duo2-no-qrtr.conf
#   rootfs/overlay/etc/systemd/system/user@.service.d/20-duo2-no-qrtr.conf
#                       RestrictAddressFamilies=~AF_QIPCRTR: no program of the
#                       GUI session or of a user manager opens a QRTR socket
#                       (seccomp, inherited across sudo / setuid exec)
#   rootfs/overlay/etc/ssh/sshd_config.d/duo2.conf
#                       the GUI user's ssh keys are root's file
#                       (/etc/ssh/authorized_keys.d/$DUO2_USER): otherwise an app
#                       writes ~/.ssh/authorized_keys and logs in over ssh,
#                       outside the filter. Checked with `sshd -t` and `sshd -T`
#                       (the administrator's effective config must not change,
#                       the GUI user's only in AuthorizedKeysFile) before ssh reloads;
#                       the running connections stay.
#   telephony/duo2-esim lpac runs as a service PID 1 starts (systemd-run), not
#                       as a child of the filtered helper
#
# Then the user manager and duo-wc restart (the session, the shell and the
# apps come back; a lock stays in duo-wc's fd store), unless --no-restart.
#
#   scripts/install-no-qrtr.sh [--no-restart]
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
restart=1
[ "${1:-}" = --no-restart ] && restart=

O=$R/rootfs/overlay
files=(etc/systemd/system/duo-wc.service.d/20-duo2-no-qrtr.conf
       etc/systemd/system/user@.service.d/20-duo2-no-qrtr.conf
       etc/ssh/sshd_config.d/duo2.conf)
tmp=$(duo2_ssh 'mktemp -d')
(cd "$O" && tar cf - "${files[@]}") | duo2_ssh "tar xf - -C $tmp"
duo2_scp "$R/telephony/duo2-esim" "$DUO2_HOST:$tmp/duo2-esim"

duo2_ssh "sudo bash -s $tmp $DUO2_USER $restart" <<'EOF'
set -eu
t=$1 u=$2 restart=${3:-}
uid=$(id -u "$u")

# ssh: the GUI user's keys from its own file into root's, once (the same keys as the
# administrator's on this phone: compared, not printed)
install -d -m 0755 /etc/ssh/authorized_keys.d
keys=/etc/ssh/authorized_keys.d/$u
if [ ! -e "$keys" ]; then
    src=/home/$u/.ssh/authorized_keys
    if [ -s "$src" ] && ! cmp -s "$src" /home/duo2admin/.ssh/authorized_keys; then
        echo "note: $u's ssh keys differ from the administrator's; taking $u's own" >&2
    fi
    if [ -s "$src" ]; then
        install -m 0644 -o root -g root "$src" "$keys"
    else
        install -m 0644 -o root -g root /home/duo2admin/.ssh/authorized_keys "$keys"
    fi
fi
conf=/etc/ssh/sshd_config.d/duo2.conf
eff() { sshd -T -C "user=$1,host=localhost,addr=127.0.0.1" 2>/dev/null | sort; }
admin_before=$(eff duo2admin)
user_before=$(eff "$u")
cp -p "$conf" "$t/duo2.conf.old"
install -m 0644 -o root -g root "$t/etc/ssh/sshd_config.d/duo2.conf" "$conf"
ok=1
sshd -t || ok=
[ -z "$ok" ] || [ "$(eff duo2admin)" = "$admin_before" ] || { echo "sshd: the administrator's config would change" >&2; ok=; }
if [ -n "$ok" ]; then
    d=$(diff <(echo "$user_before") <(eff "$u") | grep '^[<>]' | grep -v ' authorizedkeysfile ' || true)
    [ -z "$d" ] || { echo "sshd: $u's config would change beyond AuthorizedKeysFile: $d" >&2; ok=; }
    # (sshd -T prints the pattern, %u unexpanded)
    eff "$u" | grep -qx "authorizedkeysfile /etc/ssh/authorized_keys.d/%u" || { echo "sshd: $u's keys not from $keys" >&2; ok=; }
fi
if [ -z "$ok" ]; then
    install -m 0644 -o root -g root "$t/duo2.conf.old" "$conf"
    echo "sshd config NOT changed (restored)" >&2
    exit 1
fi
systemctl reload ssh.service
echo "ssh: $u's keys from $keys"

# the filter
for f in duo-wc.service.d/20-duo2-no-qrtr.conf user@.service.d/20-duo2-no-qrtr.conf; do
    install -D -m 0644 -o root -g root "$t/etc/systemd/system/$f" "/etc/systemd/system/$f"
done
install -m 0755 -o root -g root "$t/duo2-esim" /usr/local/sbin/duo2-esim
systemctl daemon-reload
rm -rf "$t"

if [ -n "$restart" ]; then
    systemctl restart "user@$uid.service"
    systemctl restart duo-wc.service
fi
for unit in duo-wc.service "user@$uid.service"; do
    printf '%s: %s %s\n' "$unit" "$(systemctl is-active "$unit")" \
        "$(systemctl show -P RestrictAddressFamilies "$unit")"
done
EOF
