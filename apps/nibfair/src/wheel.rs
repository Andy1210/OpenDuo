//! Pottery: the scene around nibfair::pottery (the wheel's rules).
//!
//! The pen's tip in the clay cuts (pressure = how much), its tilt is the
//! tool's angle (a straight rib when tilted, a round point upright), the
//! hovering pen beside the clay is a wet sponge, the eraser end adds clay.
//! The pen buzzes in step with the surface's roughness under it: a smooth
//! pot is quiet. "Fire it" scores the pot against the order.

use crate::gfx::{rgb, rgba, Canvas, Col, WHITE};
use crate::input::Input;
use crate::text::{Align, Font};
use crate::ui::{self, Fx, PKind, Rect, INK};
use crate::{Ctx, Go};
use nibfair::geom::{v2, V2};
use nibfair::haptic::{Pulse, Wave};
use nibfair::pottery::{self, Score, Tool, Wheel, HEIGHT, ORDERS, ROWS};
use nibfair::rng::Rng;
use nibfair::strings::{t, tf, Lang};
use nibfair::tween::{clamp01, ease_out_back, lerp};
use std::f32::consts::{PI, TAU};

const CLAY: Col = rgb(204, 122, 76);
const GLAZES: [Col; 8] = [
    rgb(70, 130, 200),
    rgb(90, 170, 120),
    rgb(230, 200, 150),
    rgb(60, 160, 170),
    rgb(200, 90, 80),
    rgb(120, 100, 180),
    rgb(225, 165, 60),
    rgb(90, 120, 150),
];

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Shaping,
    Firing(f32),
    Result(f32),
    Done(f32),
}

struct Lay {
    w: f32,
    h: f32,
    /// The words' and panels' column (ui::column); spanned, the pot stands in the right panel.
    mid: f32,
    span: f32,
    wide: bool,
    cx: f32,
    base: f32,
    r: f32,
    row: f32,
}

impl Lay {
    fn new(w: f32, h: f32) -> Lay {
        let (mid, span) = ui::column(w, h);
        let wide = w > h * 1.15;
        let (pot_x, pot_w) = if wide {
            (w * 0.75, w * 0.5)
        } else {
            (w / 2.0, w)
        };
        let r = (pot_w * 0.4).min(h * 0.5 / HEIGHT).min(220.0);
        let base = h * 0.79;
        Lay {
            w,
            h,
            mid,
            span,
            wide,
            cx: pot_x,
            base,
            r,
            row: r * HEIGHT / ROWS as f32,
        }
    }
    fn y(&self, row: f32) -> f32 {
        self.base - row * self.row
    }
    fn row_at(&self, y: f32) -> f32 {
        (self.base - y) / self.row
    }
    fn fire(&self) -> Rect {
        let bw = 170.0f32.min(self.span * 0.45);
        (self.cx - bw / 2.0, self.h - 62.0, bw, 48.0)
    }
    fn back(&self) -> Rect {
        (10.0, 10.0, 44.0, 44.0)
    }
    fn result_buttons(&self) -> (Rect, Rect) {
        let bw = (self.span * 0.36).min(200.0);
        let y = self.h * 0.76;
        (
            (self.mid + 8.0, y, bw, 50.0),
            (self.mid - bw - 8.0, y, bw, 50.0),
        )
    }
}

pub struct PotScene {
    wheel: Wheel,
    order: usize,
    target: Vec<f32>,
    scores: Vec<Option<Score>>,
    shapes: Vec<Option<Vec<f32>>>,
    mode: Mode,
    fx: Fx,
    t: f32,
    spin: f32,
    ghost: bool,
    tick_in: f32,
    speckles: Vec<(f32, f32, f32)>,
    sponge: f32,
    pen: (bool, bool, bool, V2, f32, V2),
    staged: bool,
    demo: bool,
    new_best: bool,
    live: Score,
    live_in: f32,
    shave_in: f32,
}

impl PotScene {
    pub fn new(cx: &mut Ctx) -> PotScene {
        let mut rng = Rng::new(cx.next_seed());
        let speckles = (0..140)
            .map(|_| {
                (
                    rng.range(0.0, ROWS as f32 - 1.0),
                    rng.range(0.0, TAU),
                    rng.range(0.6, 1.4),
                )
            })
            .collect();
        let target = pottery::target(&ORDERS[0]);
        let wheel = Wheel::new();
        let live = pottery::score(&wheel.r, &wheel.rough, &target);
        PotScene {
            wheel,
            order: 0,
            target,
            scores: vec![None; ORDERS.len()],
            shapes: vec![None; ORDERS.len()],
            mode: Mode::Shaping,
            fx: Fx::new(cx.next_seed()),
            t: 0.0,
            spin: 0.0,
            ghost: true,
            tick_in: 0.0,
            speckles,
            sponge: 0.0,
            pen: (false, false, false, v2(0.0, 0.0), 0.0, v2(0.0, 0.0)),
            staged: false,
            demo: false,
            new_best: false,
            live,
            live_in: 0.0,
            shave_in: 0.0,
        }
    }

