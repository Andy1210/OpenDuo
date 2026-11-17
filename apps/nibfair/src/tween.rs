//! Easing and smoothing helpers (the juice: springs, pops, fades).

pub fn clamp01(x: f32) -> f32 {
    x.clamp(0.0, 1.0)
}

pub fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

/// 0 at `a`, 1 at `b`, smooth in between.
pub fn smoothstep(a: f32, b: f32, x: f32) -> f32 {
    let t = clamp01((x - a) / (b - a));
    t * t * (3.0 - 2.0 * t)
}

pub fn ease_out_cubic(t: f32) -> f32 {
    let t = clamp01(t) - 1.0;
    t * t * t + 1.0
}

pub fn ease_in_out(t: f32) -> f32 {
    let t = clamp01(t);
    if t < 0.5 {
        4.0 * t * t * t
    } else {
        let u = -2.0 * t + 2.0;
        1.0 - u * u * u / 2.0
    }
}

/// An overshooting pop: 0 -> 1 with a bounce past 1 (a button appearing).
pub fn ease_out_back(t: f32) -> f32 {
    let t = clamp01(t);
    let c1 = 1.70158;
    let c3 = c1 + 1.0;
    let u = t - 1.0;
    1.0 + c3 * u * u * u + c1 * u * u
}

/// Exponential approach of `current` to `target` (frame-rate independent):
/// `rate` per second.
pub fn approach(current: f32, target: f32, rate: f32, dt: f32) -> f32 {
    target + (current - target) * (-rate * dt).exp()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ends_are_fixed() {
        for f in [ease_out_cubic, ease_in_out, ease_out_back] {
            assert!(f(0.0).abs() < 1e-5);
            assert!((f(1.0) - 1.0).abs() < 1e-5);
        }
        assert!(ease_out_back(0.7) > 1.0, "the pop overshoots");
        assert_eq!(smoothstep(0.0, 1.0, -1.0), 0.0);
        assert_eq!(smoothstep(0.0, 1.0, 2.0), 1.0);
    }

    #[test]
    fn approach_is_frame_rate_independent() {
        let mut a = 0.0;
        for _ in 0..60 {
            a = approach(a, 1.0, 3.0, 1.0 / 60.0);
        }
        let b = approach(0.0, 1.0, 3.0, 1.0);
        assert!((a - b).abs() < 1e-4);
    }
}
