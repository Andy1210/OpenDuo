//! Android apps' windows, live ([`crate::android`] has the rules): the
//! touched surface's current place for touch coordinates, Android moving a
//! task under a finger (duo-wc takes the drag over), Android's keyboard
//! window kept with the Android window being typed in, full screen (a
//! double tap on the caption bar), and the one Android display: a window
//! Waydroid makes again keeps its place, every Waydroid window gets the
//! lead's size, a spanning task fills the display.

use std::collections::HashMap;
use std::sync::mpsc::Sender;
use std::time::{Duration, Instant};

use smithay::backend::renderer::utils::RendererSurfaceStateUserData;
use smithay::desktop::Window;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::utils::{Logical, Point, Rectangle};
use smithay::wayland::compositor::{get_parent, with_states, with_surface_tree_downward, TraversalAction};
use tracing::{debug, info};

use crate::android::{self, Candidate, Gone, Sizing};
use crate::overview::Rect;
use crate::split::Split;
use crate::state::{app_id_and_title, window_data, DuoWc, Placement};

/// What a window is to the Android rules.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AndroidKind {
    /// Not known yet (no app id).
    #[default]
    None,
    /// Not an Android app's (its app id said so: not looked at again).
    Other,
    /// An Android app (a task).
    Task,
    /// Android's on-screen keyboard.
    Ime,
    /// Waydroid's whole Android screen (`Waydroid`, not multi-window).
    Screen,
}

impl AndroidKind {
    /// A window of its own on the Android display (an app, or the whole
    /// screen): it can lead the display's size, and Waydroid makes it again
    /// after a resize.
    pub fn on_display(self) -> bool {
        matches!(self, AndroidKind::Task | AndroidKind::Screen)
    }
}

/// What a Waydroid window that went leaves to the one made in its place.
#[derive(Debug, Clone)]
pub struct Carry {
    placement: Placement,
    saved: Option<Placement>,
    before_span: Option<String>,
    fullscreen: bool,
    android_full: bool,
    android_filled: Option<(i32, i32)>,
    minimized: bool,
    /// Its size and bigness for the display's size while it is away.
    sizing: Sizing,
    /// The split it was half of (its own id in it).
    split: Option<Split>,
    /// Its id, to put the new one in the split in its place.
    id: u64,
    /// The windows that were in front of it (back to front).
    above: Vec<u64>,
}

/// The Android rules' live state.
#[derive(Debug, Default)]
pub struct Android {
    /// The settings.
    pub cfg: android::Config,
    /// Touches that went down on a caption bar's middle: window id, where,
    /// when, and whether they moved too far for a tap.
    taps: HashMap<u32, (u64, (f64, f64), u32, bool)>,
    /// The last quick tap there (for a double tap).
    last: Option<android::Tap>,
    /// Waydroid windows that went just now (Waydroid makes them again after
    /// a resize of its display).
    gone: Vec<Gone<Carry>>,
    /// The last layout rank given (the lead is the one laid out last).
    rank: u64,
    /// The root helper's queue: one thread runs its calls in order (a fill
    /// and the restore after it must not overtake each other).
    helper: Option<Sender<Vec<String>>>,
    /// The window Android's keyboard window is with (its corner).
    ime_with: Option<u64>,
    /// The apps that open in full screen (the user left them there):
    /// [`full_apps_path`].
    full_apps: Vec<String>,
}

impl Android {
    /// With these settings (and the apps that open in full screen, from
    /// duo-wc's state file).
    pub fn new(cfg: android::Config) -> Self {
        let full_apps = full_apps_path()
            .and_then(|p| std::fs::read_to_string(p).ok())
            .map(|t| android::parse_full_apps(&t))
            .unwrap_or_default();
        Android { cfg, full_apps, ..Default::default() }
    }

