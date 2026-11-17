//! The lock screen's compositor side: ext-session-lock-v1.
//!
//! A lock client (duo-shell's `duo-lock`) asks to lock; from then on duo-wc
//! draws nothing but the client's lock surfaces (one per panel; a panel
//! without one shows the empty background), sends every touch on a lit panel
//! to the lock surface there and the keyboard to the active panel's. No
//! window, layer surface (status bar, keyboard, notifications), overview or
//! edge swipe shows or reacts while locked. The power key still blanks and
//! wakes. The client unlocks (unlock_and_destroy) when its PIN is right.
//!
//! **Off by default** (`session_lock = off` in the config): the global is
//! then not advertised at all, so nothing can lock the phone. `duoctl
//! set_session_lock on` turns it on until restart; `duoctl unlock` ends a
//! lock whatever the client does (the way back over ssh if a lock client
//! breaks). A lock client that dies leaves the session locked, as the
//! protocol requires, and the next lock client takes it over
//! ([`lock_may_take`]); a restart of duo-wc comes up LOCKED again (the
//! shell starts duo-lock for it), black until then.
//!
//! **Where the lock is remembered across a restart**: in systemd's file
//! descriptor store of duo-wc.service (a memfd named `duo-wc-locked`,
//! `FileDescriptorStorePreserve=yes`), not in a file. Every app runs as the
//! same user as duo-wc, so a flag file the compositor could write, an app
//! could delete, and then make duo-wc restart (kill it, or ask Duo
//! Settings' helper to restart the display) and come up unlocked. The store
//! is systemd's: only duo-wc's main process may change it (NotifyAccess=main).
//! A reboot empties it; `systemctl clean --what=fdstore duo-wc` (root) too.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use smithay::output::Output;
use smithay::reexports::wayland_protocols::ext::session_lock::v1::server::{
    ext_session_lock_manager_v1::ExtSessionLockManagerV1,
    ext_session_lock_surface_v1::ExtSessionLockSurfaceV1,
    ext_session_lock_v1::{self, ExtSessionLockV1},
};
use smithay::reexports::wayland_server::backend::ClientId;
use smithay::reexports::wayland_server::protocol::wl_output::WlOutput;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::{
    delegate_dispatch, delegate_global_dispatch, Client, DataInit, Dispatch, DisplayHandle, Resource,
};
use smithay::utils::{Logical, Size};
use smithay::wayland::session_lock::{
    ExtLockSurfaceUserData, LockSurface, SessionLockHandler, SessionLockManagerGlobalData,
    SessionLockManagerState, SessionLockState, SessionLocker,
};
use tracing::{info, warn};

use crate::state::DuoWc;

/// The name of the lock's entry in systemd's file descriptor store.
pub const FD_NAME: &str = "duo-wc-locked";

/// Whether systemd handed this start a stored lock (LISTEN_FDS with the name
/// [`FD_NAME`]). Called once at start, before any thread or child: the
/// handed descriptors are closed and the LISTEN_* variables dropped (the
/// session must not take them for its own), and NOTIFY_SOCKET is taken out
/// of the environment and kept for [`store_flag`].
pub fn take_stored_flag() -> bool {
    let pid_ok = std::env::var("LISTEN_PID")
        .ok()
        .and_then(|p| p.parse::<u32>().ok())
        .is_some_and(|p| p == std::process::id());
    let count = std::env::var("LISTEN_FDS").ok().and_then(|n| n.parse::<i32>().ok()).unwrap_or(0);
    let names = std::env::var("LISTEN_FDNAMES").unwrap_or_default();
    let found = pid_ok && stored_flag_named(count, &names);
    if pid_ok {
        for fd in 3..3 + count.clamp(0, 64) {
            // Safety: these are ours (LISTEN_PID is this process).
            unsafe { libc::close(fd) };
        }
    }
    if let Ok(socket) = std::env::var("NOTIFY_SOCKET") {
        let _ = NOTIFY_SOCKET.set(socket);
    }
    // Safety: at start, before any other thread exists.
    unsafe {
        for var in ["LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES", "NOTIFY_SOCKET"] {
            std::env::remove_var(var);
        }
    }
    found
}

