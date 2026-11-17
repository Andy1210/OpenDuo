//! What the scenes share: particles and floating text, buttons, the top
//! bar, panels.

use crate::gfx::{rgb, rgba, Canvas, Col, WHITE};
use crate::text::{Align, Font};
use nibfair::geom::{v2, V2};
use nibfair::rng::Rng;
use nibfair::tween::{clamp01, ease_out_back, ease_out_cubic};
use std::f32::consts::TAU;

pub type Rect = (f32, f32, f32, f32);

pub const INK: Col = rgb(38, 30, 60);
pub const SHADOW: Col = rgba(20, 12, 40, 0.45);
pub const GOLD: Col = rgb(255, 205, 70);

#[derive(Clone, Copy, PartialEq)]
pub enum PKind {
    Dot,
    /// A streak along its velocity.
    Spark,
    /// An expanding ring.
    Ring,
    /// A spinning flake (confetti, shavings).
    Flake,
    /// A hollow circle (bubbles).
    Bubble,
}

#[derive(Clone)]
pub struct Particle {
    pub pos: V2,
    pub vel: V2,
    pub life: f32,
    pub max: f32,
    pub size: f32,
    pub col: Col,
    pub kind: PKind,
    pub rot: f32,
    pub spin: f32,
    pub grav: f32,
    pub drag: f32,
}

pub struct Popup {
    pub pos: V2,
    pub text: String,
    pub col: Col,
    pub life: f32,
    pub max: f32,
    pub size: f32,
}

pub struct Fx {
    pub parts: Vec<Particle>,
    pub pops: Vec<Popup>,
    pub shake: f32,
    pub flash: f32,
    pub flash_col: Col,
    pub rng: Rng,
}

impl Fx {
    pub fn new(seed: u64) -> Fx {
        Fx {
            parts: Vec::new(),
            pops: Vec::new(),
            shake: 0.0,
            flash: 0.0,
            flash_col: WHITE,
            rng: Rng::new(seed),
        }
    }

    pub fn add(&mut self, p: Particle) {
        if self.parts.len() < 1500 {
            self.parts.push(p);
        }
    }

    /// `n` particles flying out of `pos`.
    #[allow(clippy::too_many_arguments)]
    pub fn burst(
        &mut self,
        pos: V2,
        n: usize,
        col: Col,
        speed: f32,
        size: f32,
        kind: PKind,
        grav: f32,
    ) {
        for _ in 0..n {
            let a = self.rng.range(0.0, TAU);
            let s = speed * self.rng.range(0.3, 1.0);
            let life = self.rng.range(0.4, 0.9);
            let c = col.lighter(self.rng.range(0.0, 0.3));
            let spin = self.rng.range(-8.0, 8.0);
            let sz = size * self.rng.range(0.6, 1.2);
            self.add(Particle {
                pos,
                vel: v2(a.cos() * s, a.sin() * s),
                life,
                max: life,
                size: sz,
                col: c,
                kind,
                rot: a,
                spin,
                grav,
                drag: 1.5,
            });
        }
    }

    /// A spray in one direction (`dir` radians, `spread` wide).
    #[allow(clippy::too_many_arguments)]
    pub fn spray(
        &mut self,
        pos: V2,
        dir: f32,
        spread: f32,
        n: usize,
        col: Col,
        speed: f32,
        size: f32,
        kind: PKind,
        grav: f32,
    ) {
        for _ in 0..n {
            let a = dir + self.rng.range(-spread, spread);
            let s = speed * self.rng.range(0.4, 1.0);
            let life = self.rng.range(0.35, 0.8);
            let spin = self.rng.range(-10.0, 10.0);
            let sz = size * self.rng.range(0.6, 1.3);
            self.add(Particle {
                pos,
                vel: v2(a.cos() * s, a.sin() * s),
                life,
                max: life,
                size: sz,
                col,
                kind,
                rot: a,
                spin,
                grav,
                drag: 1.0,
            });
        }
    }