    /// Remember (or forget) that an app opens in full screen.
    fn remember_full(&mut self, package: &str, on: bool) {
        let had = self.full_apps.iter().any(|a| a == package);
        if had == on {
            return;
        }
        if on {
            self.full_apps.push(package.to_owned());
        } else {
            self.full_apps.retain(|a| a != package);
        }
        let Some(path) = full_apps_path() else {
            return;
        };
        let written = path
            .parent()
            .map(std::fs::create_dir_all)
            .unwrap_or(Ok(()))
            .and_then(|_| std::fs::write(&path, android::full_apps_text(&self.full_apps)));
        match written {
            Ok(()) => info!(package, on, "Android app opens in full screen from now on (or not)"),
            Err(err) => {
                tracing::warn!(?err, path = %path.display(), "could not keep the Android full-screen apps")
            }
        }
    }
}

/// duo-wc's state file of the Android apps that open in full screen:
/// `$XDG_STATE_HOME/duo-wc/android-full` (`~/.local/state/...`), one
/// package per line.
fn full_apps_path() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/state")))?;
    Some(base.join("duo-wc/android-full"))
}

/// A surface's offset from its parent (a subsurface's place), as drawn.
fn view_offset(surface: &WlSurface) -> Point<i32, Logical> {
    with_states(surface, |states| {
        states
            .data_map
            .get::<RendererSurfaceStateUserData>()
            .and_then(|d| d.lock().unwrap().view())
            .map(|v| v.offset)
            .unwrap_or_default()
    })
}

impl DuoWc {
    /// Where a window's surface (its own or a subsurface of it) is now, in
    /// the global space; None for surfaces that are not a window's.
    pub fn surface_origin(&self, surface: &WlSurface) -> Option<Point<f64, Logical>> {
        let mut offset = Point::<i32, Logical>::from((0, 0));
        let mut root = surface.clone();
        while let Some(parent) = get_parent(&root) {
            offset += view_offset(&root);
            root = parent;
        }
        let window = self.window_for_surface(&root)?;
        let loc = self.space.element_location(&window)? - window.geometry().loc;
        Some((loc + offset).to_f64())
    }

    /// Where a touch point is for the client: its place relative to the
    /// surface it went down on AS THAT SURFACE IS NOW. Smithay keeps the
    /// surface's place from the touch-down; a client that moves the
    /// subsurface under the finger (Android moving a task) then got the
    /// finger ahead by every step it moved, and ran away from it.
    pub fn client_touch_location(&self, slot: u32, location: Point<f64, Logical>) -> Point<f64, Logical> {
        let Some((surface, origin)) = self.touch_slots.get(&slot).and_then(|s| s.surface.clone()) else {
            return location;
        };
        // A window shown scaled down: in its client's coordinates
        // (crate::dialog), like its origin.
        let location = self.client_location(&surface, location);
        match self.surface_origin(&surface) {
            Some(now) => {
                android::follow_surface((location.x, location.y), (origin.x, origin.y), (now.x, now.y)).into()
            }
            None => location,
        }
    }

    /// What a window is to the Android rules (from its app id).
    pub fn android_kind(&self, window: &Window) -> AndroidKind {
        *window_data(window).android.borrow()
    }

    /// Android's keyboard window: not a window of its own to the user (no
    /// card, no dock entry, never a panel's front window).
    pub fn is_accessory(&self, window: &Window) -> bool {
        self.android_kind(window) == AndroidKind::Ime
    }

    /// The union of a window's subsurfaces (its own surface left out: for
    /// Waydroid that is the see-through background over the whole Android
    /// display), in the window's frame.
    fn content_rect(window: &Window) -> Option<Rect> {
        let toplevel = window.toplevel()?;
        let root = toplevel.wl_surface().clone();
        let mut union: Option<Rect> = None;
        with_surface_tree_downward(
            &root,
            Point::<i32, Logical>::from((0, 0)),
            |surface, states, loc: &Point<i32, Logical>| {
                let view = states
                    .data_map
                    .get::<RendererSurfaceStateUserData>()
                    .and_then(|d| d.lock().unwrap().view());
                let Some(view) = view else {
                    return TraversalAction::SkipChildren;
                };
                let loc = *loc + view.offset;
                if *surface != root && view.dst.w > 0 && view.dst.h > 0 {
                    let r = Rect::new(loc.x as f64, loc.y as f64, view.dst.w as f64, view.dst.h as f64);
                    union = Some(match union {
                        None => r,
                        Some(u) => {
                            let x0 = u.x.min(r.x);
                            let y0 = u.y.min(r.y);
                            let x1 = (u.x + u.w).max(r.x + r.w);
                            let y1 = (u.y + u.h).max(r.y + r.h);
                            Rect::new(x0, y0, x1 - x0, y1 - y0)
                        }
                    });
                }
                TraversalAction::DoChildren(loc)
            },
            |_, _, _| {},
            |_, _, _| true,
        );
        union
    }

