# Boot

## A seamless boot

By default the framebuffer console (fbcon) takes over the display when the
`msm` driver loads and prints boot text on the panels until the compositor's
first frame; at shutdown the DRM master leaves and the shutdown text scrolls.
The ESP's boot entry therefore carries `fbcon=map:1 vt.global_cursor_default=0`
(`scripts/build-esp.sh`, `SEAMLESS`): all consoles map to a framebuffer that
does not exist, so the panels keep the firmware's picture until the
compositor draws. The rescue entry keeps the text console.

- `getty@tty1` is masked and logind starts no text logins
  (`logind.conf.d/duo2-no-vt-logins.conf`: `NAutoVTs=0`, `ReserveVT=0`; there
  is no keyboard). Log in over ssh or the `ttyGS0` serial gadget. Kernel and
  system messages are in the journal; a kernel panic is not shown on the
  panels (no `DRM_PANIC`), `panic=30` reboots.
- The panel driver does not pull the reset line in its probe (`GPIOD_ASIS`),
  only in the first `prepare`, so the firmware's boot logo stays until the
  compositor's first frame.

## Boot picture from the initramfs

The panels are held in reset from the TLMM probe until the real root's udev
loads `msm` (~5 s). `early_display` in `rescue/init` shortens that: after the
root is mounted, and before `switch_root`, it runs in a chroot of the root:

- `modprobe -a gpucc-sm8350 qcom-refgen-regulator panel-microsoft-elgin msm`
  using the root's kmod, modules and `modprobe.d`, so the initramfs itself
  stays kernel-independent (no modules, firmware or Plymouth inside, ~1 MB)
  and works with every `duo2-try` kernel. These four are needed because the
  `msm` component master binds only once both DSI hosts (added by the panel
  driver's `mipi_dsi_attach`), the GPU (needs gpucc and the GMU) and the
  DSI PHY regulator (refgen) are there.
- `firmware_class.path` points to the root's firmware for that window only
  (the msm fbdev client opens card0 during the bind, which loads the GPU
  firmware), then is cleared (written with a NUL byte; a newline would not
  clear it).
- When card0 exists (waits at most 6 s), the root's `plymouthd` starts in the
  chroot with `plymouth.ignore-udev` and shows the splash; the real
  `plymouth-start.service` is skipped when the initramfs' Plymouth answers.
- If anything is missing (no module for the kernel, no card0, no Plymouth,
  `nosplash`, `plymouth.enable=0` or `duo2.early-display=0`) the root brings
  the splash the old way. The boot never waits for the splash without a limit.
- With the DisplayPort external display enabled, the display stack also needs
  `phy_qcom_qmp_combo`, `aux_bridge`, `pmic_glink_altmode` and
  `aux_hpd_bridge`, which this early path does not load; the splash then comes
  from the root's udev (~5-6 s).

## The ESP is mounted read-only

Linux sets the FAT dirty bit while a volume is mounted read-write and never
clears one it found set, so a hard reset in the 30 s automount window left
"Volume was not properly unmounted" on every later mount. The ESP is
therefore read-only in `fstab`; whoever writes it remounts it
(`/usr/local/sbin/duo2-esp rw|ro`; `duo2_esp_ssh` in `scripts/lib/remote.sh`
wraps the writers in `duo2-try` and `duo2-promote`). The rescue initramfs
mounts it read-write only when the boot stays in the rescue shell. If the
dirty bit does get set, `fsck.vfat -a` clears it (back up the reserved
sectors first).