    pub fn ring(&mut self, pos: V2, size: f32, col: Col, life: f32) {
        self.add(Particle {
            pos,
            vel: v2(0.0, 0.0),
            life,
            max: life,
            size,
            col,
            kind: PKind::Ring,
            rot: 0.0,
            spin: 0.0,
            grav: 0.0,
            drag: 0.0,
        });
    }

    pub fn popup(&mut self, pos: V2, text: impl Into<String>, col: Col, size: f32) {
        self.pops.push(Popup {
            pos,
            text: text.into(),
            col,
            life: 1.1,
            max: 1.1,
            size,
        });
    }

    pub fn update(&mut self, dt: f32) {
        for p in &mut self.parts {
            p.life -= dt;
            p.vel.y += p.grav * dt;
            p.vel = p.vel * (-p.drag * dt).exp();
            p.pos = p.pos + p.vel * dt;
            p.rot += p.spin * dt;
        }
        self.parts.retain(|p| p.life > 0.0);
        for p in &mut self.pops {
            p.life -= dt;
            p.pos.y -= 38.0 * dt;
        }
        self.pops.retain(|p| p.life > 0.0);
        self.shake = (self.shake - dt * 2.5).max(0.0);
        self.flash = (self.flash - dt * 3.0).max(0.0);
    }

    pub fn shake_offset(&mut self) -> V2 {
        if self.shake <= 0.0 {
            return v2(0.0, 0.0);
        }
        let s = self.shake * self.shake * 10.0;
        v2(self.rng.range(-s, s), self.rng.range(-s, s))
    }

    pub fn draw(&self, cv: &mut Canvas) {
        for p in &self.parts {
            let t = clamp01(p.life / p.max);
            let col = p.col.a(t.min(1.0));
            match p.kind {
                PKind::Dot => cv.circle(p.pos, p.size * (0.4 + 0.6 * t), col),
                PKind::Spark => {
                    let tail = p.pos - p.vel * 0.035;
                    cv.line(tail, p.pos, p.size * t.max(0.3), col);
                }
                PKind::Ring => {
                    let k = ease_out_cubic(1.0 - t);
                    cv.ring(p.pos, p.size * (0.3 + k), 3.0 * t + 0.5, col.a(t));
                }
                PKind::Flake => {
                    let d = v2(p.size, 0.0).rot(p.rot);
                    let e = v2(0.0, p.size * 0.55 * p.rot.sin().abs().max(0.25)).rot(p.rot);
                    cv.convex(
                        p.pos,
                        &[p.pos - d - e, p.pos + d - e, p.pos + d + e, p.pos - d + e],
                        col,
                    );
                }
                PKind::Bubble => {
                    cv.ring(p.pos, p.size, (p.size * 0.18).max(0.8), col.a(0.8));
                    cv.circle(
                        p.pos + v2(-p.size * 0.35, -p.size * 0.35),
                        p.size * 0.22,
                        WHITE.a(0.7 * t),
                    );
                }
            }
        }
    }

    pub fn draw_pops(&self, cv: &mut Canvas, font: &mut Font) {
        for p in &self.pops {
            let age = p.max - p.life;
            let pop = 0.6 + 0.4 * ease_out_back(age / 0.25);
            let a = clamp01(p.life / 0.35);
            font.draw_outline_scaled(
                cv,
                &p.text,
                p.pos.x,
                p.pos.y,
                p.size,
                pop,
                p.col.a(a),
                Align::Center,
                INK.a(a * 0.85),
            );
        }
    }

    pub fn draw_flash(&self, cv: &mut Canvas, w: f32, h: f32) {
        if self.flash > 0.0 {
            cv.rect(0.0, 0.0, w, h, self.flash_col.a(self.flash * 0.5));
        }
    }
}

