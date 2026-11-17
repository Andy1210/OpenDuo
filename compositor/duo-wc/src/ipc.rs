//! The control socket.
//!
//! A unix socket at `$XDG_RUNTIME_DIR/duo-wc.sock` (also in `$DUO_WC_SOCKET` for
//! the session), one JSON object per line, request and response matched by `id`.
//! `duoctl` (a small Python script next to the binary) drives it from a terminal.
//!
//! ```text
//! -> {"id":1,"request":"get_outputs"}
//! <- {"id":1,"ok":{"outputs":[{"name":"DSI-1","x":0,"y":0,"w":538,"h":757,...}]}}
//! -> {"id":2,"request":"set_hinge_gap","gap":"off"}
//! <- {"id":2,"ok":{"hinge_gap":"off"}}
//! -> {"id":3,"request":"place_window","window":1,"output":"span"}
//! <- {"id":3,"ok":{"window":1}}
//! -> {"id":4,"request":"set_power","output":"DSI-2","power":"off"}
//! -> {"id":5,"request":"set_pose","pose":"single","output":"DSI-1"}
//! -> {"id":6,"request":"set_rotation","output":"DSI-1","rotation":90}
//! -> {"id":7,"request":"set_idle_timeout","seconds":120}
//! -> {"id":8,"request":"expect_window","output":"DSI-2"}
//! -> {"id":9,"request":"subscribe"}
//! <- {"id":9,"ok":{"subscribed":true,"state":{...}}}
//! <- {"event":"state","changed":["power"],"state":{...}}       (see crate::events)
//! <- {"event":"edge_swipe","edge":"bottom","output":"DSI-1","kind":"long","travel":240.0}
//! <- {"event":"volume_key","key":"up","state":"pressed","output":"DSI-1"}   (output null: dark)
//! <- {"event":"pen_button","press":"click","output":"DSI-2","locked":false}   (crate::penbutton)
//! -> {"id":10,"request":"overview","action":"toggle"}
//! -> {"id":11,"request":"close_window","window":3}
//! -> {"id":12,"request":"snap_window","window":3,"side":"left"}
//! <- {"id":12,"ok":{"window":3,"partner":1,"output":"DSI-1","side":"left"}}
//! -> {"id":13,"request":"set_split_ratio","output":"DSI-1","ratio":0.4}
//! -> {"id":14,"request":"unsplit","output":"DSI-1"}
//! -> {"id":15,"request":"focus_output","output":"DSI-2"}
//! -> {"id":16,"request":"set_posture","posture":"closed"}      (duo-posed)
//! -> {"id":17,"request":"ambient","action":"glance"}            (peek | glance | off)
//! -> {"id":18,"request":"home","output":"DSI-2"}
//! -> {"id":19,"request":"toggle_span","window":3}
//! -> {"id":20,"request":"move_window","window":3,"target":"DSI-1 left"}   (or DSI-2, span)
//! -> {"id":21,"request":"set_stitch","edge":60,"ms":200}
//! -> {"id":23,"request":"set_ring","ring":"loud","ttl_ms":30000}   (loud | silent | off)
//! <- {"event":"ring_silence","key":"power"}   (the power key silenced a loud ring)
//! -> {"id":24,"request":"set_idle_hold","holds":[{"app":"firefox","pid":1234}],"ttl_ms":30000}
//! -> {"id":25,"request":"set_idle_inhibit","idle_inhibit":"off"}
//! -> {"id":26,"request":"set_keyboard","layouts":"hu,us","variants":""}   (or active, add)
//! -> {"id":27,"request":"set_pen","button":"off","dot":"on","input":"apps","apps":"rnote, krita"}
//!                                                     (any of them; crate::tablet)
//!
//! -> {"id":22,"request":"trusted_socket","kind":"wayland"}       (trusted only)
//! <- {"id":22,"ok":{"kind":"wayland"}}   + the descriptor (SCM_RIGHTS)
//! ```
//!
//! **Who may ask what** ([`crate::trust`], docs/hardware/security.md):
//!
//! - `duo-wc.sock` (0600, the GUI user's): every program of the user may read
//!   the state (`get_outputs`, `get_state`, `subscribe`) and give the launch
//!   hint (`expect_window`); everything else is refused (`not allowed`),
//!   because an app must not unlock, look at the screen or move other
//!   programs' windows. Root gets everything here too.
//! - `admin.sock` (0666 in the 0711 runtime directory, but answered by the
//!   peer's credentials): root and `admin_users` (duo2admin) everything,
//!   `sensor_users` (duo-posed) the posture, pose, rotation and power too;
//!   anyone else is refused at once.
//! - A connection from the session's trust factory (fd 3 of the session,
//!   `DUO_WC_TRUST_FD`): everything. `trusted_socket` hands out a trusted
//!   Wayland connection (`wayland`), a factory of one's own (`factory`), a
//!   Wayland connection that may only capture the screen (`capture`, the
//!   screen-cast portal's) or a factory of those (`capture-factory`).

use std::io::{ErrorKind, Read, Write};
use std::os::fd::{AsFd as _, AsRawFd as _, OwnedFd};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;

use anyhow::{Context as _, Result};
use serde::{Deserialize, Serialize};
use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::{Interest, LoopHandle, Mode, PostAction};
use smithay::wayland::seat::WaylandFocus as _;
use tracing::{debug, info, warn};

use crate::layout::HingeGap;
use crate::pose::Pose;
use crate::power::Power;
use crate::rotation::Rotation;
use crate::state::{window_data, DuoWc, Placement};
use crate::trust::{Class, Door, Role};

