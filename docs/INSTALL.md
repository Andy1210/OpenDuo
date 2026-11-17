# Installing OpenDuo on a Surface Duo 2

This guide takes a Surface Duo 2 from stock Android to OpenDuo with one
script on a computer, `scripts/install-openduo.sh`. Read it to the end, and
read [RESTORE.md](RESTORE.md) as well, before you start.

> **Warning.**
> - Unlocking the bootloader (step 2) **erases everything on the phone**.
> - The install **replaces Android's user data partition**. Android can be
>   put back ([RESTORE.md](RESTORE.md)) with the backup the script makes.
> - Some partitions hold data that exists **only on your phone** (IMEI and
>   modem calibration, sensor calibration, the touch panels' alignment, the
>   factory WiFi and Bluetooth addresses). The script saves them before it
>   writes anything and never writes them; keep that backup in two places.
> - OpenDuo is a development preview. The whole way described here was tried
>   once, on the maintainer's phone (November 2026): from stock Android, the
>   script on a Linux computer (x86-64, Google's platform-tools: adb 1.0.41,
>   fastboot 37), the first start with the firmware import, and back to
>   Android ([RESTORE.md](RESTORE.md)). Other computers and other Android
>   releases are untested. If anything looks different from what is
>   described here, stop and ask (open an issue) before you go on.

The Surface Duo 2 only. The original Surface Duo (SM8150) is a different
phone and is not supported.

## What you need

**The phone**

- A Surface Duo 2 on stock Android, updated to Microsoft's last release,
  **2023.501.541** (Settings > System > System updates). Other releases
  should work but are untested.
- **Android started at least once after its last update.** An update is
  finished only at the next start of Android, and OpenDuo reads its firmware
  from the partitions that update wrote.
- Charged above 60 %, and a USB-C data cable.

**A computer**: Linux on x86_64 (developed on Ubuntu 24.04), with:

- `adb` and `fastboot`: Google's platform-tools
  (developer.android.com/tools/releases/platform-tools) or the `adb` and
  `fastboot` packages of your distribution;
