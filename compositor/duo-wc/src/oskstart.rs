//! The on-screen keyboard's start-up blink, held back. Pure rule here, the
//! live part in `crate::textspy` (next to the put-away keyboard).
//!
//! squeekboard (Debian's 1.43.1, `src/state.rs` `Application::new`) starts
//! as if a text field had just been left ("It will initially show the
//! keyboard for a blink"): visible until 200 ms (its `HIDING_TIMEOUT`) after
//! its start, on the first output it hears of, then it takes the surface
//! down. So every start of squeekboard (the session's, at every boot and
//! duo-wc restart, and its restarts by the session) put the keyboard on the
//! left panel (DSI-1, duo-wc's active panel when nothing has the focus) for
//! a moment, with no text field anywhere: seen at boot, right after the
//! splash (2026-11-09; the journal: the `osk` layer surface at
//! 7.80 s, 0.28 s after squeekboard's connection, before the shell's bars).
//!
//! duo-wc holds such a surface back: the FIRST keyboard surface of a client,
//! made within [`START_WINDOW`] of its connection while the app with the
//! keyboard focus has no text input enabled, is not drawn, not touched and
//! takes no room (as if put away, `crate::oskleave`), until squeekboard
//! takes it down (the end of the blink), a text field asks for the keyboard
//! (the focused app enables its text input), the bar's keyboard button asks
//! for it (`focus_output`), or [`HOLD_MAX_MS`] have passed: squeekboard
//! keeps the blink 200 ms, so a surface still there is a keyboard someone
//! asked for (SetVisible over D-Bus, the quick menu's tile) and shows.
//!
//! phosh-osk-stub (Debian's 0.46, the other keyboard the session may start)
//! does the same in its own way: `create_input_surface`
//! presents its surface as soon as it has the globals, while its input
//! method is still inactive, so it starts unfolded and folds away (its 250 ms
//! slide, `animate_cb`) before it unmaps. The same hold covers it.

use std::time::Duration;

/// How soon after its client connected a keyboard surface can be the
/// start-up blink. The blink is over 200 ms after squeekboard's state is
/// made at its start, and its client is made just before it starts (the
/// session's `duoctl exec-trusted`): 0.28 s measured at boot, a wide margin
/// for a loaded start.
pub const START_WINDOW: Duration = Duration::from_secs(3);

/// The longest a keyboard surface is held back (ms): longer than the blink
/// (200 ms) with room for a busy start; a surface still there is shown.
pub const HOLD_MAX_MS: u64 = 1500;

/// Whether a new keyboard surface is squeekboard's start-up blink: its
/// client's first (`first`), made `age` after the client connected (None:
/// unknown, never held), and no text field has the keyboard focus
/// (`field_focused`: the focused app's text input is enabled; a text field
/// that is there when squeekboard starts gets the keyboard at once).
pub fn is_start_blink(age: Option<Duration>, first: bool, field_focused: bool) -> bool {
    first && !field_focused && age.is_some_and(|a| a < START_WINDOW)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MEASURED: Option<Duration> = Some(Duration::from_millis(284));

    #[test]
    fn a_fresh_keyboard_with_no_field_is_the_blink() {
        assert!(is_start_blink(MEASURED, true, false));
    }

    #[test]
    fn a_field_with_the_focus_gets_the_keyboard_at_once() {
        // A terminal (foot keeps its text input enabled) or a text field
        // focused while squeekboard (re)starts.
        assert!(!is_start_blink(MEASURED, true, true));
    }

    #[test]
    fn only_the_first_surface_of_a_client() {
        // The blink is the first; a later one is a keyboard asked for.
        assert!(!is_start_blink(MEASURED, false, false));
    }

    #[test]
    fn an_old_client_is_not_starting() {
        assert!(!is_start_blink(Some(START_WINDOW), true, false));
        assert!(!is_start_blink(Some(Duration::from_secs(60)), true, false));
    }

    #[test]
    fn an_unknown_age_is_never_held() {
        assert!(!is_start_blink(None, true, false));
    }

    #[test]
    fn the_hold_outlasts_the_blink() {
        // squeekboard's HIDING_TIMEOUT, with room for a busy start.
        const { assert!(HOLD_MAX_MS >= 5 * 200) };
        // phosh-osk-stub's fold at its start: 0.06666 of the slide per
        // 16.7 ms frame (pos-input-surface.c animate_cb), 250 ms.
        const { assert!(HOLD_MAX_MS >= 4 * 250) };
    }
}
