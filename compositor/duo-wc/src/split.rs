//! Two windows on one panel: the split, its halves and its divider.
//!
//! A phone panel shows one window at a time; a **split** puts two of them on
//! the same panel, side by side or one above the other, with a divider
//! between them that a finger drags to share the room differently. It is
//! made from the task overview (a card dropped on a half of the panel) or
//! over the control socket, and ends when the divider is dragged (almost) to
//! the panel's edge, when one of its windows goes, or is placed elsewhere
//! explicitly.
//!
//! **It belongs to the glass, not to the picture.** A split is kept in the
//! panel's own, unrotated (portrait) frame: `columns` = left | right halves
//! there, otherwise top | bottom. When the panel is turned, the halves stay
//! where they are on the glass, so what was left | right in portrait reads
//! top | bottom when the device is turned clockwise (the panel's left edge
//! up), and the windows do not jump around ([`orient`]). The room it shares
//! is the panel's zone (the panel less its status bar and keyboard).
//!
//! **Poses keep it**: the split belongs to a panel; it is in force while both
//! of its windows are placed on that panel. When a pose moves one away (span
//! spreads the front window over both panels, `single` folds the other
//! panel's windows onto the one that stays) the one left behind fills the
//! panel, and when the pose ends and the window comes back the split is
//! there again.
//!
//! Pure: rectangles in logical px, no Smithay types.

use serde::Serialize;

use crate::layout::Rect;
use crate::rotation::Rotation;

/// The gap between the two halves, where the divider is drawn (logical px).
pub const DIVIDER: i32 = 12;

/// Half the width of the strip around the divider's centre line that a
/// finger grabs it in (logical px; ~9 mm in all at scale 2.5).
pub const DIVIDER_HIT: f64 = 22.0;

/// While dragging, neither half gets less than this share of the room.
pub const MIN_SHARE: f64 = 0.2;

/// Let go with a half squeezed under this share and the split ends: the other
/// window fills the panel.
pub const DISSOLVE_SHARE: f64 = 0.1;

/// A half of a panel, as the panel reads now (rotated).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    /// The left half.
    Left,
    /// The right half.
    Right,
    /// The upper half.
    Top,
    /// The lower half.
    Bottom,
}

impl Side {
    /// From `left`, `right`, `top`, `bottom`.
    pub fn parse(word: &str) -> Option<Side> {
        match word.trim().to_ascii_lowercase().as_str() {
            "left" => Some(Side::Left),
            "right" => Some(Side::Right),
            "top" | "up" | "upper" => Some(Side::Top),
            "bottom" | "down" | "lower" => Some(Side::Bottom),
            _ => None,
        }
    }

    /// The word for it.
    pub fn name(self) -> &'static str {
        match self {
            Side::Left => "left",
            Side::Right => "right",
            Side::Top => "top",
            Side::Bottom => "bottom",
        }
    }

    /// How the halves sit when one of them is this side.
    pub fn axis(self) -> Axis {
        match self {
            Side::Left | Side::Right => Axis::SideBySide,
            Side::Top | Side::Bottom => Axis::Stacked,
        }
    }

    /// Left or top: the first half in reading order.
    pub fn is_first(self) -> bool {
        matches!(self, Side::Left | Side::Top)
    }

    /// The side across the divider.
    pub fn opposite(self) -> Side {
        match self {
            Side::Left => Side::Right,
            Side::Right => Side::Left,
            Side::Top => Side::Bottom,
            Side::Bottom => Side::Top,
        }
    }

    /// The first or second half of an axis.
    pub fn of(axis: Axis, first: bool) -> Side {
        match (axis, first) {
            (Axis::SideBySide, true) => Side::Left,
            (Axis::SideBySide, false) => Side::Right,
            (Axis::Stacked, true) => Side::Top,
            (Axis::Stacked, false) => Side::Bottom,
        }
    }
}

/// How the two halves sit, as the panel reads now.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Axis {
    /// Left and right, the divider upright.
    SideBySide,
    /// Top and bottom, the divider lying.
    Stacked,
}

