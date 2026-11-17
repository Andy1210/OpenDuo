# duo-wc

The Wayland compositor for duo2-os on the Microsoft Surface Duo 2: both panels
are one desktop, each panel's touchscreen drives its own panel. Built on
[Smithay](https://github.com/Smithay/smithay).

MIT licensed (`LICENSE`). **Do not copy code from niri** (GPL-3.0); anything
learned there is re-derived.

Status: **milestones 4a and 5 done, 4b in part** of the compositor plan: two
outputs + touch + a terminal (milestone 1); panel power, idle blanking, the
power key, poses (dual / single / span) and rotation, all over IPC (milestone
2); the sensors drive poses through `duo-posed` (milestone 3, `sensors/`);
the protocols the shell needs: layer shell, the on-screen keyboard
(text-input-v3, input-method-v2, virtual-keyboard-v1, squeekboard kept on the
right panel), xdg-activation, a foreign-toplevel list, edge swipes, an event
stream on the control socket; and (2026-11-07 night) the **task overview**
with live window pictures, the panels **waking in non-blocking commits**
(off by default since 2026-11-08: one modeset per panel), the keyboard in
**span** opposite the text field, and the **lock screen**'s compositor side
(ext-session-lock-v1, shipped off); (2026-11-07 morning)
**two windows on one panel** (a split with a divider, milestone 5), the
panels **going off in one non-blocking commit** too, titles and icons on
the overview's cards (drawn by the shell) and their opening flight, and the
dock's lost first tap fixed; (2026-11-07 day) the **volume keys** go to the
shell (never to a client, also while dark, without waking), and the session
runs on the user's own D-Bus with PipeWire's sockets (`docs/hardware/audio.md` step
8); (2026-11-07 afternoon) **popups fitted into their panel**, no dock over
a fullscreen window, the on-screen keyboard **staying put** on stray
touches, the posture from `duo-posed` with the closed phone's **peek and
glance** screens, and **wp_presentation** (90 Hz pacing for clients);
(2026-11-07 evening) **one canvas** (milestone 6 of the compositor plan):
windows carried by their top to a panel, a half of one or both panels,
across the hinge with the two touchscreens stitched into one drag, a
double tap on the top for one panel <-> both panels, **home** (a panel's
windows set aside), the **back** and **top** edge swipes, the overview as
the canvas (a row of spanning windows over the hinge, the same snap
targets), close as a request with **force close**, springs for everything
that moves; (2026-11-08 night) the **pen** (`tablet.rs`): tablet-v2 for
the clients that bind it (pressure, tilt, eraser, barrel button, hover),
the tip as a touch point for the rest; (2026-11-15) its hover dot, the
barrel button as a right click where there is a pointer, the tilt turned
with the panel, `pen_button` / `pen_dot` ("The pen" below); (2026-11-09 night) **screen capture**
for the screen-cast portal (wlr-screencopy v3, ext-image-copy-capture, a
capture-only grant: docs/SCREENCAST.md); **idle inhibit** (zwp_idle_inhibit_manager_v1,
ext-idle-notify-v1, the shell's idle hold for org.freedesktop.ScreenSaver: a
video or a map keeps the screen on, "Idle inhibit" below); (2026-11-09
night and 2026-11-13, not yet on hardware) an **external display** on the
USB-C port as a desk, and **laptop mode**: one panel a desk of floating
windows, the other a keyboard and a touchpad ("Laptop mode" below); (2026-11-16) **X11 apps**
through Xwayland, started by the first X11 client (Krita: "X11 apps" below); (2026-11-16
evening) **frame pacing**: wp_fifo_v1, wp_commit_timing_v1, and hidden windows told
so (`suspended`) and given a frame a second at most ("Frame pacing" below). The shell (status bars, app grid, quick menu,
notifications, dock, lock screen client, the overview's card labels) is
`shell/duo-shell/` (separate Wayland clients).

## What it does

- **Two outputs.** Every connected connector of the one DRM device (msm,
  `card0`) becomes an output with its own CRTC, its own `DrmCompositor`
  (swapchain, page flips) and its own render/vblank path; the GPU (freedreno
  FD660, GLES 3.2) and one `GlesRenderer` are shared. A connector keeps the
  CRTC fbcon left it on. Everything is composited on the GPU (client buffers are
  not scanned out directly: `NodeFilter::None`).
- **Layout and the hinge gap.** DSI-1 (left) at x = 0, DSI-2 (right) after it.
  The gap is switchable at runtime (`src/layout.rs`, `HINGE_GAP_PX = 66`,
  physical pixels):
  - `on` (default): DSI-2 starts 66 physical px past the end of DSI-1, as if the
    picture went on under the hinge. Spanning content is physically continuous;
    the strip behind the hinge is never drawn.
  - `off`: the panels are adjacent; nothing is hidden behind the hinge.

  Switching (`duoctl set_hinge_gap on|off`) re-places the outputs, every
  window on them, and resizes a spanning window, live.
- **Scale.** 2.5 by default (the panels are ~400 ppi; stock Android used 2.625).
  Clients that speak `wp_fractional_scale_v1` (foot does) draw at exactly 2.5
  through `wp_viewporter`; the rest get buffer scale 3 and are scaled down.
  At 2.5 a panel is 538 x 757 logical; the gap is 26 logical (26.4, rounded),
  so DSI-2 sits at x = 564 (gap on) or 538 (gap off). A panel's logical width is
  rounded up the way Smithay sizes outputs, so with the gap off the panels meet
  exactly at any scale.
- **Windows.** Every toplevel is maximized on ONE output, or spans all of them
  (`place_window span`). A new toplevel opens on the panel touched last if that
  one is empty, otherwise on the first empty panel (left to right), otherwise on
  the panel touched last; so two terminals started together land one per panel.
  Only panels the pose uses count, and of those the lit ones when any is lit
  (in `span` a new window spans, in `single` it opens on the one panel).
  Windows on one panel stack, the newest or last touched in front; two of
  them can share it (**split**, below). Decorations are server-side and
  there are none. Fullscreen requests are honoured where the window is: its
  panel, or both panels when it spans; out of full screen it is where it
  was. The output a client names moves nothing (Qt names the one panel it
  thinks it is on, also for a spanning window: until 2026-11-12 Angelfish's
  video went full screen on one panel and the window stayed there). A
  fullscreen window in front hides that panel's top-layer surfaces (the
  status bar) and the shell's dock (`FULLSCREEN_HIDES_OVERLAYS`) from sight
  and touch, but not the on-screen keyboard and the app grid
  (`FULLSCREEN_KEEPS_TOP`: a long swipe up still opens it over a video).
  The state's `fullscreen` names those panels (the shell shows no dock
  there).
- **Layer shell** (wlr-layer-shell, for `duo-shell` and squeekboard). Layer
  surfaces are mapped on the output they ask for (the active one if none),
  per panel, and drawn and hit-tested in their layers (overlay and top above
  the windows, bottom and background below). An exclusive zone shrinks that
  panel's maximized windows (re-placed whenever a layer surface commits); a
  spanning window gives up each panel edge's largest inset
  (`layout::span_area`: side by side it starts below both status bars).
  Layer surfaces and popups are told the scale like windows (fractional 2.5,
  else buffer scale 3). An on-demand layer surface (the app grid) on the active
  panel takes the keyboard once its interactivity is committed. xdg popups get
  their first configure (GTK menus and popovers).
- **Popups stay on their panel** (`state.rs` `unconstrain_popup`). An xdg
  popup (a GTK menu, a popover, a drop-down) is fitted before its first
  configure and on every reposition by its positioner's constraint
  adjustment, in the protocol's order: flip, slide, resize (Smithay's
  `get_unconstrained_geometry`), into ONE panel: the parent window's own, or
  for a spanning window the panel under the popup's anchor (the nearer one
  if the anchor is in the hinge gap, `layout::panel_for_point`), so a menu
  never runs off the glass or across the hinge. The room is the panel less
  the status bar (not under a fullscreen window, which hides it), a taskbar
  dock and the on-screen keyboard; a layer surface's popup may use its whole
  panel. Text Editor's main menu ran off the right edge (it now slides left
  and ends above the keyboard, its list scrolling).