/// Whether LISTEN_FDS / LISTEN_FDNAMES name the stored lock (pure).
pub fn stored_flag_named(count: i32, names: &str) -> bool {
    count > 0 && names.split(':').take(count as usize).any(|n| n == FD_NAME)
}

static NOTIFY_SOCKET: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// Put the lock into systemd's descriptor store, or take it out.
/// Tell systemd the compositor is up (`READY=1`, once): sent when every lit
/// panel has shown its first frame (backend.rs), or after
/// [`READY_FALLBACK`] whatever happened. The unit is `Type=notify`, so what
/// is ordered after duo-wc (the boot splash leaving,
/// duo2-plymouth-quit.service) waits for the first picture.
pub fn notify_ready(why: &str) {
    static SENT: AtomicBool = AtomicBool::new(false);
    if SENT.swap(true, Ordering::SeqCst) {
        return;
    }
    let Some(path) = NOTIFY_SOCKET.get() else {
        return;
    };
    let send = || -> std::io::Result<()> {
        use std::os::linux::net::SocketAddrExt as _;
        let socket = std::os::unix::net::UnixDatagram::unbound()?;
        match path.strip_prefix('@') {
            Some(name) => socket.connect_addr(&std::os::unix::net::SocketAddr::from_abstract_name(name)?)?,
            None => socket.connect(path)?,
        }
        socket.send(b"READY=1").map(|_| ())
    };
    match send() {
        Ok(()) => info!(why, "ready (systemd told)"),
        Err(e) => warn!("cannot tell systemd we are ready: {e}"),
    }
}

/// READY=1 at the latest this long after the start (no panel lit, a panel
/// that never presents): the unit's start must not time out.
pub const READY_FALLBACK: std::time::Duration = std::time::Duration::from_secs(12);

fn store_flag(locked: bool) {
    let Some(path) = NOTIFY_SOCKET.get() else {
        warn!("no NOTIFY_SOCKET (not started by systemd): a restart would not come up locked");
        return;
    };
    if let Err(e) = notify_store(path, locked) {
        warn!("cannot update the stored lock: {e}");
    }
}

fn notify_store(path: &str, locked: bool) -> std::io::Result<()> {
    use std::os::linux::net::SocketAddrExt as _;
    let socket = std::os::unix::net::UnixDatagram::unbound()?;
    match path.strip_prefix('@') {
        Some(name) => socket.connect_addr(&std::os::unix::net::SocketAddr::from_abstract_name(name)?)?,
        None => socket.connect(path)?,
    }
    if locked {
        let name = std::ffi::CString::new(FD_NAME).expect("no NUL");
        // Safety: memfd_create with a valid name; the result is owned below.
        let fd = unsafe { libc::memfd_create(name.as_ptr(), libc::MFD_CLOEXEC) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // Safety: a fresh descriptor we own.
        let fd = unsafe { <std::os::fd::OwnedFd as std::os::fd::FromRawFd>::from_raw_fd(fd) };
        let message = format!("FDSTORE=1\nFDNAME={FD_NAME}\nFDPOLL=0");
        crate::trust::send_with_fd(&socket, message.as_bytes(), &fd)
    } else {
        socket.send(format!("FDSTOREREMOVE=1\nFDNAME={FD_NAME}").as_bytes()).map(|_| ())
    }
}

/// The lock screen's rules (root's `/var/lib/duo2-lock/policy`, 0644, no
/// secret: duo-lock-helper writes it, shell/duo-shell/duoshell/lockstore.py
/// reads it the same way).
pub const POLICY_FILE: &str = "/var/lib/duo2-lock/policy";

/// What may lock the phone by itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockEvent {
    /// Folded closed (`lock_on_close`, on by default).
    Close,
    /// The screen went off (`lock_on_blank`, only with a PIN).
    Blank,
}