    fn start_order(&mut self, i: usize) {
        self.order = i;
        self.target = pottery::target(&ORDERS[i]);
        self.wheel = Wheel::new();
        self.mode = Mode::Shaping;
        self.live = pottery::score(&self.wheel.r, &self.wheel.rough, &self.target);
    }

    pub fn update(&mut self, cx: &mut Ctx, inp: &Input, dt: f32) -> Go {
        let l = Lay::new(cx.w, cx.h);
        let now = cx.now;
        self.t += dt;
        self.spin += dt * 2.8;
        self.fx.update(dt);
        if inp.back || inp.tapped(l.back()) {
            return Go::Menu;
        }
        let p = &inp.pen;
        if !self.staged {
            self.pen = (p.near, p.down, p.eraser, p.pos, p.pressure, p.tilt);
        }
        if self.demo {
            let k = self.t * 0.7;
            let row = (k.sin() * 0.5 + 0.5) * (ROWS as f32 - 4.0) + 2.0;
            let r = self.target[row as usize] * l.r;
            self.pen = (
                true,
                true,
                false,
                v2(l.cx + r - 2.0, l.y(row)),
                0.5,
                v2(18.0 * (k * 0.5).sin(), 0.0),
            );
        }
        let (near, down, eraser, pos, pressure, tilt) = self.pen;
        match self.mode {
            Mode::Shaping => {
                if p.button_pressed {
                    self.ghost = !self.ghost;
                }
                if inp.tapped(l.fire()) && !down {
                    self.mode = Mode::Firing(0.0);
                    cx.buzz.pulse(now, Pulse::new(Wave::Press, 0.7, 2));
                    return Go::Stay;
                }
                let row = l.row_at(pos.y);
                let in_pot = (-2.0..ROWS as f32 + 2.0).contains(&row)
                    && !ui::inside(l.fire(), pos)
                    && !ui::inside(l.back(), pos);
                let dist = (pos.x - l.cx).abs() / l.r;
                let side = if pos.x >= l.cx { 1.0 } else { -1.0 };
                let mut moved = 0.0;
                self.sponge = 0.0;
                if down && in_pot {
                    // Along the pen's travel, so a quick stroke leaves no gaps.
                    let moves: Vec<_> = p.moves.iter().filter(|m| m.down).copied().collect();
                    let speed = moves.iter().map(|m| (m.to - m.from).len()).sum::<f32>()
                        / dt.max(1e-3)
                        / l.r;
                    if moves.is_empty() || self.staged || self.demo {
                        let tool = Tool {
                            row,
                            dist,
                            side,
                            pressure,
                            tilt: tilt.x,
                            eraser,
                            speed,
                        };
                        moved += self.wheel.carve(&tool, dt);
                    } else {
                        let total: f32 = moves.iter().map(|m| m.dt).sum();
                        for m in &moves {
                            let len = (m.to - m.from).len();
                            let steps = ((len / (l.row * 0.8)).ceil() as usize).clamp(1, 40);
                            for s in 1..=steps {
                                let q = m.from + (m.to - m.from) * (s as f32 / steps as f32);
                                let tool = Tool {
                                    row: l.row_at(q.y),
                                    dist: (q.x - l.cx).abs() / l.r,
                                    side: if q.x >= l.cx { 1.0 } else { -1.0 },
                                    pressure: m.pressure,
                                    tilt: tilt.x,
                                    eraser: m.eraser,
                                    speed,
                                };
                                moved += self
                                    .wheel
                                    .carve(&tool, dt * (m.dt / total.max(1e-4)) / steps as f32);
                            }
                        }
                    }
                } else if near && !down && !eraser && in_pot {
                    self.sponge = self.wheel.sponge(row, dist, dt);
                }
                // Shavings flying off the tool.
                if moved > 0.0 {
                    self.shave_in -= moved * 60.0;
                    while self.shave_in <= 0.0 {
                        self.shave_in += 1.0;
                        let dir = if eraser { -side } else { side };
                        let a = if dir > 0.0 { -0.4 } else { PI + 0.4 };
                        self.fx.spray(
                            pos,
                            a,
                            0.6,
                            1,
                            CLAY.darker(0.1),
                            160.0,
                            3.5,
                            PKind::Flake,
                            500.0,
                        );
                    }
                }
                // The buzz tells the roughness under the pen (quiet on a smooth surface).
                let touching = (down && in_pot && moved > 0.0)
                    || self.sponge > 0.05
                    || (down && in_pot && (dist - self.wheel.radius_at(row)).abs() < 0.1);
                if touching && (0.0..ROWS as f32).contains(&row) {
                    let rough = self.wheel.roughness_at(row);
                    if rough > 0.05 {
                        self.tick_in -= dt;
                        if self.tick_in <= 0.0 {
                            let k = clamp01(rough * 1.6);
                            self.tick_in = lerp(0.17, 0.05, k);
                            let wave = if down { Wave::Click } else { Wave::Hover };
                            cx.buzz.pulse(now, Pulse::new(wave, 0.15 + 0.85 * k, 0));
                        }
                    }
                }
                if self.sponge > 0.05 && self.fx.rng.chance(dt * 8.0 * self.sponge) {
                    self.fx.spray(
                        pos,
                        PI / 2.0,
                        0.3,
                        1,
                        rgba(170, 220, 255, 0.8),
                        40.0,
                        2.0,
                        PKind::Dot,
                        300.0,
                    );
                }
                self.live_in -= dt;
                if self.live_in <= 0.0 {
                    self.live_in = 0.15;
                    self.live = pottery::score(&self.wheel.r, &self.wheel.rough, &self.target);
                }
            }
            Mode::Firing(ft) => {
                let ft = ft + dt;
                if ft > 1.3 {
                    let s = pottery::score(&self.wheel.r, &self.wheel.rough, &self.target);
                    self.scores[self.order] = Some(s);
                    self.shapes[self.order] = Some(self.wheel.r.clone());
                    cx.save.best(&format!("pottery.{}", self.order), s.total);
                    cx.store();
                    self.mode = Mode::Result(0.0);
                    let c = v2(l.cx, l.h * 0.33);
                    if s.stars > 0 {
                        self.fx.burst(
                            c,
                            20 + 20 * s.stars as usize,
                            ui::GOLD,
                            320.0,
                            3.0,
                            PKind::Flake,
                            300.0,
                        );
                    }
                    cx.buzz.pulse(
                        now,
                        Pulse::new(
                            if s.stars >= 2 {
                                Wave::Success
                            } else {
                                Wave::Release
                            },
                            1.0,
                            3,
                        ),
                    );
                } else {
                    self.mode = Mode::Firing(ft);
                }
            }
            Mode::Result(rt) => {
                self.mode = Mode::Result(rt + dt);
                let (next, menu) = l.result_buttons();
                if rt > 0.5 && inp.tapped(next) {
                    if self.order + 1 < ORDERS.len() {
                        self.start_order(self.order + 1);
                    } else {
                        let total: u32 = self.scores.iter().flatten().map(|s| s.total).sum();
                        self.new_best = cx.save.best("best.pottery", total);
                        cx.store();
                        self.mode = Mode::Done(0.0);
                    }
                } else if rt > 0.5 && inp.tapped(menu) {
                    return Go::Menu;
                }
            }
            Mode::Done(dt0) => {
                self.mode = Mode::Done(dt0 + dt);
                let (again, menu) = l.result_buttons();
                if dt0 > 0.5 && inp.tapped(again) {
                    return Go::Pottery;
                }
                if dt0 > 0.5 && inp.tapped(menu) {
                    return Go::Menu;
                }
            }
        }
        Go::Stay
    }

