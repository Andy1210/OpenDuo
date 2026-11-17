//! Who is shown, who is told it is not (xdg_toplevel `suspended`), and the
//! slow frames of the hidden.
//!
//! A window is **paced** by one output: the panel it is placed on while that
//! panel is lit, or for a spanning window the first lit panel (left to
//! right), so it gets one frame callback a refresh, never two. Its fifo
//! barrier (wp_fifo_v1) is released when that output renders, the same
//! moment its frame callbacks go out: one content update a refresh.
//!
//! A window is **hidden** (and `suspended`, xdg-shell v6) when nothing of it
//! is drawn: its panel is dark (or no lit panel paces it), it is set aside
//! (home) while the overview is closed, every lit panel it shows on has a
//! fullscreen window in front of it, an ambient screen (peek, glance) shows,
//! or the session is locked. A hidden window is not paced: its frame
//! callbacks, its fifo barrier and its commit timers are served by a slow
//! tick instead, at most once a [`HIDDEN_PERIOD`] per window, and only when
//! it asked for something (a commit since the last one), so a client that
//! waits on them never hangs and one that ignores `suspended` draws once a
//! second, not at the panel's rate; a dark phone whose clients are idle does
//! not wake at all. The first slow frame of a newly hidden window is
//! immediate (a window opened on a dark panel learns at once that it is
//! shown nowhere: SDL marks it occluded only at its first frame callback).
//!
//! Pure: no Smithay types; `crate::state` feeds it the scene.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

/// At most one frame (frame callbacks, fifo barrier, commit timers) a
/// hidden window gets in this long.
pub const HIDDEN_PERIOD: Duration = Duration::from_secs(1);

/// A commit timer (wp_commit_timing_v1) is served this long before its
/// target, so the update it holds is drawn at the refresh nearest to it (the
/// panels run at 90 Hz).
pub const COMMIT_TIMING_LEAD: Duration = Duration::from_micros(11_111);

/// Why a window is hidden.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Why {
    /// Its panel (or every panel, for a spanning one) is dark.
    Dark,
    /// Set aside (home), the overview closed.
    Home,
    /// A fullscreen window is in front of it on every lit panel it shows on.
    Covered,
    /// An ambient screen (peek, glance) shows: no window is drawn.
    Ambient,
    /// The session is locked: only the lock screen is drawn.
    Locked,
}

impl Why {
    /// For logs and the control socket.
    pub fn name(self) -> &'static str {
        match self {
            Why::Dark => "dark",
            Why::Home => "home",
            Why::Covered => "covered",
            Why::Ambient => "ambient",
            Why::Locked => "locked",
        }
    }
}

/// One window as the pacing rules see it.
#[derive(Clone, Debug, Default)]
pub struct Win {
    /// Its number (`WindowData::id`).
    pub id: u64,
    /// The output it is placed on; None: it spans them all.
    pub on: Option<String>,
    /// The outputs it is drawn on (its box overlaps them and its placement
    /// lets it show there).
    pub shows_on: Vec<String>,
    /// Set aside (home).
    pub minimized: bool,
    /// The outputs it covers whole and opaquely: fullscreen, drawn at its
    /// size, its box over all of the output (empty for any other window).
    pub covers: Vec<String>,
}

/// Everything the decision needs.
#[derive(Clone, Debug, Default)]
pub struct Scene {
    /// Back to front.
    pub windows: Vec<Win>,
    /// The outputs left to right, and whether each is lit.
    pub outputs: Vec<(String, bool)>,
    /// The task overview is open: every window is a live card.
    pub overview: bool,
    /// An ambient screen shows.
    pub ambient: bool,
    /// The session is locked.
    pub locked: bool,
}

/// What one window gets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pace {
    /// The output whose refresh paces it; None while hidden.
    pub paced_by: Option<String>,
    /// Why it is hidden (then `suspended`); None: shown.
    pub hidden: Option<Why>,
}

