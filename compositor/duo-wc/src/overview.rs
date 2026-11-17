//! The task overview: every window as a live picture, per panel.
//!
//! Opened on every lit panel at once (a swipe up from the bottom edge that
//! stops and holds, the status bar's button, or `duoctl overview`). Each panel
//! shows ITS windows (front first) as cards in a grid below the status bar;
//! a window that spans is shown on the first lit panel. On a card:
//!
//! - **tap**: switch to it (it comes to the front of its panel, which becomes
//!   the active one) and the overview closes;
//! - **swipe it up** (or the X button at its top right): close the window;
//!   on the lower of two stacked panels (the phone on its side) a swipe up
//!   that goes on to the hinge is a carry to the other panel instead
//!   ([`dismiss_goes_across`]);
//! - **carry it** (sideways or down; or hold it still first, [`LIFT_MS`], to
//!   pick it up, then anywhere, the top too) and let go: it goes to the
//!   snap target under the finger, the same ones as a window carried by its
//!   top ([`crate::snap`]): the other panel (carried across the hinge, one
//!   drag over both touchscreens, [`crate::stitch`]), a half of a panel (a
//!   split with another window there, [`crate::split`]), both panels (let go
//!   on the hinge). A see-through accent area shows the target meanwhile;
//! - the arrow button at its top left: move it to the other panel.
//!
//! A tap on the empty background closes the overview (back to what was in
//! front).
//!
//! This module is the geometry and the gesture rules, pure (no Smithay types):
//! positions are logical px in the output's own frame as it reads.

/// Room between cards, and around them.
pub const GAP: f64 = 16.0;

/// Kept free at the bottom of a panel for the dock the shell shows with the
/// overview.
pub const DOCK_RESERVE: f64 = 84.0;

/// A card is never bigger than this share of its window: it must read as an
/// overview, not as the window itself.
pub const MAX_SCALE: f64 = 0.6;

/// The band above each card's picture for its label (the app's icon and the
/// window's title, drawn by the shell from the event stream: duo-wc draws no
/// text). It belongs to the card (a tap there switches to the window).
pub const LABEL: f64 = 30.0;

/// The cards' corner radius (the picture is drawn with rounded corners).
pub const CARD_RADIUS: f64 = 14.0;

/// The round buttons on a card (diameter).
pub const BUTTON: f64 = 40.0;
/// How far the buttons sit in from the picture's top edges (inside it, so
/// neighbouring cards' never meet), set by the corner radius: at 6 px (until
/// 2026-11-12) a button nearly met the rounded corner and seemed to stick
/// out of the card; three quarters of the radius keeps the disc
/// clearly inside the curve, the gap along the corner's diagonal larger
/// than along the edges.
pub const BUTTON_INSET: f64 = CARD_RADIUS * 0.75;

/// A touch that moves less than this is a tap.
pub const TAP_SLOP: f64 = 12.0;

/// How far up a card must be swiped to close its window (or this share of its
/// height, whichever is less).
pub const CLOSE_DISTANCE: f64 = 90.0;
const CLOSE_SHARE: f64 = 0.35;

/// The most columns a panel's grid has.
const MAX_COLUMNS: usize = 3;

/// Hold a card this long without moving and it is picked up: then it can be
/// carried anywhere, up too (a quick swipe up closes the window).
pub const LIFT_MS: u64 = 350;

/// A rectangle in logical px.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rect {
    /// Left.
    pub x: f64,
    /// Top.
    pub y: f64,
    /// Width.
    pub w: f64,
    /// Height.
    pub h: f64,
}

impl Rect {
    /// A rectangle.
    pub fn new(x: f64, y: f64, w: f64, h: f64) -> Self {
        Rect { x, y, w, h }
    }

    /// Whether a point is inside.
    pub fn contains(&self, x: f64, y: f64) -> bool {
        x >= self.x && x < self.x + self.w && y >= self.y && y < self.y + self.h
    }

    /// The centre.
    pub fn center(&self) -> (f64, f64) {
        (self.x + self.w / 2.0, self.y + self.h / 2.0)
    }

    /// Moved by `(dx, dy)`.
    pub fn offset(&self, dx: f64, dy: f64) -> Rect {
        Rect::new(self.x + dx, self.y + dy, self.w, self.h)
    }

    /// Shrunk by `d` on every side (never below nothing).
    pub fn inset(&self, d: f64) -> Rect {
        Rect::new(self.x + d, self.y + d, (self.w - 2.0 * d).max(0.0), (self.h - 2.0 * d).max(0.0))
    }

    /// The part shared with `other`, if any (not just an edge).
    pub fn intersect(&self, other: Rect) -> Option<Rect> {
        let x0 = self.x.max(other.x);
        let y0 = self.y.max(other.y);
        let x1 = (self.x + self.w).min(other.x + other.w);
        let y1 = (self.y + self.h).min(other.y + other.h);
        (x1 > x0 && y1 > y0).then(|| Rect::new(x0, y0, x1 - x0, y1 - y0))
    }

