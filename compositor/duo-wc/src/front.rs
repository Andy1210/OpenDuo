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
    fn the_panels_the_shell_clears() {
        assert_eq!(front_panels(Some("DSI-2"), BOTH), vec!["DSI-2".to_owned()]);
        assert_eq!(front_panels(None, BOTH), vec!["DSI-1".to_owned(), "DSI-2".to_owned()]);
    }
}
