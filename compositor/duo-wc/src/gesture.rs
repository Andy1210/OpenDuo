//! Edge swipes: a finger that goes down at the very edge of a panel and moves
//! inwards belongs to the compositor (and the shell it tells), not to the
//! window under it. The phone conventions:
//!
//! - **bottom edge, up**: the dock (short), home (long), the task overview
//!   (stop and hold); see [`SwipeKind`];
//! - **top edge, down**: the quick menu (the shell's), decided as soon as it
//!   is a swipe, so the menu follows at once;
//! - **left or right edge, inwards**: **back** (the focused app gets
//!   KEY_BACK, XF86Back), on the lift if it went far enough
//!   ([`BACK_TRIGGER`]); the compositor draws an arrow that follows the
//!   finger meanwhile.
//!
//! duo-wc is the only one that sees the raw touch, so it decides. A touch that
//! goes down in an armed edge zone is HELD (nothing reaches a client yet) until
//! it is clear what it is:
//!
//! - it travels [`swipe_min`] inwards, mostly straight: a **swipe**. The
//!   compositor announces it (`edge_swipe` on the control socket's event
//!   stream) and the touch point is never delivered;
//! - it goes sideways, outwards, or lifts before that: **not a swipe**. The
//!   held down is delivered where it happened (and the finger's current place
//!   after it), so a tap or a drag that started at the edge still works, only
//!   later;
//! - at a SIDE edge, it stays held at most [`SIDE_HOLD_MAX_MS`]: the outer
//!   edges are where the hand holding the phone rests (and their outermost
//!   millimetres read as the edge itself), so a finger that rests there and
//!   only later creeps inwards is delivered, never a back.
//!
//! Which edges are armed is the caller's ([`Arms`]: the side edges follow
//! the `back_swipe` setting, both / outer / off). Once a bottom touch is a
//! swipe, the compositor keeps following it ([`Swipe`]) until it is clear
//! which of three it is:
//!
//! - the finger **stops and holds** ([`HOLD_MS`] without moving more than
//!   [`HOLD_SLOP`]) while still down: the task overview opens (the
//!   compositor's own, [`crate::overview`]);
//! - it **lifts** after a short travel (under [`LONG_SWIPE_SHARE`] of the
//!   panel's height): `short`, the shell shows the dock;
//! - it lifts after a longer one: `long`, home (the panel's windows go
//!   aside); on a panel that already shows home, the shell's app grid.
//!
//! Positions are in the global logical space, and edges are a panel's edges
//! as it reads (a rotated panel's "bottom" is the edge at the bottom of the
//! rotated picture). Pure: no Smithay types.

/// How deep the edge zone is, in logical px (at scale 2.5: 50 physical px,
/// ~3 mm on the ~400 ppi panels).
pub const EDGE_ZONE: f64 = 20.0;

/// How far inwards a swipe must travel, in logical px.
pub const SWIPE_MIN: f64 = 48.0;

/// How far sideways (across the swipe) a held touch may wander before it is
/// not a swipe, in logical px.
pub const SIDEWAYS_MAX: f64 = 32.0;

/// How far outwards (back towards the edge) it may go before it is not one.
pub const BACKWARDS_MAX: f64 = 12.0;

/// An edge of a panel.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Edge {
    /// The bottom edge; a swipe from it goes up.
    Bottom,
    /// The top edge; a swipe from it goes down (the quick menu).
    Top,
    /// The left edge; a swipe from it goes right (back).
    Left,
    /// The right edge; a swipe from it goes left (back).
    Right,
}

impl Edge {
    /// The name on the control socket.
    pub fn name(self) -> &'static str {
        match self {
            Edge::Bottom => "bottom",
            Edge::Top => "top",
            Edge::Left => "left",
            Edge::Right => "right",
        }
    }

    /// A left or right edge (back).
    pub fn is_side(self) -> bool {
        matches!(self, Edge::Left | Edge::Right)
    }
}

/// Which edges of a panel are armed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Arms {
    /// The bottom edge (dock, home, overview).
    pub bottom: bool,
    /// The top edge (the quick menu).
    pub top: bool,
    /// The left edge (back).
    pub left: bool,
    /// The right edge (back).
    pub right: bool,
}

impl Arms {
    /// Every edge.
    pub const ALL: Arms = Arms { bottom: true, top: true, left: true, right: true };
}

/// How far inwards a held touch must travel to be a swipe from `edge`
/// (logical px): the side edges decide sooner, so the back arrow shows up
/// under the finger.
pub fn swipe_min(edge: Edge) -> f64 {
    if edge.is_side() {
        SIDE_SWIPE_MIN
    } else {
        SWIPE_MIN
    }
}

/// How far a side swipe must have come when it lifts to be a back.
pub const BACK_TRIGGER: f64 = 64.0;

