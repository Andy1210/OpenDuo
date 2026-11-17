//! Where a carried window goes: the snap targets of the one canvas.
//!
//! The two panels are one workspace with the hinge as a natural snap line.
//! A window is on ONE panel (the default), on HALF of a panel (a split,
//! [`crate::split`]) or on BOTH panels (span). A window carried by its top
//! (or a card carried in the overview) is let go over one of these targets:
//!
//! - **at the hinge** (in the gap, or within [`Config::hinge_band`] of the
//!   hinge-side edge of the panel the finger is on): **the other panel**.
//!   The two touchscreens are separate devices, so a finger carrying a
//!   window across lifts there; that is a move to the other panel, never a
//!   span (it was, and every carry to the other panel ended up spanning).
//!   A finger that just came over the hinge ([`Context::arrived`]) is on
//!   its new panel until it has left the band once;
//! - **on the span pad** ([`span_pad`]: a stretch of the hinge, both bands
//!   and the gap, from [`Config::pad_from`] to [`Config::pad_to`] of the
//!   panels' length along it, drawn while something is carried): both
//!   panels (span). The only way to span by carrying: explicit, visible,
//!   and away from the straight path across;
//! - **near a panel's outer edges** (within [`Config::half_share`] of its
//!   width or height): that half of the panel, when there is another window
//!   there to share it with; the band on the hinge side that is not the
//!   hinge's own is the panel's inner half;
//! - **anywhere else on a panel**: that whole panel.
//!
//! The top half has no band of its own for a carried window (it is carried
//! by its top, so it starts there): putting the other window at the bottom
//! does the same. A card picked up in the overview may go to the top
//! ([`Context::allow_top`]).
//!
//! Positions are logical px in the global space (both panels), as the panels
//! read (a turned panel's edges are its edges as seen). Pure: no Smithay
//! types.

use crate::overview::Rect;
use crate::split::Side;

/// The thresholds (duo-wc.conf `snap_hinge_band`, `snap_half_share`,
/// `snap_pad_from`, `snap_pad_to`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Config {
    /// How far from the hinge-side edge a finger still means "at the hinge"
    /// (logical px): the other panel; also the span pad's reach into each
    /// panel.
    pub hinge_band: f64,
    /// How close to a panel's edge (as a share of its width or height) a
    /// finger means "that half".
    pub half_share: f64,
    /// Where the span pad starts and ends along the hinge, as a share of
    /// the panels' length along it (0 = the top of side-by-side panels).
    pub pad_from: f64,
    /// See [`Config::pad_from`].
    pub pad_to: f64,
}

impl Default for Config {
    fn default() -> Self {
        Config { hinge_band: 40.0, half_share: 0.22, pad_from: 0.58, pad_to: 0.80 }
    }
}

/// What the snapping knows of a carry besides where the finger is.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Context {
    /// A card picked up in the overview: the top half is a target too.
    pub allow_top: bool,
    /// The panel (by index) the finger has just come to across the hinge:
    /// its hinge band means that panel, not the other one, until the finger
    /// has left the band once (a finger that comes down at the edge and
    /// lifts again must not be sent back).
    pub arrived: Option<usize>,
}

/// A panel as the snapping sees it.
#[derive(Debug, Clone, PartialEq)]
pub struct Panel {
    /// The connector name.
    pub name: String,
    /// Where it is (global).
    pub rect: Rect,
    /// The room a window gets there (global): less the status bar and the
    /// keyboard.
    pub zone: Rect,
    /// Another window is there to share the panel with (halves are offered).
    pub can_split: bool,
}

/// Where a carried window would go.
#[derive(Debug, Clone, PartialEq)]
pub enum Target {
    /// The whole panel with this name.
    Panel(String),
    /// A half of it.
    Half(String, Side),
    /// Both panels.
    Span,
}

impl Target {
    /// In words, for the log and the control socket.
    pub fn describe(&self) -> String {
        match self {
            Target::Panel(name) => name.clone(),
            Target::Half(name, side) => format!("{name} {}", side.name()),
            Target::Span => "span".to_owned(),
        }
    }
}

