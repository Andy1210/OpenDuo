//! Compositor state and the window rules; the Wayland protocol handlers are
//! in [`handlers`].
//!
//! Window management is deliberately thin: every toplevel is maximized on ONE
//! output, or spans all of them. A new toplevel opens on the output that was
//! touched last if that one is empty, otherwise on the first empty output,
//! otherwise on the one touched last (only lit panels the pose uses count, when
//! there are any). Windows on the same output stack, the newest (or last touched)
//! in front. A pose ([`crate::pose`]) re-places windows; panel power and the idle
//! timer follow [`crate::power`].

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};

use smithay::backend::renderer::utils::on_commit_buffer_handler;
use smithay::desktop::{
    layer_map_for_output, LayerSurface, PopupKind, PopupManager, Space, Window, WindowSurfaceType,
};
use smithay::input::{Seat, SeatHandler, SeatState};
use smithay::output::Output;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::{LoopHandle, RegistrationToken};
use smithay::reexports::wayland_protocols::xdg::decoration::zv1::server::zxdg_toplevel_decoration_v1::Mode as DecorationMode;
use smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel;
use smithay::reexports::wayland_server::protocol::{wl_output::WlOutput, wl_seat, wl_surface::WlSurface};
use smithay::reexports::wayland_server::{Client, DisplayHandle, Resource as _};
use smithay::utils::{Logical, Point, Rectangle, Serial, Transform};
use smithay::wayland::buffer::BufferHandler;
use smithay::wayland::compositor::{
    get_parent, is_sync_subsurface, send_surface_state, with_states, CompositorClientState,
    CompositorHandler, CompositorState,
};
use smithay::wayland::dmabuf::{DmabufGlobal, DmabufHandler, DmabufState, ImportNotifier};
use smithay::wayland::foreign_toplevel_list::{
    ForeignToplevelHandle, ForeignToplevelListHandler, ForeignToplevelListState,
};
use smithay::wayland::fractional_scale::{with_fractional_scale, FractionalScaleHandler};
use smithay::wayland::input_method::{
    InputMethodHandler, InputMethodManagerState, PopupSurface as InputPopupSurface,
};
use smithay::wayland::output::OutputHandler;
use smithay::wayland::seat::WaylandFocus;
use smithay::wayland::selection::data_device::{DataDeviceHandler, DataDeviceState, WaylandDndGrabHandler};
use smithay::wayland::selection::SelectionHandler;
use smithay::wayland::shell::wlr_layer::{
    Layer, LayerSurface as WlrLayerSurface, WlrLayerShellHandler, WlrLayerShellState,
};
use smithay::wayland::shell::xdg::decoration::{XdgDecorationHandler, XdgDecorationState};
use smithay::wayland::shell::xdg::{
    PopupSurface, PositionerState, ToplevelSurface, XdgShellHandler, XdgShellState,
};
use smithay::wayland::shm::{ShmHandler, ShmState};
use smithay::wayland::text_input::TextInputManagerState;
use smithay::wayland::virtual_keyboard::VirtualKeyboardManagerState;
use smithay::wayland::xdg_activation::{
    XdgActivationHandler, XdgActivationState, XdgActivationToken, XdgActivationTokenData,
};
use smithay::{
    delegate_compositor, delegate_data_device, delegate_dmabuf, delegate_layer_shell, delegate_output,
    delegate_shm, delegate_xdg_decoration, delegate_xdg_shell,
};
use tracing::{debug, info, warn};

use crate::backend::Tty;
use crate::events::{Snapshot, WindowSummary};
use crate::gesture::Held;
use crate::layout::{HingeGap, Panel};
pub use crate::pose::Placement;
use crate::pose::{Pose, WindowPlace};
use crate::power::{IdleCheck, IdleTimer, Power, PowerKey, PowerModel};
use crate::rotation::Rotation;

mod handlers;

/// Per-client state the compositor keeps.
#[derive(Default)]
pub struct ClientState {
    /// Surface bookkeeping smithay does on our behalf.
    pub compositor_state: CompositorClientState,
    /// Came through the trust factory or the admin socket (the shell, the
    /// lock, the on-screen keyboard): sees the guarded globals
    /// ([`crate::trust::Guarded`]). A client on the public socket does not.
    pub trusted: bool,
    /// Came through a capture factory (the screen-cast portal): sees the
    /// screen-capture globals, none of the other guarded ones
    /// ([`crate::trust::Grant`]).
    pub capture: bool,
    /// When it was made (trusted clients; the on-screen keyboard's start-up
    /// blink, [`crate::oskstart`]).
    pub connected: Option<Instant>,
    /// It has made an on-screen keyboard surface already ([`crate::oskstart`]).
    pub made_osk: AtomicBool,
}

impl ClientState {
    /// Whether this client may see a guarded global.
    pub fn may_bind(client: &Client, global: crate::trust::Guarded) -> bool {
        let grant = client
            .get_data::<ClientState>()
            .map(|data| crate::trust::Grant { trusted: data.trusted, capture: data.capture })
            .unwrap_or_default();
        crate::trust::may_bind(grant, global)
    }
}

impl smithay::reexports::wayland_server::backend::ClientData for ClientState {}

/// What the compositor remembers about a window, kept in its user data.
#[derive(Debug)]
pub struct WindowData {
    /// A number for the control socket to name the window by.
    pub id: u64,
    /// Where it lives.
    pub placement: RefCell<Placement>,
    /// Its own placement while a pose has moved it (see [`crate::pose`]).
    pub saved: RefCell<Option<Placement>>,
    /// The client asked for fullscreen: the whole output, past any layer-shell
    /// exclusive zone.
    pub fullscreen: RefCell<bool>,
    /// Its entry in ext-foreign-toplevel-list (task views, docks).
    pub foreign: RefCell<Option<ForeignToplevelHandle>>,
    /// Set aside (home): not drawn, not touched; still in the overview and
    /// the dock ([`crate::windrag`]).
    pub minimized: RefCell<bool>,
    /// The panel it was on before it spread over both (a toggle brings it
    /// back there).
    pub before_span: RefCell<Option<String>>,
    /// When it was mapped.
    pub mapped_at: Instant,
    /// It has drawn its first picture (and grew in).
    pub opened: RefCell<bool>,
    /// What it is to the Android rules (crate::waydroid), once known.
    pub android: RefCell<crate::waydroid::AndroidKind>,
    /// An Android task's content (its layers' union, its own frame) at its
    /// last commit: a move of it under a finger is Android moving the task.
    pub content: RefCell<Option<crate::overview::Rect>>,
    /// An Android window in duo-wc's full screen (crate::waydroid): the
    /// panel, moved up by Android's caption bar (off the glass).
    pub android_full: RefCell<bool>,
    /// The size its task was last filled to inside Android.
    pub android_filled: RefCell<Option<(i32, i32)>>,
    /// The box (logical px, from the window's corner) its task is known to
    /// lie inside while not filled ([`crate::android::needs_refit`]); None
    /// just after a fill or a restore.
    pub android_fitted: RefCell<Option<(i32, i32)>>,
    /// The box a refit has been asked for and not run yet (so the same
    /// change asks once).
    pub android_fit_want: RefCell<Option<(i32, i32)>>,
    /// A Waydroid window's app id, kept from its first commit (it is
    /// remembered when the window goes, for the one Waydroid makes again).
    pub android_app: RefCell<Option<String>>,
    /// When a Waydroid window was laid out last (the Android display takes
    /// the size of the one laid out last: crate::android).
    pub android_rank: RefCell<u64>,
    /// How it is shown when not at its area's corner at the size it draws
    /// (a dialog centred, a window too big for its panel scaled down:
    /// [`crate::dialog`]); None: as placed.
    pub fit: RefCell<Option<crate::dialog::Fit>>,
    /// What the fit was worked out from: the window geometry's size and
    /// whether the client had drawn at the size asked of it.
    pub fit_from: RefCell<Option<((i32, i32), bool)>>,
    /// The launcher's hint (`expect_window`) put it on its panel when it
    /// opened, and no activation has come since ([`crate::front`]).
    pub launched: RefCell<bool>,
    /// The launcher's hint that placed it: given back if it goes at once
    /// ([`crate::front::gives_hint_back`]: Waydroid's first window).
    pub hint: RefCell<Option<LaunchHint>>,
}

impl WindowData {
    /// A new window's data: placed by `placement` (its own placement while
    /// a pose moved it: `saved`), its task-list entry, the launcher's hint
    /// that put it there.
    pub fn new(
        id: u64,
        placement: Placement,
        saved: Option<Placement>,
        foreign: ForeignToplevelHandle,
        hint: Option<LaunchHint>,
    ) -> Self {
        WindowData {
            id,
            placement: RefCell::new(placement),
            saved: RefCell::new(saved),
            fullscreen: RefCell::new(false),
            foreign: RefCell::new(Some(foreign)),
            minimized: RefCell::new(false),
            before_span: RefCell::new(None),
            mapped_at: Instant::now(),
            opened: RefCell::new(false),
            android: RefCell::new(crate::waydroid::AndroidKind::None),
            content: RefCell::new(None),
            android_full: RefCell::new(false),
            android_filled: RefCell::new(None),
            android_fitted: RefCell::new(None),
            android_fit_want: RefCell::new(None),
            android_app: RefCell::new(None),
            android_rank: RefCell::new(0),
            fit: RefCell::new(None),
            fit_from: RefCell::new(None),
            launched: RefCell::new(hint.is_some()),
            hint: RefCell::new(hint),
        }
    }
}

/// Layer-shell namespaces of on-screen keyboards (squeekboard's is `osk`).
/// duo-wc puts such a surface on the panel of the keyboard focus, whatever
/// output the client asked for: squeekboard always asks for the first one.
pub const OSK_NAMESPACES: &[&str] = &["osk"];

/// Overlay-layer namespaces a fullscreen window in front hides too (from
/// sight and touch), like the top layer: the shell's dock. The shell hides it
/// itself; this is so that it never shows over a fullscreen video, whatever
/// the shell does.
pub const FULLSCREEN_HIDES_OVERLAYS: &[&str] = &["duo-dock"];

/// What `confirm` may ask the user about: restarting the display (Duo
/// Settings' helper), removing packages (polkit, for PackageKit), changing
/// the eSIM (duo2-esim: a profile downloaded, enabled, disabled, renamed or
/// deleted), and a quick-menu action an app asked the shell's helper for
/// without the shell's grant (`helper`: radios, mobile data, airplane mode,
/// reboot, power off; crate::grants).
pub const CONFIRMABLE: &[&str] = &["restart-display", "remove-packages", "esim", "helper"];

/// How long a waited-for question stays open: polkit's checker (packages)
/// is killed after 10 s; the eSIM helper and the shell's helper wait longer
/// (there is something to read).
pub fn confirm_wait(action: &str) -> Duration {
    match action {
        "esim" => Duration::from_secs(45),
        "helper" => Duration::from_secs(20),
        _ => CONFIRM_WAIT,
    }
}

/// How long a question that is waited for stays open (polkit's spawned
/// checker is killed after 10 s).
pub const CONFIRM_WAIT: Duration = Duration::from_secs(9);

#[cfg(test)]
mod confirm_tests {
    use super::*;

    #[test]
    fn waits_by_action() {
        assert_eq!(confirm_wait("remove-packages"), CONFIRM_WAIT);
        assert_eq!(confirm_wait("esim"), Duration::from_secs(45));
        assert_eq!(confirm_wait("helper"), Duration::from_secs(20));
        assert!(CONFIRMABLE.contains(&"esim") && CONFIRMABLE.contains(&"helper"));
    }
}
/// ... and one that is not (Duo Settings' restart).
pub const CONFIRM_OPEN: Duration = Duration::from_secs(60);

/// A question to the user in the shell, open.
pub struct PendingConfirm {
    pub id: u64,
    pub action: String,
    /// Who waits for the answer (its connection and request id).
    pub reply: Option<(std::os::unix::net::UnixStream, Option<u64>)>,
    pub timer: Option<RegistrationToken>,
}

/// A window under a point for a client: the window, its surface tree's
/// origin and the point in the window's own coordinates.
pub type ClientHit = (Window, Point<i32, Logical>, Point<f64, Logical>);

/// What a rotation shows on an output now ([`crate::rotanim`]).
pub enum RotationFrame {
    /// No turn runs there: the scene.
    None,
    /// The turn: these pictures and nothing else (no client surface is
    /// imported or drawn meanwhile).
    Cover(Vec<crate::render::Element>),
    /// The turn is over: the old picture fading in front of the scene
    /// (boxed: an element is large, a cover only a Vec).
    Front(Box<crate::render::Element>),
}

/// Layer-shell namespaces of the shell's ambient screens (peek, glance):
/// all that is drawn while one shows (crate::ambient).
pub const AMBIENT_NAMESPACES: &[&str] = &["duo-ambient"];

/// Top-layer namespaces a fullscreen window in front does NOT hide: the
/// on-screen keyboard (typing in a fullscreen app) and the app grid (a long
/// swipe up opens it over a fullscreen video, it must show).
pub const FULLSCREEN_KEEPS_TOP: &[&str] = &["osk", "duo-launcher"];

/// How long a launcher's hint (`expect_window`) waits for its window.
pub const LAUNCH_HINT_DEFAULT: Duration = Duration::from_secs(10);

/// How old an xdg-activation token may be and still move a window.
pub const ACTIVATION_TOKEN_MAX_AGE: Duration = Duration::from_secs(15);

/// The next toplevel goes to this output (a launcher asked, `expect_window`).
#[derive(Debug, Clone)]
pub struct LaunchHint {
    /// The output.
    pub output: String,
    /// Until when the hint holds.
    pub until: Instant,
}

/// A touch held at a panel edge until it is clear whether it is a swipe
/// ([`crate::gesture`]); nothing of it has reached a client yet.
#[derive(Debug, Clone)]
pub struct HeldTouch {
    /// The classifier.
    pub held: Held,
    /// The panel it went down on.
    pub output: String,
    /// Where it went down (global space) and when.
    pub start: Point<f64, Logical>,
    /// The time of the down event.
    pub start_time: u32,
    /// Where it is now (global).
    pub at: Point<f64, Logical>,
}

/// A touch that is a swipe from an edge, followed until it lifts or holds
/// still long enough to open the overview ([`crate::gesture::Swipe`]).
#[derive(Debug, Clone)]
pub struct SwipeTouch {
    /// The classifier.
    pub swipe: crate::gesture::Swipe,
    /// The panel.
    pub output: String,
    /// The panel's height (logical), for short against long.
    pub panel_h: f64,
    /// Bumped whenever the finger moves on: a hold timer from before is stale.
    pub gen: u64,
    /// It held and opened the overview: nothing more happens on its lift.
    pub done: bool,
    /// The hold timer, while it runs.
    pub timer: Option<RegistrationToken>,
    /// The furthest it came inwards (a back that comes back cancels).
    pub furthest: f64,
}

/// The data of a window, which every mapped window has.
pub fn window_data(window: &Window) -> &WindowData {
    window.user_data().get::<WindowData>().expect("every window gets its data when it is created")
}

/// Everything the compositor owns.
pub struct DuoWc {
    /// Set to false to leave the event loop.
    pub running: Arc<AtomicBool>,
    /// Handle to the Wayland display, for creating globals and sending events.
    pub display_handle: DisplayHandle,
    /// Handle to the event loop, for timers and new event sources.
    pub loop_handle: LoopHandle<'static, DuoWc>,

    /// The display hardware: one DRM device, one surface per connected connector.
    pub tty: Tty,
    /// The outputs, one per panel, in the order they came up.
    pub outputs: Vec<Output>,
    /// Whether the hinge takes room in the global space.
    pub hinge_gap: HingeGap,
    /// The scale every output runs at (fractional allowed).
    pub scale: f64,
    /// The output that was touched last; new windows and the keyboard go here.
    pub active_output: Option<String>,
    /// The panel a window was touched on last (not a layer surface): where
    /// the text field is, for the on-screen keyboard in span.
    pub field_output: Option<String>,

    /// Mapped toplevels, in stacking order (last = front).
    pub space: Space<Window>,
    /// Popups.
    pub popups: PopupManager,
    /// The next window id.
    pub next_window_id: u64,

    pub compositor_state: CompositorState,
    pub shm_state: ShmState,
    pub seat_state: SeatState<DuoWc>,
    pub data_device_state: DataDeviceState,
    pub xdg_shell_state: XdgShellState,
    /// Held for its global; every request is answered with server-side (none).
    #[allow(dead_code)]
    pub xdg_decoration_state: XdgDecorationState,
    pub layer_shell_state: WlrLayerShellState,
    pub dmabuf_state: DmabufState,
    /// The dmabuf global, once the renderer's formats are known.
    pub dmabuf_global: Option<DmabufGlobal>,

    /// The one seat: a keyboard (the phone's buttons, or a plugged one) and touch.
    pub seat: Seat<DuoWc>,

