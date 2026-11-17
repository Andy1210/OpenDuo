//! Frame pacing for the clients: wp_fifo_v1, wp_commit_timing_v1, the
//! xdg_toplevel `suspended` state, and the slow frames of hidden windows.
//! The decisions are [`crate::pacing`]'s (pure); this is the Smithay side.
//!
//! - **fifo** (Smithay's managed `FifoManagerState`): a content update that
//!   waits on the surface's fifo barrier is held (a blocker) until the
//!   barrier is released. A shown surface's barrier is released when its
//!   pacing output renders, together with its frame callbacks
//!   (`crate::render::send_frames`): one content update a refresh, the
//!   FIFO present mode of Vulkan / EGL without a busy loop. The global is
//!   also what makes SDL 3.2 choose Wayland by itself.
//! - **commit timing** (managed `CommitTimingManagerState`): an update
//!   held for a time is let go by a timer of its own, a refresh before it
//!   ([`crate::pacing::COMMIT_TIMING_LEAD`]): nothing else needs to draw
//!   meanwhile for it to come.
//! - **suspended** (xdg-shell v6): a hidden window ([`crate::pacing::Why`])
//!   has it; GTK 4, Qt 6 and SDL 3 (SDL_EVENT_WINDOW_OCCLUDED) stop
//!   drawing. A client bound at an older xdg_wm_base never hears it
//!   (Smithay filters the states by version).
//! - **the slow tick**: a hidden window gets its frame callbacks and its
//!   fifo barrier released at most once a second, only after it asked
//!   (a commit), so nothing waits forever and nothing spins.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use smithay::desktop::Window;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::{Client, Resource as _};
use smithay::utils::{Clock, Monotonic, Time};
use smithay::wayland::commit_timing::{CommitTimerBarrierStateUserData, CommitTimerStateUserData};
use smithay::wayland::compositor::{
    with_states, Barrier, CompositorHandler as _, SurfaceAttributes, SurfaceData,
};
use smithay::wayland::fifo::FifoBarrierCachedState;
use smithay::wayland::seat::WaylandFocus as _;
use tracing::{debug, info};

use crate::pacing::{self, Why};
use crate::state::{app_id_and_title, window_data, DuoWc, Placement};

impl pacing::FifoBarrier for Barrier {
    fn released(&self) -> bool {
        self.is_signaled()
    }
    fn release(&self) {
        self.signal()
    }
}

/// When a hidden window last got its slow frame (on its root surface);
/// None since it was shown.
#[derive(Default)]
struct SlowMark(Mutex<Option<Instant>>);

fn slow_mark(states: &SurfaceData) -> Option<Instant> {
    states.data_map.get::<SlowMark>().and_then(|m| *m.0.lock().unwrap())
}

fn set_slow_mark(states: &SurfaceData, at: Option<Instant>) {
    states.data_map.insert_if_missing_threadsafe(SlowMark::default);
    if let Some(mark) = states.data_map.get::<SlowMark>() {
        *mark.0.lock().unwrap() = at;
    }
}

/// The runtime side of pacing, kept in the state.
#[derive(Default)]
pub struct Pacing {
    /// The windows hidden (and suspended) since the last refresh, by id.
    pub hidden: std::collections::BTreeMap<u64, Why>,
    /// The slow tick's time.
    pub tick: pacing::Tick,
    /// Its timer.
    pub timer: Option<smithay::reexports::calloop::RegistrationToken>,
}

/// Release a surface's fifo barrier (its content update shown, or let go
/// for a hidden one). -> its client, when an update of it was held.
pub fn release_fifo(surface: &WlSurface, states: &SurfaceData) -> Option<Client> {
    let released =
        pacing::release(&mut states.cached_state.get::<FifoBarrierCachedState>().current().barrier);
    released.then(|| surface.client()).flatten()
}

/// Whether a surface waits for something the slow tick gives: frame
/// callbacks, or a fifo barrier to be released.
fn waits(states: &SurfaceData) -> bool {
    !states.cached_state.get::<SurfaceAttributes>().current().frame_callbacks.is_empty()
        || states
            .cached_state
            .get::<FifoBarrierCachedState>()
            .current()
            .barrier
            .as_ref()
            .is_some_and(|b| !b.is_signaled())
}

/// The presentation clock (CLOCK_MONOTONIC, wp_presentation's) now.
fn mono_now() -> Time<Monotonic> {
    Clock::<Monotonic>::new().now()
}

