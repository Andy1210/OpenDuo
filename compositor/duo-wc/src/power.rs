//! Panel power: which panels are lit, the idle timer and the power key.
//!
//! A panel is lit when the pose uses it ([`crate::pose::Pose::in_use`]), the
//! user has not switched it off (`set_power <output> off`), and the screen is not
//! blanked. Blanking is "the screen is off": the idle timeout or a power-key
//! press. Waking undoes the blank and brings back exactly the panels that were
//! lit before it; if that is none (both had been switched off one by one), it
//! lights every panel the pose uses, so the power key always gets a picture back.
//!
//! The rules here are pure; `state.rs` applies them to the hardware.

use std::collections::BTreeSet;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

/// A panel's power, as asked for over the control socket.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Power {
    /// Lit.
    On,
    /// Switched off (CRTC disabled: the panel driver turns the panel off).
    Off,
}

impl Power {
    /// Parse `on` / `off`.
    pub fn parse(word: &str) -> Option<Self> {
        match word.trim().to_ascii_lowercase().as_str() {
            "on" | "1" | "true" | "yes" => Some(Power::On),
            "off" | "0" | "false" | "no" => Some(Power::Off),
            _ => None,
        }
    }
}

/// Which panels the user switched off, and whether the screen is blanked.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PowerModel {
    /// Panels switched off one by one (`set_power DSI-2 off`).
    user_off: BTreeSet<String>,
    /// The screen is off: idle timeout, power key, or `set_power all off`.
    blanked: bool,
}

impl PowerModel {
    /// Whether a panel is lit; `in_use` is whether the pose uses it.
    pub fn is_lit(&self, output: &str, in_use: bool) -> bool {
        in_use && !self.blanked && !self.user_off.contains(output)
    }

    /// Whether any of the panels in use is lit.
    pub fn any_lit(&self, in_use: &[&str]) -> bool {
        in_use.iter().any(|output| self.is_lit(output, true))
    }

    /// Whether the screen is blanked (as opposed to panels switched off one by
    /// one).
    pub fn blanked(&self) -> bool {
        self.blanked
    }

    /// The panels switched off one by one.
    pub fn user_off(&self) -> impl Iterator<Item = &str> {
        self.user_off.iter().map(String::as_str)
    }

    /// The screen goes off.
    pub fn blank(&mut self) {
        self.blanked = true;
    }

    /// The screen comes back: what was lit before the blank, or every panel in
    /// use if that would be nothing.
    pub fn wake(&mut self, in_use: &[&str]) {
        self.blanked = false;
        if !self.any_lit(in_use) {
            self.user_off.retain(|output| !in_use.contains(&output.as_str()));
        }
    }

    /// `set_power <output> on|off`.
    ///
    /// Switching one panel on from a blanked screen lights THAT panel only (the
    /// others stay off), which is what the words say.
    pub fn set(&mut self, output: &str, power: Power, in_use: &[&str]) -> Result<(), String> {
        match power {
            Power::Off => {
                self.user_off.insert(output.to_owned());
            }
            Power::On => {
                if !in_use.contains(&output) {
                    return Err(format!("{output} is not in use in this pose (set the pose to dual first)"));
                }
                if self.blanked {
                    self.blanked = false;
                    for other in in_use.iter().filter(|o| **o != output) {
                        self.user_off.insert((*other).to_owned());
                    }
                }
                self.user_off.remove(output);
            }
        }
        Ok(())
    }

    /// `set_power all on|off`: off blanks the screen (the power key brings it
    /// back), on lights every panel the pose uses.
    pub fn set_all(&mut self, power: Power) {
        match power {
            Power::Off => self.blanked = true,
            Power::On => {
                self.blanked = false;
                self.user_off.clear();
            }
        }
    }

    /// A pose change: panels it brings into use come on, and the screen wakes.
    pub fn pose_changed(&mut self, in_use: &[&str]) {
        self.user_off.retain(|output| !in_use.contains(&output.as_str()));
        self.blanked = false;
    }
}

