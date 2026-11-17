//! X11 apps through Xwayland (2026-11-16).
//!
//! Krita (5.2, Qt 5) forces Qt's xcb platform on Linux and would not start
//! without an X server; other X11-only programs (xterm, xeyes, Java, Wine)
//! are the same. Smithay's Xwayland support does the hard part: it spawns
//! the server ([`XWayland`]) and is its window manager ([`X11Wm`]); this
//! module puts the X11 windows into duo-wc's own window model.
//!
//! **Lazy start.** duo-wc takes an X display of its own at start
//! (`/tmp/.X<N>-lock`, the sockets `/tmp/.X11-unix/X<N>` and its abstract
//! twin), exports `DISPLAY=:N` to the session, and only listens. The first
//! X11 client that connects starts Xwayland: the listening sockets are handed
//! to it (`-listenfd`), so that client is served by Xwayland as if it had
//! always been there (it waits in the socket's backlog meanwhile, ~0.5 s).
//! Smithay's `XWayland::spawn` makes sockets of its own (for another display
//! number nobody uses) and takes no extra arguments, so it starts
//! `Xwayland` through a wrapper on its PATH
//! (`/usr/local/lib/duo-wc/xwayland/Xwayland`, `dist/xwayland-wrapper`) that
//! appends duo-wc's sockets. When there has been no X11 window for
//! [`IDLE_STOP`] and no X11 client but the compositor's own is connected
//! (X-Resource, [`others`]), Xwayland is stopped and the display listens
//! again. `xwayland = on` in duo-wc.conf starts it with the session (no
//! wrapper needed then either way: the sockets are handed over the same
//! way), `off` never.
//!
//! **Scale.** X11 has no per-window scale. Xwayland runs at the panels'
//! own pixels: Smithay's client scale for the Xwayland client is the output
//! scale (2.5), so an X11 window configured to a panel's 538 x 757 logical
//! area is 1345 x 1893 X pixels and its buffer is shown 1:1 (no blur; the
//! one pixel of rounding is what a fractional-scale Wayland client has too).
//! The toolkits are told the density the usual X11 ways, before any client
//! can read it: XSETTINGS `Xft/DPI` = 96 x scale (Qt 5 reads it at start,
//! GTK too, with `Gdk/WindowScalingFactor` 2 and the rest of the factor in
//! `Gdk/UnscaledDPI`) and the root window's `RESOURCE_MANAGER` with
//! `Xft.dpi: 240` (Xlib / Xft programs: xterm's fonts) and `Xcursor.size`.
//! Krita turns Xft/DPI 240 into a device pixel ratio of 2 (its own
//! RoundPreferFloor policy; 2.5 with its fractional scaling switched on).
//! Input needs nothing: Smithay maps pointer, touch and tablet coordinates
//! into the client's space by the same factor.
//!
//! **Windows.** A managed X11 window is a [`Window`] in the space like an
//! xdg toplevel: the same placement rules (one panel, span, the desk,
//! fullscreen), WindowData, the task list (`app_id` = WM_CLASS class, the
//! title), focus, close (WM_DELETE_WINDOW), force close (the X client's
//! pid from X-Resource, never Xwayland's). A transient window, a dialog,
//! utility or splash window is a dialog ([`is_dialog_type`]): on its
//! parent's panel, centred, at the size it asked for within the room
//! ([`dialog_size`]). Where duo-wc puts a window is where X11 is told it is
//! (`configure` with its global position): popups an app places in root
//! coordinates then land next to it. X11 windows are never scaled down to
//! fit a panel (their popups could not follow); a bigger one is cut at the
//! panel's edge.
//!
//! **Override-redirect windows** (menus, tooltips, drop-downs) are not
//! windows of the model: they are kept in [`Xwl::overlays`], drawn and
//! touched in front of the windows and the status bar, behind the overlay
//! layer, at the place the app gave them. Qt and GTK keep a menu inside the
//! RandR output (= a panel) it opens on, so one never crosses the hinge.
//!
//! **Focus.** duo-wc's seat focuses `wl_surface`s; X11 also wants its own
//! input focus (SetInputFocus / WM_TAKE_FOCUS by the window's input model).
//! A helper X11 connection of the compositor's ([`Helper`], made in a
//! thread, never blocking the loop) does that whenever the keyboard focus
//! lands on an X11 window, and set `RESOURCE_MANAGER` first.
//!
//! **Clipboard** both ways (Smithay's selection bridge: an X11 copy is the
//! seat's selection, a Wayland copy is offered to X11). No drag and drop
//! (duo-wc has none between Wayland clients either), no primary selection.
//!
//! **Input.** Touch reaches Xwayland as wl_touch (XI 2.2 touch events, or
//! pointer emulation for apps without them); the pen as zwp_tablet_v2, which
//! Xwayland turns into XInput 2 tablet devices ("xwayland-tablet stylus",
//! pressure and tilt valuators: Qt's tablet events, Krita's pressure). Keys
//! as wl_keyboard, with the keymap the seat has (the on-screen keyboard's
//! virtual keys included, crate::keymapsync); X11 apps speak no
//! text-input-v3, so the on-screen keyboard does not come by itself: the
//! bar's keyboard button brings it.

use std::cell::RefCell;
use std::collections::HashSet;
use std::os::fd::{AsFd, AsRawFd, OwnedFd, RawFd};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::time::{Duration, Instant};

use smithay::desktop::Window;
use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::{Interest, Mode as LoopMode, PostAction, RegistrationToken};
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::{Client, Resource as _};
use smithay::reexports::x11rb;
use smithay::utils::{Logical, Point, Rectangle, Size};
use smithay::wayland::compositor::CompositorHandler as _;
use smithay::wayland::selection::data_device::{
    clear_data_device_selection, current_data_device_selection_userdata,
    request_data_device_client_selection, set_data_device_selection,
};
use smithay::wayland::selection::SelectionTarget;
use smithay::wayland::xwayland_shell::{XWaylandShellHandler, XWaylandShellState};
use smithay::xwayland::xwm::{
    Reorder, ResizeEdge, WmInputModel, WmWindowProperty, WmWindowType, X11Window, XwmId,
};
use smithay::xwayland::{X11Surface, X11Wm, XWayland, XWaylandClientData, XWaylandEvent, XwmHandler};
use tracing::{debug, info, warn};
use x11rb::connection::Connection as _;
use x11rb::protocol::xproto::ConnectionExt as _;

use crate::state::{window_data, DuoWc, Placement};

/// Where duo-wc's `Xwayland` wrapper is: put first on the PATH Smithay
/// starts `Xwayland` with, it hands duo-wc's sockets on (`-listenfd`).
pub const WRAPPER_DIR: &str = "/usr/local/lib/duo-wc/xwayland";

/// The real server.
pub const XWAYLAND_BIN: &str = "/usr/bin/Xwayland";

/// The variable the wrapper reads the sockets' descriptors from.
pub const LISTENFD_ENV: &str = "DUO_XWAYLAND_LISTENFD";

/// How long there is no X11 window before Xwayland is stopped (lazy mode).
pub const IDLE_STOP: Duration = Duration::from_secs(60);

/// After Xwayland failed to start, how long the display waits before it
/// listens again (a client in the backlog would start it again at once).
pub const RETRY_AFTER: Duration = Duration::from_secs(5);

/// What X11 apps are started with, next to DISPLAY (duo-wc's environment,
/// which the session and its apps inherit; session.env too).
///
/// `QT_XCB_TABLET_LEGACY_COORDINATES=1`: Qt 5's xcb plugin takes a pen's
/// position from the XInput valuators (the whole X screen) and turns it
/// into window coordinates with the window position it CACHED when the
/// app last asked for one (`QPlatformWindow::geometry`; a window manager's
/// ConfigureNotify, synthetic or real, does not update that cache). duo-wc
/// places every window below the status bar, wherever the app asked, so
/// Krita got the pen 120 X pixels (the bar's 48 logical px) lower than the
/// tip: its tools were chosen a button and a half too low, and strokes
/// went astray (2026-11-16, a Surface Pen; the X server's own
/// event positions were right to 2 px). With the variable Qt uses the
/// server's event coordinates. Touch and the mouse always did. Only Qt's
/// xcb plugin reads it: Wayland Qt apps are not affected.
pub const X11_APP_ENV: &[(&str, &str)] = &[("QT_XCB_TABLET_LEGACY_COORDINATES", "1")];