/// Whether the policy's text says this event locks (pure; the shell's
/// lockstore rules: defaults pin = no, lock_on_blank = off, lock_on_close =
/// on; unknown keys and values ignored).
pub fn policy_locks(text: &str, event: LockEvent) -> bool {
    let (mut pin, mut on_blank, mut on_close) = (false, false, true);
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        let Some((key, value)) = line.split_once('=') else { continue };
        match (key.trim(), value.trim()) {
            ("pin", "yes") => pin = true,
            ("pin", "no") => pin = false,
            ("lock_on_blank", "on") => on_blank = true,
            ("lock_on_blank", "off") => on_blank = false,
            ("lock_on_close", "on") => on_close = true,
            ("lock_on_close", "off") => on_close = false,
            _ => {}
        }
    }
    match event {
        LockEvent::Close => on_close,
        LockEvent::Blank => pin && on_blank,
    }
}

/// The size a lock surface is configured to: its output's logical size,
/// as it is NOW (after any rotation; pure).
pub fn lock_size(output: Size<i32, Logical>) -> Size<u32, Logical> {
    (output.w.max(1) as u32, output.h.max(1) as u32).into()
}

/// Whose `unlock_and_destroy` an unlock is ([`unlocker`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Unlocker {
    /// The lock object that holds the session: it unlocks.
    Holder,
    /// Not locked: a lock ended for its client (`finished`) answering.
    NotLocked,
    /// Any other lock object: one refused while another held the session,
    /// or one ended before a later lock. Smithay posts the protocol error
    /// and still calls `SessionLockHandler::unlock`; the session stays
    /// locked.
    Other,
}

/// Whose `unlock_and_destroy` this is (pure): `from` is the lock object
/// that sent it, `holder` the one that holds the session.
pub fn unlocker<T: PartialEq>(locked: bool, from: Option<&T>, holder: Option<&T>) -> Unlocker {
    if !locked {
        return Unlocker::NotLocked;
    }
    match (from, holder) {
        (Some(from), Some(holder)) if from == holder => Unlocker::Holder,
        _ => Unlocker::Other,
    }
}

/// Whether a lock request may take the session (pure): when it is not
/// locked, or locked with nobody holding it: from before a restart, by the
/// rules, or its holder's client gone (the session stayed locked).
pub fn lock_may_take(locked: bool, restored: bool, holder_alive: bool) -> bool {
    !locked || restored || !holder_alive
}

/// The lock's state.
#[derive(Default)]
pub struct Lock {
    /// Locked now.
    pub locked: bool,
    /// Locked from before a restart of duo-wc and no lock client has taken
    /// it over yet: the screen is black, and the next lock request is
    /// accepted (the shell starts duo-lock for it) instead of refused.
    pub restored: bool,
    /// The lock surfaces, by output name.
    pub surfaces: Vec<(String, LockSurface)>,
    /// Whether the ext-session-lock global is offered to clients (the
    /// config's `session_lock`), shared with the global's filter.
    pub enabled: Arc<AtomicBool>,
    /// The lock object of the live lock (to tell its client `finished`
    /// when the lock is ended for it: `duoctl unlock`).
    pub current: Option<ExtSessionLockV1>,
    /// Lock objects told `finished` whose clients have not answered yet
    /// (their `unlock_and_destroy` must not end a later lock).
    pub finished: Vec<ExtSessionLockV1>,
    /// The lock object whose request is being dispatched: Smithay's
    /// `unlock` does not say which one unlocked.
    pub asking: Option<ExtSessionLockV1>,
}