/// What the display hardware of one panel is doing, as far as "does the user
/// see duo-wc's picture there" goes ([`shows_picture`]). Filled from
/// `backend::Surface`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PanelHw {
    /// The power rules want it lit.
    pub want_on: bool,
    /// Its CRTC is enabled (or will be by the commit in flight).
    pub powered: bool,
    /// A wake commit of ours is under way.
    pub waking: bool,
    /// An off commit of ours is under way.
    pub powering_off: bool,
    /// Going dark (its black frame first).
    pub blackout: bool,
    /// Woken with another panel: black (or what it showed) until both are
    /// awake.
    pub wake_group: bool,
    /// It came on and its first picture since is not on screen yet.
    pub first_picture_pending: bool,
    /// Frames on screen since duo-wc started.
    pub frames_presented: u64,
}

/// Whether the panel shows duo-wc's picture now: lit, and the first picture
/// since it came on has been flipped onto it. A panel that is waking (the
/// panel's own power-on takes ~0.2 s, a group wake ~0.45 s), going dark, held
/// for the other panel, or has never shown a frame, shows nothing the user
/// could mean to touch.
pub fn shows_picture(hw: PanelHw) -> bool {
    hw.want_on
        && hw.powered
        && !hw.waking
        && !hw.powering_off
        && !hw.blackout
        && !hw.wake_group
        && !hw.first_picture_pending
        && hw.frames_presented > 0
}

/// What a touch that goes down on a panel does, as far as power goes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TouchOnPanel {
    /// The panel shows the picture: the touch is the clients' (unless an
    /// edge, the overview or the ambient screen takes it).
    Deliver,
    /// No picture there: no client gets this touch point, down to its lift.
    Swallow,
    /// No picture, and it wakes the screen (`touch_wake`); still not
    /// delivered, down to its lift.
    SwallowAndWake,
}

/// A touch goes down on a panel. `shown`: [`shows_picture`]; `lit`: the
/// power rules want the panel lit (lit but not shown is a panel waking: its
/// picture is on the way); `screen_dark`: no panel is lit; `touch_wake`: the
/// config's option.
///
/// A touch on a panel that shows nothing never reaches a client (before
/// 2026-11-08 a tap on the black left panel launched the app under the
/// finger). Only a dark SCREEN is woken by a touch, and only with
/// `touch_wake`; one dark panel beside a lit one (a tent, a panel switched
/// off, the peek strip on the other) is not woken by touching it (the
/// tent's double tap is [`crate::pose::DoubleTap`], decided on the lift).
pub fn touch_on_panel(shown: bool, lit: bool, screen_dark: bool, touch_wake: bool) -> TouchOnPanel {
    if shown {
        TouchOnPanel::Deliver
    } else if !lit && screen_dark && touch_wake {
        TouchOnPanel::SwallowAndWake
    } else {
        TouchOnPanel::Swallow
    }
}

/// The lit panels that wait for the dark ones when the screen is woken as a
/// whole while some panel is already lit (the power key, or opening the
/// phone, on the peek strip, which lights the right panel only): they keep
/// what they show until the dark ones are awake, then the picture comes on
/// all of them in the same round, as after a wake from a dark screen.
/// `waking`: the dark panels coming on; `lit`: the panels lit now that stay
/// lit; `whole_screen`: the screen wakes (not one panel switched on).
pub fn hold_for_wake(waking: &[String], lit: &[String], whole_screen: bool) -> Vec<String> {
    if !whole_screen || waking.is_empty() {
        return Vec::new();
    }
    lit.iter().filter(|o| !waking.contains(o)).cloned().collect()
}

/// One panel, as an off commit that finishes sees it ([`wake_after_off`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OffPanel {
    /// The connector.
    pub name: String,
    /// The power rules want it lit.
    pub want_on: bool,
    /// Its CRTC is enabled.
    pub powered: bool,
    /// Its off commit is still under way.
    pub powering_off: bool,
    /// A wake commit of ours is under way.
    pub waking: bool,
}