/// A panel shared by two windows, kept in the panel's unrotated frame.
#[derive(Debug, Clone, PartialEq)]
pub struct Split {
    /// The panel (connector name).
    pub output: String,
    /// The window in the unrotated frame's first half (left, or top).
    pub first: u64,
    /// The window in the other half.
    pub second: u64,
    /// The halves are left | right in the unrotated (portrait) frame;
    /// otherwise top | bottom.
    pub columns: bool,
    /// The first half's share of the room, [`MIN_SHARE`] to 1 - [`MIN_SHARE`].
    pub ratio: f64,
}

/// How a split kept in the unrotated frame reads on a panel turned by
/// `rotation`: its axis, and whether the unrotated first half is the second
/// one (right or bottom) as the panel reads now.
///
/// Turned 90 (the device clockwise) the panel's left edge is up and its top
/// edge right; turned 270 its left edge is down and its top edge left.
pub fn orient(columns: bool, rotation: Rotation) -> (Axis, bool) {
    match (columns, rotation) {
        (true, Rotation::R0) => (Axis::SideBySide, false),
        (true, Rotation::R90) => (Axis::Stacked, false),
        (true, Rotation::R180) => (Axis::SideBySide, true),
        (true, Rotation::R270) => (Axis::Stacked, true),
        (false, Rotation::R0) => (Axis::Stacked, false),
        (false, Rotation::R90) => (Axis::SideBySide, true),
        (false, Rotation::R180) => (Axis::Stacked, true),
        (false, Rotation::R270) => (Axis::SideBySide, false),
    }
}

/// The unrotated `columns` flag for halves that read along `axis` on a panel
/// turned by `rotation`.
fn columns_for(axis: Axis, rotation: Rotation) -> bool {
    match axis {
        Axis::SideBySide => !rotation.is_sideways(),
        Axis::Stacked => rotation.is_sideways(),
    }
}

impl Split {
    /// A new split on `output`: `window` on `side` (as the panel reads now,
    /// turned by `rotation`) and `partner` across the divider, sharing the
    /// room `share` to the window.
    pub fn new(output: &str, window: u64, partner: u64, side: Side, rotation: Rotation, share: f64) -> Split {
        let columns = columns_for(side.axis(), rotation);
        let (_, flipped) = orient(columns, rotation);
        // The window's half is the unrotated first one when its side is first
        // as it reads, unless the rotation flips the order.
        let window_first = side.is_first() != flipped;
        let share = share.clamp(MIN_SHARE, 1.0 - MIN_SHARE);
        let (first, second, ratio) =
            if window_first { (window, partner, share) } else { (partner, window, 1.0 - share) };
        Split { output: output.to_owned(), first, second, columns, ratio }
    }

    /// Whether a window is one of the two.
    pub fn has(&self, window: u64) -> bool {
        self.first == window || self.second == window
    }

    /// The other window of the two.
    pub fn partner(&self, window: u64) -> Option<u64> {
        if self.first == window {
            Some(self.second)
        } else if self.second == window {
            Some(self.first)
        } else {
            None
        }
    }

    /// The axis as the panel reads now.
    pub fn axis(&self, rotation: Rotation) -> Axis {
        orient(self.columns, rotation).0
    }

    /// The windows as the panel reads now: (left or top, right or bottom),
    /// and the first one's share of the room.
    pub fn reading_order(&self, rotation: Rotation) -> (u64, u64, f64) {
        let (_, flipped) = orient(self.columns, rotation);
        if flipped {
            (self.second, self.first, 1.0 - self.ratio)
        } else {
            (self.first, self.second, self.ratio)
        }
    }

    /// The side a window is on as the panel reads now.
    pub fn side_of(&self, window: u64, rotation: Rotation) -> Option<Side> {
        let (a, b, _) = self.reading_order(rotation);
        let axis = self.axis(rotation);
        if window == a {
            Some(Side::of(axis, true))
        } else if window == b {
            Some(Side::of(axis, false))
        } else {
            None
        }
    }

    /// Set the share of the window that reads first (left or top).
    fn set_reading_share(&mut self, share: f64, rotation: Rotation) {
        let share = share.clamp(MIN_SHARE, 1.0 - MIN_SHARE);
        let (_, flipped) = orient(self.columns, rotation);
        self.ratio = if flipped { 1.0 - share } else { share };
    }
}

