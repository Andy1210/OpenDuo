//! Leaving a text field by tapping beside it: the on-screen keyboard goes
//! away even when the app does not say so. Pure rules here, the live part
//! below them.
//!
//! The keyboard (squeekboard) comes and goes with the app's text input:
//! enabled when a text field gets the focus, disabled when it loses it. Qt
//! WebEngine (Angelfish) does not disable it when the page's field loses the
//! focus to a tap on the page: Chromium's text input state goes away and
//! QtWebEngine leaves Qt's input method state as it was (so that a focused
//! field whose child goes does not hide the keyboard), and the keyboard
//! stayed up until the bar's button hid it (2026-11-08).
//!
//! Worse, Angelfish enables its text input when its window gets the
//! keyboard focus, and commits it on the first touch anywhere on the page
//! (measured, 2026-11-08): the keyboard came up on a scroll, with no field.
//! So an enabled text input that is no field ([`is_field`]: no caret, no
//! text) puts the keyboard away at once (it never shows), and a real caret
//! or text brings it.
//!
//! And duo-wc watches the taps (`crate::textspy` sees the text inputs): a tap
//! on a window whose app has its text input enabled (not a terminal: foot
//! keeps it enabled all the time), with the keyboard up, further than
//! [`NEAR`] from the text cursor, is followed for [`ANSWER_MS`]. A tap into
//! a field (another one, or elsewhere in the same one) moves the cursor, and
//! the app says so (a text-input commit with a new cursor, text or
//! enable); a tap beside it changes nothing there. No answer: the keyboard
//! is put away (not drawn, not touched, its room given back to the
//! windows), until the app says something again (a tap into a field), a tap
//! lands near the cursor, the app disables its text input (then the keyboard
//! goes for real), the keyboard focus goes to another app, or the bar's
//! keyboard button asks for it (`focus_output`). GTK, Chromium and foot are
//! not touched by it: GTK and Chromium disable their text input when a field
//! loses the focus (the keyboard is gone before the wait ends), a tap into a
//! field makes them answer, and a terminal is never watched.

/// How long an app has to answer a tap (ms): a tap into a field makes it
/// send its new cursor at once (a frame or two).
pub const ANSWER_MS: u64 = 350;

/// A tap this close to the text cursor (logical px) is a tap on the field.
pub const NEAR: f64 = 48.0;

/// Whether a tap at `tap` (the app's surface frame) is off the text being
/// typed: further than [`NEAR`] from the cursor rectangle, or the app gave
/// none.
pub fn off_the_cursor(cursor: Option<(i32, i32, i32, i32)>, tap: (f64, f64)) -> bool {
    let Some((x, y, w, h)) = cursor else {
        return true;
    };
    let (x0, y0) = (x as f64, y as f64);
    let (x1, y1) = (x0 + w.max(0) as f64, y0 + h.max(0) as f64);
    let dx = (x0 - tap.0).max(0.0).max(tap.0 - x1);
    let dy = (y0 - tap.1).max(0.0).max(tap.1 - y1);
    dx.hypot(dy) > NEAR
}

/// Whether a text input's state is a text field: a caret (a cursor
/// rectangle with a height) or some text around it. Qt keeps its text
/// input enabled for a whole web view: on a touch anywhere it commits it
/// with no caret at all, or a zero-height one at the view's corner and no
/// text (measured with Angelfish, 2026-11-08): that is no field, and the
/// keyboard is put away at once, before it even shows.
pub fn is_field(cursor: Option<(i32, i32, i32, i32)>, text_len: Option<usize>) -> bool {
    cursor.map(|c| c.3 > 0).unwrap_or(false) || text_len.unwrap_or(0) > 0
}

/// Whether a tap is watched: the keyboard is up, the app's text input is
/// enabled and not a terminal's, and the tap is off the cursor.
pub fn watch(keyboard_up: bool, enabled: bool, terminal: bool, off_cursor: bool) -> bool {
    keyboard_up && enabled && !terminal && off_cursor
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn near_the_cursor_is_on_the_field() {
        let cursor = Some((100, 200, 2, 30));
        assert!(!off_the_cursor(cursor, (101.0, 215.0)));
        assert!(!off_the_cursor(cursor, (140.0, 215.0)));
        assert!(!off_the_cursor(cursor, (101.0, 270.0)));
        assert!(off_the_cursor(cursor, (101.0, 290.0)));
        assert!(off_the_cursor(cursor, (300.0, 215.0)));
        // No cursor said: anywhere is off it.
        assert!(off_the_cursor(None, (0.0, 0.0)));
    }

    #[test]
    fn a_caret_or_text_is_a_field() {
        // Angelfish, a touch on the page: a bare commit (nothing said), or
        // a zero-height cursor at the corner with no text.
        assert!(!is_field(None, None));
        assert!(!is_field(Some((0, 46, 0, 0)), Some(0)));
        // Its field: the caret.
        assert!(is_field(Some((12, 357, 1, 33)), Some(0)));
        // GTK / a field with text in it before the caret is told.
        assert!(is_field(None, Some(3)));
    }

    #[test]
    fn only_an_enabled_non_terminal_field_with_the_keyboard_up() {
        assert!(watch(true, true, false, true));
        assert!(!watch(false, true, false, true));
        assert!(!watch(true, false, false, true));
        assert!(!watch(true, true, true, true));
        assert!(!watch(true, true, false, false));
    }
}
