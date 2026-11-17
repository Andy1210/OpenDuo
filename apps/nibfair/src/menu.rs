//! The main menu: a fairground at night, three booths.

use crate::gfx::{rgb, rgba, Canvas, Col, WHITE};
use crate::input::Input;
use crate::text::Align;
use crate::ui::{self, Rect, INK};
use crate::{Ctx, Go};
use nibfair::geom::{v2, V2};
use nibfair::rng::Rng;
use nibfair::strings::Lang;
use nibfair::tween::{clamp01, ease_out_back, ease_out_cubic};
use std::f32::consts::TAU;

pub struct Menu {
    t: f32,
    stars: Vec<(V2, f32, f32)>,
    fx: ui::Fx,
    spark_in: f32,
    rng: Rng,
}

struct Booth {
    go: Go,
    name: &'static str,
    sub: &'static str,
    best: &'static str,
    a: Col,
    b: Col,
}

const BOOTHS: [Booth; 3] = [
    Booth {
        go: Go::Fishing,
        name: "menu.fishing",
        sub: "menu.fishing.sub",
        best: "best.fishing",
        a: rgb(40, 150, 210),
        b: rgb(20, 90, 160),
    },
    Booth {
        go: Go::Slicing,
        name: "menu.slicing",
        sub: "menu.slicing.sub",
        best: "best.slicing",
        a: rgb(235, 80, 90),
        b: rgb(170, 40, 70),
    },
    Booth {
        go: Go::Pottery,
        name: "menu.pottery",
        sub: "menu.pottery.sub",
        best: "best.pottery",
        a: rgb(225, 140, 60),
        b: rgb(170, 85, 35),
    },
];

impl Menu {
    pub fn new(cx: &mut Ctx) -> Menu {
        let mut rng = Rng::new(cx.next_seed());
        let stars = (0..70)
            .map(|_| {
                (
                    v2(rng.f32(), rng.f32() * 0.6),
                    rng.range(0.6, 1.8),
                    rng.range(0.0, TAU),
                )
            })
            .collect();
        Menu {
            t: 0.0,
            stars,
            fx: ui::Fx::new(cx.next_seed()),
            spark_in: 0.0,
            rng,
        }
    }

    /// Cards, the language button, quit. Spanned (a wide window): a book,
    /// the title on the left panel and the booths on the right one.
    fn layout(&self, cx: &Ctx) -> (Vec<Rect>, Rect, Rect) {
        let (w, h) = (cx.w, cx.h);
        let (col_x, col_w, top) = if cx.wide() {
            (w * 0.5 + 22.0, w * 0.5 - 40.0, h * 0.08)
        } else {
            (0.0, w, h * 0.25)
        };
        let avail = if cx.wide() { h * 0.86 } else { h * 0.62 };
        let gap = (h * 0.025).min(20.0);
        let ch = ((avail - 2.0 * gap) / 3.0).min(if cx.wide() { 210.0 } else { 150.0 });
        let cw = (col_w - 36.0).min(520.0);
        let x0 = col_x + (col_w - cw) / 2.0;
        let cards = (0..3)
            .map(|i| (x0, top + i as f32 * (ch + gap), cw, ch))
            .collect();
        let (mid, span) = crate::ui::column(w, h);
        let bw = 150.0f32.min(span * 0.38);
        let by = h - 64.0;
        let lang = (mid - bw - 8.0, by, bw, 44.0);
        let quit = (mid + 8.0, by, bw, 44.0);
        (cards, lang, quit)
    }

    pub fn update(&mut self, cx: &mut Ctx, inp: &Input, dt: f32) -> Go {
        self.t += dt;
        self.fx.update(dt);
        self.spark_in -= dt;
        if self.spark_in <= 0.0 {
            self.spark_in = self.rng.range(0.15, 0.5);
            let p = v2(self.rng.range(0.0, cx.w), self.rng.range(cx.h * 0.6, cx.h));
            let col =
                [rgb(255, 210, 90), rgb(255, 120, 180), rgb(120, 220, 255)][self.rng.below(3)];
            self.fx.add(ui::Particle {
                pos: p,
                vel: v2(self.rng.range(-10.0, 10.0), self.rng.range(-40.0, -15.0)),
                life: 2.5,
                max: 2.5,
                size: self.rng.range(1.0, 2.4),
                col,
                kind: ui::PKind::Dot,
                rot: 0.0,
                spin: 0.0,
                grav: 0.0,
                drag: 0.2,
            });
        }
        if inp.back {
            return Go::Quit;
        }
        let (cards, lang, quit) = self.layout(cx);
        for (i, r) in cards.iter().enumerate() {
            if inp.tapped(*r) && self.t > 0.3 {
                return BOOTHS[i].go;
            }
        }
        if inp.tapped(lang) {
            cx.lang = cx.lang.other();
            cx.save.set("lang", cx.lang.code());
            cx.store();
        }
        if inp.tapped(quit) {
            return Go::Quit;
        }
        for p in &inp.presses {
            self.fx.burst(
                *p,
                10,
                rgb(255, 220, 120),
                160.0,
                2.5,
                ui::PKind::Spark,
                0.0,
            );
        }
        Go::Stay
    }

