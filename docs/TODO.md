# TODO

Software work that is planned but not started. Hardware support is tracked in
the README's hardware status table instead.

## First start

- **A setup wizard on the first start**: language and keyboard, time zone,
  WiFi, a lock screen PIN (and with it the offer to encrypt the home folder),
  and whether to turn on developer mode. Today the phone starts straight on
  the home screen as the user `duo`, and all of this is in Settings.

## Translations

OpenDuo's own programs are English only today. Everything below should be
translatable through gettext and follow the language chosen in Settings >
Region (the system locale). The Debian and GNOME apps in the image already
come with their own translations.

- **The shell (`shell/duo-shell`):** the top bar, quick menu, notification
  centre, lock screen and PIN entry, SIM PIN prompt and app grid. The `duo-shell`
  gettext domain and `po/duo-shell.pot` exist; no language has a `.po` file
  yet.
- **Settings (`shell/duo-settings`):** its strings are marked with `_()`
  (`gettext.gettext`), but it has no gettext domain, catalog template or
  translations yet. Bind a `duo-settings` domain the way the shell does
  (`shell/duo-shell/duoshell/i18n.py`), add a `po/` template and its update
  script, and install the compiled catalogs.
- **Screen sharing (`shell/duo-screencast`):** the screen chooser and the
  "Your screen is shared" notification.
- **Notifications from system services:** the update checks
  (`maint/duo2maint/notify.py`) and the power notifications
  (`power/duo2-autosleep`). They run as system services, so they need the
  user's session locale, not their own.
- **Nibfair (`apps/nibfair`):** its string table has English and Hungarian;
  more languages, ideally through the same catalogs as the rest.
- **Desktop entries** (`Name`, `Comment`, `Keywords` of the apps OpenDuo
  ships: Settings, Voicemail, Nibfair, the YouTube launcher, Android
  location): only English and Hungarian today.
- Language names in the pickers already show each language in its own name.

## Android apps

- **Choose the Android image in Settings, not in a terminal.** On the first
  start of Android apps, Settings asks which image to set up: without Google
  (LineageOS only; offered first) or with Google Play. The choice runs
  `waydroid init -s VANILLA` or `-s GAPPS`, which downloads the image from the
  Waydroid project's own servers. OpenDuo itself never ships or hosts Google's
  apps: their license does not allow redistribution, and keeping them out of
  the image also keeps them optional for anyone who wants an Android without
  Google. With Google Play, Settings then explains Google's device
  registration (an uncertified device until it is registered).

## WiFi

- **The WiFi firmware can hang and nothing notices it.** Seen once on the
  release image (2026-11-18): after about 19 hours up (WiFi working, the
  panels dark for five hours, the phone held awake on the charger, WiFi power
  save on, no suspend), the firmware stopped taking frames and commands at one
  moment, with no kernel message before it: from then on every frame failed
  (`failed to transmit frame -12`, 511 frames left in the queue), every WMI
  command timed out (`wmi command ... timeout`), and the firmware neither
  crashed nor asked for a restart, so ath11k never recovered. The shell kept
  showing WiFi as connected with no traffic for hours, and mobile data stayed
  idle because the default route was still on WiFi. Before it, 71
  `msdu_done bit in attention is not set` warnings in 19 hours (known on this
  chip family, maybe unrelated). A restart of the phone brought it back.
  - **Detect it:** a watchdog on the kernel's messages (a run of transmit
    failures and WMI timeouts) or on traffic (no answer from the gateway while
    associated), which tells the user and takes WiFi's default route away so
    mobile data carries on.
  - **Recover without a restart:** taking the driver out
    (`modprobe -r ath11k_pci`) with the firmware hung restarted the whole
    phone (no oops or pstore record, about seven seconds after
    `qmi failed to send wlan mode off: -110`): a hard reset, not a panic. A
    safe way needs the driver to skip the firmware handshakes it cannot get
    and reset the chip over MHI / PCIe, as on a firmware crash; this is
    kernel work and has to go through `duo2-try`.
  - **Find the trigger:** a long run on WiFi with the panels dark, with and
    without power save, with the firmware's own logs if they can be reached.

## Updates

- Build the image from the OpenDuo packages instead of installing the
  components directly (`docs/UPDATES.md`).
- Package the root overlay (fstab, USB gadget, udev rules, UCM).
- Move the packaged files from `/usr/local` to `/usr`, and remove a component's
  files when its package is removed.

## Security

- Fingerprint unlock (the sensor sits behind TrustZone).
