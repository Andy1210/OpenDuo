//! Slicing: the scene around nibfair::slice (the range's rules).
//!
//! Strokes of the pen's tip cut what they cross, if fast enough; the
//! pressure is the force (the gauge on the right shows it against the
//! hardness marks); the eraser end wipes ink away. Every cut clicks in the
//! pen.

use crate::gfx::{rgb, rgba, Canvas, Col, WHITE};
use crate::input::Input;
use crate::text::{Align, Font};
use crate::ui::{self, Fx, PKind, Rect, INK};
use crate::{Ctx, Go};
use nibfair::geom::{v2, V2};
use nibfair::haptic::{Pulse, Wave};
use nibfair::slice::{self, Kind, Range, RangeEvent, State, HEARTS, SHIELD};
use nibfair::strings::{t, tf, Lang};
use nibfair::tween::{clamp01, ease_out_back, ease_out_cubic};
use std::f32::consts::{PI, TAU};

/// A half of something cut, flying apart.
struct Half {
    kind: Kind,
    pos: V2,
    vel: V2,
    r: f32,
    /// The cut line's angle; the half is the side `side` of it.
    angle: f32,
    side: f32,
    rot: f32,
    spin: f32,
    life: f32,
}

#[derive(Clone, Copy)]
struct TrailPt {
    p: V2,
    age: f32,
    w: f32,
}

pub struct SliceScene {
    range: Range,
    fx: Fx,
    t: f32,
    halves: Vec<Half>,
    trail: Vec<TrailPt>,
    speed: f32,
    stains: Vec<(V2, f32, f32)>,
    banner: f32,
    soft_hints: u32,
    ink_hint: f32,
    ink_seen: bool,
    finished: bool,
    end_t: f32,
    new_best: bool,
    pen_pos: V2,
    pen_near: bool,
    pen_down: bool,
    pen_eraser: bool,
    pen_pressure: f32,
    pressure_show: f32,
    staged: bool,
    demo: bool,
    demo_t: f32,
}

fn colors(kind: Kind) -> (Col, Col) {
    match kind {
        Kind::Lantern => (rgb(240, 70, 60), rgb(255, 220, 110)),
        Kind::Gourd => (rgb(90, 180, 80), rgb(225, 245, 170)),
        Kind::Log => (rgb(140, 90, 50), rgb(235, 195, 140)),
        Kind::Ice => (rgb(110, 210, 245), rgb(235, 250, 255)),
        Kind::Stone => (rgb(130, 130, 145), rgb(200, 200, 210)),
        Kind::Star => (rgb(255, 200, 40), rgb(255, 250, 200)),
        Kind::Ink => (rgb(25, 20, 40), rgb(60, 40, 90)),
    }
}

impl SliceScene {
    pub fn new(cx: &mut Ctx) -> SliceScene {
        let seed = cx.next_seed();
        SliceScene {
            range: Range::new(cx.w, cx.h, seed),
            fx: Fx::new(seed ^ 0x77),
            t: 0.0,
            halves: Vec::new(),
            trail: Vec::new(),
            speed: 0.0,
            stains: Vec::new(),
            banner: 0.0,
            soft_hints: 0,
            ink_hint: 0.0,
            ink_seen: false,
            finished: false,
            end_t: 0.0,
            new_best: false,
            pen_pos: v2(0.0, 0.0),
            pen_near: false,
            pen_down: false,
            pen_eraser: false,
            pen_pressure: 0.0,
            pressure_show: 0.0,
            staged: false,
            demo: false,
            demo_t: 0.0,
        }
    }

    fn back(&self) -> Rect {
        (10.0, 10.0, 44.0, 44.0)
    }

    fn end_buttons(w: f32, h: f32) -> (Rect, Rect) {
        let (mid, span) = ui::column(w, h);
        let bw = (span * 0.36).min(200.0);
        let y = h * 0.2 + 250.0;
        ((mid - bw - 8.0, y, bw, 50.0), (mid + 8.0, y, bw, 50.0))
    }