    /// Whether it shares more than an edge with `other`.
    pub fn overlaps(&self, other: Rect) -> bool {
        self.intersect(other).is_some()
    }
}

/// Where the other panel is, seen from this one: the way its cards' move
/// arrows point and the edge a drag drops at.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Dir {
    /// To the left.
    Left,
    /// To the right.
    Right,
    /// Above.
    Up,
    /// Below.
    Down,
}

/// Which way `to` lies from `from` (by their centres, the larger axis wins).
pub fn direction(from: Rect, to: Rect) -> Dir {
    let (fx, fy) = from.center();
    let (tx, ty) = to.center();
    let (dx, dy) = (tx - fx, ty - fy);
    if dx.abs() >= dy.abs() {
        if dx >= 0.0 {
            Dir::Right
        } else {
            Dir::Left
        }
    } else if dy >= 0.0 {
        Dir::Down
    } else {
        Dir::Up
    }
}

/// One card: a window's picture and its buttons, in the panel's frame.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Card {
    /// The window's control-socket id.
    pub id: u64,
    /// Where its picture goes (the window scaled down, aspect kept).
    pub thumb: Rect,
    /// The band above the picture where its label goes.
    pub label: Rect,
    /// How much the window is scaled down to fit.
    pub scale: f64,
    /// The close button.
    pub close: Rect,
    /// The move button, when there is another panel to move to.
    pub move_to: Option<Rect>,
    /// The force-close button, when the window was asked to close and did
    /// not answer ([`FORCE_BUTTON`] across, in the picture's middle).
    pub force: Option<Rect>,
}

/// The force-close button's size.
pub const FORCE_BUTTON: f64 = 64.0;

impl Card {
    /// The force-close button for this card (in its picture's middle).
    pub fn force_button(&self) -> Rect {
        let (cx, cy) = self.thumb.center();
        Rect::new(cx - FORCE_BUTTON / 2.0, cy - FORCE_BUTTON / 2.0, FORCE_BUTTON, FORCE_BUTTON)
    }
}

/// The area a panel's cards are laid out in: the panel less the status bar
/// on top and the dock's room at the bottom.
pub fn area(panel_w: f64, panel_h: f64, top_inset: f64) -> Rect {
    let top = top_inset.max(0.0);
    Rect::new(0.0, top, panel_w, (panel_h - top - DOCK_RESERVE).max(0.0))
}

/// The share of the panels' height the row of spanning windows takes (of
/// their width for the column of stacked panels).
pub const SPAN_ROW_SHARE: f64 = 0.36;

/// Whether the first two areas are one above the other (the phone on its
/// side: the hinge across), not side by side.
fn stacked(areas: &[Rect]) -> bool {
    let [a, b, ..] = areas else {
        return false;
    };
    let overlap_x = a.x < b.x + b.w && b.x < a.x + a.w;
    let overlap_y = a.y < b.y + b.h && b.y < a.y + a.h;
    overlap_x && !overlap_y
}

/// The canvas view: windows that span both panels get a row of their own
/// across the top of both panels' areas (`areas`, global: their cards sit
/// over the hinge, like the windows), the panels' own windows the rest of
/// each area below it. Returns the row and each area below it.
///
/// Stacked panels (the phone on its side) get a COLUMN instead, down the
/// left of both areas and across the hinge (a spanning window is tall
/// there), the panels' own windows to its right. Until 2026-11-15 they got
/// the side-by-side row: it sat on the lower panel only, and the upper
/// panel's area below it had no height, so none of its cards showed.
pub fn split_for_span(areas: &[Rect]) -> (Rect, Vec<Rect>) {
    if areas.is_empty() {
        return (Rect::new(0.0, 0.0, 0.0, 0.0), Vec::new());
    }
    if stacked(areas) {
        let left = areas.iter().map(|a| a.x).fold(f64::MIN, f64::max);
        let w = areas.iter().map(|a| a.w).fold(f64::INFINITY, f64::min);
        let y0 = areas.iter().map(|a| a.y).fold(f64::INFINITY, f64::min);
        let y1 = areas.iter().map(|a| a.y + a.h).fold(f64::MIN, f64::max);
        let column_w = (w * SPAN_ROW_SHARE).floor();
        let column = Rect::new(left, y0, column_w, y1 - y0);
        let rest = areas
            .iter()
            .map(|a| Rect::new(left + column_w, a.y, (a.x + a.w - left - column_w).max(0.0), a.h))
            .collect();
        return (column, rest);
    }
    let x0 = areas.iter().map(|a| a.x).fold(f64::INFINITY, f64::min);
    let x1 = areas.iter().map(|a| a.x + a.w).fold(f64::MIN, f64::max);
    let top = areas.iter().map(|a| a.y).fold(f64::MIN, f64::max);
    let h = areas.iter().map(|a| a.h).fold(f64::INFINITY, f64::min);
    let row_h = (h * SPAN_ROW_SHARE).floor();
    let row = Rect::new(x0, top, x1 - x0, row_h);
    let rest =
        areas.iter().map(|a| Rect::new(a.x, top + row_h, a.w, (a.y + a.h - top - row_h).max(0.0))).collect();
    (row, rest)
}

