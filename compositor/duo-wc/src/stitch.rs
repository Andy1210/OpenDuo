//! Carrying across the hinge: one drag over two touchscreens.
//!
//! The panels' digitizers are separate devices, so a finger that carries a
//! window (or an overview card) from one panel to the other LIFTS on the
//! first panel's hinge-side edge and comes DOWN again on the second panel's
//! hinge-side edge: two touch points. duo-wc stitches them into one drag:
//!
//! - the drag's finger lifts within [`Config::edge_band`] of its panel's
//!   hinge-side edge: a carried window is put down AT ONCE where its
//!   target says (the other panel, [`crate::snap`]: the carried picture is
//!   already shown there) and flies there from the lift on, but it stays
//!   open for the other side for [`Config::window_ms`] ([`lift`]). Until
//!   2026-11-10 the drop waited that long instead, and the window grew
//!   into its place only a moment after letting go (the journal: 360 ms
//!   from the lift to the drop). An overview card let go
//!   there is dropped at once the same way (since 2026-11-12; it flies
//!   from where it was let go to its place in the other panel's grid, and
//!   is picked up again from there: crate::taskview::CardLanding);
//! - a finger comes down on the OTHER panel within the band of its
//!   hinge-side edge, within the window, and within [`Config::max_offset`]
//!   along the hinge of where the first one lifted: the same drag goes on
//!   with it (a window put down meanwhile is picked up again from where its
//!   flight has got to);
//! - a finger can also touch both edges at once (it is wider than the
//!   hinge): a down on the other panel's edge next to where the dragging
//!   finger is NOW, while it is still down at its edge, takes the drag over
//!   too (the first finger's rest is ignored).
//!
//! The thresholds are configurable (duo-wc.conf `stitch_edge`,
//! `stitch_ms`, `stitch_offset`): the panels' edge geometry is still being
//! tuned, so real fingers will want other numbers than synthetic ones.
//! Positions are logical px in the global space. Pure: no Smithay types.

use crate::overview::Rect;
use crate::snap::Hinge;

/// The thresholds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Config {
    /// How far from a panel's hinge-side edge a lift or a down still counts
    /// as "at the hinge" (logical px).
    pub edge_band: f64,
    /// How long after the lift the other side may come down (ms).
    pub window_ms: u32,
    /// How far apart along the hinge the lift and the down may be (logical
    /// px; the y of side-by-side panels).
    pub max_offset: f64,
}

impl Default for Config {
    fn default() -> Self {
        // Real fingers (2026-11-08): nothing came down on the other panel
        // within 150 ms; a finger crossing the hinge's ~5 mm without contact
        // and the other digitizer seeing it take longer. The drop does not
        // wait for it (2026-11-10, [`lift`]): a longer window costs nothing
        // but a later down on the other panel's edge being taken as the
        // pick-up.
        Config { edge_band: 56.0, window_ms: 350, max_offset: 120.0 }
    }
}

/// Where and when a dragging finger lifted (or is, for a down while it is
/// still on the glass).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Contact {
    /// The panel's index (in the order the hinge was found from).
    pub panel: usize,
    /// Where (global).
    pub at: (f64, f64),
    /// When (the input events' millisecond clock).
    pub time: u32,
}

/// What the lift of a carrying finger does ([`lift`]).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Lift {
    /// Put down where its target says, and that is the end of the drag.
    Down,
    /// Put down where its target says at once (it flies there from the
    /// lift on), and for [`Config::window_ms`] a finger coming down at the
    /// other panel's hinge edge picks it up again: [`continues`] with this
    /// contact (`lifted`). The lift was at the hinge.
    DownOpen(Contact),
}

/// The lift of the finger carrying something: on panel `index` of `panels`
/// (by index, as [`continues`] takes them; None: not one of the hinge's
/// two), at `at` (global), at `time` (the input events' clock); `hinge`
/// None: one panel in use. `on_pad`: let go on the span pad (both panels:
/// nothing to go on with across the hinge).
///
/// A lift is always a drop now; nothing waits for the other side.
pub fn lift(
    hinge: Option<&Hinge>,
    panels: &[Rect],
    index: Option<usize>,
    at: (f64, f64),
    time: u32,
    on_pad: bool,
    cfg: &Config,
) -> Lift {
    let (Some(hinge), Some(index), false) = (hinge, index, on_pad) else {
        return Lift::Down;
    };
    match panels.get(index) {
        Some(panel) if at_hinge_edge(hinge, index, *panel, at, cfg) => {
            Lift::DownOpen(Contact { panel: index, at, time })
        }
        _ => Lift::Down,
    }
}

/// How far a point is from panel `index`'s hinge-side edge (into the panel),
/// or None if the panel is not one of the hinge's two.
pub fn from_hinge_edge(hinge: &Hinge, index: usize, panel: Rect, at: (f64, f64)) -> Option<f64> {
    let side = hinge.facing_side(index)?;
    let (x, y) = at;
    Some(match side {
        crate::split::Side::Right => panel.x + panel.w - x,
        crate::split::Side::Left => x - panel.x,
        crate::split::Side::Bottom => panel.y + panel.h - y,
        crate::split::Side::Top => y - panel.y,
    })
}

