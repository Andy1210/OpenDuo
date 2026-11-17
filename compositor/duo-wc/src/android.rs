//! Android apps (Waydroid, `docs/hardware/android.md`): what duo-wc
//! knows about their windows. Pure: no Smithay types.
//!
//! In Waydroid's multi-window mode every Android task is its own xdg
//! toplevel (`app_id` `waydroid.<package>`), but all of them share ONE
//! Android display: each window's surface is that whole display (a
//! see-through 1x1 background stretched over it), and the task's layers
//! are subsurfaces at their places on it. Android runs every task in its
//! freeform mode there: a medium window with Android's own caption bar
//! (back, minimize, maximize, close), which Android itself draws into the
//! app. So:
//!
//! - **Moving.** Dragging the caption is Android's own move: Android moves
//!   the task's layers on the (one-panel) display, and Waydroid moves the
//!   subsurfaces. It can never leave the panel. duo-wc sees the task's
//!   layers move under a finger ([`task_moved`]) and takes the drag over:
//!   from then on it is duo-wc's carry, to the other panel, a half or both
//!   (`crate::windrag`). Touches are given to Waydroid relative to where
//!   the touched subsurface is NOW (Waydroid adds the layer's current place
//!   on the Android display): relative to where it was at the touch-down,
//!   Android saw the finger run ahead by every step the task had moved, and
//!   the window slid away from under the finger.
//! - **The keyboard.** Android's on-screen keyboard is a window of its own,
//!   [`IME_APP_ID`], the size of the display with the keyboard at its
//!   bottom. It belongs where the Android window being typed in is: the
//!   same placement (so its part of the shared display lines up with the
//!   app's), in front of it ([`ime_companion`]). Anywhere else it opened as
//!   a new window by the open rule: on the other panel.
//! - **Full screen.** Android's caption bar is drawn by Android into the
//!   app (an app that draws to its top edge, like the Play Store, is
//!   covered by it), and Android's own maximize makes a fullscreen task,
//!   which hides every other Android app (they lose their windows). A
//!   **double tap on the caption bar's middle** ([`in_caption_middle`],
//!   [`is_double_tap`]) makes the window duo-wc's full screen instead: the
//!   whole panel, the task sized to its whole Android display while staying
//!   freeform (`android/duo2-android-task`, root, through `waydroid
//!   shell`), and the window placed [`Config::caption`] px higher, so the
//!   caption bar is above the glass, out of sight and out of reach. Back:
//!   a double tap on the window's top strip (like any window without a
//!   title bar), which also carries it (touch and hold).
//! - **One Android display, one size.** Waydroid makes the Android display
//!   the size of the last configure ANY of its windows got (its
//!   hwcomposer's `xdg_toplevel_handle_configure`: `choose_width_height`,
//!   `do_hotplug`), and a display of a new size is a hotplug, after which
//!   Waydroid destroys every one of its toplevels and makes new ones for the
//!   same tasks (`cleanup_stale_windows`: a frame without the task's
//!   layers). So (1) a new Waydroid toplevel whose app id went within
//!   [`RECREATE_MS`] is that window again ([`take_gone`]): it keeps its
//!   place, span, full screen, split; as a new window it went where the
//!   open rule put it, configured the old size, and the span or the full
//!   screen undid itself (2026-11-12, the journal: span, then `toplevel
//!   gone` for every Android window 30 ms later, the new ones on one
//!   panel). (2) Every
//!   Waydroid window is configured to the SAME size, the lead's
//!   ([`display_size`]): two sizes (a spanning app and one on a panel)
//!   would resize the display at every configure, each resize remaking
//!   every window: a loop. The lead is the spanning / full-screen / half
//!   window, the one laid out last among those; with none, the one laid out
//!   last. The others show their part of the bigger display on their own
//!   panel: their task (a medium freeform window) is kept inside that part
//!   ([`needs_fit`]). Of the big windows only those of the lead's size
//!   stay big ([`conflicts`]): spanning one app ends another's full screen.
//! - **Spanning fills the task.** A spanning (or full-screen) task is sized
//!   to the whole display inside Android ([`wants_fill`]), so the app covers
//!   both panels; back on one panel it gets its medium bounds again.
//! - **A task that is not filled is kept inside its window** ([`fit_box`],
//!   [`needs_refit`]). Android never moves a freeform task when its display
//!   changes: after a turn a portrait task ran past the bottom of the
//!   landscape panel, and the medium bounds a task gets back after a span
//!   (or a full screen) were measured in the place and orientation of that
//!   time. So whenever the part of the display a window shows shrinks in
//!   either direction (a turn, a half, a lead of a half's size), or its task
//!   came back from a fill, the task is fitted into it again
//!   (`duo2-android-task fit`, after the `restore`), once the changes have
//!   settled ([`refit_wait`]: a turn reconfigures one panel after the other,
//!   and Waydroid then makes its windows again).

