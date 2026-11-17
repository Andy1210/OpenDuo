//! Poses: how the two panels are used, set over the control socket for now (the
//! hinge and orientation sensors will pick them later).
//!
//! - **dual**: two separate panels, each with its own windows (the default).
//! - **single DSI-1 | DSI-2**: one panel; the other is switched off and its
//!   windows move to the one that stays.
//! - **span**: the front window covers both panels (with the hinge gap as set);
//!   a window opened in this pose spans too.
//!
//! A pose is a transformation of the windows' OWN placements (where the user or
//! the open rule put them). A window a pose moves keeps where it was, and the
//! next pose change first puts every such window back, then applies the new pose:
//! `single DSI-1` then `dual` returns the right panel's windows to it, and `span`
//! then `dual` returns the spanned window to its panel. Placing a window
//! explicitly (`place_window`) makes the new placement its own.

use serde::Serialize;

/// Where a window lives.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Placement {
    /// Maximized on the output with this connector name.
    Output(String),
    /// Across every panel in use: the bounding box of the layout, hinge included
    /// when the gap is on.
    Span,
}

/// How the panels are used.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum Pose {
    /// Two separate panels.
    #[default]
    Dual,
    /// Only this panel; the other one is off.
    Single(String),
    /// The front window across both panels.
    Span,
}

impl Pose {
    /// Parse `dual`, `single <output>`, `span`.
    pub fn parse(pose: &str, output: Option<&str>) -> Result<Pose, String> {
        match (pose.trim().to_ascii_lowercase().as_str(), output) {
            ("dual", None) => Ok(Pose::Dual),
            ("span", None) => Ok(Pose::Span),
            ("single", Some(output)) if !output.trim().is_empty() => {
                Ok(Pose::Single(output.trim().to_owned()))
            }
            ("single", _) => Err("single needs an output: single DSI-1 or single DSI-2".into()),
            ("dual" | "span", Some(_)) => Err(format!("{pose} takes no output")),
            (other, _) => Err(format!("unknown pose {other} (dual, single <output>, span)")),
        }
    }

    /// Whether the panel with this name shows anything in this pose.
    pub fn in_use(&self, output: &str) -> bool {
        // The poses are the panels': an external display is never "in use"
        // by one (it is lit by the power rules alone, crate::extout).
        if !crate::extout::is_panel(output) {
            return false;
        }
        match self {
            Pose::Single(only) => only == output,
            Pose::Dual | Pose::Span => true,
        }
    }

    /// The pose as the control socket reports it.
    pub fn describe(&self) -> String {
        match self {
            Pose::Dual => "dual".into(),
            Pose::Single(output) => format!("single {output}"),
            Pose::Span => "span".into(),
        }
    }
}

/// One window, as far as a pose is concerned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowPlace {
    /// The window's id.
    pub id: u64,
    /// Where it is now.
    pub placement: Placement,
    /// Where it was before a pose moved it (its own placement), if one did.
    pub saved: Option<Placement>,
}

/// Re-place every window for a new pose.
///
/// `front` is the window the user is looking at (the front window of the panel
/// touched last); in `span` it is the one that spans.
pub fn apply(pose: &Pose, windows: &mut [WindowPlace], front: Option<u64>) {
    // Back to the windows' own placements first.
    for window in windows.iter_mut() {
        if on_display(&window.placement) {
            // A window on an external display: the panels' pose is not its.
            continue;
        }
        if let Some(own) = window.saved.take() {
            window.placement = own;
        }
    }
    match pose {
        Pose::Dual => {}
        Pose::Single(only) => {
            let target = Placement::Output(only.clone());
            for window in windows.iter_mut().filter(|w| !on_display(&w.placement)) {
                if window.placement != target {
                    window.saved = Some(std::mem::replace(&mut window.placement, target.clone()));
                }
            }
        }
        Pose::Span => {
            if let Some(window) =
                windows.iter_mut().find(|w| Some(w.id) == front && !on_display(&w.placement))
            {
                if window.placement != Placement::Span {
                    window.saved = Some(std::mem::replace(&mut window.placement, Placement::Span));
                }
            }
        }
    }
}

/// Whether a placement is on an external display (no pose moves it).
fn on_display(placement: &Placement) -> bool {
    matches!(placement, Placement::Output(name) if crate::extout::is_external(name))
}