    pub fn update(&mut self, cx: &mut Ctx, inp: &Input, dt: f32) -> Go {
        let (w, h) = (cx.w, cx.h);
        let lang = cx.lang;
        let now = cx.now;
        if (w - self.range.w).abs() > 0.5 || (h - self.range.h).abs() > 0.5 {
            self.range.resize(w, h);
        }
        self.t += dt;
        self.fx.update(dt);
        self.banner = (self.banner - dt).max(0.0);
        self.ink_hint = (self.ink_hint - dt).max(0.0);
        if inp.back || inp.tapped(self.back()) {
            return Go::Menu;
        }
        if self.finished {
            self.end_t += dt;
            let (again, menu) = Self::end_buttons(w, h);
            if inp.tapped(again) && self.end_t > 0.6 {
                return Go::Slicing;
            }
            if inp.tapped(menu) && self.end_t > 0.6 {
                return Go::Menu;
            }
        }
        let p = &inp.pen;
        if !self.staged {
            self.pen_pos = p.pos;
            self.pen_near = p.near;
            self.pen_down = p.down;
            self.pen_eraser = p.eraser;
            self.pen_pressure = p.pressure;
        }
        let ts = self.range.time_scale();
        let mut events = self.range.update(dt);
        // The strokes.
        let on_back = ui::inside(self.back(), p.pos);
        let mut moves = p.moves.clone();
        if self.demo {
            moves = self.demo_moves(dt);
        }
        for m in &moves {
            if !m.down || on_back || self.finished {
                continue;
            }
            let len = (m.to - m.from).len();
            let inst = len / m.dt.max(1e-4);
            self.speed = 0.5 * self.speed + 0.5 * inst;
            events.extend(
                self.range
                    .stroke(m.from, m.to, self.speed, m.pressure, m.eraser),
            );
            if !m.eraser {
                self.trail.push(TrailPt {
                    p: m.to,
                    age: 0.0,
                    w: 2.0 + 11.0 * m.pressure,
                });
            }
        }
        if p.pressed {
            self.trail.clear();
            self.speed = 0.0;
        }
        if p.released || (self.demo && moves.is_empty()) {
            events.extend(self.range.stroke_end());
        }
        if p.button_pressed {
            if let Some(e) = self.range.use_focus() {
                events.push(e);
            }
        }
        if self.pen_down && !self.pen_eraser {
            self.pressure_show = 1.0;
        } else {
            self.pressure_show = (self.pressure_show - dt * 0.8).max(0.0);
        }
        if !self.staged {
            for tp in &mut self.trail {
                tp.age += dt;
            }
        }
        self.trail.retain(|tp| tp.age < 0.14);
        // The halves.
        let g = self.range.gravity();
        for hf in &mut self.halves {
            hf.vel.y += g * dt * ts;
            hf.pos = hf.pos + hf.vel * (dt * ts);
            hf.rot += hf.spin * dt * ts;
            hf.life -= dt;
        }
        self.halves
            .retain(|hf| hf.life > 0.0 && hf.pos.y < h + 80.0);

        if !self.ink_seen && self.range.objs.iter().any(|o| o.kind == Kind::Ink) {
            self.ink_seen = true;
            self.ink_hint = 3.5;
        }
        for e in events {
            self.event(cx, lang, now, e);
        }
        if !self.finished {
            if let State::Over { victory } = self.range.state {
                self.finished = true;
                self.end_t = 0.0;
                self.new_best =
                    cx.save.best("best.slicing", self.range.score) && self.range.score > 0;
                cx.save.best("slicing.combo", self.range.best_combo);
                cx.store();
                cx.buzz.pulse(
                    now,
                    Pulse::new(if victory { Wave::Success } else { Wave::Error }, 1.0, 3),
                );
            }
        }
        Go::Stay
    }

    fn demo_moves(&mut self, dt: f32) -> Vec<crate::input::Move> {
        // The bench's blade: a sweep through the nearest thing every so often.
        self.demo_t += dt;
        let target = self
            .range
            .objs
            .iter()
            .find(|o| o.vel.y > -80.0 && o.cool <= 0.0)
            .map(|o| (o.pos, o.kind));
        match target {
            Some((p, k)) if self.demo_t > 0.25 => {
                self.demo_t = 0.0;
                let n = 6;
                let a = p + v2(-70.0, -30.0);
                let b = p + v2(70.0, 30.0);
                (0..n)
                    .map(|i| {
                        let u0 = i as f32 / n as f32;
                        let u1 = (i + 1) as f32 / n as f32;
                        crate::input::Move {
                            from: a + (b - a) * u0,
                            to: a + (b - a) * u1,
                            dt: 0.008,
                            down: true,
                            eraser: k == Kind::Ink,
                            pressure: 0.85,
                        }
                    })
                    .collect()
            }
            _ => Vec::new(),
        }
    }

