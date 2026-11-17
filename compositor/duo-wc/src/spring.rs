//! Spring animations: everything the compositor moves (a window carried by
//! its top, a window flying to where it was dropped, a window opening or
//! going home, the back arrow) moves on a damped spring, not on a fixed
//! curve: a spring can be given a new target halfway (the finger moved on)
//! and keeps its speed, so nothing jumps.
//!
//! Evaluated in closed form from the moment the spring was (re)started, so a
//! frame that comes late lands where it should be, not one step further.
//! Pure: no Smithay types.

use std::time::Instant;

/// How stiff and how damped a spring is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Params {
    /// The undamped angular frequency (rad/s): higher is faster.
    pub omega: f64,
    /// The damping ratio: 1 settles without overshooting, below 1 overshoots
    /// a little (a "spring" feel), above 1 creeps.
    pub zeta: f64,
}

/// The house springs.
impl Params {
    /// A window following a finger or settling into its place: quick, with a
    /// hint of overshoot (settles in ~300 ms).
    pub const SNAPPY: Params = Params { omega: 26.0, zeta: 0.82 };
    /// A window opening or going home (settles in ~350 ms, no overshoot).
    pub const SMOOTH: Params = Params { omega: 20.0, zeta: 1.0 };
}

/// One value on a spring.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Spring {
    /// Where it started (at `start`) and how fast it was going then.
    from: f64,
    velocity: f64,
    /// Where it is going.
    pub to: f64,
    start: Instant,
    params: Params,
}

/// Below these it counts as at rest.
const REST_DISTANCE: f64 = 0.25;
const REST_SPEED: f64 = 2.0;

impl Spring {
    /// A spring at rest at `value`.
    pub fn at(value: f64, now: Instant, params: Params) -> Self {
        Spring { from: value, velocity: 0.0, to: value, start: now, params }
    }

    /// A spring going from `from` to `to`, starting now at rest.
    pub fn new(from: f64, to: f64, now: Instant, params: Params) -> Self {
        Spring { from, velocity: 0.0, to, start: now, params }
    }

    /// Value and speed `t` seconds after the start.
    fn eval(&self, t: f64) -> (f64, f64) {
        let Params { omega, zeta } = self.params;
        let x0 = self.from - self.to;
        let v0 = self.velocity;
        let t = t.max(0.0);
        if zeta >= 1.0 {
            // Critically damped (or treated as such above 1).
            let c2 = v0 + omega * x0;
            let e = (-omega * t).exp();
            let x = (x0 + c2 * t) * e;
            let v = (c2 - omega * (x0 + c2 * t)) * e;
            (self.to + x, v)
        } else {
            let wd = omega * (1.0 - zeta * zeta).sqrt();
            let c2 = (v0 + zeta * omega * x0) / wd;
            let e = (-zeta * omega * t).exp();
            let (s, c) = (wd * t).sin_cos();
            let x = e * (x0 * c + c2 * s);
            let v = e * ((c2 * wd - zeta * omega * x0) * c - (x0 * wd + zeta * omega * c2) * s);
            (self.to + x, v)
        }
    }

    fn since(&self, now: Instant) -> f64 {
        now.saturating_duration_since(self.start).as_secs_f64()
    }

    /// Where it is at `now`.
    pub fn value(&self, now: Instant) -> f64 {
        self.eval(self.since(now)).0
    }

    /// Whether it has come to rest at its target.
    pub fn done(&self, now: Instant) -> bool {
        let (x, v) = self.eval(self.since(now));
        (x - self.to).abs() < REST_DISTANCE && v.abs() < REST_SPEED
    }

    /// Go somewhere else from where it is now, keeping its speed.
    pub fn retarget(&mut self, to: f64, now: Instant) {
        if (to - self.to).abs() < f64::EPSILON {
            return;
        }
        let (x, v) = self.eval(self.since(now));
        *self = Spring { from: x, velocity: v, to, start: now, params: self.params };
    }

    /// Jump to `value` and stay there.
    pub fn set(&mut self, value: f64, now: Instant) {
        *self = Spring::at(value, now, self.params);
    }
}

/// A rectangle (x, y, w, h) on four springs.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RectSpring {
    parts: [Spring; 4],
}

impl RectSpring {
    /// From `from` to `to`, starting now.
    pub fn new(from: (f64, f64, f64, f64), to: (f64, f64, f64, f64), now: Instant, params: Params) -> Self {
        let f = [from.0, from.1, from.2, from.3];
        let t = [to.0, to.1, to.2, to.3];
        RectSpring { parts: std::array::from_fn(|i| Spring::new(f[i], t[i], now, params)) }
    }

    /// Where it is now.
    pub fn value(&self, now: Instant) -> (f64, f64, f64, f64) {
        let v: [f64; 4] = std::array::from_fn(|i| self.parts[i].value(now));
        (v[0], v[1], v[2], v[3].max(0.0))
    }

    /// Where it is going.
    pub fn target(&self) -> (f64, f64, f64, f64) {
        (self.parts[0].to, self.parts[1].to, self.parts[2].to, self.parts[3].to)
    }

    /// Go somewhere else, keeping the speed.
    pub fn retarget(&mut self, to: (f64, f64, f64, f64), now: Instant) {
        let t = [to.0, to.1, to.2, to.3];
        for (part, to) in self.parts.iter_mut().zip(t) {
            part.retarget(to, now);
        }
    }