/// Where a NEW window goes in a pose, given where the dual rule would put it.
///
/// Returns its placement and, when the pose overrides the rule, the placement it
/// keeps as its own for when the pose ends.
pub fn new_window(pose: &Pose, by_rule: Placement) -> (Placement, Option<Placement>) {
    if on_display(&by_rule) {
        return (by_rule, None);
    }
    match pose {
        Pose::Dual => (by_rule, None),
        // It belongs to the panel that is there.
        Pose::Single(only) => (Placement::Output(only.clone()), None),
        Pose::Span => {
            if by_rule == Placement::Span {
                (Placement::Span, None)
            } else {
                (Placement::Span, Some(by_rule))
            }
        }
    }
}

/// Whether a double tap ([`DoubleTap`]) on the dark panel `tapped` switches the single pose to it:
/// the phone stands as a tent or a brochure (`brochure_*`: stood up, the
/// hinge past ~300 degrees; the posture cannot say which panel faces the
/// user, so duo-posed keeps the one in use), the other panel is the one in
/// use, and the screen is not blanked (a tap never wakes a dark screen).
/// Folded back (`flip_*`) the back panel's touches stay ignored.
pub fn tent_tap_switches(posture: Option<&str>, pose: &Pose, tapped: &str, screen_dark: bool) -> bool {
    let stood = posture.map(|p| p.starts_with("tent") || p.starts_with("brochure")).unwrap_or(false);
    stood && !screen_dark && matches!(pose, Pose::Single(lit) if lit != tapped)
}

/// A tap on a tent's dark panel lifts within this (ms) of going down.
pub const TENT_TAP_MS: u32 = 350;
/// The second tap of a double tap goes down within this (ms) of the first
/// one lifting.
pub const TENT_DOUBLE_GAP_MS: u32 = 400;
/// ... and within this (logical px of the panel) of where the first went down.
pub const TENT_DOUBLE_PX: f64 = 60.0;

/// The double tap that switches a tent or a brochure to its dark panel
/// (a single tap switched too easily: a hand holding the stood-up phone
/// touches the back panel). Pure: fed with each
/// tap on a dark panel (went down at `down`, lifted at `up`, at `at` in
/// the panel's logical frame), it says when two of them make a double tap:
/// both short ([`TENT_TAP_MS`]), on the same panel, the second down within
/// [`TENT_DOUBLE_GAP_MS`] of the first lifting and within
/// [`TENT_DOUBLE_PX`] of it.
#[derive(Debug, Default)]
pub struct DoubleTap {
    last: Option<(String, u32, (f64, f64))>,
}

impl DoubleTap {
    /// A tap ended; true when it completes a double tap (then it starts over).
    pub fn tap(&mut self, output: &str, down: u32, up: u32, at: (f64, f64)) -> bool {
        if up.wrapping_sub(down) > TENT_TAP_MS {
            self.last = None;
            return false;
        }
        if let Some((first, first_up, first_at)) = self.last.take() {
            let gap = down.wrapping_sub(first_up);
            let near = (at.0 - first_at.0).hypot(at.1 - first_at.1) <= TENT_DOUBLE_PX;
            if first == output && gap <= TENT_DOUBLE_GAP_MS && near {
                return true;
            }
        }
        self.last = Some((output.to_owned(), up, at));
        false
    }