/// Where the two windows and the divider are on a panel.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Tiles {
    /// The window that reads first (left or top) and its rectangle.
    pub first: (u64, Rect),
    /// The other one.
    pub second: (u64, Rect),
    /// The gap between them.
    pub divider: Rect,
    /// How they sit.
    pub axis: Axis,
}

impl Tiles {
    /// A window's rectangle.
    pub fn rect_of(&self, window: u64) -> Option<Rect> {
        if self.first.0 == window {
            Some(self.first.1)
        } else if self.second.0 == window {
            Some(self.second.1)
        } else {
            None
        }
    }
}

/// A share of `room` that leaves each window at least its minimum length
/// (0 = none) along the split, when both fit; when they do not, the room is
/// shared in proportion to the minimums.
pub fn fit_share(share: f64, room: i32, min_first: i32, min_second: i32) -> f64 {
    if room <= 0 {
        return share;
    }
    let (a, b) = (min_first.max(0), min_second.max(0));
    if a + b > room {
        return a as f64 / (a + b) as f64;
    }
    let lowest = a as f64 / room as f64;
    let highest = 1.0 - b as f64 / room as f64;
    share.clamp(lowest, highest)
}

/// Lay a split out in `zone` (the panel's room, any frame; a rotated panel's
/// zone as it reads).
pub fn tiles(split: &Split, rotation: Rotation, zone: Rect) -> Tiles {
    tiles_fitting(split, rotation, zone, |_| (0, 0))
}

/// Like [`tiles`], but no window gets less than its minimum size (`min`:
/// a window's minimum width and height, 0 = none) when the room allows
/// ([`fit_share`]).
pub fn tiles_fitting(
    split: &Split,
    rotation: Rotation,
    zone: Rect,
    min: impl Fn(u64) -> (i32, i32),
) -> Tiles {
    let (a, b, share) = split.reading_order(rotation);
    let axis = split.axis(rotation);
    let (x, y, w, h) = zone;
    let length = match axis {
        Axis::SideBySide => w,
        Axis::Stacked => h,
    };
    let room = (length - DIVIDER).max(2);
    let along = |id: u64| {
        let (w, h) = min(id);
        match axis {
            Axis::SideBySide => w,
            Axis::Stacked => h,
        }
    };
    let share = fit_share(share, room, along(a), along(b));
    let first_len = ((room as f64) * share).round().clamp(1.0, (room - 1) as f64) as i32;
    let second_len = room - first_len;
    match axis {
        Axis::SideBySide => Tiles {
            first: (a, (x, y, first_len, h)),
            second: (b, (x + first_len + DIVIDER, y, second_len, h)),
            divider: (x + first_len, y, DIVIDER, h),
            axis,
        },
        Axis::Stacked => Tiles {
            first: (a, (x, y, w, first_len)),
            second: (b, (x, y + first_len + DIVIDER, w, second_len)),
            divider: (x, y + first_len, w, DIVIDER),
            axis,
        },
    }
}

/// Whether a finger at `(x, y)` grabs the divider: within [`DIVIDER_HIT`] of
/// its centre line, along its length.
pub fn divider_hit(t: &Tiles, x: f64, y: f64) -> bool {
    let (dx, dy, dw, dh) = t.divider;
    let (dx, dy, dw, dh) = (dx as f64, dy as f64, dw as f64, dh as f64);
    match t.axis {
        Axis::SideBySide => {
            let centre = dx + dw / 2.0;
            (x - centre).abs() <= DIVIDER_HIT && y >= dy && y < dy + dh
        }
        Axis::Stacked => {
            let centre = dy + dh / 2.0;
            (y - centre).abs() <= DIVIDER_HIT && x >= dx && x < dx + dw
        }
    }
}

/// The first (reading order) window's share of the room if the divider's
/// centre were at the finger, unclamped.
fn raw_share(axis: Axis, zone: Rect, x: f64, y: f64) -> f64 {
    let (zx, zy, zw, zh) = zone;
    let (pos, start, length) = match axis {
        Axis::SideBySide => (x, zx as f64, zw as f64),
        Axis::Stacked => (y, zy as f64, zh as f64),
    };
    let room = (length - DIVIDER as f64).max(1.0);
    (pos - start - DIVIDER as f64 / 2.0) / room
}

