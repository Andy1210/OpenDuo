//! The slicing range: things thrown up from below, the blade (the pen's tip)
//! and the eraser end.
//!
//! A stroke cuts what it crosses when it is fast enough; the force is the
//! pressure (a little more with speed), and hard things (wood, ice, stone)
//! need a firm stroke or the blade glances off. Ink blots are the hazard:
//! the blade bursts them (a heart lost) and one that falls back down stains
//! the paper (a heart lost): turn the pen over and wipe them away with the
//! eraser, whose touch is a round shield. Several cuts in one stroke are a
//! combo; cuts in a row without a drop build a multiplier.
//!
//! Units: pixels of the arena (`w` x `h`, y down), seconds.

use crate::geom::{seg_dist, v2, V2};
use crate::rng::Rng;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Lantern,
    Gourd,
    Log,
    Ice,
    Stone,
    Star,
    Ink,
}

pub struct KindInfo {
    pub kind: Kind,
    /// The force a stroke needs (0..1).
    pub hardness: f32,
    /// Radius as a fraction of the arena's height.
    pub radius: f32,
    pub points: u32,
}

pub const KINDS: [KindInfo; 7] = [
    KindInfo {
        kind: Kind::Lantern,
        hardness: 0.0,
        radius: 0.040,
        points: 10,
    },
    KindInfo {
        kind: Kind::Gourd,
        hardness: 0.18,
        radius: 0.043,
        points: 15,
    },
    KindInfo {
        kind: Kind::Log,
        hardness: 0.36,
        radius: 0.040,
        points: 25,
    },
    KindInfo {
        kind: Kind::Ice,
        hardness: 0.52,
        radius: 0.037,
        points: 40,
    },
    KindInfo {
        kind: Kind::Stone,
        hardness: 0.68,
        radius: 0.039,
        points: 60,
    },
    KindInfo {
        kind: Kind::Star,
        hardness: 0.0,
        radius: 0.030,
        points: 100,
    },
    KindInfo {
        kind: Kind::Ink,
        hardness: 0.0,
        radius: 0.037,
        points: 25,
    },
];

pub fn info(kind: Kind) -> &'static KindInfo {
    KINDS
        .iter()
        .find(|k| k.kind == kind)
        .expect("every kind has its info")
}

/// How many pips of hardness it shows (0..4).
pub fn pips(kind: Kind) -> u32 {
    (info(kind).hardness / 0.17).round() as u32
}

/// A stroke slower than this (x the arena height per second) cuts nothing.
pub const MIN_SPEED: f32 = 0.55;
pub const HEARTS: u32 = 3;
pub const ROUNDS: u32 = 10;
/// The eraser's shield reaches this far beyond an object (x the arena height).
pub const SHIELD: f32 = 0.035;
pub const FOCUS_TIME: f32 = 3.0;
pub const FOCUS_SCALE: f32 = 0.4;

/// The force of a stroke: the pressure, plus up to 15 % for speed. Pure.
pub fn force(pressure: f32, speed: f32, h: f32) -> f32 {
    pressure.clamp(0.0, 1.0) * (0.85 + 0.15 * (speed / (1.6 * h)).min(1.0))
}

/// Does a stroke of this force cut a thing of this hardness? Pure.
pub fn cuts(force: f32, hardness: f32) -> bool {
    force + 0.04 >= hardness
}

#[derive(Clone, Debug)]
pub struct Obj {
    pub id: u32,
    pub kind: Kind,
    pub pos: V2,
    pub vel: V2,
    pub r: f32,
    pub rot: f32,
    pub spin: f32,
    /// After a glancing blow: no more hits until this runs out.
    pub cool: f32,
    /// Seconds since it was thrown (the view pops it in).
    pub age: f32,
}

#[derive(Clone, Copy, Debug)]
pub struct RoundPlan {
    pub total: u32,
    pub interval: f32,
    pub ink: f32,
    pub star: f32,
    /// Weights of lantern, gourd, log, ice, stone.
    pub mix: [f32; 5],
    pub burst: u32,
}