- `curl`, `python3`, `xz` and `sha256sum` (most systems have them);
- about 10 GB of free disk space;
- on Linux, USB access to the phone for your user (below, "USB permissions
  on Linux": one helper script, run once with `sudo`).

**OpenDuo's release files**, from the repository's Releases page, in one
directory:

```
openduo-VERSION-surface-duo2-rootfs.img.xz   the root file system
openduo-VERSION-surface-duo2-esp.img.xz      the boot partition (systemd-boot, the kernel)
SHA256SUMS
install-openduo.sh                           (or scripts/install-openduo.sh in a clone)
udev-rules.sh                                USB access on Linux (or scripts/udev-rules.sh)
```

Check them before you use them: `sha256sum -c --ignore-missing SHA256SUMS`
in that directory.

You can also build them yourself (`scripts/build-release.sh`,
[CONTRIBUTING.md](../CONTRIBUTING.md)).

No Microsoft or Qualcomm file is in them, and you do not download one: at its
first start OpenDuo reads the firmware, the touch library and the sensor
calibration from your phone's own stock partitions, which stay on the phone
([stock/README.md](../stock/README.md)). The script downloads the
[WOA Project](https://github.com/WOA-Project)'s UEFI (release 2611.01, which
boots OpenDuo) and its recovery tools from pinned addresses and checks them
by sha256; OpenDuo does not redistribute them.

## Two things to know first

**Fastboot with the buttons.** Hold **Power for about 20 seconds**, until the
phone vibrates; let go; then hold **only Volume Down**. The phone shows the
bootloader and `fastboot devices` lists it. Never press both volume keys
together while it starts: that enters the Qualcomm emergency download mode
(USB id `05c6:9008`); if it happens, hold Power and Volume Down for about 30
seconds.

**No `fastboot flash`.** After any `fastboot flash` the Surface Duo 2's
bootloader stops answering until you force it back into fastboot with the
buttons. The script never uses it: it writes from the WOA recovery, which it
starts from RAM with `fastboot boot` (that writes nothing).

## USB permissions on Linux

On Linux, adb and fastboot may only open a USB device that a udev rule gives
your user. The phone shows up as two USB vendors during the install:

| Mode | USB vendor | Seen as |
|---|---|---|
| the bootloader (fastboot) | `045e` (Microsoft) | `fastboot getvar product`: `surfaceduo2` |
| the WOA recovery the script starts | `18d1` (Google) | `adb devices -l`: product `twrp_I006D`, the same serial as in fastboot |

Without a rule, `adb devices` or `fastboot devices` says **"no
permissions"**, and the script stops and names this fix. Run the helper
from the release once:

```sh
sudo sh udev-rules.sh
```

It writes `/etc/udev/rules.d/51-surface-duo.rules`, reloads udev and
re-applies the rules to the USB devices (`sudo sh udev-rules.sh --remove`
takes them away again). The two lines it writes, if you prefer to do it by
hand (`GROUP="plugdev"` only where that group exists):

```
SUBSYSTEM=="usb", ATTR{idVendor}=="045e", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0660", GROUP="plugdev", TAG+="uaccess"
```

followed by `sudo udevadm control --reload` and `sudo udevadm trigger`.

Then **unplug the phone and plug it in again**. An adb server that was
already running may keep seeing the phone without permission: unplug and plug
in once more, or run `adb kill-server` when no other phone is in use (it
stops adb for every device on this computer). With other phones that must
stay connected, a second adb server on another port leaves the first alone:
a two-line wrapper (`#!/bin/sh` and `exec adb -P 5038 "$@"`) given to the
script as `ADB=/path/to/wrapper`.

## 1. Save your data (stock Android)

Turn on Developer options (Settings > About, tap "Build number" seven times)
and, in Developer options, **USB debugging**. Connect the phone and accept
the computer's key on the phone. Then, in a clone of the repository:

```sh
adb devices -l                       # the phone: model:Surface_Duo_2
ADB_SERIAL=<serial> scripts/backup-userdata.sh /path/to/backup/dir
```

`scripts/backup-userdata.sh` only reads from the phone: the shared storage
(photos, downloads, documents...), the contacts, SMS and call log as text,
and the list of installed apps, checked by file counts and sizes
(`VERIFY.txt`). App data Android keeps private cannot be copied without
root: use the apps' own backups for those.

## 2. Unlock the bootloader (erases the phone)

1. In Developer options, turn on **OEM unlocking**.
2. Unlock:

   ```sh
   adb reboot bootloader
   fastboot devices                  # the phone is listed
   fastboot flashing unlock          # confirm on the phone (volume keys, then Power)
   fastboot getvar unlocked          # "unlocked: yes"
   ```

The phone erases itself and starts Android as new. Go through Android's setup
only as far as Developer options and USB debugging again, or leave the phone
in fastboot: the script starts from either.

## 3. Run the installer

First look, change nothing: `--dry-run` runs only `adb devices` and
`fastboot devices` and prints every command it would run.

```sh
scripts/install-openduo.sh --backup ~/duo2-backup --images /path/to/release --dry-run
```

Then install:

```sh
scripts/install-openduo.sh --backup ~/duo2-backup --images /path/to/release
```

`--backup` is where your phone's partitions are saved (it is created; pick a
directory you keep). With the phone in Android (USB debugging on) or in
fastboot, the script:

1. checks this computer, the release files and the WOA files (it downloads
   these once, into `~/.cache/openduo/woa`);
2. finds the Surface Duo 2 and checks that its bootloader is unlocked; it
   addresses only that phone, so other phones on the same computer are left
   alone;
3. starts the WOA recovery from RAM;
4. **backs up**, read only, every partition table and every partition except
   `userdata` and `super` (about 4 GB, a few minutes), each copy checked
   against the phone's own sha256: `persist`, `modemst1`, `modemst2`, `fsg`,
   `fsc`, `sfpd`, `boot_a`, `boot_b` and the rest;
5. shows its plan and asks you to **type `yes`**. Then it replaces
   `userdata` on the first storage unit by `esp` (1 GiB, FAT32) and `linux`
   (the rest), writes the two images there and the UEFI into `boot_a` and
   `boot_b`, and reads each one back;
6. restarts the phone.

It numbers these steps as it goes (`[4/6]`), says how long each one usually
takes and how long it took, and shows how far the big copies are (percent,
speed, time left). Measured on the maintainer's computer and phone:
**about 4 minutes in all**, most of it the backup (about 2.3 GB) and writing
the 5 GiB root image; the first run takes a little longer (the downloads,
unpacking the images). Ctrl-C stops it at any point; before the typed `yes`
nothing has been written.

