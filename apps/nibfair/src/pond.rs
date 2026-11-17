//! Fishing: the scene around nibfair::fish (the pond's rules).
//!
//! The pen hovering over the water is the rod: where it hovers, down the
//! screen, is how deep the bait hangs (the hover distance instead, on a pen
//! and digitizer that report one). Touching aims a cast (the tilt swings
//! the arc), lifting casts. A bite buzzes the pen: tap to strike, then press
//! to reel, watching the tension.

use crate::gfx::{rgb, rgba, Canvas, Col, WHITE};
use crate::input::Input;
use crate::text::{Align, Font};
use crate::ui::{self, Fx, PKind, Rect, INK};
use crate::{Ctx, Go};
use nibfair::fish::{
    self, Catch, Kind, Phase, Pond, PondEvent, PondInput, FLY_TIME, ROUND_TIME, SLACK_TENSION,
    SNAP_TENSION,
};
use nibfair::geom::{v2, V2};
use nibfair::haptic::{Pulse, Wave};
use nibfair::strings::{t, tf, Lang};
use nibfair::tween::{approach, clamp01, ease_out_back, lerp};
use std::f32::consts::{PI, TAU};

/// The pen as the scene last saw it (staged for the screenshots).
#[derive(Clone, Copy, Default)]
struct PenView {
    near: bool,
    down: bool,
    pos: V2,
    pressure: f32,
    tilt: V2,
}

struct Lay {
    w: f32,
    h: f32,
    /// The words' column (ui::column).
    mid: f32,
    span: f32,
    water_y: f32,
    bottom_y: f32,
    wx0: f32,
    wx1: f32,
    rod_base: V2,
    rod_tip: V2,
    s: f32,
}

impl Lay {
    fn new(w: f32, h: f32) -> Lay {
        let water_y = h * 0.30;
        let s = (h / 757.0).min(w / 538.0 * 1.2).max(0.6);
        let (mid, span) = ui::column(w, h);
        Lay {
            w,
            h,
            mid,
            span,
            water_y,
            bottom_y: h - 44.0 * s,
            wx0: w * 0.21,
            wx1: w - 14.0,
            rod_base: v2(w * 0.07, water_y - 18.0 * s),
            rod_tip: v2(w * 0.19, water_y - h * 0.12),
            s,
        }
    }
    fn x(&self, x: f32) -> f32 {
        self.wx0 + x * (self.wx1 - self.wx0)
    }
    fn y(&self, d: f32) -> f32 {
        self.water_y
            + 14.0 * self.s
            + d * (self.bottom_y - 22.0 * self.s - self.water_y - 14.0 * self.s)
    }
    fn to_x(&self, px: f32) -> f32 {
        (px - self.wx0) / (self.wx1 - self.wx0)
    }
    fn to_depth(&self, py: f32) -> f32 {
        (py - self.y(0.0)) / (self.y(1.0) - self.y(0.0))
    }
    fn back(&self) -> Rect {
        (10.0, 10.0, 44.0, 44.0)
    }
}

pub struct FishScene {
    pond: Pond,
    fx: Fx,
    t: f32,
    pen: PenView,
    staged: bool,
    demo: bool,
    /// The pen's tilt when it touched (None until the digitizer reports one).
    tilt0: Option<f32>,
    bob: f32,
    bite_flash: f32,
    message: Option<(String, f32, Col)>,
    landed_fish: Option<(Kind, f32)>,
    finished: bool,
    new_best: bool,
    strain_in: f32,
    bubble_in: f32,
    max_dist: f32,
    end_t: f32,
    weeds: Vec<(f32, f32, f32)>,
}

impl FishScene {
    pub fn new(cx: &mut Ctx) -> FishScene {
        let seed = cx.next_seed();
        let mut fx = Fx::new(seed ^ 0x55);
        let weeds = (0..9)
            .map(|_| {
                (
                    fx.rng.range(0.0, 1.0),
                    fx.rng.range(0.5, 1.0),
                    fx.rng.range(0.0, TAU),
                )
            })
            .collect();
        FishScene {
            pond: Pond::new(seed),
            fx,
            t: 0.0,
            pen: PenView::default(),
            staged: false,
            demo: false,
            tilt0: None,
            bob: 0.0,
            bite_flash: 0.0,
            message: None,
            landed_fish: None,
            finished: false,
            new_best: false,
            strain_in: 0.0,
            bubble_in: 0.0,
            max_dist: 0.0,
            end_t: 0.0,
            weeds,
        }
    }

    fn say(&mut self, s: String, col: Col) {
        self.message = Some((s, 1.6, col));
    }

    fn end_buttons(l: &Lay) -> (Rect, Rect) {
        let bw = (l.span * 0.36).min(200.0);
        let y = l.h * 0.80;
        let y = y.min(l.h - 70.0);
        ((l.mid - bw - 8.0, y, bw, 50.0), (l.mid + 8.0, y, bw, 50.0))
    }

