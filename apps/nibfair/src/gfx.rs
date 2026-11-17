//! The canvas: every shape of a frame as triangles in one vertex list,
//! drawn with one SDL_RenderGeometry call (SDL's GLES/GL renderer).
//!
//! Everything samples one texture, the font atlas (text.rs): shapes use its
//! white texel, glyphs their own rectangle, so a whole frame is one batch.
//! Edges are antialiased by a feather: an outer ring of vertices about one
//! device pixel wide whose alpha falls to 0 (the renderer has no MSAA).
//! Units are the window's logical points (the renderer scales them to pixels).

use nibfair::geom::{v2, V2};
use sdl3_sys::pixels::SDL_FColor;
use sdl3_sys::rect::SDL_FPoint;
use sdl3_sys::render::SDL_Vertex;
use std::f32::consts::TAU;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Col {
    pub r: f32,
    pub g: f32,
    pub b: f32,
    pub a: f32,
}

pub const fn rgb(r: u8, g: u8, b: u8) -> Col {
    Col {
        r: r as f32 / 255.0,
        g: g as f32 / 255.0,
        b: b as f32 / 255.0,
        a: 1.0,
    }
}

pub const fn rgba(r: u8, g: u8, b: u8, a: f32) -> Col {
    Col {
        r: r as f32 / 255.0,
        g: g as f32 / 255.0,
        b: b as f32 / 255.0,
        a,
    }
}

pub const WHITE: Col = rgb(255, 255, 255);
pub const BLACK: Col = rgb(0, 0, 0);

impl Col {
    pub fn a(self, a: f32) -> Col {
        Col {
            a: self.a * a,
            ..self
        }
    }
    pub fn mix(self, o: Col, t: f32) -> Col {
        let t = t.clamp(0.0, 1.0);
        Col {
            r: self.r + (o.r - self.r) * t,
            g: self.g + (o.g - self.g) * t,
            b: self.b + (o.b - self.b) * t,
            a: self.a + (o.a - self.a) * t,
        }
    }
    pub fn lighter(self, t: f32) -> Col {
        self.mix(Col { a: self.a, ..WHITE }, t)
    }
    pub fn darker(self, t: f32) -> Col {
        self.mix(Col { a: self.a, ..BLACK }, t)
    }
    pub fn from3(c: (u8, u8, u8)) -> Col {
        rgb(c.0, c.1, c.2)
    }
    fn fc(self) -> SDL_FColor {
        SDL_FColor {
            r: self.r,
            g: self.g,
            b: self.b,
            a: self.a,
        }
    }
}

pub struct Canvas {
    pub v: Vec<SDL_Vertex>,
    pub i: Vec<i32>,
    /// The atlas' white texel (its uv).
    pub white: SDL_FPoint,
    /// One device pixel in canvas units (the feather's width).
    pub px: f32,
    /// Outlines built and drawn in the same call reuse this (no allocation per shape).
    scratch: Vec<V2>,
    normals: Vec<V2>,
    ribbon_scratch: Vec<(V2, f32, Col)>,
}

fn fp(p: V2) -> SDL_FPoint {
    SDL_FPoint { x: p.x, y: p.y }
}

impl Canvas {
    pub fn new(white: (f32, f32)) -> Canvas {
        Canvas {
            v: Vec::with_capacity(1 << 16),
            i: Vec::with_capacity(1 << 17),
            white: SDL_FPoint {
                x: white.0,
                y: white.1,
            },
            px: 1.0,
            scratch: Vec::with_capacity(512),
            normals: Vec::with_capacity(512),
            ribbon_scratch: Vec::with_capacity(256),
        }
    }

    pub fn clear(&mut self) {
        self.v.clear();
        self.i.clear();
    }

    pub fn vert(&mut self, p: V2, c: Col) -> i32 {
        self.v.push(SDL_Vertex {
            position: fp(p),
            color: c.fc(),
            tex_coord: self.white,
        });
        (self.v.len() - 1) as i32
    }

