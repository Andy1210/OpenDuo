//! The one canvas, live: carrying a window by its top to a snap target
//! ([`crate::snap`]), across the hinge ([`crate::stitch`]), double-tapping
//! its top to spread it over both panels and back, sending a panel's
//! windows home, and the animations all of it moves with ([`crate::spring`]).
//!
//! **Picking a window up.** A window with a title bar of its own (GTK and
//! libadwaita header bars, Chromium's and Firefox's tab strips) asks for it:
//! dragging the bar sends `xdg_toplevel.move`, and duo-wc takes the touch
//! point over (the client gets its lift). A window without one (a terminal
//! with no decorations, anything fullscreen) has the compositor's gesture
//! instead: **touch and hold the top [`TOP_STRIP`] px of the window** for
//! [`LIFT_MS`]; the client got the touch at once (a tap there is a tap), and
//! is told it was cancelled when the window lifts. Header bars keep their
//! buttons: the compositor's gesture is only for windows that have no bar.
//!
//! **Carried**, the window shrinks to [`GHOST_SCALE`] under the finger with a
//! shadow, and the target it would go to shows as a see-through accent
//! rectangle on the panel it would land on: a panel, a half of one, or both
//! (the span pad on the hinge, drawn while carrying). At the hinge the
//! target is the OTHER panel, and the picture goes over there ahead of the
//! finger (the finger cannot follow: the two touchscreens are separate).
//! Let go, it flies there on a spring, from the lift on: the picture's own
//! spring goes on to the window's new place (at its speed, nothing waits).
//! Across the hinge the finger lifts on one panel and may come down on the
//! other: one drag. A window let go at the hinge is put down at once (on the
//! other panel, where its picture already is) and flies there; a finger
//! coming down at the other panel's hinge edge within the stitch window
//! picks it up again from where its flight has got to ([`crate::stitch`],
//! a [`Landing`]). Until 2026-11-10 the drop waited for that window (350 ms)
//! and the window grew into its place only then.
//!
//! **Double-tap the top** of a window: one panel <-> both panels. Clients
//! with their own title bar ask for it themselves (a double-click on a
//! header bar is "toggle maximized", which duo-wc reads as this), the rest
//! get it from the top strip like the hold.
//!
//! **Home**: a long swipe up from a panel's bottom edge puts the panel's
//! windows aside (minimized: not drawn, not touched, still in the overview
//! and the dock), and the shell's home screen shows there. Activating one
//! (the dock, the overview, a launcher's token) brings it back.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use smithay::desktop::Window;
use smithay::input::touch::UpEvent;
use smithay::output::Output;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::RegistrationToken;
use smithay::reexports::wayland_protocols::xdg::decoration::zv1::server::zxdg_toplevel_decoration_v1::Mode as DecorationMode;
use smithay::utils::{Logical, Point, Rectangle, SERIAL_COUNTER};
use tracing::{debug, info, warn};

use crate::fx::BackArrow;
use crate::overview::Rect;
use crate::snap::{self, Target};
use crate::spring::{Params, RectSpring, Spring};
use crate::state::{window_data, DuoWc, Placement};
use crate::stitch::{self, Contact};

/// The top strip of a window without a title bar of its own that a
/// touch-and-hold picks it up by (logical px).
pub const TOP_STRIP: f64 = 28.0;
/// How long a finger must rest on the strip to pick the window up.
pub const LIFT_MS: u64 = 380;
/// How far it may wander meanwhile.
pub const LIFT_SLOP: f64 = 10.0;
/// A tap: up within this long...
pub const TAP_MS: u32 = 280;
/// ... and the second tap's lift within this long of the first's.
pub const DOUBLE_TAP_MS: u32 = 420;
/// How far apart two taps of a double tap may be.
pub const DOUBLE_TAP_SLOP: f64 = 40.0;
/// A carried window's size against its own.
pub const GHOST_SCALE: f64 = 0.55;
/// How far into the other panel a picture going across is shown (px).
pub const LEAD_MARGIN: f64 = 16.0;
/// After a span toggle, the other way of asking for one is ignored this
/// long (a header bar's double-click and the compositor's both saw it).
const TOGGLE_GUARD: Duration = Duration::from_millis(700);
/// A client's "unmaximize" is a span toggle only if the window was touched
/// this recently (a double-tap on its title bar), and not in its first
/// seconds (an app restoring its own state at start).
const TOUCH_RECENT: Duration = Duration::from_millis(900);
const STARTUP_GRACE: Duration = Duration::from_secs(3);

/// How long a client's `unset_maximized` waits before it is the span
/// toggle: a `set_fullscreen` sent with it (Qt) comes within this.
const TOGGLE_SETTLE: Duration = Duration::from_millis(40);

/// Whether a client's `unset_maximized` may be a double tap on its title
/// bar (the span toggle): right after a touch on the window, not while it
/// is opening, and not while it is full screen (Qt leaving or entering full
/// screen changes its maximized state too).
pub fn unmaximize_is_toggle(recent_touch: bool, young: bool, fullscreen: bool) -> bool {
    recent_touch && !young && !fullscreen
}

/// A window carried by a finger.
pub struct WindowDrag {
    /// The touch point carrying it now (another one after a stitch).
    pub slot: u32,
    /// The window.
    pub window: Window,
    /// Where the finger holds it: the share across its width and the px
    /// below its top, at its own size.
    grip: (f64, f64),
    /// Its size when it was picked up.
    size: (f64, f64),
    /// The finger (global).
    pub at: Point<f64, Logical>,
    /// The panel the finger is on.
    pub output: String,
    /// Where it would go now.
    pub target: Option<Target>,
    /// The picture under the finger (global).
    ghost: RectSpring,
    /// The target's preview (global), sliding from target to target.
    preview: Option<RectSpring>,
    /// The panel the finger has just come to across the hinge (its band is
    /// that panel until the finger leaves it, [`snap::Context::arrived`]).
    arrived: Option<String>,
}

