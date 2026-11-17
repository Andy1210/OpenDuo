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

### Security
- Firewall closed by default; SSH only in developer mode, key login only.
- Optional encryption of user data tied to the PIN (fscrypt, direct boot,
  recovery key).
- Bug reports with personal identifiers masked.

### Fixed
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