    /// How the panels are used.
    pub pose: Pose,
    /// Which panels are switched off, and whether the screen is blanked.
    pub power: PowerModel,
    /// Panels held dark by the proximity sensor during a call (the shell's
    /// `set_proximity`, [`crate::power::ProximityHold`]).
    pub proximity: crate::power::ProximityHold,
    /// The timer that ends the proximity hold if the shell stops renewing it.
    pub proximity_timer: Option<RegistrationToken>,
    /// An incoming call rings (the shell's `set_ring`,
    /// [`crate::power::RingHold`]): the power key silences it, a glance
    /// stays lit, the idle timer waits.
    pub ring: crate::power::RingHold,
    /// The timer that ends the ring hold if the shell stops renewing it.
    pub ring_timer: Option<RegistrationToken>,
    /// One-time grants for the root helpers (crate::grants).
    pub grants: crate::grants::Grants,
    /// Blanks the screen after a while without input.
    pub idle: IdleTimer,
    /// The idle timer's event source, while the screen is lit.
    pub idle_timer: Option<RegistrationToken>,
    /// A touch on a dark screen wakes it.
    pub touch_wake: bool,
    /// The next apply_power wakes the screen as a whole: a panel lit now
    /// waits for the dark ones (power::hold_for_wake).
    pub wake_whole_screen: bool,
    /// The power key also suspends the phone after switching the panels off.
    pub power_key_suspend: bool,
    /// Every frame is drawn whole (the config's `full_redraw`).
    pub full_redraw: bool,
    /// Client buffers may be scanned out on planes (the config's `scanout`).
    pub scanout: bool,
    /// Panels waking together come on in non-blocking commits, one panel
    /// after the other (the config's `atomic_wake`).
    pub atomic_wake: bool,
    /// Panels going off together do so in one non-blocking commit (the
    /// config's `atomic_off`).
    pub atomic_off: bool,
    /// Short and long power-key presses.
    pub power_key: PowerKey,
    /// Keys whose press was not delivered (the screen was dark): their release
    /// is not delivered either.
    pub suppressed_keys: HashSet<u32>,
    /// How the phone is folded, as duo-posed last said (`set_posture`):
    /// Microsoft's posture name (closed, peek_left, open_dpm, ...), or a
    /// hinge class (closed, peek, open...) from the other sources.
    pub posture: Option<String>,
    /// The ambient screen shown now (peek or glance, [`crate::ambient`]).
    pub ambient: Option<crate::ambient::Kind>,
    /// The panels whose ambient surface has drawn since the ambient screen
    /// started (until then the panel shows black, render::Ambient).
    pub ambient_fresh: HashSet<String>,
    /// When the ambient screen started, and per panel how far its strip
    /// is (1 = in a queued frame, 2 = on screen): for the log's timing.
    pub ambient_started: Option<Instant>,
    pub ambient_strip: HashMap<String, u8>,
    /// The timer that ends a glance.
    pub ambient_timer: Option<RegistrationToken>,
    /// wp_fifo_v1: a content update may wait for the last one to have
    /// been shown (FIFO presentation without a busy loop; SDL 3.2 also takes
    /// Wayland only from a compositor that has it). Barriers are released by
    /// `crate::render::send_frames` and the slow tick (`crate::frames`).
    /// Held for its global (Smithay's managed mode does the rest).
    #[allow(dead_code)]
    pub fifo_state: smithay::wayland::fifo::FifoManagerState,
    /// wp_commit_timing_v1: a content update held until a time
    /// (`crate::frames`). Held for its global.
    #[allow(dead_code)]
    pub commit_timing_state: smithay::wayland::commit_timing::CommitTimingManagerState,
    /// Which window is paced by which panel, which are hidden (suspended),
    /// the slow tick ([`crate::frames`], [`crate::pacing`]).
    pub pacing: crate::frames::Pacing,
    /// wp_presentation: clients learn when their frames reached the panel
    /// (and the refresh, 90 Hz), and can pace themselves by it. Only held:
    /// the global lives as long as this.
    #[allow(dead_code)]
    pub presentation_state: smithay::wayland::presentation::PresentationState,
    /// The touch points down now, by seat slot.
    pub touch_slots: HashMap<u32, TouchSlot>,
    /// Touches whose keyboard move waits for their lift (tap or not).
    pub pending_focus: HashMap<u32, PendingFocus>,
    /// Touch slots down on the on-screen keyboard now.
    pub osk_keys: HashSet<u32>,
    /// When a finger last went down on the on-screen keyboard (event clock).
    pub last_osk_touch: Option<u32>,
    /// The current touch frame carried something a client was sent.
    pub touch_frame_delivered: bool,

    /// Held for its global (text-input-v3: clients' text fields).
    #[allow(dead_code)]
    pub text_input_state: TextInputManagerState,
    /// Held for its global (input-method-v2: the on-screen keyboard,
    /// phosh-osk-stub or squeekboard).
    #[allow(dead_code)]
    pub input_method_state: InputMethodManagerState,
    /// Held for its global (virtual-keyboard-v1: the on-screen keyboard types
    /// with it).
    #[allow(dead_code)]
    pub virtual_keyboard_state: VirtualKeyboardManagerState,
    /// xdg-activation tokens.
    pub activation_state: XdgActivationState,
    /// Whether an on-screen keyboard's keymap may be the active one
    /// ([`crate::keymapsync`]).
    pub keymap_sync: crate::keymapsync::KeymapSync,
    /// xdg-foreign (v2): a window named by a handle across clients; the
    /// portal's file chooser becomes the dialog of the app that asked
    /// ([`crate::dialog`]).
    pub xdg_foreign_state: smithay::wayland::xdg_foreign::XdgForeignState,
    /// ext-foreign-toplevel-list.
    pub foreign_toplevels: ForeignToplevelListState,
    /// ext-data-control (guarded: crate::trust::Guarded::DataControl).
    pub data_control_state: smithay::wayland::selection::ext_data_control::DataControlState,
    /// wlr-data-control, the same for phosh-osk-stub (guarded likewise).
    pub wlr_data_control_state: smithay::wayland::selection::wlr_data_control::DataControlState,
    /// wlr-foreign-toplevel-management (phosh-osk-stub; guarded like the
    /// ext list): [`crate::wlrtoplevel`].
    pub wlr_toplevels: crate::wlrtoplevel::WlrToplevels,
    /// zphoc_device_state_v1, the hardware keyboards (phosh-osk-stub;
    /// guarded): [`crate::devicestate`].
    pub device_state: crate::devicestate::DeviceState,
    /// Edge swipes are recognised (the config's `edge_swipe`).
    pub edge_swipe: bool,
    /// Touches held at an edge, by seat slot.
    pub held_touches: HashMap<u32, HeldTouch>,
    /// Edge swipes in progress, by seat slot.
    pub swipes: HashMap<u32, SwipeTouch>,
    /// The hold timers' generation counter.
    pub swipe_gen: u64,
    /// The task overview.
    pub taskview: crate::taskview::TaskView,
    /// Panels shared by two windows ([`crate::split`]), at most one per panel.
    pub splits: Vec<crate::split::Split>,
    /// A finger on a split's divider.
    pub divider: Option<crate::tiling::DividerTouch>,
    /// What the dividers are drawn with.
    pub split_gfx: crate::tiling::SplitGfx,
    /// The lock screen (ext-session-lock-v1).
    pub lock: crate::lock::Lock,
    /// The ext-session-lock global.
    pub session_lock_state: smithay::wayland::session_lock::SessionLockManagerState,
    /// Where the next toplevel goes, if a launcher said.
    pub launch_hint: Option<LaunchHint>,
    /// Control connections that subscribed to the event stream.
    pub subscribers: Vec<std::os::unix::net::UnixStream>,
    /// The last state published to them.
    pub last_snapshot: Option<Snapshot>,
    /// Something was dispatched this turn of the event loop (a client
    /// request, input, a page flip, a control request, a timer), so the state
    /// may have changed: the event stream compares a snapshot only then, not
    /// on the loop's idle wake-ups.
    pub events_dirty: bool,
    /// Carrying windows, home, their animations ([`crate::windrag`]).
    pub carry: crate::windrag::Carry,
    /// Android apps' windows ([`crate::waydroid`]).
    pub android: crate::waydroid::Android,
    /// What the clients' text inputs say ([`crate::textspy`]).
    pub textspy: crate::textspy::TextSpy,
    /// The on-screen keyboard put away by a tap beside the field
    /// ([`crate::oskleave`]).
    pub osk_away: crate::textspy::OskAway,
    /// squeekboard's start-up blink held back ([`crate::oskstart`]).
    pub osk_start: crate::textspy::OskStartHold,
    /// The rotation animation per panel ([`crate::rotanim`]).
    pub rotation_anim: crate::rotanim::Gfx,
    /// An action waiting for the user's yes in the shell (`confirm`).
    pub pending_confirm: Option<PendingConfirm>,
    pub confirm_serial: u64,
    /// Which side edges swipe back.
    pub back_swipe: crate::config::BackSwipe,
    /// How deep the edge strips are (logical px).
    pub edge_zone: f64,
    /// Touches on the dark panel of a tent or a brochure, by slot: (panel, down time).
    pub tent_taps: HashMap<u32, (String, u32, (f64, f64))>,
    /// The tent's double tap on its dark panel (pose::DoubleTap).
    pub tent_double: crate::pose::DoubleTap,
    /// The panels the glance (and its ruler) light: `both` or a connector.
    pub glance_panels: String,
    /// The panels the peek lights.
    pub peek_panels: String,
    /// A keyboard focus change waits for an idle callback
    /// ([`DuoWc::refresh_keyboard_focus_later`]).
    pub focus_refresh_queued: bool,
    /// Screen capture for the screen-cast portal ([`crate::capture`]).
    pub capture: crate::capture::Capture,
    /// Idle inhibitors, idle notifications and the shell's idle hold
    /// ([`crate::inhibit`]).
    pub inhibit: crate::inhibit::IdleInhibit,
    /// External displays on the USB-C port ([`crate::extout`]).
    pub ext: crate::extout::Ext,
    /// The floating windows of the displays ([`crate::desk`]).
    pub desk: crate::desk::Desk,
    /// A mouse or touchpad, if one is plugged ([`crate::pointer`]).
    pub pointer: crate::pointer::Pointer,
    /// A panel as the display's touchpad ([`crate::trackpad`]).
    pub trackpad: crate::trackpad::Trackpad,
    /// Laptop mode: a desk panel and a keyboard panel
    /// ([`crate::laptopmode`]).
    pub laptop: crate::laptopmode::Laptop,
    /// The seat's keyboard layouts ([`crate::kbdlayout`]).
    pub kbd: crate::kbdlayout::Layouts,
    /// X11 apps: Xwayland, its window manager, its popups
    /// ([`crate::xwayland`]).
    pub xwl: crate::xwayland::Xwl,
    /// xwayland_shell_v1 (only Xwayland sees it): an X11 window's surface.
    pub xwayland_shell_state: smithay::wayland::xwayland_shell::XWaylandShellState,
}

/// A touch that would move the keyboard to another window (or, in span, the
/// field to the other panel) while the on-screen keyboard is up: decided on
/// its lift ([`crate::oskguard`]).
#[derive(Clone)]
pub struct PendingFocus {
    /// Tap or not.
    pub tentative: crate::oskguard::Tentative,
    /// The panel it went down on.
    pub output: String,
    /// The window it touched (raised and focused if it was a tap).
    pub window: Option<Window>,
    /// It touched a window (not a popup): the text field is on `output`.
    pub on_window: bool,
}

/// A touch point that is down.
#[derive(Debug, Clone)]
pub struct TouchSlot {
    /// The panel it went down on.
    pub output: String,
    /// Whether clients see it: not when it went down on a dark panel, or the
    /// panel went dark under it.
    pub delivered: bool,
    /// Where it was last, in the global space, and when (the event clock).
    pub location: Point<f64, Logical>,
    /// The time of its last event.
    pub time: u32,
    /// The serial of its down event, when a client got it (a client's
    /// `xdg_toplevel.move` names it).
    pub serial: Option<Serial>,
    /// The window it went down on, if any.
    pub window: Option<u64>,
    /// Where it went down (global), and when (the event clock).
    pub start: Point<f64, Logical>,
    /// The time of its down event.
    pub down: u32,
    /// The surface it went down on, and where that surface was then
    /// (global): its motion reaches the client relative to where the
    /// surface is NOW (crate::waydroid, `client_touch_location`).
    pub surface: Option<(WlSurface, Point<f64, Logical>)>,
}

impl TouchSlot {
    /// A touch point that no client sees.
    pub fn held(output: &str, location: Point<f64, Logical>, time: u32) -> Self {
        TouchSlot {
            output: output.to_owned(),
            delivered: false,
            location,
            time,
            serial: None,
            window: None,
            start: location,
            down: time,
            surface: None,
        }
    }
}

impl DuoWc {
    /// A new trusted Wayland client (it sees the guarded globals): our end is
    /// inserted into the display, the other end is returned to be handed out
    /// (WAYLAND_SOCKET). From the trust factory and `trusted_socket` only.
    pub fn trusted_wayland_client(&mut self) -> anyhow::Result<std::os::unix::net::UnixStream> {
        let (ours, theirs) = std::os::unix::net::UnixStream::pair()?;
        let client = self
            .display_handle
            .insert_client(
                ours,
                std::sync::Arc::new(ClientState {
                    trusted: true,
                    connected: Some(Instant::now()),
                    ..Default::default()
                }),
            )
            .map_err(|err| anyhow::anyhow!("failed to insert a trusted client: {err}"))?;
        tracing::info!(client = ?client.id(), "trusted Wayland client made");
        Ok(theirs)
    }

    /// A new Wayland client that may capture the screen and nothing else
    /// guarded (the screen-cast portal and its screenshot tool,
    /// [`crate::capture`]). From a trust factory and `trusted_socket` only.
    pub fn capture_wayland_client(&mut self) -> anyhow::Result<std::os::unix::net::UnixStream> {
        let (ours, theirs) = std::os::unix::net::UnixStream::pair()?;
        let client = self
            .display_handle
            .insert_client(
                ours,
                std::sync::Arc::new(ClientState {
                    capture: true,
                    connected: Some(Instant::now()),
                    ..Default::default()
                }),
            )
            .map_err(|err| anyhow::anyhow!("failed to insert a capture client: {err}"))?;
        tracing::info!(client = ?client.id(), "capture Wayland client made");
        Ok(theirs)
    }

    /// An output by connector name.
    pub fn output_by_name(&self, name: &str) -> Option<Output> {
        self.outputs.iter().find(|o| o.name() == name).cloned()
    }

    /// The output new windows and the keyboard belong to.
    pub fn active_output(&self) -> Option<Output> {
        self.active_output
            .as_deref()
            .and_then(|name| self.output_by_name(name))
            .or_else(|| self.layout_order().into_iter().next())
    }

    /// The outputs left to right as they are laid out.
    pub fn layout_order(&self) -> Vec<Output> {
        let mut outputs = self.outputs.clone();
        outputs.sort_by_key(|o| {
            self.space.output_geometry(o).map(|g| (g.loc.x, g.loc.y)).unwrap_or((i32::MAX, i32::MAX))
        });
        outputs
    }

    /// Put every output where the layout says, then everything on them.
    ///
    /// Called when an output comes up and when the hinge gap is switched. Windows
    /// follow their output (a spanning one is resized to the new width), layer
    /// surfaces are re-arranged, and every output is redrawn.
    pub fn arrange_outputs(&mut self) {
        let panels: Vec<Panel> = self
            .outputs
            .iter()
            .map(|o| Panel {
                name: o.name(),
                mode: o.current_mode().map(|m| (m.size.w, m.size.h)).unwrap_or((0, 0)),
                rotation: Rotation::from_transform(o.current_transform()),
            })
            .collect();
        for (name, x, y) in crate::layout::arrange(&panels, self.hinge_gap, self.scale) {
            let Some(output) = self.output_by_name(&name) else {
                continue;
            };
            let loc: Point<i32, Logical> = (x, y).into();
            output.change_current_state(None, None, None, Some(loc));
            self.space.map_output(&output, loc);
            layer_map_for_output(&output).arrange();
            info!(output = name, x, y, "output placed");
        }
        // The lock screen follows a rotation too (crate::lock).
        self.reconfigure_lock_surfaces();
        self.place_all();
        self.queue_redraw_all();
    }

    /// Re-place every window, keeping the stacking order: `Space::map_element`
    /// puts a window that moves in FRONT, so without the restore a hinge switch
    /// would bring a panel's window over a spanning one.
    pub fn place_all(&mut self) {
        let order: Vec<Window> = self.space.elements().cloned().collect();
        for window in &order {
            self.place(window);
        }
        for window in &order {
            self.space.raise_element(window, false);
        }
    }

    /// Switch the hinge gap, live.
    pub fn set_hinge_gap(&mut self, gap: HingeGap) {
        if self.hinge_gap == gap {
            return;
        }
        info!(?gap, "hinge gap switched");
        self.hinge_gap = gap;
        self.arrange_outputs();
    }

    /// The outputs the pose uses, by name.
    pub fn in_use(&self) -> Vec<String> {
        self.outputs.iter().map(|o| o.name()).filter(|name| self.pose.in_use(name)).collect()
    }

