# duo-apps

The apps duo2-os preinstalls, and what makes them behave on duo-wc: two
538 x 757 logical panels at scale 2.5, touch only, no GNOME session around
them. Part of milestone 4 of the compositor plan.

## What is installed (Debian trixie, no recommends)

| app | package | notes |
| --- | --- | --- |
| Clocks (alarms, stopwatch, timer, world clocks) | `gnome-clocks` | alarms: see below |
| Calculator | `gnome-calculator` | no on-screen keyboard over its keypad (below) |
| Calendar | `gnome-calendar` + `evolution-data-server` | reminders through `evolution-alarm-notify` |
| Weather | `gnome-weather` | metric units (the session runs in the C locale) |
| Files | `nautilus` | the dock's "Files"; also `portfolio-filemanager` (below) |
| Image Viewer | `loupe` | |
| Document Viewer | `papers` | |
| Music | `amberol` | |
| Video | `celluloid` (+ `mpv`, `ffmpeg`: the hardware decoding) | mpv; see "Video" |
| App installer | `gnome-software` + `-plugin-deb` (PackageKit) + `-plugin-flatpak`, `flatpak`, `packagekit` | see "App installer" |
| Web browser (phone UI) | `angelfish` (+ `qt6-wayland`, `libqt6sql6-sqlite`, `qml6-module-org-kde-desktop`, `plasma-integration`, `kde-style-breeze`) | see "Browsers" |

## Browsers