    pub fn update(&mut self, cx: &mut Ctx, inp: &Input, dt: f32) -> Go {
        let l = Lay::new(cx.w, cx.h);
        let lang = cx.lang;
        self.t += dt;
        self.fx.update(dt);
        self.bite_flash = (self.bite_flash - dt).max(0.0);
        if let Some(m) = &mut self.message {
            m.1 -= dt;
            if m.1 <= 0.0 {
                self.message = None;
            }
        }
        if inp.back || inp.tapped(l.back()) {
            return Go::Menu;
        }
        if self.finished {
            self.end_t += dt;
            let (again, menu) = Self::end_buttons(&l);
            if inp.tapped(again) && self.end_t > 0.5 {
                return Go::Fishing;
            }
            if inp.tapped(menu) && self.end_t > 0.5 {
                return Go::Menu;
            }
            return Go::Stay;
        }
        let p = &inp.pen;
        if !self.staged {
            self.pen = PenView {
                near: p.near,
                down: p.down,
                pos: p.pos,
                pressure: p.pressure,
                tilt: p.tilt,
            };
        }
        if self.demo {
            // The bench: a pen that hovers back and forth over the water.
            let k = self.t * 0.4;
            self.pen = PenView {
                near: true,
                down: false,
                pos: v2(l.x(0.5 + 0.4 * k.sin()), l.y(0.5 + 0.45 * (k * 1.3).cos())),
                ..self.pen
            };
        }
        let on_back = ui::inside(l.back(), p.pos);
        let pressed = p.pressed && !on_back;
        // The tilt is measured from how the pen was held when it touched: the first tilt
        // after the touch (the digitizer may report none in the touch's first report).
        if pressed {
            self.tilt0 = None;
        }
        if self.pen.down && self.tilt0.is_none() && (self.pen.tilt.x, self.pen.tilt.y) != (0.0, 0.0)
        {
            self.tilt0 = Some(self.pen.tilt.x);
        }
        // Where the bait should hang: the hover distance when the pen has one, else the hover's height.
        let mut depth = None;
        if self.pen.near && !self.pen.down {
            depth = Some(match p.distance {
                Some(d) => {
                    self.max_dist = self.max_dist.max(d).max(0.05);
                    1.0 - (d / self.max_dist).clamp(0.0, 1.0)
                }
                None => l.to_depth(self.pen.pos.y).clamp(0.0, 0.98),
            });
        }
        let swing = match self.tilt0 {
            Some(t0) if self.pen.down => self.pen.tilt.x - t0,
            _ => 0.0,
        };
        let aim = fish::aim(l.to_x(self.pen.pos.x), swing);
        let pin = PondInput {
            depth,
            tip: self.pen.down && !on_back,
            tip_pressed: pressed,
            tip_released: p.released,
            pressure: self.pen.pressure,
            aim,
            reel_in: p.button_pressed,
        };
        let events = self.pond.update(dt, &pin);
        let now = cx.now;
        for e in events {
            self.event(cx, &l, lang, now, e);
        }
        // While the line strains: a warning buzz now and then.
        if let Some((_, reel)) = self.pond.fight() {
            if reel.tension > SNAP_TENSION * 0.92 {
                self.strain_in -= dt;
                if self.strain_in <= 0.0 {
                    self.strain_in = 0.22;
                    cx.buzz.pulse(now, Pulse::new(Wave::Release, 0.85, 1));
                }
            } else {
                self.strain_in = 0.0;
            }
        }
        // Bubbles from the bottom.
        self.bubble_in -= dt;
        if self.bubble_in <= 0.0 {
            self.bubble_in = self.fx.rng.range(0.3, 0.9);
            let x = self.fx.rng.range(l.wx0, l.wx1);
            self.bubble(v2(x, l.bottom_y - 4.0));
        }
        self.fx
            .parts
            .retain(|p| p.kind != PKind::Bubble || p.pos.y > l.water_y + 4.0);
        let target = match self.pond.phase {
            Phase::Fishing
                if self
                    .pond
                    .engaged
                    .and_then(|id| self.pond.fish_by_id(id))
                    .is_some_and(|f| matches!(f.state, fish::FishState::Bite { .. })) =>
            {
                1.0
            }
            _ => 0.0,
        };
        self.bob = approach(self.bob, target, 14.0, dt);
        if self.pond.over
            && !matches!(self.pond.phase, Phase::Fight { .. } | Phase::Landed { .. })
            && !self.finished
        {
            self.finished = true;
            self.end_t = 0.0;
            self.new_best = cx.save.best("best.fishing", self.pond.score) && self.pond.score > 0;
            for c in &self.pond.log {
                let k = fish::info(c.kind)
                    .key
                    .trim_start_matches("fish.")
                    .to_owned();
                let n = cx.save.num(&format!("fish.{k}.count"));
                cx.save.set(&format!("fish.{k}.count"), n + 1);
                if c.size > cx.save.float(&format!("fish.{k}.max")) {
                    cx.save
                        .set(&format!("fish.{k}.max"), format!("{:.1}", c.size));
                }
            }
            cx.store();
        }
        Go::Stay
    }

    fn bubble(&mut self, p: V2) {
        let life = self.fx.rng.range(2.0, 4.0);
        let size = self.fx.rng.range(1.5, 4.0);
        let vx = self.fx.rng.range(-6.0, 6.0);
        let vy = -self.fx.rng.range(25.0, 45.0);
        self.fx.add(ui::Particle {
            pos: p,
            vel: v2(vx, vy),
            life,
            max: life,
            size,
            col: rgba(220, 245, 255, 0.7),
            kind: PKind::Bubble,
            rot: 0.0,
            spin: 0.0,
            grav: 0.0,
            drag: 0.0,
        });
    }

    fn event(&mut self, cx: &mut Ctx, l: &Lay, lang: Lang, now: f64, e: PondEvent) {
        let bait_at = self.pond.bait.map(|b| v2(l.x(b.x), l.y(b.depth)));
        match e {
            PondEvent::Cast => cx.buzz.pulse(now, Pulse::new(Wave::Press, 0.5, 1)),
            PondEvent::Splash { x } => {
                let p = v2(l.x(x), l.water_y);
                self.fx.spray(
                    p,
                    -PI / 2.0,
                    0.9,
                    16,
                    rgba(210, 240, 255, 0.95),
                    160.0 * l.s,
                    2.6 * l.s,
                    PKind::Dot,
                    420.0,
                );
                self.fx.ring(p, 22.0 * l.s, WHITE, 0.7);
                cx.buzz.pulse(now, Pulse::new(Wave::Click, 0.5, 1));
            }
            PondEvent::Nibble { strength } => {
                if let Some(b) = bait_at {
                    self.fx
                        .ring(v2(b.x, l.water_y), 12.0 * l.s, WHITE.a(0.8), 0.5);
                }
                cx.buzz
                    .pulse(now, Pulse::new(Wave::Hover, 0.35 + 0.5 * strength, 1));
            }
            PondEvent::Bite => {
                self.bite_flash = 1.0;
                if let Some(b) = bait_at {
                    self.fx
                        .ring(v2(b.x, l.water_y), 30.0 * l.s, rgb(255, 230, 120), 0.6);
                    self.fx.spray(
                        v2(b.x, l.water_y),
                        -PI / 2.0,
                        0.6,
                        10,
                        rgba(210, 240, 255, 0.9),
                        120.0,
                        2.2,
                        PKind::Dot,
                        400.0,
                    );
                }
                cx.buzz.pulse(now, Pulse::new(Wave::Success, 1.0, 3));
            }
            PondEvent::TooEarly => {
                self.say(t(lang, "fish.early").to_owned(), rgb(255, 200, 120));
                cx.buzz.pulse(now, Pulse::new(Wave::Release, 0.5, 1));
            }
            PondEvent::Stolen => {
                self.say(t(lang, "fish.stolen").to_owned(), rgb(255, 200, 120));
                cx.buzz.pulse(now, Pulse::new(Wave::Release, 0.5, 1));
            }
            PondEvent::Spooked => {
                self.say(t(lang, "fish.spooked").to_owned(), rgb(200, 230, 255));
            }
            PondEvent::Hooked { .. } => {
                if let Some(b) = bait_at {
                    self.fx.spray(
                        v2(b.x, l.water_y),
                        -PI / 2.0,
                        1.0,
                        22,
                        rgba(210, 240, 255, 0.95),
                        220.0,
                        3.0,
                        PKind::Dot,
                        450.0,
                    );
                }
                self.fx.shake = 0.5;
                cx.buzz.pulse(now, Pulse::new(Wave::Press, 1.0, 2));
            }
            PondEvent::Surge => {
                self.fx.shake = self.fx.shake.max(0.35);
            }
            PondEvent::Strain => {
                self.say(t(lang, "fish.easy").to_owned(), rgb(255, 120, 100));
            }
            PondEvent::ReelTick { tension } => {
                cx.buzz.pulse(
                    now,
                    Pulse::new(Wave::Click, 0.2 + 0.5 * tension.min(1.0), 0),
                );
            }
            PondEvent::Snapped => {
                self.say(t(lang, "fish.snapped").to_owned(), rgb(255, 110, 100));
                self.fx.shake = 0.8;
                self.fx.flash = 0.6;
                self.fx.flash_col = rgb(255, 80, 60);
                cx.buzz.pulse(now, Pulse::new(Wave::Error, 1.0, 3));
            }
            PondEvent::Escaped => {
                self.say(t(lang, "fish.escaped").to_owned(), rgb(200, 230, 255));
                cx.buzz.pulse(now, Pulse::new(Wave::Release, 0.6, 2));
            }
            PondEvent::Caught(c) => {
                self.landed_fish = Some((c.kind, c.size));
                let p = v2(l.rod_tip.x + 30.0, l.water_y - 40.0);
                self.fx.spray(
                    v2(l.wx0, l.water_y),
                    -PI / 2.0,
                    1.2,
                    26,
                    rgba(210, 240, 255, 0.95),
                    260.0,
                    3.0,
                    PKind::Dot,
                    450.0,
                );
                let gold = c.kind == Kind::Goldfin;
                self.fx.burst(
                    p,
                    if gold { 60 } else { 30 },
                    if gold {
                        rgb(255, 210, 60)
                    } else {
                        rgb(255, 240, 160)
                    },
                    260.0,
                    3.0,
                    PKind::Flake,
                    300.0,
                );
                self.fx.popup(
                    p + v2(40.0, 0.0),
                    format!("+{}", c.points),
                    rgb(255, 230, 110),
                    30.0,
                );
                cx.buzz.pulse(now, Pulse::new(Wave::Success, 1.0, 3));
            }
            PondEvent::TimeUp => {
                self.say(t(lang, "fish.timeup").to_owned(), rgb(255, 210, 160));
            }
        }
    }