/// Lay out cards for windows of the given logical sizes (front first) in
/// `area`: a grid of 1 to 3 columns, whichever shows the windows biggest,
/// centred; every window is scaled by the same factor. `movable` puts a move
/// button on each card.
pub fn layout(area: Rect, windows: &[(u64, f64, f64)], movable: bool) -> Vec<Card> {
    let n = windows.len();
    if n == 0 || area.w <= 0.0 || area.h <= 0.0 {
        return Vec::new();
    }
    let max_w = windows.iter().map(|w| w.1).fold(1.0, f64::max);
    let max_h = windows.iter().map(|w| w.2).fold(1.0, f64::max);

    let mut best: Option<(f64, usize)> = None;
    for columns in 1..=n.min(MAX_COLUMNS) {
        let rows = n.div_ceil(columns);
        let cell_w = (area.w - (columns as f64 + 1.0) * GAP) / columns as f64;
        let cell_h = (area.h - (rows as f64 + 1.0) * GAP) / rows as f64 - LABEL;
        if cell_w <= 0.0 || cell_h <= 0.0 {
            continue;
        }
        let scale = (cell_w / max_w).min(cell_h / max_h).min(MAX_SCALE);
        if best.map(|(s, _)| scale > s + 1e-9).unwrap_or(true) {
            best = Some((scale, columns));
        }
    }
    let Some((scale, columns)) = best else {
        return Vec::new();
    };
    let rows = n.div_ceil(columns);
    let (cell_w, cell_h) = (max_w * scale, max_h * scale + LABEL);
    let grid_w = columns as f64 * cell_w + (columns as f64 - 1.0) * GAP;
    let grid_h = rows as f64 * cell_h + (rows as f64 - 1.0) * GAP;
    let left = area.x + (area.w - grid_w) / 2.0;
    let top = area.y + (area.h - grid_h) / 2.0;

    windows
        .iter()
        .enumerate()
        .map(|(i, (id, w, h))| {
            let (col, row) = ((i % columns) as f64, (i / columns) as f64);
            let (tw, th) = (w * scale, h * scale);
            let cx = left + col * (cell_w + GAP) + (cell_w - tw) / 2.0;
            // The picture sits in the cell below the label band, the label
            // right above the picture.
            let cy = top + row * (cell_h + GAP) + LABEL + (cell_h - LABEL - th) / 2.0;
            let thumb = Rect::new(cx, cy, tw, th);
            let label = Rect::new(cx, cy - LABEL, tw, LABEL);
            let close =
                Rect::new(thumb.x + thumb.w - BUTTON_INSET - BUTTON, thumb.y + BUTTON_INSET, BUTTON, BUTTON);
            let move_to =
                movable.then(|| Rect::new(thumb.x + BUTTON_INSET, thumb.y + BUTTON_INSET, BUTTON, BUTTON));
            Card { id: *id, thumb, label, scale, close, move_to, force: None }
        })
        .collect()
}

/// What a point on a panel's overview is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Hit {
    /// A card's close button.
    Close(u64),
    /// A card's move button.
    Move(u64),
    /// A card's force-close button.
    Force(u64),
    /// A card's picture.
    Card(u64),
    /// The background.
    Empty,
}

/// What is under `(x, y)`: buttons first (they overlap the pictures' corners).
pub fn hit(cards: &[Card], x: f64, y: f64) -> Hit {
    for card in cards {
        if let Some(button) = card.force {
            if within_circle(button, x, y) {
                return Hit::Force(card.id);
            }
        }
    }
    for card in cards {
        if within_circle(card.close, x, y) {
            return Hit::Close(card.id);
        }
        if let Some(button) = card.move_to {
            if within_circle(button, x, y) {
                return Hit::Move(card.id);
            }
        }
    }
    for card in cards {
        if card.thumb.contains(x, y) || card.label.contains(x, y) {
            return Hit::Card(card.id);
        }
    }
    Hit::Empty
}

/// Buttons are round, with a few px of grace around them for fingers.
fn within_circle(button: Rect, x: f64, y: f64) -> bool {
    let (cx, cy) = button.center();
    let r = button.w / 2.0 + 6.0;
    (x - cx).powi(2) + (y - cy).powi(2) <= r * r
}

/// What a finger on a card is doing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Drag {
    /// Not far enough to tell.
    Pending,
    /// Up: swiping the card away (it follows the finger vertically).
    Dismiss,
    /// Sideways or down: carrying the card (it follows the finger).
    Carry,
    /// Held still, then picked up: carried anywhere.
    Lifted,
}

impl Drag {
    /// The card follows the finger to a drop.
    pub fn carries(self) -> bool {
        matches!(self, Drag::Carry | Drag::Lifted)
    }
}