/// A request.
#[derive(Debug, Deserialize)]
#[serde(tag = "request", rename_all = "snake_case")]
pub enum Request {
    /// Every output: where it is in the global space and how it is doing.
    GetOutputs,
    /// The windows, the hinge gap, the scale and the active output.
    GetState,
    /// Switch the hinge gap live: `"on"` or `"off"`.
    SetHingeGap {
        /// The new mode.
        gap: HingeGap,
    },
    /// Move a window to an output (`"DSI-1"`), or across all of them (`"span"`).
    ///
    /// The window is named by `window` (the id from `get_state`) or `app_id`;
    /// with neither, the frontmost window on the active output.
    PlaceWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id (the frontmost window with it).
        #[serde(default)]
        app_id: Option<String>,
        /// An output name, or `span`.
        output: String,
    },
    /// Switch a panel (`"DSI-1"`) or `"all"` on or off. `all off` blanks the
    /// screen (the power key brings it back); one panel on from a blanked screen
    /// lights that panel only.
    SetPower {
        /// An output name, or `all`.
        output: String,
        /// `on` or `off`.
        power: Power,
    },
    /// Hold panels dark for the proximity sensor (the shell, during a call
    /// on the earpiece with something near): not lit, touches not
    /// delivered, but not a blank (no lock, the idle timer pauses while
    /// nothing else is lit). An empty list ends the hold; it also ends by
    /// itself after `ttl_ms` (default 30 s, at most 120 s) unless renewed.
    SetProximity {
        /// The panels, by name.
        #[serde(default)]
        panels: Vec<String>,
        /// How long the hold lasts unless renewed.
        #[serde(default)]
        ttl_ms: Option<u64>,
    },
    /// An incoming call rings (the shell, while one does): `loud` (the
    /// first short press of the power key silences it instead of switching
    /// the screen; `ring_silence` on the event stream, and `ring` becomes
    /// `silent`), `silent` (silenced, or waiting beside a call that is up:
    /// the power key as always) or `off`. While a call rings a glance stays
    /// lit and the idle timer does not blank. It ends by itself after
    /// `ttl_ms` (default 30 s, at most 120 s) unless renewed.
    SetRing {
        /// `loud`, `silent` or `off`.
        ring: String,
        /// How long it holds unless renewed.
        #[serde(default)]
        ttl_ms: Option<u64>,
    },
    /// The shell's idle hold (crate::idle): org.freedesktop.ScreenSaver's
    /// inhibitors, each with its application name and the caller's process
    /// id. A holder holds the idle timeout while one of its process's
    /// windows is seen, or (no window of its own: the portal's backend)
    /// while any app window is seen; never a dark, locked or home screen.
    /// An empty list ends the hold; it also ends by itself after `ttl_ms`
    /// (default 30 s, at most 120 s) unless renewed.
    SetIdleHold {
        /// The holders.
        #[serde(default)]
        holds: Vec<crate::idle::Holder>,
        /// How long it holds unless renewed.
        #[serde(default)]
        ttl_ms: Option<u64>,
    },
    /// The pen's settings, live (the config's `pen_button`, `pen_dot`,
    /// `pen_input`, `pen_apps`): any of them.
    SetPen {
        /// `right_click` or `off`.
        #[serde(default)]
        button: Option<String>,
        /// `on` or `off`.
        #[serde(default)]
        dot: Option<Power>,
        /// `apps` or `all`.
        #[serde(default)]
        input: Option<String>,
        /// The drawing apps, as `pen_apps` writes them (comma separated).
        #[serde(default)]
        apps: Option<String>,
    },
    /// Whether idle inhibitors and the shell's idle hold count at all (the
    /// config's `idle_inhibit`), live.
    SetIdleInhibit {
        /// `on` or `off`.
        idle_inhibit: Power,
    },
    /// A one-time grant for a root helper's action (the shell, before
    /// `duo-shell-helper mobile-data off`; crate::grants): a token, used once
    /// within 15 s.
    Grant {
        /// The action, as the helper's words (`mobile-data off`).
        action: String,
    },
    /// The helper (root, on the admin socket) redeems a grant: ok once.
    Redeem {
        /// The action.
        action: String,
        /// The token.
        token: String,
    },
    /// Seconds without input until the screen goes off; 0 never. Counts from now.
    SetIdleTimeout {
        /// Seconds.
        seconds: u64,
    },
    /// Whether a touch on a dark screen wakes it (the config's `touch_wake`).
    SetTouchWake {
        /// `on` or `off`.
        touch_wake: Power,
    },
    /// Draw every frame whole (the config's `full_redraw`), live.
    SetFullRedraw {
        /// `on` or `off`.
        full_redraw: Power,
    },
    /// Allow plane scan-out of client buffers (the config's `scanout`), live.
    SetScanout {
        /// `on` or `off`.
        scanout: Power,
    },
    /// `dual`, `single` (with `output`), or `span`.
    SetPose {
        /// The pose.
        pose: String,
        /// The panel for `single`.
        #[serde(default)]
        output: Option<String>,
    },
    /// Rotate an output: 0, 90, 180 or 270 degrees (the wl_output transform:
    /// 90 is drawn counter-clockwise, read with the device turned clockwise).
    SetRotation {
        /// Which output.
        output: String,
        /// Degrees.
        rotation: i64,
    },
    /// Keep the connection open and send an event line whenever the state
    /// changes (power, pose, rotation, hinge gap, focus, windows, the on-screen
    /// keyboard's panel) and on every edge swipe. See [`crate::events`].
    Subscribe,
    /// The next new window (within `timeout_ms`, default 10 s) opens on this
    /// output: what a launcher says before it starts an app.
    ExpectWindow {
        /// The output.
        output: String,
        /// How long the hint holds.
        #[serde(default)]
        timeout_ms: Option<u64>,
    },
    /// How the phone is folded (duo-posed): Microsoft's posture name or a
    /// hinge class (`closed`, `peek`, ...). A closed phone's power key gives
    /// the glance strip; peeking at a dark one the peek screen.
    SetPosture {
        /// The posture.
        posture: String,
    },
    /// Show or end an ambient screen by hand (testing): `peek`, `glance`
    /// (ends by itself after 5 s) or `off` (the panels as they were).
    Ambient {
        /// `peek`, `glance` or `off`.
        action: String,
    },
    /// Make a panel the active one and give the keyboard to what is in front
    /// there (the status bar's keyboard button: the on-screen keyboard comes
    /// up on that panel). Touching a bar no longer moves the keyboard.
    FocusOutput {
        /// The output.
        output: String,
    },
    /// Bring a window to the front of its panel and give it the keyboard.
    ActivateWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id (the frontmost window with it).
        #[serde(default)]
        app_id: Option<String>,
    },
    /// Set one window aside (minimized: not shown, still running), as the
    /// client's own minimize does: a desk's taskbar, its app clicked again.
    /// `activate_window` brings it back.
    MinimizeWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id (the frontmost window with it).
        #[serde(default)]
        app_id: Option<String>,
    },
    /// Whether swipes from a panel's bottom edge are the compositor's (the
    /// config's `edge_swipe`), live.
    SetEdgeSwipe {
        /// `on` or `off`.
        edge_swipe: Power,
    },
    /// A panel as the external display's touchpad, or none
    /// ([`crate::trackpad`]).
    SetTrackpad {
        /// `DSI-1`, `DSI-2` or `off`.
        trackpad: String,
    },
    /// A connected display used as a desk or a mirror of the phone, now
    /// ([`crate::mirror`]); answers the shell's question about it.
    SetDisplay {
        /// The display (DP-1); the only one connected if left out.
        #[serde(default)]
        output: Option<String>,
        /// `desk` or `mirror`.
        mode: String,
    },
    /// The display settings, live and kept: what a display plugged in is
    /// used as (`on_connect`: ask, desk, mirror) and what a mirror shows
    /// (`mirror`: active, both, DSI-1, DSI-2).
    SetExternal {
        #[serde(default)]
        on_connect: Option<String>,
        #[serde(default)]
        mirror: Option<String>,
    },
    /// The keyboard layouts ([`crate::kbdlayout`]): the list (`layouts`
    /// `hu,us`, `variants` aligned: the system's, from the shell), the one in
    /// force (`active`: `next`, an index or a layout of the list), or one
    /// put in force and added to the list if it is not in it (`add`, with
    /// `variant`).
    SetKeyboard {
        /// The list, comma-separated.
        #[serde(default)]
        layouts: Option<String>,
        /// Their variants, comma-separated (empty: the defaults).
        #[serde(default)]
        variants: Option<String>,
        /// `next`, an index, or a layout of the list.
        #[serde(default)]
        active: Option<String>,
        /// A layout to put in force (added to the list if missing).
        #[serde(default)]
        add: Option<String>,
        /// Its variant.
        #[serde(default)]
        variant: Option<String>,
    },
    /// Laptop mode ([`crate::laptopmode`]): on, off or toggle; and / or its
    /// settings live (`auto`: the laptop posture turns it on; `keys`: the
    /// keyboard panel when the posture does not say).
    SetLaptop {
        /// `on`, `off` or `toggle`.
        #[serde(default)]
        laptop: Option<String>,
        /// `on` or `off`.
        #[serde(default)]
        auto: Option<String>,
        /// `DSI-1` or `DSI-2`.
        #[serde(default)]
        keys: Option<String>,
    },
    /// Whether panels waking together come on in non-blocking commits, one
    /// panel after the other (the config's `atomic_wake`), live.
    SetAtomicWake {
        /// `on` or `off`.
        atomic_wake: Power,
    },
    /// Whether panels going off together do so in one non-blocking commit
    /// (the config's `atomic_off`), live.
    SetAtomicOff {
        /// `on` or `off`.
        atomic_off: Power,
    },
    /// Whether lock clients may lock the phone (the config's `session_lock`),
    /// live: the ext-session-lock global is offered only while on.
    SetSessionLock {
        /// `on` or `off`.
        session_lock: Power,
    },
    /// End a lock, whatever the lock client does (the way back over ssh).
    Unlock,
    /// Open, close or toggle the task overview (on every lit panel).
    Overview {
        /// `open`, `close` or `toggle` (the default).
        #[serde(default)]
        action: Option<String>,
    },
    /// Ask a window to close (what the overview's X does).
    CloseWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id (the frontmost window with it).
        #[serde(default)]
        app_id: Option<String>,
    },
    /// Put a window on one half of a panel (`left`, `right`, `top`,
    /// `bottom`, as the panel reads now) and another window on the other
    /// half: `with`, else the window's partner if the panel is split already
    /// (it changes sides) or the window across from that half, else the
    /// panel's front window. The panel is `output`, else the window's own.
    SnapWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id (the frontmost window with it); with
        /// neither, the frontmost window on the active output.
        #[serde(default)]
        app_id: Option<String>,
        /// Which half.
        side: String,
        /// The panel.
        #[serde(default)]
        output: Option<String>,
        /// The window for the other half.
        #[serde(default)]
        with: Option<u64>,
    },
    /// End a panel's split: both windows fill the panel again.
    Unsplit {
        /// The panel.
        output: String,
    },
    /// How a panel's split shares the room: the share of the window on the
    /// left (or top), 0.2 to 0.8.
    SetSplitRatio {
        /// The panel.
        output: String,
        /// The share.
        ratio: f64,
    },
    /// Home on a panel: its windows are set aside (minimized), the shell's
    /// home screen shows.
    Home {
        /// The panel; the active one if none.
        #[serde(default)]
        output: Option<String>,
    },
    /// One panel <-> both panels (what a double tap on a window's top does).
    ToggleSpan {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id; with neither, the active panel's front window.
        #[serde(default)]
        app_id: Option<String>,
    },
    /// Put a window on a snap target, as if carried there: `DSI-1`,
    /// `DSI-2 left` (a half: left, right, top, bottom), `span`. It flies
    /// there.
    MoveWindow {
        /// Window id.
        #[serde(default)]
        window: Option<u64>,
        /// Or the client's app id.
        #[serde(default)]
        app_id: Option<String>,
        /// The target.
        target: String,
    },
    /// Kill a window's client (after the user confirmed: unsaved data is
    /// lost): SIGTERM, then SIGKILL after 3 s.
    ForceCloseWindow {
        /// Window id.
        window: u64,
    },
    /// Which side edges swipe back: `both`, `outer`, `off`.
    SetBackSwipe {
        /// The setting.
        back_swipe: String,
    },
    /// The thresholds for carrying a drag across the hinge (until restart;
    /// what is not given stays).
    SetStitch {
        /// How close to the hinge-side edge (logical px).
        #[serde(default)]
        edge: Option<f64>,
        /// Within how long the other side is touched (ms).
        #[serde(default)]
        ms: Option<u32>,
        /// How far apart along the hinge (logical px).
        #[serde(default)]
        offset: Option<f64>,
    },
    /// The snap thresholds (until restart).
    SetSnap {
        /// At the hinge within this many px: the other panel.
        #[serde(default)]
        hinge_band: Option<f64>,
        /// A half within this share of a panel's edge.
        #[serde(default)]
        half_share: Option<f64>,
        /// The span pad's start along the hinge (share).
        #[serde(default)]
        pad_from: Option<f64>,
        /// Its end (share).
        #[serde(default)]
        pad_to: Option<f64>,
    },
    /// Windows animate (on) or just appear (off), until restart.
    SetAnimations {
        /// `on` or `off`.
        animations: String,
    },
    /// How long a turned panel's picture turns (ms, 0 = it jumps), until restart.
    SetRotateAnim {
        /// Milliseconds, 0 to 5000.
        ms: u64,
        /// The turn's frames rendered one ahead: `on` (default) or `off`.
        #[serde(default)]
        ahead: Option<String>,
    },
    /// Ask the user (the shell's question) before an action only the
    /// session may take: `restart-display` (Duo Settings' helper, as root,
    /// for a refresh rate or a language change). Answered by the shell with
    /// `confirm_answer`.
    Confirm {
        /// `restart-display`, `remove-packages`, `esim` or `helper`.
        action: String,
        /// What it is about, shown in the question (the packages).
        #[serde(default)]
        detail: Option<String>,
        /// Answer only once the user has (yes / no, or no after
        /// CONFIRM_WAIT): polkit's checker waits for it.
        #[serde(default)]
        wait: bool,
    },
    /// The shell's answer to a `confirm_request` event.
    ConfirmAnswer {
        /// The question's id (`confirm`: `id` is the envelope's).
        confirm: u64,
        /// The user said yes.
        yes: bool,
    },
    /// Render one output's scene to a PNG, for looking at it from a terminal.
    Screenshot {
        /// Which output.
        output: String,
        /// Where to write it.
        path: String,
    },
    /// A trusted socket, sent back with the reply (SCM_RIGHTS): `wayland` (a
    /// Wayland connection that sees the guarded globals, for WAYLAND_SOCKET),
    /// `factory` (a trust factory of one's own, for DUO_WC_TRUST_FD),
    /// `capture` (a Wayland connection that may capture the screen, nothing
    /// else guarded) or `capture-factory` (a factory of only those).
    TrustedSocket {
        /// `wayland`, `factory`, `capture` or `capture-factory`.
        kind: String,
    },
}

