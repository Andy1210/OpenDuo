# shellcheck shell=bash
# Shared by the scripts that drive a running duo2-os phone over the network.
# Source it; it defines R, duo2_ssh, duo2_scp, duo2_stage, duo2_user,
# duo2_session, duo2_wait_up.
#
# Where the phone is comes from local.env in the repository root (never
# committed: addresses and user names are personal; local.env.example), for
# example:
#
#   DUO2_HOST=duo2admin@192.0.2.50       # ssh destination (WiFi or USB NCM): the
#                                        # ADMINISTRATOR (passwordless sudo, no GUI)
#   DUO2_USER=duo                        # the GUI user (duo-wc's; no sudo; default:
#                                        # scripts/lib/defaults.sh)
#   DUO2_MAC=00:11:22:33:44:55           # optional: find the IP by MAC (ARP); wlan0 has
#                                        # the factory MAC (duo2-factory-addr; before it,
#                                        # ath11k picked a random one each boot)
#   DUO2_SSH_OPTS="-o UserKnownHostsFile=$R/out/known_hosts"
#
# With DUO2_MAC set, the address in DUO2_HOST is replaced by whatever the
# neighbour table says the MAC has now (DHCP may hand out another one).
#
# Who does what (docs/hardware/security.md): everything over ssh runs as the
# administrator, root work with sudo; duoctl as the administrator reaches
# duo-wc's admin socket (every request allowed). What has to run as the GUI
# user goes through duo2_user (its systemd user manager and D-Bus: systemctl
# --user, gsettings, flatpak --user) or duo2_session (inside duo-wc's session
# as well: starting an app on the panels).

R=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
[ -f "$R/local.env" ] && . "$R/local.env"
: "${DUO2_HOST:?set DUO2_HOST in $R/local.env (admin-user@address of the phone)}"
DUO2_SSH_OPTS=${DUO2_SSH_OPTS:-}
# shellcheck source=defaults.sh
. "$R/scripts/lib/defaults.sh"
DUO2_USER=${DUO2_USER:-$DUO2_USER_DEFAULT}
if [ "${DUO2_HOST%@*}" = "$DUO2_USER" ]; then
    echo "remote.sh: DUO2_HOST logs in as the GUI user $DUO2_USER, who has no sudo;" \
        "use the administrator (duo2admin@...) in $R/local.env" >&2
fi

duo2_resolve() {
    [ -n "${DUO2_MAC:-}" ] || return 0
    local user=${DUO2_HOST%@*} ip
    ip=$(ip neigh show | awk -v m="${DUO2_MAC,,}" 'tolower($5) == m && $1 ~ /\./ { print $1; exit }')
    [ -n "$ip" ] && DUO2_HOST=$user@$ip
    return 0
}

duo2_ssh() {
    # shellcheck disable=SC2086
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
        $DUO2_SSH_OPTS "$DUO2_HOST" "$@"
}

# duo2_esp_ssh 'commands': run commands that write the ESP. The ESP is
# mounted read-only (fstab, so a hard reset cannot leave its FAT dirty):
# remount it read-write for them and read-only again afterwards, also when
# they fail (/usr/local/sbin/duo2-esp; a phone without it has the ESP rw).
duo2_esp_ssh() {
    duo2_ssh "if [ -x /usr/local/sbin/duo2-esp ]; then sudo /usr/local/sbin/duo2-esp rw; trap 'sudo /usr/local/sbin/duo2-esp ro' EXIT; fi
$1"
}

# duo2_user 'commands': run shell commands on the phone as the GUI user
# ($DUO2_USER), with its systemd user manager and D-Bus session bus
# (XDG_RUNTIME_DIR=/run/user/UID), in its home directory. The commands come
# on the remote shell's stdin, so they need no quoting beyond the caller's.
duo2_user() {
    duo2_ssh "sudo -n -u $DUO2_USER -- sh -c 'cd && export XDG_RUNTIME_DIR=/run/user/\$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/\$(id -u)/bus && exec sh -s'" <<EOF
$1
EOF
}

# duo2_session 'commands': like duo2_user, inside duo-wc's session as well
# (/run/duo-wc/session.env: WAYLAND_DISPLAY, the bus, the audio sockets,
# the language): an app started here opens on the panels. Wayland clients
# started this way are ordinary apps: no virtual keyboard, input method,
# lock or layer shell (those are the session's own, see duoctl exec-trusted).
duo2_session() {
    duo2_user ". /run/duo-wc/session.env
$1"
}

duo2_scp() {
    # shellcheck disable=SC2086
    scp -q -o BatchMode=yes -o ConnectTimeout=10 $DUO2_SSH_OPTS "$@"
}

# duo2_stage NAME: print the staging path an installer copies its files to
# on the phone before sudo runs them: .duo2-NAME-install in the
# administrator's home (absolute, so a remote script may cd elsewhere),
# removed if an earlier install left it; the caller creates it (mkdir -p, or
# scp -r makes it). Never a fixed name in /tmp: any app of the GUI user could
# create that directory first, with its own install-files.sh in it, and the
# next install would run it as root.
duo2_stage() {
    local home
    home=$(duo2_ssh 'printf %s "$HOME"')
    case $home in
        /?*) ;;
        *) echo "duo2_stage: no home directory for ${DUO2_HOST%@*} on the phone" >&2; return 1 ;;
    esac
    duo2_ssh "rm -rf '$home/.duo2-$1-install'" >&2
    printf '%s/.duo2-%s-install\n' "$home" "$1"
}

# duo2_reboot: reboot the phone. After an oops (taint flag D, 128) a driver
# may have died holding a device lock, and the orderly shutdown then waits for
# it forever (seen 2026-11-06 after qcom_wdt faulted in probe): sync and reboot
# at once with SysRq b instead. Only cold resets work on this phone; never
# "reboot=warm" (the WOA UEFI ResetSystem faults, PSCI returns: system halted).
duo2_reboot() {
    duo2_ssh 'sync
t=$(cat /proc/sys/kernel/tainted)
if [ $((t & 128)) -ne 0 ]; then
    echo "kernel died in an oops (tainted $t): SysRq reboot" >&2
    echo 1 | sudo tee /proc/sys/kernel/sysrq >/dev/null
    (sleep 1; echo b | sudo tee /proc/sysrq-trigger >/dev/null) >/dev/null 2>&1 &
else
    sudo systemctl reboot
fi' >/dev/null 2>&1 || true
}

# duo2_wait_up SECONDS: wait for a NEW boot (boot_id differs from $1's
# caller-supplied OLD_BOOT_ID) to answer on ssh; prints the elapsed time.
duo2_wait_up() {
    local limit=$1 t0 now id
    t0=$(date +%s)
    sleep 20
    while now=$(date +%s); [ $((now - t0)) -lt "$limit" ]; do
        duo2_resolve
        # shellcheck disable=SC2086 # DUO2_SSH_OPTS: several options
        id=$(timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=5 $DUO2_SSH_OPTS "$DUO2_HOST" \
            cat /proc/sys/kernel/random/boot_id 2>/dev/null) || id=
        if [ -n "$id" ] && [ "$id" != "${OLD_BOOT_ID:-}" ]; then
            echo "up again after $((now - t0)) s"
            return 0
        fi
        sleep 5
    done
    echo "no new boot answered within $limit s" >&2
    return 1
}

duo2_resolve