use std::time::{Duration, Instant};

use crate::overview::Rect;

/// The app id of Android's on-screen keyboard's window.
pub const IME_APP_ID: &str = "waydroid.InputMethod";

/// The app id of Waydroid's whole Android screen (not multi-window).
pub const FULL_UI_APP_ID: &str = "Waydroid";

/// Whether a window is Waydroid's (any of its windows).
pub fn is_waydroid(app_id: Option<&str>) -> bool {
    matches!(app_id, Some(a) if a.starts_with("waydroid.") || a == FULL_UI_APP_ID)
}

/// Whether a window is Android's on-screen keyboard.
pub fn is_ime(app_id: Option<&str>) -> bool {
    app_id == Some(IME_APP_ID)
}

/// Whether a window is an Android app (a task), not the keyboard and not
/// the whole Android screen.
pub fn is_task(app_id: Option<&str>) -> bool {
    matches!(app_id, Some(a) if a.starts_with("waydroid.") && a != IME_APP_ID)
}

/// Whether a task's content (the union of its layers) went from `old` to
/// `new` by MOVING: the same size (within a px), somewhere else (2 px or
/// more). A resize, a dialog opening, a layer coming or going change the
/// size; a scroll inside the app moves no layer of it.
pub fn task_moved(old: Rect, new: Rect) -> bool {
    let same_size = (old.w - new.w).abs() <= 1.0 && (old.h - new.h).abs() <= 1.0;
    let moved = (old.x - new.x).abs().max((old.y - new.y).abs()) >= 2.0;
    same_size && moved && new.w > 0.0 && new.h > 0.0
}

/// How far a finger has to have gone on a task for a move of its layers to
/// be read as Android moving it with that finger (logical px).
pub const MOVE_SLOP: f64 = 6.0;

/// The Android rules' settings (duo-wc.conf `android_caption`,
/// `android_shadow`, `android_task_helper`).
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// The height of Android's freeform caption bar (logical px): how far a
    /// full-screen Android window is moved up so that the bar is off the
    /// glass.
    pub caption: f64,
    /// The task layer's margin for its shadow (logical px): the caption
    /// bar starts this far below the top of the task's layers.
    pub shadow: f64,
    /// The root helper that sizes a task inside Android (run with sudo -n).
    pub helper: String,
}

impl Default for Config {
    fn default() -> Self {
        // Android's caption bar is 106 px of the 2.5x display (LineageOS 20,
        // `dumpsys activity top`: android:id/caption 0,0-1345,106): 42.4.
        Config { caption: 43.0, shadow: 10.0, helper: "/usr/local/sbin/duo2-android-task".to_owned() }
    }
}

/// Whether `(x, y)` is on the middle of an Android task's caption bar
/// (`content` is the task's layers' union: the bar is the top
/// `shadow + caption` of it; its left end is Android's back button, its
/// right end minimize, maximize and close: the middle 40 % is free).
pub fn in_caption_middle(content: Rect, cfg: &Config, x: f64, y: f64) -> bool {
    y >= content.y
        && y < content.y + cfg.shadow + cfg.caption
        && x >= content.x + content.w * 0.3
        && x < content.x + content.w * 0.7
}

/// A quick tap: on window `id`, down at `at` (global) at `down` ms, lifted at
/// `up` ms, having moved `moved` px.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Tap {
    /// The window.
    pub id: u64,
    /// Where it went down.
    pub at: (f64, f64),
    /// When it lifted (the input events' clock, ms).
    pub up: u32,
}

