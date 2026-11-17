//! Where the two panels sit in the one desktop.
//!
//! The Duo 2 is two portrait panels side by side with a hinge between them. The
//! global space puts DSI-1 (left) at x = 0 and DSI-2 (right) to its right, either
//! with the hinge in between or without:
//!
//! - **gap on** (default): DSI-2 starts at `width(DSI-1) + HINGE_GAP_PX`, as if the
//!   picture went on under the hinge. Something spanning both panels then looks
//!   physically continuous; the strip behind the hinge is simply not shown.
//! - **gap off**: DSI-2 starts at `width(DSI-1)`, the panels are adjacent and nothing
//!   is lost behind the hinge.
//!
//! The hinge is a physical distance (66 panel pixels); the global space Smithay
//! keeps is logical, so both the panel widths and the gap are converted with the
//! output scale. A panel's logical width is rounded UP, the way Smithay sizes an
//! output (`Space::output_geometry`), so with the gap off the next panel starts
//! exactly where the previous one ends, at any scale.
//!
//! Rotated panels (`set_rotation`) are placed the way the device holds them when
//! both are turned alike: turned 90 or 270 degrees the panels are one above the
//! other, with the hinge between them vertically (see [`arrange`]).

use serde::{Deserialize, Serialize};

use crate::rotation::Rotation;

/// The hinge, in physical pixels of the panels: what the stock Android layout
/// counted (1344 + 66 + 1344).
pub const HINGE_GAP_PX: i32 = 66;

/// Whether the hinge takes room in the global space.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HingeGap {
    /// The space goes on under the hinge (spanning content is continuous).
    #[default]
    On,
    /// The panels touch in the space (no content is hidden by the hinge).
    Off,
}

impl HingeGap {
    /// The gap in physical pixels.
    pub fn physical(self) -> i32 {
        match self {
            HingeGap::On => HINGE_GAP_PX,
            HingeGap::Off => 0,
        }
    }

    /// Parse the words a config file or a command line uses.
    pub fn parse(word: &str) -> Option<Self> {
        match word.trim().to_ascii_lowercase().as_str() {
            "on" | "true" | "yes" | "1" => Some(HingeGap::On),
            "off" | "false" | "no" | "0" => Some(HingeGap::Off),
            _ => None,
        }
    }
}

/// The built-in panels, in the order of the hardware (left to right when the
/// device is held upright); anything else (a USB-C display) goes after them.
fn rank(name: &str) -> (u8, &str) {
    match name {
        "DSI-1" => (0, name),
        "DSI-2" => (1, name),
        _ => (2, name),
    }
}

/// An output to place.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Panel {
    /// Connector name (`DSI-1`).
    pub name: String,
    /// Mode size in physical pixels, unrotated (1344x1892 for a panel).
    pub mode: (i32, i32),
    /// How the output is rotated.
    pub rotation: Rotation,
}

impl Panel {
    /// An unrotated panel.
    pub fn new(name: &str, mode: (i32, i32)) -> Self {
        Panel { name: name.to_owned(), mode, rotation: Rotation::R0 }
    }

    /// Its logical size, rotated and rounded up the way Smithay sizes an output.
    pub fn logical_size(&self, scale: f64) -> (i32, i32) {
        let (w, h) = if self.rotation.is_sideways() { (self.mode.1, self.mode.0) } else { self.mode };
        (logical_width(w, scale), logical_width(h, scale))
    }
}

/// Where every output goes, in LOGICAL pixels: `(name, x, y)`.
///
/// The two built-in panels follow the device: with both upright they sit side by
/// side, DSI-1 left; turned the same way they sit as the device holds them
/// (rotation 90: the device turned clockwise, DSI-1 on TOP; 180: DSI-2 left;
/// 270: DSI-2 on top), with the hinge between them when the gap is on: 66
/// physical pixels, 26 logical at the default scale of 2.5 (26.4, rounded) and
/// exactly 33 at 2. Rotated differently from each other they have no common
/// picture to continue, so they just sit side by side, without the gap.
/// Anything else goes to the right of the panels, at the top.
pub fn arrange(panels: &[Panel], gap: HingeGap, scale: f64) -> Vec<(String, i32, i32)> {
    let scale = if scale >= 1.0 { scale } else { 1.0 };
    let mut sorted: Vec<&Panel> = panels.iter().collect();
    sorted.sort_by(|a, b| rank(&a.name).cmp(&rank(&b.name)));
    let (builtin, external): (Vec<&Panel>, Vec<&Panel>) =
        sorted.into_iter().partition(|p| rank(&p.name).0 < 2);

    let mut placed = Vec::with_capacity(panels.len());
    let mut right = 0;
    match builtin.as_slice() {
        [first, second] if first.rotation == second.rotation => {
            let hinge = logical_gap(gap, scale);
            let (lead, follow) = match first.rotation {
                Rotation::R0 | Rotation::R90 => (*first, *second),
                Rotation::R180 | Rotation::R270 => (*second, *first),
            };
            let (lead_w, lead_h) = lead.logical_size(scale);
            let (follow_w, _) = follow.logical_size(scale);
            placed.push((lead.name.clone(), 0, 0));
            if first.rotation.is_sideways() {
                placed.push((follow.name.clone(), 0, lead_h + hinge));
                right = lead_w.max(follow_w);
            } else {
                placed.push((follow.name.clone(), lead_w + hinge, 0));
                right = lead_w + hinge + follow_w;
            }
        }
        others => {
            for panel in others {
                placed.push((panel.name.clone(), right, 0));
                right += panel.logical_size(scale).0;
            }
        }
    }
    for panel in external {
        placed.push((panel.name.clone(), right, 0));
        right += panel.logical_size(scale).0;
    }
    placed
}