/// How many stopped instances' window managers are kept.
const RETIRED_KEPT: usize = 4;

/// The display numbers tried for duo-wc's own display.
const DISPLAYS: std::ops::Range<u32> = 0..32;

/// When Xwayland runs (`xwayland` in duo-wc.conf).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum XMode {
    /// When the first X11 client connects; stopped again when idle.
    #[default]
    Lazy,
    /// From the start, for good.
    On,
    /// Never: no DISPLAY.
    Off,
}

impl XMode {
    /// `lazy`, `on` or `off`.
    pub fn parse(value: &str) -> Option<XMode> {
        match value.trim().to_ascii_lowercase().as_str() {
            "lazy" | "auto" => Some(XMode::Lazy),
            "on" | "always" => Some(XMode::On),
            "off" | "never" => Some(XMode::Off),
            _ => None,
        }
    }

    /// The name.
    pub fn name(self) -> &'static str {
        match self {
            XMode::Lazy => "lazy",
            XMode::On => "on",
            XMode::Off => "off",
        }
    }
}

/// An override-redirect window to draw (a menu, a tooltip): its surface and
/// where X11 has it (global, logical).
#[derive(Debug, Clone)]
pub struct Overlay {
    /// Its surface.
    pub surface: WlSurface,
    /// Where it is.
    pub rect: Rectangle<i32, Logical>,
}

/// The size an X11 window asked for (at its map, by ConfigureRequest):
/// what a dialog is given, within its room.
#[derive(Debug, Default)]
struct Wants(RefCell<Option<(i32, i32)>>);

/// An X11 window's process, as the X server knows it (X-Resource, asked
/// once when it is mapped: not on every look).
#[derive(Debug)]
struct ClientPid(Option<u32>);

/// Xwayland and its windows.
#[derive(Default)]
pub struct Xwl {
    /// When it runs.
    pub mode: XMode,
    /// duo-wc's X display (DISPLAY=:N), while it has one.
    pub display: Option<u32>,
    /// Its listening sockets (the file and the abstract one).
    listeners: Vec<UnixListener>,
    /// The loop sources waiting for a first client on them.
    watch: Vec<RegistrationToken>,
    /// The XWayland source (removing it ends Xwayland).
    source: Option<RegistrationToken>,
    /// The Xwayland Wayland client.
    pub client: Option<Client>,
    /// The window manager, once Xwayland is up.
    pub xwm: Option<X11Wm>,
    /// Window managers of stopped instances: their X11 source may still
    /// deliver queued events, and Smithay asks for the manager by id then
    /// (a stopped instance's events are ignored). The last
    /// [`RETIRED_KEPT`] of them (the most recent is always kept:
    /// `xwm_state` must return one).
    retired: Vec<X11Wm>,
    /// Override-redirect windows mapped now, bottom to top.
    pub overlays: Vec<X11Surface>,
    /// X11 windows (top level) that exist now: none for a while = idle.
    windows: HashSet<X11Window>,
    /// The compositor's own X11 connection (focus, resources).
    helper: Option<Helper>,
    /// The X11 window the keyboard focus is on (sent once the helper is up).
    focus: Option<(X11Window, WmInputModel)>,
    /// When the running instance was started.
    started: Option<Instant>,
    /// The idle timer, while it runs.
    idle_timer: Option<RegistrationToken>,
    /// The X11 stacking order last sent (bottom to top).
    stacking: Vec<X11Window>,
    /// Instances started (a helper of an older one is dropped).
    pub generation: u32,
    /// Xwayland's process id (duo-wc's child named Xwayland: the Wayland
    /// client's credentials are duo-wc's own, it made the socket pair).
    pub pid: Option<i32>,
}

impl Drop for Xwl {
    fn drop(&mut self) {
        if let Some(n) = self.display {
            let _ = std::fs::remove_file(socket_path(n));
            let _ = std::fs::remove_file(lock_path(n));
        }
    }
}

/// `/tmp/.X<N>-lock`.
pub fn lock_path(n: u32) -> String {
    format!("/tmp/.X{n}-lock")
}

/// `/tmp/.X11-unix/X<N>` (the abstract socket has the same name).
pub fn socket_path(n: u32) -> String {
    format!("/tmp/.X11-unix/X{n}")
}

/// What an X server writes into its lock file: its pid, 10 wide.
pub fn lock_contents(pid: u32) -> String {
    format!("{pid:>10}\n")
}

/// The pid in a lock file's contents.
pub fn lock_pid(contents: &str) -> Option<i32> {
    contents.trim().parse::<i32>().ok().filter(|pid| *pid > 0)
}

/// The wrapper's variable: the descriptors, space-separated.
pub fn listenfd_value(fds: &[RawFd]) -> String {
    fds.iter().map(|fd| fd.to_string()).collect::<Vec<_>>().join(" ")
}

/// The DPI X11 toolkits are told for an output scale.
pub fn dpi(scale: f64) -> i32 {
    (96.0 * scale).round() as i32
}

/// GTK's integer window scale under X11 for an output scale (the rest of
/// the factor goes into its unscaled DPI).
pub fn gdk_window_scale(scale: f64) -> i32 {
    (scale.floor() as i32).max(1)
}

/// The root window's `RESOURCE_MANAGER` (what xrdb would load): the DPI
/// for Xlib / Xft programs, greyscale antialiasing (the panels are OLED),
/// the cursor's size.
pub fn resources(scale: f64) -> String {
    format!(
        "Xft.dpi:\t{}\nXft.antialias:\t1\nXft.hinting:\t1\nXft.hintstyle:\thintslight\nXft.rgba:\tnone\nXcursor.size:\t{}\n",
        dpi(scale),
        (24.0 * scale).round() as i32
    )
}

/// The XSETTINGS (integers and strings) for an output scale.
pub fn xsettings(scale: f64) -> Vec<(&'static str, XValue)> {
    let gdk = gdk_window_scale(scale);
    vec![
        ("Xft/DPI", XValue::Int(dpi(scale) * 1024)),
        ("Xft/Antialias", XValue::Int(1)),
        ("Xft/Hinting", XValue::Int(1)),
        ("Xft/HintStyle", XValue::Str("hintslight")),
        ("Xft/RGBA", XValue::Str("none")),
        ("Gdk/WindowScalingFactor", XValue::Int(gdk)),
        ("Gdk/UnscaledDPI", XValue::Int(((96.0 * scale / gdk as f64) * 1024.0).round() as i32)),
        ("Xcursor/Size", XValue::Int((24.0 * scale).round() as i32)),
    ]
}

/// An XSETTINGS value (Smithay's Value is neither Clone nor PartialEq).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum XValue {
    /// An integer.
    Int(i32),
    /// A string.
    Str(&'static str),
}

/// An X11 window's app id for the task list: its WM_CLASS class (Krita's is
/// `krita`, xterm's `XTerm`), else the instance.
pub fn app_id(class: &str, instance: &str) -> Option<String> {
    let pick = if !class.trim().is_empty() { class } else { instance };
    (!pick.trim().is_empty()).then(|| pick.trim().to_owned())
}

/// Whether an X11 window is a dialog to duo-wc's rules (crate::dialog): it
/// is transient for another, or a dialog, utility (Krita's floating
/// dockers), toolbar or splash window.
pub fn is_dialog_type(transient: bool, ty: Option<WmWindowType>) -> bool {
    transient
        || matches!(
            ty,
            Some(WmWindowType::Dialog | WmWindowType::Utility | WmWindowType::Splash | WmWindowType::Toolbar)
        )
}

/// A dialog's size: what it asked for, within the room (and its max size);
/// the room when it asked for nothing.
pub fn dialog_size(wants: Option<(i32, i32)>, max: (i32, i32), room: (i32, i32)) -> (i32, i32) {
    let (w, h) = match wants {
        Some((w, h)) if w > 0 && h > 0 => (w, h),
        _ => room,
    };
    let cap = |v: i32, max: i32, room: i32| {
        let v = if max > 0 { v.min(max) } else { v };
        v.min(room).max(1)
    };
    (cap(w, max.0, room.0), cap(h, max.1, room.1))
}