/// A tap that is quick and still enough to count (the thresholds of the
/// compositor's own double tap on a window's top, `crate::windrag`).
pub fn is_tap(down: u32, up: u32, moved: f64) -> bool {
    up.wrapping_sub(down) <= 280 && moved <= 10.0
}

/// Whether `second` makes a double tap with `first`: the same window,
/// within 420 ms (lift to lift) and 40 px.
pub fn is_double_tap(first: &Tap, second: &Tap) -> bool {
    first.id == second.id
        && second.up.wrapping_sub(first.up) <= 420
        && (first.at.0 - second.at.0).hypot(first.at.1 - second.at.1) <= 40.0
}

/// The Android display size (px, as Waydroid computes it: the window's
/// logical size times the scale, rounded down) a window of `w` x `h`
/// logical px gives.
pub fn display_px(w: f64, h: f64, scale: f64) -> (u32, u32) {
    ((w * scale).floor().max(0.0) as u32, (h * scale).floor().max(0.0) as u32)
}

/// How long after a Waydroid window went a new one with its app id is the
/// same window made again (ms). Measured 2026-11-12: 30-50 ms after a
/// resize for the window resized, up to 1.1 s for the others.
pub const RECREATE_MS: u64 = 3000;

/// A Waydroid window that went, kept for the one Waydroid makes in its
/// place.
#[derive(Debug, Clone)]
pub struct Gone<T> {
    /// Its app id (`waydroid.<package>`, or `Waydroid`).
    pub app_id: String,
    /// When it went.
    pub at: Instant,
    /// What the new one takes over.
    pub state: T,
}

/// Forget the windows gone longer than [`RECREATE_MS`] ago; whether any.
pub fn prune_gone<T>(gone: &mut Vec<Gone<T>>, now: Instant) -> bool {
    let before = gone.len();
    let limit = Duration::from_millis(RECREATE_MS);
    gone.retain(|g| now.saturating_duration_since(g.at) <= limit);
    gone.len() != before
}

/// What a new Waydroid window with `app_id` takes over: the one with that
/// app id that went last (within [`RECREATE_MS`]). Taken out of `gone`.
pub fn take_gone<T>(gone: &mut Vec<Gone<T>>, app_id: &str, now: Instant) -> Option<T> {
    prune_gone(gone, now);
    let i = gone.iter().rposition(|g| g.app_id == app_id)?;
    Some(gone.remove(i).state)
}

/// A Waydroid window (or one just gone) as the display size sees it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sizing {
    /// When it was laid out last (larger: later).
    pub rank: u64,
    /// Spanning, full screen or a half: not the plain size of a panel.
    pub big: bool,
    /// The size its own place gives it (logical px).
    pub size: (i32, i32),
}

/// The lead among `windows`: the big one laid out last, else the one laid
/// out last.
pub fn lead(windows: &[Sizing]) -> Option<Sizing> {
    let last = |only_big: bool| windows.iter().filter(|w| w.big || !only_big).max_by_key(|w| w.rank).copied();
    last(true).or_else(|| last(false))
}

/// The size of the Android display, which every Waydroid window is
/// configured to: the lead's.
pub fn display_size(windows: &[Sizing]) -> Option<(i32, i32)> {
    lead(windows).map(|w| w.size)
}

/// Whether a big window has to give up its bigness for a lead of `lead`
/// size (a window that just became big): one size is the display's.
pub fn conflicts(lead: (i32, i32), other: Sizing) -> bool {
    other.big && other.size != lead
}

/// Whether a task is sized to its whole display inside Android: when its
/// window spans both panels or is full screen.
pub fn wants_fill(span: bool, full: bool) -> bool {
    span || full
}

/// Whether a new task may lie outside the part of the display its window
/// shows (`own`): Android placed it on a display of `display` (the
/// lead's), which is bigger. Then it is moved into its part.
pub fn needs_fit(display: (i32, i32), own: (i32, i32)) -> bool {
    display.0 > own.0 || display.1 > own.1
}

/// The part of the Android display a window that is not filled shows its
/// task in (logical px, from the window's corner): its own place, cut to the
/// display where that is smaller (another window leads it at a half's
/// size: the window shows the whole display then, and no more).
pub fn fit_box(own: (i32, i32), display: (i32, i32)) -> (i32, i32) {
    (own.0.min(display.0), own.1.min(display.1))
}

