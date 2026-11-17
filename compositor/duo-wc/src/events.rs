//! What the control socket's event stream tells subscribers.
//!
//! A client that sends `{"request":"subscribe"}` keeps its connection open and
//! gets one JSON object per line whenever something it may act on changes:
//!
//! ```text
//! <- {"id":1,"ok":{"subscribed":true,"state":{...}}}
//! <- {"event":"state","changed":["power"],"state":{"pose":"dual","lit":["DSI-1"],...}}
//! <- {"event":"edge_swipe","edge":"bottom","output":"DSI-2","kind":"long","travel":240.0}
//! ```
//!
//! `posture` is duo-posed's last word on how the phone is folded, `ambient`
//! the peek or glance screen while one shows ([`crate::ambient`]).
//!
//! An edge swipe's `edge` is `bottom`, `top`, `left` or `right`. From the
//! bottom its `kind` is `short` (the shell shows the dock), `home` (a long
//! one: the compositor put the panel's windows aside), `long` (a long one on
//! a panel that already showed home: the shell opens the app grid) or
//! `hold` (it stopped while still down: the compositor opened the task
//! overview); from the top `down` (the shell opens its quick menu); from a
//! side `back` (`to`: `app`, the focused window got KEY_BACK; `shell`, a
//! shell surface on that panel is to close; `overview`, it closed).
//!
//! A volume key (`up`, `down`, `mute`; `pressed` and `released`) is never
//! delivered to a client: `{"event":"volume_key","key":"up","state":"pressed",
//! "output":"DSI-2"}` goes to the shell, which changes the volume and shows it
//! on `output` (the active panel), or shows nothing when it is null (the
//! screen is dark: volume keys work without waking it).
//!
//! The pen's top button (Bluetooth, [`crate::penbutton`]) is never delivered
//! either: `{"event":"pen_button","press":"click","output":"DSI-2",
//! "locked":false}` (`double_click`, `hold`); the shell does what Duo
//! Settings set for it on `output`, nothing when it is null (dark).
//!
//! A window that came to the front by itself (a new window's first picture,
//! an app's activation: [`crate::front`]): `{"event":"window_front",
//! "window":11,"app_id":"org.gnome.TextEditor","outputs":["DSI-2"],
//! "why":"opened"}` (`activated`); the shell closes its app grid and quick
//! menu on those panels, which would otherwise cover it.
//!
//! `ring` is how an incoming call rings as the shell last said (`set_ring`:
//! `loud`, `silent`, or null, [`crate::power::RingHold`]). The power key
//! pressed while it rings `loud` silences it instead of switching the screen:
//! `{"event":"ring_silence","key":"power"}` goes to the shell, which asks the
//! call app to stop the ringtone and the vibration (`ring` is `silent` then).
//!
//! `capturing` lists the panels a client is capturing now (a screen share, a
//! recording, a screenshot through the portal: [`crate::capture`]), for the
//! shell's indicator; its change is named `capture`.
//!
//! `laptop` is laptop mode (crate::laptop): `on`, `origin` (`manual`,
//! `auto`), the settings `auto` and `keys_pref`, and while on the `desk` and
//! `keys` panels, the keyboard panel's `layout` (each key's cap in the
//! panel's frame with what it shows in the layout in force: `label`,
//! `shift`, `altgr`; the touchpad; `group`, the layout, and `iso`), the
//! modifiers in force (`shift`, `caps`, `altgr`, `latched`) and the list of
//! layouts while it is up (`menu`: its panel and entries, each a `layout`
//! with its `variant`, `configured`, `active`); the shell draws the keys'
//! labels and the entries' names from it. Turning it on or off is also an
//! event: `{"event":"laptop","on":true,"desk":"DSI-1","keys":"DSI-2"}`.
//!
//! `keyboard` is the seat's keyboard layouts (crate::kbdlayout): `layouts`
//! (each a `layout` and its `variant`) and `active`, the index of the one in
//! force. A layout added from laptop mode's list is also an event:
//! `{"event":"keyboard_layout_added","layout":"de","variant":""}` (the shell
//! adds it to the system's list).
//!
//! `idle_inhibit` names who keeps the screen from blanking by itself now (an
//! idle inhibitor's app, the shell's idle hold's application names:
//! [`crate::idle`]); empty while nobody does.
//!
//! The compositor takes a [`Snapshot`] after every turn of its event loop and
//! compares it with the last one it published ([`changes`]); so every change
//! is caught, whatever caused it (IPC, the power key, the idle timer, a touch, a
//! client), and nothing is sent while nothing changes. Pure: no Smithay types.

use std::collections::BTreeMap;

use serde::Serialize;