    /// Whether a panel should be lit now.
    pub fn is_lit(&self, output: &str) -> bool {
        if let Some(kind) = self.ambient {
            // An ambient screen lights only its panels (the glance: DSI-2).
            if !crate::ambient::lights(kind, output, &self.glance_panels, &self.peek_panels) {
                return false;
            }
        }
        if self.proximity.covers(output) {
            // At an ear during a call: dark, touches not delivered, but not
            // "the screen is off" (screen_dark asks the power rules only).
            return false;
        }
        // An external display is lit by the power rules alone (no pose).
        self.power.is_lit(output, self.pose.in_use(output) || crate::extout::is_external(output))
    }

    /// Whether the panel shows duo-wc's picture now: lit for the power
    /// rules, AND its hardware has the picture up (not waking, not held
    /// black for the other panel, its first picture since it came on
    /// flipped onto it; power::shows_picture). Touches reach clients only
    /// here: a lit panel that is still black takes none.
    pub fn panel_shows(&self, output: &str) -> bool {
        self.is_lit(output) && self.tty.surfaces().iter().any(|s| s.name == output && s.shows_picture())
    }

    /// `set_proximity`: hold these panels dark (an ear is near the sensor
    /// during a call) for `ttl`, or none. Unknown panel names are refused.
    pub fn set_proximity(&mut self, panels: Vec<String>, ttl: Duration) -> anyhow::Result<()> {
        for name in &panels {
            if self.output_by_name(name).is_none() {
                anyhow::bail!("no output named {name}");
            }
        }
        let changed = self.proximity.set(panels, ttl, Instant::now());
        if changed {
            info!(panels = ?self.proximity.panels(), "proximity hold");
        }
        if let Some(token) = self.proximity_timer.take() {
            self.loop_handle.remove(token);
        }
        if let Some(deadline) = self.proximity.deadline() {
            let after = deadline.saturating_duration_since(Instant::now());
            match self.loop_handle.insert_source(Timer::from_duration(after), |_, _, state: &mut DuoWc| {
                state.proximity_timer = None;
                if state.proximity.expire(Instant::now()) {
                    info!("proximity hold ran out (not renewed): the panels come back");
                    state.events_dirty = true;
                    state.apply_power();
                    state.queue_redraw_all();
                }
                TimeoutAction::Drop
            }) {
                Ok(token) => self.proximity_timer = Some(token),
                Err(err) => warn!(?err, "proximity timer"),
            }
        }
        if changed {
            self.events_dirty = true;
            self.apply_power();
            self.queue_redraw_all();
        }
        Ok(())
    }

    /// `set_ring`: an incoming call rings aloud, silently, or none does
    /// ([`crate::power::RingHold`]), for `ttl` unless renewed.
    pub fn set_ring(&mut self, ring: crate::power::Ring, ttl: Duration) {
        let rang = self.ring.rings();
        if self.ring.set(ring, ttl, Instant::now()) {
            info!(ring = ring.name().unwrap_or("off"), "incoming call ring");
            self.events_dirty = true;
        }
        if let Some(token) = self.ring_timer.take() {
            self.loop_handle.remove(token);
        }
        if let Some(deadline) = self.ring.deadline() {
            let after = deadline.saturating_duration_since(Instant::now());
            match self.loop_handle.insert_source(Timer::from_duration(after), |_, _, state: &mut DuoWc| {
                state.ring_timer = None;
                if state.ring.expire(Instant::now()) {
                    info!("ring hold ran out (not renewed)");
                    state.events_dirty = true;
                    state.ring_over();
                }
                TimeoutAction::Drop
            }) {
                Ok(token) => self.ring_timer = Some(token),
                Err(err) => warn!(?err, "ring timer"),
            }
        }
        if rang && !self.ring.rings() {
            self.ring_over();
        }
    }

    /// The call no longer rings (answered, declined, missed, or the hold
    /// ran out): a glance held for it gets its usual few seconds from now
    /// (the shell's strip shows the clock again), and the idle timer counts
    /// from now.
    fn ring_over(&mut self) {
        if self.ambient == Some(crate::ambient::Kind::Glance) {
            self.ambient_arm_timer(crate::ambient::Kind::Glance, crate::ambient::GLANCE);
        }
        self.idle.activity(Instant::now());
        if !self.screen_dark() {
            self.arm_idle_timer();
        }
    }

    /// Whether no panel is lit (blanked, or every panel switched off).
    pub fn screen_dark(&self) -> bool {
        let in_use = self.in_use();
        let names: Vec<&str> = in_use.iter().map(String::as_str).collect();
        !self.power.any_lit(&names)
            && !self.outputs.iter().any(|o| crate::extout::is_external(&o.name()) && self.is_lit(&o.name()))
    }

    /// Bring the hardware in line with the pose and the power rules.
    ///
    /// Panels that go dark take their touch points with them (cancelled for the
    /// clients); the idle timer runs while anything is lit; the active output
    /// moves to a lit panel when it goes dark.
    pub fn apply_power(&mut self) {
        let was_lit = self.tty.surfaces().iter().any(|s| s.want_on);
        let wanted: Vec<_> = self
            .tty
            .surfaces()
            .iter()
            .map(|s| (s.crtc, s.name.clone(), s.want_on, self.is_lit(&s.name)))
            .collect();
        let mut went_dark = Vec::new();
        // Panels that come on from dark: non-blocking commits, one panel after
        // the other, the picture on all at once (atomic_wake,
        // backend::wake_together), else one Smithay modeset each in set_power.
        let waking: Vec<_> = wanted
            .iter()
            .filter(|(crtc, _, current, on)| {
                *on && !*current
                    && self
                        .tty
                        .surfaces()
                        .iter()
                        .any(|s| s.crtc == *crtc && !s.powered && !s.frame_pending && !s.waking)
            })
            .map(|(crtc, _, _, _)| *crtc)
            .collect();
        // The screen woken as a whole while a panel is lit (the peek strip):
        // the lit ones wait for the dark ones (power::hold_for_wake).
        let whole_screen = std::mem::take(&mut self.wake_whole_screen);
        let hold: Vec<_> = {
            let name_of = |c: &smithay::reexports::drm::control::crtc::Handle| {
                wanted.iter().find(|w| w.0 == *c).map(|w| w.1.clone())
            };
            let waking_names: Vec<String> = waking.iter().filter_map(name_of).collect();
            let lit: Vec<String> = wanted
                .iter()
                .filter(|(_, _, current, on)| *current && *on)
                .map(|(_, name, _, _)| name.clone())
                .collect();
            let held = crate::power::hold_for_wake(&waking_names, &lit, whole_screen);
            wanted.iter().filter(|w| held.contains(&w.1)).map(|w| w.0).collect()
        };
        let woke_together = self.atomic_wake && crate::backend::wake_together(self, &waking, &hold);
        // Panels that go dark with nothing in flight: together, in one
        // non-blocking commit (atomic_off); one with a frame in flight goes
        // off at its vblank (set_power).
        let going_off: Vec<_> = wanted
            .iter()
            .filter(|(crtc, _, current, on)| {
                !*on && *current
                    && self.tty.surfaces().iter().any(|s| {
                        s.crtc == *crtc && s.powered && !s.frame_pending && !s.waking && !s.powering_off
                    })
            })
            .map(|(crtc, _, _, _)| *crtc)
            .collect();
        // More than one: a black frame on each first, then the off commit
        // (backend::start_blackout), so they go dark together to the eye.
        let blackout =
            self.atomic_off && going_off.len() > 1 && crate::backend::start_blackout(self, &going_off);
        let off_together = !blackout
            && self.atomic_off
            && !going_off.is_empty()
            && crate::backend::power_off_together(self, &going_off);
        for (crtc, name, current, on) in wanted {
            if current == on {
                continue;
            }
            info!(output = name, on, "panel power");
            if on && woke_together && waking.contains(&crtc) {
                if let Some(surface) = self.tty.surface_mut(crtc) {
                    surface.want_on = true;
                }
                continue;
            }
            if on && self.tty.surfaces().iter().any(|s| s.crtc == crtc && s.powering_off) {
                // Still going off: it comes back when that is done.
                if let Some(surface) = self.tty.surface_mut(crtc) {
                    surface.want_on = true;
                }
                continue;
            }
            if !on && (off_together || blackout) && going_off.contains(&crtc) {
                if let Some(surface) = self.tty.surface_mut(crtc) {
                    surface.want_on = false;
                }
                went_dark.push(name);
                continue;
            }
            crate::backend::set_power(self, crtc, on);
            if !on {
                went_dark.push(name);
            }
        }
        if !went_dark.is_empty() {
            self.cancel_touches_on(&went_dark);
        }

        let now_lit = self.tty.surfaces().iter().any(|s| s.want_on);
        if now_lit && !was_lit {
            self.idle.activity(Instant::now());
        }
        if now_lit {
            self.arm_idle_timer();
        } else {
            self.disarm_idle_timer();
        }

        let active_dark = self.active_output().map(|o| !self.is_lit(&o.name())).unwrap_or(true);
        if active_dark {
            let lit = self.layout_order().into_iter().find(|o| self.is_lit(&o.name()));
            if let Some(lit) = lit {
                self.active_output = Some(lit.name());
                self.refresh_keyboard_focus();
            }
        }
    }

    /// The screen goes off: both panels.
    pub fn blank(&mut self, reason: &str) {
        self.ambient_end(reason);
        if self.power.blanked() && self.screen_dark() {
            return;
        }
        info!(reason, "screen off");
        self.overview_close("screen off");
        self.power.blank();
        self.apply_power();
        self.lock_by_policy(crate::lock::LockEvent::Blank);
    }

    /// The screen comes back: what was lit before.
    pub fn wake(&mut self, reason: &str) {
        let in_use = self.in_use();
        let names: Vec<&str> = in_use.iter().map(String::as_str).collect();
        info!(reason, "screen on");
        self.power.wake(&names);
        self.idle.activity(Instant::now());
        self.inhibit.notifier.notify_activity(&self.seat);
        self.apply_power();
    }

    /// Input: the idle timer starts again (and ext-idle-notify's clients
    /// hear of it).
    pub fn note_activity(&mut self) {
        self.idle.activity(Instant::now());
        self.inhibit.notifier.notify_activity(&self.seat);
    }

    /// A short press of the power key: the screen off if anything is lit,
    /// otherwise on; on a closed phone the glance strip (crate::ambient).
    /// While a call rings aloud the first press silences it instead (the
    /// shell is told, `ring_silence`); a dark screen still comes on.
    pub fn power_key_pressed(&mut self) {
        use crate::power::RingPowerKey;
        let ring = crate::power::ring_power_key(self.ring.ring(), self.screen_dark());
        if ring != RingPowerKey::AsAlways {
            self.ring.silence();
            self.events_dirty = true;
            info!("power key while a call rings: silenced");
            crate::ipc::broadcast(self, &serde_json::json!({ "event": "ring_silence", "key": "power" }));
            if ring == RingPowerKey::Silence {
                // The screen stays as it is: the call window, the lock
                // screen's card, or the strip that shows who calls.
                self.note_activity();
                return;
            }
        }
        use crate::ambient::OnPowerKey;
        match crate::ambient::on_power_key(self.posture.as_deref(), self.screen_dark(), self.ambient) {
            OnPowerKey::Glance => self.ambient_start(crate::ambient::Kind::Glance),
            OnPowerKey::Wake => {
                self.ambient_end("power key");
                if self.screen_dark() {
                    self.wake("power key");
                } else {
                    // The panels the ambient screen left dark come back,
                    // the lit one (the peek strip) waiting for them: the
                    // picture on both at once.
                    self.wake_whole_screen = true;
                    self.apply_power();
                    self.queue_redraw_all();
                }
            }
            OnPowerKey::Blank => {
                let was_ambient = self.ambient.is_some();
                self.blank("power key");
                if self.power_key_suspend && !was_ambient {
                    suspend();
                }
            }
        }
    }

    /// duo-posed says how the phone is folded now: kept for the event stream
    /// (the shell locks a closed phone), and a closed or nearly closed phone
    /// gets its ambient screens (crate::ambient).
    pub fn set_posture(&mut self, posture: &str) {
        if self.posture.as_deref() == Some(posture) {
            return;
        }
        info!(posture, "posture");
        self.posture = Some(posture.to_owned());
        self.events_dirty = true;
        if posture == "closed" {
            self.lock_by_policy(crate::lock::LockEvent::Close);
        }
        use crate::ambient::OnPosture;
        match crate::ambient::on_posture(posture, self.screen_dark(), self.ambient) {
            OnPosture::Nothing => {}
            OnPosture::Peek => self.ambient_start(crate::ambient::Kind::Peek),
            OnPosture::Blank => self.blank("closed"),
            OnPosture::Leave => {
                self.ambient_end("opened");
                // The panels the ambient screen left dark come back, with
                // the lit one, as after the power key.
                self.wake_whole_screen = true;
                self.apply_power();
            }
        }
        // The laptop posture may turn laptop mode on or off
        // (crate::laptopmode, `laptop_auto`).
        self.laptop_posture_changed();
    }

    /// Show an ambient screen: the panels lit (if dark), nothing on them but
    /// the shell's `duo-ambient` surfaces on black, touches ignored. A glance
    /// ends by itself after [`crate::ambient::GLANCE`] (not while a call
    /// rings: its strip shows who calls).
    pub fn ambient_start(&mut self, kind: crate::ambient::Kind) {
        self.ambient_cancel_timer();
        info!(kind = kind.name(), "ambient screen");
        self.ambient = Some(kind);
        // Black until the shell has drawn the strip for this screen; tell
        // its surfaces to draw now (frame callbacks), while the panel wakes,
        // not after the first frame (crate::render::Ambient).
        self.ambient_fresh.clear();
        self.ambient_strip.clear();
        self.ambient_started = Some(Instant::now());
        self.nudge_ambient_surfaces();
        self.events_dirty = true;
        self.overview_close("ambient screen");
        self.cancel_all_touches();
        self.touch_slots.clear();
        self.pending_focus.clear();
        if self.screen_dark() {
            self.wake(kind.name());
        } else {
            // The panels this screen does not light go dark.
            self.apply_power();
        }
        let ends = match kind {
            crate::ambient::Kind::Glance => Some(crate::ambient::GLANCE),
            crate::ambient::Kind::Ruler => Some(crate::ambient::RULER),
            crate::ambient::Kind::Peek => None,
        };
        if let Some(after) = ends {
            self.ambient_arm_timer(kind, after);
        }
        self.queue_redraw_all();
    }

    /// The timer that ends a glance (or its ruler) `after` from now. A
    /// glance that runs out while a call rings is kept, and looked at again
    /// [`crate::ambient::GLANCE`] later: the strip shows who calls until the
    /// ring ends ([`DuoWc::ring_over`] gives it its last few seconds).
    fn ambient_arm_timer(&mut self, kind: crate::ambient::Kind, after: Duration) {
        self.ambient_cancel_timer();
        let inserted =
            self.loop_handle.insert_source(Timer::from_duration(after), move |_, _, state: &mut DuoWc| {
                state.events_dirty = true;
                if state.ambient == Some(kind) && kind == crate::ambient::Kind::Glance && state.ring.rings() {
                    return TimeoutAction::ToDuration(crate::ambient::GLANCE);
                }
                // Forget the token first: blanking cancels the timer, and
                // this source is the one running.
                state.ambient_timer = None;
                if state.ambient == Some(kind) {
                    state.blank("glance over");
                }
                TimeoutAction::Drop
            });
        match inserted {
            Ok(token) => self.ambient_timer = Some(token),
            Err(err) => warn!(?err, "failed to start the glance timer"),
        }
    }

    /// The ambient screen ends (the panels stay as they are: the caller
    /// blanks or wakes).
    pub fn ambient_end(&mut self, reason: &str) {
        self.ambient_cancel_timer();
        self.ambient_fresh.clear();
        self.ambient_strip.clear();
        self.ambient_started = None;
        if let Some(kind) = self.ambient.take() {
            info!(kind = kind.name(), reason, "ambient screen over");
            self.events_dirty = true;
            self.refresh_keyboard_focus();
            self.queue_redraw_all();
        }
    }

    /// What an output shows of the ambient screen now.
    pub fn ambient_for(&self, output: &str) -> crate::render::Ambient {
        use crate::render::Ambient;
        if self.ambient.is_none() {
            Ambient::Off
        } else if self.ambient_fresh.contains(output) {
            Ambient::Strip
        } else {
            Ambient::Waiting
        }
    }

    /// Frame callbacks to the shell's ambient surfaces, whether drawn or
    /// not: GTK draws the next picture only after one.
    fn nudge_ambient_surfaces(&self) {
        let time = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        for output in &self.outputs {
            let map = layer_map_for_output(output);
            for layer in map.layers() {
                if AMBIENT_NAMESPACES.contains(&layer.namespace()) {
                    layer.send_frame(output, time, None, |_, _| Some(output.clone()));
                }
            }
        }
    }