/// An off commit finished on a panel: the panels that wake now, together in
/// ONE commit. While another panel's off commit is still under way, none:
/// the last one to finish wakes every panel asked on meanwhile (the power
/// key pressed again while the panels went dark; before, each panel woke on
/// its own as its off finished: two commits, the pictures apart).
pub fn wake_after_off(panels: &[OffPanel]) -> Vec<String> {
    if panels.iter().any(|p| p.powering_off) {
        return Vec::new();
    }
    panels.iter().filter(|p| p.want_on && !p.powered && !p.waking).map(|p| p.name.clone()).collect()
}

/// After a panel came on, its first picture is committed again twice:
/// [`REPEAT_SOON`] and [`REPEAT_LATER`] after it is on screen (each waits
/// while another panel's first picture is still to come). On a still screen
/// nothing else would draw there again, and a panel that missed the first
/// picture would stay black (2026-11-08, a try kernel: after a power-key
/// wake the left panel stayed black with duo-wc's picture flipped onto it,
/// until a tap launched an app and that app's frames lit it).
pub const REPEAT_SOON: Duration = Duration::from_millis(30);
/// The last repeat of the first picture after a power-on.
pub const REPEAT_LATER: Duration = Duration::from_millis(150);

/// Panels the proximity sensor holds dark during a call (the shell's
/// `set_proximity`, [`crate::ipc::Request::SetProximity`]).
///
/// While a call uses the earpiece and something is near the sensor (an ear,
/// a cheek), the panel the call is on goes dark and its touches are not
/// delivered; when it is far again, or the call ends or goes to the speaker,
/// it comes back. Apart from the blank and the panels switched off: a held
/// panel does not count as "the screen is off" (no lock on blank, a touch
/// there does not wake anything, the idle timer stops while nothing else is
/// lit), and it is lit again exactly as before.
///
/// A hold lasts `ttl` (the shell renews it while the call goes on), so a
/// shell that died while the phone was at an ear cannot leave the panel dark.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProximityHold {
    panels: BTreeSet<String>,
    until: Option<Instant>,
}

impl ProximityHold {
    /// How long a hold lasts when the request does not say.
    pub const DEFAULT_TTL: Duration = Duration::from_secs(30);
    /// The longest hold a request may ask for.
    pub const MAX_TTL: Duration = Duration::from_secs(120);

    /// Hold these panels dark until `now + ttl` (an empty list ends the
    /// hold). Whether the set of panels changed.
    pub fn set<I: IntoIterator<Item = String>>(&mut self, panels: I, ttl: Duration, now: Instant) -> bool {
        let panels: BTreeSet<String> = panels.into_iter().collect();
        let changed = panels != self.panels;
        self.until = (!panels.is_empty()).then(|| now + ttl.min(Self::MAX_TTL));
        self.panels = panels;
        changed
    }

    /// The hold ends (whatever is held).
    pub fn clear(&mut self) -> bool {
        self.until = None;
        !std::mem::take(&mut self.panels).is_empty()
    }

    /// Whether this panel is held dark.
    pub fn covers(&self, output: &str) -> bool {
        self.panels.contains(output)
    }

    /// The held panels.
    pub fn panels(&self) -> Vec<String> {
        self.panels.iter().cloned().collect()
    }

    /// When the hold runs out, while there is one.
    pub fn deadline(&self) -> Option<Instant> {
        self.until
    }

    /// The time is up at `now`: the hold ends. Whether it did.
    pub fn expire(&mut self, now: Instant) -> bool {
        match self.until {
            Some(until) if now >= until => self.clear(),
            _ => false,
        }
    }
}

/// How an incoming call rings, as the shell last said (`set_ring`,
/// [`RingHold`]).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Ring {
    /// No call rings in.
    #[default]
    Off,
    /// A call rings in aloud (its ringtone, its vibration): the first short
    /// press of the power key silences it.
    Loud,
    /// A call rings in silently (silenced, or waiting beside a call that is
    /// up): the power key does what it always does.
    Silent,
}

impl Ring {
    /// Parse `loud` / `silent` / `off`.
    pub fn parse(word: &str) -> Option<Self> {
        match word.trim().to_ascii_lowercase().as_str() {
            "loud" => Some(Ring::Loud),
            "silent" => Some(Ring::Silent),
            "off" | "none" => Some(Ring::Off),
            _ => None,
        }
    }

