#!/usr/bin/env bash
# Install OpenDuo on a Surface Duo 2 from a computer (docs/INSTALL.md).
#
#   scripts/install-openduo.sh --backup DIR [--images DIR] [options]
#
# What it does, in order (it stops at the first thing that is not as expected):
#   1. checks the computer: adb, fastboot, the release images and their
#      SHA256SUMS; fetches the WOA Project's files (the UEFI, the headless
#      recovery, parted) from their pinned URLs and checks their sha256;
#   2. finds the Surface Duo 2 (in Android with USB debugging, in fastboot,
#      or in the WOA recovery) and checks that its bootloader is unlocked;
#   3. starts the WOA headless recovery FROM RAM (fastboot boot: it writes
#      nothing by itself);
#   4. BACKUP, read only: every partition table and every partition except
#      userdata and super (persist, the modem's modemst1/modemst2/fsg/fsc,
#      sfpd, boot_a/boot_b, ...) into DIR, each one checked against the
#      phone's sha256. They hold the phone's identifiers and calibration:
#      keep DIR safe, never upload it anywhere;
#   5. after you type "yes": replaces userdata (on sda) by esp (1 GiB) and
#      linux (the rest), writes the ESP and root images there, writes the
#      UEFI into boot_a and boot_b, checking each write against its sha256;
#   6. restarts the phone. Its first start grows the root, imports the
#      firmware from the phone's own stock partitions, and restarts once more.
#
# It never writes modemst1, modemst2, fsg, fsc, persist or any other stock
# partition, never runs `fastboot flash` (it wedges this bootloader), and
# never unlocks the bootloader (that is your step, docs/INSTALL.md).
#
# It numbers the steps ([4/6]), says how long each one usually takes and how
# long it took, and shows how far the big copies are (percent, speed, time
# left). On Linux, when adb or fastboot say "no permissions", it stops and
# names the fix: udev-rules.sh (next to this script), run once with sudo.
#
# Options:
#   --backup DIR      where the backups go (needed; created; an earlier backup
#                     there is checked and kept, a run can be resumed)
#   --images DIR      the release files (openduo-*-surface-duo2-{rootfs,esp}.img
#                     or .img.xz and SHA256SUMS; default: next to this script,
#                     else out/release)
#   --woa DIR         where the WOA files are kept (default ~/.cache/openduo/woa);
#                     put them there yourself when there is no network
#   --serial SERIAL   the phone's serial (default: the one Surface Duo 2 found)
#   --dry-run         print every command, run none (except `adb devices` and
#                     `fastboot devices`, to show which phone it would pick)
#   --yes             do not ask (the typed "yes" included)
#   --reinstall       the phone has OpenDuo already (esp and linux exist):
#                     write the images into them again
#
# Environment: ADB, FASTBOOT (the tools; default adb and fastboot from PATH).
# It always names the phone (-s SERIAL) and never restarts the adb server, so
# other phones on the same computer are left alone.
set -euo pipefail

ADB=${ADB:-adb}
FASTBOOT=${FASTBOOT:-fastboot}
HERE=$(cd "$(dirname "$0")" && pwd)

# The WOA Project's files: pinned, never redistributed by OpenDuo.
UEFI_URL=https://github.com/WOA-Project/SurfaceDuo-Releases/releases/download/2611.01/Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip
UEFI_ZIP=Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip
UEFI_ZIP_SHA=bd2f8bf24369ee3b48d46a4edca247081151712adfcc5f2b2e4d03bd6aabe14f
UEFI_IN_ZIP="Surface Duo 2 UEFI (Secure Boot Disabled) (Fast Boot)/uefi_nosb.img"
UEFI_SHA=2be81b91abc40691b8829f6b870c6b1d8a668c6b23a4ff1fe22e7c9afe93e353
GUIDES=https://raw.githubusercontent.com/WOA-Project/SurfaceDuo-Guides/e51bfe0cb6caedc74ad7f33da89809bffc07b7de/Files
HEADLESS_SHA=d38809648d423b9700e1f2db7d810a9f406b2d0c08f47b9e9461617a5c317bef
PARTED_SHA=14fcc974bb9c87d26174a9bbe15659cf07d3a63eb5f950aaf1158257244f7b43

# What the phone must have before anything is written, and what is never backed up.
DUO2_PARTS="persist sfpd modemst1 modemst2 fsg fsc super boot_a boot_b modem_a dsp_a vendor_boot_a"
CRITICAL="persist modemst1 modemst2 fsg fsc sfpd devinfo misc boot_a boot_b"
NO_BACKUP="userdata super esp linux"
ESP_BYTES=$((1 << 30))

BACKUP='' IMAGES='' WOA=${XDG_CACHE_HOME:-$HOME/.cache}/openduo/woa SERIAL='' DRY=0 YES=0 REINSTALL=0
while [ $# -gt 0 ]; do
    case $1 in
    --backup) BACKUP=${2:?}; shift 2 ;;
    --images) IMAGES=${2:?}; shift 2 ;;
    --woa) WOA=${2:?}; shift 2 ;;
    --serial) SERIAL=${2:?}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes) YES=1; shift ;;
    --reinstall) REINSTALL=1; shift ;;
    -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "install-openduo: unknown option $1 (--help)" >&2; exit 2 ;;
    esac
done

# --- output ------------------------------------------------------------------

