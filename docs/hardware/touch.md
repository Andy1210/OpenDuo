# Touch and pen

## Hardware and kernel side

One digitizer (Microsoft G6) serves both panels: HID over SPI (SE4, GPI DMA,
32 MHz), IRQ GPIO 84 (falling edge), reset GPIO 8, supply L3C. The driver is
Microsoft's 5.4 `spi-hid` (from linux-surface/spi-hid), carried as a
BRING-UP patch until the upstream spi-hid handles it. The device reports
HID `045E:0C1D` with a 1651-byte report descriptor.

Without heat-map mode the digitizer sends only report 0x40: one contact for
both panels in the same 0..32767 space with no panel ID, which is unusable
for two screens. Multi-finger, per-panel touch comes from the heat maps
(left collection 0x0A, right 0x3C, 30x21 per panel) processed by Microsoft's
`libsurfacetouch` library.

## `duo2-touchd`

`touch/duo2-touchd.cpp` (Apache-2.0) runs Microsoft's library on Android's
bionic linker (`scripts/build-touch-runtime.sh` assembles the runtime from
the phone's own images; it is not distributable and never committed). It
creates two uinput touchscreens, "Surface Duo 2 Touchscreen Left/Right"
(udev: `WL_OUTPUT` DSI-1/DSI-2), range 0..17455 x 0..12019, 10 fingers and a
pen. Panel alignment comes from the phone's own `sfpd` partition
(`PixelAlignmentData.bin`).

- It needs `CONFIG_INPUT_UINPUT=m`.
- **Restarts.** The digitizer is powered off when the daemon closes it and on
  again when the next instance opens it. Back on within ~160 ms (a plain
  restart) it comes up half reset (no heat frames, only the single-point
  fallback report) until a reboot, so it must stay off at least a second
  (`ExecStartPre=/bin/sleep 1` in the service, `kMinOffMs` in
  `touch/digitizer_power.h`).
- **Sleep.** The daemon powers the digitizer off across a system suspend only
  (the system-sleep hook `rootfs/overlay/usr/lib/systemd/system-sleep/duo2-touchd`
  tells it with `SIGURG` before the suspend and after the resume): powered
  while the system slept, it drew ~95 mA of the ~150 mA sleep floor and reset
  itself, so every wake ended in a daemon restart.
- Two modes: stitched (the stock service's own sequence: stitching on from the
  start, the library told which panels are lit, the hinge angle, orientation
  and handedness from `duo-posed`; with both panels lit and the hinge under
  235 degrees the library stitches them into one surface) and split (the
  bring-up sequence, stitching off). `/etc/duo2/touch-mode` selects it.
- The stock service makes the same calls in the same order, except that it
  switches stitching mode on at start and feeds the hinge angle, orientation
  and handedness; the stock HAL closes hidraw when the display goes off and
  reopens it when it comes on.
- **The edge near the hinge:** the library's output is stretched ~4.5 % along
  x, so the two panels overlap in the shared space at the hinge. The panel
  is decided from the contact's own region byte (1 = the right panel) and a
  per-panel correction (`/etc/duo2/touch-calibration`) runs inside the
  daemon before clipping. When the region byte and x far from the hinge
  disagree, x wins and the log says so.
- **The outer edge:** the heat-map processing saturates in the outermost
  ~6 mm; a linear correction cannot recover it.

## The pen (Surface Slim Pen 2)

- Pen position and pressure arrive through the digitizer. `duo2-pend`
  (`rootfs/overlay/usr/local/sbin/duo2-pend`) is the Bluetooth side: it works
  around BlueZ 5.82 not setting the kernel's address-resolution flag for a
  device bonded under its static identity address with an IRK (without it the
  pen pairs, works, drops the idle link after a minute and never reconnects;
  fixed upstream in BlueZ, 2025-10), keeps the AutoBond host state for the
  digitizer (`/run/duo2-pen/autobond`, which `duo2-touchd` turns into the
  digitizer's reports) and drives the pen's haptics. The pen's own inking
  waveforms never play on the Duo 2 (measured), its discrete ones do, so
  the writing feel is made of short ticks sent from the phone while the tip
  is down (below).
- **Charging.** Microsoft says the Slim Pen 2 charges on the Duo 2 only with
  the "Surface Duo 2 Pen Cover" from the phone's battery. In the stock
  kernel this is one sysfs switch (`ms_pen_charger`): GPIO 39 as an output,
  **low = on**; the transmitter chip itself is invisible to software (no
  I2C/SPI device or regulator for it in the stock device tree). The stock
  `batteryd` keeps it on while the "accessory charging" property is set and
  the battery is at least 20 % or USB is online; there is no cover, heat, time
  or foreign-object detection in software. On the stock phone nothing drives
  the GPIO by default (it stays at the bootloader's level); on this system
  mainline does not touch it either (reads `out high` = off).
  `power/duo2-pen-charger` and `kernel/drafts/pen-charger/` are written as a
  draft and have not driven the GPIO on a real phone.
- **Haptics.** The digitizer's descriptor has no haptic usage page; the
  stock Android never drove the pen's motor. The pen itself exposes a
  standard Simple Haptic Controller over Bluetooth (report 0x41: auto trigger
  on tip switch, manual trigger, a waveform list), so haptics are driven
  from user space (`duo2-pend`, hidraw) and configured in Duo Settings > Pen.
- **What the pen can play (measured 2026-11-17).** One short, fixed tick per
  output report. With the pen held in the air, 20 cm from the screen,
  almost no difference could be felt between the waveform cutoffs (20, 50,
  200 ms), RepeatCount + RetriggerPeriod (5 clicks 40 ms apart, 10 hovers
  25 ms apart), intensity 60 against 200, or HOVER, CLICK and PRESS. With
  the tip on the glass, writing, the waveform and the spacing are a little
  noticeable: perception with the tip down differs from the pen in the air,
  so a haptic test is done writing. Intensity, cutoff and repeats were not
  tried again on the glass. The continuous inking waveforms never play. The
  link limits the rest: a 45 ms connection interval (about 20 reports a
  second, 45-90 ms from the write to the tick), and the pen drops the link
  each time it comes near the screen (~140 ms gap, ~1 s until reports go out
  again).
- **The writing feel is timing.** `duo2-pend` reads duo2-touchd's tablet
  devices (the tip, the position in mm, `ABS_PRESSURE` 0..65535, each frame's
  kernel time on `CLOCK_MONOTONIC`) and decides per frame whether the pen
  ticks (`WritingFeel`, pure, `touch/tests/test_pend.py`):
  - one tick at the touch, none at the lift; a stroke shorter than 1 mm (a
    tap, a dot) has only that one;
  - the grain: one tick per distance of travel, drawn anew for each tick from
    a seeded generator around the feel's spacing (a floor of
    `spacing × (1 - irregularity)` and an exponential tail, at most 3 ×
    the spacing), so density follows the pen's speed and no stroke repeats
    a pattern; below 8 mm/s the spacing widens (× 8 / speed: a crawl gives
    sparse, occasional ticks); a harder press narrows it a little (× 0.8 to
    1.2 around 35 % of the range);
  - accents: a turn sharper than the feel's angle within 2.5 mm of travel
    (headings sampled every 0.5 mm) ticks at once, and the grain starts
    afresh from it. A scribble, all turns, is dense and uneven; a long
    straight line an even, light grain;
  - the gaps: after each tick, its waveform's length and a margin (HOVER
    45 ms, CLICK 55 ms, PRESS 70 ms, never under the connection interval),
    longer at random by up to 50 %. A tick due inside a gap waits 25 ms at
    most (an accent 50 ms) and is then dropped; nothing is queued, so a fast
    stroke saturates the link unevenly instead of late. A frame read more
    than 30 ms late has its tick dropped too.
  - The waveform is the secondary lever: the grain keeps each feel's own
    waveform, the touch and the accents use a firmer one.

  | Feel   | Grain | Touch, accents | Spacing | Irregularity | Accent past |
  |--------|-------|----------------|---------|--------------|-------------|
  | ink    | HOVER | CLICK          | 3.0 mm  | 0.25         | 75°         |
  | pencil | HOVER | CLICK          | 1.5 mm  | 0.70         | 60°         |
  | marker | CLICK | PRESS          | 5.0 mm  | 0.40         | 50°         |
  | chisel | CLICK | PRESS          | 3.5 mm  | 0.45         | 45°         |
  | brush  | PRESS | PRESS          | 7.0 mm  | 0.50         | 110°        |

  `touch/tools/pen-feel-sim.py` runs the same code offline over a straight
  line, a zigzag, a slow curve and a fast line and prints the tick times.
  Apps' pulses (the haptics socket) are unchanged; a writing-feel tick never
  follows another pulse by less than 40 ms.
- **Pen firmware.** The phone's vendor image carries the pen's firmware
  (CFU offers and payloads). `touch/tools/pen-fw.py` reads and checks them
  against the rules for a safe update; vendor reports (0x2a-0x2f, 0x30,
  0x32-0x34) must never be written. The update was not run on a pen.