    /// The name on the control socket (null: no call rings).
    pub fn name(self) -> Option<&'static str> {
        match self {
            Ring::Off => None,
            Ring::Loud => Some("loud"),
            Ring::Silent => Some("silent"),
        }
    }
}

/// An incoming call that rings (the shell's `set_ring`,
/// [`crate::ipc::Request::SetRing`]).
///
/// While a call rings in, the phone acts as a phone does: the first short
/// press of the power key silences the ringing instead of switching the
/// screen off (the shell hears `ring_silence` and asks the call app to
/// silence it; the call goes on ringing, silently, and can be answered), and
/// the next press does what it always does ([`ring_power_key`]). A glance
/// (the closed phone's strip, which shows who calls) stays lit while the call
/// rings, and the idle timer does not blank the screen.
///
/// Like the proximity hold it lasts `ttl` (the shell renews it while the call
/// rings), so a shell that died during a ring leaves nothing behind.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RingHold {
    ring: Ring,
    until: Option<Instant>,
}

impl RingHold {
    /// How long a hold lasts when the request does not say.
    pub const DEFAULT_TTL: Duration = Duration::from_secs(30);
    /// The longest hold a request may ask for.
    pub const MAX_TTL: Duration = Duration::from_secs(120);

    /// The call rings so until `now + ttl` (`Off` ends the hold). Whether the
    /// ring changed (a renewal only moves the deadline).
    pub fn set(&mut self, ring: Ring, ttl: Duration, now: Instant) -> bool {
        let changed = ring != self.ring;
        self.until = (ring != Ring::Off).then(|| now + ttl.min(Self::MAX_TTL));
        self.ring = ring;
        changed
    }

    /// How it rings now.
    pub fn ring(&self) -> Ring {
        self.ring
    }

    /// Whether a call rings in (aloud or silently).
    pub fn rings(&self) -> bool {
        self.ring != Ring::Off
    }

    /// The power key silenced the ringing: aloud becomes silent (the
    /// deadline stays). Whether it rang aloud.
    pub fn silence(&mut self) -> bool {
        if self.ring == Ring::Loud {
            self.ring = Ring::Silent;
            true
        } else {
            false
        }
    }

    /// When the hold runs out, while there is one.
    pub fn deadline(&self) -> Option<Instant> {
        self.until
    }

    /// The time is up at `now`: the hold ends. Whether it did.
    pub fn expire(&mut self, now: Instant) -> bool {
        match self.until {
            Some(until) if now >= until => {
                self.until = None;
                self.ring = Ring::Off;
                true
            }
            _ => false,
        }
    }
}

/// What a short press of the power key does about a call that rings in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RingPowerKey {
    /// Nothing: no call rings aloud, the key does what it always does.
    AsAlways,
    /// The ringing is silenced and nothing else happens: the screen stays as
    /// it is (lit, or the glance / peek strip that shows who calls).
    Silence,
    /// The ringing is silenced, and the dark screen comes on as it would
    /// (the lock screen's call card; a closed phone's glance: who calls).
    SilenceAndAsAlways,
}

/// The power key was pressed while a call rings `ring`; `dark`: no panel lit.
pub fn ring_power_key(ring: Ring, dark: bool) -> RingPowerKey {
    match ring {
        Ring::Loud if dark => RingPowerKey::SilenceAndAsAlways,
        Ring::Loud => RingPowerKey::Silence,
        Ring::Off | Ring::Silent => RingPowerKey::AsAlways,
    }
}

/// The power key's press length that still counts as a short press. Anything
/// held longer does nothing: the PMIC's hardware reset sits on long presses.
pub const LONG_PRESS_MS: u32 = 1000;

/// What a power-key release was.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PowerPress {
    /// A tap: toggle the screen.
    Short,
    /// Held: ignored on purpose.
    Long,
    /// A release without a press seen (it was pressed before the compositor
    /// started).
    Stray,
}

/// Tells a short power-key press from a long one, from the event times.
#[derive(Debug, Clone, Default)]
pub struct PowerKey {
    pressed_at: Option<u32>,
}