    /// A layer surface committed: an ambient one with a picture during an
    /// ambient screen makes its panel show the strip.
    fn ambient_committed(&mut self, root: &WlSurface) {
        if self.ambient.is_none() {
            return;
        }
        for output in &self.outputs {
            let name = output.name();
            if self.ambient_fresh.contains(&name) {
                continue;
            }
            let map = layer_map_for_output(output);
            let hit = map
                .layer_for_surface(root, WindowSurfaceType::TOPLEVEL)
                .is_some_and(|l| AMBIENT_NAMESPACES.contains(&l.namespace()) && l.bbox().size.w > 0);
            drop(map);
            if hit {
                let ms = self.ambient_started.map(|t| t.elapsed().as_millis()).unwrap_or(0);
                info!(output = name, ms, "ambient strip drawn by the shell");
                self.ambient_fresh.insert(name);
            }
        }
    }

    fn ambient_cancel_timer(&mut self) {
        if let Some(token) = self.ambient_timer.take() {
            self.loop_handle.remove(token);
        }
    }

    /// `set_power`: one panel (by name) or `all`.
    pub fn set_output_power(&mut self, target: &str, power: Power) -> anyhow::Result<()> {
        if power == Power::On {
            // Lit on purpose (duo-posed opening the phone, ssh): a normal
            // screen, not an ambient one.
            self.ambient_end("set_power on");
        }
        let mut in_use = self.in_use();
        // An external display is switched like a panel (no pose has it).
        in_use.extend(self.outputs.iter().map(|o| o.name()).filter(|n| crate::extout::is_external(n)));
        let names: Vec<&str> = in_use.iter().map(String::as_str).collect();
        if target == "all" {
            self.power.set_all(power);
        } else {
            if self.output_by_name(target).is_none() {
                anyhow::bail!("no output named {target} (or all)");
            }
            self.power.set(target, power, &names).map_err(|err| anyhow::anyhow!(err))?;
        }
        if power == Power::On {
            self.idle.activity(Instant::now());
        }
        self.apply_power();
        Ok(())
    }

    /// `set_idle_timeout`: seconds, 0 never blanks. Counts from now.
    pub fn set_idle_timeout(&mut self, seconds: u64) {
        let timeout = (seconds > 0).then(|| Duration::from_secs(seconds));
        info!(seconds, "idle timeout");
        self.idle.set_timeout(timeout, Instant::now());
        if self.screen_dark() {
            self.disarm_idle_timer();
        } else {
            self.arm_idle_timer();
        }
    }

    /// (Re)start the idle timer's event source at the current deadline.
    pub(crate) fn arm_idle_timer(&mut self) {
        self.disarm_idle_timer();
        let Some(deadline) = self.idle.deadline() else {
            return;
        };
        let inserted =
            self.loop_handle.insert_source(Timer::from_deadline(deadline), |_, _, state: &mut DuoWc| {
                state.events_dirty = true;
                match state.idle.check(Instant::now()) {
                    IdleCheck::WaitUntil(at) => TimeoutAction::ToInstant(at),
                    IdleCheck::Disabled => {
                        state.idle_timer = None;
                        TimeoutAction::Drop
                    }
                    IdleCheck::Expired if state.ring.rings() || state.idle_inhibited_now() => {
                        // A call rings in: the screen stays on (as on a phone),
                        // counted again from now (and from its end, ring_over).
                        // So while an idle inhibitor's surface is seen or the
                        // shell's idle hold holds (crate::inhibit; its end
                        // counts from then too, refresh_idle_inhibit).
                        debug!(ring = state.ring.rings(), by = ?state.inhibit.seen, "idle timeout held");
                        state.idle.activity(Instant::now());
                        match state.idle.deadline() {
                            Some(at) => TimeoutAction::ToInstant(at),
                            None => {
                                state.idle_timer = None;
                                TimeoutAction::Drop
                            }
                        }
                    }
                    IdleCheck::Expired => {
                        // Forget the token first: blanking disarms the timer, and
                        // this source is the one running.
                        state.idle_timer = None;
                        state.blank("idle timeout");
                        TimeoutAction::Drop
                    }
                }
            });
        match inserted {
            Ok(token) => self.idle_timer = Some(token),
            Err(err) => warn!(?err, "failed to start the idle timer"),
        }
    }

    fn disarm_idle_timer(&mut self) {
        if let Some(token) = self.idle_timer.take() {
            self.loop_handle.remove(token);
        }
    }

    /// Touch points on panels that went dark end for the clients.
    fn cancel_touches_on(&mut self, outputs: &[String]) {
        let affected = self.touch_slots.values().any(|slot| slot.delivered && outputs.contains(&slot.output));
        if !affected {
            return;
        }
        self.cancel_all_touches();
        debug!("touch cancelled: its panel went dark");
    }

    /// End every delivered touch point for the clients (wl_touch.cancel), and
    /// deliver nothing more of them.
    ///
    /// Smithay's `TouchHandle::cancel` only reaches touch points that have an
    /// event since the last frame (it skips a slot whose last event was framed),
    /// so each live point is first re-sent where it already is: one motion of
    /// zero length, then the cancel.
    pub fn cancel_all_touches(&mut self) {
        let live: Vec<(u32, Point<f64, Logical>, u32)> = self
            .touch_slots
            .iter()
            .filter(|(_, slot)| slot.delivered)
            .map(|(id, slot)| (*id, slot.location, slot.time))
            .collect();
        for slot in self.touch_slots.values_mut() {
            slot.delivered = false;
        }
        self.touch_frame_delivered = false;
        if live.is_empty() {
            return;
        }
        let Some(touch) = self.seat.get_touch() else {
            return;
        };
        for (id, location, time) in live {
            let location = self.client_touch_location(id, location);
            touch.motion(
                self,
                None,
                &smithay::input::touch::MotionEvent { slot: Some(id).into(), location, time },
            );
        }
        touch.cancel(self);
    }

    /// Change the pose: re-place the windows, power the panels it uses.
    pub fn set_pose(&mut self, pose: Pose) -> anyhow::Result<()> {
        if let Pose::Single(name) = &pose {
            if self.output_by_name(name).is_none() {
                anyhow::bail!("no output named {name}");
            }
        }
        // Laptop mode needs both panels (crate::laptopmode).
        self.laptop_pose_changing(&pose);
        // A pose wakes the screen: a normal one.
        self.ambient_end("pose");
        // What the user is looking at: the front window of the panel touched
        // last. In span it spans; in single it stays in front if it was on the
        // panel that stays, otherwise that panel's own front window does.
        let front = self.active_output().and_then(|o| self.topmost_on(&o));
        let keep_front = match &pose {
            Pose::Single(name) => {
                self.output_by_name(name).and_then(|o| self.topmost_on(&o)).or_else(|| front.clone())
            }
            Pose::Span => front.clone(),
            Pose::Dual => None,
        };

        let windows: Vec<Window> = self.space.elements().cloned().collect();
        let mut places: Vec<WindowPlace> = windows
            .iter()
            .map(|w| {
                let data = window_data(w);
                WindowPlace {
                    id: data.id,
                    placement: data.placement.borrow().clone(),
                    saved: data.saved.borrow().clone(),
                }
            })
            .collect();
        crate::pose::apply(&pose, &mut places, front.as_ref().map(|w| window_data(w).id));
        for (window, place) in windows.iter().zip(places) {
            let data = window_data(window);
            *data.placement.borrow_mut() = place.placement;
            *data.saved.borrow_mut() = place.saved;
        }

        info!(pose = pose.describe(), "pose");
        self.pose = pose;
        if let Some(active) = self.active_output() {
            if !self.pose.in_use(&active.name()) && !crate::extout::is_external(&active.name()) {
                self.active_output =
                    self.layout_order().into_iter().find(|o| self.pose.in_use(&o.name())).map(|o| o.name());
            }
        }
        let in_use = self.in_use();
        let names: Vec<&str> = in_use.iter().map(String::as_str).collect();
        self.power.pose_changed(&names);
        self.idle.activity(Instant::now());

        self.place_all();
        if let Some(window) = keep_front {
            self.raise_window(&window, true);
        }
        self.refresh_keyboard_focus();
        self.apply_power();
        self.queue_redraw_all();
        // Back to both panels in the laptop posture: laptop mode may come
        // (`laptop_auto`; the posture may have come before the pose).
        if self.pose == Pose::Dual {
            self.laptop_posture_changed();
        }
        Ok(())
    }

    /// Rotate one output; the layout, its windows and its touches follow.
    pub fn set_rotation(&mut self, name: &str, rotation: Rotation) -> anyhow::Result<()> {
        let output = self.output_by_name(name).ok_or_else(|| anyhow::anyhow!("no output named {name}"))?;
        if Rotation::from_transform(output.current_transform()) == rotation {
            return Ok(());
        }
        info!(output = name, degrees = rotation.degrees(), "output rotated");
        let old = Rotation::from_transform(output.current_transform()).degrees();
        self.rotation_snapshots(name);
        output.change_current_state(None, Some(rotation.transform()), None, None);
        // Its old picture turns from where it stands (crate::rotanim); the
        // shader and the new picture's texture are made ready now, not
        // during the turn, and the clients' buffers are held through it.
        self.rotation_anim.turned(name, old, rotation.degrees());
        if !self.rotation_anim.anims.is_empty() {
            self.hold_client_buffers();
        }
        if self.rotation_anim.anims.contains_key(name) {
            if let (Some(mode), Some(device)) = (output.current_mode(), self.tty.device.as_mut()) {
                let size = output.current_transform().transform_size(mode.size);
                let scale = output.current_scale().fractional_scale();
                self.rotation_anim.prepare(&mut device.renderer, size, scale);
            }
        }
        self.arrange_outputs();
        // Laptop mode: the keyboard is the lower panel, laid out anew.
        self.laptop_rotation_changed();
        Ok(())
    }

    /// `confirm`: ask the user in the shell before an action only the
    /// session may take. Only root (Duo Settings' helper) and the session
    /// ask; the shell shows the question (a `confirm_request` event) and
    /// answers with `confirm_answer`; nothing happens without that yes.
    /// That keeps the action away from apps: any app can run the Settings
    /// helper (it is an app like any other), but none can answer.
    pub fn ask_confirm(
        &mut self,
        action: &str,
        detail: Option<&str>,
        reply: Option<(std::os::unix::net::UnixStream, Option<u64>)>,
    ) -> anyhow::Result<u64> {
        if !CONFIRMABLE.contains(&action) {
            anyhow::bail!("confirm knows {}, not {action}", CONFIRMABLE.join(", "));
        }
        if let Some(open) = self.pending_confirm.as_ref().map(|p| p.id) {
            // One question at a time: the open one is answered no.
            self.confirm_answered(open, false);
        }
        self.confirm_serial += 1;
        let id = self.confirm_serial;
        let detail: Option<String> = detail.map(|d| d.chars().take(300).collect());
        info!(id, action, waits = reply.is_some(), "asking the user (the shell's question)");
        let after = if reply.is_some() { confirm_wait(action) } else { CONFIRM_OPEN };
        let timer = self
            .loop_handle
            .insert_source(Timer::from_duration(after), move |_, _, state: &mut DuoWc| {
                if state.pending_confirm.as_ref().is_some_and(|p| p.id == id) {
                    info!(id, "the question went unanswered: no");
                    if let Some(p) = state.pending_confirm.as_mut() {
                        p.timer = None;
                    }
                    state.confirm_answered(id, false);
                }
                TimeoutAction::Drop
            })
            .ok();
        self.pending_confirm = Some(PendingConfirm { id, action: action.to_owned(), reply, timer });
        let output = self.active_output.clone();
        crate::ipc::broadcast(
            self,
            &serde_json::json!({
                "event": "confirm_request",
                "id": id,
                "action": action,
                "detail": detail,
                "output": output,
            }),
        );
        Ok(id)
    }

    /// The shell's answer (or no, when the question timed out or another
    /// came): whoever waits gets it; on a yes the action runs.
    pub fn confirm_answered(&mut self, id: u64, yes: bool) -> bool {
        if self.pending_confirm.as_ref().map(|p| p.id) != Some(id) {
            return false;
        }
        let Some(pending) = self.pending_confirm.take() else {
            return false;
        };
        if let Some(timer) = pending.timer {
            self.loop_handle.remove(timer);
        }
        let action = pending.action.as_str();
        if let Some((stream, request_id)) = &pending.reply {
            crate::ipc::answer_confirm(stream, *request_id, yes);
        }
        crate::ipc::broadcast(self, &serde_json::json!({ "event": "confirm_closed", "id": id }));
        if !yes {
            info!(id, action, "the user said no");
            return false;
        }
        info!(id, action, "the user said yes");
        if action == "restart-display" {
            // The unit restarts duo-wc (Restart=always); the lock stays in
            // systemd's descriptor store.
            self.running.store(false, std::sync::atomic::Ordering::SeqCst);
            self.loop_handle.insert_idle(|_| {});
        }
        true
    }

    /// Before `rotated` turns: keep every lit panel's picture as it is shown,
    /// for the rotation animation (crate::rotanim). Both panels, because
    /// turning one may move the layout of both (turned alike they stack).
    /// The other panel's animation, if one runs, goes on untouched; the
    /// turning panel keeps its old picture while it still turns or holds it
    /// (the other half of duo-posed's pair of requests, a turn during a
    /// turn), otherwise its picture as shown now is taken.
    fn rotation_snapshots(&mut self, rotated: &str) {
        if !self.carry.animate || !self.rotation_anim.usable() {
            return;
        }
        let names: Vec<String> = self.outputs.iter().map(|o| o.name()).filter(|n| self.is_lit(n)).collect();
        let started = Instant::now();
        let asked = crate::turnlog::monotonic_now();
        let mut begun = Vec::new();
        for name in names {
            let keep = if name == rotated {
                self.rotation_anim.in_turn(&name, asked)
            } else {
                self.rotation_anim.anims.contains_key(&name)
            };
            if keep {
                continue;
            }
            let Some(output) = self.output_by_name(&name) else {
                continue;
            };
            match self.capture_shown(&output) {
                Ok(picture) => {
                    self.rotation_anim.begin(&name, picture);
                    begun.push(name);
                }
                Err(err) => warn!(output = name, ?err, "no rotation snapshot"),
            }
        }
        let capture_ms = started.elapsed().as_secs_f64() * 1000.0;
        for name in begun {
            self.rotation_anim.logs.start(&name, asked, capture_ms);
        }
    }

    /// A turn starts: every client buffer is held until it is over, then let
    /// go a few at a time between frames once the panels settled
    /// (crate::rotanim::BufferHold).
    fn hold_client_buffers(&mut self) {
        if !self.rotation_anim.hold.hold() {
            return;
        }
        debug!(held = self.rotation_anim.hold.held.len(), "rotation: client buffers held");
        if self.rotation_anim.hold.timer {
            // Still letting go of the last turn's: that timer goes on.
            return;
        }
        let timer = Timer::from_duration(crate::rotanim::RELEASE_EVERY);
        let inserted =
            self.loop_handle.insert_source(timer, |_, _, state: &mut DuoWc| state.client_buffers_tick());
        match inserted {
            Ok(_) => self.rotation_anim.hold.timer = true,
            Err(_) => {
                // No timer: let them go at once rather than keep them.
                self.rotation_anim.hold.holding = false;
                self.rotation_anim.hold.held.clear();
            }
        }
    }

    /// A client changed its picture during a rotation (crate::rotanim): the
    /// panels it shows on count it (a surface found on none counts on all).
    fn rotation_note_commit(&mut self, root: &WlSurface) {
        let names: Vec<String> = self.rotation_anim.anims.keys().cloned().collect();
        let window = self.window_for_surface(root);
        let mut on: Vec<String> = Vec::new();
        for name in &names {
            let Some(output) = self.output_by_name(name) else {
                continue;
            };
            let here = match &window {
                Some(w) => self.is_on(w, &output),
                None => {
                    layer_map_for_output(&output).layer_for_surface(root, WindowSurfaceType::ALL).is_some()
                }
            };
            if here {
                on.push(name.clone());
            }
        }
        if on.is_empty() && window.is_none() {
            on = names;
        }
        for name in on {
            self.rotation_anim.changed(&name);
        }
    }

    /// The held client buffers' timer: wait for the turns to end (a turn
    /// on a panel that went dark ends here), wait [`RELEASE_DELAY`] more,
    /// then let go of them a few at a time.
    ///
    /// [`RELEASE_DELAY`]: crate::rotanim::RELEASE_DELAY
    fn client_buffers_tick(&mut self) -> TimeoutAction {
        use crate::rotanim::{RELEASE_DELAY, RELEASE_EVERY, RELEASE_STEP};
        let now = crate::turnlog::monotonic_now();
        for name in self.rotation_anim.stale(now, |n| self.is_lit(n)) {
            debug!(output = name, "rotation: ended (the panel is dark)");
            self.rotation_anim.finish(&name);
            self.rotation_anim.logs.logs.remove(&name);
        }
        if !self.rotation_anim.anims.is_empty() {
            return TimeoutAction::ToDuration(RELEASE_EVERY);
        }
        let hold = &mut self.rotation_anim.hold;
        if hold.holding {
            hold.holding = false;
            info!(
                held = hold.held.len(),
                imported = hold.imports,
                import_ms = format!("{:.1}", hold.import_ms),
                import_max_ms = format!("{:.1}", hold.import_max_ms),
                "rotation: over; the client buffers held through it are let go soon"
            );
            hold.imports = 0;
            hold.import_ms = 0.0;
            hold.import_max_ms = 0.0;
            return TimeoutAction::ToDuration(RELEASE_DELAY);
        }
        let more = match self.tty.device.as_mut() {
            Some(device) => self.rotation_anim.release_some(&mut device.renderer),
            None => {
                self.rotation_anim.hold.held.clear();
                false
            }
        };
        if more {
            return TimeoutAction::ToDuration(RELEASE_STEP);
        }
        let hold = &mut self.rotation_anim.hold;
        info!(
            released = hold.released,
            ticks = hold.release_ticks,
            longest_ms = format!("{:.1}", hold.release_max_ms),
            "rotation: the held client buffers are let go"
        );
        hold.released = 0;
        hold.release_ticks = 0;
        hold.release_max_ms = 0.0;
        hold.timer = false;
        TimeoutAction::Drop
    }