/// Decide for every window.
pub fn decide(scene: &Scene) -> BTreeMap<u64, Pace> {
    let lit = |name: &str| scene.outputs.iter().any(|(o, l)| o == name && *l);
    let first_lit = scene.outputs.iter().find(|(_, l)| *l).map(|(o, _)| o.clone());
    let mut out = BTreeMap::new();
    for (i, win) in scene.windows.iter().enumerate() {
        let paced = match &win.on {
            Some(name) if scene.outputs.iter().any(|(o, _)| o == name) => lit(name).then(|| name.clone()),
            // Its output went away: any lit one paces it.
            Some(_) => first_lit.clone(),
            None => first_lit.clone(),
        };
        let covered = !scene.overview && {
            let in_front = &scene.windows[i + 1..];
            let mut lit_shown = win.shows_on.iter().filter(|o| lit(o)).peekable();
            lit_shown.peek().is_some()
                && lit_shown.all(|o| in_front.iter().any(|f| !f.minimized && f.covers.iter().any(|c| c == o)))
        };
        let hidden = if scene.locked {
            Some(Why::Locked)
        } else if scene.ambient {
            Some(Why::Ambient)
        } else if paced.is_none() {
            Some(Why::Dark)
        } else if win.minimized && !scene.overview {
            Some(Why::Home)
        } else if covered {
            Some(Why::Covered)
        } else {
            None
        };
        let paced_by = if hidden.is_some() { None } else { paced };
        out.insert(win.id, Pace { paced_by, hidden });
    }
    out
}

/// When a hidden window's next slow frame may go out: at once if it never
/// had one since it was last shown, a period after the last one otherwise.
pub fn due(last: Option<Instant>, now: Instant) -> Instant {
    match last {
        Some(last) => (last + HIDDEN_PERIOD).max(now),
        None => now,
    }
}

/// When to serve a commit timer whose target is `target` (the presentation
/// clock, as a duration since its epoch), `now` on the same clock: a refresh
/// early, never in the past. -> how long from now.
pub fn commit_timer_wait(target: Duration, now: Duration) -> Duration {
    target.saturating_sub(COMMIT_TIMING_LEAD).saturating_sub(now)
}

/// The one slow tick: its timer is armed for the earliest window that has
/// something waiting, and moved only to an earlier time.
#[derive(Debug, Default)]
pub struct Tick {
    /// When the armed timer fires.
    pub at: Option<Instant>,
}

impl Tick {
    /// Something waits from `at` on. -> Some(time) when the timer is to be
    /// (re)armed for it, None when the armed one comes soon enough.
    pub fn want(&mut self, at: Instant) -> Option<Instant> {
        match self.at {
            Some(armed) if armed <= at => None,
            _ => {
                self.at = Some(at);
                Some(at)
            }
        }
    }

    /// The timer fired (or was dropped).
    pub fn fired(&mut self) {
        self.at = None;
    }
}

/// Take a fifo barrier out of its slot and release it. -> whether one was
/// still holding a content update back (so the client's queued commits are
/// to be looked at again).
pub fn release<B: FifoBarrier>(slot: &mut Option<B>) -> bool {
    match slot.take() {
        Some(barrier) if !barrier.released() => {
            barrier.release();
            true
        }
        _ => false,
    }
}

/// A fifo barrier (Smithay's `Barrier`; a stand-in in the tests).
pub trait FifoBarrier {
    /// Released already.
    fn released(&self) -> bool;
    /// Let the content updates that wait on it go.
    fn release(&self);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::rc::Rc;

    fn out(name: &str, lit: bool) -> (String, bool) {
        (name.to_owned(), lit)
    }

    fn on(id: u64, output: &str) -> Win {
        Win { id, on: Some(output.into()), shows_on: vec![output.into()], ..Default::default() }
    }

    fn span(id: u64) -> Win {
        Win { id, on: None, shows_on: vec!["DSI-1".into(), "DSI-2".into()], ..Default::default() }
    }

    fn both_lit() -> Vec<(String, bool)> {
        vec![out("DSI-1", true), out("DSI-2", true)]
    }

    fn shown(by: &str) -> Pace {
        Pace { paced_by: Some(by.into()), hidden: None }
    }