/// A window let go at the hinge: already put down and flying to its place,
/// but for the stitch window ([`stitch::Config::window_ms`]) a finger coming
/// down at the other panel's hinge edge picks it up again
/// ([`stitch::Lift::DownOpen`]).
pub struct Landing {
    window: Window,
    /// The carry's grip and size (the picture under the new finger is the
    /// one it had).
    grip: (f64, f64),
    size: (f64, f64),
    /// Where and when the finger lifted.
    lift: Contact,
    timer: Option<RegistrationToken>,
    gen: u64,
}

/// A finger resting on a bar-less window's top strip.
#[derive(Debug, Clone)]
pub struct TopHold {
    window: Window,
    start: Point<f64, Logical>,
    time: u32,
    gen: u64,
    moved: bool,
}

/// The last quick tap on a top strip (for a double tap).
#[derive(Debug, Clone, Copy)]
struct TopTap {
    id: u64,
    at: (f64, f64),
    time: u32,
}

/// A window drawn somewhere else than where it is, for a while.
pub struct Anim {
    rect: RectSpring,
    alpha: Spring,
}

/// Everything about carrying windows and animating them.
#[derive(Default)]
pub struct Carry {
    /// The window being carried.
    pub drag: Option<WindowDrag>,
    /// The window let go at the hinge, open for the other side a moment.
    pub landing: Option<Landing>,
    /// Touch points a stitch took the drag away from: the rest of them is
    /// ignored.
    pub ignored: HashSet<u32>,
    /// Fingers resting on top strips, by slot.
    pub holds: HashMap<u32, TopHold>,
    last_tap: Option<TopTap>,
    /// Windows drawn where an animation puts them, by id.
    pub anims: HashMap<u64, Anim>,
    /// When a span toggle last happened.
    toggled_at: Option<Instant>,
    /// The window touched last, and when.
    pub last_touch: Option<(u64, Instant)>,
    gen: u64,
    /// The back arrow while a side swipe goes on.
    pub back: Option<BackArrow>,
    /// What the effects draw with.
    pub gfx: crate::fx::Gfx,
    /// The snap thresholds.
    pub snap: snap::Config,
    /// The stitch thresholds.
    pub stitch: stitch::Config,
    /// Windows animate (open, drop, home); off: they just appear.
    pub animate: bool,
    /// The frame timing of the last drop's flight, per panel, from the
    /// lift (crate::turnlog: `drop: frame timing`).
    pub flight_logs: crate::turnlog::TurnLogs,
    /// The window whose flight is timed.
    timed_flight: Option<u64>,
}

/// What the renderer needs of the carrying on one output, in its frame.
pub struct CarryPlan {
    /// The carried window, its picture's rectangle and the preview's.
    pub ghost: Option<(Window, Rect)>,
    /// The target's preview.
    pub preview: Option<Rect>,
    /// The span pad (this panel's frame) while something is carried, and
    /// whether it is the target now.
    pub pad: Option<(Rect, bool)>,
    /// The on-screen keyboard is put away (crate::oskleave): not drawn.
    pub hide_osk: bool,
    /// Windows drawn elsewhere: id -> (rectangle, alpha).
    pub moved: HashMap<u64, (Rect, f32)>,
    /// Windows not drawn at all (the carried one, minimized ones at rest).
    pub hidden: HashSet<u64>,
    /// The back arrow.
    pub back: Option<BackArrow>,
    /// The panel's width (for the arrow).
    pub panel_w: f64,
}

impl Carry {
    /// Nothing carried yet, with these thresholds.
    pub fn new(snap: snap::Config, stitch: stitch::Config, animate: bool) -> Self {
        Carry { snap, stitch, animate, ..Default::default() }
    }
}

fn rect_of(r: Rectangle<i32, Logical>) -> Rect {
    Rect::new(r.loc.x as f64, r.loc.y as f64, r.size.w as f64, r.size.h as f64)
}

fn tuple(r: Rect) -> (f64, f64, f64, f64) {
    (r.x, r.y, r.w, r.h)
}

fn untuple(t: (f64, f64, f64, f64)) -> Rect {
    Rect::new(t.0, t.1, t.2, t.3)
}

/// One panel's line of a drop's frame timing.
fn log_flight(output: &str, log: &crate::turnlog::TurnLog, s: &crate::turnlog::Summary) {
    info!(
        output,
        frames = s.frames,
        first_ms = format!("{:.1}", s.first_ms),
        max_gap_ms = format!("{:.1}", s.max_gap_ms),
        late = s.late,
        missed_vblanks = s.missed_vblanks,
        busy = log.busy,
        max_render_ms = format!("{:.1}", s.max_render_ms),
        mean_render_ms = format!("{:.1}", s.mean_render_ms),
        ahead = log.ahead,
        gaps = ?s.gaps,
        "drop: frame timing"
    );
}

/// Whether a window has no title bar of its own: it took the compositor's
/// (empty) decorations, or it is fullscreen.
pub fn barless(window: &Window) -> bool {
    if *window_data(window).fullscreen.borrow() {
        return true;
    }
    if let Some(x11) = window.x11_surface() {
        // X11 windows get no frame from duo-wc: barless unless the app
        // draws its own (Motif hints, crate::xwayland).
        return !x11.is_decorated();
    }
    window
        .toplevel()
        .map(|t| t.with_pending_state(|s| s.decoration_mode == Some(DecorationMode::ServerSide)))
        .unwrap_or(false)
}

impl DuoWc {
    /// Whether a window is set aside (home).
    pub fn is_minimized(&self, window: &Window) -> bool {
        *window_data(window).minimized.borrow()
    }

