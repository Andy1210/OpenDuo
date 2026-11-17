# Screen sharing, recording and screenshots (and casting to a TV: research)

Status 2026-11-09: screen sharing through the desktop portal works on the
phone (the ScreenCast and Screenshot portals, a PipeWire stream recorded to a
file, the touch chooser). Casting to a TV is research only (the last part).

Marking: **verified** = measured on this phone or read in the source we run;
**reported** = upstream documentation or release notes; **inferred** = our
reasoning, to be confirmed.

## How it fits together

```
app (Firefox, Chromium, a recorder)            the user
   | D-Bus: org.freedesktop.portal.ScreenCast     | taps a panel
   v                                              v
xdg-desktop-portal (frontend, user unit)    chooser (shell/duo-screencast)
   | org.freedesktop.impl.portal.ScreenCast       ^ stdout: DSI-1 / DSI-2
   v                                              |
xdg-desktop-portal-wlr 0.7.1 (started by duo-wc's session, capture grant)
   | zwlr_screencopy_manager_v1 (copy_with_damage, wl_shm or DMA-BUF)
   v
duo-wc (compositor/duo-wc/src/capture.rs, screencopy.rs)
   frames -> PipeWire stream -> the app (WebRTC, GStreamer, ...)
```

- **duo-wc** implements **wlr-screencopy-unstable-v1, version 3** (what Debian
  trixie's xdg-desktop-portal-wlr 0.7.1 and grim 1.4 speak; verified in their
  sources) and **ext-image-copy-capture-v1** with
  **ext-output-image-capture-source-v1** (Smithay's handlers; what
  xdg-desktop-portal-wlr 0.8 and grim 1.5 use, for later). One engine for
  both: the output's scene is drawn again into the client's buffer (wl_shm or
  DMA-BUF) in the output's buffer space (1344 x 1892 whatever the rotation; the
  transform goes with it), with damage from a damage tracker per wlr manager
  and output or per ext session. Details: the module comment of
  `compositor/duo-wc/src/capture.rs` and `compositor/duo-wc/README.md`
  "Screen capture".
- **The portal backend** is Debian's `xdg-desktop-portal-wlr` 0.7.1,
  configured by `shell/duo-shell/portals.conf` for
  `org.freedesktop.impl.portal.ScreenCast` and `...Screenshot` (the GTK backend
  keeps everything else). duo-wc's session starts it
  (`shell/duo-screencast/portal`) before it restarts the portal frontend; its
  D-Bus-activated user unit is masked (started by the user manager it would
  have no capture connection).
- **The chooser** (`shell/duo-screencast/chooser`, libadwaita): a page on the
  panel touched last, one big button per panel ("Left panel", "Right panel",
  "(dark)" when off; "Both panels" for a screenshot), Cancel, 60 s. It prints
  the output name; nothing = declined.
- **The screenshot gate** (`shell/duo-screencast/grim`): the portal runs
  `grim` by name with no question of its own; this `grim` is first in the
  portal's PATH, asks the chooser every time, then runs `/usr/bin/grim` with a
  capture connection (`duoctl exec-trusted --capture`), at the panels' own
  scale (2.5). Colour picking (`PickColor`) is refused: it needs slurp, which
  needs the layer shell.
- **A notification** "Your screen is shared" while a stream runs (the
  backend's `exec_before` / `exec_after`, `shell/duo-screencast/notify`), and
  duo-wc's event stream has `capturing` (the panels being captured) for a
  status-bar indicator in the shell (not drawn yet).

## Who may capture (security)

- **The capture globals are guarded** (`crate::trust::Guarded::ScreenCapture`):
  apps on duo-wc's public socket do not see them (verified: `wayland-info` as
  an app lists none of `zwlr_screencopy_manager_v1`,
  `ext_image_copy_capture_manager_v1`,
  `ext_output_image_capture_source_manager_v1`). They are seen by the session's
  trusted clients, the administrator's (`duoctl exec-trusted --wayland`), and
  by **capture-only** clients.
- **The capture grant** is new (2026-11-09): a trust factory hands out a
  `capture` Wayland connection (the capture globals, none of the other guarded
  ones: no virtual keyboard, input method, lock, layer shell, toplevel list,
  clipboard) and a `capture-factory` that hands out only those. `duoctl
  exec-trusted --capture [--keep-capture] -- CMD`; the admin socket's
  `trusted_socket` takes the same kinds. The portal backend runs with a
  capture connection and a capture factory (for the screenshot gate); the
  session's full factory is closed before it starts.
