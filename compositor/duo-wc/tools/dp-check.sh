#!/bin/sh
# The external display on the USB-C port (DisplayPort alt mode, docs/hardware/display.md): what the kernel and duo-wc see, and a hotplug test that
# needs no hand at the cable.
#
# Runs ON THE PHONE as the admin user (sudo for debugfs, the journal and the
# connector's status file):
#
#     sh dp-check.sh OUTDIR snap NAME          what is there now (read only)
#     sh dp-check.sh OUTDIR cycle N [WAIT_S]   N software unplug / replug cycles
#     sh dp-check.sh OUTDIR poses [WAIT_S]     the panels' CTL hand-over with the
#                                              display up: dual -> single DSI-1
#                                              -> (display off, on) -> dual
#
# snap: the Type-C port (roles, orientation, the partner and its alt modes),
# GPIO 26 (aux-en) and 81 (the plug orientation), the DP connector (status,
# modes), the DRM state's resource mapping (which CRTC holds which CTL,
# mixers, DSCs), duoctl get_outputs, and the kernel's DP / alt mode lines
# since the boot. Nothing is written.
#
# cycle: writes "off" then "detect" to /sys/class/drm/card0-DP-1/status (the
# DRM core's forced status: the connector reads disconnected, duo-wc takes
# the display down; then it is probed again and comes back), a snap after
# each, then the kernel's and duo-wc's journal since the start. The cable and
# the alt mode stay as they are. Leaves the connector on "detect".
#
# poses: the CTL / mixer hand-over of docs/hardware/display.md
# with the display up: one panel off, the display down and up again (it
# then takes the free CTL and mixers), both panels on again.
set -eu
out=${1:?usage: dp-check.sh OUTDIR snap NAME | cycle N [WAIT_S] | poses [WAIT_S]}
what=${2:?snap, cycle or poses}
dri=/sys/kernel/debug/dri/0
conn=/sys/class/drm/card0-DP-1
mkdir -p "$out"
t0=$(date +%s)

snap() {
    f="$out/$1.txt"
    {
        echo "== $(date +%T.%N | cut -c1-12) $1"
        echo "-- typec"
        for p in /sys/class/typec/port0 /sys/class/typec/port0-partner; do
            [ -d "$p" ] || continue
            for a in data_role power_role orientation accessory_mode usb_power_delivery_revision; do
                [ -r "$p/$a" ] && printf '%s/%s: %s\n' "${p##*/}" "$a" "$(cat "$p/$a" 2>/dev/null)"
            done
        done
        for m in /sys/class/typec/port0-partner/port0-partner.*; do
            [ -d "$m" ] || continue
            printf '%s: svid %s mode %s active %s\n' "${m##*/}" "$(cat "$m/svid" 2>/dev/null)" \
                "$(cat "$m/mode" 2>/dev/null)" "$(cat "$m/active" 2>/dev/null)"
        done
        echo "-- gpio"
        sudo grep -E 'gpio(26|81) ' /sys/kernel/debug/gpio || true
        echo "-- connector"
        if [ -d "$conn" ]; then
            printf 'status %s enabled %s dpms %s\n' "$(cat $conn/status)" "$(cat $conn/enabled)" "$(cat $conn/dpms)"
            sed 's/^/mode /' "$conn/modes"
        else
            echo "no DP-1 connector (not the DP device tree?)"
        fi
        echo "-- drm state"
        # shellcheck disable=SC2024 # the redirect is meant for the calling user
        sudo cat "$dri/state" > "$out/$1-state.txt"
        grep -E '^crtc\[|^	(enable|active)=|^connector\[|^	crtc=' "$out/$1-state.txt" || true
        grep -A8 '^resource mapping' "$out/$1-state.txt" | tr -d '\t' || true
        echo "-- duo-wc"
        duoctl get_outputs 2>&1 || true
    } > "$f" 2>&1
    grep -E '^status|ctl=|mixer=' "$f" | tr '\n' ' '
    echo
}

journals() {
    # shellcheck disable=SC2024 # the redirect is meant for the calling user
    sudo journalctl -k -o short-precise --since "@$t0" > "$out/kernel.txt"
    # shellcheck disable=SC2024 # the redirect is meant for the calling user
    sudo journalctl -u duo-wc -o short-precise --since "@$t0" > "$out/duo-wc.txt"
    printf 'kernel lines: %s, DP/DPU/DSI errors: %s; duo-wc display lines: %s\n' \
        "$(wc -l < "$out/kernel.txt")" \
        "$(grep -ciE 'msm_dp|dp_ctrl|dp_aux|link train|dpu error|_dpu_encoder|dsi_err_worker|frame done timeout' "$out/kernel.txt" || true)" \
        "$(grep -cE 'display (dis)?connected|scan-out gone|window back from the display' "$out/duo-wc.txt" || true)"
}

force() {
    echo "$(date +%T.%N | cut -c1-12) DP-1 status <- $1"
    echo "$1" | sudo tee "$conn/status" > /dev/null
}

case $what in
snap)
    snap "${3:?snap NAME}"
    sudo journalctl -k -b -o short-precise | grep -iE 'altmode|ucsi|pmic_glink|qmp|typec|aux_bridge|hpd|msm_dp|dp_|displayport' \
        > "$out/${3}-kernel-dp.txt" || true
    echo "kernel DP / alt mode lines this boot: $(wc -l < "$out/${3}-kernel-dp.txt")"
    ;;
cycle)
    n=${3:?cycle N}
    wait_s=${4:-4}
    [ -d "$conn" ] || { echo "no DP-1 connector" >&2; exit 1; }
    snap 0-start
    i=1
    while [ "$i" -le "$n" ]; do
        force off
        sleep "$wait_s"
        snap "$i-off"
        force detect
        sleep "$wait_s"
        snap "$i-on"
        i=$((i + 1))
    done
    journals
    ;;
poses)
    wait_s=${3:-3}
    [ -d "$conn" ] || { echo "no DP-1 connector" >&2; exit 1; }
    snap 0-dual
    duoctl set_pose single DSI-1 > /dev/null; sleep "$wait_s"; snap 1-left
    force off; sleep "$wait_s"; snap 2-left-display-off
    force detect; sleep "$wait_s"; snap 3-left-display-on
    duoctl set_pose dual > /dev/null; sleep "$wait_s"; snap 4-dual
    duoctl set_pose single DSI-2 > /dev/null; sleep "$wait_s"; snap 5-right
    duoctl set_pose dual > /dev/null; sleep "$wait_s"; snap 6-dual
    journals
    ;;
*)
    echo "snap, cycle or poses, not $what" >&2
    exit 2
    ;;
esac