    /// The output's picture as its next frame shows it, rendered into a
    /// texture (upright as it reads, physical px), like a screenshot.
    fn capture_shown(&mut self, output: &Output) -> anyhow::Result<crate::rotanim::Picture> {
        let elements = self.shown_elements(output)?;
        self.capture_elements(output, &elements)
    }

    /// `elements` (laid out for `output`) rendered into a picture.
    fn capture_elements(
        &mut self,
        output: &Output,
        elements: &[crate::render::Element],
    ) -> anyhow::Result<crate::rotanim::Picture> {
        let mode = output.current_mode().ok_or_else(|| anyhow::anyhow!("no mode"))?;
        let size = output.current_transform().transform_size(mode.size);
        let scale = output.current_scale().fractional_scale();
        let device = self.tty.device.as_mut().ok_or_else(|| anyhow::anyhow!("no device opened"))?;
        self.rotation_anim.capture(&mut device.renderer, size, scale, elements)
    }

    /// The output's elements as its next frame shows them: a turn's
    /// pictures, or the scene (a turn's old picture fading in front of it).
    pub fn shown_elements(&mut self, output: &Output) -> anyhow::Result<Vec<crate::render::Element>> {
        match self.rotation_frame(output, false, false) {
            RotationFrame::Cover(elements) => Ok(elements),
            RotationFrame::Front(front) => self.scene_elements(output, Some(*front)),
            RotationFrame::None => self.scene_elements(output, None),
        }
    }

    /// The output's scene (windows, layer surfaces, the overview, the lock
    /// and ambient screens), `front` in front of everything.
    fn scene_elements(
        &mut self,
        output: &Output,
        front: Option<crate::render::Element>,
    ) -> anyhow::Result<Vec<crate::render::Element>> {
        if self.laptop_shows(&output.name()) {
            // Laptop mode's keyboard panel: its keys and touchpad only.
            return Ok(self.laptop_scene(output, front));
        }
        let plan = self.overview_plan(output);
        let split = self.split_plan(output);
        let ambient = self.ambient_for(&output.name());
        let locked = (self.lock.locked && self.ambient.is_none()).then(|| self.lock_surface(output));
        let carry = self.carry_plan(output);
        let has_pointer = self.seat.get_pointer().is_some();
        let x11_popups = self.x11_overlays();
        let device = self.tty.device.as_mut().ok_or_else(|| anyhow::anyhow!("no device opened"))?;
        // The pointer's cursor, in front of everything (crate::pointer).
        let cursor = crate::pointer::cursor_elements(
            &mut self.pointer,
            &self.space,
            &mut device.renderer,
            output,
            has_pointer,
        );
        let overview = plan.as_ref().map(|plan| (&mut self.taskview.gfx, plan));
        let split = split.as_ref().map(|plan| (&mut self.split_gfx, plan));
        let carry = carry.as_ref().map(|plan| (&mut self.carry.gfx, plan));
        let mut elements = crate::render::elements(
            &mut device.renderer,
            &self.space,
            output,
            overview,
            split,
            locked,
            ambient,
            carry,
            front,
            &x11_popups,
        );
        if !cursor.is_empty() {
            elements.splice(0..0, cursor);
        }
        Ok(elements)
    }

    /// What a rotation shows on an output now (crate::rotanim). `render`:
    /// for the frame about to be queued (the clock starts with the first;
    /// the clients' new picture is captured here, once they drew at the new
    /// size), otherwise for a look (a screenshot, a snapshot); `ahead`: that
    /// frame goes behind one in flight (it shows a refresh later).
    pub fn rotation_frame(&mut self, output: &Output, render: bool, ahead: bool) -> RotationFrame {
        let name = output.name();
        let Some(timing) = self.rotation_anim.anims.get(&name).map(|a| self.rotation_anim.timing(a)) else {
            return RotationFrame::None;
        };
        let (Some(mode), Some(geometry)) = (output.current_mode(), self.space.output_geometry(output)) else {
            return RotationFrame::None;
        };
        let phys = output.current_transform().transform_size(mode.size);
        let period = if mode.refresh > 0 {
            Duration::from_secs_f64(1000.0 / mode.refresh as f64)
        } else {
            Duration::ZERO
        };
        let now = crate::turnlog::monotonic_now();
        // The frame's progress at the vblank it will be on the panel.
        let at = if render {
            let next =
                crate::rotanim::display_time(self.rotation_anim.last_flip.get(&name).copied(), now, period);
            if ahead {
                next + period
            } else {
                next
            }
        } else {
            now
        };
        let Some(anim) = self.rotation_anim.anims.get_mut(&name) else {
            return RotationFrame::None;
        };
        if render && anim.t0.is_none() {
            anim.t0 = Some(at);
        }
        let elapsed = anim.t0.map_or(Duration::ZERO, |t0| at.saturating_sub(t0));
        let serial = self.rotation_anim.changes.get(&name).copied().unwrap_or(0);
        // The pictures stay (the scene is not shown) until the clients'
        // picture at the new size is taken, for at most SETTLE after the
        // turn: never a half-drawn scene.
        let until = timing.turn + crate::rotanim::SETTLE;
        let want_new = render && anim.turned && anim.new.is_none() && !anim.capture_failed && elapsed < until;
        // Taken already and the clients changed since (the shell's wallpaper
        // cut for the new frame comes a while after its first frame at the
        // new size): taken again, the last one fading into it.
        let retake = render
            && anim.new.is_some()
            && anim.new_serial != serial
            && elapsed < until
            && anim.ready.is_some_and(|r| elapsed >= r + crate::rotanim::RETAKE_EVERY);
        if retake && self.drawn_at_new_size(output) {
            let started = Instant::now();
            let picture = self
                .scene_elements(output, None)
                .and_then(|elements| self.capture_elements(output, &elements));
            let capture_ms = started.elapsed().as_secs_f64() * 1000.0;
            match picture {
                Ok(picture) => {
                    self.rotation_anim.retaken(&name, picture, elapsed, serial);
                    debug!(
                        output = name,
                        at_ms = elapsed.as_millis() as u64,
                        capture_ms = format!("{capture_ms:.1}"),
                        "rotation: the clients changed, their picture taken again"
                    );
                }
                Err(err) => warn!(output = name, ?err, "rotation: no new picture of the new frame"),
            }
        } else if want_new && self.drawn_at_new_size(output) {
            // The clients drew at the new size: their picture, once.
            let started = Instant::now();
            let elements = self.scene_elements(output, None);
            let build_ms = started.elapsed().as_secs_f64() * 1000.0;
            let picture = elements.and_then(|elements| self.capture_elements(output, &elements));
            let capture_ms = started.elapsed().as_secs_f64() * 1000.0;
            if let Some(anim) = self.rotation_anim.anims.get_mut(&name) {
                match picture {
                    Ok(picture) => {
                        anim.new = Some(picture);
                        anim.ready = Some(elapsed);
                        anim.new_serial = serial;
                        debug!(
                            output = name,
                            at_ms = elapsed.as_millis() as u64,
                            capture_ms = format!("{capture_ms:.1}"),
                            build_ms = format!("{build_ms:.1}"),
                            "rotation: the clients drew at the new size, their picture taken"
                        );
                    }
                    Err(err) => {
                        anim.capture_failed = true;
                        warn!(output = name, ?err, "rotation: no picture of the new frame");
                    }
                }
            }
        }
        let Some(anim) = self.rotation_anim.anims.get_mut(&name) else {
            return RotationFrame::None;
        };
        let size = |s: (i32, i32)| (s.0 as f64, s.1 as f64);
        // Not "changed since": a client that never stops changing (a video)
        // would hold the panel still until SETTLE for nothing.
        let waiting = anim.new.is_none() && !anim.capture_failed;
        let settle = anim.turned && elapsed < until && waiting;
        let Some(f) = crate::rotanim::frame(
            anim.from,
            size(anim.old.size),
            size((phys.w, phys.h)),
            elapsed,
            &timing,
            anim.ready,
            settle,
        ) else {
            if render && anim.new.is_some() && anim.new_serial != serial {
                debug!(output = name, "rotation: over; the clients changed after their last picture");
            }
            self.rotation_anim.finish(&name);
            return RotationFrame::None;
        };
        if render {
            anim.shown = f.angle;
        }
        let Some(device) = self.tty.device.as_mut() else {
            return RotationFrame::None;
        };
        let Some(program) = self.rotation_anim.program(&mut device.renderer) else {
            self.rotation_anim.finish(&name);
            return RotationFrame::None;
        };
        let Some(anim) = self.rotation_anim.anims.get(&name) else {
            return RotationFrame::None;
        };
        let mut elements = Vec::new();
        let phys = (phys.w, phys.h);
        if let Some(pose) = &f.old {
            elements.push(crate::render::Element::Rotate(crate::rotanim::picture_element(
                &mut device.renderer,
                &program,
                &anim.old,
                pose,
                phys,
                geometry.size,
            )));
        }
        if let (Some(pose), Some(new)) = (&f.new, anim.new.as_ref()) {
            elements.push(crate::render::Element::Rotate(crate::rotanim::picture_element(
                &mut device.renderer,
                &program,
                new,
                pose,
                phys,
                geometry.size,
            )));
        }
        if f.live {
            match elements.into_iter().next() {
                Some(front) => RotationFrame::Front(Box::new(front)),
                None => RotationFrame::None,
            }
        } else {
            RotationFrame::Cover(elements)
        }
    }

    /// Whether every window and layer surface on an output has drawn at the
    /// size it was last configured to (after a rotation: the new one).
    /// While locked only the lock surface is shown: whether it has drawn at
    /// the output's size (crate::lock; before, the turn took the lock
    /// screen's picture at its old size, and the right size came in a jump
    /// at the end of the turn).
    fn drawn_at_new_size(&self, output: &Output) -> bool {
        if self.lock.locked && self.ambient.is_none() {
            return self.lock_drawn_at_size(output);
        }
        let windows_done = self.space.elements().all(|w| {
            if !self.is_on(w, output) || self.is_minimized(w) {
                return true;
            }
            let Some(toplevel) = w.toplevel() else {
                return true;
            };
            let pending = toplevel.with_pending_state(|s| s.size);
            pending.is_none() || toplevel.with_committed_state(|s| s.and_then(|s| s.size)) == pending
        });
        let map = layer_map_for_output(output);
        let layers_done = map.layers().all(|l| {
            let pending = l.layer_surface().with_pending_state(|s| s.size);
            pending.is_none() || l.layer_surface().with_committed_state(|s| s.and_then(|s| s.size)) == pending
        });
        windows_done && layers_done
    }

    /// The rectangle a placement covers, in the global space.
    pub fn placement_rect(&self, placement: &Placement, fullscreen: bool) -> Option<Rectangle<i32, Logical>> {
        match placement {
            Placement::Output(name) => {
                let output = self.output_by_name(name).or_else(|| self.layout_order().into_iter().next())?;
                let geometry = self.space.output_geometry(&output)?;
                if fullscreen {
                    return Some(geometry);
                }
                // A layer-shell panel (a status bar, later) takes its exclusive
                // zone off the maximized area.
                let mut zone = self.window_zone(&output);
                zone.loc += geometry.loc;
                Some(zone)
            }
            Placement::Span => {
                // The panels' bounding box, less their layer-shell panels'
                // exclusive zones edge by edge (fullscreen: none of them).
                // The on-screen keyboard is not among them: it is on ONE
                // panel, over the window there (osk_target_output), and the
                // other panel keeps its full height.
                let panels: Vec<(crate::layout::Rect, crate::layout::Rect)> = self
                    .outputs
                    .iter()
                    .filter(|o| self.pose.in_use(&o.name()))
                    .filter_map(|o| {
                        let g = self.space.output_geometry(o)?;
                        let zone = if fullscreen {
                            g
                        } else {
                            let mut zone = self.zone_without_osk(o);
                            zone.loc += g.loc;
                            zone
                        };
                        Some((
                            (g.loc.x, g.loc.y, g.size.w, g.size.h),
                            (zone.loc.x, zone.loc.y, zone.size.w, zone.size.h),
                        ))
                    })
                    .collect();
                crate::layout::span_area(&panels)
                    .map(|(x, y, w, h)| Rectangle::new((x, y).into(), (w, h).into()))
            }
        }
    }

    /// An output's area (in its own frame) less the exclusive zones of its
    /// layer surfaces other than the on-screen keyboard.
    pub fn zone_without_osk(&self, output: &Output) -> Rectangle<i32, Logical> {
        self.zone_of(output, true, false)
    }

    /// The room of a window maximized on an output (in the output's frame):
    /// less every exclusive zone, but while the task overview is open not the
    /// on-screen keyboard's (it is not shown then: the windows keep their
    /// size, and the cards do not shift if the keyboard comes or goes).
    pub fn window_zone(&self, output: &Output) -> Rectangle<i32, Logical> {
        if self.taskview.open || self.osk_hidden() {
            self.zone_of(output, true, false)
        } else {
            layer_map_for_output(output).non_exclusive_zone()
        }
    }

    /// The layer surface a (root) surface is, on whichever panel.
    pub fn layer_of_surface(&self, root: &WlSurface) -> Option<LayerSurface> {
        self.outputs.iter().find_map(|o| {
            layer_map_for_output(o).layer_for_surface(root, WindowSurfaceType::TOPLEVEL).cloned()
        })
    }

    /// Whether a touch on `output` (on `touched`, a window or nothing) would
    /// take the keyboard elsewhere: to another window than the one with the
    /// keyboard focus, or, on the spanning window that has it, to the other
    /// panel (the text field's panel, which puts the on-screen keyboard on the
    /// opposite one).
    pub fn touch_moves_keyboard(&self, output: &str, touched: Option<&Window>, on_window: bool) -> bool {
        let Some(touched) = touched else {
            return false;
        };
        let focused = self
            .seat
            .get_keyboard()
            .and_then(|k| k.current_focus())
            .and_then(|surface| self.window_for_surface(&surface));
        if focused.as_ref() != Some(touched) {
            return true;
        }
        let spans = *window_data(touched).placement.borrow() == Placement::Span;
        spans && on_window && self.field_output.as_deref() != Some(output)
    }

    /// A touch takes the keyboard: its panel becomes the active one (new
    /// windows and the keyboard go there), the window touched comes to the
    /// front with the keyboard, and a touch on a window marks its panel as the
    /// text field's (span: the on-screen keyboard goes to the other one).
    pub fn focus_by_touch(&mut self, output: &str, touched: Option<&Window>, on_window: bool) {
        if self.active_output.as_deref() != Some(output) {
            self.active_output = Some(output.to_owned());
        }
        if on_window && self.field_output.as_deref() != Some(output) {
            self.field_output = Some(output.to_owned());
            self.loop_handle.insert_idle(|state| state.place_osk());
        }
        if let Some(window) = touched {
            if self.space.elements().any(|w| w == window) {
                self.raise_window(window, true);
                self.queue_redraw_all();
            }
        }
        self.refresh_keyboard_focus();
    }

    /// An output's area (in its own frame) less the exclusive zones of its
    /// layer surfaces: the shell's (status bar, a taskbar dock) when `bars`,
    /// the on-screen keyboard's when `osk`.
    pub fn zone_of(&self, output: &Output, bars: bool, osk: bool) -> Rectangle<i32, Logical> {
        let map = layer_map_for_output(output);
        let size = self.space.output_geometry(output).map(|g| (g.size.w, g.size.h)).unwrap_or((0, 0));
        let zones: Vec<(crate::layout::ZoneEdge, i32)> = map
            .layers()
            .filter(|l| if OSK_NAMESPACES.contains(&l.namespace()) { osk } else { bars })
            .filter_map(|l| {
                let cached = l.cached_state();
                let z = match cached.exclusive_zone {
                    smithay::wayland::shell::wlr_layer::ExclusiveZone::Exclusive(z) => z as i32,
                    _ => return None,
                };
                let a = cached.anchor;
                use smithay::wayland::shell::wlr_layer::Anchor;
                let edge = crate::layout::exclusive_edge(
                    a.contains(Anchor::TOP),
                    a.contains(Anchor::BOTTOM),
                    a.contains(Anchor::LEFT),
                    a.contains(Anchor::RIGHT),
                )?;
                // A margin on that edge moves the zone in by as much.
                let margin = match edge {
                    crate::layout::ZoneEdge::Top => cached.margin.top,
                    crate::layout::ZoneEdge::Bottom => cached.margin.bottom,
                    crate::layout::ZoneEdge::Left => cached.margin.left,
                    crate::layout::ZoneEdge::Right => cached.margin.right,
                };
                Some((edge, z + margin))
            })
            .collect();
        let (x, y, w, h) = crate::layout::zone(size, &zones);
        Rectangle::new((x, y).into(), (w, h).into())
    }

