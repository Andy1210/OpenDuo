#!/bin/bash
# Run this checkout's power daemons on the phone for the RUNNING BOOT only:
# duo2-autosleep, duo2-try-netwatch, duo2-push-poke and tools/notif-log go to
# /run/duo2/bin (tmpfs, root's), and systemd runtime drop-ins
# (/run/systemd/system/<unit>.d/zz-runtime.conf) point the two services at
# them. Nothing is installed: the next boot (after a duo2-try, the known-good
# one) runs the installed versions again. For a try boot that tests new
# daemons, a WoWLAN or a measurement series (docs/hardware/power.md); install.sh is the real installation.
#   power/stage-runtime.sh          stage them and restart the services
#   power/stage-runtime.sh --undo   back to the installed ones (this boot)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../scripts/lib/remote.sh"
duo2_resolve
B=/run/duo2/bin
D=/run/systemd/system
UNITS="duo2-autosleep duo2-try-netwatch"
if [ "${1:-}" = --undo ]; then
    duo2_ssh "sudo sh -c 'set -e; for u in $UNITS; do rm -f $D/\$u.service.d/zz-runtime.conf; rmdir $D/\$u.service.d 2>/dev/null || true; done; rm -rf $B; systemctl daemon-reload; systemctl restart duo2-autosleep; systemctl try-restart duo2-try-netwatch'"
    echo "the installed daemons run again (this boot)"
    exit 0
fi
[ $# -eq 0 ] || { echo "usage: $0 [--undo]" >&2; exit 2; }
tar -C "$here" -cf - duo2-autosleep duo2-try-netwatch duo2-push-poke tools/notif-log |
    duo2_ssh "sudo sh -c 'set -e
rm -rf $B.new; mkdir -p $B.new
tar -C $B.new --no-same-owner -xf -
mv $B.new/tools/notif-log $B.new/notif-log; rmdir $B.new/tools
chown -R root:root $B.new; chmod 0755 $B.new $B.new/*
rm -rf $B; mv $B.new $B
for u in $UNITS; do
    mkdir -p $D/\$u.service.d
    printf \"[Service]\\nExecStart=\\nExecStart=$B/%s\\n\" \$u > $D/\$u.service.d/zz-runtime.conf
done
systemctl daemon-reload
systemctl restart duo2-autosleep
systemctl try-restart duo2-try-netwatch
for u in $UNITS; do echo \"\$u: \$(systemctl show -p ExecStart --value \$u | grep -o \"path=[^ ;]*\") \$(systemctl is-active \$u)\"; done'"
echo "staged in $B for this boot (power/stage-runtime.sh --undo, or any reboot, ends it)"