/// Classify a finger that went down on a card and is now `(dx, dy)` away.
/// Once decided it stays: callers keep the first verdict that is not Pending.
pub fn classify(dx: f64, dy: f64) -> Drag {
    if dx.hypot(dy) < TAP_SLOP {
        return Drag::Pending;
    }
    if dy < 0.0 && dy.abs() > dx.abs() {
        Drag::Dismiss
    } else {
        Drag::Carry
    }
}

/// Whether a card being swiped away goes on as a carry: on a panel whose
/// other panel lies ABOVE it (`toward`; stacked panels, the lower one) a
/// swipe up is also the way across the hinge. It is the close until the
/// finger comes within `reach` of the panel's top edge (`from_top`, the
/// hinge side; the band where a lift puts a carried card on the other
/// panel), then a carry like any other: the card goes over to the other
/// panel ahead of the finger, a lift there drops it there, a finger coming
/// down on the other panel carries on with it, and back down it is
/// dropped on its own panel (nothing closes). Until 2026-11-15 a drag up
/// was only ever the close there: cards carried from the lower panel up to
/// the other one had their windows asked to close.
pub fn dismiss_goes_across(drag: Drag, toward: Option<Dir>, from_top: f64, reach: f64) -> bool {
    drag == Drag::Dismiss && toward == Some(Dir::Up) && from_top <= reach
}

/// What letting go of a card does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Release {
    /// It was a tap: switch to the window.
    Activate,
    /// Swiped away: close the window.
    Close,
    /// Carried and let go: it goes to the snap target under the finger
    /// ([`crate::snap`]: a panel, a half of one, both panels).
    Drop,
    /// Nothing: the card goes back.
    SnapBack,
}

/// The verdict when a finger lets go of a card: `drag` is what the finger
/// was doing, `(dx, dy)` how far it went, `thumb_h` the card's height.
pub fn release(drag: Drag, dx: f64, dy: f64, thumb_h: f64) -> Release {
    match drag {
        Drag::Pending => Release::Activate,
        Drag::Dismiss => {
            let needed = CLOSE_DISTANCE.min(thumb_h * CLOSE_SHARE);
            if -dy >= needed {
                Release::Close
            } else {
                Release::SnapBack
            }
        }
        Drag::Carry | Drag::Lifted => {
            if dx.hypot(dy) < TAP_SLOP {
                // Picked up and put down where it was.
                Release::SnapBack
            } else {
                Release::Drop
            }
        }
    }
}

/// How long the cards take to fly from their windows to their places when
/// the overview opens.
pub const OPEN_MS: u64 = 180;

/// Where a card is `t` (0..1) of the way through the opening: from its
/// window's own rectangle `from` to its place `to`, easing out (fast, then
/// settling).
pub fn opening(from: Rect, to: Rect, t: f64) -> Rect {
    let t = t.clamp(0.0, 1.0);
    let e = 1.0 - (1.0 - t).powi(3);
    let mix = |a: f64, b: f64| a + (b - a) * e;
    Rect::new(mix(from.x, to.x), mix(from.y, to.y), mix(from.w, to.w), mix(from.h, to.h))
}

/// How far up or down a card being swiped away is drawn for a finger `dy`
/// from where it went down: with the finger upwards, a little of it
/// downwards (it resists).
pub fn dismiss_offset(dy: f64) -> f64 {
    dy.min(0.0) + dy.max(0.0) * 0.3
}

/// How see-through a card being swiped away is (1 = solid).
pub fn dismiss_alpha(dy: f64, thumb_h: f64) -> f32 {
    let t = (dy.abs() / thumb_h.max(1.0)).min(1.0);
    (1.0 - 0.7 * t) as f32
}

#[cfg(test)]
mod tests {
    use super::*;

    const PANEL: (f64, f64) = (538.0, 757.0);

    /// How far a point is inside a rectangle with rounded corners (negative:
    /// outside): the distance to its outline.
    fn inside_rounded(r: Rect, radius: f64, (x, y): (f64, f64)) -> f64 {
        let (cx, cy) = r.center();
        let qx = (x - cx).abs() - (r.w / 2.0 - radius);
        let qy = (y - cy).abs() - (r.h / 2.0 - radius);
        let outside = qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - radius;
        -outside
    }

    #[test]
    fn the_buttons_sit_inside_the_rounded_corners() {
        // One card, two columns, three columns: the buttons keep their
        // distance from the card's rounded outline, corners included.
        for n in [1u64, 2, 5] {
            let windows: Vec<_> = (1..=n).map(|id| (id, 538.0, 709.0)).collect();
            for card in layout(panel_area(), &windows, true) {
                let buttons = std::iter::once(card.close).chain(card.move_to);
                for b in buttons {
                    // The disc's clearance: centre's distance to the outline
                    // less its radius.
                    let clearance = inside_rounded(card.thumb, CARD_RADIUS, b.center()) - b.w / 2.0;
                    assert!(clearance >= BUTTON_INSET - 1e-9, "n = {n}: {clearance}");
                    assert!(clearance >= 10.0, "n = {n}: {clearance}");
                }
                // The two buttons do not meet.
                if let Some(m) = card.move_to {
                    assert!(m.x + m.w < card.close.x, "n = {n}");
                }
            }
        }
    }

