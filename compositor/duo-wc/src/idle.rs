//! Idle inhibition: what keeps the screen from blanking by itself.
//!
//! The idle timer ([`crate::power::IdleTimer`]) blanks both panels after a
//! while without a touch or a key. Two things hold it while the user only
//! watches (a video, a map, a recipe):
//!
//! - **zwp_idle_inhibit_manager_v1** (any client; mpv, Firefox, Chromium on
//!   Wayland): an inhibitor on a surface holds the blank while that surface
//!   is SEEN, as the protocol says: its window (the surface's root, through
//!   subsurfaces and popups) is mapped, not set aside (home), on a lit panel
//!   that shows windows (not locked, no peek or glance), and not covered
//!   whole by the windows in front of it there ([`seen_through`]). An
//!   inhibitor on a surface nobody sees does nothing until it is seen again;
//!   one made while the screen is dark does not light it.
//! - **the shell's idle hold** (`set_idle_hold`, trusted only): duo-shell
//!   serves org.freedesktop.ScreenSaver on the session bus (Chromium,
//!   Firefox, and every GTK4 app through the Inhibit portal, whose GTK
//!   backend falls back to it) and passes the inhibitors on with their
//!   callers' process ids. A holder whose process has windows holds while
//!   one of THOSE is seen; a holder without windows of its own (the portal
//!   backend, which speaks for the app) while any app window is seen
//!   ([`hold_honored`]). The hold lasts `ttl` unless renewed, so a shell
//!   that died leaves nothing behind ([`IdleHold`]).
//!
//! Neither wakes a dark screen, holds the lock screen, keeps the home screen
//! lit, or stops the power key: the idle timeout is all they hold. When the
//! last one ends, the idle timer counts from then.
//!
//! The rules here are pure; `crate::inhibit` applies them to the windows.

use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use smithay::utils::{Logical, Rectangle};

/// Surfaces with inhibitors, each with how many it has (a client may make
/// more than one for a surface; each is destroyed on its own).
#[derive(Debug, Clone)]
pub struct Inhibitors<K> {
    entries: Vec<(K, u32)>,
}

impl<K> Default for Inhibitors<K> {
    fn default() -> Self {
        Inhibitors { entries: Vec::new() }
    }
}

impl<K: PartialEq + Clone> Inhibitors<K> {
    /// An inhibitor was made for `key`.
    pub fn add(&mut self, key: K) {
        match self.entries.iter_mut().find(|(k, _)| *k == key) {
            Some((_, count)) => *count += 1,
            None => self.entries.push((key, 1)),
        }
    }

    /// One of `key`'s inhibitors was destroyed. Whether it had one.
    pub fn remove(&mut self, key: &K) -> bool {
        let Some(index) = self.entries.iter().position(|(k, _)| k == key) else {
            return false;
        };
        self.entries[index].1 -= 1;
        if self.entries[index].1 == 0 {
            self.entries.remove(index);
        }
        true
    }

    /// Forget every key `keep` says no to (dead surfaces: a client that
    /// went away destroys nothing on its own).
    pub fn retain(&mut self, mut keep: impl FnMut(&K) -> bool) {
        self.entries.retain(|(k, _)| keep(k));
    }

    /// The keys with inhibitors.
    pub fn keys(&self) -> impl Iterator<Item = &K> {
        self.entries.iter().map(|(k, _)| k)
    }

    /// How many inhibitors `key` has.
    pub fn count(&self, key: &K) -> u32 {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, c)| *c).unwrap_or(0)
    }

    /// Whether no surface has one.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// One inhibitor the shell passes on (org.freedesktop.ScreenSaver.Inhibit):
/// the caller's application name, and its process id if the shell could
/// learn it from the bus.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Holder {
    /// The application's name as it gave it (a label: logs, the state).
    pub app: String,
    /// The calling process (its D-Bus connection's).
    #[serde(default)]
    pub pid: Option<u32>,
}

/// The most holders one `set_idle_hold` may carry (the rest are dropped).
pub const MAX_HOLDERS: usize = 32;

/// The longest application name kept (in characters).
pub const MAX_APP_LEN: usize = 64;

/// An application name fit for a log line and the state: printable, one
/// line, at most [`MAX_APP_LEN`] characters; `unnamed` for nothing.
pub fn clean_app(app: &str) -> String {
    let cleaned: String = app
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(MAX_APP_LEN)
        .collect();
    if cleaned.is_empty() {
        "unnamed".to_owned()
    } else {
        cleaned
    }
}

