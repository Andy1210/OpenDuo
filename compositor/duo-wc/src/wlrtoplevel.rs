//! wlr-foreign-toplevel-management-unstable-v1 (version 3): the windows, for
//! the on-screen keyboard phosh-osk-stub (Debian trixie's 0.46). It makes its keyboard surface only once it has bound
//! this global among others ([`crate::devicestate`] lists them all), and
//! uses it for one thing: its `ignore-activation` setting (application ids
//! whose text fields do not unfold the keyboard by themselves; GNOME
//! Calculator by default) needs the app id of the ACTIVATED toplevel. The
//! shell reads the windows through ext-foreign-toplevel-list (Smithay's,
//! `crate::state`); this is the same list in the older protocol.
//!
//! Every window the event stream lists (not Android's keyboard window) is a
//! handle with its title, app id and state: `activated` for the window with
//! the keyboard focus, `minimized` for one set aside (home), `fullscreen`
//! (version 2 on). `maximized` is never claimed, `output_enter` /
//! `output_leave` and `parent` are not sent (nobody here reads them). The
//! requests (activate, close, minimize, fullscreen, set_rectangle) are
//! ignored, as the protocol allows: what windows do is the user's, through
//! the shell and duo-wc's own gestures.
//!
//! The list is compared with what the handles were told once per turn of
//! the event loop ([`sync`], only while a client has bound the global), so
//! every change is caught whatever made it (a commit with a new title, the
//! keyboard focus moving, a window going home), and nothing is sent while
//! nothing changes.
//!
//! The global is guarded ([`crate::trust::Guarded::ForeignToplevels`], as
//! the ext list): only trusted clients see it.

use std::collections::BTreeMap;

use smithay::reexports::wayland_protocols_wlr::foreign_toplevel::v1::server::{
    zwlr_foreign_toplevel_handle_v1::{self, ZwlrForeignToplevelHandleV1},
    zwlr_foreign_toplevel_manager_v1::{self, ZwlrForeignToplevelManagerV1},
};
use smithay::reexports::wayland_server::backend::ClientId;
use smithay::reexports::wayland_server::{
    Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
};
use tracing::{debug, info};

use crate::state::{app_id_and_title, window_data, ClientState, DuoWc};
use crate::trust::Guarded;

/// The version offered.
pub const VERSION: u32 = 3;

/// The protocol's state values.
const STATE_MINIMIZED: u32 = 1;
const STATE_ACTIVATED: u32 = 2;
const STATE_FULLSCREEN: u32 = 3;

/// What the handles of a window say about it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Facts {
    /// Its title ("" while it has none).
    pub title: String,
    /// Its app id ("" while it has none).
    pub app_id: String,
    /// It has the keyboard focus.
    pub activated: bool,
    /// It is set aside (home).
    pub minimized: bool,
    /// It is fullscreen.
    pub fullscreen: bool,
}

/// The `state` event's array for a client of `version`: the protocol's
/// values as native-endian u32s (a Wayland array is the sender's memory),
/// `fullscreen` only from version 2 (it did not exist before).
pub fn state_array(facts: &Facts, version: u32) -> Vec<u8> {
    let mut states = Vec::new();
    if facts.minimized {
        states.push(STATE_MINIMIZED);
    }
    if facts.activated {
        states.push(STATE_ACTIVATED);
    }
    if facts.fullscreen && version >= 2 {
        states.push(STATE_FULLSCREEN);
    }
    states.into_iter().flat_map(u32::to_ne_bytes).collect()
}

/// What a handle that last heard `old` (None: a new handle, told
/// everything) is to be told about `new`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Update {
    /// Send the title.
    pub title: bool,
    /// Send the app id.
    pub app_id: bool,
    /// Send the state array.
    pub state: bool,
}

impl Update {
    /// Anything to send (then `done` follows).
    pub fn any(self) -> bool {
        self.title || self.app_id || self.state
    }
}

/// The events a handle needs to go from `old` to `new`.
pub fn update(old: Option<&Facts>, new: &Facts) -> Update {
    match old {
        None => Update { title: true, app_id: true, state: true },
        Some(old) => Update {
            title: old.title != new.title,
            app_id: old.app_id != new.app_id,
            state: (old.activated, old.minimized, old.fullscreen)
                != (new.activated, new.minimized, new.fullscreen),
        },
    }
}

/// The handles of one window, and what they were told.
struct Tracked {
    facts: Facts,
    handles: Vec<ZwlrForeignToplevelHandleV1>,
}

/// The managers bound and the windows they were told about.
#[derive(Default)]
pub struct WlrToplevels {
    /// Managers that have been told about every window.
    managers: Vec<ZwlrForeignToplevelManagerV1>,
    /// Managers bound this turn, told about the windows at its end.
    fresh: Vec<ZwlrForeignToplevelManagerV1>,
    /// By window id.
    windows: BTreeMap<u64, Tracked>,
}

