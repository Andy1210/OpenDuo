//! A window that comes to the front by itself: a new window's first
//! picture, an app activating its window (xdg-activation: a launch, a
//! notification). The pure part.
//!
//! **Where an activated window goes** ([`activation_panel`]). An activation
//! token made by a surface on another panel moved the window there (the
//! app grid's launch of an app already running elsewhere). But GTK makes a
//! launch's token with whichever of the launcher's surfaces has the
//! keyboard, not the one tapped: Text Editor started from the home
//! screen on DSI-1 while the app grid was open on DSI-2 (2026-11-12): the
//! shell asked for DSI-1 (`expect_window`), the window opened there, and
//! the token (made by the grid on DSI-2) moved it to DSI-2,
//! under the grid. Now the launcher's word counts first: a window the
//! launcher's hint put on its panel stays there at its first activation,
//! and a hint still waiting (an app already running, launched again) says
//! where the window goes; the token's panel only without a hint.
//!
//! **The shell makes way**: duo-wc tells the event stream (`window_front`,
//! [`front_panels`]) the panels such a window is in front on, and the
//! shell closes its app grid and quick menu there (they are layer surfaces
//! above the windows: the new window opened under them).
//!
//! **A window that goes at once gives the hint back** ([`gives_hint_back`]).
//! The hint is the next toplevel's, whatever it is. Starting Android apps
//! cold, Waydroid makes a toplevel that is gone again 5 ms later, ~3 s
//! after the tap (2026-11-17: `new toplevel id=13`, `toplevel gone id=13`
//! 5 ms later): it took the hint, and the app's own window, ~16 s later,
//! opened by the open rule (the owner had tapped again by then, and that
//! tap's hint placed it). Now a toplevel the hint placed that goes within
//! [`HINT_TRANSIENT`] leaves the hint for the next one, until the time the
//! launcher asked for is over.

use std::time::Duration;

/// A toplevel the launcher's hint placed that is gone within this long was
/// not the window launched (Waydroid's first one lived 5 ms).
pub const HINT_TRANSIENT: Duration = Duration::from_millis(1000);

/// Whether the hint that placed a window that has gone is the launcher's
/// again: the window `lived` that long, `fresh`: the hint's time is not
/// over, `newer`: another launch has asked since (its hint stays).
pub fn gives_hint_back(lived: Duration, fresh: bool, newer: bool) -> bool {
    lived < HINT_TRANSIENT && fresh && !newer
}

/// Where an activated window goes, if it moves. `current`: its panel (None:
/// it spans, it stays so); `launched_here`: the launcher's hint put it on
/// that panel when it opened and this is its first activation; `hint`: a
/// launcher's hint still waiting (fresh); `token`: the panel of the surface
/// the activation token was made with (another window's or a shell
/// surface's); `in_use`: the panels the pose uses.
pub fn activation_panel(
    current: Option<&str>,
    launched_here: bool,
    hint: Option<&str>,
    token: Option<&str>,
    in_use: &[&str],
) -> Option<String> {
    let current = current?;
    if launched_here {
        return None;
    }
    let usable = |p: &&str| *p != current && in_use.contains(p);
    if let Some(hint) = hint {
        return Some(hint).filter(usable).map(str::to_owned);
    }
    token.filter(usable).map(str::to_owned)
}

/// The panels a window in front is on: its own, or every panel in use for
/// one that spans.
pub fn front_panels(own: Option<&str>, in_use: &[&str]) -> Vec<String> {
    match own {
        Some(panel) => vec![panel.to_owned()],
        None => in_use.iter().map(|p| (*p).to_owned()).collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOTH: &[&str] = &["DSI-1", "DSI-2"];

    #[test]
    fn a_window_the_launcher_placed_stays_at_its_first_activation() {
        // Launched from home on DSI-1, the token made by the app grid on
        // DSI-2.
        assert_eq!(activation_panel(Some("DSI-1"), true, None, Some("DSI-2"), BOTH), None);
    }

    #[test]
    fn a_waiting_hint_says_where_a_running_app_goes() {
        // Launched again from DSI-1 while its window is on DSI-2.
        assert_eq!(
            activation_panel(Some("DSI-2"), false, Some("DSI-1"), Some("DSI-2"), BOTH).as_deref(),
            Some("DSI-1")
        );
        // Already there.
        assert_eq!(activation_panel(Some("DSI-1"), false, Some("DSI-1"), Some("DSI-2"), BOTH), None);
    }

    #[test]
    fn otherwise_the_tokens_panel() {
        assert_eq!(
            activation_panel(Some("DSI-2"), false, None, Some("DSI-1"), BOTH).as_deref(),
            Some("DSI-1")
        );
        assert_eq!(activation_panel(Some("DSI-2"), false, None, None, BOTH), None);
        // A panel the pose does not use (folded): nowhere.
        assert_eq!(activation_panel(Some("DSI-2"), false, None, Some("DSI-1"), &["DSI-2"]), None);
        assert_eq!(activation_panel(Some("DSI-2"), false, Some("DSI-1"), None, &["DSI-2"]), None);
    }

    #[test]
    fn a_spanning_window_stays_spanning() {
        assert_eq!(activation_panel(None, false, Some("DSI-1"), Some("DSI-2"), BOTH), None);
    }

    #[test]
    fn a_window_gone_at_once_gives_the_hint_back() {
        let ms = Duration::from_millis;
        // Waydroid's first toplevel: 5 ms.
        assert!(gives_hint_back(ms(5), true, false));
        // A window that stayed was the one launched.
        assert!(!gives_hint_back(HINT_TRANSIENT, true, false));
        assert!(!gives_hint_back(ms(20_000), true, false));
        // The hint's time is over, or a newer launch has its own.
        assert!(!gives_hint_back(ms(5), false, false));
        assert!(!gives_hint_back(ms(5), true, true));
    }

    #[test]
    fn the_panels_the_shell_clears() {
        assert_eq!(front_panels(Some("DSI-2"), BOTH), vec!["DSI-2".to_owned()]);
        assert_eq!(front_panels(None, BOTH), vec!["DSI-1".to_owned(), "DSI-2".to_owned()]);
    }
}
