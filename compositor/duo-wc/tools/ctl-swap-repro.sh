#!/bin/sh
# The two panels handing a DPU CTL to each other (2026-11-08): dual -> single DSI-1 -> single DSI-2 -> dual, one
# `duoctl set_pose` a step. The msm kernel gives each modeset the lowest free
# CTL, so DSI-2 takes CTL0 from DSI-1 and DSI-1 then takes CTL1, the one
# DSI-2 had. Before the kernel fix ("drm/msm/dpu: Clear a CTL's interface
# paths before a display sets it up") both CTLs end up driving both
# interfaces and the panels hang on the last step.
#
# Runs ON THE PHONE as the admin user (sudo for debugfs and the journal):
#
#     sh ctl-swap-repro.sh OUTDIR [WAIT_S]
#
# After each step (WAIT_S, default 2 s) it saves the DRM state (its "resource
# mapping" says which CRTC owns which CTL), and while BOTH connectors are on
# the display register snapshot (debugfs kms; never with a panel off: it reads
# the DSI PHYs without powering them). At the end the kernel's and duo-wc's
# journal since the start. Apart from the three poses it changes nothing.
set -eu
out=${1:?usage: ctl-swap-repro.sh OUTDIR [WAIT_S]}
wait_s=${2:-2}
dri=/sys/kernel/debug/dri/0
mkdir -p "$out"
t0=$(date +%s)

both_on() {
    [ "$(cat /sys/class/drm/card0-DSI-1/dpms)" = On ] &&
        [ "$(cat /sys/class/drm/card0-DSI-2/dpms)" = On ]
}

snap() {
    # shellcheck disable=SC2024 # the redirect is meant for the calling user
    sudo cat "$dri/state" > "$out/$1-state.txt"
    printf '%s: dpms DSI-1 %s DSI-2 %s; ' "$1" \
        "$(cat /sys/class/drm/card0-DSI-1/dpms)" "$(cat /sys/class/drm/card0-DSI-2/dpms)"
    { grep -m1 -A3 '^resource mapping' "$out/$1-state.txt" | grep -E 'ctl=' | tr -d '\t'; } ||
        echo "no resource mapping"
    if both_on; then
        # shellcheck disable=SC2024 # the redirect is meant for the calling user
        sudo cat "$dri/kms" > "$out/$1-kms.txt"
    else
        echo "$1: a connector is off, no register snapshot"
    fi
}

step() {
    echo "$(date +%T.%N | cut -c1-12) duoctl set_pose $*"
    duoctl set_pose "$@" > /dev/null
    sleep "$wait_s"
}

duoctl get_state > "$out/wc-state.json" 2>&1 || true
grep -o '"atomic_wake": *[a-z]*' "$out/wc-state.json" || true
snap 0-dual
step single DSI-1
snap 1-left
step single DSI-2
snap 2-right
step dual
snap 3-dual
sleep 2
snap 4-dual-later

# shellcheck disable=SC2024 # the redirect is meant for the calling user
sudo journalctl -k -o short-precise --since "@$t0" > "$out/kernel.txt"
# shellcheck disable=SC2024 # the redirect is meant for the calling user
sudo journalctl -u duo-wc -o short-precise --since "@$t0" > "$out/duo-wc.txt"
printf 'kernel lines since the start: %s, DPU/DSI errors: %s\n' \
    "$(wc -l < "$out/kernel.txt")" \
    "$(grep -cE 'dpu error|_dpu_encoder|dsi_err_worker|wait_for_idle' "$out/kernel.txt" || true)"