    /// A window committed: the Android rules. Its kind once it has an app
    /// id; Android's keyboard put with the app typed in; an Android task
    /// that moved under a finger is taken over (duo-wc carries it).
    pub fn android_commit(&mut self, window: &Window) {
        let data = window_data(window);
        if *data.android.borrow() == AndroidKind::None {
            let (app_id, _) = app_id_and_title(window);
            let kind = if android::is_ime(app_id.as_deref()) {
                AndroidKind::Ime
            } else if android::is_task(app_id.as_deref()) {
                AndroidKind::Task
            } else if app_id.as_deref() == Some(android::FULL_UI_APP_ID) {
                AndroidKind::Screen
            } else if app_id.is_some() {
                AndroidKind::Other
            } else {
                AndroidKind::None
            };
            *data.android.borrow_mut() = kind;
            if matches!(kind, AndroidKind::None | AndroidKind::Other) {
                return;
            }
            if kind.on_display() {
                if let Some(app_id) = app_id {
                    self.android_identified(window, kind, &app_id);
                }
            }
            if kind == AndroidKind::Ime {
                info!(id = data.id, "Android's keyboard window: it goes with the app typed in");
                // Not a window of its own in task lists.
                if let Some(handle) = data.foreign.borrow_mut().take() {
                    self.foreign_toplevels.remove_toplevel(&handle);
                }
                self.events_dirty = true;
                // It took the keyboard as a new window: back to the app.
                self.follow_android_ime();
                self.refresh_keyboard_focus();
                return;
            }
        }
        let kind = *data.android.borrow();
        match kind {
            AndroidKind::Ime => self.follow_android_ime(),
            AndroidKind::Task => self.android_task_commit(window),
            AndroidKind::None | AndroidKind::Other | AndroidKind::Screen => {}
        }
    }

    /// A Waydroid window (an app, the whole screen) just got its app id, at
    /// its first commit, before its first configure. If Waydroid made it
    /// again after a resize of its display (one with its app id went just
    /// now), it is that window: its place, span, full screen, split and
    /// stacking come back, and it is configured the size it had (another
    /// size would resize the display again). Otherwise it is a new one,
    /// laid out last; its task, which Android placed on a display bigger
    /// than its window (the lead's), is moved inside its window.
    fn android_identified(&mut self, window: &Window, kind: AndroidKind, app_id: &str) {
        let data = window_data(window);
        *data.android_app.borrow_mut() = Some(app_id.to_owned());
        let first = window.toplevel().map(|t| !t.is_initial_configure_sent()).unwrap_or(false);
        let carry =
            if first { android::take_gone(&mut self.android.gone, app_id, Instant::now()) } else { None };
        let inherited = carry.is_some();
        match carry {
            Some(carry) => self.android_inherit(window, carry),
            None => {
                self.android.rank += 1;
                *data.android_rank.borrow_mut() = self.android.rank;
            }
        }
        // Everyone at the display's size (this one may lead it now).
        self.place_all();
        if inherited || kind != AndroidKind::Task {
            return;
        }
        // An app the user left in full screen opens in full screen (Android
        // does not say when an app wants it: a game's own wish is unseen).
        let remembered = android_package(window).is_some_and(|p| self.android.full_apps.contains(&p));
        if remembered {
            info!(id = data.id, "an Android app left in full screen opens in full screen");
            self.android_set_full(window, true);
            return;
        }
        let own = self.android_own_rect(window).map(|r| (r.size.w, r.size.h));
        if let (Some(own), Some(shared)) = (own, self.android_display_size()) {
            if android::needs_fit(shared, own) {
                if let Some(package) = android_package(window) {
                    let (pw, ph) = android::display_px(own.0 as f64, own.1 as f64, self.scale);
                    info!(
                        id = data.id,
                        ?own,
                        ?shared,
                        "a new Android app on a bigger display: its task is kept inside its window"
                    );
                    self.android_helper(&["fit", &package, &pw.to_string(), &ph.to_string()]);
                }
            }
        }
    }