    fn event(&mut self, cx: &mut Ctx, lang: Lang, now: f64, e: RangeEvent) {
        match e {
            RangeEvent::Cut {
                kind,
                pos,
                vel,
                angle,
                points,
                force,
                ..
            } => {
                let r = slice::info(kind).radius * self.range.h;
                let n = v2(-angle.sin(), angle.cos());
                for side in [-1.0f32, 1.0] {
                    let spin = self.fx.rng.range(2.0, 6.0) * side;
                    self.halves.push(Half {
                        kind,
                        pos: pos + n * (side * r * 0.15),
                        vel: vel * 0.5 + n * (side * 160.0) + v2(0.0, -60.0),
                        r,
                        angle,
                        side,
                        rot: 0.0,
                        spin,
                        life: 2.0,
                    });
                }
                let (a, b) = colors(kind);
                self.fx.burst(pos, 14, b, 260.0, 3.0, PKind::Dot, 600.0);
                self.fx
                    .spray(pos, angle, 0.3, 6, WHITE, 500.0, 2.0, PKind::Spark, 0.0);
                self.fx.burst(pos, 6, a, 180.0, 4.0, PKind::Flake, 700.0);
                if kind == Kind::Star {
                    self.fx
                        .burst(pos, 30, rgb(255, 230, 90), 320.0, 3.0, PKind::Flake, 300.0);
                }
                let mult = self.range.multiplier();
                let txt = if mult > 1.0 {
                    format!("+{points} x{mult:.2}")
                } else {
                    format!("+{points}")
                };
                self.fx.popup(
                    pos + v2(0.0, -r - 10.0),
                    txt,
                    if kind == Kind::Star { ui::GOLD } else { WHITE },
                    20.0,
                );
                cx.buzz
                    .pulse(now, Pulse::new(Wave::Click, 0.55 + 0.45 * force, 1));
            }
            RangeEvent::Clank { pos, .. } => {
                self.fx.spray(
                    pos,
                    -PI / 2.0,
                    1.4,
                    12,
                    rgb(255, 230, 120),
                    340.0,
                    2.0,
                    PKind::Spark,
                    300.0,
                );
                self.fx.ring(pos, 30.0, rgb(255, 240, 200), 0.3);
                if self.soft_hints < 4 {
                    self.soft_hints += 1;
                    self.fx.popup(
                        pos + v2(0.0, -40.0),
                        t(lang, "slice.soft"),
                        rgb(255, 200, 120),
                        18.0,
                    );
                }
                cx.buzz.pulse(now, Pulse::new(Wave::Press, 0.45, 1));
            }
            RangeEvent::Erased { pos, points } => {
                self.fx
                    .burst(pos, 22, rgb(255, 170, 200), 220.0, 3.5, PKind::Flake, 100.0);
                self.fx.ring(pos, 50.0, rgb(255, 180, 210), 0.45);
                self.fx.popup(
                    pos + v2(0.0, -30.0),
                    format!("+{points}"),
                    rgb(255, 190, 220),
                    20.0,
                );
                cx.buzz.pulse(now, Pulse::new(Wave::Hover, 0.8, 1));
            }
            RangeEvent::Shoved { pos } => {
                self.fx.ring(pos, 30.0, rgb(255, 180, 210), 0.3);
            }
            RangeEvent::Burst { pos } => {
                self.splat(pos, 1.3);
                self.fx.shake = 0.9;
                self.fx.flash = 0.7;
                self.fx.flash_col = rgb(40, 20, 60);
                cx.buzz.pulse(now, Pulse::new(Wave::Error, 1.0, 3));
            }
            RangeEvent::Splat { x } => {
                let p = v2(x.clamp(20.0, self.range.w - 20.0), self.range.h - 8.0);
                self.splat(p, 1.0);
                self.fx.shake = 0.6;
                cx.buzz.pulse(now, Pulse::new(Wave::Error, 0.8, 2));
            }
            RangeEvent::Dropped { .. } => {}
            RangeEvent::Combo { cuts, bonus, pos } => {
                let p = v2(
                    pos.x.clamp(80.0, self.range.w - 80.0),
                    pos.y.clamp(120.0, self.range.h - 80.0),
                );
                self.fx.popup(
                    p,
                    tf(lang, "slice.combo", &[&cuts]),
                    rgb(120, 230, 255),
                    26.0,
                );
                self.fx.popup(
                    p + v2(0.0, 28.0),
                    format!("+{bonus}"),
                    rgb(120, 230, 255),
                    20.0,
                );
                self.fx
                    .burst(p, 30, rgb(140, 230, 255), 300.0, 3.0, PKind::Spark, 0.0);
                cx.buzz.pulse(now, Pulse::new(Wave::Success, 0.9, 2));
            }
            RangeEvent::RoundClear { perfect, bonus, .. } => {
                if perfect {
                    let p = v2(
                        ui::column(self.range.w, self.range.h).0,
                        self.range.h * 0.42,
                    );
                    self.fx
                        .popup(p, tf(lang, "slice.perfect", &[&bonus]), ui::GOLD, 26.0);
                    self.fx
                        .burst(p, 40, ui::GOLD, 300.0, 3.0, PKind::Flake, 200.0);
                }
            }
            RangeEvent::RoundStart { .. } => {
                self.banner = 1.6;
            }
            RangeEvent::Focus => {
                self.fx.flash = 0.5;
                self.fx.flash_col = rgb(120, 200, 255);
                self.fx.popup(
                    v2(ui::column(self.range.w, self.range.h).0, self.range.h * 0.3),
                    t(lang, "slice.slow"),
                    rgb(140, 220, 255),
                    28.0,
                );
                cx.buzz.pulse(now, Pulse::new(Wave::Success, 0.7, 2));
            }
            RangeEvent::Thrown { .. } | RangeEvent::Over { .. } => {}
        }
    }

