# Back to stock Android

This guide puts a Surface Duo 2 that runs OpenDuo back to Microsoft's
Android: about 10 minutes, plus Android's own first start. It needs the
backup `scripts/install-openduo.sh` made
([INSTALL.md](INSTALL.md), step 3: the `--backup` directory) and, for a clean
reinstall of every Android partition, Microsoft's recovery image for the
phone (the OTA package `ota_c1-12-customer_2023.501.541.zip`, from
Microsoft's "Download a recovery image for your Surface" page; it asks for
the serial number).

> **Warning.**
> - This **erases OpenDuo and everything stored in it**: copy your files off
>   first (step 1).
> - Steps 2 to 5 were carried out once, on the maintainer's phone
>   (November 2026), without the optional sideload of step 5 (no recovery
>   image at hand): the factory reset worked and Android started as new.
>   Step 8 (locking) is **not tested**. If anything looks different from
>   what is described here, stop and ask before you go on.
> - Never write the modem's partitions (`modemst1`, `modemst2`, `fsg`,
>   `fsc`), `persist` or `sfpd` unless Android shows that they are damaged
>   (step 7): they are unique to your phone.

## What OpenDuo changed

The install changed exactly three things:

| Changed | Undone in |
|---|---|
| `userdata` (on `sda`) was replaced by `esp` and `linux` | step 3: `userdata` is made again with its saved bounds |
| `boot_a` and `boot_b` hold the WOA UEFI | step 4: the saved `boot_a.img` and `boot_b.img` go back |
| Android's data was erased by the bootloader unlock | step 5: Android sets itself up as new |

Everything else (the bootloaders, `vendor_boot`, `dtbo`, `super`, `modem`,
`dsp`, `persist`, ...) is as before; OpenDuo only read it. While OpenDuo ran,
the modem kept its own files in `modemst1` / `modemst2` up to date, as it
does under Android: that is normal use, nothing to undo.

## 1. Save your data from OpenDuo

Copy what you want to keep to another computer, for example with `scp` or
`rsync` over developer mode's SSH ([INSTALL.md](INSTALL.md), step 5), or with
`duo2-backup` ([maint/](../maint/)). If your home folder is encrypted, unlock
the phone first. Android cannot read OpenDuo's file system.

## 2. Start the WOA recovery

Go to fastboot: hold **Power for about 20 seconds** until the phone vibrates,
let go, then hold **only Volume Down**. On the computer (the WOA files are
where the install script put them):

```sh
W=~/.cache/openduo/woa B=~/duo2-backup        # your --backup directory
fastboot devices                              # the phone
fastboot boot $W/surfaceduo2-headless.img
adb devices                                   # listed as "recovery"
adb push $W/parted /tmp/parted && adb shell chmod 755 /tmp/parted
```

With more than one phone on the computer, add `-s SERIAL` to every `adb`
and `fastboot` command. On Linux, "no permissions" from adb or fastboot means
the udev rules are missing: [INSTALL.md](INSTALL.md), "USB permissions on
Linux" (the recovery is USB vendor `18d1`, the bootloader `045e`).

## 3. Make `userdata` again

The saved table says where `userdata` was: its line in
`$B/parted-sda.m.txt` (`NUMBER:STARTs:ENDs:...:userdata:;`). Look at the
table now:

```sh
grep ':userdata:' $B/parted-sda.m.txt
adb shell /tmp/parted -m -s /dev/block/sda unit s print
```

`esp` and `linux` stand where `userdata` was, with the same first and last
sector. A backup made by hand with the older guide has only
`parted-sda.txt`, in bytes: there START is the Start in bytes divided by
4096, and END is (the End in bytes + 1) divided by 4096, minus 1; or simply
the first sector of `esp` and the last of `linux` in today's table. With `E` and `L` their numbers now and `START` / `END` the sectors
of the saved `userdata` line:

```sh
P="/tmp/parted -s -a none /dev/block/sda"
adb shell "$P rm L"
adb shell "$P rm E"
adb shell "$P mkpart userdata ${START}s ${END}s"
adb shell /tmp/parted -m -s /dev/block/sda unit s print
```

Compare the result with `parted-sda.m.txt` line by line: the same numbers,
names, starts and ends. The new `userdata` has Linux's data type GUID, not
the original one: that is fine (tried: Android finds it by name, and the
factory reset of step 5 formats it). `$B/gpt-sda-head.bin` holds sda's GPT as
it was before the install.