impl PowerKey {
    /// The key went down at `time_ms` (the input event's clock).
    pub fn press(&mut self, time_ms: u32) {
        self.pressed_at = Some(time_ms);
    }

    /// The key came up at `time_ms`. The clock is libinput's millisecond clock,
    /// which wraps; the difference is taken modulo 2^32.
    pub fn release(&mut self, time_ms: u32) -> PowerPress {
        match self.pressed_at.take() {
            None => PowerPress::Stray,
            Some(down) if time_ms.wrapping_sub(down) < LONG_PRESS_MS => PowerPress::Short,
            Some(_) => PowerPress::Long,
        }
    }
}

/// The idle timer: the screen blanks after `timeout` without input.
#[derive(Debug, Clone)]
pub struct IdleTimer {
    timeout: Option<Duration>,
    last_activity: Instant,
}

/// What the idle timer says when it is asked.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdleCheck {
    /// No timeout set: never blank.
    Disabled,
    /// The time is up: blank now.
    Expired,
    /// Ask again at this instant.
    WaitUntil(Instant),
}

impl IdleTimer {
    /// A timer that starts counting now. `None` (or zero) never blanks.
    pub fn new(timeout: Option<Duration>, now: Instant) -> Self {
        IdleTimer { timeout: timeout.filter(|t| !t.is_zero()), last_activity: now }
    }

    /// The timeout, if any.
    pub fn timeout(&self) -> Option<Duration> {
        self.timeout
    }

    /// Input happened: start counting again.
    pub fn activity(&mut self, now: Instant) {
        self.last_activity = now;
    }

    /// A new timeout, counted from now.
    pub fn set_timeout(&mut self, timeout: Option<Duration>, now: Instant) {
        self.timeout = timeout.filter(|t| !t.is_zero());
        self.last_activity = now;
    }

    /// When the screen will blank if nothing happens, if ever.
    pub fn deadline(&self) -> Option<Instant> {
        self.timeout.map(|t| self.last_activity + t)
    }