/// How sensitive a request is ([`crate::trust`]). Every request is named
/// here, so a new one cannot slip through unclassified.
pub fn class(request: &Request) -> Class {
    match request {
        Request::GetOutputs | Request::GetState | Request::Subscribe => Class::Read,
        Request::ExpectWindow { .. } => Class::Hint,
        Request::Confirm { .. } => Class::Confirm,
        Request::SetPosture { .. }
        | Request::SetPose { .. }
        | Request::SetRotation { .. }
        | Request::SetPower { .. } => Class::Sensors,
        Request::SetHingeGap { .. }
        | Request::PlaceWindow { .. }
        | Request::SetIdleTimeout { .. }
        | Request::SetTouchWake { .. }
        | Request::SetFullRedraw { .. }
        | Request::SetScanout { .. }
        | Request::Ambient { .. }
        | Request::FocusOutput { .. }
        | Request::ActivateWindow { .. }
        | Request::MinimizeWindow { .. }
        | Request::SetEdgeSwipe { .. }
        | Request::SetAtomicWake { .. }
        | Request::SetAtomicOff { .. }
        | Request::SetSessionLock { .. }
        | Request::Unlock
        | Request::Overview { .. }
        | Request::CloseWindow { .. }
        | Request::SnapWindow { .. }
        | Request::Unsplit { .. }
        | Request::SetSplitRatio { .. }
        | Request::Home { .. }
        | Request::ToggleSpan { .. }
        | Request::MoveWindow { .. }
        | Request::SetTrackpad { .. }
        | Request::SetDisplay { .. }
        | Request::SetExternal { .. }
        | Request::SetLaptop { .. }
        | Request::SetKeyboard { .. }
        | Request::ForceCloseWindow { .. }
        | Request::SetBackSwipe { .. }
        | Request::SetStitch { .. }
        | Request::SetSnap { .. }
        | Request::SetProximity { .. }
        | Request::SetRing { .. }
        | Request::SetIdleHold { .. }
        | Request::SetIdleInhibit { .. }
        | Request::SetPen { .. }
        | Request::Grant { .. }
        | Request::Redeem { .. }
        | Request::SetAnimations { .. }
        | Request::SetRotateAnim { .. }
        | Request::ConfirmAnswer { .. }
        | Request::Screenshot { .. }
        | Request::TrustedSocket { .. } => Class::Privileged,
    }
}

#[derive(Debug, Serialize)]
struct Response<'a> {
    id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    ok: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'a str>,
}

#[derive(Debug, Deserialize)]
struct Envelope {
    #[serde(default)]
    id: Option<u64>,
    #[serde(flatten)]
    request: Request,
}

/// The longest request line accepted; a peer that never sends a newline is
/// dropped rather than buffered forever.
const MAX_LINE: usize = 64 * 1024;

/// A control connection's bytes, cut into request lines. At most a little
/// over [`MAX_LINE`] is held: reading stops there until the lines in it are
/// answered (the source is level-triggered, it fires again).
#[derive(Debug, Default)]
struct Lines(Vec<u8>);

impl Lines {
    /// Add what was read; false when no more should be read before the
    /// lines held are taken.
    fn push(&mut self, bytes: &[u8]) -> bool {
        self.0.extend_from_slice(bytes);
        self.0.len() <= MAX_LINE
    }

    /// The next whole line, without its newline.
    fn next_line(&mut self) -> Option<Vec<u8>> {
        let end = self.0.iter().position(|byte| *byte == b'\n')?;
        let mut line: Vec<u8> = self.0.drain(..=end).collect();
        line.pop();
        Some(line)
    }

    /// What is left once the lines are taken is longer than any request.
    fn overlong(&self) -> bool {
        self.0.len() > MAX_LINE
    }
}

/// Who a door answers: the administrators and the sensor users by name
/// (looked up at every connection: a user made later counts at once).
#[derive(Debug, Clone, Default)]
pub struct Doorkeeper {
    /// `admin_users` from the config.
    pub admin_users: Vec<String>,
    /// `sensor_users` from the config.
    pub sensor_users: Vec<String>,
    /// `confirm_users` from the config (polkit: it may only ask the user).
    pub confirm_users: Vec<String>,
}

impl Doorkeeper {
    fn role(&self, door: Door, stream: &UnixStream) -> Option<(Role, i32, u32)> {
        let (pid, uid, _gid) = match crate::trust::peer_cred(stream) {
            Ok(cred) => cred,
            Err(err) => {
                warn!(?err, "no peer credentials on a control connection");
                return None;
            }
        };
        // Safety: getuid cannot fail.
        let own = unsafe { libc::getuid() };
        let (admins, sensors) = match door {
            Door::User => (Vec::new(), Vec::new()),
            Door::Admin => {
                (crate::trust::uids_of(&self.admin_users), crate::trust::uids_of(&self.sensor_users))
            }
        };
        crate::trust::role_for_peer(door, uid, own, &admins, &sensors)
            .or_else(|| {
                let confirmers = match door {
                    Door::User => Vec::new(),
                    Door::Admin => crate::trust::uids_of(&self.confirm_users),
                };
                crate::trust::confirmer_role(door, uid, own, &confirmers)
            })
            .map(|role| (role, pid, uid))
    }
}

/// Start listening on a door, and return the path so it can be handed to
/// children. The user's door is 0600; the admin door is 0666 (its runtime
/// directory 0711: reachable by path, not listable) and answers by the
/// peer's credentials alone.
pub fn listen(
    loop_handle: &LoopHandle<'static, DuoWc>,
    path: PathBuf,
    door: Door,
    keeper: Doorkeeper,
) -> Result<PathBuf> {
    let _ = std::fs::remove_file(&path);
    let listener = UnixListener::bind(&path)
        .with_context(|| format!("failed to bind the control socket at {}", path.display()))?;
    let mode = match door {
        Door::User => 0o600,
        Door::Admin => 0o666,
    };
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).with_context(|| {
        format!("failed to set the permissions of the control socket at {}", path.display())
    })?;
    listener.set_nonblocking(true).context("failed to make the control socket non-blocking")?;

    loop_handle
        .insert_source(Generic::new(listener, Interest::READ, Mode::Level), move |_, listener, _state| {
            loop {
                match listener.accept() {
                    Ok((stream, _)) => accept(stream, door, &keeper),
                    Err(err) if err.kind() == ErrorKind::WouldBlock => break,
                    Err(err) => {
                        warn!(?err, "failed to accept a control connection");
                        break;
                    }
                }
            }
            Ok(PostAction::Continue)
        })
        .map_err(|err| anyhow::anyhow!("failed to insert the control socket source: {err}"))?;

    Ok(path)
}

fn accept(stream: UnixStream, door: Door, keeper: &Doorkeeper) {
    let Some((role, pid, uid)) = keeper.role(door, &stream) else {
        // Blocking is fine for one short line to a fresh connection.
        let _ = send(
            &stream,
            r#"{"id":null,"error":"not allowed: this socket does not answer you (docs/hardware/security.md)"}"#,
        );
        tracing::info!(?door, "control connection refused");
        return;
    };
    if let Err(err) = stream.set_nonblocking(true) {
        warn!(?err, "failed to configure a control connection");
        return;
    }
    debug!(?door, role = role.name(), pid, uid, "control connection accepted");
    CONNECTIONS.with(|pending| pending.borrow_mut().push((stream, role)));
}

/// Register a control connection made by duo-wc itself (the trust factory's).
fn adopt(stream: UnixStream, role: Role) -> std::io::Result<()> {
    stream.set_nonblocking(true)?;
    CONNECTIONS.with(|pending| pending.borrow_mut().push((stream, role)));
    Ok(())
}

