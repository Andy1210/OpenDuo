#!/bin/bash
# Panel power on the phone, measured: battery current per panel state, and
# repeated off/on cycles of each panel with the kernel log checked afterwards.
#
#   tools/power-test.sh current [SETTLE_S] [SAMPLES]   battery current: both lit,
#                                                      DSI-2 off, DSI-1 off, both off
#   tools/power-test.sh cycles [N] [PANEL...]          N off/on cycles per panel
#                                                      (default 20, DSI-1 DSI-2)
#
# Runs against the duo-wc that is running (duo-wc.service). The idle timeout is
# set to 0 for the run and back to the config's value at the end. The modem
# remoteproc is stopped first, as scripts/duo2-power does (it crash-loops every
# few minutes and makes the current jump); the battery current is only
# comparable with the same USB supply (the Pi gives ~100 mA SDP).
#
# What "came back" means for one cycle: the CRTC is active again in DRM debugfs
# (/sys/kernel/debug/dri/0/state), the output's page-flip counter
# (frames_presented) moved past its value before the switch-off, and duo-wc
# logged "panel on: first frame on screen" (the time it took is printed). At the
# end every kernel line since the start that is not spi_hid chatter is printed:
# DSI / DPU errors (frame done timeouts, underruns) would show there.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../../scripts/lib/remote.sh"

mode=${1:-}
shift || true

# shellcheck disable=SC2016 # the script text is expanded by the shell it is sent to
common='
set -u
B=/sys/class/power_supply/qcom-battmgr-bat
for r in /sys/class/remoteproc/remoteproc*; do
    [ "$(cat $r/name)" = modem ] && [ "$(cat $r/state)" = running ] &&
        echo stop | sudo tee $r/state >/dev/null && echo "(modem remoteproc stopped)"
done
conf_idle=$(sed -n "s/^[[:space:]]*idle_timeout[[:space:]]*=[[:space:]]*\([0-9]*\).*/\1/p" /etc/duo-wc/duo-wc.conf 2>/dev/null)
conf_idle=${conf_idle:-60}
duoctl set_idle_timeout 0 >/dev/null
trap "duoctl set_power all on >/dev/null; duoctl set_idle_timeout $conf_idle >/dev/null" EXIT
start=$(date "+%Y-%m-%d %H:%M:%S")
crtc_of() { duoctl get_outputs | python3 -c "import json,sys; print([o[\"crtc\"] for o in json.load(sys.stdin)[\"ok\"][\"outputs\"] if o[\"name\"]==\"$1\"][0])"; }
presented() { duoctl get_outputs | python3 -c "import json,sys; print([o[\"frames_presented\"] for o in json.load(sys.stdin)[\"ok\"][\"outputs\"] if o[\"name\"]==\"$1\"][0])"; }
active() { sudo cat /sys/kernel/debug/dri/0/state | awk -v c="crtc[$1]:" "\$1==c {f=1; next} f && /active=/ {sub(/.*active=/,\"\"); print; exit}"; }
kernel_log() {
    echo "--- kernel log since $start (spi_hid filtered):"
    sudo journalctl -k --since "$start" --no-pager -o short-monotonic | grep -v spi_hid | grep -v "^-- " || echo "(nothing)"
}
'

case "$mode" in
current)
    settle=${1:-20}
    samples=${2:-30}
    duo2_ssh "bash -s" <<EOF
$common
measure() {
    sleep $settle
    s=0; mn=0; mx=-99999999
    for k in \$(seq 1 $samples); do
        c=\$(cat \$B/current_now); s=\$((s + c))
        [ \$c -lt \$mn ] && mn=\$c; [ \$c -gt \$mx ] && mx=\$c
        sleep 1
    done
    printf "%-28s mean %5d mA  (min %d, max %d)  %s%%\n" "\$1" \$((s / $samples / 1000)) \$((mn / 1000)) \$((mx / 1000)) \$(cat \$B/capacity)
}
duoctl set_power all on >/dev/null;   measure "both panels lit"
duoctl set_power DSI-2 off >/dev/null; measure "DSI-2 off (DSI-1 lit)"
duoctl set_power all on >/dev/null; duoctl set_power DSI-1 off >/dev/null; measure "DSI-1 off (DSI-2 lit)"
duoctl set_power all off >/dev/null;  measure "both off (blanked)"
duoctl set_power all on >/dev/null;   measure "both lit again"
kernel_log
EOF
    ;;
cycles)
    n=${1:-20}
    shift || true
    panels=${*:-DSI-1 DSI-2}
    duo2_ssh "bash -s" <<EOF
$common
fail=0
for panel in $panels; do
    crtc=\$(crtc_of \$panel)
    ok=0
    for i in \$(seq 1 $n); do
        before=\$(presented \$panel)
        duoctl set_power \$panel off >/dev/null
        sleep 1
        off=\$(active \$crtc)
        duoctl set_power \$panel on >/dev/null
        sleep 1.5
        on=\$(active \$crtc)
        after=\$(presented \$panel)
        if [ "\$off" = 0 ] && [ "\$on" = 1 ] && [ "\$after" -gt "\$before" ]; then
            ok=\$((ok + 1))
        else
            fail=1
            echo "\$panel cycle \$i: crtc active off=\$off on=\$on, frames_presented \$before -> \$after"
        fi
    done
    echo "\$panel: \$ok of $n cycles came back (crtc \$crtc)"
done
echo "--- duo-wc: times from switch-on to the first frame on screen, and switch-off times (ms):"
sudo journalctl -u duo-wc --since "\$start" --no-pager -o cat | python3 -c '
import re, sys
on, off = {}, {}
for line in sys.stdin:
    m = re.search(r"panel on: first frame on screen output=\"(\S+)\" ms=(\d+)", line)
    if m: on.setdefault(m[1], []).append(int(m[2]))
    m = re.search(r"panel off output=\"(\S+)\" ms=(\d+)", line)
    if m: off.setdefault(m[1], []).append(int(m[2]))
for name in sorted(set(on) | set(off)):
    a, b = on.get(name, [0]), off.get(name, [0])
    print(f"{name}: on {len(on.get(name, []))}x min {min(a)} median {sorted(a)[len(a)//2]} max {max(a)};"
          f" off {len(off.get(name, []))}x min {min(b)} median {sorted(b)[len(b)//2]} max {max(b)}")
'
echo "--- duo-wc warnings since the start:"
sudo journalctl -u duo-wc --since "\$start" --no-pager -o cat | grep -E "WARN|ERROR" | grep -v "destroy old mode property blob" | cut -c1-200; true
kernel_log
exit \$fail
EOF
    ;;
*)
    sed -n '2,22p' "$0"
    exit 2
    ;;
esac