/// The X clients that are not the compositor's: X-Resource's client bases
/// less the server's (0), the window manager's and the helpers'.
pub fn others(bases: &[u32], ours: &[u32]) -> usize {
    bases.iter().filter(|b| **b != 0 && !ours.contains(b)).count()
}

/// Take the X display number `n`: its lock file, with our pid (a lock of a
/// process that is gone is taken over). Ok(false): someone else has it.
fn grab_lock(n: u32) -> std::io::Result<bool> {
    use std::io::Write as _;
    let path = lock_path(n);
    for _ in 0..2 {
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut file) => {
                file.write_all(lock_contents(std::process::id()).as_bytes())?;
                return Ok(true);
            }
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                let pid = std::fs::read_to_string(&path).ok().as_deref().and_then(lock_pid);
                let gone = match pid {
                    Some(pid) => {
                        // SAFETY: kill(2) with signal 0 only checks the pid.
                        let alive = unsafe { libc::kill(pid, 0) } == 0;
                        !alive && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
                    }
                    None => false,
                };
                if !gone {
                    return Ok(false);
                }
                info!(display = n, ?pid, "X display lock of a process that is gone: taken over");
                std::fs::remove_file(&path)?;
            }
            Err(err) => return Err(err),
        }
    }
    Ok(false)
}

/// The two listening sockets of display `n`.
fn bind_display(n: u32) -> std::io::Result<Vec<UnixListener>> {
    use std::os::linux::net::SocketAddrExt as _;
    let path = socket_path(n);
    let _ = std::fs::remove_file(&path);
    let file = UnixListener::bind(&path)?;
    let addr = std::os::unix::net::SocketAddr::from_abstract_name(path.as_bytes())?;
    let abstract_socket = UnixListener::bind_addr(&addr)?;
    for l in [&file, &abstract_socket] {
        l.set_nonblocking(true)?;
    }
    Ok(vec![file, abstract_socket])
}

/// The first free display: its number and sockets.
fn open_display() -> std::io::Result<(u32, Vec<UnixListener>)> {
    for n in DISPLAYS {
        match grab_lock(n) {
            Ok(true) => {}
            Ok(false) => continue,
            Err(err) => {
                debug!(display = n, ?err, "X display lock");
                continue;
            }
        }
        match bind_display(n) {
            Ok(listeners) => return Ok((n, listeners)),
            Err(err) => {
                warn!(display = n, ?err, "could not open the X display's sockets");
                let _ = std::fs::remove_file(lock_path(n));
            }
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AddrInUse, "no free X display"))
}

/// Set or clear FD_CLOEXEC.
fn set_cloexec(fd: RawFd, on: bool) {
    // SAFETY: fcntl on a descriptor we own (a listener held in Xwl).
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFD);
        if flags >= 0 {
            let flags = if on { flags | libc::FD_CLOEXEC } else { flags & !libc::FD_CLOEXEC };
            libc::fcntl(fd, libc::F_SETFD, flags);
        }
    }
}

/// The compositor's own X11 connection: sets the root window's resources
/// before the clients read them, gives X11 windows the X input focus.
pub struct Helper {
    conn: x11rb::rust_connection::RustConnection,
    wm_protocols: u32,
    wm_take_focus: u32,
    generation: u32,
}

impl Helper {
    /// Connect to display `display`, set `RESOURCE_MANAGER`, learn the atoms
    /// (in a thread: it waits for Xwayland).
    fn connect(display: u32, resources: &str, generation: u32) -> anyhow::Result<Helper> {
        use x11rb::wrapper::ConnectionExt as _;
        let (conn, screen) = x11rb::rust_connection::RustConnection::connect(Some(&format!(":{display}")))?;
        let root = conn.setup().roots[screen].root;
        conn.change_property8(
            x11rb::protocol::xproto::PropMode::REPLACE,
            root,
            x11rb::protocol::xproto::AtomEnum::RESOURCE_MANAGER,
            x11rb::protocol::xproto::AtomEnum::STRING,
            resources.as_bytes(),
        )?;
        conn.flush()?;
        let protocols = conn.intern_atom(false, b"WM_PROTOCOLS")?;
        let take_focus = conn.intern_atom(false, b"WM_TAKE_FOCUS")?;
        let wm_protocols = protocols.reply()?.atom;
        let wm_take_focus = take_focus.reply()?.atom;
        // Who started it (the log: what in the session speaks X11).
        let own = conn.setup().resource_id_base;
        match x_clients(&conn, screen) {
            Ok(clients) => {
                let named: Vec<String> = clients
                    .iter()
                    .filter(|(base, _)| *base != own)
                    .map(|(_, pid)| describe_pid(*pid))
                    .collect();
                info!(clients = ?named, "X11 clients when Xwayland came up");
            }
            Err(err) => debug!(?err, "X11 client list"),
        }
        Ok(Helper { conn, wm_protocols, wm_take_focus, generation })
    }

    /// The X input focus to `window` (by its input model), or to none.
    fn focus(&self, target: Option<(X11Window, WmInputModel)>) {
        use x11rb::protocol::xproto::{ClientMessageEvent, EventMask, InputFocus};
        let result = (|| -> Result<(), x11rb::errors::ConnectionError> {
            match target {
                None => {
                    self.conn.set_input_focus(InputFocus::NONE, x11rb::NONE, x11rb::CURRENT_TIME)?;
                }
                Some((window, model)) => {
                    let (set, take) = match model {
                        WmInputModel::None => (false, false),
                        WmInputModel::Passive => (true, false),
                        WmInputModel::LocallyActive => (true, true),
                        WmInputModel::GloballyActive => (false, true),
                    };
                    if set {
                        self.conn.set_input_focus(InputFocus::NONE, window, x11rb::CURRENT_TIME)?;
                    }
                    if take {
                        let event = ClientMessageEvent::new(
                            32,
                            window,
                            self.wm_protocols,
                            [self.wm_take_focus, x11rb::CURRENT_TIME, 0, 0, 0],
                        );
                        self.conn.send_event(false, window, EventMask::NO_EVENT, event)?;
                    }
                }
            }
            self.conn.flush()
        })();
        if let Err(err) = result {
            debug!(?err, "X11 focus");
        }
        // Errors of these requests (a window gone meanwhile) come back as
        // events: dropped.
        while let Ok(Some(_)) = self.conn.poll_for_event() {}
    }
}

/// The X clients but the server and the window manager: each one's resource
/// base and process (X-Resource).
fn x_clients(
    conn: &x11rb::rust_connection::RustConnection,
    screen: usize,
) -> anyhow::Result<Vec<(u32, Option<u32>)>> {
    use x11rb::protocol::res::{ClientIdMask, ClientIdSpec, ConnectionExt as _};
    let setup = conn.setup();
    let mask = setup.resource_id_mask;
    let root = setup.roots[screen].root;
    let check = conn.intern_atom(false, b"_NET_SUPPORTING_WM_CHECK")?.reply()?.atom;
    let wm = conn
        .get_property(false, root, check, x11rb::protocol::xproto::AtomEnum::WINDOW, 0, 1)?
        .reply()?
        .value32()
        .and_then(|mut v| v.next())
        .map(|w| w & !mask);
    let bases: Vec<u32> =
        conn.res_query_clients()?.reply()?.clients.iter().map(|c| c.resource_base).collect();
    let ids = conn
        .res_query_client_ids(&[ClientIdSpec { client: 0, mask: ClientIdMask::LOCAL_CLIENT_PID }])?
        .reply()?
        .ids;
    Ok(bases
        .into_iter()
        .filter(|b| *b != 0 && Some(*b) != wm)
        .map(|b| {
            let pid = ids.iter().find(|v| v.spec.client & !mask == b).and_then(|v| v.value.first().copied());
            (b, pid)
        })
        .collect())
}

/// A process for the log: `pid comm` (or `?`).
fn describe_pid(pid: Option<u32>) -> String {
    match pid {
        Some(pid) => {
            let comm = std::fs::read_to_string(format!("/proc/{pid}/comm")).unwrap_or_default();
            format!("{pid} {}", comm.trim())
        }
        None => "?".to_owned(),
    }
}