impl Lock {
    /// A lock that is not offered unless `enabled`; `stored`: systemd handed
    /// back a lock from before this start ([`take_stored_flag`]).
    pub fn new(enabled: bool, stored: bool) -> Self {
        // A phone that was locked when duo-wc went down comes up locked: black
        // from the very first frame, until the lock client is back.
        let restored = stored && enabled;
        if restored {
            info!("the session was locked before this start: locked, waiting for the lock client");
        }
        Lock {
            locked: restored,
            restored,
            surfaces: Vec::new(),
            enabled: Arc::new(AtomicBool::new(enabled)),
            current: None,
            finished: Vec::new(),
            asking: None,
        }
    }

    /// Whether clients may lock.
    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::SeqCst)
    }
}

impl DuoWc {
    /// The lock surface on an output, while locked.
    pub fn lock_surface(&self, output: &Output) -> Option<WlSurface> {
        if !self.lock.locked {
            return None;
        }
        self.lock
            .surfaces
            .iter()
            .find(|(name, s)| *name == output.name() && s.alive())
            .map(|(_, s)| s.wl_surface().clone())
    }

    /// Whether the lock surface on `output` has drawn at the output's size
    /// as it is now (true without one: the panel shows black). The rotation
    /// animation waits for this before it takes the new picture.
    pub fn lock_drawn_at_size(&self, output: &Output) -> bool {
        let Some(geometry) = self.space.output_geometry(output) else {
            return true;
        };
        let want = lock_size(geometry.size);
        let name = output.name();
        self.lock
            .surfaces
            .iter()
            .filter(|(n, s)| *n == name && s.alive())
            .all(|(_, s)| s.with_committed_state(|state| state.and_then(|st| st.size)) == Some(want))
    }

    /// Size every lock surface to its output again, after the outputs were
    /// re-arranged (`arrange_outputs`). A rotation swaps a panel's logical
    /// width and height; a lock surface keeps the size of its last
    /// configure, and it is drawn from the output's corner, so one made
    /// upright and then turned on its side covered only the left part of
    /// the panel: a black bar at the right edge, its bottom cut off
    /// (2026-11-08: locked by the fold at 0 degrees, opened at 90).
    /// `send_configure` sends nothing for an unchanged size.
    pub fn reconfigure_lock_surfaces(&mut self) {
        for (name, surface) in &self.lock.surfaces {
            if !surface.alive() {
                continue;
            }
            let Some(geometry) = self.output_by_name(name).and_then(|o| self.space.output_geometry(&o))
            else {
                continue;
            };
            let size = lock_size(geometry.size);
            let changed = surface.with_pending_state(|state| {
                let changed = state.size != Some(size);
                state.size = Some(size);
                changed
            });
            if changed {
                info!(output = name, w = size.w, h = size.h, "lock surface resized to its output");
            }
            surface.send_configure();
        }
    }

    /// End a lock from the control socket, whatever the client does. The
    /// lock client is told (`finished`): it answers `unlock_and_destroy`
    /// and exits, so the shell can lock again later (before, duo-lock
    /// stayed, and the shell started no new lock while it ran).
    pub fn force_unlock(&mut self) {
        if !self.lock.locked {
            return;
        }
        warn!("session unlocked by force (control socket)");
        if let Some(lock) = self.lock.current.take() {
            if lock.is_alive() {
                lock.finished();
                self.lock.finished.push(lock);
            }
        }
        self.end_lock();
    }

