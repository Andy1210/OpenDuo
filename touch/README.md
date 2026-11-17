# duo2-touchd

The Surface Duo 2's touch daemon: Microsoft's heat processor
(`libsurfacetouch_c1.so`, from the phone's own stock vendor image) turns the
digitizer's heat frames into contacts, and `duo2-touchd` feeds them to two
uinput touchscreens, one per panel. From luma-duo2 (none24smu, Apache-2.0),
modified for duo2-os; how it works and the history: docs/hardware/touch.md.

- `duo2-touchd.cpp`, `digitizer_power.h`, `pen_rules.h`, `liblog-stderr.c`: built with the
  Android NDK by `scripts/build-touch-runtime.sh`, installed by
  `scripts/install-touch-runtime.sh` (the stock runtime they run with is
  assembled from the phone's own images and never committed;
  `--daemon out/duo2-touchd` puts only a new daemon into the phone's).
- Two modes (`--mode`, else `/etc/duo2/touch-mode`, else split):
  `stitched` drives the library exactly as the stock
  `vendor.surface.touchpen@1.3-service` does (stitching on at start, every
  library message to the firmware as a SET_FEATURE, the lit panels / hinge
  angle / orientation / handedness from duo-posed's `/run/duo-posed/touch`);
  `split` is the bring-up sequence and the last-resort fallback: a stitched
  daemon that gets the digitizer's single-point report (0x40, not in heat
  mode) exits and is restarted stitched (a re-initialisation, logged in
  `/run/duo2-touchd.reinit`); only after 3 of those within 10 minutes does it
  write `/run/duo2-touchd.fallback`, and restarted it runs split. (Heuristics
  on heat frames without contacts were dropped on 2026-11-07: a hand near the
  glass tripped them half an hour into a working stitched run.)
- The pen (`pen_rules.h`, host-tested): the library's pen reports
  (`SendPenReports`, the stock service's `InputEvdev::PenReports` layout)
  become one tablet device per panel, "Surface Duo 2 Pen Left/Right"
  (pressure, tilt, the eraser end, the barrel button, hover), mapped and
  calibrated as the panel's contacts. The library makes the pen out of the
  heat collections' own reports (no enable call; the stock HAL sends the
  firmware nothing pen-specific but the Bluetooth pairing of the top button,
  "AutoBond"), so the tip, the eraser and the barrel need no pairing (the top
  button does: duo2-os pairs it the ordinary BLE way, Duo Settings > Pen,
  not through AutoBond; duo-wc's `penbutton.rs`). libinput
  takes a pen's contact from the pressure (by default above 5 % of the range):
  the tip goes out as at least `pen::kTipPressureMin` (3), a hover as 0, and
  the quirk `rootfs/overlay/usr/share/libinput/90-duo2-pen.quirks`
  (`AttrPressureRange=2:1`, `AttrTabletSmoothing=0`) makes libinput's tip the
  library's tip bit. libinput also takes a pen out of proximity after 50 ms
  without a frame (until it has once seen the pen really leave; no quirk turns
  that off), and a hovering pen is often that quiet: the library repeats its
  last report when it has no new position, and the kernel drops a frame with
  no changed value. So every frame ends with `MSC_TIMESTAMP` (libinput ignores
  it, but a repeated report is still a frame), and a pen in range whose device
  got nothing for `pen::kKeepAliveMs` (30) gets a timestamp-only frame; no
  position is repeated or made up. The pen leaves when the library says so, or
  after 300 ms without a report. Every 30 s, while the pen is in range, the
  journal has the library's output per mode (hover / tip): reports, repeats,
  heat frames, the gaps between new positions, the keep-alive frames.
  Palm rejection: while the pen is in range (and 300 ms
  after) new contacts are held back, and contacts already down become palms
  (MT_TOOL_PALM, which libinput cancels), on both panels, only on the pen's,
  or off: `palm_rejection = both|panel|off` in `/etc/duo2/pen.conf` (Duo
  Settings > Pen; read again when it changes), `palm both|panel|off` on the
  FIFO, `--no-palm-rejection`. The journal says the first pen report of a run,
  the first time the pen comes into range, and the first few of the reports
  only a pen makes that the library is not given (the firmware's own pen
  report 0x01, the pen id 0x22, the pen tail 0x58, AutoBond's "pen in range"
  0x6e, without its id and address).
- AutoBond's host state (`autobond.h`, host-tested): as the stock touch-pen
  HAL, the digitizer is told feature 0x70 = 01 (AutoBond on), feature 0x56 =
  the host's LE address (LSB first) and type, output 0x38 = bit 0 the pen's
  Bluetooth link, bit 1 Bluetooth on; from duo2-pend's
  `/run/duo2-pen/autobond` (rootfs/overlay/usr/local/sbin/duo2-pend: the
  pen's Bluetooth side), again after every power-on or reset of the
  digitizer and whenever the file changes (stitched mode; the split mode
  keeps its own 0x38). Nothing is sent without a host line.
- System sleep (`digitizer_power.h`): powered while the system slept, the
  digitizer drew ~95 mA of the ~150 mA sleep floor and reset itself (a restart
  of the daemon after every wake). The system-sleep hook
  (`rootfs/overlay/usr/lib/systemd/system-sleep/duo2-touchd`) sends SIGURG
  before a suspend and waits (at most 3 s) until `/run/duo2-touchd.digitizer`
  says `off`: the contacts lifted, hidraw closed, the digitizer powered down by
  spi-hid. After the resume it sends SIGWINCH: hidraw is opened again (after
  at least 1 s off), and at `ready` the library gets OnDeviceReset (the split
  mode sets the heat mode by hand too), as the stock HAL does at screen-on;
  the touch devices, the library and the pose stay. Anything unexpected on
  the way back (no `ready` in 3 s, another device, no message from
  OnDeviceReset in the stitched mode, the single-point report before the
  first heat frame) is the clean restart (exit 75), not counted as a stitched
  failure. While the system is awake the digitizer stays on, dark panels too
  (a touch wakes them). Host tests: `touch/tests/run.sh` (the power state
  machine, the pen rules, the sleep hook).
- The contact log (`log on`, `--log-contacts`: every touch's position in the
  journal, for calibration) works only in Developer mode with its own switch
  on, Settings > Developer > **Log touch positions** (`devgate.h`,
  host-tested): the positions of the lock screen's keys would give the PIN
  away. The switch is `/run/duo-devmode/touch-log` (root's, made by
  `sudo duo-devmode touchlog on`, which asks the user first; gone after a
  reboot). The daemon checks Developer mode and the switch when the log is
  asked for and every 2 s while it logs, and stops as soon as either is off.
- Control FIFO `/run/duo2-touchd.ctl`: `log on|off`, `status`, `tap l|r FX FY`,
  `lift`, `pen l|r FX FY [PRESSURE [FLAGS]]`, `pen out`, `penstroke l|r` (a
  scripted pen stroke: hover, tip, barrel, eraser), `palm both|panel|off`, `suspend` /
  `resume` (what the sleep hook's signals do, without a suspend; `suspend`
  alone comes back by itself after 20 s awake), and in the stitched mode
  `display both|left|right`, `hinge DEG`, `rotation DEG`, `hand left|right`,
  `fallback`.
- `tools/pen-test.py` (Duo Settings > Pen > Pen test): a GTK 4 canvas that
  draws with the pen and shows live what an app gets (tool, hover or touch,
  a pressure bar, the tilt in degrees while the tip touches: GTK gives it
  as -1..1 of -90..90 degrees, and the library reports none while hovering;
  the side button, the pen device), and counts the
  fingers that reach it while the pen is near (the palm check); every event
  is printed on stdout too.
- `tools/pen-drop-capture.sh` (root, by hand, never armed): why the pen drops
  its Bluetooth link near the screen. `pen-bt-trace.py capture` (btmon and the
  digitizer's AutoBond reports, every address a role) plus the journal of
  duo-wc, the kernel, bluetoothd, seatd, duo2-pend and duo2-touchd, and the
  pen's HID descriptor and input devices at its first link, into
  `/run/duo2-penbt` (0700; the btsnoop holds link keys: delete it after). A
  second argument adds `pen-bt-trace.py`'s raw scan, which switches Bluetooth
  off for that long: only with the owner's go.
- `tools/pen-hover-timing.py` (root, by hand, read-only): how evenly the pen's
  reports reach the system while it hovers and writes (intervals, gaps, motion
  statistics per report, no positions stored) and whether the pen's Bluetooth
  link coming and going lines up with gaps; `--start-on-pen` waits for the
  pen's first hover, so another capture can start on the same cue. Two kinds
  of interval: between new positions (what moves the cursor) and between any
  frames (what libinput's 50 ms proximity timer sees); and libinput's own
  proximity events, from `libinput debug-events` on each pen device (a fresh
  context, as after a boot: only its proximity lines are kept), against the
  device's (`--no-libinput` leaves that out).
- `tools/pen-ab-check.py` (root, by hand, read-only): whether the stock HAL's
  rule turns the pen id of AutoBond's 0x6e into the pen's Bluetooth identity
  address (SHA-1 over a 16-byte constant of the stock HAL and the id; the
  constant comes from the phone's own stock image and stays root's). Prints
  only yes / no.
- `tools/pen-fw.py`: the Slim Pen 2's firmware over Bluetooth (Microsoft's
  CFU on the pen's own BLE HID): `find`, `version` (read-only, compared with
  a firmware folder), `update` (a dry run; `--go` updates, only with the
  owner's GO, after its checks: charger, pen battery, no other Bluetooth
  device, a known Microsoft file set), `inspect` and `verify-cat` (a Windows
  driver catalog's signature and members, on the host). Why and how: docs/hardware/touch.md.
- `tools/touch-edge-targets.py`, `tools/touch-edge-fit.py`: edge and corner
  targets and the fit for `/etc/duo2/touch-calibration` (per geometry:
  `left/right` split, `stitched-left/right`, `single-left/right`).