thread_local! {
    static CONNECTIONS: std::cell::RefCell<Vec<(UnixStream, Role)>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// Register any connection accepted since the last call (from the event loop's
/// per-iteration callback, which has the loop handle).
pub fn register_pending(state: &mut DuoWc) {
    let pending: Vec<(UnixStream, Role)> =
        CONNECTIONS.with(|pending| pending.borrow_mut().drain(..).collect());
    for (stream, role) in pending {
        let mut buffer = Lines::default();
        let inserted = state.loop_handle.insert_source(
            Generic::new(stream, Interest::READ, Mode::Level),
            move |_, stream, state: &mut DuoWc| {
                let mut chunk = [0u8; 4096];
                loop {
                    match (&**stream).read(&mut chunk) {
                        Ok(0) => return Ok(PostAction::Remove),
                        Ok(n) => {
                            if !buffer.push(&chunk[..n]) {
                                break;
                            }
                        }
                        Err(err) if err.kind() == ErrorKind::WouldBlock => break,
                        Err(err) if err.kind() == ErrorKind::Interrupted => continue,
                        Err(err) => {
                            warn!(?err, "control connection failed");
                            return Ok(PostAction::Remove);
                        }
                    }
                }

                state.events_dirty = true;
                while let Some(line) = buffer.next_line() {
                    let (reply, fd) = handle_line(state, &line, stream, role);
                    let sent = match fd {
                        Some(fd) => send_line_with_fd(stream, &reply, &fd),
                        None => send(stream, &reply),
                    };
                    if let Err(err) = sent {
                        warn!(?err, "failed to answer a control request");
                        return Ok(PostAction::Remove);
                    }
                }
                if buffer.overlong() {
                    warn!("a control connection sent a line longer than any request");
                    return Ok(PostAction::Remove);
                }
                Ok(PostAction::Continue)
            },
        );
        if let Err(err) = inserted {
            warn!(?err, "failed to register a control connection");
        }
    }
}

fn handle_line(state: &mut DuoWc, line: &[u8], stream: &UnixStream, role: Role) -> (String, Option<OwnedFd>) {
    if line.iter().all(u8::is_ascii_whitespace) {
        return (String::new(), None);
    }
    let envelope: Envelope = match serde_json::from_slice(line) {
        Ok(envelope) => envelope,
        Err(err) => {
            return (encode(&Response { id: None, ok: None, error: Some(&err.to_string()) }), None);
        }
    };
    let id = envelope.id;
    if !role.permits(class(&envelope.request)) {
        let name = request_name(line);
        tracing::info!(request = name, role = role.name(), "control request refused");
        return (
            encode(&Response {
                id,
                ok: None,
                error: Some(&format!(
                    "not allowed: {name} is for the session and the administrator, not for apps \
                     (docs/hardware/security.md)"
                )),
            }),
            None,
        );
    }
    if let Request::TrustedSocket { kind } = &envelope.request {
        return match trusted_socket(state, kind) {
            Ok(fd) => (
                encode(&Response { id, ok: Some(serde_json::json!({ "kind": kind })), error: None }),
                Some(fd),
            ),
            Err(err) => (encode(&Response { id, ok: None, error: Some(&err.to_string()) }), None),
        };
    }
    if let Request::Confirm { action, detail, wait: true } = &envelope.request {
        // Answered when the user has (crate::state::DuoWc::confirm_answered).
        let reply = match stream.try_clone() {
            Ok(clone) => clone,
            Err(err) => {
                return (
                    encode(&Response {
                        id,
                        ok: None,
                        error: Some(&format!("failed to answer later: {err}")),
                    }),
                    None,
                )
            }
        };
        return match state.ask_confirm(action, detail.as_deref(), Some((reply, id))) {
            Ok(_) => (String::new(), None),
            Err(err) => (encode(&Response { id, ok: None, error: Some(&err.to_string()) }), None),
        };
    }
    if let Request::Screenshot { output, path } = &envelope.request {
        // The scene is read back here (it needs the renderer: a few ms);
        // the PNG is encoded and written in a thread, which answers on a
        // clone of this connection when it is done. duo-wc's loop goes on
        // meanwhile (the encoding held every client up for ~4 s).
        let started = std::time::Instant::now();
        let captured = state.screenshot(output);
        let read_ms = started.elapsed().as_millis();
        let fail = |err: String| (encode(&Response { id, ok: None, error: Some(&err) }), None);
        let (w, h, pixels) = match captured {
            Ok(captured) => captured,
            Err(err) => return fail(err.to_string()),
        };
        let clone = match stream.try_clone() {
            Ok(clone) => clone,
            Err(err) => return fail(format!("failed to answer later: {err}")),
        };
        let path = path.clone();
        let spawned = std::thread::Builder::new().name("duo-wc-png".into()).spawn(move || {
            let written = crate::screenshot::write_png(std::path::Path::new(&path), w, h, &pixels);
            let reply = match written {
                Ok(()) => encode(&Response {
                    id,
                    ok: Some(serde_json::json!({ "path": path, "w": w, "h": h })),
                    error: None,
                }),
                Err(err) => encode(&Response { id, ok: None, error: Some(&err.to_string()) }),
            };
            tracing::info!(read_ms, total_ms = started.elapsed().as_millis(), "screenshot written");
            if let Err(err) = send(&clone, &reply) {
                debug!(?err, "the screenshot's asker went away");
            }
        });
        return match spawned {
            Ok(_) => (String::new(), None),
            Err(err) => fail(format!("failed to start the PNG thread: {err}")),
        };
    }
    let reply = if matches!(envelope.request, Request::Subscribe) {
        match stream.try_clone() {
            Ok(clone) => {
                let snapshot = state.snapshot();
                if state.last_snapshot.is_none() {
                    state.last_snapshot = Some(snapshot.clone());
                }
                state.subscribers.push(clone);
                debug!(subscribers = state.subscribers.len(), "event stream subscribed");
                encode(&Response {
                    id,
                    ok: Some(serde_json::json!({ "subscribed": true, "state": snapshot })),
                    error: None,
                })
            }
            Err(err) => {
                encode(&Response { id, ok: None, error: Some(&format!("failed to subscribe: {err}")) })
            }
        }
    } else {
        match dispatch(state, envelope.request) {
            Ok(value) => encode(&Response { id, ok: Some(value), error: None }),
            Err(err) => encode(&Response { id, ok: None, error: Some(&err.to_string()) }),
        }
    };
    (reply, None)
}

/// Answer a `confirm` that waited for the user (`yes`), on its connection.
pub fn answer_confirm(stream: &UnixStream, id: Option<u64>, yes: bool) {
    let reply = encode(&Response { id, ok: Some(serde_json::json!({ "yes": yes })), error: None });
    if let Err(err) = send(stream, &reply) {
        debug!(?err, "the confirm's asker went away");
    }
}

/// The `request` field of a line, for messages (the line parsed already).
fn request_name(line: &[u8]) -> String {
    serde_json::from_slice::<serde_json::Value>(line)
        .ok()
        .and_then(|v| v.get("request").and_then(|r| r.as_str()).map(str::to_owned))
        .unwrap_or_else(|| "this request".to_owned())
}

/// A trusted socket for `trusted_socket`: a Wayland connection or a factory.
fn trusted_socket(state: &mut DuoWc, kind: &str) -> Result<OwnedFd> {
    match kind {
        "wayland" => Ok(OwnedFd::from(state.trusted_wayland_client()?)),
        "factory" => factory(&state.loop_handle, crate::trust::FactoryLevel::Full),
        "capture" => Ok(OwnedFd::from(state.capture_wayland_client()?)),
        "capture-factory" => factory(&state.loop_handle, crate::trust::FactoryLevel::Capture),
        other => {
            anyhow::bail!("trusted_socket kind is wayland, factory, capture or capture-factory, not {other}")
        }
    }
}

/// A trust factory: a SOCK_SEQPACKET socketpair whose other end is returned
/// (for the session: its fd 3). Each packet `wayland`, `control`, `capture`
/// or `capture-factory` on it is answered with `ok` and a fresh connection
/// of that kind (SCM_RIGHTS), anything else, or anything its `level` does
/// not hand out, with `error ...`. Whoever holds a full factory's end is
/// trusted: it is handed only to the session and to the administrator; a
/// capture factory ([`crate::trust::FactoryLevel::Capture`]) goes to the
/// screen-cast portal.
pub fn factory(
    loop_handle: &LoopHandle<'static, DuoWc>,
    level: crate::trust::FactoryLevel,
) -> Result<OwnedFd> {
    let (ours, theirs) = crate::trust::seqpacket_pair().context("failed to make a trust factory")?;
    loop_handle
        .insert_source(Generic::new(ours, Interest::READ, Mode::Level), move |_, ours, state: &mut DuoWc| {
            loop {
                let packet = match crate::trust::recv_packet(&ours.as_fd()) {
                    Ok(Some(packet)) if packet.is_empty() => return Ok(PostAction::Remove),
                    Ok(Some(packet)) => packet,
                    Ok(None) => return Ok(PostAction::Continue),
                    Err(err) => {
                        warn!(?err, "trust factory failed");
                        return Ok(PostAction::Remove);
                    }
                };
                state.events_dirty = true;
                let ask = crate::trust::parse_factory_packet(&packet);
                let made: Result<OwnedFd> = match ask {
                    Some(ask) if !level.permits(ask) => {
                        tracing::info!(?ask, ?level, "trust factory: not this factory's to give");
                        Err(anyhow::anyhow!("this factory hands out capture connections only"))
                    }
                    Some(crate::trust::FactoryAsk::Wayland) => {
                        state.trusted_wayland_client().map(OwnedFd::from)
                    }
                    Some(crate::trust::FactoryAsk::Control) => (|| {
                        let (mine, yours) = UnixStream::pair()?;
                        adopt(mine, Role::Trusted)?;
                        Ok(OwnedFd::from(yours))
                    })(),
                    Some(crate::trust::FactoryAsk::Capture) => {
                        state.capture_wayland_client().map(OwnedFd::from)
                    }
                    Some(crate::trust::FactoryAsk::CaptureFactory) => {
                        factory(&state.loop_handle, crate::trust::FactoryLevel::Capture)
                    }
                    None => Err(anyhow::anyhow!("ask for wayland, control, capture or capture-factory")),
                };
                let sent = match made {
                    Ok(fd) => crate::trust::send_with_fd(&ours.as_fd(), b"ok", &fd),
                    Err(err) => {
                        let message = format!("error {err}");
                        // Safety: a plain send of a live buffer.
                        let n = unsafe {
                            libc::send(
                                ours.as_fd().as_raw_fd(),
                                message.as_ptr() as *const libc::c_void,
                                message.len(),
                                libc::MSG_NOSIGNAL | libc::MSG_DONTWAIT,
                            )
                        };
                        if n < 0 {
                            Err(std::io::Error::last_os_error())
                        } else {
                            Ok(())
                        }
                    }
                };
                if let Err(err) = sent {
                    warn!(?err, "trust factory could not answer");
                    return Ok(PostAction::Remove);
                }
            }
        })
        .map_err(|err| anyhow::anyhow!("failed to insert the trust factory: {err}"))?;
    Ok(theirs)
}

fn dispatch(state: &mut DuoWc, request: Request) -> Result<serde_json::Value> {
    match request {
        Request::GetOutputs => {
            let outputs: Vec<serde_json::Value> = state
                .tty
                .surfaces()
                .iter()
                .map(|surface| {
                    let geometry = state.space.output_geometry(&surface.output);
                    let mode = surface.output.current_mode();
                    serde_json::json!({
                        "name": surface.name,
                        "x": geometry.map(|g| g.loc.x),
                        "y": geometry.map(|g| g.loc.y),
                        "w": geometry.map(|g| g.size.w),
                        "h": geometry.map(|g| g.size.h),
                        "mode": mode.map(|m| serde_json::json!({
                            "w": m.size.w, "h": m.size.h, "refresh": m.refresh,
                        })),
                        "scale": surface.output.current_scale().fractional_scale(),
                        "rotation": Rotation::from_transform(surface.output.current_transform()),
                        "crtc": u32::from(surface.crtc),
                        "in_use": state.pose.in_use(&surface.name) || crate::extout::is_external(&surface.name),
                        "external": crate::extout::is_external(&surface.name),
                        "display": state.ext.names.get(&surface.name),
                        "power": if surface.want_on { "on" } else { "off" },
                        "crtc_active": surface.powered,
                        "power_cycles": surface.power_cycles,
                        "frames_queued": surface.frames_queued,
                        "frames_presented": surface.frames_presented,
                    })
                })
                .collect();
            Ok(serde_json::json!({ "outputs": outputs, "desktop": state.ext_describe() }))
        }
        Request::GetState => {
            let focused = state.seat.get_keyboard().and_then(|keyboard| keyboard.current_focus());
            let windows: Vec<serde_json::Value> = state
                .space
                .elements()
                .map(|window| {
                    let data = window_data(window);
                    let (app_id, title) = crate::state::app_id_and_title(window);
                    let geometry = state.space.element_geometry(window);
                    let surface = window.wl_surface().map(|s| s.into_owned());
                    serde_json::json!({
                        "id": data.id,
                        "app_id": app_id,
                        "title": title,
                        "placement": *data.placement.borrow(),
                        "own_placement": *data.saved.borrow(),
                        "fullscreen": *data.fullscreen.borrow(),
                        "tile": tile_side(state, window),
                        "minimized": *data.minimized.borrow(),
                        "x": geometry.map(|g| g.loc.x),
                        "y": geometry.map(|g| g.loc.y),
                        "w": geometry.map(|g| g.size.w),
                        "h": geometry.map(|g| g.size.h),
                        "keyboard": surface.is_some() && surface == focused,
                        "accessory": state.is_accessory(window),
                        "x11": window.is_x11(),
                    })
                })
                .collect();
            let lit: Vec<String> =
                state.outputs.iter().map(|o| o.name()).filter(|name| state.is_lit(name)).collect();
            let idle_in = state
                .idle
                .deadline()
                .filter(|_| !lit.is_empty())
                .map(|d| d.saturating_duration_since(std::time::Instant::now()).as_secs());
            Ok(serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "hinge_gap": state.hinge_gap,
                "hinge_gap_px": crate::layout::HINGE_GAP_PX,
                "scale": state.scale,
                "pose": state.pose.describe(),
                "lit": lit,
                "blanked": state.power.blanked(),
                "switched_off": state.power.user_off().collect::<Vec<_>>(),
                "proximity": state.proximity.panels(),
                "ring": state.ring.ring().name(),
                "idle_timeout": state.idle.timeout().map(|t| t.as_secs()).unwrap_or(0),
                "idle_in": idle_in,
                // Who keeps the screen on now (crate::idle), whether that
                // counts at all, and the shell's hold as it said.
                "idle_inhibit": state.inhibit.seen,
                "idle_inhibit_enabled": state.inhibit.enabled,
                "idle_hold": state.inhibit.hold.holders(),
                "touch_wake": state.touch_wake,
                // X11 apps (crate::xwayland).
                "xwayland": state.xwl_summary(),
                "power_key_suspend": state.power_key_suspend,
                "full_redraw": state.full_redraw,
                "scanout": state.scanout,
                "active_output": state.active_output().map(|o| o.name()),
                "keyboard_output": state.keyboard_output().map(|o| o.name()),
                "osk_output": state.osk_output().map(|o| o.name()),
                "edge_swipe": state.edge_swipe,
                "overview": state.taskview.open,
                "atomic_wake": state.atomic_wake,
                "atomic_off": state.atomic_off,
                "session_lock": state.lock.is_enabled(),
                "locked": state.lock.locked,
                "subscribers": state.subscribers.len(),
                "splits": state.split_summaries(),
                "laptop": state.laptop_summary(),
                "keyboard": state.keyboard_summary(),
                "displays": state.displays_summary(),
                "pen": crate::tablet::summary(),
                // Back to front.
                "windows": windows,
            }))
        }
        Request::SetHingeGap { gap } => {
            state.set_hinge_gap(gap);
            Ok(serde_json::json!({ "hinge_gap": state.hinge_gap }))
        }
        Request::PlaceWindow { window, app_id, output } => {
            let target = match (window, app_id) {
                (Some(id), _) => state.window_by_id(id),
                (None, Some(app_id)) => state
                    .space
                    .elements()
                    .rev()
                    .find(|w| crate::state::app_id_and_title(w).0.as_deref() == Some(&app_id))
                    .cloned(),
                (None, None) => state.active_output().and_then(|o| state.topmost_on(&o)),
            }
            .ok_or_else(|| anyhow::anyhow!("no such window"))?;
            if state.laptop_on() && (output == "span" || state.is_laptop_keys(&output)) {
                anyhow::bail!(
                    "laptop mode: {output} is the keyboard's (windows go to {})",
                    state.laptop.desk
                );
            }
            let placement = if output == "span" {
                if matches!(state.pose, Pose::Single(_)) {
                    anyhow::bail!("nothing spans in pose {}", state.pose.describe());
                }
                Placement::Span
            } else if state.output_by_name(&output).is_some() {
                if !state.pose.in_use(&output) {
                    anyhow::bail!("{output} is not in use in pose {}", state.pose.describe());
                }
                Placement::Output(output)
            } else {
                anyhow::bail!("no output named {output} (and not \"span\")");
            };
            state.set_placement(&target, placement);
            Ok(serde_json::json!({ "window": window_data(&target).id }))
        }
        Request::SetPower { output, power } => {
            state.set_output_power(&output, power)?;
            Ok(power_report(state))
        }
        Request::Grant { action } => {
            let token = crate::grants::random_token()?;
            state
                .grants
                .grant(&action, token.clone(), std::time::Instant::now())
                .map_err(|err| anyhow::anyhow!(err))?;
            info!(action, "grant given");
            Ok(serde_json::json!({ "token": token }))
        }
        Request::Redeem { action, token } => {
            if !state.grants.redeem(&action, &token, std::time::Instant::now()) {
                warn!(action, "a grant that is not valid (expired, used, or never given)");
                anyhow::bail!("no such grant");
            }
            info!(action, "grant redeemed");
            Ok(serde_json::json!({ "redeemed": true }))
        }
        Request::SetProximity { panels, ttl_ms } => {
            let ttl = ttl_ms
                .map(std::time::Duration::from_millis)
                .unwrap_or(crate::power::ProximityHold::DEFAULT_TTL);
            state.set_proximity(panels, ttl)?;
            Ok(serde_json::json!({ "proximity": state.proximity.panels() }))
        }
        Request::SetRing { ring, ttl_ms } => {
            let parsed = crate::power::Ring::parse(&ring)
                .ok_or_else(|| anyhow::anyhow!("ring is loud, silent or off, not {ring}"))?;
            let ttl =
                ttl_ms.map(std::time::Duration::from_millis).unwrap_or(crate::power::RingHold::DEFAULT_TTL);
            state.set_ring(parsed, ttl);
            Ok(serde_json::json!({ "ring": state.ring.ring().name() }))
        }
        Request::SetIdleHold { holds, ttl_ms } => {
            let ttl =
                ttl_ms.map(std::time::Duration::from_millis).unwrap_or(crate::idle::IdleHold::DEFAULT_TTL);
            state.set_idle_hold(holds, ttl);
            Ok(serde_json::json!({ "idle_hold": state.inhibit.hold.holders() }))
        }
        Request::SetPen { button, dot, input, apps } => {
            let mut settings = crate::tablet::settings();
            if let Some(button) = button {
                settings.button = crate::tablet::PenButton::parse(&button)
                    .ok_or_else(|| anyhow::anyhow!("button is right_click or off, not {button}"))?;
            }
            if let Some(dot) = dot {
                settings.dot = dot == Power::On;
            }
            if let Some(input) = input {
                settings.input = crate::tablet::PenInput::parse(&input)
                    .ok_or_else(|| anyhow::anyhow!("input is apps or all, not {input}"))?;
            }
            if let Some(apps) = apps {
                settings.apps = crate::tablet::parse_apps(&apps);
            }
            crate::tablet::set_settings(state, settings);
            Ok(crate::tablet::summary())
        }
        Request::SetIdleInhibit { idle_inhibit } => {
            state.set_idle_inhibit_enabled(idle_inhibit == Power::On);
            Ok(serde_json::json!({ "idle_inhibit": state.inhibit.enabled }))
        }
        Request::SetIdleTimeout { seconds } => {
            if seconds > crate::config::MAX_IDLE_TIMEOUT_S {
                anyhow::bail!("idle timeout is 0 (never) to {} seconds", crate::config::MAX_IDLE_TIMEOUT_S);
            }
            state.set_idle_timeout(seconds);
            Ok(serde_json::json!({ "idle_timeout": seconds }))
        }
        Request::SetTouchWake { touch_wake } => {
            state.touch_wake = touch_wake == Power::On;
            tracing::info!(touch_wake = state.touch_wake, "touch wake");
            Ok(serde_json::json!({ "touch_wake": state.touch_wake }))
        }
        Request::SetFullRedraw { full_redraw } => {
            state.full_redraw = full_redraw == Power::On;
            tracing::info!(full_redraw = state.full_redraw, "full redraw");
            state.queue_redraw_all();
            Ok(serde_json::json!({ "full_redraw": state.full_redraw }))
        }
        Request::SetScanout { scanout } => {
            state.scanout = scanout == Power::On;
            tracing::info!(scanout = state.scanout, "plane scan-out");
            state.queue_redraw_all();
            Ok(serde_json::json!({ "scanout": state.scanout }))
        }
        Request::SetPose { pose, output } => {
            let pose = Pose::parse(&pose, output.as_deref()).map_err(|err| anyhow::anyhow!(err))?;
            state.set_pose(pose)?;
            let mut report = power_report(state);
            report["pose"] = serde_json::json!(state.pose.describe());
            Ok(report)
        }
        Request::SetRotation { output, rotation } => {
            let rotation = Rotation::from_degrees(rotation)
                .ok_or_else(|| anyhow::anyhow!("rotation is 0, 90, 180 or 270, not {rotation}"))?;
            state.set_rotation(&output, rotation)?;
            Ok(serde_json::json!({ "output": output, "rotation": rotation }))
        }
        Request::Subscribe | Request::TrustedSocket { .. } => unreachable!("handled in handle_line"),
        Request::ExpectWindow { output, timeout_ms } => {
            if state.output_by_name(&output).is_none() {
                anyhow::bail!("no output named {output}");
            }
            let timeout = timeout_ms
                .map(std::time::Duration::from_millis)
                .unwrap_or(crate::state::LAUNCH_HINT_DEFAULT)
                .min(std::time::Duration::from_secs(60));
            tracing::info!(output, ms = timeout.as_millis() as u64, "expecting a window");
            state.launch_hint = Some(crate::state::LaunchHint {
                output: output.clone(),
                until: std::time::Instant::now() + timeout,
            });
            Ok(serde_json::json!({ "output": output, "timeout_ms": timeout.as_millis() as u64 }))
        }
        Request::SetPosture { posture } => {
            if posture.is_empty() || posture.len() > 40 {
                anyhow::bail!("a posture is a short name");
            }
            state.set_posture(&posture);
            Ok(serde_json::json!({
                "posture": state.posture,
                "ambient": state.ambient.map(|k| k.name()),
            }))
        }
        Request::Ambient { action } => {
            match action.as_str() {
                "peek" => state.ambient_start(crate::ambient::Kind::Peek),
                "glance" => state.ambient_start(crate::ambient::Kind::Glance),
                "glance-ruler" | "ruler" => state.ambient_start(crate::ambient::Kind::Ruler),
                "off" => {
                    state.ambient_end("ambient off");
                    state.apply_power();
                }
                other => anyhow::bail!("ambient is peek, glance, glance-ruler or off, not {other}"),
            }
            Ok(serde_json::json!({ "ambient": state.ambient.map(|k| k.name()) }))
        }
        Request::FocusOutput { output } => {
            if state.output_by_name(&output).is_none() {
                anyhow::bail!("no output named {output}");
            }
            if (!state.pose.in_use(&output) && !crate::extout::is_external(&output)) || !state.is_lit(&output)
            {
                anyhow::bail!("{output} is not in use or not lit");
            }
            // Laptop mode: the keyboard panel takes no keyboard; its desk does.
            let output = state.laptop_hint_output(&output);
            tracing::info!(output, "keyboard to the panel (focus_output)");
            state.osk_back("the bar's keyboard button");
            state.osk_start_release("the bar's keyboard button");
            state.focus_by_touch(&output, None, false);
            Ok(serde_json::json!({ "output": output }))
        }
        Request::ActivateWindow { window, app_id } => {
            let target = match (window, app_id) {
                (Some(id), _) => state.window_by_id(id),
                (None, Some(app_id)) => state
                    .space
                    .elements()
                    .rev()
                    .find(|w| crate::state::app_id_and_title(w).0.as_deref() == Some(&app_id))
                    .cloned(),
                (None, None) => anyhow::bail!("name a window (window or app_id)"),
            }
            .ok_or_else(|| anyhow::anyhow!("no such window"))?;
            state.activate_window(&target);
            Ok(serde_json::json!({ "window": window_data(&target).id }))
        }
        Request::MinimizeWindow { window, app_id } => {
            if window.is_none() && app_id.is_none() {
                anyhow::bail!("name a window (window or app_id)");
            }
            let target = find_window(state, window, app_id)?;
            let went = state.minimize_window(&target, "asked");
            Ok(serde_json::json!({ "window": window_data(&target).id, "minimized": went }))
        }
        Request::SetKeyboard { layouts, variants, active, add, variant } => {
            if let Some(layouts) = layouts {
                let list = crate::kbdlayout::parse(&layouts, variants.as_deref().unwrap_or(""))
                    .map_err(|e| anyhow::anyhow!(e))?;
                state.set_keyboard_layouts(list, "asked")?;
            }
            if let Some(add) = add {
                state.keyboard_pick(crate::kbdlayout::Entry::new(&add, variant.as_deref().unwrap_or("")))?;
            }
            match active.as_deref() {
                None => {}
                Some("next") => state.keyboard_next(),
                Some(which) => {
                    let index = match which.parse::<u32>() {
                        Ok(i) => i,
                        Err(_) => state
                            .kbd
                            .list
                            .iter()
                            .position(|e| e.layout == which)
                            .ok_or_else(|| anyhow::anyhow!("{which} is not in the list"))?
                            as u32,
                    };
                    state.keyboard_select(index)?;
                }
            }
            Ok(serde_json::to_value(state.keyboard_summary())?)
        }
        Request::SetLaptop { laptop, auto, keys } => {
            let auto = match auto.as_deref() {
                None => None,
                Some("on") => Some(true),
                Some("off") => Some(false),
                Some(other) => anyhow::bail!("auto is on or off, not {other}"),
            };
            state.set_laptop_config(auto, keys)?;
            match laptop.as_deref() {
                None => {}
                Some("on") => state.set_laptop(true, crate::laptop::Origin::Manual)?,
                Some("off") => state.set_laptop(false, crate::laptop::Origin::Manual)?,
                Some("toggle") => {
                    let on = !state.laptop_on();
                    state.set_laptop(on, crate::laptop::Origin::Manual)?;
                }
                Some(other) => anyhow::bail!("laptop is on, off or toggle, not {other}"),
            }
            Ok(serde_json::to_value(state.laptop_summary())?)
        }
        Request::SetTrackpad { trackpad } => {
            let panel = (trackpad != "off").then_some(trackpad);
            state.set_trackpad(panel)?;
            Ok(serde_json::json!({ "trackpad": state.trackpad.panel }))
        }
        Request::SetDisplay { output, mode } => {
            let used = crate::mirror::DisplayUse::parse(&mode)
                .ok_or_else(|| anyhow::anyhow!("mode is desk or mirror, not {mode}"))?;
            let name = state.set_display_use(output, used)?;
            Ok(serde_json::json!({ "output": name, "use": used.name() }))
        }
        Request::SetExternal { on_connect, mirror } => {
            let on_connect = match on_connect.as_deref() {
                None => None,
                Some(v) => Some(
                    crate::mirror::OnConnect::parse(v)
                        .ok_or_else(|| anyhow::anyhow!("on_connect is ask, desk or mirror, not {v}"))?,
                ),
            };
            let mirror = match mirror.as_deref() {
                None => None,
                Some(v) => Some(
                    crate::mirror::MirrorSource::parse(v)
                        .ok_or_else(|| anyhow::anyhow!("mirror is active, both, DSI-1 or DSI-2, not {v}"))?,
                ),
            };
            state.set_display_settings(on_connect, mirror)?;
            Ok(serde_json::to_value(state.displays_summary())?)
        }
        Request::SetEdgeSwipe { edge_swipe } => {
            state.edge_swipe = edge_swipe == Power::On;
            tracing::info!(edge_swipe = state.edge_swipe, "edge swipes");
            Ok(serde_json::json!({ "edge_swipe": state.edge_swipe }))
        }
        Request::SetSessionLock { session_lock } => {
            state.lock.enabled.store(session_lock == Power::On, std::sync::atomic::Ordering::SeqCst);
            tracing::info!(session_lock = state.lock.is_enabled(), "session lock");
            Ok(serde_json::json!({ "session_lock": state.lock.is_enabled() }))
        }
        Request::Unlock => {
            let was = state.lock.locked;
            state.force_unlock();
            Ok(serde_json::json!({ "was_locked": was }))
        }
        Request::SetAtomicOff { atomic_off } => {
            state.atomic_off = atomic_off == Power::On;
            tracing::info!(atomic_off = state.atomic_off, "atomic off");
            Ok(serde_json::json!({ "atomic_off": state.atomic_off }))
        }
        Request::SetAtomicWake { atomic_wake } => {
            state.atomic_wake = atomic_wake == Power::On;
            tracing::info!(atomic_wake = state.atomic_wake, "atomic wake");
            Ok(serde_json::json!({ "atomic_wake": state.atomic_wake }))
        }
        Request::Overview { action } => {
            match action.as_deref().unwrap_or("toggle") {
                "open" => state.overview_open("control socket"),
                "close" => state.overview_close("control socket"),
                "toggle" if state.taskview.open => state.overview_close("control socket"),
                "toggle" => state.overview_open("control socket"),
                other => anyhow::bail!("overview takes open, close or toggle, not {other}"),
            }
            Ok(serde_json::json!({ "overview": state.taskview.open }))
        }
        Request::CloseWindow { window, app_id } => {
            let target = match (window, app_id) {
                (Some(id), _) => state.window_by_id(id),
                (None, Some(app_id)) => state
                    .space
                    .elements()
                    .rev()
                    .find(|w| crate::state::app_id_and_title(w).0.as_deref() == Some(&app_id))
                    .cloned(),
                (None, None) => anyhow::bail!("name a window (window or app_id)"),
            }
            .ok_or_else(|| anyhow::anyhow!("no such window"))?;
            state.close_window(&target);
            Ok(serde_json::json!({ "window": window_data(&target).id }))
        }
        Request::SnapWindow { window, app_id, side, output, with } => {
            let side = crate::split::Side::parse(&side)
                .ok_or_else(|| anyhow::anyhow!("side is left, right, top or bottom, not {side}"))?;
            let target = match (window, app_id) {
                (Some(id), _) => state.window_by_id(id),
                (None, Some(app_id)) => state
                    .space
                    .elements()
                    .rev()
                    .find(|w| crate::state::app_id_and_title(w).0.as_deref() == Some(&app_id))
                    .cloned(),
                (None, None) => state.active_output().and_then(|o| state.topmost_on(&o)),
            }
            .ok_or_else(|| anyhow::anyhow!("no such window"))?;
            let output = match output {
                Some(name) => {
                    state.output_by_name(&name).ok_or_else(|| anyhow::anyhow!("no output named {name}"))?
                }
                None => match &*window_data(&target).placement.borrow() {
                    Placement::Output(name) => state.output_by_name(name),
                    Placement::Span => state.active_output(),
                }
                .ok_or_else(|| anyhow::anyhow!("the window has no panel"))?,
            };
            let with = match with {
                Some(id) => Some(
                    state
                        .window_by_id(id)
                        .ok_or_else(|| anyhow::anyhow!("no window {id} to share the panel with"))?,
                ),
                None => None,
            };
            let partner = state.snap_window(&target, &output, side, with, None)?;
            Ok(serde_json::json!({
                "window": window_data(&target).id,
                "partner": partner,
                "output": output.name(),
                "side": side,
            }))
        }
        Request::Unsplit { output } => {
            if state.output_by_name(&output).is_none() {
                anyhow::bail!("no output named {output}");
            }
            let was = state.unsplit(&output);
            Ok(serde_json::json!({ "output": output, "was_split": was }))
        }
        Request::SetSplitRatio { output, ratio } => {
            let out =
                state.output_by_name(&output).ok_or_else(|| anyhow::anyhow!("no output named {output}"))?;
            if !ratio.is_finite() {
                anyhow::bail!("ratio is a number from 0.2 to 0.8");
            }
            state.set_split_share(&out, ratio)?;
            Ok(serde_json::json!({ "output": output, "splits": state.split_summaries() }))
        }
        Request::Home { output } => {
            let out = match output {
                Some(name) => {
                    state.output_by_name(&name).ok_or_else(|| anyhow::anyhow!("no output named {name}"))?
                }
                None => state.active_output().ok_or_else(|| anyhow::anyhow!("no output"))?,
            };
            state.overview_close("home");
            let went = state.go_home(&out);
            Ok(serde_json::json!({ "output": out.name(), "home": went }))
        }
        Request::ToggleSpan { window, app_id } => {
            let target = find_window(state, window, app_id)?;
            state.toggle_span(&target, "control socket");
            Ok(serde_json::json!({
                "window": window_data(&target).id,
                "placement": *window_data(&target).placement.borrow(),
            }))
        }
        Request::MoveWindow { window, app_id, target } => {
            let window = find_window(state, window, app_id)?;
            let mut words = target.split_whitespace();
            let first = words.next().unwrap_or_default();
            let target = if first.eq_ignore_ascii_case("span") {
                crate::snap::Target::Span
            } else {
                if state.output_by_name(first).is_none() {
                    anyhow::bail!(
                        "no output named {first} (an output, \"<output> left|right|top|bottom\" or span)"
                    );
                }
                match words.next() {
                    None => crate::snap::Target::Panel(first.to_owned()),
                    Some(side) => crate::snap::Target::Half(
                        first.to_owned(),
                        crate::split::Side::parse(side).ok_or_else(|| {
                            anyhow::anyhow!("side is left, right, top or bottom, not {side}")
                        })?,
                    ),
                }
            };
            let from = state.window_rect(&window);
            state.apply_target(&window, Some(&target));
            if let Some(from) = from {
                state.animate_from(&window, from, 1.0, 1.0, crate::spring::Params::SNAPPY);
            }
            Ok(serde_json::json!({
                "window": window_data(&window).id,
                "target": target.describe(),
                "placement": *window_data(&window).placement.borrow(),
            }))
        }
        Request::ForceCloseWindow { window } => {
            let target = state.window_by_id(window).ok_or_else(|| anyhow::anyhow!("no window {window}"))?;
            let pid = state.force_close(&target)?;
            Ok(serde_json::json!({ "window": window, "pid": pid }))
        }
        Request::SetBackSwipe { back_swipe } => {
            state.back_swipe = crate::config::BackSwipe::parse(&back_swipe)
                .ok_or_else(|| anyhow::anyhow!("back_swipe is both, outer or off"))?;
            Ok(serde_json::json!({ "back_swipe": state.back_swipe.name() }))
        }
        Request::SetStitch { edge, ms, offset } => {
            let c = &mut state.carry.stitch;
            if let Some(v) = edge {
                c.edge_band = v.clamp(4.0, 300.0);
            }
            if let Some(v) = ms {
                c.window_ms = v.min(2000);
            }
            if let Some(v) = offset {
                c.max_offset = v.clamp(4.0, 800.0);
            }
            Ok(serde_json::json!({ "edge": c.edge_band, "ms": c.window_ms, "offset": c.max_offset }))
        }
        Request::SetSnap { hinge_band, half_share, pad_from, pad_to } => {
            let c = &mut state.carry.snap;
            if let Some(v) = hinge_band {
                c.hinge_band = v.clamp(0.0, 200.0);
            }
            if let Some(v) = half_share {
                c.half_share = v.clamp(0.0, 0.5);
            }
            if let Some(v) = pad_from {
                c.pad_from = v.clamp(0.0, 1.0);
            }
            if let Some(v) = pad_to {
                c.pad_to = v.clamp(0.0, 1.0);
            }
            Ok(serde_json::json!({
                "hinge_band": c.hinge_band,
                "half_share": c.half_share,
                "pad_from": c.pad_from,
                "pad_to": c.pad_to,
            }))
        }
        Request::SetAnimations { animations } => {
            state.carry.animate = match animations.as_str() {
                "on" => true,
                "off" => false,
                other => anyhow::bail!("animations is on or off, not {other}"),
            };
            Ok(serde_json::json!({ "animations": state.carry.animate }))
        }
        Request::SetRotateAnim { ms, ahead } => {
            if ms > 5000 {
                anyhow::bail!("rotate_anim is 0 to 5000 ms, not {ms}");
            }
            if let Some(ahead) = ahead {
                state.rotation_anim.render_ahead = match ahead.as_str() {
                    "on" => true,
                    "off" => false,
                    other => anyhow::bail!("ahead is on or off, not {other}"),
                };
            }
            state.rotation_anim.rotate_ms = ms;
            Ok(serde_json::json!({ "rotate_anim_ms": ms, "ahead": state.rotation_anim.render_ahead }))
        }
        Request::Confirm { action, detail, .. } => {
            let id = state.ask_confirm(&action, detail.as_deref(), None)?;
            Ok(serde_json::json!({ "asked": id, "action": action }))
        }
        Request::ConfirmAnswer { confirm, yes } => {
            let done = state.confirm_answered(confirm, yes);
            Ok(serde_json::json!({ "confirm": confirm, "done": done }))
        }
        Request::Screenshot { output, path } => {
            // Only through handle_line's thread path; here for the tests'
            // parsing and in case: written at once.
            let (w, h, pixels) = state.screenshot(&output)?;
            crate::screenshot::write_png(std::path::Path::new(&path), w, h, &pixels)?;
            Ok(serde_json::json!({ "path": path, "w": w, "h": h }))
        }
    }
}