/// Whether a task that is not filled has to be fitted into `now` (its
/// [`fit_box`] at present) again. It is known to lie inside `inside` (the
/// box it was last fitted to, or the one Android placed it in), and so
/// inside every box that holds that one: only a box narrower or lower in
/// either direction needs a fit. None (just back from a fill with the
/// bounds it had before, from another place or orientation): not known,
/// fitted.
pub fn needs_refit(inside: Option<(i32, i32)>, now: (i32, i32)) -> bool {
    match inside {
        None => true,
        Some(was) => now.0 < was.0 || now.1 < was.1,
    }
}

/// How long a refit waits after the last change that asked for it (ms): a
/// turn reconfigures one panel after the other, and Waydroid then makes its
/// windows again (up to 1.1 s): one fit per task for the lot.
pub const REFIT_QUIET_MS: u64 = 800;

/// The longest a refit waits after the first change that asked for it (ms):
/// changes that keep coming (a storm of turns) do not hold it off for ever.
pub const REFIT_MAX_MS: u64 = 3000;

/// When the refit runs: None now, else after that much longer.
/// `since_first` / `since_last`: the time since the first / the last change
/// that asked for it.
pub fn refit_wait(since_first: Duration, since_last: Duration) -> Option<Duration> {
    let quiet = Duration::from_millis(REFIT_QUIET_MS);
    let max = Duration::from_millis(REFIT_MAX_MS);
    if since_last >= quiet || since_first >= max {
        return None;
    }
    Some((quiet - since_last).min(max - since_first))
}

/// Whether `name` is an Android package name (letters, digits, `_`,
/// dot-separated, at least two parts).
pub fn is_package(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && name.contains('.')
        && name
            .split('.')
            .all(|part| !part.is_empty() && part.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
}

/// The apps that open in full screen (the user put them there last time:
/// Waydroid never tells the compositor that an app wants full screen, so
/// a game's own wish cannot be seen). Read from duo-wc's state file, one
/// package per line; anything else is left out.
pub fn parse_full_apps(text: &str) -> Vec<String> {
    let mut apps: Vec<String> = Vec::new();
    for line in text.lines().map(str::trim) {
        if is_package(line) && !apps.iter().any(|a| a == line) {
            apps.push(line.to_owned());
        }
    }
    apps
}

/// The state file's text for these apps.
pub fn full_apps_text(apps: &[String]) -> String {
    apps.iter().map(|a| format!("{a}\n")).collect()
}

/// Where a touch is for the client, relative to the surface it went down
/// on: `location` (global) less where that surface is NOW (`origin_now`).
/// Smithay keeps the surface's place at the touch-down (`origin_then`) and
/// subtracts that from every motion: this is the location to hand it so
/// that what the client gets is relative to the surface as it is now.
pub fn follow_surface(location: (f64, f64), origin_then: (f64, f64), origin_now: (f64, f64)) -> (f64, f64) {
    (location.0 + origin_then.0 - origin_now.0, location.1 + origin_then.1 - origin_now.1)
}

/// A window as the keyboard's placement sees it.
#[derive(Debug, Clone, PartialEq)]
pub struct Candidate<'a> {
    /// Its id.
    pub id: u64,
    /// Its app id.
    pub app_id: Option<&'a str>,
    /// Set aside (home).
    pub minimized: bool,
}

