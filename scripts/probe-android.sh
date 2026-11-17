#!/usr/bin/env bash
# Read-only hardware probe of a Surface Duo 2 running stock Android, over adb.
# Needs no root and writes nothing to the phone: every command only reads.
#
#   scripts/probe-android.sh            # props, kernel, display, input, sensors,
#                                       # power, wifi, firmware listing, modules
#   scripts/probe-android.sh --firmware # also pulls the readable vendor firmware
#                                       # directories (adsp/cdsp/slpi/wlan/gpu ...)
#
# Output goes to dumps/<date>-<time>/ (gitignored: it holds the serial, the
# IMEI-bearing props, SSIDs and Microsoft/Qualcomm blobs; never commit it).
# Pick a device with ADB_SERIAL=<ip:port> when more than one is connected.
set -u

ADB=${ADB:-$(command -v adb || echo "$HOME/android-sdk/platform-tools/adb")}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/dumps/$(date +%Y%m%d-%H%M%S)"
PULL_FW=0
[ "${1:-}" = "--firmware" ] && PULL_FW=1

is_duo2() { # <serial>: true when that device is a Surface Duo 2
    local m
    m=$(timeout 20 "$ADB" -s "$1" shell 'getprop ro.product.model; getprop ro.product.device' 2>/dev/null | tr '\n' ' ')
    case "$m" in *"Duo 2"*|*duo2*|*zeta*) return 0 ;; *) return 1 ;; esac
}

# Other phones (other test devices) are often connected too: pick the Duo 2
# by itself and refuse to probe anything else.
if [ -z "${ADB_SERIAL:-}" ]; then
    for s in $("$ADB" devices | awk 'NR > 1 && $2 == "device" { print $1 }'); do
        if is_duo2 "$s"; then ADB_SERIAL=$s; break; fi
    done
fi
if [ -z "${ADB_SERIAL:-}" ] || ! is_duo2 "$ADB_SERIAL"; then
    echo "No Surface Duo 2 on adb. Pair it first (Wireless debugging > Pair device with pairing code):" >&2
    echo "  $ADB pair <ip>:<pair-port> <code>; $ADB connect <ip>:<port>" >&2
    "$ADB" devices -l >&2
    exit 1
fi
echo "Surface Duo 2: $ADB_SERIAL"
adbc() { "$ADB" -s "$ADB_SERIAL" "$@"; }
mkdir -p "$OUT"
echo "Writing to $OUT"

# run <file> <shell command>: one read-only shell command, 90 s cap, stderr kept.
run() {
    local f=$1; shift
    mkdir -p "$(dirname "$OUT/$f")"
    timeout 90 "$ADB" ${ADB_SERIAL:+-s "$ADB_SERIAL"} shell "$*" >"$OUT/$f" 2>&1
    printf '  %-34s %8s bytes\n' "$f" "$(wc -c <"$OUT/$f")"
}

echo "identity / boot state"
run props.txt                'getprop'
run kernel/uname.txt         'uname -a; cat /proc/version'
run kernel/cmdline.txt       'cat /proc/cmdline'
run kernel/cpuinfo.txt       'cat /proc/cpuinfo'
run kernel/meminfo.txt       'cat /proc/meminfo'
run kernel/soc0.txt          'for f in /sys/devices/soc0/*; do [ -f "$f" ] && echo "$f: $(cat $f 2>&1)"; done'
run kernel/partitions.txt    'ls -l /dev/block/by-name/ /dev/block/bootdevice/by-name/ 2>&1; cat /proc/partitions'
run kernel/modules.txt       'cat /proc/modules; echo ---; ls -l /vendor/lib/modules /vendor_dlkm/lib/modules; echo ---; cat /vendor/lib/modules/modules.load /vendor_dlkm/lib/modules/modules.load'
run kernel/kernel-log.txt    'logcat -b kernel -d'

echo "live device tree (if SELinux lets the shell read it)"
timeout 120 "$ADB" ${ADB_SERIAL:+-s "$ADB_SERIAL"} exec-out \
    'tar -cf - -C /sys/firmware devicetree 2>/dev/null' >"$OUT/devicetree.tar"
printf '  %-34s %8s bytes\n' devicetree.tar "$(wc -c <"$OUT/devicetree.tar")"

