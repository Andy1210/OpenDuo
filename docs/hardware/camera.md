# Cameras

## Hardware

Four Sony sensors, all 4-lane MIPI D-PHY with a 19.2 MHz MCLK: main (wide)
IMX563 (12 MP), telephoto IMX481 (12 MP crop), ultrawide IMX481 (16 MP) and
front IMX481 (12 MP crop). The main and tele cameras have an LC898129
(onsemi) AF/OIS controller; the ultrawide and front are fixed focus with a
64 kbit EEPROM each. The back also has a dual-LED flash and an ams TMF8801
time-of-flight sensor.

Two CCI (camera I2C) controllers with two buses each: CCI0 = tele + main,
CCI1 = ultrawide + front (+ ToF). The sensors' supplies come from two PM8008
PMICs on the QUP SE18 I2C bus (`i2c18`, which mainline's `sm8350.dtsi` does
not define). The SoC side is the Titan 580 (CAMSS SM8350).

## Status (tested on the phone)

- All four sensors bind at boot, CAMSS delivers RAW10 frames (about 30 fps,
  no CSI errors, also on the main camera's 2.5 Gb/s lanes), and libcamera
  lists them (`cam -l`, also as the GUI user) and produces RGB frames; the
  GStreamer `libcamerasrc` and PipeWire (three "Built-in Back Camera" and one
  "Front" source) work.
- Orientation is `rotation = <270>` on all four sensors (the stock
  configuration's 90-degree roll). Colours look right after auto white
  balance (checked on real scenes by the owner).
- Not done: AF/OIS (no driver; the main and tele lenses stay parked), flash,
  ToF, a colour correction matrix, the EEPROM calibration (lens shading, AWB
  data are not decoded), per-module tuning (one `imx481.yaml` serves all
  three IMX481 modules).

## Quirks the code works around

- **Bayer order per mode.** The IMX481 front and tele full-resolution modes
  had the wrong Bayer order (GBRG instead of RGGB); fixed in the driver
  patches.
- **Green images with Debian's libcamera 0.4.0.** Its AWB subtracted only a
  quarter to a half of the black level and there was no sensor helper for the
  IMX481/IMX563; in the dark all three back cameras gave pure green. The
  image therefore ships libcamera 0.7.2 (Debian sid's packaging rebuilt for
  trixie with our patches: the two sensor helpers with the stock gain
  formula, black level 64, tuning files, the 0.8 AWB fix, a faster AGC) and
  PipeWire 1.4.2 rebuilt against it, as local `.deb` files
  (`camera/build-debs.sh`, `camera/install-debs.sh`).
- **Reported `ExposureTime`** in binned modes was twice the real one (the soft
  IPA computed the line length from the full-resolution mode); patched in the
  libcamera packages. AGC works in lines, so only the reported value was off.
- **Probe race.** At boot the four sensors probe at once on two CCIs; one
  read can time out, and a missing sensor stalled the whole CAMSS media graph.
  The `imx48x` driver therefore rereads three times, then returns
  `-EPROBE_DEFER`.
- `modules-load.d/duo2-camera.conf` loads the system DMA heap (nothing else
  loads it); `72-duo2-camera.rules` gives libcamera's software ISP access to
  it.

## Debugging

Run on the phone, as root, one module at a time (the scripts only read, or
stream test patterns):

```sh
sudo duo2-camera-probe camera          # PMICs, camcc, CCI, sensors, CAMSS, heap
sudo FRAMES=16 duo2-camera-stream uw   # or front, tele, wide; MODE=WxH; tpg 0 1
cam -l                                 # as the GUI user
scripts/duo2-camera-colour             # colour check, as the GUI user (video group)
scripts/camx-module-dump.py            # prints what a stock camx module file holds
```

The sensors' own test pattern is off by default. The stock camx module files
and tuning data come from the phone's own vendor image and are never
committed; `camera/` and `kernel/patches/` carry only our own code.
