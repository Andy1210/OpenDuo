//! The clients' text-input-v3 requests, seen on their way to Smithay (which
//! routes them to the input method, squeekboard): duo-wc's own dispatch for
//! `zwp_text_input_v3` notes what each text input says (enabled, its content
//! purpose, its cursor rectangle, when it last committed) and hands the
//! request on unchanged. What the on-screen keyboard rules read
//! ([`crate::oskleave`]; squeekboard's start-up blink, [`crate::oskstart`],
//! is held back here too).

use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::time::Instant;

use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;

use smithay::reexports::wayland_protocols::wp::text_input::zv3::server::{
    zwp_text_input_manager_v3::ZwpTextInputManagerV3,
    zwp_text_input_v3::{self, ContentPurpose, ZwpTextInputV3},
};
use smithay::reexports::wayland_server::backend::{ClientId, ObjectId};
use smithay::reexports::wayland_server::{
    delegate_dispatch, delegate_global_dispatch, Client, DataInit, Dispatch, DisplayHandle, Resource as _,
};
use smithay::wayland::text_input::{TextInputManagerState, TextInputUserData};
use tracing::debug;

use crate::state::DuoWc;

/// What one text input has said (its committed state).
#[derive(Debug, Clone, Default)]
pub struct Seen {
    /// Its client.
    pub client: Option<ClientId>,
    /// Enabled (the client wants an input method).
    pub enabled: bool,
    /// What the field is for (a terminal keeps it enabled all the time).
    pub terminal: bool,
    /// The cursor rectangle (surface-local logical px).
    pub cursor: Option<(i32, i32, i32, i32)>,
    /// The surrounding text, the cursor and the anchor in it.
    pub text: Option<(String, i32, i32)>,
    /// When it last committed.
    pub committed: Option<Instant>,
    /// Requests since its last commit (pending: they count at the commit).
    pending: Pending,
}

#[derive(Debug, Clone, Default)]
struct Pending {
    enable: Option<bool>,
    terminal: Option<bool>,
    cursor: Option<(i32, i32, i32, i32)>,
    text: Option<(String, i32, i32)>,
}

/// Every live text input's state, by object.
#[derive(Debug, Default)]
pub struct TextSpy {
    /// By the text input object's id.
    pub inputs: HashMap<ObjectId, Seen>,
}

impl TextSpy {
    /// The enabled text input of a client, if it has one.
    pub fn enabled_of(&self, client: &ClientId) -> Option<&Seen> {
        self.inputs.values().find(|s| s.enabled && s.client.as_ref() == Some(client))
    }
}

delegate_global_dispatch!(DuoWc: [ZwpTextInputManagerV3: ()] => TextInputManagerState);
delegate_dispatch!(DuoWc: [ZwpTextInputManagerV3: ()] => TextInputManagerState);

impl Dispatch<ZwpTextInputV3, TextInputUserData> for DuoWc {
    fn request(
        state: &mut Self,
        client: &Client,
        resource: &ZwpTextInputV3,
        request: zwp_text_input_v3::Request,
        data: &TextInputUserData,
        dhandle: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        state.text_input_request(client, resource, &request);
        <TextInputManagerState as Dispatch<ZwpTextInputV3, TextInputUserData, DuoWc>>::request(
            state, client, resource, request, data, dhandle, data_init,
        );
    }

    fn destroyed(state: &mut Self, client: ClientId, resource: &ZwpTextInputV3, data: &TextInputUserData) {
        <TextInputManagerState as Dispatch<ZwpTextInputV3, TextInputUserData, DuoWc>>::destroyed(
            state, client, resource, data,
        );
        state.textspy.inputs.remove(&resource.id());
    }
}

