# Installing OpenDuo on a Surface Duo 2

This guide takes a Surface Duo 2 from stock Android to OpenDuo. Read it to
the end, and read [RESTORE.md](RESTORE.md) as well, before you start.

> **Warning.**
> - Unlocking the bootloader **erases everything on the phone**.
> - Step 5 **deletes Android's user data partition**. Android can be put
>   back ([RESTORE.md](RESTORE.md)), but only with the backups of step 4.
> - Some partitions hold data that exists **only on your phone** (IMEI and
>   modem calibration, sensor calibration, the touch panels' alignment, the
>   factory WiFi and Bluetooth addresses). Without the step 4 backup, a
>   mistake there cannot be undone by anyone.
> - OpenDuo is a development preview. This guide was written from the
>   maintainer's own install; the full sequence has **not yet been repeated
>   end to end on a fresh phone**. Steps that are not scripted yet are
>   marked **TODO**. If anything looks different from what is described
>   here, stop and ask (open an issue) before you go on.

The Surface Duo 2 only. The original Surface Duo (SM8150) is a different
phone and is not supported.

## What you need

**The phone**

- A Surface Duo 2 on stock Android, updated to the last Microsoft release
  (2023.501.541; the scripts' defaults and this guide use that version, and
  other versions are untested).
- Charged above 60 %, and a USB-C data cable.

**A computer** (the "host"): Linux on x86_64. OpenDuo is developed on an
Ubuntu 24.04 host; other distributions should work with the same tools.