    /// Lock by the rules, without waiting for the shell (2026-11-07 night):
    /// an app could kill the shell, and for the second until it is back a
    /// fold or a blank used to leave the phone unlocked. Locked like after
    /// a restart: black, the next lock client (the shell's duo-lock) takes
    /// it over.
    pub fn lock_by_policy(&mut self, event: LockEvent) {
        if self.lock.locked || !self.lock.is_enabled() {
            return;
        }
        let text = std::fs::read_to_string(POLICY_FILE).unwrap_or_default();
        if !policy_locks(&text, event) {
            return;
        }
        info!(?event, "locking by the lock screen's rules (the shell's duo-lock takes it over)");
        self.overview_close("locked");
        self.cancel_all_touches();
        self.held_touches.clear();
        self.lock.locked = true;
        self.lock.restored = true;
        self.lock.surfaces.clear();
        store_flag(true);
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    fn end_lock(&mut self) {
        self.lock.locked = false;
        self.lock.restored = false;
        store_flag(false);
        self.lock.surfaces.clear();
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.place_all();
        self.queue_redraw_all();
    }
}

impl SessionLockHandler for DuoWc {
    fn lock_state(&mut self) -> &mut SessionLockManagerState {
        &mut self.session_lock_state
    }

    fn lock(&mut self, confirmation: SessionLocker) {
        if !self.lock.is_enabled() {
            // Dropping the locker tells the client it failed.
            warn!("a lock was asked for, but session_lock is off");
            return;
        }
        let holder_alive = self.lock.current.as_ref().is_some_and(|l| l.is_alive());
        if !lock_may_take(self.lock.locked, self.lock.restored, holder_alive) {
            // Already locked by a live client: the protocol refuses a second.
            warn!("a lock was asked for while locked");
            return;
        }
        self.lock.finished.retain(|l| l.is_alive());
        if !self.lock.finished.is_empty() {
            // The client of a lock ended by force has not answered yet:
            // its unlock_and_destroy (still to come) could not be told
            // from this lock's.
            warn!("a lock was asked for while an ended lock's client is still there");
            return;
        }
        self.lock.current = Some(confirmation.ext_session_lock().clone());
        info!(restored = self.lock.restored, taken_over = self.lock.locked, "session locked");
        // A lock taken over is in the store already (an unlock removes
        // every entry of the name anyway).
        if !self.lock.locked {
            store_flag(true);
        }
        self.lock.restored = false;
        self.overview_close("locked");
        self.cancel_all_touches();
        self.held_touches.clear();
        self.lock.locked = true;
        self.lock.surfaces.clear();
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
        // From the next frame on only lock surfaces are drawn (render.rs);
        // nothing of the session is reachable before that either (input.rs).
        confirmation.lock();
    }

    fn unlock(&mut self) {
        self.lock.finished.retain(|l| l.is_alive());
        match unlocker(self.lock.locked, self.lock.asking.as_ref(), self.lock.current.as_ref()) {
            Unlocker::NotLocked => info!("an ended lock's client let go"),
            Unlocker::Other => {
                warn!("unlock_and_destroy from a lock that does not hold the session: still locked")
            }
            Unlocker::Holder => {
                info!("session unlocked");
                self.lock.current = None;
                self.end_lock();
            }
        }
    }

    fn new_surface(&mut self, surface: LockSurface, wl_output: WlOutput) {
        let Some(output) = Output::from_resource(&wl_output) else {
            return;
        };
        let Some(geometry) = self.space.output_geometry(&output) else {
            return;
        };
        info!(output = output.name(), "lock surface");
        surface.with_pending_state(|state| {
            state.size = Some(lock_size(geometry.size));
        });
        surface.send_configure();
        output.enter(surface.wl_surface());
        self.lock.surfaces.retain(|(name, s)| *name != output.name() && s.alive());
        self.lock.surfaces.push((output.name(), surface));
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }
}

// Smithay's delegate_session_lock!, but the lock object's requests go
// through DuoWc first, which notes which lock object asks (Lock::asking).
delegate_global_dispatch!(DuoWc: [ExtSessionLockManagerV1: SessionLockManagerGlobalData] => SessionLockManagerState);
delegate_dispatch!(DuoWc: [ExtSessionLockManagerV1: ()] => SessionLockManagerState);
delegate_dispatch!(DuoWc: [ExtSessionLockSurfaceV1: ExtLockSurfaceUserData] => SessionLockManagerState);

impl Dispatch<ExtSessionLockV1, SessionLockState> for DuoWc {
    fn request(
        state: &mut Self,
        client: &Client,
        lock: &ExtSessionLockV1,
        request: ext_session_lock_v1::Request,
        data: &SessionLockState,
        dhandle: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        state.lock.asking = Some(lock.clone());
        <SessionLockManagerState as Dispatch<ExtSessionLockV1, SessionLockState, Self>>::request(
            state, client, lock, request, data, dhandle, data_init,
        );
        state.lock.asking = None;
    }

