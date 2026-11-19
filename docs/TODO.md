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

- **The WiFi chip's PCIe link (held at Gen2 since 2026-11-19).** At Gen3 the
  link reported receiver errors and completion timeouts; three times in a day
  the WLAN firmware then stopped answering (connected, nothing got through for
  hours) or crashed, and once ath11k's recovery failed with the link still bad
  and a mac80211 oops wedged the phone (RCU stall). The stock kernel keeps this
  link at Gen2 (`max-link-speed = <2>`), and so does OpenDuo now; ath11k also
  starts its crash recovery when WMI commands time out three times in a row.
  Left to watch: a correctable receiver error was still logged once at Gen2,
  and the `msdu_done bit in attention is not set` warnings come more often
  with the panels dark. Whether a recovery from a firmware that hung without
  crashing works (the firmware ignores the WMI hang simulations) is not tested.

## Stability

- **Resets that leave no trace.** The phone restarted by itself once while
  nobody used it (2026-11-18, about 25 minutes after a boot): the journal
  goes quiet with nothing unusual before it, no oops, no shutdown, and
  nothing in pstore (not set up). Keep the kernel's last messages across a
  reset (ramoops in reserved memory: a device-tree change, tried through
  `duo2-try`), so the next one can be read.

## Updates

- Build the image from the OpenDuo packages instead of installing the
  components directly (`docs/UPDATES.md`).
- Package the root overlay (fstab, USB gadget, udev rules, UCM).
- Move the packaged files from `/usr/local` to `/usr`, and remove a component's
  files when its package is removed.

## Security

- Fingerprint unlock (the sensor sits behind TrustZone).