/// The X clients connected now that are not the compositor's (a thread's
/// own short connection: X-Resource's client list less the server, the
/// window manager (the owner of `_NET_SUPPORTING_WM_CHECK`'s window), the
/// helper and itself).
fn count_others(display: u32, helper_base: Option<u32>) -> anyhow::Result<usize> {
    let (conn, screen) = x11rb::rust_connection::RustConnection::connect(Some(&format!(":{display}")))?;
    let own = conn.setup().resource_id_base;
    let clients = x_clients(&conn, screen)?;
    let bases: Vec<u32> = clients.iter().map(|(b, _)| *b).collect();
    let mut ours = vec![own];
    ours.extend(helper_base);
    let n = others(&bases, &ours);
    if n > 0 {
        let named: Vec<String> =
            clients.iter().filter(|(b, _)| !ours.contains(b)).map(|(_, pid)| describe_pid(*pid)).collect();
        debug!(clients = ?named, "X11 clients without windows");
    }
    Ok(n)
}

impl DuoWc {
    /// At start (before the session): duo-wc's X display, DISPLAY for the
    /// session, and Xwayland at once if `xwayland = on`.
    pub fn xwl_init(&mut self, mode: XMode) {
        self.xwl.mode = mode;
        if mode == XMode::Off {
            info!("X11 apps: off (xwayland = off)");
            return;
        }
        if !Path::new(XWAYLAND_BIN).exists() {
            info!(bin = XWAYLAND_BIN, "X11 apps: no Xwayland installed (apt install xwayland)");
            self.xwl.mode = XMode::Off;
            return;
        }
        if !Path::new(WRAPPER_DIR).join("Xwayland").exists() {
            warn!(dir = WRAPPER_DIR, "X11 apps: duo-wc's Xwayland wrapper is missing (install.sh): off");
            self.xwl.mode = XMode::Off;
            return;
        }
        match open_display() {
            Ok((n, listeners)) => {
                self.xwl.display = Some(n);
                self.xwl.listeners = listeners;
                // SAFETY: the loop is not running yet and no thread reads
                // the environment now; the session inherits it.
                unsafe { std::env::set_var("DISPLAY", format!(":{n}")) };
                for (key, value) in X11_APP_ENV {
                    // SAFETY: as above.
                    unsafe { std::env::set_var(key, value) };
                }
                info!(display = n, mode = mode.name(), "X11 apps: DISPLAY=:{n}");
            }
            Err(err) => {
                warn!(?err, "X11 apps: no X display");
                self.xwl.mode = XMode::Off;
                return;
            }
        }
        match mode {
            XMode::On => self.xwl_start("xwayland = on"),
            _ => self.xwl_arm(),
        }
    }

    /// Listen for the first X11 client (lazy).
    fn xwl_arm(&mut self) {
        if !self.xwl.watch.is_empty() || self.xwl.source.is_some() {
            return;
        }
        let fds: Vec<OwnedFd> =
            self.xwl.listeners.iter().filter_map(|l| l.as_fd().try_clone_to_owned().ok()).collect();
        for fd in fds {
            let token = self.loop_handle.insert_source(
                Generic::new(fd, Interest::READ, LoopMode::Level),
                |_, _, state: &mut DuoWc| {
                    state.events_dirty = true;
                    // Started from an idle callback (still this turn of the
                    // loop): this source goes with the others there.
                    // (Disabling it here made that removal warn: calloop
                    // unregistered it twice.)
                    state.loop_handle.insert_idle(|state| state.xwl_start("an X11 client connected"));
                    Ok(PostAction::Continue)
                },
            );
            match token {
                Ok(token) => self.xwl.watch.push(token),
                Err(err) => warn!(?err, "could not watch the X display"),
            }
        }
        debug!(display = ?self.xwl.display, "X display listening (Xwayland starts with its first client)");
    }

    /// Start Xwayland on duo-wc's display.
    pub fn xwl_start(&mut self, why: &str) {
        if self.xwl.source.is_some() || self.xwl.display.is_none() {
            return;
        }
        for token in std::mem::take(&mut self.xwl.watch) {
            self.loop_handle.remove(token);
        }
        let fds: Vec<RawFd> = self.xwl.listeners.iter().map(|l| l.as_raw_fd()).collect();
        let path = format!(
            "{WRAPPER_DIR}:{}",
            std::env::var("PATH").unwrap_or_else(|_| "/usr/local/bin:/usr/bin:/bin".to_owned())
        );
        // The listeners reach Xwayland through exec: no FD_CLOEXEC on them
        // for the spawn only (nothing else is spawned meanwhile: this is
        // the event loop's thread).
        for fd in &fds {
            set_cloexec(*fd, false);
        }
        let spawned = XWayland::spawn(
            &self.display_handle,
            None,
            [("PATH".to_owned(), path), (LISTENFD_ENV.to_owned(), listenfd_value(&fds))],
            true,
            std::process::Stdio::null(),
            std::process::Stdio::inherit(),
            |_| {},
        );
        for fd in &fds {
            set_cloexec(*fd, true);
        }
        let (xwayland, client) = match spawned {
            Ok(spawned) => spawned,
            Err(err) => {
                warn!(?err, "could not start Xwayland");
                self.xwl_retry_later();
                return;
            }
        };
        // X11 at the panels' own pixels (the module's docs, "Scale").
        self.client_compositor_state(&client).set_client_scale(self.scale);
        self.xwl.pid = xwayland_child();
        self.xwl.generation += 1;
        let generation = self.xwl.generation;
        let token = self.loop_handle.insert_source(xwayland, move |event, _, state: &mut DuoWc| {
            state.events_dirty = true;
            match event {
                XWaylandEvent::Ready { x11_socket, display_number } => {
                    state.xwl_ready(generation, x11_socket, display_number)
                }
                XWaylandEvent::Error => {
                    warn!("Xwayland exited while starting");
                    state.loop_handle.insert_idle(|state| {
                        state.xwl_stop("it failed to start");
                        state.xwl_retry_later();
                    });
                }
            }
        });
        match token {
            Ok(token) => {
                self.xwl.source = Some(token);
                self.xwl.client = Some(client);
                self.xwl.started = Some(Instant::now());
                info!(why, display = ?self.xwl.display, pid = ?self.xwl.pid, "starting Xwayland");
            }
            Err(err) => {
                warn!(?err, "could not watch Xwayland");
                self.xwl_retry_later();
            }
        }
    }

    /// Xwayland is up: be its window manager, tell the toolkits the scale,
    /// connect the helper.
    fn xwl_ready(&mut self, generation: u32, x11_socket: UnixStream, display_number: u32) {
        if generation != self.xwl.generation {
            return;
        }
        let Some(client) = self.xwl.client.clone() else {
            return;
        };
        // The helper first: it waits in the backlog of Xwayland's own display,
        // whose sockets come before duo-wc's in Xwayland's list, so it is
        // served before the app that started Xwayland, and RESOURCE_MANAGER
        // is set before that app reads it (Xlib does at XOpenDisplay).
        self.xwl_spawn_helper(display_number, generation);
        match X11Wm::start_wm(self.loop_handle.clone(), &self.display_handle, x11_socket, client) {
            Ok(mut wm) => {
                let settings = xsettings(self.scale).into_iter().map(|(name, value)| {
                    let value = match value {
                        XValue::Int(i) => smithay::xwayland::xwm::settings::Value::Integer(i),
                        XValue::Str(s) => smithay::xwayland::xwm::settings::Value::String(s.to_owned()),
                    };
                    (name.to_owned(), value)
                });
                if let Err(err) = wm.set_xsettings(settings) {
                    warn!(?err, "XSETTINGS");
                }
                self.xwl.xwm = Some(wm);
                if self.xwl.pid.is_none() {
                    self.xwl.pid = xwayland_child();
                }
                let ms = self.xwl.started.map(|t| t.elapsed().as_millis()).unwrap_or(0);
                info!(display = ?self.xwl.display, internal = display_number, ms, pid = ?self.xwl.pid, "Xwayland is up");
                // A client without windows (xdpyinfo) does not keep it.
                self.xwl_note_idle();
            }
            Err(err) => {
                warn!(?err, "the X11 window manager did not start");
                // Its client goes now: a commit of it would ask for the
                // window manager there is not.
                if let Some(client) = self.xwl.client.take() {
                    self.display_handle.backend_handle().kill_client(
                        client.id(),
                        smithay::reexports::wayland_server::backend::DisconnectReason::ConnectionClosed,
                    );
                }
                self.loop_handle.insert_idle(|state| {
                    state.xwl_stop("no window manager");
                    state.xwl_retry_later();
                });
            }
        }
    }