    fn hidden(why: Why) -> Pace {
        Pace { paced_by: None, hidden: Some(why) }
    }

    #[test]
    fn a_window_is_paced_by_its_lit_panel() {
        let scene = Scene {
            windows: vec![on(1, "DSI-1"), on(2, "DSI-2")],
            outputs: both_lit(),
            ..Default::default()
        };
        let d = decide(&scene);
        assert_eq!(d[&1], shown("DSI-1"));
        assert_eq!(d[&2], shown("DSI-2"));
    }

    #[test]
    fn a_dark_panel_hides_its_windows() {
        let scene = Scene {
            windows: vec![on(1, "DSI-1"), on(2, "DSI-2")],
            outputs: vec![out("DSI-1", true), out("DSI-2", false)],
            ..Default::default()
        };
        let d = decide(&scene);
        assert_eq!(d[&1], shown("DSI-1"));
        assert_eq!(d[&2], hidden(Why::Dark));
    }

    #[test]
    fn a_blanked_screen_hides_everything() {
        let scene = Scene {
            windows: vec![on(1, "DSI-1"), span(2)],
            outputs: vec![out("DSI-1", false), out("DSI-2", false)],
            ..Default::default()
        };
        let d = decide(&scene);
        assert_eq!(d[&1], hidden(Why::Dark));
        assert_eq!(d[&2], hidden(Why::Dark));
    }

    #[test]
    fn a_spanning_window_is_paced_by_the_first_lit_panel() {
        let both = Scene { windows: vec![span(1)], outputs: both_lit(), ..Default::default() };
        assert_eq!(decide(&both)[&1], shown("DSI-1"));
        let right = Scene {
            windows: vec![span(1)],
            outputs: vec![out("DSI-1", false), out("DSI-2", true)],
            ..Default::default()
        };
        assert_eq!(decide(&right)[&1], shown("DSI-2"));
    }

    #[test]
    fn a_window_whose_output_went_is_paced_by_a_lit_one() {
        let scene = Scene { windows: vec![on(1, "DP-1")], outputs: both_lit(), ..Default::default() };
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
    }

    #[test]
    fn home_hides_until_the_overview_opens() {
        let mut w = on(1, "DSI-1");
        w.minimized = true;
        let mut scene = Scene { windows: vec![w], outputs: both_lit(), ..Default::default() };
        assert_eq!(decide(&scene)[&1], hidden(Why::Home));
        scene.overview = true;
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
    }