    /// The screenshots' and the bench's set pieces.
    pub fn stage(&mut self, _cx: &mut Ctx, n: u32) {
        let l = Lay::new(538.0, 757.0);
        let _ = &l;
        match n {
            1 => {
                self.staged = true;
                self.pond.phase = Phase::Aim {
                    land: 0.62,
                    held: 0.3,
                };
                // Touched upright, then leant 14 degrees right: the arc reaches past the middle.
                self.pen = PenView {
                    near: true,
                    down: true,
                    pos: v2(l.x(0.5), l.y(0.2)),
                    pressure: 0.4,
                    tilt: v2(14.0, 0.0),
                };
                self.tilt0 = Some(0.0);
                self.pond.time_left = ROUND_TIME * 0.85;
            }
            2 => {
                self.staged = true;
                self.pond.phase = Phase::Fishing;
                self.pond.bait = Some(fish::Bait {
                    x: 0.55,
                    depth: 0.42,
                    target: 0.42,
                    speed: 0.0,
                });
                self.pen = PenView {
                    near: true,
                    down: false,
                    pos: v2(l.x(0.78), l.y(0.42)),
                    ..PenView::default()
                };
                self.pond.time_left = ROUND_TIME * 0.55;
                if let Some(f) = self.pond.fish.first_mut() {
                    f.kind = Kind::Trout;
                    f.size = 44.0;
                    f.x = 0.5;
                    f.depth = 0.42;
                    f.dir = 1.0;
                    f.vx = 0.0;
                    f.state = fish::FishState::Nibble { left: 2, t: 9.0 };
                    let id = f.id;
                    self.pond.engaged = Some(id);
                }
            }
            3 => {
                self.staged = true;
                let id = self.pond.fish[0].id;
                {
                    let f = &mut self.pond.fish[0];
                    f.kind = Kind::Pike;
                    f.size = 80.0;
                    f.state = fish::FishState::Hooked;
                }
                let mut reel = fish::Reel::new(Kind::Pike, 80.0, 0.9, 3);
                reel.line = 0.55;
                reel.tension = 0.78;
                self.pond.phase = Phase::Fight {
                    fish: id,
                    reel,
                    from: (0.85, 0.7),
                };
                self.pond.engaged = Some(id);
                self.pen = PenView {
                    near: true,
                    down: true,
                    pos: v2(l.x(0.6), l.y(0.3)),
                    pressure: 0.55,
                    tilt: v2(0.0, 0.0),
                };
                self.pond.time_left = ROUND_TIME * 0.3;
            }
            4 => {
                self.pond.score = 412;
                for (k, s) in [
                    (Kind::Perch, 24.0),
                    (Kind::Trout, 47.0),
                    (Kind::Pike, 81.0),
                    (Kind::Perch, 30.0),
                    (Kind::Boot, 41.0),
                    (Kind::Goldfin, 50.0),
                ] {
                    self.pond.log.push(Catch {
                        kind: k,
                        size: s,
                        points: fish::points_for(k, s),
                    });
                }
                self.pond.time_left = 0.0;
                self.pond.over = true;
                self.finished = true;
                self.end_t = 2.0;
            }
            100 => {
                self.demo = true;
                self.pond.phase = Phase::Fishing;
                self.pond.bait = Some(fish::Bait {
                    x: 0.5,
                    depth: 0.4,
                    target: 0.4,
                    speed: 0.0,
                });
            }
            _ => {}
        }
    }

