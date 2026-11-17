//! 2D vectors and the hit tests the games use.

use std::ops::{Add, Mul, Sub};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct V2 {
    pub x: f32,
    pub y: f32,
}

pub const fn v2(x: f32, y: f32) -> V2 {
    V2 { x, y }
}

impl V2 {
    pub fn len(self) -> f32 {
        // Not hypot: libm's hypotf guards against overflow and was a tenth of a frame's CPU
        // (every outline's feather normalises its edges); screen coordinates never come near.
        (self.x * self.x + self.y * self.y).sqrt()
    }
    pub fn dot(self, o: V2) -> f32 {
        self.x * o.x + self.y * o.y
    }
    pub fn norm(self) -> V2 {
        let l = self.len();
        if l > 1e-6 {
            v2(self.x / l, self.y / l)
        } else {
            v2(0.0, 0.0)
        }
    }
    /// Turned 90 degrees (counter-clockwise on screen with y down: (x, y) -> (y, -x)).
    pub fn perp(self) -> V2 {
        v2(self.y, -self.x)
    }
    pub fn rot(self, a: f32) -> V2 {
        let (s, c) = a.sin_cos();
        v2(self.x * c - self.y * s, self.x * s + self.y * c)
    }
}

impl Add for V2 {
    type Output = V2;
    fn add(self, o: V2) -> V2 {
        v2(self.x + o.x, self.y + o.y)
    }
}

impl Sub for V2 {
    type Output = V2;
    fn sub(self, o: V2) -> V2 {
        v2(self.x - o.x, self.y - o.y)
    }
}

impl Mul<f32> for V2 {
    type Output = V2;
    fn mul(self, k: f32) -> V2 {
        v2(self.x * k, self.y * k)
    }
}

/// The closest point of segment a-b to p, as the segment parameter t (0..1).
pub fn closest_t(a: V2, b: V2, p: V2) -> f32 {
    let ab = b - a;
    let l2 = ab.dot(ab);
    if l2 < 1e-9 {
        return 0.0;
    }
    ((p - a).dot(ab) / l2).clamp(0.0, 1.0)
}

/// The distance of p from segment a-b.
pub fn seg_dist(a: V2, b: V2, p: V2) -> f32 {
    let t = closest_t(a, b, p);
    (a + (b - a) * t - p).len()
}

/// Does segment a-b pass within `r` of `c`?
pub fn seg_hits_circle(a: V2, b: V2, c: V2, r: f32) -> bool {
    seg_dist(a, b, c) <= r
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn segment_distance() {
        let a = v2(0.0, 0.0);
        let b = v2(10.0, 0.0);
        assert_eq!(seg_dist(a, b, v2(5.0, 3.0)), 3.0);
        assert_eq!(seg_dist(a, b, v2(13.0, 4.0)), 5.0);
        assert!(seg_hits_circle(a, b, v2(5.0, 2.0), 2.0));
        assert!(!seg_hits_circle(a, b, v2(5.0, 2.5), 2.0));
        assert_eq!(seg_dist(a, a, v2(3.0, 4.0)), 5.0, "a point segment");
    }

    #[test]
    fn rotation_and_perp() {
        let p = v2(1.0, 0.0).rot(std::f32::consts::FRAC_PI_2);
        assert!((p.x).abs() < 1e-6 && (p.y - 1.0).abs() < 1e-6);
        assert_eq!(v2(1.0, 0.0).perp(), v2(0.0, -1.0));
    }
}