# The serial is an identifier: shown as its first and last two characters.
masked() {
    local s=$1
    if [ ${#s} -gt 4 ] && [ "$s" != SERIAL ]; then printf '%s…%s' "${s:0:2}" "${s: -2}"; else printf '%s' "$s"; fi
}
mask() { if [ -n "$SERIAL" ]; then sed "s|$SERIAL|$(masked "$SERIAL")|g"; else cat; fi; }
say() { printf '%s\n' "$*" | mask; }
die() { printf 'install-openduo: %s\n' "$*" | mask >&2; exit 1; }

# A duration: 48 s, 2 min 05 s, 1 h 02 min.
dur() {
    local s=$1
    if [ "$s" -lt 60 ]; then
        printf '%d s' "$s"
    elif [ "$s" -lt 3600 ]; then
        printf '%d min %02d s' $((s / 60)) $((s % 60))
    else
        printf '%d h %02d min' $((s / 3600)) $((s % 3600 / 60))
    fi
}
# A size: 5.3 GiB, 812 MiB, 96 KiB.
human() {
    local b=$1
    if [ "$b" -ge $((1 << 30)) ]; then
        printf '%d.%d GiB' $((b >> 30)) $(((b * 10 >> 30) % 10))
    elif [ "$b" -ge $((1 << 20)) ]; then
        printf '%d MiB' $((b >> 20))
    else
        printf '%d KiB' $((b >> 10))
    fi
}

STEPS=6 STEP_N=0 STEP_T0=''
step_end() { # how long the step that ends now took
    if [ -n "$STEP_T0" ]; then
        say "   (step $STEP_N took $(dur $((SECONDS - STEP_T0))))"
        STEP_T0=''
    fi
}
step() { # step N TITLE [USUALLY]: a new step, and how long it usually takes
    step_end
    STEP_N=$1 STEP_T0=$SECONDS
    printf '\n[%s/%s] %s\n' "$1" "$STEPS" "$2" | mask
    if [ -n "${3:-}" ]; then say "   (usually $3)"; fi
}

# A long copy runs in the background while watch_copy shows how far it is.
# Copies smaller than BIG run in the foreground, as quick as they are.
BIG=$((32 << 20))
PROGRESS_EVERY=${OPENDUO_PROGRESS_EVERY:-30}
COPY_PID='' COPY_SECS=0
copied() { # copied PID FROM: the bytes so far; FROM is file:PATH (the file
    # the copy writes) or fd:N (how far the process has read its fd N)
    local n=
    case $2 in
    file:*) n=$(stat -c %s "${2#file:}" 2>/dev/null || true) ;;
    fd:*) n=$(sed -n 's/^pos:[[:space:]]*//p' "/proc/$1/fdinfo/${2#fd:}" 2>/dev/null || true) ;;
    esac
    case $n in '' | *[!0-9]*) n=0 ;; esac
    printf '%s\n' "$n"
}
watch_copy() { # watch_copy PID TOTAL FROM: wait for the copy; its exit status
    local pid=$1 total=$2 from=$3 t0=$SECONDS last=$SECONDS n best=0 el rate line rc=0 tty=0 shown=0
    COPY_PID=$pid
    if [ -t 1 ]; then tty=1; fi
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        el=$((SECONDS - t0))
        [ "$el" -gt 0 ] || continue
        # never backwards (a reader closes its fd before it exits)
        n=$(copied "$pid" "$from")
        if [ "$n" -lt "$best" ]; then n=$best; fi
        best=$n
        rate=$((n / el))
        line="   $(human "$n") of $(human "$total")"
        if [ "$total" -gt 0 ]; then line+=" ($((n * 100 / total)) %)"; fi
        line+=", $((rate / 1000000)) MB/s"
        if [ "$rate" -gt 0 ] && [ "$total" -gt "$n" ]; then
            line+=", about $(dur $(((total - n) / rate + 1))) left"
        fi
        if [ "$tty" = 1 ]; then
            printf '\r\033[K%s' "$line"
            shown=1
        elif [ $((SECONDS - last)) -ge "$PROGRESS_EVERY" ]; then
            printf '%s\n' "$line"
            last=$SECONDS
        fi
    done
    wait "$pid" || rc=$?
    COPY_PID='' COPY_SECS=$((SECONDS - t0))
    if [ "$shown" = 1 ]; then printf '\r\033[K'; fi
    return "$rc"
}
# Ctrl-C: a copy in the background ignores it (it is not the terminal's
# foreground job), so it is stopped here.
stop_copy() {
    if [ -n "$COPY_PID" ]; then
        kill "$COPY_PID" 2>/dev/null || true
        COPY_PID=''
    fi
}
trap 'stop_copy; printf "\ninstall-openduo: stopped\n" >&2; exit 130' INT TERM
took() { # "(5.3 GiB in 48 s, 110 MB/s)" after a long copy, nothing after a short one
    if [ "$COPY_SECS" -ge 2 ]; then
        printf ' (%s in %s, %d MB/s)' "$(human "$1")" "$(dur "$COPY_SECS")" $(($1 / COPY_SECS / 1000000))
    fi
}
show() { # the command as it runs (or would run); adbs/fbs spelled out
    local out=+ a
    case $1 in
    adbs) shift; set -- "$ADB" -s "$SERIAL" "$@" ;;
    fbs) shift; set -- "$FASTBOOT" -s "$SERIAL" "$@" ;;
    esac
    for a in "$@"; do
        case $a in
        *[[:space:]\|\<\>\;\&\$]* | '') out+=" '$a'" ;;
        *) out+=" $a" ;;
        esac
    done
    printf '%s\n' "$out" | mask
}
# run CMD...: a command that changes something or takes long; printed, and
# not run with --dry-run.
run() { show "$@"; [ "$DRY" = 1 ] || "$@"; }
adbs() { "$ADB" -s "$SERIAL" "$@"; }
fbs() { "$FASTBOOT" -s "$SERIAL" "$@"; }
# rsh CMD: a command line on the phone (the recovery's root shell), its output
# without the carriage returns an old adbd adds.
rsh() { adbs shell "$1" | tr -d '\r'; }
ask() { # ask "question": a plain yes/no
    [ "$YES" = 1 ] && return 0
    local a
    read -r -p "$1 [y/N] " a
    case $a in y | Y | yes) return 0 ;; *) return 1 ;; esac
}
confirm_typed() { # the one question before the destructive steps
    [ "$YES" = 1 ] && return 0
    local a
    read -r -p "Type yes to go on: " a
    [ "$a" = yes ] || die "stopped: nothing was written"
}