    fn panel_area() -> Rect {
        area(PANEL.0, PANEL.1, 48.0)
    }

    #[test]
    fn rectangles_intersect() {
        let a = Rect::new(0.0, 0.0, 100.0, 50.0);
        assert_eq!(a.intersect(Rect::new(80.0, 10.0, 40.0, 100.0)), Some(Rect::new(80.0, 10.0, 20.0, 40.0)));
        assert_eq!(a.intersect(Rect::new(100.0, 0.0, 10.0, 10.0)), None);
        assert!(!a.overlaps(Rect::new(-10.0, 50.0, 200.0, 5.0)));
        assert!(a.overlaps(Rect::new(-10.0, 49.0, 200.0, 5.0)));
    }

    #[test]
    fn area_leaves_room_for_the_bar_and_the_dock() {
        let a = panel_area();
        assert_eq!(a, Rect::new(0.0, 48.0, 538.0, 757.0 - 48.0 - DOCK_RESERVE));
    }

    #[test]
    fn one_window_is_one_card_at_most_the_max_scale() {
        let cards = layout(panel_area(), &[(7, 538.0, 709.0)], true);
        assert_eq!(cards.len(), 1);
        let c = cards[0];
        assert_eq!(c.id, 7);
        assert!((c.scale - MAX_SCALE).abs() < 1e-9);
        // Centred in the area.
        let a = panel_area();
        assert!((c.thumb.center().0 - a.center().0).abs() < 1e-6);
        // The label and the picture together are centred vertically.
        let middle = (c.label.y + c.thumb.y + c.thumb.h) / 2.0;
        assert!((middle - a.center().1).abs() < 1e-6);
        assert!(c.move_to.is_some());
    }

    #[test]
    fn two_to_four_windows_make_two_columns() {
        for n in 2..=4u64 {
            let windows: Vec<_> = (1..=n).map(|id| (id, 538.0, 709.0)).collect();
            let cards = layout(panel_area(), &windows, false);
            assert_eq!(cards.len(), n as usize);
            // Two columns: cards 1 and 2 side by side.
            assert!((cards[0].thumb.y - cards[1].thumb.y).abs() < 1e-6, "n = {n}");
            assert!(cards[1].thumb.x > cards[0].thumb.x + cards[0].thumb.w);
            assert!(cards.iter().all(|c| c.move_to.is_none()));
        }
    }

    #[test]
    fn cards_stay_inside_the_area_and_do_not_overlap() {
        let a = panel_area();
        for n in 1..=9u64 {
            let windows: Vec<_> = (1..=n).map(|id| (id, 538.0, 709.0)).collect();
            let cards = layout(a, &windows, true);
            for (i, c) in cards.iter().enumerate() {
                assert!(c.thumb.x >= a.x - 1e-6 && c.thumb.x + c.thumb.w <= a.x + a.w + 1e-6);
                assert!(c.thumb.y >= a.y - 1e-6 && c.thumb.y + c.thumb.h <= a.y + a.h + 1e-6);
                // The label band is right above the picture, inside the area.
                assert!((c.label.y + c.label.h - c.thumb.y).abs() < 1e-6);
                assert!(c.label.y >= a.y - 1e-6, "n = {n}");
                for d in &cards[i + 1..] {
                    let apart = c.thumb.x + c.thumb.w <= d.thumb.x + 1e-6
                        || d.thumb.x + d.thumb.w <= c.thumb.x + 1e-6
                        || c.thumb.y + c.thumb.h <= d.thumb.y + 1e-6
                        || d.thumb.y + d.thumb.h <= c.thumb.y + 1e-6;
                    assert!(apart, "cards {} and {} overlap for n = {n}", c.id, d.id);
                }
            }
        }
    }

    #[test]
    fn a_spanning_window_keeps_its_aspect() {
        let cards = layout(panel_area(), &[(1, 1102.0, 709.0), (2, 538.0, 709.0)], true);
        let wide = cards[0];
        assert!((wide.thumb.w / wide.thumb.h - 1102.0 / 709.0).abs() < 1e-6);
        assert!((cards[1].thumb.w / cards[1].thumb.h - 538.0 / 709.0).abs() < 1e-6);
    }

