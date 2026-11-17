//! The few pictures the compositor draws itself: the overview's round
//! buttons (close, move to the other panel), the span pad's two panels. Drawn procedurally into memory
//! (no image files, no font): a dark see-through disc with a white glyph made
//! of anti-aliased strokes, at the panel's physical pixel size.
//!
//! Pure: the output is premultiplied ARGB8888 as Wayland and DRM lay it out
//! in memory (little-endian: B, G, R, A).

use crate::overview::Dir;

/// Which picture.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Icon {
    /// An X: close the window.
    Close,
    /// An arrow: move the window to the panel that way.
    Arrow(Dir),
    /// An exclamation mark on red: the app does not answer (force close).
    Force,
    /// Two panels side by side: the span pad (let go here: both panels).
    Span,
}

/// The disc behind the glyph: black at this opacity.
const DISC_ALPHA: f64 = 0.62;

/// The glyph's stroke width as a share of the icon's size.
const STROKE: f64 = 0.075;

/// Draw `icon` as a `size` x `size` premultiplied ARGB8888 image.
pub fn draw(icon: Icon, size: usize) -> Vec<u8> {
    let segments = glyph(icon);
    let s = size as f64;
    let radius = s / 2.0 - 0.5;
    let half_stroke = s * STROKE / 2.0;
    let mut out = vec![0u8; size * size * 4];
    for py in 0..size {
        for px in 0..size {
            let (x, y) = (px as f64 + 0.5, py as f64 + 0.5);
            let disc = (radius - ((x - s / 2.0).hypot(y - s / 2.0)) + 0.5).clamp(0.0, 1.0);
            if disc <= 0.0 {
                continue;
            }
            let d = segments
                .iter()
                .map(|&((ax, ay), (bx, by))| segment_distance((x, y), (ax * s, ay * s), (bx * s, by * s)))
                .fold(f64::INFINITY, f64::min);
            let ink = (half_stroke - d + 0.5).clamp(0.0, 1.0);
            // White ink over the disc (black and see-through, or red for
            // Force): premultiplied, the ink over the disc's colour.
            let (rgb, disc_alpha) = disc_colour(icon);
            let alpha = disc * (disc_alpha * (1.0 - ink) + ink);
            let channel = |c: f64| {
                let v = disc * (ink + (1.0 - ink) * disc_alpha * c);
                (v * 255.0).round() as u8
            };
            let i = (py * size + px) * 4;
            out[i] = channel(rgb[2]);
            out[i + 1] = channel(rgb[1]);
            out[i + 2] = channel(rgb[0]);
            out[i + 3] = (alpha * 255.0).round() as u8;
        }
    }
    out
}

/// A pill (a rectangle with fully rounded ends) of `w` x `h` pixels in one
/// opaque colour, anti-aliased at its edge: the split divider's handle.
/// Premultiplied ARGB8888 like [`draw`].
pub fn pill(w: usize, h: usize, rgb: [u8; 3]) -> Vec<u8> {
    let (fw, fh) = (w as f64, h as f64);
    let r = fw.min(fh) / 2.0;
    // The segment the pill is drawn around: its long axis, inset by r.
    let (a, b) =
        if fw >= fh { ((r, fh / 2.0), (fw - r, fh / 2.0)) } else { ((fw / 2.0, r), (fw / 2.0, fh - r)) };
    let mut out = vec![0u8; w * h * 4];
    for py in 0..h {
        for px in 0..w {
            let p = (px as f64 + 0.5, py as f64 + 0.5);
            let coverage = (r - segment_distance(p, a, b) + 0.5).clamp(0.0, 1.0);
            let i = (py * w + px) * 4;
            out[i] = (rgb[2] as f64 * coverage).round() as u8;
            out[i + 1] = (rgb[1] as f64 * coverage).round() as u8;
            out[i + 2] = (rgb[0] as f64 * coverage).round() as u8;
            out[i + 3] = (255.0 * coverage).round() as u8;
        }
    }
    out
}

type Segment = ((f64, f64), (f64, f64));

/// The glyph's strokes, in fractions of the icon's size.
/// The disc behind an icon's glyph: colour (straight, 0..1) and opacity.
fn disc_colour(icon: Icon) -> ([f64; 3], f64) {
    match icon {
        Icon::Force => ([0.878, 0.106, 0.141], 0.95),
        _ => ([0.0, 0.0, 0.0], DISC_ALPHA),
    }
}