    /// The panels a window can be carried to, left to right (or top to
    /// bottom), for the snapping.
    pub fn snap_panels(&self, carried: u64) -> Vec<snap::Panel> {
        self.layout_order()
            .into_iter()
            .filter(|o| self.pose.in_use(&o.name()) && self.is_lit(&o.name()))
            .filter_map(|o| {
                let g = self.space.output_geometry(&o)?;
                let mut zone = self.window_zone(&o);
                zone.loc += g.loc;
                let name = o.name();
                let can_split = self.space.elements().any(|w| {
                    window_data(w).id != carried
                        && !self.is_minimized(w)
                        && !self.is_accessory(w)
                        && *window_data(w).placement.borrow() == Placement::Output(name.clone())
                });
                Some(snap::Panel { name, rect: rect_of(g), zone: rect_of(zone), can_split })
            })
            .collect()
    }

    /// The room a window spanning both panels gets (global), if two are in use.
    pub fn span_zone(&self) -> Option<Rect> {
        if self.snap_panels(u64::MAX).len() < 2 {
            return None;
        }
        self.placement_rect(&Placement::Span, false).map(rect_of)
    }

    /// The rectangle a window has now (global): its half, its placement's;
    /// on a desk its own frame.
    pub fn window_rect(&self, window: &Window) -> Option<Rect> {
        if let Some(shown) = self.desk_rect(window) {
            // A desk's window floats in its own frame (crate::desk): its
            // picture is there, not over its panel's whole room (it grew in
            // over the whole panel and then jumped, crate::desk::picture_rect).
            let size = window.geometry().size;
            let caught_up = crate::state::drew_as_asked(window);
            let (x, y, w, h) = crate::desk::picture_rect(
                (shown.loc.x, shown.loc.y, shown.size.w, shown.size.h),
                (size.w, size.h),
                caught_up,
            );
            return Some(Rect::new(x as f64, y as f64, w as f64, h as f64));
        }
        let fullscreen = *window_data(window).fullscreen.borrow();
        let tile = if fullscreen { None } else { self.tile_rect(window) };
        if tile.is_none() {
            // A dialog centred, a window scaled into its panel
            // (crate::dialog): where its picture is.
            if let Some(fit) = *window_data(window).fit.borrow() {
                let size = window.geometry().size;
                if size.w > 0 && size.h > 0 {
                    let (x, y, w, h) = fit.shown((size.w, size.h));
                    return Some(Rect::new(x, y, w, h));
                }
            }
        }
        tile.or_else(|| self.placement_rect(&window_data(window).placement.borrow(), fullscreen)).map(rect_of)
    }

    fn ghost_rect(drag: &WindowDrag) -> Rect {
        let (w, h) = (drag.size.0 * GHOST_SCALE, drag.size.1 * GHOST_SCALE);
        Rect::new(drag.at.x - drag.grip.0 * w, drag.at.y - drag.grip.1 * GHOST_SCALE, w, h)
    }

