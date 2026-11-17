# duo-shell

The duo2-os shell clients for [duo-wc](../../compositor/duo-wc/README.md): a
status bar on each panel, the home screen, an app grid, the quick menu, the
notification daemon and its banners, the dock, the titles and icons on
duo-wc's task overview cards, the volume keys with their display, the
force-close question and the lock screen client (`duo-lock`). Parts 4a,
4b and 4c of the compositor plan, and the shell's side of milestone 6
(the one canvas, the gestures, the look).
duo-wc does displays, input, windows, poses, edge gestures and the rules
(where the on-screen keyboard goes); everything here is an ordinary Wayland
client on standard protocols (wlr-layer-shell, text-input-v3 through GTK,
xdg-activation through GTK's launch context) plus duo-wc's control socket.

The on-screen keyboard is **phosh-osk-stub** (Debian's; Phosh's "Stevia":
word completion, long-press accents) since 2026-11-09, with **squeekboard**
(Debian's) as the fallback; neither is part of this code. Which one runs is
the session's (`compositor/duo-wc/dist/osk`); the
shell talks to whichever owns `sm.puri.OSK0`. This directory carries their
packages (`install.sh`) and defaults (`90_duo2-os.gschema.override`: the
layouts Hungarian + English, completion on).

## What it does

- **Status bar**, one per panel (GDK monitor = duo-wc output, matched by the
  connector name `DSI-1` / `DSI-2`), along the top edge, 48 logical px, on the
  layer-shell top layer with an exclusive zone of its height: duo-wc shrinks
  that panel's windows below it (and a spanning window below both bars).
  - left: the clock (`%H:%M`, the phone's local time zone; a tap opens
    GNOME Clocks on its Alarms page on this panel, below);
  - after the clock: the icons of the newest notifications' apps (up to 3);
  - right: WiFi, Bluetooth, battery (icon and per cent, with the charging
    icon while charging), the **keyboard** button (hides squeekboard if it was
    shown on this panel just before the tap, otherwise brings it up here: the
    tap no longer moves the keyboard by itself, so it asks duo-wc for
    `focus_output` first, `oskpolicy.py`; lit while squeekboard is on this
    panel, not while duo-wc keeps it away: put away by a tap beside the
    field, or squeekboard's blink at its start held back, `osk_away`), the
    **windows** button (duo-wc's task overview, on every
    lit panel; lit while open) and the **apps** button (opens / closes the
    app grid on this panel; lit while open). Buttons are 48 x 48 logical px.
  - **dragging down** anywhere on the bar (24 px), or tapping the status
    icons or the notification icons, opens the quick menu on that panel.
- **App grid**, one per panel, made when first opened: every installed app
  (`Gio.AppInfo.get_all()`, what `should_show()` allows, minus squeekboard and
  foot's server / client entries), a search field (every word must match the
  name, generic name, description, keywords or executable; accents and case
  ignored; names that start with the query first), tiles of 56 px icons. It
  covers its panel below the status bar (top layer, all edges, keyboard on
  demand). Opened by the apps button, the dock's apps button, or a **long
  swipe up from the panel's bottom edge** (duo-wc recognises it and announces
  `edge_swipe` with kind `long` on its event stream). Nothing is focused when it opens, so the keyboard comes up only
  when the search field is tapped; the field loses the focus when the grid
  closes (kept while hidden, it brought the keyboard up for a moment on the
  next open, 2026-11-09). Tapping an app (or Enter in the search
  field: the first match) tells duo-wc `expect_window <this panel>` and starts
  the app with GTK's launch context (an xdg-activation token made on this
  panel goes with it); the grid closes. **Starting Android apps**
  (`androidstartmodel.py` pure, `androidstart.py`; 2026-11-17): an Android
  app (`waydroid.<package>.desktop`, or Waydroid's own entry) launched from
  the grid, the home screen, the dock or a desk while the Waydroid session
  is not on the session bus (`id.waydro.Session`, watched) waits for
  Android to boot: its window came ~19 s after the tap, with nothing on the
  screen meanwhile, and the owner tapped again. Now a pill "Starting
  Android apps..." (spinner, overlay layer, above the dock, touches pass
  through) shows on that panel until the app's window has been in duo-wc's
  window list for 0.5 s (Waydroid's first toplevel of a start is gone 5 ms
  after it came), at most 30 s, and goes when the screen goes dark; and the
  hint asks duo-wc to wait 40 s (`timeout_ms`) instead of its 10 s, also
  for another Android app launched while the notice shows. Not tested on
  the phone yet. The close button and Escape close it.
  A window that comes to the front on its panel by itself (a new window,
  an app's activation: duo-wc's `window_front` event, 2026-11-12) closes the
  grid and the quick menu there: as layer surfaces they covered it (Text
  Editor opened under the open grid; `launchermodel.front_outputs`).
  **Grid or list** (2026-11-07, like Microsoft Launcher; `launchermodel.py`):
  the button left of the close button switches to an alphabetical list (the
  app's full name, a letter header over each section) with an **A-Z index**
  down the right edge (22 px in from the glass: duo-wc's edge strip would
  hold a touch there back as a possible back swipe): a tap or a scrub jumps
  to that letter's section (a missing letter: the next one), a big letter
  in an accent disc follows the finger; search filters both (the list hides
  its letters and the index while searching). The choice is
  `~/.config/duo-shell/launcher.conf` (`view = grid | list`), also in Duo
  Settings > Home & apps; read when the drawer opens. Names under the icons
  are the **short name** (the shorter of Name and X-GNOME-FullName, without
  a trailing GenericName: "Firefox ESR", "Chromium"; `apps.short_name`), on
  up to two lines.
- **Edge gestures** (duo-wc decides, `compositor/duo-wc` README "Edge
  swipes", the full map there): from the bottom a **short** swipe up shows
  the dock on that panel, a **long** one goes **home** (duo-wc sets the
  panel's windows aside; on a panel that shows home already it is the app
  grid), a swipe that **stops and holds** opens duo-wc's task overview (the
  dock comes with it); with the quick menu down, any swipe up closes it.
  From the **top**, a swipe down opens the quick menu (over a fullscreen app
  too). From a **side**, inwards: back; when a shell surface is up on that
  panel duo-wc leaves it to the shell (`to: shell`): the quick menu, the
  app grid or the force-close question closes, on a home panel the edit
  mode ends.
- **Home screen** (`home.py`; the layout `homemodel.py`, pure): what a panel
  shows with no window on it (duo-wc's `home`): a background-layer surface
  per panel under every window (covering the whole panel with exclusive
  zone -1, 2026-11-07: with 0 the on-screen keyboard's zone pushed the icons
  and the wallpaper up; now only the dock stands above the keyboard; the
  status bar's 48 px are kept free by hand), pages of app icons (Adw.Carousel: swipe
  sideways, dots below), the first page with the **widgets**: the clock and
  the date (`timefmt`; a tap on them opens GNOME Clocks on its Alarms page,
  below), the **weather** (GNOME Weather's first place, through
  libgweather, every 30 min while lit; no place: "Add a place", a tap opens
  Weather) and the **battery** (a tap opens Duo Settings on its Battery
  page: the desktop file's `battery` action, `homemodel.CARDS`). The dock stands at its bottom (dock mode
  `home`). Tap an icon: the app starts on this panel (`expect_window`, GTK's
  launch context). **Touch and hold** an icon (or the page): edit mode, the
  icons get a remove badge and can be carried (to another slot, to another
  page by holding them at the page's edge; past the last page a new one),
  "Done" or Back ends it. **Swipe up** on a page: the app grid. Apps come
  onto the home screen from the app grid (hold an app: "Add to home
  screen"). Layout: `~/.config/duo-shell/home.json` (`{"panels": {"DSI-1":
  [[desktop ids], ...]}}`); the first launch fills each panel from a short
  list of installed apps (`homemodel.DEFAULTS`). The first page holds 12
  icons (the widgets are above), the others 20. Labels: the short name on
  up to two lines (above), always 10 characters wide (a wrapping label is
  otherwise taller at its minimum width than at its natural one, and GTK
  warned "needs at least 707" at every layout). **On its side** (757 x 538
  logical, 2026-11-08): the first page puts the clock, the date and the two
  cards in a column beside its 4 x 3 icons, the others lay their 20 icons out
  7 x 3 (`homemodel.grid_shape`; the pages hold as many either way, a
  rotation only lays them out again, on the monitor's `notify::geometry`).
  Screenshots: `out/night/home-landscape{,-before}.png` (not in git). While the **Bing picture of the day** shows, a
  small "i" sits above the dock at the right: a card with the picture's
  title (not Bing's empty "Info"), what it is and whose (`current.json`'s
  copyright) and **Learn more**, which opens Bing's page in the default
  browser on this panel (`wallpaper.bing_caption`).
- **Quick menu** (`quickmenu.py`), one per panel: an overlay surface over the
  whole panel, keyboard none (the window's text field keeps the focus). Tiles,
  **three to a row** (2026-11-13; two took most of a portrait panel: 7 rows of
  68 px, now 5), blue = on; the title and its status line are centred on the
  icon, an empty status line takes no room, and a line too long for the
  ~164 px tile wraps to a second line (between words; inside a word only when
  one word does not fit), ellipsized only past that (`tests/gtk_quick_tiles.py`
  below): **WiFi** (the radio, rfkill; "Off (hotspot
  on)" while the hotspot holds the WiFi client off, and a tap then asks the
  hotspot for it back instead of switching the radio), **Mobile data**
  (below), **Hotspot** (hotspot/README.md: shares the mobile data over WiFi;
  "N devices", "No mobile data", "Starting…"; `duo2-hotspot on|off` with the
  shell's grant, `hotspot.py`; the state file `/run/duo2/hotspot.json`
  watched; when the hotspot cannot run beside the WiFi connection and the
  user allowed switching it off, the menu asks first, "An ssh session over
  WiFi drops."), **Bluetooth** (BlueZ `Powered`, as the user; a radio rfkill-blocked
  by airplane mode is freed through the helper first, BlueZ refuses to power
  it while blocked), **Airplane mode** (below), **Sound** (the sound mode,
  below: a tap is the next of the four), **Do not disturb** (below),
  **Flashlight** (2026-11-13, `torchmodel.py`: the rear camera's flash LED
  lit as a torch through the kernel's LED class, `/sys/class/leds/white:flash`,
  brightness 0 or `max_brightness` = the stock torch current, 2 x 200 mA;
  the video group may write that one file, `power/91-duo2-torch.rules`. The
  tile reads the LED every second while the menu shows, so a switch-off from
  elsewhere shows; "No access" without the rule, "No flashlight" without the
  LED. It stays lit with the panels dark: duo2-autosleep does not suspend
  while it is lit, a suspend that happens anyway, a shutdown and the
  empty-battery power-off switch it off, `power/duo2-torch-off`; the icons
  are the shell's own, `icons/hicolor/symbolic/apps/duo-torch{,-off}-symbolic.svg`,
  as Adwaita has no flashlight),
  **Panels** (both / left only / right only:
  duo-posed's `panels`, the user's choice over the sensors, through the
  helper; the pose changes at once), **Auto-rotate** (duo-posed's `auto_rotate` in
  `/etc/duo-posed.conf`, then duo-posed reads it again, SIGHUP; duo-posed
  has no control socket, its config is its interface; shown as "Sensors
  only log" while duo-posed's `enabled = off`, the default; `sudo
  duo2-sensors-mode on` switches the sensors on), **Keyboard**
  (squeekboard), **Hinge gap** (duo-wc), **Laptop mode** (2026-11-13:
  duo-wc's `set_laptop toggle`, one panel a desk, the other a keyboard and
  a touchpad, compositor/duo-wc README "Laptop mode"; "Needs both panels"
  and greyed while the pose is not dual, "Not available" with a duo-wc
  without it, `laptopmodel.tile`; the menu closes, the panels change under
  it; the icon is the shell's own `duo-laptop-symbolic`), **Windows** (duo-wc's overview).
  Then **brightness** of both panels (the DSI panels' DCS backlight,
  `/sys/class/backlight/ae94000.dsi.0` and `ae96000.dsi.0`, 0..2047, shown
  1..100 %; the slider is left out where no backlight exists); the pose switch
  is gone (2026-11-07: one canvas, the poses come from the sensors);
  **Settings** (Duo Settings, `shell/duo-settings`, on this panel; added by
  the apps agent), **Screen off**, **Restart** and **Power off** (the last
  two ask first, in a red bar with Cancel); and the **notifications**,
  newest first. Opened by a swipe down from the top edge, a drag down on
  the bar or a tap on its status icons; it slides down. A **long press** on
  a tile (GTK's long-press time, as in the app grid; also on a greyed tile)
  closes the menu and opens Duo Settings on that tile's page
  (`quickpages.py`: WiFi, Mobile data -> SIM card, Hotspot, Bluetooth,
  Location -> Privacy, Sound, Do not disturb -> Notifications, Auto-rotate
  and Panels -> Rotation & poses, Keyboard, Hinge gap -> Display, Laptop
  mode, Display -> External display; Airplane mode, Flashlight and Windows
  have none: a long press there is a tap), with `duo-settings PAGE` and the
  launch context of the Settings button. Closed by the arrow
  at the top, the handle at the bottom (both also by dragging up), a swipe
  up from the bottom edge, Back, or the screen going off; it slides up.
  **Without a touch** (tests, screenshots; 2026-11-13, `shellbus.py`): the
  shell owns `org.duo2os.Shell` on the **system** bus, and root may call
  `OpenQuickMenu(output)` (the swipe's own `Shell.open_quick`) and
  `CloseQuickMenu()` (every open one, as its arrow does):
  `sudo busctl --system call org.duo2os.Shell /org/duo2os/Shell
  org.duo2os.Shell OpenQuickMenu s DSI-2`, then `duoctl screenshot DSI-2
  /tmp/q.png`. Root only: every app is the GUI user on the session bus, so
  that bus cannot tell an app from the administrator; the system bus policy
  (`org.duo2os.Shell.conf` in `/etc/dbus-1/system.d/`) lets only the GUI user
  own the name and only root call it, and the shell checks the caller's uid
  again (`GetConnectionUnixUser`). Without the policy the name is not had and
  the shell logs it.
- **Notifications** (`notifications.py`, `notifystore.py`):
  `org.freedesktop.Notifications` on the session bus (spec 1.2: actions,
  body, persistence, icons; `ActionInvoked`, `ActivationToken`,
  `NotificationClosed`). Every notification goes into the list and stays
  until dismissed (swipe a card sideways, Clear all, the app's
  `CloseNotification`, an action), except `transient` ones. A **banner**
  (overlay surface along the top) shows the newest one on the panel of the
  keyboard focus (duo-wc's `keyboard_output`) for 5 s (or the app's
  `expire_timeout`; a critical one until dismissed); swipe it up or sideways
  to hide it, tap it for its default action. None while the screen is dark,
  except: an **urgent notification lights the screen** (critical urgency,
  which GNOME Clocks' alarms and timers have, or any from Clocks:
  `notifystore.wakes_screen`), then shows its banner; a closed phone shows
  the glance strip instead. **Do not disturb** (below): no banners except
  critical ones, the list keeps everything. `notify-send` works
  (libnotify-bin is installed).
- **Do not disturb** (2026-11-09, `dnd.py`, `dndmodel.py`): GNOME's switch
  (`org.gnome.desktop.notifications show-banners` = false), set by the
  quick menu's **Do not disturb** tile and Duo Settings' Notifications
  page; a crossed-out bell in the bar while it is on. Notifications: no
  banner, no sound or vibration (critical ones, an alarm or a timer, still
  do). Calls: Calls' ringer is silenced through feedbackd's per-app profile
  (`/org/sigxcpu/feedbackd/application/org-gnome-calls/` `profile =
  silent`; the sound mode stays as it was), applied when the switch changes
  and when the shell starts; a call that rings in lights nothing and counts
  as silenced (the keys need not silence it; it can be answered, and Calls
  lists it as missed). Exceptions (Duo Settings, `~/.config/duo-shell/
  dnd.json`): a caller who rang within the last 15 minutes (`repeat_callers`,
  on by default) and callers from the address book (`contacts`, off; Calls'
  DisplayName, empty for an unknown number) ring through: the shell plays
  feedbackd's `phone-incoming-call` itself (app id org.duo2os.Shell: the
  chosen ringtone, the sound mode) until the call stops ringing or a key
  silences it, and lights the screen. Calls' favourites are not on its bus,
  so there is no favourites rule. Alarms ring in every case (Clocks plays
  them itself). Callers' numbers stay in the shell's memory (the repeat
  rule), never written or logged.
- **Dock** (`dock.py`, `dockmodel.py`): an overlay surface along a panel's
  bottom with the windows button, the pinned apps, the running ones not
  pinned (a dot under every running app), and the apps button. **Peek**: a
  short swipe up shows it on that panel for 5 s, in every pose (span too:
  the standing span taskbar is gone, 2026-11-07, the owner); **with the
  overview**: on every panel the overview is on. **Never over a fullscreen
  window** (a video): duo-wc's state names those panels (`fullscreen`), a
  short swipe there shows nothing and a peeking dock goes when one comes to
  the front (duo-wc hides the dock there itself too; `dockmodel.wanted`,
  `may_peek`).
  Tap a running app: its frontmost window comes forward (duo-wc's
  `activate_window`); another app starts on this panel. Hold an app 0.6 s
  and let go: pin / unpin. The row is rebuilt only when what it shows
  changes (`dockmodel.signature`): a row rebuilt while a finger goes down
  has no buttons in place yet (GTK lays them out at the next frame), so
  the press landed on the row. That was **the lost first tap**: touching
  the dock made duo-wc raise the window under it, the window list
  changed, and the dock rebuilt itself under the finger. Both sides are
  fixed (duo-wc no longer raises a window for a touch on a layer surface). The pinned list is
  `~/.config/duo-shell/dock.json` (default: foot, Text Editor, Files,
  Firefox: those installed). A window's app id is matched to its desktop
  file by id, StartupWMClass or executable. **On a desk** (an external
  display, laptop mode's desk panel: `dockmodel.is_desk`) there is no
  dock: the desk has its own taskbar (below); **laptop mode's keyboard
  panel** has no dock at all (no peek either).
- **A desk** (`deskmodel.py` pure, `taskbar.py`, `deskmenu.py`, home.py's
  desk mode; 2026-11-13, the owner: "a desktop, not the phone's home
  screen", for laptop mode and a TV alike). An external display and laptop
  mode's desk panel (`dockmodel.is_desk`, one rule for both) show a
  computer's desktop, not the phone's: the **wallpaper** only (the home
  surface keeps its picture and hides its page, icons and widgets; a right
  click or a long press on it: Settings, Wallpaper, Leave laptop mode on
  laptop mode's desk), **no status bar** (unmapped, its zone gone), and a
  **taskbar** along the bottom (`duo-taskbar`, top layer, an exclusive
  zone of 48 px: the floating windows stay above it; not under a
  fullscreen window, not while locked): the **app menu** button, the
  pinned apps then this desk's other windows (a dot under a running one,
  the front window's app lit; a click starts an app with no window there,
  brings its window to the front, and sets it aside when it is in front
  already: duo-wc's `minimize_window`, `deskmodel.tap_action`; a right
  click or a long press pins or unpins), and the **tray** (WiFi, battery,
  the time and the date; a click opens the quick menu on that desk). The
  **app menu** (`duo-deskmenu`, overlay, keyboard on demand) is a card at
  the bottom left over a see-through surface (a click beside it closes
  it): a search field that has the keyboard at once (laptop mode's keys
  type into it), every app (`apps.installed_apps`, ordered by
  `deskmodel.menu_apps`: names that start with the query first), Enter
  starts the first, Down goes into the list, Escape closes; Settings and
  Leave laptop mode below it. A long swipe up on a desk opens it too (not
  the phone's app grid). When the output stops being a desk (laptop mode
  off, the display gone), its home screen, bar and dock come back as they
  were (nothing of the home layout is touched).
- **A display: desk or mirror** (`displaymodel.py` pure, `displayprompt.py`;
  2026-11-13, compositor/duo-wc README "A display as a desk or a mirror").
  duo-wc uses a display plugged in as a desk or as a mirror of a panel; the
  state's `displays` says which. A mirror is no desk (`dockmodel.is_desk`:
  no taskbar, no dock there). While duo-wc asks (`asking`; it mirrors
  meanwhile), the shell shows a card at the bottom of the panel in use (never
  laptop mode's keyboard panel): "A display is connected", **Desktop** /
  **Mirror**, and "Remember my choice" (`set_display`, plus `set_external
  on_connect` when remembered); it goes when answered anywhere or unplugged.
  The quick menu has a **Display** tile while one is connected (Desktop /
  Mirror, a tap switches).
- **Laptop mode's key labels** (`laptopkeys.py`, `laptopmodel.py`;
  2026-11-13): duo-wc's laptop mode makes one panel a desk and the other a
  keyboard with a touchpad (compositor/duo-wc README "Laptop mode"). duo-wc
  draws the key caps and the touchpad and reads every touch there, but has
  no fonts: the shell draws each key's label on a see-through overlay
  surface over the keyboard panel (namespace `duo-laptop-keys`, the only
  surface duo-wc shows there; input region empty), from duo-wc's state
  (`laptop.layout`: each cap in the panel's own frame and what it shows in
  the layout in force, read by duo-wc from the keymap: its label, its
  shifted label, its AltGr symbol). Letters (the accented ones too) in
  capitals with Shift or Caps Lock, a digit's or a sign's shifted symbol
  big with Shift and the other small in the corner, the AltGr symbol small
  at the bottom right in blue (big with AltGr in force), words (Esc, Shift,
  Enter) smaller than signs (arrows, the delete key; a two-row Enter keeps
  a one-row key's size), the layout key (`HU`) a little blue, Cantarell
  Bold, sized to the cap; while the list of layouts is up (`laptop.menu`,
  the layout key held) its entries' names in their own language
  (`keyboardmodel.NAMES`; one not configured with a "+") and no labels
  under it; redrawn only when what it shows changes
  (`laptopmodel.signature`: the layout, Shift, Caps Lock, AltGr, the list).
- **The keyboard layouts** (`keyboardmodel.py`, 2026-11-13): the system's
  list, `org.gnome.desktop.input-sources` `sources` (the on-screen
  keyboard's; Duo Settings > Keyboard edits it), goes to duo-wc
  (`set_keyboard layouts=hu,us`) when the shell connects and at every
  change, for a plugged keyboard and laptop mode's keyboard (an `xkb`
  source is its layout, `de+nodeadkeys` with the variant; phosh-osk-stub's
  `('ibus','presage:en')` types on the US layout, so it is `us`; at most
  four). A layout picked on laptop mode's keyboard that is not in the list
  (duo-wc's `keyboard_layout_added`) is added to the system's list.
- **Mobile network** (`mobile.py` live, `mobilemodel.py` pure, `simpin.py`;
  2026-11-07, telephony/README.md): ModemManager on the system bus (its GI).
  A modem `locked` with `sim-pin` / `sim-puk` gets the **SIM PIN dialog**:
  an overlay over the whole active panel (`duo-sim-pin`; the dock steps
  aside), a numeric keypad (a plugged keyboard's digits too), the attempts
  left (`UnlockRetries`; the last one warned), after three wrong PINs the
  PUK and a new PIN twice; `Sim.SendPin` / `SendPuk`; the digits are never
  logged, stored or shown. It comes after the session's lock screen (duo-wc
  draws nothing of the shell over a lock), again at every screen-on until
  the SIM is unlocked; "Not now" or Back hides it until then, the bar's SIM
  icon (or the quick menu's data tile) brings it back. The **status bar**
  shows the signal bars and, after the clock, the operator (the SIM's name,
  else the network's) when a SIM is present; a locked-SIM icon while it
  waits. **Emergency call** (2026-11-09) on the dialog turns its keypad into
  an emergency dialler (`emergencymodel.py`: only emergency numbers, 112 and
  the country's own as one-tap buttons, the OK key becomes the call key);
  the shell places the call (ModemManager, below "Lock screen") and the
  dialog steps aside for Calls' call window. The quick menu's **Mobile
  data** tile writes
  `/var/lib/duo2/mobile-data` (`duo-shell-helper mobile-data on|off`), which
  duo2-wwan watches. **Airplane mode** (2026-11-07): the tile runs
  `duo-shell-helper airplane on|off`: on notes which of WiFi and Bluetooth are
  on (`/var/lib/duo2/airplane-radios`; Bluetooth counts only when BlueZ has it
  powered, since BlueZ powers an adapter up whenever its block goes), blocks
  both (rfkill; systemd-rfkill keeps the blocks over a reboot) and writes
  `/var/lib/duo2/airplane` = `on`, which duo2-wwan follows (the modem
  disabled, low power, RF off); off frees the noted radios (Bluetooth then
  powered through BlueZ) and duo2-wwan enables the modem. WiFi and Bluetooth
  can be switched on one by one in airplane mode, as on phones; the mode
  stays on until the tile switches it off, also over a reboot. The bar shows
  the airplane instead of the signal bars (with or without a SIM), the
  Mobile data tile says "Airplane mode" and cannot be switched. Testing without a SIM: `tests/fake_mm.py [pin|puk|
  registered]` plays ModemManager on the session bus, which the shell uses
  while root's `/etc/duo2/shell-fake-modem` exists (an app cannot switch it
  on; remove it afterwards).
- **Questions from duo-wc** (`confirm.py`, 2026-11-07): duo-wc's
  `confirm_request` (Duo Settings asks to restart the display; polkit asks
  before PackageKit removes packages, naming them) becomes the same sheet at
  the bottom of the active panel; Cancel / Restart (Remove) goes back as
  `confirm_answer` on the shell's trusted connection; nothing else can
  answer (docs/hardware/security.md).
- **Force close** (`confirm.py`): a window asked to close that does not
  answer gets a red "!" on its overview card (duo-wc); tapping it raises a
  question at the bottom of the panel ("Force close <app>? It does not
  answer. Anything it has not saved is lost.", Cancel / Force close) and
  then duo-wc's `force_close_window`. A card whose window was asked to close
  says "Closing..." until it goes, "Not responding" when it does not answer.
- **Overview card labels** (`overviewlabels.py`, `overviewmodel.py`):
  duo-wc draws its task overview (live pictures of the windows) but no
  text; it keeps a band above each card and publishes the bands on its
  event stream (`overview_cards`). The shell puts the app's icon and the
  window's title there (the app's name for a window without a title,
  ellipsized to the card's width; none for a card being carried) on one
  see-through overlay surface per panel (`duo-overview-labels`, the whole
  panel, exclusive zone -1) whose **input region is empty**, so every
  touch goes through to the overview (duo-wc hit-tests layer surfaces by
  their input region). The labels fade in (220 ms) while the cards fly to
  their places. Needs `python3-gi-cairo` (`Gdk.Surface.set_input_region`).
- **The pen's top button and battery** (2026-11-16, `penmodel.py` pure,
  `pen.py`): the Surface pen's top button comes over Bluetooth (paired in Duo
  Settings > Pen); duo-wc takes its keys and announces each press
  (`pen_button`: `click`, `double_click`, `hold`, the panel it is for, null
  while dark, `locked`). The shell does what `~/.config/duo-shell/pen.conf`
  says (Duo Settings writes it; read at every press): `notes` opens the note
  app on that panel (Rnote, else Xournal++, else Text Editor; a running one
  is brought forward with `activate_window`), `screenshot` asks duo-wc for
  that panel's picture (its `screenshot` request on the session's trusted
  connection, in a thread: duo-wc answers when the PNG is written) into
  `~/Pictures/Screenshots/Screenshot from <date>.png` with a notification
  that opens it, `quick_menu` opens the quick menu there, `off` nothing.
  Defaults: notes / screenshot / quick menu. Nothing while the screen is
  dark or locked. **Battery**: the paired Surface pens and their
  `org.bluez.Battery1` (`dbus.Bluetooth`, also on Battery1's
  PropertiesChanged); at or below 10 % "Pen battery low" once, again only
  after the pen was seen at 20 % or more; what was said is kept in
  `~/.cache/duo-shell/pen-battery` (hashed keys, no addresses), so a
  restarted shell does not say it twice. `tests/test_penmodel.py`.
- **Volume keys** (`volume.py` pure, `volumeosd.py`): duo-wc never gives the
  keys to an app; it announces them on its event stream (`volume_key`, press
  and release, the panel to show it on). The shell changes the default
  sink's volume with `wpctl` (PipeWire's software volume; the speakers'
  hardware gains are fixed by the UCM profile, `docs/hardware/audio.md` step 8): 5 %
  steps on the 5 % grid, never above 100 %, down at the bottom mutes, up
  unmutes; held, one step at once, then every 100 ms after 400 ms (at most 60
  per press). The volume moves in the shell's own copy at once (the display
  follows the finger) and `wpctl` runs in the background, one call at a time;
  the copy is read back on a press more than 2 s after the last. **Display**:
  a pill (icon, bar, per cent) on the overlay layer below the status bar of
  the active panel, gone 1.5 s after the last change; its input region is
  empty (touches go through). **While the screen is dark** duo-wc names no
  panel: the volume changes, nothing shows and nothing wakes (as on a phone).
  The keys then act on whatever the default sink is (a Bluetooth headset once
  one is connected). **While a call rings aloud** a press silences it and
  the volume stays as it was (below, "Incoming calls").
- **Dark apps**: `xdg-desktop-portal` + `xdg-desktop-portal-gtk`
  (`portals.conf`: the GTK backend), D-Bus activated on the session bus; its
  Settings portal gives libadwaita apps `color-scheme` 1 (prefer dark) from
  GSettings, so Text Editor & co. are dark.
- **Lock screen** (`duo-lock`, `lockclient.py`, `lockpad.py`): a swipe
  lock without a PIN; folding the phone closed locks it once the sensors
  are on. See "Lock screen" below.
- **Peek and glance strips** (`ambient.py`, `ambientmodel.py`): while
  duo-wc shows an ambient screen (its state's `ambient`: the phone opened
  just a little, or the power key of a closed phone) each lit panel gets a
  black overlay surface (`duo-ambient`, no input; made when the panels
  appear and kept mapped, duo-wc draws it only during an ambient screen:
  showing a strip is a redraw of one drawing area, done first when duo-wc's
  state changes, the bars' screen-on work waiting for a real screen, icons
  cached; duo-wc shows black until it is there; on screen ~240-310 ms after
  the posture, it took ~10 s) with one strip: **peek**
  on the panel's OUTER edge (opposite the hinge: looking into the gap of a
  slightly opened phone): one rotated line with the clock, a drawn battery
  with the percentage INSIDE it and up to four notification icons
  (`peek_notifications = icons | count | off`); **glance** next to the HINGE (what shows from
  outside on a closed phone): a thin line with the clock, the percentage, a
  small drawn battery and a "• N" count of the notifications (13 px is too
  thin for icons: `glance_notifications = count` by default, `icons` or `off`
  possible). Nothing is added without notifications. Both lines carry
  the weekday and the full date after the clock (2026-11-08), in the
  session's LC_TIME language and its own word order (`timefmt.full_dates`:
  "Sunday 8 November 2026" in en_GB, "Sonntag, 8. November 2026" in
  de_DE); where the line would not fit along the panel, the year goes, then
  the names are abbreviated, then the time stands alone. **While a call
  rings in** (2026-11-08) both strips say who calls instead: a drawn
  handset, the caller (the contact's name, else the number: the lock
  screen's card's words) and the time; the time goes first when the line
  does not fit, then a name too long on its own is cut short with "…"
  (`ambientmodel.call_line`, the same at every rotation: the strip runs
  along the panel's long side). Answered, declined or missed, the strip is
  the clock and the date again. Everything else is black (AMOLED: unlit). The strips follow a turned panel. Widths and
  panels in `~/.config/duo-shell/ambient.conf` (`peek_width = 72`,
  `glance_width = 44`, `peek_panels` / `glance_panels = both | DSI-1 |
  DSI-2`): guesses until the owner has looked at the real phone.
- **A restart of duo-wc keeps the lock.** duo-wc remembers a lock in
  systemd's descriptor store of its unit (since 2026-11-07; before, a file in
  `/run/user/UID` that any app could delete); started with it, it comes up
  locked and black, and the shell's first state (`locked`) starts `duo-lock`
  (`restore_lock`), which takes the session over. `duoctl unlock` (as the
  administrator) still ends it.
- **Trust** (`duoshell/trust.py`, docs/hardware/security.md): the
  session starts the shell with `duoctl exec-trusted --wayland
  --keep-trust`: a trusted Wayland connection (`WAYLAND_SOCKET`: the layer
  shell is for trusted clients only) and duo-wc's trust factory
  (`DUO_WC_TRUST_FD`). The shell first makes itself non-dumpable (no other
  process of the user may attach to it), makes the factory close-on-exec
  and drops it from the environment (the apps it starts never get it), and
  runs with `python3 -sE` (no user site-packages, which any app could
  write). Its control connections come from the factory (every request
  allowed); `duo-lock` is started with a trusted Wayland connection of its
  own (only trusted clients see ext-session-lock). Started by hand (no
  factory), the shell falls back to the user's socket: the state works, the
  privileged requests are refused.
- **Root actions** go through `duo-shell-helper` (WiFi and Bluetooth radios,
  airplane mode, mobile data, brightness, auto-rotate, panels, restart, power
  off), run with `sudo -n`; `install.sh` puts a
  sudoers rule for exactly that file (`/etc/sudoers.d/duo-shell`). It checks
  every argument against a fixed list.
- **Power**: the shell reads duo-wc's event stream; while no panel is lit
  (idle blank, power key) the clock, battery and WiFi timers stop, and the
  quick menu, peeking dock and banners go away.
- **Grants for the helper** (2026-11-07 night, `system.grant_for`): the
  quick menu's actions that matter (WiFi, Bluetooth, mobile data, airplane
  mode, the touch driver's restart, restart, power off) ask duo-wc for a
  one-time grant on the trusted connection and give the token to
  `duo-shell-helper` on its standard input; the helper redeems it (root,
  admin socket) for exactly those words. Another app running the helper
  through sudo has no grant: the user is asked first ("Allow this? An app
  asks to switch WiFi off.", duo-wc's confirm `helper`). Before, any app
  could switch off mobile data or reboot the phone.
- **Locking does not wait for the shell**: duo-wc locks by the lock
  screen's rules itself on a fold and a blank (`lock_by_policy`), black
  until duo-lock takes the lock over; the shell starts duo-lock when it sees
  `locked` (it also still locks itself). Killing the shell no longer leaves
  a second in which a fold does not lock.
- **Android (Waydroid)** (2026-11-07 night): **the clipboard** is shared
  by `duo-clipboard-bridge`, which the shell starts (when Waydroid is
  installed) with a trusted Wayland connection of its own, the one client
  of duo-wc's guarded `ext_data_control_manager_v1`, and started again when
  it exits (it dies with the shell, `PR_SET_PDEATHSIG`). It is Waydroid's
  host side of `lineageos.waydroid.IClipboard` (`waydroidclipboard` in
  Android's service manager; Waydroid's own needs `pyclip`, which Debian
  lacks): Android's copy sets the host selection, Android's paste reads it
  (text only, at most 1 MiB). The binder devices are open to every host
  process, so a call is answered only when its sender is in the container's
  cgroup (`/lxc.payload.waydroid*`, `clipmodel.py`). It registers whenever
  the container is up (it checks every 3 s). **Notifications** of Android
  apps come by themselves: Waydroid 1.6's notification manager posts them
  to the shell's `org.freedesktop.Notifications` (with `desktop-entry`
  `waydroid.<package>`, actions back to Android). **Names**: an Android app
  whose name a host app has too is shown as "Name (Android)" (grid, dock,
  home, overview; `apps.label`); "Contacts" is the one now.
- **Sound and vibration** (2026-11-08, `haptics.py`, `hapticsmodel.py`;
  the Vibration tile of 2026-11-07 became the Sound tile): four **sound
  modes**, shown and switched the same way in the quick menu's **Sound** tile
  (a tap goes to the next: Sound and vibration, Sound only, Vibration only,
  Silent, round again; its second line names the mode, blue unless silent)
  and in Duo Settings' Sound page. A mode is two feedbackd settings
  (GSettings `org.sigxcpu.feedbackd`):

  | mode | `profile` | `max-haptic-strength` |
  | --- | --- | --- |
  | Sound and vibration | `full` | the strength chosen in Duo Settings |
  | Sound only | `full` | 0 |
  | Vibration only | `quiet` | the strength chosen |
  | Silent | `silent` | 0 |

  feedbackd plays, for an event, what each profile from the current one
  down to `silent` has: `full` is the default theme's sounds (the ringtone
  `phone-incoming-call`, the message sound) plus the phone's vibrations
  (its theme's `quiet`), `quiet` the vibrations, `silent` only LEDs (none on
  the Duo); a strength of 0 scales every vibration to nothing. The phone's
  theme gives the keyboard's keys no sound in `full` (they only vibrate).
  Alarms ring in every mode (GNOME Clocks plays them itself). The ringtone
  itself is chosen in Duo Settings > Sound (a feedbackd user theme,
  telephony/README.md "Ringtone, do not disturb, voicemail"). The strength
  chosen stays in `~/.config/duo-shell/vibration.json`; Duo Settings'
  Vibration switch keeps the sound as it is (Silent + vibration on =
  Vibration only). A switch into a vibrating mode gives a short taste. The shell is the notification
  daemon, so it asks feedbackd for a vibration per new notification
  (`TriggerFeedback`): `im.received` a message, `email.arrived` a mail, a
  critical one the alarm, others the generic one; nothing for an update, a
  low-urgency one, `suppress-sound`, an incoming call (Calls rings
  itself) or, apart from critical ones, while do-not-disturb is on.
  Squeekboard's keys vibrate through feedbackd by themselves
  (`button-pressed`). The effects are the phone's feedbackd theme
  (`rootfs/overlay/usr/share/feedbackd/themes/microsoft,surface-duo2.json`).
- **Location** (2026-11-07 night, `location.py`, `locationmodel.py`): the
  shell is geoclue's agent (`org.duo2os.Shell`, whitelisted in
  `/etc/geoclue/conf.d/90-duo2.conf`), registered whenever geoclue appears
  (it is D-Bus activated and stops when idle). The quick menu's **Location**
  tile is its `MaxAccuracyLevel` (on: exact, off: none, geoclue refuses
  everyone: "Geolocation disabled for UID 1000", and its sources stop, the
  modem's GNSS engine too). geoclue asks the agent only about sandboxed apps
  (Flatpaks talking to it directly): those get "Allow <app> to use your
  location?" on the panel in use (`ConfirmSheet.ask_question`, 20 s without
  an answer = no, not remembered), or the answer xdg-desktop-portal's
  permission store has. Apps from Debian packages are never asked by geoclue
  (their desktop id is their own claim); Duo Settings' Privacy page refuses
  one by its id in geoclue's config. The switch and the answers:
  `~/.config/duo-shell/location.json`.
  **In use** (2026-11-09): while geoclue has a running client (geoclue's
  `Manager.InUse`, read without starting geoclue: any app, the Android apps'
  bridge `duo2-android-location`, the location test) the status bar shows
  the location icon (`location-services-active-symbolic`, "Location in use")
  and the tile says "In use". docs/hardware/location.md.
- **Incoming calls** (2026-11-08, `calls.py`, `callsmodel.py`): GNOME Calls
  rings (feedbackd) and shows its call window by itself, but sends no
  notification for a call that rings in and cannot light the screen, and a
  locked session shows nothing but the lock. The shell watches Calls' own
  call objects on the session bus (`org.gnome.Calls`, the object manager at
  `/org/gnome/Calls`, `org.gnome.Calls.Call`: what Phosh's lock screen reads;
  nothing polled): a call that starts ringing in (`Inbound`, `State` 5,
  Calls' `ui-active` hint) **lights the panels** (`set_power all on`, as an
  alarm does; a closed phone shows the glance strip), also right after the
  call woke the phone from suspend (duo2-autosleep does not suspend while a
  call rings). Unlocked, Calls' window is in front; **locked**, duo-lock
  shows a **call card** (the caller: the contact's name, else the number;
  Decline / Answer; once up, its time, Speaker / Hang up). Answer on a swipe
  lock also unlocks (Calls' window is the call screen then); a PIN lock stays
  locked, its card has "Enter PIN" (the keypad, with the call on a green line
  above it that brings the card back). The card goes to duo-lock over a
  socketpair the shell makes when it starts the lock
  (`DUO_LOCK_CALL_FD`, one JSON line per change), its buttons come back the
  same way and the shell calls Calls' `Accept` / `Hangup` and
  duo2-callaudio's `EnableSpeaker`, for a call Calls has. The caller's
  name and number are never logged. An SMS lights nothing (a notification of
  normal urgency).
  A closed phone's **glance** (and the **peek** of one opened a little)
  shows who calls while it rings (above, "Peek and glance strips"), and the
  glance stays lit for the whole ring (duo-wc holds it while a call rings,
  then gives it its usual 5 s with the clock).
  **Silencing** (2026-11-08): the **power key** or a **volume key** while a
  call rings aloud silences it, as on any phone: the call goes on ringing
  silently (the card, Calls' window and the strips stay) and can still be
  answered; it is NOT declined. The shell calls Calls' own
  `org.gnome.Calls.Call.Silence`, whose ringer then ends its feedbackd event
  (`phone-incoming-call`: the ringtone and the vibration together). duo-wc
  owns the power key, so the shell tells it how the call rings
  (`set_ring loud|silent|off`, renewed every 10 s, running out by itself
  30 s after the last renewal): while `loud`, the key's first press only
  silences (`ring_silence` on the event stream; on a dark screen it also
  comes on as it would: the lock screen's card, or a closed phone's glance)
  and does not blank; the next press does what it always does. A volume
  key's press silences instead of changing the volume (its release, too,
  is the ring's). Beside a call that is up (a call waiting: Calls only buzzes
  briefly) the keys keep their call roles. While a call rings at all, the
  screen does not blank by itself (duo-wc's idle timer counts again from the
  ring's end). Calls does not say which calls are silenced, so the shell
  remembers the ones it silenced (`callsmodel.py`: `ring_state`, `loud`,
  `RingKeys`).
- **Proximity during calls** (2026-11-07 night, `proximity.py`,
  `proximitymodel.py`): while a call is dialed, rings out or is up (not
  while it rings in) and is heard on the earpiece (callaudiod's
  `SpeakerState` 0, duo2-callaudio), the shell claims iio-sensor-proxy's
  proximity sensor; "near" darkens the panel with the Calls window (both when
  it spans; every lit panel when no Calls window is on one) through duo-wc's
  `set_proximity` (touches there are not delivered), "far" (after 250 ms),
  the speaker or the end of the call brings it back. Outside calls the sensor
  is released (it costs the sensor DSP power). A held panel is not "the
  screen off": no lock, the timers keep running. The hold is renewed every
  10 s and runs out in duo-wc after 30 s without a renewal; a new shell clears
  any hold at start. Testing without a call: `tests/fake_call.py` (below).
- **Keeping the screen on** (2026-11-09 night, `screensaver.py`,
  `screensavermodel.py`): the shell serves **org.freedesktop.ScreenSaver** on
  the session bus, at `/org/freedesktop/ScreenSaver` (Chromium, the portal's
  GTK backend) and `/ScreenSaver` (Firefox, VLC): `Inhibit(app, reason)` gives
  a cookie, `UnInhibit(cookie)` lets go (only the connection that took it),
  and an app that leaves the bus lets go of all of its own; at most 16 per
  connection, 64 in all. duo-wc is told the holders (application name and
  the caller's process id, from the bus) as its idle hold (`set_idle_hold`,
  renewed every 10 s while there are any, 30 s ttl; a new shell clears any
  hold at start and sends its own again when duo-wc comes back): a holder
  whose process has windows keeps the screen on while one of those is seen,
  one without (the portal backend, which speaks for the app) while any app
  window is; never a dark, locked or home screen (compositor/duo-wc README,
  "Idle inhibit"). `GetActive` / `GetActiveTime` / `ActiveChanged` follow
  duo-wc's state (blanked or locked); `SetActive` is refused (false).
  Who reaches it: **Chromium** (no org.gnome.SessionManager here, so it asks
  the freedesktop service; it also makes a Wayland inhibitor on its focused
  window), **Firefox** (its first choice; the Wayland protocol is its last),
  and **GTK4 apps** (Celluloid, Showtime: `gtk_application_inhibit`) through
  the Inhibit portal, whose GTK backend (xdg-desktop-portal-gtk) falls back
  to this interface when there is no GNOME session manager. GTK only asks
  the portal if the portal already runs when the app starts (it reads the
  interface's version without starting it), so the shell starts the portal
  frontend (`StartServiceByName`) as soon as it owns the name. mpv needs
  none of this: it makes a Wayland inhibitor itself. No org.gnome.SessionManager
  is served on purpose: GNOME components that see one take it for a GNOME
  session (they register clients, wait for its presence API).

### Where the numbers come from

| item | source |
| --- | --- |
| battery | `/sys/class/power_supply/<type Battery>/{capacity,status}` (the Duo 2: `qcom-battmgr-bat`), every 30 s |
| WiFi | wpa_supplicant's control socket `/run/wpa_supplicant/wlan0` (group `netdev`): `STATUS` (state, SSID) and `SIGNAL_POLL` (RSSI), every 10 s; the image has no NetworkManager and no `/proc/net/wireless`. Bars: >= -55 dBm 4, -65 3, -75 2, -85 1 |
| Bluetooth | BlueZ on the system bus: the first `org.bluez.Adapter1`'s `Powered`, followed by signals. No adapter registered (as now: `hci0` exists, BlueZ has no object for it) shows the "hardware disabled" icon |
| keyboard | the on-screen keyboard's `sm.puri.OSK0` on the session bus (phosh-osk-stub or squeekboard): the `Visible` property, `SetVisible(b)` |
| screen lit | duo-wc's event stream (`subscribe`) |

## Toolkit: GTK4 + gtk4-layer-shell through PyGObject

Chosen for tonight's constraints, and it stays a good fit:

- **Reproducible without a build**: everything is in Debian trixie
  (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-gtk4layershell-1.0`), the repo holds
  plain Python, `install.sh` copies it. No cross toolchain for GTK (the
  duo-wc Docker image would need the whole arm64 GTK4 `-dev` tree), no
  compile on the phone.
- **Same toolkit as the apps**: GTK4, Adwaita icons and theme, so the shell
  looks like the GNOME apps next to it; text, icons, translation (gettext) and
  accessibility come with GTK.
- **Fast to change without eyes on the panels**: edit, `install.sh`, the
  session restarts the shell, `duoctl screenshot`.
- Costs: one Python process, ~150 MB RSS (most of it GTK and GL), 0 % CPU
  while idle. The pure parts are unit-tested on the host.

The Rust bindings (`gtk4-layer-shell` crate) remain the way to go if the shell
grows into something with heavy logic; the protocol side would not change.

libgtk4-layer-shell must be loaded before libwayland-client: the `duo-shell`
entry script loads it with `ctypes` before importing GTK (no `LD_PRELOAD`, so
the apps the grid starts do not inherit it).

**The pen scrolls the lists like a finger** (`duoshell/penscroll.py`, the same
file as Duo Settings' `duosettings/penscroll.py`, a test there keeps them
equal): GTK 4 drags a scrolled view only for a touchscreen, and the pen's tip
is a button press of a tablet tool. The app grid, the quick menu, the desk menu
and the taskbar each have one capture-phase drag gesture for the pen (on
Wayland the press's device is the tablet's logical pointer, a mouse to GTK:
the event's device tool says pen, `penscroll.is_pen`): past the drag
threshold it claims the press and moves the scrolled view under it (a flick
glides on). Mouse and finger stay GTK's. Since 2026-11-16 duo-wc gives the pen
as a finger to every app but the drawing apps (`pen_input = apps`, the
default), the shell included: then GTK scrolls with it itself, and this is
for `pen_input = all`.

## Files

| path | what |
| --- | --- |
| `duo-shell` | entry script (loads libgtk4-layer-shell, runs `duoshell.main`) |
| `duoshell/main.py` | the `Shell`: monitors, bars, grids, timers, duo-wc events |
| `duoshell/bar.py`, `duoshell/launcher.py` | the status bar, the app grid |
| `duoshell/quickpages.py` | which Settings page a quick-menu tile's long press opens, the command line, the click that ends a long press (pure, `tests/test_quickpages.py`: every page exists in Duo Settings, every key is a tile) |
| `duoshell/audioroute.py`, `duoshell/audioroutemodel.py` | a Bluetooth or USB output that connects becomes the output (`pactl subscribe`, then `pactl set-default-sink`; not during a call, then after it); WirePlumber falls back to the speaker when it goes (pure rules, `tests/test_audioroutemodel.py`) |
| `duoshell/quickmenu.py`, `duoshell/system.py` | the quick menu; WiFi / backlight / duo-posed readers and the helper call (pure parsers) |
| `duoshell/torchmodel.py`, `icons/` | the Flashlight tile: the flash LED found, read, switched; what the tile says (pure, `tests/test_torchmodel.py`); the shell's own symbolic icons (a hicolor tree: an icon straight in a search path is not recoloured) |
| `duoshell/shellbus.py`, `org.duo2os.Shell.conf` | the administrator's remote on the system bus: the quick menu opened / closed without a touch, root only (the decision pure, `tests/test_shellbus.py`); its bus policy |
| `duoshell/hotspot.py`, `duoshell/hotspotmodel.py` | the quick menu's Hotspot tile: `duo2-hotspot` with the shell's grant, the state file watched; what the tile says (pure, `tests/test_hotspotmodel.py`) |
| `duoshell/notifications.py`, `duoshell/notifystore.py` | the notification daemon and banners; the notifications as data (pure) |
| `duoshell/dock.py`, `duoshell/dockmodel.py` | the dock; pinned + running apps (pure) |
| `duoshell/displayprompt.py`, `duoshell/displaymodel.py` | "Desktop or Mirror?" for a display plugged in; the quick menu's Display tile (pure, `tests/test_displaymodel.py`) |
| `duoshell/taskbar.py`, `duoshell/deskmenu.py`, `duoshell/deskmodel.py` | a desk's taskbar and app menu; what a desk shows, the menu's search, a taskbar click (pure, `tests/test_deskmodel.py`) |
| `duoshell/overviewlabels.py`, `duoshell/overviewmodel.py` | titles and icons on duo-wc's overview cards; what they say and where (pure) |
| `duoshell/laptopkeys.py`, `duoshell/laptopmodel.py` | the labels on laptop mode's keyboard panel; what each key shows with Shift / Caps Lock / AltGr, the list of layouts' names, which panel is which (pure, `tests/test_laptopmodel.py`) |
| `duoshell/keyboardmodel.py` | the system's keyboard layouts sent to duo-wc, a layout added on laptop mode's keyboard added back (pure, `tests/test_keyboardmodel.py`) |
| `duoshell/volumeosd.py`, `duoshell/volume.py` | the volume keys: wpctl calls and the display; the steps, mute, repeat (pure) |
| `duoshell/pen.py`, `duoshell/penmodel.py` | the pen's top button (duo-wc's `pen_button`: the note app, a screenshot, the quick menu) and its low-battery notice; pen.conf, the actions, which device is a Surface pen, the once-only battery rule (pure, `tests/test_penmodel.py`) |
| `duoshell/widgets.py`, `duoshell/swipe.py` | swipe-to-dismiss boxes and icons; the swipe rules (pure) |
| `duoshell/lockclient.py`, `duoshell/lockpad.py`, `duo-lock` | the lock screen client; swipe, PIN, keypad, back-off (pure) |
| `duoshell/lockstore.py`, `duo-lock-helper` | the PIN and the lock's rules, root's (`/var/lib/duo2-lock`): the policy, the back-off (pure); the one door to them (sudoers: verify, set, clear, status, policy) |
| `duoshell/launchermodel.py` | the app drawer's list view: sections, the A-Z index, grid / list (pure) |
| `duoshell/mobile.py`, `duoshell/mobilemodel.py`, `duoshell/simpin.py`, `tests/fake_mm.py` | ModemManager (the SIM's lock, signal, operator, the PIN sent); what the bar, the data tile and the PIN dialog show, the PIN / PUK entry (pure); the dialog; a fake ModemManager for trying it |
| `duoshell/ambient.py`, `duoshell/ambientmodel.py` | the closed phone's peek / glance strips (who calls while a call rings); where and what, the call line's fit (pure) |
| `duo-clipboard-bridge`, `duoshell/clipmodel.py`, `tests/clipboard_probe.py` | the Android clipboard bridge (ext-data-control + Waydroid's IClipboard over binder); its rules (pure); a trusted probe that reads / sets the host clipboard |
| `duoshell/haptics.py`, `duoshell/hapticsmodel.py` | the Sound tile (the four sound modes), a notification's feedback (feedbackd); the modes as feedbackd's profile and strength, which event a notification gives (pure) |
| `duoshell/svgicon.py` | symbolic icons from the icon theme for plain cairo (the lock screen, the glance strip): the SVG path data read and filled; only paths, arcs and transforms refused (the caller falls back), `tests/test_svgicon.py` |
| `duoshell/emergencymodel.py` | emergency calls from the lock screen and the SIM PIN dialog: the numbers, the keypad, the lock's layout, the lock-shell wire (pure) |
| `duoshell/dnd.py`, `duoshell/dndmodel.py` | do not disturb: the switch, Calls' ringer silenced through feedbackd's per-app profile; the exceptions (repeated callers, contacts) and their file (pure) |
| `duoshell/calls.py`, `duoshell/callsmodel.py` | incoming calls: the screen lights, the lock screen's call card and its buttons, the strips' caller, silencing (Calls' objects on the session bus; duo-wc's `set_ring`); which call rings, aloud or silenced, the card, the shell-lock wire, the card's layout, the volume keys during a ring (pure) |
| `duoshell/alarmsmodel.py` | a tap on a clock: how GNOME Clocks is opened on its Alarms page (pure) |
| `duoshell/location.py`, `duoshell/locationmodel.py` | geoclue's agent: the Location switch, the question for a sandboxed app; the switch and the answers (pure) |
| `duoshell/proximity.py`, `duoshell/proximitymodel.py`, `tests/fake_call.py` | the proximity sensor during calls (ModemManager's calls, callaudiod's speaker, iio-sensor-proxy); which panels go dark, the far delay (pure); a fake call and sensor on the session bus |
| `duoshell/screensaver.py`, `duoshell/screensavermodel.py` | org.freedesktop.ScreenSaver: apps' "keep the screen on" as duo-wc's idle hold, the portal started; the inhibitors, their holders, GetActive (pure) |
| `duo-shell-helper` | root actions for the quick menu (sudoers rule for this file only) |
| `lock/gen-protocols.py` | generates the ext-session-lock / viewporter bindings at install |
| `portals.conf` | xdg-desktop-portal: the GTK backend (dark libadwaita apps); ScreenCast and Screenshot from xdg-desktop-portal-wlr (`shell/duo-screencast`, docs/SCREENCAST.md) |
| `duoshell/status.py` | battery, WiFi (pure parsing + readers) |
| `duoshell/apps.py` | installed apps, search matching (pure) |
| `duoshell/dbus.py` | BlueZ and the on-screen keyboard (sm.puri.OSK0) over D-Bus |
| `duoshell/oskpolicy.py` | what the keyboard button does (pure) |
| `duoshell/wc.py` | duo-wc's control socket: requests and the event stream |
| `duoshell/trust.py` | the session's trust factory: trusted control and Wayland connections, non-dumpable (docs/hardware/security.md) |
| `duoshell/i18n.py` | gettext domain `duo-shell` |
| `duoshell/style.css` | the look: one dark theme, one accent (Adwaita blue), rounded cards, Cantarell, one spacing scale, spring-like motion (black where nothing is: AMOLED) |
| `duoshell/home.py`, `duoshell/homemodel.py` | the home screen; its pages as data (pure) |
| `duoshell/wallpaper.py`, `duoshell/wallpaperview.py`, `duoshell/wallpicture.py`, `duoshell/bing.py`, `duo-wallpaper-bing`, `systemd/` | the wallpaper (choice, geometry, decoding; the watcher that hands the picture to the home screens; the home screen's widget that cuts its part as it draws); the Bing parsing / retention (pure) and the daily fetch + its user service and timer |
| `duoshell/weather.py` | the weather widget's data (libgweather, GNOME Weather's place) |
| `duoshell/confirm.py` | the force-close question |
| `po/duo-shell.pot`, `po/update-pot.py` | translation template and its generator |
| `90_duo2-os.gschema.override` | GNOME defaults: on-screen keyboard on, dark |
| `tests/` | unit tests (`python3 -m unittest discover -s tests`) |
| `tests/run-gtk.sh`, `tests/gtk.Dockerfile`, `tests/gtk_quick_tiles.py` | the quick menu's tiles under GTK 4 + the shell's CSS (Debian trixie, broadway, in a container): every tile with each of its status lines, English and a Hungarian sample, at 538 px; no line cut short or over two lines, tiles >= 48 px; the long press (the tile under it and its page, a greyed tile too, none without a page, the click that ends it swallowed); pictures to `/tmp/duo-shell-gtk` |

## Install and run

```sh
shell/duo-shell/install.sh     # packages, /usr/local/lib/duo-shell, schema override;
                               # restarts a running shell
```

duo-wc's session (`compositor/duo-wc/dist/session`, installed by duo-wc's
`install.sh`) runs everything on the user's own session bus
(`/run/user/UID/bus`, the lingering user manager's, the one an ssh login gets
too; `dbus-run-session` only when there is none), starts `duo-shell`
and the on-screen keyboard (duo-wc's `osk` script: phosh-osk-stub, or
squeekboard with `GTK_THEME=Adwaita:dark`), both through `duoctl
exec-trusted` (above, "Trust"), and starts them again when
they exit (1 s, doubling up to a minute while they keep failing); their PIDs
are in `/run/duo-wc/duo-shell.pid` and `osk.pid`. The log is duo-wc's
journal (`sudo journalctl -t duo-wc -b | grep duo-shell`).

From an ssh shell, as the session's user (the administrator: `sudo -u USER
sh -c '. /run/duo-wc/session.env; ...'`, or remote.sh's `duo2_session`):

```sh
. /run/duo-wc/session.env                       # WAYLAND_DISPLAY, the bus, duo-wc's socket
gnome-text-editor &                             # opens on the panel touched last
busctl --user call sm.puri.OSK0 /sm/puri/OSK0 sm.puri.OSK0 SetVisible b true
notify-send hello                               # (the bus and audio work without session.env too)
python3 -m unittest discover -s tests           # on the host, in this directory
```

Translations: `po/update-pot.py` writes the template; a language goes to
`/usr/local/share/locale/<lang>/LC_MESSAGES/duo-shell.mo` (none yet).

## Verified (2026-11-06, without eyes on the panels)

Screenshots with `duoctl screenshot` (which includes layer surfaces), touches
through evemu clones of the touchscreens (`compositor/duo-wc/tools/touch.py`):

- Both bars map (`duo-bar` on DSI-1 and DSI-2), windows start at y = 48;
  clock, WiFi (signal from wpa_supplicant), Bluetooth (no adapter), battery
  89 % charging, the two buttons; icons from Adwaita (needs `librsvg2-common`,
  the SVG loader).
- A swipe up from DSI-2's bottom edge opens the grid on DSI-2, with nothing
  focused (no keyboard); tapping the search field brings up squeekboard on
  DSI-2 (normal layout), typing `te` on it filters the grid, Enter starts Text
  Editor (GTK4), which opens on DSI-2 (`expect_window`), gets the keyboard,
  activates through xdg-activation and brings up the keyboard in its text
  view; the window shrinks above it.
- The keyboard button: on DSI-1 with squeekboard hidden shows it on DSI-1,
  again hides it, on DSI-2 shows it on DSI-2, then on DSI-1 brings it to
  DSI-1, again hides it (`Visible` and duo-wc's `osk_output` checked after
  each tap; windows grow back when it hides).
- With the screen blanked the shell stops its timers, and starts them again
  when it comes back.
- Rotation (DSI-2 at 90): the bar and the keyboard follow the landscape panel.
- `span`: the spanning window starts below both bars.

## Wallpaper

The home screen (both panels) and the lock screen show a picture (scaled to
cover, 28 % black over it for the clock and the widgets, a little more on the
lock screen) or, by default, the dark gradient look. The choice is
`~/.config/duo-shell/wallpaper.conf` (`key = value`, written by Duo
Settings > Wallpaper): `source = default | file`, `file = /path`,
`mode = same | span`, `bing = on | off`, `bing_market`, `dim` (per cent).
`same` shows the picture on each panel; `span` lays one picture over the box
around both panels, the hinge gap inside it (`wallpaper.source_rect`, from
duo-wc's `get_outputs`, so the picture is continuous over the gap and follows
`set_hinge_gap`, poses and rotation). The shell watches the file and the Bing
directory (Gio file monitor) and decodes the picture once, in a thread
(GdkPixbuf, at most 4096 px on its longer side), into one Gdk.Texture for
both home screens; each draws its own part of it as it draws
(`wallpicture.py`, a Gtk.Widget: `wallpaper.draw_rect` from duo-wc's
layout, the GPU scales, trilinear). A rotation or a pose needs no new cut: a
home screen given a new size asks duo-wc for the layout before its first
frame at that size, so that frame already shows the right part (it used to
be cut per panel 400 ms after the change, and the new cut popped in after
duo-wc's turn animation, the owner, 2026-11-07); the state event's layout
changes (hinge gap) redraw it. duo-lock reads the file when it locks and
draws the same crop with cairo (`render_panel`). Nothing restarts, duo-wc is
untouched.

**Bing image of the day** (`bing = on`): `duo-wallpaper-bing`, a systemd
**user** service run by a daily timer (`duo-wallpaper-bing.timer`: 04:00 plus up
to 2 h, `Persistent`, and 5 min after the session starts; the script skips
the request when today's picture is already stored), or at once when the
switch is turned on in Settings. It does nothing, and asks nobody, while the
switch is off. One request to
`https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=<market>`
(a generic User-Agent, no cookies or ids), then the picture from the same host
(UHD, else 1920x1080; HTTPS, 20 s timeouts, JPEG magic and size checked, the
JSON fields validated before they reach a URL or a file name). Stored in
`~/.local/share/duo-shell/wallpapers/bing/<date>_<market>.jpg` (the last 5;
`current.json` names the current one with title, copyright and link, for a
caption). Offline or an odd answer: the last good picture stays. The market
defaults from `LANG` (`hu_HU` -> `hu-HU`). Downloaded pictures are user data,
never part of the repo or the image.

Built-in pictures: Debian's `gnome-backgrounds` (the dark variants, in
`/usr/share/backgrounds/gnome`; JPEG XL through `libjxl-gdk-pixbuf`), free
licences: CC BY-SA 3.0 (Jakub Steiner; Amber by David Lapshin; see
`/usr/share/doc/gnome-backgrounds/copyright`). The app grid's search entry
is not focusable until it is tapped (opening the grid no longer flashes the
keyboard up).

## Opening the alarms from a clock (2026-11-08)

A tap on the status bar's clock or on the home screen's clock and date opens
GNOME Clocks on its **Alarms** page, on that panel (`alarmsmodel.py`,
`Shell.open_alarms`). Clocks 48 has no option, D-Bus method or exported
action for a page (its "set-page" / Alt+2 is a widget action, not on the
bus); a Clocks window takes its page from its state,
`org.gnome.clocks.state.window panel-id`, when it is made, and writes the
page it shows back there when it closes. So the shell sets `panel-id` to
`alarm` and launches Clocks' desktop file like any app (D-Bus activation,
the launch context's token, `expect_window` for the panel): a Clocks that is
not running, or runs in the background without a window (duo-apps' session
hook leaves it that way when alarms are set), opens its new window on
Alarms. A Clocks window that is up is first asked to close (duo-wc's
`close_window`, as the overview's X; Clocks stays running), and 0.3 s after
it has gone (its own write of the page lands first) the same launch brings it
back on Alarms. While an alarm rings (Clocks' urgent notification is there)
its window only comes to the front.

## Lock screen

`duo-lock` locks with ext-session-lock-v1 (pywayland + cairo; Debian's
gtk4-layer-shell 1.0.4 has no session lock) on every panel, unlock on
either:

- **without a PIN** (the default, 2026-11-07): the clock, the date and
  "Swipe up to unlock"; a swipe up (at least 90 px or 12 % of the panel,
  more up than sideways, `lockpad.swipe_unlocks`) or Enter on a keyboard
  unlocks;
- **with a PIN** (`duo-lock --set-pin` or Duo Settings; optional, the owner
  decides): PIN dots and a keypad; a plugged keyboard's digits, Enter and
  Backspace work too. **Root keeps the PIN** (2026-11-07; it was in
  `~/.config/duo-shell/lock.conf`, which any app could read or delete):
  PBKDF2-SHA256 (salted, 200 000 rounds) in `/var/lib/duo2-lock/pin`
  (root, 0600), the rules in `/var/lib/duo2-lock/policy` (0644, no
  secret: `pin = yes|no`, `lock_on_blank`, `lock_on_close`). The only door
  is `duo-lock-helper` (`lockstore.py`; a sudoers rule for its `verify`,
  `set`, `clear`, `status` and `policy` only): duo-lock sends the typed PIN
  to `verify` and never sees the hash; `set` and `clear` want the current
  PIN; a rule that makes the phone less safe (lock_on_close off,
  lock_on_blank off) wants it too; the back-off is root's (five wrong PINs,
  then 30 s, doubling with each further one up to 15 min), so no app can
  guess through the helper faster. `install.sh` moves an old lock.conf PIN
  over (`migrate`, the installer's only).

**A call while locked** (2026-11-08, "Incoming calls" above): the lower part
of the lock screen becomes the call card (the clock and the date stay): the
caller, "Incoming call" and Decline (red) / Answer (green); once the call is
up its time, Speaker (blue while on) / Hang up and, with a PIN, "Enter PIN".
The handset, the speaker and backspace are the icon theme's symbolic icons
(`call-start`, `call-stop`, `audio-speakers`, `edit-clear`; 2026-11-10: the drawn
handset and the font's backspace glyph looked home-made), read from Adwaita's SVG
and filled with plain cairo by `svgicon.py` (the lock has no GTK and no librsvg
binding); the old drawings remain the fallback. The buttons
are pressed where the finger went down and lifted; a swipe up still unlocks
a swipe lock. Pictures: `out/shell-shots/lock-call-*.png` (not in git).

**Emergency calls from the lock screen** (2026-11-09,
telephony/README.md "Emergency calls"): an "Emergency call" pill (red;
below the PIN keypad, beside it on its side, above the swipe lock's hint;
not while a call is up or ringing) opens an emergency keypad over the whole
panel: "Emergency call", the number typed, one-tap buttons for 112 and the
country's own numbers (the network's and the SIM's MCC; a tap types the
number, the green call key dials), Cancel. Only emergency numbers are
dialled (`emergencymodel.py`: 112, 911, ModemManager's no-SIM list without a
SIM, the SIM's list, the national numbers while ModemManager is registered
or in airplane mode); the shell sends the lock the list with every card
message (`"emergency": {"numbers", "suggestions"}`), the lock sends
`{"do": "emergency", "number": "112"}` back on the same socketpair, the
shell checks it again and asks ModemManager for the call (`mobile.Mobile.
dial`: Calls starts and shows it; started by the shell when nobody did
after 3 s). The call then shows on the call card (Speaker / Hang up), and
when it is over the lock screen is back. A swipe up still unlocks a swipe
lock. Pictures (drawn offline with cairo): `out/shell-shots/lock-emergency-*.png`,
`lock-swipe-button-*.png`, `lock-pin-button-*.png` (not in git).

**When it locks**: when the phone is **folded closed** (duo-wc's state says
`posture: closed`, from duo-posed; `lock_on_close` in the lock's policy, on
by default), and, only with a PIN and `lock_on_blank = on`, when the screen
goes off. duo-wc offers the protocol only with `session_lock = on`, which
`sudo duo2-sensors-mode on` sets together with duo-posed's `enabled` (and
`off` clears both and ends a lock). The power key of a locked, closed phone
shows the glance strip; opening the phone shows the lock screen. `duoctl
unlock` from ssh (as the administrator) ends any lock; duo-lock is told
(`finished`) and exits, so the next fold locks again (it used to stay and
block every later lock; the shell ends one that stays 3 s). `duo-lock` run by
hand as the user is an app: it sees no ext-session-lock and says so; the
administrator can run it trusted with `sudo duoctl exec-trusted --wayland --
setpriv --reuid=USER --regid=USER --init-groups env HOME=/home/USER
XDG_RUNTIME_DIR=/run/duo-wc duo-lock`.

## Verified (2026-11-07 night, without eyes)

Pictures: `out/shell-shots/m4b-*.png`.

- Quick menu pulled down on DSI-2 (drag on the bar): tiles show WiFi
  (the home network), Bluetooth on, Auto-rotate "Sensors only log", Keyboard,
  Hinge gap on, Dual lit. Taps: hinge gap off / on, Span then Dual,
  Bluetooth off / on (BlueZ `Powered` false / true), brightness slider tap at
  40 % -> both backlights 819 (set back to 69 %).
- `notify-send` -> banner on the panel of the focused terminal, the mail
  icon in both bars; three more notifications in the list (the critical one
  with a red edge); a sideways swipe dismissed one.
- Dock: a short swipe on DSI-1 peeks it above the keyboard (foot with a
  running dot, Text Editor); the overview shows it on both panels; tapping
  Text Editor in it launched it on DSI-2 and closed the overview; in span it
  is a taskbar on both panels.
- Text Editor (libadwaita) dark after the portal (`color-scheme` 1).
- Lock screen: see the duo-wc README; nothing is set now.
- Unit tests: 45 (`python3 -m unittest discover -s tests`).

## Verified (2026-11-07 morning)

Pictures: `out/shell-shots/m5-*.png`.

- Dock: the lost first tap reproduced (fresh shell, and 2 of 3 when the
  keyboard moved to the dock's panel with the swipe). With WAYLAND_DEBUG
  and GDK_DEBUG=events the dock got `wl_touch.down` and GDK made the touch
  event, but `pick()` at touch-begin found the row Box (rebuilt buttons
  without a place yet), at touch-end the button's Image. After the fixes:
  4 of 4, 3 of 3 and, with the overview's dock, 2 of 2 first taps pressed.
- Overview labels on both panels ("g (Draft) - Text Editor", "foot", with
  their icons); taps on a label band, a card and the background reached
  the overview through the label surface.
- Unit tests: 51 (`python3 -m unittest discover -s tests`).

## Verified (2026-11-07 day)

Pictures: `out/shell-shots/audio-*.png`.

- Volume keys (evemu clones of `gpio-keys` and `pmic_resin`): steps, hold,
  mute at the bottom, the display on the active panel and gone after 1.5 s,
  nothing while dark (details in the duo-wc README).
- Browsers: Chromium and Firefox ESR in the app grid. Chromium tapped in the
  grid on DSI-2: its window on DSI-2 in 1 s, native Wayland at 2.5, the
  on-screen keyboard came up for the address bar (text-input-v3). Firefox
  tapped on DSI-1: on DSI-1 in 4 s. Both rendered a local `file://` test
  page, scrolled with a finger, and a tap reached the page as a `touchstart`.
  No GPU fault in the kernel log during either. Pinch-zoom not tried (the
  test tool has one finger).
- Notifications and the portal (dark Text Editor) on the user's bus.
- Unit tests: 59 (`python3 -m unittest discover -s tests`).

## Verified (2026-11-07 afternoon)

Pictures: `out/shell-shots/quick-menu-tiles-centred-DSI-1.png`,
`ambient-peek-both.png`, `ambient-peek-notification-DSI-2.png`,
`ambient-glance-both.png`, `lock-swipe-DSI-1.png`,
`fullscreen-short-swipe-no-dock-DSI-2.png`, `popup-after-span-menu-both.png`.

- Quick menu tiles: title and status centred on the icon (they started at
  the tile's top).
- Span: no standing taskbar (the spanning window reaches the bottom); a
  fullscreen window: a short swipe shows no dock.
- Peek (`duoctl ambient peek`): the clock, the battery and a notification's
  icon on DSI-1's left and DSI-2's right edge, black elsewhere; glance: a
  thin clock and the battery next to the hinge on both.
- Posture closed with duo-wc's session lock on: duo-lock locked both panels
  with "Swipe up to unlock"; a synthetic swipe up unlocked.
- Unit tests: 70 (`python3 -m unittest discover -s tests`).

## Verified (2026-11-07 evening)

Pictures: `out/shell-shots/m6-*.png`.

- Home screen on DSI-1 after a long swipe up (duo-wc set foot aside): the
  clock, the date, the weather card ("Add a place": GNOME Weather has no
  place yet) and the battery card, ten icons, the standing dock
  (`m6-home-DSI-1.png`); a swipe up on the page opened the app grid; a
  hold on Text Editor's icon started the edit mode and carried it one slot
  on (saved in home.json; `m6-home-edit-DSI-1.png`), Done ended it.
- The app grid's hold menu (`m6-grid-menu-DSI-1.png`).
- The quick menu by a swipe down from the top edge, without the pose row,
  closed again by a short swipe up from the bottom edge
  (`m6-quick-menu-DSI-2.png`); a back swipe closed the app grid.
- A banner and the peeking dock over a terminal with the keyboard up
  (`m6-dock-banner-DSI-1.png`).
- Unit tests: 82 (`python3 -m unittest discover -s tests`): the home layout
  (pages, spill, move, add, remove, load / save, slots, edges), alarms
  waking the screen, the glance text and the right-panel defaults, the
  overview labels' closing states.

## Verified (2026-11-07 evening, the owner's list)

Pictures: `out/shell-shots/n7/`.

- Home: the wallpaper under the bar, icons where they were with the
  keyboard up, the dock above it (`home-osk-DSI-1.png`); "Firefox ESR",
  "Chromium" under the icons; the "i" card fitted into the panel with the
  bison's caption and Learn more (`bing-card-DSI-1.png`).
- App drawer: the grid with short names (`drawer-grid-DSI-1.png`); the
  toggle to the list (`drawer-list-2.png`); a scrub on the index to the
  bottom (`drawer-list-4.png`) and a hold on H: the "H" bubble, the list at
  I (`drawer-list-scrub.png`).
- Peek / glance: see the duo-wc README (strip on screen in 236-309 ms).
- Quick menu with the Panels tile (`quick-menu-panels-DSI-1.png`); the
  restart and the remove-packages questions (`confirm-*.png`).
- Lock: the helper as the session user: `verify` with no PIN set exits 4 (no PIN),
  `status` prints the policy, `migrate` is refused by sudo. No PIN was set
  on the phone (the owner chooses it); set / change / clear / back-off are
  covered by `tests/test_lockstore.py` against a temporary store.
- Unit tests: 130 (`python3 -m unittest discover -s tests`).
- SIM PIN (later the same evening, a fake ModemManager, `sim-*.png`): the
  dialog over the home screen with "3 attempts left", four dots, a wrong
  PIN: "Wrong PIN.", "2 attempts left"; the right one: unlocked, two
  seconds later "Example SIM" and the bars in the bar, the Mobile data tile
  On; started blocked: "SIM blocked", "10 attempts left", the PUK, the new
  PIN twice: unlocked. The data tile Off / On: duo2-wwan logged "mobile
  data switch: off / on". Unit tests: 138.
- Airplane mode (2026-11-07, night; telephony/README.md): the switch file
  on / off by hand (WiFi is the way to the phone, so the helper's own
  `airplane on` was not run on it): the shell logged "airplane mode on /
  off" and duo2-wwan put the modem into low power in 2 s, mobile data was
  back 3 s after off; `duo-shell-helper bluetooth off / on`: rfkill 1 / 0,
  BlueZ `Powered` false / true by itself. The helper's airplane logic
  (which radios come back, a WiFi switched on in airplane mode stays on, a
  Bluetooth that was off stays off) is unit-tested on a fake rfkill tree.
  `tests/fake_mm.py` also plays the SIM lock for Duo Settings now
  (EnablePin / ChangePin, `EnabledFacilityLocks`). Unit tests: 146.
- Wallpaper at a rotation (2026-11-07, late): the home screens cut their
  part of the one decoded picture as they draw (`wallpicture.py`); duo-wc's
  turn animation slowed to 3 s, screenshots of DSI-2 during it (`out/shell-
  shots/n7/rotate2-to90-home.png`, `rotate2-to0-home.png`): the new frame's
  picture taken at the clients' first frame at the new size already has the
  right cut, the last frame of the turn and the first live one are the same
  picture; at full speed duo-wc took no second picture of the home screen
  (it had nothing to catch up). Decoding the Bing UHD picture: 195 ms, once
  per wallpaper. Unit tests: two more (`draw_rect`).

## Proximity: trying it without a call (2026-11-07 night)

While root's `/etc/duo2/shell-fake-call` exists the shell reads the calls
and the sensor from the SESSION bus, where `tests/fake_call.py` plays them
(an app cannot switch this on). As the GUI user in the session:

```sh
sudo touch /etc/duo2/shell-fake-call; sudo kill $(cat /run/duo-wc/duo-shell.pid)   # admin
python3 tests/fake_call.py &                                   # session user, duo2_session
F="busctl --user call org.freedesktop.ModemManager1 /fake org.duo2os.FakeCall"
$F SetCall s active; $F SetNear b true      # the Calls panel goes dark
$F SetNear b false                          # back after 250 ms
busctl --user call org.mobian_project.CallAudio /org/mobian_project/CallAudio \
    org.mobian_project.CallAudio EnableSpeaker b true          # speaker: back at once
$F SetCall s none                           # sensor released
sudo rm /etc/duo2/shell-fake-call            # and restart the shell
```

Verified on the phone so (duo-wc `set_idle_timeout 0` for the run, Calls'
window on DSI-1): dialing claims the sensor, near holds DSI-1 (`lit`
DSI-2, `proximity` DSI-1, not locked), far 0.1 s later still held, 0.6 s
later back; the speaker on while near releases it, off holds it again;
the call terminated releases the sensor; ringing in claims nothing. A
hold not renewed runs out after its ttl (`duoctl set_proximity DSI-2 ttl
4000`: back after 4 s). The real sensor: `scripts/duo2-proximity-test`
claims it (190 ms) and reads "far" on the desk.

## The touch fallback notice (2026-11-07)

When duo2-touchd has fallen back to its last-resort split mode (the outer
edges out of reach; touch/README.md), its note `/run/duo2-touchd.fallback`
exists. The shell watches it (a file monitor, plus a check 3 s after start)
and posts its own notification, "Touch screen in basic mode", resident and
critical (shown in do-not-disturb too), with "Try again":
`duo-shell-helper touch retry` removes the note and the re-init count and
restarts duo2-touchd, stitched; the notification closes when the note goes.
The shell's own notifications go through `NotificationCenter.post()` (no
D-Bus round trip; their actions call back into the shell). Verified on the
phone with a simulated note: the banner showed, the owner tapped Try again,
the daemon came back stitched and the banner closed.

## Not yet

- WiFi off / on from the quick menu: the helper's rfkill path is unit-tested,
  but switching the radio off over WiFi was only tried once at the end of
  the night (see the report); a home screen with widgets (4c) is not there.
- The clock follows the phone's time zone, which is UTC in the image
  (`sudo timedatectl set-timezone Europe/Budapest`).
- A dock row the shell has to rebuild while a finger is on it (a window
  opening or closing at that very moment) can still lose that one tap.