    fn xwl_spawn_helper(&mut self, display_number: u32, generation: u32) {
        let (sender, channel) = smithay::reexports::calloop::channel::channel::<Helper>();
        let inserted = self.loop_handle.insert_source(channel, |event, _, state: &mut DuoWc| {
            if let smithay::reexports::calloop::channel::Event::Msg(helper) = event {
                if helper.generation != state.xwl.generation || state.xwl.xwm.is_none() {
                    return;
                }
                helper.focus(state.xwl.focus);
                state.xwl.helper = Some(helper);
                debug!("X11 helper connection up");
            }
        });
        if let Err(err) = inserted {
            warn!(?err, "could not watch the X11 helper");
            return;
        }
        let resources = resources(self.scale);
        let spawned = std::thread::Builder::new().name("x11-helper".to_owned()).spawn(move || {
            match Helper::connect(display_number, &resources, generation) {
                Ok(helper) => {
                    let _ = sender.send(helper);
                }
                Err(err) => warn!(?err, "the X11 helper connection failed"),
            }
        });
        if let Err(err) = spawned {
            warn!(?err, "could not start the X11 helper thread");
        }
    }

    /// Xwayland is gone (stopped, crashed, failed): its windows go, the
    /// display listens again.
    pub fn xwl_stop(&mut self, why: &str) {
        let running = self.xwl.source.is_some();
        let x11_windows: Vec<Window> = self.space.elements().filter(|w| w.is_x11()).cloned().collect();
        for window in &x11_windows {
            self.window_gone(window, "x11");
        }
        self.xwl.overlays.clear();
        self.xwl.windows.clear();
        self.xwl.stacking.clear();
        self.xwl.focus = None;
        self.xwl.helper = None;
        if let Some(wm) = self.xwl.xwm.take() {
            // Its source may still deliver queued events: kept (a few; an
            // old one's source closed long ago).
            self.xwl.retired.push(wm);
            if self.xwl.retired.len() > RETIRED_KEPT {
                self.xwl.retired.remove(0);
            }
        }
        if let Some(token) = self.xwl.idle_timer.take() {
            self.loop_handle.remove(token);
        }
        // Removing the source drops XWayland: its client is killed.
        if let Some(token) = self.xwl.source.take() {
            self.loop_handle.remove(token);
        }
        self.xwl.client = None;
        self.xwl.pid = None;
        if running {
            let ran = self.xwl.started.take().map(|t| t.elapsed().as_secs()).unwrap_or(0);
            info!(why, ran_s = ran, windows = x11_windows.len(), "Xwayland stopped");
        }
        if self.xwl.mode == XMode::Lazy {
            self.xwl_arm();
        }
        self.queue_redraw_all();
    }

    /// Listen again after a while (a failed start).
    fn xwl_retry_later(&mut self) {
        // The client that started it would start it again at once: it is
        // turned away (its connect fails as with no server).
        for l in &self.xwl.listeners {
            while let Ok((stream, _)) = l.accept() {
                drop(stream);
            }
        }
        let _ =
            self.loop_handle.insert_source(Timer::from_duration(RETRY_AFTER), |_, _, state: &mut DuoWc| {
                if state.xwl.source.is_none() {
                    if state.xwl.mode == XMode::On {
                        state.xwl_start("retry");
                    } else {
                        state.xwl_arm();
                    }
                }
                TimeoutAction::Drop
            });
    }

    /// No X11 window now: Xwayland is stopped after [`IDLE_STOP`] unless a
    /// window comes or an X client is still connected (lazy mode).
    fn xwl_note_idle(&mut self) {
        if self.xwl.mode != XMode::Lazy || self.xwl.xwm.is_none() || !self.xwl.windows.is_empty() {
            if !self.xwl.windows.is_empty() {
                if let Some(token) = self.xwl.idle_timer.take() {
                    self.loop_handle.remove(token);
                }
            }
            return;
        }
        if self.xwl.idle_timer.is_some() {
            return;
        }
        let generation = self.xwl.generation;
        let token = self.loop_handle.insert_source(
            Timer::from_duration(IDLE_STOP),
            move |_, _, state: &mut DuoWc| {
                state.xwl.idle_timer = None;
                if generation == state.xwl.generation && state.xwl.windows.is_empty() {
                    state.xwl_idle_check(generation);
                }
                TimeoutAction::Drop
            },
        );
        if let Ok(token) = token {
            self.xwl.idle_timer = Some(token);
        }
    }

    /// Count the X clients in a thread; stop when only ours are left.
    fn xwl_idle_check(&mut self, generation: u32) {
        let Some(display) = self.xwl.display else {
            return;
        };
        let helper_base = self.xwl.helper.as_ref().map(|h| h.conn.setup().resource_id_base);
        let (sender, channel) = smithay::reexports::calloop::channel::channel::<usize>();
        let _ = self.loop_handle.insert_source(channel, move |event, _, state: &mut DuoWc| {
            if let smithay::reexports::calloop::channel::Event::Msg(others) = event {
                if generation != state.xwl.generation || !state.xwl.windows.is_empty() {
                    return;
                }
                if others == 0 {
                    state.xwl_stop("no X11 window or client for a minute");
                } else {
                    debug!(others, "X11 clients without windows: Xwayland stays");
                    state.xwl_note_idle();
                }
            }
        });
        let _ = std::thread::Builder::new().name("x11-idle".to_owned()).spawn(move || {
            match count_others(display, helper_base) {
                Ok(n) => {
                    let _ = sender.send(n);
                }
                Err(err) => warn!(?err, "could not count the X11 clients"),
            }
        });
    }

    /// Whether X11 events of `xwm` are the running instance's (a stopped
    /// one's queued events are ignored).
    fn xwl_current(&self, xwm: XwmId) -> bool {
        self.xwl.xwm.as_ref().is_some_and(|w| w.id() == xwm)
    }

    /// The X11 window of a managed window.
    pub fn x11_window_of(&self, x11: &X11Surface) -> Option<Window> {
        self.space.elements().find(|w| w.x11_surface() == Some(x11)).cloned()
    }

    /// The managed window an X11 window id belongs to (its own id or its
    /// frame's).
    fn x11_window_by_id(&self, id: X11Window) -> Option<Window> {
        self.space
            .elements()
            .find(|w| {
                w.x11_surface()
                    .map(|x| x.window_id() == id || x.mapped_window_id() == Some(id))
                    .unwrap_or(false)
            })
            .cloned()
    }

    /// A dialog's parent among the X11 windows (WM_TRANSIENT_FOR).
    pub fn x11_parent(&self, window: &Window) -> Option<Window> {
        let parent = window.x11_surface()?.is_transient_for()?;
        self.x11_window_by_id(parent)
    }

    /// Whether a managed X11 window is a dialog to the rules.
    pub fn x11_is_dialog(x11: &X11Surface) -> bool {
        is_dialog_type(x11.is_transient_for().is_some(), x11.window_type())
    }

    /// The size an X11 dialog is given in a room.
    pub fn x11_dialog_size(window: &Window, room: Size<i32, Logical>) -> Size<i32, Logical> {
        let Some(x11) = window.x11_surface() else {
            return room;
        };
        let wants = x11.user_data().get::<Wants>().and_then(|w| *w.0.borrow());
        let max = x11.max_size().map(|s| (s.w, s.h)).unwrap_or((0, 0));
        Size::from(dialog_size(wants, max, (room.w, room.h)))
    }