# --- the computer ---------------------------------------------------------------

need_tool() { command -v "$1" >/dev/null 2>&1 || die "no $1 ($2)"; }

sha() { sha256sum "$1" | cut -c1-64; }

fetch() { # fetch URL FILE SHA: download unless a checked copy is there
    local url=$1 file=$2 want=$3
    if [ -f "$file" ] && [ "$(sha "$file")" = "$want" ]; then
        return 0
    fi
    [ "$DRY" = 1 ] && { show curl -fL -o "$file" "$url"; return 0; }
    mkdir -p "$(dirname "$file")"
    say "   downloading $(basename "$file")"
    if ! curl -fL --retry 3 -o "$file.part" "$url"; then
        rm -f "$file.part"
        die "could not download $url: put the file there yourself ($file, sha256 $want)"
    fi
    [ "$(sha "$file.part")" = "$want" ] || { rm -f "$file.part"; die "$(basename "$file"): wrong sha256 (want $want)"; }
    mv "$file.part" "$file"
}

prepare_host() {
    step 1 "This computer: the tools, the release files, the WOA files" \
        "seconds; the first time a minute or two: downloads, unpacking"
    need_tool "$ADB" "Android platform-tools: adb; or ADB=/path/to/adb"
    need_tool "$FASTBOOT" "Android platform-tools: fastboot; or FASTBOOT=/path/to/fastboot"
    need_tool sha256sum coreutils
    need_tool curl curl
    need_tool python3 python3
    say "   adb: $("$ADB" version | head -n 1), fastboot: $("$FASTBOOT" --version 2>&1 | head -n 1)"

    if [ -z "$IMAGES" ]; then
        if ls "$HERE"/openduo-*-surface-duo2-rootfs.img* >/dev/null 2>&1; then
            IMAGES=$HERE
        else
            IMAGES=$(cd "$HERE/.." && pwd)/out/release
        fi
    fi
    [ -f "$IMAGES/SHA256SUMS" ] || die "no $IMAGES/SHA256SUMS (--images DIR: the release files)"
    shopt -s nullglob
    for what in rootfs esp; do
        local files=("$IMAGES"/openduo-*-surface-duo2-"$what".img "$IMAGES"/openduo-*-surface-duo2-"$what".img.xz)
        [ ${#files[@]} -gt 0 ] || die "no openduo-*-surface-duo2-$what.img(.xz) in $IMAGES"
        local names name xz=
        names=$(for f in "${files[@]}"; do basename "${f%.xz}"; done | sort -u)
        [ "$(printf '%s\n' "$names" | wc -l)" = 1 ] || die "more than one release in $IMAGES: keep one"
        name=$names
        [ ! -f "$IMAGES/$name.xz" ] || xz=$name.xz
        local want
        want=$(awk -v f="$name" '$2 == f || $2 == "*" f {print $1}' "$IMAGES/SHA256SUMS")
        [ -n "$want" ] || die "$name is not in $IMAGES/SHA256SUMS"
        local file=$IMAGES/$name
        if [ ! -f "$file" ] || [ "$(sha "$file")" != "$want" ]; then
            local wantxz
            wantxz=$(awk -v f="$name.xz" '$2 == f || $2 == "*" f {print $1}' "$IMAGES/SHA256SUMS")
            [ -n "$xz" ] && [ -n "$wantxz" ] || die "$name: missing or damaged, and no .xz to unpack it from"
            say "   checking $name.xz"
            [ "$(sha "$IMAGES/$name.xz")" = "$wantxz" ] || die "$name.xz: wrong sha256 (download it again)"
            need_tool xz xz-utils
            file=${XDG_CACHE_HOME:-$HOME/.cache}/openduo/images/$name
            if [ ! -f "$file" ] || [ "$(sha "$file")" != "$want" ]; then
                mkdir -p "$(dirname "$file")"
                if [ "$DRY" = 1 ]; then
                    show xz -dc "$IMAGES/$name.xz" ">" "$file"
                else
                    say "   unpacking $name ($(du -hL "$IMAGES/$name.xz" | cut -f1) packed)"
                    local total
                    total=$(xz --robot --list "$IMAGES/$name.xz" | awk -F'\t' '$1 == "file" {print $5}')
                    xz -dc "$IMAGES/$name.xz" >"$file.part" &
                    watch_copy $! "${total:-0}" "file:$file.part" || { rm -f "$file.part"; die "$name.xz: could not unpack it"; }
                    [ "$(sha "$file.part")" = "$want" ] || { rm -f "$file.part"; die "$name: wrong sha256 after unpacking"; }
                    mv "$file.part" "$file"
                fi
            fi
        fi
        if [ "$what" = rootfs ]; then ROOTFS=$file ROOTFS_SHA=$want; else ESPIMG=$file ESP_SHA=$want; fi
        say "   $name: ok"
    done
    if [ "$DRY" != 1 ]; then
        [ "$(stat -L -c %s "$ESPIMG")" -le "$ESP_BYTES" ] || die "the ESP image is larger than its 1 GiB partition"
    fi

    [ "$DRY" = 1 ] || mkdir -p "$WOA"
    fetch "$UEFI_URL" "$WOA/$UEFI_ZIP" "$UEFI_ZIP_SHA"
    fetch "$GUIDES/surfaceduo2-headless.img" "$WOA/surfaceduo2-headless.img" "$HEADLESS_SHA"
    fetch "$GUIDES/parted" "$WOA/parted" "$PARTED_SHA"
    UEFI=$WOA/uefi_nosb.img
    if [ "$DRY" != 1 ] && { [ ! -f "$UEFI" ] || [ "$(sha "$UEFI")" != "$UEFI_SHA" ]; }; then
        python3 -I -c 'import sys, zipfile; open(sys.argv[3], "wb").write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))' \
            "$WOA/$UEFI_ZIP" "$UEFI_IN_ZIP" "$UEFI"
        [ "$(sha "$UEFI")" = "$UEFI_SHA" ] || die "uefi_nosb.img: wrong sha256"
    fi
    say "   WOA UEFI 2611.01, headless recovery, parted: ok ($WOA)"

    [ -n "$BACKUP" ] || die "--backup DIR is needed (where the phone's partitions are saved)"
    if [ "$DRY" = 1 ]; then
        show mkdir -p "$BACKUP"
    else
        mkdir -p "$BACKUP"
        chmod 700 "$BACKUP"
        BACKUP=$(cd "$BACKUP" && pwd)
    fi
    say "   backups: $BACKUP"
}

# --- the phone ------------------------------------------------------------------

# A Surface Duo 2 in Android says so in `adb devices -l`; in fastboot,
# `getvar product` names it. Nothing else is ever asked of another device.
is_duo2_adb_line() { printf '%s\n' "$1" | grep -Eq '(device|model|product):(duo2|Surface_Duo_2|surfaceduo2)'; }
is_duo2_product() { printf '%s\n' "$1" | grep -Eiq '^(duo2|surfaceduo2|zeta)'; }
# On Linux the USB id says it too: 045e:0c2f is the Surface Duo's fastboot.
usb_id() {
    local d
    for d in /sys/bus/usb/devices/*; do
        [ -f "$d/serial" ] && [ "$(cat "$d/serial" 2>/dev/null)" = "$1" ] || continue
        printf '%s:%s\n' "$(cat "$d/idVendor")" "$(cat "$d/idProduct")"
        return 0
    done
    return 1
}
is_duo2_fastboot() { # is_duo2_fastboot SERIAL PRODUCT
    is_duo2_product "$2" || [ "$(usb_id "$1" 2>/dev/null)" = 045e:0c2f ]
}

# "no permissions": adb or fastboot lists a device that this user may not open
# (on Linux: no udev rule for its USB vendor). fastboot then shows the hint
# where the serial would be; adb shows the serial.
noperm_adb() { "$ADB" devices | tr -d '\r' | awk '/no permissions/ {print $1}'; }
noperm_fastboot() { "$FASTBOOT" devices | tr -d '\r' | grep -c 'no permissions' || true; }
# What the lists said before the phone changes its mode: a device without
# permission that is new afterwards is the phone (wait_for).
NP_ADB='' NP_FB=0
noperm_mark() { NP_ADB=$(noperm_adb) NP_FB=$(noperm_fastboot); }
usb_advice() { # the fix, in short lines (a long pasted line breaks in some terminals)
    local helper=$HERE/udev-rules.sh
    printf '%s lists the phone, but this computer may not use it:\n' "$1"
    printf '"no permissions", no udev rule for it yet. Once, as root:\n\n'
    if [ -f "$helper" ]; then
        printf '    sudo sh %s\n\n' "$helper"
    else
        printf '    the two rules in docs/INSTALL.md, "USB permissions on Linux"\n\n'
    fi
    printf 'Then unplug the phone, plug it in again, and run this again: it\n'
    printf 'goes on where it stopped. Still "no permissions"? The adb server\n'
    printf 'started before the rules: unplug and plug in once more, or run\n'
    printf '"adb kill-server" (it stops adb for every phone on this computer).\n'
}
die_usb() { # die_usb TOOL
    { printf 'install-openduo: stopped.\n'; usb_advice "$1"; } | mask >&2
    exit 1
}

adb_state() { # adb_state SERIAL: device, recovery, unauthorized... or nothing
    "$ADB" devices | tr -d '\r' | awk -v s="$1" '$1 == s {print $2}'
}
fastboot_has() { "$FASTBOOT" devices | tr -d '\r' | awk '{print $1}' | grep -qx "$1"; }
fastboot_product() { "$FASTBOOT" -s "$1" getvar product 2>&1 | tr -d '\r' | sed -n 's/^product: *//p' | head -n 1; }

find_phone() {
    step 2 "The phone" "a few seconds"
    local found=() s line p noperm=''
    if [ -n "$SERIAL" ]; then
        found=("$SERIAL")
    else
        while IFS= read -r line; do
            s=${line%%[[:space:]]*}
            [ -n "$s" ] && [ "$s" != List ] || continue
            case $line in *"no permissions"*) noperm=adb; continue ;; esac
            # in Android the phone names itself; in a recovery (the WOA one,
            # from an earlier run) its partitions are checked before anything
            # else is done (check_recovery)
            if is_duo2_adb_line "$line" || [ "$(printf '%s\n' "$line" | awk '{print $2}')" = recovery ]; then
                found+=("$s")
            fi
        done < <("$ADB" devices -l | tr -d '\r')
        while IFS= read -r line; do
            case $line in *"no permissions"*) noperm=fastboot; continue ;; esac
            s=${line%%[[:space:]]*}
            [ -n "$s" ] || continue
            if [ "$DRY" = 1 ]; then
                show "$FASTBOOT" -s "$s" getvar product
                continue
            fi
            p=$(fastboot_product "$s")
            if is_duo2_fastboot "$s" "$p"; then
                found+=("$s")
            else
                say "   (a device in fastboot that says product \"$p\": not a Surface Duo 2, left alone)"
            fi
        done < <("$FASTBOOT" devices | tr -d '\r')
    fi
    if [ ${#found[@]} -eq 0 ] && [ -n "$noperm" ]; then
        [ "$DRY" = 1 ] || die_usb "$noperm"
        usb_advice "$noperm" | sed 's/^/   /'
    fi
    if [ ${#found[@]} -eq 0 ]; then
        [ "$DRY" = 1 ] && { SERIAL=SERIAL; say "   no Surface Duo 2 connected (the commands below show SERIAL)"; return 0; }
        die "no Surface Duo 2 found. Connect it in Android with USB debugging on (and accept this computer), or in fastboot (docs/INSTALL.md), then run this again."
    fi
    [ ${#found[@]} -eq 1 ] || die "more than one Surface Duo 2: name one with --serial"
    SERIAL=${found[0]}
    say "   Surface Duo 2, serial $(masked "$SERIAL")"
}

wait_for() { # wait_for fastboot|recovery|device SECONDS
    local want=$1 n=$2
    [ "$DRY" = 1 ] && { say "   (waits until the phone is in $want)"; return 0; }
    say "   waiting for the phone in $want (up to ${n}s)"
    local others np seen=0
    while [ "$n" -gt 0 ]; do
        case $want in
        fastboot) fastboot_has "$SERIAL" && return 0 ;;
        *) [ "$(adb_state "$SERIAL")" = "$want" ] && return 0 ;;
        esac
        # A device that came up without permission while waiting is the
        # phone in its new mode, without a udev rule for it (a moment is
        # given: udev applies a rule just after the device appears).
        if [ "$want" = fastboot ]; then
            if [ "$(noperm_fastboot)" -gt "$NP_FB" ]; then seen=$((seen + 2)); else seen=0; fi
        else
            np=$(noperm_adb)
            if printf '%s\n' "$np" | grep -qxF -e "$SERIAL" ||
                [ -n "$(printf '%s\n' "$np" | grep -vxF -e "$NP_ADB" || true)" ]; then
                seen=$((seen + 2))
            else
                seen=0
            fi
        fi
        if [ "$seen" -ge 8 ]; then
            if [ "$want" = fastboot ]; then die_usb fastboot; else die_usb adb; fi
        fi
        # A recovery may report another serial than fastboot did: the one
        # device in recovery right after `fastboot boot` is this phone (its
        # partitions are checked next).
        if [ "$want" = recovery ] && [ "$n" -lt "$(($2 - 30))" ]; then
            others=$("$ADB" devices | tr -d '\r' | awk '$2 == "recovery" {print $1}')
            if [ -n "$others" ] && [ "$(printf '%s\n' "$others" | wc -l)" = 1 ]; then
                say "   the recovery reports the serial $(masked "$others"): that is the phone"
                SERIAL=$others
                return 0
            fi
        fi
        sleep 2
        n=$((n - 2))
    done
    return 1
}

to_recovery() {
    step 3 "The WOA headless recovery, from RAM (writes nothing)" "about a minute"
    local st
    st=$(adb_state "$SERIAL")
    if [ "$st" = recovery ]; then
        say "   in the WOA recovery already"
        return 0
    fi
    if [ "$st" = device ]; then
        say "   in Android: restarting into fastboot"
        noperm_mark
        run adbs reboot bootloader
        wait_for fastboot 90 || die "the phone did not come up in fastboot: go there with the buttons (docs/INSTALL.md) and run this again"
    elif [ "$st" = unauthorized ]; then
        die "accept this computer on the phone (USB debugging), then run this again"
    elif [ "$DRY" != 1 ] && ! fastboot_has "$SERIAL"; then
        die "the phone is neither in Android, fastboot nor the WOA recovery"
    fi
    if [ "$DRY" != 1 ]; then
        local p u
        p=$(fastboot_product "$SERIAL")
        is_duo2_fastboot "$SERIAL" "$p" || die "fastboot says product \"$p\" (USB id $(usb_id "$SERIAL" || echo unknown)): not a Surface Duo 2"
        u=$(fbs getvar unlocked 2>&1 | tr -d '\r' | sed -n 's/^unlocked: *//p' | head -n 1)
        [ "$u" = yes ] || die "the bootloader is locked (unlocked: ${u:-?}). Unlock it first (docs/INSTALL.md, step 2: it erases the phone), then run this again."
        say "   fastboot: product $p, bootloader unlocked"
    else
        show "$FASTBOOT" -s "$SERIAL" getvar unlocked
    fi
    noperm_mark
    run fbs boot "$WOA/surfaceduo2-headless.img"
    wait_for recovery 120 || die "the recovery did not show up in adb; press Power for 20 s, go to fastboot with the buttons and run this again"
}

check_recovery() {
    if [ "$DRY" = 1 ]; then
        show "$ADB" -s "$SERIAL" shell "ls /dev/block/by-name/"
        show "$ADB" -s "$SERIAL" push "$WOA/parted" /tmp/parted
        return 0
    fi
    local names p
    names=$(rsh 'ls /dev/block/by-name/')
    for p in $DUO2_PARTS; do
        printf '%s\n' "$names" | grep -qx "$p" || die "the phone has no $p partition: not a Surface Duo 2's layout, stopped"
    done
    run adbs push "$WOA/parted" /tmp/parted >/dev/null
    run adbs shell chmod 755 /tmp/parted
    BYNAME=$names
}

# --- 4. backup -------------------------------------------------------------------

backup() {
    step 4 "Backup of the phone's own partitions (read only) into $BACKUP" \
        "2-3 minutes the first time, about 2.3 GB; seconds when it resumes"
    if [ "$DRY" = 1 ]; then
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -s /dev/block/sdX unit B print" ">" "$BACKUP/parted-sdX.txt"
        show "$ADB" -s "$SERIAL" exec-out "dd if=/dev/block/by-name/PART bs=4194304" ">" "$BACKUP/PART.img"
        show "$ADB" -s "$SERIAL" shell "sha256sum /dev/block/by-name/PART"
        say "   (every partition except: $NO_BACKUP)"
        return 0
    fi
    local d p s skip
    cat >"$BACKUP/README.txt" <<'EOF'
The partitions of ONE Surface Duo 2, saved by scripts/install-openduo.sh before
OpenDuo was installed. They hold this phone's identifiers (IMEI and the modem's
calibration: modemst1, modemst2, fsg, fsc), its sensor calibration (persist), the
touch panels' alignment and factory addresses (sfpd) and its stock boot images.
Keep this directory in at least two places, never share or upload it.
Back to Android: docs/RESTORE.md.
EOF
    for d in $(rsh 'ls /dev/block/' | grep -E '^sd[a-z]$'); do
        rsh "/tmp/parted -s /dev/block/$d unit B print" >"$BACKUP/parted-$d.txt"
        rsh "/tmp/parted -m -s /dev/block/$d unit s print" >"$BACKUP/parted-$d.m.txt"
    done
    # sda's GPT as it is (protective MBR, header, entries): only sda changes
    adbs exec-out "dd if=/dev/block/sda bs=4096 count=6 2>/dev/null" >"$BACKUP/gpt-sda-head.bin"
    say "   partition tables: $(cd "$BACKUP" && echo parted-sd?.txt) gpt-sda-head.bin"
    touch "$BACKUP/SHA256SUMS"
    for p in $BYNAME; do
        # The WOA recovery also lists the whole disks (sda, sdb, ...) by name.
        case "$p" in sd[a-z] | mmcblk*) continue ;; esac
        skip=
        for s in $NO_BACKUP; do [ "$p" = "$s" ] && skip=1; done
        [ -z "$skip" ] || continue
        backup_one "$p"
    done
    for p in $CRITICAL; do
        grep -q "  $p.img\$" "$BACKUP/SHA256SUMS" || die "no checked backup of $p: stopped before anything is written"
    done
    say "   $(grep -c . "$BACKUP/SHA256SUMS") partitions saved and checked. Copy $BACKUP somewhere else too, now."
}

backup_one() {
    local p=$1 phone host try size
    # Nothing a restore needs is bigger than the modem (400 MiB); a bigger one
    # would be a disk or a data partition, not a stock image to keep.
    size=$(rsh "blockdev --getsize64 /dev/block/by-name/$p" | tr -dc 0-9)
    if [ -n "$size" ] && [ "$size" -gt $((1024 * 1024 * 1024)) ]; then
        say "   skip $p ($((size / 1048576)) MiB: not a partition a restore needs)"
        return 0
    fi
    phone=$(rsh "sha256sum /dev/block/by-name/$p" | cut -c1-64)
    [ ${#phone} = 64 ] || die "could not read $p's sha256 on the phone"
    if [ -f "$BACKUP/$p.img" ] && grep -q "^$phone  $p.img\$" "$BACKUP/SHA256SUMS"; then
        say "   ok   $p (saved before)"
        return 0
    fi
    # An earlier, checked backup is never replaced: it is the phone as it was
    # before OpenDuo (boot_a/boot_b hold the UEFI now, the modem's files move on).
    if [ -f "$BACKUP/$p.img" ] && grep -q "  $p.img\$" "$BACKUP/SHA256SUMS" &&
        [ "$(sha "$BACKUP/$p.img")" = "$(grep "  $p.img\$" "$BACKUP/SHA256SUMS" | cut -c1-64)" ]; then
        say "   ok   $p (the earlier backup is kept; the phone's copy has changed since)"
        return 0
    fi
    for try in 1 2; do
        COPY_SECS=0
        if [ -n "$size" ] && [ "$size" -ge "$BIG" ]; then
            say "   copying $p ($(human "$size"))"
            "$ADB" -s "$SERIAL" exec-out "dd if=/dev/block/by-name/$p bs=4194304 2>/dev/null" \
                >"$BACKUP/$p.img.part" &
            watch_copy $! "$size" "file:$BACKUP/$p.img.part" || true
        else
            adbs exec-out "dd if=/dev/block/by-name/$p bs=4194304 2>/dev/null" >"$BACKUP/$p.img.part" || true
        fi
        host=$(sha "$BACKUP/$p.img.part")
        if [ "$host" = "$phone" ]; then
            mv "$BACKUP/$p.img.part" "$BACKUP/$p.img"
            grep -v "  $p.img\$" "$BACKUP/SHA256SUMS" >"$BACKUP/SHA256SUMS.new" || true
            printf '%s  %s\n' "$phone" "$p.img" >>"$BACKUP/SHA256SUMS.new"
            mv "$BACKUP/SHA256SUMS.new" "$BACKUP/SHA256SUMS"
            say "   ok   $p$(took "${size:-0}")"
            return 0
        fi
        say "   $p: the copy differs from the phone (try $try)"
    done
    rm -f "$BACKUP/$p.img.part"
    die "$p could not be saved: stopped before anything is written"
}

# --- 5. partitions and images ----------------------------------------------------------

table() { rsh '/tmp/parted -m -s /dev/block/sda unit s print'; }
field() { # field NAME COLUMN: from the machine-readable table in $TABLE
    printf '%s\n' "$TABLE" | awk -F: -v n="$1" -v c="$2" '$6 == n {sub(/s$/, "", $c); print $c}'
}

plan() {
    step 5 "Partitions on sda, then the images" "about 2 minutes: 5 GiB to write and check"
    if [ "$DRY" = 1 ]; then
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -m -s /dev/block/sda unit s print"
        say "   (userdata N, START..END: esp START..START+1GiB-1, linux the rest)"
        ESPN=N LINUXN=N+1 MODE=dry
        return 0
    fi
    TABLE=$(table)
    LSS=$(printf '%s\n' "$TABLE" | awk -F: 'NR == 2 {print $4}')
    [ "$LSS" = 4096 ] || [ "$LSS" = 512 ] || die "sda: unexpected sector size ${LSS:-?}"
    local espn linuxn un
    espn=$(field esp 1) linuxn=$(field linux 1) un=$(field userdata 1)
    if [ -n "$espn" ] && [ -n "$linuxn" ]; then
        [ "$REINSTALL" = 1 ] || die "esp and linux are there already (OpenDuo is installed): run again with --reinstall to write the images over them"
        ESPN=$espn LINUXN=$linuxn MODE=reinstall
        say "   esp (sda$ESPN) and linux (sda$LINUXN) exist: the images go there again"
    elif [ -n "$un" ]; then
        [ -z "$espn$linuxn" ] || die "sda has userdata and one of esp/linux: not a layout this script knows, stopped"
        UN=$un START=$(field userdata 2) END=$(field userdata 3)
        ESPEND=$((START + ESP_BYTES / LSS - 1))
        LSTART=$((ESPEND + 1))
        local need=$(($(stat -L -c %s "$ROOTFS") / LSS + 1))
        [ $((END - LSTART + 1)) -ge "$need" ] || die "userdata is too small for the root image"
        MODE=partition
        say "   now: userdata is sda$UN, sectors $START-$END ($LSS-byte sectors)"
        say "   new: esp   sectors $START-$ESPEND (1 GiB, FAT32, the ESP flag)"
        say "        linux sectors $LSTART-$END (ext4, OpenDuo's root)"
    else
        die "sda has no userdata (and no esp + linux): not a layout this script knows, stopped"
    fi
}

partition() {
    [ "$MODE" = partition ] || [ "$MODE" = dry ] || return 0
    if [ "$DRY" = 1 ]; then
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -s -a none /dev/block/sda rm N"
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -s -a none /dev/block/sda mkpart esp fat32 STARTs ESPENDs"
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -s -a none /dev/block/sda mkpart linux ext4 LSTARTs ENDs"
        show "$ADB" -s "$SERIAL" shell "/tmp/parted -s /dev/block/sda set ESPN esp on"
        return 0
    fi
    local P="/tmp/parted -s -a none /dev/block/sda"
    run adbs shell "$P rm $UN"
    run adbs shell "$P mkpart esp fat32 ${START}s ${ESPEND}s"
    run adbs shell "$P mkpart linux ext4 ${LSTART}s ${END}s"
    TABLE=$(table)
    ESPN=$(field esp 1) LINUXN=$(field linux 1)
    [ -n "$ESPN" ] && [ -n "$LINUXN" ] || die "parted did not make esp and linux: see 'adb shell /tmp/parted /dev/block/sda print' and docs/INSTALL.md"
    run adbs shell "/tmp/parted -s /dev/block/sda set $ESPN esp on"
    TABLE=$(table)
    [ "$(field esp 2)" = "$START" ] && [ "$(field esp 3)" = "$ESPEND" ] &&
        [ "$(field linux 2)" = "$LSTART" ] && [ "$(field linux 3)" = "$END" ] ||
        die "the new partitions are not where they should be: nothing written to them yet; see docs/INSTALL.md"
    printf '%s\n' "$TABLE" | grep -q "^$ESPN:.*:esp:.*esp" || die "esp has no ESP flag"
    say "   esp is sda$ESPN, linux is sda$LINUXN"
}

wait_node() { # the recovery's device node for a new partition
    local n=15
    while [ $n -gt 0 ]; do
        rsh "[ -b /dev/block/$1 ] && echo yes" | grep -q yes && return 0
        sleep 1
        n=$((n - 1))
    done
    die "/dev/block/$1 did not appear"
}

write_checked() { # write_checked DEVICE IMAGE SHA256 NAME
    local dev=$1 img=$2 want=$3 name=$4 size phone
    size=$(stat -L -c %s "$img" 2>/dev/null || echo SIZE)
    if [ "$DRY" = 1 ]; then
        show "$ADB" -s "$SERIAL" exec-in "dd of=$dev bs=4194304" "<" "$img"
        show "$ADB" -s "$SERIAL" shell "head -c $size $dev | sha256sum"
        return 0
    fi
    say "   writing $name ($(du -hL "$img" | cut -f1)) to $dev"
    local try
    for try in 1 2; do
        if [ $((size % 4194304)) -ne 0 ] && [ "$size" -le $((256 * 1048576)) ]; then
            # adb exec-in can close before dd writes its last, short block:
            # a small image goes through a copy on the phone instead.
            show "$ADB" -s "$SERIAL" push "$img" /tmp/openduo-write.img
            adbs push "$img" /tmp/openduo-write.img >/dev/null
            rsh "dd if=/tmp/openduo-write.img of=$dev bs=4194304 2>/dev/null; rm -f /tmp/openduo-write.img" >/dev/null
        elif [ "$size" -ge "$BIG" ]; then
            show "$ADB" -s "$SERIAL" exec-in "dd of=$dev bs=4194304" "<" "$img"
            "$ADB" -s "$SERIAL" exec-in "dd of=$dev bs=4194304 2>/dev/null" <"$img" &
            watch_copy $! "$size" fd:0 || true
            say "   written$(took "$size"); the phone reads it back to check it"
        else
            show "$ADB" -s "$SERIAL" exec-in "dd of=$dev bs=4194304" "<" "$img"
            adbs exec-in "dd of=$dev bs=4194304 2>/dev/null" <"$img" || true
        fi
        rsh sync >/dev/null
        phone=$(rsh "head -c $size $dev | sha256sum" | cut -c1-64)
        if [ "$phone" = "$want" ]; then
            say "   ok   $name"
            return 0
        fi
        say "   $name: the check differs, writing it again"
    done
    die "$name: what the phone holds now differs from the image (sha256): run this again (with --reinstall), nothing else was changed"
}

write_all() {
    if [ "$DRY" != 1 ]; then
        wait_node "sda$ESPN"
        wait_node "sda$LINUXN"
    fi
    write_checked "/dev/block/sda$ESPN" "$ESPIMG" "$ESP_SHA" "the ESP image"
    write_checked "/dev/block/sda$LINUXN" "$ROOTFS" "$ROOTFS_SHA" "the root image"
    # Last: from here on the phone starts the UEFI, then OpenDuo.
    local s
    for s in a b; do
        write_checked "/dev/block/by-name/boot_$s" "$UEFI" "$UEFI_SHA" "the UEFI into boot_$s"
    done
}

# --- main -------------------------------------------------------------------------

prepare_host
find_phone
to_recovery
check_recovery
backup
plan
say ""
say "   Next, on the phone:"
case $MODE in
partition) say "   - userdata (Android's data, empty after the unlock) is replaced by esp + linux" ;;
reinstall) say "   - esp and linux are overwritten (OpenDuo and everything stored in it is erased)" ;;
esac
say "   - the ESP and root images are written there"
say "   - the UEFI replaces Android's boot images in boot_a and boot_b (saved in $BACKUP)"
say "   Nothing else is written. Afterwards Android cannot start until it is restored (docs/RESTORE.md)."
[ "$DRY" = 1 ] || confirm_typed
STEP_T0=$SECONDS # the step's time from here, not the time spent reading the plan
partition
write_all
step 6 "Restart" "seconds"
run adbs reboot
step_end
say ""
say "Done on this computer in $(dur "$SECONDS")."
cat <<'EOF'

The phone now starts on its own (times measured on a Surface Duo 2):
  - the UEFI's picture, then OpenDuo's splash: about 25 s;
  - the first start grows the root file system and imports the phone's own
    firmware ("Setting up with your own firmware", then "Done, restarting"
    on the splash): about 20 s;
  - it restarts by itself once; the home screen comes up about 20 s later.
Touch does not work before that restart: wait for the home screen.

Then set up WiFi and a lock screen PIN in Settings. If the restart does not
come, or touch does not work after it: docs/INSTALL.md, "If something goes
wrong".
EOF
say "Keep the backup ($BACKUP) in at least two places."