impl WlrToplevels {
    /// Create the global.
    pub fn init(display: &DisplayHandle) {
        display.create_global::<DuoWc, ZwlrForeignToplevelManagerV1, ()>(VERSION, ());
    }
}

/// A handle's window.
pub struct HandleData {
    window: u64,
}

/// Every window as the handles are to describe it, in the space's order.
fn current(state: &DuoWc) -> Vec<(u64, Facts)> {
    let focus = state
        .seat
        .get_keyboard()
        .and_then(|k| k.current_focus())
        .and_then(|s| state.window_for_surface(&s))
        .map(|w| window_data(&w).id);
    state
        .space
        .elements()
        .filter(|w| !state.is_accessory(w))
        .map(|w| {
            let data = window_data(w);
            let (app_id, title) = app_id_and_title(w);
            let facts = Facts {
                title: title.unwrap_or_default(),
                app_id: app_id.unwrap_or_default(),
                activated: focus == Some(data.id),
                minimized: state.is_minimized(w),
                fullscreen: *data.fullscreen.borrow(),
            };
            (data.id, facts)
        })
        .collect()
}

/// Tell one handle what changed (and `done`).
fn tell(handle: &ZwlrForeignToplevelHandleV1, old: Option<&Facts>, new: &Facts) {
    let what = update(old, new);
    if !what.any() {
        return;
    }
    if what.title {
        handle.title(new.title.clone());
    }
    if what.app_id {
        handle.app_id(new.app_id.clone());
    }
    if what.state {
        handle.state(state_array(new, handle.version()));
    }
    handle.done();
}

/// A new handle of window `id` for `manager`, told everything.
fn announce(
    dh: &DisplayHandle,
    manager: &ZwlrForeignToplevelManagerV1,
    id: u64,
    facts: &Facts,
) -> Option<ZwlrForeignToplevelHandleV1> {
    let client = dh.get_client(manager.id()).ok()?;
    let handle = client
        .create_resource::<ZwlrForeignToplevelHandleV1, HandleData, DuoWc>(
            dh,
            manager.version(),
            HandleData { window: id },
        )
        .ok()?;
    manager.toplevel(&handle);
    tell(&handle, None, facts);
    Some(handle)
}

/// Bring every handle up to date (once per turn of the event loop).
pub fn sync(state: &mut DuoWc) {
    {
        let w = &mut state.wlr_toplevels;
        if w.managers.is_empty() && w.fresh.is_empty() {
            // Nobody listens: nothing to remember either.
            w.windows.clear();
            return;
        }
    }
    let now = current(state);
    let dh = state.display_handle.clone();
    let w = &mut state.wlr_toplevels;
    w.managers.retain(|m| m.is_alive());
    w.fresh.retain(|m| m.is_alive());

    // Windows gone.
    let ids: Vec<u64> = now.iter().map(|(id, _)| *id).collect();
    w.windows.retain(|id, tracked| {
        if ids.contains(id) {
            return true;
        }
        for handle in tracked.handles.iter().filter(|h| h.is_alive()) {
            handle.closed();
        }
        false
    });

    for (id, facts) in &now {
        match w.windows.get_mut(id) {
            // Known: what changed, to every handle.
            Some(tracked) => {
                tracked.handles.retain(|h| h.is_alive());
                if tracked.facts != *facts {
                    for handle in &tracked.handles {
                        tell(handle, Some(&tracked.facts), facts);
                    }
                    tracked.facts = facts.clone();
                }
            }
            // New: a handle for every manager.
            None => {
                let handles = w.managers.iter().filter_map(|m| announce(&dh, m, *id, facts)).collect();
                w.windows.insert(*id, Tracked { facts: facts.clone(), handles });
            }
        }
    }

    // Managers bound this turn: every window.
    for manager in std::mem::take(&mut w.fresh) {
        for (id, facts) in &now {
            if let Some(handle) = announce(&dh, &manager, *id, facts) {
                if let Some(tracked) = w.windows.get_mut(id) {
                    tracked.handles.push(handle);
                }
            }
        }
        w.managers.push(manager);
    }
}

impl GlobalDispatch<ZwlrForeignToplevelManagerV1, ()> for DuoWc {
    fn bind(
        state: &mut Self,
        _handle: &DisplayHandle,
        client: &Client,
        resource: New<ZwlrForeignToplevelManagerV1>,
        _global_data: &(),
        data_init: &mut DataInit<'_, Self>,
    ) {
        let manager = data_init.init(resource, ());
        info!(client = ?client.id(), version = manager.version(), "wlr foreign toplevel manager bound");
        // Told about the windows at the end of this turn (sync).
        state.wlr_toplevels.fresh.push(manager);
    }