    fn splat(&mut self, p: V2, size: f32) {
        self.fx
            .burst(p, 30, rgb(30, 20, 50), 300.0 * size, 5.0, PKind::Dot, 500.0);
        let r = self.fx.rng.range(26.0, 40.0) * size;
        let a = self.fx.rng.range(0.0, TAU);
        self.stains.push((p, r, a));
    }

    pub fn stage(&mut self, _cx: &mut Ctx, n: u32) {
        let (w, h) = (self.range.w, self.range.h);
        match n {
            1 | 2 => {
                self.staged = true;
                self.range.state = State::Playing;
                self.range.round = if n == 1 { 3 } else { 4 };
                self.range.score = if n == 1 { 845 } else { 1630 };
                self.range.streak = 12;
                self.range.focus = 0.7;
                let kinds: &[Kind] = if n == 1 {
                    &[
                        Kind::Lantern,
                        Kind::Log,
                        Kind::Ice,
                        Kind::Stone,
                        Kind::Gourd,
                    ]
                } else {
                    &[Kind::Ink, Kind::Lantern, Kind::Star, Kind::Ink]
                };
                for (i, k) in kinds.iter().enumerate() {
                    self.range.throw(*k);
                    let o = self.range.objs.last_mut().unwrap();
                    o.pos = v2(
                        w * (0.15 + 0.7 * i as f32 / (kinds.len() - 1) as f32),
                        h * (0.35 + 0.2 * ((i * 7) % 3) as f32 / 2.0),
                    );
                    o.vel = v2(0.0, -400.0);
                    o.age = 1.0;
                    o.rot = i as f32;
                }
                // Hold them still for the picture: no gravity in a staged scene.
                if n == 1 {
                    self.pen_down = true;
                    self.pen_eraser = false;
                    self.pen_pressure = 0.7;
                    self.pressure_show = 1.0;
                    let a = v2(w * 0.2, h * 0.72);
                    for k in 0..12 {
                        let u = k as f32 / 11.0;
                        self.trail.push(TrailPt {
                            p: a + v2(w * 0.55 * u, -h * 0.18 * u - 30.0 * (u * PI).sin()),
                            age: 0.13 * (1.0 - u),
                            w: 2.0 + 11.0 * 0.7,
                        });
                    }
                    self.pen_pos = self.trail.last().unwrap().p;
                    self.range.hearts = 2;
                } else {
                    self.pen_down = true;
                    self.pen_eraser = true;
                    self.pen_pressure = 0.5;
                    self.pen_pos = self.range.objs[0].pos + v2(30.0, 20.0);
                    self.ink_hint = 3.0;
                    self.ink_seen = true;
                    self.stains.push((v2(w * 0.7, h - 8.0), 34.0, 1.0));
                    self.range.hearts = 2;
                }
                self.pen_near = true;
            }
            3 => {
                self.range.score = 2310;
                self.range.best_combo = 4;
                self.range.round = 7;
                self.range.hearts = 0;
                self.range.state = State::Over { victory: false };
                self.finished = true;
                self.end_t = 2.0;
            }
            100 => {
                self.demo = true;
            }
            _ => {}
        }
    }