    pub fn draw(&mut self, cx: &mut Ctx, inp: &Input) {
        let l = Lay::new(cx.w, cx.h);
        let lang = cx.lang;
        let shake = self.fx.shake_offset();
        let cv = &mut cx.cv;
        let font = &mut cx.font;
        let progress = 1.0 - self.pond.time_left / ROUND_TIME;
        draw_sky(cv, &l, progress, self.t);
        draw_water(cv, &l, progress, self.t, &self.weeds);
        let base = cv.v.len();

        // The fish.
        let hooked = self.pond.fight().map(|(f, _)| f.id);
        for f in &self.pond.fish {
            let info = fish::info(f.kind);
            let pos = v2(l.x(f.x), l.y(f.depth));
            let len = (18.0 + f.size * 0.62) * l.s;
            let thrash = if Some(f.id) == hooked { 1.0 } else { 0.0 };
            draw_fish(
                cv,
                pos,
                f.dir,
                len,
                info,
                f.wiggle + thrash * self.t * 20.0,
                f.shown,
                thrash,
            );
        }

        // The line, the bobber, the bait.
        let tip = self.rod_tip(&l);
        draw_rod(cv, &l, tip);
        match &self.pond.phase {
            Phase::Aim { land, .. } => {
                let to = v2(l.x(*land), l.water_y);
                let n = 18;
                for k in 0..n {
                    let u = k as f32 / n as f32;
                    let p = arc_point(tip, to, u);
                    cv.circle(
                        p,
                        2.2 * l.s,
                        WHITE.a(0.4 + 0.6 * ((self.t * 6.0 - u * 8.0).sin() * 0.5 + 0.5)),
                    );
                }
                let pulse = 0.5 + 0.5 * (self.t * 5.0).sin();
                cv.ring(to, (12.0 + 4.0 * pulse) * l.s, 2.5, rgb(255, 230, 120));
                cv.ring(to, 5.0 * l.s, 2.0, WHITE);
            }
            Phase::Fly { t: ft, to } => {
                let u = (ft / FLY_TIME).min(1.0);
                let p = arc_point(tip, v2(l.x(*to), l.water_y), u);
                cv.polyline(&[tip, p], 1.2, WHITE.a(0.8));
                bobber(cv, p, 0.0, l.s);
            }
            Phase::Fishing => {
                if let Some(b) = self.pond.bait {
                    let bx = l.x(b.x);
                    let dunk = self.bob * 9.0 * l.s
                        + if self.bob > 0.5 {
                            (self.t * 30.0).sin() * 2.0
                        } else {
                            0.0
                        };
                    let float = v2(bx, l.water_y + (self.t * 2.0).sin() * 1.5 + dunk);
                    let mid = (tip + float) * 0.5 + v2(0.0, 30.0 * l.s);
                    let pts: Vec<V2> = (0..=16)
                        .map(|k| quad_bez(tip, mid, float, k as f32 / 16.0))
                        .collect();
                    cv.polyline(&pts, 1.2, WHITE.a(0.85));
                    let hook = v2(bx, l.y(b.depth));
                    cv.line(float, hook, 1.0, WHITE.a(0.6));
                    worm(cv, hook, self.t, l.s);
                    bobber(cv, float, self.bob, l.s);
                    if self.bite_flash > 0.0 || self.bob > 0.5 {
                        let p = float + v2(0.0, -34.0 * l.s);
                        let k = ease_out_back(1.0 - self.bite_flash.min(1.0) * 0.0);
                        cv.circle(p, 15.0 * l.s * k, rgb(255, 225, 90));
                        font.draw(cv, "!", p.x, p.y, 22.0 * l.s, INK, Align::Center);
                    }
                }
            }
            Phase::Fight { reel, .. } => {
                if let Some((f, _)) = self.pond.fight() {
                    let fp = v2(l.x(f.x), l.y(f.depth));
                    let mouth = fp + v2(f.dir * (7.0 + f.size * 0.18) * l.s, 0.0);
                    let tens = clamp01(reel.tension / SNAP_TENSION);
                    let col = WHITE.mix(rgb(255, 70, 60), clamp01((reel.tension - 0.7) / 0.3));
                    let n = 20;
                    let d = mouth - tip;
                    let nrm = v2(-d.y, d.x).norm();
                    let amp = (reel.tension - 0.75).max(0.0) * 10.0 * l.s;
                    let pts: Vec<V2> = (0..=n)
                        .map(|k| {
                            let u = k as f32 / n as f32;
                            let sag = (1.0 - tens) * 26.0 * l.s * (u * PI).sin();
                            tip + d * u
                                + v2(0.0, sag)
                                + nrm * (amp * (u * PI).sin() * (self.t * 50.0 + u * 9.0).sin())
                        })
                        .collect();
                    cv.polyline(&pts, 1.4 + tens, col);
                    // The splashes of a fish near the surface.
                    if f.depth < 0.08 && (self.t * 10.0).fract() < 0.2 {
                        self.fx.rng.f32();
                    }
                }
            }
            _ => {}
        }
        let _ = base;

        // The pen over the water: where the bait will hang.
        if self.pen.near
            && !self.pen.down
            && matches!(self.pond.phase, Phase::Fishing)
            && !self.finished
        {
            let p = self.pen.pos;
            if let Some(b) = self.pond.bait {
                let target_y = l.y(b.target);
                let bx = l.x(b.x);
                cv.dashed(v2(p.x, target_y), v2(bx, target_y), 1.5, 6.0, WHITE.a(0.45));
                cv.ring(v2(bx, target_y), 6.0, 1.5, WHITE.a(0.6));
                let m = format!("{:.1} m", b.target * 6.0);
                font.draw_shadow(
                    cv,
                    &m,
                    p.x + 14.0,
                    p.y - 14.0,
                    14.0,
                    WHITE,
                    Align::Left,
                    INK.a(0.6),
                );
            }
            cv.ring(p, 10.0, 2.0, WHITE.a(0.8));
        }

        self.fx.draw(cv);

        // The catch, shown big.
        if let Phase::Landed { t: lt, catch } = self.pond.phase {
            let k = ease_out_back((lt / 0.35).min(1.0));
            let c = v2(l.mid, l.h * 0.45);
            let a = clamp01((1.6 - lt) / 0.3);
            ui::panel(
                cv,
                (c.x - 140.0 * k, c.y - 85.0 * k, 280.0 * k, 170.0 * k),
                rgb(255, 250, 235).a(a),
                rgb(235, 225, 200).a(a),
                20.0,
            );
            if k > 0.3 {
                let info = fish::info(catch.kind);
                draw_fish(
                    cv,
                    c + v2(0.0, -20.0),
                    1.0,
                    120.0 * k,
                    info,
                    self.t * 6.0,
                    a,
                    0.0,
                );
                font.draw(
                    cv,
                    t(lang, info.key),
                    c.x,
                    c.y + 38.0,
                    22.0,
                    INK.a(a),
                    Align::Center,
                );
                let line = format!("{:.0} cm   +{}", catch.size, catch.points);
                font.draw(
                    cv,
                    &line,
                    c.x,
                    c.y + 64.0,
                    16.0,
                    rgb(200, 110, 40).a(a),
                    Align::Center,
                );
            }
        }

        // The top bar: back, the sun's clock, the score.
        ui::back_button(cv, l.back(), inp.held_in(l.back()), rgb(60, 120, 180));
        let tc = v2(l.mid, 32.0);
        cv.rrect(tc.x - 58.0, tc.y - 17.0, 116.0, 34.0, 17.0, INK.a(0.35));
        cv.circle(tc + v2(-36.0, 0.0), 9.0, rgb(255, 200, 80));
        font.draw(
            cv,
            &ui::clock(self.pond.time_left),
            tc.x + 10.0,
            tc.y,
            19.0,
            WHITE,
            Align::Center,
        );
        let sc = format!("{}", self.pond.score);
        font.draw_shadow(
            cv,
            &sc,
            l.w - 16.0,
            32.0,
            26.0,
            rgb(255, 235, 140),
            Align::Right,
            INK.a(0.6),
        );

        // The fight's gauges: the tension, and how far the fish is.
        if let Some((f, reel)) = self.pond.fight() {
            let gw = (l.span * 0.72).min(420.0);
            let gx = l.mid - gw / 2.0;
            let gy = 66.0;
            let max = 1.25;
            cv.rrect(gx - 4.0, gy - 4.0, gw + 8.0, 22.0, 11.0, INK.a(0.5));
            let zone = |a: f32, b: f32| (gx + gw * a / max, gw * (b - a) / max);
            let (x0, w0) = zone(0.0, SLACK_TENSION);
            cv.rrect(x0, gy, w0, 14.0, 7.0, rgb(90, 160, 255));
            let (x1, w1) = zone(SLACK_TENSION, SNAP_TENSION * 0.92);
            cv.rect(x1, gy, w1, 14.0, rgb(90, 210, 110));
            let (x2, w2) = zone(SNAP_TENSION * 0.92, max);
            cv.rrect(x2, gy, w2, 14.0, 7.0, rgb(240, 80, 70));
            let nx = gx + gw * (reel.tension / max).clamp(0.0, 1.0);
            let wob = if reel.tension > 0.92 {
                (self.t * 40.0).sin() * 2.0
            } else {
                0.0
            };
            cv.convex(
                v2(nx + wob, gy + 7.0),
                &[
                    v2(nx + wob - 7.0, gy - 8.0),
                    v2(nx + wob + 7.0, gy - 8.0),
                    v2(nx + wob, gy + 4.0),
                ],
                WHITE,
            );
            cv.rect(nx + wob - 1.5, gy, 3.0, 14.0, WHITE);
            // Snap / slack timers as thin bars under it.
            if reel.strain > 0.0 {
                cv.rect(
                    gx,
                    gy + 18.0,
                    gw * clamp01(reel.strain / fish::SNAP_TIME),
                    3.0,
                    rgb(255, 90, 80),
                );
            }
            if reel.slack > 0.0 {
                cv.rect(
                    gx,
                    gy + 18.0,
                    gw * clamp01(reel.slack / fish::SLACK_TIME),
                    3.0,
                    rgb(120, 180, 255),
                );
            }
            // How much line is out: the fish on a track towards the dock.
            let ty = gy + 34.0;
            cv.rrect(gx, ty, gw, 6.0, 3.0, INK.a(0.4));
            let u = clamp01(reel.line / (reel.line0 * fish::RUN_OFF));
            let fx = gx + gw * u;
            cv.rrect(gx, ty, gw * u, 6.0, 3.0, rgba(255, 255, 255, 0.5));
            draw_fish(
                cv,
                v2(fx, ty + 3.0),
                -1.0,
                24.0,
                fish::info(f.kind),
                self.t * 10.0,
                1.0,
                0.0,
            );
            // The pressure the pen gives now.
            let pr = self.pen.pressure;
            cv.rrect(l.w - 22.0, l.h * 0.35, 10.0, l.h * 0.3, 5.0, INK.a(0.4));
            let ph = l.h * 0.3 * pr;
            cv.rrect(
                l.w - 22.0,
                l.h * 0.35 + l.h * 0.3 - ph,
                10.0,
                ph,
                5.0,
                rgb(255, 200, 90),
            );
        }

        // The hint and the messages.
        let hint = match &self.pond.phase {
            Phase::Ready => Some("fish.hint.cast"),
            Phase::Aim { .. } | Phase::Fly { .. } => Some("fish.hint.cast"),
            Phase::Fishing => {
                if self.bob > 0.5 {
                    None
                } else if !self.pen.near {
                    Some("fish.hint.depth")
                } else if self.pond.engaged.is_some() {
                    Some("fish.hint.wait")
                } else {
                    Some(if ((self.t / 6.0) as u32).is_multiple_of(2) {
                        "fish.hint.depth"
                    } else {
                        "fish.hint.recast"
                    })
                }
            }
            Phase::Fight { .. } => Some("fish.hint.reel"),
            Phase::Landed { .. } => None,
        };
        if !self.finished {
            if let Some(k) = hint {
                let y = l.h - 20.0;
                let txt = t(lang, k);
                let w = font.width(txt, 14.0) + 24.0;
                cv.rrect(l.mid - w / 2.0, y - 14.0, w, 28.0, 14.0, INK.a(0.45));
                font.draw(cv, txt, l.mid, y, 14.0, WHITE, Align::Center);
            }
            if self.bob > 0.5 {
                let pulse = 1.0 + 0.08 * (self.t * 12.0).sin();
                font.draw_outline_scaled(
                    cv,
                    t(lang, "fish.bite"),
                    l.mid,
                    l.h * 0.22,
                    30.0,
                    pulse,
                    rgb(255, 230, 90),
                    Align::Center,
                    INK,
                );
            }
            if let Some((m, life, col)) = &self.message {
                let a = clamp01(*life / 0.4);
                font.draw_outline(
                    cv,
                    m,
                    l.mid,
                    l.h * 0.5,
                    26.0,
                    col.a(a),
                    Align::Center,
                    INK.a(a * 0.9),
                );
            }
            if !inp.pen.seen && !self.staged && !self.demo && self.t > 1.5 {
                font.draw_shadow(
                    cv,
                    t(lang, "pen.missing"),
                    l.mid,
                    l.h - 52.0,
                    13.0,
                    rgb(255, 230, 200),
                    Align::Center,
                    INK.a(0.6),
                );
            }
        }
        self.fx.draw_pops(cv, font);
        self.fx.draw_flash(cv, l.w, l.h);

        if self.finished {
            self.draw_end(cv, font, &l, lang, inp);
        }
        if shake.len() > 0.0 {
            for v in &mut cv.v[0..] {
                v.position.x += shake.x;
                v.position.y += shake.y;
            }
        }
    }