    /// Whether the time is up at `now`.
    pub fn check(&self, now: Instant) -> IdleCheck {
        match self.deadline() {
            None => IdleCheck::Disabled,
            Some(deadline) if now >= deadline => IdleCheck::Expired,
            Some(deadline) => IdleCheck::WaitUntil(deadline),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOTH: &[&str] = &["DSI-1", "DSI-2"];

    fn lit(model: &PowerModel, in_use: &[&str]) -> Vec<String> {
        BOTH.iter().filter(|o| model.is_lit(o, in_use.contains(o))).map(|o| (*o).to_owned()).collect()
    }

    #[test]
    fn everything_in_use_is_lit_at_start() {
        let model = PowerModel::default();
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
        assert_eq!(lit(&model, &["DSI-1"]), vec!["DSI-1"]);
    }

    #[test]
    fn blank_and_wake_restore_what_was_lit() {
        let mut model = PowerModel::default();
        model.set("DSI-2", Power::Off, BOTH).unwrap();
        model.blank();
        assert!(lit(&model, BOTH).is_empty());
        model.wake(BOTH);
        assert_eq!(lit(&model, BOTH), vec!["DSI-1"]);
    }

    #[test]
    fn waking_with_both_switched_off_lights_both() {
        let mut model = PowerModel::default();
        model.set("DSI-1", Power::Off, BOTH).unwrap();
        model.set("DSI-2", Power::Off, BOTH).unwrap();
        assert!(!model.any_lit(BOTH));
        model.wake(BOTH);
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
    }

    #[test]
    fn one_panel_on_from_a_blank_lights_only_that_one() {
        let mut model = PowerModel::default();
        model.blank();
        model.set("DSI-2", Power::On, BOTH).unwrap();
        assert_eq!(lit(&model, BOTH), vec!["DSI-2"]);
        model.set("DSI-1", Power::On, BOTH).unwrap();
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
    }

    #[test]
    fn a_panel_the_pose_does_not_use_cannot_be_switched_on() {
        let mut model = PowerModel::default();
        assert!(model.set("DSI-2", Power::On, &["DSI-1"]).is_err());
        assert_eq!(lit(&model, &["DSI-1"]), vec!["DSI-1"]);
    }

    #[test]
    fn all_off_is_a_blank_and_all_on_lights_everything() {
        let mut model = PowerModel::default();
        model.set("DSI-1", Power::Off, BOTH).unwrap();
        model.set_all(Power::Off);
        assert!(model.blanked());
        model.set_all(Power::On);
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
    }

    #[test]
    fn a_pose_change_wakes_and_lights_what_it_brings_in() {
        let mut model = PowerModel::default();
        model.set("DSI-2", Power::Off, BOTH).unwrap();
        model.blank();
        model.pose_changed(BOTH);
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
    }

    #[test]
    fn power_key_short_long_and_stray() {
        let mut key = PowerKey::default();
        assert_eq!(key.release(5), PowerPress::Stray);
        key.press(1000);
        assert_eq!(key.release(1150), PowerPress::Short);
        key.press(2000);
        assert_eq!(key.release(2000 + LONG_PRESS_MS), PowerPress::Long);
        // Across the wrap of the millisecond clock.
        key.press(u32::MAX - 50);
        assert_eq!(key.release(100), PowerPress::Short);
        // A release is matched to one press only.
        assert_eq!(key.release(200), PowerPress::Stray);
    }

    #[test]
    fn idle_timer() {
        let start = Instant::now();
        let mut timer = IdleTimer::new(Some(Duration::from_secs(60)), start);
        assert_eq!(
            timer.check(start + Duration::from_secs(10)),
            IdleCheck::WaitUntil(start + Duration::from_secs(60))
        );
        timer.activity(start + Duration::from_secs(30));
        assert_eq!(
            timer.check(start + Duration::from_secs(60)),
            IdleCheck::WaitUntil(start + Duration::from_secs(90))
        );
        assert_eq!(timer.check(start + Duration::from_secs(90)), IdleCheck::Expired);

        timer.set_timeout(Some(Duration::from_secs(5)), start + Duration::from_secs(100));
        assert_eq!(timer.check(start + Duration::from_secs(106)), IdleCheck::Expired);

        timer.set_timeout(Some(Duration::ZERO), start);
        assert_eq!(timer.check(start + Duration::from_secs(1000)), IdleCheck::Disabled);
        assert_eq!(timer.timeout(), None);
        let never = IdleTimer::new(None, start);
        assert_eq!(never.deadline(), None);
    }

    #[test]
    fn proximity_holds_until_cleared_or_expired() {
        let start = Instant::now();
        let mut hold = ProximityHold::default();
        assert!(!hold.covers("DSI-1"));
        assert_eq!(hold.deadline(), None);
        assert!(hold.set(["DSI-1".to_owned()], Duration::from_secs(30), start));
        assert!(hold.covers("DSI-1") && !hold.covers("DSI-2"));
        // Renewing the same panels is no change, but moves the deadline.
        assert!(!hold.set(["DSI-1".to_owned()], Duration::from_secs(30), start + Duration::from_secs(10)));
        assert_eq!(hold.deadline(), Some(start + Duration::from_secs(40)));
        assert!(!hold.expire(start + Duration::from_secs(39)));
        assert!(hold.expire(start + Duration::from_secs(40)));
        assert!(!hold.covers("DSI-1"));
        assert_eq!(hold.deadline(), None);
        // An empty list ends it; a too long ttl is capped.
        hold.set(["DSI-1".to_owned(), "DSI-2".to_owned()], Duration::from_secs(9999), start);
        assert_eq!(hold.deadline(), Some(start + ProximityHold::MAX_TTL));
        assert_eq!(hold.panels(), vec!["DSI-1".to_owned(), "DSI-2".to_owned()]);
        assert!(hold.set(Vec::new(), Duration::from_secs(30), start));
        assert!(!hold.covers("DSI-2"));
        assert_eq!(hold.deadline(), None);
        assert!(!hold.clear());
    }

    #[test]
    fn a_ring_holds_until_renewal_stops_or_it_ends() {
        let start = Instant::now();
        let mut hold = RingHold::default();
        assert!(!hold.rings());
        assert_eq!(hold.deadline(), None);
        assert!(hold.set(Ring::Loud, Duration::from_secs(30), start));
        assert!(hold.rings());
        assert_eq!(hold.ring(), Ring::Loud);
        // A renewal is no change, but moves the deadline.
        assert!(!hold.set(Ring::Loud, Duration::from_secs(30), start + Duration::from_secs(10)));
        assert_eq!(hold.deadline(), Some(start + Duration::from_secs(40)));
        // The power key silences it once; it still rings (silently).
        assert!(hold.silence());
        assert!(!hold.silence());
        assert_eq!(hold.ring(), Ring::Silent);
        assert!(hold.rings());
        assert_eq!(hold.deadline(), Some(start + Duration::from_secs(40)));
        // Not renewed: it ends by itself.
        assert!(!hold.expire(start + Duration::from_secs(39)));
        assert!(hold.expire(start + Duration::from_secs(40)));
        assert!(!hold.rings());
        assert_eq!(hold.deadline(), None);
        // Off ends it; a too long ttl is capped.
        hold.set(Ring::Silent, Duration::from_secs(9999), start);
        assert_eq!(hold.deadline(), Some(start + RingHold::MAX_TTL));
        assert!(hold.set(Ring::Off, Duration::from_secs(30), start));
        assert_eq!(hold.deadline(), None);
        assert!(!hold.expire(start + Duration::from_secs(9999)));
    }

    #[test]
    fn the_power_key_silences_a_loud_ring_once() {
        // Lit (the call window, the lock screen's card, a glance or a peek
        // strip): silenced, the screen stays as it is.
        assert_eq!(ring_power_key(Ring::Loud, false), RingPowerKey::Silence);
        // Dark: silenced, and the screen comes on as it would.
        assert_eq!(ring_power_key(Ring::Loud, true), RingPowerKey::SilenceAndAsAlways);
        // Silenced already (the next press), or no call: as always.
        assert_eq!(ring_power_key(Ring::Silent, false), RingPowerKey::AsAlways);
        assert_eq!(ring_power_key(Ring::Off, false), RingPowerKey::AsAlways);
        assert_eq!(ring_power_key(Ring::Off, true), RingPowerKey::AsAlways);
    }

    #[test]
    fn ring_words() {
        assert_eq!(Ring::parse("Loud"), Some(Ring::Loud));
        assert_eq!(Ring::parse("silent"), Some(Ring::Silent));
        assert_eq!(Ring::parse("off"), Some(Ring::Off));
        assert_eq!(Ring::parse("ringing"), None);
        assert_eq!(Ring::Loud.name(), Some("loud"));
        assert_eq!(Ring::Off.name(), None);
    }

    #[test]
    fn the_power_key_on_an_open_phone_wakes_both_panels() {
        use crate::ambient::{on_power_key, OnPowerKey};
        let mut model = PowerModel::default();
        model.blank();
        // Open (or no posture at all): the key wakes the screen, and the
        // screen is every panel in use, not the one the key is next to.
        for posture in [Some("open_dpm"), Some("flat"), Some("read"), None] {
            assert_eq!(on_power_key(posture, true, None), OnPowerKey::Wake);
        }
        model.wake(BOTH);
        assert_eq!(lit(&model, BOTH), vec!["DSI-1", "DSI-2"]);
        // A tent / folded back: the pose uses one panel, the key lights that one.
        model.blank();
        model.wake(&["DSI-1"]);
        assert_eq!(lit(&model, &["DSI-1"]), vec!["DSI-1"]);
        // Closed: the glance, not a wake.
        assert_eq!(on_power_key(Some("closed"), true, None), OnPowerKey::Glance);
    }

    #[test]
    fn a_panel_shows_the_picture_only_once_it_is_on_screen() {
        let shown = PanelHw { want_on: true, powered: true, frames_presented: 10, ..Default::default() };
        assert!(shows_picture(shown));
        // Waking (the panel's power-on, ~0.2 s; both one after the other ~0.45 s).
        assert!(!shows_picture(PanelHw { waking: true, ..shown }));
        // Awake, held black until the other one is awake too.
        assert!(!shows_picture(PanelHw { wake_group: true, ..shown }));
        // Awake, the first picture since not flipped onto it yet.
        assert!(!shows_picture(PanelHw { first_picture_pending: true, ..shown }));
        // Going dark, dark, asked dark, never drawn.
        assert!(!shows_picture(PanelHw { blackout: true, ..shown }));
        assert!(!shows_picture(PanelHw { powering_off: true, ..shown }));
        assert!(!shows_picture(PanelHw { powered: false, ..shown }));
        assert!(!shows_picture(PanelHw { want_on: false, ..shown }));
        assert!(!shows_picture(PanelHw { frames_presented: 0, ..shown }));
    }

    #[test]
    fn a_touch_on_a_panel_without_its_picture_reaches_no_client() {
        use TouchOnPanel::*;
        // shown, lit, screen dark, touch_wake
        assert_eq!(touch_on_panel(true, true, false, false), Deliver);
        // The left panel lit for duo-wc but black (waking,
        // or its picture not up yet): swallowed, whatever touch_wake says.
        assert_eq!(touch_on_panel(false, true, false, false), Swallow);
        assert_eq!(touch_on_panel(false, true, false, true), Swallow);
        // A dark screen: only touch_wake wakes it, and even then the touch
        // itself is not delivered.
        assert_eq!(touch_on_panel(false, false, true, false), Swallow);
        assert_eq!(touch_on_panel(false, false, true, true), SwallowAndWake);
        // One dark panel beside a lit one (a tent's back, a panel switched
        // off, the peek strip on the other): not woken by a touch.
        assert_eq!(touch_on_panel(false, false, false, true), Swallow);
    }

    #[test]
    fn a_wake_on_the_peek_strip_holds_the_lit_panel_for_the_dark_one() {
        let s = |v: &[&str]| v.iter().map(|o| (*o).to_owned()).collect::<Vec<_>>();
        // The peek strip lights DSI-2; the power key (or opening) brings
        // DSI-1: DSI-2 waits for it, the picture on both at once.
        assert_eq!(hold_for_wake(&s(&["DSI-1"]), &s(&["DSI-2"]), true), s(&["DSI-2"]));
        // From a dark screen there is nothing lit to hold.
        assert!(hold_for_wake(&s(&["DSI-1", "DSI-2"]), &[], true).is_empty());
        // One panel switched on by hand, or nothing waking: nothing held.
        assert!(hold_for_wake(&s(&["DSI-1"]), &s(&["DSI-2"]), false).is_empty());
        assert!(hold_for_wake(&[], &s(&["DSI-2"]), true).is_empty());
    }

    #[test]
    fn panels_asked_on_while_going_dark_wake_in_one_commit() {
        let panel = |name: &str, powering_off: bool| OffPanel {
            name: name.to_owned(),
            want_on: true,
            powered: false,
            powering_off,
            waking: false,
        };
        // The power key twice in quick succession: DSI-1's off is done,
        // DSI-2's not yet: nothing wakes alone.
        assert!(wake_after_off(&[panel("DSI-1", false), panel("DSI-2", true)]).is_empty());
        // Both done: both, together.
        assert_eq!(
            wake_after_off(&[panel("DSI-1", false), panel("DSI-2", false)]),
            vec!["DSI-1".to_owned(), "DSI-2".to_owned()]
        );
        // A panel that stays off, or is already coming on, is not woken.
        let off = OffPanel { want_on: false, ..panel("DSI-1", false) };
        let waking = OffPanel { waking: true, ..panel("DSI-1", false) };
        assert_eq!(wake_after_off(&[off, panel("DSI-2", false)]), vec!["DSI-2".to_owned()]);
        assert_eq!(wake_after_off(&[waking, panel("DSI-2", false)]), vec!["DSI-2".to_owned()]);
    }

    #[test]
    fn power_words() {
        assert_eq!(Power::parse("ON"), Some(Power::On));
        assert_eq!(Power::parse("off"), Some(Power::Off));
        assert_eq!(Power::parse("dim"), None);
    }
}