/// A window by id, else by app id (the frontmost with it), else the active
/// panel's front window.
fn find_window(
    state: &DuoWc,
    window: Option<u64>,
    app_id: Option<String>,
) -> Result<smithay::desktop::Window> {
    match (window, app_id) {
        (Some(id), _) => state.window_by_id(id),
        (None, Some(app_id)) => state
            .space
            .elements()
            .rev()
            .find(|w| crate::state::app_id_and_title(w).0.as_deref() == Some(&app_id))
            .cloned(),
        (None, None) => state.active_output().and_then(|o| state.topmost_on(&o)),
    }
    .ok_or_else(|| anyhow::anyhow!("no such window"))
}

/// The half a window has, if it is in a split in force (`left`...).
fn tile_side(state: &DuoWc, window: &smithay::desktop::Window) -> Option<&'static str> {
    let id = window_data(window).id;
    let split = state.splits.iter().find(|s| s.has(id))?;
    let output = state.output_by_name(&split.output)?;
    let split = state.active_split(&output)?;
    split.side_of(id, state.rotation_of(&output)).map(|s| s.name())
}

/// Which panels are lit, after a power or pose change.
fn power_report(state: &DuoWc) -> serde_json::Value {
    let outputs: serde_json::Map<String, serde_json::Value> = state
        .tty
        .surfaces()
        .iter()
        .map(|s| (s.name.clone(), serde_json::json!(if s.want_on { "on" } else { "off" })))
        .collect();
    serde_json::json!({ "power": outputs, "blanked": state.power.blanked() })
}