/// What round `n` (1..) throws. Pure.
pub fn plan(n: u32) -> RoundPlan {
    let n = n.max(1);
    let f = (n - 1) as f32;
    RoundPlan {
        total: 8 + 3 * n,
        interval: (1.15 - 0.07 * f).max(0.45),
        ink: if n == 1 {
            0.0
        } else {
            (0.07 + 0.025 * f).min(0.28)
        },
        star: 0.04,
        mix: [
            (4.0 - 0.35 * f).max(0.8),
            (2.5 - 0.1 * f).max(1.0),
            1.0 + 0.3 * f,
            if n >= 2 { 0.4 + 0.3 * f } else { 0.0 },
            if n >= 3 { 0.2 + 0.25 * f } else { 0.0 },
        ],
        burst: 1 + n.min(8) / 3,
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum RangeEvent {
    Thrown {
        kind: Kind,
    },
    Cut {
        id: u32,
        kind: Kind,
        pos: V2,
        vel: V2,
        angle: f32,
        points: u32,
        force: f32,
    },
    Clank {
        kind: Kind,
        pos: V2,
        force: f32,
    },
    Erased {
        pos: V2,
        points: u32,
    },
    Shoved {
        pos: V2,
    },
    /// The blade burst an ink blot: a heart lost.
    Burst {
        pos: V2,
    },
    /// An ink blot fell on the paper: a heart lost.
    Splat {
        x: f32,
    },
    Dropped {
        kind: Kind,
        x: f32,
    },
    Combo {
        cuts: u32,
        bonus: u32,
        pos: V2,
    },
    RoundClear {
        round: u32,
        perfect: bool,
        bonus: u32,
    },
    RoundStart {
        round: u32,
    },
    Focus,
    Over {
        victory: bool,
    },
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum State {
    Playing,
    /// Between rounds: the next starts when `t` runs out.
    Break {
        t: f32,
    },
    Over {
        victory: bool,
    },
}

pub struct Range {
    pub w: f32,
    pub h: f32,
    pub objs: Vec<Obj>,
    pub score: u32,
    pub hearts: u32,
    pub round: u32,
    pub streak: u32,
    pub best_combo: u32,
    pub state: State,
    /// The focus (slow time, the barrel button) fills with cuts.
    pub focus: f32,
    pub focus_left: f32,
    plan: RoundPlan,
    thrown: u32,
    throw_in: f32,
    round_drops: u32,
    round_hurt: bool,
    stroke_cuts: u32,
    stroke_at: V2,
    next_id: u32,
    rng: Rng,
}

impl Range {
    pub fn new(w: f32, h: f32, seed: u64) -> Range {
        Range {
            w,
            h,
            objs: Vec::new(),
            score: 0,
            hearts: HEARTS,
            round: 1,
            streak: 0,
            best_combo: 0,
            state: State::Break { t: 1.2 },
            focus: 0.0,
            focus_left: 0.0,
            plan: plan(1),
            thrown: 0,
            throw_in: 0.0,
            round_drops: 0,
            round_hurt: false,
            stroke_cuts: 0,
            stroke_at: v2(0.0, 0.0),
            next_id: 1,
            rng: Rng::new(seed),
        }
    }

    /// The arena changed size (the window): everything scales with it.
    pub fn resize(&mut self, w: f32, h: f32) {
        let (sx, sy) = (w / self.w, h / self.h);
        for o in &mut self.objs {
            o.pos = v2(o.pos.x * sx, o.pos.y * sy);
            o.vel = v2(o.vel.x * sx, o.vel.y * sy);
            o.r *= sy;
        }
        self.w = w;
        self.h = h;
    }

    pub fn multiplier(&self) -> f32 {
        1.0 + 0.25 * (self.streak / 5).min(8) as f32
    }

    pub fn gravity(&self) -> f32 {
        1.25 * self.h
    }

    /// The time scale now (the focus slows the things, not the pen).
    pub fn time_scale(&self) -> f32 {
        if self.focus_left > 0.0 {
            FOCUS_SCALE
        } else {
            1.0
        }
    }

    pub fn throw(&mut self, kind: Kind) -> u32 {
        let k = info(kind);
        let r = k.radius * self.h;
        let x = self.rng.range(0.15, 0.85) * self.w;
        let apex = self.rng.range(0.30, 0.82) * self.h; // how high it flies (from the bottom)
        let g = self.gravity();
        let vy = -(2.0 * g * (apex + r)).sqrt();
        let t_flight = 2.0 * -vy / g;
        // Towards the middle, landing somewhere on the other side.
        let land = self.rng.range(0.1, 0.9) * self.w;
        let mut vx = (land - x) / t_flight;
        if kind == Kind::Star {
            vx *= 1.6;
        }
        let id = self.next_id;
        self.next_id += 1;
        let spin = self.rng.range(-3.0, 3.0);
        self.objs.push(Obj {
            id,
            kind,
            pos: v2(x, self.h + r),
            vel: v2(vx, vy),
            r,
            rot: 0.0,
            spin,
            cool: 0.0,
            age: 0.0,
        });
        id
    }

    fn pick_kind(&mut self) -> Kind {
        if self.rng.chance(self.plan.ink) {
            return Kind::Ink;
        }
        if self.rng.chance(self.plan.star) {
            return Kind::Star;
        }
        [
            Kind::Lantern,
            Kind::Gourd,
            Kind::Log,
            Kind::Ice,
            Kind::Stone,
        ][self.rng.weighted(&self.plan.mix)]
    }

    /// Move everything on by `dt` (real seconds).
    pub fn update(&mut self, dt: f32) -> Vec<RangeEvent> {
        let mut ev = Vec::new();
        if self.focus_left > 0.0 {
            self.focus_left = (self.focus_left - dt).max(0.0);
        }
        let dt = dt * self.time_scale();
        match self.state {
            State::Over { .. } => {}
            State::Break { t } => {
                let t = t - dt;
                if t <= 0.0 && self.objs.is_empty() {
                    self.state = State::Playing;
                    self.plan = plan(self.round);
                    self.thrown = 0;
                    self.throw_in = 0.3;
                    self.round_drops = 0;
                    self.round_hurt = false;
                    ev.push(RangeEvent::RoundStart { round: self.round });
                } else {
                    self.state = State::Break { t };
                }
            }
            State::Playing => {
                if self.thrown < self.plan.total {
                    self.throw_in -= dt;
                    if self.throw_in <= 0.0 {
                        let n = 1 + self.rng.below(self.plan.burst as usize) as u32;
                        for _ in 0..n.min(self.plan.total - self.thrown) {
                            let k = self.pick_kind();
                            self.throw(k);
                            self.thrown += 1;
                            ev.push(RangeEvent::Thrown { kind: k });
                        }
                        self.throw_in =
                            self.plan.interval * self.rng.range(0.7, 1.3) * (0.7 + 0.3 * n as f32);
                    }
                } else if self.objs.is_empty() {
                    let perfect = self.round_drops == 0 && !self.round_hurt;
                    let bonus = if perfect { 50 * self.round } else { 0 };
                    self.score += bonus;
                    ev.push(RangeEvent::RoundClear {
                        round: self.round,
                        perfect,
                        bonus,
                    });
                    if self.round >= ROUNDS {
                        self.score += 500;
                        self.state = State::Over { victory: true };
                        ev.push(RangeEvent::Over { victory: true });
                    } else {
                        self.round += 1;
                        self.state = State::Break { t: 1.8 };
                    }
                }
            }
        }
        let g = self.gravity();
        let (w, h) = (self.w, self.h);
        let mut fell = Vec::new();
        for o in &mut self.objs {
            o.vel.y += g * dt;
            o.pos = o.pos + o.vel * dt;
            o.rot += o.spin * dt;
            o.cool = (o.cool - dt).max(0.0);
            o.age += dt;
            // The side walls bounce (a shove may push things out).
            if (o.pos.x < o.r && o.vel.x < 0.0) || (o.pos.x > w - o.r && o.vel.x > 0.0) {
                o.vel.x = -o.vel.x * 0.6;
            }
            if o.vel.y > 0.0 && o.pos.y - o.r > h {
                fell.push((o.id, o.kind, o.pos.x));
            }
        }
        for (id, kind, x) in fell {
            self.objs.retain(|o| o.id != id);
            if matches!(self.state, State::Over { .. }) {
                continue;
            }
            if kind == Kind::Ink {
                self.hurt(&mut ev);
                ev.push(RangeEvent::Splat { x });
            } else {
                self.streak = 0;
                self.round_drops += 1;
                ev.push(RangeEvent::Dropped { kind, x });
            }
        }
        ev
    }

    fn hurt(&mut self, ev: &mut Vec<RangeEvent>) {
        self.round_hurt = true;
        self.streak = 0;
        self.hearts = self.hearts.saturating_sub(1);
        if self.hearts == 0 && !matches!(self.state, State::Over { .. }) {
            self.state = State::Over { victory: false };
            ev.push(RangeEvent::Over { victory: false });
        }
    }

    /// One piece of a stroke, from `a` to `b`: the tip (`eraser` false)
    /// cuts, the eraser wipes ink and shoves the rest. `speed` is the
    /// stroke's speed (px/s, smoothed by the caller).
    pub fn stroke(
        &mut self,
        a: V2,
        b: V2,
        speed: f32,
        pressure: f32,
        eraser: bool,
    ) -> Vec<RangeEvent> {
        let mut ev = Vec::new();
        if matches!(self.state, State::Over { .. }) {
            return ev;
        }
        self.stroke_at = b;
        let f = force(pressure, speed, self.h);
        let fast = speed >= MIN_SPEED * self.h;
        let shield = SHIELD * self.h;
        let dir = (b - a).norm();
        let mut gone = Vec::new();
        for o in &mut self.objs {
            if o.cool > 0.0 {
                continue;
            }
            let reach = if eraser { o.r + shield } else { o.r };
            if seg_dist(a, b, o.pos) > reach {
                continue;
            }
            if eraser {
                if o.kind == Kind::Ink {
                    gone.push(o.id);
                    let points = (info(Kind::Ink).points as f32
                        * (1.0 + 0.25 * (self.streak / 5).min(8) as f32))
                        as u32;
                    self.score += points;
                    ev.push(RangeEvent::Erased { pos: o.pos, points });
                } else {
                    // The shield shoves things away from the eraser.
                    let away = (o.pos - b).norm();
                    o.vel = o.vel + away * (0.5 * self.h) + v2(0.0, -0.2 * self.h);
                    o.cool = 0.3;
                    ev.push(RangeEvent::Shoved { pos: o.pos });
                }
                continue;
            }
            if !fast {
                continue;
            }
            if o.kind == Kind::Ink {
                gone.push(o.id);
                ev.push(RangeEvent::Burst { pos: o.pos });
                continue;
            }
            let k = info(o.kind);
            if cuts(f, k.hardness) {
                gone.push(o.id);
                self.streak += 1;
                self.stroke_cuts += 1;
                self.focus = (self.focus + 0.06).min(1.0);
                let mult = 1.0 + 0.25 * (self.streak / 5).min(8) as f32;
                let points = (k.points as f32 * mult).round() as u32;
                self.score += points;
                ev.push(RangeEvent::Cut {
                    id: o.id,
                    kind: o.kind,
                    pos: o.pos,
                    vel: o.vel,
                    angle: dir.y.atan2(dir.x),
                    points,
                    force: f,
                });
            } else {
                // A glancing blow: it is knocked aside and spins up.
                let side = dir.perp();
                let s = if (o.pos - a).dot(side) > 0.0 {
                    1.0
                } else {
                    -1.0
                };
                o.vel = o.vel + side * (s * 0.35 * self.h) + dir * (0.25 * self.h);
                o.spin += 6.0 * s;
                o.cool = 0.35;
                ev.push(RangeEvent::Clank {
                    kind: o.kind,
                    pos: o.pos,
                    force: f,
                });
            }
        }
        let burst = ev
            .iter()
            .filter(|e| matches!(e, RangeEvent::Burst { .. }))
            .count();
        self.objs.retain(|o| !gone.contains(&o.id));
        for _ in 0..burst {
            self.hurt(&mut ev);
        }
        ev
    }

    /// The pen lifted: the stroke's combo, if it cut more than one.
    pub fn stroke_end(&mut self) -> Vec<RangeEvent> {
        let n = std::mem::take(&mut self.stroke_cuts);
        if n >= 2 {
            let bonus = 10 * n * (n - 1);
            self.score += bonus;
            self.best_combo = self.best_combo.max(n);
            vec![RangeEvent::Combo {
                cuts: n,
                bonus,
                pos: self.stroke_at,
            }]
        } else {
            Vec::new()
        }
    }

    /// The barrel button: slow time, when the focus is full.
    pub fn use_focus(&mut self) -> Option<RangeEvent> {
        if self.focus >= 1.0 && self.focus_left <= 0.0 && self.state == State::Playing {
            self.focus = 0.0;
            self.focus_left = FOCUS_TIME;
            Some(RangeEvent::Focus)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: f32 = 538.0;
    const H: f32 = 757.0;

    fn range_with(kind: Kind) -> (Range, V2) {
        let mut r = Range::new(W, H, 1);
        r.state = State::Playing;
        r.plan.total = 0;
        r.throw(kind);
        let o = &mut r.objs[0];
        o.pos = v2(W / 2.0, H / 2.0);
        o.vel = v2(0.0, 0.0);
        let p = o.pos;
        (r, p)
    }

    fn swipe(r: &mut Range, at: V2, speed: f32, pressure: f32, eraser: bool) -> Vec<RangeEvent> {
        let a = at + v2(-60.0, -10.0);
        let b = at + v2(60.0, 10.0);
        let mut ev = r.stroke(a, b, speed, pressure, eraser);
        ev.extend(r.stroke_end());
        ev
    }

    #[test]
    fn a_fast_stroke_cuts_and_a_slow_one_does_not() {
        let (mut r, p) = range_with(Kind::Lantern);
        assert!(swipe(&mut r, p, 0.2 * H, 0.5, false).is_empty());
        assert_eq!(r.objs.len(), 1);
        let ev = swipe(&mut r, p, 1.2 * H, 0.1, false);
        assert!(
            matches!(
                ev[0],
                RangeEvent::Cut {
                    kind: Kind::Lantern,
                    ..
                }
            ),
            "{ev:?}"
        );
        assert!(r.objs.is_empty());
        assert_eq!(r.score, 10);
    }

    #[test]
    fn stone_needs_a_firm_stroke() {
        let (mut r, p) = range_with(Kind::Stone);
        let ev = swipe(&mut r, p, 1.2 * H, 0.3, false);
        assert!(
            matches!(
                ev[0],
                RangeEvent::Clank {
                    kind: Kind::Stone,
                    ..
                }
            ),
            "{ev:?}"
        );
        assert_eq!(r.objs.len(), 1, "it glanced off");
        assert!(r.objs[0].vel.len() > 0.0, "and was knocked aside");
        r.objs[0].cool = 0.0;
        let p = r.objs[0].pos;
        let ev = swipe(&mut r, p, 1.2 * H, 0.8, false);
        assert!(
            matches!(
                ev[0],
                RangeEvent::Cut {
                    kind: Kind::Stone,
                    ..
                }
            ),
            "{ev:?}"
        );
    }

    #[test]
    fn force_is_mostly_pressure() {
        assert!(force(0.8, 0.0, H) > force(0.4, 2.0 * H, H));
        assert!(force(0.5, 2.0 * H, H) > force(0.5, 0.6 * H, H));
        for k in KINDS.iter() {
            assert!(
                cuts(force(1.0, MIN_SPEED * H, H), k.hardness),
                "everything yields to a full press"
            );
        }
        assert!(!cuts(force(0.2, H, H), info(Kind::Ice).hardness));
        assert!(pips(Kind::Lantern) == 0 && pips(Kind::Stone) == 4);
    }

    #[test]
    fn the_eraser_wipes_ink_and_the_blade_bursts_it() {
        let (mut r, p) = range_with(Kind::Ink);
        let ev = swipe(&mut r, p, 0.1 * H, 0.2, true);
        assert!(matches!(ev[0], RangeEvent::Erased { .. }), "{ev:?}");
        assert_eq!(r.hearts, HEARTS);
        let (mut r, p) = range_with(Kind::Ink);
        let ev = swipe(&mut r, p, 1.2 * H, 0.5, false);
        assert!(matches!(ev[0], RangeEvent::Burst { .. }), "{ev:?}");
        assert_eq!(r.hearts, HEARTS - 1);
    }

    #[test]
    fn the_shield_reaches_past_the_blot() {
        let (mut r, p) = range_with(Kind::Ink);
        let rad = r.objs[0].r;
        let at = p + v2(0.0, rad + 0.5 * SHIELD * H);
        assert!(matches!(
            swipe(&mut r, at, 0.0, 0.1, true)[0],
            RangeEvent::Erased { .. }
        ));
    }

    #[test]
    fn the_eraser_shoves_but_never_cuts() {
        let (mut r, p) = range_with(Kind::Log);
        let ev = swipe(&mut r, p, 2.0 * H, 1.0, true);
        assert!(matches!(ev[0], RangeEvent::Shoved { .. }), "{ev:?}");
        assert_eq!(r.objs.len(), 1);
    }

    #[test]
    fn one_stroke_through_three_is_a_combo() {
        let mut r = Range::new(W, H, 2);
        r.state = State::Playing;
        r.plan.total = 0;
        for (i, k) in [Kind::Lantern, Kind::Gourd, Kind::Lantern]
            .into_iter()
            .enumerate()
        {
            r.throw(k);
            r.objs[i].pos = v2(100.0 + 150.0 * i as f32, 300.0);
            r.objs[i].vel = v2(0.0, 0.0);
        }
        let mut ev = r.stroke(v2(50.0, 300.0), v2(500.0, 300.0), 1.5 * H, 0.5, false);
        ev.extend(r.stroke_end());
        assert_eq!(
            ev.iter()
                .filter(|e| matches!(e, RangeEvent::Cut { .. }))
                .count(),
            3
        );
        assert!(
            ev.contains(&RangeEvent::Combo {
                cuts: 3,
                bonus: 60,
                pos: v2(500.0, 300.0)
            }),
            "{ev:?}"
        );
        assert_eq!(r.best_combo, 3);
    }

    #[test]
    fn falling_ink_costs_a_heart_and_a_dropped_thing_the_streak() {
        let (mut r, _) = range_with(Kind::Ink);
        r.throw(Kind::Log);
        r.streak = 7;
        for o in &mut r.objs {
            o.pos.y = H + o.r - 1.0;
            o.vel = v2(0.0, 100.0);
        }
        let ev = r.update(0.05);
        assert!(
            ev.iter().any(|e| matches!(e, RangeEvent::Splat { .. })),
            "{ev:?}"
        );
        assert!(
            ev.iter().any(|e| matches!(
                e,
                RangeEvent::Dropped {
                    kind: Kind::Log,
                    ..
                }
            )),
            "{ev:?}"
        );
        assert_eq!(r.hearts, HEARTS - 1);
        assert_eq!(r.streak, 0);
    }

    #[test]
    fn rounds_get_harder() {
        let (a, b) = (plan(1), plan(8));
        assert!(b.total > a.total && b.interval < a.interval && b.ink > a.ink);
        assert_eq!(a.ink, 0.0, "the first round has no ink");
        assert!(b.mix[4] > a.mix[4]);
    }

    /// A perfect player: cuts everything firmly as it reaches its apex, wipes all ink.
    fn play(r: &mut Range, skill: bool) -> Vec<RangeEvent> {
        let dt = 1.0 / 60.0;
        let mut all = Vec::new();
        for _ in 0..(60 * 60 * 15) {
            all.extend(r.update(dt));
            if let State::Over { .. } = r.state {
                break;
            }
            if !skill {
                continue;
            }
            let targets: Vec<(Kind, V2)> = r
                .objs
                .iter()
                .filter(|o| o.vel.y > -50.0 && o.cool <= 0.0)
                .map(|o| (o.kind, o.pos))
                .collect();
            for (k, p) in targets {
                let ev = swipe(r, p, 1.5 * H, 0.9, k == Kind::Ink);
                all.extend(ev);
            }
        }
        all
    }

    #[test]
    fn a_perfect_player_clears_all_rounds() {
        let mut r = Range::new(W, H, 4);
        let ev = play(&mut r, true);
        assert_eq!(r.state, State::Over { victory: true });
        assert_eq!(r.hearts, HEARTS);
        assert_eq!(
            ev.iter()
                .filter(|e| matches!(e, RangeEvent::RoundClear { perfect: true, .. }))
                .count(),
            ROUNDS as usize
        );
        assert!(r.score > 1000, "{}", r.score);
    }

    #[test]
    fn an_idle_player_loses_to_the_ink() {
        let mut r = Range::new(W, H, 4);
        play(&mut r, false);
        assert_eq!(r.state, State::Over { victory: false });
        assert_eq!(r.hearts, 0);
        assert!(r.round >= 2);
    }

    #[test]
    fn focus_slows_time_once_full() {
        let mut r = Range::new(W, H, 4);
        r.state = State::Playing;
        assert!(r.use_focus().is_none());
        r.focus = 1.0;
        assert_eq!(r.use_focus(), Some(RangeEvent::Focus));
        assert_eq!(r.time_scale(), FOCUS_SCALE);
        r.update(FOCUS_TIME + 0.1);
        assert_eq!(r.time_scale(), 1.0);
    }
}