impl DuoWc {
    /// The scene as the pacing rules see it.
    pub fn pacing_scene(&self) -> pacing::Scene {
        let outputs: Vec<(String, bool)> =
            self.layout_order().iter().map(|o| (o.name(), self.is_lit(&o.name()))).collect();
        // A carried or flying window is drawn where its spring is: no
        // fullscreen window covers anything meanwhile.
        let carrying = self.carry.drag.is_some() || !self.carry.anims.is_empty();
        let windows = self
            .space
            .elements()
            .map(|window| {
                let data = window_data(window);
                let bbox = self.space.element_bbox(window);
                let mut shows_on = Vec::new();
                let mut covers = Vec::new();
                let full = *data.fullscreen.borrow()
                    && *data.opened.borrow()
                    && !*data.minimized.borrow()
                    && !carrying
                    && data.fit.borrow().map(|f| f.factor >= 1.0).unwrap_or(true);
                for output in &self.outputs {
                    let (Some(g), Some(b)) = (self.space.output_geometry(output), bbox) else {
                        continue;
                    };
                    if !b.overlaps(g) || !crate::render::shown_on(&self.space, window, output) {
                        continue;
                    }
                    shows_on.push(output.name());
                    if full && b.contains_rect(g) {
                        covers.push(output.name());
                    }
                }
                let on = match &*data.placement.borrow() {
                    Placement::Output(name) => Some(name.clone()),
                    Placement::Span => None,
                };
                pacing::Win { id: data.id, on, shows_on, minimized: *data.minimized.borrow(), covers }
            })
            .collect();
        pacing::Scene {
            windows,
            outputs,
            overview: self.taskview.open,
            ambient: self.ambient.is_some(),
            locked: self.lock.locked,
        }
    }

    /// Who is paced by which output, who is hidden.
    pub fn pacing_decide(&self) -> std::collections::BTreeMap<u64, pacing::Pace> {
        pacing::decide(&self.pacing_scene())
    }

    /// Once a turn of the event loop: tell the windows that became hidden
    /// or shown (`suspended`), and serve the newly hidden ones' waits.
    pub fn pacing_refresh(&mut self) {
        let decisions = self.pacing_decide();
        let mut shown_again = false;
        let mut newly_hidden = false;
        for window in self.space.elements() {
            let id = window_data(window).id;
            let hidden = decisions.get(&id).and_then(|p| p.hidden);
            let was = self.pacing.hidden.get(&id).copied();
            if let Some(toplevel) = window.toplevel() {
                let want = hidden.is_some();
                let changed = toplevel.with_pending_state(|state| {
                    let has = state.states.contains(xdg_toplevel::State::Suspended);
                    if has != want {
                        if want {
                            state.states.set(xdg_toplevel::State::Suspended);
                        } else {
                            state.states.unset(xdg_toplevel::State::Suspended);
                        }
                    }
                    has != want
                });
                // Before its first configure the state rides on it.
                if changed && toplevel.is_initial_configure_sent() {
                    toplevel.send_pending_configure();
                }
            }
            if was != hidden {
                let (app_id, _) = app_id_and_title(window);
                match hidden {
                    Some(why) => info!(window = id, app_id, why = why.name(), "window hidden: suspended"),
                    None => info!(window = id, app_id, "window shown again"),
                }
            }
            match (was, hidden) {
                (None, Some(_)) => newly_hidden = true,
                (Some(_), None) => {
                    shown_again = true;
                    if let Some(root) = window.wl_surface() {
                        with_states(&root, |states| set_slow_mark(states, None));
                    }
                }
                _ => {}
            }
        }
        self.pacing.hidden =
            decisions.into_iter().filter_map(|(id, p)| p.hidden.map(|why| (id, why))).collect();
        if shown_again {
            // Its next frame callbacks and barrier come with a render.
            self.queue_redraw_all();
        }
        if newly_hidden {
            self.pacing_arm(Instant::now());
        }
    }

    /// A hidden window committed (it may wait for a frame now): its slow
    /// frame is due a period after its last one.
    pub fn pacing_committed(&mut self, window: &Window) {
        if !self.pacing.hidden.contains_key(&window_data(window).id) {
            return;
        }
        let Some(root) = window.wl_surface() else {
            return;
        };
        let last = with_states(&root, slow_mark);
        self.pacing_arm(pacing::due(last, Instant::now()));
    }