    pub fn draw(&mut self, cx: &mut Ctx, inp: &Input) {
        let (w, h) = (cx.w, cx.h);
        let t = self.t;
        let lang_now = cx.lang;
        let (cards, lang, quit) = self.layout(cx);
        let cv = &mut cx.cv;
        // The night sky over the fair.
        cv.rect_v(0.0, 0.0, w, h * 0.55, rgb(22, 18, 58), rgb(70, 36, 110));
        cv.rect_v(
            0.0,
            h * 0.55,
            w,
            h * 0.45 + 1.0,
            rgb(70, 36, 110),
            rgb(150, 60, 110),
        );
        cv.glow(v2(w * 0.5, h * 1.05), h * 0.7, rgba(255, 150, 90, 0.35));
        for (p, s, ph) in &self.stars {
            let tw = 0.55 + 0.45 * (t * 1.7 + ph).sin();
            cv.circle(
                v2(p.x * w, p.y * h),
                *s * 0.6,
                rgba(255, 250, 230, 0.35 + 0.5 * tw),
            );
        }
        // A big wheel's silhouette and tents on the horizon.
        let hc = v2(w * 0.82, h * 0.86);
        let hr = h * 0.2;
        cv.ring(hc, hr, 3.0, rgba(40, 20, 70, 0.8));
        for k in 0..10 {
            let a = t * 0.15 + k as f32 / 10.0 * TAU;
            let p = hc + v2(a.cos(), a.sin()) * hr;
            cv.line(hc, p, 1.6, rgba(40, 20, 70, 0.7));
            cv.circle(
                p,
                6.0,
                rgba(
                    255,
                    200,
                    120,
                    0.25 + 0.2 * ((t * 2.0 + k as f32).sin() * 0.5 + 0.5),
                ),
            );
        }
        for k in 0..4 {
            let x = w * (0.05 + k as f32 * 0.17);
            let tw = h * 0.1;
            cv.convex(
                v2(x + tw * 0.5, h * 0.93),
                &[v2(x, h), v2(x + tw * 0.5, h * 0.86), v2(x + tw, h)],
                rgba(45, 22, 72, 0.9),
            );
        }
        // The string lights.
        draw_lights(cv, w, h * 0.035, t);

        // The title.
        let wide = w > h * 1.15;
        let (mid, span) = crate::ui::column(w, h);
        let ty = if wide { h * 0.36 } else { h * 0.12 } + (t * 1.6).sin() * 3.0;
        let pop = ease_out_back(clamp01(t / 0.6));
        // The title pops in: drawn at its full size, scaled (its glyphs rasterised once).
        let full = (h * if wide { 0.11 } else { 0.075 }).min(span * 0.14);
        let pop = pop.max(0.01);
        let size = full * pop;
        let tw = cx.font.width(cx_t(lang_now, "app.name"), full) * pop;
        nib(cv, v2(mid - tw / 2.0 - size * 0.55, ty), size * 0.42, t);
        let name = cx_t(lang_now, "app.name");
        cx.font.draw_outline_scaled(
            cv,
            name,
            mid + size * 0.1,
            ty,
            full,
            pop,
            rgb(255, 228, 140),
            Align::Center,
            rgb(60, 20, 80),
        );
        let tag = cx_t(lang_now, "app.tagline");
        cx.font.draw_shadow_scaled(
            cv,
            tag,
            mid,
            ty + size * 0.85,
            full * 0.34,
            pop,
            rgba(255, 240, 255, 0.9),
            Align::Center,
            INK.a(0.6),
        );

        for (i, r) in cards.iter().enumerate() {
            let k = ease_out_back(clamp01((t - 0.15 - 0.1 * i as f32) / 0.5));
            if k <= 0.0 {
                continue;
            }
            let held = inp.held_in(*r);
            let rr = (
                r.0,
                r.1 + (1.0 - k) * 60.0 + if held { 3.0 } else { 0.0 },
                r.2,
                r.3,
            );
            booth(cx, rr, &BOOTHS[i], i, t, k.min(1.0));
        }
        ui::button(
            &mut cx.cv,
            &mut cx.font,
            lang,
            cx_t(cx.lang, "menu.lang"),
            rgb(95, 80, 170),
            inp.held_in(lang),
            17.0,
        );
        ui::button(
            &mut cx.cv,
            &mut cx.font,
            quit,
            cx_t(cx.lang, "menu.quit"),
            rgb(120, 60, 110),
            inp.held_in(quit),
            17.0,
        );

        if !inp.pen.seen && t > 2.0 {
            let key = if inp.finger_seen && t > 4.0 {
                "pen.finger"
            } else {
                "pen.missing"
            };
            let a = clamp01((t - 2.0) * 2.0) * (0.75 + 0.25 * (t * 3.0).sin());
            let y = lang.1 - 30.0;
            let txt = cx_t(cx.lang, key);
            let (mid, span) = crate::ui::column(w, h);
            let lines = cx.font.wrap(txt, span - 40.0, 13.0);
            for (i, l) in lines.iter().enumerate() {
                let yy = y - (lines.len() - 1 - i) as f32 * 17.0;
                cx.font.draw_shadow(
                    &mut cx.cv,
                    l,
                    mid,
                    yy,
                    13.0,
                    rgba(255, 235, 200, a),
                    Align::Center,
                    INK.a(0.6 * a),
                );
            }
        }
        self.fx.draw(&mut cx.cv);
    }
}