    #[test]
    fn a_fullscreen_window_in_front_covers_the_one_behind() {
        let behind = on(1, "DSI-1");
        let mut full = on(2, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        let other = on(3, "DSI-2");
        let scene = Scene { windows: vec![behind, full, other], outputs: both_lit(), ..Default::default() };
        let d = decide(&scene);
        assert_eq!(d[&1], hidden(Why::Covered));
        assert_eq!(d[&2], shown("DSI-1"));
        assert_eq!(d[&3], shown("DSI-2"));
    }

    #[test]
    fn a_fullscreen_window_behind_covers_nothing() {
        let mut full = on(1, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        let front = on(2, "DSI-1");
        let scene = Scene { windows: vec![full, front], outputs: both_lit(), ..Default::default() };
        let d = decide(&scene);
        assert_eq!(d[&1], shown("DSI-1"));
        assert_eq!(d[&2], shown("DSI-1"));
    }

    #[test]
    fn a_spanning_window_is_covered_only_where_it_shows_lit() {
        let mut full = on(2, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        let scene = Scene { windows: vec![span(1), full.clone()], outputs: both_lit(), ..Default::default() };
        // Still seen on DSI-2.
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
        // DSI-2 dark: nothing of it is seen.
        let scene = Scene {
            windows: vec![span(1), full],
            outputs: vec![out("DSI-1", true), out("DSI-2", false)],
            ..Default::default()
        };
        assert_eq!(decide(&scene)[&1], hidden(Why::Covered));
    }

    #[test]
    fn a_minimized_fullscreen_window_covers_nothing() {
        let mut full = on(2, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        full.minimized = true;
        let scene = Scene { windows: vec![on(1, "DSI-1"), full], outputs: both_lit(), ..Default::default() };
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
    }

    #[test]
    fn the_overview_shows_covered_windows() {
        let mut full = on(2, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        let scene = Scene {
            windows: vec![on(1, "DSI-1"), full],
            outputs: both_lit(),
            overview: true,
            ..Default::default()
        };
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
    }

    #[test]
    fn a_window_not_drawn_anywhere_yet_is_not_covered() {
        // No buffer yet: its box overlaps nothing.
        let mut new = on(1, "DSI-1");
        new.shows_on.clear();
        let mut full = on(2, "DSI-1");
        full.covers = vec!["DSI-1".into()];
        let scene = Scene { windows: vec![new, full], outputs: both_lit(), ..Default::default() };
        assert_eq!(decide(&scene)[&1], shown("DSI-1"));
    }

    #[test]
    fn ambient_and_lock_hide_every_window() {
        let ambient =
            Scene { windows: vec![on(1, "DSI-2")], outputs: both_lit(), ambient: true, ..Default::default() };
        assert_eq!(decide(&ambient)[&1], hidden(Why::Ambient));
        let locked =
            Scene { windows: vec![on(1, "DSI-2")], outputs: both_lit(), locked: true, ..Default::default() };
        assert_eq!(decide(&locked)[&1], hidden(Why::Locked));
    }

    #[test]
    fn slow_frames_once_a_period() {
        let t0 = Instant::now();
        // Never had one (or shown since): at once.
        assert_eq!(due(None, t0), t0);
        // Just had one: a period later.
        assert_eq!(due(Some(t0), t0 + Duration::from_millis(10)), t0 + HIDDEN_PERIOD);
        // Long ago: at once.
        let late = t0 + Duration::from_secs(5);
        assert_eq!(due(Some(t0), late), late);
    }

    #[test]
    fn the_tick_moves_only_earlier() {
        let t0 = Instant::now();
        let mut tick = Tick::default();
        assert_eq!(tick.want(t0 + HIDDEN_PERIOD), Some(t0 + HIDDEN_PERIOD));
        // Later than the armed one: it serves that too.
        assert_eq!(tick.want(t0 + 2 * HIDDEN_PERIOD), None);
        // Earlier: re-armed.
        assert_eq!(tick.want(t0), Some(t0));
        tick.fired();
        assert_eq!(tick.at, None);
        assert_eq!(tick.want(t0 + HIDDEN_PERIOD), Some(t0 + HIDDEN_PERIOD));
    }

    #[test]
    fn a_commit_timer_is_served_a_refresh_early_never_in_the_past() {
        let now = Duration::from_secs(100);
        assert_eq!(
            commit_timer_wait(now + Duration::from_millis(50), now),
            Duration::from_millis(50) - COMMIT_TIMING_LEAD
        );
        assert_eq!(commit_timer_wait(now + Duration::from_millis(5), now), Duration::ZERO);
        assert_eq!(commit_timer_wait(now - Duration::from_secs(1), now), Duration::ZERO);
    }

    #[derive(Clone, Default)]
    struct FakeBarrier(Rc<Cell<bool>>);

    impl FifoBarrier for FakeBarrier {
        fn released(&self) -> bool {
            self.0.get()
        }
        fn release(&self) {
            self.0.set(true)
        }
    }

    #[test]
    fn a_barrier_is_released_once_and_taken_out() {
        let barrier = FakeBarrier::default();
        // The blocked content update holds a clone of the same barrier.
        let waiting = barrier.clone();
        let mut slot = Some(barrier);
        assert!(release(&mut slot));
        assert!(slot.is_none());
        assert!(waiting.released());
        // Nothing left: nothing to release, no client to look at.
        assert!(!release(&mut slot));
        // One released already (by an earlier render) holds nothing back.
        let done = FakeBarrier::default();
        done.release();
        let mut slot = Some(done);
        assert!(!release(&mut slot));
        assert!(slot.is_none());
    }
}