/// The shell's idle hold: its holders until `until` (renewed while any is
/// there, [`crate::ipc::Request::SetIdleHold`]).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct IdleHold {
    holders: Vec<Holder>,
    until: Option<Instant>,
}

impl IdleHold {
    /// How long a hold lasts when the request does not say.
    pub const DEFAULT_TTL: Duration = Duration::from_secs(30);
    /// The longest hold a request may ask for.
    pub const MAX_TTL: Duration = Duration::from_secs(120);

    /// These holders hold until `now + ttl` (none ends the hold). Names are
    /// cleaned, duplicates dropped, at most [`MAX_HOLDERS`] kept. Whether
    /// the holders changed (a renewal only moves the deadline).
    pub fn set(&mut self, holders: Vec<Holder>, ttl: Duration, now: Instant) -> bool {
        let mut cleaned: Vec<Holder> = holders
            .into_iter()
            .map(|h| Holder { app: clean_app(&h.app), pid: h.pid.filter(|pid| *pid > 0) })
            .collect();
        cleaned.sort();
        cleaned.dedup();
        cleaned.truncate(MAX_HOLDERS);
        let changed = cleaned != self.holders;
        self.until = (!cleaned.is_empty()).then(|| now + ttl.min(Self::MAX_TTL));
        self.holders = cleaned;
        changed
    }

    /// The holders now.
    pub fn holders(&self) -> &[Holder] {
        &self.holders
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
                self.holders.clear();
                true
            }
            _ => false,
        }
    }
}

/// One app window as the screen shows it now: whose process it is, and
/// whether it is seen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Shown {
    /// The window's client process, if known.
    pub pid: Option<u32>,
    /// Seen now (see the module's rules).
    pub seen: bool,
}

/// Whether a holder of the shell's hold holds now: a process with windows
/// while one of its own is seen (a browser playing in a window the user
/// covered with another app does not), a process without (the portal's
/// backend, which speaks for the app it got the inhibitor from; a process
/// whose id is unknown) while any app window is seen.
pub fn hold_honored(pid: Option<u32>, windows: &[Shown]) -> bool {
    let own = pid.filter(|pid| windows.iter().any(|w| w.pid == Some(*pid)));
    match own {
        Some(pid) => windows.iter().any(|w| w.pid == Some(pid) && w.seen),
        None => windows.iter().any(|w| w.seen),
    }
}

/// A part of a window narrower than this (logical px) left uncovered does
/// not make it seen: a rounding seam between two windows, a client-side
/// corner.
pub const MIN_SEEN_SIDE: i32 = 8;