    /// Fit an xdg popup (a GTK menu, a popover, a drop-down) into the part
    /// of ONE panel it may use, as its positioner allows (xdg_positioner's
    /// constraint adjustment: flip, then slide, then resize; Smithay's
    /// `get_unconstrained_geometry`). The panel: a window's own; for a
    /// spanning window the panel under the popup's anchor (the nearer one if
    /// the anchor is in the hinge gap), so a menu never straddles the hinge.
    /// The part: the panel less the status bar (not under a fullscreen
    /// window, which hides it), a taskbar dock and the on-screen keyboard. A
    /// layer surface's popup (the shell's) may use its whole panel less the
    /// keyboard.
    pub fn unconstrain_popup(&self, popup: &PopupSurface) {
        let kind = PopupKind::Xdg(popup.clone());
        let Ok(root) = smithay::desktop::find_popup_root_surface(&kind) else {
            return;
        };
        let parent_offset = smithay::desktop::get_popup_toplevel_coords(&kind);
        // The root's origin (its window geometry, or the layer surface) in
        // the global space, the panels it may use, and whether the bars count.
        let (origin, candidates, bars) = if let Some(window) = self.window_for_surface(&root) {
            let Some(geometry) = self.space.element_geometry(&window) else {
                return;
            };
            let candidates: Vec<Output> = match &*window_data(&window).placement.borrow() {
                Placement::Output(name) => self
                    .output_by_name(name)
                    .or_else(|| self.layout_order().into_iter().next())
                    .into_iter()
                    .collect(),
                Placement::Span => {
                    self.layout_order().into_iter().filter(|o| self.pose.in_use(&o.name())).collect()
                }
            };
            let fullscreen = *window_data(&window).fullscreen.borrow();
            (geometry.loc, candidates, !fullscreen)
        } else {
            let mut found = None;
            for output in &self.outputs {
                let map = layer_map_for_output(output);
                if let Some(layer) = map.layer_for_surface(&root, WindowSurfaceType::TOPLEVEL) {
                    if let (Some(g), Some(o)) =
                        (map.layer_geometry(layer), self.space.output_geometry(output))
                    {
                        found = Some((o.loc + g.loc, vec![output.clone()], false));
                    }
                    break;
                }
            }
            match found {
                Some(found) => found,
                None => return,
            }
        };
        let positioner = popup.with_pending_state(|state| state.positioner);
        let anchor = origin
            + parent_offset
            + positioner.anchor_rect.loc
            + Point::from((positioner.anchor_rect.size.w / 2, positioner.anchor_rect.size.h / 2));
        let rects: Vec<crate::layout::Rect> = candidates
            .iter()
            .filter_map(|o| self.space.output_geometry(o))
            .map(|g| (g.loc.x, g.loc.y, g.size.w, g.size.h))
            .collect();
        if rects.len() != candidates.len() {
            return;
        }
        let Some(index) = crate::layout::panel_for_point(&rects, (anchor.x, anchor.y)) else {
            return;
        };
        let output = &candidates[index];
        let mut usable = self.zone_of(output, bars, true);
        usable.loc += Point::from((rects[index].0, rects[index].1));
        // A window shown scaled down: the room in its client's coordinates
        // (its popups are drawn scaled with it, crate::dialog).
        if let Some(fit) = self.window_for_surface(&root).and_then(|w| self.scaled_fit(&w)) {
            let (x, y, w, h) = crate::dialog::usable_for_client(&fit, rect_tuple(usable));
            usable = Rectangle::new((x, y).into(), (w, h).into());
        }
        // The target is in the parent's frame (its window geometry).
        let mut target = usable;
        target.loc -= origin + parent_offset;
        let before = positioner.get_geometry();
        let after = positioner.get_unconstrained_geometry(target);
        popup.with_pending_state(|state| {
            state.geometry = after;
        });
        if before != after {
            debug!(output = output.name(), ?before, ?after, ?target, "popup fitted into its panel");
        }
    }

    /// Whether a window is (at least partly) on an output.
    pub fn is_on(&self, window: &Window, output: &Output) -> bool {
        match &*window_data(window).placement.borrow() {
            Placement::Output(name) => *name == output.name(),
            Placement::Span => self.pose.in_use(&output.name()),
        }
    }

    /// The frontmost window on an output (Android's keyboard window does
    /// not count: it is in front of the app typed in, which is the front).
    pub fn topmost_on(&self, output: &Output) -> Option<Window> {
        self.space
            .elements()
            .rev()
            .find(|w| self.is_on(w, output) && !self.is_minimized(w) && !self.is_accessory(w))
            .cloned()
    }

    /// Whether a layer surface of one of these namespaces is shown on a panel
    /// (mapped with a picture).
    pub fn layer_shown(&self, output: &str, namespaces: &[&str]) -> bool {
        let Some(output) = self.output_by_name(output) else {
            return false;
        };
        let map = layer_map_for_output(&output);
        let shown = map.layers().any(|l| namespaces.contains(&l.namespace()) && l.bbox().size.w > 0);
        shown
    }

    /// The window under a point (not a minimized one), with where it is drawn.
    pub fn window_under(&self, point: Point<f64, Logical>) -> Option<(Window, Point<i32, Logical>)> {
        self.window_under_for_client(point).map(|(w, loc, _)| (w, loc))
    }

    /// The window under a point, with where it is drawn and where the point
    /// is for its client (a window shown scaled down: mapped back,
    /// crate::dialog::to_client). A window on one panel is touched only
    /// there, even where a client drew past its panel (it is not drawn on
    /// the other one either).
    pub fn window_under_for_client(&self, point: Point<f64, Logical>) -> Option<ClientHit> {
        self.space.elements().rev().find_map(|w| {
            if self.is_minimized(w) {
                return None;
            }
            let loc = self.space.element_location(w)? - w.geometry().loc;
            if let Some(output) = self.clip_output(w).or_else(|| self.own_output(w)) {
                let on =
                    self.space.output_geometry(&output).map(|g| g.to_f64().contains(point)).unwrap_or(false);
                if !on {
                    return None;
                }
            }
            let client = match self.scaled_fit(w) {
                Some(fit) => crate::dialog::to_client(&fit, (point.x, point.y)).into(),
                None => point,
            };
            smithay::desktop::space::SpaceElement::is_in_input_region(w, &(client - loc.to_f64()))
                .then(|| (w.clone(), loc, client))
        })
    }

    /// The panel a window placed on one panel is on (None: it spans, or its
    /// panel is gone and it shows wherever it fell back to).
    pub fn own_output(&self, window: &Window) -> Option<Output> {
        match &*window_data(window).placement.borrow() {
            Placement::Output(name) => self.output_by_name(name),
            Placement::Span => None,
        }
    }

    /// Where a new window goes: the active output if it is empty, else the first
    /// empty output left to right, else the active output; among the outputs the
    /// pose uses, and of those the lit ones if any is lit.
    /// With it the launcher's hint when that chose it.
    pub(crate) fn output_for_new_window(&mut self) -> Option<(Output, Option<LaunchHint>)> {
        if let Some(mut hint) = self.launch_hint.take() {
            // Laptop mode: never on the keyboard panel.
            hint.output = self.laptop_hint_output(&hint.output);
            if Instant::now() < hint.until
                && (self.pose.in_use(&hint.output) || crate::extout::is_external(&hint.output))
            {
                if let Some(output) = self.output_by_name(&hint.output) {
                    info!(output = hint.output, "new window goes where the launcher asked");
                    // The launch happened there: that panel is the active one
                    // (the new window gets the keyboard).
                    if self.is_lit(&hint.output) {
                        self.active_output = Some(hint.output.clone());
                    }
                    return Some((output, Some(hint)));
                }
            }
        }
        // Working on an external display: new windows open there.
        if let Some(active) = self.active_output().filter(|o| crate::extout::is_external(&o.name())) {
            if self.is_lit(&active.name()) {
                return Some((active, None));
            }
        }
        // Laptop mode: on its desk panel.
        if let Some(desk) = self.laptop_desk().and_then(|name| self.output_by_name(&name)) {
            return Some((desk, None));
        }
        let usable: Vec<Output> =
            self.layout_order().into_iter().filter(|o| self.pose.in_use(&o.name())).collect();
        let lit: Vec<Output> = usable.iter().filter(|o| self.is_lit(&o.name())).cloned().collect();
        let pool = if lit.is_empty() { usable } else { lit };
        let active = self.active_output().filter(|o| pool.contains(o)).or_else(|| pool.first().cloned())?;
        if self.topmost_on(&active).is_none() {
            return Some((active, None));
        }
        pool.iter().find(|o| self.topmost_on(o).is_none()).cloned().or(Some(active)).map(|o| (o, None))
    }

    /// Put a window where its placement says, sized to it.
    pub fn place(&mut self, window: &Window) {
        // Nothing is put on a display that mirrors the phone: back to a
        // panel (crate::mirrorlive).
        if self.mirroring() {
            self.mirror_rehome(window);
        }
        let data = window_data(window);
        let placement = data.placement.borrow().clone();
        let fullscreen = *data.fullscreen.borrow();
        // On an external display: its own frame there (crate::desk).
        if self.desk_place(window, &placement, fullscreen) {
            return;
        }
        // Half of a split in force: its half; otherwise its placement's area.
        let tile = if fullscreen { None } else { self.tile_rect(window) };
        let Some(mut rect) = tile.or_else(|| self.placement_rect(&placement, fullscreen)) else {
            return;
        };
        let android_full = *data.android_full.borrow();
        if android_full {
            // Android's caption bar above the glass (crate::waydroid).
            let caption = self.android.cfg.caption.round() as i32;
            rect.loc.y -= caption;
            rect.size.h += caption;
        }
        if self.android_kind(window) == crate::waydroid::AndroidKind::Ime {
            // Android's keyboard: at the corner of the window it is with
            // (a half's too), where the display it is drawn on lines up.
            if let Some(with) = self.android_ime_rect() {
                rect.loc = with.loc;
            }
        }
        let waydroid = crate::android::is_waydroid(app_id_and_title(window).0.as_deref());
        // A dialog: at most the room, smaller where its max size says so
        // (crate::dialog); maximized only when it fills the room.
        let dialog = !fullscreen && tile.is_none() && !android_full && !waydroid && self.is_dialog(window);
        let size = if dialog && window.is_x11() {
            // An X11 dialog: the size it asked for, within the room.
            Self::x11_dialog_size(window, rect.size)
        } else if dialog {
            let (w, h) = crate::dialog::dialog_size((rect.size.w, rect.size.h), max_size(window));
            smithay::utils::Size::from((w, h))
        } else if waydroid {
            // One Android display: every Waydroid window at the lead's
            // size, or the display would be resized at every configure
            // (crate::android).
            self.android_display_size().map(smithay::utils::Size::from).unwrap_or(rect.size)
        } else {
            rect.size
        };
        let maximized = !fullscreen && size == rect.size;
        if let Some(toplevel) = window.toplevel() {
            toplevel.with_pending_state(|state| {
                // Activated follows the keyboard (Space::raise_element), not this.
                state.size = Some(size);
                if fullscreen {
                    state.states.set(xdg_toplevel::State::Fullscreen);
                    state.states.unset(xdg_toplevel::State::Maximized);
                } else {
                    state.states.unset(xdg_toplevel::State::Fullscreen);
                    if maximized {
                        state.states.set(xdg_toplevel::State::Maximized);
                    } else {
                        state.states.unset(xdg_toplevel::State::Maximized);
                    }
                }
                // Back from an external display's desk, where it drew its
                // own title bar (crate::extout): none on a panel.
                if state.decoration_mode == Some(DecorationMode::ClientSide) {
                    state.decoration_mode = Some(DecorationMode::ServerSide);
                }
            });
            if toplevel.is_initial_configure_sent() {
                // Only sends when the pending state differs from the last one.
                toplevel.send_pending_configure();
            }
        }
        // Where it shows: a split half, Android's windows (their surface is
        // the whole Android display, crate::android) at their corner;
        // anything else centred (a dialog) or scaled into its panel when it
        // draws bigger than the panel (crate::dialog).
        let loc = if tile.is_some() || android_full || waydroid {
            *data.fit.borrow_mut() = None;
            *data.fit_from.borrow_mut() = None;
            rect.loc
        } else {
            self.fit_location(window, &placement, rect, size, fullscreen, dialog)
        };
        if self.space.element_location(window) != Some(loc) {
            // map_element re-inserts at the END of the stacking order (in front);
            // callers that move several windows restore the order (place_all).
            self.space.map_element(window.clone(), loc, false);
        }
        // An X11 window is told where it is, how big, and its states
        // (crate::xwayland): X11 places its popups by it.
        self.x11_configure(window, Some(size), Some((fullscreen, maximized)));
        if self.android_kind(window) == crate::waydroid::AndroidKind::Task {
            // Spanning or full screen: the task fills its display inside
            // Android; back on a panel it gets its medium bounds. Not
            // filled, it is kept inside the part of the display it shows
            // (a turn, back from a span: crate::android::needs_refit).
            self.android_fill_for(window, placement == Placement::Span, size.w, size.h);
            self.android_keep_inside(window, (rect.size.w, rect.size.h), (size.w, size.h));
        }
    }

    /// Where a window on a panel shows (its window geometry's corner, the
    /// global space), keeping its fit in its data: centred if it is a
    /// dialog, scaled down if it draws bigger than the panel's room less its
    /// bars (crate::dialog::fit). `area` is the room it was configured to,
    /// `configured` the size asked of it (the size until it has drawn). A
    /// spanning window stays at its area's corner, as it draws.
    fn fit_location(
        &self,
        window: &Window,
        placement: &Placement,
        area: Rectangle<i32, Logical>,
        configured: smithay::utils::Size<i32, Logical>,
        fullscreen: bool,
        dialog: bool,
    ) -> Point<i32, Logical> {
        let data = window_data(window);
        // An X11 window is the size it is configured to (X11 resizes at
        // once), and it is never scaled down to fit: its popups, placed by
        // the app in X11's coordinates, could not follow (crate::xwayland).
        let x11 = window.is_x11();
        let geometry = if x11 { configured } else { window.geometry().size };
        let settled = drew_as_asked(window);
        *data.fit_from.borrow_mut() = Some(((geometry.w, geometry.h), settled));
        let room = match placement {
            Placement::Output(name) => self
                .output_by_name(name)
                .or_else(|| self.layout_order().into_iter().next())
                .and_then(|output| {
                    let g = self.space.output_geometry(&output)?;
                    if fullscreen {
                        return Some(g);
                    }
                    let mut zone = self.zone_without_osk(&output);
                    zone.loc += g.loc;
                    Some(zone)
                }),
            Placement::Span => None,
        };
        let Some(room) = room else {
            *data.fit.borrow_mut() = None;
            return area.loc;
        };
        let size = if geometry.w > 0 && geometry.h > 0 { geometry } else { configured };
        let fit =
            crate::dialog::fit(rect_tuple(area), rect_tuple(room), (size.w, size.h), dialog, settled && !x11);
        let plain = fit.factor >= 1.0 && fit.at == (area.loc.x, area.loc.y);
        let was = *data.fit.borrow();
        if !plain && was.map(|w| w.factor) != Some(fit.factor) && fit.factor < 1.0 {
            info!(
                id = data.id,
                w = size.w,
                h = size.h,
                room_w = room.size.w,
                room_h = room.size.h,
                factor = fit.factor,
                "window bigger than its panel: shown scaled down"
            );
        }
        *data.fit.borrow_mut() = (!plain).then_some(fit);
        Point::from(fit.at)
    }

    /// A window's fit again after it drew at a new size (or caught up with
    /// the size asked of it): a dialog stays centred, a window that drew
    /// bigger than its panel is scaled into it. It keeps its place in the
    /// stacking order. Nothing to do if neither changed.
    fn refit(&mut self, window: &Window) {
        if window.is_x11() {
            // Its size is the compositor's (place configures it): nothing
            // to follow (crate::xwayland).
            return;
        }
        if self.on_desk(window) {
            // A desk's window floats in its own frame (crate::desk,
            // extout::desk_place); never fitted. This ran on every commit
            // and put a floating window at its panel's corner until the
            // next re-place moved it back: on laptop mode's desk panel a
            // window jumped to the top left as it drew.
            return;
        }
        let data = window_data(window);
        let geometry = window.geometry().size;
        if geometry.w <= 0 || geometry.h <= 0 {
            return;
        }
        let key = ((geometry.w, geometry.h), drew_as_asked(window));
        if *data.fit_from.borrow() == Some(key) {
            return;
        }
        let placement = data.placement.borrow().clone();
        let fullscreen = *data.fullscreen.borrow();
        let tiled = !fullscreen && self.tile_rect(window).is_some();
        let waydroid = crate::android::is_waydroid(app_id_and_title(window).0.as_deref());
        if tiled || waydroid || *data.android_full.borrow() {
            *data.fit.borrow_mut() = None;
            *data.fit_from.borrow_mut() = Some(key);
            return;
        }
        let Some(area) = self.placement_rect(&placement, fullscreen) else {
            return;
        };
        let dialog = !fullscreen && self.is_dialog(window);
        let configured =
            window.toplevel().and_then(|t| t.with_pending_state(|s| s.size)).unwrap_or(area.size);
        let loc = self.fit_location(window, &placement, area, configured, fullscreen, dialog);
        if self.space.element_location(window) != Some(loc) {
            self.relocate(window, loc);
        }
    }