- `git`, `python3`, `adb` and `fastboot` (Debian/Ubuntu: `adb fastboot`,
  or Google's platform-tools), `unzip`, `curl`.
- Docker, and arm64 emulation for it (the root file system is built as
  arm64 under QEMU): for example `sudo apt install qemu-user-static
  binfmt-support`, or `docker run --privileged --rm tonistiigi/binfmt
  --install arm64`.
- About 60 GB of free disk space (the kernel tree and build, the unpacked
  stock firmware, the images and several Docker images) and a few hours for
  the first build.

**Downloads**

| What | Where | Used in |
|---|---|---|
| Microsoft's recovery image for your phone (a full OTA package, `ota_c1-12-customer_2023.501.541.zip` for the version above) | Microsoft's Surface recovery image page (support.microsoft.com, "Download a recovery image for your Surface"; it asks for the serial number) | steps 2, 10, and [RESTORE.md](RESTORE.md) |
| WOA Project's UEFI for the Surface Duo 2, release 2611.01, the "Secure Boot Disabled, Fast Boot" package | the WOA Project ([github.com/WOA-Project](https://github.com/WOA-Project), [mu_andromeda_platforms](https://github.com/WOA-Project/mu_andromeda_platforms) releases) | steps 8, 9 |
| WOA Project's headless recovery for the Surface Duo 2 (`surfaceduo2-headless.img`), its `parted`, and its file-system tools (`surfaceduo2-twrp-fssupport.tar`, only for repairs) | the WOA Project's Surface Duo 2 installation guide ([SurfaceDuo-Guides](https://github.com/WOA-Project/SurfaceDuo-Guides)) | steps 4, 5, 7 |
| Linux v7.3.4 source | kernel.org (stable tree) | step 6 |
| Android NDK r27c (to build the touch daemon) | developer.android.com/ndk | step 10 |

**TODO:** confirm the exact download pages of the WOA files (the headless
recovery, `parted` and the fssupport tarball) in the end-to-end test.

The files OpenDuo was developed with have these SHA-256 sums:

```
bd2f8bf24369ee3b48d46a4edca247081151712adfcc5f2b2e4d03bd6aabe14f  Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip
2be81b91abc40691b8829f6b870c6b1d8a668c6b23a4ff1fe22e7c9afe93e353  uefi_nosb.img (inside that zip)
d38809648d423b9700e1f2db7d810a9f406b2d0c08f47b9e9461617a5c317bef  surfaceduo2-headless.img
14fcc974bb9c87d26174a9bbe15659cf07d3a63eb5f950aaf1158257244f7b43  parted
70bd9878108aad0cea20e11cfecc6148725a9683dd86e2755ad73128f8bdb46e  surfaceduo2-twrp-fssupport.tar
```

Put them where the repository's scripts look by default (all of these
directories are ignored by git; nothing in them may ever be committed):

```
firmware/ota-2023.501.541/        the OTA's payload.bin (step 2 unpacks it into img/)
firmware/uefi/2611.01/            the UEFI zip
firmware/woa-tools/               surfaceduo2-headless.img, parted, surfaceduo2-twrp-fssupport.tar
firmware/partitions/              your phone's partition backups (step 4)
```

## Two things to know first

**Fastboot with the buttons.** Hold **Power for about 20 seconds**, until the
phone vibrates; let go; then hold **only Volume Down**. The phone shows the
bootloader and `fastboot devices` lists it (USB id `045e:0c2f`). Never press
both volume keys together while it starts: that enters the Qualcomm
emergency download mode (`05c6:9008`); if it happens, hold Power and Volume
Down for about 30 seconds.

**`fastboot flash` wedges this bootloader.** After any `fastboot flash`
finishes, the Surface Duo 2's bootloader stops answering (`getvar` and
`reboot` hang), although the write itself completed. Force it back into
fastboot with the buttons (above) after every flash. That is why this guide
writes partitions with `dd` from the WOA recovery instead. `fastboot boot`
(start an image from RAM, write nothing) is not affected.

## 1. Back up your data (stock Android)

Turn on Developer options (in Settings, under About, tap "Build number"
seven times) and, in Developer options, **USB debugging**; connect the phone
and accept the computer's key on the phone.

```sh
git clone https://github.com/Andy1210/OpenDuo.git
cd OpenDuo
adb devices                                   # note the phone's serial
ADB_SERIAL=<serial> scripts/backup-userdata.sh /path/to/backup/dir
```

`scripts/backup-userdata.sh` only reads from the phone. It copies the shared
storage (photos, downloads, documents...), the contacts, SMS and call log as
text, and the lists of installed apps, then compares file counts and sizes
(`VERIFY.txt`). It cannot copy app data that Android keeps private (no root):
use the apps' own backups for those. Run it again to continue an interrupted
backup.

Optional: `scripts/probe-android.sh` records the phone's hardware facts
(read only, no root) into `dumps/`, which is useful when something differs
from this guide. `dumps/` holds identifiers: never commit or post it.

## 2. Unpack the stock firmware (host)

OpenDuo needs files from Microsoft's stock images: DSP, modem, WiFi,
Bluetooth, GPU and video firmware, the touch library and the sensor
configuration. They may not be redistributed, so you take them from the
recovery image of your own phone.

```sh
mkdir -p firmware/ota-2023.501.541
unzip -d firmware/ota-2023.501.541 /path/to/ota_c1-12-customer_2023.501.541.zip payload.bin
python3 -I scripts/payload-extract.py firmware/ota-2023.501.541/payload.bin \
    firmware/ota-2023.501.541/img
```

`payload-extract.py` writes one `.img` per partition and checks each against
the SHA-256 in the package (every line must say `ok`).

Then take the firmware out of three of those images into `firmware/stock/`
and assemble the tree OpenDuo installs into `/lib/firmware`.
**TODO: the first part is not a script yet.** These commands (mtools and
e2fsprogs on the host) reproduce, byte for byte, the tree the maintainer's
phone runs:

```sh
I=firmware/ota-2023.501.541/img
mkdir -p firmware/stock/modem firmware/stock/bt firmware/stock/vendor
MTOOLS_SKIP_CHECK=1 mcopy -s -n -i $I/modem.img ::image firmware/stock/modem/
MTOOLS_SKIP_CHECK=1 mcopy -s -n -i $I/bluetooth.img ::image firmware/stock/bt/
debugfs -R "rdump /firmware firmware/stock/vendor" $I/vendor.img
scripts/collect-firmware.sh              # firmware/stock -> firmware/linux
```

`collect-firmware.sh` ends with `firmware tree: .../firmware/linux (269
files, ...)`.

## 3. Unlock the bootloader (erases the phone)

1. In Developer options, turn on **OEM unlocking**.
2. Unlock:

   ```sh
   adb reboot bootloader
   fastboot devices                  # the phone is listed
   fastboot flashing unlock          # confirm on the phone with the volume keys and Power
   fastboot getvar unlocked          # "unlocked: yes"
   ```

The phone erases itself and starts Android as new. You do not need to set
it up again for the next step.

## 4. Back up the phone's own partitions

Start the WOA headless recovery **from RAM** (it writes nothing by itself)
and get a root shell over adb:

```sh
fastboot boot firmware/woa-tools/surfaceduo2-headless.img
adb devices                          # the phone is listed as "recovery"
adb push firmware/woa-tools/parted /tmp/parted
adb shell chmod 755 /tmp/parted
```

**Save the partition tables** of every storage unit (you need the numbers
in [RESTORE.md](RESTORE.md)):

```sh
B=firmware/partitions
mkdir -p $B
for d in sda sdb sdc sdd sde sdf; do
    adb shell "/tmp/parted -s /dev/block/$d unit B print" > $B/parted-$d.txt
done
```

**Save the partitions.** These hold data unique to your phone and must be
saved: `persist` (sensor calibration and more), `modemst1`, `modemst2`,
`fsg`, `fsc` (the modem's file system: IMEI and radio calibration), `sfpd`
(the touch panels' alignment, the factory WiFi and Bluetooth addresses),
`devinfo`, `misc`. Save `boot_a` and `boot_b` too: step 9 overwrites them.
The maintainer saved every partition except `userdata` and `super` (69 of
them). **TODO: this is not a script yet**; the loop below does the same:

```sh
B=firmware/partitions
for p in $(adb shell ls /dev/block/by-name/ | tr -d '\r'); do
    case $p in userdata|super) continue ;; esac
    adb exec-out "dd if=/dev/block/by-name/$p bs=4194304 2>/dev/null" > "$B/$p.img"
    phone=$(adb shell "sha256sum /dev/block/by-name/$p" | cut -c1-64)
    host=$(sha256sum "$B/$p.img" | cut -c1-64)
    [ "$phone" = "$host" ] && echo "ok   $p" || echo "DIFFERENT $p"
done
(cd $B && sha256sum *.img > SHA256SUMS)
```

Every line must say `ok`. Then copy `firmware/partitions/` to at least one
more place that is not the phone and not this computer's only disk. Treat it
like a password: it holds your phone's identifiers. Never commit, share or
post it.

Keep the recovery running for the next step.

## 5. Partition: replace `userdata` with `esp` and `linux`

This deletes Android's user data partition (already empty after the unlock)
and puts two partitions in its place on the first storage unit (`sda`):

| Name | File system | Size | Holds |
|---|---|---|---|
| `esp` | FAT32 (with 4096-byte sectors), ESP flag | 1 GiB | systemd-boot, kernels, device trees, initramfs |
| `linux` | ext4 | the rest | OpenDuo's root file system |

The names matter (OpenDuo mounts `PARTLABEL=esp` and `PARTLABEL=linux`) and
must not contain spaces: the bootloader fails on partition names with
spaces. Android's other partitions (`super`, `vendor_boot`, `boot_a/b`,
`modem`, `dsp`...) are not touched.

**TODO: the exact commands below are reconstructed from the maintainer's
notes and must be confirmed in the end-to-end test.** Check every number
against your own `parted-sda.txt` before you type it.

```sh
adb shell /tmp/parted -s /dev/block/sda unit MiB print
```

Find the `userdata` partition (number 8 on the maintainer's phone) and note
its number, Start and End. With `N`, `START` and `END` from that line (in
MiB):

```sh
adb shell /tmp/parted -s /dev/block/sda rm N
adb shell /tmp/parted -s /dev/block/sda mkpart esp fat32 STARTMiB $((START + 1024))MiB
adb shell /tmp/parted -s /dev/block/sda mkpart linux ext4 $((START + 1024))MiB ENDMiB
adb shell /tmp/parted -s /dev/block/sda set N esp on
adb shell /tmp/parted -s /dev/block/sda unit MiB print
```

The new list must show `esp` (1024 MiB) and `linux` where `userdata` was,
and nothing else changed. Check that the recovery sees them:
`adb shell ls -l /dev/block/by-name/esp /dev/block/by-name/linux`.

## 6. Build OpenDuo (host)

There is no prebuilt image: an image holds your phone's firmware (step 2)
and your SSH key, so it is personal and must never be shared.

**One-time preparation**

```sh
docker build -t duo2-build build/          # the kernel and tools container

# The kernel: Linux v7.3.4 with OpenDuo's patches, in src/ (ignored by git)
git clone --depth 1 --branch v7.3.4 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git src/linux
git -C src/linux switch -c duo2
git -C src/linux am "$PWD"/kernel/patches/*.patch

# Your key for SSH (step 8 onwards): ~/.ssh/id_ed25519.pub is used by default
[ -f ~/.ssh/id_ed25519.pub ] || ssh-keygen -t ed25519

cp local.env.example local.env             # never committed
```

In `local.env`, set:

```sh
DUO2_HOST=duo2admin@10.15.19.1             # the phone on the USB cable
DUO2_DEVELOPER_MODE=on                     # SSH and the USB network on from the first boot
```

Developer mode is needed for the first install: until the touch runtime is
installed (step 10), the touch screen does not work, so the phone can only
be reached over SSH. You can switch it off in Settings afterwards.

**Build**

```sh
scripts/build-kernel.sh                    # out/linux: Image, device trees, modules
scripts/build-images.sh                    # out/images/esp.img, linux.img (+ sparse .simg)
```

`build-images.sh` also builds the compositor, the sensor services, the eSIM
tool and the camera packages the first time (some of it arm64 under QEMU:
expect an hour or more). `AUTHORIZED_KEYS=/path/key.pub` picks another
public key; `DUO2_USER=name` another name for the phone's user (default
`duo`).

**TODO:** the image recipe was last compared file by file with a running
phone on 2026-11-07; the phone has been updated component by component
since. A fresh image has to be built, flashed and checked against this guide
in the end-to-end test.

## 7. Write the images

Still in the WOA recovery (if it is gone: fastboot with the buttons, then
`fastboot boot firmware/woa-tools/surfaceduo2-headless.img` again):

```sh
for p in esp linux; do
    adb exec-in "dd of=/dev/block/by-name/$p bs=4194304" < out/images/$p.img
    size=$(stat -c %s out/images/$p.img)
    phone=$(adb shell "head -c $size /dev/block/by-name/$p | sha256sum" | cut -c1-64)
    host=$(sha256sum out/images/$p.img | cut -c1-64)
    [ "$phone" = "$host" ] && echo "ok   $p" || echo "DIFFERENT $p"
done
```

Both must say `ok`. (The root image is 3 GiB; it grows to fill the `linux`
partition by itself on the first boot.)

The alternative is `fastboot flash esp out/images/esp.simg` and `fastboot
flash linux out/images/linux.simg`, with a forced return to fastboot after
each flash (see "Two things to know first").

## 8. First boot, from RAM

Go back to fastboot (`adb reboot bootloader` from the recovery, or the
buttons), unpack `uefi_nosb.img` from the UEFI zip, and start it **from RAM**:

```sh
fastboot boot uefi_nosb.img
```

The UEFI starts systemd-boot from the `esp` partition, which starts OpenDuo
(its menu shows for one second; the first entry is the system, the second a
rescue shell over USB, used when the system does not come up). The first boot takes longer: the root file system
grows to the whole partition, and SSH host keys are made. The panels keep
the UEFI's picture until the OpenDuo splash and then the home screen appear.
**Touch does not work yet** (step 10).

Connect over USB: the phone is `10.15.19.1` and gives the computer an
address on the cable.

```sh
ssh duo2admin@10.15.19.1
```

`duo2admin` is the administrator (SSH keys only, sudo); the phone's user
(`duo`) has no password and no sudo. Nothing has been written to the boot
partitions yet: if this boot fails, hold Power to switch the phone off, go to
fastboot with the buttons and try again. (A normal start now tries to boot
Android, which cannot work any more without its data partition: that is
expected, and step 9 replaces it.)

## 9. Make OpenDuo start by itself

So far the UEFI only runs when the computer sends it. To make every start
boot OpenDuo, write the UEFI into **both** boot slots. This replaces
Android's boot images (your copies are in `firmware/partitions/boot_a.img`
and `boot_b.img`).

**TODO: confirm these commands in the end-to-end test** (the maintainer
wrote the slots from OpenDuo with `dd` and read them back):

```sh
scp uefi_nosb.img duo2admin@10.15.19.1:/tmp/
ssh duo2admin@10.15.19.1 'set -e
    for s in a b; do
        sudo dd if=/tmp/uefi_nosb.img of=/dev/disk/by-partlabel/boot_$s bs=4M conv=fsync
        size=$(stat -c %s /tmp/uefi_nosb.img)
        sudo head -c $size /dev/disk/by-partlabel/boot_$s | sha256sum
    done
    sha256sum /tmp/uefi_nosb.img'
```

All three sums must be the same. From now on, every start of the phone
boots OpenDuo; fastboot with the buttons stays available for emergencies.

Only a cold restart works on this UEFI (a "warm" reboot halts), and OpenDuo
is set up for that. A kernel that fails is caught by the boot entry's
counting (`scripts/duo2-try` in [CONTRIBUTING.md](../CONTRIBUTING.md)) and by
the rescue entry.

## 10. Install the parts that come from your phone

These are built on the host from your stock images and sent to the phone
over SSH. They are never in an image or in git. The scripts find the phone
through `local.env` (step 6).

```sh
# WiFi (the passphrase is read from stdin; give both names of a dual-band router)
echo 'your-passphrase' | ssh duo2admin@10.15.19.1 sudo duo2-wifi-join MyNetwork5G MyNetwork

# Touch and pen: Microsoft's heat-map library with its Android runtime, and the daemon
NDK=/path/to/android-ndk-r27c scripts/build-touch-runtime.sh
scripts/install-touch-runtime.sh

# Sensors and postures: the sensor DSP's configuration and your phone's calibration
# (read from firmware/partitions/persist.img, saved in step 4)
scripts/build-sensors-runtime.sh
scripts/install-sensors.sh

# Cameras: the sensors' register tables
scripts/build-camera-tables.sh --install

# Optional: Microsoft's power figures for the battery usage page
scripts/build-power-profile.sh && scripts/install-power-profile.sh

# Your time zone
ssh duo2admin@10.15.19.1 sudo timedatectl set-timezone Region/City
```

The factory WiFi and Bluetooth addresses are read from the phone's `sfpd`
partition by themselves on the first boot (Bluetooth at once, WiFi from the
second boot); `scripts/install-factory-addr.sh` does it by hand.

After the touch runtime is installed, touch and the pen work at once.

**Android apps** (optional): once, as the administrator, with WiFi up:
`sudo waydroid init -s GAPPS` (with Google Play) or `-s VANILLA` (without),
about 1.4 GB. Then open "Waydroid" or any Android app from the app grid
([android/](../android/)).

**TODO:** the scripts in this step were run on the maintainer's phone one
by one as they were written, not as one sequence on a fresh install; the
end-to-end test confirms the order and the flags.

## 11. Developer mode

Settings > Developer is for people who help develop OpenDuo
([devmode/README.md](../devmode/README.md)): SSH (keys only, as `duo2admin`,
on WiFi and USB only; the firewall keeps every other network out), the USB
network, the list of keys that may log in, the developer tools, and the bug
report.

- The image of step 6 had developer mode on. When you do not need SSH any
  more, switch **Developer mode** off: SSH and the USB network go off with
  it.
- To use it later: Settings > Developer > Developer mode on, then SSH on
  (the shell asks you to confirm), add your computer's public key (paste it
  or pick the `.pub` file), and connect with `ssh duo2admin@ADDRESS`, the
  address shown on the page, or `10.15.19.1` on a cable with USB networking
  on.