    /// Tell an X11 window where it is (its place in the space, global) and
    /// how big (`size`, else as it is), with its fullscreen and maximized
    /// states, if anything changed.
    pub fn x11_configure(
        &self,
        window: &Window,
        size: Option<Size<i32, Logical>>,
        states: Option<(bool, bool)>,
    ) {
        let Some(x11) = window.x11_surface() else {
            return;
        };
        let Some(loc) = self.space.element_location(window) else {
            return;
        };
        if let Some((fullscreen, maximized)) = states {
            if x11.is_fullscreen() != fullscreen {
                let _ = x11.set_fullscreen(fullscreen);
            }
            if x11.is_maximized() != maximized {
                let _ = x11.set_maximized(maximized);
            }
        }
        let size = size.unwrap_or_else(|| x11.geometry().size);
        let rect = Rectangle::new(loc, size);
        if x11.geometry() != rect {
            debug!(
                id = window_data(window).id,
                x = loc.x,
                y = loc.y,
                w = size.w,
                h = size.h,
                "X11 window configured"
            );
            if let Err(err) = x11.configure(rect) {
                debug!(?err, "X11 configure");
            }
        }
    }

    /// Ask an X11 window to close (WM_DELETE_WINDOW, or destroyed if it
    /// does not speak it).
    pub fn x11_close(window: &Window) -> bool {
        match window.x11_surface() {
            Some(x11) => {
                if let Err(err) = x11.close() {
                    warn!(?err, "X11 close");
                }
                true
            }
            None => false,
        }
    }

    /// The process of an X11 window's client (X-Resource, asked when it was
    /// mapped; _NET_WM_PID without it), never Xwayland's or duo-wc's.
    pub fn x11_pid(&self, window: &Window) -> Option<i32> {
        let x11 = window.x11_surface()?;
        let pid = x11
            .user_data()
            .get::<ClientPid>()
            .and_then(|p| p.0)
            .or_else(|| x11.pid())
            .and_then(|pid| i32::try_from(pid).ok())?;
        (pid > 1 && Some(pid) != self.xwl.pid && pid as u32 != std::process::id()).then_some(pid)
    }

    /// The override-redirect windows to draw, bottom to top.
    pub fn x11_overlays(&self) -> Vec<Overlay> {
        self.xwl
            .overlays
            .iter()
            .filter_map(|x| Some(Overlay { surface: x.wl_surface()?, rect: x.geometry() }))
            .collect()
    }

    /// The override-redirect surface under a point (global), with its
    /// origin.
    pub fn x11_overlay_under(&self, point: Point<f64, Logical>) -> Option<(WlSurface, Point<f64, Logical>)> {
        self.x11_overlays().into_iter().rev().find_map(|o| {
            if !o.rect.to_f64().contains(point) {
                return None;
            }
            smithay::desktop::utils::under_from_surface_tree(
                &o.surface,
                point - o.rect.loc.to_f64(),
                (0, 0),
                smithay::desktop::WindowSurfaceType::ALL,
            )
            .map(|(s, offset)| (s, (o.rect.loc + offset).to_f64()))
        })
    }

    /// After a turn of the loop: the X11 stacking order follows the space's.
    /// Xwayland hands a touch or a click to the X11 window that X11 has in
    /// front at that point, whichever surface it came on: a dialog stacked
    /// below its spanning parent in X11 got none (2026-11-16, Krita's New
    /// Document dialog). `update_stacking_order_upwards` puts each window
    /// ABOVE the one before it (Smithay's code; its doc says top to bottom):
    /// the space's own order, bottom to top.
    pub fn xwl_sync(&mut self) {
        if self.xwl.xwm.is_none() {
            return;
        }
        let order: Vec<X11Surface> = self.space.elements().filter_map(|w| w.x11_surface().cloned()).collect();
        let ids: Vec<X11Window> = order.iter().map(|x| x.window_id()).collect();
        if ids == self.xwl.stacking {
            return;
        }
        if let Some(xwm) = self.xwl.xwm.as_mut() {
            if let Err(err) = xwm.update_stacking_order_upwards(order.iter()) {
                debug!(?err, "X11 stacking");
            }
        }
        self.xwl.stacking = ids;
    }

    /// The keyboard focus moved: the X input focus follows onto an X11
    /// window, or leaves X11.
    pub fn xwl_focus_changed(&mut self, focused: Option<&WlSurface>) {
        if self.xwl.xwm.is_none() {
            return;
        }
        let target = focused.and_then(|surface| {
            self.space
                .elements()
                .filter_map(|w| w.x11_surface())
                .find(|x| x.wl_surface().as_ref() == Some(surface))
                .map(|x| (x.window_id(), x.input_model()))
        });
        if target == self.xwl.focus {
            return;
        }
        debug!(window = ?target.map(|t| t.0), "X11 input focus");
        self.xwl.focus = target;
        if let Some(helper) = &self.xwl.helper {
            helper.focus(target);
        }
    }

    /// Put a newly mapped X11 window into the model, like a new toplevel.
    fn x11_adopt(&mut self, x11: X11Surface) {
        if self.x11_window_of(&x11).is_some() {
            return;
        }
        self.overview_close("new window");
        let Some((output, hint)) = self.output_for_new_window() else {
            warn!("an X11 window arrived before any output; it will not be shown");
            return;
        };
        if let Err(err) = x11.set_mapped(true) {
            warn!(?err, "could not map an X11 window");
            return;
        }
        let geometry = x11.geometry();
        // Its process (a round trip, as Smithay's own map handling makes).
        let pid = x11.get_client_pid().ok().filter(|pid| *pid > 0);
        x11.user_data().insert_if_missing(|| ClientPid(pid));
        x11.user_data().insert_if_missing(Wants::default);
        if let Some(wants) = x11.user_data().get::<Wants>() {
            *wants.0.borrow_mut() = Some((geometry.size.w, geometry.size.h));
        }
        let window = Window::new_x11_window(x11.clone());
        let id = self.next_window_id;
        self.next_window_id += 1;
        let (placement, saved) = crate::pose::new_window(&self.pose, Placement::Output(output.name()));
        let loc = self.placement_rect(&placement, false).map(|r| r.loc).unwrap_or_default();
        let foreign = self.foreign_toplevels.new_toplevel::<DuoWc>(String::new(), String::new());
        info!(
            id,
            x11 = x11.window_id(),
            class = x11.class(),
            title = x11.title(),
            dialog = Self::x11_is_dialog(&x11),
            pid = ?pid,
            w = geometry.size.w,
            h = geometry.size.h,
            output = output.name(),
            "new X11 window"
        );
        window
            .user_data()
            .insert_if_missing(|| crate::state::WindowData::new(id, placement, saved, foreign, hint));
        self.space.map_element(window.clone(), loc, true);
        self.place(&window);
        if self.is_dialog(&window) {
            self.settle_dialog(&window);
        }
        crate::state::update_foreign_toplevel(&window);
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// What `get_state` says about Xwayland.
    pub fn xwl_summary(&self) -> serde_json::Value {
        serde_json::json!({
            "mode": self.xwl.mode.name(),
            "display": self.xwl.display.map(|n| format!(":{n}")),
            "running": self.xwl.xwm.is_some(),
            "starting": self.xwl.source.is_some() && self.xwl.xwm.is_none(),
            "pid": self.xwl.pid,
            "windows": self.space.elements().filter(|w| w.is_x11()).count(),
            "x11_windows": self.xwl.windows.len(),
            "overlays": self.xwl.overlays.len(),
            "helper": self.xwl.helper.is_some(),
            "starts": self.xwl.generation,
            "up_s": self.xwl.started.filter(|_| self.xwl.xwm.is_some()).map(|t| t.elapsed().as_secs()),
        })
    }
}

/// duo-wc's child process named Xwayland (the wrapper execs it), from /proc.
fn xwayland_child() -> Option<i32> {
    let me = std::process::id().to_string();
    std::fs::read_dir("/proc").ok()?.flatten().find_map(|entry| {
        let pid: i32 = entry.file_name().to_str()?.parse().ok()?;
        let stat = std::fs::read_to_string(entry.path().join("stat")).ok()?;
        let (comm, rest) = parse_stat(&stat)?;
        (comm == "Xwayland" && rest.split_whitespace().nth(1) == Some(me.as_str())).then_some(pid)
    })
}

/// A /proc/PID/stat line: the command name (in parentheses, may hold spaces)
/// and what follows it (state, ppid, ...).
pub fn parse_stat(stat: &str) -> Option<(&str, &str)> {
    let open = stat.find('(')?;
    let close = stat.rfind(')')?;
    Some((&stat[open + 1..close], stat.get(close + 1..)?))
}

/// Whether a surface is Xwayland's (its scale is the client scale's).
pub fn is_xwayland_surface(surface: &WlSurface) -> bool {
    surface.client().map(|c| c.get_data::<XWaylandClientData>().is_some()).unwrap_or(false)
}

impl XWaylandShellHandler for DuoWc {
    fn xwayland_shell_state(&mut self) -> &mut XWaylandShellState {
        &mut self.xwayland_shell_state
    }