    pub fn stage(&mut self, _cx: &mut Ctx, n: u32) {
        let l = Lay::new(538.0, 757.0);
        match n {
            1 | 2 => {
                self.start_order(3);
                // Rough work towards the vase, unfinished at the neck.
                for j in 0..ROWS {
                    let t = self.target[j];
                    let k = if j > 60 { 0.4 } else { 0.95 };
                    self.wheel.r[j] = lerp(1.0, t, k) + 0.02 * ((j as f32) * 0.9).sin();
                    self.wheel.rough[j] = if j > 40 { 0.35 } else { 0.05 };
                }
                self.staged = true;
                let row = 66.0;
                let r = self.wheel.r[66] * l.r;
                self.pen = (
                    true,
                    true,
                    false,
                    v2(l.cx + r - 3.0, l.y(row)),
                    0.6,
                    v2(22.0, 0.0),
                );
                self.live = pottery::score(&self.wheel.r, &self.wheel.rough, &self.target);
                if n == 2 {
                    for j in 0..ROWS {
                        self.wheel.r[j] = self.target[j] + 0.03 * ((j as f32) * 0.3).sin();
                        self.wheel.rough[j] = 0.04;
                    }
                    self.mode = Mode::Firing(1.25);
                    self.pen.1 = false;
                }
            }
            3 => {
                for i in 0..ORDERS.len() {
                    let tg = pottery::target(&ORDERS[i]);
                    let r: Vec<f32> = tg
                        .iter()
                        .enumerate()
                        .map(|(j, x)| x + 0.03 * ((j + i) as f32 * 0.4).sin())
                        .collect();
                    self.scores[i] = Some(pottery::score(&r, &vec![0.05; ROWS], &tg));
                    self.shapes[i] = Some(r);
                }
                self.order = ORDERS.len() - 1;
                self.mode = Mode::Done(2.0);
                self.staged = true;
            }
            100 => {
                self.demo = true;
                self.start_order(3);
            }
            _ => {}
        }
    }

