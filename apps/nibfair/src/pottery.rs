//! The potter's wheel: a spinning lump of clay seen from the side (a lathe
//! profile), shaped with the pen.
//!
//! The profile is the radius at each of ROWS heights (row 0 at the foot),
//! in units of the starting lump's radius. The tip is the tool: where it
//! touches inside the clay it takes clay off, as much per second as it is
//! pressed (a light touch shaves and polishes, a hard one gouges and leaves
//! the surface rough); its tilt is the tool's angle: upright it is a round
//! point that cuts a groove, tilted it is a straight rib that leaves a
//! straight wall at the pen's lean. The hovering pen near the surface is a
//! wet sponge: it smooths bumps and roughness away. The eraser end adds clay
//! back where it touches outside the surface. An order is a target profile;
//! the score is how close the pot came to it, and how smooth it is.

use crate::tween::{lerp, smoothstep};

pub const ROWS: usize = 96;
/// The pot's height in radius units.
pub const HEIGHT: f32 = 2.4;
pub const ROW_H: f32 = HEIGHT / ROWS as f32;
/// The thinnest a wall may get (the clay around the axis stays).
pub const MIN_R: f32 = 0.1;
pub const MAX_R: f32 = 1.12;
/// The round point's radius (an upright tool).
pub const TIP: f32 = 0.07;
/// Radius per second at full pressure.
pub const CUT_RATE: f32 = 1.6;
pub const ADD_RATE: f32 = 0.9;

#[derive(Clone, Copy, Debug, Default)]
pub struct Tool {
    /// The height, in rows (fractional; 0 = the foot).
    pub row: f32,
    /// Distance from the axis, radius units.
    pub dist: f32,
    /// +1 on the right of the axis, -1 on the left.
    pub side: f32,
    pub pressure: f32,
    /// The pen's tilt, degrees (+ = its top leans to the right on screen).
    pub tilt: f32,
    pub eraser: bool,
    /// How fast the tool moves, radius units per second.
    pub speed: f32,
}

#[derive(Clone, Debug)]
pub struct Wheel {
    pub r: Vec<f32>,
    pub rough: Vec<f32>,
}

impl Default for Wheel {
    fn default() -> Self {
        Wheel::new()
    }
}

/// How much of the tilt makes a rib rather than a point (0..1). Pure.
pub fn rib(tilt: f32) -> f32 {
    smoothstep(4.0, 22.0, tilt.abs())
}

/// The tool's half length along the profile, radius units. Pure.
pub fn reach(tilt: f32) -> f32 {
    let t = tilt.abs().min(60.0).to_radians();
    lerp(TIP, 0.08 + 0.35 * t.sin(), rib(tilt))
}

/// Where the tool's edge is at `h` (radius units above the touch) and how
/// much it acts there (0..1). Pure.
pub fn tool_edge(t: &Tool, h: f32) -> (f32, f32) {
    let k = rib(t.tilt);
    let len = reach(t.tilt);
    let slope = t.tilt.clamp(-60.0, 60.0).to_radians().tan();
    let edge = t.dist + t.side * h * slope;
    if h.abs() <= TIP {
        let round = t.dist + (TIP - (TIP * TIP - h * h).max(0.0).sqrt());
        (lerp(round, edge, k), 1.0)
    } else if h.abs() <= len {
        (edge, k * (1.0 - smoothstep(len * 0.8, len, h.abs())))
    } else {
        (edge, 0.0)
    }
}

impl Wheel {
    /// The starting lump: a cylinder, a little lumpy.
    pub fn new() -> Wheel {
        let rough = (0..ROWS)
            .map(|i| 0.18 + 0.06 * ((i as f32) * 0.7).sin())
            .collect();
        Wheel {
            r: vec![1.0; ROWS],
            rough,
        }
    }

    fn rows_around(&self, row: f32, half: f32) -> std::ops::Range<usize> {
        let lo = (row - half).floor().max(0.0) as usize;
        let hi = ((row + half).ceil() as usize + 1).min(ROWS);
        lo.min(ROWS)..hi
    }