    fn rod_tip(&self, l: &Lay) -> V2 {
        let pull = match self.pond.fight() {
            Some((f, reel)) => {
                (v2(l.x(f.x), l.y(f.depth)) - l.rod_tip).norm() * (reel.tension * 22.0 * l.s)
            }
            None => v2(0.0, 0.0),
        };
        let hover = if self.pen.near {
            v2((self.pen.pos.x - l.rod_tip.x) * 0.03, 0.0)
        } else {
            v2(0.0, 0.0)
        };
        l.rod_tip + pull + hover + v2(0.0, (self.t * 1.3).sin() * 1.5)
    }

    fn draw_end(&self, cv: &mut Canvas, font: &mut Font, l: &Lay, lang: Lang, inp: &Input) {
        let k = ease_out_back(clamp01(self.end_t / 0.45));
        cv.rect(0.0, 0.0, l.w, l.h, INK.a(0.45 * clamp01(self.end_t * 3.0)));
        let pw = (l.span - 32.0).min(460.0);
        let kinds_n = {
            let mut seen: Vec<Kind> = Vec::new();
            for c in &self.pond.log {
                if !seen.contains(&c.kind) {
                    seen.push(c.kind);
                }
            }
            seen.len()
        };
        let ph = (220.0 + kinds_n.div_ceil(2).max(1) as f32 * 46.0).min(l.h * 0.66);
        let r = (l.mid - pw / 2.0, l.h * 0.1 + (1.0 - k) * 80.0, pw, ph);
        ui::panel(cv, r, rgb(255, 248, 230), rgb(236, 220, 190), 22.0);
        let cxp = l.mid;
        font.draw(
            cv,
            t(lang, "fish.timeup"),
            cxp,
            r.1 + 36.0,
            22.0,
            INK,
            Align::Center,
        );
        font.draw_outline(
            cv,
            &self.pond.score.to_string(),
            cxp,
            r.1 + 86.0,
            48.0,
            rgb(255, 210, 80),
            Align::Center,
            INK,
        );
        if self.new_best {
            let pulse = 1.0 + 0.06 * (self.end_t * 8.0).sin();
            font.draw_scaled(
                cv,
                t(lang, "common.newbest"),
                cxp,
                r.1 + 128.0,
                18.0,
                pulse,
                rgb(220, 70, 90),
                Align::Center,
            );
        }
        font.draw(
            cv,
            t(lang, "fish.log"),
            cxp,
            r.1 + 160.0,
            16.0,
            INK.a(0.7),
            Align::Center,
        );
        // The log: each kind caught, how many, the biggest.
        let mut kinds: Vec<(Kind, u32, f32)> = Vec::new();
        for c in &self.pond.log {
            match kinds.iter_mut().find(|k| k.0 == c.kind) {
                Some(k) => {
                    k.1 += 1;
                    k.2 = k.2.max(c.size);
                }
                None => kinds.push((c.kind, 1, c.size)),
            }
        }
        if kinds.is_empty() {
            font.draw(
                cv,
                t(lang, "fish.none"),
                cxp,
                r.1 + 200.0,
                15.0,
                INK.a(0.6),
                Align::Center,
            );
        }
        let cols = 2;
        let cw = (pw - 30.0) / cols as f32;
        let rh = 46.0;
        for (i, (kind, n, size)) in kinds.iter().enumerate() {
            let x = r.0 + 15.0 + (i % cols) as f32 * cw;
            let y = r.1 + 190.0 + (i / cols) as f32 * rh;
            if y > r.1 + ph - 30.0 {
                break;
            }
            let info = fish::info(*kind);
            cv.rrect(
                x + 2.0,
                y,
                cw - 4.0,
                rh - 6.0,
                12.0,
                rgba(255, 255, 255, 0.7),
            );
            draw_fish(cv, v2(x + 26.0, y + 20.0), 1.0, 34.0, info, 0.0, 1.0, 0.0);
            font.draw(
                cv,
                t(lang, info.key),
                x + 50.0,
                y + 13.0,
                13.0,
                INK,
                Align::Left,
            );
            font.draw(
                cv,
                &format!("{n} x  {size:.0} cm"),
                x + 50.0,
                y + 29.0,
                12.0,
                INK.a(0.6),
                Align::Left,
            );
        }
        let (again, menu) = Self::end_buttons(l);
        ui::button(
            cv,
            font,
            again,
            t(lang, "common.again"),
            rgb(60, 160, 90),
            inp.held_in(again),
            18.0,
        );
        ui::button(
            cv,
            font,
            menu,
            t(lang, "common.back"),
            rgb(70, 110, 190),
            inp.held_in(menu),
            18.0,
        );
        let _ = tf;
    }
}