    /// Move a window in the space without bringing it to the front (map_element
    /// re-inserts at the end of the stacking order: the ones that were in
    /// front of it go back there).
    fn relocate(&mut self, window: &Window, loc: Point<i32, Logical>) {
        let above: Vec<Window> =
            self.space.elements().skip_while(|w| *w != window).skip(1).cloned().collect();
        self.space.map_element(window.clone(), loc, false);
        for w in &above {
            self.space.raise_element(w, false);
        }
        self.x11_configure(window, None, None);
    }

    /// How a window is shown scaled down (crate::dialog), if it is.
    pub fn scaled_fit(&self, window: &Window) -> Option<crate::dialog::Fit> {
        window_data(window).fit.borrow().filter(|f| f.factor < 1.0)
    }

    /// The window a surface belongs to: a toplevel's own surface or a
    /// subsurface of it, or one of its popups (and their subsurfaces).
    pub fn window_of_surface(&self, surface: &WlSurface) -> Option<Window> {
        let mut root = surface.clone();
        while let Some(parent) = get_parent(&root) {
            root = parent;
        }
        if let Some(window) = self.window_for_surface(&root) {
            return Some(window);
        }
        let popup = self.popups.find_popup(&root)?;
        let toplevel = smithay::desktop::find_popup_root_surface(&popup).ok()?;
        self.window_for_surface(&toplevel)
    }

    /// Where a point (global) is for the client of `surface`: the same,
    /// unless its window is shown scaled down (crate::dialog::to_client).
    pub fn client_location(&self, surface: &WlSurface, point: Point<f64, Logical>) -> Point<f64, Logical> {
        let Some(fit) = self.window_of_surface(surface).and_then(|w| self.scaled_fit(&w)) else {
            return point;
        };
        crate::dialog::to_client(&fit, (point.x, point.y)).into()
    }

    /// Tell the event stream that a window came to the front by itself (a
    /// new window, an activation): the shell closes its app grid and quick
    /// menu on those panels (crate::front).
    pub fn announce_front(&mut self, window: &Window, why: &str) {
        let own = match &*window_data(window).placement.borrow() {
            Placement::Output(name) => Some(name.clone()),
            Placement::Span => None,
        };
        let in_use = self.in_use();
        let in_use: Vec<&str> = in_use.iter().map(String::as_str).collect();
        let outputs = crate::front::front_panels(own.as_deref(), &in_use);
        let id = window_data(window).id;
        debug!(id, why, ?outputs, "window in front");
        crate::ipc::broadcast(
            self,
            &serde_json::json!({
                "event": "window_front",
                "window": id,
                "app_id": app_id_and_title(window).0,
                "outputs": outputs,
                "why": why,
            }),
        );
    }

    /// Whether a window is a dialog (crate::dialog): it has a parent (its
    /// own client's window, or another client's through xdg-foreign), or it
    /// is a portal backend's window.
    pub fn is_dialog(&self, window: &Window) -> bool {
        if let Some(x11) = window.x11_surface() {
            return Self::x11_is_dialog(x11);
        }
        let Some(toplevel) = window.toplevel() else {
            return false;
        };
        toplevel.parent().is_some() || crate::dialog::is_portal_app(app_id_and_title(window).0.as_deref())
    }