fn cx_t(lang: Lang, key: &str) -> &'static str {
    nibfair::strings::t(lang, key)
}

fn draw_lights(cv: &mut Canvas, w: f32, y: f32, t: f32) {
    let n = 14;
    let sag = 26.0;
    let pt = |u: f32| v2(u * w, y + sag * 4.0 * u * (1.0 - u));
    let pts: Vec<V2> = (0..=40).map(|k| pt(k as f32 / 40.0)).collect();
    cv.polyline(&pts, 1.5, rgba(30, 20, 40, 0.9));
    let cols = [
        rgb(255, 210, 90),
        rgb(255, 110, 170),
        rgb(110, 220, 255),
        rgb(150, 255, 140),
    ];
    for k in 0..n {
        let u = (k as f32 + 0.5) / n as f32;
        let p = pt(u) + v2(0.0, 7.0);
        let on = 0.6 + 0.4 * (t * 2.2 + k as f32 * 1.3).sin();
        let c = cols[k % 4];
        cv.glow(p, 16.0, c.a(0.35 * on));
        cv.circle(p, 4.5, c.mix(WHITE, 0.3 * on));
        cv.rect(p.x - 2.0, p.y - 7.5, 4.0, 4.0, rgb(40, 30, 50));
    }
}

/// A fountain pen nib, the app's mark.
pub fn nib(cv: &mut Canvas, c: V2, s: f32, t: f32) {
    let rot = (t * 1.3).sin() * 0.12 + 0.5;
    let p = |x: f32, y: f32| c + v2(x * s, y * s).rot(rot);
    let gold = rgb(255, 205, 80);
    cv.glow(c, s * 2.2, rgba(255, 210, 120, 0.25));
    cv.convex(
        p(0.0, 0.1),
        &[
            p(0.0, 1.2),
            p(-0.65, -0.1),
            p(-0.5, -0.9),
            p(0.5, -0.9),
            p(0.65, -0.1),
        ],
        gold.darker(0.35),
    );
    cv.convex(
        p(0.0, 0.05),
        &[
            p(0.0, 1.05),
            p(-0.52, -0.1),
            p(-0.4, -0.8),
            p(0.4, -0.8),
            p(0.52, -0.1),
        ],
        gold,
    );
    cv.line(p(0.0, 1.0), p(0.0, 0.0), s * 0.06, gold.darker(0.5));
    cv.circle(p(0.0, -0.05), s * 0.14, gold.darker(0.5));
    cv.rrect(
        c.x - s * 0.5,
        c.y - s * 1.3,
        s,
        s * 0.35,
        3.0,
        rgb(60, 40, 90),
    );
}

