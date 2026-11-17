//! Idle inhibition on the compositor's side: the zwp_idle_inhibit_manager_v1
//! and ext-idle-notify-v1 globals, the shell's idle hold, and which of them
//! keep the screen on now (the rules: [`crate::idle`]).
//!
//! Both globals are public (no trust needed): an inhibitor only works while
//! its own surface is seen, and an idle notification tells a client no more
//! than its frame callbacks stopping would. The shell's hold
//! (`set_idle_hold`) is a privileged request.
//!
//! Who holds is evaluated after every turn of the event loop that did
//! something ([`DuoWc::refresh_idle_inhibit`], cheap: a few windows) and
//! again when the idle timer runs out ([`DuoWc::idle_inhibited_now`]): when
//! the last holder lets go (the inhibitor destroyed, its window covered or
//! set aside), the idle timer counts from then; ext-idle-notify's clients
//! are told whether idle is inhibited.

use std::time::{Duration, Instant};

use smithay::desktop::{find_popup_root_surface, layer_map_for_output, Window, WindowSurfaceType};
use smithay::output::Output;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::{LoopHandle, RegistrationToken};
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::{DisplayHandle, Resource as _};
use smithay::utils::{Logical, Rectangle};
use smithay::wayland::compositor::get_parent;
use smithay::wayland::idle_inhibit::{IdleInhibitHandler, IdleInhibitManagerState};
use smithay::wayland::idle_notify::{IdleNotifierHandler, IdleNotifierState};
use tracing::{debug, info, warn};

use crate::idle::{hold_honored, seen_through, Holder, IdleHold, Inhibitors, Shown};
use crate::state::{app_id_and_title, window_data, DuoWc, Placement};

/// Everything idle inhibition keeps.
pub struct IdleInhibit {
    /// The zwp_idle_inhibit_manager_v1 global; only held.
    #[allow(dead_code)]
    manager: IdleInhibitManagerState,
    /// ext-idle-notify-v1 (its clients' timers are Smithay's).
    pub notifier: IdleNotifierState<DuoWc>,
    /// The surfaces with inhibitors.
    pub surfaces: Inhibitors<WlSurface>,
    /// The shell's hold (org.freedesktop.ScreenSaver's inhibitors).
    pub hold: IdleHold,
    /// The timer that ends the hold if the shell stops renewing it.
    pub hold_timer: Option<RegistrationToken>,
    /// Whether inhibitors and the hold count at all (the config's
    /// `idle_inhibit`, `set_idle_inhibit`).
    pub enabled: bool,
    /// Who keeps the screen on, as last evaluated: app ids (or the hold's
    /// application names), sorted, each once. Empty: nobody.
    pub seen: Vec<String>,
}

impl IdleInhibit {
    /// The two globals, nothing inhibited yet.
    pub fn new(display: &DisplayHandle, loop_handle: LoopHandle<'static, DuoWc>, enabled: bool) -> Self {
        IdleInhibit {
            manager: IdleInhibitManagerState::new::<DuoWc>(display),
            notifier: IdleNotifierState::new(display, loop_handle),
            surfaces: Inhibitors::default(),
            hold: IdleHold::default(),
            hold_timer: None,
            enabled,
            seen: Vec::new(),
        }
    }
}

impl IdleInhibitHandler for DuoWc {
    fn inhibit(&mut self, surface: WlSurface) {
        debug!(surface = ?surface.id(), "idle inhibitor made");
        self.inhibit.surfaces.add(surface);
        self.events_dirty = true;
    }

    fn uninhibit(&mut self, surface: WlSurface) {
        debug!(surface = ?surface.id(), "idle inhibitor destroyed");
        self.inhibit.surfaces.remove(&surface);
        self.events_dirty = true;
    }
}
smithay::delegate_idle_inhibit!(DuoWc);

