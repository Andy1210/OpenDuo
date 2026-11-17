//! Text: the system's bold sans face (fontconfig's "sans:bold", DejaVu Sans
//! Bold on the phone) rasterised by fontdue into one atlas texture, glyph
//! by glyph as they are first needed, at the device's pixel size (crisp at
//! the panels' scale 2.5). The atlas' corner holds the white texel every
//! other shape uses (gfx.rs), so text and shapes are one draw call.

use crate::gfx::{Canvas, Col};
use nibfair::geom::v2;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

/// The glyph cache's hash: its keys are a char and a pixel size, looked up for every glyph
/// drawn (std's SipHash was 3 % of a frame's CPU). One multiply, keys from the game itself.
#[derive(Default)]
struct GlyphHasher(u64);

impl Hasher for GlyphHasher {
    fn finish(&self) -> u64 {
        self.0
    }
    fn write(&mut self, bytes: &[u8]) {
        for b in bytes {
            self.write_u64(*b as u64);
        }
    }
    fn write_u32(&mut self, v: u32) {
        self.write_u64(v as u64);
    }
    fn write_u16(&mut self, v: u16) {
        self.write_u64(v as u64);
    }
    fn write_u64(&mut self, v: u64) {
        self.0 = (self.0.rotate_left(5) ^ v).wrapping_mul(0x51_7c_c1_b7_27_22_0a_95);
    }
}

pub const ATLAS: usize = 2048;

#[derive(Clone, Copy)]
struct Glyph {
    uv: [f32; 4],
    /// Bitmap size and offset from the pen position (device px, y up from the baseline).
    w: f32,
    h: f32,
    xmin: f32,
    ymin: f32,
    advance: f32,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Center,
    Right,
}

pub struct Font {
    font: fontdue::Font,
    glyphs: HashMap<(char, u16), Glyph, BuildHasherDefault<GlyphHasher>>,
    /// The atlas' pixels (RGBA, white with the coverage as alpha).
    pub pixels: Vec<u8>,
    /// Rows changed since the last upload (y0, y1).
    /// The atlas' part changed since the last upload: x0, y0, x1, y1 (pixels, ends excluded).
    pub dirty: Option<(usize, usize, usize, usize)>,
    shelf_x: usize,
    shelf_y: usize,
    shelf_h: usize,
    /// Device pixels per canvas unit.
    pub density: f32,
}

/// The uv of the white texel (the shapes').
pub fn white_uv() -> (f32, f32) {
    (2.0 / ATLAS as f32, 2.0 / ATLAS as f32)
}

/// The face's file: fontconfig first, then the usual Debian paths.
pub fn find_face() -> Option<Vec<u8>> {
    if let Ok(p) = std::env::var("NIBFAIR_FONT") {
        if let Ok(b) = std::fs::read(p) {
            return Some(b);
        }
    }
    let mut paths = Vec::new();
    if let Ok(out) = std::process::Command::new("fc-match")
        .args(["-f", "%{file}", "sans:bold"])
        .output()
    {
        if out.status.success() {
            paths.push(String::from_utf8_lossy(&out.stdout).trim().to_owned());
        }
    }
    paths.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        .map(String::from),
    );
    paths.iter().find_map(|p| std::fs::read(p).ok())
}

impl Font {
    pub fn new(face: &[u8], density: f32) -> Result<Font, String> {
        let font = fontdue::Font::from_bytes(face, fontdue::FontSettings::default())
            .map_err(|e| e.to_string())?;
        let mut f = Font {
            font,
            glyphs: HashMap::default(),
            pixels: vec![0; ATLAS * ATLAS * 4],
            dirty: None,
            shelf_x: 8,
            shelf_y: 0,
            shelf_h: 8,
            density,
        };
        f.reset();
        Ok(f)
    }

    /// Empty the atlas (all glyphs drawn again on demand), keeping the white block.
    pub fn reset(&mut self) {
        self.pixels.fill(0);
        for y in 0..6 {
            for x in 0..6 {
                let o = (y * ATLAS + x) * 4;
                self.pixels[o..o + 4].copy_from_slice(&[255, 255, 255, 255]);
            }
        }
        self.glyphs.clear();
        self.shelf_x = 8;
        self.shelf_y = 0;
        self.shelf_h = 8;
        self.dirty = Some((0, 0, ATLAS, ATLAS));
    }

    pub fn set_density(&mut self, d: f32) {
        if (d - self.density).abs() > 1e-3 {
            self.density = d;
            self.reset();
        }
    }

    fn glyph(&mut self, ch: char, px: u16) -> Glyph {
        if let Some(g) = self.glyphs.get(&(ch, px)) {
            return *g;
        }
        let (m, bitmap) = self.font.rasterize(ch, px as f32);
        let (w, h) = (m.width, m.height);
        if self.shelf_x + w + 2 > ATLAS {
            self.shelf_x = 1;
            self.shelf_y += self.shelf_h + 2;
            self.shelf_h = 0;
        }
        if self.shelf_y + h + 2 > ATLAS {
            self.reset(); // full: start over (the frame's earlier glyphs are drawn again next frame)
        }
        let (x, y) = (self.shelf_x, self.shelf_y);
        for row in 0..h {
            for col in 0..w {
                let o = ((y + row) * ATLAS + x + col) * 4;
                self.pixels[o..o + 4].copy_from_slice(&[255, 255, 255, bitmap[row * w + col]]);
            }
        }
        self.shelf_x += w + 2;
        self.shelf_h = self.shelf_h.max(h);
        let (x1, y1) = ((x + w + 1).min(ATLAS), (y + h + 1).min(ATLAS));
        self.dirty = Some(match self.dirty {
            Some((a, b, c, d)) => (a.min(x), b.min(y), c.max(x1), d.max(y1)),
            None => (x, y, x1, y1),
        });
        let s = ATLAS as f32;
        let g = Glyph {
            uv: [
                x as f32 / s,
                y as f32 / s,
                (x + w) as f32 / s,
                (y + h) as f32 / s,
            ],
            w: w as f32,
            h: h as f32,
            xmin: m.xmin as f32,
            ymin: m.ymin as f32,
            advance: m.advance_width,
        };
        self.glyphs.insert((ch, px), g);
        g
    }