impl DuoWc {
    /// Note a text input's request (before Smithay handles it).
    fn text_input_request(
        &mut self,
        client: &Client,
        resource: &ZwpTextInputV3,
        request: &zwp_text_input_v3::Request,
    ) {
        let seen = self.textspy.inputs.entry(resource.id()).or_default();
        seen.client = Some(client.id());
        match request {
            zwp_text_input_v3::Request::Enable => seen.pending.enable = Some(true),
            zwp_text_input_v3::Request::Disable => seen.pending.enable = Some(false),
            zwp_text_input_v3::Request::SetSurroundingText { text, cursor, anchor } => {
                seen.pending.text = Some((text.clone(), *cursor, *anchor));
            }
            zwp_text_input_v3::Request::SetContentType { purpose, .. } => {
                seen.pending.terminal = Some(matches!(purpose.into_result(), Ok(ContentPurpose::Terminal)));
            }
            zwp_text_input_v3::Request::SetCursorRectangle { x, y, width, height } => {
                seen.pending.cursor = Some((*x, *y, *width, *height));
            }
            zwp_text_input_v3::Request::Commit => {
                let pending = std::mem::take(&mut seen.pending);
                let was = seen.enabled;
                if let Some(e) = pending.enable {
                    seen.enabled = e;
                }
                if let Some(t) = pending.terminal {
                    seen.terminal = t;
                }
                let moved = pending.cursor.is_some() && pending.cursor != seen.cursor;
                if pending.cursor.is_some() {
                    seen.cursor = pending.cursor;
                }
                let retyped = pending.text.is_some() && pending.text != seen.text;
                if pending.text.is_some() {
                    seen.text = pending.text.clone();
                }
                seen.committed = Some(Instant::now());
                let (enabled, cursor) = (seen.enabled, seen.cursor);
                debug!(
                    enable = ?pending.enable,
                    enabled,
                    was,
                    text = pending.text.as_ref().map(|t| t.0.chars().count()),
                    retyped,
                    ?cursor,
                    moved,
                    "text input commit"
                );
                let changed = pending.enable == Some(true) && !was || moved || retyped;
                let field = crate::oskleave::is_field(seen.cursor, seen.text.as_ref().map(|t| t.0.len()));
                let terminal = seen.terminal;
                self.osk_text_input_commit(&client.id(), enabled, changed, field, terminal);
            }
            _ => {}
        }
    }
}

/// Taps beside a text field put the on-screen keyboard away
/// ([`crate::oskleave`]).
#[derive(Debug, Default)]
pub struct OskAway {
    /// A tap is being watched: the app (client) has to answer by this
    /// generation's timer.
    watch: Option<(ClientId, u64)>,
    /// The keyboard is put away for this app's text input.
    pub away: Option<ClientId>,
    gen: u64,
    /// The rule is on (duo-wc.conf `osk_leave`).
    pub enabled: bool,
}

impl OskAway {
    /// On or off.
    pub fn new(enabled: bool) -> Self {
        OskAway { enabled, ..Default::default() }
    }
}

/// squeekboard's start-up blink held back ([`crate::oskstart`]).
#[derive(Debug, Default)]
pub struct OskStartHold {
    /// The keyboard surface held back (its wl_surface), and its timer's
    /// generation.
    held: Option<(ObjectId, u64)>,
    gen: u64,
}

impl DuoWc {
    /// Whether the on-screen keyboard is put away now (a tap left the field)
    /// or held back (squeekboard's start-up blink, [`crate::oskstart`]): not
    /// drawn, not touched, no room taken.
    pub fn osk_put_away(&self) -> bool {
        self.osk_away.away.is_some() || self.osk_start.held.is_some()
    }

    /// The client with the keyboard focus (its text input is the one the
    /// input method serves).
    fn keyboard_focus_client(&self) -> Option<ClientId> {
        self.seat.get_keyboard().and_then(|k| k.current_focus()).and_then(|s| s.client()).map(|c| c.id())
    }

    /// A new on-screen keyboard surface (before it is mapped): held back if
    /// it is squeekboard's start-up blink.
    pub fn osk_start_check(&mut self, surface: &WlSurface) {
        let Some(client) = surface.client() else {
            return;
        };
        let Some(data) = client.get_data::<crate::state::ClientState>() else {
            return;
        };
        let first = !data.made_osk.swap(true, Ordering::Relaxed);
        let age = data.connected.map(|t| t.elapsed());
        let field = self.keyboard_focus_client().is_some_and(|c| self.textspy.enabled_of(&c).is_some());
        if !crate::oskstart::is_start_blink(age, first, field) {
            return;
        }
        tracing::info!(
            age_ms = age.map(|a| a.as_millis() as u64),
            "on-screen keyboard at its start with no text field (its start-up blink): held back"
        );
        self.osk_start.gen += 1;
        let gen = self.osk_start.gen;
        self.osk_start.held = Some((surface.id(), gen));
        self.events_dirty = true;
        let _ = self.loop_handle.insert_source(
            smithay::reexports::calloop::timer::Timer::from_duration(std::time::Duration::from_millis(
                crate::oskstart::HOLD_MAX_MS,
            )),
            move |_, _, state: &mut DuoWc| {
                if state.osk_start.held.as_ref().map(|(_, g)| *g) == Some(gen) {
                    state.osk_start_release("still up after the blink: asked for");
                }
                smithay::reexports::calloop::timer::TimeoutAction::Drop
            },
        );
    }

    /// The held-back keyboard shows after all (a text field or the bar's
    /// button asked for it).
    pub fn osk_start_release(&mut self, why: &str) {
        if self.osk_start.held.take().is_some() {
            tracing::info!(why, "on-screen keyboard held back at its start: shown");
            self.events_dirty = true;
            self.place_all();
            self.queue_redraw_all();
        }
    }