/// A big rounded button, pressed (held) or not. `col` is its face.
pub fn button(
    cv: &mut Canvas,
    font: &mut Font,
    r: Rect,
    label: &str,
    col: Col,
    held: bool,
    size: f32,
) {
    let (x, y, w, h) = r;
    let rad = h * 0.42;
    let dy = if held { 3.0 } else { 0.0 };
    cv.shadow(
        x + 2.0,
        y + 5.0,
        w - 4.0,
        h,
        rad,
        8.0,
        SHADOW.a(if held { 0.3 } else { 0.55 }),
    );
    cv.rrect(x, y + 4.0, w, h, rad, col.darker(0.35));
    cv.rrect_v(x, y + dy, w, h, rad, col.lighter(0.18), col);
    cv.rrect(
        x + h * 0.25,
        y + dy + h * 0.1,
        w - h * 0.5,
        h * 0.18,
        h * 0.09,
        WHITE.a(0.22),
    );
    font.draw_shadow(
        cv,
        label,
        x + w / 2.0,
        y + dy + h / 2.0,
        size,
        WHITE,
        Align::Center,
        col.darker(0.5).a(0.8),
    );
}

/// The round back button (top left).
pub fn back_button(cv: &mut Canvas, r: Rect, held: bool, col: Col) {
    let c = v2(
        r.0 + r.2 / 2.0,
        r.1 + r.3 / 2.0 + if held { 2.0 } else { 0.0 },
    );
    let rad = r.2.min(r.3) / 2.0;
    cv.circle(c + v2(0.0, 3.0), rad, col.darker(0.4).a(0.6));
    cv.circle(c, rad, col);
    cv.circle(c + v2(0.0, -rad * 0.08), rad * 0.85, col.lighter(0.12));
    let s = rad * 0.38;
    cv.line(
        c + v2(s * 0.35, -s),
        c + v2(-s * 0.6, 0.0),
        rad * 0.2,
        WHITE,
    );
    cv.line(c + v2(-s * 0.6, 0.0), c + v2(s * 0.35, s), rad * 0.2, WHITE);
}

/// A rounded panel with a soft shadow (results, cards).
pub fn panel(cv: &mut Canvas, r: Rect, top: Col, bottom: Col, rad: f32) {
    cv.shadow(r.0, r.1 + 6.0, r.2, r.3, rad, 14.0, SHADOW.a(0.6));
    cv.rrect_v(r.0, r.1, r.2, r.3, rad, top, bottom);
    cv.rrect(r.0 + 6.0, r.1 + 4.0, r.2 - 12.0, 3.0, 1.5, WHITE.a(0.25));
}

/// A star of a rating: filled or empty, popping in at `t` (0..1).
pub fn rating_star(cv: &mut Canvas, c: V2, r: f32, filled: bool, t: f32) {
    let s = if filled { ease_out_back(t) } else { 1.0 };
    if s <= 0.0 {
        return;
    }
    cv.star(c + v2(0.0, 2.0), r * s, 0.0, INK.a(0.35));
    if filled {
        cv.glow(c, r * 1.8 * s, GOLD.a(0.35));
        cv.star(c, r * s, 0.0, GOLD);
        cv.star(
            c + v2(-r * 0.12, -r * 0.15),
            r * 0.45 * s,
            0.0,
            WHITE.a(0.45),
        );
    } else {
        cv.star(c, r, 0.0, rgba(255, 255, 255, 0.25));
    }
}

/// The time as m:ss.
pub fn clock(secs: f32) -> String {
    let s = secs.max(0.0).ceil() as u32;
    format!("{}:{:02}", s / 60, s % 60)
}

/// Where the scenes put their words and panels: the middle of the window,
/// or with the two panels spanned (a wide window), the middle of the left
/// one, so nothing sits under the hinge. -> (centre x, usable width).
pub fn column(w: f32, h: f32) -> (f32, f32) {
    if w > h * 1.15 {
        (w * 0.25, w * 0.5 - 24.0)
    } else {
        (w / 2.0, w)
    }
}

pub fn inside(r: Rect, p: V2) -> bool {
    p.x >= r.0 && p.x <= r.0 + r.2 && p.y >= r.1 && p.y <= r.1 + r.3
}