    pub fn draw(&mut self, cx: &mut Ctx, inp: &Input) {
        let (w, h) = (cx.w, cx.h);
        let (mid, span) = ui::column(w, h);
        let lang = cx.lang;
        let shake = self.fx.shake_offset();
        let cv = &mut cx.cv;
        let font = &mut cx.font;
        // Paper.
        cv.rect_v(0.0, 0.0, w, h, rgb(252, 244, 226), rgb(240, 222, 188));
        for k in 0..3 {
            let c = v2(
                w * (0.2 + 0.35 * k as f32),
                h * (0.25 + 0.3 * (k % 2) as f32),
            );
            let r = w * (0.22 + 0.06 * k as f32);
            cv.arc(
                c,
                r,
                10.0,
                0.3 + k as f32,
                0.3 + k as f32 + TAU * 0.85,
                rgba(150, 110, 70, 0.07),
            );
        }
        if self.range.time_scale() < 1.0 {
            cv.rect(0.0, 0.0, w, h, rgba(110, 180, 255, 0.12));
        }
        // The ink stains on the paper's edge.
        for (p, r, a) in &self.stains {
            ink_blot(cv, *p, *r, *a, 0.0, 0.75);
        }
        cv.rect(0.0, h - 4.0, w, 4.0, rgba(120, 80, 50, 0.25));

        for hf in &self.halves {
            draw_half(cv, hf);
        }
        for o in &self.range.objs {
            let pop = ease_out_back(clamp01(o.age / 0.25));
            draw_thing(cv, o.kind, o.pos, o.r * pop.max(0.2), o.rot, self.t);
            if o.kind == Kind::Ink {
                let rr = o.r + SHIELD * h * 0.6;
                let k = 0.5 + 0.5 * (self.t * 6.0).sin();
                cv.arc(
                    o.pos,
                    rr,
                    2.0,
                    self.t * 2.0,
                    self.t * 2.0 + TAU * 0.25,
                    rgb(255, 130, 180).a(0.5 + 0.4 * k),
                );
                cv.arc(
                    o.pos,
                    rr,
                    2.0,
                    self.t * 2.0 + PI,
                    self.t * 2.0 + PI + TAU * 0.25,
                    rgb(255, 130, 180).a(0.5 + 0.4 * k),
                );
            } else {
                let n = slice::pips(o.kind);
                for i in 0..n {
                    let a = -PI / 2.0 + (i as f32 - (n as f32 - 1.0) / 2.0) * 0.32;
                    let p = o.pos + v2(a.cos(), a.sin()) * (o.r + 8.0);
                    cv.circle(p, 3.6, INK.a(0.75));
                    cv.circle(p, 2.4, WHITE);
                }
            }
        }

        // The blade's trail.
        if self.trail.len() >= 2 {
            let n = self.trail.len();
            let glow: Vec<(V2, f32, Col)> = self
                .trail
                .iter()
                .enumerate()
                .map(|(i, tp)| {
                    let u = (i + 1) as f32 / n as f32;
                    (tp.p, tp.w * 2.4 * u, rgba(120, 220, 255, 0.35 * u))
                })
                .collect();
            cv.ribbon(&glow);
            let core: Vec<(V2, f32, Col)> = self
                .trail
                .iter()
                .enumerate()
                .map(|(i, tp)| {
                    let u = (i + 1) as f32 / n as f32;
                    (tp.p, tp.w * u, WHITE.a(0.95))
                })
                .collect();
            cv.ribbon(&core);
        }
        // The eraser: a rubber block with its shield.
        if (self.pen_near || self.pen_down) && self.pen_eraser {
            let p = self.pen_pos;
            let rr = SHIELD * h + 14.0;
            let on = if self.pen_down { 1.0 } else { 0.4 };
            cv.glow(p, rr * 1.4, rgba(255, 140, 190, 0.25 * on));
            cv.ring(p, rr, 3.0, rgb(255, 120, 170).a(0.4 + 0.5 * on));
            cv.rrect(p.x - 18.0, p.y - 11.0, 36.0, 22.0, 7.0, rgb(255, 150, 180));
            cv.rrect(p.x - 18.0, p.y - 11.0, 12.0, 22.0, 5.0, rgb(90, 110, 200));
            cv.rect(p.x - 10.0, p.y - 11.0, 4.0, 22.0, rgb(90, 110, 200));
            cv.rect(p.x - 4.0, p.y - 8.0, 18.0, 3.0, WHITE.a(0.35));
        }

        self.fx.draw(cv);
        self.fx.draw_pops(cv, font);

        // The pressure gauge: the force against the hardness marks.
        if self.pressure_show > 0.0 {
            let a = clamp01(self.pressure_show * 2.0);
            let (gx, gy, gw, gh) = (w - 26.0, h * 0.3, 12.0, h * 0.4);
            cv.rrect(gx - 3.0, gy - 3.0, gw + 6.0, gh + 6.0, 9.0, INK.a(0.35 * a));
            let f = self.pen_pressure.clamp(0.0, 1.0);
            let fill = gh * f;
            let col = rgb(120, 220, 255).mix(rgb(255, 120, 90), f);
            cv.rrect(gx, gy + gh - fill, gw, fill, 6.0, col.a(a));
            for k in [Kind::Gourd, Kind::Log, Kind::Ice, Kind::Stone] {
                let hd = slice::info(k).hardness;
                let y = gy + gh * (1.0 - hd);
                let (c, _) = colors(k);
                cv.rect(gx - 8.0, y - 1.0, gw + 8.0, 2.0, INK.a(0.6 * a));
                cv.circle(v2(gx - 12.0, y), 5.0, c.a(a));
            }
        }

        // The top bar: back, hearts, round, score.
        ui::back_button(cv, self.back(), inp.held_in(self.back()), rgb(200, 80, 90));
        for i in 0..HEARTS {
            let c = v2(74.0 + i as f32 * 30.0, 32.0);
            if i < self.range.hearts {
                cv.heart(c + v2(0.0, 2.0), 24.0, INK.a(0.3));
                cv.heart(c, 24.0, rgb(235, 60, 80));
                cv.circle(c + v2(-5.0, -5.0), 2.5, WHITE.a(0.7));
            } else {
                cv.heart(c, 24.0, INK.a(0.18));
            }
        }
        let sc = self.range.score.to_string();
        font.draw_shadow(
            cv,
            &sc,
            w - 16.0,
            30.0,
            28.0,
            rgb(60, 45, 90),
            Align::Right,
            WHITE.a(0.6),
        );
        let mult = self.range.multiplier();
        if mult > 1.0 {
            font.draw(
                cv,
                &format!("x{mult:.2}"),
                w - 16.0,
                58.0,
                16.0,
                rgb(40, 150, 200),
                Align::Right,
            );
        }
        let rt = tf(lang, "slice.round", &[&self.range.round]);
        font.draw(cv, &rt, mid, 30.0, 16.0, INK.a(0.6), Align::Center);
        // The focus meter.
        let fw = 90.0;
        let fx0 = mid - fw / 2.0;
        cv.rrect(fx0, 46.0, fw, 8.0, 4.0, INK.a(0.15));
        let full = self.range.focus >= 1.0;
        let fc = if full {
            rgb(80, 180, 255).lighter(0.3 * (self.t * 6.0).sin().abs())
        } else {
            rgb(80, 180, 255)
        };
        cv.rrect(fx0, 46.0, fw * self.range.focus.min(1.0), 8.0, 4.0, fc);
        if full && !self.finished {
            font.draw(
                cv,
                t(lang, "slice.focus"),
                mid,
                66.0,
                12.0,
                rgb(40, 120, 200),
                Align::Center,
            );
        }

        // The round's banner and the hints.
        if self.banner > 0.0 {
            let k = ease_out_back(clamp01((1.6 - self.banner) / 0.4));
            let a = clamp01(self.banner / 0.3);
            font.draw_outline_scaled(
                cv,
                &rt,
                mid,
                h * 0.4,
                44.0,
                k,
                rgb(255, 210, 90).a(a),
                Align::Center,
                INK.a(a),
            );
        }
        if !self.finished {
            let hint = if self.ink_hint > 0.0 {
                Some("slice.hint.ink")
            } else if self.range.round == 1 && self.t < 10.0 {
                Some("slice.hint.cut")
            } else if self.range.round <= 3 && self.soft_hints > 0 && self.soft_hints < 4 {
                Some("slice.hint.hard")
            } else {
                None
            };
            if let Some(k) = hint {
                let txt = t(lang, k);
                let lines = font.wrap(txt, span - 60.0, 15.0);
                let y = h - 34.0 - (lines.len() as f32 - 1.0) * 19.0;
                let bw = lines
                    .iter()
                    .map(|l| font.width(l, 15.0))
                    .fold(0.0, f32::max)
                    + 28.0;
                let bh = 30.0 + (lines.len() as f32 - 1.0) * 19.0;
                let ink = k == "slice.hint.ink";
                cv.rrect(
                    mid - bw / 2.0,
                    y - 15.0,
                    bw,
                    bh,
                    15.0,
                    if ink {
                        rgb(255, 120, 170).a(0.92)
                    } else {
                        INK.a(0.55)
                    },
                );
                for (i, l) in lines.iter().enumerate() {
                    font.draw(cv, l, mid, y + i as f32 * 19.0, 15.0, WHITE, Align::Center);
                }
            }
            if !inp.pen.seen && !self.staged && !self.demo && self.t > 1.5 {
                font.draw(
                    cv,
                    t(lang, "pen.missing"),
                    mid,
                    h - 70.0,
                    13.0,
                    INK.a(0.6),
                    Align::Center,
                );
            }
        }
        self.fx.draw_flash(cv, w, h);
        if self.finished {
            self.draw_end(cv, font, w, h, lang, inp);
        }
        if shake.len() > 0.0 {
            for v in cv.v.iter_mut() {
                v.position.x += shake.x;
                v.position.y += shake.y;
            }
        }
    }