Chromium and Firefox ESR (in the image's base list, rootfs/Dockerfile) and
**Angelfish**, KDE's phone browser: QtWebEngine, i.e. the Chromium engine
(the owner wants a Chromium-based one; GNOME Web, WebKit, is not installed),
with a Kirigami phone UI (address bar at the bottom, tabs, no Plasma
needed). What it needs here, besides the package:

- `qt6-wayland`: native Wayland (`QT_QPA_PLATFORM=wayland`; there is no X
  server), with wp_fractional_scale: it draws at exactly 2.5.
- `libqt6sql6-sqlite`: its history, bookmarks and icons (without it every
  query fails: "QSQLITE driver not loaded").
- The phone layout and dark colours without Plasma: `qml6-module-org-kde-desktop`
  (Kirigami's desktop style; without it Qt falls back to Fusion, and
  Kirigami finds no platform plugin for it), `plasma-integration` (the
  `kde` platform theme reads the colours) and `kde-style-breeze` (Breeze
  and its colour schemes). `/etc/environment.d/60-duo2-qt.conf`
  (`QT_QPA_PLATFORMTHEME=kde`, `QT_QUICK_CONTROLS_STYLE=org.kde.desktop`,
  `QT_QUICK_CONTROLS_MOBILE=1`: Angelfish's mobile UI instead of the desktop
  one) is read by the user manager and exported by duo-wc's session;
  `/etc/xdg/kdeglobals` (written by install-files.sh from Breeze's own
  `BreezeDark.colors`) makes every Qt app dark by default (a user's
  `~/.config/kdeglobals` still wins).
- It needs none of the session's guarded Wayland globals (docs/hardware/security.md).

Also: `fonts-cantarell` (the GNOME UI font: the schema default, which was
missing, so everything fell back to DejaVu), `fonts-noto-color-emoji`,
`libgtk-4-media-gstreamer` (GTK4's media backend: without it Clocks' alarm is
silent), the GStreamer codecs (`gstreamer1.0-libav`, `-plugins-bad`,
`-pipewire`), `sound-theme-freedesktop` (the alarm sound), `appstream` +
`apt-config-icons(-hidpi)` (apt fetches the DEP-11 catalogue and icons that
GNOME Software lists Debian's apps from).

All of them are libadwaita apps: dark through the Settings portal, adaptive
at 538 px, drawn at exactly 2.5 (fractional scale), and they open on the
panel the app grid or the dock launched them from (duo-wc's
`expect_window` + the activation token).

## What this directory adds

| file | where | why |
| --- | --- | --- |
| `session-start` + `systemd/duo-apps-session.{path,service}` | `/usr/local/lib/duo-apps/`, `/etc/systemd/user/` (enabled globally) | runs whenever duo-wc's session (re)starts (it writes `/run/duo-wc/session.env`): restarts the portals, makes the XDG user directories, starts the alarm services |
| `systemd/xdg-desktop-portal-gtk.conf` | `xdg-desktop-portal-gtk.service.d/duo2.conf` | the GTK portal backend retries when it was asked before the display was up |
| `systemd/duo-calendar-alarms.service` | `/etc/systemd/user/` | calendar reminders |
| `dbus-services/org.gnome.clocks.service` | `/usr/local/share/dbus-1/services/` | Clocks always starts plainly (alarms) |
| `applications/org.gnome.Calculator.desktop.sed` | `/usr/local/share/applications/` | Calculator with GTK's simple input method |
| `91_duo2-os-apps.gschema.override` | `/usr/share/glib-2.0/schemas/` | GNOME Software and weather defaults |
| `software/50-duo2-packagekit.rules` | `/etc/polkit-1/rules.d/` | PackageKit for the desktop user |
| `software/deployment-featured.ini`, `works-on-duo.txt` -> `works-on-duo.xml` | `/etc/xdg/gnome-software/`, `/usr/share/swcatalog/xml/` | "Works well on the Duo" |
| `touch-apps.py` | (tool) | lists the apps an AppStream catalogue marks touch + small screen |

`install.sh` installs the packages and runs `install-files.sh` on the phone
over the network; the image runs the same `install-files.sh`
(`rootfs/install-components.sh`) after `rootfs/Dockerfile` installed the
same packages.

## Why each fix

- **Dark apps, and why they were light after a duo-wc restart.**
  xdg-desktop-portal asks its GTK backend for the Settings interface once,
  at its start. When duo-wc restarts, the backend (a GTK program) loses the
  display and dies; the next portal start then finds no backend, and serves
  no Settings for the rest of its life, so every libadwaita app came up
  light (seen 2026-11-07). `session-start` restarts both when the session
  starts.
- **Clocks' alarms.** Clocks 48 rings an alarm only while it runs, and has
  no background mode: started as a D-Bus service (Debian's service file adds
  `--gapplication-service`) it quits 10 s after its window closes, alarms or
  not (measured); started plainly it stays after its window closes. So our
  service file starts it plainly, and `session-start` starts it at session
  start when an alarm is set (an alarm that will ring has a `ring_time` in
  `org.gnome.clocks alarms`), closing its window as soon as duo-wc shows it.
  The sound is GTK's media file through GStreamer, PipeWire's "Clocks"
  stream at the speaker's volume. Verified: an alarm set a minute ahead rang
  with the window closed (at 12 % volume, stopped after 3 s).
- **Calculator.** Its display field has the focus at start, so the on-screen
  keyboard came up over its own keypad. With `GTK_IM_MODULE=
  gtk-im-context-simple` it speaks no text-input-v3; a plugged keyboard still
  types.
- **Weather units**: metric (`org.gnome.GWeather4`).

## Files: Nautilus, with Portfolio beside it

Compared on the phone (`out/shell-shots/apps-files*.png`,
`apps-portfolio*.png`): Portfolio (`dev.tchx84.Portfolio`, built for phones)
has the biggest rows and a places list made for a thumb, but little else;
Nautilus 48 is adaptive (path bar, bottom navigation at this width), shows
thumbnails, searches, has every file operation, and is what apps call for
"show in folder" (`org.freedesktop.FileManager1`). **Nautilus is the Files
app** (the dock pins it); Portfolio stays installed as the light one.

## Video: Celluloid, not Showtime or Clapper

Showtime and Clapper (GStreamer, `gtk4paintablesink`) dropped frames on a
1080p H.264 file at ~330-390 % CPU. Measured with GStreamer pipelines:
`avdec_h264 ! fakesink` decodes 20 s of video in 2.9 s; `avdec_h264 !
glupload` takes 37 s, because the decoder writes straight into the GL
buffer pool that `glupload` offers (direct rendering), and on freedreno
those buffers are write-combined GPU memory the decoder also has to READ
(reference frames). With `direct-rendering=false` the same pipeline takes
3.4 s. The players build their own pipelines, so there is no place to set
it. Celluloid (mpv) decodes into its own memory and uploads: ~80 % CPU,
smooth. Showtime and Clapper are not installed (and hidden if they are).

**Hardware decoding (2026-11-07, docs/hardware/video.md).** Celluloid
decodes H.264 and HEVC on the video core (the iris driver, `/dev/video0`):
the gschema override (`mpv-config-enable`, `mpv-config-file`) makes it load
the system mpv configuration, `/etc/mpv/mpv.conf` from the image overlay
(`rootfs/overlay/etc/mpv/`), the one mpv itself reads: `hwdec=v4l2m2m-copy`
for H.264 and HEVC, and `duo2-hwdec.lua`, which sends 10-bit and interlaced
files to software before a decoder opens (the driver refuses them). 1080p30
H.264 in Celluloid on a panel: ~30-45 % CPU instead of ~120-150 %; 4K30
H.264 ~70 %. VP9 stays in software here (FFmpeg's VP9 wrapper trips the
driver); GStreamer's `v4l2vp9dec` decodes it. Celluloid's preferences can
turn it off (Load mpv configuration file).

## App installer

- **GNOME Software 48**, PackageKit's apt backend (Debian's packages, from
  the DEP-11 catalogue) and Flatpak. **Flathub is a user remote**
  (`flatpak remote-add --user`): Flatpaks go into `~/.local/share/flatpak`
  and need no polkit at all. **Flatpaks are preferred** when an app is in
  both (`packaging-format-preference=['flatpak', 'deb']`, owner 2026-11-07):
  a Flatpak runs sandboxed, and every app runs as the same user as the
  shell, so the less an app can reach the better (docs/hardware/security.md); the Debian package stays one tap away (the source picker).
- **Authentication**: duo-wc's session has no logind session and no polkit
  agent, so PackageKit's default (an admin password for an active local
  session) can never be met. `50-duo2-packagekit.rules` lets the desktop
  user install and remove packages and refresh the lists, nothing else (no
  untrusted packages, no repositories or keys, no offline updates). Any
  program of the user can use this rule, not only GNOME Software (polkit
  cannot tell them apart): installing comes only from the configured,
  signed repositories, but REMOVING a package is open to every app too (a
  known gap, docs/hardware/security.md).