    /// Put a dialog where it belongs (crate::dialog::dialog_panel): on its
    /// parent's panel; for a spanning parent (or none known) the panel
    /// touched last, else the one with more room; never across the hinge,
    /// whatever the pose would do with a new window. Its parent comes back
    /// from home with it, and a parent asked to close has answered. Before
    /// its first configure, and again if its parent changes later.
    pub(crate) fn settle_dialog(&mut self, window: &Window) {
        if !self.is_dialog(window) {
            return;
        }
        let parent = window
            .toplevel()
            .and_then(|t| t.parent())
            .and_then(|p| self.window_for_surface(&p))
            .or_else(|| self.x11_parent(window));
        if let Some(parent) = &parent {
            let id = window_data(parent).id;
            if self.taskview.closing.remove(&id).is_some() {
                info!(id, "the window answered its close with a dialog");
            }
            self.restore_window(parent);
        }
        let parent_place = match &parent {
            Some(p) => match &*window_data(p).placement.borrow() {
                Placement::Output(name) => crate::dialog::Parent::Panel(name.clone()),
                Placement::Span => crate::dialog::Parent::Span,
            },
            None => crate::dialog::Parent::Unknown,
        };
        let rooms: Vec<(String, crate::dialog::Rect)> = self
            .layout_order()
            .into_iter()
            .filter(|o| self.pose.in_use(&o.name()))
            .filter_map(|o| {
                let g = self.space.output_geometry(&o)?;
                let mut zone = self.window_zone(&o);
                zone.loc += g.loc;
                Some((o.name(), rect_tuple(zone)))
            })
            .collect();
        let panels: Vec<(&str, crate::dialog::Rect)> = rooms.iter().map(|(n, r)| (n.as_str(), *r)).collect();
        let Some(panel) = crate::dialog::dialog_panel(&parent_place, self.active_output.as_deref(), &panels)
        else {
            return;
        };
        let data = window_data(window);
        info!(
            id = data.id,
            parent = ?parent.as_ref().map(|p| window_data(p).id),
            ?parent_place,
            panel,
            "dialog placed on one panel"
        );
        *data.placement.borrow_mut() = Placement::Output(panel);
        *data.saved.borrow_mut() = None;
        self.place(window);
        self.space.raise_element(window, true);
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// The output a window in Android full screen is cut to (its caption
    /// bar reaches above its panel: never drawn or touched there).
    pub fn clip_output(&self, window: &Window) -> Option<Output> {
        if let Placement::Output(name) = &*window_data(window).placement.borrow() {
            if crate::extout::is_external(name) {
                // A window on a display is cut to it (it may hang over its
                // edge, which in the global space is a panel's).
                return self.output_by_name(name);
            }
        }
        if !*window_data(window).android_full.borrow() {
            return None;
        }
        match &*window_data(window).placement.borrow() {
            Placement::Output(name) => self.output_by_name(name),
            Placement::Span => None,
        }
    }

    /// Move a window to another placement; that becomes its own placement (a
    /// pose no longer brings it back anywhere).
    ///
    /// A window placed explicitly leaves the split it was half of (its
    /// partner fills its panel again).
    pub fn set_placement(&mut self, window: &Window, placement: Placement) {
        let id = window_data(window).id;
        info!(id, ?placement, "window placed");
        self.restore_window(window);
        *window_data(window).placement.borrow_mut() = placement;
        *window_data(window).saved.borrow_mut() = None;
        let split_ended = self.forget_split_of(id);
        // An Android window: the display may take its size, and the other
        // Waydroid windows with it (crate::android).
        let android = self.android_kind(window).on_display();
        if android {
            self.android_laid_out(window);
        }
        if split_ended || android {
            self.place_all();
        }
        self.place(window);
        self.space.raise_element(window, true);
        // Android's keyboard goes along with the app typed in.
        self.follow_android_ime();
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// Render one output's scene to a PNG: what its next frame shows (a
    /// rotation's turn too: this is how it is checked, slowed down).
    pub fn screenshot(&mut self, name: &str) -> anyhow::Result<(i32, i32, Vec<u8>)> {
        let output = self.output_by_name(name).ok_or_else(|| anyhow::anyhow!("no output named {name}"))?;
        let elements = self.shown_elements(&output)?;
        let device = self.tty.device.as_mut().ok_or_else(|| anyhow::anyhow!("no device opened"))?;
        crate::screenshot::capture(&mut device.renderer, &output, &elements)
    }

    /// A window by its control-socket id.
    pub fn window_by_id(&self, id: u64) -> Option<Window> {
        self.space.elements().find(|w| window_data(w).id == id).cloned()
    }

    /// The window a `wl_surface` belongs to, if any.
    pub fn window_for_surface(&self, surface: &WlSurface) -> Option<Window> {
        self.space
            .elements()
            .find(|window| window.wl_surface().map(|s| *s == *surface).unwrap_or(false))
            .cloned()
    }

    /// The output a point of the global space is on.
    pub fn output_at(&self, point: Point<f64, Logical>) -> Option<Output> {
        self.space.output_under(point).next().cloned()
    }

    /// The surface under a point, for touch focus, with its origin in the global
    /// space.
    pub fn surface_under(&self, point: Point<f64, Logical>) -> Option<(WlSurface, Point<f64, Logical>)> {
        let output = self.output_at(point)?;
        let output_loc = self.space.output_geometry(&output)?.loc.to_f64();
        if self.lock.locked {
            // Locked: only the lock surface of this panel, nothing else.
            let surface = self.lock_surface(&output)?;
            return smithay::desktop::utils::under_from_surface_tree(
                &surface,
                point - output_loc,
                (0, 0),
                WindowSurfaceType::ALL,
            )
            .map(|(s, offset)| (s, output_loc + offset.to_f64()));
        }

        let hide_top = crate::render::fullscreen_in_front(&self.space, &output);
        // Every surface of a layer, front first, by its input region (a
        // see-through one lets the touch through to the next).
        let layer_hit = |layers: &[Layer]| {
            let map = layer_map_for_output(&output);
            for wanted in layers {
                for layer in map.layers_on(*wanted).rev() {
                    if AMBIENT_NAMESPACES.contains(&layer.namespace()) {
                        // The shell keeps its peek / glance surfaces mapped
                        // (ready to show at once); they take no touch.
                        continue;
                    }
                    if *wanted == Layer::Top && hide_top && !FULLSCREEN_KEEPS_TOP.contains(&layer.namespace())
                    {
                        continue;
                    }
                    if self.osk_hidden() && OSK_NAMESPACES.contains(&layer.namespace()) {
                        // Put away by a tap beside the field: not touched.
                        continue;
                    }
                    if *wanted == Layer::Overlay
                        && hide_top
                        && !self.taskview.open
                        && FULLSCREEN_HIDES_OVERLAYS.contains(&layer.namespace())
                    {
                        continue;
                    }
                    let Some(geometry) = map.layer_geometry(layer) else {
                        continue;
                    };
                    let layer_loc = geometry.loc.to_f64() + output_loc;
                    if let Some((surface, offset)) =
                        layer.surface_under(point - layer_loc, WindowSurfaceType::ALL)
                    {
                        return Some((surface, layer_loc + offset.to_f64()));
                    }
                }
            }
            None
        };

        if let Some(hit) = layer_hit(&[Layer::Overlay]) {
            return Some(hit);
        }
        // X11's menus and tooltips (override-redirect, crate::xwayland): in
        // front of the windows and the status bar.
        if !self.taskview.open {
            if let Some(hit) = self.x11_overlay_under(point) {
                return Some(hit);
            }
        }
        if let Some(hit) = layer_hit(&[Layer::Top]) {
            return Some(hit);
        }
        // A split at the front of this panel: each half belongs to its own
        // window, even if a client drew past its half.
        if let Some(window) = self.split_window_under(&output, point) {
            if let Some(window) = window {
                if let Some(loc) = self.space.element_location(&window) {
                    let render_loc = loc - window.geometry().loc;
                    if let Some((surface, offset)) =
                        window.surface_under(point - render_loc.to_f64(), WindowSurfaceType::ALL)
                    {
                        return Some((surface, (render_loc + offset).to_f64()));
                    }
                }
            }
            return layer_hit(&[Layer::Bottom, Layer::Background]);
        }
        if let Some((window, render_loc, client)) = self.window_under_for_client(point) {
            // A window shown scaled down: the origin is in its client's
            // coordinates, and so is the location the caller hands on
            // (client_location).
            if let Some((surface, offset)) =
                window.surface_under(client - render_loc.to_f64(), WindowSurfaceType::ALL)
            {
                return Some((surface, (render_loc + offset).to_f64()));
            }
        }
        layer_hit(&[Layer::Bottom, Layer::Background])
    }

    /// Ask for a frame on every output.
    pub fn queue_redraw_all(&mut self) {
        let crtcs = self.tty.crtcs();
        for crtc in crtcs {
            self.queue_redraw(crtc);
        }
    }

    /// Ask for a frame on one output (by CRTC). The frame is rendered from an
    /// idle callback, so many commits in one loop turn cost one render.
    pub fn queue_redraw(&mut self, crtc: smithay::reexports::drm::control::crtc::Handle) {
        let Some(surface) = self.tty.surface_mut(crtc) else {
            return;
        };
        surface.redraw_needed = true;
        let panel = crate::extout::is_panel(&surface.name);
        let queued = surface.redraw_queued;
        // A panel that draws: the displays that mirror it draw too
        // (crate::mirrorlive), at their own refresh.
        if panel && self.mirroring() {
            let mirrors: Vec<_> =
                self.tty.surfaces().iter().filter(|s| self.is_mirror(&s.name)).map(|s| s.crtc).collect();
            for mirror in mirrors {
                self.queue_redraw(mirror);
            }
        }
        let Some(surface) = self.tty.surface_mut(crtc) else {
            return;
        };
        if queued {
            return;
        }
        surface.redraw_queued = true;
        self.loop_handle.insert_idle(move |state| {
            if let Some(surface) = state.tty.surface_mut(crtc) {
                surface.redraw_queued = false;
            }
            crate::backend::render(state, crtc);
        });
    }

    /// Give the keyboard to the topmost layer surface on the active output that
    /// wants it, otherwise to the frontmost window there, otherwise to the
    /// frontmost window anywhere.
    ///
    /// Never while a client that the focus would leave or enter is being torn
    /// down: then the change waits for an idle callback ([`crate::teardown`]).
    pub fn refresh_keyboard_focus(&mut self) {
        let Some(keyboard) = self.seat.get_keyboard() else {
            return;
        };
        if keyboard.current_focus().as_ref().is_some_and(crate::teardown::client_dying) {
            self.refresh_keyboard_focus_later();
            return;
        }
        let reachable = |s: &WlSurface| crate::teardown::reachable(s);
        if self.lock.locked {
            let target = self
                .active_output()
                .and_then(|o| self.lock_surface(&o))
                .or_else(|| self.outputs.iter().find_map(|o| self.lock_surface(o)))
                .filter(reachable);
            if target != keyboard.current_focus() {
                self.set_keyboard_focus(&keyboard, target);
            }
            return;
        }
        if self.taskview.open {
            // The overview holds the keyboard off the windows (Escape is
            // the compositor's); the on-screen keyboard goes away.
            if keyboard.current_focus().is_some() {
                self.set_keyboard_focus(&keyboard, None);
            }
            return;
        }
        let active = self.active_output();
        let mut target = None;
        if let Some(output) = active.as_ref() {
            let layers = layer_map_for_output(output);
            for layer in layers.layers() {
                if layer.can_receive_keyboard_focus() && reachable(layer.wl_surface()) {
                    target = Some(layer.wl_surface().clone());
                }
            }
        }
        let target = target
            .or_else(|| {
                active
                    .as_ref()
                    .and_then(|o| self.topmost_on(o))
                    .and_then(|w| w.wl_surface().map(|s| s.into_owned()))
                    .filter(reachable)
            })
            .or_else(|| {
                self.space
                    .elements()
                    .rev()
                    .filter(|w| !self.is_minimized(w))
                    .filter_map(|w| w.wl_surface().map(|s| s.into_owned()))
                    .find(reachable)
            });
        if target == keyboard.current_focus() {
            return;
        }
        self.set_keyboard_focus(&keyboard, target);
    }

    /// Move the keyboard focus now, or from an idle callback when a client
    /// it touches is being torn down ([`crate::teardown`]).
    fn set_keyboard_focus(
        &mut self,
        keyboard: &smithay::input::keyboard::KeyboardHandle<DuoWc>,
        target: Option<WlSurface>,
    ) {
        let serial = smithay::utils::SERIAL_COUNTER.next_serial();
        // The keyboard put away for one app comes back for another.
        let to = target.as_ref().and_then(|s| s.client()).map(|c| c.id());
        if self.osk_away.away.is_some() && self.osk_away.away != to {
            self.osk_back("the keyboard focus went to another app");
        }
        if !crate::teardown::set_keyboard_focus(keyboard, self, target, serial) {
            self.refresh_keyboard_focus_later();
        }
    }

    /// What a destroyed window or layer surface leaves to settle: re-place the
    /// windows (`place`), move the keyboard focus, redraw. From an idle
    /// callback, not inside the destructor: a destructor runs in the middle
    /// of its client's teardown, when an event to that client's other objects
    /// can abort the compositor ([`crate::teardown`]).
    fn after_destroyed(&mut self, what: &str, place: bool) {
        // The case that used to abort: the keyboard is still on a surface of
        // the client this object went with.
        let focus = self.seat.get_keyboard().and_then(|k| k.current_focus());
        if focus.as_ref().is_some_and(crate::teardown::client_dying) {
            debug!(what, "gone with its client, which has the keyboard: the focus moves after the teardown");
        }
        self.loop_handle.insert_idle(move |state| {
            if place {
                state.place_all();
            }
            state.refresh_keyboard_focus();
            state.queue_redraw_all();
        });
    }

    /// Work out the keyboard focus again once this dispatch is over (a
    /// client torn down in it is gone for good by then). One callback for
    /// any number of asks.
    pub fn refresh_keyboard_focus_later(&mut self) {
        if self.focus_refresh_queued {
            return;
        }
        debug!("keyboard focus: a client is being torn down, the change waits");
        self.focus_refresh_queued = true;
        self.loop_handle.insert_idle(|state| {
            state.focus_refresh_queued = false;
            state.refresh_keyboard_focus();
        });
    }

    /// Send the first configure a surface is waiting for.
    fn ensure_initial_configure(&mut self, surface: &WlSurface) {
        if let Some(window) = self.window_for_surface(surface) {
            if let Some(toplevel) = window.toplevel() {
                if !toplevel.is_initial_configure_sent() {
                    // Its parent, app id and size limits are known now: a
                    // dialog goes on its parent's panel, at its size.
                    self.settle_dialog(&window);
                    // Opened where nothing is drawn (a dark panel): its
                    // first configure says so (`suspended`, crate::frames).
                    self.pacing_refresh();
                    toplevel.send_configure();
                }
            }
            return;
        }

        for output in self.outputs.clone() {
            let mut layers = layer_map_for_output(&output);
            let layer = layers.layers().find(|layer| layer.wl_surface() == surface).cloned();
            if let Some(layer) = layer {
                let sent = with_states(surface, |states| {
                    states
                        .data_map
                        .get::<smithay::wayland::shell::wlr_layer::LayerSurfaceData>()
                        .map(|data| data.lock().unwrap().initial_configure_sent)
                        .unwrap_or(true)
                });
                layers.arrange();
                if !sent {
                    layer.layer_surface().send_configure();
                }
                drop(layers);
                // An exclusive zone changes the maximized area.
                self.place_all();
                // Keyboard interactivity is known only once committed: an
                // app grid (on demand) takes the keyboard now, not at its map.
                self.refresh_keyboard_focus();
                return;
            }
        }
    }

    /// The output a surface is shown on: a window's own panel (the active
    /// panel for a spanning one), a layer surface's panel.
    pub fn output_of_surface(&self, surface: &WlSurface) -> Option<Output> {
        if let Some(window) = self.window_for_surface(surface) {
            return match &*window_data(&window).placement.borrow() {
                Placement::Output(name) => self.output_by_name(name),
                Placement::Span => self.active_output(),
            };
        }
        self.outputs
            .iter()
            .find(|o| layer_map_for_output(o).layer_for_surface(surface, WindowSurfaceType::ALL).is_some())
            .cloned()
    }

    /// A surface in words, for the log: a window's id and app id, or a
    /// layer surface's namespace.
    pub fn describe_surface(&self, surface: &WlSurface) -> String {
        let mut root = surface.clone();
        while let Some(parent) = get_parent(&root) {
            root = parent;
        }
        let sub = if root != *surface { " (subsurface)" } else { "" };
        if let Some(window) = self.window_for_surface(&root) {
            let (app_id, _) = app_id_and_title(&window);
            return format!("window {} {}{sub}", window_data(&window).id, app_id.unwrap_or_default());
        }
        for output in &self.outputs {
            let map = layer_map_for_output(output);
            if let Some(layer) = map.layer_for_surface(&root, WindowSurfaceType::ALL) {
                return format!("layer {}{sub}", layer.namespace());
            }
        }
        if self.popups.find_popup(&root).is_some() {
            return format!("popup{sub}");
        }
        format!("surface {:?}{sub}", root.id())
    }

    /// The panel of the keyboard focus, else the active one.
    pub fn keyboard_output(&self) -> Option<Output> {
        self.seat
            .get_keyboard()
            .and_then(|k| k.current_focus())
            .and_then(|surface| self.output_of_surface(&surface))
            .or_else(|| self.active_output())
    }

    /// Where an on-screen keyboard belongs: with the keyboard focus, on a lit
    /// panel the pose uses. A spanning window covers both panels: there the
    /// keyboard goes on the panel the text field is NOT on (the one touched
    /// last is where the field is), over the window, so the field stays in
    /// sight and nothing is cut off the other panel.
    fn osk_target_output(&self) -> Option<Output> {
        let focus_spans = self
            .seat
            .get_keyboard()
            .and_then(|k| k.current_focus())
            .and_then(|surface| self.window_for_surface(&surface))
            .map(|w| *window_data(&w).placement.borrow() == Placement::Span)
            .unwrap_or(false);
        if focus_spans {
            let field = self.field_output.clone().or_else(|| self.active_output().map(|o| o.name()));
            let other = self
                .layout_order()
                .into_iter()
                .filter(|o| self.pose.in_use(&o.name()) && self.is_lit(&o.name()))
                .find(|o| Some(o.name()) != field);
            if other.is_some() {
                return other;
            }
        }
        // Typing on an external display: the keyboard on a lit panel
        // (the phone in front of it), never on the display.
        self.keyboard_output()
            .filter(|o| self.pose.in_use(&o.name()))
            .or_else(|| self.active_output().filter(|o| !crate::extout::is_external(&o.name())))
            .or_else(|| {
                self.layout_order().into_iter().find(|o| {
                    self.pose.in_use(&o.name())
                        && self.is_lit(&o.name())
                        && self.trackpad.panel.as_deref() != Some(o.name().as_str())
                })
            })
    }

    /// The panel an on-screen keyboard is on now, if one is mapped (and not
    /// put away by a tap beside the field, crate::oskleave).
    pub fn osk_output(&self) -> Option<Output> {
        if self.osk_hidden() {
            return None;
        }
        self.outputs
            .iter()
            .find(|o| layer_map_for_output(o).layers().any(|l| OSK_NAMESPACES.contains(&l.namespace())))
            .cloned()
    }

    /// Move on-screen keyboards to the panel of the keyboard focus. The
    /// client is not asked: the layer surface leaves one output and enters the
    /// other, and gets a configure with the new panel's width.
    pub fn place_osk(&mut self) {
        let Some(target) = self.osk_target_output() else {
            return;
        };
        let mut moved = false;
        for output in self.outputs.clone() {
            if output == target {
                continue;
            }
            let layers: Vec<LayerSurface> = layer_map_for_output(&output)
                .layers()
                .filter(|l| OSK_NAMESPACES.contains(&l.namespace()))
                .cloned()
                .collect();
            for layer in layers {
                layer_map_for_output(&output).unmap_layer(&layer);
                if let Err(err) = layer_map_for_output(&target).map_layer(&layer) {
                    warn!(?err, "failed to move the on-screen keyboard");
                    continue;
                }
                info!(from = output.name(), to = target.name(), "on-screen keyboard moved");
                moved = true;
            }
        }
        if moved {
            self.events_dirty = true;
            self.place_all();
            self.queue_redraw_all();
        }
    }

    /// Bring a window to the front of its panel and give it the keyboard
    /// (xdg-activation, the dock, the overview). The overview closes: the
    /// window is what is wanted now.
    pub fn activate_window(&mut self, window: &Window) {
        self.overview_close("a window was activated");
        self.restore_window(window);
        let output = match &*window_data(window).placement.borrow() {
            Placement::Output(name) => Some(name.clone()),
            Placement::Span => None,
        };
        if let Some(output) = output {
            if self.is_lit(&output) {
                self.active_output = Some(output);
            }
        }
        self.raise_window(window, true);
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// Set one window aside (minimized: not shown, still running; the
    /// keyboard goes to what is in front then). False: it already was.
    pub fn minimize_window(&mut self, window: &Window, why: &str) -> bool {
        if self.is_minimized(window) {
            return false;
        }
        info!(id = window_data(window).id, why, "window set aside");
        *window_data(window).minimized.borrow_mut() = true;
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
        true
    }

    /// What the event stream's subscribers see.
    pub fn snapshot(&self) -> Snapshot {
        let lit: Vec<String> =
            self.outputs.iter().map(|o| o.name()).filter(|name| self.is_lit(name)).collect();
        let rotation = self
            .outputs
            .iter()
            .map(|o| (o.name(), Rotation::from_transform(o.current_transform()).degrees()))
            .collect();
        let windows = self
            .space
            .elements()
            .filter(|w| !self.is_accessory(w))
            .map(|w| {
                let (app_id, title) = app_id_and_title(w);
                WindowSummary {
                    id: window_data(w).id,
                    app_id,
                    title,
                    placement: window_data(w).placement.borrow().clone(),
                    minimized: self.is_minimized(w),
                }
            })
            .collect();
        Snapshot {
            pose: self.pose.describe(),
            lit,
            blanked: self.power.blanked(),
            hinge_gap: self.hinge_gap,
            rotation,
            active_output: self.active_output().map(|o| o.name()),
            keyboard_output: self.keyboard_output().map(|o| o.name()),
            osk_away: self.osk_put_away(),
            osk_output: if self.taskview.open { None } else { self.osk_output().map(|o| o.name()) },
            overview: self.taskview.open,
            overview_cards: self.overview_labels(),
            locked: self.lock.locked,
            windows,
            splits: self.split_summaries(),
            posture: self.posture.clone(),
            ambient: self.ambient.map(|k| k.name().to_owned()),
            fullscreen: self
                .outputs
                .iter()
                .filter(|o| crate::render::fullscreen_in_front(&self.space, o))
                .map(|o| o.name())
                .collect(),
            home: self.home_panels(),
            carrying: self.carry.drag.is_some(),
            proximity: self.proximity.panels(),
            ring: self.ring.ring().name().map(str::to_owned),
            capturing: self.capture.capturing(Instant::now()),
            idle_inhibit: self.inhibit.seen.clone(),
            laptop: self.laptop_summary(),
            keyboard: self.keyboard_summary(),
            displays: self.displays_summary(),
        }
    }

    /// A window is gone (its toplevel destroyed, its X11 window unmapped,
    /// crate::xwayland): out of the space, the task list, the splits, the
    /// overview's and the carry's bookkeeping.
    pub fn window_gone(&mut self, window: &Window, what: &str) {
        info!(id = window_data(window).id, what, "toplevel gone");
        // A window that went at once was not the one launched: the
        // launcher's hint is for the next (crate::front).
        self.give_hint_back(window);
        // Waydroid makes its windows again after a resize of its
        // display: what this one was is kept for its successor.
        self.android_window_gone(window);
        if let Some(handle) = window_data(window).foreign.borrow_mut().take() {
            self.foreign_toplevels.remove_toplevel(&handle);
        }
        let id = window_data(window).id;
        self.carry.anims.remove(&id);
        self.taskview.closing.remove(&id);
        self.desk.forget(id);
        self.ext.homes.remove(&id);
        if self.carry.drag.as_ref().map(|d| d.window == *window) == Some(true) {
            self.carry.drag = None;
        }
        self.space.unmap_elem(window);
        // Its partner fills the panel again.
        let replace = self.forget_split_of(id);
        self.after_destroyed(what, replace);
    }

    /// A window the launcher's hint placed is gone: if it went at once (a
    /// splash, Waydroid's first toplevel), the hint waits for the next one
    /// again, for the rest of its time (crate::front::gives_hint_back).
    fn give_hint_back(&mut self, window: &Window) {
        let data = window_data(window);
        let Some(hint) = data.hint.borrow_mut().take() else {
            return;
        };
        let lived = data.mapped_at.elapsed();
        let fresh = Instant::now() < hint.until;
        if crate::front::gives_hint_back(lived, fresh, self.launch_hint.is_some()) {
            info!(
                id = data.id,
                output = hint.output,
                lived_ms = lived.as_millis() as u64,
                "a window that went at once gives the launcher's hint back"
            );
            self.launch_hint = Some(hint);
        }
    }

    fn find_toplevel(&self, surface: &ToplevelSurface) -> Option<Window> {
        self.space.elements().find(|window| window.toplevel().map(|t| t == surface).unwrap_or(false)).cloned()
    }
}

/// Whether an overlay-layer surface on `output` takes input at `local` (the
/// panel's frame): its input region, not its rectangle, counts, so a
/// see-through surface (the shell's overview labels) lets touches through.
pub fn overlay_takes_input(output: &Output, local: Point<f64, Logical>) -> bool {
    let map = layer_map_for_output(output);
    let takes = map.layers_on(Layer::Overlay).rev().any(|layer| {
        if AMBIENT_NAMESPACES.contains(&layer.namespace()) {
            return false;
        }
        map.layer_geometry(layer)
            .map(|g| layer.surface_under(local - g.loc.to_f64(), WindowSurfaceType::ALL).is_some())
            .unwrap_or(false)
    });
    takes
}

/// Tell a surface the scale it should draw at.
pub fn tell_scale(surface: &WlSurface, states: &smithay::wayland::compositor::SurfaceData, scale: f64) {
    send_surface_state(surface, states, scale.ceil() as i32, Transform::Normal);
    with_fractional_scale(states, |fractional| fractional.set_preferred_scale(scale));
}

/// Tell ext-foreign-toplevel-list a window's new title or app id.
pub(crate) fn update_foreign_toplevel(window: &Window) {
    let Some(data) = window.user_data().get::<WindowData>() else {
        return;
    };
    let foreign = data.foreign.borrow();
    let Some(handle) = foreign.as_ref() else {
        return;
    };
    let (app_id, title) = app_id_and_title(window);
    let (app_id, title) = (app_id.unwrap_or_default(), title.unwrap_or_default());
    let mut changed = false;
    if handle.title() != title {
        handle.send_title(&title);
        changed = true;
    }
    if handle.app_id() != app_id {
        handle.send_app_id(&app_id);
        changed = true;
    }
    if changed {
        handle.send_done();
    }
}

/// The name of a client's window, for logs and the control socket.
pub fn app_id_and_title(window: &Window) -> (Option<String>, Option<String>) {
    if let Some(x11) = window.x11_surface() {
        // WM_CLASS's class and the title (crate::xwayland).
        let title = x11.title();
        return (
            crate::xwayland::app_id(&x11.class(), &x11.instance()),
            (!title.is_empty()).then_some(title),
        );
    }
    let Some(toplevel) = window.toplevel() else {
        return (None, None);
    };
    with_states(toplevel.wl_surface(), |states| {
        states
            .data_map
            .get::<smithay::wayland::shell::xdg::XdgToplevelSurfaceData>()
            .map(|data| {
                let data = data.lock().unwrap();
                (data.app_id.clone(), data.title.clone())
            })
            .unwrap_or((None, None))
    })
}

/// A rectangle as the pure modules take it.
fn rect_tuple(r: Rectangle<i32, Logical>) -> crate::layout::Rect {
    (r.loc.x, r.loc.y, r.size.w, r.size.h)
}

/// A window's maximum size as its client last committed it (0 = none).
fn max_size(window: &Window) -> (i32, i32) {
    if let Some(x11) = window.x11_surface() {
        return x11.max_size().map(|s| (s.w.max(0), s.h.max(0))).unwrap_or((0, 0));
    }
    let Some(toplevel) = window.toplevel() else {
        return (0, 0);
    };
    with_states(toplevel.wl_surface(), |states| {
        let size =
            states.cached_state.get::<smithay::wayland::shell::xdg::SurfaceCachedState>().current().max_size;
        (size.w.max(0), size.h.max(0))
    })
}

/// Whether a window has drawn at the size last asked of it (it acked and
/// committed the latest configure's size): one still on its way to a new
/// size is not judged by its old one.
pub(crate) fn drew_as_asked(window: &Window) -> bool {
    let Some(toplevel) = window.toplevel() else {
        return true;
    };
    let pending = toplevel.with_pending_state(|s| s.size);
    pending.is_none() || toplevel.with_committed_state(|s| s.and_then(|s| s.size)) == pending
}

/// Suspend the phone through logind (`power_key_suspend = on`). On a thread, so
/// the compositor does not wait for the system to come back.
fn suspend() {
    info!("suspending (power_key_suspend = on)");
    let spawned = std::thread::Builder::new().name("suspend".to_owned()).spawn(|| {
        match std::process::Command::new("systemctl").arg("suspend").status() {
            Ok(status) if status.success() => info!("suspend requested"),
            Ok(status) => warn!(?status, "systemctl suspend failed"),
            Err(err) => warn!(?err, "could not run systemctl suspend"),
        }
    });
    if let Err(err) = spawned {
        warn!(?err, "could not start the suspend thread");
    }
}
