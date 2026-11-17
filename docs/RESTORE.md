# Back to stock Android

This guide puts a Surface Duo 2 that runs OpenDuo back to Microsoft's
Android. It needs the backups made during the install
([INSTALL.md](INSTALL.md), step 4: `firmware/partitions/`) and Microsoft's
recovery image for the phone (the OTA package, for example
`ota_c1-12-customer_2023.501.541.zip`).

> **Warning.**
> - This **erases OpenDuo and everything stored in it**: copy your files off
>   first (step 1).
> - The procedure has **not yet been carried out end to end**; it follows
>   from what the install changed. Steps that are not confirmed are marked
>   **TODO**. If anything looks different from what is described here, stop
>   and ask before you go on.
> - Never write the modem partitions (`modemst1`, `modemst2`, `fsg`, `fsc`)
>   or `persist` unless Android shows that they are damaged (step 7): they
>   are unique to your phone.

## What OpenDuo changed

The install changed exactly three things, and only these have to be undone:

| Changed | How it is undone |
|---|---|
| `userdata` (on `sda`) was replaced by `esp` and `linux` | step 3: `userdata` is made again with its original bounds |
| `boot_a` and `boot_b` hold the WOA UEFI | step 4: your saved `boot_a.img` and `boot_b.img` go back (or the OTA's `boot.img`) |
| Android's data was erased by the bootloader unlock | step 5: Android sets itself up as new |

Everything else (the bootloaders, `vendor_boot`, `dtbo`, `super`, `modem`,
`dsp`, `persist`, ...) is the same as before. While OpenDuo ran, the modem
kept its own file system in `modemst1` / `modemst2` up to date, as it does
under Android; that is normal use, not a change to undo.

## 1. Save your data from OpenDuo

Copy what you want to keep to another computer, for example with `scp` or
`rsync` over developer mode's SSH ([INSTALL.md](INSTALL.md), step 11), or set
up `duo2-backup` ([maint/](../maint/)). If your home folder is encrypted,
unlock the phone first. Android cannot read OpenDuo's file system.

## 2. Start the WOA recovery

Go to fastboot: hold **Power for about 20 seconds** until the phone
vibrates, let go, then hold **only Volume Down**. Then, on the computer:

```sh
fastboot boot firmware/woa-tools/surfaceduo2-headless.img
adb devices                          # listed as "recovery"
adb push firmware/woa-tools/parted /tmp/parted
adb shell chmod 755 /tmp/parted
```

## 3. Make `userdata` again

Open your saved partition table, `firmware/partitions/parted-sda.txt`, and
find the `userdata` line: its number, Start and End (in bytes). Then look at
the table as it is now:

```sh
adb shell /tmp/parted -s /dev/block/sda unit B print
```

`esp` and `linux` stand where `userdata` was. **TODO: confirm these commands
in the end-to-end test.** With `E` and `L` the numbers of `esp` and `linux`
now, and `START` / `END` the bytes from the saved `userdata` line:

```sh
adb shell /tmp/parted -s /dev/block/sda rm L
adb shell /tmp/parted -s /dev/block/sda rm E
adb shell /tmp/parted -s /dev/block/sda mkpart userdata STARTB ENDB
adb shell /tmp/parted -s /dev/block/sda unit B print
```

Compare the result with `parted-sda.txt` line by line: the same numbers,
names, starts and ends as before the install. (**TODO:** check whether the
partition's type GUID needs to match the original; Android finds it by name.)

## 4. Put Android's boot images back

Write your saved images to both slots and read them back:

```sh
B=firmware/partitions
for s in a b; do
    adb exec-in "dd of=/dev/block/by-name/boot_$s bs=4194304" < $B/boot_$s.img
    phone=$(adb shell "sha256sum /dev/block/by-name/boot_$s" | cut -c1-64)
    host=$(sha256sum $B/boot_$s.img | cut -c1-64)
    [ "$phone" = "$host" ] && echo "ok   boot_$s" || echo "DIFFERENT boot_$s"
done
```

Without the saved images, use the stock `boot.img` from the recovery image
for both slots (`python3 -I scripts/payload-extract.py payload.bin img boot`
writes `img/boot.img`), with the same commands.

## 5. Reset Android and, if needed, reinstall it

Android's recovery lives in the boot image you just put back.

1. Restart into Android's recovery. **TODO: confirm the way in the
   end-to-end test**: from fastboot, select "Recovery mode" with the volume
   keys and confirm with Power, or run `adb reboot recovery` from the WOA
   recovery.
2. Choose **Wipe data/factory reset**. This formats `userdata` (and the
   `metadata` that goes with it); Android cannot start on the empty
   partition without it.
3. Recommended: choose **Apply update from ADB** and send Microsoft's
   recovery image, which reinstalls every Android partition:

   ```sh
   adb sideload ota_c1-12-customer_2023.501.541.zip
   ```

4. Choose **Reboot system now**. The first start takes several minutes;
   Android then asks to be set up.

The other way to put the stock images back is `fastboot flash` with the
images that `payload-extract.py` writes. Remember that every `fastboot flash`
leaves this bootloader unresponsive until you return to fastboot with the
buttons, and that the system, vendor, product, odm and system_ext images live
inside `super` and need fastbootd (`fastboot reboot fastboot`, from Android's
recovery). The sideload above does all of it in one step.

## 6. Check Android

- Dial `*#06#`: the IMEI must be shown.
- WiFi and Bluetooth find networks and devices; calls and mobile data work.
- Touch works on both panels, and rotating and folding the phone is noticed.

Restore your data from the backup of [INSTALL.md](INSTALL.md) step 1 (the
files in shared storage; contacts and messages were saved as text lists).

## 7. Only if something unique is damaged

If the IMEI is missing, the modem never registers, or the sensors or touch
behave as if uncalibrated **after** step 5, the phone's own data may be
damaged. Then, and only then, write back the matching saved partitions from
the WOA recovery, one at a time, with the same `dd` and checksum commands as
in step 4: `modemst1`, `modemst2`, `fsg`, `fsc` for the modem, `persist`
for the sensors, `sfpd` for the touch alignment. Ask in an issue first if
you are unsure: a wrong image in these partitions can disable the radio.

## 8. Lock the bootloader again (optional)

You do not have to: an unlocked phone runs stock Android normally; it shows
a warning at every start, and some apps refuse to run on it.

> **Warning.** Locking **erases the phone again**. And if anything on the
> phone is not exactly Microsoft's signed stock (a boot slot still holding
> the UEFI, a partition from another version), a locked bootloader refuses to
> start it, and the phone may not start at all. Lock only when Android has
> started normally after step 5, from both boot slots (step 4 restored
> both), with Microsoft's recovery image applied.

```sh
adb reboot bootloader
fastboot flashing lock               # confirm on the phone
```

The phone erases itself and starts Android as new. Turn off OEM unlocking
in Developer options afterwards if you like.