/// How the two panels meet.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Hinge {
    /// Side by side: the hinge is the vertical strip from x `from` (the left
    /// panel's right edge) to x `to` (the right panel's left edge).
    Vertical {
        /// The left panel's index.
        first: usize,
        /// The right panel's index.
        second: usize,
        /// Strip start.
        from: f64,
        /// Strip end.
        to: f64,
    },
    /// One above the other: the hinge is the strip from y `from` to y `to`.
    Horizontal {
        /// The upper panel's index.
        first: usize,
        /// The lower panel's index.
        second: usize,
        /// Strip start.
        from: f64,
        /// Strip end.
        to: f64,
    },
}

/// The hinge between the first two panels, if there are two.
pub fn hinge(panels: &[Rect]) -> Option<Hinge> {
    if panels.len() < 2 {
        return None;
    }
    let (a, b) = (panels[0], panels[1]);
    let overlap_y = a.y < b.y + b.h && b.y < a.y + a.h;
    let overlap_x = a.x < b.x + b.w && b.x < a.x + a.w;
    if overlap_y && !overlap_x {
        let (first, second) = if a.x <= b.x { (0, 1) } else { (1, 0) };
        let (l, r) = (panels[first], panels[second]);
        Some(Hinge::Vertical { first, second, from: l.x + l.w, to: r.x })
    } else if overlap_x && !overlap_y {
        let (first, second) = if a.y <= b.y { (0, 1) } else { (1, 0) };
        let (t, u) = (panels[first], panels[second]);
        Some(Hinge::Horizontal { first, second, from: t.y + t.h, to: u.y })
    } else {
        None
    }
}

impl Hinge {
    /// How far a point is from the hinge strip (0 inside it), measured across
    /// it.
    pub fn distance(&self, x: f64, y: f64) -> f64 {
        let (v, from, to) = match *self {
            Hinge::Vertical { from, to, .. } => (x, from, to),
            Hinge::Horizontal { from, to, .. } => (y, from, to),
        };
        if v < from {
            from - v
        } else if v > to {
            v - to
        } else {
            0.0
        }
    }

    /// The panel across the hinge from panel `index`, if it is one of the two.
    pub fn other(&self, index: usize) -> Option<usize> {
        let (first, second) = match *self {
            Hinge::Vertical { first, second, .. } | Hinge::Horizontal { first, second, .. } => {
                (first, second)
            }
        };
        if index == first {
            Some(second)
        } else if index == second {
            Some(first)
        } else {
            None
        }
    }

    /// The side of panel `index` that faces the hinge, if it is one of the two.
    pub fn facing_side(&self, index: usize) -> Option<Side> {
        match *self {
            Hinge::Vertical { first, second, .. } => {
                if index == first {
                    Some(Side::Right)
                } else if index == second {
                    Some(Side::Left)
                } else {
                    None
                }
            }
            Hinge::Horizontal { first, second, .. } => {
                if index == first {
                    Some(Side::Bottom)
                } else if index == second {
                    Some(Side::Top)
                } else {
                    None
                }
            }
        }
    }
}

/// The panel a point is on, else the nearest one.
pub fn panel_at(panels: &[Panel], x: f64, y: f64) -> Option<usize> {
    if let Some(i) = panels.iter().position(|p| p.rect.contains(x, y)) {
        return Some(i);
    }
    let distance = |r: &Rect| {
        let dx = (r.x - x).max(0.0).max(x - (r.x + r.w));
        let dy = (r.y - y).max(0.0).max(y - (r.y + r.h));
        dx.hypot(dy)
    };
    panels
        .iter()
        .enumerate()
        .min_by(|a, b| distance(&a.1.rect).total_cmp(&distance(&b.1.rect)))
        .map(|(i, _)| i)
}

