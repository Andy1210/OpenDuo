# Devices: keeping the device-specific parts apart (proposal)

**Status: a proposal. Nothing has been moved yet.**

OpenDuo runs on one device today, the Surface Duo 2 (SM8350). A port to the
original Surface Duo (SM8150) should be another device in the same project,
not a fork: the compositor, the shell, Settings and most services would be
shared, and only what depends on the hardware would differ. This page
proposes a layout that makes that possible, what would move, and what it
means for the scripts. Each phase below is meant to be one pull request.

## What is device-specific today

| Part | Today | Why it is device-specific |
|---|---|---|
| Kernel series | `kernel/patches/`, `kernel/config/`, `kernel/drafts/` | the board device tree, SM8350 drivers, the panel driver; the Duo 1 is another SoC (SM8150) with its own series |
| Firmware recipe | `scripts/collect-firmware.sh` | the firmware names and paths (`qcom/sm8350/microsoft/`, ath11k `WCN6855/hw1.1`) and the stock images they come from |
| Touch | `touch/`, `scripts/build-touch-runtime.sh`, `scripts/install-touch-runtime.sh`, `rootfs/overlay/etc/duo2/touch-*`, `.../duo2-touch-calibration` | the digitizer (HID `045E:0C1D`), Microsoft's heat-map library of this phone (`libsurfacetouch_c1.so`), the panel alignment in `sfpd` |
| Pen | `rootfs/overlay/usr/local/sbin/duo2-pend`, `.../libinput/90-duo2-pen.quirks`, `.../etc/duo2/pen.conf`, the pen-charger draft | AutoBond and the digitizer's pen reports; the charger's GPIO |
| Sensors | `scripts/build-sensors-runtime.sh`, `scripts/install-sensors.sh`, `sensors/dist/hexagonrpcd-duo2.conf` | the sensor DSP's registry and configuration from this phone's images; the device facts the registry was made with |
| Cameras | `camera/libcamera/patches/` (IMX481 / IMX563 helpers and tuning), `scripts/build-camera-tables.sh`, `scripts/camx-*.py`, `rootfs/overlay/etc/modules-load.d/duo2-camera.conf` | the sensor modules of this phone |
| Audio | `rootfs/overlay/usr/share/alsa/ucm2/Qualcomm/sm8350/*SurfaceDuo2*`, `.../wireplumber.conf.d/50-duo2.conf` | the sound card and its routes |
| Hardware glue in the root | `rootfs/overlay/usr/lib/udev/rules.d/*duo2*`, `.../feedbackd/themes/microsoft,surface-duo2.json`, `.../duo2-factory-addr`, `.../duo2-bt-addr` | device nodes, the vibration motor, the factory addresses' format in `sfpd` |
| Boot | `scripts/build-esp.sh` (DTB names, kernel options), `rescue/init` (the early display), `scripts/duo2-try` (the default DTB) | the device tree names and the display modules |
| Power | parts of `power/` (charge hold, torch, wake sources), `battery/` (the power profile from the stock overlays) | the battery firmware's properties, the PMIC's LEDs |
| Compositor | `compositor/duo-wc/dist/duo-wc.conf`; some constants in the code (the 66 px hinge gap, the panel size in tests and capture) | the panels' size, their names and the hinge gap |
| Bug reports | `devmode/bugreport.d/surface-duo2.conf` | already one file per device |
| Install | `docs/INSTALL.md`, `docs/RESTORE.md`, the WOA UEFI | the partition plan, the UEFI, the bootloader's quirks |

Shared as they are: `shell/`, `apps/`, `android/`, `hotspot/`, `firewall/`,
`homecrypt/`, `maint/`, `devmode/` (apart from `bugreport.d`), most of
`telephony/` (Qualcomm modems over QRTR), the posture and brightness
policies in `sensors/`, `scripts/payload-extract.py`, `scripts/mdt2mbn.py`,
`scripts/check-personal-data`, `scripts/lint-services`, `scripts/lib/`.

## Proposed layout