    /// A keyboard surface went: if it was the one held back, its blink is
    /// over (the caller re-places and redraws).
    pub fn osk_start_gone(&mut self, surface: &WlSurface) {
        if self.osk_start.held.as_ref().map(|(id, _)| id) == Some(&surface.id()) {
            tracing::info!("the on-screen keyboard's start-up blink over, never shown");
            self.osk_start.held = None;
            self.events_dirty = true;
        }
    }

    /// A text input committed (`changed`: a new enable, cursor or text;
    /// `field`: its state is a text field, a caret or text).
    fn osk_text_input_commit(
        &mut self,
        client: &ClientId,
        enabled: bool,
        changed: bool,
        field: bool,
        terminal: bool,
    ) {
        // A text field with the focus asks for the keyboard: one held back
        // at squeekboard's start shows (the input method is activated).
        if enabled && changed && self.keyboard_focus_client().as_ref() == Some(client) {
            self.osk_start_release("a text field asked for it");
        }
        if !self.osk_away.enabled {
            return;
        }
        if changed && field && self.osk_away.watch.as_ref().map(|(c, _)| c == client) == Some(true) {
            debug!("the app answered the tap: a field (the keyboard stays)");
            self.osk_away.watch = None;
        }
        let away_here = self.osk_away.away.as_ref() == Some(client);
        if enabled && !field && !terminal {
            if !away_here {
                tracing::info!(
                    "text input enabled with no field (no caret, no text): the keyboard stays away"
                );
                self.osk_away.watch = None;
                self.osk_away.away = Some(client.clone());
                self.events_dirty = true;
                self.place_all();
                self.queue_redraw_all();
            }
            return;
        }
        if away_here && (!enabled || (changed && field)) {
            self.osk_back(if enabled { "the app's field again" } else { "the app disabled its text input" });
        }
    }

    /// The keyboard is back (drawn, touched, its room taken again).
    pub fn osk_back(&mut self, why: &str) {
        self.osk_away.watch = None;
        if self.osk_away.away.take().is_some() {
            tracing::info!(why, "on-screen keyboard back");
            self.events_dirty = true;
            self.place_all();
            self.queue_redraw_all();
        }
    }

    /// A tap (down at `start`, global) lifted on `surface`, whose root is a
    /// window: if it may have left the text field, watch for the app's
    /// answer; a tap near the cursor of a put-away keyboard brings it back.
    pub fn osk_tap(
        &mut self,
        surface: &smithay::reexports::wayland_server::protocol::wl_surface::WlSurface,
        start: smithay::utils::Point<f64, smithay::utils::Logical>,
    ) {
        if !self.osk_away.enabled {
            return;
        }
        let Some(client) = surface.client().map(|c| c.id()) else {
            return;
        };
        // The text input is the keyboard focus's (its client's).
        if self.keyboard_focus_client().as_ref() != Some(&client) {
            return;
        }
        let mut root = surface.clone();
        while let Some(parent) = smithay::wayland::compositor::get_parent(&root) {
            root = parent;
        }
        let Some(origin) = self.surface_origin(&root) else {
            return;
        };
        let local = (start.x - origin.x, start.y - origin.y);
        let Some(seen) = self.textspy.enabled_of(&client).cloned() else {
            return;
        };
        let off = crate::oskleave::off_the_cursor(seen.cursor, local);
        if self.osk_away.away.as_ref() == Some(&client) {
            if !off {
                self.osk_back("a tap on the field");
            }
            return;
        }
        let up = self.osk_output().is_some();
        if !crate::oskleave::watch(up, seen.enabled, seen.terminal, off) {
            return;
        }
        self.osk_away.gen += 1;
        let gen = self.osk_away.gen;
        debug!(?local, cursor = ?seen.cursor, "a tap beside the text field: does the app answer?");
        self.osk_away.watch = Some((client.clone(), gen));
        let _ = self.loop_handle.insert_source(
            smithay::reexports::calloop::timer::Timer::from_duration(std::time::Duration::from_millis(
                crate::oskleave::ANSWER_MS,
            )),
            move |_, _, state: &mut DuoWc| {
                state.events_dirty = true;
                let pending =
                    state.osk_away.watch.as_ref().map(|(c, g)| *c == client && *g == gen) == Some(true);
                if pending {
                    state.osk_away.watch = None;
                    if state.osk_output().is_some() && state.textspy.enabled_of(&client).is_some() {
                        tracing::info!(
                            "a tap left the text field and the app said nothing: the keyboard goes away"
                        );
                        state.osk_away.away = Some(client.clone());
                        state.place_all();
                        state.queue_redraw_all();
                    }
                }
                smithay::reexports::calloop::timer::TimeoutAction::Drop
            },
        );
    }
}