fn booth(cx: &mut Ctx, r: Rect, b: &Booth, idx: usize, t: f32, k: f32) {
    let cv = &mut cx.cv;
    let (x, y, w, h) = r;
    let wide = w < h * 1.3;
    ui::panel(
        cv,
        r,
        rgb(255, 250, 240).a(k),
        rgb(240, 225, 205).a(k),
        18.0,
    );
    // The awning: stripes with a scalloped edge.
    let aw = h * 0.16;
    let n = ((w / 26.0) as usize).max(6);
    let sw = w / n as f32;
    for i in 0..n {
        let col = if i % 2 == 0 { b.a } else { WHITE };
        let x0 = x + i as f32 * sw;
        let left = if i == 0 { 18.0 } else { 0.0 };
        let right = if i == n - 1 { 18.0 } else { 0.0 };
        if left > 0.0 || right > 0.0 {
            cv.rrect(x0, y, sw, aw, 14.0, col.a(k));
            cv.rect(
                x0 + if left > 0.0 { sw * 0.5 } else { 0.0 },
                y,
                sw * 0.5,
                aw,
                col.a(k),
            );
            cv.rect(x0, y + aw * 0.5, sw, aw * 0.5, col.a(k));
        } else {
            cv.rect(x0, y, sw, aw, col.a(k));
        }
        let c = v2(x0 + sw / 2.0, y + aw);
        let pts: Vec<V2> = (0..=10)
            .map(|j| {
                c + v2(
                    -(sw / 2.0) * (j as f32 / 10.0 * std::f32::consts::PI).cos(),
                    (sw / 2.0) * 0.8 * (j as f32 / 10.0 * std::f32::consts::PI).sin(),
                )
            })
            .collect();
        let mut all = vec![c + v2(-sw / 2.0, 0.0)];
        all.extend(pts);
        cv.convex(c + v2(0.0, sw * 0.2), &all, col.a(k));
    }
    // The icon.
    let (ic, ir) = if wide {
        (v2(x + w / 2.0, y + aw + h * 0.26), (w * 0.22).min(h * 0.17))
    } else {
        (v2(x + h * 0.5, y + aw + (h - aw) * 0.5), (h - aw) * 0.36)
    };
    cv.circle(ic + v2(0.0, 3.0), ir, b.b.darker(0.3).a(0.4 * k));
    cv.circle_grad(ic, ir, b.a.lighter(0.25).a(k), b.b.a(k));
    icon(cv, ic, ir, idx, t);
    // The words.
    let name = cx_t(cx.lang, b.name);
    let sub = cx_t(cx.lang, b.sub);
    let best = cx.save.num(b.best);
    let ink = INK.a(k);
    if wide {
        let ty = ic.y + ir + 26.0;
        cx.font
            .draw(cv, name, x + w / 2.0, ty, 22.0, ink, Align::Center);
        cx.font.draw_wrapped(
            cv,
            sub,
            x + w / 2.0,
            ty + 26.0,
            w - 24.0,
            13.0,
            INK.a(0.7 * k),
            Align::Center,
        );
        if best > 0 {
            badge(cx, v2(x + w / 2.0, y + h - 24.0), best, b.a, k);
        }
    } else {
        let tx = x + h * 0.95;
        let ty = y + aw + (h - aw) * 0.32;
        cx.font.draw(cv, name, tx, ty, 23.0, ink, Align::Left);
        let maxw = w - (tx - x) - 16.0;
        cx.font.draw_wrapped(
            cv,
            sub,
            tx,
            ty + 24.0,
            maxw,
            13.0,
            INK.a(0.7 * k),
            Align::Left,
        );
        if best > 0 {
            badge(cx, v2(x + w - 52.0, y + aw + 16.0), best, b.a, k);
        }
    }
    // A glint that runs over the card now and then.
    let g = ((t * 0.35 + idx as f32 * 0.33) % 1.0) * 3.0 - 1.0;
    if (0.0..1.0).contains(&g) {
        let gx = x + w * ease_out_cubic(g);
        cx.cv.convex(
            v2(gx, y + h / 2.0),
            &[
                v2(gx - 10.0, y + aw),
                v2(gx + 14.0, y + aw),
                v2(gx + 4.0, y + h - 4.0),
                v2(gx - 20.0, y + h - 4.0),
            ],
            WHITE.a(0.18 * k),
        );
    }
}

fn badge(cx: &mut Ctx, c: V2, best: u32, col: Col, k: f32) {
    let s = cx.tf("menu.best", &[&best]);
    let w = cx.font.width(&s, 13.0) + 20.0;
    cx.cv.rrect(
        c.x - w / 2.0,
        c.y - 12.0,
        w,
        24.0,
        12.0,
        col.darker(0.15).a(k),
    );
    cx.font
        .draw(&mut cx.cv, &s, c.x, c.y, 13.0, WHITE.a(k), Align::Center);
}