/// A length in physical pixels as logical ones, rounded up the way Smithay sizes
/// an output.
pub fn logical_width(physical: i32, scale: f64) -> i32 {
    (physical as f64 / scale).ceil() as i32
}

/// The hinge in logical pixels, to the nearest one.
pub fn logical_gap(gap: HingeGap, scale: f64) -> i32 {
    (gap.physical() as f64 / scale).round() as i32
}

/// A rectangle in logical pixels: `(x, y, w, h)`.
pub type Rect = (i32, i32, i32, i32);

/// The area a spanning window gets: the bounding box of the panels it spans,
/// less what their layer-shell panels (status bars, the on-screen keyboard)
/// keep for themselves, edge by edge.
///
/// `panels` is each panel's geometry with the part its exclusive zones leave
/// free (`non_exclusive_zone`, in the same global space). An edge of the box
/// gives up the largest inset of the panels that touch it: side by side, both
/// panels' status bars touch the top, so the window starts below the taller
/// one; stacked (both turned 90 degrees), only the upper panel touches the top.
pub fn span_area(panels: &[(Rect, Rect)]) -> Option<Rect> {
    let (first, _) = panels.first()?;
    let (mut left, mut top) = (first.0, first.1);
    let (mut right, mut bottom) = (first.0 + first.2, first.1 + first.3);
    for ((x, y, w, h), _) in panels {
        left = left.min(*x);
        top = top.min(*y);
        right = right.max(x + w);
        bottom = bottom.max(y + h);
    }
    let (mut inset_left, mut inset_top, mut inset_right, mut inset_bottom) = (0, 0, 0, 0);
    for ((x, y, w, h), (zx, zy, zw, zh)) in panels {
        if *x == left {
            inset_left = inset_left.max(zx - x);
        }
        if *y == top {
            inset_top = inset_top.max(zy - y);
        }
        if x + w == right {
            inset_right = inset_right.max((x + w) - (zx + zw));
        }
        if y + h == bottom {
            inset_bottom = inset_bottom.max((y + h) - (zy + zh));
        }
    }
    let w = (right - inset_right) - (left + inset_left);
    let h = (bottom - inset_bottom) - (top + inset_top);
    Some((left + inset_left, top + inset_top, w.max(0), h.max(0)))
}

/// The edge a layer surface's exclusive zone is taken from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ZoneEdge {
    /// Top.
    Top,
    /// Bottom.
    Bottom,
    /// Left.
    Left,
    /// Right.
    Right,
}

/// Which edge an exclusive zone applies to, from the surface's anchors: one
/// edge, alone or with both perpendicular ones (wlr-layer-shell); otherwise
/// (a corner, two parallel edges, all four, none) it has no edge.
pub fn exclusive_edge(top: bool, bottom: bool, left: bool, right: bool) -> Option<ZoneEdge> {
    match (top, bottom, left, right) {
        (true, false, l, r) if l == r => Some(ZoneEdge::Top),
        (false, true, l, r) if l == r => Some(ZoneEdge::Bottom),
        (t, b, true, false) if t == b => Some(ZoneEdge::Left),
        (t, b, false, true) if t == b => Some(ZoneEdge::Right),
        _ => None,
    }
}

/// A panel's area (in its own frame, from 0, 0) less exclusive zones: each
/// edge gives up the largest zone on it (zones on one edge do not add up
/// here: the status bar and a dock are on different edges).
pub fn zone(size: (i32, i32), zones: &[(ZoneEdge, i32)]) -> Rect {
    let mut inset = [0i32; 4];
    for (edge, z) in zones {
        let i = *edge as usize;
        inset[i] = inset[i].max((*z).max(0));
    }
    let [top, bottom, left, right] = inset;
    (left, top, (size.0 - left - right).max(0), (size.1 - top - bottom).max(0))
}