- **Consent is the chooser**: every share asks (no persist mode:
  `XDPW_PERSIST_MODE` is unset, so no restore tokens), every screenshot asks
  (the gate). The backend's config is read by path
  (`/usr/local/lib/duo-screencast/xdpw.conf`, root's), never from
  `~/.config`, where an app could switch the chooser off.
- **The lock screen is never captured**: while the session is locked a frame
  is black (no element at all, not the lock surface), also under the peek and
  glance screens (unit-tested; not yet tried on the phone, where the lock is
  off by default).
- **Ptrace** (verified): `kernel.yama.ptrace_scope` is 1 on the phone, so an
  app cannot attach to the portal backend (not its descendant) and borrow its
  connection.
- **What stays open** (the usual Linux desktop model): while a stream runs,
  any native PipeWire client of the user could also connect to its node
  (WirePlumber restricts only sandboxed clients); an app of the user can kill
  the backend or take its bus name (`ALLOW_REPLACEMENT`): that denies or spoofs
  a request, it gains no capture; the frontend asks once whether unsandboxed
  apps may take non-interactive screenshots and keeps the answer for all of
  them (the gate still asks every time).

## Verified on the phone (2026-11-09, kernel 7.3.3-duo2-g9d07e7d6b)

- `duoctl exec-trusted --capture -- grim -o DSI-1 -s 2.5 out.png`: one panel,
  1345 x 1892; without `-o` both panels with the hinge gap, 2755 x 1892;
  colours and orientation right.
- Portal ScreenCast end to end (`duo-screencast-test record 15`): the chooser
  on the panel, a tap on "Left panel", the backend's PipeWire stream (node,
  `size` 538 x 757 logical), GStreamer `pipewiresrc` -> VP8 / WebM: a playable
  file showing the panel and the "Your screen is shared" banner; the event
  stream's `capturing: ["DSI-1"]`. After the app closes the session the
  stream ends within ~1 s and the notification goes.
- Portal Screenshot (`duo-screencast-test screenshot --interactive`): the
  chooser in screenshot mode, "Both panels", `file:///tmp/out.png`.
- Frames are drawn only when the panel changes (8 frames for the chooser's
  closing animation, then nothing on a still screen); a frame that waits 2 s
  without damage fails back to the backend (no drawing).