/// A side swipe that comes back this far from the furthest it got, cancels.
pub const BACK_RETREAT: f64 = 28.0;

/// The side edges' swipe threshold.
pub const SIDE_SWIPE_MIN: f64 = 28.0;

/// A touch held at a side edge is delivered after this long if it has not
/// become a swipe: a resting hand never turns into a back.
pub const SIDE_HOLD_MAX_MS: u64 = 350;

/// How far across a side swipe (up or down) may wander.
const SIDE_SIDEWAYS_MAX: f64 = 56.0;

/// A rectangle in logical px: where the panel is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Area {
    /// Left.
    pub x: f64,
    /// Top.
    pub y: f64,
    /// Width.
    pub w: f64,
    /// Height.
    pub h: f64,
}

/// Which armed edge a touch going down at `(x, y)` on `area` starts at, if any.
/// The bottom and the top win in the corners (the dock and the quick menu
/// are the more common), the sides take the rest of their strips.
pub fn edge_at(area: Area, x: f64, y: f64, arms: Arms) -> Option<Edge> {
    edge_at_zone(area, x, y, arms, EDGE_ZONE)
}

/// [`edge_at`] with the strip's depth (logical px; duo-wc.conf `edge_zone`).
pub fn edge_at_zone(area: Area, x: f64, y: f64, arms: Arms, zone: f64) -> Option<Edge> {
    let inside = x >= area.x && x < area.x + area.w && y >= area.y && y < area.y + area.h;
    if !inside {
        return None;
    }
    let from_bottom = area.y + area.h - y;
    let from_top = y - area.y;
    let from_left = x - area.x;
    let from_right = area.x + area.w - x;
    if arms.bottom && from_bottom <= zone {
        return Some(Edge::Bottom);
    }
    if arms.top && from_top <= zone {
        return Some(Edge::Top);
    }
    if arms.left && from_left <= zone {
        return Some(Edge::Left);
    }
    if arms.right && from_right <= zone {
        return Some(Edge::Right);
    }
    None
}

/// How far `at` is inwards from `start` for a swipe from `edge`, and how far
/// across.
fn travel_from(edge: Edge, start: (f64, f64), at: (f64, f64)) -> (f64, f64) {
    let (dx, dy) = (at.0 - start.0, at.1 - start.1);
    match edge {
        Edge::Bottom => (-dy, dx.abs()),
        Edge::Top => (dy, dx.abs()),
        Edge::Left => (dx, dy.abs()),
        Edge::Right => (-dx, dy.abs()),
    }
}

/// How long a swiping finger must stand still to open the overview.
pub const HOLD_MS: u64 = 300;

/// How far a standing finger may wander and still be holding, in logical px.
pub const HOLD_SLOP: f64 = 10.0;

/// A swipe that travelled less than this share of the panel's height when it
/// lifted is `short` (the dock); more is `long` (the app grid).
pub const LONG_SWIPE_SHARE: f64 = 0.22;

/// What a finished edge swipe was.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SwipeKind {
    /// Lifted after a short travel: the dock.
    Short,
    /// Lifted after a long one: the app grid.
    Long,
    /// Stopped and held: the task overview.
    Hold,
    /// A long one that sent the panel's windows home.
    Home,
    /// From the top: the quick menu.
    Down,
    /// From a side: back.
    Back,
}

impl SwipeKind {
    /// The name on the event stream.
    pub fn name(self) -> &'static str {
        match self {
            SwipeKind::Short => "short",
            SwipeKind::Long => "long",
            SwipeKind::Hold => "hold",
            SwipeKind::Home => "home",
            SwipeKind::Down => "down",
            SwipeKind::Back => "back",
        }
    }
}

/// A touch that is a swipe from an edge, followed until it lifts or holds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Swipe {
    /// The edge it started at.
    pub edge: Edge,
    /// Where it went down.
    pub start: (f64, f64),
    /// Where it is now.
    pub at: (f64, f64),
    /// Where it last stood still: a hold counts from when it got here.
    pub anchor: (f64, f64),
}

impl Swipe {
    /// A held touch that turned out to be a swipe, now at `at`.
    pub fn new(held: &Held, at: (f64, f64)) -> Self {
        Swipe { edge: held.edge, start: held.start, at, anchor: at }
    }

    /// The finger moved to `(x, y)`. True when it left its anchor (moved more
    /// than [`HOLD_SLOP`]): the hold starts counting again from here.
    pub fn moved(&mut self, x: f64, y: f64) -> bool {
        self.at = (x, y);
        let (ax, ay) = self.anchor;
        if (x - ax).hypot(y - ay) > HOLD_SLOP {
            self.anchor = (x, y);
            return true;
        }
        false
    }

    /// How far it has come inwards from the edge.
    pub fn travel(&self) -> f64 {
        travel_from(self.edge, self.start, self.at).0
    }