    pub fn draw(&mut self, cx: &mut Ctx, inp: &Input) {
        let l = Lay::new(cx.w, cx.h);
        let lang = cx.lang;
        let cv = &mut cx.cv;
        let font = &mut cx.font;
        // The workshop.
        cv.rect_v(0.0, 0.0, l.w, l.h, rgb(250, 222, 186), rgb(226, 178, 132));
        cv.glow(
            v2(l.cx, l.base - l.r * 1.2),
            l.r * 2.4,
            rgba(255, 245, 220, 0.5),
        );
        for k in 0..6 {
            let x = l.w * (k as f32 + 0.5) / 6.0;
            cv.rect(x - 1.0, 0.0, 2.0, l.h, rgba(150, 100, 60, 0.06));
        }
        // The shelf of finished pots.
        let (sx0, sw) = (l.mid - l.span / 2.0 + 14.0, l.span - 28.0);
        let sy = if l.wide {
            l.h * 0.45
        } else {
            150.0f32.min(l.base - l.r * HEIGHT - 20.0)
        };
        cv.rect(sx0, sy, sw, 8.0, rgb(150, 95, 55));
        cv.rect(sx0, sy + 8.0, sw, 4.0, rgb(110, 70, 40));
        let slot = (sw - 12.0) / ORDERS.len() as f32;
        for i in 0..ORDERS.len() {
            let c = v2(sx0 + 6.0 + slot * (i as f32 + 0.5), sy);
            let hgt = (slot * 0.9).min(if l.wide { 70.0 } else { 52.0 });
            match &self.shapes[i] {
                Some(shape) => {
                    mini_pot(cv, c, hgt, shape, GLAZES[i]);
                    if let Some(s) = self.scores[i] {
                        for k in 0..s.stars {
                            cv.star(c + v2((k as f32 - 1.0) * 9.0, 12.0), 4.0, 0.0, ui::GOLD);
                        }
                    }
                }
                None => {
                    let tg = pottery::target(&ORDERS[i]);
                    mini_pot(
                        cv,
                        c,
                        hgt,
                        &tg,
                        if i == self.order {
                            rgba(255, 255, 255, 0.6)
                        } else {
                            rgba(120, 80, 50, 0.22)
                        },
                    );
                }
            }
        }

        // The wheel and its stand.
        let wr = l.r * 1.3;
        cv.rect(
            l.cx - 18.0,
            l.base + 10.0,
            36.0,
            l.h - l.base,
            rgb(90, 80, 85),
        );
        cv.ellipse(v2(l.cx, l.base + 14.0), wr, wr * 0.17, 0.0, rgb(60, 55, 62));
        cv.ellipse(
            v2(l.cx, l.base + 6.0),
            wr,
            wr * 0.17,
            0.0,
            rgb(130, 125, 135),
        );
        cv.ellipse(
            v2(l.cx, l.base + 4.0),
            wr * 0.92,
            wr * 0.13,
            0.0,
            rgb(160, 155, 165),
        );
        for k in 0..10 {
            let a = self.spin + k as f32 / 10.0 * TAU;
            if a.cos() > 0.0 {
                let p = v2(
                    l.cx + wr * 0.85 * a.sin(),
                    l.base + 4.0 + wr * 0.12 * a.cos(),
                );
                cv.rect(
                    p.x - 1.5,
                    p.y - 3.0,
                    3.0,
                    6.0,
                    rgba(60, 55, 62, 0.6 * a.cos()),
                );
            }
        }

        // The pot.
        let fired = match self.mode {
            Mode::Firing(ft) => clamp01(ft / 1.2),
            Mode::Result(_) | Mode::Done(_) => 1.0,
            _ => 0.0,
        };
        let glaze = GLAZES[self.order];
        let showing_done = matches!(self.mode, Mode::Done(_)) && !l.wide;
        if !showing_done {
            draw_pot(cv, &l, &self.wheel, self.spin, &self.speckles, fired, glaze);
        }
        // The order's outline.
        if self.ghost && matches!(self.mode, Mode::Shaping) {
            for side in [-1.0f32, 1.0] {
                for j in (0..ROWS - 1).step_by(2) {
                    let (a, b) = (self.target[j], self.target[j + 1]);
                    let ok = (self.wheel.r[j] - a).abs() < 0.03;
                    let col = if ok {
                        rgba(120, 230, 140, 0.95)
                    } else {
                        rgba(255, 255, 255, 0.85)
                    };
                    cv.line(
                        v2(l.cx + side * a * l.r, l.y(j as f32)),
                        v2(l.cx + side * b * l.r, l.y((j + 1) as f32)),
                        2.2,
                        col,
                    );
                }
            }
        }
        // The kiln's glow while firing.
        if let Mode::Firing(ft) = self.mode {
            let k = (ft * 6.0).sin().abs();
            cv.glow(
                v2(l.cx, l.base - l.r * 1.2),
                l.r * 2.6,
                rgba(255, 140, 40, 0.35 + 0.25 * k),
            );
        }

        // The tool.
        let (near, down, eraser, pos, pressure, tilt) = self.pen;
        if matches!(self.mode, Mode::Shaping) && (near || down) {
            if eraser {
                cv.glow(pos, 26.0, CLAY.a(0.4));
                cv.circle(pos, 10.0 + 6.0 * pressure, CLAY.lighter(0.1));
                cv.circle(pos + v2(-3.0, -3.0), 3.0, WHITE.a(0.4));
            } else if down {
                let a = tilt.x.clamp(-60.0, 60.0).to_radians();
                let dir = v2(a.sin(), -a.cos());
                let len = pottery::reach(tilt.x) * l.r;
                // The pen above it.
                cv.line(
                    pos + dir * 10.0,
                    pos + dir * 120.0,
                    7.0,
                    rgba(60, 60, 70, 0.25),
                );
                if pottery::rib(tilt.x) > 0.3 {
                    cv.line(pos - dir * len, pos + dir * len, 5.0, rgb(150, 160, 175));
                    cv.line(pos - dir * len, pos + dir * len, 2.0, rgb(230, 235, 240));
                } else {
                    cv.circle(pos, pottery::TIP * l.r * 0.6, rgb(150, 160, 175));
                }
                cv.circle(pos, 3.0, rgb(255, 120, 60).a(0.5 + 0.5 * pressure));
            } else if self.sponge > 0.05 {
                cv.glow(pos, 30.0, rgba(140, 200, 255, 0.4 * self.sponge));
                cv.ellipse(pos, 14.0, 10.0, 0.3, rgb(250, 220, 110));
                for k in 0..5 {
                    cv.circle(
                        pos + v2(-8.0 + 4.0 * k as f32, -2.0 + (k % 2) as f32 * 4.0),
                        1.6,
                        rgb(220, 180, 70),
                    );
                }
            } else {
                cv.ring(pos, 8.0, 2.0, INK.a(0.4));
            }
        }
        self.fx.draw(cv);
        self.fx.draw_pops(cv, font);

        // The top bar: back, the order, the live match.
        ui::back_button(cv, l.back(), inp.held_in(l.back()), rgb(190, 110, 60));
        let ot = tf(lang, "pot.order", &[&(self.order + 1), &ORDERS.len()]);
        font.draw(cv, &ot, l.mid, 22.0, 13.0, INK.a(0.6), Align::Center);
        let name = tf(lang, "pot.make", &[&t(lang, ORDERS[self.order].key)]);
        font.draw_shadow(
            cv,
            &name,
            l.mid,
            46.0,
            21.0,
            INK,
            Align::Center,
            WHITE.a(0.5),
        );
        if matches!(self.mode, Mode::Shaping) {
            let m = self.live.total;
            let col = if m >= 90 {
                rgb(60, 170, 90)
            } else if m >= 78 {
                rgb(120, 160, 60)
            } else if m >= 60 {
                rgb(200, 150, 50)
            } else {
                rgb(190, 90, 70)
            };
            let bw = 74.0;
            let x = l.w - bw - 12.0;
            cv.rrect(x, 17.0, bw, 30.0, 15.0, INK.a(0.15));
            cv.rrect(
                x,
                17.0,
                (bw * m as f32 / 100.0).max(30.0),
                30.0,
                15.0,
                col.a(0.85),
            );
            font.draw(
                cv,
                &format!("{m}%"),
                x + bw / 2.0,
                32.0,
                15.0,
                WHITE,
                Align::Center,
            );
            // Roughness under the pen, as a word.
            let row = l.row_at(pos.y);
            if (near || down) && (0.0..ROWS as f32).contains(&row) {
                let r = self.wheel.roughness_at(row);
                if r > 0.12 {
                    font.draw(
                        cv,
                        t(lang, "pot.rough"),
                        pos.x + 18.0,
                        pos.y - 18.0,
                        13.0,
                        rgb(150, 70, 40).a(clamp01(r * 2.5)),
                        Align::Left,
                    );
                }
            }
            // The hint and the fire button.
            let keys = [
                "pot.hint.cut",
                "pot.hint.tilt",
                "pot.hint.smooth",
                "pot.hint.eraser",
            ];
            let k = keys[(self.t / 5.0) as usize % keys.len()];
            let txt = t(lang, k);
            let lines = font.wrap(txt, l.span - 40.0, 14.0);
            let y = if l.wide {
                l.h * 0.7
            } else {
                l.fire().1 - 14.0 - (lines.len() as f32 - 1.0) * 18.0
            };
            for (i, ln) in lines.iter().enumerate() {
                font.draw_shadow(
                    cv,
                    ln,
                    l.mid,
                    y + i as f32 * 18.0,
                    14.0,
                    INK.a(0.85),
                    Align::Center,
                    WHITE.a(0.5),
                );
            }
            ui::button(
                cv,
                font,
                l.fire(),
                t(lang, "pot.fire"),
                rgb(230, 110, 50),
                inp.held_in(l.fire()),
                19.0,
            );
        }
        match self.mode {
            Mode::Result(rt) => self.draw_result(cv, font, &l, lang, inp, rt),
            Mode::Done(dt) => self.draw_done(cv, font, &l, lang, inp, dt),
            _ => {}
        }
        if !inp.pen.seen
            && !self.staged
            && !self.demo
            && self.t > 1.5
            && matches!(self.mode, Mode::Shaping)
        {
            font.draw(
                cv,
                t(lang, "pen.missing"),
                l.mid,
                120.0,
                13.0,
                INK.a(0.6),
                Align::Center,
            );
        }
    }

