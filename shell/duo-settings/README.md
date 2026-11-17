# Duo Settings

duo2-os's settings app: Python + GTK4 + libadwaita, adaptive like GNOME
Settings on a phone. On one panel (538 x 757 logical) it is a list of pages
and one page at a time with a back arrow; spanned (wider than 640 sp) the
list and the page sit side by side. Dark, rows at least 50 px high, every
control a stock libadwaita row. App id `org.duo2os.Settings`; in the app grid
as "Settings", and the quick menu's **Settings** button opens it on that
panel. `duo-settings PAGE` opens a page (`wifi`, `bluetooth`, `hotspot`, `display`, `battery`, `region`,
`sim`, `sound`, `notifications`, `rotation`, `laptop`, `lock`, `android`, `appearance`, `wallpaper`, `home`, `datetime`, `region`, `pen`, `touch`, `about`);
a second start goes to the running window. The desktop file's actions do the same (`battery`:
`duo-settings battery`, which the home screen's battery card launches).

Pictures: `out/shell-shots/settings-*.png`, `apps-settings-list.png`.

## Pages

| page | what | through |
| --- | --- | --- |
| Wi-Fi | radio on/off; the network in use (signal, address, Forget); the networks around (signal, lock, "Saved"); tap to join, with a password dialog when needed | wpa_supplicant's control socket (`/run/wpa_supplicant/wlan0`, group `netdev`); the radio via `duo-shell-helper wifi` |
| Bluetooth | power; my devices (connect, disconnect, forget; "Connected · Battery 64 %" where BlueZ knows the battery); nearby devices (tap to pair) while the page is open | BlueZ on the system bus as the user, one `bluetooth.Bluez` for the window (`shared`: the Pen page uses it too); a pairing agent (`org.bluez.Agent1`, KeyboardDisplay) answering PIN / passkey / confirmation in dialogs (BlueZ makes the adapter bondable only while a default agent is registered: `AlwaysPairable` is off); a pairing sets the device trusted and connects it; the battery is `org.bluez.Battery1` (GATT Battery Service); a radio rfkill-blocked by airplane mode is freed first (`duo-shell-helper bluetooth on`) |
| SIM card | **Voicemail number** (below "Voicemail"); the SIM's state; **Lock the SIM with its PIN** (on / off, asks the current SIM PIN, the attempts left under it, "1 attempt left: a wrong PIN blocks the SIM"); **Change the SIM PIN** (current, new twice; only with the lock on, as SIMs allow); a SIM waiting for its PIN or PUK is the shell's PIN dialog's, the page only says so | ModemManager on the system bus as the user (polkit's Device.Control): `Modem3gpp.EnabledFacilityLocks` ("sim"), `UnlockRetries`, `Sim.EnablePin` / `DisablePin` / `ChangePin` (`duosettings/sim.py`); a PIN goes to ModemManager only, is never logged, stored or shown, the fields are emptied when the dialog closes |
| Hotspot | share the mobile data: WiFi hotspot on/off (its band, channel and whether it runs beside the phone's own WiFi), network name, password (Show / Copy, a new one, your own: Save only for 8 to 63 characters without accents, the dialog says what is wrong; the password goes to the helper on stdin), band (5 / 2.4 GHz), security (WPA2/WPA3, WPA3, WPA2); the connected devices (name, address, WiFi / USB, minutes); USB tethering (asks first: the USB connection drops for a moment); if WiFi cannot stay on (don't start / ask to switch WiFi off: greyed in development mode), off with no device (never, 5-60 min), use 5G for the hotspot, data shared this time | `sudo -n duo2-hotspot` (hotspot/README.md): on, USB on and a new password are asked of the user in the shell (this app has no grant); the state from `/run/duo2/hotspot.json` every 3 s (no password in it), the bytes every 10 s while on; the password is fetched only for Show / Copy and forgotten after 60 s or when the page closes (`duosettings/hotspot.py` pure, `tests/test_hotspot.py`; `tests/gtk_hotspot_page.py` under GTK) |
| Display | brightness (both panels), screen off after (30 s to never), refresh rate 60/90 Hz, scale 200-300 %, hinge gap | brightness: `duo-shell-helper`; idle and hinge gap saved in `duo-wc.conf` and applied live by the helper (duo-wc's admin socket); refresh and scale take a display restart (offered on the page) |
| Appearance | **Style: Dark / Light / Automatic** (light from / dark from, whole hours; dark by default): the apps follow it; the bars, the home screen and the lock screen stay dark (AMOLED), the on-screen keyboard (phosh-osk-stub, libadwaita) follows the apps | `duo-appearance` (the user's, next to this app): `~/.config/duo2/appearance.conf`; GSettings `color-scheme` (`prefer-dark` / `prefer-light`: `default` would leave apps that ask for dark dark; the portal hands it to Flatpaks) and `gtk-theme` (Adwaita-dark / Adwaita, GTK 3); Qt / KDE apps: Breeze Light's colours in `~/.config/kdeglobals` (a file marked as the tool's; a user's own is never touched), removed again for dark (the system's `/etc/xdg/kdeglobals` is dark), running ones told by KGlobalSettings `notifyChange`; Android: `sudo duo2-android-task night yes|no` while it runs, and `duo2-waydroid-idle` asks again when Android starts; Automatic: the user timer `duo-appearance.timer` (every 15 min, enabled only for Automatic). `duosettings/appearance.py` pure, `tests/test_settings.py`; `tests/gtk_appearance_page.py` under GTK |
| Battery | the level and the charger; **Sleep: Stay awake while charging** (on by default: on external power the phone does not go to sleep, the screens still turn off, so it stays reachable over the network), with an "Automatic sleep" line only while something else holds it (switched off, or "Kept awake for remote work until 18:40": a timed hold); **battery usage since the last full charge**, Android style: a graph of the level (charging shaded, screen-on and asleep strips under it), the % used and the time on battery, the apps and the parts of the phone (screen, idle, standby, mobile network, Wi-Fi, Bluetooth, the DSPs, the compositor, the shell, the kernel...) by their estimated share; tap one: CPU time on the small / medium / big cores, GPU time, time on screen and active in the background, or the part's own counters; "Include time on the charger"; how the numbers are made; **Clear usage history** | the switch: `stay_awake_charging` in duo2-autosleep's `/etc/duo2/autosleep.conf`, set through `sudo -n duo2-autosleep-switch charging on\|off` (power/, the sudoers rule allows exactly that and on / off; the daemon reads the file again by itself), the holds from `/etc/duo2/autosleep-hold` and `autosleep-hold-until` (`duosettings/sleep.py` pure, `tests/test_settings.py`); the collector `duo2-battery-stats` (`battery/`, below "Battery usage"); its report through `duo-settings-helper battery refresh` |
| Sound | volume, mute, output, **ringtone** (below), sound mode, vibration | `wpctl` (default sink), `pactl` (outputs), feedbackd's GSettings and a feedbackd user theme |
| Notifications | do not disturb (no banners, no sound or vibration for notifications and calls; alarms and critical notifications still); **calls that ring anyway**: repeated calls (15 min), calls from contacts | GSettings `org.gnome.desktop.notifications show-banners`, which duo-shell honours (dnd.py: Calls' ringer through feedbackd's per-app profile); the exceptions in `~/.config/duo-shell/dnd.json` (`dndprefs.py`) |
| Rotation & poses | use the sensors (duo-posed `enabled`), auto-rotate, automatic poses, hinge gap; **Panels: Both / Left only / Right only** (2026-11-07: the old Dual / Left / Right / Span pose buttons are gone: one canvas, a window spans by itself) | `/etc/duo-posed.conf` (helper, then duo-posed reloads): `panels` is the user's choice over the sensors, the helper sets the matching pose at once (duo-wc's admin socket); the hinge gap as on Display |
| Laptop mode | **Laptop mode** on / off (one screen a desktop of floating windows with a taskbar and a pointer, the other a keyboard and a touchpad: compositor/duo-wc README "Laptop mode"); **Turn on in the laptop posture** (off by default: one half flat, the other up; off again when the phone leaves the posture); **Keyboard screen** Left / Right when the phone is not turned sideways (sideways it is always the lower one); what is on now | `duo-settings-helper laptop on\|off`, `laptop auto on\|off`, `laptop keys DSI-1\|DSI-2` (duo-wc's admin socket, `set_laptop`; duo-wc keeps the two settings in its own state file, not in duo-wc.conf); the state from duo-wc's `get_state` (`laptop`), every 2 s while shown (`duosettings/laptoppage.py`) |
| External display | **When a display is connected**: Ask / Desktop / Mirror (Ask: the shell asks on the phone, the display mirrors meanwhile); **A mirror shows**: The screen in use (it follows the finger) / Both screens; what is connected now and how it is used (compositor/duo-wc README "A display as a desk or a mirror") | `duo-settings-helper display on_connect ask\|desk\|mirror`, `display mirror active\|both` (duo-wc's `set_external`; duo-wc keeps them in its own state file); the state from `get_state` (`displays`), every 2 s while shown (`duosettings/displayspage.py`) |
| Lock screen | set / change / remove the PIN (asks the current one), lock when the screen turns off (only with a PIN), lock when folded (switching a lock off asks the PIN when one is set); **Encrypt my data** (only with a PIN): the warnings first (a forgotten PIN loses the data, the recovery key, free space and time, a short PIN can be guessed with tools, deleted files), then the PIN; the recovery key (36 digits) shown once, never copied; a restart offered (the encryption runs while the phone starts); a waiting change with Cancel / Restart now; switching off decrypts at the next restart; New key; removing the PIN is refused while encrypted (homecrypt/README.md) | root's store `/var/lib/duo2-lock` through `duo-lock-helper` (`set`, `clear`, `policy`, `home enable\|disable\|cancel\|recovery-key`, the PIN on its stdin; the page reads only the policy file and the public `home.state`, never a hash or a key); turning the lock on (and encryption) also turns on duo-wc's `session_lock`; the restart is `duo-shell-helper reboot` (the shell asks); `duosettings/system.py` `home_view` pure, `tests/test_settings.py`; `tests/gtk_lock_page.py` under GTK |
| Updates | OpenDuo's own packages and Debian's: the state (looking, installing, up to date, restart to finish), Check now, Install (a snapshot first; only on the charger or at 50 % battery), what's new (the CHANGELOG sections of the build on offer), the packages, the new kernel's checks; tell me when updates are ready, look on mobile data too, install at night (WiFi + charger, off by default); the testing channel in developer mode | `sudo -n duo2-updates` (maint/duo2-updates, docs/UPDATES.md): the check and the install run as systemd units, Install and Restart are asked of the user in the shell; the status every 30 s, every 2 s while a job runs (`duosettings/updates.py` pure, `tests/test_updates.py`; `tests/gtk_updates_page.py` under GTK) |
| Developer | developer mode (SSH, its keys, USB networking), **Log touch positions** ("For debugging, can reveal what you type": off by default, asks in the shell, off again with developer mode and after a restart), the developer tools' state (installed only with developer mode on), a bug report | root's `duo-devmode` (devmode/README.md) through sudo: `status`, `mode`, `ssh`, `usb`, `key add\|remove`, `touchlog on\|off`, `bugreport`; `duosettings/developerpage.py`, `tests/gtk_developer_page.py` under GTK |
| Android apps | **Keep Android apps running in the background** (off by default): off, Android (Waydroid) is paused a few minutes after the last Android window closes and stopped after `IDLE_MIN` (5) minutes; on, it keeps running without a window so messengers stay connected and notify, at a battery cost (memory, its own timers wake the phone); the state now (not running / paused / running); switching on while paused wakes it at once | `KEEP_RUNNING` in `~/.config/duo2/waydroid.conf` (over `/etc/duo2/waydroid.conf`), read at every check by `duo2-waydroid-idle` (no idle stop) and by the container manager's `hardware_manager.py` as `duo2-waydroid-patch` changes it (Android's suspend request does not freeze the container); the state from the container's cgroup (`lxc.payload.waydroid`, `cgroup.events`); the wake is Waydroid's own `id.waydro.ContainerManager.Unfreeze` on the system bus (`duosettings/android.py` pure, `tests/test_settings.py`; `tests/gtk_android_page.py` under GTK) |
| Home & apps | the app drawer as a grid or an A-Z list | `~/.config/duo-shell/launcher.conf` (`view`), read by duo-shell when the drawer opens |
| Language & region | interface language (the generated locales, native names), formats region with a live preview (date, time, number, first weekday, units, paper), first day of the week override; Apply asks, then restarts the session | `/etc/locale.conf` through the helper (`locale-set`) |
| Wallpaper | the picture for the home and lock screens: a grid of thumbnails (Default = the dark gradient, `gnome-backgrounds`' dark pictures, "Your picture"), Choose image (GTK file chooser through the portal), same on each panel / across both panels, and the Bing image of the day (switch, region from the locale, the day's title) | `~/.config/duo-shell/wallpaper.conf`, watched live by duo-shell; the Bing switch starts the user service `duo-wallpaper-bing` (once a day; it contacts bing.com only while on) |
| Date & time | now, network time on/off, time zone (searchable list) | timedated (read property by property: `timedatectl show` fails on this phone, the RTC property gives an I/O error); changes through the helper |
| Keyboard | the keyboard layouts in order (the first in use at the start; the keyboards' layout key switches), each moved up / down or removed (not the last), one added from a list (names in their own language; at most four) | `org.gnome.desktop.input-sources` `sources` with Gio.Settings: the on-screen keyboard reads it, the shell sends it to duo-wc for a keyboard you connect and laptop mode's keyboard (compositor/duo-wc README "Laptop mode"); `duosettings/keyboard.py` (pure, `tests/test_keyboard.py`), `duosettings/keyboardpage.py` |
| Pen | what the pen does now (hovering over / writing on / erasing on a screen, every second); **Pen test** (opens `touch/tools/pen-test.py` on this panel: the live values and a canvas); **Bluetooth**: the paired pen ("Surface Slim Pen 2", connected and its battery, or "press the top button to connect it") and **Pair your Surface Pen** (Pair / Pair again): a dialog says to hold the top button 5-7 s until the light flashes white, the page looks (LE only) and pairs the first Surface pen heard advertising since; a pen paired here before that is in pairing mode again is forgotten first and paired when heard again; "is paired and connected", "Paired. Press the top button to try it.", or after 90 s "No pen was found"; **Top button**: Click / Double click / Press and hold, each Open Rnote / Screenshot / Quick menu / Nothing (defaults: Rnote, screenshot, quick menu; nothing while the screen is off or locked); **Feel**: Writing feel Off / Ink / Pencil / Marker / Chisel marker / Brush (short pulses the phone sends to the pen every few millimetres while its tip moves: the pen's own inking waveforms never play on the Duo 2; off by default), Strength (1-100 %, default 50), Click when the top button is pressed (off by default); **Firmware**: In the pen (the versions read at its last connection), On this phone (the newest Microsoft set there, and whether it is newer), Update (asks first; progress, "press the pen's top button" while it waits for the link, the result: "Updated to ..." and the new versions in "In the pen" at once, from the versions the update read at its end; Stop); **Palm rejection**: Both screens (default) / The pen's screen / Off; **Apps**: Pen input Drawing apps only (the default: the drawing apps get the pen as a pen, every other app as a finger, so pages and lists scroll with it) / All apps that take a pen, and **Drawing apps** (the list, app ids or their last part, comma separated; Apply, and a button for the default list: Rnote, Xournal++, Krita, GIMP, Inkscape, MyPaint, the Pen test, Nibfair); **Side button**: Right click (where there is a pointer: laptop mode, a mouse, a display's touchpad; drawing apps take the button themselves) / Nothing; **Show where the pen points** (the hover dot); **Charge pen from phone** (the Surface Duo 2 Pen Cover; off by default, shown only on a kernel with the pen charger; "Paused" while the stock rule holds it off: below 20 % on battery) | palm rejection: `palm_rejection` in `/etc/duo2/pen.conf` through `duo-settings-helper pen palm both\|panel\|off` (duo2-touchd reads the file again by itself, touch/README.md); the feel: `writing_feel`, `feel_strength`, `button_click` in the same file through `duo-settings-helper pen feel\|strength\|click` (duo2-pend reads it again by itself and sets the pen at every connection; `pen.haptics_from_conf` reads it as the daemon does); the firmware: `/var/lib/duo2-pen/firmware.json` (duo2-pend, at every connection), `duo-settings-helper pen fw-available` (the root-only `/var/lib/duo2-pen-fw`), `pen fw-update` / `pen fw-stop` (touch/tools/pen-fw.py `--until-done` as the unit duo2-pen-fw-update), its progress in `/run/duo2-pen-fw/status.json`; the button, the dot and the apps: `pen_button`, `pen_dot`, `pen_input`, `pen_apps` through `wc-conf` (duo-wc's `set_pen`, live; `pen.pen_apps_value` and the helper check the list the same way), read back from `get_state`'s `pen`; the charger: `duo-settings-helper pen charge on\|off` runs power/'s `duo2-pen-charger on\|off`, read back from `/etc/duo2/pen-charger` and the kernel's `/sys/devices/platform/pen-charger/state`; the pen over Bluetooth: the window's `bluetooth.Bluez` (a Surface pen: "pen" in its name and Microsoft's by name, company id 0x0006 in its manufacturer data or vendor 045E, or a nameless digital pen by appearance with that company id; `pen.is_surface_pen`, the same rule as the shell's), `SetDiscoveryFilter` `le` while it looks and none after, the agent's dialogs are the Bluetooth page's (`pen.PairFlow` pure); the top button: `click`, `double_click`, `hold` in duo-shell's `~/.config/duo-shell/pen.conf`, written as the user, read by the shell at every press (`duoshell/penmodel.py`; `tests/test_pen.py` checks both read the same) (`duosettings/pen.py` pure, `tests/test_pen.py`; `tests/gtk_pen_page.py` under GTK, with a stand-in BlueZ: the rows, pen.conf, the pairing walk) |
| Touch calibration | calibrate a panel: `touch-calib.py` (from `compositor/duo-wc/tools`) on that panel, five crosshairs, two taps each; a fit, the error before and after, Apply or keep; reset to the shipped calibration | the fit is `duosettings/touchcal.py`; Apply writes `/etc/udev/rules.d/91-duo2-touch-calibration.rules` (over the shipped `90-duo2-touch.rules`) and restarts the display |
| About | like Android's "About phone": the device name and four sub-pages, below. Every value is a label over its value, not selectable (a tap shows no text cursor); a long press copies it (toast "Copied") | `duosettings/about.py`, `aboutpage.py` |

## About (2026-11-08)

`duosettings/about.py` (readers + pure parsers, `tests/test_about.py`) and
`duosettings/aboutpage.py` (an `Adw.NavigationView` inside the page; a sub-page's
header has the back arrow, the root's goes back to the list). Sub-pages load in a
thread and, for Status, refresh every 10 s while shown. Where each value comes from:

| page | rows | source |
| --- | --- | --- |
| Status, per SIM slot | operator, network type (5G / 4G ...), signal strength (ModemManager's quality %), roaming, phone number (only when the SIM stores it, else "Unknown") | ModemManager (`mmcli -J`, D-Bus, works in the session); slot N = `sim-slots[N-1]`, the active one is `primary-sim-slot`; an inactive slot says "Not in use" and the SIM's operator |
| Status, identity | IMEI, IMEI SV | `duo-settings-helper identity` -> systemd-run (outside the session's QRTR ban) -> `duo-settings-qmi`: QMI DMS Get IDs over QRTR (IMEI + IMEI SV). The reader also tries DMS Bind Subscription for a per-slot IMEI; **this modem answers error 58, so there is one IMEI** (the IMEI SV is the modem's too) and the page shows it once. Fallback: ModemManager's equipment id |
| Status, addresses | Wi-Fi IP, mobile IP (the bearer's interface), Wi-Fi MAC (`wlan0`), Bluetooth address | `ip -j addr`, `mmcli -b`, `/sys/class/net`, BlueZ `Adapter1.Address` |
| Status, device | serial number, up time | SMBIOS `product_serial` / `board_serial` / `chassis_serial` (root-only in sysfs: read by the helper, placeholders refused); `/proc/uptime` |
| Heading | OpenDuo, its version large, then the build id, kernel and "Based on Debian 13" | `/etc/duo2-release` (`openduo=` from the repo's `VERSION` file, `build=` UTC date + git short hash) |
| Software | OpenDuo version + build id and date, Debian version, kernel + build date, modem firmware (ModemManager Revision), last security update, graphics driver (Mesa), Android apps (Waydroid, with the Android version when it runs), compositor, GTK / libadwaita | `/etc/duo2-release` (written by `scripts/lib/release-stamp.sh`: version, build id, git describe, branch, time; `rootfs/install-os-release.sh` writes `/etc/os-release` from it; the image gets it from `scripts/build-images.sh`, a phone from `install.sh`); `uname`; apt's `history.log*`: the newest run that upgraded a package (Debian has no separate security channel to tell apart); `dpkg-query`; `waydroid status` / `prop get` |
| Hardware | model, SoC (soc_id 415 = SM8350 / Snapdragon 888), CPU cores by cluster and max frequency, GPU, memory, storage; battery (charge, design capacity, health, cycles, technology); displays (count x size, refresh); camera sensors; sensors | `/sys/devices/soc0`, `/proc/cpuinfo` + cpufreq, `/sys/block/sda`, qcom_battmgr (`charge_full*` give "no data" on this phone: the design capacity is read from the cell's name, `..._3600MAH_...`; health = `state_of_health`; cycles as reported), DRM modes + the panel driver's `refresh`, the sensor sub-devices' names (`/sys/class/video4linux/v4l-subdev*`: imx481 x 3, imx563; libcamera is not opened, it would disturb the camera work), iio-sensor-proxy's `Has*` properties |
| Legal | open-source licences: every installed package and its licence names (Debian copyright files; tap for the file; search), where the licences are | `/usr/share/doc/*/copyright`, `/usr/share/common-licenses`; **the duo2-os components have no licence file yet** and the page says so |

The identifiers are personal: they are shown in the app only, never logged. Pictures and the GTK test use
`DUO_SETTINGS_FAKE_ABOUT=1` (made-up values everywhere, nothing read from the phone). `tests/run-gtk.sh
tests/gtk_about_page.py` draws the pages under GTK 4 + libadwaita and checks the rows, that no label is
selectable, the long-press copy and the back navigation.

**The pen scrolls like a finger** (`duosettings/penscroll.py`, the same file as the shell's `duoshell/penscroll.py`): GTK 4 drags a scrolled view only for a touchscreen (GtkScrolledWindow's gestures are `touch_only`), and a tablet tool's tip is a button press, so in every GTK app a pen drag selects instead. The window has one capture-phase drag gesture for the pen (on Wayland the press's device is the tablet's logical pointer, whose source is a mouse: the event's device tool says pen, `penscroll.is_pen`; until 2026-11-16 it looked at the device's source and never matched). With duo-wc's `pen_input = apps` (the default since 2026-11-16) Settings gets the pen as a finger and GTK scrolls by itself; this is for `pen_input = all`. past the drag threshold it claims the press (the row under the pen is not activated, as with a finger) and moves the scrolled view under it; a flick glides on. A slider or a text field that takes the press keeps it, a widget with the `pen-input` style class opts out, a mouse and a finger stay GTK's. `tests/gtk_pen_page.py` checks it on the Pen page.

Going back on a phone leaves no row lit: the list has no selection while collapsed, the window drops
the focus when a page closes (a pointer left under the finger and GTK's focus restore used to light a
different row), and `.touch-layout` takes the hover colour from activatable rows (press still shows).

## Root actions

`duo-settings-helper` (sudoers rule for exactly that file:
`/etc/sudoers.d/duo-settings`) takes a fixed set of commands and checks every
argument against a list or a pattern (see its `--help` text): the panel
refresh, six `duo-wc.conf` keys, the pen's palm rejection (`pen palm`), restarting duo-wc (asked in the shell
first, below), laptop mode (`laptop on|off|toggle`, `laptop auto on|off`,
`laptop keys DSI-1|DSI-2`: duo-wc's `set_laptop`), three `duo-posed.conf` switches and `panels`, the time zone, network time, the language and formats (`locale-set`), a touch matrix (near identity only). The
WiFi radio and the brightness go through duo-shell's own helper.

**Duo Settings is an app** (docs/hardware/security.md, 2026-11-07): duo-wc
answers it only the state and the launch hint on the user's socket. What it
changes in duo-wc live (idle timeout, hinge gap, the lock on, the pose, laptop mode) goes
through this helper, as root, to duo-wc's admin socket: `wc-conf KEY VALUE`
saves the key and applies it live, `pose dual|span|single DSI-1|single DSI-2`
sets the pose. `battery refresh|clear` signals the battery usage collector
(a fresh report, printed on stdout, or the usage history cleared). Every app of the user can run the helper too (sudo cannot
tell them apart), so it offers nothing more: `session_lock off` is refused
(the lock is switched off only by the administrator: `duo2-sensors-mode off`
or the config), and nothing unlocks, takes a screenshot or reaches other
apps' windows. **Restarting the display** (the refresh rate, the language,
the touch calibration, the Display page's button) is not done by the helper
any more (any app could have restarted duo-wc and closed every app): it
asks duo-wc (`confirm restart-display`), the shell asks the user on the
active panel, and duo-wc restarts only on that yes (2026-11-07). The lock's
PIN is not the helper's either: `duo-lock-helper` (shell/duo-shell) is its
one door.

The refresh rate is the panel driver's module parameter
(`panel_microsoft_elgin.refresh`), which decides the driver's preferred mode;
duo-wc takes that mode when it starts. The choice is kept in
`/etc/duo-settings/display.conf` and written to the driver before duo-wc
starts at boot (`duo-panel-refresh.service`). No boot entry is touched.

## Battery usage

The Battery page's numbers come from **`duo2-battery-stats`** (`battery/`,
installed by its own `install.sh` / `install-files.sh`, also in the image):
a small collector service. The page asks it for a fresh report
(`sudo duo-settings-helper battery refresh`: SIGUSR1, then the report on
stdout) when it opens and every minute while it is shown.

**The window: since the last full charge**, Android's choice: from where the
phone was last unplugged at 90 % or more (Android resets its statistics on
that unplug); without such an unplug in the store, from its oldest data. The
list counts the time **on battery** (what drained it); "Include time on the
charger" adds the time plugged in. Shares are of the estimated use and add
up to 100 %; "% used" is the level the battery lost on battery.

**How the estimate is made** (`battery/duo2battery/model.py`). The phone has
no per-rail power monitor (the MAX34417s are only on Microsoft's development
boards), so as on Android every part is estimated from what it did, with the
manufacturer's measured currents, and the sum is scaled to what the battery
measured:

| part | what is counted | figure |
| --- | --- | --- |
| apps and system processes | CPU time per process (`/proc/PID/stat`); which cluster: each thread's last CPU, then fitted so every cluster's total matches its measured busy time (`/proc/stat`; iterative proportional fitting); at the speeds each cluster ran (cpufreq time in state) | Microsoft's per-speed core currents (`cpu.core_power.clusterN`), + `cpu.active` |
| GPU, per process | the msm DRM client's busy time and cycles (`/proc/PID/fdinfo`), the holder of a client found once | assumed (no figure in the profile), scaled with the square of the speed |
| screen | per panel: lit time (DRM connector `dpms`) and brightness (backlight), every 10 s | `screen.on` + `screen.full` x brightness, taken as both panels together |
| idle (awake, panels off) and standby (suspended) | awake / suspended time (CLOCK_MONOTONIC vs CLOCK_BOOTTIME) | assumed; the calibration learns them |
| mobile network | in service, the modem's awake time (debugfs `qcom_stats`), bytes on `rmnet_ipa0` | `radio.on`, `modem.controller.idle`, `radio.active` |
| Wi-Fi, Bluetooth | on time, bytes and packets (HCI counters for Bluetooth) | `wifi.controller.*`, `bluetooth.*` |
| DSPs | the audio, compute and sensor DSPs' awake time (`qcom_stats`) | `dsp.audio` for the ADSP and the CDSP, an assumed low figure for the SLPI |

The calibration (per group of intervals, about 10 mAh of drain): a scale per
part (idle, standby, screen, cpu, gpu, radio, dsp) learned by a ridge
regression over the discharge groups (starting at 1), then the group's
estimates scaled once more so that they **add up exactly to the drain the
battery measured** (the charge counter, 1 mAh steps; while it stands still
at 100 % the current bridges it). On the charger with the battery not
charging, the drain is the charger's input power x 0.88 (assumed efficiency)
less what the battery takes; while charging there is no measurement and the
figures stand as they are. The page says how far the figures alone were
from the measurement.

Which app a process belongs to (`battery/duo2battery/classify.py`): GLib's
`GIO_LAUNCHED_DESKTOP_FILE` in the process's environment (duo-shell launches
apps through GDesktopAppInfo; the app's children inherit it; only that one
variable is read), a Flatpak's `/.flatpak-info`, Waydroid's container (the
Android app's package name), a table of the phone's own parts
(`system:compositor`, `system:shell`, `system:kernel`...), otherwise the
program's name, which the page matches to a desktop file's `Exec` (Calls in
the background is the same row as Calls on screen). "On screen" is the time
a window of the app was the front one on a lit panel (duo-wc's `get_state`,
every 10 s while a panel is lit).

**The power figures** are Microsoft's own, from the phone's stock images:
`scripts/build-power-profile.sh` extracts `res/xml/power_profile.xml` from the
vendor overlay `FrameworksResTarget.apk` (compiled XML, decoded by
`scripts/axml2xml.py`) into `out/power-profile/`, and
`scripts/install-power-profile.sh` puts it on the phone as
`/opt/duo2-power-profile/power_profile.xml`. Never in git or in the image
(the repository has only the recipe); without it the collector takes the
CPU from the kernel's energy model and assumes the rest.

**The collector** runs as root, capability-bounded (`CAP_SYS_PTRACE` only, the
ptrace system calls themselves filtered out; read-only system, no `/home`, no
IP): three of its sources are root's (debugfs `qcom_stats`; the GPU fdinfo of
duo-wc and the shell, which make themselves non-dumpable; duo-wc's admin
socket for the windows on screen, where it only sends `get_state`). It
samples once a minute and looks at the screen every 10 s; every wait is a
`select()` timeout on CLOCK_MONOTONIC, which stands still in suspend: it never
wakes the phone, and the systemd sleep hook takes a sample right before and
after a suspend. It writes its store every 10 minutes. Measured on the
phone: 0.17 % of one core (1.9 s of CPU in 18 minutes, about 0.1 s a
sample), 10 MB of memory; `systemd-analyze security` 2.2.

**Privacy.** Which apps ran when is personal data: it stays on the phone, in
`/var/lib/duo2-battery/usage.db` (SQLite, root:duo2-battery 0640 in a 0750
directory; the GUI user is in the `duo2-battery` group, which takes a new
login) and `/run/duo2-battery/report.json`; nothing is sent anywhere. Kept 7
days, then pruned. **Clear usage history** (`duo-settings-helper battery
clear`: SIGUSR2) deletes the samples, the periods, the per-app use and the
calibration groups; only the calibration's learned scales stay (no personal
data). Like every helper command, any app of the user can run it.

Files in `battery/`: `duo2-battery-stats` (`run`: the service; `report [DB]`:
a store's report on stdout), `duo2battery/` (`collector.py` the loop and the
signals, `sources.py` the readers and their parsers, `classify.py`,
`model.py`, `profile.py`, `store.py`, `report.py`), `duo2-battery-stats.service`,
`duo2-battery-stats.sleep` (the sleep hook, `/usr/lib/systemd/system-sleep/`),
`install.sh` / `install-files.sh`, `tests/` (`python3 -m unittest discover -s
tests` there: 42, the attribution math and the suspend accounting included).

## Secrets

A WiFi password is typed into a password field, goes to wpa_supplicant and
nowhere else: for WPA2 the app derives the 256-bit PSK (PBKDF2, as
`wpa_passphrase` does) and wpa_supplicant's config keeps only that; WPA3
(SAE) needs the passphrase itself, which wpa_supplicant keeps in its own
config. Nothing is logged; the field is emptied when the dialog closes. A
network that fails to join (wrong password) is removed again. Joining uses
`SELECT_NETWORK` and then `ENABLE_NETWORK all`, so the other saved networks
stay enabled. The lock PIN is only stored hashed.

## Files

| path | what |
| --- | --- |
| `duo-settings` | entry script |
| `duosettings/main.py` | the application, the window (split view, breakpoint), shared rows and dialogs |
| `duosettings/pages.py` | the pages |
| `duosettings/wifi.py` | wpa_supplicant: the socket, scan / network / status parsers, PSK (pure parts tested) |
| `duosettings/bluetooth.py` | BlueZ and the pairing agent |
| `duosettings/sim.py` | the SIM lock over ModemManager; what the page shows (`lock_view`, pure, tested); with root's `/etc/duo2/shell-fake-modem` the session bus, where `shell/duo-shell/tests/fake_mm.py` plays ModemManager |
| `duosettings/system.py` | readers, PipeWire, timedated, the PIN, duo-wc's socket (pure parts tested) |
| `duosettings/touchcal.py` | the calibration fit (pure, tested) |
| `duosettings/conf.py` | `key = value` files (pure, tested) |
| `duosettings/battery.py` | the battery usage report: names and icons (desktop files), merging, shares, texts, the graph's geometry (pure, tested) |
| `duosettings/batterypage.py` | the Battery page and its graph (cairo) |
| `duo-settings-helper` | the root actions |
| `duo-settings-qmi` | the modem identity reader the helper runs through systemd-run (QRTR) |
| `duosettings/about.py`, `aboutpage.py` | Settings > About (above) |
| `duo-panel-refresh.service` | the kept refresh rate, before duo-wc |
| `org.duo2os.Settings.desktop`, `.svg` | the launcher and its icon |
| `install.sh`, `install-files.sh` | install on the phone over the network / in the image (`rootfs/install-components.sh` runs `install-files.sh`) |
| `duosettings/ringtone.py`, `duosettings/ringtonegroup.py` | the ringtone: the choices, the feedbackd user theme, the conversion (pure, tested); the Sound page's group |
| `duosettings/dndprefs.py` | do not disturb's exceptions for calls (the file duo-shell's dndmodel.py reads) |
| `duosettings/voicemail.py`, `duo-voicemail`, `org.duo2os.Voicemail.desktop` | the mailbox number and the app grid's Voicemail |
| `duosettings/hotspot.py`, `duosettings/hotspotpage.py` | Settings > Hotspot (above; the daemon and helper: hotspot/) |
| `duosettings/appearance.py`, `duosettings/appearancepage.py`, `duo-appearance`, `systemd/duo-appearance.{service,timer}`, `tests/gtk_appearance_page.py` | Settings > Appearance (above) |
| `duosettings/android.py`, `duosettings/androidpage.py`, `tests/gtk_android_page.py` | Settings > Android apps (above; the readers of the switch: android/) |
| `tests/gtk_combo_rows.py` | every page's combo rows (`tests/run-gtk.sh tests/gtk_combo_rows.py`): each page at the widths Settings gets on the phone (one panel upright at the five scales, 448 to 672 px; on its side, 631 and 757; wide) in the phone's font (Cantarell 11) and GTK's default; no value of up to 24 characters cut short beside its title, no page wider than the panel. `main.keep_value_readable` gives every combo row a value label that keeps such a value whole (the title and subtitle wrap): beside a long subtitle libadwaita's own label shrank to "5…" (Hotspot's Band at 538 px) |
| `tests/gtk_hotspot_page.py` | the Hotspot page under GTK 4 + libadwaita (`tests/run-gtk.sh tests/gtk_hotspot_page.py`): a made-up state, its rows, Show and forget of the password, your own password (the dialog, Save, Enter, the helper's stdin), the helper's words; pictures |
| `tests/gtk_updates_page.py` | the Updates page under GTK 4 + libadwaita (`tests/run-gtk.sh tests/gtk_updates_page.py`) against a stand-in for duo2-updates: never checked, updates found, Install refused at low battery, installing, restart asked, the switches, the testing channel only in developer mode; pictures |
| `tests/` | `python3 -m unittest discover -s tests` (139) |
| `tests/run-gtk.sh`, `gtk.Dockerfile`, `gtk_battery_page.py` | the Battery page under GTK 4 + libadwaita (Debian trixie, broadway, in a container): built from a made-up report at 538 x 757, its rows, shares, merging, details and charger switch checked, pictures to `/tmp/duo-settings-gtk` |

## Verified (2026-11-07)

On the phone, scale 2.5, one panel, by screenshot: every page lays out
without clipped text; the WiFi page lists the network in use and the ones
around; Bluetooth discovers nearby devices; the time zone list opens with
its search field and the keyboard; the touch calibration ran end to end
(ten taps through evemu clones of the touchscreen, "Touch is accurate",
nothing applied). The helper's commands were run with the values in force
(no change) and refused a path as a time zone.

Not tried on the phone, on purpose: joining or forgetting a network (the
phone is reached over WiFi), pairing (no device at hand), refresh / scale /
calibration Apply and Reset (each restarts duo-wc, closing every app), the
lock switches (the owner chooses a PIN).

The SIM card page (2026-11-07, night): never with the owner's SIM (a wrong
PIN costs an attempt, three need the PUK). Tried against
`shell/duo-shell/tests/fake_mm.py` twice: `SimLock` on the phone, as the session user,
on the session bus (wrong PIN: 2 attempts left; lock on; change 1234 ->
5678; the old PIN refused; lock off with the new one; Change refused with
the lock off; three wrong PINs: "Waiting for its PUK", nothing switchable),
and the page itself in GTK 4 + libadwaita (Debian trixie, a broadway
display, the switch and the dialogs driven by code): the dialog, a wrong
PIN toast with the switch back off, Cancel, lock on, two different new PINs
refused, PIN changed, lock off.

The Battery page (2026-11-07, evening): on the phone with
`duo2-battery-stats` running since 20:56, **on the charger the whole time**
(nothing could be measured on battery): the page with the level graph, the
charger switch, the apps (Settings, Text Editor in front and Calls / Chats
in the background, each one row: the app's window key and its program
folded together) and the system parts, an app's and a part's details sheet,
by screenshot (`out/shell-shots/settings-battery-*.png`); taps through the
touch tool's evemu clone. The CPU loops of a measurement over ssh landed in
"Remote login (ssh)", the Text Editor's minute on screen in its own row.
The helper's `battery refresh` as the GUI user (sudo, no group) returned the
report; `battery clear` emptied the samples, periods, usage and groups and
kept the calibration; the sleep hook's `pre` took a sample in 0.27 s;
another agent's `rtcwake` suspends (which bypass the hook) came out as
suspended periods and standby time of the right length (31 s and 20 s for
the kernel's 34 s and 23 s s2idle, wake-up latency included); after a reboot
the service came back by itself with its store. Not tried: the page on
battery data (only the GTK test's made-up report).

## Not yet

- The quick menu's Settings button and the do-not-disturb check are in
  duo-shell (`quickmenu.py`, `notifications.py`) but reach the phone with
  the shell's next `install.sh`.
- No translations yet (gettext domain `duo-settings`); the session runs in
  the C locale.
- Mobile data settings (APN, roaming; the switch is the quick menu's), VPN, region and language, default apps, storage cleanup.
- Battery usage: not yet run on battery (the calibration has learned
  nothing; on this phone the power profile's CPU figures are about twice and
  its screen figures about three times what was measured on the charger,
  (measured), so until it learns, the
  CPU and the screen are overestimated in the shares; the total is the
  measured drain all the same). No figure for the GPU (assumed), no OLED
  content, no per-app network traffic (Wi-Fi and the modem stay parts of the
  phone), background time at a minute's resolution.

## Privacy: location (2026-11-07 night)

`duosettings/privacypage.py` (the page), `duosettings/privacy.py` (pure:
the store, the lists; `tests/test_privacy.py`). **Location** is the same
switch as the quick menu's tile (`~/.config/duo-shell/location.json`, the
shell follows the file: geoclue's agent caps every app at its level).
**Apps**:

- apps that declare location use (`X-Geoclue-Reason` in their `.desktop`
  file: Clocks, Weather), "Not sandboxed": geoclue never asks the agent about
  them; switched off, they are refused by geoclue itself
  (`duo-settings-helper geoclue-deny ID...` writes
  `/etc/geoclue/conf.d/91-duo2-apps.conf` with `allowed=false` sections and
  restarts a running geoclue: "'org.gnome.Weather' disallowed by
  configuration");
- apps the shell asked (sandboxed ones that talk to geoclue), from the
  store;
- Flatpaks that asked through xdg-desktop-portal: the portal's permission
  store (`location` / `location`), changed with `SetPermission`.

## Sound: sound mode and vibration (2026-11-07 night, the modes 2026-11-08)

`duosettings/vibration.py` (the rule in `duosettings/soundmode.py`, pure,
`tests/test_settings.py` checks it against duo-shell's `hapticsmodel.py`),
two groups on the Sound page:

- **Sound mode** (calls, messages and notifications; alarms ring in every
  mode): Sound and vibration / Sound only / Vibration only / Silent, one of
  four, the same as the quick menu's Sound tile. A mode is feedbackd's
  `profile` and `max-haptic-strength` (GSettings `org.sigxcpu.feedbackd`):
  `full` + the chosen strength, `full` + 0, `quiet` + the chosen strength,
  `silent` + 0 (duo-shell README "Sound and vibration" for what each
  profile plays).
- **Vibration** (on / off: the sound stays as it is, Silent + on = Vibration
  only) and **Strength** (Light 0.6, Medium 0.8, Strong 1.0). The strength
  is kept in `~/.config/duo-shell/vibration.json`, where the shell finds it
  when vibration comes back on; choosing one plays a taste of it (a
  notification's vibration).

The page follows a change made elsewhere (the quick menu) at once (the
GSettings `changed` signal). Picture: `out/shell-shots/settings-sound-modes.png`
(not in git; drawn in the trixie container with a memory GSettings backend).

## Ringtone (2026-11-09)

`duosettings/ringtone.py` (pure parts, `tests/test_ringtone.py`),
`duosettings/ringtonegroup.py` (the group on the Sound page, above the sound
mode). GNOME Calls rings with feedbackd's `phone-incoming-call`; another
ringtone is a feedbackd user theme, written to
`~/.config/feedbackd/themes/duo2-ring-a.json` (or `-b`: every change goes
under the other name, so the GSettings key `org.sigxcpu.feedbackd theme`
changes and feedbackd reloads):

```json
{"name": "duo2-ring-a", "parent-name": "$device",
 "profiles": [{"name": "full", "feedbacks": [
   {"event-name": "phone-incoming-call", "type": "Sound", "file-name": "/usr/share/sounds/freedesktop/stereo/bell.oga"}]}]}
```

`$device` is feedbackd's name for the device theme (the phone's
`microsoft,surface-duo2.json`, itself over `default`), so only the `full`
profile's sound changes: the vibrations and the sound mode stay. "Phone
ringing (default)" resets the key and removes our files. The list: each
installed sound theme's phone-incoming-call (XDG data dirs, `sounds/*/
index.theme`), other ring-like sounds of those themes (alarm clock,
ringback, bell, chime, message, attention), and **Use your own sound…**:
a file through the portal's file chooser, copied to
`~/.local/share/duo2/ringtones/own-ringtone.oga`, converted by ffmpeg (its
first 60 s, Ogg Vorbis) unless it is Ogg Vorbis or WAV already (what
libcanberra, feedbackd's player, reads). The play button plays the choice
once (`paplay`). Tried in GTK 4 + libadwaita (the trixie container, a
memory GSettings backend, a made-up sound tree): the rows, a choice writing
the theme and the key, the default removing them. On the phone (the owner,
2026-11-12): a chosen ringtone rings a real call, the default comes back.
Open: the file chooser of "Use your own sound…" is bigger than one panel
(it does not fit on one, and hangs over onto the other in span): duo-wc's
dialog sizing.

## Voicemail (2026-11-09)

`duosettings/voicemail.py` (pure, `tests/test_ringtone.py`), `duo-voicemail`,
`org.duo2os.Voicemail.desktop`. ModemManager knows no mailbox number and
GNOME Calls has no voicemail key (telephony/README.md "Ringtone, do not
disturb, voicemail"), so the SIM card page has **Voicemail number**
(`~/.config/duo-shell/voicemail.json`) and offers the network's documented
numbers by the SIM's network code, never by a provider's name (Telekom's
network 26201: 3311, and 4712 for some prepaid providers); the app grid's **Voicemail** runs
`duo-voicemail`, which has Calls dial it (`gnome-calls --dial`), or opens
this page when no number is set.

## eSIM (2026-11-07 night)

`duosettings/esimpage.py` (the page, after "SIM card"), `duosettings/esim.py`
(pure: the helper's answer, the activation code's shape, masking;
`tests/test_esim.py`). Everything goes through `sudo -n duo2-esim`
(telephony/README.md "eSIM"): the EID (by its last four digits; a tap shows
it whole), the profiles (name or nickname, operator, in use / off, the
ICCID's last four digits), a menu per profile (Use this profile / Switch
off, Rename, Delete: not for the one in use, asked here first and then in
the shell), and **Add a profile**: paste the activation code (the text
under the operator's QR code; there is no camera yet) and an optional
confirmation code. The helper asks the user in the shell before every
change; this page only says what to do and shows the result.