/// Whether a contact at `at` on panel `index` is at its hinge-side edge.
pub fn at_hinge_edge(hinge: &Hinge, index: usize, panel: Rect, at: (f64, f64), cfg: &Config) -> bool {
    from_hinge_edge(hinge, index, panel, at).map(|d| d <= cfg.edge_band).unwrap_or(false)
}

/// How far apart two points are along the hinge.
fn along(hinge: &Hinge, a: (f64, f64), b: (f64, f64)) -> f64 {
    match hinge {
        Hinge::Vertical { .. } => (a.1 - b.1).abs(),
        Hinge::Horizontal { .. } => (a.0 - b.0).abs(),
    }
}

/// Whether a finger down at `down` continues the drag whose finger lifted
/// at `lift` (`lifted` true) or is at `lift` now (`lifted` false: both on
/// the glass). `panels` are the two panels' rectangles by index.
pub fn continues(
    hinge: &Hinge,
    panels: &[Rect],
    lift: &Contact,
    lifted: bool,
    down: &Contact,
    cfg: &Config,
) -> bool {
    if down.panel == lift.panel {
        return false;
    }
    let (Some(a), Some(b)) = (panels.get(lift.panel), panels.get(down.panel)) else {
        return false;
    };
    if !at_hinge_edge(hinge, lift.panel, *a, lift.at, cfg)
        || !at_hinge_edge(hinge, down.panel, *b, down.at, cfg)
    {
        return false;
    }
    if lifted {
        // The event clock wraps after ~49 days; a down "before" a lift is not
        // this case.
        let dt = down.time.wrapping_sub(lift.time);
        if dt > cfg.window_ms {
            return false;
        }
    }
    along(hinge, lift.at, down.at) <= cfg.max_offset
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snap::hinge;

    fn panels() -> Vec<Rect> {
        vec![Rect::new(0.0, 0.0, 538.0, 757.0), Rect::new(564.0, 0.0, 538.0, 757.0)]
    }

    fn c(panel: usize, x: f64, y: f64, time: u32) -> Contact {
        Contact { panel, at: (x, y), time }
    }

    #[test]
    fn a_lift_at_the_hinge_and_a_down_across_it_are_one_drag() {
        let p = panels();
        let h = hinge(&p).unwrap();
        let cfg = Config::default();
        let lift = c(0, 530.0, 300.0, 1000);
        assert!(continues(&h, &p, &lift, true, &c(1, 570.0, 320.0, 1080), &cfg));
        // Right to left too.
        let back = c(1, 566.0, 300.0, 5000);
        assert!(continues(&h, &p, &back, true, &c(0, 520.0, 260.0, 5100), &cfg));
    }

    #[test]
    fn too_late_too_far_or_not_at_the_edge_is_not() {
        let p = panels();
        let h = hinge(&p).unwrap();
        let cfg = Config::default();
        let lift = c(0, 530.0, 300.0, 1000);
        // Too late.
        assert!(!continues(&h, &p, &lift, true, &c(1, 570.0, 300.0, 1000 + cfg.window_ms + 1), &cfg));
        // Too far along the hinge.
        assert!(!continues(&h, &p, &lift, true, &c(1, 570.0, 300.0 + cfg.max_offset + 1.0, 1050), &cfg));
        // The down is in the middle of the other panel.
        assert!(!continues(&h, &p, &lift, true, &c(1, 800.0, 300.0, 1050), &cfg));
        // The lift was in the middle of its panel.
        assert!(!continues(&h, &p, &c(0, 300.0, 300.0, 1000), true, &c(1, 570.0, 300.0, 1050), &cfg));
        // Same panel.
        assert!(!continues(&h, &p, &lift, true, &c(0, 530.0, 300.0, 1050), &cfg));
        // The outer edge is not the hinge's.
        assert!(!continues(&h, &p, &c(0, 5.0, 300.0, 1000), true, &c(1, 570.0, 300.0, 1050), &cfg));
    }

    #[test]
    fn a_finger_on_both_edges_at_once_hands_over() {
        let p = panels();
        let h = hinge(&p).unwrap();
        let cfg = Config::default();
        // Still down (no time window), next to it across the hinge.
        assert!(continues(&h, &p, &c(0, 535.0, 400.0, 1000), false, &c(1, 568.0, 410.0, 9000), &cfg));
        assert!(!continues(&h, &p, &c(0, 400.0, 400.0, 1000), false, &c(1, 568.0, 410.0, 1010), &cfg));
    }

    #[test]
    fn stacked_panels_stitch_along_x() {
        let p = vec![Rect::new(0.0, 0.0, 757.0, 538.0), Rect::new(0.0, 564.0, 757.0, 538.0)];
        let h = hinge(&p).unwrap();
        let cfg = Config::default();
        assert!(continues(&h, &p, &c(0, 300.0, 530.0, 0), true, &c(1, 340.0, 570.0, 90), &cfg));
        assert!(!continues(&h, &p, &c(0, 300.0, 530.0, 0), true, &c(1, 500.0, 570.0, 90), &cfg));
        assert_eq!(from_hinge_edge(&h, 1, p[1], (10.0, 600.0)), Some(36.0));
    }

    #[test]
    fn stacked_panels_stitch_both_ways_at_90_and_270() {
        // 90: index 0 (DSI-1) on top; 270: index 1 (DSI-2) on top.
        let cfg = Config::default();
        let upper = Rect::new(0.0, 0.0, 757.0, 538.0);
        let lower = Rect::new(0.0, 564.0, 757.0, 538.0);
        for (p, top) in [(vec![upper, lower], 0usize), (vec![lower, upper], 1usize)] {
            let bottom = 1 - top;
            let h = hinge(&p).unwrap();
            // Up: a lift at the lower panel's top edge, a down on the upper
            // panel's bottom edge.
            let l = lift(Some(&h), &p, Some(bottom), (300.0, 580.0), 1000, false, &cfg);
            assert_eq!(l, Lift::DownOpen(c(bottom, 300.0, 580.0, 1000)), "top {top}");
            assert!(continues(
                &h,
                &p,
                &c(bottom, 300.0, 580.0, 1000),
                true,
                &c(top, 330.0, 520.0, 1100),
                &cfg
            ));
            // Down: the other way round.
            let l = lift(Some(&h), &p, Some(top), (300.0, 525.0), 2000, false, &cfg);
            assert_eq!(l, Lift::DownOpen(c(top, 300.0, 525.0, 2000)), "top {top}");
            assert!(continues(
                &h,
                &p,
                &c(top, 300.0, 525.0, 2000),
                true,
                &c(bottom, 280.0, 575.0, 2090),
                &cfg
            ));
            // The far edges are not the hinge's.
            assert_eq!(lift(Some(&h), &p, Some(top), (300.0, 10.0), 0, false, &cfg), Lift::Down);
            assert_eq!(lift(Some(&h), &p, Some(bottom), (300.0, 1095.0), 0, false, &cfg), Lift::Down);
            assert_eq!(from_hinge_edge(&h, bottom, p[bottom], (10.0, 600.0)), Some(36.0));
            assert_eq!(from_hinge_edge(&h, top, p[top], (10.0, 500.0)), Some(38.0));
        }
    }

    #[test]
    fn a_lift_puts_down_at_once_and_at_the_hinge_stays_open_for_the_other_side() {
        let p = panels();
        let h = hinge(&p).unwrap();
        let cfg = Config::default();
        // At the hinge edge: down now, open for the other side...
        let l = lift(Some(&h), &p, Some(0), (530.0, 300.0), 1000, false, &cfg);
        assert_eq!(l, Lift::DownOpen(c(0, 530.0, 300.0, 1000)));
        let Lift::DownOpen(contact) = l else { unreachable!() };
        // ... for the window: a finger across the hinge in it picks it up
        // again, one later is a touch of its own.
        assert!(continues(&h, &p, &contact, true, &c(1, 570.0, 310.0, 1000 + cfg.window_ms), &cfg));
        assert!(!continues(&h, &p, &contact, true, &c(1, 570.0, 310.0, 1001 + cfg.window_ms), &cfg));
        // From the right panel too (its hinge edge is its left one).
        assert_eq!(
            lift(Some(&h), &p, Some(1), (600.0, 300.0), 7, false, &cfg),
            Lift::DownOpen(c(1, 600.0, 300.0, 7))
        );
        // In the middle, at the outer edge, on the span pad, with one panel
        // in use or off the hinge's two: down, and that is all.
        assert_eq!(lift(Some(&h), &p, Some(0), (300.0, 300.0), 1000, false, &cfg), Lift::Down);
        assert_eq!(lift(Some(&h), &p, Some(0), (5.0, 300.0), 1000, false, &cfg), Lift::Down);
        assert_eq!(lift(Some(&h), &p, Some(0), (530.0, 500.0), 1000, true, &cfg), Lift::Down);
        assert_eq!(lift(None, &p[..1], Some(0), (530.0, 300.0), 1000, false, &cfg), Lift::Down);
        assert_eq!(lift(Some(&h), &p, None, (530.0, 300.0), 1000, false, &cfg), Lift::Down);
        // The band is the config's.
        let wide = Config { edge_band: 120.0, ..cfg };
        assert_eq!(lift(Some(&h), &p, Some(0), (440.0, 300.0), 0, false, &cfg), Lift::Down);
        assert!(matches!(lift(Some(&h), &p, Some(0), (440.0, 300.0), 0, false, &wide), Lift::DownOpen(_)));
    }

    #[test]
    fn the_thresholds_are_the_configs() {
        let p = panels();
        let h = hinge(&p).unwrap();
        let loose = Config { edge_band: 120.0, window_ms: 400, max_offset: 200.0 };
        let lift = c(0, 440.0, 300.0, 0);
        let down = c(1, 650.0, 480.0, 350);
        assert!(continues(&h, &p, &lift, true, &down, &loose));
        assert!(!continues(&h, &p, &lift, true, &down, &Config::default()));
    }
}