use crate::layout::HingeGap;
use crate::pose::Placement;
use crate::split::Axis;

/// Where an overview card's label goes, for the shell to draw (the app's
/// icon and the window's title): in its panel's own frame, logical px.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CardLabel {
    /// The panel.
    pub output: String,
    /// The window the card shows.
    pub window: u64,
    /// The band above the card's picture.
    pub x: i32,
    /// Top.
    pub y: i32,
    /// Width.
    pub w: i32,
    /// Height.
    pub h: i32,
    /// A finger is carrying or swiping the card (its label hides).
    pub dragged: bool,
    /// The window was asked to close and is not gone yet.
    pub closing: bool,
    /// ... and has not answered for a while (it may be forced).
    pub stuck: bool,
}

/// A panel shared by two windows ([`crate::split`]), as it reads now.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SplitSummary {
    /// The panel.
    pub output: String,
    /// `side_by_side` or `stacked`.
    pub axis: Axis,
    /// The two windows, left (or top) first.
    pub windows: [u64; 2],
    /// The first one's share of the room.
    pub share: f64,
    /// Both windows are on the panel (a pose may have moved one away).
    pub active: bool,
    /// The pair is the front of its panel (the divider shows).
    pub front: bool,
}

/// One window, as subscribers see it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WindowSummary {
    /// The control-socket id.
    pub id: u64,
    /// The client's app id.
    pub app_id: Option<String>,
    /// The client's title.
    pub title: Option<String>,
    /// Where it lives.
    pub placement: Placement,
    /// Set aside (home): not shown, still running.
    pub minimized: bool,
}

/// What subscribers may act on.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Snapshot {
    /// `dual`, `single DSI-1`, `span`.
    pub pose: String,
    /// The lit panels, by name.
    pub lit: Vec<String>,
    /// Blanked (idle timeout, power key, `set_power all off`).
    pub blanked: bool,
    /// The hinge gap.
    pub hinge_gap: HingeGap,
    /// Each output's rotation in degrees.
    pub rotation: BTreeMap<String, u32>,
    /// The panel touched last (new windows and the keyboard go there).
    pub active_output: Option<String>,
    /// The panel of the surface with the keyboard focus.
    pub keyboard_output: Option<String>,
    /// The panel the on-screen keyboard is shown on, while it is.
    pub osk_output: Option<String>,
    /// The on-screen keyboard is up for the app but put away by a tap beside
    /// its text field (crate::oskleave): the bar's button brings it back.
    pub osk_away: bool,
    /// The task overview is open (on every lit panel).
    pub overview: bool,
    /// Its cards' label bands, while it is open.
    pub overview_cards: Vec<CardLabel>,
    /// The session is locked (ext-session-lock-v1).
    pub locked: bool,
    /// Windows, back to front.
    pub windows: Vec<WindowSummary>,
    /// Panels shared by two windows.
    pub splits: Vec<SplitSummary>,
    /// The panels whose front window is fullscreen (a video): no status
    /// bar, no dock there.
    pub fullscreen: Vec<String>,
    /// How the phone is folded, as duo-posed last said (`closed`,
    /// `peek_left`, `open_dpm`, ...), or null.
    pub posture: Option<String>,
    /// The ambient screen shown now: `peek` or `glance` (crate::ambient).
    pub ambient: Option<String>,
    /// The lit panels that show home (no window on them but minimized
    /// ones): the shell's home screen is what is seen there.
    pub home: Vec<String>,
    /// A window is being carried (crate::windrag).
    pub carrying: bool,
    /// Panels the proximity sensor holds dark during a call (an ear is
    /// near): not lit, but the screen is not off either (no lock).
    pub proximity: Vec<String>,
    /// An incoming call rings: `loud` (the power key silences it) or
    /// `silent`, as the shell said (`set_ring`, crate::power::RingHold).
    pub ring: Option<String>,
    /// The panels being captured (a screen share, a recording, a
    /// screenshot: crate::capture), for the shell's indicator.
    pub capturing: Vec<String>,
    /// Who keeps the screen from blanking by itself now: the app ids of
    /// seen idle inhibitors and the shell's idle hold's application names
    /// (crate::idle), sorted. Empty: the idle timeout blanks as usual.
    pub idle_inhibit: Vec<String>,
    /// Laptop mode: on or off, its settings, and while on its panels, the
    /// keyboard panel's layout (for the shell's labels) and the modifiers
    /// in force (crate::laptop).
    pub laptop: crate::laptop::Summary,
    /// The keyboard layouts and the one in force (crate::kbdlayout).
    pub keyboard: crate::kbdlayout::Summary,
    /// The external displays: each one's use (`desk` / `mirror`), whether
    /// the shell is to ask, the panels a mirror shows, and the settings
    /// (crate::mirror).
    pub displays: crate::mirror::Summary,
}