    fn px_for(&self, size: f32) -> u16 {
        (size * self.density).round().clamp(4.0, 400.0) as u16
    }

    /// The width of a line of text at `size` (canvas units).
    pub fn width(&mut self, s: &str, size: f32) -> f32 {
        let px = self.px_for(size);
        let mut w = 0.0;
        let mut prev = None;
        for ch in s.chars() {
            if let Some(p) = prev {
                w += self.font.horizontal_kern(p, ch, px as f32).unwrap_or(0.0);
            }
            w += self.glyph(ch, px).advance;
            prev = Some(ch);
        }
        w / self.density
    }

    /// One line, its capitals centred on `y`. -> its width.
    #[allow(clippy::too_many_arguments)]
    pub fn draw(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        col: Col,
        align: Align,
    ) -> f32 {
        self.draw_scaled(cv, s, x, y, size, 1.0, col, align)
    }

    /// Laid out and rasterised at `size`, shown `scale` times as big about (x, y). A size
    /// that changes every frame (a popup that pops, a pulsing word) goes through here: a new
    /// pixel size would rasterise its glyphs again every frame, upload them to the GPU and
    /// fill the atlas (a full atlas starts over: 16 MB cleared and uploaded, a hitch).
    #[allow(clippy::too_many_arguments)]
    pub fn draw_scaled(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        scale: f32,
        col: Col,
        align: Align,
    ) -> f32 {
        let px = self.px_for(size);
        let width = self.width(s, size);
        let mut pen = match align {
            Align::Left => x,
            Align::Center => x - width / 2.0,
            Align::Right => x - width,
        } * self.density;
        let base = (y + 0.36 * size) * self.density;
        let k = 1.0 / self.density;
        let (sx, sy) = (|v: f32| x + (v - x) * scale, |v: f32| y + (v - y) * scale);
        let mut prev = None;
        for ch in s.chars() {
            if let Some(p) = prev {
                pen += self.font.horizontal_kern(p, ch, px as f32).unwrap_or(0.0);
            }
            let g = self.glyph(ch, px);
            if g.w > 0.0 {
                let x0 = (pen + g.xmin).round();
                let y0 = (base - g.ymin - g.h).round();
                cv.image(
                    sx(x0 * k),
                    sy(y0 * k),
                    sx((x0 + g.w) * k),
                    sy((y0 + g.h) * k),
                    g.uv,
                    col,
                );
            }
            pen += g.advance;
            prev = Some(ch);
        }
        width * scale
    }

    /// With a soft drop shadow (the games' labels).
    #[allow(clippy::too_many_arguments)]
    pub fn draw_shadow(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        col: Col,
        align: Align,
        shadow: Col,
    ) -> f32 {
        self.draw_shadow_scaled(cv, s, x, y, size, 1.0, col, align, shadow)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn draw_shadow_scaled(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        scale: f32,
        col: Col,
        align: Align,
        shadow: Col,
    ) -> f32 {
        let d = (size * 0.06).max(1.0) * scale;
        self.draw_scaled(cv, s, x + d, y + d * 1.4, size, scale, shadow, align);
        self.draw_scaled(cv, s, x, y, size, scale, col, align)
    }

    /// With an outline all around (titles over busy backgrounds).
    #[allow(clippy::too_many_arguments)]
    pub fn draw_outline(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        col: Col,
        align: Align,
        outline: Col,
    ) -> f32 {
        self.draw_outline_scaled(cv, s, x, y, size, 1.0, col, align, outline)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn draw_outline_scaled(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        size: f32,
        scale: f32,
        col: Col,
        align: Align,
        outline: Col,
    ) -> f32 {
        let d = (size * 0.07).max(1.2) * scale;
        for k in 0..8 {
            let a = k as f32 / 8.0 * std::f32::consts::TAU;
            let o = v2(a.cos(), a.sin()) * d;
            self.draw_scaled(cv, s, x + o.x, y + o.y, size, scale, outline, align);
        }
        self.draw_scaled(cv, s, x, y, size, scale, col, align)
    }

    /// Words wrapped into `max_w`, lines `size * 1.3` apart, the block's top line on `y`.
    /// -> the number of lines.
    #[allow(clippy::too_many_arguments)]
    pub fn draw_wrapped(
        &mut self,
        cv: &mut Canvas,
        s: &str,
        x: f32,
        y: f32,
        max_w: f32,
        size: f32,
        col: Col,
        align: Align,
    ) -> usize {
        let lines = self.wrap(s, max_w, size);
        for (i, l) in lines.iter().enumerate() {
            self.draw(cv, l, x, y + i as f32 * size * 1.3, size, col, align);
        }
        lines.len()
    }

    pub fn wrap(&mut self, s: &str, max_w: f32, size: f32) -> Vec<String> {
        let mut lines = Vec::new();
        let mut cur = String::new();
        for word in s.split_whitespace() {
            let cand = if cur.is_empty() {
                word.to_owned()
            } else {
                format!("{cur} {word}")
            };
            if !cur.is_empty() && self.width(&cand, size) > max_w {
                lines.push(std::mem::take(&mut cur));
                cur = word.to_owned();
            } else {
                cur = cand;
            }
        }
        if !cur.is_empty() {
            lines.push(cur);
        }
        lines
    }
}