    #[test]
    fn spanning_windows_get_a_row_over_the_hinge() {
        let left = Rect::new(0.0, 48.0, 538.0, 625.0);
        let right = Rect::new(564.0, 48.0, 538.0, 625.0);
        let (row, rest) = split_for_span(&[left, right]);
        assert_eq!((row.x, row.y, row.w), (0.0, 48.0, 1102.0));
        assert!((row.h - (625.0 * SPAN_ROW_SHARE).floor()).abs() < 1e-9);
        assert_eq!(rest.len(), 2);
        assert_eq!(rest[1].x, 564.0);
        assert!((rest[0].y - (48.0 + row.h)).abs() < 1e-9);
        assert!((rest[0].y + rest[0].h - (48.0 + 625.0)).abs() < 1e-9);
        // A spanning window's card sits across the hinge.
        let cards = layout(row, &[(9, 1102.0, 709.0)], false);
        let c = cards[0];
        assert!(c.thumb.x < 538.0 && c.thumb.x + c.thumb.w > 564.0);
        assert!(split_for_span(&[]).1.is_empty());
    }

    #[test]
    fn nothing_to_lay_out() {
        assert!(layout(panel_area(), &[], true).is_empty());
        assert!(layout(Rect::new(0.0, 0.0, 0.0, 0.0), &[(1, 10.0, 10.0)], true).is_empty());
    }

    #[test]
    fn buttons_win_over_the_picture_they_sit_on() {
        let cards = layout(panel_area(), &[(3, 538.0, 709.0)], true);
        let c = cards[0];
        let (cx, cy) = c.close.center();
        assert_eq!(hit(&cards, cx, cy), Hit::Close(3));
        let (mx, my) = c.move_to.unwrap().center();
        assert_eq!(hit(&cards, mx, my), Hit::Move(3));
        let (tx, ty) = c.thumb.center();
        assert_eq!(hit(&cards, tx, ty), Hit::Card(3));
        let (lx, ly) = c.label.center();
        assert_eq!(hit(&cards, lx, ly), Hit::Card(3));
        assert_eq!(hit(&cards, 2.0, 60.0), Hit::Empty);
        // A card that did not answer its close: the force button wins.
        let mut stuck = cards.clone();
        stuck[0].force = Some(stuck[0].force_button());
        assert_eq!(hit(&stuck, tx, ty), Hit::Force(3));
        assert_eq!(hit(&stuck, cx, cy), Hit::Close(3));
    }

    #[test]
    fn the_other_panel_is_where_its_centre_is() {
        let left = Rect::new(0.0, 0.0, 538.0, 757.0);
        let right = Rect::new(564.0, 0.0, 538.0, 757.0);
        assert_eq!(direction(left, right), Dir::Right);
        assert_eq!(direction(right, left), Dir::Left);
        // Both turned 90: DSI-1 above DSI-2.
        let top = Rect::new(0.0, 0.0, 757.0, 538.0);
        let bottom = Rect::new(0.0, 564.0, 757.0, 538.0);
        assert_eq!(direction(top, bottom), Dir::Down);
        assert_eq!(direction(bottom, top), Dir::Up);
    }

    #[test]
    fn a_drag_is_decided_by_its_larger_axis() {
        assert_eq!(classify(3.0, -4.0), Drag::Pending);
        assert_eq!(classify(5.0, -40.0), Drag::Dismiss);
        // Down carries the card (to the lower half), it does not dismiss.
        assert_eq!(classify(2.0, 30.0), Drag::Carry);
        assert_eq!(classify(-40.0, 10.0), Drag::Carry);
        assert!(Drag::Lifted.carries() && Drag::Carry.carries() && !Drag::Dismiss.carries());
    }

    #[test]
    fn letting_go() {
        // A tap.
        assert_eq!(release(Drag::Pending, 1.0, 1.0, 300.0), Release::Activate);
        // Swiped up far enough, and not; a small card needs less.
        assert_eq!(release(Drag::Dismiss, 0.0, -100.0, 300.0), Release::Close);
        assert_eq!(release(Drag::Dismiss, 0.0, -50.0, 300.0), Release::SnapBack);
        assert_eq!(release(Drag::Dismiss, 0.0, -60.0, 150.0), Release::Close);
        // Down is not a dismissal.
        assert_eq!(release(Drag::Dismiss, 0.0, 200.0, 300.0), Release::SnapBack);
        // Carried: dropped on the target under the finger; picked up and
        // put back: nothing.
        assert_eq!(release(Drag::Carry, 200.0, 0.0, 300.0), Release::Drop);
        assert_eq!(release(Drag::Lifted, 0.0, -250.0, 300.0), Release::Drop);
        assert_eq!(release(Drag::Lifted, 2.0, 3.0, 300.0), Release::SnapBack);
    }

    #[test]
    fn opening_goes_from_the_window_to_the_card() {
        let from = Rect::new(0.0, 48.0, 538.0, 709.0);
        let to = Rect::new(100.0, 200.0, 200.0, 260.0);
        assert_eq!(opening(from, to, 0.0), from);
        assert_eq!(opening(from, to, 1.0), to);
        assert_eq!(opening(from, to, 3.0), to);
        // Easing out: past half way at half the time.
        let mid = opening(from, to, 0.5);
        assert!(mid.x > 50.0 && mid.x < 100.0);
        assert!(mid.w < (538.0 + 200.0) / 2.0);
    }