fn quad_bez(a: V2, c: V2, b: V2, u: f32) -> V2 {
    a * ((1.0 - u) * (1.0 - u)) + c * (2.0 * u * (1.0 - u)) + b * (u * u)
}

/// A cast's flight: a parabola from the rod tip to the landing point.
fn arc_point(from: V2, to: V2, u: f32) -> V2 {
    // Never up into the top bar.
    let lift = ((to.x - from.x).abs() * 0.22 + 30.0)
        .min(from.y.min(to.y) - 80.0)
        .max(0.0);
    v2(
        lerp(from.x, to.x, u),
        lerp(from.y, to.y, u) - lift * 4.0 * u * (1.0 - u),
    )
}

fn sky_cols(p: f32) -> (Col, Col) {
    let day = (rgb(105, 185, 250), rgb(205, 236, 255));
    let dusk = (rgb(95, 90, 185), rgb(255, 165, 115));
    let night = (rgb(35, 28, 85), rgb(190, 90, 120));
    if p < 0.7 {
        let k = clamp01((p - 0.35) / 0.35);
        (day.0.mix(dusk.0, k), day.1.mix(dusk.1, k))
    } else {
        let k = clamp01((p - 0.7) / 0.3);
        (dusk.0.mix(night.0, k), dusk.1.mix(night.1, k))
    }
}

