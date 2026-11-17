#!/usr/bin/env bash
# Back up the Surface Duo 2's USER data to a NAS (or any directory) over adb, without root:
# shared storage (/sdcard) minus the re-downloadable data of big online games,
# plus contacts, SMS, call log and the installed-app list. No system files.
# Read-only on the phone. Re-running skips the folders already finished.
#
#   ADB_SERIAL=<ip:port> scripts/backup-userdata.sh [dest]
#
# dest defaults to $DUO2_BACKUP_DIR/<date>; with DUO2_BACKUP_MOUNT set, that
# mount point (a network share, say) has to be mounted. Both come from local.env
# (local.env.example).
set -u

R=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$R/local.env" ] && . "$R/local.env"

ADB=${ADB:-$(command -v adb || echo "$HOME/android-sdk/platform-tools/adb")}
S=${ADB_SERIAL:?set ADB_SERIAL to the Duo 2}
DEST=${1:-${DUO2_BACKUP_DIR:?give dest or set DUO2_BACKUP_DIR in local.env}/$(date +%Y-%m-%d)}
LOG="$DEST/backup.log"

# Big online games: their data is downloaded again at first launch.
SKIP_DATA="com.miHoYo.GenshinImpact com.epicgames.fortnite com.riotgames.league.wildrift com.blizzard.arc com.nianticlabs.pokemongo"

a() { "$ADB" -s "$S" "$@" </dev/null; }  # adb shell must not eat the loops' stdin
model=$(a shell getprop ro.product.model 2>/dev/null | tr -d '\r')
[ "$model" = "Surface Duo 2" ] || { echo "not a Surface Duo 2: '$model'" >&2; exit 1; }
[ -z "${DUO2_BACKUP_MOUNT:-}" ] || mountpoint -q "$DUO2_BACKUP_MOUNT" || { echo "$DUO2_BACKUP_MOUNT (DUO2_BACKUP_MOUNT) not mounted" >&2; exit 1; }
mkdir -p "$DEST/sdcard/Android/data" "$DEST/meta" "$DEST/.done"
echo "== $(date '+%F %T') backup to $DEST" | tee -a "$LOG"

# pull <phone path> <dest dir>: one tree, timestamps kept; marked done on success.
pull() {
    local src=$1 dst=$2 key
    key=$(echo "$src" | tr '/ ' '__')
    [ -e "$DEST/.done/$key" ] && { echo "skip (done) $src" >>"$LOG"; return 0; }
    echo "$(date '+%T') pull $src" | tee -a "$LOG"
    if a pull -a "$src" "$dst/" >>"$LOG" 2>&1; then
        touch "$DEST/.done/$key"
    else
        echo "  FAILED: $src (see log)" | tee -a "$LOG"
    fi
}

# Top level of /sdcard (incl. hidden recycle bins), Android/ handled below.
a shell 'ls -1a /sdcard/' | tr -d '\r' | while read -r e; do
    case "$e" in .|..|Android|'') continue ;; esac
    pull "/sdcard/$e" "$DEST/sdcard"
done
pull /sdcard/Android/media "$DEST/sdcard/Android"
pull /sdcard/Android/obb   "$DEST/sdcard/Android"
a shell 'ls -1 /sdcard/Android/data/' | tr -d '\r' | while read -r p; do
    [ -z "$p" ] && continue
    case " $SKIP_DATA " in *" $p "*) echo "skip (game data) $p" | tee -a "$LOG"; continue ;; esac
    pull "/sdcard/Android/data/$p" "$DEST/sdcard/Android/data"
done

echo "$(date '+%T') contacts, sms, call log, apps" | tee -a "$LOG"
a shell 'content query --uri content://com.android.contacts/data --projection raw_contact_id:display_name:mimetype:data1:data2:data3' >"$DEST/meta/contacts.txt" 2>&1
a shell 'content query --uri content://sms --projection _id:thread_id:address:date:date_sent:type:read:body' >"$DEST/meta/sms.txt" 2>&1
a shell 'content query --uri content://call_log/calls --projection number:name:date:duration:type' >"$DEST/meta/call-log.txt" 2>&1
a shell 'pm list packages -3 -i' | sort >"$DEST/meta/apps-user.txt" 2>&1
a shell 'pm list packages -s' | sort >"$DEST/meta/apps-system.txt" 2>&1
a shell 'getprop ro.build.fingerprint; getprop ro.serialno' >"$DEST/meta/device.txt" 2>&1
echo "$SKIP_DATA" | tr ' ' '\n' >"$DEST/meta/skipped-game-data.txt"

# Verify: file count and bytes per pulled top-level folder, phone vs backup.
echo "$(date '+%T') verify" | tee -a "$LOG"
{
    printf '%-46s %10s %10s %14s %14s\n' folder files_phone files_nas bytes_phone bytes_nas
    a shell 'cd /sdcard && for d in $(ls -1a | grep -v -x -e . -e .. -e Android) Android/media Android/obb $(ls -1 Android/data | sed "s|^|Android/data/|"); do echo "$d $(find "$d" -type f 2>/dev/null | wc -l) $(find "$d" -type f -exec stat -c %s {} + 2>/dev/null | awk "{s+=\$1} END {print s+0}")"; done' | tr -d '\r' |
    while read -r d nf nb; do
        [ -e "$DEST/sdcard/$d" ] || { printf '%-46s %10s %10s %14s %14s\n' "$d" "$nf" - "$nb" "(skipped)"; continue; }
        lf=$(find "$DEST/sdcard/$d" -type f | wc -l)
        lb=$(find "$DEST/sdcard/$d" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')
        flag=""; { [ "$nf" != "$lf" ] || [ "$nb" != "$lb" ]; } && flag="  <-- MISMATCH"
        printf '%-46s %10s %10s %14s %14s%s\n' "$d" "$nf" "$lf" "$nb" "$lb" "$flag"
    done
} >"$DEST/VERIFY.txt"
chmod -R ug+rwX "$DEST" 2>/dev/null
grep -c MISMATCH "$DEST/VERIFY.txt" | sed 's/^/mismatching folders: /' | tee -a "$LOG"
du -sh "$DEST" | tee -a "$LOG"
echo "== $(date '+%F %T') done" | tee -a "$LOG"