    #[test]
    fn a_card_fades_as_it_goes() {
        assert_eq!(dismiss_alpha(0.0, 300.0), 1.0);
        assert!(dismiss_alpha(150.0, 300.0) < 1.0);
        assert!((dismiss_alpha(600.0, 300.0) - 0.3).abs() < 1e-6);
        // Drawn with the finger upwards, resisting downwards.
        assert_eq!(dismiss_offset(-120.0), -120.0);
        assert!((dismiss_offset(100.0) - 30.0).abs() < 1e-9);
    }

    /// The phone on its side, both panels turned alike: 90 puts DSI-1 above
    /// DSI-2, 270 DSI-2 above DSI-1. The panels in DSI-1, DSI-2 order (as
    /// duo-wc lists them), the gap 26 px. Returns the panels' rectangles
    /// and which index is the upper one.
    fn landscape(rotation: u32) -> (Vec<Rect>, usize) {
        let upper = Rect::new(0.0, 0.0, 757.0, 538.0);
        let lower = Rect::new(0.0, 564.0, 757.0, 538.0);
        match rotation {
            90 => (vec![upper, lower], 0),
            270 => (vec![lower, upper], 1),
            _ => unreachable!(),
        }
    }

    fn snap_panels(rects: &[Rect]) -> Vec<crate::snap::Panel> {
        rects
            .iter()
            .enumerate()
            .map(|(i, r)| crate::snap::Panel {
                name: format!("DSI-{}", i + 1),
                rect: *r,
                zone: area(r.w, r.h, 48.0).offset(r.x, r.y),
                can_split: true,
            })
            .collect()
    }

    #[test]
    fn a_swipe_up_to_the_hinge_carries_on_the_lower_panel_only() {
        let reach = 56.0;
        // The lower panel: up is the other panel; the close until the
        // finger is at the top edge, then a carry.
        assert!(!dismiss_goes_across(Drag::Dismiss, Some(Dir::Up), 200.0, reach));
        assert!(!dismiss_goes_across(Drag::Dismiss, Some(Dir::Up), 57.0, reach));
        assert!(dismiss_goes_across(Drag::Dismiss, Some(Dir::Up), 56.0, reach));
        assert!(dismiss_goes_across(Drag::Dismiss, Some(Dir::Up), 3.0, reach));
        assert!(dismiss_goes_across(Drag::Dismiss, Some(Dir::Up), -10.0, reach));
        // The upper panel (the other one is below), side-by-side panels,
        // one panel (laptop mode's desk, a closed phone): a swipe up is the
        // close, whatever the top edge.
        for toward in [Some(Dir::Down), Some(Dir::Left), Some(Dir::Right), None] {
            assert!(!dismiss_goes_across(Drag::Dismiss, toward, 3.0, reach), "{toward:?}");
        }
        // Only a dismissal turns (a carry is one already).
        for drag in [Drag::Pending, Drag::Carry, Drag::Lifted] {
            assert!(!dismiss_goes_across(drag, Some(Dir::Up), 3.0, reach), "{drag:?}");
        }
    }

