# Android apps (Waydroid)

Android apps run in an LXC container (LineageOS 20 = Android 13, Waydroid's
image, with or without Google apps); each app is its own window in duo-wc.
Files: `android/` (installer `android/install.sh`; the image recipe adds it
through `rootfs/Dockerfile` and `rootfs/install-components.sh`).

## How it runs

- Install: `sudo waydroid init -s GAPPS` (or `-s VANILLA`) once as the
  administrator; the ~1.4 GB image is downloaded from Waydroid's OTA channel,
  checked by sha256, and is neither in git nor in the image. Then set
  `persist.waydroid.multi_windows = true` in `/var/lib/waydroid/waydroid.cfg`
  `[properties]` (one window per app; kept across `init -f` and `upgrade`).
- **On demand.** The root container manager (`waydroid-container.service`)
  is disabled at boot and D-Bus-activated when a session asks. The session is
  the GUI user's `duo2-waydroid-session.service`, activated by D-Bus when
  `waydroid app launch PKG` asks for the name `id.waydro.Session`. It reads
  duo-wc's `session.env` (Wayland socket, PipeWire socket) and uses
  `GRID_UNIT_PX=20`, i.e. 400 dpi, the stock Duo 2 density.
- **Stopping.** `duo2-waydroid-idle` looks at duo-wc's window list every 30 s
  and, after `IDLE_MIN` minutes (default 5; `/etc/duo2/waydroid.conf`, a
  per-user override in `~/.config/duo2/waydroid.conf`) without an Android
  window, stops the session and with it the container, freeing all of
  Android's memory. `duo2-waydroid-reaper.timer` stops the root manager when
  no container has existed for that long. With no visible window Waydroid
  itself freezes the container (cgroup freezer): 0 % CPU.
- **Keep running in the background** (Settings > Android apps, off by
  default, `KEEP_RUNNING = yes`): the session stays up without windows and
  the container does not freeze when Android asks to suspend
  (`duo2-waydroid-patch` patches Waydroid's `hardware_manager.py`), so
  messengers stay connected, at the price of battery.
- **Updates:** weekly through `duo2-waydroid-upgrade.timer` (WiFi only, only
  when Android is not running); the in-Android updater is disabled.
- The package comes from repo.waydro.id (pinned in
  `preferences.d/waydroid`, with its key in the image), the rest from Debian.
- The container's own network namespace has its own firewall
  (`android/firewall.nft`); inside it QRTR is unavailable (see
  [security.md](security.md)). Position comes from the bridge in
  [location.md](location.md). Android's typing uses its own keyboard inside
  the window, not the on-screen keyboard.
- The container has no framebuffers and no V4L2 nodes of the host
  (`duo2-waydroid-patch` marks this in its LXC config).

## Measured

The GPU is hardware (`GLES: freedreno, FD660, OpenGL ES 3.2 Mesa`, not
swiftshader/llvmpipe). Scrolling Settings (12 swipes, `dumpsys gfxinfo`):
611 frames, 0.33 % janky, frame time p50 7 ms / p90 10 ms / p99 14 ms.
First launch takes ~14 s (container + Android boot), a running container
under 1 s.