    fn draw_result(
        &self,
        cv: &mut Canvas,
        font: &mut Font,
        l: &Lay,
        lang: Lang,
        inp: &Input,
        rt: f32,
    ) {
        let Some(s) = self.scores[self.order] else {
            return;
        };
        let k = ease_out_back(clamp01(rt / 0.4));
        if !l.wide {
            cv.rect(0.0, 0.0, l.w, l.h, INK.a(0.35 * clamp01(rt * 3.0)));
        }
        let pw = (l.span - 40.0).min(420.0);
        let ph = l.h * 0.6;
        let ph2 = (ph * 0.42).min(220.0);
        let r = (
            l.mid - pw / 2.0,
            l.h * 0.11 + (1.0 - k) * 60.0,
            pw,
            128.0 + ph2 + 90.0,
        );
        ui::panel(cv, r, rgb(255, 250, 238), rgb(242, 228, 205), 22.0);
        font.draw_outline(
            cv,
            &format!("{}", s.total),
            l.mid,
            r.1 + 42.0,
            44.0,
            rgb(255, 200, 80),
            Align::Center,
            INK,
        );
        for i in 0..3 {
            let c = v2(l.mid + (i as f32 - 1.0) * 46.0, r.1 + 94.0);
            ui::rating_star(
                cv,
                c,
                18.0,
                (i as u8) < s.stars,
                clamp01((rt - 0.3 - 0.2 * i as f32) / 0.3),
            );
        }
        // The pot as fired, the order's outline over it.
        let foot = v2(l.mid, r.1 + 128.0 + ph2);
        cv.ellipse(foot + v2(0.0, 2.0), ph2 * 0.45, ph2 * 0.06, 0.0, INK.a(0.2));
        if let Some(shape) = &self.shapes[self.order] {
            mini_pot(cv, foot, ph2, shape, GLAZES[self.order]);
        }
        let rs = ph2 / HEIGHT;
        for side in [-1.0f32, 1.0] {
            for j in (0..ROWS - 1).step_by(3) {
                let j2 = (j + 2).min(ROWS - 1);
                let (a, b) = (self.target[j] * rs, self.target[j2] * rs);
                let y0 = foot.y - ph2 * j as f32 / (ROWS - 1) as f32;
                let y1 = foot.y - ph2 * j2 as f32 / (ROWS - 1) as f32;
                cv.line(
                    v2(foot.x + side * a, y0),
                    v2(foot.x + side * b, y1),
                    1.8,
                    WHITE.a(0.9),
                );
            }
        }
        let bars = [("pot.shape", s.shape), ("pot.smooth", s.smooth)];
        for (i, (key, v)) in bars.iter().enumerate() {
            let y = foot.y + 30.0 + i as f32 * 30.0;
            let pct = (v * 100.0).round() as u32;
            font.draw(
                cv,
                &tf(lang, key, &[&pct]),
                r.0 + 22.0,
                y,
                14.0,
                INK.a(0.75),
                Align::Left,
            );
            let bx = r.0 + pw * 0.5;
            let bw = pw * 0.44;
            cv.rrect(bx, y - 7.0, bw, 14.0, 7.0, INK.a(0.12));
            cv.rrect(
                bx,
                y - 7.0,
                bw * clamp01(v * clamp01(rt / 0.8)),
                14.0,
                7.0,
                rgb(90, 180, 110),
            );
        }
        let (next, menu) = l.result_buttons();
        let label = if self.order + 1 < ORDERS.len() {
            t(lang, "pot.next")
        } else {
            t(lang, "pot.done")
        };
        ui::button(
            cv,
            font,
            next,
            label,
            rgb(70, 160, 90),
            inp.held_in(next),
            17.0,
        );
        ui::button(
            cv,
            font,
            menu,
            t(lang, "common.back"),
            rgb(170, 110, 70),
            inp.held_in(menu),
            17.0,
        );
    }