    #[test]
    fn a_card_goes_up_and_down_across_a_horizontal_hinge_at_90_and_270() {
        let snap = crate::snap::Config::default();
        let stitch = crate::stitch::Config::default();
        let ctx = crate::snap::Context::default();
        for rotation in [90, 270] {
            let (rects, upper) = landscape(rotation);
            let lower = 1 - upper;
            let panels = snap_panels(&rects);
            let hinge = crate::snap::hinge(&rects).unwrap();
            let (top, bottom) = (rects[upper], rects[lower]);
            let up_name = panels[upper].name.clone();
            let low_name = panels[lower].name.clone();
            // The direction each panel's move arrow points.
            assert_eq!(direction(bottom, top), Dir::Up, "{rotation}");
            assert_eq!(direction(top, bottom), Dir::Down, "{rotation}");

            // Lower -> upper: a card in the middle of
            // the lower panel, the finger straight up.
            let start = (300.0, bottom.y + 270.0);
            let mut drag = classify(0.0, -20.0);
            assert_eq!(drag, Drag::Dismiss, "{rotation}");
            for y in (0..=270).rev().step_by(10) {
                let at = (start.0, bottom.y + y as f64);
                if dismiss_goes_across(
                    drag,
                    Some(Dir::Up),
                    at.1 - bottom.y,
                    stitch.edge_band.max(snap.hinge_band),
                ) {
                    drag = Drag::Carry;
                }
                if y > 56 {
                    assert_eq!(drag, Drag::Dismiss, "{rotation}: y = {y}");
                }
            }
            assert_eq!(drag, Drag::Carry, "{rotation}");
            // At the top edge the target is the upper panel, and the lift
            // there drops it there (open for the other side).
            let at = (300.0, bottom.y + 10.0);
            assert_eq!(
                crate::snap::target(&panels, at.0, at.1, ctx, &snap),
                Some(crate::snap::Target::Panel(up_name.clone())),
                "{rotation}"
            );
            assert!(matches!(
                crate::stitch::lift(Some(&hinge), &rects, Some(lower), at, 0, false, &stitch),
                crate::stitch::Lift::DownOpen(_)
            ));
            assert_eq!(release(drag, 0.0, at.1 - start.1, 290.0), Release::Drop);
            // A finger down on the upper panel's bottom edge right after
            // goes on with it.
            let lifted = crate::stitch::Contact { panel: lower, at, time: 0 };
            let down = crate::stitch::Contact { panel: upper, at: (320.0, top.y + top.h - 12.0), time: 120 };
            assert!(crate::stitch::continues(&hinge, &rects, &lifted, true, &down, &stitch), "{rotation}");
            // The picture goes over ahead of the finger, into the upper panel.
            let ghost = Rect::new(150.0, bottom.y - 20.0, 300.0, 200.0);
            let led = crate::snap::lead_across(&rects, lower, ghost, 16.0);
            assert!(led.y >= top.y && led.y + led.h <= top.y + top.h, "{rotation}: {led:?}");

            // Upper -> lower: down is a carry from the start.
            assert_eq!(classify(0.0, 20.0), Drag::Carry);
            let at = (300.0, top.y + top.h - 10.0);
            assert_eq!(
                crate::snap::target(&panels, at.0, at.1, ctx, &snap),
                Some(crate::snap::Target::Panel(low_name.clone())),
                "{rotation}"
            );
            assert!(matches!(
                crate::stitch::lift(Some(&hinge), &rects, Some(upper), at, 0, false, &stitch),
                crate::stitch::Lift::DownOpen(_)
            ));
            let lifted = crate::stitch::Contact { panel: upper, at, time: 0 };
            let down = crate::stitch::Contact { panel: lower, at: (280.0, bottom.y + 15.0), time: 90 };
            assert!(crate::stitch::continues(&hinge, &rects, &lifted, true, &down, &stitch), "{rotation}");
            let led = crate::snap::lead_across(&rects, upper, Rect::new(150.0, 400.0, 300.0, 200.0), 16.0);
            assert!(led.y >= bottom.y && led.y + led.h <= bottom.y + bottom.h, "{rotation}: {led:?}");

            // A swipe up on the upper panel still closes, at its top edge too.
            assert!(!dismiss_goes_across(Drag::Dismiss, Some(Dir::Down), 5.0, 56.0));
            assert_eq!(release(Drag::Dismiss, 0.0, -120.0, 290.0), Release::Close);
        }
    }

    #[test]
    fn stacked_panels_give_spanning_windows_a_column_over_the_hinge() {
        for rotation in [90, 270] {
            let (rects, upper) = landscape(rotation);
            let lower = 1 - upper;
            let areas: Vec<Rect> = rects.iter().map(|r| area(r.w, r.h, 48.0).offset(r.x, r.y)).collect();
            let (column, rest) = split_for_span(&areas);
            // Down the left of both areas, across the hinge.
            assert_eq!(column.x, 0.0);
            assert!((column.w - (757.0 * SPAN_ROW_SHARE).floor()).abs() < 1e-9);
            assert_eq!(column.y, areas[upper].y);
            assert_eq!(column.y + column.h, areas[lower].y + areas[lower].h);
            // Each panel keeps its whole height to the right of it (until
            // 2026-11-15 the upper one was left with none).
            for (a, r) in areas.iter().zip(&rest) {
                assert_eq!((r.y, r.h), (a.y, a.h), "{rotation}");
                assert_eq!(r.x, column.x + column.w);
                assert!((r.x + r.w - (a.x + a.w)).abs() < 1e-9);
            }
            // A spanning window's card sits across the hinge; both panels'
            // own windows get cards inside their own panel.
            let span = layout(column, &[(9, 757.0, 1054.0)], false);
            let c = span[0].thumb;
            assert!(c.y < rects[upper].y + rects[upper].h && c.y + c.h > rects[lower].y, "{rotation}: {c:?}");
            for (i, r) in rest.iter().enumerate() {
                let cards = layout(*r, &[(1, 757.0, 490.0), (2, 757.0, 490.0)], true);
                assert_eq!(cards.len(), 2, "{rotation}: panel {i}");
                for card in cards {
                    assert!(rects[i].contains(card.thumb.x, card.thumb.y), "{rotation}: panel {i}");
                    assert!(card.thumb.y + card.thumb.h <= rects[i].y + rects[i].h);
                }
            }
        }
        // Side by side: the row across the top, as before.
        let left = Rect::new(0.0, 48.0, 538.0, 625.0);
        let right = Rect::new(564.0, 48.0, 538.0, 625.0);
        let (row, _) = split_for_span(&[left, right]);
        assert_eq!((row.x, row.y, row.w), (0.0, 48.0, 1102.0));
    }
}