echo "displays / fold postures"
run display/dumpsys-display.txt  'dumpsys display'
run display/surfaceflinger.txt   'dumpsys SurfaceFlinger'
run display/sf-display-ids.txt   'dumpsys SurfaceFlinger --display-id'
run display/wm.txt               'wm size; wm density; dumpsys window displays'
run display/device-state.txt     'dumpsys device_state; cmd device_state print-states'
run display/drm-sysfs.txt        'ls -l /sys/class/drm/ 2>&1; for c in /sys/class/drm/card*-*; do echo "== $c"; cat $c/status $c/modes 2>&1; done'
run display/backlight.txt        'ls -l /sys/class/backlight/ 2>&1; for b in /sys/class/backlight/*; do echo "== $b"; cat $b/max_brightness $b/brightness 2>&1; done'

echo "input / touch / pen / sensors"
run input/dumpsys-input.txt  'dumpsys input'
run input/getevent-lp.txt    'getevent -lp'
run input/hidraw.txt         'ls -l /dev/hidraw* /sys/class/hidraw/ 2>&1; for h in /sys/class/hidraw/*; do echo "== $h"; cat $h/device/uevent 2>&1; done'
run sensors/sensorservice.txt 'dumpsys sensorservice'
run sensors/vendor-config.txt 'ls -laR /vendor/etc/sensors/'

echo "power / charging / thermal"
run power/battery.txt        'dumpsys battery'
run power/power.txt          'dumpsys power'
run power/thermal.txt        'dumpsys thermalservice'
run power/usb.txt            'dumpsys usb'
run power/power-supply.txt   'for d in /sys/class/power_supply/*; do echo "== $d"; cat $d/uevent 2>&1; done'
run power/typec.txt          'for d in /sys/class/typec/*; do echo "== $d"; ls $d; cat $d/* 2>/dev/null; done'
run power/suspend.txt        'cat /sys/power/state /sys/power/mem_sleep /sys/kernel/debug/suspend_stats 2>&1; dumpsys suspend_control_internal 2>&1 | head -200'

echo "wifi / bluetooth"
run net/wifi.txt             'cmd wifi status; dumpsys wifi | head -400'
run net/wifi-vendor.txt      'ls -laR /vendor/etc/wifi/; cat /vendor/etc/wifi/*/WCNSS_qcom_cfg.ini /vendor/etc/wifi/WCNSS_qcom_cfg.ini 2>&1'
run net/bluetooth.txt        'dumpsys bluetooth_manager | head -200'
run net/pci.txt              'ls -l /sys/bus/pci/devices/ 2>&1; for d in /sys/bus/pci/devices/*; do echo "== $d"; cat $d/vendor $d/device $d/subsystem_vendor $d/subsystem_device 2>&1; done'

echo "vendor partition: firmware, HALs, init, configs"
run vendor/firmware-listing.txt 'ls -laR /vendor/firmware /vendor/firmware_mnt /vendor/dsp /vendor/bt_firmware /vendor/modem_firmware 2>&1'
run vendor/build-prop.txt    'cat /vendor/build.prop /odm/etc/build.prop 2>&1'
run vendor/hals.txt          'ls -la /vendor/bin/hw/ /vendor/bin/ 2>&1'
run vendor/init-rc.txt       'for f in /vendor/etc/init/*.rc /vendor/etc/init/hw/*.rc; do echo "===== $f"; cat $f; done'
run vendor/vintf.txt         'cat /vendor/etc/vintf/manifest.xml /vendor/etc/vintf/manifest/*.xml 2>&1'
run vendor/libs.txt          'ls -la /vendor/lib64/ /vendor/lib64/c1/ 2>&1'
run vendor/features.txt      'pm list features; echo ---; pm list packages -f | grep -i -E "microsoft|surface|qti|qualcomm"'

if [ "$PULL_FW" = 1 ]; then
    echo "pulling readable vendor firmware (private, stays in dumps/)"
    for d in /vendor/firmware_mnt/image /vendor/firmware /vendor/dsp /vendor/bt_firmware; do
        dest="$OUT/firmware$(dirname "$d")"
        mkdir -p "$dest"
        adbc pull "$d" "$dest/" >>"$OUT/firmware-pull.log" 2>&1 || echo "  partial: $d (see firmware-pull.log)"
    done
    du -sh "$OUT/firmware" 2>/dev/null
fi

echo
echo "== summary"
grep -E '^\[(ro\.product\.(model|device|name)|ro\.build\.(fingerprint|version\.release)|ro\.bootloader|ro\.boot\.(flash\.locked|verifiedbootstate|vbmeta\.device_state|slot_suffix|hardware\.revision)|sys\.oem_unlock_allowed|ro\.oem_unlock_supported)\]' "$OUT/props.txt"
echo "devicetree readable: $([ -s "$OUT/devicetree.tar" ] && echo yes || echo no)"
echo "done: $OUT"