/// The divider follows a finger at `(x, y)`: the split with its new ratio
/// (neither half under [`MIN_SHARE`]).
pub fn dragged(split: &Split, rotation: Rotation, zone: Rect, x: f64, y: f64) -> Split {
    let mut split = split.clone();
    let share = raw_share(split.axis(rotation), zone, x, y);
    split.set_reading_share(share, rotation);
    split
}

/// What letting go of the divider does.
#[derive(Debug, Clone, PartialEq)]
pub enum DividerRelease {
    /// The split stays, with this ratio.
    Keep(Split),
    /// A half was squeezed to the edge: the split ends and this window fills
    /// the panel.
    Dissolve {
        /// The window that stays.
        keep: u64,
    },
}

/// The divider is let go with the finger at `(x, y)`.
pub fn release(split: &Split, rotation: Rotation, zone: Rect, x: f64, y: f64) -> DividerRelease {
    let share = raw_share(split.axis(rotation), zone, x, y);
    let (a, b, _) = split.reading_order(rotation);
    if share < DISSOLVE_SHARE {
        DividerRelease::Dissolve { keep: b }
    } else if share > 1.0 - DISSOLVE_SHARE {
        DividerRelease::Dissolve { keep: a }
    } else {
        DividerRelease::Keep(dragged(split, rotation, zone, x, y))
    }
}