fn draw_sky(cv: &mut Canvas, l: &Lay, p: f32, t: f32) {
    let (top, bot) = sky_cols(p);
    cv.rect_v(0.0, 0.0, l.w, l.water_y + 2.0, top, bot);
    // The sun, setting.
    let sun = v2(
        lerp(l.w * 0.8, l.w * 0.58, p),
        lerp(l.h * 0.09, l.water_y - 8.0, p * p),
    );
    let sc = rgb(255, 235, 120).mix(rgb(255, 120, 60), clamp01(p * 1.2 - 0.2));
    cv.glow(sun, 70.0 * l.s, sc.a(0.45));
    cv.circle(sun, 20.0 * l.s, sc);
    // Clouds drifting.
    for k in 0..3 {
        let x = ((t * 6.0 + k as f32 * 210.0) % (l.w + 200.0)) - 100.0;
        let y = l.h * (0.08 + 0.05 * k as f32);
        let c = WHITE.a(0.75).mix(rgb(255, 190, 170), p * 0.6);
        for (dx, dy, r) in [
            (0.0, 0.0, 16.0),
            (18.0, -6.0, 20.0),
            (38.0, 0.0, 15.0),
            (20.0, 5.0, 16.0),
        ] {
            cv.circle(v2(x + dx * l.s, y + dy * l.s), r * l.s, c);
        }
    }
    // The far bank: hills, trees.
    let hill = rgb(70, 150, 90).mix(rgb(40, 50, 80), p * 0.8);
    let pts: Vec<V2> = (0..=24)
        .map(|k| {
            let x = l.w * k as f32 / 24.0;
            v2(
                x,
                l.water_y
                    - 18.0 * l.s
                    - 14.0 * l.s * ((x * 0.013).sin() + 0.5 * (x * 0.031 + 1.0).sin()),
            )
        })
        .collect();
    for k in 0..24 {
        cv.quad4(
            [
                pts[k],
                pts[k + 1],
                v2(pts[k + 1].x, l.water_y + 1.0),
                v2(pts[k].x, l.water_y + 1.0),
            ],
            [hill; 4],
        );
    }
    for k in 0..5 {
        let x = l.w * (0.35 + 0.14 * k as f32);
        let y = l.water_y - 22.0 * l.s;
        let tr = hill.darker(0.15);
        cv.rect(
            x - 2.0,
            y - 6.0,
            4.0,
            14.0,
            rgb(90, 60, 40).mix(rgb(30, 30, 50), p),
        );
        cv.circle(v2(x, y - 14.0 * l.s), 11.0 * l.s, tr);
        cv.circle(v2(x - 6.0, y - 8.0 * l.s), 8.0 * l.s, tr);
        cv.circle(v2(x + 6.0, y - 8.0 * l.s), 8.0 * l.s, tr);
    }
}

fn draw_water(cv: &mut Canvas, l: &Lay, p: f32, t: f32, weeds: &[(f32, f32, f32)]) {
    let dark = p * 0.35;
    let top = rgb(90, 200, 230).darker(dark);
    let mid = rgb(30, 110, 170).darker(dark);
    let deep = rgb(12, 40, 90).darker(dark);
    let my = (l.water_y + l.bottom_y) / 2.0;
    cv.rect_v(0.0, l.water_y, l.w, my - l.water_y, top, mid);
    cv.rect_v(0.0, my, l.w, l.h - my, mid, deep);
    // Light rays from the surface.
    for k in 0..5 {
        let x = l.w * (0.2 + 0.18 * k as f32) + (t * 0.4 + k as f32).sin() * 20.0;
        let w0 = 26.0 * l.s;
        cv.quad4(
            [
                v2(x, l.water_y),
                v2(x + w0, l.water_y),
                v2(x + w0 + 80.0, l.bottom_y),
                v2(x + 50.0, l.bottom_y),
            ],
            [WHITE.a(0.10), WHITE.a(0.10), WHITE.a(0.0), WHITE.a(0.0)],
        );
    }
    // Depth marks on the right.
    for m in 1..6 {
        let y = l.y(m as f32 / 6.0);
        cv.rect(l.w - 10.0, y, 6.0, 1.5, WHITE.a(0.25));
    }
    // The surface's shimmer.
    let n = 40;
    let pts: Vec<V2> = (0..=n)
        .map(|k| {
            let x = l.w * k as f32 / n as f32;
            v2(x, l.water_y + (x * 0.05 + t * 2.0).sin() * 1.5)
        })
        .collect();
    cv.polyline(&pts, 2.5, WHITE.a(0.55));
    // The bottom: sand, pebbles, weeds.
    let sand = rgb(200, 170, 110).darker(0.25 + dark);
    let spts: Vec<V2> = (0..=n)
        .map(|k| {
            let x = l.w * k as f32 / n as f32;
            v2(x, l.bottom_y + (x * 0.02).sin() * 5.0)
        })
        .collect();
    for k in 0..n {
        cv.quad4(
            [
                spts[k],
                spts[k + 1],
                v2(spts[k + 1].x, l.h),
                v2(spts[k].x, l.h),
            ],
            [sand, sand, sand.darker(0.3), sand.darker(0.3)],
        );
    }
    for (u, hgt, ph) in weeds {
        let x = l.wx0 + u * (l.wx1 - l.wx0);
        let hgt = hgt * 90.0 * l.s;
        let pts: Vec<(V2, f32, Col)> = (0..=8)
            .map(|k| {
                let f = k as f32 / 8.0;
                let sway = (t * 1.2 + ph + f * 2.0).sin() * 10.0 * f * l.s;
                (
                    v2(x + sway, l.bottom_y + 4.0 - hgt * f),
                    6.0 * l.s * (1.0 - f * 0.8),
                    rgb(60, 140, 80).darker(dark + 0.1 * f),
                )
            })
            .collect();
        cv.ribbon(&pts);
    }
    // The dock.
    let wood = rgb(150, 100, 60).darker(dark);
    let dy = l.water_y - 8.0 * l.s;
    for k in 0..3 {
        let x = l.w * (0.03 + 0.06 * k as f32);
        let post = wood.darker(0.35);
        cv.rect_v(
            x,
            dy,
            7.0 * l.s,
            90.0 * l.s,
            post,
            post.mix(rgb(30, 110, 170).darker(dark), 0.9).a(0.0),
        );
    }
    cv.rrect(
        -10.0,
        dy - 8.0 * l.s,
        l.w * 0.2 + 10.0,
        12.0 * l.s,
        4.0,
        wood,
    );
    cv.rect(
        -10.0,
        dy - 8.0 * l.s,
        l.w * 0.2 + 10.0,
        3.0,
        wood.lighter(0.25),
    );
}

fn draw_rod(cv: &mut Canvas, l: &Lay, tip: V2) {
    let base = l.rod_base;
    let mid = (base + tip) * 0.5 + v2(-6.0, -14.0) * l.s;
    let pts: Vec<(V2, f32, Col)> = (0..=12)
        .map(|k| {
            let u = k as f32 / 12.0;
            (
                quad_bez(base, mid, tip, u),
                (6.0 - 4.5 * u) * l.s,
                rgb(110, 70, 40).lighter(0.3 * u),
            )
        })
        .collect();
    cv.ribbon(&pts);
    cv.circle(quad_bez(base, mid, tip, 0.18), 5.0 * l.s, rgb(80, 80, 90));
    cv.circle(tip, 2.0, rgb(240, 200, 80));
}