    pub fn vert_uv(&mut self, p: V2, c: Col, u: f32, v: f32) -> i32 {
        self.v.push(SDL_Vertex {
            position: fp(p),
            color: c.fc(),
            tex_coord: SDL_FPoint { x: u, y: v },
        });
        (self.v.len() - 1) as i32
    }

    pub fn tri_idx(&mut self, a: i32, b: i32, c: i32) {
        self.i.extend_from_slice(&[a, b, c]);
    }

    pub fn tri(&mut self, a: V2, b: V2, c: V2, col: Col) {
        let (ia, ib, ic) = (self.vert(a, col), self.vert(b, col), self.vert(c, col));
        self.tri_idx(ia, ib, ic);
    }

    /// A quad a-b-c-d (in order around), a colour per corner.
    pub fn quad4(&mut self, p: [V2; 4], c: [Col; 4]) {
        let i = [
            self.vert(p[0], c[0]),
            self.vert(p[1], c[1]),
            self.vert(p[2], c[2]),
            self.vert(p[3], c[3]),
        ];
        self.tri_idx(i[0], i[1], i[2]);
        self.tri_idx(i[0], i[2], i[3]);
    }

    pub fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, col: Col) {
        self.quad4(
            [v2(x, y), v2(x + w, y), v2(x + w, y + h), v2(x, y + h)],
            [col; 4],
        );
    }

    /// A vertical gradient.
    pub fn rect_v(&mut self, x: f32, y: f32, w: f32, h: f32, top: Col, bottom: Col) {
        self.quad4(
            [v2(x, y), v2(x + w, y), v2(x + w, y + h), v2(x, y + h)],
            [top, top, bottom, bottom],
        );
    }

    /// A glyph's textured quad.
    #[allow(clippy::too_many_arguments)]
    pub fn image(&mut self, x0: f32, y0: f32, x1: f32, y1: f32, uv: [f32; 4], col: Col) {
        let a = self.vert_uv(v2(x0, y0), col, uv[0], uv[1]);
        let b = self.vert_uv(v2(x1, y0), col, uv[2], uv[1]);
        let c = self.vert_uv(v2(x1, y1), col, uv[2], uv[3]);
        let d = self.vert_uv(v2(x0, y1), col, uv[0], uv[3]);
        self.tri_idx(a, b, c);
        self.tri_idx(a, c, d);
    }

    /// A convex shape: a fan from `center`, `inner` at the centre, `edge` on
    /// the outline, plus a feather `feather` wide outside it.
    pub fn convex_grad(&mut self, center: V2, outline: &[V2], inner: Col, edge: Col, feather: f32) {
        let n = outline.len();
        if n < 3 {
            return;
        }
        let ic = self.vert(center, inner);
        let first = self.v.len() as i32;
        for p in outline {
            self.vert(*p, edge);
        }
        for k in 0..n as i32 {
            self.tri_idx(ic, first + k, first + (k + 1) % n as i32);
        }
        if feather > 0.0 {
            // Each edge's outward normal once (away from the centre), then each corner's
            // the mean of its two edges'.
            let mut normals = std::mem::take(&mut self.normals);
            normals.clear();
            for k in 0..n {
                let (a, b) = (outline[k], outline[(k + 1) % n]);
                let e = (b - a).norm();
                let nrm = v2(e.y, -e.x);
                normals.push(if nrm.dot((a + b) * 0.5 - center) < 0.0 {
                    nrm * -1.0
                } else {
                    nrm
                });
            }
            let fo = self.v.len() as i32;
            for k in 0..n {
                let p = outline[k];
                let mut d = (normals[(k + n - 1) % n] + normals[k]).norm();
                if d.len() < 0.5 {
                    d = (p - center).norm();
                }
                self.vert(p + d * feather, edge.a(0.0));
            }
            self.normals = normals;
            for k in 0..n as i32 {
                let k1 = (k + 1) % n as i32;
                self.tri_idx(first + k, fo + k, fo + k1);
                self.tri_idx(first + k, fo + k1, first + k1);
            }
        }
    }

    pub fn convex(&mut self, center: V2, outline: &[V2], col: Col) {
        let f = self.px;
        self.convex_grad(center, outline, col, col, f);
    }

    fn segments(&self, r: f32) -> usize {
        ((r / self.px).sqrt() * 4.0).clamp(10.0, 96.0) as usize
    }

    /// An ellipse's outline into `pts` (cleared first). One sin/cos for the step, then a
    /// rotation per point (not a sin and a cos per point).
    fn ellipse_into(&self, pts: &mut Vec<V2>, c: V2, rx: f32, ry: f32, rot: f32) {
        let n = self.segments(rx.max(ry));
        let (s, co) = rot.sin_cos();
        let (ds, dc) = (TAU / n as f32).sin_cos();
        let (mut ux, mut uy) = (1.0f32, 0.0f32);
        pts.clear();
        for _ in 0..n {
            let (x, y) = (ux * rx, uy * ry);
            pts.push(c + v2(x * co - y * s, x * s + y * co));
            (ux, uy) = (ux * dc - uy * ds, ux * ds + uy * dc);
        }
    }

    /// A shape from an outline built into the scratch buffer.
    fn with_outline(
        &mut self,
        build: impl FnOnce(&Canvas, &mut Vec<V2>),
        draw: impl FnOnce(&mut Canvas, &[V2]),
    ) {
        let mut pts = std::mem::take(&mut self.scratch);
        build(self, &mut pts);
        draw(self, &pts);
        self.scratch = pts;
    }

    pub fn circle(&mut self, c: V2, r: f32, col: Col) {
        if r <= 0.0 {
            return;
        }
        self.with_outline(
            |cv, pts| cv.ellipse_into(pts, c, r, r, 0.0),
            |cv, pts| cv.convex(c, pts, col),
        );
    }

    /// A radial gradient (a glow when `outer` is transparent).
    pub fn circle_grad(&mut self, c: V2, r: f32, inner: Col, outer: Col) {
        if r <= 0.0 {
            return;
        }
        self.with_outline(
            |cv, pts| cv.ellipse_into(pts, c, r, r, 0.0),
            |cv, pts| cv.convex_grad(c, pts, inner, outer, 0.0),
        );
    }

    pub fn glow(&mut self, c: V2, r: f32, col: Col) {
        self.circle_grad(c, r, col, col.a(0.0));
    }

    pub fn ellipse(&mut self, c: V2, rx: f32, ry: f32, rot: f32, col: Col) {
        self.with_outline(
            |cv, pts| cv.ellipse_into(pts, c, rx, ry, rot),
            |cv, pts| cv.convex(c, pts, col),
        );
    }

    /// An arc band from angle a0 to a1 (radians, clockwise on screen), `w` wide.
    pub fn arc(&mut self, c: V2, r: f32, w: f32, a0: f32, a1: f32, col: Col) {
        let n = ((self.segments(r) as f32) * ((a1 - a0).abs() / TAU))
            .ceil()
            .max(2.0) as usize;
        let f = self.px;
        let (ri, ro) = (r - w / 2.0, r + w / 2.0);
        let mut prev: Option<[i32; 4]> = None;
        for k in 0..=n {
            let a = a0 + (a1 - a0) * k as f32 / n as f32;
            let d = v2(a.cos(), a.sin());
            let ids = [
                self.vert(c + d * (ri - f), col.a(0.0)),
                self.vert(c + d * ri, col),
                self.vert(c + d * ro, col),
                self.vert(c + d * (ro + f), col.a(0.0)),
            ];
            if let Some(p) = prev {
                for j in 0..3 {
                    self.tri_idx(p[j], ids[j], ids[j + 1]);
                    self.tri_idx(p[j], ids[j + 1], p[j + 1]);
                }
            }
            prev = Some(ids);
        }
    }

    pub fn ring(&mut self, c: V2, r: f32, w: f32, col: Col) {
        self.arc(c, r, w, 0.0, TAU, col);
    }

    /// A straight line `w` wide with round ends.
    pub fn line(&mut self, a: V2, b: V2, w: f32, col: Col) {
        self.ribbon(&[(a, w, col), (b, w, col)]);
        self.circle(a, w / 2.0, col);
        self.circle(b, w / 2.0, col);
    }

    /// A strip through points, each with its own width and colour (trails,
    /// the fishing line, tapered strokes). Feathered on both sides.
    pub fn ribbon(&mut self, pts: &[(V2, f32, Col)]) {
        if pts.len() < 2 {
            return;
        }
        let f = self.px;
        let mut prev: Option<[i32; 4]> = None;
        for k in 0..pts.len() {
            let (p, w, col) = pts[k];
            let dir = if k == 0 {
                pts[1].0 - p
            } else if k == pts.len() - 1 {
                p - pts[k - 1].0
            } else {
                pts[k + 1].0 - pts[k - 1].0
            }
            .norm();
            let n = v2(-dir.y, dir.x);
            let h = (w / 2.0).max(f * 0.5);
            let ids = [
                self.vert(p + n * (h + f), col.a(0.0)),
                self.vert(p + n * h, col),
                self.vert(p - n * h, col),
                self.vert(p - n * (h + f), col.a(0.0)),
            ];
            if let Some(q) = prev {
                for j in 0..3 {
                    self.tri_idx(q[j], ids[j], ids[j + 1]);
                    self.tri_idx(q[j], ids[j + 1], q[j + 1]);
                }
            }
            prev = Some(ids);
        }
    }

    pub fn polyline(&mut self, pts: &[V2], w: f32, col: Col) {
        let mut p = std::mem::take(&mut self.ribbon_scratch);
        p.clear();
        p.extend(pts.iter().map(|q| (*q, w, col)));
        self.ribbon(&p);
        self.ribbon_scratch = p;
    }

    /// A dashed line.
    pub fn dashed(&mut self, a: V2, b: V2, w: f32, dash: f32, col: Col) {
        let len = (b - a).len();
        if len < 1e-3 {
            return;
        }
        let d = (b - a) * (1.0 / len);
        let mut s = 0.0;
        while s < len {
            let e = (s + dash).min(len);
            self.ribbon(&[(a + d * s, w, col), (a + d * e, w, col)]);
            s += dash * 2.0;
        }
    }

    fn rrect_into(&self, pts: &mut Vec<V2>, x: f32, y: f32, w: f32, h: f32, r: f32) {
        let r = r.min(w / 2.0).min(h / 2.0).max(0.0);
        let n = (self.segments(r) / 4).max(3);
        pts.clear();
        let corners = [
            (x + w - r, y + r, -0.25),
            (x + w - r, y + h - r, 0.0),
            (x + r, y + h - r, 0.25),
            (x + r, y + r, 0.5),
        ];
        for (cx, cy, start) in corners {
            for k in 0..=n {
                let a = (start + 0.25 * k as f32 / n as f32) * TAU;
                pts.push(v2(cx + a.cos() * r, cy + a.sin() * r));
            }
        }
    }

    pub fn rrect(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, col: Col) {
        let c = v2(x + w / 2.0, y + h / 2.0);
        self.with_outline(
            |cv, pts| cv.rrect_into(pts, x, y, w, h, r),
            |cv, pts| cv.convex(c, pts, col),
        );
    }

    /// A rounded rectangle with a vertical gradient.
    #[allow(clippy::too_many_arguments)]
    pub fn rrect_v(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, top: Col, bottom: Col) {
        let mut pts = std::mem::take(&mut self.scratch);
        self.rrect_into(&mut pts, x, y, w, h, r);
        self.rrect_v_pts(&pts, x, y, w, h, top, bottom);
        self.scratch = pts;
    }

    #[allow(clippy::too_many_arguments)]
    fn rrect_v_pts(&mut self, pts: &[V2], x: f32, y: f32, w: f32, h: f32, top: Col, bottom: Col) {
        let n = pts.len();
        let c = v2(x + w / 2.0, y + h / 2.0);
        let ic = self.vert(c, top.mix(bottom, 0.5));
        let first = self.v.len() as i32;
        for p in pts {
            let t = ((p.y - y) / h).clamp(0.0, 1.0);
            self.vert(*p, top.mix(bottom, t));
        }
        for k in 0..n as i32 {
            self.tri_idx(ic, first + k, first + (k + 1) % n as i32);
        }
        // The feather.
        let f = self.px;
        let fo = self.v.len() as i32;
        for p in pts {
            let d = (*p - c).norm();
            let t = ((p.y - y) / h).clamp(0.0, 1.0);
            self.vert(*p + d * f, top.mix(bottom, t).a(0.0));
        }
        for k in 0..n as i32 {
            let k1 = (k + 1) % n as i32;
            self.tri_idx(first + k, fo + k, fo + k1);
            self.tri_idx(first + k, fo + k1, first + k1);
        }
    }

    /// A soft shadow under a rounded rectangle: `spread` wide, fading out.
    #[allow(clippy::too_many_arguments)]
    pub fn shadow(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, spread: f32, col: Col) {
        let c = v2(x + w / 2.0, y + h / 2.0);
        self.with_outline(
            |cv, pts| cv.rrect_into(pts, x, y, w, h, r),
            |cv, pts| cv.convex_grad(c, pts, col, col, spread),
        );
    }

    /// A regular polygon (n corners), rotated.
    pub fn ngon(&mut self, c: V2, r: f32, n: usize, rot: f32, col: Col) {
        let pts: Vec<V2> = (0..n)
            .map(|k| c + v2(r, 0.0).rot(rot + k as f32 / n as f32 * TAU))
            .collect();
        self.convex(c, &pts, col);
    }

    /// A five-pointed star.
    pub fn star(&mut self, c: V2, r: f32, rot: f32, col: Col) {
        let ri = r * 0.45;
        let pts: Vec<V2> = (0..10)
            .map(|k| {
                let rr = if k % 2 == 0 { r } else { ri };
                c + v2(0.0, -rr).rot(rot + k as f32 / 10.0 * TAU)
            })
            .collect();
        // Not convex: a fan from the centre still works (it is star-shaped).
        let ic = self.vert(c, col);
        let first = self.v.len() as i32;
        for p in &pts {
            self.vert(*p, col);
        }
        for k in 0..10 {
            self.tri_idx(ic, first + k, first + (k + 1) % 10);
        }
        // Feather each edge.
        let f = self.px;
        for k in 0..10 {
            let (a, b) = (pts[k], pts[(k + 1) % 10]);
            let n = (b - a).perp() * -1.0;
            let n = n.norm();
            let ia = self.vert(a, col);
            let ib = self.vert(b, col);
            let oa = self.vert(a + n * f, col.a(0.0));
            let ob = self.vert(b + n * f, col.a(0.0));
            self.tri_idx(ia, oa, ob);
            self.tri_idx(ia, ob, ib);
        }
    }

    /// A heart (lives), centred at c, `s` across.
    pub fn heart(&mut self, c: V2, s: f32, col: Col) {
        let r = s * 0.27;
        self.circle(c + v2(-r * 0.95, -r * 0.35), r, col);
        self.circle(c + v2(r * 0.95, -r * 0.35), r, col);
        let pts = [
            c + v2(-r * 1.9, -r * 0.1),
            c + v2(r * 1.9, -r * 0.1),
            c + v2(0.0, s * 0.55),
        ];
        self.convex(c + v2(0.0, s * 0.1), &pts, col);
    }
}
