//! The ambient screens of a closed or nearly closed phone. Pure.
//!
//! Like the stock Duo, a phone that is folded shut is not simply dark:
//!
//! - **peek**: opened just a little (Microsoft's posture `peek_left` /
//!   `peek_right`, from `duo-posed`), the panels light up black with the
//!   clock, the battery and the notification icons along their OUTER edge
//!   (the edge opposite the hinge: that is where one looks into the gap);
//! - **glance**: closed, a press of the power key lights the panels for a
//!   few seconds with a thin clock on the strip next to the hinge (the part
//!   of the panels seen from outside when the phone is closed), then they go
//!   dark again; another press ends it at once.
//!
//! In both the compositor draws nothing but the shell's ambient surfaces
//! (layer-shell namespace `duo-ambient`) on black, ignores touches (a hand
//! holds a closed phone), and sends no frame callbacks to windows. The
//! shell decides what goes on the strips; this module decides when.
//!
//! The posture comes from `duo-posed` (`set_posture`); without it (the
//! sensors only logging, `enabled = off`) there is no closed phone and the
//! power key does what it always did.

use std::time::Duration;

/// How long a glance stays lit.
pub const GLANCE: Duration = Duration::from_secs(5);

/// How long the glance ruler (a test pattern for finding the part of the
/// panel seen from outside a closed phone) stays lit.
pub const RULER: Duration = Duration::from_secs(30);

/// Which panels an ambient screen lights: the glance (and its ruler) only
/// the configured ones (`glance_panels`, DSI-2 by default: only the curved
/// edge of the right panel's hinge side shows on a closed phone, lighting
/// the other is wasted power), the peek every panel the pose uses
/// (`peek_panels`, both by default). `wanted`: `both` or a connector name.
pub fn lights(kind: Kind, output: &str, glance_panels: &str, peek_panels: &str) -> bool {
    let wanted = match kind {
        Kind::Peek => peek_panels,
        Kind::Glance | Kind::Ruler => glance_panels,
    };
    wanted == "both" || wanted == output
}

/// Which ambient screen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// Opened a little.
    Peek,
    /// Closed, after the power key.
    Glance,
    /// The glance ruler: numbered bands from the hinge-side edge inwards,
    /// for [`RULER`] (`duoctl ambient glance-ruler`).
    Ruler,
}

impl Kind {
    /// The name on the event stream.
    pub fn name(self) -> &'static str {
        match self {
            Kind::Peek => "peek",
            Kind::Glance => "glance",
            Kind::Ruler => "glance-ruler",
        }
    }
}

/// Whether a posture name is the closed phone.
pub fn is_closed(posture: Option<&str>) -> bool {
    posture == Some("closed")
}

/// Whether a posture name is the phone opened a little.
pub fn is_peek(posture: &str) -> bool {
    posture == "peek" || posture.starts_with("peek_")
}

/// What a new posture does to the screen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnPosture {
    /// Nothing here (duo-posed sets the pose or blanks).
    Nothing,
    /// Light the panels for the peek screen.
    Peek,
    /// The ambient screen ends and the panels go dark (closed again).
    Blank,
    /// The ambient screen ends; the panels stay lit for the pose duo-posed
    /// sets now (opened).
    Leave,
}

/// A posture arrived. `dark`: no panel lit; `ambient`: the ambient screen
/// shown now, if one is.
pub fn on_posture(posture: &str, dark: bool, ambient: Option<Kind>) -> OnPosture {
    if posture == "closed" {
        return if ambient.is_some() { OnPosture::Blank } else { OnPosture::Nothing };
    }
    if is_peek(posture) {
        return match ambient {
            Some(Kind::Peek) => OnPosture::Nothing,
            // Peeking at a dark (closed) phone, or opening it during a glance.
            Some(Kind::Glance) | Some(Kind::Ruler) => OnPosture::Peek,
            None if dark => OnPosture::Peek,
            // An open, lit phone folded towards closed: nothing yet.
            None => OnPosture::Nothing,
        };
    }
    if ambient.is_some() {
        OnPosture::Leave
    } else {
        OnPosture::Nothing
    }
}