    /// A side swipe lifted now: a back if it came far enough and is not
    /// on its way back to the edge (`furthest` is the most it travelled).
    pub fn is_back(&self, furthest: f64) -> bool {
        let travel = self.travel();
        travel >= BACK_TRIGGER && travel > furthest - BACK_RETREAT
    }

    /// What it was, lifted now on a panel `panel_h` high.
    pub fn lifted(&self, panel_h: f64) -> SwipeKind {
        if self.travel() >= panel_h * LONG_SWIPE_SHARE {
            SwipeKind::Long
        } else {
            SwipeKind::Short
        }
    }
}

/// What a held touch turned out to be.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Not clear yet: keep holding.
    Holding,
    /// A swipe from the edge.
    Swipe,
    /// Not a swipe: deliver it after all.
    Release,
}

/// A touch held at an edge until it is clear what it is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Held {
    /// The edge it started at.
    pub edge: Edge,
    /// Where it went down.
    pub start: (f64, f64),
}

impl Held {
    /// A touch down at `start` on `edge`.
    pub fn new(edge: Edge, start: (f64, f64)) -> Self {
        Held { edge, start }
    }

    /// The finger moved to `(x, y)`.
    pub fn moved(&self, x: f64, y: f64) -> Verdict {
        let (inwards, sideways) = travel_from(self.edge, self.start, (x, y));
        let sideways_max = if self.edge.is_side() { SIDE_SIDEWAYS_MAX } else { SIDEWAYS_MAX };
        if sideways > sideways_max || inwards < -BACKWARDS_MAX {
            return Verdict::Release;
        }
        if inwards >= swipe_min(self.edge) && inwards >= sideways * 1.5 {
            return Verdict::Swipe;
        }
        Verdict::Holding
    }

    /// Held this long (ms) without becoming a swipe: a side or the top edge
    /// lets go ([`SIDE_HOLD_MAX_MS`]; a long press on the status bar, a
    /// fullscreen window's top), the bottom keeps holding.
    pub fn expired(&self, held_ms: u64) -> bool {
        self.edge != Edge::Bottom && held_ms >= SIDE_HOLD_MAX_MS
    }