- **Updates are off in GNOME Software** (`allow-updates=false`): Debian's
  updates would go through PackageKit's offline update, a reboot into
  `system-update.target`, which this phone's boot path has not been tried
  with. Update with `sudo apt upgrade` (as duo2admin) and `flatpak update
  --user` (as the user: `duo2_user 'flatpak update --user -y'`).
- **"Works well on the Duo"**: GNOME Software 48's deployment-featured
  section on the Explore page (`/etc/xdg/gnome-software/
  deployment-featured.ini`: title and selector). `works-on-duo.txt` is the
  list (tried here, or declaring touch + a small screen), with a note per
  app; `software/gen-featured.py` turns it into AppStream merge components
  (both id forms, Debian's `.desktop` suffix and Flathub's without).
  Regenerate `works-on-duo.xml` after editing the list. The candidates come
  from `touch-apps.py`: Debian trixie arm64 has 79 apps declaring touch + a
  display of at most 420 px, Flathub's aarch64 catalogue 412.
- Verified: the Explore page at 538 px, the section, search, and GNOME
  Sudoku installed (Debian package, 16 s, the polkit rule granted it) and
  removed from its page by touch.

## Not done / known

- The session's locale is C: Calendar starts weeks on Sunday, apps are in
  English (no `locales` package in the image).
- Clocks' alarm plays at the speaker's volume (muted = silent), and the
  screen stays dark when it rings (duo-wc wakes only on the power key); the
  notification banner shows once the screen is on.
- GVFS warns that `$XDG_RUNTIME_DIR/gvfsd` (`/run/duo-wc/gvfsd`) is missing
  and falls back to the session bus: harmless.
- Weather's "current location" needs geoclue and an agent; search a city.