    fn destroyed(state: &mut Self, client: ClientId, lock: &ExtSessionLockV1, data: &SessionLockState) {
        <SessionLockManagerState as Dispatch<ExtSessionLockV1, SessionLockState, Self>>::destroyed(
            state, client, lock, data,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_rules_as_the_shell_reads_them() {
        assert!(policy_locks("", LockEvent::Close), "lock_on_close is on by default");
        assert!(!policy_locks("", LockEvent::Blank), "no PIN: no lock on blank");
        let text = "# duo2-lock\npin = yes\nlock_on_blank = on\nlock_on_close = off\n";
        assert!(policy_locks(text, LockEvent::Blank));
        assert!(!policy_locks(text, LockEvent::Close));
        assert!(!policy_locks("pin = no\nlock_on_blank = on\n", LockEvent::Blank));
        assert!(policy_locks("lock_on_close = maybe\n", LockEvent::Close), "a bad value: the default");
    }

    #[test]
    fn a_lock_surface_takes_its_output_s_size_as_it_is_now() {
        // A panel turned on its side: the configure is the turned size.
        assert_eq!(lock_size((757, 538).into()), Size::<u32, Logical>::from((757, 538)));
        assert_eq!(lock_size((538, 757).into()), Size::<u32, Logical>::from((538, 757)));
        assert_eq!(lock_size((0, 0).into()), Size::<u32, Logical>::from((1, 1)));
    }

    #[test]
    fn only_the_lock_holding_the_session_unlocks_it() {
        assert_eq!(unlocker(true, Some(&1), Some(&1)), Unlocker::Holder);
        // A lock refused while lock 1 held the session (Smithay told it
        // `finished`) sending unlock_and_destroy: it unlocked the session.
        assert_eq!(unlocker(true, Some(&2), Some(&1)), Unlocker::Other);
        // Locked by the rules or from before a restart: nobody holds it
        // yet, a stale lock object cannot end it.
        assert_eq!(unlocker(true, Some(&2), None), Unlocker::Other);
        assert_eq!(unlocker(true, None, Some(&1)), Unlocker::Other);
        assert_eq!(unlocker(false, Some(&1), None), Unlocker::NotLocked);
    }

    #[test]
    fn a_lock_nobody_holds_may_be_taken_over() {
        assert!(lock_may_take(false, false, false));
        assert!(!lock_may_take(true, false, true), "a live holder: a second lock is refused");
        assert!(lock_may_take(true, true, false), "from before a restart, or by the rules");
        // The lock client died: the session stayed locked, black, and
        // refused every new lock client.
        assert!(lock_may_take(true, false, false));
    }

    #[test]
    fn a_lock_that_is_off_never_comes_up_restored() {
        let lock = Lock::new(false, true);
        assert!(!lock.locked && !lock.restored);
    }

    #[test]
    fn the_stored_lock_is_found_by_name() {
        assert!(stored_flag_named(1, "duo-wc-locked"));
        assert!(stored_flag_named(2, "other:duo-wc-locked"));
        assert!(!stored_flag_named(0, "duo-wc-locked"));
        assert!(!stored_flag_named(1, "other:duo-wc-locked"));
        assert!(!stored_flag_named(1, ""));
        assert!(!stored_flag_named(1, "duo-wc-locked-not"));
    }
}