/// What a short press of the power key does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnPowerKey {
    /// The screen off.
    Blank,
    /// The screen on (as before: the lock screen when locked).
    Wake,
    /// The glance strip, for [`GLANCE`].
    Glance,
}

/// The power key was pressed. `posture` as duo-posed last said.
pub fn on_power_key(posture: Option<&str>, dark: bool, ambient: Option<Kind>) -> OnPowerKey {
    match ambient {
        Some(Kind::Glance) | Some(Kind::Ruler) => OnPowerKey::Blank,
        // Peeking, the key opens the phone's screen properly.
        Some(Kind::Peek) => OnPowerKey::Wake,
        None if dark && is_closed(posture) => OnPowerKey::Glance,
        None if dark => OnPowerKey::Wake,
        None => OnPowerKey::Blank,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_glance_lights_only_its_panels() {
        assert!(lights(Kind::Glance, "DSI-2", "DSI-2", "both"));
        assert!(!lights(Kind::Glance, "DSI-1", "DSI-2", "both"));
        assert!(!lights(Kind::Ruler, "DSI-1", "DSI-2", "both"));
        assert!(lights(Kind::Peek, "DSI-1", "DSI-2", "both"));
        assert!(lights(Kind::Glance, "DSI-1", "both", "both"));
        assert_eq!(Kind::Ruler.name(), "glance-ruler");
        // The ruler ends like a glance.
        assert_eq!(on_power_key(Some("closed"), false, Some(Kind::Ruler)), OnPowerKey::Blank);
    }

    #[test]
    fn the_power_key_on_a_closed_phone_glances() {
        assert_eq!(on_power_key(Some("closed"), true, None), OnPowerKey::Glance);
        assert_eq!(on_power_key(Some("closed"), true, Some(Kind::Glance)), OnPowerKey::Blank);
        // Not closed (or no sensors): as always.
        assert_eq!(on_power_key(Some("open_dpm"), true, None), OnPowerKey::Wake);
        assert_eq!(on_power_key(None, true, None), OnPowerKey::Wake);
        assert_eq!(on_power_key(None, false, None), OnPowerKey::Blank);
        // Peeking: the key wakes the screen properly.
        assert_eq!(on_power_key(Some("peek_left"), false, Some(Kind::Peek)), OnPowerKey::Wake);
    }

    #[test]
    fn peeking_at_a_dark_phone_lights_the_peek_screen() {
        assert_eq!(on_posture("peek_right", true, None), OnPosture::Peek);
        assert_eq!(on_posture("peek_left", false, Some(Kind::Glance)), OnPosture::Peek);
        assert_eq!(on_posture("peek_left", false, Some(Kind::Peek)), OnPosture::Nothing);
        // A lit phone folded down to the peek angle: nothing (yet).
        assert_eq!(on_posture("peek_left", false, None), OnPosture::Nothing);
        // The hinge-angle sources say just "peek".
        assert_eq!(on_posture("peek", true, None), OnPosture::Peek);
    }

    #[test]
    fn closing_or_opening_ends_it() {
        assert_eq!(on_posture("closed", false, Some(Kind::Peek)), OnPosture::Blank);
        assert_eq!(on_posture("closed", true, None), OnPosture::Nothing);
        assert_eq!(on_posture("read", false, Some(Kind::Peek)), OnPosture::Leave);
        assert_eq!(on_posture("open_dpm", false, Some(Kind::Glance)), OnPosture::Leave);
        assert_eq!(on_posture("open_dpm", false, None), OnPosture::Nothing);
    }

    #[test]
    fn names() {
        assert!(is_closed(Some("closed")) && !is_closed(Some("peek_left")) && !is_closed(None));
        assert!(is_peek("peek_left") && is_peek("peek") && !is_peek("open_dpm"));
        assert_eq!(Kind::Peek.name(), "peek");
        assert_eq!(Kind::Glance.name(), "glance");
    }
}