    fn draw_end(&self, cv: &mut Canvas, font: &mut Font, w: f32, h: f32, lang: Lang, inp: &Input) {
        let k = ease_out_back(clamp01(self.end_t / 0.45));
        cv.rect(0.0, 0.0, w, h, INK.a(0.4 * clamp01(self.end_t * 3.0)));
        let (mid, span) = ui::column(w, h);
        let pw = (span - 40.0).min(420.0);
        let r = (mid - pw / 2.0, h * 0.2 + (1.0 - k) * 80.0, pw, 220.0);
        ui::panel(cv, r, rgb(255, 250, 238), rgb(240, 226, 200), 22.0);
        let victory = matches!(self.range.state, State::Over { victory: true });
        let title = t(
            lang,
            if victory {
                "slice.victory"
            } else {
                "slice.over"
            },
        );
        font.draw_wrapped(
            cv,
            title,
            mid,
            r.1 + 36.0,
            pw - 30.0,
            20.0,
            INK,
            Align::Center,
        );
        font.draw_outline(
            cv,
            &self.range.score.to_string(),
            mid,
            r.1 + 100.0,
            50.0,
            rgb(255, 120, 110),
            Align::Center,
            INK,
        );
        if self.new_best {
            let pulse = 1.0 + 0.06 * (self.end_t * 8.0).sin();
            font.draw_scaled(
                cv,
                t(lang, "common.newbest"),
                mid,
                r.1 + 144.0,
                18.0,
                pulse,
                rgb(220, 70, 90),
                Align::Center,
            );
        }
        let line = format!(
            "{}   ·   {}",
            tf(lang, "slice.round", &[&self.range.round]),
            tf(lang, "slice.combobest", &[&self.range.best_combo])
        );
        font.draw(
            cv,
            &line,
            mid,
            r.1 + 178.0,
            15.0,
            INK.a(0.65),
            Align::Center,
        );
        let (again, menu) = Self::end_buttons(w, h);
        ui::button(
            cv,
            font,
            again,
            t(lang, "common.again"),
            rgb(230, 90, 90),
            inp.held_in(again),
            18.0,
        );
        ui::button(
            cv,
            font,
            menu,
            t(lang, "common.back"),
            rgb(110, 90, 170),
            inp.held_in(menu),
            18.0,
        );
        let _ = ease_out_cubic;
    }
}