```
devices/
  surface-duo2/
    README.md              the hardware status table and notes for this device
    device.conf            identifiers and build parameters (below)
    kernel/
      patches/             the series, with SERIES.md
      config/              the config fragments
      drafts/
    firmware/
      collect-firmware.sh  stock images -> /lib/firmware layout
    touch/                 duo2-touchd, its tests and tools (Apache-2.0, with its LICENSE)
    camera/                the libcamera sensor patches and tuning; the table recipe
    sensors/               the registry recipe, hexagonrpcd's configuration
    overlay/               root file system files only this device needs
    bugreport.d/           the device's bug report items
    install/               partition plan and boot notes used by docs/INSTALL.md
  surface-duo/             later: the original Surface Duo (SM8150)
rootfs/overlay/            only what every device shares
```

`devices/surface-duo2/device.conf`, sourced by the build scripts (POSIX sh):

```sh
DEVICE_ID=surface-duo2
DEVICE_NAME="Surface Duo 2"
DT_COMPATIBLE=microsoft,surface-duo2        # what the running system checks
SOC=sm8350
KERNEL_BASE=v7.3.4
DTB_BOOT=sm8350-microsoft-surface-duo2-bringup-j.dtb
DTB_RESCUE=sm8350-microsoft-surface-duo2-bringup.dtb
FIRMWARE_SUBDIR=qcom/sm8350/microsoft
STOCK_OTA=ota-2023.501.541
```

On the phone, services that need to know the device read the device tree's
`compatible` (`/sys/firmware/devicetree/base/compatible`) rather than a file
the image writes, so one package set can serve several devices.

## What changes in the scripts

| Script | Change |
|---|---|
| `scripts/build-kernel.sh` | `DEVICE=` (default `surface-duo2`): the config fragment from `devices/$DEVICE/kernel/config/`; `src/linux` stays the tree, on a branch per device |
| `scripts/build-esp.sh` | DTB names and the device's kernel options from `device.conf` |
| `scripts/build-images.sh` | `DEVICE=`; copies `rootfs/overlay` and then `devices/$DEVICE/overlay`; the device's components (touch, camera packages) by name from `device.conf` |
| `scripts/collect-firmware.sh` | moves to `devices/$DEVICE/firmware/`; a thin `scripts/collect-firmware.sh` calls the device's |
| `scripts/build-touch-runtime.sh`, `install-touch-runtime.sh`, `build-sensors-runtime.sh`, `install-sensors.sh`, `build-camera-tables.sh`, `build-power-profile.sh` | read `device.conf`; device paths and names from it, or move under `devices/$DEVICE/` |
| `scripts/duo2-try`, `duo2-promote` | unchanged at first (they read the DTB from the phone's boot entry) |
| `rescue/init` | the early-display modules from a list the image writes, instead of fixed names |
| `scripts/lint-services`, `.github/workflows/ci.yml` | the new paths in the component lists and path filters |
| `devmode` | installs `devices/$DEVICE/bugreport.d/` |
| `compositor/duo-wc` | the panel size, names and hinge gap from a device section of `duo-wc.conf`, with the Duo 2's values as the default; the constants in the code to be audited |

Installed paths on the phone (`/usr/local/sbin/duo2-*`, `/etc/duo2/`, unit
names) do not change: this is a repository layout, so installed phones and
their updates are not affected.

## Names

Most files and services are prefixed `duo2-`, also shared ones
(`duo2-autosleep`, `duo2-wwan`...). Renaming installed services means a
migration on every phone, so this proposal keeps the names. New shared
components should not use the `duo2-` prefix; a rename of the shared ones
can come later, with its own migration, if the project decides on one.

## Phases

1. `device.conf` and `DEVICE=` in the build scripts, with the Duo 2 as the
   default; no file moves. The image and the phone stay byte for byte the
   same (`scripts/rootfs-compare.sh`).
2. `git mv kernel devices/surface-duo2/kernel`, and the firmware recipe;
   update the scripts, CI and the docs.
3. Split `rootfs/overlay` into the shared part and
   `devices/surface-duo2/overlay`; check with `scripts/rootfs-compare.sh`
   that the image's file list is unchanged.
4. Move `touch/` and the camera patches under the device.
5. The compositor's device section, and the shell's and Settings' few
   device names (the About page, the pen page).
6. A second device: `devices/surface-duo/` with its own `device.conf`,
   kernel series and status table.
