//! Haptic pulses in the pen: what the games ask for and how often.
//!
//! The pen's vibration is the root service duo2-pend's (it owns the pen's
//! Bluetooth HID device); an app asks it for one discrete pulse at a time
//! with a datagram `pulse <waveform> <intensity 1-255>` on its socket
//! (/run/duo2-pen/haptics). duo2-pend takes at most one every 40 ms and
//! drops the rest, so this side keeps the same pace and, when two want the
//! same slot, sends the more important one (a bite beats a reel click).
//! Pulses are a nice-to-have: the pen drops its Bluetooth link for about a
//! second each time it comes near the screen, and nothing in the games
//! waits on a pulse or needs one.

/// The pen's discrete waveforms that duo2-pend lets apps play.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Wave {
    Click,
    Press,
    Release,
    Hover,
    Success,
    Error,
}

impl Wave {
    pub fn name(self) -> &'static str {
        match self {
            Wave::Click => "click",
            Wave::Press => "press",
            Wave::Release => "release",
            Wave::Hover => "hover",
            Wave::Success => "success",
            Wave::Error => "error",
        }
    }

    /// How long the pen plays it (its own list, feature 0x42), s.
    pub fn length(self) -> f32 {
        match self {
            Wave::Click => 0.045,
            Wave::Press | Wave::Release => 0.060,
            Wave::Hover => 0.035,
            Wave::Success => 0.145,
            Wave::Error => 0.185,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pulse {
    pub wave: Wave,
    pub intensity: u8,
    /// Higher wins a contested slot.
    pub priority: u8,
}

impl Pulse {
    pub fn new(wave: Wave, intensity: f32, priority: u8) -> Pulse {
        Pulse {
            wave,
            intensity: (intensity.clamp(0.0, 1.0) * 254.0).round() as u8 + 1,
            priority,
        }
    }
}

/// The request duo2-pend reads.
pub fn request_text(p: &Pulse) -> String {
    format!("pulse {} {}", p.wave.name(), p.intensity.max(1))
}

/// The service's minimum gap between two pulses (duo2-pend drops closer ones).
pub const MIN_GAP: f64 = 0.040;
/// A pulse that waited longer than this is dropped (a late buzz is no feel).
pub const MAX_WAIT: f64 = 0.090;

/// Keeps the pulses at the service's pace: at most one per gap, and while
/// a slot is taken, the most important pulse offered waits for the next one
/// (briefly: see MAX_WAIT). A long waveform holds the slot while it plays,
/// so the next one does not cut it.
#[derive(Debug)]
pub struct Limiter {
    last_sent: f64,
    busy_until: f64,
    waiting: Option<(Pulse, f64)>,
}

impl Default for Limiter {
    fn default() -> Self {
        Limiter {
            last_sent: -1e9,
            busy_until: -1e9,
            waiting: None,
        }
    }
}

impl Limiter {
    fn free_at(&self) -> f64 {
        (self.last_sent + MIN_GAP + 0.002).max(self.busy_until)
    }

    /// Offer a pulse at `now`: Some(pulse) to send now, or None (it waits, or
    /// something more important is waiting).
    pub fn offer(&mut self, now: f64, p: Pulse) -> Option<Pulse> {
        if now >= self.free_at() && self.waiting.is_none() {
            return Some(self.take(now, p));
        }
        match self.waiting {
            Some((w, _)) if w.priority >= p.priority => {}
            _ => self.waiting = Some((p, now)),
        }
        self.poll(now)
    }

    /// A pulse that waited and may go now (call every frame).
    pub fn poll(&mut self, now: f64) -> Option<Pulse> {
        let (p, since) = self.waiting?;
        if now - since > MAX_WAIT {
            self.waiting = None;
            return None;
        }
        if now >= self.free_at() {
            self.waiting = None;
            return Some(self.take(now, p));
        }
        None
    }

    fn take(&mut self, now: f64, p: Pulse) -> Pulse {
        self.last_sent = now;
        // A long waveform (success, error) is not cut by a click.
        self.busy_until = if p.priority >= 2 {
            now + p.wave.length() as f64
        } else {
            now
        };
        p
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_request_names_the_waveform_and_intensity() {
        let p = Pulse::new(Wave::Success, 1.0, 3);
        assert_eq!(request_text(&p), "pulse success 255");
        assert_eq!(
            request_text(&Pulse::new(Wave::Click, 0.0, 0)),
            "pulse click 1"
        );
        assert_eq!(Pulse::new(Wave::Hover, 0.5, 0).intensity, 128);
    }

    #[test]
    fn at_most_one_pulse_per_gap() {
        let mut l = Limiter::default();
        let click = Pulse::new(Wave::Click, 0.5, 0);
        assert!(l.offer(0.0, click).is_some());
        assert!(l.offer(0.010, click).is_none(), "too close to the last");
        assert!(l.poll(0.030).is_none());
        let sent = l.poll(0.043).expect("the waiting one goes after the gap");
        assert_eq!(sent, click);
        assert!(l.poll(0.050).is_none(), "nothing left");
        assert!(l.offer(0.200, click).is_some());
    }

    #[test]
    fn the_important_pulse_wins_the_slot() {
        let mut l = Limiter::default();
        let click = Pulse::new(Wave::Click, 0.3, 0);
        let bite = Pulse::new(Wave::Success, 1.0, 3);
        assert!(l.offer(0.0, click).is_some());
        assert!(l.offer(0.005, bite).is_none());
        assert!(
            l.offer(0.010, click).is_none(),
            "a click does not push the bite out"
        );
        assert_eq!(l.poll(0.045), Some(bite));
    }

    #[test]
    fn a_stale_pulse_is_dropped_and_a_long_one_is_not_cut() {
        let mut l = Limiter::default();
        let ok = Pulse::new(Wave::Success, 1.0, 3);
        assert!(l.offer(0.0, ok).is_some());
        let click = Pulse::new(Wave::Click, 0.3, 0);
        assert!(
            l.offer(0.050, click).is_none(),
            "the success still plays (145 ms)"
        );
        assert!(l.poll(0.120).is_none(), "still playing");
        assert!(l.poll(0.150).is_none(), "and stale once the slot is free");
        assert!(l.offer(0.300, click).is_some());
    }
}