    fn draw_done(
        &self,
        cv: &mut Canvas,
        font: &mut Font,
        l: &Lay,
        lang: Lang,
        inp: &Input,
        dt: f32,
    ) {
        let k = ease_out_back(clamp01(dt / 0.4));
        let pw = (l.span - 32.0).min(460.0);
        let r = (
            l.mid - pw / 2.0,
            l.h * 0.2 + (1.0 - k) * 60.0,
            pw,
            l.h * 0.52,
        );
        ui::panel(cv, r, rgb(255, 250, 238), rgb(242, 228, 205), 22.0);
        font.draw(
            cv,
            t(lang, "pot.done"),
            l.mid,
            r.1 + 34.0,
            22.0,
            INK,
            Align::Center,
        );
        let total: u32 = self.scores.iter().flatten().map(|s| s.total).sum();
        font.draw_outline(
            cv,
            &tf(lang, "pot.total", &[&total]),
            l.mid,
            r.1 + 80.0,
            36.0,
            rgb(255, 200, 80),
            Align::Center,
            INK,
        );
        if self.new_best {
            font.draw(
                cv,
                t(lang, "common.newbest"),
                l.mid,
                r.1 + 118.0,
                17.0,
                rgb(220, 70, 90),
                Align::Center,
            );
        }
        let per_row = 4;
        let slot = (pw - 30.0) / per_row as f32;
        let ph = ((r.3 - 150.0) / 2.0 - 26.0).min(slot * 1.3);
        for i in 0..ORDERS.len() {
            let row = (i / per_row) as f32;
            let c = v2(
                r.0 + 15.0 + slot * ((i % per_row) as f32 + 0.5),
                r.1 + 140.0 + ph + row * (ph + 30.0),
            );
            if let Some(shape) = &self.shapes[i] {
                mini_pot(cv, c, ph, shape, GLAZES[i]);
            }
            if let Some(s) = self.scores[i] {
                font.draw(
                    cv,
                    &s.total.to_string(),
                    c.x,
                    c.y + 13.0,
                    12.0,
                    INK.a(0.7),
                    Align::Center,
                );
            }
        }
        let (again, menu) = l.result_buttons();
        ui::button(
            cv,
            font,
            again,
            t(lang, "common.again"),
            rgb(70, 160, 90),
            inp.held_in(again),
            17.0,
        );
        ui::button(
            cv,
            font,
            menu,
            t(lang, "common.back"),
            rgb(170, 110, 70),
            inp.held_in(menu),
            17.0,
        );
    }
}