- **Collect a bug report** writes a report with identifiers masked into
  Downloads, for an issue.

## If something goes wrong

- **A boot from RAM** (`fastboot boot`) writes nothing: a long press on
  Power ends it.
- **OpenDuo does not come up** after step 9: the systemd-boot menu's second
  entry is a rescue shell over USB (a serial console and the USB network).
  **TODO:** describe how to pick it with the buttons (the menu shows for one
  second, and Volume Down at the wrong moment goes to fastboot instead).
  Or go to fastboot with the buttons, start the WOA recovery from RAM, and
  repair the root file system from there (the recovery's own `e2fsck` is too
  old for OpenDuo's ext4 features: use a current static `e2fsck`).
- **Back to Android:** [RESTORE.md](RESTORE.md).
- Open an issue with what you did, what you saw, and (if you can reach the
  phone) the bug report.

## What is not scripted yet (TODO summary)

1. Extracting `firmware/stock/` from the OTA images (step 2): commands, no script.
2. Backing up the partitions (step 4): a loop, no script.
3. The partitioning commands (step 5): to be confirmed.
4. The image recipe: last compared with a running phone on 2026-11-07 (step 6).
5. Writing the UEFI into the boot slots (step 9): to be confirmed.
6. The post-install sequence (step 10): to be run once on a fresh install.
7. The download locations of the WOA files.
8. Picking the rescue entry with the buttons.
