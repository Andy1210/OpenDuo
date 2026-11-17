//! When a touch may take the keyboard to another window. Pure.
//!
//! The keyboard follows the finger: a touch on a window gives it the
//! keyboard focus, and the on-screen keyboard goes to that window's panel.
//! While the on-screen keyboard is up that is too eager: the hand that holds
//! the phone touches the other panel (its edges, a palm in the middle), and
//! every such touch took the keyboard away from the text being typed, the
//! on-screen keyboard jumping between the panels every few hundred ms (seen
//! on 2026-11-07, and part of the conditions of a GPU hang: each jump
//! re-configures the keyboard and the windows of both panels).
//!
//! So while the on-screen keyboard is up, a touch on ANOTHER window than the
//! one with the keyboard (or, in span, on the other panel's part of the
//! spanning window, which says where the text field is) is **tentative**: it
//! reaches the app at once, but the keyboard moves only when the touch turns
//! out to be a deliberate tap: lifted within [`TAP_MS`], moved less than
//! [`TAP_SLOP`], and not started while typing (a finger on the on-screen
//! keyboard, or a key touched less than [`TYPING_GUARD_MS`] before). A
//! scroll, a long press, a resting palm or a touch amid typing leaves the
//! keyboard where it is.

/// A tap is lifted within this long (ms of the input event clock).
pub const TAP_MS: u32 = 450;

/// ... and moves less than this (logical px).
pub const TAP_SLOP: f64 = 16.0;

/// A touch that starts this soon after a touch on the on-screen keyboard is
/// the other hand, not a switch.
pub const TYPING_GUARD_MS: u32 = 800;

/// Whether the user is typing: a finger is on the on-screen keyboard, or one
/// went down on it less than [`TYPING_GUARD_MS`] ago (`now` and `last_key`
/// on the input event clock, which wraps).
pub fn typing(now: u32, last_key: Option<u32>, keys_held: bool) -> bool {
    keys_held || last_key.map(|t| now.wrapping_sub(t) < TYPING_GUARD_MS).unwrap_or(false)
}

/// A touch that would move the keyboard, while it is decided.
#[derive(Debug, Clone, PartialEq)]
pub struct Tentative {
    start: (f64, f64),
    down_ms: u32,
    typing: bool,
    moved: bool,
}

impl Tentative {
    /// A touch went down at `start` (global logical px) at `down_ms`;
    /// `typing` from [`typing`].
    pub fn new(start: (f64, f64), down_ms: u32, typing: bool) -> Self {
        Self { start, down_ms, typing, moved: false }
    }

    /// The finger is at `(x, y)` now.
    pub fn moved(&mut self, x: f64, y: f64) {
        let (dx, dy) = (x - self.start.0, y - self.start.1);
        if dx * dx + dy * dy > TAP_SLOP * TAP_SLOP {
            self.moved = true;
        }
    }

    /// It lifted at `up_ms`: does the keyboard move now?
    pub fn commits(&self, up_ms: u32) -> bool {
        !self.typing && !self.moved && up_ms.wrapping_sub(self.down_ms) <= TAP_MS
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_short_still_tap_moves_the_keyboard() {
        let t = Tentative::new((100.0, 300.0), 1000, false);
        assert!(t.commits(1200));
        assert!(t.commits(1000 + TAP_MS));
    }

    #[test]
    fn a_long_press_or_a_resting_palm_does_not() {
        let t = Tentative::new((100.0, 300.0), 1000, false);
        assert!(!t.commits(1000 + TAP_MS + 1));
        assert!(!t.commits(5000));
    }

    #[test]
    fn a_scroll_does_not() {
        let mut t = Tentative::new((100.0, 300.0), 1000, false);
        t.moved(105.0, 305.0);
        assert!(t.commits(1100), "a few px is still a tap");
        t.moved(100.0, 300.0 + TAP_SLOP + 1.0);
        assert!(!t.commits(1100));
        // Moving back does not make it a tap again.
        t.moved(100.0, 300.0);
        assert!(!t.commits(1100));
    }

    #[test]
    fn a_touch_amid_typing_does_not() {
        let t = Tentative::new((100.0, 300.0), 1000, true);
        assert!(!t.commits(1100));
    }

    #[test]
    fn typing_is_a_key_held_or_touched_just_before() {
        assert!(typing(1000, None, true));
        assert!(typing(1000, Some(500), false));
        assert!(!typing(1000 + TYPING_GUARD_MS, Some(1000), false));
        assert!(!typing(1000, None, false));
        // The event clock wraps.
        assert!(typing(100, Some(u32::MAX - 100), false));
    }

    #[test]
    fn the_clock_wraps_for_taps_too() {
        let t = Tentative::new((0.0, 0.0), u32::MAX - 50, false);
        assert!(t.commits(100));
    }
}