/// The window Android's keyboard belongs to: the one with the keyboard
/// focus if it is an Android app, else the front-most Android app that is
/// not set aside (touching a text field raises its window). `windows` are
/// front first.
pub fn ime_companion(windows: &[Candidate<'_>], focus: Option<u64>) -> Option<u64> {
    let usable = |c: &&Candidate<'_>| is_task(c.app_id) && !c.minimized;
    if let Some(f) = windows.iter().filter(usable).find(|c| Some(c.id) == focus) {
        return Some(f.id);
    }
    windows.iter().find(usable).map(|c| c.id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_ids() {
        assert!(is_waydroid(Some("waydroid.com.android.settings")));
        assert!(is_waydroid(Some("Waydroid")));
        assert!(!is_waydroid(Some("org.gnome.TextEditor")));
        assert!(!is_waydroid(None));
        assert!(is_ime(Some("waydroid.InputMethod")));
        assert!(!is_ime(Some("waydroid.com.android.vending")));
        assert!(is_task(Some("waydroid.com.android.vending")));
        assert!(!is_task(Some("waydroid.InputMethod")));
        assert!(!is_task(Some("Waydroid")));
        assert!(!is_task(Some("foot")));
    }

    #[test]
    fn the_caption_bars_middle_and_double_taps() {
        let cfg = Config::default();
        // A medium task (412 x 538) with its 10 px shadow margin.
        let content = Rect::new(52.0, 65.0, 432.0, 558.0);
        assert!(in_caption_middle(content, &cfg, 268.0, 90.0));
        assert!(in_caption_middle(content, &cfg, 268.0, 65.0));
        // Below the bar, at its ends (Android's buttons), above it.
        assert!(!in_caption_middle(content, &cfg, 268.0, 120.0));
        assert!(!in_caption_middle(content, &cfg, 80.0, 90.0));
        assert!(!in_caption_middle(content, &cfg, 470.0, 90.0));
        assert!(!in_caption_middle(content, &cfg, 268.0, 60.0));
        assert!(is_tap(1000, 1200, 4.0));
        assert!(!is_tap(1000, 1300, 4.0));
        assert!(!is_tap(1000, 1100, 20.0));
        let a = Tap { id: 3, at: (268.0, 90.0), up: 1000 };
        assert!(is_double_tap(&a, &Tap { id: 3, at: (280.0, 95.0), up: 1300 }));
        assert!(!is_double_tap(&a, &Tap { id: 4, at: (280.0, 95.0), up: 1300 }));
        assert!(!is_double_tap(&a, &Tap { id: 3, at: (280.0, 95.0), up: 1500 }));
        assert!(!is_double_tap(&a, &Tap { id: 3, at: (350.0, 95.0), up: 1300 }));
        // Waydroid's own rounding of the display size.
        assert_eq!(display_px(538.0, 757.0 + 43.0, 2.5), (1345, 2000));
        assert_eq!(display_px(538.0, 709.0, 2.5), (1345, 1772));
    }

    /// Android moving a task with the finger, through Waydroid: the task's
    /// layer is a subsurface; Waydroid passes Android `surface-local + the
    /// layer's place NOW`; Android's task positioner puts the task where
    /// the finger has gone since the drag began. Returns where the task
    /// ends up after the finger went `steps` px to the right, 1 px a frame.
    fn android_drag(steps: usize, follow: bool) -> f64 {
        let mut task = 100.0; // the layer's place (logical px)
        let start_task = task;
        let finger0 = 150.0; // the finger on the caption bar
        let origin_then = task; // Smithay's surface place at the touch-down
        for i in 1..=steps {
            let finger = finger0 + i as f64;
            let given = if follow {
                follow_surface((finger, 0.0), (origin_then, 0.0), (task, 0.0)).0
            } else {
                finger
            };
            // What Smithay sends, what Waydroid makes of it.
            let local = given - origin_then;
            let android_x = local + task;
            task = start_task + (android_x - finger0);
        }
        task
    }

    #[test]
    fn touches_follow_the_surface_or_the_window_runs_away() {
        // The window slid away from under the finger (2026-11-08).
        // Without following the surface, every step the task moved is
        // added again: 30 px of finger moved it 465 px.
        assert!(android_drag(30, false) > 400.0);
        // Following it, the task moves exactly with the finger.
        assert_eq!(android_drag(30, true), 130.0);
        assert_eq!(follow_surface((10.0, 20.0), (0.0, 0.0), (5.0, -5.0)), (5.0, 25.0));
    }

    #[test]
    fn a_task_moved_is_the_same_size_elsewhere() {
        let a = Rect::new(60.0, 75.0, 412.0, 538.0);
        assert!(task_moved(a, a.offset(30.0, 0.0)));
        assert!(task_moved(a, a.offset(0.0, -3.0)));
        // Not moved, or not just moved.
        assert!(!task_moved(a, a));
        assert!(!task_moved(a, a.offset(1.0, 1.0)));
        assert!(!task_moved(a, Rect::new(0.0, 0.0, 538.0, 709.0)));
        assert!(!task_moved(a, Rect::new(90.0, 75.0, 412.0, 300.0)));
        // A sub-pixel size wobble (rounding) still counts as the same size.
        assert!(task_moved(a, Rect::new(90.0, 75.0, 412.6, 538.0)));
        assert!(!task_moved(a, Rect::new(90.0, 75.0, 0.0, 0.0)));
    }

    #[test]
    fn a_window_made_again_is_found_by_its_app_id() {
        let t0 = Instant::now();
        let at = |ms: u64| t0 + Duration::from_millis(ms);
        let gone = |app: &str, ms: u64, state: u32| Gone { app_id: app.to_owned(), at: at(ms), state };
        let mut list = vec![gone("waydroid.a", 0, 1), gone("waydroid.b", 10, 2), gone("waydroid.a", 20, 3)];
        // The last one of that app id, once.
        assert_eq!(take_gone(&mut list, "waydroid.a", at(60)), Some(3));
        assert_eq!(take_gone(&mut list, "waydroid.a", at(70)), Some(1));
        assert_eq!(take_gone(&mut list, "waydroid.a", at(80)), None);
        // Another app id is not it; too late is not it.
        assert_eq!(take_gone(&mut list, "waydroid.c", at(90)), None);
        assert_eq!(take_gone(&mut list, "waydroid.b", at(10 + RECREATE_MS + 1)), None);
        assert!(list.is_empty());
        let mut list = vec![gone("Waydroid", 0, 7)];
        assert!(!prune_gone(&mut list, at(RECREATE_MS)));
        assert!(prune_gone(&mut list, at(RECREATE_MS + 1)));
    }

    fn sz(rank: u64, big: bool, size: (i32, i32)) -> Sizing {
        Sizing { rank, big, size }
    }

    #[test]
    fn the_display_takes_the_big_window_laid_out_last() {
        let panel = (538, 709);
        let span = (1102, 709);
        let full = (538, 800);
        // Panels only: the last laid out (all the same size anyway).
        assert_eq!(lead(&[sz(1, false, panel), sz(2, false, (300, 709))]), Some(sz(2, false, (300, 709))));
        // A spanning one leads, even laid out before a panel's window.
        assert_eq!(display_size(&[sz(3, false, panel), sz(2, true, span)]), Some(span));
        // Of two big ones, the later.
        assert_eq!(display_size(&[sz(2, true, span), sz(4, true, full), sz(5, false, panel)]), Some(full));
        assert_eq!(display_size(&[]), None);
        // Spanning ends a full screen elsewhere (another size); two
        // spanning windows, or full screen on each panel, are one size.
        assert!(conflicts(span, sz(1, true, full)));
        assert!(!conflicts(span, sz(1, true, span)));
        assert!(!conflicts(full, sz(1, true, full)));
        assert!(!conflicts(span, sz(1, false, panel)));
    }

    /// Waydroid's display (its hwcomposer, lineage-20): every configure of a
    /// new size resizes it, and every resize makes all its windows again,
    /// each configured once more. Returns how many resizes happen for
    /// windows configured to `sizes` (at most 20: a loop).
    fn waydroid_resizes(sizes: &[(i32, i32)]) -> usize {
        let mut display = (538, 709);
        let mut resizes = 0;
        loop {
            let before = resizes;
            for size in sizes {
                if *size != display {
                    display = *size;
                    resizes += 1;
                }
            }
            if resizes == before || resizes >= 20 {
                return resizes.min(20);
            }
        }
    }

    #[test]
    fn one_size_for_every_waydroid_window_or_the_display_loops() {
        let span = (1102, 709);
        let panel = (538, 709);
        // Each window at its own size: a spanning app and one on a panel
        // resize the display back and forth forever.
        assert_eq!(waydroid_resizes(&[span, panel]), 20);
        // All at the lead's size: one resize.
        let windows = [sz(2, true, span), sz(3, false, panel)];
        let shared = display_size(&windows).unwrap();
        assert_eq!(waydroid_resizes(&[shared, shared]), 1);
        // The other app's task was placed by Android on the bigger display:
        // it is kept inside its own panel's part.
        assert!(needs_fit(shared, panel));
        assert!(!needs_fit(panel, panel));
        assert!(needs_fit((538, 800), panel));
        assert!(wants_fill(true, false) && wants_fill(false, true) && !wants_fill(false, false));
    }

    #[test]
    fn a_task_is_fitted_again_when_its_part_of_the_display_shrinks() {
        let portrait = (538, 709);
        let landscape = (757, 490);
        let span = (1102, 709);
        // On its own panel, at the display's size, or behind a spanning
        // lead: its own place. A lead of a half's size: that half.
        assert_eq!(fit_box(portrait, portrait), portrait);
        assert_eq!(fit_box(portrait, span), portrait);
        assert_eq!(fit_box(portrait, (269, 709)), (269, 709));
        // A turn: lower (portrait to landscape) or narrower (back), each a
        // fit; the same place again, or a bigger one, none.
        assert!(needs_refit(Some(portrait), landscape));
        assert!(needs_refit(Some(landscape), portrait));
        assert!(!needs_refit(Some(portrait), portrait));
        assert!(!needs_refit(Some((269, 709)), portrait));
        // Into a half: narrower.
        assert!(needs_refit(Some(portrait), (269, 709)));
        // Back from a span (the bounds from before it): always.
        assert!(needs_refit(None, portrait));
    }

    #[test]
    fn a_refit_waits_for_the_changes_to_settle() {
        let ms = Duration::from_millis;
        // The last change just now: the whole quiet time.
        assert_eq!(refit_wait(ms(0), ms(0)), Some(ms(REFIT_QUIET_MS)));
        // A change 300 ms after the first: quiet counted from it.
        assert_eq!(refit_wait(ms(500), ms(200)), Some(ms(REFIT_QUIET_MS - 200)));
        // Quiet long enough: now.
        assert_eq!(refit_wait(ms(2000), ms(REFIT_QUIET_MS)), None);
        // Changes that keep coming: at most REFIT_MAX_MS after the first.
        assert_eq!(refit_wait(ms(REFIT_MAX_MS - 100), ms(50)), Some(ms(100)));
        assert_eq!(refit_wait(ms(REFIT_MAX_MS), ms(10)), None);
    }

    #[test]
    fn the_apps_that_open_full_screen() {
        assert!(is_package("com.trundleway.game"));
        assert!(is_package("org.mozilla.fenix_beta"));
        assert!(!is_package("game"));
        assert!(!is_package(".com.x") && !is_package("com..x") && !is_package("com.x."));
        assert!(!is_package("com.x/../y") && !is_package("com.x y") && !is_package(""));
        let apps =
            parse_full_apps("com.trundleway.game\n  com.example.b \n\n# not one\ncom.trundleway.game\nbad\n");
        assert_eq!(apps, vec!["com.trundleway.game".to_owned(), "com.example.b".to_owned()]);
        assert_eq!(parse_full_apps(&full_apps_text(&apps)), apps);
        assert_eq!(full_apps_text(&[]), "");
    }

    fn c(id: u64, app_id: &str, minimized: bool) -> Candidate<'_> {
        Candidate { id, app_id: Some(app_id), minimized }
    }

    #[test]
    fn the_keyboard_goes_with_the_android_window_typed_in() {
        // Front first: a GTK app, the keyboard itself, two Android apps.
        let windows = [
            c(5, "org.gnome.TextEditor", false),
            c(9, "waydroid.InputMethod", false),
            c(4, "waydroid.com.android.vending", false),
            c(2, "waydroid.com.android.settings", false),
        ];
        // The focus on an Android app: that one.
        assert_eq!(ime_companion(&windows, Some(2)), Some(2));
        // The focus elsewhere: the front-most Android app.
        assert_eq!(ime_companion(&windows, Some(5)), Some(4));
        assert_eq!(ime_companion(&windows, None), Some(4));
        // Never the keyboard itself, never one set aside.
        assert_eq!(ime_companion(&windows, Some(9)), Some(4));
        let windows = [c(9, "waydroid.InputMethod", false), c(4, "waydroid.com.android.vending", true)];
        assert_eq!(ime_companion(&windows, Some(4)), None);
        assert_eq!(ime_companion(&[], None), None);
    }
}