    fn can_view(client: Client, _global_data: &()) -> bool {
        ClientState::may_bind(&client, Guarded::ForeignToplevels)
    }
}

impl Dispatch<ZwlrForeignToplevelManagerV1, ()> for DuoWc {
    fn request(
        state: &mut Self,
        _client: &Client,
        resource: &ZwlrForeignToplevelManagerV1,
        request: zwlr_foreign_toplevel_manager_v1::Request,
        _data: &(),
        _dhandle: &DisplayHandle,
        _data_init: &mut DataInit<'_, Self>,
    ) {
        if let zwlr_foreign_toplevel_manager_v1::Request::Stop = request {
            // No more new toplevels for it; `finished` ends the object.
            forget_manager(state, resource);
            resource.finished();
        }
    }

    fn destroyed(state: &mut Self, _client: ClientId, resource: &ZwlrForeignToplevelManagerV1, _data: &()) {
        // Bookkeeping only: nothing is sent from a destructor (crate::teardown).
        forget_manager(state, resource);
    }
}

fn forget_manager(state: &mut DuoWc, manager: &ZwlrForeignToplevelManagerV1) {
    let w = &mut state.wlr_toplevels;
    w.managers.retain(|m| m.id() != manager.id());
    w.fresh.retain(|m| m.id() != manager.id());
}

impl Dispatch<ZwlrForeignToplevelHandleV1, HandleData> for DuoWc {
    fn request(
        _state: &mut Self,
        _client: &Client,
        _resource: &ZwlrForeignToplevelHandleV1,
        request: zwlr_foreign_toplevel_handle_v1::Request,
        data: &HandleData,
        _dhandle: &DisplayHandle,
        _data_init: &mut DataInit<'_, Self>,
    ) {
        // Ignored, as the protocol allows (see the module's comment).
        if !matches!(request, zwlr_foreign_toplevel_handle_v1::Request::Destroy) {
            debug!(window = data.window, ?request, "wlr foreign toplevel request ignored");
        }
    }

    fn destroyed(
        state: &mut Self,
        _client: ClientId,
        resource: &ZwlrForeignToplevelHandleV1,
        data: &HandleData,
    ) {
        // Bookkeeping only: nothing is sent from a destructor (crate::teardown).
        if let Some(tracked) = state.wlr_toplevels.windows.get_mut(&data.window) {
            tracked.handles.retain(|h| h.id() != resource.id());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(title: &str, app_id: &str) -> Facts {
        Facts { title: title.into(), app_id: app_id.into(), ..Default::default() }
    }

    fn values(array: &[u8]) -> Vec<u32> {
        array.chunks(4).map(|c| u32::from_ne_bytes([c[0], c[1], c[2], c[3]])).collect()
    }

    #[test]
    fn a_new_handle_is_told_everything() {
        let f = facts("", "");
        assert_eq!(update(None, &f), Update { title: true, app_id: true, state: true });
    }

    #[test]
    fn only_what_changed_is_sent() {
        let old = facts("Text Editor", "org.gnome.TextEditor");
        let mut new = old.clone();
        assert!(!update(Some(&old), &new).any());
        new.title = "notes.txt - Text Editor".into();
        assert_eq!(update(Some(&old), &new), Update { title: true, ..Default::default() });
        let mut focused = old.clone();
        focused.activated = true;
        assert_eq!(update(Some(&old), &focused), Update { state: true, ..Default::default() });
    }

    #[test]
    fn the_state_array_is_the_protocols_values() {
        let mut f = facts("Calculator", "org.gnome.Calculator");
        assert!(state_array(&f, 3).is_empty());
        f.activated = true;
        assert_eq!(values(&state_array(&f, 3)), vec![STATE_ACTIVATED]);
        f.minimized = true;
        f.fullscreen = true;
        assert_eq!(values(&state_array(&f, 3)), vec![STATE_MINIMIZED, STATE_ACTIVATED, STATE_FULLSCREEN]);
    }

    #[test]
    fn fullscreen_is_only_for_version_2_on() {
        let f = Facts { fullscreen: true, ..Default::default() };
        assert!(state_array(&f, 1).is_empty());
        assert_eq!(values(&state_array(&f, 2)), vec![STATE_FULLSCREEN]);
    }

    #[test]
    fn the_values_match_the_protocol() {
        use zwlr_foreign_toplevel_handle_v1::State;
        assert_eq!(STATE_MINIMIZED, u32::from(State::Minimized));
        assert_eq!(STATE_ACTIVATED, u32::from(State::Activated));
        assert_eq!(STATE_FULLSCREEN, u32::from(State::Fullscreen));
    }
}