/// Whether `target` (a window's area on a panel) shows past the areas in
/// front of it there: a piece at least [`MIN_SEEN_SIDE`] in both directions
/// is left uncovered.
pub fn seen_through(target: Rectangle<i32, Logical>, front: &[Rectangle<i32, Logical>]) -> bool {
    if target.size.w < MIN_SEEN_SIDE || target.size.h < MIN_SEEN_SIDE {
        return false;
    }
    target
        .subtract_rects(front.iter().copied())
        .iter()
        .any(|rest| rest.size.w >= MIN_SEEN_SIDE && rest.size.h >= MIN_SEEN_SIDE)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x: i32, y: i32, w: i32, h: i32) -> Rectangle<i32, Logical> {
        Rectangle::new((x, y).into(), (w, h).into())
    }

    #[test]
    fn inhibitors_count_per_surface() {
        let mut inhibitors = Inhibitors::default();
        assert!(inhibitors.is_empty());
        inhibitors.add(1);
        inhibitors.add(1);
        inhibitors.add(2);
        assert_eq!(inhibitors.count(&1), 2);
        assert_eq!(inhibitors.keys().copied().collect::<Vec<_>>(), vec![1, 2]);
        // One of two destroyed: the surface still has one.
        assert!(inhibitors.remove(&1));
        assert_eq!(inhibitors.count(&1), 1);
        assert!(inhibitors.remove(&1));
        assert_eq!(inhibitors.count(&1), 0);
        // A destroy for a surface already forgotten (it died first).
        assert!(!inhibitors.remove(&1));
        // Dead surfaces go.
        inhibitors.retain(|k| *k != 2);
        assert!(inhibitors.is_empty());
    }

    #[test]
    fn a_hold_lasts_until_renewal_stops_or_it_ends() {
        let start = Instant::now();
        let firefox = Holder { app: "firefox".into(), pid: Some(100) };
        let mut hold = IdleHold::default();
        assert_eq!(hold.deadline(), None);
        assert!(hold.set(vec![firefox.clone()], Duration::from_secs(30), start));
        assert_eq!(hold.holders(), std::slice::from_ref(&firefox));
        // A renewal is no change, but moves the deadline.
        assert!(!hold.set(vec![firefox.clone()], Duration::from_secs(30), start + Duration::from_secs(10)));
        assert_eq!(hold.deadline(), Some(start + Duration::from_secs(40)));
        assert!(!hold.expire(start + Duration::from_secs(39)));
        assert!(hold.expire(start + Duration::from_secs(40)));
        assert!(hold.holders().is_empty());
        assert_eq!(hold.deadline(), None);
        // A too long ttl is capped; none ends it.
        hold.set(vec![firefox], Duration::from_secs(9999), start);
        assert_eq!(hold.deadline(), Some(start + IdleHold::MAX_TTL));
        assert!(hold.set(Vec::new(), Duration::from_secs(30), start));
        assert_eq!(hold.deadline(), None);
        assert!(!hold.expire(start + Duration::from_secs(9999)));
    }

    #[test]
    fn holders_are_cleaned_deduplicated_and_capped() {
        let start = Instant::now();
        let mut hold = IdleHold::default();
        let many: Vec<Holder> = (0..100)
            .map(|i| Holder { app: format!("app{i:03}"), pid: Some(i + 1) })
            .chain([
                Holder { app: "Chromium\n\tvideo".into(), pid: Some(0) },
                Holder { app: "Chromium video".into(), pid: None },
                Holder { app: "   ".into(), pid: None },
            ])
            .collect();
        hold.set(many, Duration::from_secs(30), start);
        assert_eq!(hold.holders().len(), MAX_HOLDERS);
        assert_eq!(clean_app("Chromium\n\tvideo"), "Chromium video");
        assert_eq!(clean_app(""), "unnamed");
        assert_eq!(clean_app(&"x".repeat(200)).chars().count(), MAX_APP_LEN);
        // pid 0 is no pid; the same holder twice is one.
        hold.set(
            vec![
                Holder { app: "Chromium\n\tvideo".into(), pid: Some(0) },
                Holder { app: "Chromium video".into(), pid: None },
            ],
            Duration::from_secs(30),
            start,
        );
        assert_eq!(hold.holders(), &[Holder { app: "Chromium video".into(), pid: None }]);
    }

    #[test]
    fn a_holder_with_windows_holds_while_one_of_its_own_is_seen() {
        let browser_seen = Shown { pid: Some(100), seen: true };
        let browser_covered = Shown { pid: Some(100), seen: false };
        let other_seen = Shown { pid: Some(200), seen: true };
        assert!(hold_honored(Some(100), &[browser_seen, other_seen]));
        // The browser covered by another app: its hold does nothing.
        assert!(!hold_honored(Some(100), &[browser_covered, other_seen]));
        // No window of its own (the portal's backend), or no pid: any
        // app window seen holds, none (home, lock) does not.
        assert!(hold_honored(Some(300), &[browser_covered, other_seen]));
        assert!(hold_honored(None, &[other_seen]));
        assert!(!hold_honored(None, &[browser_covered]));
        assert!(!hold_honored(Some(300), &[]));
    }

    #[test]
    fn a_window_is_seen_unless_covered_whole() {
        let panel = rect(0, 0, 538, 757);
        // Nothing in front.
        assert!(seen_through(panel, &[]));
        // A window of the same size in front: covered.
        assert!(!seen_through(panel, &[panel]));
        // Two halves together cover it (a split in front).
        assert!(!seen_through(panel, &[rect(0, 0, 538, 380), rect(0, 378, 538, 379)]));
        // A seam of 2 px or a sliver at the side does not count.
        assert!(!seen_through(panel, &[rect(0, 0, 538, 377), rect(0, 379, 538, 378)]));
        assert!(!seen_through(panel, &[rect(4, 0, 534, 757)]));
        // A small window in front (a picture in picture): seen around it.
        assert!(seen_through(panel, &[rect(300, 500, 200, 150)]));
        // Half of it showing.
        assert!(seen_through(panel, &[rect(0, 0, 538, 380)]));
        // Nothing of it on this panel to begin with.
        assert!(!seen_through(rect(0, 0, 4, 757), &[]));
    }
}