## 4. Put Android's boot images back

Write the saved images into both slots and read them back. The check reads
exactly as many bytes as the image has (`head -c`): an image can be smaller
than its partition.

```sh
for s in a b; do
    img=$B/boot_$s.img
    adb exec-in "dd of=/dev/block/by-name/boot_$s bs=4194304" < $img
    phone=$(adb shell "head -c $(stat -c %s $img) /dev/block/by-name/boot_$s | sha256sum" | cut -c1-64)
    [ "$phone" = "$(sha256sum $img | cut -c1-64)" ] && echo "ok boot_$s" || echo "DIFFERENT boot_$s"
done
```

Without the saved images, use the stock `boot.img` from the recovery image
for both slots (`python3 -I scripts/payload-extract.py payload.bin img boot`
writes `img/boot.img`): the same loop, with `img=img/boot.img`.
Release 2023.501.541's `boot.img` is 96 MiB in a 128 MiB partition: a
`sha256sum` of the whole partition never matches it, the `head -c` one does.
A DIFFERENT line: write that slot again.

## 5. Reset Android and, if needed, reinstall it

Android's recovery lives in the boot image you just put back.

1. Go to Android's recovery **with the buttons**: hold Power for about 20
   seconds until the phone vibrates, let go, hold only Volume Down (fastboot),
   then select **Recovery mode** with the volume keys and confirm with Power.
   (`adb reboot recovery` from the WOA recovery does not work: it hangs.)
2. If the phone shows **"No command"** (a robot lying down): hold Power and
   press Volume Up once; the recovery's menu appears.
3. Choose **Wipe data/factory reset**. This formats `userdata` (and the
   `metadata` that goes with it); Android cannot start on the empty partition
   without it.
4. Optional: **Apply update from ADB**, and send Microsoft's recovery image,
   which rewrites every Android partition (a clean reinstall; not needed when
   only OpenDuo's changes are undone, and not tried yet with this guide):

   ```sh
   adb sideload ota_c1-12-customer_2023.501.541.zip
   ```

5. **Reboot system now**. The first start takes several minutes; Android then
   asks to be set up.

The other way to put the stock images back is `fastboot flash` with the
images that `payload-extract.py` writes. Every `fastboot flash` leaves this
bootloader unresponsive until you go back to fastboot with the buttons, and
the system, vendor, product, odm and system_ext images live inside `super`
and need fastbootd (`fastboot reboot fastboot`, from Android's recovery).
The sideload does all of it in one step.

## 6. Check Android

- Dial `*#06#`: the IMEI must be shown.
- WiFi and Bluetooth find networks and devices; calls and mobile data work.
- Touch works on both panels; rotating and folding the phone is noticed.

Restore your data from the backup of [INSTALL.md](INSTALL.md), step 1 (the
files in shared storage; contacts and messages were saved as text).

To install OpenDuo again later: start Android once (so an update it installs
is finished), then [INSTALL.md](INSTALL.md) from step 3, with the same
`--backup` directory (the earlier backup is kept).

## 7. Only if something unique is damaged

If the IMEI is missing, the modem never registers, or the sensors or touch
behave as if uncalibrated **after** step 5, the phone's own data may be
damaged. Then, and only then, write back the matching saved partitions from
the WOA recovery, one at a time, with the same `dd` and checksum commands as
in step 4: `modemst1`, `modemst2`, `fsg`, `fsc` for the modem, `persist` for
the sensors, `sfpd` for the touch alignment. Ask in an issue first if you are
unsure: a wrong image in these partitions can disable the radio.

## 8. Lock the bootloader again (optional)

You do not have to: an unlocked phone runs stock Android normally; it shows a
warning at every start, and some apps refuse to run on it.

> **Warning.** Locking **erases the phone again**. And if anything on the
> phone is not exactly Microsoft's signed stock (a boot slot still holding
> the UEFI, a partition from another release), a locked bootloader refuses
> to start it, and the phone may not start at all. Lock only when Android has
> started normally after step 5, from both boot slots, with Microsoft's
> recovery image applied.

```sh
adb reboot bootloader
fastboot flashing lock               # confirm on the phone
```

The phone erases itself and starts Android as new. Turn off OEM unlocking in
Developer options afterwards if you like.