/// The booths' little pictures: a fish, a cut lantern, a pot on the wheel.
fn icon(cv: &mut Canvas, c: V2, r: f32, idx: usize, t: f32) {
    match idx {
        0 => {
            let sw = (t * 6.0).sin() * 0.25;
            let body = rgb(255, 170, 60);
            let tail = c + v2(r * 0.45, 0.0);
            cv.convex(
                tail,
                &[
                    tail,
                    tail + v2(r * 0.35, -r * 0.3).rot(sw),
                    tail + v2(r * 0.35, r * 0.3).rot(sw),
                ],
                body.darker(0.2),
            );
            cv.ellipse(c + v2(-r * 0.05, 0.0), r * 0.55, r * 0.32, 0.0, body);
            cv.ellipse(
                c + v2(-r * 0.1, r * 0.1),
                r * 0.4,
                r * 0.15,
                0.0,
                body.lighter(0.4),
            );
            cv.circle(c + v2(-r * 0.35, -r * 0.07), r * 0.08, WHITE);
            cv.circle(c + v2(-r * 0.37, -r * 0.07), r * 0.045, INK);
            for k in 0..3 {
                let ph = (t * 0.8 + k as f32 * 0.33) % 1.0;
                cv.ring(
                    c + v2(-r * 0.7, -r * 0.2 - ph * r * 0.6),
                    r * 0.06 * (0.5 + ph),
                    1.2,
                    WHITE.a(1.0 - ph),
                );
            }
        }
        1 => {
            // A lantern cut on the diagonal, its halves drifting apart.
            let open = 0.10 + 0.08 * (t * 3.0).sin().abs();
            let col = rgb(255, 95, 70);
            let cut = -0.6f32;
            let n = v2(-cut.sin(), cut.cos());
            for s in [-1.0f32, 1.0] {
                let o = n * (s * r * open);
                let start = cut + if s < 0.0 { std::f32::consts::PI } else { 0.0 };
                let pts: Vec<V2> = (0..=16)
                    .map(|k| {
                        c + o
                            + v2(r * 0.55, 0.0).rot(start + k as f32 / 16.0 * std::f32::consts::PI)
                    })
                    .collect();
                cv.convex(c + o + n * (s * r * 0.2), &pts, col);
                let d = v2(r * 0.55, 0.0).rot(cut);
                cv.line(c + o - d, c + o + d, 3.0, rgb(255, 225, 120));
            }
            let d = v2(r * 0.95, 0.0).rot(cut);
            cv.line(c - d, c + d, 2.0, WHITE.a(0.85));
        }
        _ => {
            let clay = rgb(205, 120, 70);
            cv.ellipse(
                c + v2(0.0, r * 0.56),
                r * 0.8,
                r * 0.12,
                0.0,
                rgb(150, 145, 160),
            );
            let prof = [0.3f32, 0.42, 0.5, 0.46, 0.32, 0.2, 0.22, 0.3];
            let n = prof.len();
            let (top, bot) = (c.y - r * 0.6, c.y + r * 0.5);
            for i in 0..n - 1 {
                let y0 = bot - (bot - top) * i as f32 / (n - 1) as f32;
                let y1 = bot - (bot - top) * (i + 1) as f32 / (n - 1) as f32;
                let (a, b) = (prof[i] * r * 1.4, prof[i + 1] * r * 1.4);
                cv.quad4(
                    [
                        v2(c.x - a, y0),
                        v2(c.x + a, y0),
                        v2(c.x + b, y1),
                        v2(c.x - b, y1),
                    ],
                    [
                        clay.darker(0.25),
                        clay.darker(0.1),
                        clay.darker(0.1),
                        clay.darker(0.25),
                    ],
                );
                cv.quad4(
                    [
                        v2(c.x - a * 0.6, y0),
                        v2(c.x - a * 0.1, y0),
                        v2(c.x - b * 0.1, y1),
                        v2(c.x - b * 0.6, y1),
                    ],
                    [clay.lighter(0.25); 4],
                );
            }
            let ph = (t * 4.0) % 1.0;
            cv.line(
                v2(c.x - r * 0.6 + ph * r * 1.2, bot + r * 0.05),
                v2(c.x - r * 0.5 + ph * r * 1.2, bot + r * 0.05),
                2.0,
                WHITE.a(0.5),
            );
        }
    }
}