/// The half `side` of an area `(x, y, w, h)` (f64), with the divider's gap
/// left out: what the overview shows as the drop target.
pub fn half_of(area: (f64, f64, f64, f64), side: Side) -> (f64, f64, f64, f64) {
    let (x, y, w, h) = area;
    let g = DIVIDER as f64 / 2.0;
    match side {
        Side::Left => (x, y, w / 2.0 - g, h),
        Side::Right => (x + w / 2.0 + g, y, w / 2.0 - g, h),
        Side::Top => (x, y, w, h / 2.0 - g),
        Side::Bottom => (x, y + h / 2.0 + g, w, h / 2.0 - g),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use smithay::utils::Size;

    const ZONE: Rect = (0, 48, 538, 709);
    const ALL: [Rotation; 4] = [Rotation::R0, Rotation::R90, Rotation::R180, Rotation::R270];

    #[test]
    fn halves_side_by_side_share_the_room_around_the_divider() {
        let split = Split::new("DSI-1", 7, 9, Side::Left, Rotation::R0, 0.5);
        let t = tiles(&split, Rotation::R0, ZONE);
        assert_eq!(t.axis, Axis::SideBySide);
        let room = 538 - DIVIDER;
        assert_eq!(t.first, (7, (0, 48, room / 2, 709)));
        assert_eq!(t.second, (9, (room / 2 + DIVIDER, 48, room - room / 2, 709)));
        assert_eq!(t.divider, (room / 2, 48, DIVIDER, 709));
        // Nothing is lost: first + divider + second = the zone.
        assert_eq!(t.first.1 .2 + DIVIDER + t.second.1 .2, 538);
    }

    #[test]
    fn halves_one_above_the_other() {
        let split = Split::new("DSI-2", 3, 4, Side::Bottom, Rotation::R0, 0.5);
        let t = tiles(&split, Rotation::R0, ZONE);
        assert_eq!(t.axis, Axis::Stacked);
        // The window asked for the bottom: the partner is on top.
        assert_eq!(t.first.0, 4);
        assert_eq!(t.second.0, 3);
        assert_eq!(t.first.1, (0, 48, 538, (709 - DIVIDER) / 2 + 1));
        let (_, sy, _, sh) = t.second.1;
        assert_eq!(sy + sh, 48 + 709);
        assert_eq!(split.side_of(3, Rotation::R0), Some(Side::Bottom));
    }

    /// Where a point of the unrotated panel (fractions) reads on the panel
    /// turned by `rotation`, through the same mapping touches use.
    fn reads_at(rotation: Rotation, fx: f64, fy: f64) -> (f64, f64) {
        let p = crate::rotation::touch_to_local((fx, fy), rotation, Size::from((1344, 1892)), 2.5);
        let (w, h) = if rotation.is_sideways() { (757.0, 538.0) } else { (538.0, 757.0) };
        (p.x / w, p.y / h)
    }

    #[test]
    fn a_split_stays_on_the_glass_when_the_panel_turns() {
        // The halves' centres in the unrotated frame must land in the right
        // halves as the turned panel reads, for both kinds of split.
        for rotation in ALL {
            for columns in [true, false] {
                let split = Split { output: "DSI-1".into(), first: 1, second: 2, columns, ratio: 0.5 };
                let (first_at, second_at) = if columns {
                    (reads_at(rotation, 0.25, 0.5), reads_at(rotation, 0.75, 0.5))
                } else {
                    (reads_at(rotation, 0.5, 0.25), reads_at(rotation, 0.5, 0.75))
                };
                let side_at = |(u, v): (f64, f64)| match split.axis(rotation) {
                    Axis::SideBySide => {
                        if u < 0.5 {
                            Side::Left
                        } else {
                            Side::Right
                        }
                    }
                    Axis::Stacked => {
                        if v < 0.5 {
                            Side::Top
                        } else {
                            Side::Bottom
                        }
                    }
                };
                assert_eq!(
                    split.side_of(1, rotation),
                    Some(side_at(first_at)),
                    "{rotation:?} columns {columns}"
                );
                assert_eq!(
                    split.side_of(2, rotation),
                    Some(side_at(second_at)),
                    "{rotation:?} columns {columns}"
                );
                // And the halves really lie across that axis: the two centres
                // differ along it only.
                match split.axis(rotation) {
                    Axis::SideBySide => assert!((first_at.1 - second_at.1).abs() < 1e-6),
                    Axis::Stacked => assert!((first_at.0 - second_at.0).abs() < 1e-6),
                }
            }
        }
    }

    #[test]
    fn made_on_a_turned_panel_it_reads_as_asked() {
        for rotation in ALL {
            for side in [Side::Left, Side::Right, Side::Top, Side::Bottom] {
                let split = Split::new("DSI-1", 5, 6, side, rotation, 0.4);
                assert_eq!(split.side_of(5, rotation), Some(side), "{rotation:?} {side:?}");
                assert_eq!(split.side_of(6, rotation), Some(side.opposite()));
                let (a, _, share) = split.reading_order(rotation);
                let window_share = if a == 5 { share } else { 1.0 - share };
                assert!((window_share - 0.4).abs() < 1e-9);
            }
        }
    }

    #[test]
    fn upright_left_right_reads_top_bottom_turned_clockwise() {
        let split = Split::new("DSI-1", 1, 2, Side::Left, Rotation::R0, 0.5);
        assert_eq!(split.side_of(1, Rotation::R90), Some(Side::Top));
        assert_eq!(split.side_of(1, Rotation::R270), Some(Side::Bottom));
        assert_eq!(split.side_of(1, Rotation::R180), Some(Side::Right));
    }

    #[test]
    fn minimum_sizes_move_the_divider_when_they_fit() {
        // 526 px of room: a window that needs 360 gets it.
        assert!((fit_share(0.5, 526, 0, 360) - (1.0 - 360.0 / 526.0)).abs() < 1e-9);
        assert!((fit_share(0.5, 526, 360, 0) - 360.0 / 526.0).abs() < 1e-9);
        // Both fit at the share asked for: unchanged.
        assert_eq!(fit_share(0.5, 526, 200, 200), 0.5);
        // Neither can have all it wants: in proportion.
        assert!((fit_share(0.5, 526, 360, 360) - 0.5).abs() < 1e-9);
        assert!((fit_share(0.5, 526, 400, 200) - 400.0 / 600.0).abs() < 1e-9);
        let split = Split::new("DSI-1", 3, 1, Side::Right, Rotation::R0, 0.5);
        let t = tiles_fitting(&split, Rotation::R0, ZONE, |id| if id == 3 { (360, 200) } else { (0, 0) });
        assert_eq!(t.second.1 .2, 360);
        assert_eq!(t.first.1 .2, 526 - 360);
        // Stacked, the heights count.
        let split = Split::new("DSI-1", 3, 1, Side::Bottom, Rotation::R0, 0.5);
        let t = tiles_fitting(&split, Rotation::R0, ZONE, |id| if id == 3 { (360, 500) } else { (0, 0) });
        assert_eq!(t.second.1 .3, 500);
    }

    #[test]
    fn the_divider_is_grabbed_around_its_line() {
        let split = Split::new("DSI-1", 1, 2, Side::Left, Rotation::R0, 0.5);
        let t = tiles(&split, Rotation::R0, ZONE);
        let centre = t.divider.0 as f64 + DIVIDER as f64 / 2.0;
        assert!(divider_hit(&t, centre, 300.0));
        assert!(divider_hit(&t, centre + DIVIDER_HIT - 1.0, 300.0));
        assert!(!divider_hit(&t, centre + DIVIDER_HIT + 1.0, 300.0));
        // Not above the zone (the status bar).
        assert!(!divider_hit(&t, centre, 20.0));
        let stacked = Split::new("DSI-1", 1, 2, Side::Top, Rotation::R0, 0.5);
        let t = tiles(&stacked, Rotation::R0, ZONE);
        let centre = t.divider.1 as f64 + DIVIDER as f64 / 2.0;
        assert!(divider_hit(&t, 100.0, centre - 10.0));
        assert!(!divider_hit(&t, 100.0, centre - 40.0));
    }

    #[test]
    fn dragging_the_divider_moves_the_share_and_stops_at_the_minimum() {
        let split = Split::new("DSI-1", 1, 2, Side::Left, Rotation::R0, 0.5);
        let room = (538 - DIVIDER) as f64;
        let at = |share: f64| share * room + DIVIDER as f64 / 2.0;
        let moved = dragged(&split, Rotation::R0, ZONE, at(0.3), 300.0);
        assert!((moved.ratio - 0.3).abs() < 1e-9);
        let squeezed = dragged(&split, Rotation::R0, ZONE, 5.0, 300.0);
        assert!((squeezed.ratio - MIN_SHARE).abs() < 1e-9);
        let t = tiles(&squeezed, Rotation::R0, ZONE);
        assert!(t.first.1 .2 >= (room * MIN_SHARE) as i32 - 1);
    }

    #[test]
    fn dragging_on_a_flipped_panel_follows_the_finger() {
        // Upside down the unrotated first window reads on the right.
        let split = Split::new("DSI-1", 1, 2, Side::Left, Rotation::R0, 0.5);
        let moved = dragged(&split, Rotation::R180, ZONE, 0.3 * 526.0 + 6.0, 300.0);
        // As it reads, the left window (2) has 0.3 now.
        let (a, _, share) = moved.reading_order(Rotation::R180);
        assert_eq!(a, 2);
        assert!((share - 0.3).abs() < 1e-9);
    }

    #[test]
    fn letting_go_at_the_edge_ends_the_split() {
        let split = Split::new("DSI-1", 1, 2, Side::Left, Rotation::R0, 0.5);
        assert_eq!(release(&split, Rotation::R0, ZONE, 10.0, 300.0), DividerRelease::Dissolve { keep: 2 });
        assert_eq!(release(&split, Rotation::R0, ZONE, 530.0, 300.0), DividerRelease::Dissolve { keep: 1 });
        match release(&split, Rotation::R0, ZONE, 200.0, 300.0) {
            DividerRelease::Keep(s) => assert!(s.ratio > MIN_SHARE && s.ratio < 0.5),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn halves_of_an_area_for_the_drop_preview() {
        let area = (0.0, 0.0, 100.0, 200.0);
        let g = DIVIDER as f64 / 2.0;
        assert_eq!(half_of(area, Side::Left), (0.0, 0.0, 50.0 - g, 200.0));
        assert_eq!(half_of(area, Side::Bottom), (0.0, 100.0 + g, 100.0, 100.0 - g));
    }

    #[test]
    fn sides_parse_and_pair() {
        assert_eq!(Side::parse("LEFT"), Some(Side::Left));
        assert_eq!(Side::parse("down"), Some(Side::Bottom));
        assert_eq!(Side::parse("middle"), None);
        assert_eq!(Side::Top.opposite(), Side::Bottom);
        assert_eq!(Side::of(Axis::Stacked, false), Side::Bottom);
        let split = Split::new("DSI-1", 1, 2, Side::Right, Rotation::R0, 0.5);
        assert_eq!(split.partner(1), Some(2));
        assert_eq!(split.partner(3), None);
        assert!(split.has(2) && !split.has(5));
    }
}