**Copy the backup directory to a second place** (not the phone, not only this
computer). It holds your phone's identifiers: never share or upload it.

If the script stops, it says why, and nothing after that point has
happened. Running it again resumes: saved partitions are checked and kept,
and an earlier backup is never replaced. On a phone that has OpenDuo already,
`--reinstall` writes the images into the existing `esp` and `linux` (which
erases OpenDuo and everything stored in it).

Other options: `--serial SERIAL` when the script cannot tell which phone,
`--woa DIR` for the WOA files (without a network, put them there yourself:
the script names each file and its sha256), `ADB=` and `FASTBOOT=` for other
tools. `--help` lists everything.

## 4. The first start

Measured on the maintainer's phone:

1. The panels keep the UEFI's picture for a while, then the OpenDuo splash
   shows: **about 25 s** after the restart.
2. The first start grows the root file system to its partition, and
   `duo2-stock-import` copies the firmware, the touch runtime, the sensor
   configuration and calibration, the camera tables and the power profile
   from the phone's own stock partitions (it only reads them). The splash
   says "Setting up with your own firmware", then "Done, restarting":
   **about 20 s**.
3. **The phone restarts by itself once**, and the home screen comes up
   **about 20 s** later, with touch, WiFi, Bluetooth, the modem and the
   sensors working.

**Touch does not work before that restart.** Whatever shows before it, wait
for the home screen. Do not force a restart while the splash shows "Setting
up": an interrupted import starts again at the next start.

**The clock.** Linux cannot read a date from this phone's clock chip until
it has once been set from the network, so the first start begins at the date
the image was built. The phone sets its clock from the network (NTP) a few
seconds after WiFi connects, and keeps it from then on. An update check in
those first seconds can fail (the repository's files look "not valid yet");
it is simply tried again (Settings > Updates, or the next daily check).

Then, in Settings:

- **WiFi** (Settings > WiFi).
- **A lock screen PIN** (Settings > Lock screen). Until you set one, the
  lock screen is a swipe. The user is `duo`: no password, no administrator
  rights; nothing logs in over the network, and SSH is off (developer mode,
  below).
- **The time zone** (Settings > Language and region).

Updates come from Settings > Updates (OpenDuo's signed repository and
Debian's): [UPDATES.md](UPDATES.md). Android apps (optional, about 1.4 GB
over WiFi) need the administrator once: with developer mode on and SSH set
up (below), `sudo waydroid init -s GAPPS` (with Google Play) or
`-s VANILLA` (without); then open any Android app from the app grid
([android/](../android/)).

## 5. Developer mode

Settings > Developer is for people who help develop OpenDuo
([devmode/README.md](../devmode/README.md)): SSH (keys only, as the
administrator `duo2admin`, on WiFi and USB only; the firewall keeps every
other network out), the USB network, the keys that may log in, the developer
tools, and the bug report. All of it is off on a new install.

## If something goes wrong

- **The script stopped.** Its last line names the step and what to do.
  Before the typed `yes` nothing was written to the phone, and a boot from
  RAM ends with a long press on Power.
- **"no permissions"** (Linux): the udev rules are missing, or the adb
  server has not seen them yet: "USB permissions on Linux" above. Run the
  script again afterwards: it goes on where it stopped (a phone left in the
  WOA recovery stays there across the replug).
- **No home screen after the first start, or touch does not work.** The
  import probably failed (its log: `/var/log/duo2-stock-import.log`). The
  usual cause is an Android update that was never finished: put Android back
  ([RESTORE.md](RESTORE.md)), start it once, and install again. Open an issue
  with what you saw.
- **OpenDuo does not start at all.** The systemd-boot menu (one second at
  every start) has a second entry, a rescue shell over USB (a serial console
  and the USB network). A kernel from an update that fails is replaced by the
  previous one by itself ([UPDATES.md](UPDATES.md)).
- **Back to Android:** [RESTORE.md](RESTORE.md).
- Open an issue with what you did, what you saw, the script's output (it
  shows serial numbers masked) and, if you can reach the phone, the bug
  report (Settings > Developer > Collect a bug report).