    /// The slow tick is to run at `at` (or earlier).
    pub fn pacing_arm(&mut self, at: Instant) {
        let Some(at) = self.pacing.tick.want(at) else {
            return;
        };
        if let Some(token) = self.pacing.timer.take() {
            self.loop_handle.remove(token);
        }
        match self.loop_handle.insert_source(Timer::from_deadline(at), |_, _, state: &mut DuoWc| {
            state.pacing.timer = None;
            state.pacing_tick();
            TimeoutAction::Drop
        }) {
            Ok(token) => self.pacing.timer = Some(token),
            Err(err) => {
                self.pacing.tick.fired();
                tracing::warn!(?err, "no slow frame timer");
            }
        }
    }

    /// The slow tick: every hidden window that waits and is due gets its
    /// frame callbacks and its fifo barrier released; the tick comes again
    /// for those that wait but are not due yet.
    fn pacing_tick(&mut self) {
        self.pacing.tick.fired();
        let now = Instant::now();
        let decisions = self.pacing_decide();
        let Some(output) = self.outputs.first().cloned() else {
            return;
        };
        let time = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        let mut held = Vec::new();
        let mut next: Option<Instant> = None;
        let mut served = 0;
        for window in self.space.elements() {
            if decisions.get(&window_data(window).id).and_then(|p| p.hidden).is_none() {
                continue;
            }
            let Some(root) = window.wl_surface() else {
                continue;
            };
            let mut waiting = false;
            window.with_surfaces(|_, states| waiting |= waits(states));
            if !waiting {
                continue;
            }
            let at = pacing::due(with_states(&root, slow_mark), now);
            if at > now {
                next = Some(next.map_or(at, |n| n.min(at)));
                continue;
            }
            // Its frame callbacks (`throttle` None: all of them, whatever
            // output is named).
            window.send_frame(&output, time, None, |_, _| Some(output.clone()));
            window.with_surfaces(|surface, states| {
                if let Some(client) = release_fifo(surface, states) {
                    held.push(client);
                }
            });
            with_states(&root, |states| set_slow_mark(states, Some(now)));
            served += 1;
        }
        if served > 0 {
            debug!(served, "slow frames for hidden windows");
        }
        self.blockers_cleared(held);
        if let Some(at) = next {
            self.pacing_arm(at);
        }
    }

    /// Clients whose held content updates may go now (a fifo barrier or a
    /// commit timer released): Smithay applies the ready ones (each runs
    /// the commit handler).
    pub fn blockers_cleared(&mut self, clients: Vec<Client>) {
        // A ClientId hashes by identity, not by anything mutable in it.
        #[allow(clippy::mutable_key_type)]
        let mut seen = std::collections::HashSet::new();
        let dh = self.display_handle.clone();
        for client in clients {
            if seen.insert(client.id()) {
                self.client_compositor_state(&client).blocker_cleared(self, &dh);
            }
        }
    }

    /// A content update with a commit timer's target time
    /// (wp_commit_timing_v1) came: let it go a refresh before that time.
    /// Called from a pre-commit hook every surface has (`new_surface`),
    /// which runs before Smithay's own (that takes the timestamp).
    pub fn commit_timer_requested(&mut self, surface: &WlSurface) {
        let target = with_states(surface, |states| {
            states.data_map.get::<CommitTimerStateUserData>().and_then(|s| s.borrow().timestamp)
        });
        let Some(target) = target else {
            return;
        };
        let target = Duration::from_micros(Time::<Monotonic>::from(target).as_micros());
        let now = Duration::from_micros(mono_now().as_micros());
        let wait = pacing::commit_timer_wait(target, now);
        let surface = surface.clone();
        let timer = Timer::from_duration(wait);
        let inserted = self.loop_handle.insert_source(timer, move |_, _, state: &mut DuoWc| {
            if surface.is_alive() {
                let deadline = mono_now() + pacing::COMMIT_TIMING_LEAD;
                let released = with_states(&surface, |states| {
                    states
                        .data_map
                        .get::<CommitTimerBarrierStateUserData>()
                        .is_some_and(|barriers| barriers.lock().unwrap().signal_until(deadline))
                });
                if released {
                    if let Some(client) = surface.client() {
                        state.blockers_cleared(vec![client]);
                    }
                }
            }
            TimeoutAction::Drop
        });
        if let Err(err) = inserted {
            tracing::warn!(?err, "no commit timer");
        }
    }
}