/// The span pad (global): the stretch of the hinge, the gap and
/// [`Config::hinge_band`] into each panel, from [`Config::pad_from`] to
/// [`Config::pad_to`] of the panels' common length along it. None with
/// fewer than two panels.
pub fn span_pad(panels: &[Rect], cfg: &Config) -> Option<Rect> {
    let h = hinge(panels)?;
    let (a, b) = (panels[0], panels[1]);
    Some(match h {
        Hinge::Vertical { from, to, .. } => {
            let (y0, y1) = (a.y.max(b.y), (a.y + a.h).min(b.y + b.h));
            let (p0, p1) = (y0 + (y1 - y0) * cfg.pad_from, y0 + (y1 - y0) * cfg.pad_to);
            Rect::new(from - cfg.hinge_band, p0, to - from + 2.0 * cfg.hinge_band, p1 - p0)
        }
        Hinge::Horizontal { from, to, .. } => {
            let (x0, x1) = (a.x.max(b.x), (a.x + a.w).min(b.x + b.w));
            let (p0, p1) = (x0 + (x1 - x0) * cfg.pad_from, x0 + (x1 - x0) * cfg.pad_to);
            Rect::new(p0, from - cfg.hinge_band, p1 - p0, to - from + 2.0 * cfg.hinge_band)
        }
    })
}

/// Whether `(x, y)` is in the hinge band (the gap, or within
/// [`Config::hinge_band`] of a hinge-side edge).
pub fn in_hinge_band(panels: &[Rect], x: f64, y: f64, cfg: &Config) -> bool {
    hinge(panels).map(|h| h.distance(x, y) <= cfg.hinge_band).unwrap_or(false)
}

/// Where a window carried to `(x, y)` would go.
pub fn target(panels: &[Panel], x: f64, y: f64, ctx: Context, cfg: &Config) -> Option<Target> {
    let rects: Vec<Rect> = panels.iter().map(|p| p.rect).collect();
    let index = panel_at(panels, x, y)?;
    if let Some(h) = hinge(&rects) {
        if span_pad(&rects, cfg).map(|pad| pad.contains(x, y)).unwrap_or(false) {
            return Some(Target::Span);
        }
        if h.distance(x, y) <= cfg.hinge_band {
            if ctx.arrived == Some(index) {
                return Some(Target::Panel(panels[index].name.clone()));
            }
            return h.other(index).map(|o| Target::Panel(panels[o].name.clone()));
        }
    }
    let panel = &panels[index];
    if !panel.can_split {
        return Some(Target::Panel(panel.name.clone()));
    }
    let r = panel.rect;
    let (u, v) = ((x - r.x) / r.w.max(1.0), (y - r.y) / r.h.max(1.0));
    let mut sides = vec![(u, Side::Left), (1.0 - u, Side::Right), (1.0 - v, Side::Bottom)];
    if ctx.allow_top {
        sides.push((v, Side::Top));
    }
    let nearest = sides.into_iter().filter(|(d, _)| *d < cfg.half_share).min_by(|a, b| a.0.total_cmp(&b.0));
    match nearest {
        Some((_, side)) => Some(Target::Half(panel.name.clone(), side)),
        None => Some(Target::Panel(panel.name.clone())),
    }
}

/// Where the carried picture `ghost` (global) is shown while its target is
/// the OTHER panel than the one the finger is on (`from`, by index): moved
/// across the hinge, wholly inside the other panel next to its hinge-side
/// edge (`margin` px in), at the same height (or the same x for stacked
/// panels): the window is seen going over before the finger is let go.
/// The ghost unchanged when it is not a case of that.
pub fn lead_across(panels: &[Rect], from: usize, ghost: Rect, margin: f64) -> Rect {
    let Some(h) = hinge(panels) else {
        return ghost;
    };
    let Some(to) = h.other(from) else {
        return ghost;
    };
    let p = panels[to];
    let clamp = |v: f64, lo: f64, hi: f64| if hi < lo { lo } else { v.clamp(lo, hi) };
    match h {
        Hinge::Vertical { first, .. } => {
            let x = if to == first { p.x + p.w - margin - ghost.w } else { p.x + margin };
            Rect::new(x, clamp(ghost.y, p.y, p.y + p.h - ghost.h), ghost.w, ghost.h)
        }
        Hinge::Horizontal { first, .. } => {
            let y = if to == first { p.y + p.h - margin - ghost.h } else { p.y + margin };
            Rect::new(clamp(ghost.x, p.x, p.x + p.w - ghost.w), y, ghost.w, ghost.h)
        }
    }
}