impl IdleNotifierHandler for DuoWc {
    fn idle_notifier_state(&mut self) -> &mut IdleNotifierState<Self> {
        &mut self.inhibit.notifier
    }
}
smithay::delegate_idle_notify!(DuoWc);

impl DuoWc {
    /// Who keeps the screen on now (see [`crate::idle`]): the app ids of
    /// the seen surfaces with inhibitors, and the shell's holders that
    /// hold, sorted, each once. Empty while inhibition is switched off.
    pub fn idle_inhibitors_now(&self) -> Vec<String> {
        if !self.inhibit.enabled {
            return Vec::new();
        }
        let mut who: Vec<String> = self
            .inhibit
            .surfaces
            .keys()
            .filter(|surface| surface.is_alive())
            .filter_map(|surface| self.surface_seen(surface))
            .collect();
        let holders = self.inhibit.hold.holders();
        if !holders.is_empty() {
            let shown: Vec<Shown> = self
                .space
                .elements()
                .filter(|w| !self.is_accessory(w))
                .map(|w| Shown { pid: self.window_pid(w), seen: self.window_seen(w) })
                .collect();
            who.extend(holders.iter().filter(|h| hold_honored(h.pid, &shown)).map(|h| h.app.clone()));
        }
        who.sort();
        who.dedup();
        who
    }

    /// Whether anything keeps the screen on right now (the idle timer asks
    /// when it runs out).
    pub fn idle_inhibited_now(&self) -> bool {
        !self.idle_inhibitors_now().is_empty()
    }

    /// After a turn of the event loop that did something: forget dead
    /// surfaces, and if who keeps the screen on changed, say so; when the
    /// last one lets go the idle timer counts from now.
    pub fn refresh_idle_inhibit(&mut self) {
        if self.inhibit.surfaces.is_empty()
            && self.inhibit.hold.holders().is_empty()
            && self.inhibit.seen.is_empty()
        {
            return;
        }
        self.inhibit.surfaces.retain(|surface| surface.is_alive());
        let now = self.idle_inhibitors_now();
        if now == self.inhibit.seen {
            return;
        }
        let (was, is) = (!self.inhibit.seen.is_empty(), !now.is_empty());
        if is {
            info!(by = ?now, "idle inhibited: the screen does not blank by itself");
        } else {
            info!("idle no longer inhibited: the idle timer counts from now");
        }
        self.inhibit.seen = now;
        self.events_dirty = true;
        if was != is {
            self.inhibit.notifier.set_is_inhibited(is);
            if !is {
                self.idle.activity(Instant::now());
                if !self.screen_dark() {
                    self.arm_idle_timer();
                }
            }
        }
    }

    /// `set_idle_hold`: the shell's holders (org.freedesktop.ScreenSaver's
    /// inhibitors) for `ttl` unless renewed; none ends the hold.
    pub fn set_idle_hold(&mut self, holders: Vec<Holder>, ttl: Duration) {
        if self.inhibit.hold.set(holders, ttl, Instant::now()) {
            let apps: Vec<&str> = self.inhibit.hold.holders().iter().map(|h| h.app.as_str()).collect();
            info!(holders = ?apps, "the shell's idle hold");
            self.events_dirty = true;
        }
        if let Some(token) = self.inhibit.hold_timer.take() {
            self.loop_handle.remove(token);
        }
        if let Some(deadline) = self.inhibit.hold.deadline() {
            let after = deadline.saturating_duration_since(Instant::now());
            match self.loop_handle.insert_source(Timer::from_duration(after), |_, _, state: &mut DuoWc| {
                state.inhibit.hold_timer = None;
                if state.inhibit.hold.expire(Instant::now()) {
                    info!("the shell's idle hold ran out (not renewed)");
                    state.events_dirty = true;
                }
                TimeoutAction::Drop
            }) {
                Ok(token) => self.inhibit.hold_timer = Some(token),
                Err(err) => warn!(?err, "idle hold timer"),
            }
        }
    }