fn bobber(cv: &mut Canvas, p: V2, sunk: f32, s: f32) {
    let r = 7.0 * s;
    let a = 1.0 - 0.4 * sunk;
    cv.line(
        p + v2(0.0, -r),
        p + v2(0.0, -r - 8.0 * s),
        2.0,
        rgb(40, 40, 40).a(a),
    );
    cv.circle(p, r, WHITE.a(a));
    let pts: Vec<V2> = (0..=12)
        .map(|k| p + v2(r, 0.0).rot(PI + k as f32 / 12.0 * PI))
        .collect();
    cv.convex(p + v2(0.0, -r * 0.4), &pts, rgb(230, 50, 50).a(a));
    cv.circle(p + v2(-r * 0.35, -r * 0.45), r * 0.25, WHITE.a(0.6 * a));
}

fn worm(cv: &mut Canvas, p: V2, t: f32, s: f32) {
    cv.arc(
        p + v2(-3.0 * s, -3.0 * s),
        4.0 * s,
        1.5,
        0.0,
        PI,
        rgb(200, 200, 210),
    );
    let pts: Vec<(V2, f32, Col)> = (0..=6)
        .map(|k| {
            let u = k as f32 / 6.0;
            (
                p + v2(
                    (u - 0.5) * 12.0 * s,
                    (t * 5.0 + u * 6.0).sin() * 2.5 * s + 2.0 * s,
                ),
                3.5 * s,
                rgb(240, 120, 140),
            )
        })
        .collect();
    cv.ribbon(&pts);
}

/// A fish facing `dir` (+1 right), `len` long.
#[allow(clippy::too_many_arguments)]
pub fn draw_fish(
    cv: &mut Canvas,
    pos: V2,
    dir: f32,
    len: f32,
    info: &fish::KindInfo,
    wiggle: f32,
    alpha: f32,
    thrash: f32,
) {
    let body = Col::from3(info.color).a(alpha);
    let acc = Col::from3(info.accent).a(alpha);
    let d = if dir < 0.0 { -1.0 } else { 1.0 };
    let sw = wiggle.sin() * (0.25 + 0.4 * thrash);
    let roll = (wiggle * 0.5).sin() * 0.1 * thrash;
    let at = |x: f32, y: f32| pos + v2(x * len * d, y * len).rot(roll * d);
    if info.kind == Kind::Boot {
        let c = Col::from3(info.color).a(alpha);
        cv.rrect(
            pos.x - len * 0.15,
            pos.y - len * 0.4,
            len * 0.3,
            len * 0.5,
            4.0,
            c,
        );
        cv.rrect(pos.x - len * 0.15, pos.y, len * 0.55, len * 0.2, 5.0, c);
        cv.rect(
            pos.x - len * 0.15,
            pos.y + len * 0.15,
            len * 0.55,
            len * 0.06,
            acc,
        );
        for k in 0..3 {
            cv.circle(
                v2(pos.x + len * 0.02, pos.y - len * 0.3 + k as f32 * len * 0.1),
                1.5,
                acc,
            );
        }
        return;
    }
    if info.kind == Kind::Eel {
        let pts: Vec<(V2, f32, Col)> = (0..=12)
            .map(|k| {
                let u = k as f32 / 12.0;
                let y = (wiggle * 1.5 + u * 7.0).sin() * 0.06 * u;
                (
                    at(0.5 - u, y),
                    len * 0.11 * (1.0 - 0.6 * u),
                    body.mix(acc, 0.2 * u),
                )
            })
            .collect();
        cv.ribbon(&pts);
        cv.circle(at(0.48, 0.0), len * 0.055, body);
        cv.circle(at(0.43, -0.02), len * 0.018, WHITE.a(alpha));
        return;
    }
    let ry = match info.kind {
        Kind::Carp => 0.27,
        Kind::Pike => 0.15,
        Kind::Catfish => 0.19,
        Kind::Minnow => 0.2,
        _ => 0.23,
    };
    // Tail.
    let tb = at(-0.42, 0.0);
    let t1 = at(-0.68, -0.2) + v2(0.0, sw * len * 0.15);
    let t2 = at(-0.68, 0.2) + v2(0.0, sw * len * 0.15);
    cv.convex(
        (tb + t1 + t2) * (1.0 / 3.0),
        &[tb, t1, t2],
        acc.mix(body, 0.4),
    );
    // Dorsal fin.
    cv.convex(
        at(0.0, -ry * 0.9),
        &[
            at(-0.15, -ry * 0.7),
            at(0.12, -ry * 0.8),
            at(-0.12, -ry * 1.55),
        ],
        acc.mix(body, 0.3),
    );
    // Body and belly.
    cv.ellipse(pos, len * 0.5, len * ry, roll * d, body);
    cv.ellipse(
        at(0.02, ry * 0.35),
        len * 0.38,
        len * ry * 0.45,
        roll * d,
        acc.a(0.75),
    );
    match info.kind {
        Kind::Perch => {
            for k in 0..4 {
                let x = -0.25 + 0.14 * k as f32;
                cv.line(
                    at(x, -ry * 0.8),
                    at(x + 0.03, ry * 0.2),
                    len * 0.04,
                    body.darker(0.45),
                );
            }
        }
        Kind::Trout => {
            for k in 0..7 {
                let x = -0.3 + 0.09 * k as f32;
                cv.circle(
                    at(x, -ry * 0.35 + (k % 2) as f32 * 0.06),
                    len * 0.018,
                    body.darker(0.5),
                );
            }
            cv.line(at(-0.35, 0.0), at(0.3, 0.0), len * 0.05, acc.a(0.6));
        }
        Kind::Pike => {
            for k in 0..6 {
                cv.circle(
                    at(-0.3 + 0.11 * k as f32, -0.03 + (k % 2) as f32 * 0.06),
                    len * 0.02,
                    acc.a(0.8),
                );
            }
        }
        Kind::Catfish => {
            cv.line(
                at(0.45, 0.04),
                at(0.62, 0.16 + sw * 0.05),
                len * 0.012,
                body.darker(0.4),
            );
            cv.line(
                at(0.45, 0.06),
                at(0.6, 0.24 + sw * 0.05),
                len * 0.012,
                body.darker(0.4),
            );
        }
        Kind::Goldfin => {
            cv.glow(pos, len * 0.8, rgba(255, 220, 80, 0.35 * alpha));
            cv.circle(at(0.1, -ry * 0.4), len * 0.04, WHITE.a(0.8 * alpha));
        }
        _ => {}
    }
    // Eye.
    cv.circle(at(0.3, -ry * 0.25), len * 0.06, WHITE.a(alpha));
    cv.circle(at(0.32, -ry * 0.25), len * 0.032, INK.a(alpha));
    // Gill line.
    cv.arc(
        at(0.18, 0.0),
        len * ry * 0.75,
        len * 0.025,
        if d > 0.0 { -0.9 } else { PI - 0.9 },
        if d > 0.0 { 0.9 } else { PI + 0.9 },
        body.darker(0.3),
    );
    let _ = TAU;
}