fn encode(response: &Response<'_>) -> String {
    serde_json::to_string(response)
        .unwrap_or_else(|err| format!("{{\"id\":null,\"error\":\"failed to encode a response: {err}\"}}"))
}

/// A reply line with a descriptor (SCM_RIGHTS on the line's first byte). The
/// stream is non-blocking: a full buffer is retried briefly, like `send`.
fn send_line_with_fd(stream: &UnixStream, reply: &str, fd: &OwnedFd) -> std::io::Result<()> {
    let mut line = Vec::with_capacity(reply.len() + 1);
    line.extend_from_slice(reply.as_bytes());
    line.push(b'\n');
    for _ in 0..16 {
        match crate::trust::send_with_fd(stream, &line, fd) {
            Err(err) if err.kind() == ErrorKind::WouldBlock => std::thread::yield_now(),
            other => return other,
        }
    }
    Err(std::io::Error::new(ErrorKind::WouldBlock, "the client stopped reading"))
}

fn send(mut stream: &UnixStream, reply: &str) -> std::io::Result<()> {
    if reply.is_empty() {
        return Ok(());
    }
    let mut line = Vec::with_capacity(reply.len() + 1);
    line.extend_from_slice(reply.as_bytes());
    line.push(b'\n');

    // A partial write means the peer stopped reading; a bounded retry keeps a
    // stuck client from blocking the compositor.
    let mut written = 0;
    for _ in 0..16 {
        match stream.write(&line[written..]) {
            Ok(0) => break,
            Ok(n) => {
                written += n;
                if written == line.len() {
                    return Ok(());
                }
            }
            Err(err) if err.kind() == ErrorKind::WouldBlock => std::thread::yield_now(),
            Err(err) if err.kind() == ErrorKind::Interrupted => continue,
            Err(err) => return Err(err),
        }
    }
    Err(std::io::Error::new(ErrorKind::WouldBlock, "the client stopped reading"))
}