    /// The new window Waydroid made takes over the one that went.
    fn android_inherit(&mut self, window: &Window, carry: Carry) {
        let data = window_data(window);
        let id = data.id;
        info!(
            id,
            was = carry.id,
            placement = ?carry.placement,
            full = carry.android_full,
            "Waydroid made an Android window again: it keeps its place"
        );
        *data.placement.borrow_mut() = carry.placement;
        *data.saved.borrow_mut() = carry.saved;
        *data.before_span.borrow_mut() = carry.before_span;
        *data.fullscreen.borrow_mut() = carry.fullscreen;
        *data.android_full.borrow_mut() = carry.android_full;
        *data.android_filled.borrow_mut() = carry.android_filled;
        *data.minimized.borrow_mut() = carry.minimized;
        *data.android_rank.borrow_mut() = carry.sizing.rank;
        // The same window: no grow-in, no launcher's word.
        *data.opened.borrow_mut() = true;
        *data.launched.borrow_mut() = false;
        if let Some(mut split) = carry.split {
            let partner = split.partner(carry.id).and_then(|p| self.window_by_id(p));
            let fits = partner.as_ref().is_some_and(|p| {
                let pid = window_data(p).id;
                *window_data(p).placement.borrow() == Placement::Output(split.output.clone())
                    && !self.splits.iter().any(|s| s.has(pid) || s.output == split.output)
            });
            if fits {
                if split.first == carry.id {
                    split.first = id;
                } else {
                    split.second = id;
                }
                info!(id, output = split.output, "the split it was half of goes on");
                self.splits.push(split);
            }
        }
        // Mapped in front as new: the windows that were in front of it go
        // there again.
        for above in carry.above {
            if let Some(w) = self.window_by_id(above) {
                self.space.raise_element(&w, false);
            }
        }
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// A Waydroid window goes (from `toplevel_destroyed`, while it is still
    /// in the space): what it was is kept for the one Waydroid may make in
    /// its place. If none comes, the display's size is worked out without
    /// it once the wait is over.
    pub fn android_window_gone(&mut self, window: &Window) {
        if !self.android_kind(window).on_display() {
            return;
        }
        let data = window_data(window);
        let Some(app_id) = data.android_app.borrow().clone() else {
            return;
        };
        let Some(sizing) = self.android_sizing(window) else {
            return;
        };
        let id = data.id;
        let above: Vec<u64> =
            self.space.elements().skip_while(|w| *w != window).skip(1).map(|w| window_data(w).id).collect();
        let carry = Carry {
            placement: data.placement.borrow().clone(),
            saved: data.saved.borrow().clone(),
            before_span: data.before_span.borrow().clone(),
            fullscreen: *data.fullscreen.borrow(),
            android_full: *data.android_full.borrow(),
            android_filled: *data.android_filled.borrow(),
            minimized: *data.minimized.borrow(),
            sizing,
            split: self.splits.iter().find(|s| s.has(id)).cloned(),
            id,
            above,
        };
        let now = Instant::now();
        android::prune_gone(&mut self.android.gone, now);
        self.android.gone.push(Gone { app_id, at: now, state: carry });
        let _ = self.loop_handle.insert_source(
            Timer::from_duration(Duration::from_millis(android::RECREATE_MS + 100)),
            |_, _, state: &mut DuoWc| {
                if android::prune_gone(&mut state.android.gone, Instant::now()) {
                    debug!("an Android window did not come back: the display's size without it");
                    state.place_all();
                }
                TimeoutAction::Drop
            },
        );
    }

    /// The big window leading the Android display (spanning, full screen,
    /// a half), if one does.
    pub fn android_big_lead(&self) -> Option<Window> {
        self.space
            .elements()
            .filter(|w| self.android_kind(w).on_display())
            .filter_map(|w| self.android_sizing(w).filter(|s| s.big).map(|s| (s.rank, w)))
            .max_by_key(|(rank, _)| *rank)
            .map(|(_, w)| w.clone())
    }

    /// Where Android's keyboard window goes: the corner of the window it is
    /// with (its half too).
    pub fn android_ime_rect(&self) -> Option<Rectangle<i32, Logical>> {
        let with = self.window_by_id(self.android.ime_with?)?;
        self.android_own_rect(&with)
    }

    /// The rectangle a Waydroid window's own place gives it (global): its
    /// half, its placement's area, in full screen moved up by the caption.
    pub fn android_own_rect(&self, window: &Window) -> Option<Rectangle<i32, Logical>> {
        let data = window_data(window);
        let fullscreen = *data.fullscreen.borrow();
        let tile = if fullscreen { None } else { self.tile_rect(window) };
        let mut rect = tile.or_else(|| self.placement_rect(&data.placement.borrow(), fullscreen))?;
        if *data.android_full.borrow() {
            let caption = self.android.cfg.caption.round() as i32;
            rect.loc.y -= caption;
            rect.size.h += caption;
        }
        Some(rect)
    }

    /// A Waydroid window as the display's size sees it.
    fn android_sizing(&self, window: &Window) -> Option<Sizing> {
        let data = window_data(window);
        let rect = self.android_own_rect(window)?;
        let fullscreen = *data.fullscreen.borrow();
        let big = *data.placement.borrow() == Placement::Span
            || *data.android_full.borrow()
            || (!fullscreen && self.tile_rect(window).is_some());
        Some(Sizing { rank: *data.android_rank.borrow(), big, size: (rect.size.w, rect.size.h) })
    }

    /// The Android display's size, which every Waydroid window is
    /// configured to (crate::android): the lead's, of the windows there and
    /// those just gone (Waydroid is making them again).
    pub fn android_display_size(&self) -> Option<(i32, i32)> {
        let mut all: Vec<Sizing> = self
            .space
            .elements()
            .filter(|w| self.android_kind(w).on_display())
            .filter_map(|w| self.android_sizing(w))
            .collect();
        all.extend(self.android.gone.iter().map(|g| g.state.sizing));
        android::display_size(&all)
    }

    /// A Waydroid window was laid out (placed, spanned, full screen, a
    /// half), its new place already set: it is the latest, and the other
    /// big ones of another size give up their bigness (one size is the
    /// display's: spanning one app ends another's full screen). The caller
    /// re-places everything.
    pub fn android_laid_out(&mut self, window: &Window) {
        if !self.android_kind(window).on_display() {
            return;
        }
        self.android.rank += 1;
        *window_data(window).android_rank.borrow_mut() = self.android.rank;
        let Some(me) = self.android_sizing(window) else {
            return;
        };
        if !me.big {
            return;
        }
        let id = window_data(window).id;
        let partner = self.splits.iter().find(|s| s.has(id)).and_then(|s| s.partner(id));
        let others: Vec<Window> = self
            .space
            .elements()
            .filter(|w| {
                *w != window && Some(window_data(w).id) != partner && self.android_kind(w).on_display()
            })
            .cloned()
            .collect();
        for other in others {
            if self.android_sizing(&other).is_some_and(|s| android::conflicts(me.size, s)) {
                self.android_normalize(&other);
            }
        }
    }

    /// An Android window back to a plain panel: out of full screen, off the
    /// span (to the panel it came from), out of its split.
    fn android_normalize(&mut self, window: &Window) {
        let data = window_data(window);
        let id = data.id;
        info!(id, "another Android window leads the display: this one goes back to a plain panel");
        if *data.android_full.borrow() {
            *data.android_full.borrow_mut() = false;
            *data.fullscreen.borrow_mut() = false;
        }
        if *data.placement.borrow() == Placement::Span {
            let back = data
                .before_span
                .borrow()
                .clone()
                .filter(|n| self.pose.in_use(n))
                .or_else(|| self.active_output().map(|o| o.name()));
            if let Some(back) = back {
                *data.placement.borrow_mut() = Placement::Output(back);
                *data.saved.borrow_mut() = None;
            }
        }
        if self.tile_rect(window).is_some() {
            self.forget_split_of(id);
        }
        self.events_dirty = true;
    }

    /// An Android task committed: if its layers moved while a finger that
    /// went down on it is moving, Android is moving the task with that
    /// finger (its caption bar dragged). Android can only move it within
    /// its display (this window, one panel), so duo-wc takes the drag over:
    /// the client gets the lift, the window is carried (to the other panel,
    /// a half, both).
    fn android_task_commit(&mut self, window: &Window) {
        let Some(now) = Self::content_rect(window) else {
            return;
        };
        let data = window_data(window);
        let before = data.content.replace(Some(now));
        let Some(before) = before else {
            return;
        };
        if !android::task_moved(before, now) {
            return;
        }
        if self.carry.drag.is_some() || self.taskview.open || self.lock.locked {
            return;
        }
        let id = data.id;
        let slot = self
            .touch_slots
            .iter()
            .filter(|(_, s)| s.delivered && s.window == Some(id))
            .find(|(_, s)| (s.location.x - s.start.x).hypot(s.location.y - s.start.y) >= android::MOVE_SLOP)
            .map(|(slot, _)| *slot);
        let Some(slot) = slot else {
            debug!(id, "an Android task moved with no finger on it");
            return;
        };
        info!(id, slot, "Android moves its task under the finger: duo-wc carries the window");
        let window = window.clone();
        self.loop_handle.insert_idle(move |state| {
            let Some(touch) = state.touch_slots.get(&slot).filter(|s| s.delivered).cloned() else {
                return;
            };
            if state.carry.drag.is_some() || !state.space.elements().any(|w| *w == window) {
                return;
            }
            state.end_touch_for_client(slot);
            state.drag_begin(slot, &window, touch.location, &touch.output);
        });
    }

    /// A finger went down on a window (delivered to it): on an Android
    /// task's caption bar's middle it may be half of a double tap.
    pub fn android_tap_down(&mut self, slot: u32, window: &Window, at: Point<f64, Logical>, time: u32) {
        if self.android_kind(window) != AndroidKind::Task || *window_data(window).android_full.borrow() {
            return;
        }
        let Some(loc) = self.space.element_location(window).map(|l| l - window.geometry().loc) else {
            return;
        };
        let Some(content) = *window_data(window).content.borrow() else {
            return;
        };
        let content = content.offset(loc.x as f64, loc.y as f64);
        if android::in_caption_middle(content, &self.android.cfg, at.x, at.y) {
            self.android.taps.insert(slot, (window_data(window).id, (at.x, at.y), time, false));
        }
    }

    /// A finger moved: too far, and it is no tap.
    pub fn android_tap_motion(&mut self, slot: u32, at: Point<f64, Logical>) {
        if let Some(tap) = self.android.taps.get_mut(&slot) {
            if (at.x - tap.1 .0).hypot(at.y - tap.1 .1) > 10.0 {
                tap.3 = true;
            }
        }
    }

    /// A finger lifted: two quick taps on the caption bar's middle make the
    /// window full screen.
    pub fn android_tap_up(&mut self, slot: u32, time: u32) {
        let Some((id, at, down, moved)) = self.android.taps.remove(&slot) else {
            return;
        };
        if !android::is_tap(down, time, if moved { f64::INFINITY } else { 0.0 }) {
            self.android.last = None;
            return;
        }
        let tap = android::Tap { id, at, up: time };
        match self.android.last.take() {
            Some(first) if android::is_double_tap(&first, &tap) => {
                if let Some(window) = self.window_by_id(id) {
                    self.loop_handle.insert_idle(move |state| state.android_set_full(&window, true));
                }
            }
            _ => self.android.last = Some(tap),
        }
    }

    /// An Android window full screen (or back): duo-wc's full screen on its
    /// panel with the window moved up by the caption bar's height (out of
    /// sight), and the task inside Android sized to the whole display (it
    /// stays a freeform task: the other Android apps keep their windows).
    pub fn android_set_full(&mut self, window: &Window, on: bool) {
        if self.android_kind(window) != AndroidKind::Task {
            return;
        }
        let data = window_data(window);
        if *data.android_full.borrow() == on {
            return;
        }
        if on && !self.android_helper_allowed() {
            // Without the helper the task cannot fill the window: a full
            // screen of a medium Android window is no full screen.
            tracing::warn!(
                helper = self.android.cfg.helper,
                "Android full screen needs the task helper (sudo -n): not allowed here, nothing done"
            );
            return;
        }
        info!(id = data.id, on, "Android window full screen");
        // The next time it opens, it opens like this.
        if let Some(package) = android_package(window) {
            self.android.remember_full(&package, on);
        }
        let from = self.window_rect(window);
        *data.android_full.borrow_mut() = on;
        *data.fullscreen.borrow_mut() = on;
        // The display takes its size; the task is filled to it (or, back
        // on a panel, gets its medium bounds; spanning, the span) in place().
        self.android_laid_out(window);
        self.place_all();
        self.follow_android_ime();
        if let Some(from) = from {
            self.animate_from(window, from, 1.0, 1.0, crate::spring::Params::SNAPPY);
        }
        self.activate_window(window);
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// An Android task's window was placed (sized): spanning or full screen,
    /// the task inside Android is sized to the whole display (`w` x `h`
    /// logical, the size configured), if it was not already; otherwise a
    /// task that was filled gets its medium bounds back.
    pub fn android_fill_for(&mut self, window: &Window, span: bool, w: i32, h: i32) {
        let data = window_data(window);
        if !android::wants_fill(span, *data.android_full.borrow()) {
            if data.android_filled.borrow_mut().take().is_some() {
                if let Some(package) = android_package(window) {
                    self.android_helper(&["restore", &package]);
                }
            }
            return;
        }
        if *data.android_filled.borrow() == Some((w, h)) {
            return;
        }
        *data.android_filled.borrow_mut() = Some((w, h));
        let Some(package) = android_package(window) else {
            return;
        };
        let (pw, ph) = android::display_px(w as f64, h as f64, self.scale);
        self.android_helper(&["fill", &package, &pw.to_string(), &ph.to_string()]);
    }

    /// Whether the root helper may be run (`sudo -n -l HELPER`: the
    /// sudoers rule of `android/install-files.sh` is in place).
    fn android_helper_allowed(&self) -> bool {
        std::process::Command::new("sudo")
            .args(["-n", "-l", &self.android.cfg.helper])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }

    /// Run the root helper (sudo -n), not waiting for it. One thread runs
    /// the calls in the order asked (each takes ~1.6 s through `waydroid
    /// shell`): a restore never overtakes the fill before it.
    fn android_helper(&mut self, args: &[&str]) {
        let args: Vec<String> = args.iter().map(|a| a.to_string()).collect();
        info!(?args, "Android task helper");
        if self.android.helper.is_none() {
            let helper = self.android.cfg.helper.clone();
            let (tx, rx) = std::sync::mpsc::channel::<Vec<String>>();
            let spawned = std::thread::Builder::new().name("android-task".to_owned()).spawn(move || {
                for args in rx {
                    run_helper(&helper, &args);
                }
            });
            match spawned {
                Ok(_) => self.android.helper = Some(tx),
                Err(err) => {
                    tracing::warn!(?err, "could not start the Android task helper thread");
                    return;
                }
            }
        }
        let sent = self.android.helper.as_ref().map(|tx| tx.send(args).is_ok()).unwrap_or(false);
        if !sent {
            tracing::warn!("the Android task helper thread is gone: nothing done");
            self.android.helper = None;
        }
    }

    /// Put Android's keyboard window where the Android window being typed
    /// in is (the same placement: the Android display they share lines up)
    /// and in front of it; back from home with it.
    pub fn follow_android_ime(&mut self) {
        let imes: Vec<Window> =
            self.space.elements().filter(|w| self.android_kind(w) == AndroidKind::Ime).cloned().collect();
        if imes.is_empty() {
            return;
        }
        let ids: Vec<(u64, Option<String>, bool)> = self
            .space
            .elements()
            .rev()
            .map(|w| (window_data(w).id, app_id_and_title(w).0, self.is_minimized(w)))
            .collect();
        let candidates: Vec<Candidate<'_>> = ids
            .iter()
            .map(|(id, app_id, minimized)| Candidate {
                id: *id,
                app_id: app_id.as_deref(),
                minimized: *minimized,
            })
            .collect();
        let focus = self
            .seat
            .get_keyboard()
            .and_then(|k| k.current_focus())
            .and_then(|s| self.window_for_surface(&s))
            .map(|w| window_data(&w).id);
        let Some(companion) = android::ime_companion(&candidates, focus).and_then(|id| self.window_by_id(id))
        else {
            return;
        };
        // Android lays its keyboard out across the whole display: while a
        // spanning (full-screen, half) app leads the display, the keyboard
        // goes where that app is, or half of it would be off its panel.
        let companion = self.android_big_lead().filter(|w| !self.is_minimized(w)).unwrap_or(companion);
        self.android.ime_with = Some(window_data(&companion).id);
        let placement = window_data(&companion).placement.borrow().clone();
        // The same rectangle as the app (full screen too): every Waydroid
        // window's size is the ONE Android display's size, so a keyboard
        // window of another size would resize the app's display under it.
        let full = *window_data(&companion).android_full.borrow();
        let fullscreen = *window_data(&companion).fullscreen.borrow();
        for ime in imes {
            let data = window_data(&ime);
            let mut changed = false;
            if *data.placement.borrow() != placement {
                info!(id = data.id, ?placement, "Android's keyboard goes with the app typed in");
                *data.placement.borrow_mut() = placement.clone();
                *data.saved.borrow_mut() = None;
                changed = true;
            }
            if *data.android_full.borrow() != full || *data.fullscreen.borrow() != fullscreen {
                *data.android_full.borrow_mut() = full;
                *data.fullscreen.borrow_mut() = fullscreen;
                changed = true;
            }
            if *data.minimized.borrow() {
                *data.minimized.borrow_mut() = false;
                changed = true;
            }
            if changed {
                self.place(&ime);
                self.events_dirty = true;
            }
            if self.space.elements().last() != Some(&ime) {
                self.space.raise_element(&ime, false);
                changed = true;
            }
            if changed {
                self.queue_redraw_all();
            }
        }
    }
}

/// One call of the root helper (sudo -n), waited for, its outcome logged.
fn run_helper(helper: &str, args: &[String]) {
    match std::process::Command::new("sudo")
        .arg("-n")
        .arg(helper)
        .args(args)
        .stdin(std::process::Stdio::null())
        .output()
    {
        Ok(out) if out.status.success() => debug!(?args, "Android task helper done"),
        Ok(out) => tracing::warn!(
            ?args,
            status = ?out.status,
            stderr = %String::from_utf8_lossy(&out.stderr).trim(),
            "Android task helper failed"
        ),
        Err(err) => tracing::warn!(?err, "Android task helper could not run"),
    }
}

/// The Android package of a task's window (`waydroid.<package>`).
fn android_package(window: &Window) -> Option<String> {
    let (app_id, _) = app_id_and_title(window);
    app_id?.strip_prefix("waydroid.").map(str::to_owned)
}
