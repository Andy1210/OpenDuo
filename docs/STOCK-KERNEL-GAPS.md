# Stock kernel vs duo2-os: what the vendor source can still give us (2026-11-16)

What Microsoft's GPL kernel for the Surface Duo 2 has that our mainline 7.3.4 + `kernel/patches`
does not, and what each gap is worth porting. References only: no vendor code is copied here; a
port would come as a proper patch with attribution.

**Sources** (none of them in git; recipes to get them again):

- Kernel: [microsoft/surface-duo-oss-kernel.msm-5..4](https://github.com/microsoft/surface-duo-oss-kernel.msm-5..4),
  branch `surfaceduo2/11/2023.501.541` (the owner's firmware version). A blobless, shallow,
  sparse clone is in `out/ms-kernel/msm-5.4` (`git clone --depth 1 --filter=blob:none --no-checkout
  --branch surfaceduo2/11/2023.501.541 …`, then `git sparse-checkout` of the directories needed;
  the whole file list is `out/ms-kernel/tree.txt`). Duo 2 DT: `arch/arm64/boot/dts/surface/duo2/`
  (it includes Qualcomm's `vendor/qcom/*` DT, which lives in
  `microsoft/surface-duo-oss-sm8350.12.devicetree`).
- Camera and audio kernels: `microsoft/surface-duo-oss-platform.vendor.opensource.camera-kernel`,
  `…audio-kernel` (same branch name).
- The stock device tree as the phone ran it: `out/stockdt/` (`vendor_boot-00` + `dtbo-09`, merged
  with `fdtoverlay` as in docs/hardware/camera.md); the list of enabled nodes was
  made from that.
- Stock userspace: `firmware/ota-2023.501.541/img/` (`vendor.img`, `system_ext.img`, read with
  `debugfs`).

Effort: **S** = days, DT or a small patch on an existing mainline driver; **M** = a new small
driver or a protocol to work out; **L** = a large driver, a DSP / firmware interface, or
upstream-blocked work. Microsoft's own files are marked "MS"; the others are Qualcomm's (or a
part vendor's) with Microsoft changes ("MSCHANGE" comments) where noted.

## The known gaps

| Gap | Stock driver (license) | Mainline today | Port effort | Risk | Value to the owner |
|---|---|---|---|---|---|
| Camera AF / OIS (LC898129, main + tele) | camera-kernel `drivers/cam_sensor_module/cam_actuator/`, `cam_ois/` (GPL-2.0): generic CCI drivers that play register lists from userspace (the camx module `.bin` files; OIS firmware by `request_firmware` `<name>.prog/.coeff`) | none for LC898129 (only names in `ipu-bridge.c`); V4L2 lens drivers exist for other VCMs (`dw9719`, `dw9768`…) | AF: **M** (a V4L2 lens subdev: power, AF mode, position; the register map is in docs/hardware/camera.md 1.2); OIS: **L** | low for AF (same CCI bus as the sensors); OIS: the calibration lives in the controller's flash, do not write it | high: the main and tele cameras are fixed-focus without it |
| ToF (ams TMF8801, CCI1 0x41) | `drivers/input/misc/tof8801/` (ams, GPL): input device + sysfs, loads `tof8801_firmware.bin` | none (IIO proximity has VL53L0X etc.) | **M** (IIO driver + its firmware patch download) | low | low: only an AF assist; worth it after AF |
| NFC (NXP SN1xx, `qcom,sn-nci` on QUP SE15 I2C 0x28; VEN gpio62, IRQ gpio87, FW-DL gpio86, CLKREQ gpio63) | `drivers/nfc/qti/` (`nfc_i2c_drv.c`, `nfc_common.c`, GPL-2.0-only; MSCHANGE: an NCI "standby enable" sequence at init): a char device for NXP's userspace NCI stack | `drivers/nfc/nxp-nci` (`nxp,nxp-nci-i2c`): PN547/PN548/PN7150 class; SN1xx (NCI 2.0) untried | **M** (DT; NCI 2.0 differences; the standby sequence) | low (do not use firmware-download mode) | medium-low: tags and cards with neard / libnfc; never payments (that needs the secure element) |
| Fingerprint (FPC1020) | `drivers/misc/fpr_FingerprintCard/fpc1020_platform_tee.c` (FPC, GPL v2): only reset, IRQ and power; the sensor's SPI belongs to TrustZone, the matcher is a TA | none, and the kernel part alone is useless | **L / blocked** (QSEECOM TA loading and the TA's protocol) | high (TZ) | high, but not reachable |
| Charge limit | `drivers/power/supply/qti_battery_charger.c` (GPL-2.0-only, many MSCHANGEs): owner opcodes 0x81/0x83 (pack 1/2), 0x85/0x86 (get/set), property DOC (`doc_en`) | done: our patch 0120 (`qcom_battmgr`: `charge_types` Standard / Long Life) | the threshold is the firmware's: **S** (measure it) | none | medium (already usable) |
| …the rest of that driver | same file: two battery packs as their own power supplies (current, voltage, RSOC, temperature, FCC, cycle count, per pack), ship mode (`ship_mode_en`, reboot notifier), fault flags (OVP, RSOC imbalance, over-temperature), thermal zones of the pack currents, USB telemetry | `qcom_battmgr` knows one battery | **S–M** (the same opcodes as 0120) | low for reads; ship mode cuts power until a charger is plugged in | medium: per-pack health and cycle counts; ship mode for storage |
| Speaker protection | ADSP FBSP module + `wsa883x` VI sense (audio-kernel repo, GPL) + the HAL's calibration (docs/hardware/audio.md) | `wsa883x` plays; no VI-sense feedback, no protection on the q6asm/q6afe (APR) path | **L** | medium (speakers can be damaged by the wrong model) | medium: louder speakers (today a −3 dB cap) |
| USB host / OTG, USB 3 | `dwc3-msm` + `ucsi_glink` + `altmode-glink` (Qualcomm, GPL); the role comes from the ADSP over UCSI, no extcon (MSCHANGE removes it) | all there: `dwc3-qcom`, `ucsi_glink`, `pmic_glink_altmode`, the QMP USB3/DP combo PHY; our DT keeps dwc3 a fixed peripheral (bring-up), orientation gpio81 known from the DP work | **S–M** (DT: `dr_mode = "otg"`, `usb-role-switch`, the SS lanes and orientation) | medium: the USB gadget is the rescue console; try with `duo2-try` and keep the gadget working | high: keyboards, sticks, Ethernet, docks; SuperSpeed |
| Pen top button | userspace: `TouchPenService.apk` pairs the pen over Bluetooth through AutoBond (the digitizer's 0xFFF4 collection: 0x56 the phone's address, 0x6f the OOB key) and makes `surface_tail_button` (uinput, Win+F20/F19/F18); the digitizer also has a "pen tail" report 0x58 (0xFFA1) | BlueZ HID-over-GATT handles the pen's BLE HID; no kernel work | **S** (pairing; optionally AutoBond OOB from duo2-touchd; map the keys) | none | medium; the same pairing opens pen haptics (below) |
| VoLTE | userspace (IMS) | — | — | — | — |
| eSIM | userspace (lpac + the modem; `MSeSIMFwUpdateAgent` updates the eUICC) | — | — | — | — |
| Haptics extras | `drivers/input/misc/qcom-hv-haptics.c` (Qualcomm, no MSCHANGE) + `surface-duo2-haptics.dtsi` (MS): LRA 2.828 V peak, 5263 µs (190 Hz), six predefined effects (patterns) | our `qcom-spmi-haptics` (patches 0045–0048) drives the PM8350B HAP520 as an FF device | **S** (predefined patterns / FIFO, LRA auto-resonance) | low | low-medium (crisper clicks) |
| Posture, hall, hinge angle | SSC (SLPI) sensors, userspace (`sensors.ssc.so`) | done: duo-posed over libssc | — | — | — |

### `surface_util` (MS, `drivers/firmware/surface/`, GPL-2.0)

One platform device (`surface,surface_util`) with modules picked by `module-names`:

| Module | What it does | Port? |
|---|---|---|
| `ms_pen_charger` | GPIO 39 as a sysfs switch for the Pen Cover's charger | done as a draft: `kernel/drafts/pen-charger` + `power/duo2-pen-charger` (docs/hardware/touch.md) |
| `fuse_check` | secure-boot, debug-disable, anti-rollback, RPMB and SIM-lock fuse states (an SCM call; the SIM-lock fuse by `ioremap` of 0x078621c) | no (factory information; mainline reads fuses through `qfprom` nvmem if ever needed) |
| `mcfg_check` | manufacturing / ACT / RAFLA modes from the boot loader's hand-off block | no (factory) |
| `display_state` | drives GPIO 67 ("C3") and 47 ("R2") from sysfs; reads the panel's RDID / TDM version from the hand-off block | no (a factory hook; the RDID could tell panel revisions apart if that is ever needed) |
| `surface_product_id` (+ `xbl_hlos_hob.c`, `hob_mem.c`, `telemetry.c`) | the product id and other boot-loader data from `oem_smem` (0xE3400000), the hand-off block memory as a bin file | already covered for what needs it: the SSC registry's product id is answered by our hexagonrpcd patch |

## Stock drivers we have no equivalent of, not in the list above

| Driver | What it is | Worth it? |
|---|---|---|
| `drivers/soc/qcom/ms-pon-hob.c` (MS, GPL v2) + MSCHANGEs in `pmic-pon-log.c`, `qcom-reboot-reason.c` | why the phone last powered on or reset: the PMIC's PON/fault history (SDAM) and the boot loader's record; custom reboot reasons (ship mode 0x21, "rnr" 0x22, kernel panic 0x23) | **S–M**, medium: a real reason for unexplained resets and freezes (the GPU hangs, see docs/hardware/display.md); read-only |
| BCL (`qcom,bcl-v5`, `qcom,msm-bcl-soc` on PM8350B; Qualcomm `bcl_pmic5.c`, `bcl_soc.c`) | battery current limiting: throttles before a brown-out at high load and low charge | **M**, medium: no mainline driver for this PMIC generation; the ADSP charger firmware covers part of it |
| `qti_battery_debug.c` (MSCHANGEs) | the ADSP battery manager's own log (ulog) | S, low (debugging the charger firmware) |
| `drivers/hwmon/qti_amoled_ecm.c` | the AMOLED panels' energy meter on PM8350B | S–M, low (display power telemetry) |
| `drivers/leds/leds-qti-flash.c` (MSCHANGE) | the flash at up to 1.8 A per LED for the dual-LED flash | S, low: our flash (patch 0133) keeps the module's 2 A for the pair |
| `drivers/misc/booting_watchdog/` (MS) | a timer that reports and stops a boot that takes too long | no (duo2-try and systemd cover it) |
| `drivers/gpio/ms_gpio_verifier.c` (MS, GPL) | checks that the secure GPIOs are configured as listed | no (a debug check) |
| `drivers/hwmon/max34417.c` (MS) + `surface-duo2-powermonitors.dtsi` | power monitors; the DT include is commented out for the retail build | no (not populated) |
| MSCHANGEs in `spi-msm-geni.c` (interruptible transfer waits), `regulator/core.c`, `soc_sleep_stats.c`, `rpmh_master_stat.c`, `socinfo.c`, `msm-poweroff.c`, `qcom-spmi-pmic.c` | logging, debug output, the OEM platform subtype, minidump mode | no |
| `drivers/hid/spi-hid/` (MS, GPL) | the digitizer's HID-over-SPI transport | already ours (BRING-UP patches 0022, 0043, 0122) |

Not kernel, but read for the owner on the way (docs/hardware/touch.md): the Duo 2's
digitizer has no HID Haptics collection, but the Slim Pen 2's own Bluetooth HID descriptor (in the
pen firmware the stock image carries) has a standard Simple Haptic Controller whose auto trigger is
tied to the pen's tip switch: the inking feel may be reachable over Bluetooth, with the pairing
the top button needs anyway.

## Top 5 by value for the effort

1. **USB host / OTG and USB 3** (S–M, high): DT only on drivers we already run; keep the rescue
   gadget working while trying it.
2. **Camera autofocus, LC898129** (M, high): a V4L2 lens driver for AF on the main and tele
   cameras; OIS later, if ever.
3. **Slim Pen 2 over Bluetooth** (S–M, medium-high): pairing gives the top button (Win+F20/F19/F18
   as stock) and opens the pen's haptics; userspace only.
4. **The rest of Microsoft's battery protocol** (S–M, medium): per-pack status and cycle counts,
   ship mode, fault flags, on the opcodes patch 0120 already uses; plus measuring where the charge
   hold stops.
5. **NFC** (M, medium-low): `nxp-nci` on the SN1xx with the stock pins and supplies; tags only.

High value but large or blocked: speaker protection (L), fingerprint (blocked by TrustZone).
Cheap diagnostics worth adding when a reset needs explaining: the PMIC power-on / fault history.
