#!/usr/bin/env bash
# Stage OpenDuo's own components the way they are installed: one directory per
# component, holding exactly what that component's install-files.sh reads.
#
#   scripts/lib/stage-components.sh OUTDIR [COMPONENT...]
#
# Two users, one list (keep it so): scripts/build-images.sh stages every
# image component into the image's build context (rootfs/install-components.sh
# runs their install-files.sh under qemu), and packaging/build-packages.sh
# stages each package's component into the package (its postinst runs the same
# install-files.sh on the phone: docs/UPDATES.md).
#
# The built parts are taken as they are (rebuild them first after a source
# change): duo-wc (compositor/duo-wc/build-aarch64.sh), the free half of the
# sensor stack (scripts/build-sensors.sh), lpac (telephony/lpac/build.sh),
# Nibfair (apps/nibfair/build-aarch64.sh), duo2-touchd (touch/build-touchd.sh).
# A component whose built part is missing is an error, except duo2-touchd: the
# touch component is staged without it (the runtime from the owner's phone
# brings its own).
#
# Image components (no argument: all of these): duo-wc duo-shell duo-apps
# duo-settings duo-screencast battery power telephony plymouth android maint
# hotspot firewall homecrypt devmode sensors stock. Package-only components
# (named explicitly): touch nibfair.
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
O=$R/out
OUT=${1:?usage: stage-components.sh OUTDIR [COMPONENT...]}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
shift
IMAGE_COMPONENTS="duo-wc duo-shell duo-apps duo-settings duo-screencast battery power telephony plymouth
android maint hotspot firewall homecrypt devmode sensors stock"
# shellcheck disable=SC2086  # the list is split into words on purpose
[ $# -gt 0 ] || set -- $IMAGE_COMPONENTS
mkdir -p "$OUT"

need() { # a built file a component needs
    [ -e "$1" ] || { echo "stage-components: missing $1 (build it first: $2)" >&2; exit 1; }
}

# A component's directory, copied whole (its tests and caches left out).
tree() { # source destination
    mkdir -p "$2"
    tar -C "$1" --exclude=tests --exclude=__pycache__ --exclude='*.pyc' --exclude=out --exclude=target \
        --exclude=.ruff_cache --exclude=.pytest_cache --exclude=.mypy_cache \
        -cf - . | tar -C "$2" -xf -
}

for c in "$@"; do
    d=$OUT/$c
    rm -rf "$d"
    case $c in
    duo-wc)
        W=$R/compositor/duo-wc
        need "$W/out/duo-wc" compositor/duo-wc/build-aarch64.sh
        mkdir -p "$d"
        cp -a "$W/out/duo-wc" "$W"/dist/* "$W/tools/touch.py" "$W/tools/key.py" "$W/tools/toplevels.py" \
            "$W/install-files.sh" "$d/"
        ;;
    duo-shell)
        S=$R/shell/duo-shell
        mkdir -p "$d/duoshell"
        cp -a "$S/duo-shell" "$S/duo-shell-helper" "$S/duo-lock" "$S/duo-lock-helper" "$S/duo-wallpaper-bing" \
            "$S/duo-clipboard-bridge" "$S/90_duo2-os.gschema.override" "$S/systemd" \
            "$S/portals.conf" "$S/org.duo2os.Shell.conf" "$S/lock/gen-protocols.py" "$S/install-files.sh" "$d/"
        cp -a "$S"/duoshell/*.py "$S"/duoshell/*.css "$d/duoshell/"
        cp -a "$S/icons" "$d/"
        ;;
    duo-apps | duo-settings | duo-screencast)
        tree "$R/shell/$c" "$d"
        [ "$c" != duo-settings ] || cp -a "$R/compositor/duo-wc/tools/touch-calib.py" "$d/"
        ;;
    battery | power | android | maint | hotspot | firewall | homecrypt)
        tree "$R/$c" "$d"
        ;;
    telephony)
        need "$O/lpac/bin/lpac" telephony/lpac/build.sh
        tree "$R/telephony" "$d"
        # duo2-esim's fake lpac is installed (tests/ is left out above)
        mkdir -p "$d/tests"
        cp -a "$R/telephony/tests/fake_lpac.py" "$d/tests/"
        cp -a "$O/lpac" "$d/lpac-bin"
        ;;
    plymouth)
        tree "$R/rootfs/plymouth" "$d"
        ;;
    devmode)
        tree "$R/devmode" "$d"
        cp -a "$R/scripts/check-personal-data" "$d/check_personal_data.py"
        cp -a "$R/telephony/debug/imsd-logger" "$d/imsd-logger"
        ;;
    sensors)
        need "$O/duo2-sensors-bin/usr/local/bin/hexagonrpcd" scripts/build-sensors.sh
        mkdir -p "$d/bin"
        cp -a "$R"/sensors/duo-posed/{duo-posed,duo2-ssc,duo2_ssc.py,posepolicy.py} "$R"/sensors/dist/* "$d/"
        cp -a "$R"/sensors/duo-brightness/{duo-brightness,brightnesspolicy.py} "$d/"
        cp -a "$R/sensors/install-files.sh" "$d/"
        cp -a "$O/duo2-sensors-bin/." "$d/bin/"
        ;;
    touch)
        # Our part of the touch stack: the daemon (built from touch/ with the
        # NDK), the pen and touch tools, and the image's touch files (the
        # overlay is their source). The runtime it runs in (/opt/duo2-touch:
        # Microsoft's heat processor and the stock libraries) is never staged.
        V=$R/rootfs/overlay
        mkdir -p "$d/tools" "$d/files"
        cp -a "$R/touch/install-files.sh" "$d/"
        for f in "$R"/touch/tools/*.py "$R"/touch/tools/*.sh; do
            [ ! -e "$f" ] || cp -a "$f" "$d/tools/"
        done
        (cd "$V" && cp -a --parents etc/systemd/system/duo2-touchd.service usr/lib/udev/rules.d/90-duo2-touch.rules \
            usr/lib/systemd/system-sleep/duo2-touchd usr/local/sbin/duo2-touch-calibration \
            etc/duo2/touch-calibration etc/duo2/touch-mode etc/duo2/pen.conf \
            usr/share/libinput/90-duo2-pen.quirks usr/local/sbin/duo2-pend etc/systemd/system/duo2-pend.service \
            "$d/files/")
        if [ -x "$O/touchd/duo2-touchd" ]; then
            cp -a "$O/touchd/duo2-touchd" "$d/duo2-touchd"
            # the liblog shim the runtime loads beside it (the same build)
            [ ! -f "$O/touchd/liblog-stderr.so" ] || cp -a "$O/touchd/liblog-stderr.so" "$d/liblog-stderr.so"
        else
            echo "stage-components: no out/touchd/duo2-touchd (touch/build-touchd.sh): touch staged without the daemon" >&2
        fi
        ;;
    stock)
        # duo2-stock-import and the host recipes it runs on the phone
        tree "$R/stock" "$d"
        mkdir -p "$d/scripts"
        cp -a "$R/scripts/collect-firmware.sh" "$R/scripts/mdt2mbn.py" "$R/scripts/camx-sensor-tables.py" \
            "$R/scripts/camx-module-dump.py" "$R/scripts/axml2xml.py" "$d/scripts/"
        ;;
    nibfair)
        need "$R/apps/nibfair/out/nibfair" apps/nibfair/build-aarch64.sh
        mkdir -p "$d"
        cp -a "$R/apps/nibfair/out/nibfair" "$R/apps/nibfair/org.duo2os.Nibfair.desktop" \
            "$R/apps/nibfair/org.duo2os.Nibfair.svg" "$R/apps/nibfair/install-files.sh" "$d/"
        ;;
    *)
        echo "stage-components: unknown component $c" >&2
        exit 2
        ;;
    esac
done
# Git keeps only the executable bit: the files' other mode bits come from the
# checkout's umask (0002 here: group-writable root files in /etc). What is
# installed takes the repository's modes, not the build host's.
chmod -R go-w "$OUT"
