# Changelog

All notable changes to OpenDuo are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) (0.x until 1.0). The version lives in
the `VERSION` file at the repository root; a build is identified by its UTC date
and git short hash, for example `20261116.71fc19a`.

## [Unreleased]

Development state: `0.1.0-dev`. The first public release will be 0.1.0.

### Hardware
- Mainline Linux 7.3 on the Surface Duo 2 (SM8350) with Debian 13 (trixie).
- Both panels at 90 Hz, GPU acceleration (Mesa freedreno/turnip), touch on both
  panels including stitched mode across the hinge.
- WiFi, Bluetooth, modem calls (2G fallback), SMS, mobile data, eSIM, GNSS
  location, speakers, microphones, haptics, cameras, hardware video decoding.
  Wake-on-WLAN is built but off by default and not tested yet.
- Sensors: accelerometers, hinge angle and posture, ambient light with automatic
  brightness, proximity, magnetometers (the screen off at the ear during calls
  is built, not yet tried in a real call).
- Deep sleep with timed autosleep, wake sources, battery health figures and
  temperature handling; Microsoft's "Long Life" charge hold exposed (where it
  stops charging is not measured yet).
- Boot splash on both panels, shown from the initramfs; a rescue boot entry.

### Compositor
- Own dual-screen Wayland compositor (Smithay): windows span or snap across the
  panels, laptop and desktop layouts, rotation, screen casting, idle inhibit,
  XWayland.

### Shell & Settings
- Touch-first home screen, app drawer, on-screen keyboard with word completion
  and Hungarian accents, lock screen with PIN.
- Settings: network, display, appearance (light/dark), battery, sound, mobile
  and eSIM, hotspot, Android apps, pen, security, developer options, and an
  About page with the OpenDuo version, build id and kernel.
- System updates and backups with snapshots; Settings > Updates checks daily,
  shows what's new, installs with a snapshot first (on the charger or at 50 %
  battery), can install at night (off by default), and offers a testing channel
  in developer mode.
- Settings > eSIM reads the operator's activation code from its QR code: with
  the camera (the ultra-wide first, a live view) or from a picture such as a
  screenshot; typing it stays as the third way. Factory test profiles are
  marked as such, a download shows its progress, and a finished download
  offers to switch to the new profile.
- Settings > SIM card chooses the SIM in use: the SIM card in the tray or the
  eSIM. The modem takes calls, messages and data only on its first logical
  slot, so the chosen SIM is moved there (`duo2-sim use`); switching on an
  eSIM profile while the tray's SIM is in use offers the switch.

### Pen
- Surface Slim Pen 2 (the pen tested): pressure, tilt, eraser, the Bluetooth top
  button, scrolling, haptic feedback for apps, pen firmware update. Charging in
  the pen cover is drafted, not enabled.
- The pen's writing feel comes from timing rather than a fixed beat: an
  irregular grain that follows speed and pressure, a firmer tick at the touch
  and on sharp turns.

### Apps
- Android apps through Waydroid, with clipboard, notifications and names.
- Video playback in the browser with hardware decoding.
- Pen-aware games and tools, for example Nibfair.

### Install
- A release image (root and ESP, built from OpenDuo's own packages, so updates
  work from the first start) and one script on a Linux computer,
  `scripts/install-openduo.sh`: it backs up every partition of the phone
  (checked), partitions, writes the images and the WOA UEFI (pinned downloads,
  checked), and never touches the modem's partitions or another phone.
- No vendor file in any image: `duo2-stock-import` reads the firmware, the touch
  runtime, the sensor calibration, the camera tables and the power profile from
  the phone's own stock partitions at the first start, then restarts once.
- Tried end to end on a phone put back to stock Android: the installer (about
  4 minutes from a computer), the first start with the firmware import, and
  the way back to Android ([docs/RESTORE.md](docs/RESTORE.md)).
- The installer numbers its steps, says how long each one usually takes and
  took, shows the progress of the big copies (percent, speed, time left), and
  explains "no permissions" from adb or fastboot on Linux; `udev-rules.sh`
  (next to it in the release) sets up the USB access once.
- WiFi works from the first start: the image enables `wpa_supplicant@wlan0`
  with an empty configuration that Settings > WiFi saves networks into.
- The first start's clock begins at the image's build date instead of a date
  months earlier, until the network sets it.

### Security
- Firewall closed by default; SSH only in developer mode, key login only.
- Optional encryption of user data tied to the PIN (fscrypt, direct boot,
  recovery key).
- Bug reports with personal identifiers masked.

### Fixed
- The first start's firmware import refused a phone whose copy of the sensor
  configuration beside its registry was older than the registry itself (the
  sensors step failed, so no touch, WiFi or sensors). It now checks what the
  sensor DSP reads: the registry's version against the vendor configuration's
  (issue #1).
- An eSIM download that failed said only lpac's internal step name (for
  example `es9p_initiate_authentication`); it now says what went wrong (no
  internet, what the operator's server answered, a confirmation code missing)
  with the step under it, logs the steps (`journalctl -t duo2-esim`, no
  identifiers), and a download counts as done only when the chip lists the
  new profile.
- The eSIM page started two lpac processes at once, and one of them could
  crash; lpac now runs one at a time.
- `duo2-sim primary 2` could never make the eSIM the SIM in use: the modem
  opens the primary subscription only on its first logical slot, and the
  request on the second one was refused. It now moves the slot there.
- A network saved in Settings > WiFi no longer leaves wpa_supplicant's
  configuration, with the network's key, readable by every user.
- The image no longer carries the D-Bus machine id made during its build
  (the same on every phone): D-Bus reads the phone's own, made at its first
  start.
- The lock screen's keypad digits sit in the middle of their circles.
- A new kernel's health check read the compositor and touch as failed while
  they ran, so every packaged kernel went back to the previous one.
- An update no longer stops Android apps from starting on a phone whose
  Waydroid was patched by an earlier version of OpenDuo's patch.
- An Android app on one panel is fitted into its window again after the phone
  is turned and after it leaves a span or full screen; it used to come up cut
  off (not tested on the phone yet).
- Starting an Android app while Android is not running shows "Starting
  Android apps..." on that panel, and its window opens there even when it
  takes Android ~20 s to start (not tested on the phone yet).
- USB data to a computer works again (networking, the serial console), also
  after the cable is unplugged and plugged in again: the USB controller follows
  the role the USB-C port reports and starts afresh on every attach. USB
  tethering is not tested yet.
- The video decoder no longer stays stuck until the next restart after its
  firmware crashes: the kernel restarts the firmware. A video the decoder
  cannot play no longer leaves a kernel warning behind.

### Tooling
- Image build from the repository (`scripts/build-images.sh`), kernel and
  initrd try-boot with automatic fallback, install scripts for a running phone.
- Test suites for the shell, settings, maintenance and devmode components.
- One version source (`VERSION`) stamped into `/etc/os-release`, Settings >
  About, bug reports and the packages' versions.
- Over-the-air updates without reflashing: a Debian package for every OpenDuo
  component, built reproducibly in containers; a signed apt repository with
  stable and testing suites, published by a release workflow; packaged kernels
  with their own boot entries, boot counting, health checks (compositor, touch,
  network) and automatic fallback to the previous kernel ([docs/UPDATES.md](docs/UPDATES.md)).