## By hand (what the script does)

For a computer the script does not run on, or to see each step: the
commands are the script's own, `-s SERIAL` left out. Check every number
against your phone's output before you type it.

```sh
W=~/.cache/openduo/woa B=~/duo2-backup
fastboot boot $W/surfaceduo2-headless.img                  # in fastboot; then:
adb push $W/parted /tmp/parted && adb shell chmod 755 /tmp/parted

# the backup: the partition tables, then every partition but userdata and super
# (the recovery lists the whole disks, sda..., by name too: not those)
for d in sda sdb sdc sdd sde sdf sdg; do
    adb shell "/tmp/parted -s /dev/block/$d unit B print" > $B/parted-$d.txt
    adb shell "/tmp/parted -m -s /dev/block/$d unit s print" > $B/parted-$d.m.txt
done
adb exec-out "dd if=/dev/block/sda bs=4096 count=6 2>/dev/null" > $B/gpt-sda-head.bin
for p in $(adb shell ls /dev/block/by-name/ | tr -d '\r'); do
    case $p in userdata|super|esp|linux|sd[a-z]|mmcblk*) continue ;; esac
    adb exec-out "dd if=/dev/block/by-name/$p bs=4194304 2>/dev/null" > $B/$p.img
    [ "$(adb shell "sha256sum /dev/block/by-name/$p" | cut -c1-64)" = "$(sha256sum $B/$p.img | cut -c1-64)" ] &&
        echo "ok $p" || echo "DIFFERENT $p"
done

# the partitions: N, START and END from userdata's line in
#   adb shell /tmp/parted -m -s /dev/block/sda unit s print
# (4096-byte sectors: 1 GiB is 262144 of them)
P="/tmp/parted -s -a none /dev/block/sda"
adb shell "$P rm N"
adb shell "$P mkpart esp fat32 STARTs $((START + 262143))s"
adb shell "$P mkpart linux ext4 $((START + 262144))s ENDs"
adb shell "/tmp/parted -m -s /dev/block/sda unit s print"   # esp's number E, linux's L
adb shell "/tmp/parted -s /dev/block/sda set E esp on"

# the images (xz -dk unpacks them), each read back afterwards: compare
# every sha256 with sha256sum of the file on the computer
adb exec-in "dd of=/dev/block/sdaE bs=4194304" < openduo-VERSION-surface-duo2-esp.img
adb exec-in "dd of=/dev/block/sdaL bs=4194304" < openduo-VERSION-surface-duo2-rootfs.img
adb shell "head -c $(stat -c %s openduo-VERSION-surface-duo2-esp.img) /dev/block/sdaE | sha256sum"
adb shell "head -c $(stat -c %s openduo-VERSION-surface-duo2-rootfs.img) /dev/block/sdaL | sha256sum"
# the UEFI (15 MB, not a whole number of 4 MiB blocks: exec-in can lose the
# last, short block) through a copy on the phone
adb push $W/uefi_nosb.img /tmp/uefi.img
for s in a b; do
    adb shell "dd if=/tmp/uefi.img of=/dev/block/by-name/boot_$s bs=4194304 && sync"
    adb shell "head -c $(stat -c %s $W/uefi_nosb.img) /dev/block/by-name/boot_$s | sha256sum"
done
adb reboot
```

`uefi_nosb.img` is in the UEFI zip. On a Mac or under Windows the same
commands work with the platform-tools there (`shasum -a 256` instead of
`sha256sum` on a Mac); not tested.

## If the firmware import fails for good

`duo2-stock-import` needs the stock partitions as Android left them. When
they are damaged or incomplete, put Android back with Microsoft's recovery
image ([RESTORE.md](RESTORE.md), step 5: the sideload rewrites every stock
partition), start Android once, and install again.

For developers, the older way still works: an image that carries the
firmware, built on the computer from Microsoft's recovery image
(`scripts/payload-extract.py`, `scripts/collect-firmware.sh`,
`scripts/build-images.sh`; such an image is personal and must never be
shared). The same import also runs on the computer, from the unpacked
recovery image and the saved `persist`:

```sh
python3 -I stock/duo2-stock-import --images firmware/ota-2023.501.541/img \
    --persist ~/duo2-backup/persist.img --root /tmp/stock-out --touchd out/touchd
```