    /// The finger lifted before anything was decided: it was a tap (or a
    /// short drag), deliver it.
    pub fn lifted(&self) -> Verdict {
        Verdict::Release
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PANEL: Area = Area { x: 564.0, y: 0.0, w: 538.0, h: 757.0 };

    const BOTTOM: Arms = Arms { bottom: true, top: false, left: false, right: false };

    #[test]
    fn only_the_bottom_strip_is_an_edge_when_only_it_is_armed() {
        assert_eq!(edge_at(PANEL, 800.0, 750.0, BOTTOM), Some(Edge::Bottom));
        assert_eq!(edge_at(PANEL, 800.0, 757.0 - EDGE_ZONE, BOTTOM), Some(Edge::Bottom));
        assert_eq!(edge_at(PANEL, 800.0, 757.0 - EDGE_ZONE - 1.0, BOTTOM), None);
        assert_eq!(edge_at(PANEL, 800.0, 10.0, BOTTOM), None);
        // On the other panel's x: not this panel.
        assert_eq!(edge_at(PANEL, 100.0, 750.0, BOTTOM), None);
    }

    #[test]
    fn every_edge_when_all_are_armed_the_bottom_and_top_win_the_corners() {
        assert_eq!(edge_at(PANEL, 800.0, 5.0, Arms::ALL), Some(Edge::Top));
        assert_eq!(edge_at(PANEL, 566.0, 300.0, Arms::ALL), Some(Edge::Left));
        assert_eq!(edge_at(PANEL, 1100.0, 300.0, Arms::ALL), Some(Edge::Right));
        assert_eq!(edge_at(PANEL, 566.0, 750.0, Arms::ALL), Some(Edge::Bottom));
        assert_eq!(edge_at(PANEL, 1100.0, 3.0, Arms::ALL), Some(Edge::Top));
        assert_eq!(edge_at(PANEL, 800.0, 300.0, Arms::ALL), None);
        // Only the outer (right) side armed.
        let outer = Arms { right: true, ..Arms::ALL };
        let outer = Arms { left: false, ..outer };
        assert_eq!(edge_at(PANEL, 566.0, 300.0, outer), None);
        assert_eq!(edge_at_zone(PANEL, 600.0, 300.0, Arms::ALL, 40.0), Some(Edge::Left));
    }

    #[test]
    fn a_swipe_down_from_the_top_and_in_from_the_sides() {
        let top = Held::new(Edge::Top, (800.0, 4.0));
        assert_eq!(top.moved(800.0, 30.0), Verdict::Holding);
        assert_eq!(top.moved(805.0, 60.0), Verdict::Swipe);
        assert_eq!(top.moved(800.0, -20.0), Verdict::Release);
        let left = Held::new(Edge::Left, (566.0, 300.0));
        assert_eq!(left.moved(566.0 + SIDE_SWIPE_MIN, 310.0), Verdict::Swipe);
        assert_eq!(left.moved(580.0, 300.0 + SIDE_SIDEWAYS_MAX + 1.0), Verdict::Release);
        let right = Held::new(Edge::Right, (1100.0, 300.0));
        assert_eq!(right.moved(1100.0 - SIDE_SWIPE_MIN, 290.0), Verdict::Swipe);
        assert_eq!(right.moved(1110.0 + BACKWARDS_MAX, 300.0), Verdict::Release);
    }

    #[test]
    fn a_resting_thumb_at_a_side_is_let_go() {
        let left = Held::new(Edge::Left, (566.0, 300.0));
        assert!(!left.expired(SIDE_HOLD_MAX_MS - 1));
        assert!(left.expired(SIDE_HOLD_MAX_MS));
        // The bottom keeps holding (a slow swipe up is still a swipe), the
        // top does not.
        assert!(!Held::new(Edge::Bottom, (800.0, 750.0)).expired(5000));
        assert!(Held::new(Edge::Top, (800.0, 5.0)).expired(SIDE_HOLD_MAX_MS));
    }

    #[test]
    fn a_back_needs_the_distance_and_no_retreat() {
        let held = Held::new(Edge::Left, (566.0, 300.0));
        let mut swipe = Swipe::new(&held, (600.0, 300.0));
        assert!(!swipe.is_back(34.0));
        swipe.moved(566.0 + BACK_TRIGGER + 10.0, 300.0);
        assert!(swipe.is_back(BACK_TRIGGER + 10.0));
        // Went further, then came most of the way back: cancelled.
        assert!(!swipe.is_back(BACK_TRIGGER + 10.0 + BACK_RETREAT + 1.0));
        let right = Held::new(Edge::Right, (1100.0, 300.0));
        let mut r = Swipe::new(&right, (1060.0, 300.0));
        r.moved(1100.0 - BACK_TRIGGER, 300.0);
        assert!(r.is_back(BACK_TRIGGER));
        assert_eq!(Edge::Right.name(), "right");
    }

    #[test]
    fn straight_up_is_a_swipe() {
        let held = Held::new(Edge::Bottom, (800.0, 750.0));
        assert_eq!(held.moved(801.0, 740.0), Verdict::Holding);
        assert_eq!(held.moved(805.0, 750.0 - SWIPE_MIN), Verdict::Swipe);
        assert_eq!(held.moved(790.0, 600.0), Verdict::Swipe);
    }

    #[test]
    fn sideways_or_down_is_not() {
        let held = Held::new(Edge::Bottom, (800.0, 750.0));
        assert_eq!(held.moved(800.0 + SIDEWAYS_MAX + 1.0, 745.0), Verdict::Release);
        assert_eq!(held.moved(800.0, 750.0 + BACKWARDS_MAX + 1.0), Verdict::Release);
        // A diagonal that went far enough up but more sideways than up/1.5.
        let diagonal = Held::new(Edge::Bottom, (800.0, 750.0));
        assert_eq!(diagonal.moved(831.0, 750.0 - 40.0), Verdict::Holding);
    }

    #[test]
    fn a_swipe_holds_while_it_stands_still() {
        let held = Held::new(Edge::Bottom, (800.0, 750.0));
        let mut swipe = Swipe::new(&held, (800.0, 690.0));
        // Jitter within the slop keeps the anchor.
        assert!(!swipe.moved(803.0, 686.0));
        assert!(!swipe.moved(797.0, 694.0));
        // Moving on restarts the hold from the new place.
        assert!(swipe.moved(800.0, 600.0));
        assert_eq!(swipe.anchor, (800.0, 600.0));
        assert!(!swipe.moved(801.0, 605.0));
    }

    #[test]
    fn short_and_long_swipes() {
        let held = Held::new(Edge::Bottom, (800.0, 750.0));
        let mut swipe = Swipe::new(&held, (800.0, 700.0));
        assert_eq!(swipe.lifted(757.0), SwipeKind::Short);
        swipe.moved(800.0, 750.0 - 757.0 * LONG_SWIPE_SHARE - 1.0);
        assert_eq!(swipe.lifted(757.0), SwipeKind::Long);
        swipe.moved(800.0, 300.0);
        assert_eq!(swipe.travel(), 450.0);
        assert_eq!(swipe.lifted(757.0), SwipeKind::Long);
        assert_eq!(SwipeKind::Hold.name(), "hold");
    }

    #[test]
    fn a_tap_is_released() {
        let held = Held::new(Edge::Bottom, (800.0, 750.0));
        assert_eq!(held.lifted(), Verdict::Release);
    }
}