/// The pot on the wheel: rows of shaded strips, the roughness making the
/// outline wobble as it spins, speckles turning with it.
fn draw_pot(
    cv: &mut Canvas,
    l: &Lay,
    w: &Wheel,
    spin: f32,
    speckles: &[(f32, f32, f32)],
    fired: f32,
    glaze: Col,
) {
    let base_col = CLAY.mix(glaze, fired * 0.85);
    let cols = [-1.0f32, -0.62, -0.28, 0.2, 0.62, 1.0];
    let light = [0.55f32, 0.85, 1.05, 0.92, 0.72, 0.5];
    let wob = |j: usize| -> f32 {
        let r = w.rough[j];
        r * 0.03
            * ((j as f32 * 0.55 + spin * 2.0).sin() + 0.5 * (j as f32 * 1.3 - spin * 3.0).sin())
    };
    // The shadow on the wheel.
    cv.ellipse(
        v2(l.cx, l.base + 3.0),
        w.r[0] * l.r * 1.1,
        w.r[0] * l.r * 0.16,
        0.0,
        INK.a(0.3),
    );
    let shade = |j: usize, k: usize| -> Col {
        let rough = w.rough[j];
        let mut c = base_col.darker(0.12 * rough);
        let lk = light[k];
        c = if lk > 1.0 {
            c.lighter((lk - 1.0) * 2.5 * (1.0 - rough) + 0.06)
        } else {
            c.darker(1.0 - lk)
        };
        c
    };
    for j in 0..ROWS - 1 {
        let (y0, y1) = (l.y(j as f32), l.y((j + 1) as f32) - 0.5);
        let (r0, r1) = ((w.r[j] + wob(j)) * l.r, (w.r[j + 1] + wob(j + 1)) * l.r);
        for k in 0..cols.len() - 1 {
            let (a, b) = (cols[k], cols[k + 1]);
            cv.quad4(
                [
                    v2(l.cx + a * r0, y0),
                    v2(l.cx + b * r0, y0),
                    v2(l.cx + b * r1, y1),
                    v2(l.cx + a * r1, y1),
                ],
                [
                    shade(j, k),
                    shade(j, k + 1),
                    shade(j + 1, k + 1),
                    shade(j + 1, k),
                ],
            );
        }
    }
    // The opening at the top.
    let top = ROWS - 1;
    let rt = w.r[top] * l.r;
    let yt = l.y(top as f32);
    cv.ellipse(v2(l.cx, yt), rt, rt * 0.16, 0.0, base_col.lighter(0.1));
    cv.ellipse(
        v2(l.cx, yt + 1.0),
        rt * 0.72,
        rt * 0.11,
        0.0,
        base_col.darker(0.55),
    );
    // Speckles turning with the wheel (thicker where it is rough).
    for (row, a0, s) in speckles {
        let j = (*row as usize).min(ROWS - 1);
        let a = a0 + spin;
        let c = a.cos();
        if c <= 0.0 {
            continue;
        }
        let rough = w.rough[j];
        let x = l.cx + w.r[j] * l.r * a.sin() * 0.97;
        let alpha = c * (0.12 + 0.7 * rough) * (1.0 - fired * 0.7);
        cv.circle(
            v2(x, l.y(*row)),
            (0.9 + 1.4 * rough) * s,
            base_col.darker(0.45).a(alpha),
        );
    }
    // A wet sheen line that runs up the smooth parts.
    if fired > 0.0 {
        cv.circle(
            v2(l.cx - w.r[ROWS / 2] * l.r * 0.35, l.y(ROWS as f32 * 0.6)),
            6.0,
            WHITE.a(0.35 * fired),
        );
    }
}

/// A small pot (the shelf, the order, the results), `h` tall, its foot at `c`.
fn mini_pot(cv: &mut Canvas, c: V2, h: f32, shape: &[f32], col: Col) {
    let n = shape.len();
    let rs = h / HEIGHT;
    let step = 4;
    let mut j = 0;
    while j + step < n {
        let (y0, y1) = (
            c.y - h * j as f32 / (n - 1) as f32,
            c.y - h * (j + step) as f32 / (n - 1) as f32,
        );
        let (a, b) = (shape[j] * rs, shape[j + step] * rs);
        cv.quad4(
            [
                v2(c.x - a, y0),
                v2(c.x + a, y0),
                v2(c.x + b, y1),
                v2(c.x - b, y1),
            ],
            [
                col.darker(0.2),
                col.lighter(0.1),
                col.lighter(0.1),
                col.darker(0.2),
            ],
        );
        j += step;
    }
}