    /// Forget a first tap (the pose changed, the touch was cancelled).
    pub fn reset(&mut self) {
        self.last = None;
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_tents_dark_panel_wants_a_double_tap() {
        use super::DoubleTap;
        let mut d = DoubleTap::default();
        // One tap: nothing.
        assert!(!d.tap("DSI-2", 1000, 1080, (100.0, 200.0)));
        // The second, 200 ms later and 20 px away: the switch.
        assert!(d.tap("DSI-2", 1280, 1350, (112.0, 216.0)));
        // It starts over: a third tap alone does nothing.
        assert!(!d.tap("DSI-2", 1500, 1560, (100.0, 200.0)));
        // Too slow between the taps.
        let mut d = DoubleTap::default();
        assert!(!d.tap("DSI-2", 0, 80, (100.0, 200.0)));
        assert!(!d.tap("DSI-2", 600, 680, (100.0, 200.0)));
        // ... but that second one counts as a first: a third in time switches.
        assert!(d.tap("DSI-2", 900, 960, (100.0, 200.0)));
        // Too far apart (a hand, not a double tap).
        let mut d = DoubleTap::default();
        assert!(!d.tap("DSI-2", 0, 80, (100.0, 200.0)));
        assert!(!d.tap("DSI-2", 200, 280, (300.0, 200.0)));
        // A long press is no tap.
        let mut d = DoubleTap::default();
        assert!(!d.tap("DSI-2", 0, 80, (100.0, 200.0)));
        assert!(!d.tap("DSI-2", 200, 800, (100.0, 200.0)));
        assert!(!d.tap("DSI-2", 900, 950, (100.0, 200.0)));
        // Two panels are not one double tap.
        let mut d = DoubleTap::default();
        assert!(!d.tap("DSI-1", 0, 80, (100.0, 200.0)));
        assert!(!d.tap("DSI-2", 200, 280, (100.0, 200.0)));
        // Time wraps (u32 milliseconds).
        let mut d = DoubleTap::default();
        assert!(!d.tap("DSI-2", u32::MAX - 100, u32::MAX - 20, (1.0, 1.0)));
        assert!(d.tap("DSI-2", 100, 150, (1.0, 1.0)));
    }

    #[test]
    fn a_tap_on_a_tents_dark_panel_switches_to_it() {
        use super::{tent_tap_switches, Pose};
        let single1 = Pose::Single("DSI-1".into());
        assert!(tent_tap_switches(Some("tent_right"), &single1, "DSI-2", false));
        assert!(tent_tap_switches(Some("tent_left"), &single1, "DSI-2", false));
        // Stood up as a brochure (on a stand: brochure_right, hinge ~319).
        assert!(tent_tap_switches(Some("brochure_right"), &single1, "DSI-2", false));
        assert!(tent_tap_switches(Some("brochure_left"), &single1, "DSI-2", false));
        assert!(!tent_tap_switches(Some("brochure_right"), &single1, "DSI-1", false));
        assert!(!tent_tap_switches(Some("brochure_right"), &single1, "DSI-2", true));
        // Not the panel in use, not folded back, not a blanked screen, not dual.
        assert!(!tent_tap_switches(Some("tent_right"), &single1, "DSI-1", false));
        assert!(!tent_tap_switches(Some("flip_p_left"), &single1, "DSI-2", false));
        assert!(!tent_tap_switches(Some("flip_l_right"), &single1, "DSI-2", false));
        assert!(!tent_tap_switches(Some("tent_right"), &single1, "DSI-2", true));
        assert!(!tent_tap_switches(Some("tent_right"), &Pose::Dual, "DSI-2", false));
        assert!(!tent_tap_switches(None, &single1, "DSI-2", false));
    }

    use super::*;

    fn on(output: &str) -> Placement {
        Placement::Output(output.into())
    }

    fn win(id: u64, placement: Placement) -> WindowPlace {
        WindowPlace { id, placement, saved: None }
    }

    fn placements(windows: &[WindowPlace]) -> Vec<Placement> {
        windows.iter().map(|w| w.placement.clone()).collect()
    }

    #[test]
    fn parsing() {
        assert_eq!(Pose::parse("dual", None), Ok(Pose::Dual));
        assert_eq!(Pose::parse("SPAN", None), Ok(Pose::Span));
        assert_eq!(Pose::parse("single", Some("DSI-2")), Ok(Pose::Single("DSI-2".into())));
        assert!(Pose::parse("single", None).is_err());
        assert!(Pose::parse("dual", Some("DSI-1")).is_err());
        assert!(Pose::parse("tent", None).is_err());
        assert_eq!(Pose::Single("DSI-1".into()).describe(), "single DSI-1");
    }

    #[test]
    fn single_moves_everything_to_the_panel_that_stays_and_dual_brings_it_back() {
        let mut windows = vec![win(1, on("DSI-1")), win(2, on("DSI-2")), win(3, on("DSI-2"))];
        apply(&Pose::Single("DSI-1".into()), &mut windows, Some(2));
        assert_eq!(placements(&windows), vec![on("DSI-1"); 3]);
        assert_eq!(windows[0].saved, None);
        assert_eq!(windows[1].saved, Some(on("DSI-2")));

        apply(&Pose::Dual, &mut windows, None);
        assert_eq!(placements(&windows), vec![on("DSI-1"), on("DSI-2"), on("DSI-2")]);
        assert!(windows.iter().all(|w| w.saved.is_none()));
    }

    #[test]
    fn a_spanning_window_is_folded_onto_the_single_panel_and_spans_again_after() {
        let mut windows = vec![win(1, Placement::Span), win(2, on("DSI-1"))];
        apply(&Pose::Single("DSI-2".into()), &mut windows, Some(1));
        assert_eq!(placements(&windows), vec![on("DSI-2"), on("DSI-2")]);
        apply(&Pose::Dual, &mut windows, None);
        assert_eq!(placements(&windows), vec![Placement::Span, on("DSI-1")]);
    }

    #[test]
    fn span_spreads_the_front_window_only() {
        let mut windows = vec![win(1, on("DSI-1")), win(2, on("DSI-2"))];
        apply(&Pose::Span, &mut windows, Some(2));
        assert_eq!(placements(&windows), vec![on("DSI-1"), Placement::Span]);
        apply(&Pose::Dual, &mut windows, None);
        assert_eq!(placements(&windows), vec![on("DSI-1"), on("DSI-2")]);
    }

    #[test]
    fn switching_between_poses_goes_through_the_own_placements() {
        let mut windows = vec![win(1, on("DSI-1")), win(2, on("DSI-2"))];
        apply(&Pose::Span, &mut windows, Some(1));
        // From span straight to single DSI-2: window 1 is back on DSI-1 first,
        // then folded onto DSI-2, and still knows it belongs to DSI-1.
        apply(&Pose::Single("DSI-2".into()), &mut windows, Some(1));
        assert_eq!(placements(&windows), vec![on("DSI-2"), on("DSI-2")]);
        assert_eq!(windows[0].saved, Some(on("DSI-1")));
        apply(&Pose::Dual, &mut windows, None);
        assert_eq!(placements(&windows), vec![on("DSI-1"), on("DSI-2")]);
    }

    #[test]
    fn the_same_pose_twice_changes_nothing() {
        let mut windows = vec![win(1, on("DSI-1")), win(2, on("DSI-2"))];
        apply(&Pose::Span, &mut windows, Some(1));
        let once = windows.clone();
        apply(&Pose::Span, &mut windows, Some(1));
        assert_eq!(windows, once);
    }

    #[test]
    fn span_without_a_window_is_fine() {
        let mut windows: Vec<WindowPlace> = Vec::new();
        apply(&Pose::Span, &mut windows, None);
        assert!(windows.is_empty());
    }

    #[test]
    fn new_windows_follow_the_pose() {
        assert_eq!(new_window(&Pose::Dual, on("DSI-2")), (on("DSI-2"), None));
        assert_eq!(new_window(&Pose::Single("DSI-1".into()), on("DSI-2")), (on("DSI-1"), None));
        assert_eq!(new_window(&Pose::Span, on("DSI-2")), (Placement::Span, Some(on("DSI-2"))));
    }

    #[test]
    fn windows_on_a_display_keep_their_place_in_every_pose() {
        let display = Placement::Output("DP-1".into());
        let mut windows = vec![
            WindowPlace { id: 1, placement: on("DSI-2"), saved: None },
            WindowPlace { id: 2, placement: display.clone(), saved: None },
        ];
        apply(&Pose::Single("DSI-1".into()), &mut windows, Some(2));
        assert_eq!(windows[0].placement, on("DSI-1"));
        assert_eq!((windows[1].placement.clone(), windows[1].saved.clone()), (display.clone(), None));
        // The front window on the display does not span.
        apply(&Pose::Span, &mut windows, Some(2));
        assert_eq!(windows[1].placement, display);
        assert_eq!(new_window(&Pose::Single("DSI-1".into()), display.clone()), (display.clone(), None));
        assert_eq!(new_window(&Pose::Span, display.clone()), (display, None));
    }

    #[test]
    fn in_use() {
        assert!(Pose::Dual.in_use("DSI-2"));
        assert!(Pose::Span.in_use("DSI-1"));
        let single = Pose::Single("DSI-1".into());
        assert!(single.in_use("DSI-1") && !single.in_use("DSI-2"));
        // An external display is no pose's.
        assert!(!Pose::Dual.in_use("DP-1") && !Pose::Span.in_use("DP-1"));
    }
}