fn glyph(icon: Icon) -> Vec<Segment> {
    match icon {
        Icon::Force => vec![((0.5, 0.27), (0.5, 0.56)), ((0.5, 0.72), (0.5, 0.725))],
        Icon::Close => vec![((0.34, 0.34), (0.66, 0.66)), ((0.66, 0.34), (0.34, 0.66))],
        Icon::Span => {
            // Two upright rectangles with a gap between them.
            let mut out = Vec::new();
            for (x0, x1) in [(0.22, 0.44), (0.56, 0.78)] {
                let (y0, y1) = (0.32, 0.68);
                out.extend([
                    ((x0, y0), (x1, y0)),
                    ((x1, y0), (x1, y1)),
                    ((x1, y1), (x0, y1)),
                    ((x0, y1), (x0, y0)),
                ]);
            }
            out
        }
        Icon::Arrow(dir) => {
            // Pointing right, then turned.
            let right: [Segment; 3] =
                [((0.28, 0.5), (0.72, 0.5)), ((0.72, 0.5), (0.55, 0.33)), ((0.72, 0.5), (0.55, 0.67))];
            let turn = |(x, y): (f64, f64)| -> (f64, f64) {
                match dir {
                    Dir::Right => (x, y),
                    Dir::Left => (1.0 - x, y),
                    Dir::Down => (y, x),
                    Dir::Up => (y, 1.0 - x),
                }
            };
            right.iter().map(|&(a, b)| (turn(a), turn(b))).collect()
        }
    }
}

fn segment_distance(p: (f64, f64), a: (f64, f64), b: (f64, f64)) -> f64 {
    let (abx, aby) = (b.0 - a.0, b.1 - a.1);
    let (apx, apy) = (p.0 - a.0, p.1 - a.1);
    let len2 = abx * abx + aby * aby;
    let t = if len2 > 0.0 { ((apx * abx + apy * aby) / len2).clamp(0.0, 1.0) } else { 0.0 };
    (apx - t * abx).hypot(apy - t * aby)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pixel(img: &[u8], size: usize, x: usize, y: usize) -> [u8; 4] {
        let i = (y * size + x) * 4;
        [img[i], img[i + 1], img[i + 2], img[i + 3]]
    }

    #[test]
    fn corners_are_clear_and_the_disc_is_dark() {
        let img = draw(Icon::Close, 100);
        assert_eq!(img.len(), 100 * 100 * 4);
        assert_eq!(pixel(&img, 100, 0, 0), [0, 0, 0, 0]);
        assert_eq!(pixel(&img, 100, 99, 99), [0, 0, 0, 0]);
        // On the disc, away from the X: black, see-through.
        let p = pixel(&img, 100, 50, 15);
        assert_eq!(&p[..3], &[0, 0, 0]);
        assert_eq!(p[3], (DISC_ALPHA * 255.0).round() as u8);
    }

    #[test]
    fn the_x_crosses_the_middle() {
        let img = draw(Icon::Close, 100);
        assert_eq!(pixel(&img, 100, 50, 50), [255, 255, 255, 255]);
    }

    #[test]
    fn premultiplied_everywhere() {
        for icon in [Icon::Close, Icon::Arrow(Dir::Left), Icon::Arrow(Dir::Up), Icon::Force, Icon::Span] {
            let img = draw(icon, 64);
            for px in img.chunks(4) {
                assert!(px[0] <= px[3] && px[1] <= px[3] && px[2] <= px[3]);
            }
        }
    }

    #[test]
    fn a_pill_is_solid_inside_and_round_at_the_ends() {
        let (w, h) = (10, 60);
        let img = pill(w, h, [10, 20, 30]);
        assert_eq!(img.len(), w * h * 4);
        // The middle: the colour (B, G, R), opaque.
        let i = (30 * w + 5) * 4;
        assert_eq!(&img[i..i + 4], &[30, 20, 10, 255]);
        // The very corners are outside the round ends.
        assert_eq!(img[3], 0);
        assert_eq!(img[((h - 1) * w + (w - 1)) * 4 + 3], 0);
        for px in img.chunks(4) {
            assert!(px[0] <= px[3] && px[1] <= px[3] && px[2] <= px[3]);
        }
    }

    #[test]
    fn the_span_icon_is_two_panels() {
        let size = 100;
        let img = draw(Icon::Span, size);
        // Ink on both rectangles' outer edges, none in the gap between them.
        assert_eq!(pixel(&img, size, 22, 50)[0], 255);
        assert_eq!(pixel(&img, size, 77, 50)[0], 255);
        assert_eq!(pixel(&img, size, 50, 50)[0], 0);
        assert_eq!(pixel(&img, size, 33, 32)[0], 255);
    }

    #[test]
    fn arrows_point_where_they_say() {
        // The arrow's head is on the side it points to: ink there, and the
        // tail's far end on the other side.
        let size = 100;
        let right = draw(Icon::Arrow(Dir::Right), size);
        let left = draw(Icon::Arrow(Dir::Left), size);
        // The head's tips at (0.55, 0.33) for right, mirrored for left.
        assert_eq!(pixel(&right, size, 56, 34)[0], 255);
        assert_eq!(pixel(&left, size, 44, 34)[0], 255);
        assert_eq!(pixel(&right, size, 44, 34)[0], 0);
        let down = draw(Icon::Arrow(Dir::Down), size);
        let up = draw(Icon::Arrow(Dir::Up), size);
        assert_eq!(pixel(&down, size, 34, 56)[0], 255);
        assert_eq!(pixel(&up, size, 34, 44)[0], 255);
    }
}