    /// Pick a window up by the touch point `slot` at `at` (global). The
    /// client is no longer sent the touch point (the caller ended it there).
    pub fn drag_begin(&mut self, slot: u32, window: &Window, at: Point<f64, Logical>, output: &str) {
        if self.on_desk(window) {
            // A desk's window (laptop mode's desk panel) floats: it moves
            // with the finger, no snap targets (crate::desk).
            self.desk_touch_drag_begin(slot, window, (at.x, at.y));
            return;
        }
        if self.lock.locked || self.taskview.open || self.carry.drag.is_some() {
            return;
        }
        let Some(rect) = self.window_rect(window) else {
            return;
        };
        if rect.w < 1.0 || rect.h < 1.0 {
            return;
        }
        let id = window_data(window).id;
        info!(id, output, "window picked up");
        self.landing_forget();
        let now = Instant::now();
        let mut drag = WindowDrag {
            slot,
            window: window.clone(),
            grip: (((at.x - rect.x) / rect.w).clamp(0.0, 1.0), (at.y - rect.y).clamp(0.0, rect.h)),
            size: (rect.w, rect.h),
            at,
            output: output.to_owned(),
            target: None,
            ghost: RectSpring::new(tuple(rect), tuple(rect), now, Params::SNAPPY),
            preview: None,
            arrived: None,
        };
        drag.ghost.retarget(tuple(Self::ghost_rect(&drag)), now);
        self.carry.anims.remove(&id);
        self.carry.drag = Some(drag);
        self.drag_retarget();
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// Work out the target under the finger again, slide the preview, and
    /// put the picture under the finger (or across the hinge, ahead of it,
    /// when the target is the other panel).
    fn drag_retarget(&mut self) {
        let Some(id) = self.carry.drag.as_ref().map(|d| window_data(&d.window).id) else {
            return;
        };
        let panels = self.snap_panels(id);
        let rects: Vec<Rect> = panels.iter().map(|p| p.rect).collect();
        let span = self.span_zone();
        let now = Instant::now();
        let cfg = self.carry.snap;
        let Some(drag) = self.carry.drag.as_mut() else {
            return;
        };
        if drag.arrived.is_some() && !snap::in_hinge_band(&rects, drag.at.x, drag.at.y, &cfg) {
            drag.arrived = None;
        }
        let finger = panels.iter().position(|p| p.name == drag.output);
        let arrived = drag.arrived.as_ref().and_then(|n| panels.iter().position(|p| p.name == *n));
        let ctx = snap::Context { allow_top: false, arrived };
        let target = snap::target(&panels, drag.at.x, drag.at.y, ctx, &cfg);
        let target = match target {
            Some(Target::Span) if span.is_none() => None,
            t => t,
        };
        let mut ghost = Self::ghost_rect(drag);
        if let (Some(Target::Panel(name)), Some(from)) = (&target, finger) {
            if *name != drag.output {
                ghost = snap::lead_across(&rects, from, ghost, LEAD_MARGIN);
            }
        }
        drag.ghost.retarget(tuple(ghost), now);
        if target != drag.target {
            debug!(target = target.as_ref().map(|t| t.describe()), "carried window over a new target");
            drag.target = target;
            let to = drag.target.as_ref().and_then(|t| snap::preview(&panels, t, span));
            match (to, drag.preview.as_mut()) {
                (Some(to), Some(preview)) => preview.retarget(tuple(to), now),
                (Some(to), None) => {
                    // Grows out of the carried picture.
                    let from = drag.ghost.value(now);
                    drag.preview = Some(RectSpring::new(from, tuple(to), now, Params::SNAPPY));
                }
                (None, _) => drag.preview = None,
            }
        }
    }

    /// The carried window's finger moved; true if it is the carrying one
    /// (or one a stitch took it from).
    pub fn drag_motion(&mut self, slot: u32, at: Point<f64, Logical>, output: &str) -> bool {
        if self.carry.ignored.contains(&slot) {
            return true;
        }
        let Some(drag) = self.carry.drag.as_mut().filter(|d| d.slot == slot) else {
            return false;
        };
        drag.at = at;
        drag.output = output.to_owned();
        self.drag_retarget();
        self.note_activity();
        self.queue_redraw_all();
        true
    }

    /// The panels' rectangles and the hinge, for the stitching, with the
    /// index of each panel by name.
    fn stitch_frame(&self) -> Option<(snap::Hinge, Vec<Rect>, Vec<String>)> {
        let panels = self.snap_panels(u64::MAX);
        if panels.len() < 2 {
            return None;
        }
        let rects: Vec<Rect> = panels.iter().map(|p| p.rect).collect();
        let names = panels.iter().map(|p| p.name.clone()).collect();
        Some((snap::hinge(&rects)?, rects, names))
    }

    /// The carrying finger lifted; true if it was the carrying one. The
    /// window is put down at once; let go at the hinge, the other side may
    /// pick it up again a moment ([`Landing`]).
    pub fn drag_up(&mut self, slot: u32, time: u32) -> bool {
        if self.carry.ignored.remove(&slot) {
            return true;
        }
        let Some(drag) = self.carry.drag.as_ref().filter(|d| d.slot == slot) else {
            return false;
        };
        let (at, output) = ((drag.at.x, drag.at.y), drag.output.clone());
        let (window, grip, size) = (drag.window.clone(), drag.grip, drag.size);
        let on_pad = drag.target == Some(Target::Span);
        let cfg = self.carry.stitch;
        let lift = match self.stitch_frame() {
            Some((hinge, rects, names)) => {
                let index = names.iter().position(|n| *n == output);
                stitch::lift(Some(&hinge), &rects, index, at, time, on_pad, &cfg)
            }
            None => stitch::Lift::Down,
        };
        match lift {
            stitch::Lift::Down if on_pad => self.drag_drop("let go on the span pad"),
            stitch::Lift::Down => self.drag_drop("let go"),
            stitch::Lift::DownOpen(contact) => {
                self.drag_drop("let go at the hinge");
                self.landing_open(window, grip, size, contact);
            }
        }
        true
    }

    /// A window let go at the hinge is open for the other side (a finger
    /// coming down there picks it up again, [`Self::drag_stitch`]) for the
    /// stitch window.
    fn landing_open(&mut self, window: Window, grip: (f64, f64), size: (f64, f64), lift: Contact) {
        self.landing_forget();
        if !self.space.elements().any(|w| *w == window) {
            return;
        }
        self.carry.gen += 1;
        let gen = self.carry.gen;
        let after = Duration::from_millis(self.carry.stitch.window_ms as u64 + 10);
        let timer = self
            .loop_handle
            .insert_source(Timer::from_duration(after), move |_, _, state: &mut DuoWc| {
                if state.carry.landing.as_ref().map(|l| l.gen == gen) == Some(true) {
                    debug!("nothing came on the other side: the drop stands");
                    state.carry.landing = None;
                }
                TimeoutAction::Drop
            })
            .ok();
        debug!(id = window_data(&window).id, "let go at the hinge: put down, open for the other side");
        self.carry.landing = Some(Landing { window, grip, size, lift, timer, gen });
    }

    /// No window let go at the hinge waits for the other side any more.
    pub fn landing_forget(&mut self) {
        if let Some(landing) = self.carry.landing.take() {
            if let Some(timer) = landing.timer {
                self.loop_handle.remove(timer);
            }
        }
    }

    /// A finger came down at `at` (global) on `output`: if it continues the
    /// carry across the hinge, it takes it over (true; nothing of it goes
    /// to a client). Either the carrying finger is still down at its edge
    /// (a finger on both edges at once), or it lifted there a moment ago
    /// and the window it let go is picked up again.
    pub fn drag_stitch(&mut self, slot: u32, output: &str, at: Point<f64, Logical>, time: u32) -> bool {
        if self.carry.drag.is_none() && self.carry.landing.is_none() {
            return false;
        }
        let Some((hinge, rects, names)) = self.stitch_frame() else {
            return false;
        };
        let Some(down_panel) = names.iter().position(|n| n == output) else {
            return false;
        };
        let down = Contact { panel: down_panel, at: (at.x, at.y), time };
        let cfg = self.carry.stitch;
        if let Some(drag) = self.carry.drag.as_ref() {
            let Some(panel) = names.iter().position(|n| *n == drag.output) else {
                return false;
            };
            let contact = Contact { panel, at: (drag.at.x, drag.at.y), time };
            if !stitch::continues(&hinge, &rects, &contact, false, &down, &cfg) {
                return false;
            }
            info!(from = names[panel], to = output, lifted = false, "carried window crosses the hinge");
            let old_slot = drag.slot;
            self.carry.ignored.insert(old_slot);
            if let Some(drag) = self.carry.drag.as_mut() {
                drag.slot = slot;
                drag.arrived = Some(output.to_owned());
            }
            self.drag_motion(slot, at, output);
            return true;
        }
        let Some(landing) = self.carry.landing.as_ref() else {
            return false;
        };
        if !stitch::continues(&hinge, &rects, &landing.lift, true, &down, &cfg) {
            return false;
        }
        let from = names[landing.lift.panel].clone();
        let window = landing.window.clone();
        if self.lock.locked
            || self.taskview.open
            || !self.space.elements().any(|w| *w == window)
            || self.is_minimized(&window)
        {
            self.landing_forget();
            return false;
        }
        info!(from, to = output, lifted = true, "carried window crosses the hinge: picked up again");
        self.landing_pick_up(slot, at, output);
        true
    }

    /// The window let go at the hinge, picked up again by the finger `slot`
    /// on `output` at `at`: its picture goes from where its flight has got
    /// to (at its speed) back under the finger.
    fn landing_pick_up(&mut self, slot: u32, at: Point<f64, Logical>, output: &str) {
        let Some(landing) = self.carry.landing.take() else {
            return;
        };
        if let Some(timer) = landing.timer {
            self.loop_handle.remove(timer);
        }
        let id = window_data(&landing.window).id;
        let now = Instant::now();
        let ghost = match self.carry.anims.remove(&id) {
            Some(anim) => anim.rect,
            // Already in its place (or animations off): from there.
            None => match self.window_rect(&landing.window) {
                Some(r) => RectSpring::new(tuple(r), tuple(r), now, Params::SNAPPY),
                None => return,
            },
        };
        self.carry.drag = Some(WindowDrag {
            slot,
            window: landing.window,
            grip: landing.grip,
            size: landing.size,
            at,
            output: output.to_owned(),
            target: None,
            ghost,
            preview: None,
            arrived: Some(output.to_owned()),
        });
        self.drag_retarget();
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// Put the carried window where its target says, flying there from
    /// where its picture is, from this frame on.
    pub fn drag_drop(&mut self, reason: &str) {
        let Some(drag) = self.carry.drag.take() else {
            return;
        };
        let window = drag.window.clone();
        let id = window_data(&window).id;
        if !self.space.elements().any(|w| *w == window) {
            self.queue_redraw_all();
            return;
        }
        let target = drag.target.clone();
        info!(id, reason, target = target.as_ref().map(|t| t.describe()), "carried window dropped");
        self.carry.gfx.forget("drag/");
        self.apply_target(&window, target.as_ref());
        self.fly_on(&window, drag.ghost);
        self.flight_timing_start(id);
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// A window let go flies to where it is now on the spring its picture
    /// was on (from its place, at its speed: no jump, no restart from rest).
    /// The client was configured for the new place already; until its new
    /// buffer comes, the old one is drawn scaled into the flight.
    fn fly_on(&mut self, window: &Window, mut rect: RectSpring) {
        if !self.carry.animate {
            return;
        }
        let Some(to) = self.window_rect(window) else {
            return;
        };
        let now = Instant::now();
        rect.retarget(tuple(to), now);
        self.carry
            .anims
            .insert(window_data(window).id, Anim { rect, alpha: Spring::at(1.0, now, Params::SMOOTH) });
        self.queue_redraw_all();
    }

    /// Time the flight of the window `id` just let go on every lit panel,
    /// from now (crate::turnlog; `first_ms`: the lift to its first frame on
    /// the glass).
    fn flight_timing_start(&mut self, id: u64) {
        let now = crate::turnlog::monotonic_now();
        let lit: Vec<String> = self.outputs.iter().map(|o| o.name()).filter(|n| self.is_lit(n)).collect();
        for name in lit {
            self.carry.flight_logs.start(&name, now, 0.0);
        }
        self.carry.timed_flight = Some(id);
    }

    /// Whether the timed flight still moves.
    pub fn flight_running(&self) -> bool {
        let now = Instant::now();
        self.carry
            .timed_flight
            .and_then(|id| self.carry.anims.get(&id))
            .map(|a| !a.rect.done(now) || !a.alpha.done(now))
            .unwrap_or(false)
    }

    /// After a frame: once the timed flight is over (at rest, picked up
    /// again, or there was none), every panel's log of it ends.
    pub fn flight_check(&mut self) {
        if self.carry.timed_flight.is_none() || self.flight_running() {
            return;
        }
        self.carry.timed_flight = None;
        let names: Vec<String> = self.carry.flight_logs.logs.keys().cloned().collect();
        for name in names {
            if let Some((log, summary)) = self.carry.flight_logs.end(&name) {
                log_flight(&name, &log, &summary);
            }
        }
    }

    /// A flip on `output` (the kernel's time and vblank sequence): the last
    /// one of the flight's frames there closes its log.
    pub fn flight_flipped(&mut self, output: &str, at: Duration, sequence: u32) {
        if let Some((log, summary)) = self.carry.flight_logs.flipped(output, at, sequence) {
            log_flight(output, &log, &summary);
        }
    }

    /// Put a window on a snap target (the drop of a carry, the overview's
    /// drop, `move_window` over the control socket).
    pub fn apply_target(&mut self, window: &Window, target: Option<&Target>) {
        let id = window_data(window).id;
        self.restore_window(window);
        // Laptop mode: never onto its keyboard panel, never across both
        // panels, no halves on its desk (crate::laptopmode).
        let target = self.laptop_target(target);
        let target = target.as_ref();
        match target {
            None => {}
            Some(Target::Panel(name)) => {
                let here = *window_data(window).placement.borrow() == Placement::Output(name.clone());
                let in_split = self.splits.iter().any(|s| s.has(id));
                if !here || in_split {
                    self.set_placement(window, Placement::Output(name.clone()));
                }
                if self.is_lit(name) {
                    self.active_output = Some(name.clone());
                }
            }
            Some(Target::Half(name, side)) => {
                let Some(output) = self.output_by_name(name) else {
                    return;
                };
                if let Err(err) = self.snap_window(window, &output, *side, None, None) {
                    warn!(?err, "no split: the window takes the panel");
                    self.set_placement(window, Placement::Output(name.clone()));
                }
            }
            Some(Target::Span) if self.in_use().len() >= 2 => {
                self.remember_panel(window);
                self.set_placement(window, Placement::Span);
            }
            Some(Target::Span) => {}
        }
        self.activate_window(window);
    }

    /// Remember the panel a window is on before it spreads over both (a
    /// toggle brings it back there).
    fn remember_panel(&self, window: &Window) {
        if let Placement::Output(name) = &*window_data(window).placement.borrow() {
            *window_data(window).before_span.borrow_mut() = Some(name.clone());
        }
    }

    /// One panel <-> both panels (a double tap on the window's top).
    pub fn toggle_span(&mut self, window: &Window, why: &str) {
        if let Some(at) = self.carry.toggled_at {
            if at.elapsed() < TOGGLE_GUARD {
                debug!(why, "span toggle already done");
                return;
            }
        }
        if self.carry.drag.is_some() {
            return;
        }
        if *window_data(window).android_full.borrow() {
            // An Android window in full screen: the double tap on its top
            // brings it back (crate::waydroid).
            self.carry.toggled_at = Some(Instant::now());
            self.android_set_full(window, false);
            return;
        }
        if self.on_desk(window) {
            // A desk's window: maximized <-> its own frame.
            self.carry.toggled_at = Some(Instant::now());
            let id = window_data(window).id;
            if let Some(frame) = self.desk.frames.get_mut(&id) {
                frame.snap = if frame.snap == Some(crate::desk::Snap::Max) {
                    None
                } else {
                    Some(crate::desk::Snap::Max)
                };
                info!(id, why, maximized = frame.snap.is_some(), "desk window toggled");
            }
            self.place(window);
            self.queue_redraw_all();
            return;
        }
        if self.laptop_on() && *window_data(window).placement.borrow() != Placement::Span {
            // Laptop mode: the other panel is the keyboard.
            return;
        }
        self.carry.toggled_at = Some(Instant::now());
        let from = self.window_rect(window);
        let placement = window_data(window).placement.borrow().clone();
        let id = window_data(window).id;
        match placement {
            Placement::Span => {
                let back = window_data(window)
                    .before_span
                    .borrow()
                    .clone()
                    .filter(|n| self.pose.in_use(n))
                    .or_else(|| self.active_output().map(|o| o.name()));
                let Some(back) = back else {
                    return;
                };
                info!(id, to = back, why, "window back on one panel");
                self.set_placement(window, Placement::Output(back));
            }
            Placement::Output(_) => {
                if self.in_use().len() < 2 {
                    return;
                }
                info!(id, why, "window across both panels");
                self.remember_panel(window);
                self.set_placement(window, Placement::Span);
            }
        }
        if let Some(from) = from {
            self.animate_from(window, from, 1.0, 1.0, Params::SNAPPY);
        }
        self.activate_window(window);
    }

    /// A client asked to be unmaximized (or maximized): what a double-click
    /// on its title bar does. Read as the span toggle when it follows a
    /// touch on the window, and not right after the window opened.
    ///
    /// Qt sends `unset_maximized` right before `set_fullscreen` (its window
    /// state loses Maximized as it gains FullScreen), so a video put full
    /// screen after a tap read as this toggle: the window spread over both
    /// panels for a moment (2026-11-12, Angelfish). The toggle
    /// waits [`TOGGLE_SETTLE`] for a full-screen request coming with it.
    pub fn client_toggle_request(&mut self, window: &Window) {
        let id = window_data(window).id;
        let recent =
            self.carry.last_touch.map(|(w, at)| w == id && at.elapsed() < TOUCH_RECENT).unwrap_or(false);
        let young = window_data(window).mapped_at.elapsed() < STARTUP_GRACE;
        if !unmaximize_is_toggle(recent, young, *window_data(window).fullscreen.borrow()) {
            debug!(id, recent, young, "maximize toggle not from a title bar double tap: ignored");
            return;
        }
        let window = window.clone();
        let _ = self.loop_handle.insert_source(
            Timer::from_duration(TOGGLE_SETTLE),
            move |_, _, state: &mut DuoWc| {
                if !state.space.elements().any(|w| *w == window) {
                    return TimeoutAction::Drop;
                }
                if *window_data(&window).fullscreen.borrow() {
                    info!(id, "unset_maximized came with set_fullscreen (Qt): full screen, no span toggle");
                } else {
                    state.toggle_span(&window, "title bar double tap");
                }
                TimeoutAction::Drop
            },
        );
    }

    /// A window drawn from `from` (global) to where it is, fading from
    /// `alpha_from` to `alpha_to`.
    pub fn animate_from(
        &mut self,
        window: &Window,
        from: Rect,
        alpha_from: f32,
        alpha_to: f32,
        params: Params,
    ) {
        if !self.carry.animate {
            return;
        }
        let Some(to) = self.window_rect(window) else {
            return;
        };
        let now = Instant::now();
        let id = window_data(window).id;
        let start = match self.carry.anims.get(&id) {
            // Already flying: from where it is now.
            Some(anim) => untuple(anim.rect.value(now)),
            None => from,
        };
        self.carry.anims.insert(
            id,
            Anim {
                rect: RectSpring::new(tuple(start), tuple(to), now, params),
                alpha: Spring::new(alpha_from as f64, alpha_to as f64, now, Params::SMOOTH),
            },
        );
        self.queue_redraw_all();
    }

    /// A window opened: it grows in from a little smaller, fading in.
    pub fn animate_open(&mut self, window: &Window) {
        let Some(to) = self.window_rect(window) else {
            return;
        };
        let k = 0.92;
        let from =
            Rect::new(to.x + to.w * (1.0 - k) / 2.0, to.y + to.h * (1.0 - k) / 2.0, to.w * k, to.h * k);
        self.animate_from(window, from, 0.0, 1.0, Params::SMOOTH);
    }

    /// Where a panel's windows go when it goes home: a small rectangle at
    /// its bottom middle (global).
    fn home_spot(&self, output: &Output) -> Option<Rect> {
        let g = rect_of(self.space.output_geometry(output)?);
        let (w, h) = (g.w * 0.28, g.h * 0.28);
        Some(Rect::new(g.x + (g.w - w) / 2.0, g.y + g.h - h - 24.0, w, h))
    }

    /// Home on a panel: its windows are set aside (shrinking towards the
    /// bottom), the shell's home screen shows. False if there was nothing.
    pub fn go_home(&mut self, output: &Output) -> bool {
        let windows: Vec<Window> = self
            .space
            .elements()
            .filter(|w| self.is_on(w, output) && !self.is_minimized(w))
            .cloned()
            .collect();
        if windows.is_empty() {
            return false;
        }
        info!(output = output.name(), windows = windows.len(), "home");
        let spot = self.home_spot(output);
        let now = Instant::now();
        for window in &windows {
            let id = window_data(window).id;
            let from = self.window_rect(window);
            *window_data(window).minimized.borrow_mut() = true;
            if let (Some(from), Some(spot), true) = (from, spot, self.carry.animate) {
                let start = self.carry.anims.get(&id).map(|a| untuple(a.rect.value(now))).unwrap_or(from);
                self.carry.anims.insert(
                    id,
                    Anim {
                        rect: RectSpring::new(tuple(start), tuple(spot), now, Params::SMOOTH),
                        alpha: Spring::new(1.0, 0.0, now, Params::SMOOTH),
                    },
                );
            }
        }
        if self.is_lit(&output.name()) {
            self.active_output = Some(output.name());
        }
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
        true
    }

    /// Bring a window set aside back (with its split partner), growing out
    /// of the bottom of its panel.
    pub fn restore_window(&mut self, window: &Window) {
        let id = window_data(window).id;
        let partner = self
            .splits
            .iter()
            .find(|s| s.has(id))
            .and_then(|s| s.partner(id))
            .and_then(|p| self.window_by_id(p));
        for w in std::iter::once(window.clone()).chain(partner) {
            if !self.is_minimized(&w) {
                continue;
            }
            *window_data(&w).minimized.borrow_mut() = false;
            info!(id = window_data(&w).id, "window back from home");
            let output = match &*window_data(&w).placement.borrow() {
                Placement::Output(name) => self.output_by_name(name),
                Placement::Span => self.active_output(),
            };
            if let Some(spot) = output.and_then(|o| self.home_spot(&o)) {
                self.animate_from(&w, spot, 0.0, 1.0, Params::SMOOTH);
            }
            self.events_dirty = true;
        }
    }

    /// The panels that show home now (lit, nothing on them but minimized
    /// windows).
    pub fn home_panels(&self) -> Vec<String> {
        self.outputs
            .iter()
            .filter(|o| self.is_lit(&o.name()) && self.topmost_on(o).is_none())
            .map(|o| o.name())
            .collect()
    }

    /// Whether anything of the carrying moves (frames are drawn until not).
    pub fn carry_animating(&self) -> bool {
        let now = Instant::now();
        self.carry
            .drag
            .as_ref()
            .map(|d| !d.ghost.done(now) || d.preview.map(|p| !p.done(now)).unwrap_or(false))
            == Some(true)
            || self.carry.anims.values().any(|a| !a.rect.done(now) || !a.alpha.done(now))
    }

    /// Forget the animations that are over.
    pub fn carry_tick(&mut self) {
        let now = Instant::now();
        self.carry.anims.retain(|_, a| !(a.rect.done(now) && a.alpha.done(now)));
    }

    /// What one output draws of the carrying this frame.
    pub fn carry_plan(&self, output: &Output) -> Option<CarryPlan> {
        let g = self.space.output_geometry(output)?;
        let origin = (g.loc.x as f64, g.loc.y as f64);
        let local = |r: Rect| Rect::new(r.x - origin.0, r.y - origin.1, r.w, r.h);
        let now = Instant::now();
        let mut plan = CarryPlan {
            ghost: None,
            preview: None,
            pad: None,
            hide_osk: self.osk_hidden(),
            moved: HashMap::new(),
            hidden: HashSet::new(),
            back: self.carry.back.clone().filter(|b| b.output == output.name()),
            panel_w: g.size.w as f64,
        };
        for w in self.space.elements() {
            if self.is_minimized(w) && !self.carry.anims.contains_key(&window_data(w).id) {
                plan.hidden.insert(window_data(w).id);
            }
        }
        for (id, anim) in &self.carry.anims {
            let r = local(untuple(anim.rect.value(now)));
            plan.moved.insert(*id, (r, anim.alpha.value(now).clamp(0.0, 1.0) as f32));
        }
        if let Some(drag) = &self.carry.drag {
            let id = window_data(&drag.window).id;
            plan.hidden.insert(id);
            plan.moved.remove(&id);
            plan.ghost = Some((drag.window.clone(), local(untuple(drag.ghost.value(now)))));
            plan.preview = drag.preview.map(|p| local(untuple(p.value(now))));
            let rects: Vec<Rect> = self.snap_panels(id).iter().map(|p| p.rect).collect();
            plan.pad = snap::span_pad(&rects, &self.carry.snap)
                .map(|pad| (local(pad), drag.target == Some(Target::Span)));
        }
        Some(plan)
    }

    /// `xdg_toplevel.move`: a client's title bar is being dragged. The touch
    /// point that went down with `serial` (else the newest on the window)
    /// carries the window from now on; the client gets its lift.
    pub fn client_move_request(&mut self, window: &Window, serial: smithay::utils::Serial) {
        let id = window_data(window).id;
        let slot = self
            .touch_slots
            .iter()
            .filter(|(_, s)| s.delivered)
            .find(|(_, s)| s.serial == Some(serial))
            .map(|(slot, _)| *slot)
            .or_else(|| {
                self.touch_slots
                    .iter()
                    .filter(|(_, s)| s.delivered && s.window == Some(id))
                    .max_by_key(|(_, s)| s.time)
                    .map(|(slot, _)| *slot)
            });
        let Some(slot) = slot else {
            debug!(id, "move request without a finger down: ignored");
            return;
        };
        self.end_touch_for_client(slot);
        let Some(touch) = self.touch_slots.get(&slot).cloned() else {
            return;
        };
        debug!(id, slot, "the client asked to be moved");
        self.drag_begin(slot, window, touch.location, &touch.output);
    }

    /// End one touch point for the clients (a lift where it is) and send
    /// them nothing more of it.
    pub fn end_touch_for_client(&mut self, slot: u32) {
        let Some(touch_slot) = self.touch_slots.get_mut(&slot) else {
            return;
        };
        if !touch_slot.delivered {
            return;
        }
        touch_slot.delivered = false;
        let time = touch_slot.time;
        self.pending_focus.remove(&slot);
        self.carry.holds.remove(&slot);
        if let Some(touch) = self.seat.get_touch() {
            touch.up(self, &UpEvent { slot: Some(slot).into(), serial: SERIAL_COUNTER.next_serial(), time });
            touch.frame(self);
        }
    }

    /// Whether a touch at `at` (global) is on the top strip of `window` (a
    /// window without a title bar of its own), given its rectangle.
    pub fn on_top_strip(&self, window: &Window, at: Point<f64, Logical>) -> bool {
        if !barless(window) {
            return false;
        }
        let Some(r) = self.window_rect(window) else {
            return false;
        };
        at.x >= r.x && at.x < r.x + r.w && at.y >= r.y && at.y < r.y + TOP_STRIP
    }

    /// A finger went down on a bar-less window's top strip (delivered to
    /// it): held still for [`LIFT_MS`], it picks the window up.
    pub fn top_hold_start(&mut self, slot: u32, window: &Window, at: Point<f64, Logical>, time: u32) {
        self.carry.gen += 1;
        let gen = self.carry.gen;
        self.carry.holds.insert(slot, TopHold { window: window.clone(), start: at, time, gen, moved: false });
        let _ = self.loop_handle.insert_source(
            Timer::from_duration(Duration::from_millis(LIFT_MS)),
            move |_, _, state: &mut DuoWc| {
                state.events_dirty = true;
                let hold = state.carry.holds.get(&slot).filter(|h| h.gen == gen && !h.moved).cloned();
                if let Some(hold) = hold {
                    state.carry.holds.remove(&slot);
                    let touch = state.touch_slots.get(&slot).filter(|s| s.delivered).cloned();
                    if let Some(touch) = touch {
                        debug!(slot, "held on the top strip: the window lifts");
                        // The client saw a long press: it is cancelled.
                        state.cancel_all_touches();
                        state.drag_begin(slot, &hold.window, touch.location, &touch.output);
                    }
                }
                TimeoutAction::Drop
            },
        );
    }

    /// A finger on a top strip moved: too far, and it is not a hold.
    pub fn top_hold_motion(&mut self, slot: u32, at: Point<f64, Logical>) {
        if let Some(hold) = self.carry.holds.get_mut(&slot) {
            if (at.x - hold.start.x).hypot(at.y - hold.start.y) > LIFT_SLOP {
                hold.moved = true;
            }
        }
    }

    /// A finger on a top strip lifted: a quick tap, twice, is a double tap.
    pub fn top_hold_up(&mut self, slot: u32, time: u32) {
        let Some(hold) = self.carry.holds.remove(&slot) else {
            return;
        };
        if hold.moved || time.wrapping_sub(hold.time) > TAP_MS {
            self.carry.last_tap = None;
            return;
        }
        let id = window_data(&hold.window).id;
        let at = (hold.start.x, hold.start.y);
        let double = self
            .carry
            .last_tap
            .map(|t| {
                t.id == id
                    && time.wrapping_sub(t.time) <= DOUBLE_TAP_MS
                    && (t.at.0 - at.0).hypot(t.at.1 - at.1) <= DOUBLE_TAP_SLOP
            })
            .unwrap_or(false);
        if double {
            self.carry.last_tap = None;
            let window = hold.window.clone();
            self.loop_handle.insert_idle(move |state| state.toggle_span(&window, "double tap on the top"));
        } else {
            self.carry.last_tap = Some(TopTap { id, at, time });
        }
    }

    /// A tap on the dark panel of a phone standing as a tent or a brochure: that panel
    /// takes over (the front window comes with it, the other one goes dark).
    pub fn tent_switch(&mut self, output: &str) {
        let front = self.active_output().and_then(|o| self.topmost_on(&o));
        info!(to = output, "tent: the tapped panel takes over");
        if let Err(err) = self.set_pose(crate::pose::Pose::Single(output.to_owned())) {
            warn!(?err, "tent: no pose change");
            return;
        }
        self.active_output = Some(output.to_owned());
        if let Some(front) = front {
            self.restore_window(&front);
            self.raise_window(&front, true);
        }
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// Whether a touch on a dark panel is a tent tap candidate: the phone
    /// stands as a tent or a brochure, one panel is lit and it is the other.
    pub fn tent_tap_wanted(&self, output: &str) -> bool {
        crate::pose::tent_tap_switches(self.posture.as_deref(), &self.pose, output, self.screen_dark())
            && self.output_by_name(output).is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unmaximize_is_a_title_bar_double_tap_only_off_full_screen() {
        // GTK's double click on its header bar, after a touch on an older
        // window: the span toggle.
        assert!(unmaximize_is_toggle(true, false, false));
        // No touch just before, or a window still opening: not the user.
        assert!(!unmaximize_is_toggle(false, false, false));
        assert!(!unmaximize_is_toggle(true, true, false));
        // Full screen already (Qt's state change around full screen).
        assert!(!unmaximize_is_toggle(true, false, true));
        // Qt's unset_maximized before its set_fullscreen comes within the
        // wait (the same flush); a person's double tap needs far longer.
        assert!(TOGGLE_SETTLE < Duration::from_millis(100));
    }
}