- **Dialogs, and windows too big for their panel** (`dialog.rs` pure,
  2026-11-12; the owner: Duo Settings' "Use your own sound..." file chooser
  did not fit one panel, and over a spanning Settings it hung across the
  hinge). A **dialog** is a toplevel with a parent: `xdg_toplevel.set_parent`,
  or across clients through **xdg-foreign v2** (`zxdg_exporter_v2` /
  `zxdg_importer_v2`, Smithay's; GTK 3 and 4 speak it): the portal's file
  chooser (xdg-desktop-portal-gtk, a GTK 3 window of another client) is the
  dialog of the app that asked. A portal backend's window whose app named no
  parent counts too (`dialog::PORTAL_APP_IDS`). Before, duo-wc never learned
  a parent (`new_toplevel` comes at `get_toplevel`, before `set_parent`, and
  nothing handled `parent_changed`; there was no xdg-foreign), so a dialog
  opened by the new-window rule, an EMPTY panel first.
  - **Where**: decided at its first commit (`settle_dialog`, when parent, app
    id and size limits are known; again if the parent changes later): its
    parent's panel; for a spanning parent the panel touched last (where the
    button that opened it is), else the one with more room; never across
    the hinge, whatever the pose would do with a new window
    (`dialog::dialog_panel`). Its parent comes back from home with it, and a
    window asked to close that opens a dialog has answered.
  - **How big**: configured to the panel's room (less the status bar, a
    taskbar dock and the on-screen keyboard), smaller on an axis where its
    xdg `max_size` is smaller (`dialog::dialog_size`; a larger preferred size
    is not given), maximized only when it fills the room, and shown centred
    in the room at the size it draws.
  - **Too big for the panel** (any window on one panel, a dialog or not): a
    client is never smaller than its `min_size`, and the GTK 3 file chooser
    is at least 593 x 373 logical px, the GTK 4 one 656 x 326 (measured with
    Cantarell 11), on a 538 px wide panel. A window that took its configured
    size and still draws bigger is **scaled down to fit** the panel less its
    bars (`dialog::fit`, as phoc's scale-to-fit): its picture and popups
    drawn smaller (`render::scaled_window`: each surface element rescaled
    around the window's corner; a smaller `Scale` given to Smithay when the
    elements are made only moves subsurfaces and popups, the surfaces keep
    the output's scale: the first build drew the chooser full size, cut at
    the panel's edge, and only the flights, which rescale, showed it fitted),
    its touches and the pen mapped back into its
    coordinates (`client_location`, `window_under_for_client`), its popups
    fitted into the panel in its coordinates (`dialog::usable_for_client`).
    Not cut at the edge like a split half: that would hide the chooser's own
    Open button at its right edge. The on-screen keyboard does not count for
    the scale (a window that cannot get shorter goes under the keyboard, as
    before, instead of shrinking when the keyboard comes), and a window on
    its way to a new size (not yet drawn at the size asked) is not scaled
    for those frames. The log says `window bigger than its panel: shown
    scaled down` with the factor.
  - **Never over the hinge**: a window placed on one panel is drawn and
    touched only on that panel (`render::shown_on`); before, a client that
    drew past its panel's edge showed on the other panel, and counted as
    that panel's front window for the fullscreen check.
- **On-screen keyboard.** `zwp_text_input_manager_v3` (clients' text fields),
  `zwp_input_method_manager_v2` (the keyboard: phosh-osk-stub since
  2026-11-09, squeekboard before and as the fallback; the session's `osk`
  script picks, see below) and `zwp_virtual_keyboard_manager_v1` (the
  keyboard types with it); Smithay routes
  text-input enable / disable to the input method with the keyboard focus, so
  the keyboard shows when a text field gets the focus and hides when it goes.
  Everything below holds for both: they are the same kind of client (GTK3,
  a layer surface `osk` made when shown and destroyed when hidden, the same
  `sm.puri.OSK0` on the bus); where the text says squeekboard, read "the
  keyboard".
  **Where it shows is duo-wc's rule:** squeekboard always asks for the first
  output, so a layer surface with the namespace `osk` is mapped on the panel
  of the keyboard focus instead (a window's own panel; the active panel for a
  spanning one), and moved there whenever the focus changes panel (the
  surface leaves one output, enters the other, gets a configure); its
  exclusive zone then shrinks that panel's windows. **The keyboard stays
  put** (`oskguard.rs`, 2026-11-07): it used to follow every touch, and the
  hand holding the phone (an edge, a palm on the other panel) took the
  keyboard focus away from the text being typed, the on-screen keyboard
  jumping between the panels every few hundred ms. Now, while the on-screen
  keyboard is up, a touch on ANOTHER window than the one with the keyboard
  (or, in span, on the other panel's part of the spanning window) reaches
  the app at once but moves the keyboard only if it lifts as a tap (within
  450 ms, under 16 px, and not within 800 ms of a touch on the keyboard or
  while a finger is on it); a scroll, a long press or a resting palm moves
  nothing. A touch on a layer surface that takes no keyboard (a status bar,
  the dock, a banner, the keyboard itself) moves neither the active panel
  nor the keyboard; the edge swipe does not either (the app grid's long
  swipe makes its panel the active one). `focus_output` (IPC, `duoctl
  focus_output DSI-2`) gives a panel the keyboard on purpose: the bar's
  keyboard button uses it. While the **task overview** is open the
  on-screen keyboard is not drawn and its zone does not shrink the windows
  (the cards no longer shift). Input-method popups
  (candidate lists) are not drawn (squeekboard has none). **In span** the
  keyboard goes on the panel the text field is NOT on (the panel whose window
  was touched last; touching the keyboard itself does not count), over the
  spanning window, which keeps its full height: its area leaves the keyboard's
  zone out (`zone_without_osk`, `layout::zone`), so the other panel no longer
  loses a strip to an empty background.
- **A tap beside the text field puts the keyboard away** (`oskleave.rs`
  pure, `textspy.rs` live, `osk_leave = on`; 2026-11-08). duo-wc
  dispatches `zwp_text_input_v3` itself, noting each text input's enable,
  content purpose, cursor rectangle and surrounding text, and hands every
  request on to Smithay unchanged. Angelfish (Qt 6.8 WebEngine) keeps its
  text input enabled for the whole web view: it enables it when its window
  gets the keyboard focus and commits it on the first touch anywhere on
  the page (so a scroll brought the keyboard up), and a tap beside a page
  field sends nothing at all (measured with `WAYLAND_DEBUG`; the keyboard
  stayed up until the bar's button hid it). Now: an enabled text input
  whose state is no field (no caret with a height, no text:
  `oskleave::is_field`) puts the keyboard away at once (it never shows);
  and a tap on the focused app further than 48 px from its caret, with the
  keyboard up, is watched for 350 ms: an app that answers (a new caret,
  new text: a tap into a field) keeps it, silence puts it away. Put away =
  not drawn, not touched, its exclusive zone not taken off the windows,
  `osk_output` null and `osk_away` true in the state (the bar's button
  brings it back with one tap: `focus_output`). It comes back when the app
  shows a field again (a caret, text), a tap lands near the caret, the
  keyboard focus goes to another app or the app disables its text input
  (then squeekboard goes for real). Terminals (content purpose
  `terminal`: foot) are never put away; GTK and Chromium disable their text
  input on leaving a field, before the wait ends.
- **squeekboard's blink at its start is held back** (`oskstart.rs` pure,
  `textspy.rs` live; 2026-11-09). squeekboard (1.43, `Application::new`)
  starts as if a field had just been left and shows itself for 200 ms on
  the first output it hears of: at every boot the keyboard flashed on the
  left panel right after the splash (the `osk` surface 0.28 s after its
  connection, no window, no field). Now a client's FIRST keyboard surface,
  made within 3 s of its connection while the app with the keyboard focus
  has no text input enabled, is held back like a put-away one (not drawn,
  not touched, no room taken, `osk_away` true) until squeekboard takes it
  down (the blink is over), a text field asks (the focused app enables its
  text input), the bar's button asks (`focus_output`), or 1.5 s have
  passed (then it was asked for: shown). The trusted clients carry the
  time they were made (`ClientState::connected`).
- **What phosh-osk-stub needs** (`devicestate.rs`, `wlrtoplevel.rs`;
  2026-11-09). Debian trixie's phosh-osk-stub 0.46 (Phosh's keyboard,
  "Stevia" upstream: word completion, long-press accents, the GNOME layouts)
  makes its keyboard surface only once it has bound three globals besides
  the input method, the virtual keyboard and the layer shell:
  `zphoc_device_state_v1` (phoc's own protocol, generated here from its XML
  in `protocols/`: the `keyboard` capability while a libinput keyboard that
  can type is attached, letters, space and enter; the Duo's power, volume
  and headset keys are keyboards to libinput and do not count; the
  keyboard then does not unfold by itself, the bar's button still brings it;
  the tablet-mode and lid switches are never announced),
  `zwlr_foreign_toplevel_manager_v1` (v3: the windows the event stream lists,
  title, app id, `activated` = the keyboard focus, `minimized`, `fullscreen`;
  compared with what the handles were told once per loop turn, only while a
  client has it bound; every request ignored, as the protocol allows; the
  keyboard needs only the activated app id, for its `ignore-activation`
  list) and `zwlr_data_control_manager_v1` (Smithay's, beside the ext one:
  the keyboard's paste key). All three are guarded like the rest. The
  session is told `DUO_WC_FEATURES=phosh-osk` (`session.rs` `FEATURES`):
  the `osk` script starts phosh-osk-stub only then, so an older duo-wc put
  back from `duo-wc.last-good` gets squeekboard rather than a keyboard that
  never appears.
- **The on-screen keyboard's keymap reaches every app** (`keymapsync.rs`,
  2026-11-12). phosh-osk-stub types Return, Tab, the arrows and Escape
  through the virtual keyboard, with a keymap of its own (letters go through
  the input method). Smithay keeps one active keymap per seat: a virtual
  key makes its keymap active and sends it to the wl_keyboards known THEN;
  a wl_keyboard bound later gets the seat's keymap while the virtual one
  stays active, so the next virtual key reached it with no keymap first
  (Pipeline, started after the keyboard's first Return, got key 171 and no
  Enter; found with WAYLAND_DEBUG). duo-wc's `wl_seat` and
  `zwp_virtual_keyboard_v1` dispatch go through `keymapsync`: a
  `get_keyboard` after a virtual key (and no real key since) sets the seat
  keymap again (`set_xkb_config`, the same config), which makes it active
  and sends it to every keyboard; the virtual keyboard's next key then
  sends its own keymap to every keyboard, the new one included. Once per
  bound keyboard, nothing on focus changes; keys held on a hardware
  keyboard stay held, a Caps / Num Lock it had locked is reset in that one
  case. The log says `keyboard bound after an on-screen key: the seat
  keymap is active again`. Tested on a real libwayland server with Smithay's
  seat and virtual keyboard and hand-written clients (the keymap passed as
  a descriptor): without the fix the later app has the seat's keymap at the
  key, with it the keyboard's. Upstream: `new_kbd` should send the active
  keymap.
- **xdg-activation.** A client hands over a token (GTK's launch context makes
  one) to bring a window forward: it is raised, its panel becomes the active
  one, it gets the keyboard. Every fresh token (under 15 s) is honoured.
  Where it goes (`front.rs`, 2026-11-12): the launcher's word first. A
  window the launcher's `expect_window` put on its panel stays there at its
  first activation, and a hint still waiting (an app already running,
  launched again) says where the window goes; only without a hint does a
  token made by a surface on another panel move the window there. GTK makes
  a launch's token with whichever of the launcher's surfaces has the
  keyboard: Text Editor launched from the home screen on DSI-1 was moved by
  the app grid's token to DSI-2. A window that comes to the front by itself
  (its first picture, an activation) is told on the event stream
  (`window_front`, with its panels): the shell closes its app grid and quick
  menu there, which as layer surfaces would cover it.
- **Launch hint** (`expect_window <output>`): the next toplevel within 10 s
  (or `timeout_ms`) opens on that panel, in front, whatever the open rule
  says, and that panel becomes the active one (the window gets the keyboard);
  what the app grid sends before it starts an app.
- **ext-foreign-toplevel-list** (read-only list of windows, for task views and
  docks later): every toplevel with its title and app id, kept up to date,
  closed when it goes. `tools/toplevels.py` prints it.
- **Edge swipes** (`gesture.rs`, pure; the phone conventions). A touch
  that goes down within `edge_zone` (20 logical px, ~3 mm) of a lit panel's
  edge (the edges of the picture as it reads, also rotated) is held back
  until it is clear what it is: if it travels inwards (48 px from the bottom
  or the top, 28 px from a side; more inwards than across) it is a swipe,
  never delivered; if it goes across (32 px; 56 px at a side), outwards
  (12 px) or lifts first, it is delivered after all, from where it went down
  (a tap in the strip still works, only later). At the **sides and the top**
  a touch that has not become a swipe in 350 ms is delivered: the hand
  holding the phone rests on the outer edges (whose outermost millimetres
  read as the edge itself), and a resting finger must never turn into a
  back. The final map:

  | edge | gesture | what happens |
  | --- | --- | --- |
  | bottom | short swipe up (under 22 % of the panel) | the shell's dock peeks (`short`) |
  | bottom | long swipe up | **home**: the panel's windows are set aside (`home`); on a panel that shows home already: the app grid (`long`) |
  | bottom | swipe up and hold (300 ms) | the task overview (`hold`) |
  | bottom | any swipe up while the quick menu is down on that panel | the quick menu closes (no dock, no home, no overview) |
  | top | swipe down (decided at 48 px, the menu comes at once) | the quick menu (`down`), also over a fullscreen app |
  | left / right | swipe inwards and let go past 64 px (not on its way back) | **back** (`back`): the overview closes if open (`to: overview`); else a shell surface on that panel closes (the quick menu, the app grid, a question: `to: shell`); else the panel's front window gets the keyboard and a KEY_BACK (XF86Back: libadwaita, Chromium, Firefox go back; `to: app`); no window: `to: none` (the home screen's edit mode ends) |

  A compositor-drawn arrow follows a side swipe (a dark disc with a
  chevron, accent-ringed once letting go would go back). `back_swipe =
  both` (each panel's two side edges; `outer` = only the edges away from
  the hinge; `off`). Announced on the event stream:
  `{"event":"edge_swipe","edge":"left","output":"DSI-2","kind":"back","travel":120.0,"to":"app"}`.
  `edge_swipe = off` (config, or `duoctl set_edge_swipe off`) delivers every
  touch.
- **One canvas** (`snap.rs`, `stitch.rs`, `spring.rs` pure; `windrag.rs`,
  `fx.rs` live; milestone 6). The two panels are one workspace with the
  hinge as a snap line; a window is on ONE panel (the default: a new window
  opens on the panel it was launched from), on HALF of one (a split, below)
  or on BOTH (span, with the hinge gap as set). There is no user pose
  switch: the poses come from the sensors (folded or folded back: one
  panel; landscape: the panels stacked).
  - **Carrying a window by its top.** A window with a title bar of its own
    asks for it: dragging a GTK / libadwaita header bar or a browser's tab
    strip sends `xdg_toplevel.move`; duo-wc takes that touch point over (the
    client gets its lift). A window without one (it took the compositor's
    empty decorations, e.g. foot; or it is fullscreen) has the compositor's
    gesture: **touch and hold its top 28 px for 380 ms** (the touch reaches
    the app at once, it is cancelled for it when the window lifts). Design
    choice: no drawn grab handle (it would cover the apps' own title bars
    and content on a 538 px wide panel); header bars keep their buttons
    because the hold is only for bar-less windows.
  - Carried, the window shrinks to 55 % under the finger (a spring) with a
    soft shadow, in front of everything; the **target** shows as a
    see-through accent rectangle with rounded corners, only on the panel it
    would land on, sliding from target to target (`snap.rs`): **the panel**
    (anywhere in its middle), **a half** (within 22 % of its left, right or
    bottom edge; the band on the hinge side that is not the hinge's own is
    its inner half; only when another window is there to share it with),
    **the other panel** (within `snap_hinge_band`, 40 px, of the hinge-side
    edge, or in the gap: the picture itself goes over to the other panel
    ahead of the finger, 16 px inside its hinge-side edge, so the window is
    seen crossing although the finger cannot follow), **both panels** only
    on the **span pad**: a rounded accent pad with a two-panels icon drawn
    across the hinge while something is carried, from 58 % to 80 % of the
    panels' height (`snap_pad_from`, `snap_pad_to`), the hinge band's width
    on each side; it lights up under the finger. Let go, it flies from the
    picture to its new place on a spring, from the lift on: the picture's
    own spring goes on to the new place at its speed, nothing waits (the
    client is configured at once; its old buffer is stretched until the new
    one comes). The top half has no band of its own (the window is carried
    by its top): the other window goes to the bottom instead. **Before 2026-11-08** the hinge band itself
    was "both panels", and since a finger carrying a window to the other
    panel lifts exactly there (the touchscreens are separate), every such
    carry ended up spanning (the owner's report); the target showed on both
    panels and the picture stopped at the edge.
  - **Across the hinge**: the two touchscreens are separate devices, so the
    finger lifts on one and may come down on the other. A carrying finger
    that lifts within `stitch_edge` (56 px) of its panel's hinge-side edge
    puts the window down AT ONCE where the preview showed (the other panel)
    and it flies there from the lift on (`stitch::lift`); for `stitch_ms`
    (350 ms) a finger that comes down within `stitch_edge` of the OTHER
    panel's hinge-side edge, within `stitch_offset` (120 px) along the
    hinge, picks it up again from where its flight has got to and carries
    on (`windrag::Landing`; a finger that touches both edges at once hands
    over too), and its hinge band is its own panel until it has left the
    band once (`snap::Context::arrived`: a finger that lands at the edge and
    lifts is not sent back). Until 2026-11-10 the drop WAITED those 350 ms
    and only then grew into its place (the owner: the zoom started after a
    short delay; the journal: 360 ms from `let go at the hinge` to `carried
    window dropped`). A picked-up-again window now grows a little towards
    the other panel and shrinks back under the finger (one spring, no
    jump). Each drop's flight writes `drop: frame timing` per panel (the
    rotation's log, `turnlog.rs`; `first_ms` = the lift to the flight's
    first frame on the glass). An overview card let go at the hinge is
    dropped at once the same way (2026-11-12, `stitch::lift`; it waited the
    350 ms until then): it flies from where it was let go to its place in
    the other panel's grid, and a finger at the other edge in time picks it
    up again from there (`taskview::CardLanding`). The old numbers (48 px, 150 ms,
    90 px) were for synthetic contacts; with the owner's fingers nothing
    came down on the other panel within 150 ms (the journal of 2026-11-08
    07:33 and 07:35). Live: `duoctl set_stitch edge 60 ms 400 offset 140`, `duoctl
    set_snap pad_from 0.5 pad_to 0.8`.
  - **Double tap on the top**: one panel <-> both panels (back to the panel
    it came from). A header bar's own double click is GTK's "toggle
    maximized", sent as `unset_maximized`: duo-wc reads it as this toggle
    when the window was touched in the last 0.9 s and is older than 3 s (an
    app restoring its state at start is not a toggle); bar-less windows get
    it from the top strip (two taps of under 280 ms, the second lifting
    within 420 ms, 40 px apart at most). Whichever comes second within
    700 ms is ignored. Qt sends `unset_maximized` right before
    `set_fullscreen` (its window state loses Maximized as it gains
    FullScreen), so a video put full screen after a tap was read as the
    toggle and the window spread over both panels for a moment (Angelfish,
    the owner, 2026-11-12): the client's toggle now waits 40 ms
    (`TOGGLE_SETTLE`) and is dropped when a full-screen request came with
    it, and is never read while the window is full screen
    (`windrag::unmaximize_is_toggle`).
  - **Home**: a long swipe up from the bottom edge sets the panel's windows
    aside (minimized: not drawn, not touched, `suspended`, a frame a
    second at most: "Frame pacing"; still in
    the overview, the dock, the event stream's `windows` with `minimized`);
    they shrink towards the bottom middle while fading. The shell's home
    screen (a background-layer surface) is what shows there; the state's
    `home` names those panels. Activating a window (the dock, the overview, a
    launcher's token, `place_window`) brings it back growing out of the
    bottom; a new window opens normally. `xdg_toplevel.set_minimized` sets a
    window aside alone. `duoctl home DSI-1`.
  - **Animations** (`animations = on`): a new window grows in (92 % -> 100 %,
    fading in), a dropped or toggled window flies to its place, home and back
    as above; a tap on an overview card grows the window out of its card.
    On a desk (a display's, laptop mode's desk panel) these aim at the
    window's own frame, at the size it drew (`desk::picture_rect`): until
    2026-11-15 they aimed at the panel's whole room, so a window opening on
    laptop mode's desk grew in over the whole panel and jumped to its own
    size when the spring came to rest (the owner: "it starts full size and
    jumps back to a window"). The first configure was already the frame's
    size (WAYLAND_DEBUG: Text Editor, Firefox, Duo Settings drew it).
    Damped springs (`spring.rs`, closed form, retargetable without a jump),
    drawn from the windows' own buffers (scaled, cut), every frame until at
    rest.
  - **Tent and brochure** (stood up, hinge past ~300 degrees): the postures
    `tent_*` and `brochure_*` cannot say which panel faces the user, so
    duo-posed keeps the panel in use (single); a **double tap on the dark
    panel** switches the single pose to it (it wakes, the front window
    comes along, the other goes dark): two taps of under 350 ms, the second
    going down within 400 ms of the first lifting and within 60 px of it
    (`pose::DoubleTap`; a single tap switched too easily, the hand holding
    the stood-up phone touches the back panel). Folded back (`flip_*`): the
    back panel's touches stay ignored.
  - The rounded boxes (preview, shadow, the arrow's ring, the overview's
    card frames and corners) are one small GLES pixel shader (`fx.rs`;
    rounded-box distance, border, soft edge, or the corner mask).
- **Two windows on one panel** (`split.rs` pure, `tiling.rs` live;
  milestone 5). A panel can be shared by two windows, **side by side or one
  above the other**; otherwise the phone rule stays (one window per panel,
  the others behind it). Made from the task overview (a card dropped on a
  half of its panel, below) or over the control socket (`snap_window`,
  `duoctl snap 3 left`); ended by dragging the divider to the panel's edge,
  `duoctl unsplit DSI-1`, the window closing, or placing one of the two
  elsewhere explicitly (`place_window`, the overview's move across).
  - Each window is configured to its half of the panel's room (the panel
    less its status bar and keyboard) and **cut to it** when drawn (its
    popups are not), so a client that draws bigger does not spill over; a
    window's xdg `min_size` moves the divider when the room allows (Text
    Editor will not be narrower than 360 px: next to it a terminal gets
    166). Halves only: a quarter of a 538 x 757 panel is too small for
    any app.
  - The pair is **raised together**: touching either brings both to the
    front with the touched one focused (keyboard, on-screen keyboard). A
    new window opens in front of the pair, filling the panel, as before;
    the pair stays a pair behind it (as on Android).
  - **The divider** (a 12 px black gap with a light pill handle; blue while
    dragged) is the compositor's, shown while the pair is the front of its
    panel: a finger within 22 px of its line drags it (never delivered to a
    client), neither half under 20 %; let go with a half squeezed under 10 %
    and the split ends, the other window filling the panel.
    `duoctl split_ratio DSI-1 0.4` sets it.
  - **On the glass**: a split is kept in the panel's unrotated frame, so
    turned 90 degrees what was left | right reads top | bottom and the
    windows stay where they are on the panel (the mapping is checked
    against the touch mapping, `split::orient`).
  - **Poses**: a split is in force while both windows are placed on its
    panel. `span` spreads only the front window (its partner fills its
    panel behind it), `single` folds the other panel's windows onto one;
    back in `dual` the windows return and the split is there again.
  - `get_state` shows each window's `tile` (left / right / top / bottom)
    and `splits`; the event stream's state has `splits` (`changed`:
    `split`).
- **Task overview** (`overview.rs` pure geometry and gesture rules,
  `taskview.rs` the live part, `icons.rs` its buttons). Opened on EVERY lit
  panel at once by a swipe up that holds, the status bar's windows button,
  the dock, or `duoctl overview`. Each panel shows its own windows (front
  first; on the first lit panel also spanning windows and windows of a dark
  panel) as cards below the status bar, in a grid of 1 to 3 columns, scaled
  alike (at most 0.6), the front window framed in blue; room is kept at the
  bottom for the dock. The pictures are the windows' own buffers scaled into
  the card (`RescaleRenderElement` + `CropRenderElement`), so they stay live;
  the close (X) and move (arrow towards the other panel) buttons are drawn by
  duo-wc into memory (no image files, no font). On a card:
  - **tap**: switch to it (front of its panel, which becomes the active one;
    a window of a dark panel comes to this one), the overview closes;
  - **swipe it up** (35 % of its height or 90 px), or the X: the window is
    asked to close (`xdg_toplevel.close`; its card hides for 2 s, and comes
    back if the app stays, e.g. with "Save changes?"). On the LOWER of two
    stacked panels (the phone on its side) up is also the way to the other
    panel: a swipe up whose finger comes within `stitch_edge` (56 px) of
    the panel's top edge goes on as a carry (`overview::dismiss_goes_across`,
    the log's `a card swiped up to the hinge: carried across`): the card
    goes over ahead of the finger, a lift there puts it on the upper panel,
    back down it is dropped on its own panel; let go short of the edge it
    is the close as before. Until 2026-11-15 a drag up there was only ever
    the close: the owner's cards carried up to the other panel closed their
    windows (the journal: `asking the window to close` a second after each
    touch);
  - **carry it to the hinge** (within the hinge band of the inner edge,
    the edge facing the other panel), or the arrow: the window moves to the
    other panel and the overview stays open, so it shows up in the other
    grid. Carried there, the card itself slides over to the other panel
    ahead of the finger and the target shows on that panel only (the same
    snap targets as a carried window, `snap.rs`); a finger that comes down
    on the other panel carries on with it (the stitching), and it may go to
    a half there. Both panels only on the span pad;
  - **carry it to a half of the panel** (let go within 30 % of the area's
    left, right or bottom edge; a see-through blue half shows where it
    goes), or **hold it still 350 ms** to pick it up (a blue frame) and
    then carry it anywhere, the top too (a quick swipe up still closes):
    the window shares the panel with another one there (a split, above:
    with the panel's front window, or it changes sides in its pair, or
    replaces the one on that half), and the overview closes on the result.
    Only when the panel has another window to share it with.

  Above each card's picture is a 30 px band with the **app's icon and the
  window's title**: duo-wc keeps the band and publishes it on the event
  stream (`overview_cards`: panel, window, rectangle, `dragged`), the shell
  draws it (duo-wc has no text rendering; GTK has fonts, icons and
  translations) on a see-through overlay surface; a tap on the band is a
  tap on the card. **Opening**, the cards fly from their windows' places
  to the grid in 180 ms (eased out; the buttons and the frame appear when
  they settle).

  **The canvas** (2026-11-07 evening): with two panels, windows that span
  both get a row of their own across the top of both panels, their cards
  over the hinge like the windows (stacked panels: a column down the left
  of both, across the hinge, the panels' own cards to its right; until
  2026-11-15 they got the row, which sat on the lower panel and left the
  upper one's cards no room at all); a card **carried** goes to the same snap
  targets as a window carried by its top (`snap.rs`, with the overview's
  areas as the zones: a panel, a half, the other panel at the hinge, both
  on the span pad), across the hinge too (the same stitching); the target
  shows as a rounded accent area on the panel it lands on. The carried card
  is drawn on every panel it is over and slides across the hinge ahead of
  the finger when the other panel is the target (on springs, 2026-11-08;
  before, it was drawn only on the finger's panel and stopped at the
  edge). A card dropped on the other panel moves the window there and the
  overview stays open; a half or both panels close the overview and the
  window flies from where it was let go. Cards have rounded corners (a
  corner mask in the background colour) and the front one a rounded accent
  ring. The round buttons (close, move) sit `BUTTON_INSET` in from the
  picture's top edges, three quarters of the corner radius (10.5 px; 6 px
  until 2026-11-12, when a button nearly met the rounded corner and seemed
  to stick out of the card).

  **Closing is a request** (the owner, 2026-11-07): the X or a swipe up
  sends `xdg_toplevel.close`, and the card stays, dimmed, with "Closing..."
  (the shell's label) until the window really goes. If the app answers
  (it draws something and is still there 400 ms after the request, e.g.
  Text Editor's "Save changes?" inside the window, or it opens a dialog, a
  toplevel with this window as its parent), the overview closes and the
  window (with its dialog) comes to the front on its panel. If it answers
  nothing for 2.5 s, its label says "Not responding" and a red "!" button
  sits in the middle of its card: **force close**, which the shell confirms
  first ("Anything it has not saved is lost") and then asks duo-wc
  (`force_close_window`): SIGTERM to the client's process (from the Wayland
  socket's credentials; never duo-wc itself, never pid 1), SIGKILL 3 s
  later if the window is still there. Every close duo-wc sends (the
  overview, `duoctl close_window`) follows these rules. A dialog (a toplevel
  with a parent) opens on its parent's panel.

  A tap on the background, Escape, a new window, an activated window, the
  screen going off or `duoctl overview close` closes it. While open it takes
  its panels' touches below the status bar except those on overlay surfaces
  (the dock, banners, the quick menu), holds the keyboard focus off the
  windows (the on-screen keyboard goes away), and the windows themselves are
  not drawn there. The event stream's state has `overview`.
- **Android apps** (`android.rs` pure, `waydroid.rs` live; Waydroid's
  multi-window mode, `docs/hardware/android.md`). Every Android task
  is a toplevel of its own (`waydroid.<package>`), but all of them share
  ONE Android display: each window's surface is that whole display (a
  see-through 1x1 background stretched over it) and the task's layers are
  subsurfaces at their places on it; Android runs every task in its
  freeform mode there (a medium window with Android's own caption bar:
  back, minimize, maximize, close), and Waydroid adds a layer's current
  place on the Android display to every touch it passes on.
  - **Touches follow the surface as it is now.** Smithay hands a touch's
    motion to the client relative to where the touched surface was at the
    touch-down. Android moving a task under the finger (its caption bar
    dragged) moved the subsurface, Waydroid added the NEW place, so
    Android saw the finger ahead by every step the task had moved, and
    moved it further: the window slid away from under the finger (the
    owner, 2026-11-08; measured: a 202 px synthetic drag moved Android
    Settings 788 px). Now every touch remembers the surface it went down on
    and that surface's place then (`TouchSlot::surface`), and its motion
    goes out relative to where the surface is now
    (`client_touch_location`); right by the protocol for every client.
  - **Carried by duo-wc.** Android can only move a task within its display,
    which is this window: one panel. When a task's layers move (the same
    size, somewhere else: `android::task_moved`) while a finger that went
    down on it has moved 6 px, Android is moving it with that finger:
    duo-wc takes the drag over (the client gets the lift, Android's move
    ends where it is) and carries the window like any other: to the other
    panel, a half, both (the span pad), across the hinge, in the overview
    too.
  - **Android's keyboard** is a window of its own, `waydroid.InputMethod`
    (the size of the display, the keyboard at its bottom; a Waydroid
    window, not the squeekboard: Android does not use text-input-v3). It
    opened by the open rule like a new app, on the OTHER panel (the
    owner's report). Now it gets the placement of the Android window being
    typed in (the one with the keyboard focus, else the front-most Android
    app: `android::ime_companion`), so its part of the shared display lines
    up with the app's, and stays in front of it (every raise puts it back
    on top); it follows the app when that moves. It is not a window to the
    user: no overview card, not in the event stream's windows (the dock),
    no foreign-toplevel entry, never a panel's front window (the app keeps
    the keyboard focus), no grow-in; `get_state` lists it with
    `"accessory": true`.
  - **Full screen.** Android's caption bar is drawn by Android into the app
    (an app that draws to its top edge, like the Play Store, is covered by
    it), Waydroid never asks for xdg fullscreen, and Android's own maximize
    makes the task a fullscreen one, which hides every other Android app
    (measured: the Play Store launched behind a maximized Settings stayed
    `visible=false`, without a window, until Settings was restored). A
    **double tap on the middle of the caption bar** (the middle 40 %: the
    back button is at its left, minimize / maximize / close at its right)
    makes the window duo-wc's full screen: the whole panel (no status bar,
    like any fullscreen window), moved up by `android_caption` px so the
    caption bar is above the glass (cut there: never drawn on the other
    panel, never touched), and the task sized to the whole Android display
    inside Android while staying freeform (the other Android apps keep
    their windows) by `android/duo2-android-task` (root, through `waydroid
    shell am task resize`; the GUI user may run it with `sudo -n`; it keeps
    the medium bounds to restore). Back: a double tap on the window's top
    strip (it is a window without a title bar now: touch and hold there
    carries it). Android's keyboard window takes the same rectangle.
    Measured (2026-11-08): a freeform task resized to the whole display
    leaves the other Android apps visible (Play Store `visible=true`), and
    Android 13 lays the app out UNDER its caption bar (`dumpsys activity
    top`: `android:id/content 0,0-1345,1772`, `android:id/caption
    0,0-1345,106`, a caption-bar inset that only some apps honour): 106 px
    = 42.4 logical, hence `android_caption = 43`. Without the helper's
    sudoers rule (`sudo -n -l` fails) the double tap does nothing but a log
    line. **It undid itself until 2026-11-12** (one display, below): the
    taller full-screen rectangle resized the Android display, Waydroid made
    the window again, and the new window was placed as a new one (the
    journal: `Android window full screen`, `toplevel gone` 30 ms later, two
    new toplevels on the panel). Spanning, a double tap on the caption
    makes it full screen across both panels.
    **An app left in full screen opens in full screen**: Waydroid never
    tells the compositor that an app wants full screen (its hwcomposer has
    no such request, the HAL interfaces neither: a game's immersive mode is
    not seen), so duo-wc keeps the packages the user put there in
    `$XDG_STATE_HOME/duo-wc/android-full` (`~/.local/state/...`, one per
    line), and a new window of one goes full screen at once; leaving full
    screen (the top strip's double tap) forgets it. The way out is the
    usual one over a full-screen app: a swipe down from the top (the quick
    menu), a long swipe up from the bottom (home).
  - **One Android display, one size** (`android.rs`, 2026-11-12). Waydroid
    sizes its one Android display to the last configure ANY of its windows
    got (its hwcomposer, lineage-20: `xdg_toplevel_handle_configure`,
    `choose_width_height`, `do_hotplug`), and after a display of a new size
    it destroys every one of its toplevels and makes new ones for the same
    tasks (`cleanup_stale_windows`). That undid every span (the owner,
    2026-11-12: "Android apps cannot be put on both panels"; the journal:
    `window placed placement=Span`, 30 ms later `toplevel gone` for both
    Android windows, the new ones by the open rule on one panel, and
    their configure resized the display back), every full screen and every
    half, and two Android windows of two sizes would resize the display at
    every configure, each resize making them again: a loop. Now:
    - a new Waydroid toplevel whose app id went within 3 s
      (`RECREATE_MS`) is that window again (`take_gone`): it keeps its
      placement, span, full screen, the task's fill, being set aside, its
      split and its place in the stacking, with no grow-in;
    - every Waydroid window (and Android's keyboard) is configured to ONE
      size, the lead's (`display_size`): the spanning / full-screen / half
      window laid out last, else the one laid out last (a window just gone
      still counts while it may come back). The others show their part of
      the bigger display on their own panel; a new app whose task Android
      placed on that bigger display is moved inside its own window
      (`duo2-android-task fit`). Of the big windows only those of the
      lead's size stay big (`conflicts`): spanning one app ends another's
      full screen, which goes back to its panel. Android's keyboard is
      laid out across the whole display, so while a big app leads it goes
      where that app is;
    - **a spanning task fills the display** inside Android (`wants_fill`,
      the helper's `fill`), so the app covers both panels with the hinge
      gap as for a Linux app, touches mapped the same way (the window's
      surface is the display); back on one panel it gets its medium bounds
      (`restore`). The helper's calls run in order on one thread.
    Spanning: carry the window by Android's caption bar to the span pad,
    or the overview's card to it; back the same way to a panel. Two
    Android apps share one panel side by side only at equal halves (a half
    is the display's size then).
- **Touch.** duo2-touchd's two uinput devices ("Surface Duo 2 Touchscreen
  Left/Right") carry `WL_OUTPUT=DSI-1/DSI-2` from the udev rule; libinput
  returns it as the device's output name, and a touch is the device's
  normalised position on THAT output's rectangle, turned with the output's
  rotation (`rotation.rs`). Touch points use libinput's
  seat-wide slot, so the two devices never collide. The kernel's own devices
  for the same digitizer ("spi 045E:0C1D Touchscreen", single touch, both panels
  folded into one space; and its "Stylus") have no output name and are switched
  off in libinput (`SendEventsMode::DISABLED`); a touch from any device without
  an output is dropped anyway. Touch-down makes that panel the active one,
  raises the window the touched surface belongs to (with its split partner)
  and gives it the keyboard; a touch on a layer surface (the dock, a bar,
  the keyboard) raises nothing (it used to raise the window under it, and
  the shell's dock, rebuilt for the changed window list, lost that tap).
  Layer surfaces are hit by their input region, every surface of a layer
  in turn, so a see-through one passes touches on. There is no pointer
  unless something drives one: a plugged mouse, an external display's
  touchpad panel, laptop mode's touchpad ("Laptop mode" below).
- **Keyboard.** The phone's keys (gpio-keys, pmic) and anything plugged in go to
  the active panel's front window, except the power key (below) and the
  volume keys. While the screen is dark a key press is dropped, and so is its
  release. Ctrl+Alt+Backspace quits.
- **Volume keys** (KEY_VOLUMEUP from `gpio-keys`, KEY_VOLUMEDOWN from the
  PMIC's `pmic_resin`, KEY_MUTE from a plugged keyboard) are the shell's:
  never delivered to a client, announced on the event stream, press and
  release (`{"event":"volume_key","key":"up","state":"pressed","output":"DSI-1"}`;
  the shell repeats while a key is held). `output` is the panel for the volume
  display, the active one while anything is lit, and `null` while the screen
  is dark: **the keys work in the dark and do not wake the screen** (the phone
  convention: the volume changes, nothing lights up). While lit a press
  restarts the idle timer. duo-shell changes the default sink's volume and
  shows it (`shell/duo-shell` README).
- **Panel power** (`backend.rs` `set_power`, `power_off_together`,
  `wake_together`). **Panels go off in ONE non-blocking atomic commit** of
  duo-wc's own (`atomic_off = on`, the default): every plane on their CRTCs
  detached, each connector off its CRTC, each CRTC inactive without a mode,
  so the DSI encoder is disabled and the panel driver runs its off sequence
  (display off, sleep in, reset held, its share of the supplies released);
  TEST_ONLY first, then NONBLOCK with a page-flip event per CRTC, which the
  msm driver sends when the panel is off (~330 ms for both, in the kernel's
  commit worker). `set_power all off` answers in 3-5 ms (it took 335-346 ms
  before). A frame or a wake still in flight is waited for first; a panel
  asked on while it is going off wakes when the off commit is done; a
  refused test falls back to Smithay's blocking `DrmCompositor::clear`
  (~170 ms per panel), which `duoctl set_atomic_off off` also selects.
  **Panels come on with one blocking modeset each** (`atomic_wake = off`,
  the default since 2026-11-08; ~215 ms a panel, its first frame in it, the
  left lit just before the right). With `atomic_wake = on` they come on in
  **non-blocking atomic commits** of duo-wc's own, one panel per commit, the
  next when the one before is awake: the CRTC active with its mode, the
  connector on it, no planes; tested with TEST_ONLY first, then committed
  with NONBLOCK and a page-flip event. The call returns in ~0.1 ms; the
  kernel runs the panel's power-on sequence in its commit worker (~210 ms a
  panel, ~450 ms for both) while the event loop goes on; the first panel's
  event starts the next one's commit (`wake_queued`). That is why it is off:
  a panel left on its black first frame (no planes) while the other panel
  came up often stayed dark (the left; seconds, until a new frame or a
  brightness change), although every frame went out and the display
  registers were as on a good wake (`tools/wake-capture.py`). Both panels in
  ONE commit (until 2026-11-08): 5 of 7 dark; one commit per panel: about 2
  in 3; the left alone through a wake commit and one modeset per panel: 4 of
  4 good each. On each CRTC's event Smithay re-reads the
  CRTC state (`reset_state`), so its first frame is a plain page flip; a busy
  display (EBUSY, the two first frames meeting) is retried after 8 ms; if an
  event never came, a 1.5 s timeout goes on anyway; a refused test falls back
  to the old way (one blocking Smithay modeset per panel, ~215 ms each),
  which `duoctl set_atomic_wake off` also selects.
  **Together to the eye** (2026-11-07, the owner: the panels went dark and
  came back one after the other): going dark, both panels first show a
  black frame (an OLED's black is unlit; `start_blackout`), and the off
  commit goes out only when both show it (9-13 ms after the request; at
  most 250 ms), so the kernel's sequential off is not seen. Waking with
  `atomic_wake = on`, a panel awake before the other is not drawn (its CRTC has no planes: black) and
  both get their first picture in the same turn when the last wake event
  is in: measured 447/448, 435/438, 466/467 ms (DSI-2/DSI-1) after the
  commit, 1-3 ms apart (`panel on: the picture on screen (with the other
  panel)`). A dark panel renders
  nothing; its windows are `suspended` and get a frame callback a second at
  most ("Frame pacing" below); a spanning window is paced by the first lit
  panel.
  **Both, also in the corner cases** (2026-11-08): the power key pressed
  again while the panels are still going dark used to wake each panel on
  its own as its off commit finished (two commits); now the last off to
  finish wakes every panel asked on meanwhile in one commit
  (`power::wake_after_off`). Waking from the peek strip (the power key, or
  opening the phone: the strip lights DSI-2 only) the lit panel keeps the
  strip until DSI-1 is awake, and the picture comes on both in the same
  round (`power::hold_for_wake`, `wake_together`'s `hold`). The folded
  rules stay: closed + power key = the glance on DSI-2, a tent or folded
  back = the one panel in use.
  **The first picture after a power-on is committed twice more** (30 ms
  and 150 ms after it is on screen, each waiting while the other panel's
  first picture is still to come, `power::REPEAT_SOON` / `REPEAT_LATER`):
  on a still screen nothing else draws there again, and a panel that
  missed its first picture stayed black (2026-11-08 on a try kernel: after
  a power-key wake both CRTCs flipped duo-wc's picture, the left panel
  stayed black until a tap launched an app). An empty frame (nothing
  changed for Smithay) on a panel that has not had its picture since it
  came on is drawn whole instead.
- **Who decides** (`power.rs`, pure). A panel is lit when the pose uses it, it
  was not switched off by hand (`set_power DSI-2 off`), and the screen is not
  blanked. Blanking (idle timeout, power key, `set_power all off`) switches
  both off; waking brings back exactly what was lit before, or everything the
  pose uses if that would be nothing (both had been switched off one by one).
  `set_power DSI-1 on` from a blanked screen lights that panel only.
- **Proximity hold** (2026-11-07 night, `power.rs` `ProximityHold`,
  `set_proximity`, trusted only). During a call on the earpiece the shell
  holds the panel the call is on dark while something is near the sensor
  (`duoctl set_proximity DSI-1 [ttl MS]`; none = the hold ends): the panel
  goes off as if switched off, its touches are not delivered (and wake
  nothing), but it is not a blank: `screen_dark` and the power key ignore
  it, the shell does not lock, the idle timer pauses while nothing else is
  lit, and the panel comes back as it was. The hold ends by itself after
  `ttl_ms` (default 30 s, at most 120 s) unless renewed (the shell renews
  it every 10 s), so a dead shell cannot leave a panel dark. `get_state` and
  the event stream carry `proximity` (the held panels, also a `changed`
  name); `lit` leaves them out.
- **Ring hold** (2026-11-08, `power.rs` `RingHold`, `set_ring`, trusted
  only). While a call rings in, the shell says how (`duoctl set_ring
  loud|silent|off [ttl MS]`): `loud` (its ringtone and vibration: a key
  silences it), `silent` (silenced, or waiting beside a call that is up),
  `off`. While `loud`, the power key's first short press **silences** the
  call instead of switching the screen: `{"event":"ring_silence","key":
  "power"}` goes to the shell (which asks GNOME Calls to silence it; the call
  goes on ringing silently, it is not declined), `ring` becomes `silent`,
  and a lit screen stays as it is (the call window, the lock screen's card,
  the glance or peek strip that shows who calls); a dark one also comes on
  as it would (wake, or a closed phone's glance). The next press does what
  it always does (`power::ring_power_key`). While a call rings at all, a
  **glance stays lit** (its 5 s timer looks again every 5 s instead of
  blanking) and the **idle timer does not blank**; when the ring ends the
  glance gets its usual 5 s from then and the idle timer counts from then.
  The hold ends by itself after `ttl_ms` (default 30 s, at most 120 s)
  unless renewed (the shell renews it every 10 s), so a dead shell leaves no
  power key that silences nothing. `get_state` and the event stream carry
  `ring` (`loud` / `silent` / null, also a `changed` name).
- **Idle blanking.** After `idle_timeout` seconds (default 60, 0 = never) with
  no delivered touch or key, both panels go off. Set in
  `/etc/duo-wc/duo-wc.conf` and live with `duoctl set_idle_timeout N`. One
  calloop timer, re-armed at its deadline, not on every touch. An app can
  hold it while it is seen (a video, a map): "Idle inhibit" below.
- **Idle inhibit** (2026-11-09 night, `idle.rs` pure, `inhibit.rs`). Two
  sources hold the idle blank, and only that: neither wakes a dark screen,
  holds the lock screen or the home screen, or stops the power key (or a
  pose, a fold, `set_power`).
  - **zwp_idle_inhibit_manager_v1** (public: mpv, Firefox, Chromium, any
    client): an inhibitor holds while its surface is SEEN: its window (the
    root through subsurfaces and popups) is on a lit panel that shows
    windows (not locked, no peek or glance), not set aside (home), and not
    covered whole by the windows in front of it on that panel (window
    geometry cut to the panel and to a split's half; an uncovered piece
    under 8 px across does not count, `idle::seen_through`). The task
    overview shows every window of its panels live: seen. A layer surface's
    (the shell's) holds on a lit panel. One made while dark does nothing
    until its surface is seen again (the protocol's rule). A dead client's
    inhibitors are forgotten with its surfaces (Smithay calls `uninhibit`
    only for an explicit destroy).
  - **The shell's idle hold** (`set_idle_hold`, trusted only): duo-shell
    serves org.freedesktop.ScreenSaver on the session bus (Chromium's and
    Firefox's D-Bus path, and every GTK4 app through the Inhibit portal,
    whose GTK backend falls back to it: shell/duo-shell README) and sends
    the holders with their callers' process ids, renewed every 10 s; the
    hold ends by itself after `ttl_ms` (default 30 s, at most 120 s). A
    holder whose process has windows holds while one of THOSE is seen (a
    browser covered by another app does not); one without windows of its
    own (the portal backend, which speaks for the app; an unknown pid)
    while any app window is seen (`idle::hold_honored`).

  Who holds is evaluated after every loop turn that did something (a few
  windows: cheap) and again when the idle timer runs out: while anyone
  holds, the timer counts again from then (like a ringing call); when the
  last one lets go (destroyed, covered, set aside) it counts from that
  moment. `get_state` and the event stream carry `idle_inhibit` (who holds
  now: the window's app id, the hold's application name; also a `changed`
  name), the log says `idle inhibited: ...` / `idle no longer inhibited`.
  **ext-idle-notify-v1** (public; idle notifications for apps that want
  them: every delivered touch or key, and a wake, is activity; while an
  inhibitor holds, plain notifications do not fire, the input-only ones of
  version 2 do). `idle_inhibit = off` in the config (or `duoctl
  set_idle_inhibit off`, live) makes the idle timeout blank whatever holds.
  With the panels dark nothing of this keeps the phone awake: duo2-autosleep
  suspends unless something else holds it (power/duo2-autosleep: audio
  playing, a logind sleep inhibitor).
- **Dark panels take no touch.** A touch that goes down on a panel that
  shows no picture never reaches a client, nor does the rest of that touch
  point, down to its lift: a panel that is off, and (2026-11-08) also one
  that is lit for the power rules but whose picture is not on the glass yet:
  waking (~0.2 s alone, ~0.45 s both), held black for the other panel, or
  its first frame since it came on not flipped yet
  (`power::shows_picture`, `power::touch_on_panel`, `DuoWc::panel_shows`;
  the pen too). Before, a tap on the black left panel during (or after) a
  wake was delivered and launched the app under the finger. A panel that goes
  dark under a finger cancels the touch for its client (`wl_touch.cancel`).
  **Wake rule (decided): only the power key wakes**, as on a phone (a pocket is
  full of touches). `touch_wake = on` (config, or `duoctl set_touch_wake on`)
  makes a touch on a dark SCREEN wake it; the waking touch is not delivered.
- **Power key** (KEY_POWER from `pmic_pwrkey`, the PMIC's PON). A short press
  (released within 1 s) switches the screen off if anything is lit, otherwise
  on (while a call rings aloud the first press silences it instead: "Ring
  hold" above). Long presses are not bound at all (the PMIC's hardware reset lives
  there); the key never reaches a client. logind must leave the key alone: its
  default `HandlePowerKey=poweroff` would shut the phone down on every press,
  so `install.sh` installs `/etc/systemd/logind.conf.d/50-duo-wc-power-key.conf`
  (`HandlePowerKey=ignore`, `HandlePowerKeyLongPress=ignore`) and reloads
  logind. `power_key_suspend = on` would also suspend the phone after the
  panels go off (`systemctl suspend`; it needs polkit to allow the duo-wc user,
  and the WiFi link drops): **off by default and not tried on the phone**.
- **Poses** (`pose.rs`, pure; `duoctl set_pose`):
  - `dual`: two separate panels (the start).
  - `single DSI-1|DSI-2`: the other panel off, its windows moved onto the one
    that stays (behind that panel's own front window; a spanning window is
    folded onto it). `set_power` cannot switch the unused panel on, and
    `place_window` refuses it and `span`.
  - `span`: the front window of the panel touched last covers both panels
    (with the hinge gap as set); a window opened in this pose spans too.
  A pose moves windows away from their OWN placement and remembers it: every
  pose change first puts those windows back, then applies the new pose, so
  `single DSI-1` then `dual` returns the right panel's windows to it, and
  `span` then `dual` returns the spanned window to its panel. `place_window`
  makes the new placement the window's own. A pose change wakes the screen and
  lights the panels it uses. `get_state` shows `placement` and
  `own_placement` per window.
- **Rotation** (`rotation.rs`; `duoctl set_rotation DSI-1 0|90|180|270`). The
  output's `wl_output` transform: **90 = the picture is drawn 90 degrees
  counter-clockwise on the panel, so it reads upright with the device turned 90
  degrees clockwise** (the panel's left edge up); 270 the other way round, 180
  upside down. Smithay's renderer turns the picture (clients keep drawing
  upright buffers); touches are turned with the exact inverse of the
  renderer's mapping (unit-tested against Smithay's own transform code), the
  window is re-sized to the rotated panel (757 x 538 logical at 90/270), and
  screenshots come out upright. When both panels are turned alike the layout
  follows the device: 90 = DSI-1 on top of DSI-2, 270 = DSI-2 on top, 180 =
  DSI-2 left, with the hinge gap between them, so `span` spans across the
  hinge either way; turned differently they sit side by side without the gap.
  **The turn is animated the way phones do it** (`rotanim.rs`; reworked
  the same day after the owner's "it stretches, and it lags"): before the
  change every lit panel's picture as shown is rendered into a texture (the
  OLD picture; both panels: turning one may move the layout of both); for
  `rotate_anim_ms` (320) it turns, eased in and out (Material's standard
  curve), from where it stood on the glass to upright in the new frame,
  scaled UNIFORMLY so that at every angle it fits inside the panel (black
  around it, never stretched: a portrait picture ends letterboxed in
  landscape). The clients are configured at once; as soon as every window
  and layer surface of the panel has drawn at its new size, their picture is
  taken once (the NEW picture) and turns in with the same motion (same
  angle, its own fitting scale, it ends filling the panel) while the old one
  fades out over it in 120 ms; the fade starts then or at the end of the
  turn, whichever comes first. A client that changes its picture after that
  is taken again (at most every 120 ms, the last picture fading into the
  new one), so the last frame of the turn is the first frame of the scene.
  If the clients have not drawn at the new size by the end of the turn, the
  old picture stays at rest, letterboxed, until they have (at most 300 ms
  more), never a half-drawn scene. A panel whose own rotation did not change
  holds its picture 200 ms (duo-posed's second request of the pair comes
  within it and that panel turns too), then fades into the scene; a rotation
  during a turn turns the old picture on from where it is shown.
  **During the turn the frame is the two pictures and nothing else**: no
  client surface is imported or drawn, each frame is two textured quads,
  driven by the vblank loop at the panel's rate, its progress taken at the
  vblank it will be on the panel (the last flip plus whole refresh periods),
  and rendered one frame ahead (queued behind the frame in flight,
  DrmCompositor's queued frame: a frame that takes long to render is not
  late; `duoctl set_rotate_anim MS ahead off` turns that off). **The clients'
  buffers are held through the turn**: at a turn every client reallocates
  at the new size and destroys its old buffers, and letting go of an
  imported buffer frees it in the kernel (GEM close, 2-45 ms each, measured
  on the phone: the lag the owner saw, 10-50 ms frames inside
  `GlesRenderer::cleanup` in the frame's render); they are kept until the
  turn is over and let go 400 ms later, a few at a time between frames.
  The shader is compiled and the new picture's texture allocated before the
  first frame; picture textures are reused from turn to turn (four kept).
  Every turn logs its frame timing (`turnlog.rs`, one `rotation: frame
  timing` line per panel: frames, intervals between flips from the kernel's
  timestamps, missed vblanks, CPU time per frame and its parts);
  `tools/rotate-timing.py` (on the phone, root) turns both panels like
  duo-posed and prints them as a table. `duoctl set_rotate_anim 3000` slows
  the turn down (screenshots show it), `0` = the old jump.

- **Posture and the closed phone** (`ambient.rs`, pure rules). `duo-posed`
  tells duo-wc how the phone is folded (`set_posture closed|peek_left|
  open_dpm|...`, Microsoft's posture names, or a hinge class from the other
  sources); the state carries it as `posture` (the shell locks a phone that
  is folded closed). Two **ambient screens**, like the stock Duo:
  - **peek**: a posture `peek_*` while the screen is dark (the phone opened
    a little) lights the panels for the shell's peek strips (the clock, the
    battery, the notification icons on each panel's outer edge);
  - **glance**: the power key of a closed phone (posture `closed`, screen
    dark) lights ONLY the glance panel (`glance_panels`, DSI-2 by default:
    only its hinge-side edge shows from outside) for the glance strip (one
    line along the hinge edge) for 5 s, then it goes dark; another press
    ends it at once. While a call rings in it stays lit until the ring ends
    (then 5 s more): the shell's strip shows who calls;
  - **glance ruler** (`duoctl ambient glance-ruler`): the same, for 30 s,
    with the shell's numbered bands from the hinge-side edge inwards (to
    find the visible sliver). The peek lights `peek_panels` (DSI-2 since
    2026-11-07, like the glance; it lit both).
  **At once** (the owner, 2026-11-07: the peek took ~10 s and showed the lock
  screen's clock meanwhile): the shell keeps its `duo-ambient` surfaces
  mapped all the time (never drawn or hit outside an ambient screen), and
  duo-wc sends them frame callbacks the moment one starts (the shell
  redraws while the panel is still waking); until a panel's surface has
  committed a picture for THIS screen the panel is plain black
  (`render::Ambient::Waiting`), never the lock screen or home and never
  the last screen's stale clock. The log has `ambient strip drawn by the
  shell ms=` and `ambient strip on screen ms=` (from the posture): 240-310
  ms for a peek, 225 ms for a glance (the panel's wake alone is ~220 ms).
  While one shows only layer surfaces of the namespace `duo-ambient` are
  drawn, on black (also over a lock), touches and keys (except the power and
  volume keys) are ignored, windows are `suspended` and get a frame a
  second at most ("Frame pacing"); the state's
  `ambient` says which. Closing the phone ends it and blanks, opening it
  (another posture), a pose, `set_power ... on` or the power key during a
  peek end it with the panels lit (the lock screen, when locked). Without
  postures (duo-posed `enabled = off` or `forward_posture = off`) the power
  key does what it always did. By hand: `duoctl set_posture closed`,
  `duoctl ambient peek|glance|off`; `tools/key.py power` presses the power
  key through an evemu clone.
- **wp_presentation** (presentation-time): every frame queued carries the
  presentation feedback of the surfaces it shows (windows paced by that
  panel, its layer surfaces); on the page flip they learn the flip's kernel
  timestamp (CLOCK_MONOTONIC, `Vsync | HwClock | HwCompletion`), the
  sequence and the refresh (11.1 ms at 90 Hz). Chromium paces itself by it:
  its frame clock went from 60 to 90 Hz.
- **Frame pacing** (2026-11-16 evening; `pacing.rs` pure, `frames.rs`).
  Each window is paced by one output: its panel while lit, a spanning one
  by the first lit panel. When that output renders, the window gets its
  frame callbacks and its **wp_fifo_v1** barrier is released (Smithay's
  managed fifo holds a content update that waits on it): one update a
  refresh, the FIFO present mode without a busy loop. Mesa's Vulkan WSI
  (25.0) uses it, with **wp_commit_timing_v1**: an update held for a time is
  let go by a timer of its own a refresh early. SDL 3.2 chooses Wayland only
  from a compositor with wp_fifo_v1 (before this every SDL 3 app went
  through Xwayland). A window is **hidden** when nothing of it is drawn: its
  panel is dark, it is at home (the overview closed), every lit panel it
  shows on has a fullscreen window in front of it, an ambient screen shows,
  or the session is locked. A hidden window gets the xdg_toplevel
  **`suspended`** state (xdg-shell v6; also in its first configure: a window
  opened on a dark panel) and no frames at the panel's rate: a slow tick
  gives it its frame callbacks and releases its fifo barrier at most once a
  second, only after a commit asked for it (the first one at once), so a
  client that waits on them never hangs, one that ignores `suspended`
  draws once a second, and a dark phone whose clients are idle does not
  wake. GTK 4, Qt 6, mpv and SDL 3 (`SDL_EVENT_WINDOW_OCCLUDED`) stop
  drawing while suspended; X11 windows (no such state) get the slow frames
  alone. The log says `window hidden: suspended why=dark|home|covered|
  ambient|locked` and `window shown again`.
- **Lock screen, compositor side** (`lock.rs`, ext-session-lock-v1). While
  locked only the lock client's surfaces are drawn (one per panel; a panel
  without one shows the empty background), touches on a lit panel go to the
  lock surface there and the keyboard to the active panel's; no window, layer
  surface (bar, keyboard, notifications), overview or edge swipe shows or
  reacts. The power key still blanks and wakes. A rotation configures every
  lock surface again to its panel's new size (`reconfigure_lock_surfaces`,
  from `arrange_outputs`; 2026-11-08: a lock made upright and turned to
  landscape kept its upright size, a black bar at the right edge), and the
  rotation animation takes the lock screen's new picture only once the lock
  surface has drawn at that size (`lock_drawn_at_size`; before, it turned
  the old-size picture and jumped to the right size at the end). **Off by default**:
  `session_lock = off` does not even offer the global, so nothing can lock the
  phone; `duoctl set_session_lock on` until restart. `duoctl unlock` ends a
  lock whatever the client does (the way back over ssh); a restart of duo-wc
  comes up LOCKED again (flag `/run/user/UID/duo-wc-locked`, black until the
  shell's `duo-lock` takes over; a reboot clears it). The client is the shell's `duo-lock`.
  `duoctl unlock` tells the lock client (`ext_session_lock_v1.finished`):
  duo-lock answers `unlock_and_destroy` and exits, so the shell can lock
  again (it used to keep running and block every later lock); that
  answer never ends a later lock (a lock asked for while an ended lock's
  client is still there is refused).
- **Questions to the user** (`confirm`, 2026-11-07): an action only the
  session may take, asked for by someone who must not take it alone.
  Root (Duo Settings' helper: `restart-display`) and polkit
  (`confirm_users = polkitd`, a role that may make no other request:
  `remove-packages` for PackageKit) send `confirm`; duo-wc announces
  `confirm_request` (`id`, `action`, `detail`, `output`), the shell asks
  on the active panel and answers `confirm_answer` (`confirm`: the id,
  `yes`) on its trusted connection; `confirm_closed` when it is over.
  `restart-display`: on a yes duo-wc exits and systemd brings it back
  (the lock stays in the descriptor store). `remove-packages` (`wait`:
  true): the answer goes back on polkit's connection; no answer in 9 s is
  no. One question at a time (a new one answers the open one no).
- **Popups of layer surfaces** are fitted into their panel when the layer
  surface takes them (`WlrLayerShellHandler::new_popup`: before it they
  have no parent), so a popover at a panel's edge (the home screen's Bing
  card) slides inside.
- **Client teardown** (`teardown.rs`). A client that goes away (exits,
  is killed, restarts like duo-shell after an install) is destroyed by
  libwayland object by object, and each destructor runs Smithay's handler and
  ours at once, while the client's later objects still look alive. An event
  that names another object (`wl_keyboard.enter/leave`, the text-input's
  `enter/leave`) sent to that client then panics in wayland-backend 0.3
  (`InvalidId`, `server_impl/mod.rs:1142`, inside an `extern "C"`
  destructor: abort). So the destroy handlers (`toplevel_destroyed`,
  `layer_destroyed`) only forget the window or layer and leave the rest
  (re-placing, the keyboard focus, the redraw) to an idle callback, and a
  keyboard focus change that would leave or enter a client being torn down
  waits for one too. A panic is logged with its backtrace (`main.rs`; the
  unit sets `RUST_BACKTRACE=1`).

## Build (cross, on an x86_64 host)

```sh
./build-aarch64.sh          # -> out/duo-wc (release, aarch64)
./build-aarch64.sh test     # unit tests (layout, rotation, its animation and frame timing,
                            # pose, power, config, cli),
                            # natively in the same image
./build-aarch64.sh check    # rustfmt --check and clippy --all-targets -D warnings
```

Every commit keeps `check` clean (`rustfmt.toml` sets the width). An image
built before clippy and rustfmt were in the `Dockerfile` needs
`REBUILD_IMAGE=1` once.

The `Dockerfile` is `debian:trixie` with `dpkg --add-architecture arm64`,
`crossbuild-essential-arm64`, the Smithay `-dev` set as `:arm64` (and for the
host, for the tests), and rustup with `aarch64-unknown-linux-gnu`; so the binary
links against the phone's own library versions. The cargo registry is cached in
`~/.cache/duo-wc-cargo`, the target dir is `./target`. `JOBS=n` caps the jobs
(default 10); `REBUILD_IMAGE=1` rebuilds the image.

## Install and run (on the phone)

```sh
./install.sh            # copy binary, unit, session, duoctl; restart if running
./install.sh --start    # ... and start it now
./install.sh --enable   # ... and at every boot (not done by default)
```

`install.sh` uses `scripts/lib/remote.sh` (the phone from `local.env`) and
installs, if missing, `foot seatd libseat1 libdisplay-info2`. The shell and
the keyboard come with `shell/duo-shell/install.sh` (the session starts them
if they are installed). On the phone:

| path | what |
| --- | --- |
| `/usr/local/bin/duo-wc`, `/usr/local/bin/duoctl` | the compositor, the control client |
| `/usr/local/lib/duo-wc/session` | started by duo-wc with its trust factory on fd 3: the user's own D-Bus session bus (`/run/user/UID/bus`, the lingering user manager's; `dbus-run-session` only without it), PipeWire's sockets for the clients (`PIPEWIRE_RUNTIME_DIR`, `PULSE_SERVER`), the image's app environment (`/etc/environment.d/*duo2*.conf`), `duo-shell` and the on-screen keyboard (`osk`) kept running through `duoctl exec-trusted`, the hooks in `session.d/`; writes `/run/duo-wc/session.env` |
| `/usr/local/lib/duo-wc/osk` | starts the on-screen keyboard: phosh-osk-stub (`POS_DEBUG=force-completion`: completion on from the start) when it is installed and the running duo-wc has its globals (`DUO_WC_FEATURES` has `phosh-osk`), else squeekboard (`GTK_THEME=Adwaita:dark`); `~/.config/duo2/osk` (`squeekboard` or `phosh-osk-stub`) picks by hand; `osk --print` shows the choice |
| `/usr/local/lib/duo-wc/session.d/` | root's hooks the session runs once at every start, with its trust (duo-apps' Clocks alarms) |
| `/usr/local/lib/duo-wc/duo-wc.last-good` | the binary the last `install.sh` replaced, if it was running: the way back (`sudo install -m 0755` it over `/usr/local/bin/duo-wc`, restart) |
| `/usr/local/lib/duo-wc/hello` | what each terminal shows first, then `bash -l` |
| `/usr/local/lib/duo-wc/xwayland/Xwayland` | the wrapper duo-wc starts Xwayland through (`dist/xwayland-wrapper`): hands duo-wc's X display sockets to `/usr/bin/Xwayland` ("X11 apps") |
| `/etc/duo-wc/duo-wc.conf` | defaults (`hinge_gap`, `scale`, `idle_timeout`, `idle_inhibit`, `touch_wake`, `power_key_suspend`); never overwritten (a key it lacks takes its default) |
| `/etc/duo-wc/duo-wc.conf.dist` | the shipped config with every key documented; always replaced |
| `/etc/systemd/system/duo-wc.service` | the unit |
| `/etc/systemd/logind.conf.d/50-duo-wc-power-key.conf` | logind ignores the power key (duo-wc owns it); logind is reloaded when it changes |

Over ssh as the administrator (duo2admin: `duoctl` reaches the admin
socket, every request allowed); as the GUI user (an app, an ssh shell of
the GUI user, `DUO2_USER`) only the state, the event stream and
`expect_window` (see "Who may do what" below).

```sh
sudo systemctl start|stop|restart duo-wc
journalctl -t duo-wc -b             # the log (RUST_LOG=info,duo_wc=debug in the unit: touches too)
duoctl get_outputs                  # positions, modes, frames queued/presented per CRTC
duoctl get_state                    # windows (id, app_id, placement, geometry), gap, scale
duoctl set_hinge_gap off            # live
duoctl place_window span 1          # window 1 across both panels (or DSI-1 / DSI-2)
duoctl set_power DSI-2 off          # one panel off (on again: on); all off = blank
duoctl set_pose single DSI-1        # or: dual, span
duoctl set_rotation DSI-1 90        # 0 | 90 | 180 | 270
duoctl set_idle_timeout 300         # seconds, 0 = never (until restart; the config sets the start)
duoctl set_idle_inhibit off         # idle inhibitors (a video) do not hold the blank (until restart)
duoctl set_pen button off dot on    # the pen's barrel button and hover dot (until restart)
duoctl set_idle_hold test:1234      # what the shell sends for org.freedesktop.ScreenSaver (30 s)
duoctl set_touch_wake on            # a touch wakes a dark screen (until restart)
duoctl screenshot DSI-2 /tmp/r.png  # one output's scene, rendered off-screen (upright;
                                    # read back on the loop in 20-50 ms, the PNG
                                    # written in a thread: duo-wc goes on; it held
                                    # everything up ~4 s before 2026-11-07),
                                    # layer surfaces (bars, grid, keyboard) included
duoctl subscribe                    # print the event stream (Ctrl+C ends)
duoctl expect_window DSI-2          # the next new window opens on DSI-2
duoctl activate_window org.gnome.TextEditor   # or a window id
duoctl set_edge_swipe off           # every touch to the apps (until restart)
duoctl overview [open|close|toggle] # the task overview on every lit panel
duoctl close_window <id>|<app-id>   # ask a window to close (the overview's X)
duoctl set_atomic_wake off          # wake panels the old way (one blocking modeset each)
duoctl set_atomic_off off           # switch panels off the old way (blocking, ~170 ms each)
duoctl snap 3 left                  # window 3 on the left half of its panel, the panel's
                                    # front window on the right (also: right, top, bottom;
                                    # "with 5", "on DSI-2"; an app id instead of 3)
duoctl split_ratio DSI-1 0.4        # the left / top window's share (0.2 to 0.8)
duoctl unsplit DSI-1                # both windows fill the panel again
duoctl focus_output DSI-2            # that panel active, its front window gets the keyboard
duoctl set_posture closed           # what duo-posed sends (closed, peek_left, open_dpm, ...)
duoctl ambient peek|glance|off      # the closed phone's screens, by hand
duoctl set_ring loud                # a call rings (the shell sends it): the power key silences
duoctl home DSI-1                   # that panel's windows aside: the home screen
duoctl toggle_span 3                # one panel <-> both panels
duoctl move_window 3 "DSI-2 left"   # as if carried there (DSI-1, DSI-2 [side], span)
duoctl force_close_window 3         # kill its app (SIGTERM, then SIGKILL)
duoctl set_back_swipe outer         # which side edges go back (both | outer | off)
duoctl set_stitch edge 60 ms 220    # the hinge stitching thresholds (until restart)
duoctl set_snap hinge_band 50       # the snap thresholds (until restart)
duoctl set_animations off           # no springs
duoctl set_session_lock on|off      # offer ext-session-lock (off by default)
duoctl set_rotate_anim 3000         # a turn takes this long (ms; 0 = it jumps; 320 by default)
duoctl set_rotate_anim 320 ahead off  # ... its frames not rendered one ahead (on by default)
sudo python3 rotate-timing.py 90 0  # (tools/, copied over) turn both panels, print each
                                    # turn's frame timing from the journal
duoctl confirm restart-display      # ask the user in the shell first (root only)
duoctl confirm_answer 3 no          # the shell's answer to question 3
duoctl unlock                       # end a lock whatever the lock client does
sudo -u "$(systemctl show -p User --value duo-wc)" \
    sh -c '. /run/duo-wc/session.env; foot &'   # an app on the panels (as the GUI user)
                                    # (remote.sh: duo2_session 'foot &')
duoctl exec-trusted --wayland -- /usr/local/lib/duo-wc/tools/toplevels.py
                                    # a TRUSTED Wayland client (sees the guarded globals)
```

### The pen (2026-11-08 night; 2026-11-15: ready for a real pen; 2026-11-16: drawing apps, the compositor's UI)

`src/tablet.rs`. duo2-touchd makes one tablet device per panel from the heat
processor's pen reports ("Surface Duo 2 Pen Left/Right", `WL_OUTPUT` from
the udev rule; touch/README.md) and does the palm rejection itself.

- **libinput's tip** (`rootfs/overlay/usr/share/libinput/90-duo2-pen.quirks`):
  with a pressure axis libinput takes a pen's contact from the pressure, never
  from BTN_TOUCH, and by default only above 5 % of the range (3277 of
  0..65535, about 200 of the pen's 4096 levels): a light stroke would never
  ink, and every stroke would start late. The quirk sets
  `AttrPressureRange=2:1` and duo2-touchd sends the tip as at least 3, a hover
  as 0, so libinput's contact is the heat processor's tip bit;
  `AttrTabletSmoothing=0`, because the heat processor filters the pen itself.
  libinput reads the quirks when duo-wc starts.
- **Tablet clients** (bound `zwp_tablet_manager_v2`: GTK 3 / 4, Qt,
  Xwayland) **that are drawing apps** (2026-11-16, `pen_input = apps`, the
  default): the tool. Proximity and hover motion, the tip with pressure (the
  surface it went down on keeps the tool to its lift), the tilt turned with
  the panel's rotation (`rotation::tilt_to_local`, tested against the point
  mapping), the barrel button (BTN_STYLUS: GTK makes it the secondary
  button), the eraser end as an eraser tool. The drawing apps are
  `pen_apps`: app ids or their last dot-separated part, any case, X11 ones
  by their WM_CLASS (`drawing_app`, unit-tested); by default Rnote,
  Xournal++, Krita, GIMP, Inkscape, MyPaint, the Pen test and Nibfair.
  Every other app gets the pen as a finger (below), because GTK and Qt
  drag-scroll only with a touchscreen: a GTK list dragged with a tablet
  tool selects or does nothing (GtkScrolledWindow's gestures are
  `touch_only`), so the pen did not scroll Duo Settings, Files or Firefox.
  `pen_input = all` gives the tool to every tablet client (the behaviour
  before).
- **The compositor's own UI takes the pen as a finger** whatever app is
  under it (`compositor_takes`, `tablet_focus`): the overview (its cards
  drag, close and split with the pen; no app under it gets the pen's
  hover), a split's divider, and while a window is carried or was just let
  go at the hinge (the pen coming down on the other panel picks it up, as a
  finger does). A drawing app's own title bar carries its window with the
  pen too (`tablet::move_request`: the `xdg_toplevel.move` that names the
  tip's serial ends the stroke for the app, tip up and out of proximity,
  and the pen goes on as the carrying finger in `PEN_TOUCH_SLOT`). Over a
  drawing app the screen edges are the app's (the pen draws to the edge);
  wherever the pen is a finger, the edge swipes come with it.
- **The others** (foot, Waydroid, every app not in `pen_apps`, ...): the tip is a touch point in its own
  seat slot (`PEN_TOUCH_SLOT`), through the finger's whole path (edge swipes,
  the overview, the keyboard rules). The **barrel button** is a right click
  where the seat has a pointer (laptop mode's desk, a display's touchpad, a
  mouse): the pointer jumps to the pen and its right button follows the
  barrel's; while it is held the tip does nothing (the tap that usually comes
  with it would hit the menu the click opened). With no pointer (the phone as
  a phone) nobody gets it: a long press is the touch way to a menu
  (`barrel_to`, unit-tested).
- **The hover dot**: while the pen is in range of a panel that shows its
  picture, a white dot with a dark rim (a ring for the eraser end), 9 logical
  px, in front of everything but the mouse cursor (`dot_elements`, drawn in
  the frame path only: not in screenshots). The tablet clients' tool cursors
  are not drawn.
- **Settings**: `pen_button = right_click | off` (off: the barrel goes to
  nobody, tablet clients neither), `pen_dot = on | off`, `pen_input = apps |
  all`, `pen_apps = rnote, krita, ...` (comma or space separated; empty: no
  drawing apps); live with
  `{"request":"set_pen","button":"off","dot":"on","input":"apps","apps":"rnote, krita"}`
  (any of them; privileged: Duo Settings > Pen goes through
  `duo-settings-helper wc-conf`).
- **State**: `get_state`'s `pen`: `in_range`, `output`, `tool` (pen /
  eraser), `tip`, `as_touch`, `right_click`, `tablet_clients`, `button`,
  `dot`, `input`, `apps`, `top_button` (the pen's Bluetooth keys are up:
  paired and connected).
- **The top button** (2026-11-16, `src/penbutton.rs`): it comes over
  Bluetooth LE, not through the digitizer. Paired (Duo Settings > Pen),
  BlueZ's HID-over-GATT makes a uhid HID device of the pen and hid-input a
  key device; as on Windows the button is expected to send left Super + F20
  (one click), F19 (double click), F18 (press and hold). Every input device
  of the pen's Bluetooth HID (on the Bluetooth bus, `id/bustype` 0005, with
  "pen" in its name, Microsoft's by vendor 045E / 0006 or named "Surface":
  `is_pen_bluetooth`, unit-tested) is the compositor's: its keys reach no
  client and not the seat's modifiers, and one with no keys (a pointer
  collection) is switched off, so the pen never becomes the seat's mouse or
  a hardware keyboard. F20 / F19 / F18 go to the event stream as
  `{"event":"pen_button","press":"click|double_click|hold","output":"DSI-2","locked":false}`;
  `output` is the panel the pen is over, or left less than 5 s ago, else the
  active one (only a lit one: `button_output`), `null` while the screen is
  dark (the button does not wake it, as the tip does not). duo-shell does
  what Duo Settings set for the press (`shell/duo-shell` README). The first
  12 keys of each connection are logged with their codes (`journalctl -t
  duo-wc | grep "pen key"`).
- As for a finger: nothing on a dark panel or the ambient screen (the pen does
  not wake), only the lock surface while locked, and laptop mode's keyboard
  panel and a touchpad panel take the pen as a finger.

### X11 apps (Xwayland, 2026-11-16)

`src/xwayland.rs`. Krita (5.2, Qt 5) forces Qt's xcb platform and would not
start without an X server ("could not connect to display"); xterm, xeyes,
Java and Wine programs are X11 only too. Smithay spawns Xwayland and is its
window manager (`XWayland`, `X11Wm`); duo-wc puts the X11 windows into its
own model. Needs the `xwayland` package (install.sh installs it).

- **Lazy.** At start duo-wc takes an X display of its own (the first free
  `:N`: `/tmp/.XN-lock` with its pid, `/tmp/.X11-unix/XN` and the abstract
  socket of the same name), exports `DISPLAY=:N` to the session (the
  apps the shell starts, `session.env`; not to the user manager's and
  D-Bus's activation environment: Chatty's background service opened the
  display at every session start and Xwayland ran for nothing), and only
  listens. The first X11 client that connects starts Xwayland, and
  is served by it from the sockets' backlog (it waits about half a second):
  the listening sockets go to Xwayland as `-listenfd`. Smithay's spawn makes
  sockets of its own (another display number, used only by duo-wc's helper)
  and takes no extra arguments, so it starts `Xwayland` from a PATH that has
  duo-wc's wrapper first (`/usr/local/lib/duo-wc/xwayland/Xwayland`, which
  appends `-listenfd` for duo-wc's sockets, `DUO_XWAYLAND_LISTENFD`, and
  execs `/usr/bin/Xwayland`). With no X11 window for a minute and no X11
  client but duo-wc's own connected (X-Resource), Xwayland is stopped and
  the display listens again. A start that fails turns the waiting clients
  away and listens again after 5 s. No Xwayland or no wrapper installed:
  no `DISPLAY` (logged at start).
- **`xwayland = lazy | on | off`** in duo-wc.conf (lazy by default; on: with
  the session and for good; off: no X11 at all). `get_state`'s `xwayland`:
  `mode`, `display`, `running`, `starting`, `pid`, `windows` (managed),
  `x11_windows` (all top-level X11 windows), `overlays`, `helper`,
  `starts`, `up_s`; each window has `x11`.
- **Scale: X11 at the panels' own pixels.** X11 has no per-window scale, so
  the choice is between Xwayland at scale 1 scaled up by the compositor
  (every X11 app blurred 2.5 times) and this: the Xwayland client's
  Smithay client scale is the output scale (2.5), so the X11 screen is the
  global space in physical pixels (a panel 1345 x 1893, DSI-2 at x = 1410
  with the hinge gap) and an X11 buffer is shown 1:1. The toolkits size
  their UI by the density they are told, before any client reads it:
  XSETTINGS `Xft/DPI` 240 (96 x scale; Qt 5 reads it at start, GTK too, with
  `Gdk/WindowScalingFactor` 2 and `Gdk/UnscaledDPI` 120), greyscale
  antialiasing (`Xft/RGBA none`: OLED), `Xcursor/Size` 60; and the root
  window's `RESOURCE_MANAGER` (`Xft.dpi: 240`, the same, for Xlib / Xft
  programs: xterm's `-fa` fonts). Krita makes a device pixel ratio of 2
  out of 240 dpi (its RoundPreferFloor policy; 2.5 with Settings > Configure
  Krita > General > Window > "Enable fractional scale factor"). Apps that
  size themselves in pixels (xterm's bitmap fonts, xeyes) are small. Input
  maps by itself: Smithay converts pointer, touch and tablet positions into
  the client's space by the same factor.
- **Windows** are `Window`s in the space like xdg toplevels, with the same
  rules: the panel a new window opens on, span, a split half, laptop mode's
  and a display's desk (floating), fullscreen (`_NET_WM_STATE_FULLSCREEN`
  both ways), home, the overview, the dock and task list (app id = WM_CLASS
  class: `krita`, `XTerm`; the title), close (WM_DELETE_WINDOW), force
  close (the X client's pid from X-Resource, asked once when it is mapped;
  never Xwayland's). Where duo-wc puts a window is where X11 is told it is
  (`configure` with its global position), so the popups an app places in
  root coordinates land next to it. The X11 stacking order follows the
  space's after each turn of the loop: Xwayland gives a touch or click to
  the X11 window X11 has in front at that point, whichever surface it came
  on (Smithay's `update_stacking_order_upwards` puts each window above the
  one before it: the space's order, bottom to top; with the reverse, Krita's
  New Document dialog over its spanning parent got no touches). A **dialog** is a transient window or
  one of type dialog, utility (Krita's floating dockers), toolbar or splash:
  on its parent's panel, centred, at the size it asked for within the room
  (`dialog_size`). X11 windows are never scaled down to fit a panel (their
  popups could not follow); one bigger than its room is cut at the panel's
  edge. A move or resize request (`_NET_WM_MOVERESIZE`) is ignored (duo-wc
  places windows); an activation (`_NET_ACTIVE_WINDOW`) is honoured only
  while an X11 window has the keyboard (no focus stealing).
- **Menus, tooltips, drop-downs** (override-redirect windows) are not
  windows of the model: drawn and touched in front of the windows and the
  status bar, behind the overlay layer and not during the overview, where
  the app put them, and given frame callbacks on the panels they are on.
  Qt and GTK keep a menu inside the RandR output it opens on (an X11
  output is a panel), so a menu never crosses the hinge.
- **Focus.** The seat focuses the window's `wl_surface`; X11 also needs its
  own input focus. A helper X11 connection of duo-wc's (made in a thread,
  never blocking the loop) sets it whenever the keyboard focus lands on an
  X11 window (SetInputFocus and/or WM_TAKE_FOCUS by the window's input
  model) and takes it away when the focus leaves X11. The same helper sets
  `RESOURCE_MANAGER` first: it waits in the backlog of Smithay's display,
  whose sockets come before duo-wc's in Xwayland's list, so it is served
  before the app that started Xwayland.
- **Clipboard** both ways (Smithay's selection bridge: an X11 copy becomes
  the seat's selection, a Wayland copy is offered to X11; the X11 app with
  the keyboard may read it). No primary selection (duo-wc has none) and no
  drag and drop (none between Wayland clients either).
- **Input.** Touch reaches Xwayland as wl_touch: XI 2.2 touch events, or
  the emulated pointer for apps without them. The pen as zwp_tablet_v2
  (Xwayland binds it, so it is a tablet client to `tablet.rs`): Xwayland
  makes XInput 2 devices of the tool ("xwayland-tablet stylus" / "eraser",
  with pressure and tilt valuators) as soon as it learns of a tablet, which
  Qt turns into tablet events (Krita's pressure and tilt). Qt 5 sees only
  the devices there when the app starts: duo-wc adds the pen's tablets with
  the first input event it processes after its start (libinput), so an
  X11 app started before any touch after a duo-wc restart (only possible
  from ssh) gets no pressure until it is started again.
  **`QT_XCB_TABLET_LEGACY_COORDINATES=1`** is set with DISPLAY
  (`X11_APP_ENV`, duo-wc's environment, so the session, the shell's apps
  and `session.env` have it): Qt 5's xcb plugin takes a pen's position
  from the XInput valuators and turns it into window coordinates with the
  window position it cached when the app last asked for one (a window
  manager's ConfigureNotify does not update that cache). duo-wc puts every
  window below the status bar wherever the app asked, so Krita got the
  pen 120 X px (the bar's 48 logical px) too low: its tools were picked a
  button and a half below the tip and strokes went astray, while the X
  server's own positions were right to 2 px (the owner's Surface Pen,
  2026-11-16). With the variable Qt uses the server's event coordinates;
  touch and the mouse always did. Only Qt's xcb plugin reads it. Keys as
  wl_keyboard, with whatever keymap the seat has (the on-screen keyboard's
  virtual keys too, `keymapsync.rs`; Xwayland rebuilds its XKB keymap on
  every keymap event). X11 apps speak no text-input-v3, so the on-screen
  keyboard does not come up by itself in them: the bar's keyboard button
  brings it.
- **Logs:** `journalctl -t duo-wc | grep -iE "xwayland|x11"` (start, up
  with its time, windows, popups, stop); Xwayland's own messages go to the
  same journal (its stderr).

### Screen capture (2026-11-09)

For the screen-cast portal (screen sharing in a video call, recorders,
screenshots: docs/SCREENCAST.md). `capture.rs` (the engine, Smithay's
ext-image-copy-capture-v1 + ext-output-image-capture-source-v1 handlers) and
`screencopy.rs` (wlr-screencopy-unstable-v1 version 3, what Debian trixie's
xdg-desktop-portal-wlr 0.7.1 and grim 1.4 speak):

- One output per capture, drawn again off-screen into the client's wl_shm
  buffer (XRGB8888 / ARGB8888, read back as BGRA) or DMA-BUF (XRGB8888 /
  ARGB8888 with the renderer's drawable modifiers, on the render node) with
  the panel's element list (`DuoWc::shown_elements`, as the IPC screenshot),
  in buffer space: 1344 x 1892 whatever the rotation, the transform reported
  (wl_output / the ext frame's `transform`).
- Damage per wlr manager and output, or per ext session (an
  `OutputDamageTracker` asked what changed since its last frame). A
  `copy_with_damage` / ext frame waits until the panel has rendered a
  change (`backend::render` calls `capture::output_rendered`), fails after
  2 s without one (the portal ends a stream only when its last frame comes
  back); `copy` is answered on the next turn of the loop. A DMA-BUF frame is
  ready when its fence signals (waited on the loop). Failures are answered
  100 ms late (no spinning client).
- **Locked: black** (no element at all, not the lock surface).
- Guarded: the trusted clients and the capture-only grant (above); the event
  stream's state has `capturing` (the panels captured now, change
  `capture`), for the shell's indicator.

```sh
duoctl exec-trusted --capture -- grim -o DSI-1 -s 2.5 /tmp/s.png   # as the administrator
sudo journalctl -u duo-wc -b | grep -E "capture frame|wl_shm frame"  # dma=, draw_ms=, waited_ms=
```

### External display: the desktop mode (2026-11-09 night, not yet on hardware)

A monitor or a TV on the USB-C port (a USB-C to HDMI / DP adapter, the
DisplayPort alt mode; the kernel side is docs/hardware/display.md).
The kernel shows it as a third connector, `DP-1`, connected once the
adapter's sink answers. Never "DeX" in code, docs or UI: it is the
external display and the desktop mode.

- **Hotplug** (`extout.rs`): the external connectors are scanned at the
  start and on every udev change of the DRM device (300 ms settle). A
  connected display is brought up on a CRTC no panel holds, in its
  preferred mode (or, when that is below 50 Hz, the largest smooth one:
  `choose_mode`), at a scale from the size its EDID reports
  (`auto_scale`: 1 for a 24-27" 1080p / 1440p monitor, 2 for a 4K TV),
  to the right of the panels, top-aligned, as a `wl_output` named DP-1.
  It is lit by the power rules alone (blanked with the panels, woken with
  them); no pose has it. Unplugged: its windows go back to the panel they
  came from (else the active / first lit one), its layer surfaces are
  closed, the CRTC goes off, the output and its global are gone.
- **The desk** (`desk.rs`, pure, tested): windows on the display float.
  Each has its own frame (opened at 60 % x 70 %, cascaded), drawn with
  its own title bar (client-side decoration there; none on a panel).
  Super + Up maximizes, Super + Down restores, Super + Left / Right takes
  a half; xdg maximize works; a moved window let go at the display's top
  edge maximizes, at a side edge takes that half. A display shows only its
  own windows and a panel none of a display's (a window may hang over the
  display's edge, which in the global space is a panel).
- **The pointer** (`pointer.rs`): a Bluetooth or USB mouse or touchpad.
  The seat has `wl_pointer` only while one is plugged (the digitizer's own
  HID collections are never one), so the phone without a mouse is what it
  was. It moves over the lit outputs, panels included, and stops at their
  edges; click focuses and raises (and makes its output the active one);
  scroll; Super + left button moves, Super + right button resizes (the
  nearest edges); a client's own title bar / border drag works too
  (xdg move / resize from a pointer press). Let go on another output, a
  window goes there (a panel: maximized; a display: floating where the
  pointer is). The cursor is drawn by duo-wc in front of everything (the
  client's cursor surface, or a built-in arrow); a touch on a panel hides
  it until the mouse moves again.
- **Keys**: Super + Shift + Left / Right moves the focused window to the
  output beside its own (DSI-1, DSI-2, DP-1); Alt + Tab brings the active
  output's next window to the front (Shift: the previous). New windows open
  on the display while it is the active output (the last one clicked or
  launched on); the shell's taskbar there launches onto it. The on-screen
  keyboard never goes onto the display (a lit panel). At a desk a key or
  the mouse wakes a dark screen. A plugged keyboard types in the system's
  keyboard layouts (Duo Settings > Keyboard; "Laptop mode" below,
  `kbdlayout.rs`); `xkb_options` and `xkb_model` in duo-wc.conf.
- **The panels meanwhile** go on as a phone (both, as they were), unless one
  is made the display's **touchpad** (`trackpad.rs`, opt-in: `duoctl
  set_trackpad DSI-1`, or `external_trackpad = DSI-1` for every display
  plugged): its touches never reach its apps, it is shown under a dark veil,
  and one finger moves the pointer (2.5x), a tap clicks, a two-finger tap
  right-clicks, tap-and-a-half drags, two fingers scroll. It ends when the
  last display goes. The idle timeout while a display is up is
  `external_idle_timeout` (600 s; 0 never).
- **The shell** (`shell/duo-shell`): on the display its status bar and home
  screen as on a panel, and the dock standing as a taskbar (that display's
  windows, the pinned apps, the app grid).

```sh
duoctl get_outputs                  # DP-1 with "external": true, its EDID name, and
                                    # "desktop": the displays, their windows, the pointer
duoctl move_window 3 DP-1           # a window onto the display (and back: DSI-1)
duoctl expect_window DP-1           # the next window opens there
duoctl set_power DP-1 off           # the display dark, the panels lit (on: back)
duoctl set_trackpad DSI-1           # the left panel as the display's touchpad (off: back)
sh tools/dp-check.sh /tmp/dp snap now       # the port, GPIOs, connector, CTLs, mixers (read only)
sh tools/dp-check.sh /tmp/dp cycle 5        # 5 software unplug / replug cycles (status file)
sh tools/dp-check.sh /tmp/dp poses          # the panels' CTL hand-over with the display up
```

duo-wc.conf: `external = on|off`, `external_scale = auto|1..4`,
`external_mode = auto|preferred|WxH[@Hz]`, `external_idle_timeout`,
`external_trackpad = off|DSI-1|DSI-2`.

A panel as the display's keyboard is laptop mode's keyboard panel (below:
turned on with a display up, the touchpad drives the pointer over the
desk panel and the display alike). Idle inhibitors (a film) hold the
display's blank like the panels' ("Idle inhibit" above). Not done yet: a
second display, drag-and-drop between apps, the display's own rotation and
scale over the control socket.

### A display as a desk or a mirror (2026-11-13, no DP kernel booted yet: unit-tested only)

The owner: "with a TV plugged, choose: laptop mode or just screen
mirroring". A connected display is either a **desk** (all of the above:
windows float there, the shell's desk with its taskbar, the pointer goes
there; the panels stay a phone, or one is laptop mode's keyboard) or a
**mirror** (`mirror.rs` pure and tested, `mirrorlive.rs` live):

- **The mirror's picture**: the source panel's own scene, as a screenshot
  renders it (`shown_elements`: its windows, bars, the cursor over it, the
  lock screen when locked), drawn into that panel's GPU texture (kept and
  reused) and the texture drawn on the display, scaled alike to fit,
  centred, black around (`mirror::fit`: a portrait panel is pillarboxed on
  a TV, never blown up past twice its pixels), upright as the panel reads.
  No copy through the CPU. A panel asked to draw asks its mirrors too
  (`queue_redraw`), so the display follows at its own refresh; a change of
  source redraws it (`mirror_check` after every loop turn). With nothing to
  draw the display is black, never a desk scene.
- **Which panels** (`external_mirror`, `set_external mirror`): `active`
  (default: the panel touched last; it follows the focus), `both` (side by
  side in their canvas order, a 16 px gap), `DSI-1` / `DSI-2`.
- **A mirror is no desk** (`is_desk_output` false): a window placed there
  goes back to a panel at once (`mirror_rehome`, at every place; switching
  a desk to mirror sends its windows home), the pointer never goes there
  (a Bluetooth mouse and keyboard work on the panels as usual), the shell
  shows nothing there, a touchpad panel goes off.
- **On connect** (`external_on_connect`, `set_external on_connect`):
  `ask` (default) mirrors and sets `asking`; the shell asks on the phone
  "Desktop / Mirror" with "Remember my choice" (the answer is
  `set_display`; remembered, also `set_external on_connect`); `desk` or
  `mirror` without asking. Switch a connected display any time:
  `set_display [output] desk|mirror` (the shell's quick menu Display tile).
  The two settings are duo-wc's own, in `$XDG_STATE_HOME/duo-wc/external`
  (over duo-wc.conf's keys), like laptop mode's.
- **The state**: `displays` (change `displays`): `on_connect`, `mirror`,
  and `connected`: `output`, `name`, `use` (`desk` / `mirror`), `asking`,
  `mirrors` (the panels it shows now). The `display` event carries `use`
  and `asking` too.

```sh
duoctl set_display mirror                 # the only display connected mirrors the phone
duoctl set_display DP-1 desk              # ... or is a desk
duoctl set_external on_connect ask        # ask | desk | mirror (kept)
duoctl set_external mirror both           # active | both | DSI-1 | DSI-2 (kept)
```

Untested on the phone: everything that needs a display (no DP kernel is
booted on it yet). Covered by unit tests: the settings and their file, the
sources, the fit, the state, the requests' privilege; the shell's
question, tile and desk rules (shell/duo-shell tests).

### Laptop mode (2026-11-13, not yet on hardware)

The owner's idea: the phone as a laptop without any display plugged in.
One panel is a **desk** (floating windows, a taskbar, a pointer, still a
touch screen), the other the laptop's lower half: a **keyboard and a
touchpad**. Never "DeX" in code, docs or UI: it is laptop mode. `laptop.rs` is the pure part (which panel is which, the
posture rule, the keys by their place, what each shows read from the
keymap, the list of layouts, the modifiers, tested), `laptopmode.rs` the
live one, `kbdlayout.rs` the seat's keyboard layouts.

- **On and off**: `duoctl set_laptop on|off|toggle`, Duo Settings' Laptop
  mode page (through `duo-settings-helper laptop on|off`), or by itself in
  the **laptop posture** when `laptop_auto` is on (off by default):
  duo-posed's posture `compose` is Microsoft's own laptop posture (one half
  flat on the table, the other standing, landscape); leaving it turns
  laptop mode off again, but only when the posture had turned it on; turned
  off by hand in the posture it stays off until the posture is left
  (`laptop::Auto`). It needs both panels (pose `dual`): any other pose ends
  it, and a `dual` pose in the posture may start it. The exit key on the
  keyboard (top right, held 0.6 s) ends it too. `laptop_auto` and
  `laptop_keys` change live (`duoctl set_laptop auto on keys DSI-1`) and
  duo-wc keeps them in `$XDG_STATE_HOME/duo-wc/laptop` (over duo-wc.conf's
  `laptop_*`: an older duo-wc put back never meets a key it does not know
  in its config file).
- **Which panel is the keyboard** (`laptop::panels`): the lower one when
  both panels are turned alike to landscape (duo-posed turns them together;
  90 lays DSI-1 out on top of DSI-2, 270 DSI-2 on top: the lower panel is
  the one lying on the table); otherwise `laptop_keys` (DSI-2 by default).
  The phone turned round in laptop mode swaps them, the windows going over
  to the new desk.
- **The desk panel** is a desk like an external display's (`desk.rs`, the
  "External display" section): its windows float in their own frames
  (opened at 86 % x 86 % of the room, cascaded; a frame made on a bigger
  display is kept reachable here), drawn with their own title bars
  (client-side decoration), maximized and halved with Super + arrows or a
  title bar's double tap, moved and resized by the pointer (a title bar, a
  border, Super + left / right button) and **by a finger**: a title bar
  dragged with a finger moves the window (the client's `xdg_toplevel.move`
  from a touch), a border dragged with one resizes it, and the top of a
  window without a title bar, held, moves it too (no snap targets, no
  carry to the other panel: it floats). A tap still taps; the edge swipes
  and the task overview are there (the overview on the desk only). The
  windows that were on the keyboard panel or across both panels move
  there (their places kept for the way back), new windows open there, a
  launch hint or `focus_output` naming the keyboard panel means the desk,
  and nothing is carried, spanned or snapped onto the keyboard panel. A
  desk window is placed by its frame only (`refit`, which centres dialogs
  and scales big windows on a panel, skips it: it ran at every commit and
  put a floating window at its panel's top left until the next re-place,
  so a window jumped as it drew); a window re-placed under a still pointer
  (maximized, restored, a half) gets the pointer told again once it drew at
  its new size (`pointer::refocus`: a double click right after a maximize
  went to the old title bar). A
  split on either panel waits (kept, not in force) until laptop mode ends.
  **Android windows** keep today's one-display rule: never floating (each
  is configured to one shared size or Waydroid recreates them all, `android.rs`),
  they fill the desk panel as on a phone, behind or in front of the
  floating windows; a spanning or full-screen Android app is put back on
  the desk panel (and comes back across both panels after). The shell's
  dock stands there as a **taskbar** (that desk's windows, the pinned apps,
  the app grid). The idle timeout while laptop mode is on is
  `external_idle_timeout` (600 s), as at a desk.
- **The keyboard panel** shows duo-wc's keys and touchpad and nothing else
  (no window, no bar, no dock, no home screen, no wallpaper: its layer
  surfaces are kept mapped but not drawn and get no frame callbacks; an
  opaque background; one function, `laptopmode::keyboard_scene`, draws it
  for the glass and for a screenshot alike; the first build drew it only in
  screenshots, and the phone showed the home screen under the shell's
  labels),
  except the shell's labels: a see-through overlay surface of the namespace
  `duo-laptop-keys`, which the shell draws from the layout in the state
  (`laptop.layout`: every key's cap in the panel's frame, its label and its
  shifted label; duo-wc has no fonts). Locked, the lock screen shows there
  as on any panel. Every touch there is duo-wc's (never an app's, no edge
  swipe; the pen is a finger there, never a tablet client's): on a key it is a key, on the touchpad the pointer, beside them
  (the palm rest) nothing. Drawn: a blue-black background, the caps as
  rounded dark-glass boxes (`fx.rs`), the layout key's cap tinted blue, a
  pressed key in the accent colour, a latched modifier and Caps Lock
  ringed, the touchpad a rounded field (lighter while touched).
- **The keys** (`laptop::rows`, six rows of 15 units, by their place: evdev
  codes): Esc, F1-F12, Home, End, Del and the exit key; then the keyboard
  of the layout in force **as on a physical one**: the digit row with
  Backspace; Tab, the letters; Caps Lock, the letters; Shift, the letters,
  Shift; Ctrl, Super, Alt, space, AltGr (or Alt), the **layout key**, Ctrl
  and the arrows (up and down sharing one cell). An **ISO** board (102
  keys: the key left of Z, a two-row Enter, the key left of it on the home
  row) when the layout has a key of its own left of Z (Hungarian: í,
  German: <), else **ANSI** (US: backslash above a wide Enter): Hungarian
  is the Hungarian QWERTZ (0 on the left of the digit row, ö ü ó at its
  right, ő ú right of P, é á ű right of L, í left of Y), German QWERTZ
  with ä ö ü ß, US QWERTY. **What a key shows comes from the seat's own
  keymap** (`laptop::group_keys`: xkb's state with nothing, the left Shift
  and the right Alt pressed, for every group; `laptop::Board`, compiled
  from the seat's names when laptop mode turns on and when the list of
  layouts changes): the symbol big, the shifted one small at the top left
  (a letter's capital: with Shift or Caps Lock the letter itself turns), the
  AltGr one small at the bottom right in blue (with AltGr in force it is
  the big one); a dead key shows its accent (´ ˝ ^). So every layout gets
  its own arrangement without a table. **Landscape** (the laptop posture):
  the keys along the hinge (55 % of the panel's height, keys about as wide
  as tall: 49 logical px on a 757 x 538 panel, the F keys 0.8 of that), the
  touchpad below them, centred, 52 %
  of the width; **portrait**: the keys at the bottom (40 %), the touchpad
  above them, across. A finger hits the key whose cell it is in (the gaps
  between the caps belong to the keys); it stays that key until it lifts.
  A key goes into the seat as a hardware key would (`input::key_input`, the
  same path as a Bluetooth keyboard's: the compositor's shortcuts work,
  Super + arrows, Alt + Tab, a dark screen drops it), so every app gets it
  with its repeat (the clients repeat a held key themselves). **Modifiers**
  (`laptop::Mods`): held with another key as on a keyboard; tapped alone a
  modifier stays down (latched, ringed) for the next key and lets go after
  it; tapped again it lets go; both Shift keys press Shift once.
- **The layout**: the keys type in the seat's own keymap, unchanged (no
  group of laptop mode's own: a key goes into the seat by its place and xkb
  types what the layout in force types there, dead keys included); a
  Bluetooth keyboard types in the same layout. **The layout key** (bottom
  row, right of AltGr) shows the layout in force (`HU`, `US`): **tapped**,
  the next layout of the list (`kbdlayout`, the seat's group: the caps
  change at once, the next key types in it); **held 0.45 s**
  (`LAYOUT_HOLD_MS`), the **list of layouts** comes up over the keys
  (`laptop::menu`): the configured ones first (the one in force in the
  accent colour), then common ones (`COMMON_LAYOUTS`: us, then de, gb, es,
  fr, it, hu, pl, by their own names) while the list has room (four, xkb's groups); the finger
  slides onto one and lifts, or lifts and taps one; a touch beside it closes
  it. A layout picked that is not configured is **added** to the seat's
  list (the keymap compiled again) and put in force, and the shell is told
  (`keyboard_layout_added`): it adds it to the system's list (below), so the
  on-screen keyboard and Duo Settings have it too. A plugged keyboard's own
  layout switch (`xkb_options = grp:...`) changes what the keys show too.
  Android apps take keys by their own keyboard layout.
- **The keyboard layouts** (`kbdlayout.rs`, for the seat: a plugged
  keyboard and laptop mode's keys) are **the system's list**:
  `org.gnome.desktop.input-sources` `sources`, the on-screen keyboard's,
  which **Duo Settings > Keyboard** edits (in order, add, remove); the
  shell sends it with `set_keyboard layouts=hu,us` at its start and at every
  change (an `xkb` source is that layout, `de+nodeadkeys` with its variant;
  phosh-osk-stub's word-suggestion source `('ibus','presage:en')` types on
  the US layout, so it is `us`; at most four). The first is in force at the
  start. duo-wc keeps the last list in `$XDG_STATE_HOME/duo-wc/keyboard`
  and starts with it; before the first one, duo-wc.conf's `xkb_layout` /
  `xkb_variant` (empty: `XKB_DEFAULT_LAYOUT`, else `us`); `xkb_model` and
  `xkb_options` always come from duo-wc.conf. A new list compiles the seat's
  keymap again (the layout in force kept if it is still in it); the keymap
  sync (`keymapsync.rs`, after an on-screen key) sets that keymap back. The
  image's list is Hungarian, then English (US) with word suggestions: the
  keyboards start Hungarian.
- **The touchpad** (`trackpad::Pad`, the external display's touchpad
  panel's gesture machine): one finger moves the pointer, accelerated by
  the finger's speed (`laptop::accel`: 0.75x slow to 2.6x fast, times 1.5),
  a tap clicks, a two-finger tap right-clicks, two fingers scroll (natural),
  **tap and touch again drags** (libinput's tap-and-drag, `trackpad::Pad`):
  a tap's press goes out when it lifts and its release waits 250 ms
  (`DRAG_MS`) for a finger to come back; one that does holds that same
  press and drags it (a title bar moves the window; it lifts: the button
  goes), one that taps again is a double click, none is a click (its
  release then; typing a key ends it at once). The first build sent a
  second press for the drag, and two presses are the app's double click: a
  title bar maximized instead of moving. A second finger before the drag
  began ends the click and scrolls; one during a drag is ignored. A touch
  just too late for a drag is logged (`touchpad: a touch came too late`).
  `touchpad_drag_lock = on` (duo-wc.conf, off by default, both touchpads):
  a drag survives its finger lifted for 0.3 s. The seat has a pointer while laptop mode is on (the
  `duo-wc-laptop` pointer device; with a mouse plugged it stays after);
  it moves over the desk panel (and an external display), never over the
  keyboard panel; the cursor shows when the touchpad moves it and hides
  when a finger touches the desk.
- **The on-screen keyboard** never shows in laptop mode (not drawn, not
  touched, no room taken: `osk_hidden`), and the device state
  (`zphoc_device_state_v1`) says a hardware keyboard is there, so
  phosh-osk-stub does not unfold by itself.
- **The shell's desk** (shell/duo-shell README "A desk"): on the desk
  panel (and on an external display) the shell shows a desktop: the
  wallpaper, a taskbar with an app menu and a tray, no status bar. Its
  taskbar's second click on an app sets the window aside with
  `minimize_window` (`duoctl minimize_window <id>|<app-id>`; the client's
  own minimize does the same; `activate_window` brings it back).
- **The state** (`get_state`, the event stream's `laptop`, change
  `laptop`): `on`, `origin` (`manual` / `auto`), the settings `auto` and
  `keys_pref`, and while on `desk`, `keys`, `layout` (the keys with
  `label` / `shift` / `altgr`, the touchpad, the layout in force `group`,
  `iso`), `shift`, `caps`, `altgr`, `latched` and `menu` (the list of
  layouts while it is up). Turning on or off is also an event:
  `{"event":"laptop","on":true,"desk":"DSI-1","keys":"DSI-2"}`. The
  layouts are `keyboard` (change `keyboard`): `layouts` (`layout`,
  `variant`) and `active`.

```sh
duoctl set_laptop on                 # the keyboard on DSI-2 (or the lower panel in landscape)
duoctl set_laptop off
duoctl set_laptop auto on            # on by itself in the laptop posture (kept)
duoctl set_laptop keys DSI-1         # the keyboard panel when the posture does not say (kept)
duoctl get_state | python3 -c 'import json,sys; print(json.load(sys.stdin)["ok"]["laptop"])'
duoctl set_posture compose           # what duo-posed sends in the laptop posture
duoctl set_keyboard active next      # the next layout (what the layout key does)
duoctl set_keyboard add de           # German in force, added to the seat's list
duoctl set_keyboard layouts hu,us    # the list (the shell sends the system's)
```

Open, for the owner: the keyboard's layout (US QWERTY with the accented
letters in their own row now; a Hungarian QWERTZ arrangement is one
table), whether a key press vibrates (the shell's haptics; none now), the
desk panel's scale (2.5 like the phone; 2 would give the windows a third
more room), new windows floating at 86 % (or maximized), and how the
typing and the touchpad feel.

### Grants and locking by the rules (2026-11-07 night)

- **Grants** (`grants.rs`, pure; `grant` / `redeem`, both privileged):
  the shell asks for a one-time token for a root helper's exact action
  (`duoctl`-style words, "mobile-data off"), passes it to the helper on its
  standard input, and the helper (root, admin socket) redeems it, once,
  within 15 s. sudo cannot tell the shell from an app; a grant can only be
  had on a trusted connection. `confirm` has a new action `helper` for a
  helper call without a grant (another app: the user is asked, 20 s), and
  `esim` (duo2-esim, 45 s).
- **Locking by the rules** (`lock.rs` `lock_by_policy`, `policy_locks`
  pure): on a fold (`posture closed`, `lock_on_close`) and on a blank
  (`lock_on_blank` with a PIN) duo-wc locks by itself, reading root's
  `/var/lib/duo2-lock/policy`: locked like after a restart (black, the
  next lock client takes it over, the systemd fd store notes it), so a
  killed shell cannot leave the phone unlocked; the shell starts duo-lock
  when it sees `locked`.

### Ready for systemd, the boot splash (2026-11-08)

`duo-wc.service` is `Type=notify`: duo-wc sends `READY=1` when every lit
panel has shown its first frame (`backend.rs`, `lock::notify_ready`), at the
latest after 12 s (a timer, so the start never times out). Units ordered
after it wait for the first picture: `duo2-plymouth-quit.service` lets the
boot splash go (`plymouth quit --retain-splash`) only then; before starting,
the splash's drop-in (`rootfs/plymouth`) runs `plymouth deactivate`
(`ExecStartPre=-+`, nothing without plymouth), so the splash's picture stays
until duo-wc's replaces it.

**No power cycle at the hand-off** (2026-11-08). duo-wc opens the device
with `DrmDevice::new(fd, false)`: the CRTCs stay as the splash left them
(with `true` Smithay switched every CRTC off first, one blocking commit, and
each panel came back with a modeset: ~1.1 s of black between the logo and
duo-wc's first frame). A panel lit on its connector in the mode duo-wc picks
(the driver's preferred one, 1344x1892@90) is brought up with the CRTC's
own mode word for word (Smithay compares the name and the type bits too),
so its first frame is a plain page flip: `bringing up the output ...
takeover="lit in this mode: kept (page flip, no power cycle)"`, then
`first frame queued ... modeset=false`. Off (a start without the splash) or
lit in another mode (a refresh rate chosen in Duo Settings after the splash
came up) is a modeset with the first frame, as before (`backend::takeover`).
If the driver refused the flip, the panel is switched off and the next frame
is a modeset. The card is found from sysfs (`/sys/class/drm/card0-*`), not
by opening it: with nobody else holding the device, that close was the
device's last one and the kernel put fbcon's picture back on the panels.
Measured (2026-11-08, kernel 7.3.3-duo2-ge5570a93c, Plymouth from the
initramfs; a temporary boot unit sampled `/sys/kernel/debug/dri/0/state`
every 25 ms): both CRTCs active from 3.92 s (the splash) to the end of the
sample at 28.9 s, never inactive; duo-wc started 7.66 s, outputs kept 8.14 s,
first frames page flips (`modeset=false`) on screen 8.340 / 8.349 s, READY
8.349 s, `plymouth quit --retain-splash` 8.41 s: the logo goes straight to
duo-wc's first frame. Before (kernel g46debb1e7, ordinary Plymouth): CRTCs
off at 7.37 s, display on 8.46 / 8.67 s. Also kept, no modeset, on two duo-wc
restarts (fbcon had relit the panels) and two more boots.
A wake with the repeats (same day, injected power key): pictures on screen
426 / 430 ms after the commit, the two repeats 30 and 150 ms later on both.

### dconf in the session (2026-11-07 night)

The session's clients have `XDG_RUNTIME_DIR=/run/duo-wc`, but dconf-service
(the user bus's) flags a change in `/run/user/UID/dconf/user`: the shell's
GSettings never saw a change made by another process. `dist/session` makes
`/run/duo-wc/dconf` a symlink to `/run/user/UID/dconf` before it starts
anything.

**Since 2026-11-10 the session's clients have the user's own runtime
directory** (`XDG_RUNTIME_DIR=/run/user/UID`, written so to `session.env`
too) and duo-wc's display by its absolute path
(`WAYLAND_DISPLAY=/run/duo-wc/wayland-1`; libwayland, GTK, Qt, Chromium,
Flatpak and Waydroid all take one). With `/run/duo-wc` there, flatpak wrote
its instance files (`.flatpak/ID/bwrapinfo.json`) where xdg-desktop-portal
(a user service in `/run/user/UID`) never looks, and refused every portal
call of every Flatpak ("Portal operation not allowed"): no dark style, no
file chooser, no open-URI. duo-wc's own files (sockets, `session.env`, the
PID files) stay in `/run/duo-wc`; the dconf link stays for anything still
started with it.

### Who may do what (2026-11-07)

Every app runs as the GUI user, the same user as duo-wc, so the user id
decides nothing; trust is a capability (`src/trust.rs`, docs/hardware/security.md):

| who | how | control requests | Wayland |
| --- | --- | --- | --- |
| any app of the user | `duo-wc.sock` (0600), `wayland-1` | `get_outputs`, `get_state`, `subscribe`, `expect_window`; everything else `not allowed` | everything but the guarded globals |
| the session: duo-shell, duo-lock, the on-screen keyboard (phosh-osk-stub or squeekboard), the session hooks | the trust factory (fd 3, `DUO_WC_TRUST_FD`): SOCK_SEQPACKET, a packet `control` / `wayland` comes back as `ok` + a fresh connection (SCM_RIGHTS) | everything | also the guarded ones |
| the screen-cast portal (xdg-desktop-portal-wlr) and its screenshot gate | a capture factory (`capture-factory` from a full factory or the admin socket): it answers only `capture` and `capture-factory` | - | the screen-capture globals, none of the other guarded ones |
| root, `admin_users` (duo2admin) | `admin.sock` (0666 in the 0711 runtime directory, answered by SO_PEERCRED) | everything; `trusted_socket` hands out a trusted Wayland connection or a factory (or their capture-only kinds) | through `duoctl exec-trusted --wayland` |
| `sensor_users` (duo-posed) | `admin.sock` | the state, `set_posture`, `set_pose`, `set_rotation`, `set_power` | - |
| `confirm_users` (polkitd) | `admin.sock` | `confirm` only (a question to the user, answered by the shell) | - |
| anyone else | `admin.sock` | refused at once | - |

The **guarded globals** (trusted clients only, global filters on the
client's `ClientState::trusted`): `zwp_virtual_keyboard_manager_v1`,
`zwp_input_method_manager_v2`, `ext_session_lock_manager_v1`,
`ext_foreign_toplevel_list_v1`, `zwlr_layer_shell_v1`, and (2026-11-07
night) `ext_data_control_manager_v1` (the clipboard at any time, not only
while focused: its one client is the shell's Android clipboard bridge,
`duo-clipboard-bridge`), and (2026-11-09) the screen capture,
`zwlr_screencopy_manager_v1`, `ext_image_copy_capture_manager_v1` and
`ext_output_image_capture_source_manager_v1`, which a **capture-only**
client sees too (`ClientState::capture`, `trust::Grant`: the screen-cast
portal, "Screen capture" below), and (2026-11-09, for phosh-osk-stub)
`zwlr_foreign_toplevel_manager_v1`, `zwlr_data_control_manager_v1` and
`zphoc_device_state_v1`. GNOME apps,
Chromium, Firefox, Angelfish and foot need none of them;
`zwp_text_input_manager_v3` stays public, so the on-screen keyboard types
into every app. `duoctl exec-trusted [--wayland] [--keep-trust] -- CMD`
runs CMD with a trusted `WAYLAND_SOCKET` (and the factory passed on): the
session starts the shell (`--keep-trust`) and the on-screen keyboard with it.
`--capture` / `--keep-capture` do the same with a capture-only connection
and factory (the session starts the portal backend so).

duo-wc makes itself **non-dumpable** at start (the kernel has no Yama: any
process of the same user could otherwise ptrace the compositor), and so do
duo-shell and duo-lock; squeekboard and the session script are ordinary
(see "Known gaps"). **The lock outlives a restart** in systemd's descriptor
store (`FileDescriptorStorePreserve=yes`, `NotifyAccess=main`): a memfd
named `duo-wc-locked` while locked; `systemctl show duo-wc -p
NFileDescriptorStore` is 1 while locked. It used to be a file in
`/run/user/UID`, which any app could delete before making duo-wc restart.

The unit restarts duo-wc always (`Restart=always`, 3 s): the session never
ends while the shell runs, but if it does (or Ctrl+Alt+Backspace), the phone
gets a compositor back. `systemctl stop duo-wc` still stops it.

### The event stream

`{"request":"subscribe"}` keeps the control connection open. The reply carries
the current state; after it, one line per change:

```text
<- {"id":1,"ok":{"subscribed":true,"state":{"pose":"dual","lit":["DSI-1","DSI-2"],...}}}
<- {"event":"state","changed":["power"],"state":{"pose":"dual","lit":[],"blanked":true,...}}
<- {"event":"edge_swipe","edge":"bottom","output":"DSI-2"}
<- {"event":"volume_key","key":"up","state":"pressed","output":"DSI-2"}
<- {"event":"pen_button","press":"click","output":"DSI-2","locked":false}
<- {"event":"ring_silence","key":"power"}
```

`state` is `events::Snapshot`: `pose`, `lit`, `blanked`, `hinge_gap`,
`fullscreen` (panels with a fullscreen window in front), `posture`,
`ambient` (`peek` / `glance` / null),
`rotation` (per output), `active_output`, `keyboard_output` (the panel of the
keyboard focus), `osk_output` (the panel squeekboard is on, or null),
`overview`, `overview_cards` (each card's label band while it is open:
`output`, `window`, `x`, `y`, `w`, `h`, `dragged`, `closing`, `stuck`),
`locked`, `windows` (id, app_id, title, placement, minimized; back to
front), `home` (the lit panels that show home), `carrying` (a window is
carried), `proximity` (the panels held dark at an ear), `ring` (`loud` /
`silent` / null: an incoming call rings, "Ring hold"), `capturing` (the panels
captured now), `idle_inhibit` (who keeps the screen from blanking by itself:
"Idle inhibit"), `splits` (`output`, `axis`
`side_by_side` / `stacked`, `windows` left or top first, `share`,
`active`, `front`). `changed` names what moved: `pose`, `power`,
`hinge_gap`, `rotation`, `active_output`, `keyboard`, `osk`, `overview`
(also when the cards move), `locked`, `windows`, `split`, `fullscreen`,
`posture`, `ambient`, `home`, `carrying`, `proximity`, `ring`, `capture`,
`idle_inhibit`. Edge swipes carry `edge`,
`kind` (`short`, `long`, `home`, `hold`, `down`, `back`), `travel` and,
for a back, `to`. `force_close_request` (`window`, `app_id`, `title`,
`output`) asks the shell to confirm a force close; `confirm_request` (`id`,
`action`, `detail`, `output`) a question to the user, `confirm_closed`
(`id`) its end (answered, timed out); `ring_silence` (`key`: `power`) that
the power key silenced a call ringing aloud; `pen_button` (`press`: `click`,
`double_click`, `hold`; `output`, null while dark; `locked`) the pen's top
button ("The pen" above). duo-wc compares a snapshot after every turn of its event
loop with the last one it sent, so nothing is missed whatever caused it, and
nothing is sent while nothing changes. A subscriber that stops reading is
dropped. `duo-shell` uses it (edge swipes; its timers stop while the screen is
dark) and so does `duo-posed` (it polls `get_state` only while the stream is
down).

### A wake that does not reach a panel

`tools/wake-capture.py` (copied to the phone, run as root, e.g. as a
transient unit: `sudo systemd-run --unit=duo2-wake-capture --collect
/usr/bin/python3 -I /var/tmp/wake-capture.py /var/tmp/duo2-wake --regs
--limit 1800`) follows the event stream and, 0.7 s and 3 s after every
wake, saves the DRM atomic state, the encoders' status and (`--regs`,
only while both connectors are on: the snapshot reads the DSI PHYs
unpowered otherwise) the display register snapshot (`dri/0/kms`). Someone
watching the panels says which wakes were dark; the files of a dark and a
good wake are then compared block by block.

### Testing without fingers

`tools/touch.py` (on the phone, as root; needs `evemu-tools`) clones a
panel's touchscreen with `evemu-device` (same name, so the same `WL_OUTPUT`)
and plays a touch into it:

```sh
sudo /usr/local/lib/duo-wc/tools/touch.py right tap 0.5 0.5           # panel fractions, unrotated frame
sudo /usr/local/lib/duo-wc/tools/touch.py right swipe 0.5 0.995 0.5 0.7 300   # swipe up from the bottom edge
sudo /usr/local/lib/duo-wc/tools/touch.py left taps 0.45,0.698 0.25,0.698     # several taps (keyboard keys)
sudo /usr/local/lib/duo-wc/tools/touch.py right swipehold 0.5 0.996 0.5 0.75 250 600   # swipe up and hold: overview
sudo /usr/local/lib/duo-wc/tools/touch.py left liftdrag 0.25 0.47 0.5 0.1 400 600 2000  # hold, then drag: pick up a card
sudo /usr/local/lib/duo-wc/tools/touch.py right dtap 0.5 0.075                # a double tap (a window's top)
sudo /usr/local/lib/duo-wc/tools/touch.py left cross 0.5 0.075 0.985 0.4 right 0.015 0.42 0.5 0.4 60 600 400
                                    # hold a window's top, carry it to the hinge, lift, 60 ms later
                                    # down on the other panel's hinge edge and on (one drag)
```

Since 2026-11-07 the tool writes the events into the clone itself with the
planned timing; `evemu-play` had delivered a whole tap at once (a
zero-length touch, which GTK sometimes ignored).

`tools/teardown-repro.py` (not installed: copy it to the phone; `text` and
`toplevel` run as the user after `. /run/duo-wc/session.env`, `layer` needs
`duoctl exec-trusted --wayland --` as the administrator; plain Python,
nothing drawn) is a client that
dies while it has the keyboard, in the object order that used to abort duo-wc
(see "Client teardown" below): `layer`, `text` (a text-input enabled, the
on-screen keyboard comes up) or `toplevel`, then `exit` or `kill`
(SIGKILL). It prints `focus` when the keyboard was on it; duo-wc logs
`gone with its client, which has the keyboard` for each such teardown.

Sockets live in `/run/duo-wc/` (the unit's `RuntimeDirectory`, 0711, also
the session's `XDG_RUNTIME_DIR`): `wayland-1`, `duo-wc.sock` (0600, the
user's) and `admin.sock` (0666, answered by credentials). The IPC is
one JSON object per line, `{"id":1,"request":"set_hinge_gap","gap":"off"}`
(the other requests: `ipc.rs`).

`tools/power-test.sh current` measures the battery current with both panels
lit, each one off and both off; `tools/power-test.sh cycles [N]` switches each
panel off and on N times (default 20) and checks that it came back (CRTC
active again in DRM debugfs, a new page flip, duo-wc's "panel on" line) and
prints the kernel log since the start. Both stop the crash-looping modem
remoteproc first, as `scripts/duo2-power` does.

### Seat: seatd, not logind

The unit runs duo-wc as the user under **seatd** (`seatd -g video`, Debian's
`seatd.service`; the user is in `video`). seatd opens the DRM device (DRM
master) and the input devices and puts the active VT in graphics mode, so fbcon
stops drawing while duo-wc runs and comes back when it stops. The unit has no
TTY and no PAM session.

A logind session (`PAMName=login`, `TTYPath=/dev/tty1`, `Conflicts=getty@tty1`)
was tried first and works, but it froze systemd on this phone: every systemd TTY
reset takes a lock on `/dev/console`, and the serial getty on the USB gadget
(`serial-getty@ttyGS0`) holds that lock for as long as no USB host reads the
port (its pre-exec reset write blocks on the disconnected ACM tty). Stopping
`getty@tty1` for duo-wc then left PID 1 waiting in `flock()` forever (ssh
logins hung on pam_systemd). With seatd duo-wc needs no TTY, so it never needs
that lock. The getty problem itself is outside duo-wc; see known gaps.

**Device hotplug off the loop** (2026-11-16, `src/input.rs`). The last close
of an evdev descriptor waits in the kernel for an RCU grace period
(`evdev_release` -> `evdev_detach_client` -> `synchronize_rcu`: 7-28 ms on the
phone), in the thread that closes it, and libinput closes a device that went
inside its dispatch. The Surface pen drops and re-makes its Bluetooth HID
device every time it comes near the screen, so every approach held the whole
compositor 15-24 ms (a second drop right after the reconnect doubled it). duo-wc
gives libinput its own interface (`SeatInterface`): the seat still closes the
device at once (seatd revokes it), but the last descriptor is a duplicate that
a helper thread (`LateCloser`) closes. A new device still costs ~12 ms on the
loop (libinput's device setup and the seatd round trip). Every turn of the
input source over 8 ms is logged (`input turn held the loop ... ms (libinput
..., handlers ...): N events, devices +A -R`), an open through the seat over
4 ms at debug level.

## Verified, frame pacing (2026-11-16 evening, kernel 7.3.4, Mesa 25.0.7)

CPU over 10-20 s (utime + stime from `/proc`), no synthetic touches:
- Globals: `wp_fifo_manager_v1` 1, `wp_commit_timing_manager_v1` 1,
  `xdg_wm_base` 7 (`wayland-info`).
- Nibfair (SDL 3.2.10, its Wayland override removed): `wayland / opengl`
  chosen by SDL itself. Screen on: 89.8 fps, 66 % of a core. Blanked:
  `window hidden: suspended why="dark"`, two slow frames, then Nibfair
  0.1 % (it waits for events while occluded), duo-wc 0.0 % with 4 context
  switches in 20 s. Woken: `window shown again`, 89.2 fps again. Started
  on the dark screen: suspended in its first configure, 0.1 % from the
  start, no buffer drawn until the screen came on (then it opened at 538 x
  709 and ran at 85 fps).
- mpv, Vulkan (`--gpu-context=waylandvk`, Mesa's WSI: `get_fifo`,
  `set_barrier` + `wait_barrier` and a commit timer every frame): 90 Hz
  shown (median 11.16 ms between barriers), 51 % CPU; blanked, a barrier
  every 1001 ms, mpv 1.1 %, duo-wc 0.2 %; 90 Hz again after the wake.
- es2gears (EGL, ignores `suspended`): 6 % shown, 0.1 % blanked (a frame
  a second).
- GNOME Text Editor (GTK 4.18): 0.0 % suspended (dark, home); after the
  wake and back from home it redrew at once at a new size (one panel <->
  span). The overview open: home windows shown again, closed: hidden.
  Covered by a fullscreen mpv on its panel: `why="covered"`, shown again
  when mpv quit.
- Krita 5.2.9 (X11): Krita and Xwayland 0.0 % blanked; both of its windows
  drawn right after the wake (screenshots).

## Verified, X11 apps (2026-11-16, kernel 7.3.4, Xwayland 24.1.6)

- Lazy: no Xwayland after a duo-wc start (once Chatty's service no longer
  got DISPLAY); the first X client (`xwd`) answered in 196 ms with
  Xwayland's start (up in 120-175 ms), the next one in 5 ms; with no X11
  window for a minute Xwayland stopped, duo-wc stayed up (the first build
  panicked there: a stopped window manager's queued X11 events; fixed).
- Cost: Xwayland 86 MB RSS idle (25 MB anonymous, the rest shared
  libraries, Mesa), 90 ms CPU to start; 137-172 MB with Krita open (Krita
  itself 270-390 MB); nothing at all while it is not running.
- xterm (`-fa Monospace -fs 10`): sharp at the panels' pixels, text the
  status bar's size (Xft.dpi 240); on one panel, spanned (1102 x 709),
  full screen (`-fullscreen`: 538 x 757, no bar), closed by
  WM_DELETE_WINDOW; keys from a hardware-style keyboard with the seat's
  Hungarian keymap.
- Krita 5.2.9: its splash a dialog, the main window maximized on the
  empty panel, spanned; the File menu under File, the Settings menu next
  to the hinge moved left to end at it; the New Document dialog on the
  panel touched (asked 751 wide, 538 given), Create by touch; force close
  killed Krita's own pid, Xwayland went on. A synthetic pen (a clone of
  "Surface Duo 2 Pen Right" with evemu) drew strokes whose width follows
  the pressure; XI2 showed pressure 0..65535 and tilt -60..60 degrees.
- Clipboard: a Wayland copy (ext-data-control) pasted in a GTK 3 X11
  client, an X11 copy read by a Wayland client.
- The owner's Surface Pen in Krita: first 1.5 tool buttons too low (the
  tools changed under the pen, strokes went astray), the X server's
  positions right; with `QT_XCB_TABLET_LEGACY_COORDINATES=1` the toolbox
  and the canvas both right ("now it is right"), spanning both panels. An app
  started from the shell gets `DISPLAY=:0` (GIO does not put the Wayland
  display there).

## Verified, the owner's list (2026-11-07 evening)

Pictures: `out/shell-shots/n7/`.

- **Rotation animation** (first version): slowed to 5 s, DSI-1 0 -> 90 ->
  0, screenshots during it (`rotate-1..4.png`): the whole portrait picture
  turning clockwise with black corners, near upright stretched to
  landscape. A first try showed a corner of the snapshot magnified 2.4x: a
  TextureRenderElement without a source takes the destination's logical
  size as its source rectangle.
- **Rotation animation, reworked** (the owner: it stretched and lagged).
  Measured with `turnlog.rs` and `tools/rotate-timing.py` (both panels
  turned 0 -> 90 -> 0 ... like duo-posed, frame intervals from the kernel's
  flip timestamps; a frame later than 13 ms is late; 90 Hz = 11.1 ms):

  | | panel-turns | late frames | longest interval | longest frame CPU |
  | --- | --- | --- | --- | --- |
  | before, home screen only | 8 | 8 (in 4) | 31.2 ms | 18.6 ms |
  | before, Text Editor + Files open | 12 | 66 (in all 12) | 76.6 ms | 52.1 ms |
  | after, home screen only | 16 + 8 | 1 | 23.3 ms | 6.5 ms |
  | after, Text Editor + Files open | 16 + 12 | 4 + 4 | 22.0 ms | 11.9 ms |

  The cause, from the frame breakdown and stack samples of the compositor
  thread during turns (ptrace PC samples with frame-pointer walks, no tools
  installed): the long frames were all in `render_frame`, inside
  `GlesRenderer::cleanup` -> `eglDestroyImage` -> the kernel (GEM close of
  the clients' old buffers, which they all destroy at once when they
  reallocate at the new size), not in the snapshot, the shader or the
  commit; holding the clients' buffers through the turn removed them. The
  late frames left are single missed vblanks with a short frame (1-2 ms):
  the GPU busy with the clients' re-layout (one ring, no preemption on this
  kernel). After the turn the held buffers are let go in 3-9 steps, the
  longest 6-22 ms (400 ms after the turn, between frames). The clients now
  draw at the new size 54-177 ms into the turn. Slowed to 3 s
  (`rotate2-*.png`): uniform
  scale and letterbox both ways, the old picture fading into the new one
  turning with it, the last frame of the turn and the first live frame the
  same picture. Edge cases (rapid re-turns 50 ms apart, one panel only, a
  half turn, a panel switched off mid-turn and on again): the panels kept
  presenting, frames queued = presented.
- **Wallpaper at the end of a turn** (the owner: it jumped): the shell cut
  the wallpaper per panel 400 ms after a rotation (decode + crop in a
  thread), so the turn ended on the old cut stretched to the new shape and
  the new cut popped in afterwards. The shell now cuts its part of one
  decoded picture as it draws, from duo-wc's layout fetched at the new size
  (shell/duo-shell/README.md "Wallpaper"): its first frame at the new size
  is final, and duo-wc's turn ends on exactly what the scene shows (at full
  speed no second picture of the home screen was needed).
- **Peek**: blank, `set_posture peek_right` (duo-posed stopped: it sends
  its own posture), four times: the shell drew the strip 115-204 ms and it
  was on screen 236-309 ms after the posture (the wake commit 215-249 ms);
  only DSI-2 lit; locked first, the peek showed the strip on black, opened
  the lock screen (`peek-DSI-2.png`, `peek-locked-DSI-2.png`,
  `lock-DSI-1.png`). Glance: 225 ms.
- **Tent**: `tent_right`, `single DSI-1`: one tap on DSI-2 nothing; a
  double tap (`touch.py right dtap`) switched to DSI-2.
- **Unlock**: locked by `set_posture closed` (no PIN: swipe), `duoctl
  unlock`: duo-lock logged "the compositor ended the lock" and exited, the
  shell reaped it.
- **Screenshots**: `read_ms` 20-50, `total_ms` 51-97 (the PNG in its
  thread); a `get_state` during one waited 167 ms with the first build
  (a byte count over the 10 MB buffer on the loop took ~300 ms; gone).
- **Confirm**: Duo Settings' helper `restart-display` as the GUI user: the shell's
  question on DSI-1 (`confirm-restart-DSI-1.png`), Cancel: "the user said
  no", nothing restarted. `pkcon remove nano` as the GUI user: polkit asked
  through duo2-polkit-confirm ("An app asks to uninstall: pkcon: nano",
  `confirm-remove-DSI-1.png`), Cancel: "Failed to obtain authentication",
  nano still installed.

## Verified, client teardown (2026-11-07 afternoon)

The crash: 14:38:18, duo-shell reinstalled (SIGTERM) with its app grid
open; duo-wc aborted in `resource_destructor` (wayland-backend
`server_impl/mod.rs:1142:56`, `InvalidId`): `layer_destroyed` moved the
keyboard focus in the middle of duo-shell's teardown. The same panic, line
and column, comes back on the host from `cargo test -- --ignored --exact
teardown::tests::unguarded_focus_change_aborts` (SIGABRT); the guarded
path is `teardown::tests::a_focus_change_inside_a_dying_clients_teardown_waits`.

On the phone, with the fix:
- `tools/teardown-repro.py`, 330 runs (layer 210, toplevel 90, text with
  the on-screen keyboard up 30; half closing, half SIGKILL): every run had
  the keyboard when it died, every teardown was the old crash's case (330
  `gone with its client, which has the keyboard` lines), duo-wc's PID
  unchanged, no panic.
- duo-shell killed 5 times (SIGTERM and SIGKILL) with the app grid or the
  quick menu open (14 layer surfaces of it went while it had the keyboard),
  squeekboard SIGKILLed twice, gnome-text-editor SIGKILLed 5 times: no
  crash; the shell, the keyboard and the session came back each time.
- Then: app grid, back swipe, quick menu, the lock (swipe unlock; the lock
  client SIGKILLed keeps the session locked until `duoctl unlock`), Text
  Editor with the on-screen keyboard; after each teardown the keyboard
  went back to the editor.

## Verified, the one canvas (2026-11-07 evening)

Same kernel and Mesa; synthetic touches through `tools/touch.py` (`swipe`,
`liftdrag`, `dtap`, `cross`), screenshots, `get_state`, the journal.
Pictures: `out/shell-shots/m6-*.png`.

- **Header bar drag** (Text Editor on DSI-2, a drag from its header bar
  towards the hinge): GTK sent `xdg_toplevel.move` (`the client asked to be
  moved`, `window picked up`); the targets went DSI-2 -> DSI-2 left -> span
  while the finger moved; the picture under the finger and the span
  preview across both panels showed mid-drag (`m6-drag-span-*.png`); let go
  on the hinge edge it waited for the other side and dropped as span:
  Text Editor 1102 wide.
- **Double tap on a header bar**: GTK's own double click came as
  `unset_maximized` -> `window back on one panel ... why="title bar double
  tap"` (to DSI-2, where it came from).
- **Hold on a bar-less window and across the hinge** (foot on DSI-1:
  `cross`, held 600 ms at its top, carried to DSI-1's hinge edge, lifted,
  60 ms later down at DSI-2's hinge edge and on to its middle):
  `window picked up`, `let go at the hinge: waiting for the other side`,
  `carried window crosses the hinge from="DSI-1" to="DSI-2" lifted=true`,
  `dropped ... target="DSI-2"`: foot on DSI-2.
- **Double tap on a bar-less top** (foot): one panel -> span.
- **Back** from DSI-2's right edge: `Back key sent ... id=1`, `kind="back"`;
  from DSI-1's left edge with the app grid up: `to: shell`, the grid closed.
- **Home**: a long swipe up on DSI-1 -> `home output="DSI-1" windows=1`,
  `kind="home"`; the shell's home screen and the standing dock there
  (`m6-home-*.png`); a long swipe on the home panel -> `long` -> the app grid.
- **Top edge**: a swipe down from DSI-2's top -> `kind="down"`, the quick
  menu; with it down, a short swipe up from the bottom -> it closed (no dock).
- **Quick pull-downs on the left part of the status bar** (the earlier
  report): from y = 2, 26 and 41 px, 120 ms each: the quick menu opened all
  three times (the top 20 px are duo-wc's swipe now, below it GTK's drag).
- **Close as a request** (Text Editor with typed, unsaved text, closed
  from the open overview): the card dimmed with "Closing..." and the app's
  live "Save Changes?" in it (`m6-close-request-DSI-2.png`); 0.7 s later
  `the window answered its close: brought to the front`, the overview
  closed, Text Editor in front with its question (`m6-close-answer-...`).
  An empty draft closed at once (its card gone with the window).
- **Force close** (a throwaway foot stopped with SIGSTOP, then closed):
  after 2.5 s "Not responding" and the red "!" on its card
  (`m6-force-stuck-DSI-1.png`); a tap on it raised the shell's question
  (`m6-force-question-DSI-1.png`); "Force close": `SIGTERM ... pid=...`,
  3 s later `still there, SIGKILL`, the process gone.
- **The canvas**: Text Editor spanning, the overview open: its card in a
  row across both panels over the hinge, the label continuing across, the
  panels' own windows below, rounded cards with the accent ring
  (`m6-overview-canvas-*.png`). A split pair sent home leaves no divider
  over the home screen (fixed after the first try).
- **Carry to a half** (Text Editor's header bar to DSI-2's right edge): the
  right-half preview under the picture, dropped: foot left, Text Editor
  right (`m6-drag-half-*.png`). **Back arrow** mid-swipe
  (`m6-back-arrow-DSI-1.png`).
- **Glance** (`duoctl ambient glance`): `lit` = DSI-2 only, one line along
  its hinge-side edge at 34..54 px (`m6-glance-DSI-2.png`); the ruler for
  30 s on DSI-2 only (`m6-glance-ruler-DSI-2.png`); `ambient off` brought
  DSI-1 back.
- Note: the phone lay closed (posture `closed` from duo-posed, sensors on),
  so duo-posed blanked the panels again at some state changes; touches
  were tested with the panels lit by hand.
- After each install both outputs presented new frames (`get_outputs`).
- Unit tests: 134 (`./build-aarch64.sh test`): snap targets (side by side,
  stacked, one panel, previews), stitching (lift then down, both at once,
  too late / too far / not at the edge, stacked, thresholds), springs
  (settling, overshoot, retarget without a jump, rectangles), the edges
  (top, sides, corners, back distance and retreat, the resting finger),
  the overview's release and its span row, the tent tap, the glance
  lighting only its panel, config keys, event names, the force-close
  button's hit.

## Verified, popups, keyboard, fullscreen, closed phone, video (2026-11-07 afternoon)

Same kernel and Mesa; screenshots, `get_state`, the journal, touches through
`tools/touch.py`, keys through `tools/key.py`. Pictures:
`out/shell-shots/popup-*.png`, `fullscreen-*.png`, `ambient-*.png`,
`lock-swipe-DSI-1.png`, `quick-menu-tiles-centred-DSI-1.png`.

- **Popups**: Text Editor's main menu on DSI-2 with the keyboard up ran off
  the right edge (`popup-before-...`); after, the log says `popup fitted
  into its panel before=(355, 40, 241x522) after=(297, 40, 241x408)` and the
  menu sits inside the panel, ending above the keyboard. Spanning, the menu
  stays on DSI-2 under its button; the Open popover on the left fits as
  before.
- **Fullscreen**: Text Editor fullscreen across both panels: no status bar,
  a short swipe up logs `no dock over a fullscreen window`; the state's
  `fullscreen` lists the panels.
- **Keyboard**: a tap on the other panel's window while the keyboard was up
  logged `touch would move the keyboard: decided on its lift`, then `a tap
  takes the keyboard there`. The morning's journals had 21-38 `on-screen
  keyboard moved` lines per boot, many from touches at the panels' edges
  (x = 25-28 or 489-517 logical) in the middle of typing.
- **Closed phone** (`duoctl set_posture`, `tools/key.py power`): closed,
  dark, power key -> glance, both panels lit, a touch `on the ambient
  screen, not delivered`, dark again after 5 s (`glance over`); peek_left
  from dark -> peek, closed -> off; peek_right then open_dpm -> a normal lit
  screen. With `session_lock` on, posture closed -> the shell's duo-lock on
  both panels ("Swipe up to unlock"); a synthetic swipe up unlocked.
- **Folded back** (duo-posed `--fake`, acting): flip_p_left -> `single
  DSI-1`, DSI-2 off, a tap on DSI-2 `touch on a dark panel, not delivered`;
  turned accelerometer readings -> rotation 90 on both panels, back -> 0.
- **Video** (`tools/video-measure.sh`, Chromium 156, fullscreen across both
  panels at 2.5 with its own fractional scale, dmabufs; no hardware video
  decoder: no `/dev/video*`, VA-API fails to initialise):

  | video | renderer | gpu-process | duo-wc | flips/s per panel | decoded / dropped per s |
  | --- | --- | --- | --- | --- | --- |
  | H.264 1080p30 | 104 % | 26 % | 12 % | 30 | 30 / 0 |
  | H.264 1080p60 | 177 % | 39 % | 18-22 % | 59 | 60 / 0-2 |
  | VP9 1440p30 | 254 % | 27 % | 10 % | 29 | 26-31 / 0-1 |
  | AV1 1080p30 | 138 % | 25 % | 11 % | 29 | 22-30 / 0 |
  | AV1 1440p60 | 306 % | 55 % | 25 % | 58 | 50-61 / 0-2 |

  (% of one core; the GPU stays at its lowest clock, 315 MHz, 95-100 % of
  the time.) The compositor keeps up; decoding is software, and 1440p and
  up (what a site picks for a 2755 x 1892 device-pixel player) is where
  frames go. 60 fps content on the 90 Hz panels alternates one and two
  vblanks per frame. With wp_presentation Chromium ticks at 90 Hz (was 60):
  a plain video page costs the same; a page with its own animation loop
  costs more (60 fps video with an rAF loop: renderer 228 % instead of 177 %).
- After each install both outputs presented new frames (`get_outputs`).
- Unit tests: 109 (`./build-aarch64.sh test`): the popup's panel, the
  keyboard's tap rules, the ambient rules, the new event names.

## Verified, volume keys and the audio session (2026-11-07 day)

Same kernel and Mesa; volume keys through evemu clones of `gpio-keys` and
`pmic_resin` (same name and codes), the volume read back with `wpctl`.
Pictures: `out/shell-shots/audio-*.png`.

- Up 0.30 -> 0.35, down -> 0.30 -> 0.25, up -> 0.30; held 1.2 s down -> 0.00
  muted, held up -> 0.50 (one step, then every 100 ms after 400 ms). The
  volume display on DSI-1 (the active panel), gone 2 s later. With the
  screen blanked: up / down change the volume, `lit` stays empty, nothing
  shown; duo-wc logs `output=None`.
- The session on the user's bus: one `dbus-daemon --session` (the user
  manager's), the shell's notification name on it, `notify-send` from a plain
  ssh login reaches the shell; Text Editor dark (the portal, activated by the
  user manager with `WAYLAND_DISPLAY=/run/duo-wc/wayland-1`). `session.env`
  carries `PIPEWIRE_RUNTIME_DIR` and `PULSE_SERVER`; `paplay` and `aplay -D
  default` from that environment play through PipeWire.
- After each install both outputs presented new frames (`get_outputs`).
- Unit tests: 98 (`./build-aarch64.sh test`): the volume key codes and their
  event lines.

## Verified, splits, off commit, card labels, dock tap (2026-11-07 morning)

Same kernel and Mesa; without eyes (screenshots, `get_state`, the journal,
DRM debugfs, touches through `tools/touch.py`). Pictures:
`out/shell-shots/m5-*.png`.

- **Split over IPC**: `duoctl snap org.gnome.TextEditor right` -> foot left
  (0, 48, 166 x 448), Text Editor right (178, 48, 360 x 448: its min width
  moved the divider), divider and handle drawn. A synthetic drag on the
  divider (0.32 -> 0.22 of the panel) -> share 0.213, foot 112, Text Editor
  414; a drag to the far edge ended the split (both full again). A tap on
  the left half gave foot the keyboard. `snap ... bottom` -> stacked.
- **Poses and rotation**: `span` -> Text Editor spans 1102 wide, foot
  fills DSI-1 behind it, the split inactive; `dual` -> the split back;
  `single DSI-2` -> all three on DSI-2, `dual` -> the split back on DSI-1;
  DSI-1 at 90 -> top | bottom became left | right with the windows where
  they were on the glass.
- **Overview**: Text Editor's card carried to the left edge -> the left
  half previewed in blue, let go -> split, overview closed; a card held
  600 ms then carried to the top -> picked up (blue frame), top half
  previewed, let go -> stacked split.
- **Card labels**: icon and title over each card on both panels ("g
  (Draft) - Text Editor", "foot"); a tap on a label band, on a card and on
  the background went through the label surface (Card(3), Card(1), close).
- **Off commit**: `set_power all off` 2.7-5.3 ms (blocking way: 335-346
  ms); both panels off in ~330 ms (its event); 20 of 20 blank / wake
  cycles with both CRTCs `active=0` then `active=1` and new page flips;
  off then on after 0, 50, 150, 300 ms all back; one panel off / on,
  `single` / `dual` fine.
- **Dock first tap**: reproduced (2 of 3 when the keyboard moved panels
  with the swipe); found with WAYLAND_DEBUG + GDK_DEBUG=events in the
  shell and `pick()` at touch-begin (the dock got `wl_touch.down`, GTK
  picked the row: its buttons had just been rebuilt). After the fix 4 of
  4, 3 of 3 and 2 of 2 (with the overview) first taps pressed.
- Unit tests: 96 (`./build-aarch64.sh test`): split layout, sides on a
  turned panel (against the touch mapping), divider hit / drag / release,
  min sizes, drop sides; overview drops, lift, label bands, opening;
  the pill; config `atomic_off`.

## Verified, overview, wake, span keyboard, lock (2026-11-07 night)

Same kernel and Mesa; without eyes (screenshots, `get_state`, the journal,
DRM debugfs, touches through `tools/touch.py`). Pictures:
`out/shell-shots/m4b-*.png`.

- **Overview**: a swipe up that holds on DSI-2 opens it on both panels
  (`kind=hold`, travel 186 px); live pictures of foot (running `top`), Text
  Editor and foot; tap on a card -> that window in front with the keyboard,
  overview closed; swipe up on the Text Editor card -> it closed; a drag of a
  card to DSI-1's inner edge -> the window on DSI-2, overview still open; a
  drag held at the drop zone shows the card over the others and the blue
  strip; the arrow button moves too; a tap on the background closes it.
  Short and long swipes lift as `short` (travel 110) and `long` (375).
- **Wake**: 20 of 20 blank / wake cycles (`set_power all off` / `on`) with
  both CRTCs `active=1` in debugfs and a new page flip on each; the commit
  call took 73-690 us, both panels' wake commit done at 407-424 ms, first
  frame on screen 445 ms average (DSI-1, max 470) and 450 (DSI-2, max 482);
  one panel alone 210-234 ms. No "Failed to destroy old mode property blob"
  any more (no Smithay modeset on wake).
- **Span keyboard**: Text Editor spanning, its text tapped on DSI-1 ->
  squeekboard on DSI-2, the window 637 high on both panels (757 - bar - the
  span taskbar); tapped on DSI-2 -> keyboard on DSI-1; typing on it does not
  move it; back to dual, the editor on DSI-1 with the keyboard there.
- **Lock**: with `session_lock` off, `duo-lock` is refused (no global);
  switched on for the test with a throwaway PIN: locked, lock surfaces on
  both panels, only they drawn; a wrong PIN says so, digits show as dots, the
  right PIN unlocks (`locked: false`, client exit 0). Switched off again; no
  PIN is set on the phone.
- Unit tests: 79 (`./build-aarch64.sh test`): overview layout / hit / drag /
  release rules, icons, swipe kinds and hold, zone and exclusive edges,
  config keys, event names.

## Verified, shell protocols (2026-11-06 night, same kernel and Mesa)

Without eyes: `duoctl screenshot` (layer surfaces included), `get_state`,
the journal, `wayland-info`, `tools/toplevels.py`, and touches through
`tools/touch.py`. The pictures are in the owner's report.

- Globals: `zwlr_layer_shell_v1`, `zwp_text_input_manager_v3`,
  `zwp_input_method_manager_v2`, `zwp_virtual_keyboard_manager_v1`,
  `xdg_activation_v1`, `ext_foreign_toplevel_list_v1` next to the old ones.
- Status bars (`duo-shell`) on both panels, exclusive zone 48: windows at
  y = 48, h = 709.
- squeekboard: foot focused on DSI-1 -> keyboard on DSI-1 (terminal layout),
  foot shrinks to h = 448; tapping DSI-2's text editor moves it to DSI-2
  (DSI-1's window grows back, DSI-2's shrinks); the bar's keyboard button hides
  it and shows it again (`Visible` false / true). Typing on it reaches the
  focused field (the app grid's search: `te`).
- Edge swipe up on DSI-2 -> `edge_swipe` event -> the app grid on DSI-2, which
  takes the keyboard (nothing focused inside, so no keyboard shows); a tap in
  the edge strip is delivered as a tap on lift, a sideways drag from it is
  delivered from where it went down.
- Launch: `expect_window DSI-2` then Text Editor (GTK4) starts -> toplevel 3 on
  DSI-2 in front with the keyboard; it activates itself with GTK's
  xdg-activation token (honoured), its text view enables text-input and the
  keyboard comes up on DSI-2.
- ext-foreign-toplevel-list lists foot, foot, org.gnome.TextEditor with
  identifiers and titles.
- Event stream: `duo-shell` and `duo-posed` subscribed (`subscribers: 2`);
  `set_power all off` reaches duo-posed (accelerometers off) in 0.6 s.
- `span` with both bars: the spanning window at y = 48; DSI-2 rotated 90:
  bar and keyboard follow the landscape panel.
- Idle: the event loop now sleeps until something happens (it used to wake
  every 16 ms): blanked, duo-wc had 0 CPU ticks and 0 context switches in 30 s
  (before: 22 ticks, 1788 wake-ups); lit and still, ~1 % (the shell's and the
  sensors' own traffic). The event stream compares snapshots only after a turn
  that dispatched something. duo-shell, squeekboard and foot sit at 0 %.
  `systemctl restart duo-wc` takes 0.3 s (SIGTERM still ends the loop), the
  idle timer still blanks.

## Verified, milestone 2 (2026-11-06, same kernel and Mesa)

Without eyes on the panels: logs, IPC, DRM debugfs, battery current, and
uinput clones of the input devices (`evemu-device`: both touchscreens, the
power key, the volume key) with `wev` as the client.

- **Battery current** (`tools/power-test.sh current`: 20 s settle, 30 samples,
  modem stopped, the Pi's USB supplying ~100 mA; negative = drawn). The panels
  are AMOLED, so it depends on what they show:

  | state | dark content (two terminals) | white full screen on both |
  | --- | --- | --- |
  | both panels lit | -255 / -264 mA | -722 / -728 mA |
  | DSI-2 off, DSI-1 lit | -237 mA | -464 mA |
  | DSI-1 off, DSI-2 lit | -240 mA | -481 mA |
  | both off (blanked) | -168 mA | -162 mA |

  Both off saves ~90 mA over two dark terminals and ~560 mA over white. One
  panel off saves only ~20 mA with dark content: the panels share their
  supplies (vdda, vddio, vdd, the DDIC rail) and the display controller, which
  stay up while either panel is lit; with white content it saves ~250 mA (the
  emission).
- **Off/on cycles** (`tools/power-test.sh cycles 20`): DSI-1 20 of 20 and DSI-2
  20 of 20 came back: CRTC `active=0` while off and `active=1` after, a new
  page flip each time (`frames_presented`), switch-off 165-174 ms, first frame
  on screen 201-232 ms after the switch-on. Plus 20 power-key cycles (both
  panels at once, through the key clone): 20 of 20, both CRTCs back, ~430 ms
  until both show a frame (the two modesets run one after the other). Over the
  whole boot (100+ panel switches, rotations, poses) the kernel log has no
  DSI / DPU error, no frame-done timeout, no underrun; duo-wc's only warnings
  are Smithay's harmless "failed to destroy old mode property blob" at each
  switch-on (see gaps) and "unable to become drm master" at start (seatd
  already made it master).
- **Power key**: short press off, short press on; a 1.5 s and a 2 s hold do
  nothing, lit or dark. logind's `HandlePowerKey` reads `ignore` after the
  install (it was `poweroff`).
- **Idle**: with a 4 s timeout, taps every 2 s keep the screen lit; 4 s after
  the last tap both panels go off; a tap then (touch_wake off) and a volume key
  change nothing and reach no client (wev saw no key, no touch); the power key
  brings both back and the timer runs again.
- **Touch on dark panels** (wev on DSI-2): a tap on lit DSI-2 arrives at the
  centre (268.8, 378.4); with DSI-2 off a tap arrives nowhere; DSI-2 switched
  off under a held finger sends wev `cancel` and nothing of the release;
  touch_wake on: a tap on the blanked screen wakes it and is not delivered, the
  next tap is.
- **Poses**: `single DSI-1` moves both DSI-2 windows onto DSI-1 (DSI-1's own
  terminal stays in front), CRTC of DSI-2 inactive; `place_window span` and
  `set_power DSI-2 on` are refused; `dual` puts them back; `span` spans the
  active panel's front window 1102 x 757 and a window opened then spans too;
  `single DSI-2` straight from `span` folds the spanning window onto DSI-2, and
  `dual` returns it to DSI-1.
- **Rotation**: at 90 and 270 DSI-1 is 757 x 538 logical and its window is
  re-sized to it; a tap at the panel's (0.1, 0.9) lands in wev at (75.7, 53.8)
  at 90, (483.8, 75.7) at 180, (681.1, 483.8) at 270, (53.8, 681.1) at 0, which
  is the unit-tested mapping; the screenshot at 90 is 1892 x 1344 with the
  terminal upright and re-flowed. Both at 90: DSI-1 at (0, 0), DSI-2 at
  (0, 564); `span` then gives a 757 x 1102 window.
- Not verified: what any of it looks like on the panels (needs eyes; see the
  owner's checklist in the report), the real power key (the clone has the same
  name and code, the real `pmic_pwrkey` is opened by duo-wc), real fingers,
  `power_key_suspend = on` (would drop the WiFi link).

## Verified, milestone 1 (2026-11-06, kernel 7.3.0-duo2-gcf89883d2, Mesa 25.0.7)

Without eyes on the panels, from logs, IPC, DRM debugfs and screenshots:

- Both outputs come up: DSI-1 on crtc 93, DSI-2 on crtc 94, 1344x1892@60, scale
  2.5; `get_outputs` places them at x = 0 and 564 (gap on), 0 and 538 (gap off).
- Frames reach both CRTCs: "first frame on screen" for each after its page flip;
  `/sys/kernel/debug/dri/0/state` shows crtc-0 and crtc-1 `enable=1 active=1`,
  planes 45 and 51 with framebuffers "allocated by = duo-wc"; the
  `frames_presented` counters advance with client damage and stay still when
  nothing changes (duo-wc idles at ~0 % CPU).
- Clients: the session's two foot terminals map one per panel (`new toplevel
  id=1 output=DSI-1`, `id=2 output=DSI-2`); per-output screenshots show each
  terminal on its own panel, text readable at 2.5.
- Input classification: both duo2-touchd devices are taken with their outputs;
  "spi 045E:0C1D Touchscreen" and "... Stylus" are ignored.
- Touch end to end with clones of the two touch devices (same name, so the
  same udev `WL_OUTPUT`; `evemu-device` + `evemu-event`): a drag at the centre of
  the left device lands at (269, 378) on DSI-1, of the right device at
  (833, 378) = 564 + 269; `wev` with `WAYLAND_DEBUG=1` receives `wl_touch`
  down / motion / frame / up with the right surface-local coordinates.
- Hinge gap live: a spanning window is configured 1102 wide (gap on), 1076
  (off), 1102 again; a touch at the right panel's centre lands on it at x = 833
  (on) and 807 (off); the spanning window stays in front across switches.
- Not verified by measurement: what the panels actually look like (needs eyes),
  real finger input (the clones prove the path from the device on), 90 Hz.

## Known gaps

- X11 (2026-11-16): an app's own window moves and resizes are ignored, so
  an X11 app on a desk cannot be dragged by its own title bar (it has none
  from duo-wc either); no drag and drop or primary selection between X11
  and Wayland; the on-screen keyboard does not come up by itself in an X11
  text field; an X11 app on an external display with another scale draws
  at the panels' density (2.5) and is scaled down there. The X display
  `:N` and Smithay's own display (the next free one, as a rule `:N+1`)
  are both taken while Xwayland runs (`:N` while duo-wc runs).

- **Trust between processes of the same user rests on ptrace being closed**,
  and this kernel has no Yama (`CONFIG_SECURITY_YAMA` off; the image ships
  `kernel.yama.ptrace_scope = 1` for when it has). duo-wc, duo-shell and
  duo-lock are non-dumpable, but squeekboard (a Debian binary) and the
  session script are not: an app could attach to squeekboard and type with
  its virtual keyboard, or to the session script and use its trust factory.
  Building Yama into the kernel closes this (docs/hardware/security.md).
- Any process of the user can still send signals to the shell (kill(2)
  between processes of one user): a dead shell restarts in ~1 s, but while
  it is gone a fold does not lock. duo-wc keeps a lock it has; it cannot
  start one by itself.
- The screenshot's read-back (20-50 ms) is still on the loop; the rotation
  snapshot renders each lit panel once (1-3 ms, 20-40 ms the first time
  after a start) when a turn starts.
- A rotation's turn can still miss a vblank when the clients' re-layout
  keeps the GPU busy (one GPU ring, no preemption): 0-2 per turn with
  apps open; the first turn after duo-wc starts misses 2-3 (first-time
  allocations: the swapchain's third buffer for the frame rendered ahead,
  the picture textures). After a turn the clients' old buffers are freed in the kernel
  (GEM close: 2-45 ms each, 400 ms later, a few at a time between frames).
- While idle in landscape the home screen logs GTK's "Trying to measure
  HomeScreen for height of 686, but it needs at least 707" (the shell's
  layout is taller than a landscape panel).
- Neither switching off nor waking blocks the event loop any more (one
  non-blocking commit each way). The kernel still runs the two panels'
  sequences one after the other inside a commit: off ~330 ms, on ~450 ms
  until both show a frame.
- With `atomic_wake = off` Smithay logs `Failed to destroy old mode property
  blob` at every switch-on: harmless.
- Smithay's `TouchHandle::cancel` skips touch points whose last event was
  already framed; duo-wc re-sends each live point where it is (a zero-length
  motion) before cancelling (`state.rs` `cancel_all_touches`).
- Idle inhibit (2026-11-09 night) is built and host-tested, not yet tried
  on the phone. Android apps' "keep the screen on" does not reach it:
  Waydroid's hwcomposer binds the idle-inhibit manager but never makes an
  inhibitor (lineage-20), so a navigation app in Android blanks after the
  timeout. No brightness control and no dimming before the blank.
- `power_key_suspend` is untested on the phone on purpose, and needs a polkit
  rule for the duo-wc user; resume would leave the screen dark until the next
  power-key press.
- `set_touch_wake` and `set_idle_timeout` over IPC last until duo-wc restarts;
  the config file is the persistent setting.
- Poses and rotation come over IPC (by hand, or from `duo-posed` and the
  sensors, milestone 3). Layer-shell surfaces stay on their output in every
  pose (a status bar on a panel that `single` switches off stays there, dark),
  except the on-screen keyboard, which follows the keyboard focus.
- **Fixed in the image (2026-11-06): the serial getty on ttyGS0 wedged
  systemd's console lock** whenever no USB host read the ACM port (the phone
  on battery): its pre-exec TTY reset blocked holding `/dev/console`'s flock,
  and any unit with a TTY reset (a getty, a logind-style compositor unit) then
  froze PID 1. The getty on the gadget port no longer resets its TTY
  (`rootfs/overlay/etc/systemd/system/serial-getty@ttyGS0.service.d/`), so it
  can wait on the port without blocking anyone. duo-wc stays on seatd anyway.
- The overview's labels come from the shell (no shell, no titles); the
  cards' layout follows the windows' current sizes, so when the on-screen
  keyboard goes away as the overview opens, the grid settles once more. No
  closing animation, no scrolling (with many windows the cards get
  smaller); spanning windows have no "un-span" button (the pose does that).
  A split pair shows as two cards (Android shows one). The foreign-toplevel
  list stays read-only.
- Splits: halves only (no quarters); a split ends when duo-wc restarts
  (it is not saved); a client that refuses to be as narrow as its half is
  cut, and the divider stops where both minimum sizes fit; while the
  divider is dragged each move re-configures both windows (clients resize
  a frame or two late).
- In span the keyboard covers the bottom of the spanning window on its panel
  (by design: the text field is on the other one); a field typed into on the
  keyboard's own panel would be under it (no cursor-rectangle logic yet).
- Input-method popups (candidate lists) are not drawn.
- The session's clients share the user manager's D-Bus: restarting
  `user@UID.service` (never needed in normal use) takes the bus away, and
  GTK clients on it exit (the session starts the shell and the keyboard
  again; open apps are gone).
- No direct scan-out of client buffers (`NodeFilter::None`): a fullscreen
  video is composited on the GPU, which costs little (the GPU stays at its
  lowest clock); a spanning window could not be scanned out by one plane
  anyway. Screen capture (wlr-screencopy, ext-image-copy-capture) is for
  the portal and trusted clients only; a wl_shm capture's read-back is on
  the loop (~26 ms a frame while the screen changes, ~100 ms at idle clocks),
  a DMA-BUF one is not (docs/SCREENCAST.md). The lock screen
  is built but `session_lock` is off until the sensors are switched on
  (`duo2-sensors-mode on`, `sensors/`).
- Video: no hardware decoder on this kernel (no venus / iris V4L2 device),
  and Debian's Chromium would not use V4L2 anyway: decoding is software.
  60 fps content on the 90 Hz panels judders (1.5 vblanks per frame); a
  60 Hz mode while a fullscreen video plays would fix it (a modeset, not
  tried).
- The ambient strips' widths and panels are guesses until the owner looks
  at a peeking / closed phone (`~/.config/duo-shell/ambient.conf`).
- libinput's device-added events are processed with the first input event, so
  the "input device" log lines appear on the first touch or key, not at start.
- The one canvas (2026-11-07 evening): the hinge stitching is tested with
  synthetic contacts only (`tools/touch.py cross`), its thresholds wait for
  real fingers after duo2-touchd's edge work (`duoctl set_stitch`, the
  config keys); the drag across the hinge with a real finger is the
  owner's to try. A carried window's picture has square corners (only the
  overview's cards are rounded: a corner mask in the background colour,
  which a carried picture over arbitrary content cannot use); its old
  buffer is stretched to the new place for the frame or two until the
  client draws the new size.
- The compositor's top-strip hold and double tap are only for windows
  without a title bar (server-side decorations, fullscreen); a client with
  client-side decorations but no draggable bar has neither. The 350 ms after
  which a resting finger at a side or the top is delivered also delays a
  long press in those 20 px strips by as much.
- Tent: the tap on the dark panel is built and its rule unit-tested
  (`pose::tent_tap_switches`); it was not tried live (the phone lay closed, and
  duo-posed puts the real posture back within half a second).