    /// The tool on the clay for `dt` s. -> how much clay it moved (radius x rows).
    pub fn carve(&mut self, t: &Tool, dt: f32) -> f32 {
        if t.eraser {
            return self.add(t, dt);
        }
        let len = reach(t.tilt) / ROW_H;
        let p = t.pressure.clamp(0.0, 1.0);
        let rate = CUT_RATE * (0.15 + 0.85 * p) * dt;
        let rougher = 1.5 * p + 0.6 * (t.speed / 1.5).min(1.0);
        let mut moved = 0.0;
        for j in self.rows_around(t.row, len + 1.0) {
            let h = (j as f32 - t.row) * ROW_H;
            let (edge, w) = tool_edge(t, h);
            if w <= 0.0 {
                continue;
            }
            // The foot and the rim keep a little more clay.
            let floor = if j < 3 || j + 2 >= ROWS {
                MIN_R + 0.05
            } else {
                MIN_R
            };
            let excess = self.r[j] - edge.max(floor);
            if excess > 0.0 {
                let amt = excess.min(w * rate);
                self.r[j] -= amt;
                self.rough[j] = (self.rough[j] + amt * rougher * 3.0).min(1.0);
                moved += amt;
            }
            // A light touch polishes what it passes over.
            if p < 0.25 && (self.r[j] - edge).abs() < 0.04 {
                self.rough[j] *= 1.0 - (1.6 * dt * w).min(1.0);
            }
        }
        moved
    }

    /// The eraser end: clay back where it touches outside the surface.
    fn add(&mut self, t: &Tool, dt: f32) -> f32 {
        let half = 0.12 / ROW_H;
        let rate = ADD_RATE * (0.3 + 0.7 * t.pressure.clamp(0.0, 1.0)) * dt;
        let mut moved = 0.0;
        for j in self.rows_around(t.row, half + 1.0) {
            let d = (j as f32 - t.row).abs() / half;
            if d > 1.0 {
                continue;
            }
            let w = 1.0 - d * d;
            let want = t.dist.min(MAX_R);
            if self.r[j] < want {
                let amt = (want - self.r[j]).min(w * rate);
                self.r[j] += amt;
                self.rough[j] = (self.rough[j] + amt * 0.8).min(1.0);
                moved += amt;
            }
        }
        moved
    }

    /// The hovering pen as a wet sponge, at `row` and `dist` from the axis:
    /// it acts only near the surface. -> how strongly it touched (0..1).
    pub fn sponge(&mut self, row: f32, dist: f32, dt: f32) -> f32 {
        let i = (row.round().max(0.0) as usize).min(ROWS - 1);
        let gap = (dist - self.r[i]).abs();
        let near = 1.0 - smoothstep(0.08, 0.25, gap);
        if near <= 0.0 {
            return 0.0;
        }
        let half = 5.0;
        let before = self.r.clone();
        for j in self.rows_around(row, half + 1.0) {
            let d = (j as f32 - row).abs() / half;
            if d > 1.0 {
                continue;
            }
            let w = near * (1.0 - d * d);
            self.rough[j] *= (-3.0 * dt * w).exp();
            if j > 0 && j + 1 < ROWS {
                let avg = 0.5 * (before[j - 1] + before[j + 1]);
                self.r[j] += (avg - before[j]) * (4.0 * dt * w).min(1.0);
            }
        }
        near
    }

    /// The roughness around a row (what the pen's buzz tells).
    pub fn roughness_at(&self, row: f32) -> f32 {
        let r = self.rows_around(row, 2.0);
        let n = r.len().max(1) as f32;
        self.rough[r].iter().sum::<f32>() / n
    }

    pub fn radius_at(&self, row: f32) -> f32 {
        let i = row.clamp(0.0, (ROWS - 1) as f32);
        let a = i.floor() as usize;
        let b = (a + 1).min(ROWS - 1);
        lerp(self.r[a], self.r[b], i - a as f32)
    }
}

// --- orders ------------------------------------------------------------------------------

pub struct Order {
    /// The strings key of its name.
    pub key: &'static str,
    /// (height 0..1, radius) control points, from the foot up.
    pub points: &'static [(f32, f32)],
    /// Straight lines between the points (a stepped shape) instead of a curve.
    pub straight: bool,
}

pub const ORDERS: [Order; 8] = [
    Order {
        key: "order.cup",
        points: &[(0.0, 0.62), (0.5, 0.64), (1.0, 0.72)],
        straight: false,
    },
    Order {
        key: "order.flowerpot",
        points: &[(0.0, 0.45), (1.0, 0.86)],
        straight: true,
    },
    Order {
        key: "order.bowl",
        points: &[(0.0, 0.35), (0.35, 0.7), (0.75, 0.9), (1.0, 0.93)],
        straight: false,
    },
    Order {
        key: "order.vase",
        points: &[
            (0.0, 0.5),
            (0.3, 0.85),
            (0.6, 0.55),
            (0.82, 0.32),
            (1.0, 0.5),
        ],
        straight: false,
    },
    Order {
        key: "order.hourglass",
        points: &[(0.0, 0.82), (0.5, 0.32), (1.0, 0.82)],
        straight: false,
    },
    Order {
        key: "order.bottle",
        points: &[
            (0.0, 0.72),
            (0.45, 0.78),
            (0.68, 0.38),
            (0.88, 0.22),
            (1.0, 0.26),
        ],
        straight: false,
    },
    Order {
        key: "order.tower",
        points: &[
            (0.0, 0.85),
            (0.32, 0.85),
            (0.34, 0.62),
            (0.65, 0.62),
            (0.67, 0.4),
            (1.0, 0.4),
        ],
        straight: true,
    },
    Order {
        key: "order.amphora",
        points: &[
            (0.0, 0.3),
            (0.12, 0.5),
            (0.42, 0.9),
            (0.72, 0.45),
            (0.88, 0.3),
            (1.0, 0.42),
        ],
        straight: false,
    },
];