- Costs (wl_shm, a whole panel, on duo-wc's loop): ~2 ms to draw plus ~26 ms
  to read back while the screen is busy; ~10 + ~100 ms at idle clocks. The CPU
  reading the GPU's write-combined staging memory is most of it
  (glReadPixels straight into the client's buffer was measured no faster).
  GStreamer's `pipewiresrc` takes wl_shm buffers.
- **Not verified**: the DMA-BUF path (what browsers' WebRTC capturers ask
  for). duo-wc announces XRGB8888 / ARGB8888 with the renderer's four
  drawable modifiers, the backend allocated from the render node, but
  `pipewiresrc ! video/x-raw(memory:DMABuf)` found no common format (a
  GStreamer limitation, not tested further). Check it with a browser.

## How to try it (the owner)

Install (done on the phone 2026-11-09; a fresh image has it):
`shell/duo-screencast/install.sh` (the packages `xdg-desktop-portal-wlr`,
`grim`, the files, `portals.conf`), then `compositor/duo-wc/install.sh` (a
duo-wc restart, so the session starts the backend).

1. **A video call**: in Firefox or Chromium open a Jitsi, Meet or Teams call,
   "Share screen". The chooser comes up on the panel last touched: tap the
   panel to share. The other side sees that panel; a notification says it is
   shared; "Stop sharing" in the call ends it (the notification goes). Then,
   over ssh: `sudo journalctl -u duo-wc -b | grep "capture frame drawn" | tail`
   — `dma=true` means the browser took DMA-BUFs (the cheap path).
2. **A recording**: `duo-screencast-test record 10` in the session (an ssh
   login: `. /run/duo-wc/session.env` first) writes `~/Videos/screencast-*.webm`
   (half size, 15 fps, VP8). An app: Kooha from Flathub (`flatpak install
   flathub io.github.seadve.Kooha`) records through the same portal.
3. **A screenshot through the portal**: `duo-screencast-test screenshot
   --interactive`, or an app's "take screenshot" (the chooser asks; the first
   non-interactive one also brings the portal's own "Allow applications to take
   screenshots?" dialog once).
4. **The lock**: with the lock screen on, start a share, lock the phone: the
   other side sees black until it is unlocked.

Testing without fingers (`compositor/duo-wc/tools/touch.py`): **a synthetic
tap only after the target window is confirmed** (`duoctl get_state` lists
`org.duo2os.ScreencastChooser`, and a screenshot shows it): the chooser takes
1-3 s to appear, and a tap before that lands on the home screen (on
2026-11-09 two early taps opened Snapshot and Celluloid).

## Open items

- The DMA-BUF path with a browser (above), and Kooha.
- wl_shm streaming costs ~26 ms of duo-wc's loop per frame while the screen
  changes (`max_fps=30` in the backend's config bounds it). An asynchronous
  read-back (a fence, the copy off the loop) would take it off the loop.
- One panel at a time: xdg-desktop-portal-wlr 0.7.1 knows only outputs. Both
  panels as one source would need a virtual output (or ext-image-capture-source
  with xdg-desktop-portal-wlr 0.8, which can also share a window via the
  foreign-toplevel source).
- No app name in the chooser (the backend does not pass it), no cursor (there
  is none), no colour picking, a region of a turned panel only as the whole
  panel.
- The shell's status-bar indicator from the event stream's `capturing`.
- The lock test on the phone (the lock is off by default).

## Casting to a TV (research)

**GNOME Network Displays** (`gnome-network-displays`) is the candidate: 0.96.0
is in trixie (verified: `apt-cache policy` on the phone). It takes the screen
from the ScreenCast portal (libportal since 0.92.0, reported), so it would use
the pieces above, and encodes with GStreamer.

- **Miracast over Wi-Fi Direct (P2P)** (reported, README): needs
  **NetworkManager > 1.15.2 managing both the WiFi device and its `p2p-dev-*`
  device**, and wpa_supplicant built with P2P and Wi-Fi Display support.
  - The WiFi chip can (verified): `iw list` on the phone shows the interface
    modes managed, AP, P2P-client, P2P-GO, P2P-device, and the combinations
    `#{managed} <= 2, #{AP, P2P-client, P2P-GO} <= 16, #{P2P-device} <= 1,
    total <= 3, #channels <= 2` (staying on the home WiFi while a P2P group
    runs on another channel); ath11k's "wcn6855 hw1.1" entry (our WCN6851)
    lists the three P2P modes in `drivers/net/wireless/ath/ath11k/core.c`, and
    wpa_supplicant 2.10 already made a P2P device. Whether the mobile firmware
    really forms a group: inferred yes (Android uses it), untested.
  - The blocker is ours: the phone's WiFi is wpa_supplicant + networkd
    (`duo2-wifi-join`, the shell's WiFi from wpa_supplicant's control socket),
    no NetworkManager. Miracast through GNOME Network Displays means moving
    WiFi to NetworkManager (and the shell's WiFi page to its D-Bus API). A
    Miracast source of our own on wpa_supplicant (`p2p_find`, the WFD
    sub-elements, `p2p_connect`, then an RTSP server on 7236 and an H.264 /
    MPEG-TS stream) is the other way, and much more work.
  - Encoding: software H.264 (x264enc from gstreamer1.0-plugins-ugly, or
    openh264); no VA-API on this GPU, and the hardware encoder stays out of
    this (the iris encoder node is not to be touched). A 1344 x 1892 panel at
    30 fps in x264's `ultrafast` preset on the big cores: inferred workable
    at reduced size, to be measured.
- **Chromecast** (reported, NEWS 0.91.0) and **MICE** (Miracast over the
  infrastructure network, 0.91.0): over the home WiFi, no P2P and no
  NetworkManager needed for the link (inferred: the P2P path is the one that
  asks NetworkManager); discovery is mDNS through Avahi (systemd-resolved
  only from 0.98.0, newer than trixie), so `avahi-daemon` would be needed.
  MICE receivers are mostly Windows PCs and some TVs; Chromecast and Google TV
  are common. Untested.
- **Without new packages**: Chromium's own Cast (the Media Router) casts a tab
  to a Chromecast and, through the ScreenCast portal, a screen (reported for
  Chromium on Linux; whether Debian's build has it enabled is to be checked).

**What it would take**, cheapest first: (1) try Chromium's "Cast..." to a
Chromecast / Google TV on the home network; (2) install
`gnome-network-displays` + `avahi-daemon` + `gstreamer1.0-plugins-ugly` and try
its Chromecast path; (3) for Miracast to a TV: NetworkManager for the WiFi
(a decision for the owner: it replaces our WiFi stack), then GNOME Network
Displays' P2P path, with the firewall open for DHCP and RTSP (7236) on the
P2P interface.