/// Send the subscribers what changed since the last turn of the event loop
/// (called once per turn; nothing is sent while nothing changes).
pub fn publish(state: &mut DuoWc) {
    if !std::mem::take(&mut state.events_dirty) {
        return;
    }
    if state.subscribers.is_empty() {
        state.last_snapshot = None;
        return;
    }
    let snapshot = state.snapshot();
    let changed = match &state.last_snapshot {
        Some(last) => crate::events::changes(last, &snapshot),
        None => Vec::new(),
    };
    let first = state.last_snapshot.is_none();
    state.last_snapshot = Some(snapshot.clone());
    if first || changed.is_empty() {
        return;
    }
    broadcast(state, &serde_json::json!({ "event": "state", "changed": changed, "state": snapshot }));
}

/// Send one event line to every subscriber; one that stopped reading is
/// dropped (its next write fails).
pub fn broadcast(state: &mut DuoWc, event: &serde_json::Value) {
    if state.subscribers.is_empty() {
        return;
    }
    let line = event.to_string();
    state.subscribers.retain(|stream| match send(stream, &line) {
        Ok(()) => true,
        Err(err) => {
            debug!(?err, "event stream subscriber gone");
            false
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn class_of(line: &str) -> Class {
        let envelope: Envelope = serde_json::from_str(line).expect(line);
        class(&envelope.request)
    }

    #[test]
    fn confirm_asks_and_only_the_session_answers() {
        use crate::trust::Class;
        let ask = r#"{"id":4,"request":"confirm","action":"remove-packages","detail":"x","wait":true}"#;
        assert_eq!(class_of(ask), Class::Confirm);
        assert!(Role::Confirmer.permits(class_of(ask)));
        assert!(!Role::App.permits(class_of(ask)));
        let answer = r#"{"id":5,"request":"confirm_answer","confirm":4,"yes":true}"#;
        assert_eq!(class_of(answer), Class::Privileged);
        assert!(!Role::Confirmer.permits(class_of(answer)));
        let envelope: Envelope = serde_json::from_str(answer).unwrap();
        assert_eq!(envelope.id, Some(5));
        assert!(matches!(envelope.request, Request::ConfirmAnswer { confirm: 4, yes: true }));
    }

    #[test]
    fn apps_may_read_and_hint_only() {
        for line in [
            r#"{"request":"get_outputs"}"#,
            r#"{"request":"get_state"}"#,
            r#"{"request":"subscribe"}"#,
            r#"{"request":"expect_window","output":"DSI-1"}"#,
        ] {
            assert!(Role::App.permits(class_of(line)), "{line}");
        }
    }

    #[test]
    fn the_dangerous_requests_are_privileged() {
        for line in [
            r#"{"request":"unlock"}"#,
            r#"{"request":"screenshot","output":"DSI-1","path":"/tmp/x.png"}"#,
            r#"{"request":"set_session_lock","session_lock":"off"}"#,
            r#"{"request":"ambient","action":"peek"}"#,
            r#"{"request":"place_window","output":"span"}"#,
            r#"{"request":"close_window","app_id":"foot"}"#,
            r#"{"request":"activate_window","window":1}"#,
            r#"{"request":"minimize_window","window":1}"#,
            r#"{"request":"set_display","mode":"mirror"}"#,
            r#"{"request":"set_display","output":"DP-1","mode":"desk"}"#,
            r#"{"request":"set_external","on_connect":"ask"}"#,
            r#"{"request":"set_external","mirror":"both"}"#,
            r#"{"request":"minimize_window","app_id":"foot"}"#,
            r#"{"request":"force_close_window","window":1}"#,
            r#"{"request":"move_window","window":1,"target":"span"}"#,
            r#"{"request":"focus_output","output":"DSI-2"}"#,
            r#"{"request":"set_edge_swipe","edge_swipe":"off"}"#,
            r#"{"request":"set_back_swipe","back_swipe":"off"}"#,
            r#"{"request":"set_idle_timeout","seconds":0}"#,
            r#"{"request":"set_touch_wake","touch_wake":"on"}"#,
            r#"{"request":"overview"}"#,
            r#"{"request":"home"}"#,
            r#"{"request":"set_hinge_gap","gap":"off"}"#,
            r#"{"request":"set_ring","ring":"loud"}"#,
            r#"{"request":"set_idle_hold","holds":[{"app":"firefox","pid":1}]}"#,
            r#"{"request":"set_idle_inhibit","idle_inhibit":"off"}"#,
            r#"{"request":"set_pen","button":"off"}"#,
            r#"{"request":"set_pen","dot":"off"}"#,
            r#"{"request":"set_pen","input":"all","apps":"krita, rnote"}"#,
            r#"{"request":"trusted_socket","kind":"wayland"}"#,
        ] {
            let c = class_of(line);
            assert_eq!(c, Class::Privileged, "{line}");
            assert!(!Role::App.permits(c), "{line}");
            assert!(!Role::Sensors.permits(c), "{line}");
            assert!(Role::Trusted.permits(c), "{line}");
        }
    }

    #[test]
    fn the_sensors_drive_posture_pose_rotation_power() {
        for line in [
            r#"{"request":"set_posture","posture":"closed"}"#,
            r#"{"request":"set_pose","pose":"single","output":"DSI-1"}"#,
            r#"{"request":"set_rotation","output":"DSI-1","rotation":90}"#,
            r#"{"request":"set_power","output":"all","power":"off"}"#,
        ] {
            let c = class_of(line);
            assert_eq!(c, Class::Sensors, "{line}");
            assert!(!Role::App.permits(c), "{line}");
            assert!(Role::Sensors.permits(c), "{line}");
            assert!(Role::Trusted.permits(c), "{line}");
        }
    }

    #[test]
    fn an_idle_hold_reads_its_holders() {
        let line = r#"{"id":7,"request":"set_idle_hold","holds":[{"app":"Chromium","pid":4321},{"app":"xdg-desktop-portal-gtk"}],"ttl_ms":30000}"#;
        let envelope: Envelope = serde_json::from_str(line).unwrap();
        let Request::SetIdleHold { holds, ttl_ms } = envelope.request else {
            panic!("not a set_idle_hold");
        };
        assert_eq!(ttl_ms, Some(30000));
        assert_eq!(holds[0], crate::idle::Holder { app: "Chromium".into(), pid: Some(4321) });
        assert_eq!(holds[1].pid, None);
        // None at all: the hold ends.
        let envelope: Envelope = serde_json::from_str(r#"{"request":"set_idle_hold"}"#).unwrap();
        assert!(matches!(envelope.request, Request::SetIdleHold { holds, ttl_ms: None } if holds.is_empty()));
    }

    #[test]
    fn a_control_connection_holds_at_most_one_request_line() {
        let mut lines = Lines::default();
        assert!(lines.push(b"{\"request\":\"get_state\"}\n{\"req"));
        assert_eq!(lines.next_line().as_deref(), Some(&b"{\"request\":\"get_state\"}"[..]));
        assert_eq!(lines.next_line(), None);
        assert!(!lines.overlong());
        // A newline first, then no end: the old check saw the newline and
        // read on without a bound.
        let mut lines = Lines::default();
        assert!(lines.push(b"\n"));
        let chunk = [b'a'; 1000];
        let mut reads = 0;
        while lines.push(&chunk) {
            reads += 1;
        }
        assert!(reads <= MAX_LINE / chunk.len(), "reading stops at MAX_LINE");
        assert_eq!(lines.next_line().as_deref(), Some(&b""[..]));
        assert_eq!(lines.next_line(), None);
        assert!(lines.overlong(), "the unfinished rest is dropped");
    }

    #[test]
    fn a_refused_request_is_named() {
        assert_eq!(request_name(br#"{"id":3,"request":"unlock"}"#), "unlock");
        assert_eq!(request_name(b"{}"), "this request");
    }
}