    fn surface_associated(&mut self, xwm: XwmId, _wl_surface: WlSurface, surface: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.events_dirty = true;
        // A window mapped before its surface came can take the keyboard now.
        if self.x11_window_of(&surface).is_some() {
            self.refresh_keyboard_focus();
        }
        self.queue_redraw_all();
    }
}

impl XwmHandler for DuoWc {
    fn xwm_state(&mut self, xwm: XwmId) -> &mut X11Wm {
        // The running instance's, or a stopped one's (its X11 source may
        // still deliver queued events: 2026-11-16 the first idle stop
        // panicked here). There is always one once an instance started.
        let current = self.xwl.xwm.as_ref().is_some_and(|w| w.id() == xwm);
        if current {
            return self.xwl.xwm.as_mut().unwrap();
        }
        if let Some(i) = self.xwl.retired.iter().position(|w| w.id() == xwm) {
            return &mut self.xwl.retired[i];
        }
        match (&mut self.xwl.xwm, self.xwl.retired.last_mut()) {
            (Some(wm), _) => wm,
            (None, Some(wm)) => wm,
            (None, None) => unreachable!("X11 events come only after a window manager started"),
        }
    }

    fn new_window(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.xwl.windows.insert(window.window_id());
        self.xwl_note_idle();
    }

    fn new_override_redirect_window(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.xwl.windows.insert(window.window_id());
        self.xwl_note_idle();
    }

    fn map_window_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.events_dirty = true;
        self.x11_adopt(window);
    }

    fn mapped_override_redirect_window(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.events_dirty = true;
        if !self.xwl.overlays.contains(&window) {
            let g = window.geometry();
            debug!(
                x11 = window.window_id(),
                x = g.loc.x,
                y = g.loc.y,
                w = g.size.w,
                h = g.size.h,
                "X11 popup"
            );
            self.xwl.overlays.push(window);
        }
        self.queue_redraw_all();
    }

    fn unmapped_window(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.events_dirty = true;
        self.xwl.overlays.retain(|x| *x != window);
        if let Some(w) = self.x11_window_of(&window) {
            info!(id = window_data(&w).id, x11 = window.window_id(), "X11 window unmapped");
            self.window_gone(&w, "x11");
        }
        if !window.is_override_redirect() {
            let _ = window.set_mapped(false);
        }
        self.queue_redraw_all();
    }

    fn destroyed_window(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        self.events_dirty = true;
        self.xwl.windows.remove(&window.window_id());
        self.xwl.overlays.retain(|x| *x != window);
        if let Some(w) = self.x11_window_of(&window) {
            self.window_gone(&w, "x11");
        }
        self.xwl_note_idle();
        self.queue_redraw_all();
    }

    fn configure_request(
        &mut self,
        xwm: XwmId,
        window: X11Surface,
        x: Option<i32>,
        y: Option<i32>,
        w: Option<u32>,
        h: Option<u32>,
        _reorder: Option<Reorder>,
    ) {
        if !self.xwl_current(xwm) {
            return;
        }
        let managed = self.x11_window_of(&window);
        let mut geometry = window.geometry();
        if let Some(w) = w {
            geometry.size.w = w as i32;
        }
        if let Some(h) = h {
            geometry.size.h = h as i32;
        }
        window.user_data().insert_if_missing(Wants::default);
        if let Some(wants) = window.user_data().get::<Wants>() {
            *wants.0.borrow_mut() = Some((geometry.size.w, geometry.size.h));
        }
        match managed {
            // Not managed yet (before its map): as it asks.
            None => {
                if let Some(x) = x {
                    geometry.loc.x = x;
                }
                if let Some(y) = y {
                    geometry.loc.y = y;
                }
                let _ = window.configure(geometry);
            }
            // duo-wc decides: a dialog may take the size it asks for (within
            // its room); everything else keeps its place (Smithay answers
            // with a synthetic configure).
            Some(managed) => {
                if self.is_dialog(&managed) {
                    self.place(&managed);
                    self.queue_redraw_all();
                }
            }
        }
    }

    fn configure_notify(
        &mut self,
        xwm: XwmId,
        window: X11Surface,
        _geometry: Rectangle<i32, Logical>,
        _above: Option<u32>,
    ) {
        if !self.xwl_current(xwm) {
            return;
        }
        if window.is_override_redirect() {
            self.queue_redraw_all();
        }
    }

    fn property_notify(&mut self, xwm: XwmId, window: X11Surface, property: WmWindowProperty) {
        if !self.xwl_current(xwm) {
            return;
        }
        let Some(managed) = self.x11_window_of(&window) else {
            return;
        };
        self.events_dirty = true;
        match property {
            WmWindowProperty::Title | WmWindowProperty::Class => {
                crate::state::update_foreign_toplevel(&managed)
            }
            WmWindowProperty::TransientFor | WmWindowProperty::WindowType if self.is_dialog(&managed) => {
                self.settle_dialog(&managed);
            }
            WmWindowProperty::NormalHints if self.is_dialog(&managed) => self.place(&managed),
            _ => {}
        }
    }