/// What differs between two snapshots, by the names subscribers filter on.
pub fn changes(old: &Snapshot, new: &Snapshot) -> Vec<&'static str> {
    let mut changed = Vec::new();
    if old.pose != new.pose {
        changed.push("pose");
    }
    if old.lit != new.lit || old.blanked != new.blanked {
        changed.push("power");
    }
    if old.hinge_gap != new.hinge_gap {
        changed.push("hinge_gap");
    }
    if old.rotation != new.rotation {
        changed.push("rotation");
    }
    if old.active_output != new.active_output {
        changed.push("active_output");
    }
    if old.keyboard_output != new.keyboard_output {
        changed.push("keyboard");
    }
    if old.osk_output != new.osk_output {
        changed.push("osk");
    }
    if old.overview != new.overview || old.overview_cards != new.overview_cards {
        changed.push("overview");
    }
    if old.locked != new.locked {
        changed.push("locked");
    }
    if old.windows != new.windows {
        changed.push("windows");
    }
    if old.splits != new.splits {
        changed.push("split");
    }
    if old.fullscreen != new.fullscreen {
        changed.push("fullscreen");
    }
    if old.posture != new.posture {
        changed.push("posture");
    }
    if old.ambient != new.ambient {
        changed.push("ambient");
    }
    if old.home != new.home {
        changed.push("home");
    }
    if old.carrying != new.carrying {
        changed.push("carrying");
    }
    if old.proximity != new.proximity {
        changed.push("proximity");
    }
    if old.ring != new.ring {
        changed.push("ring");
    }
    if old.capturing != new.capturing {
        changed.push("capture");
    }
    if old.idle_inhibit != new.idle_inhibit {
        changed.push("idle_inhibit");
    }
    if old.displays != new.displays {
        changed.push("displays");
    }
    if old.laptop != new.laptop {
        changed.push("laptop");
    }
    if old.keyboard != new.keyboard {
        changed.push("keyboard");
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> Snapshot {
        Snapshot {
            pose: "dual".into(),
            lit: vec!["DSI-1".into(), "DSI-2".into()],
            blanked: false,
            hinge_gap: HingeGap::On,
            rotation: [("DSI-1".to_owned(), 0), ("DSI-2".to_owned(), 0)].into(),
            active_output: Some("DSI-1".into()),
            keyboard_output: Some("DSI-1".into()),
            osk_output: None,
            osk_away: false,
            overview: false,
            overview_cards: Vec::new(),
            locked: false,
            windows: vec![WindowSummary {
                id: 1,
                app_id: Some("foot".into()),
                title: Some("foot".into()),
                placement: Placement::Output("DSI-1".into()),
                minimized: false,
            }],
            splits: Vec::new(),
            fullscreen: Vec::new(),
            posture: None,
            home: Vec::new(),
            carrying: false,
            ambient: None,
            proximity: Vec::new(),
            ring: None,
            capturing: Vec::new(),
            idle_inhibit: Vec::new(),
            laptop: crate::laptop::Summary {
                on: false,
                origin: None,
                auto: false,
                keys_pref: "DSI-2".into(),
                desk: None,
                keys: None,
                layout: None,
                shift: false,
                caps: false,
                altgr: false,
                latched: Vec::new(),
                menu: None,
            },
            keyboard: crate::kbdlayout::Summary::default(),
            displays: crate::mirror::Summary::default(),
        }
    }

    #[test]
    fn a_display_asking_is_its_own_change() {
        let mut new = base();
        new.displays.connected.push(crate::mirror::DisplaySummary {
            output: "DP-1".into(),
            name: None,
            used_as: crate::mirror::DisplayUse::Mirror,
            asking: true,
            mirrors: vec!["DSI-1".into()],
        });
        assert_eq!(changes(&base(), &new), vec!["displays"]);
        let json = serde_json::to_value(&new).unwrap();
        assert_eq!(json["displays"]["connected"][0]["use"], "mirror");
        assert_eq!(json["displays"]["on_connect"], "ask");
    }

    #[test]
    fn the_keyboard_layouts_are_their_own_change() {
        let mut new = base();
        new.keyboard.layouts =
            vec![crate::kbdlayout::Entry::new("hu", ""), crate::kbdlayout::Entry::new("us", "")];
        assert_eq!(changes(&base(), &new), vec!["keyboard"]);
        let mut switched = new.clone();
        switched.keyboard.active = 1;
        assert_eq!(changes(&new, &switched), vec!["keyboard"]);
        let json = serde_json::to_value(&switched).unwrap();
        assert_eq!(json["keyboard"]["layouts"][1]["layout"], "us");
        assert_eq!(json["keyboard"]["active"], 1);
    }

    #[test]
    fn laptop_mode_is_its_own_change() {
        let mut new = base();
        new.laptop.on = true;
        new.laptop.desk = Some("DSI-1".into());
        new.laptop.keys = Some("DSI-2".into());
        new.laptop.layout = Some(crate::laptop::LayoutSummary::of(
            &crate::laptop::layout(757.0, 538.0, crate::laptop::Form::default()),
            None,
        ));
        assert_eq!(changes(&base(), &new), vec!["laptop"]);
        let json = serde_json::to_value(&new).unwrap();
        assert_eq!(json["laptop"]["keys"], "DSI-2");
        assert!(json["laptop"]["layout"]["keys"].as_array().unwrap().len() > 60);
        let mut shifted = new.clone();
        shifted.laptop.shift = true;
        shifted.laptop.latched = vec![crate::laptop::ModKind::Shift];
        assert_eq!(changes(&new, &shifted), vec!["laptop"]);
        assert_eq!(serde_json::to_value(&shifted).unwrap()["laptop"]["latched"][0], "shift");
    }

    #[test]
    fn a_proximity_hold_is_its_own_change() {
        let mut new = base();
        new.lit = vec!["DSI-2".into()];
        new.proximity = vec!["DSI-1".into()];
        assert_eq!(changes(&base(), &new), vec!["power", "proximity"]);
    }

    #[test]
    fn a_ring_is_its_own_change() {
        let mut new = base();
        new.ring = Some("loud".into());
        assert_eq!(changes(&base(), &new), vec!["ring"]);
        let mut silenced = new.clone();
        silenced.ring = Some("silent".into());
        assert_eq!(changes(&new, &silenced), vec!["ring"]);
        assert_eq!(serde_json::to_value(&silenced).unwrap()["ring"], "silent");
        assert!(serde_json::to_value(base()).unwrap()["ring"].is_null());
    }

    #[test]
    fn a_capture_is_its_own_change() {
        let mut new = base();
        new.capturing = vec!["DSI-1".into()];
        assert_eq!(changes(&base(), &new), vec!["capture"]);
        assert_eq!(serde_json::to_value(&new).unwrap()["capturing"][0], "DSI-1");
    }

    #[test]
    fn an_idle_inhibit_is_its_own_change() {
        let mut new = base();
        new.idle_inhibit = vec!["mpv".into()];
        assert_eq!(changes(&base(), &new), vec!["idle_inhibit"]);
        assert_eq!(serde_json::to_value(&new).unwrap()["idle_inhibit"][0], "mpv");
        assert!(serde_json::to_value(base()).unwrap()["idle_inhibit"].as_array().unwrap().is_empty());
    }

    #[test]
    fn nothing_changed_is_nothing() {
        assert!(changes(&base(), &base()).is_empty());
    }

    #[test]
    fn blanking_is_a_power_change() {
        let mut new = base();
        new.blanked = true;
        new.lit.clear();
        assert_eq!(changes(&base(), &new), vec!["power"]);
    }

    #[test]
    fn each_part_has_its_name() {
        let mut new = base();
        new.pose = "span".into();
        new.rotation.insert("DSI-2".into(), 90);
        new.osk_output = Some("DSI-2".into());
        new.windows[0].title = Some("vim".into());
        new.hinge_gap = HingeGap::Off;
        new.overview = true;
        new.splits.push(SplitSummary {
            output: "DSI-1".into(),
            axis: Axis::SideBySide,
            windows: [1, 2],
            share: 0.5,
            active: true,
            front: true,
        });
        new.fullscreen.push("DSI-1".into());
        new.posture = Some("closed".into());
        new.ambient = Some("glance".into());
        new.home.push("DSI-2".into());
        new.carrying = true;
        assert_eq!(
            changes(&base(), &new),
            vec![
                "pose",
                "hinge_gap",
                "rotation",
                "osk",
                "overview",
                "windows",
                "split",
                "fullscreen",
                "posture",
                "ambient",
                "home",
                "carrying"
            ]
        );
        let mut minimized = base();
        minimized.windows[0].minimized = true;
        assert_eq!(changes(&base(), &minimized), vec!["windows"]);
    }

    #[test]
    fn serialises_the_way_the_socket_sends_it() {
        let json = serde_json::to_value(base()).unwrap();
        assert_eq!(json["hinge_gap"], "on");
        assert_eq!(json["rotation"]["DSI-1"], 0);
        assert_eq!(json["windows"][0]["placement"]["output"], "DSI-1");
        assert!(json["splits"].as_array().unwrap().is_empty());
    }
}