/// An order's profile, one radius per row. Pure.
pub fn target(order: &Order) -> Vec<f32> {
    let pts = order.points;
    (0..ROWS)
        .map(|i| {
            let y = i as f32 / (ROWS - 1) as f32;
            let k = pts
                .iter()
                .rposition(|p| p.0 <= y)
                .unwrap_or(0)
                .min(pts.len().saturating_sub(2));
            if pts.len() == 1 {
                return pts[0].1;
            }
            let (p1, p2) = (pts[k], pts[k + 1]);
            let t = ((y - p1.0) / (p2.0 - p1.0).max(1e-6)).clamp(0.0, 1.0);
            if order.straight {
                return lerp(p1.1, p2.1, t);
            }
            // Catmull-Rom through the points (the ends repeated).
            let p0 = if k > 0 { pts[k - 1].1 } else { p1.1 };
            let p3 = if k + 2 < pts.len() {
                pts[k + 2].1
            } else {
                p2.1
            };
            let (t2, t3) = (t * t, t * t * t);
            0.5 * (2.0 * p1.1
                + (-p0 + p2.1) * t
                + (2.0 * p0 - 5.0 * p1.1 + 4.0 * p2.1 - p3) * t2
                + (-p0 + 3.0 * p1.1 - 3.0 * p2.1 + p3) * t3)
        })
        .map(|r| r.clamp(MIN_R, 1.0))
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Score {
    /// 0..1: how close the shape is.
    pub shape: f32,
    /// 0..1: how smooth the surface is.
    pub smooth: f32,
    pub total: u32,
    pub stars: u8,
}

/// The mean distance from the target at which the shape counts nothing.
pub const SHAPE_TOLERANCE: f32 = 0.22;

/// How well a pot matches an order. Pure.
pub fn score(r: &[f32], rough: &[f32], target: &[f32]) -> Score {
    let n = r.len().min(target.len()).max(1);
    let err = r
        .iter()
        .zip(target)
        .map(|(a, b)| (a - b).abs())
        .sum::<f32>()
        / n as f32;
    let shape = (1.0 - err / SHAPE_TOLERANCE).clamp(0.0, 1.0);
    let mean_rough = rough.iter().sum::<f32>() / rough.len().max(1) as f32;
    let smooth = (1.0 - mean_rough / 0.25).clamp(0.0, 1.0);
    let total = (100.0 * (0.85 * shape + 0.15 * smooth)).round() as u32;
    let stars = match total {
        90.. => 3,
        78..=89 => 2,
        60..=77 => 1,
        _ => 0,
    };
    Score {
        shape,
        smooth,
        total,
        stars,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tool(row: f32, dist: f32, pressure: f32, tilt: f32) -> Tool {
        Tool {
            row,
            dist,
            side: 1.0,
            pressure,
            tilt,
            eraser: false,
            speed: 0.3,
        }
    }

    #[test]
    fn an_upright_tool_cuts_a_narrow_groove() {
        let mut w = Wheel::new();
        for _ in 0..120 {
            w.carve(&tool(48.0, 0.6, 0.8, 0.0), 1.0 / 60.0);
        }
        assert!((w.r[48] - 0.6).abs() < 0.02, "{}", w.r[48]);
        assert_eq!(w.r[40], 1.0, "a point does not reach that far");
        assert_eq!(w.r[56], 1.0);
        assert!(w.r[47] < 1.0 && w.r[49] < 1.0);
    }

    #[test]
    fn a_tilted_tool_leaves_a_wall_at_its_lean() {
        let mut w = Wheel::new();
        let t = tool(48.0, 0.6, 0.8, 30.0);
        for _ in 0..240 {
            w.carve(&t, 1.0 / 60.0);
        }
        let slope = 30f32.to_radians().tan();
        for (j, &rj) in w.r.iter().enumerate().take(55).skip(42) {
            let h = (j as f32 - 48.0) * ROW_H;
            let want = 0.6 + h * slope;
            assert!((w.r[j] - want).abs() < 0.03, "row {j}: {} vs {want}", rj);
        }
        // On the left of the axis the same lean slopes the other way.
        let mut w2 = Wheel::new();
        let t2 = Tool { side: -1.0, ..t };
        for _ in 0..240 {
            w2.carve(&t2, 1.0 / 60.0);
        }
        assert!(w2.r[52] < w2.r[44]);
        assert!(w.r[52] > w.r[44]);
    }

    #[test]
    fn pressure_is_how_much_clay_comes_off() {
        let mut soft = Wheel::new();
        let mut hard = Wheel::new();
        let a = soft.carve(&tool(30.0, 0.3, 0.1, 0.0), 0.05);
        let b = hard.carve(&tool(30.0, 0.3, 0.9, 0.0), 0.05);
        assert!(b > 3.0 * a, "{a} {b}");
        assert!(
            hard.rough[30] > soft.rough[30],
            "a hard cut leaves it rough"
        );
    }

    #[test]
    fn the_wall_keeps_its_core() {
        let mut w = Wheel::new();
        for _ in 0..600 {
            w.carve(&tool(50.0, 0.0, 1.0, 0.0), 1.0 / 60.0);
        }
        assert!(w.r.iter().all(|r| *r >= MIN_R - 1e-6));
    }

    #[test]
    fn the_sponge_smooths_and_the_eraser_adds() {
        let mut w = Wheel::new();
        for _ in 0..30 {
            w.carve(&tool(48.0, 0.7, 1.0, 0.0), 1.0 / 60.0);
        }
        let rough = w.roughness_at(48.0);
        assert!(rough > 0.3, "{rough}");
        assert_eq!(
            w.sponge(48.0, 3.0, 0.5),
            0.0,
            "far from the surface: nothing"
        );
        for _ in 0..60 {
            w.sponge(48.0, w.r[48] + 0.05, 1.0 / 60.0);
        }
        assert!(w.roughness_at(48.0) < rough * 0.5);
        let before = w.r[20];
        let mut e = tool(20.0, 1.05, 0.5, 0.0);
        e.eraser = true;
        for _ in 0..60 {
            w.carve(&e, 1.0 / 60.0);
        }
        assert!(w.r[20] > before);
        assert!(w.r[20] <= 1.05 + 1e-4);
    }

    #[test]
    fn targets_stay_inside_the_lump() {
        for o in ORDERS.iter() {
            let t = target(o);
            assert_eq!(t.len(), ROWS);
            assert!(t.iter().all(|r| (MIN_R..=1.0).contains(r)), "{}", o.key);
            assert!((t[0] - o.points[0].1).abs() < 1e-3, "{}", o.key);
            assert!(
                (t[ROWS - 1] - o.points.last().unwrap().1).abs() < 1e-3,
                "{}",
                o.key
            );
        }
    }

    #[test]
    fn the_score_follows_the_match() {
        let t = target(&ORDERS[3]);
        let smooth = vec![0.0; ROWS];
        let s = score(&t, &smooth, &t);
        assert_eq!((s.total, s.stars), (100, 3));
        let block = Wheel::new();
        assert!(score(&block.r, &block.rough, &t).total < 30);
        let off: Vec<f32> = t.iter().map(|r| r + 0.05).collect();
        let s = score(&off, &smooth, &t);
        assert!((75..=85).contains(&s.total), "{}", s.total);
        let rough = vec![0.5; ROWS];
        assert!(
            score(&t, &rough, &t).total < 90,
            "a rough pot loses the polish part"
        );
    }

    #[test]
    fn a_patient_potter_gets_stars() {
        // Pass an upright tool down the target, row by row, then sponge it smooth.
        for o in ORDERS.iter() {
            let t = target(o);
            let mut w = Wheel::new();
            for pass in 0..3 {
                for (j, &tj) in t.iter().enumerate().take(ROWS) {
                    for _ in 0..6 {
                        w.carve(
                            &tool(j as f32, tj, if pass < 2 { 0.7 } else { 0.2 }, 0.0),
                            1.0 / 60.0,
                        );
                    }
                }
            }
            for _ in 0..2 {
                for j in 0..ROWS {
                    for _ in 0..3 {
                        let d = w.r[j];
                        w.sponge(j as f32, d + 0.04, 1.0 / 60.0);
                    }
                }
            }
            let s = score(&w.r, &w.rough, &t);
            assert!(s.stars >= 2, "{}: {s:?}", o.key);
        }
    }
}