    fn maximize_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        if let Some(managed) = self.x11_window_of(&window) {
            self.place(&managed);
        }
    }

    fn unmaximize_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        if let Some(managed) = self.x11_window_of(&window) {
            self.place(&managed);
        }
    }

    fn fullscreen_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        let Some(managed) = self.x11_window_of(&window) else {
            return;
        };
        info!(id = window_data(&managed).id, "X11 window full screen where it is");
        *window_data(&managed).fullscreen.borrow_mut() = true;
        self.place(&managed);
        self.queue_redraw_all();
    }

    fn unfullscreen_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        let Some(managed) = self.x11_window_of(&window) else {
            return;
        };
        info!(id = window_data(&managed).id, "X11 window out of full screen");
        *window_data(&managed).fullscreen.borrow_mut() = false;
        self.place(&managed);
        self.queue_redraw_all();
    }

    fn minimize_request(&mut self, xwm: XwmId, window: X11Surface) {
        if !self.xwl_current(xwm) {
            return;
        }
        if let Some(managed) = self.x11_window_of(&window) {
            self.minimize_window(&managed, "the X11 client minimized itself");
        }
    }

    fn resize_request(&mut self, _xwm: XwmId, window: X11Surface, _button: u32, _edges: ResizeEdge) {
        debug!(x11 = window.window_id(), "X11 resize request: windows are placed by duo-wc");
    }

    fn move_request(&mut self, _xwm: XwmId, window: X11Surface, _button: u32) {
        debug!(x11 = window.window_id(), "X11 move request: windows are placed by duo-wc");
    }

    fn active_window_request(
        &mut self,
        xwm: XwmId,
        window: X11Surface,
        _timestamp: u32,
        currently_active: Option<X11Surface>,
    ) {
        if !self.xwl_current(xwm) {
            return;
        }
        // Only from an X11 app that has the keyboard now (one of its windows
        // brings up another): no focus stealing.
        let focused_x11 = self.xwl.focus.is_some();
        let Some(managed) = self.x11_window_of(&window) else {
            return;
        };
        if focused_x11 || currently_active.is_some_and(|c| self.xwl.focus.map(|f| f.0) == Some(c.window_id()))
        {
            self.activate_window(&managed);
        }
    }

    fn allow_selection_access(&mut self, xwm: XwmId, selection: SelectionTarget) -> bool {
        if !self.xwl_current(xwm) {
            return false;
        }
        // The clipboard, for the X11 app with the keyboard (as on any
        // desktop: the app being used pastes).
        selection == SelectionTarget::Clipboard && self.xwl.focus.is_some()
    }

    fn send_selection(&mut self, xwm: XwmId, selection: SelectionTarget, mime_type: String, fd: OwnedFd) {
        if !self.xwl_current(xwm) {
            return;
        }
        if selection != SelectionTarget::Clipboard {
            return;
        }
        if let Err(err) = request_data_device_client_selection(&self.seat, mime_type, fd) {
            debug!(?err, "the Wayland clipboard for X11");
        }
    }

    fn new_selection(&mut self, xwm: XwmId, selection: SelectionTarget, mime_types: Vec<String>) {
        if !self.xwl_current(xwm) {
            return;
        }
        if selection != SelectionTarget::Clipboard {
            return;
        }
        debug!(?mime_types, "X11 copied: the seat's clipboard");
        set_data_device_selection(&self.display_handle, &self.seat, mime_types, ());
    }

    fn cleared_selection(&mut self, xwm: XwmId, selection: SelectionTarget) {
        if !self.xwl_current(xwm) {
            return;
        }
        if selection == SelectionTarget::Clipboard
            && current_data_device_selection_userdata(&self.seat).is_some()
        {
            clear_data_device_selection(&self.display_handle, &self.seat);
        }
    }

    fn disconnected(&mut self, xwm: XwmId) {
        if !self.xwl_current(xwm) {
            // A stopped instance's connection, closing after it.
            return;
        }
        warn!("the X11 connection closed");
        self.loop_handle.insert_idle(|state| state.xwl_stop("the X11 connection closed"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn modes_parse() {
        assert_eq!(XMode::parse("lazy"), Some(XMode::Lazy));
        assert_eq!(XMode::parse(" ON "), Some(XMode::On));
        assert_eq!(XMode::parse("off"), Some(XMode::Off));
        assert_eq!(XMode::parse("sometimes"), None);
        assert_eq!(XMode::default(), XMode::Lazy);
        for m in [XMode::Lazy, XMode::On, XMode::Off] {
            assert_eq!(XMode::parse(m.name()), Some(m));
        }
    }

    #[test]
    fn the_lock_file_is_an_x_servers() {
        assert_eq!(lock_contents(1234), "      1234\n");
        assert_eq!(lock_contents(1234).len(), 11);
        assert_eq!(lock_pid("      1234\n"), Some(1234));
        assert_eq!(lock_pid("junk"), None);
        assert_eq!(lock_pid("0"), None);
        assert_eq!(lock_path(0), "/tmp/.X0-lock");
        assert_eq!(socket_path(3), "/tmp/.X11-unix/X3");
    }

    #[test]
    fn proc_stat_names_and_parents() {
        let (comm, rest) = parse_stat("4242 (Xwayland) S 1234 4242 4242 0 -1").unwrap();
        assert_eq!(comm, "Xwayland");
        assert_eq!(rest.split_whitespace().nth(1), Some("1234"));
        let (comm, _) = parse_stat("7 (a (b) c) R 1 7").unwrap();
        assert_eq!(comm, "a (b) c");
        assert!(parse_stat("junk").is_none());
    }

    #[test]
    fn the_wrapper_gets_the_descriptors() {
        assert_eq!(listenfd_value(&[12, 13]), "12 13");
        assert_eq!(listenfd_value(&[]), "");
    }

    #[test]
    fn the_scale_is_told_as_x11_dpi() {
        assert_eq!(dpi(2.5), 240);
        assert_eq!(dpi(1.0), 96);
        assert_eq!(gdk_window_scale(2.5), 2);
        assert_eq!(gdk_window_scale(0.8), 1);
        let r = resources(2.5);
        assert!(r.contains("Xft.dpi:\t240\n"), "{r}");
        assert!(r.contains("Xcursor.size:\t60\n"), "{r}");
        assert!(r.ends_with('\n'));
        let s = xsettings(2.5);
        let get = |name: &str| s.iter().find(|(n, _)| *n == name).map(|(_, v)| v.clone());
        assert_eq!(get("Xft/DPI"), Some(XValue::Int(240 * 1024)));
        // GTK: 2 x 120 dpi = the whole 2.5.
        assert_eq!(get("Gdk/WindowScalingFactor"), Some(XValue::Int(2)));
        assert_eq!(get("Gdk/UnscaledDPI"), Some(XValue::Int(120 * 1024)));
        assert_eq!(get("Xft/RGBA"), Some(XValue::Str("none")));
    }

    #[test]
    fn the_app_id_is_the_class() {
        assert_eq!(app_id("krita", "krita"), Some("krita".to_owned()));
        assert_eq!(app_id("XTerm", "xterm"), Some("XTerm".to_owned()));
        assert_eq!(app_id("", "xeyes"), Some("xeyes".to_owned()));
        assert_eq!(app_id(" ", ""), None);
    }

    #[test]
    fn dialogs_by_transient_or_type() {
        assert!(is_dialog_type(true, None));
        assert!(is_dialog_type(true, Some(WmWindowType::Normal)));
        assert!(is_dialog_type(false, Some(WmWindowType::Dialog)));
        assert!(is_dialog_type(false, Some(WmWindowType::Utility)));
        assert!(is_dialog_type(false, Some(WmWindowType::Splash)));
        assert!(!is_dialog_type(false, Some(WmWindowType::Normal)));
        assert!(!is_dialog_type(false, None));
    }

    #[test]
    fn a_dialog_keeps_its_size_within_the_room() {
        let room = (538, 709);
        assert_eq!(dialog_size(Some((400, 300)), (0, 0), room), (400, 300));
        // Bigger than the panel: the room.
        assert_eq!(dialog_size(Some((900, 300)), (0, 0), room), (538, 300));
        // Its max size counts.
        assert_eq!(dialog_size(Some((400, 300)), (350, 0), room), (350, 300));
        // Asked for nothing: the room.
        assert_eq!(dialog_size(None, (0, 0), room), room);
        assert_eq!(dialog_size(Some((0, 0)), (0, 0), room), room);
    }

    #[test]
    fn only_foreign_clients_keep_xwayland() {
        // The server (0), the window manager and the helpers are ours.
        assert_eq!(others(&[0, 0x200000, 0x400000, 0x600000], &[0x200000, 0x400000, 0x600000]), 0);
        assert_eq!(others(&[0, 0x200000, 0x800000], &[0x200000]), 1);
        assert_eq!(others(&[], &[]), 0);
    }

    #[test]
    fn x11_apps_get_the_qt_tablet_switch() {
        assert!(X11_APP_ENV.contains(&("QT_XCB_TABLET_LEGACY_COORDINATES", "1")));
    }

    #[test]
    fn the_pen_reaches_x11_at_the_panels_pixels() {
        // A pen at (300, 300) logical over a window placed below the status
        // bar, at (0, 48): Smithay sends the window's surface the point
        // relative to it, times the client scale; Xwayland adds the
        // window's X position. The X server's position is the global one
        // in X pixels (what xinput showed for the pen on the phone).
        let scale = 2.5;
        let pen: Point<f64, Logical> = (300.0, 300.0).into();
        let window: Point<i32, Logical> = (0, 48).into();
        let local = (pen - window.to_f64()).to_physical(scale);
        let window_x = window.to_f64().to_physical(scale);
        let root = (local.x + window_x.x, local.y + window_x.y);
        assert_eq!(root, (750.0, 750.0));
        assert_eq!((local.x, local.y), (750.0, 630.0));
        // A client that maps the root position into its window with a
        // cached origin of (0, 0) (Qt 5's valuator path, the app asked for
        // the top left) is the bar's 48 px (120 X px) off: what the
        // variable above avoids.
        assert_eq!(root.1 - local.y, 120.0);
    }

    #[test]
    fn x11_space_is_the_logical_space_times_the_scale() {
        // What Smithay's X11Surface::configure sends for duo-wc's panels at
        // 2.5 (its to_client_precise_round is crate-private; the physical
        // conversion is the same maths: times the scale, rounded): DSI-2
        // starts at 564 logical with the hinge gap.
        let panel: Rectangle<i32, Logical> = Rectangle::new((564, 48).into(), (538, 709).into());
        let x = panel.to_physical_precise_round::<f64, i32>(2.5);
        assert_eq!((x.loc.x, x.loc.y, x.size.w, x.size.h), (1410, 120, 1345, 1773));
        // And back (Smithay's ConfigureNotify divides and rounds): the same
        // rectangle, so a window's place survives the round trip.
        let back = x.to_f64().to_logical(2.5).to_i32_round();
        assert_eq!(back, panel);
    }
}