/// The panel a popup belongs to: the one whose rectangle holds `point` (the
/// centre of the popup's anchor, in the global space), else the nearest one
/// (a point in the hinge gap belongs to the closer panel). A popup is fitted
/// into ONE panel, never across the hinge. An index into `panels`.
pub fn panel_for_point(panels: &[Rect], point: (i32, i32)) -> Option<usize> {
    let distance = |(x, y, w, h): &Rect| {
        let dx = (x - point.0).max(point.0 - (x + w - 1)).max(0) as i64;
        let dy = (y - point.1).max(point.1 - (y + h - 1)).max(0) as i64;
        dx * dx + dy * dy
    };
    panels.iter().enumerate().min_by_key(|(_, r)| distance(r)).map(|(i, _)| i)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MODE: (i32, i32) = (1344, 1892);

    #[test]
    fn exclusive_edges_follow_the_anchors() {
        // A status bar: top, left, right.
        assert_eq!(exclusive_edge(true, false, true, true), Some(ZoneEdge::Top));
        // A keyboard or a dock: bottom, left, right.
        assert_eq!(exclusive_edge(false, true, true, true), Some(ZoneEdge::Bottom));
        assert_eq!(exclusive_edge(false, false, true, false), Some(ZoneEdge::Left));
        assert_eq!(exclusive_edge(true, true, false, true), Some(ZoneEdge::Right));
        // A corner, all four, two parallel: no edge.
        assert_eq!(exclusive_edge(true, false, true, false), None);
        assert_eq!(exclusive_edge(true, true, true, true), None);
        assert_eq!(exclusive_edge(true, true, false, false), None);
    }

    #[test]
    fn a_zone_less_its_edges() {
        let z = zone((538, 757), &[(ZoneEdge::Top, 48), (ZoneEdge::Bottom, 72), (ZoneEdge::Bottom, 30)]);
        assert_eq!(z, (0, 48, 538, 757 - 48 - 72));
        assert_eq!(zone((538, 757), &[]), (0, 0, 538, 757));
    }

    fn duo() -> Vec<Panel> {
        // Deliberately out of order: the connector list is not a layout.
        vec![Panel::new("DSI-2", MODE), Panel::new("DSI-1", MODE)]
    }

    fn rotated(rotation: Rotation) -> Vec<Panel> {
        duo().into_iter().map(|p| Panel { rotation, ..p }).collect()
    }

    fn at(name: &str, x: i32, y: i32) -> (String, i32, i32) {
        (name.into(), x, y)
    }

    #[test]
    fn the_gap_puts_the_right_panel_past_the_hinge() {
        assert_eq!(arrange(&duo(), HingeGap::On, 1.0), vec![at("DSI-1", 0, 0), at("DSI-2", 1344 + 66, 0)]);
    }

    #[test]
    fn without_the_gap_the_panels_touch() {
        assert_eq!(arrange(&duo(), HingeGap::Off, 1.0), vec![at("DSI-1", 0, 0), at("DSI-2", 1344, 0)]);
    }

    #[test]
    fn the_layout_is_logical_at_scale_two() {
        assert_eq!(arrange(&duo(), HingeGap::On, 2.0), vec![at("DSI-1", 0, 0), at("DSI-2", 672 + 33, 0)]);
        assert_eq!(arrange(&duo(), HingeGap::Off, 2.0), vec![at("DSI-1", 0, 0), at("DSI-2", 672, 0)]);
    }

    #[test]
    fn at_the_default_fractional_scale_the_panels_still_line_up() {
        // 1344 / 2.5 = 537.6, which Smithay rounds up to 538.
        assert_eq!(arrange(&duo(), HingeGap::On, 2.5), vec![at("DSI-1", 0, 0), at("DSI-2", 538 + 26, 0)]);
        assert_eq!(arrange(&duo(), HingeGap::Off, 2.5), vec![at("DSI-1", 0, 0), at("DSI-2", 538, 0)]);
    }

    #[test]
    fn turned_clockwise_the_left_panel_is_on_top() {
        assert_eq!(
            arrange(&rotated(Rotation::R90), HingeGap::On, 2.5),
            vec![at("DSI-1", 0, 0), at("DSI-2", 0, 538 + 26)]
        );
        assert_eq!(
            arrange(&rotated(Rotation::R90), HingeGap::Off, 2.5),
            vec![at("DSI-1", 0, 0), at("DSI-2", 0, 538)]
        );
    }

    #[test]
    fn turned_counter_clockwise_the_right_panel_is_on_top() {
        assert_eq!(
            arrange(&rotated(Rotation::R270), HingeGap::On, 2.5),
            vec![at("DSI-2", 0, 0), at("DSI-1", 0, 538 + 26)]
        );
    }

    #[test]
    fn upside_down_the_right_panel_is_on_the_left() {
        assert_eq!(
            arrange(&rotated(Rotation::R180), HingeGap::On, 2.5),
            vec![at("DSI-2", 0, 0), at("DSI-1", 538 + 26, 0)]
        );
    }

    #[test]
    fn panels_turned_differently_sit_side_by_side_without_the_gap() {
        let panels =
            vec![Panel { rotation: Rotation::R90, ..Panel::new("DSI-1", MODE) }, Panel::new("DSI-2", MODE)];
        // DSI-1 is landscape: 1892 / 2.5 = 756.8 -> 757 wide.
        assert_eq!(arrange(&panels, HingeGap::On, 2.5), vec![at("DSI-1", 0, 0), at("DSI-2", 757, 0)]);
    }

    #[test]
    fn one_panel_alone_sits_at_the_origin() {
        let right = vec![Panel::new("DSI-2", MODE)];
        assert_eq!(arrange(&right, HingeGap::On, 2.5), vec![at("DSI-2", 0, 0)]);
    }

    #[test]
    fn an_external_display_goes_after_the_panels_without_a_hinge() {
        let mut panels = duo();
        panels.push(Panel::new("DP-1", (1920, 1080)));
        assert_eq!(
            arrange(&panels, HingeGap::On, 1.0),
            vec![at("DSI-1", 0, 0), at("DSI-2", 1410, 0), at("DP-1", 1410 + 1344, 0)]
        );
    }

    #[test]
    fn logical_sizes_follow_the_rotation() {
        assert_eq!(Panel::new("DSI-1", MODE).logical_size(2.5), (538, 757));
        let sideways = Panel { rotation: Rotation::R270, ..Panel::new("DSI-1", MODE) };
        assert_eq!(sideways.logical_size(2.5), (757, 538));
    }

    #[test]
    fn gap_words() {
        assert_eq!(HingeGap::parse(" ON "), Some(HingeGap::On));
        assert_eq!(HingeGap::parse("off"), Some(HingeGap::Off));
        assert_eq!(HingeGap::parse("maybe"), None);
    }

    #[test]
    fn a_span_starts_below_both_status_bars() {
        // Side by side, gap on: a 48 px bar on both, the keyboard (300) on DSI-2.
        let left = ((0, 0, 538, 757), (0, 48, 538, 709));
        let right = ((564, 0, 538, 757), (564, 48, 538, 409));
        assert_eq!(span_area(&[left, right]), Some((0, 48, 1102, 409)));
        // No layer surfaces: the whole box.
        let bare = [((0, 0, 538, 757), (0, 0, 538, 757)), ((564, 0, 538, 757), (564, 0, 538, 757))];
        assert_eq!(span_area(&bare), Some((0, 0, 1102, 757)));
    }

    #[test]
    fn stacked_panels_give_up_only_their_own_edges() {
        // Both at 90: DSI-1 on top, DSI-2 below; a bar at the top of each.
        let upper = ((0, 0, 757, 538), (0, 48, 757, 490));
        let lower = ((0, 564, 757, 538), (0, 612, 757, 490));
        assert_eq!(span_area(&[upper, lower]), Some((0, 48, 757, 1054)));
        assert_eq!(span_area(&[]), None);
    }

    #[test]
    fn a_popup_belongs_to_the_panel_under_its_anchor() {
        let panels = [(0, 0, 538, 757), (564, 0, 538, 757)];
        assert_eq!(panel_for_point(&panels, (100, 300)), Some(0));
        assert_eq!(panel_for_point(&panels, (900, 10)), Some(1));
        // In the hinge gap: the nearer panel.
        assert_eq!(panel_for_point(&panels, (545, 300)), Some(0));
        assert_eq!(panel_for_point(&panels, (560, 300)), Some(1));
        // Outside everything (a popup anchored above a window): still a panel.
        assert_eq!(panel_for_point(&panels, (1200, -40)), Some(1));
        // Stacked (both at 90).
        let stacked = [(0, 0, 757, 538), (0, 564, 757, 538)];
        assert_eq!(panel_for_point(&stacked, (300, 600)), Some(1));
        assert_eq!(panel_for_point(&[], (0, 0)), None);
    }
}