/// An ink blot: a wobbly dark disc with spikes.
fn ink_blot(cv: &mut Canvas, c: V2, r: f32, phase: f32, t: f32, alpha: f32) {
    let n = 22;
    let pts: Vec<V2> = (0..n)
        .map(|k| {
            let a = k as f32 / n as f32 * TAU;
            let spike = if k % 4 == 0 { 0.35 } else { 0.0 };
            let wob = 0.08 * (a * 3.0 + phase * 5.0 + t * 4.0).sin();
            c + v2(a.cos(), a.sin()) * (r * (0.9 + wob + spike))
        })
        .collect();
    cv.convex(c, &pts, rgb(28, 20, 45).a(alpha));
}

fn draw_thing(cv: &mut Canvas, kind: Kind, c: V2, r: f32, rot: f32, t: f32) {
    let (a, b) = colors(kind);
    cv.ellipse(c + v2(3.0, 5.0), r, r * 0.95, 0.0, INK.a(0.15));
    match kind {
        Kind::Lantern => {
            cv.glow(c, r * 1.7, rgba(255, 180, 80, 0.35));
            cv.rrect(
                c.x - r * 0.35,
                c.y - r * 1.12,
                r * 0.7,
                r * 0.22,
                3.0,
                rgb(60, 40, 40),
            );
            cv.rrect(
                c.x - r * 0.35,
                c.y + r * 0.9,
                r * 0.7,
                r * 0.22,
                3.0,
                rgb(60, 40, 40),
            );
            cv.ellipse(c, r * 0.95, r, 0.0, a);
            cv.circle_grad(c, r * 0.7, b.a(0.85), a.a(0.0));
            for k in [-0.55f32, 0.0, 0.55] {
                let pts: Vec<V2> = (0..=8)
                    .map(|i| {
                        let u = i as f32 / 8.0 * PI;
                        c + v2(k * r * u.sin(), -r * u.cos())
                    })
                    .collect();
                cv.polyline(&pts, 1.6, a.darker(0.35));
            }
            cv.line(
                c + v2(0.0, r * 1.1),
                c + v2((t * 3.0).sin() * 3.0, r * 1.45),
                2.0,
                rgb(250, 200, 60),
            );
        }
        Kind::Gourd => {
            cv.circle(c, r, a);
            for k in 0..5 {
                let off = (k as f32 - 2.0) * 0.38 + (rot * 0.3).sin() * 0.1;
                let pts: Vec<V2> = (0..=10)
                    .map(|i| {
                        let u = i as f32 / 10.0 * PI;
                        c + v2(off * r * u.sin(), -r * 0.97 * u.cos())
                    })
                    .collect();
                cv.polyline(&pts, 2.4, a.darker(0.25));
            }
            cv.circle(c + v2(-r * 0.35, -r * 0.4), r * 0.2, WHITE.a(0.3));
            cv.line(
                c + v2(0.0, -r),
                c + v2(4.0, -r - 9.0),
                4.0,
                rgb(110, 80, 40),
            );
        }
        Kind::Log => {
            cv.circle(c, r, a.darker(0.25));
            cv.circle(c, r * 0.86, b);
            for k in 1..4 {
                cv.ring(
                    c + v2(1.5, -1.0),
                    r * 0.22 * k as f32,
                    1.6,
                    a.lighter(0.15).a(0.7),
                );
            }
            cv.line(c, c + v2(r * 0.7, 0.0).rot(rot), 1.5, a.a(0.5));
        }
        Kind::Ice => {
            cv.glow(c, r * 1.5, rgba(160, 230, 255, 0.35));
            cv.ngon(c, r * 1.05, 6, rot, a);
            cv.ngon(c, r * 0.7, 6, rot, b.a(0.7));
            cv.line(
                c + v2(-r * 0.4, -r * 0.2).rot(rot),
                c + v2(r * 0.1, -r * 0.6).rot(rot),
                3.0,
                WHITE.a(0.9),
            );
        }
        Kind::Stone => {
            let pts: Vec<V2> = (0..9)
                .map(|k| {
                    let ang = rot + k as f32 / 9.0 * TAU;
                    let rr = r * (0.88 + 0.14 * ((k * 5) % 3) as f32 / 2.0);
                    c + v2(ang.cos(), ang.sin()) * rr
                })
                .collect();
            cv.convex(c, &pts, a);
            cv.circle(
                c + v2(-r * 0.3, -r * 0.3).rot(rot * 0.2),
                r * 0.35,
                b.a(0.5),
            );
            for k in 0..4 {
                cv.circle(
                    c + v2(r * 0.45, 0.0).rot(rot + k as f32 * 1.7),
                    r * 0.08,
                    a.darker(0.35),
                );
            }
        }
        Kind::Star => {
            cv.glow(c, r * 2.2, rgba(255, 220, 80, 0.45));
            cv.star(c, r * 1.25, rot, a);
            cv.star(c, r * 0.6, rot, b);
        }
        Kind::Ink => {
            ink_blot(cv, c, r, rot, t, 1.0);
            cv.circle(c + v2(-r * 0.3, -r * 0.3), r * 0.18, WHITE.a(0.55));
            cv.circle(c + v2(-r * 0.05, -r * 0.42), r * 0.08, WHITE.a(0.45));
        }
    }
}

fn draw_half(cv: &mut Canvas, hf: &Half) {
    let (a, b) = colors(hf.kind);
    let alpha = clamp01(hf.life / 0.4);
    let base = hf.angle + hf.rot;
    let n = 14;
    let start = if hf.side > 0.0 { 0.0 } else { PI };
    let pts: Vec<V2> = (0..=n)
        .map(|k| hf.pos + v2(hf.r, 0.0).rot(base + start + k as f32 / n as f32 * PI))
        .collect();
    let mid = hf.pos + v2(0.0, hf.r * 0.4).rot(base + start);
    cv.convex(mid, &pts, a.a(alpha));
    // The cut face.
    let d = v2(hf.r, 0.0).rot(base);
    cv.line(hf.pos - d * 0.92, hf.pos + d * 0.92, 4.0, b.a(alpha));
    if hf.kind == Kind::Log {
        cv.arc(
            hf.pos,
            hf.r * 0.5,
            1.5,
            base + start,
            base + start + PI,
            a.a(0.5 * alpha),
        );
    }
}