    /// `set_idle_inhibit`: whether inhibitors and the shell's hold count.
    pub fn set_idle_inhibit_enabled(&mut self, enabled: bool) {
        if self.inhibit.enabled != enabled {
            info!(enabled, "idle inhibition");
            self.inhibit.enabled = enabled;
            self.events_dirty = true;
        }
    }

    /// Whether a surface with an inhibitor is seen now, and whose it is
    /// (the window's app id, a layer surface's namespace).
    fn surface_seen(&self, surface: &WlSurface) -> Option<String> {
        if self.lock.locked || self.ambient.is_some() {
            // Only the lock screen, or only the shell's peek / glance strip.
            return None;
        }
        let mut root = surface.clone();
        while let Some(parent) = get_parent(&root) {
            root = parent;
        }
        // A popup's (or its subsurface's): the window it belongs to.
        if let Some(popup) = self.popups.find_popup(&root) {
            root = find_popup_root_surface(&popup).ok()?;
        }
        if let Some(window) = self.window_for_surface(&root) {
            return self
                .window_seen(&window)
                .then(|| app_id_and_title(&window).0.unwrap_or_else(|| "a window".to_owned()));
        }
        // A layer surface (the shell's): seen on a lit panel.
        self.outputs.iter().filter(|o| self.is_lit(&o.name())).find_map(|output| {
            let map = layer_map_for_output(output);
            let namespace = map
                .layer_for_surface(&root, WindowSurfaceType::TOPLEVEL)
                .map(|layer| layer.namespace().to_owned());
            namespace
        })
    }

    /// Whether a window is seen now: on a lit panel that shows windows (the
    /// overview shows every window of its panels, live), not set aside,
    /// and not covered whole by the windows in front of it there.
    fn window_seen(&self, window: &Window) -> bool {
        if self.lock.locked || self.ambient.is_some() {
            return false;
        }
        for output in &self.outputs {
            if !self.is_lit(&output.name()) || !self.is_on(window, output) {
                continue;
            }
            if self.overview_on(output) {
                return true;
            }
            if self.is_minimized(window) {
                continue;
            }
            let Some(area) = self.window_area_on(window, output) else {
                continue;
            };
            let front: Vec<Rectangle<i32, Logical>> = self
                .space
                .elements()
                .rev()
                .take_while(|w| *w != window)
                .filter(|w| !self.is_minimized(w))
                .filter_map(|w| self.window_area_on(w, output))
                .collect();
            if seen_through(area, &front) {
                return true;
            }
        }
        false
    }

    /// Where a window shows on a panel (global space): its geometry cut to
    /// the panel, and to its half when the panel is split.
    fn window_area_on(&self, window: &Window, output: &Output) -> Option<Rectangle<i32, Logical>> {
        let panel = self.space.output_geometry(output)?;
        if *window_data(window).android_full.borrow() {
            // A full-screen Android window shows on its own panel only
            // (render.rs cuts its caption bar there).
            if let Placement::Output(name) = &*window_data(window).placement.borrow() {
                if *name != output.name() {
                    return None;
                }
            }
        }
        let mut area = self.space.element_geometry(window)?.intersection(panel)?;
        if let Some(tile) = self.split_plan(output).and_then(|plan| plan.tile_of(window_data(window).id)) {
            area = area.intersection(Rectangle::new(tile.loc + panel.loc, tile.size))?;
        }
        Some(area)
    }

    /// The process a window's client runs as (its socket's credentials).
    fn window_pid(&self, window: &Window) -> Option<u32> {
        if window.is_x11() {
            return self.x11_pid(window).and_then(|pid| u32::try_from(pid).ok());
        }
        let toplevel = window.toplevel()?;
        let client = toplevel.wl_surface().client()?;
        let credentials = client.get_credentials(&self.display_handle).ok()?;
        u32::try_from(credentials.pid).ok().filter(|pid| *pid > 0)
    }
}
