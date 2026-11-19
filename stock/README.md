# stock: the phone's own firmware, from its stock partitions

OpenDuo needs files that only Microsoft and Qualcomm may distribute. No
OpenDuo image, package or repository carries them. `duo2-stock-import` reads
them from the phone's own stock partitions, which stay on the phone when
OpenDuo is installed (only Android's `userdata` is replaced). It never
writes those partitions.

| What | From | Installed as |
|---|---|---|
| DSP, modem, WLAN, Bluetooth, GPU and video firmware, modem configuration | `modem_<slot>` (FAT), `bluetooth_<slot>` (FAT), `vendor` in `super` (`/firmware`) | `/usr/lib/firmware/{qcom,qca,ath11k}/...` ([scripts/collect-firmware.sh](../scripts/collect-firmware.sh)) |
| Camera register tables | `vendor` (`/lib64/camera/com.qti.sensormodule.*.bin`) | `/usr/lib/firmware/qcom/sm8350/microsoft/imx*.bin` |
| Touch runtime: Microsoft's heat processor, the stock linker and libraries | `vendor`, `system`, the VNDK apex in `system_ext` | `/opt/duo2-touch` (with `duo2-touchd` and its liblog shim from the touch package) |
| Sensor DSP configuration and THIS phone's sensor registry | `vendor` (`/etc/sensors`), `dsp_<slot>` (`/sdsp`), `persist` (`/sensors/registry`) | `/usr/share/qcom/sm8350/Microsoft/surfaceduo2` |
| Power profile | the vendor overlay in `vendor` (or `framework-res.apk` in `system`) | `/opt/duo2-power-profile/power_profile.xml` |

The factory WiFi and Bluetooth addresses and the touch panels' alignment
come from `sfpd` at every boot already (`duo2-factory-addr`,
`duo2-touch-calibration`).

## When it runs

`duo2-stock-import.service` runs once, at the first boot of a new install,
after the root file system has grown to its partition and before the
compositor, touch and the sensors start. The import takes about a minute; then
the phone restarts once, and the drivers find their firmware. The marker
`/var/lib/duo2-stock/imported` (JSON: the slot, the stock release, the parts)
keeps it from running again; the log is `/var/log/duo2-stock-import.log`.

By hand, as root:

```sh
sudo duo2-stock-import --check     # read only: which slot, do the images match 2023.501.541
sudo duo2-stock-import             # import again (then restart the phone)
```

## How it finds the files

- **The slot.** The one Android last booted: the "active" bit (50) of
  `boot_a` / `boot_b` in the GPT. Installing OpenDuo writes the UEFI into the
  boot partitions but never their GPT entries, so the bit survives. If that
  slot does not give a usable set, the other one is tried.
- **super** holds the dynamic partitions (Virtual A/B). Its LP metadata
  (one metadata slot per A/B slot; the checksums are checked) lists the
  extents of `vendor_<slot>`, `system_<slot>` and `system_ext_<slot>`; a
  partition may be spread over several extents (`system` is in six on the
  maintainer's phone), so each one is copied into a file under
  `/var/lib/duo2-stock/` (about 3.3 GB, deleted afterwards) and read with
  `debugfs`. The FAT partitions are read with `mcopy`.
- **The release.** Every image's sha256 is compared with Microsoft's
  2023.501.541 images, the release OpenDuo is tested with (on the
  maintainer's phone the active slot matched all six byte for byte). Another
  release is imported too, with a warning in the log and the marker.
- **The sensor registry** must belong to the vendor's `sns_reg_config`: the
  registry's own `sns_reg_version` must give the config's version. The copy
  of the config that Android keeps beside the registry is not read by the
  sensor DSP and is not always refreshed when Android rebuilds the registry,
  so an older copy is only noted in the log (issue #1).

## When it fails

The phone still starts, without touch, WiFi or sensors. The usual cause is an
Android update that was installed but never finished (Android was not started
after it before the bootloader was unlocked): `super` then does not hold the
whole update. Start Android once after an update before you install OpenDuo.
The manual way ([docs/INSTALL.md](../docs/INSTALL.md), "If the import
fails") runs the same tool on a computer, from Microsoft's recovery image:

```sh
python3 -I stock/duo2-stock-import --images DIR --persist persist.img --root OUT --touchd DIR
```

## Files

| Source | On the phone |
|---|---|
| `duo2-stock-import`, `duo2stock.py`, `README.md` | `/usr/local/lib/duo2-stock/`, a link in `/usr/local/sbin/` |
| `scripts/collect-firmware.sh`, `mdt2mbn.py`, `camx-sensor-tables.py`, `camx-module-dump.py`, `axml2xml.py` (staged from the repository's `scripts/`) | `/usr/local/lib/duo2-stock/scripts/` |
| `duo2-stock-import.service` | `/etc/systemd/system/` (enabled by the package `openduo-stock-surface-duo2`; not in an image built with the firmware) |

Tests: `cd stock && python3 -m unittest discover -s tests` (the GPT bits, the
LP metadata, the extent copy, the ELF reader, the firmware merge). With
`DUO2_STOCK_IMAGES` (an unpacked OTA's `img/`), `DUO2_STOCK_PERSIST` and
`debugfs` + `mcopy` in `PATH`, the whole import also runs on real images.