/// The rectangle a target covers (global): the panel's room, its half
/// (with the divider's gap), or `span` (both panels' room, as the window
/// would get it).
pub fn preview(panels: &[Panel], target: &Target, span: Option<Rect>) -> Option<Rect> {
    let find = |name: &str| panels.iter().find(|p| p.name == name);
    match target {
        Target::Panel(name) => find(name).map(|p| p.zone),
        Target::Half(name, side) => find(name).map(|p| {
            let (x, y, w, h) = crate::split::half_of((p.zone.x, p.zone.y, p.zone.w, p.zone.h), *side);
            Rect::new(x, y, w, h)
        }),
        Target::Span => span,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn side_by_side(split: bool) -> Vec<Panel> {
        vec![
            Panel {
                name: "DSI-1".into(),
                rect: Rect::new(0.0, 0.0, 538.0, 757.0),
                zone: Rect::new(0.0, 48.0, 538.0, 709.0),
                can_split: split,
            },
            Panel {
                name: "DSI-2".into(),
                rect: Rect::new(564.0, 0.0, 538.0, 757.0),
                zone: Rect::new(564.0, 48.0, 538.0, 709.0),
                can_split: split,
            },
        ]
    }

    fn stacked() -> Vec<Panel> {
        vec![
            Panel {
                name: "DSI-1".into(),
                rect: Rect::new(0.0, 0.0, 757.0, 538.0),
                zone: Rect::new(0.0, 48.0, 757.0, 490.0),
                can_split: true,
            },
            Panel {
                name: "DSI-2".into(),
                rect: Rect::new(0.0, 564.0, 757.0, 538.0),
                zone: Rect::new(0.0, 612.0, 757.0, 490.0),
                can_split: true,
            },
        ]
    }

    #[test]
    fn the_hinge_is_found_either_way_round() {
        let p = side_by_side(false);
        let rects: Vec<Rect> = p.iter().map(|p| p.rect).collect();
        assert_eq!(hinge(&rects), Some(Hinge::Vertical { first: 0, second: 1, from: 538.0, to: 564.0 }));
        let reversed = vec![rects[1], rects[0]];
        assert_eq!(hinge(&reversed), Some(Hinge::Vertical { first: 1, second: 0, from: 538.0, to: 564.0 }));
        let s: Vec<Rect> = stacked().iter().map(|p| p.rect).collect();
        assert_eq!(hinge(&s), Some(Hinge::Horizontal { first: 0, second: 1, from: 538.0, to: 564.0 }));
        assert_eq!(hinge(&rects[..1]), None);
        // Gap off: the panels touch; the strip has no width but is there.
        let touching = vec![Rect::new(0.0, 0.0, 538.0, 757.0), Rect::new(538.0, 0.0, 538.0, 757.0)];
        assert_eq!(hinge(&touching), Some(Hinge::Vertical { first: 0, second: 1, from: 538.0, to: 538.0 }));
    }

    const CARRIED: Context = Context { allow_top: false, arrived: None };
    const LIFTED: Context = Context { allow_top: true, arrived: None };

    #[test]
    fn at_the_hinge_is_the_other_panel_never_both() {
        // Before 2026-11-08 a window carried to the other panel always
        // ended up across both (a finger lifting at the hinge was "both").
        let p = side_by_side(true);
        let cfg = Config::default();
        for x in [500.0, 520.0, 537.0] {
            assert_eq!(target(&p, x, 300.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())), "x = {x}");
        }
        for x in [565.0, 590.0, 603.0] {
            assert_eq!(target(&p, x, 300.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())), "x = {x}");
        }
        // Higher up and lower down along the hinge alike (outside the pad).
        assert_eq!(target(&p, 530.0, 60.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 530.0, 740.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 570.0, 700.0, LIFTED, &cfg), Some(Target::Panel("DSI-1".into())));
        // Just past the band: the inner half (there is a partner) ...
        assert_eq!(target(&p, 490.0, 300.0, CARRIED, &cfg), Some(Target::Half("DSI-1".into(), Side::Right)));
        assert_eq!(target(&p, 620.0, 300.0, CARRIED, &cfg), Some(Target::Half("DSI-2".into(), Side::Left)));
        // ... or the panel (nobody to share with).
        let alone = side_by_side(false);
        assert_eq!(target(&alone, 490.0, 300.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
        assert_eq!(target(&alone, 520.0, 300.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
    }

    #[test]
    fn a_finger_that_came_across_stays_on_its_new_panel_at_the_edge() {
        let p = side_by_side(true);
        let cfg = Config::default();
        // Came down on DSI-2's hinge edge after crossing: DSI-2, not back.
        let arrived = Context { allow_top: false, arrived: Some(1) };
        assert_eq!(target(&p, 575.0, 300.0, arrived, &cfg), Some(Target::Panel("DSI-2".into())));
        // Further in, the usual targets.
        assert_eq!(target(&p, 620.0, 300.0, arrived, &cfg), Some(Target::Half("DSI-2".into(), Side::Left)));
        // The flag is about its own panel only.
        let other = Context { allow_top: false, arrived: Some(0) };
        assert_eq!(target(&p, 575.0, 300.0, other, &cfg), Some(Target::Panel("DSI-1".into())));
    }

    #[test]
    fn the_span_pad_is_the_only_way_to_both_panels() {
        let p = side_by_side(true);
        let cfg = Config::default();
        let rects: Vec<Rect> = p.iter().map(|p| p.rect).collect();
        let pad = span_pad(&rects, &cfg).unwrap();
        // Both bands and the gap, 58..80 % down the panels.
        assert_eq!((pad.x, pad.w), (498.0, 106.0));
        assert!((pad.y - 757.0 * 0.58).abs() < 1e-9 && (pad.y + pad.h - 757.0 * 0.80).abs() < 1e-9);
        for (x, y) in [(500.0, 500.0), (537.0, 450.0), (550.0, 520.0), (566.0, 600.0), (603.0, 500.0)] {
            assert_eq!(target(&p, x, y, CARRIED, &cfg), Some(Target::Span), "({x}, {y})");
            assert_eq!(target(&p, x, y, LIFTED, &cfg), Some(Target::Span), "({x}, {y})");
        }
        // Just above and below it: the other panel; beside it: a half.
        assert_eq!(target(&p, 520.0, 430.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 520.0, 610.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 480.0, 500.0, CARRIED, &cfg), Some(Target::Half("DSI-1".into(), Side::Right)));
        // Nothing anywhere else spans.
        for x in (0..1102).step_by(7) {
            for y in (0..757).step_by(11) {
                let (x, y) = (x as f64, y as f64);
                if !pad.contains(x, y) {
                    assert_ne!(target(&p, x, y, CARRIED, &cfg), Some(Target::Span), "({x}, {y})");
                }
            }
        }
        // One panel: no hinge, no pad.
        assert_eq!(span_pad(&rects[..1], &cfg), None);
    }

    #[test]
    fn the_middle_is_the_panel_and_the_edges_its_halves() {
        let p = side_by_side(true);
        let cfg = Config::default();
        assert_eq!(target(&p, 269.0, 380.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
        assert_eq!(target(&p, 40.0, 380.0, CARRIED, &cfg), Some(Target::Half("DSI-1".into(), Side::Left)));
        assert_eq!(target(&p, 1080.0, 380.0, CARRIED, &cfg), Some(Target::Half("DSI-2".into(), Side::Right)));
        assert_eq!(target(&p, 830.0, 740.0, CARRIED, &cfg), Some(Target::Half("DSI-2".into(), Side::Bottom)));
        // The top: the panel for a carried window, the top half for a lifted card.
        assert_eq!(target(&p, 269.0, 60.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
        assert_eq!(target(&p, 269.0, 60.0, LIFTED, &cfg), Some(Target::Half("DSI-1".into(), Side::Top)));
        // Off the glass (a finger past the outer edge): the nearest panel.
        assert_eq!(target(&p, 1200.0, 380.0, CARRIED, &cfg), Some(Target::Half("DSI-2".into(), Side::Right)));
    }

    #[test]
    fn a_lone_window_gets_no_half_and_the_middle_ends_a_split() {
        // Nothing to share the panel with: its edges are the whole panel.
        let p = side_by_side(false);
        let cfg = Config::default();
        assert_eq!(target(&p, 40.0, 380.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
        assert_eq!(target(&p, 830.0, 740.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        // A window of a split carried to its panel's middle: the whole panel
        // (apply_target places it there, which ends the split: back to full).
        let p = side_by_side(true);
        assert_eq!(target(&p, 269.0, 380.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
    }

    #[test]
    fn stacked_panels_meet_at_a_horizontal_hinge() {
        let p = stacked();
        let cfg = Config::default();
        // At the hinge, outside the pad (which is 58..80 % along x): across.
        assert_eq!(target(&p, 200.0, 520.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 200.0, 580.0, CARRIED, &cfg), Some(Target::Panel("DSI-1".into())));
        // On the pad: both.
        assert_eq!(target(&p, 500.0, 520.0, CARRIED, &cfg), Some(Target::Span));
        assert_eq!(target(&p, 500.0, 590.0, CARRIED, &cfg), Some(Target::Span));
        assert_eq!(target(&p, 380.0, 610.0, CARRIED, &cfg), Some(Target::Panel("DSI-2".into())));
        assert_eq!(target(&p, 380.0, 470.0, CARRIED, &cfg), Some(Target::Half("DSI-1".into(), Side::Bottom)));
        assert_eq!(target(&p, 30.0, 800.0, CARRIED, &cfg), Some(Target::Half("DSI-2".into(), Side::Left)));
    }

    #[test]
    fn stacked_either_way_round_at_90_and_270() {
        // 90: DSI-1 (index 0) above DSI-2; 270: DSI-2 (index 1) above DSI-1.
        // Across the hinge both ways, whichever index is on top.
        let cfg = Config::default();
        let upper = Rect::new(0.0, 0.0, 757.0, 538.0);
        let lower = Rect::new(0.0, 564.0, 757.0, 538.0);
        for (rects, top) in [(vec![upper, lower], 0usize), (vec![lower, upper], 1usize)] {
            let bottom = 1 - top;
            let p: Vec<Panel> = rects
                .iter()
                .enumerate()
                .map(|(i, r)| Panel {
                    name: format!("DSI-{}", i + 1),
                    rect: *r,
                    zone: Rect::new(r.x, r.y + 48.0, r.w, r.h - 48.0),
                    can_split: true,
                })
                .collect();
            let h = hinge(&rects).unwrap();
            assert_eq!(h, Hinge::Horizontal { first: top, second: bottom, from: 538.0, to: 564.0 });
            assert_eq!(h.facing_side(top), Some(Side::Bottom));
            assert_eq!(h.facing_side(bottom), Some(Side::Top));
            let (up, down) = (p[top].name.clone(), p[bottom].name.clone());
            // From the lower panel's top edge up, from the upper's bottom
            // edge down (outside the pad, at either end of the hinge).
            for x in [20.0, 200.0, 740.0] {
                assert_eq!(target(&p, x, 570.0, CARRIED, &cfg), Some(Target::Panel(up.clone())), "x = {x}");
                assert_eq!(target(&p, x, 600.0, CARRIED, &cfg), Some(Target::Panel(up.clone())), "x = {x}");
                assert_eq!(target(&p, x, 530.0, CARRIED, &cfg), Some(Target::Panel(down.clone())), "x = {x}");
                assert_eq!(target(&p, x, 500.0, LIFTED, &cfg), Some(Target::Panel(down.clone())), "x = {x}");
            }
            // In the gap: the other panel of the nearest one.
            assert_eq!(target(&p, 200.0, 552.0, CARRIED, &cfg), Some(Target::Panel(up.clone())));
            // Past the bands: halves on the far side of the hinge.
            assert_eq!(target(&p, 200.0, 470.0, CARRIED, &cfg), Some(Target::Half(up.clone(), Side::Bottom)));
            assert_eq!(
                target(&p, 200.0, 1080.0, CARRIED, &cfg),
                Some(Target::Half(down.clone(), Side::Bottom))
            );
            // The pad spans, from either side.
            assert_eq!(target(&p, 500.0, 520.0, CARRIED, &cfg), Some(Target::Span));
            assert_eq!(target(&p, 500.0, 590.0, CARRIED, &cfg), Some(Target::Span));
            // The picture is led into the other panel, whole.
            let ghost = Rect::new(100.0, 440.0, 300.0, 200.0);
            let led = lead_across(&rects, top, ghost, 16.0);
            assert_eq!(led, Rect::new(100.0, 580.0, 300.0, 200.0));
            let led = lead_across(&rects, bottom, ghost, 16.0);
            assert_eq!(led, Rect::new(100.0, 538.0 - 16.0 - 200.0, 300.0, 200.0));
        }
    }

    #[test]
    fn one_panel_has_no_hinge() {
        let p = side_by_side(true)[..1].to_vec();
        let cfg = Config::default();
        assert_eq!(target(&p, 530.0, 300.0, CARRIED, &cfg), Some(Target::Half("DSI-1".into(), Side::Right)));
        assert_eq!(target(&[], 5.0, 5.0, CARRIED, &cfg), None);
    }

    #[test]
    fn a_window_going_across_is_shown_on_the_other_panel() {
        let p = side_by_side(true);
        let rects: Vec<Rect> = p.iter().map(|p| p.rect).collect();
        // A 55 % ghost under a finger at DSI-1's hinge edge.
        let ghost = Rect::new(400.0, 100.0, 296.0, 390.0);
        let led = lead_across(&rects, 0, ghost, 16.0);
        assert_eq!(led, Rect::new(580.0, 100.0, 296.0, 390.0));
        assert!(led.x >= rects[1].x && led.x + led.w <= rects[1].x + rects[1].w);
        // From DSI-2 to DSI-1: up against DSI-1's right edge.
        let led = lead_across(&rects, 1, Rect::new(560.0, 600.0, 296.0, 390.0), 16.0);
        assert_eq!(led, Rect::new(538.0 - 16.0 - 296.0, 757.0 - 390.0, 296.0, 390.0));
        // Stacked: below the hinge.
        let s: Vec<Rect> = stacked().iter().map(|p| p.rect).collect();
        let led = lead_across(&s, 0, Rect::new(100.0, 400.0, 300.0, 200.0), 16.0);
        assert_eq!(led, Rect::new(100.0, 580.0, 300.0, 200.0));
        // One panel: nothing to lead to.
        assert_eq!(lead_across(&rects[..1], 0, ghost, 16.0), ghost);
    }

    #[test]
    fn the_hinge_names_the_panel_across() {
        let rects: Vec<Rect> = side_by_side(false).iter().map(|p| p.rect).collect();
        let h = hinge(&rects).unwrap();
        assert_eq!((h.other(0), h.other(1), h.other(2)), (Some(1), Some(0), None));
        assert!(in_hinge_band(&rects, 520.0, 10.0, &Config::default()));
        assert!(!in_hinge_band(&rects, 490.0, 10.0, &Config::default()));
    }

    #[test]
    fn previews_cover_what_the_window_would_get() {
        let p = side_by_side(true);
        let span = Rect::new(0.0, 48.0, 1102.0, 709.0);
        assert_eq!(preview(&p, &Target::Panel("DSI-2".into()), Some(span)), Some(p[1].zone));
        assert_eq!(preview(&p, &Target::Span, Some(span)), Some(span));
        let half = preview(&p, &Target::Half("DSI-1".into(), Side::Left), None).unwrap();
        assert_eq!((half.x, half.y, half.h), (0.0, 48.0, 709.0));
        assert!(half.w < 538.0 / 2.0);
        assert_eq!(preview(&p, &Target::Panel("DSI-9".into()), None), None);
        assert_eq!(Target::Half("DSI-1".into(), Side::Left).describe(), "DSI-1 left");
    }
}