    /// At rest at its target.
    pub fn done(&self, now: Instant) -> bool {
        self.parts.iter().all(|p| p.done(now))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn at(start: Instant, ms: u64) -> Instant {
        start + Duration::from_millis(ms)
    }

    #[test]
    fn a_spring_starts_where_it_is_and_settles_on_its_target() {
        let t0 = Instant::now();
        for params in [Params::SNAPPY, Params::SMOOTH] {
            let s = Spring::new(0.0, 100.0, t0, params);
            assert!((s.value(t0) - 0.0).abs() < 1e-9);
            assert!(!s.done(t0));
            // Most of the way within ~150 ms, at rest by 600 ms.
            assert!(s.value(at(t0, 150)) > 70.0, "{params:?}");
            assert!(s.done(at(t0, 600)), "{params:?}");
            assert!((s.value(at(t0, 2000)) - 100.0).abs() < 0.01);
        }
    }

    #[test]
    fn the_snappy_spring_overshoots_a_little_the_smooth_one_not() {
        let t0 = Instant::now();
        let snappy = Spring::new(0.0, 100.0, t0, Params::SNAPPY);
        let smooth = Spring::new(0.0, 100.0, t0, Params::SMOOTH);
        let peak = |s: &Spring| (0..800).map(|ms| s.value(at(t0, ms))).fold(f64::MIN, f64::max);
        let p = peak(&snappy);
        assert!(p > 100.0 && p < 103.0, "overshoot {p}");
        assert!(peak(&smooth) <= 100.0 + 1e-9);
    }

    #[test]
    fn retargeting_keeps_the_place_and_the_speed() {
        let t0 = Instant::now();
        let mut s = Spring::new(0.0, 100.0, t0, Params::SNAPPY);
        let mid = at(t0, 60);
        let before = s.value(mid);
        let before_next = s.value(at(t0, 61));
        s.retarget(-50.0, mid);
        // No jump at the moment of the retarget...
        assert!((s.value(mid) - before).abs() < 1e-9);
        // ...and it keeps moving the way it went for an instant.
        assert!(s.value(at(t0, 61)) > before - 1e-6);
        assert!((s.value(at(t0, 61)) - before_next).abs() < 2.0);
        assert!((s.value(at(t0, 2000)) + 50.0).abs() < 0.01);
    }

    #[test]
    fn a_dropped_picture_grows_from_the_first_frame_and_can_be_picked_up_again() {
        // crate::windrag: the carried picture's spring IS the flight (it is
        // retargeted at the lift, not replaced), and a finger picking the
        // window up again across the hinge retargets the flight back.
        let t0 = Instant::now();
        let ghost = (580.0, 120.0, 296.0, 390.0); // 55 % on the other panel
        let place = (564.0, 30.0, 538.0, 709.0); // that panel's room
        let mut r = RectSpring::new(ghost, ghost, t0, Params::SNAPPY);
        let lift = at(t0, 400);
        let before = r.value(lift);
        r.retarget(place, lift);
        let same = |a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)| {
            [a.0 - b.0, a.1 - b.1, a.2 - b.2, a.3 - b.3].iter().all(|d| d.abs() < 1e-9)
        };
        // No jump at the lift, and one 90 Hz frame later it is growing.
        assert!(same(r.value(lift), before));
        let first = r.value(at(t0, 411));
        assert!(first.2 > before.2 + 5.0 && first.3 > before.3 + 5.0, "{first:?}");
        // Most of the way in 100 ms, in its place well within 600 ms.
        let mid = r.value(at(t0, 500));
        assert!(mid.2 > 296.0 + 0.7 * (538.0 - 296.0), "{mid:?}");
        assert!(r.done(at(t0, 1000)));
        // Picked up again 200 ms after the lift: from where it has got to.
        let mut again = RectSpring::new(ghost, ghost, t0, Params::SNAPPY);
        again.retarget(place, lift);
        let pick = at(t0, 600);
        let there = again.value(pick);
        again.retarget(ghost, pick);
        assert!(same(again.value(pick), there));
        assert!(again.value(at(t0, 1400)).2 < 296.0 + 1.0);
    }

    #[test]
    fn a_spring_at_rest_is_done() {
        let t0 = Instant::now();
        let mut s = Spring::at(5.0, t0, Params::SMOOTH);
        assert!(s.done(t0));
        s.set(9.0, t0);
        assert_eq!(s.value(at(t0, 10)), 9.0);
    }

    #[test]
    fn rectangles_move_on_four_springs() {
        let t0 = Instant::now();
        let mut r = RectSpring::new((0.0, 0.0, 100.0, 100.0), (50.0, 60.0, 200.0, 300.0), t0, Params::SMOOTH);
        assert_eq!(r.value(t0), (0.0, 0.0, 100.0, 100.0));
        assert!(r.done(at(t0, 1000)));
        let end = r.value(at(t0, 1000));
        assert!((end.0 - 50.0).abs() < 0.3 && (end.3 - 300.0).abs() < 0.3);
        r.retarget((0.0, 0.0, 10.0, 10.0), at(t0, 1000));
        assert_eq!(r.target(), (0.0, 0.0, 10.0, 10.0));
        assert!(!r.done(at(t0, 1000)));
    }
}
