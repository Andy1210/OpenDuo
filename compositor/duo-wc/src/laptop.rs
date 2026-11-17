//! Laptop mode, the pure part: which panel is which, when it turns on by
//! itself, the keyboard panel's keys and touchpad, the modifier keys.
//!
//! Laptop mode needs no external display. One panel is a desk (windows
//! float there with their own title bars, a taskbar, a pointer: the same
//! desk as an external display's, [`crate::desk`]); the other panel is the
//! laptop's lower half: a full keyboard and a touchpad, drawn and read by
//! duo-wc ([`crate::laptopmode`]). Never "DeX": that is Samsung's name.
//!
//! - **Which panel is the keyboard** ([`panels`]): the lower one when both
//!   panels are turned alike to landscape (duo-posed turns them together;
//!   90 degrees puts DSI-1 on top of DSI-2, 270 DSI-2 on top: the lower one
//!   is the one lying on the table in the laptop posture), else the user's
//!   choice (`laptop_keys`, DSI-2 by default).
//! - **By itself** ([`Auto`], `laptop_auto`, off by default): duo-posed's
//!   posture `compose` (Microsoft's own laptop posture: one half flat, the
//!   other up, landscape) turns it on; leaving the posture turns it off
//!   again, but only if the posture had turned it on. Turned off by hand in
//!   the posture, it stays off until the posture is left.
//! - **The keyboard** ([`rows`], [`layout`]): six rows of a laptop's keys
//!   by their place (evdev codes): Esc, F1-F12, Home, End, Del and the key
//!   that ends laptop mode (held) on top, then the keyboard of the layout in
//!   force, as on a physical one: an ISO board (the key left of Z, a tall
//!   Enter) when the layout has a key of its own there (Hungarian, German),
//!   else ANSI (US). What a character key shows comes from the seat's own
//!   keymap ([`group_keys`]: its symbol plain, with Shift and with AltGr),
//!   so every layout gets its own arrangement; its keys go into the seat
//!   unchanged (xkb does the rest, dead keys too). The layout key
//!   ([`Kind::Layout`]) shows the layout in force; a list of layouts
//!   ([`menu`]) comes up when it is held. Landscape: the keys along the
//!   hinge, the touchpad below them; portrait: the keys at the bottom, the
//!   touchpad above. A key is hit by its cell (the gaps between the keys
//!   belong to the nearest key).
//! - **Modifiers** ([`Mods`]): held with another key as on a keyboard; a
//!   modifier tapped on its own stays down for the next key (latched), a
//!   second tap lets it go.

use std::collections::HashMap;

use serde::Serialize;

use crate::overview::Rect;

/// The panel that is the keyboard when the posture does not say.
pub const DEFAULT_KEYS: &str = "DSI-2";

/// duo-posed's name for Microsoft's laptop posture ("compose": one half
/// flat on the table, the other standing, landscape).
pub const LAPTOP_POSTURE: &str = "compose";

/// The layer-shell namespace of the shell's labels on the keyboard panel
/// (the only surface drawn there).
pub const LABELS_NAMESPACE: &str = "duo-laptop-keys";

/// How long the exit key is held to end laptop mode (ms).
pub const EXIT_HOLD_MS: u32 = 600;

/// What duo-wc.conf says about laptop mode.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    /// `laptop_auto`: the laptop posture turns it on by itself.
    pub auto: bool,
    /// `laptop_keys`: the keyboard panel when the posture does not say.
    pub keys: String,
}

impl Default for Config {
    fn default() -> Self {
        Config { auto: false, keys: DEFAULT_KEYS.to_owned() }
    }
}

impl Config {
    /// The config keys this takes.
    pub const KEYS: &'static [&'static str] = &["laptop_auto", "laptop_keys"];

    /// One `key = value` line.
    pub fn set(&mut self, key: &str, value: &str) -> Result<(), String> {
        match key {
            "laptop_auto" => {
                self.auto = match value {
                    "on" => true,
                    "off" => false,
                    _ => return Err(format!("laptop_auto is on or off, not {value}")),
                };
            }
            "laptop_keys" => {
                if !is_panel_name(value) {
                    return Err(format!("laptop_keys is DSI-1 or DSI-2, not {value}"));
                }
                self.keys = value.to_owned();
            }
            other => return Err(format!("unknown key {other}")),
        }
        Ok(())
    }
}

impl Config {
    /// The settings as changed live (Duo Settings through `set_laptop`),
    /// over the config file's: duo-wc keeps them in its state file
    /// (`$XDG_STATE_HOME/duo-wc/laptop`, `auto on|off` and `keys DSI-N`,
    /// one per line), so an older duo-wc never meets a key it does not
    /// know in duo-wc.conf. Lines it does not understand are skipped.
    pub fn with_state(mut self, text: &str) -> Self {
        for line in text.lines() {
            let mut words = line.split_whitespace();
            match (words.next(), words.next()) {
                (Some("auto"), Some("on")) => self.auto = true,
                (Some("auto"), Some("off")) => self.auto = false,
                (Some("keys"), Some(panel)) if is_panel_name(panel) => self.keys = panel.to_owned(),
                _ => {}
            }
        }
        self
    }

    /// The state file's text for these settings.
    pub fn state_text(&self) -> String {
        format!("auto {}\nkeys {}\n", if self.auto { "on" } else { "off" }, self.keys)
    }
}

/// DSI-1 or DSI-2.
pub fn is_panel_name(name: &str) -> bool {
    name == "DSI-1" || name == "DSI-2"
}

/// The other panel.
pub fn other_panel(name: &str) -> &'static str {
    if name == "DSI-1" {
        "DSI-2"
    } else {
        "DSI-1"
    }
}

/// The lower panel, from the panels' rotations (DSI-1's, DSI-2's), when
/// both are turned alike to landscape: 90 lays DSI-1 out on top of DSI-2
/// (the device turned clockwise), 270 DSI-2 on top. None otherwise.
pub fn lower_panel(rotations: (u32, u32)) -> Option<&'static str> {
    match rotations {
        (90, 90) => Some("DSI-2"),
        (270, 270) => Some("DSI-1"),
        _ => None,
    }
}

/// The desk and the keyboard panel: the keyboard is the lower panel when
/// the rotations say which, else `preferred`.
pub fn panels(preferred: &str, rotations: (u32, u32)) -> (String, String) {
    let keys =
        lower_panel(rotations).unwrap_or(if is_panel_name(preferred) { preferred } else { DEFAULT_KEYS });
    (other_panel(keys).to_owned(), keys.to_owned())
}

/// Who turned laptop mode on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Origin {
    /// The user (Duo Settings, the quick menu, `duoctl set_laptop on`).
    Manual,
    /// The laptop posture (`laptop_auto`).
    Auto,
}

/// What the posture rule asks for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AutoStep {
    /// Turn laptop mode on (by the posture).
    TurnOn,
    /// Turn it off (the posture had turned it on and is left).
    TurnOff,
    /// Leave it as it is.
    Nothing,
}

/// The posture rule's memory: whether the user turned laptop mode off
/// while in the laptop posture (it then stays off until the posture is
/// left).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Auto {
    /// Turned off by hand in the posture.
    pub suppressed: bool,
}

impl Auto {
    /// The posture is now `laptop_posture` (in it or not); `enabled` is
    /// `laptop_auto`, `on` who turned laptop mode on, if it is on.
    pub fn step(&mut self, enabled: bool, laptop_posture: bool, on: Option<Origin>) -> AutoStep {
        if !laptop_posture {
            self.suppressed = false;
            return if on == Some(Origin::Auto) { AutoStep::TurnOff } else { AutoStep::Nothing };
        }
        if enabled && on.is_none() && !self.suppressed {
            AutoStep::TurnOn
        } else {
            AutoStep::Nothing
        }
    }

    /// The user turned laptop mode off; in the posture it stays off.
    pub fn user_off(&mut self, laptop_posture: bool) {
        if laptop_posture {
            self.suppressed = true;
        }
    }
}

/// evdev key codes (linux/input-event-codes.h).
#[allow(missing_docs)]
pub mod code {
    pub const ESC: u32 = 1;
    pub const N1: u32 = 2;
    pub const N2: u32 = 3;
    pub const N3: u32 = 4;
    pub const N4: u32 = 5;
    pub const N5: u32 = 6;
    pub const N6: u32 = 7;
    pub const N7: u32 = 8;
    pub const N8: u32 = 9;
    pub const N9: u32 = 10;
    pub const N0: u32 = 11;
    pub const MINUS: u32 = 12;
    pub const EQUAL: u32 = 13;
    pub const BACKSPACE: u32 = 14;
    pub const TAB: u32 = 15;
    pub const Q: u32 = 16;
    pub const W: u32 = 17;
    pub const E: u32 = 18;
    pub const R: u32 = 19;
    pub const T: u32 = 20;
    pub const Y: u32 = 21;
    pub const U: u32 = 22;
    pub const I: u32 = 23;
    pub const O: u32 = 24;
    pub const P: u32 = 25;
    pub const LEFTBRACE: u32 = 26;
    pub const RIGHTBRACE: u32 = 27;
    pub const ENTER: u32 = 28;
    pub const LEFTCTRL: u32 = 29;
    pub const A: u32 = 30;
    pub const S: u32 = 31;
    pub const D: u32 = 32;
    pub const F: u32 = 33;
    pub const G: u32 = 34;
    pub const H: u32 = 35;
    pub const J: u32 = 36;
    pub const K: u32 = 37;
    pub const L: u32 = 38;
    pub const SEMICOLON: u32 = 39;
    pub const APOSTROPHE: u32 = 40;
    pub const GRAVE: u32 = 41;
    pub const LEFTSHIFT: u32 = 42;
    pub const BACKSLASH: u32 = 43;
    pub const Z: u32 = 44;
    pub const X: u32 = 45;
    pub const C: u32 = 46;
    pub const V: u32 = 47;
    pub const B: u32 = 48;
    pub const N: u32 = 49;
    pub const M: u32 = 50;
    pub const COMMA: u32 = 51;
    pub const DOT: u32 = 52;
    pub const SLASH: u32 = 53;
    pub const RIGHTSHIFT: u32 = 54;
    pub const LEFTALT: u32 = 56;
    pub const SPACE: u32 = 57;
    pub const CAPSLOCK: u32 = 58;
    pub const F1: u32 = 59;
    pub const F2: u32 = 60;
    pub const F3: u32 = 61;
    pub const F4: u32 = 62;
    pub const F5: u32 = 63;
    pub const F6: u32 = 64;
    pub const F7: u32 = 65;
    pub const F8: u32 = 66;
    pub const F9: u32 = 67;
    pub const F10: u32 = 68;
    /// The ISO key left of Z (xkb's `<LSGT>`).
    pub const KEY_102ND: u32 = 86;
    pub const F11: u32 = 87;
    pub const F12: u32 = 88;
    pub const RIGHTCTRL: u32 = 97;
    pub const RIGHTALT: u32 = 100;
    pub const HOME: u32 = 102;
    pub const UP: u32 = 103;
    pub const LEFT: u32 = 105;
    pub const RIGHT: u32 = 106;
    pub const END: u32 = 107;
    pub const DOWN: u32 = 108;
    pub const DELETE: u32 = 111;
    pub const LEFTMETA: u32 = 125;
}

/// A modifier key's kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModKind {
    /// Shift.
    Shift,
    /// Control.
    Ctrl,
    /// Alt.
    Alt,
    /// Super (the logo key).
    Super,
    /// The right Alt key: AltGr where the layout has a third level (it
    /// types the symbols drawn small at a cap's bottom right), else Alt.
    #[serde(rename = "altgr")]
    AltGr,
}

/// What a key does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// A character key: what it shows comes from the keymap ([`Cap`]).
    Char,
    /// Types, with a label of its own (Esc, Enter, an arrow ...).
    Key,
    /// A modifier: held, or latched by a tap ([`Mods`]).
    Mod(ModKind),
    /// Ends laptop mode when held [`EXIT_HOLD_MS`].
    Exit,
    /// The layout in force: tapped, the next one; held [`LAYOUT_HOLD_MS`],
    /// a list of layouts ([`menu`]).
    Layout,
}

/// Which part of its cell a key takes (the up and down arrows share one;
/// an ISO board's Enter is two rows tall).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Part {
    /// All of it.
    Whole,
    /// The upper half.
    Top,
    /// The lower half.
    Bottom,
    /// Its row and the one below (the row below leaves its room free).
    Tall,
}

/// One key of the keyboard panel, by its place.
#[derive(Debug, Clone, PartialEq)]
pub struct KeyDef {
    /// A stable name (xkb's key name for a character key: `ad01`; the
    /// shell's, the log's).
    pub id: &'static str,
    /// Its evdev code (0: the exit key and the layout key, not keys of the
    /// seat).
    pub code: u32,
    /// What it shows (a character key's comes from the keymap).
    pub label: &'static str,
    /// How wide, in key units (a row is [`ROW_UNITS`]).
    pub units: f64,
    /// What it does.
    pub kind: Kind,
    /// Which part of its cell.
    pub part: Part,
}

/// A row's width in key units.
pub const ROW_UNITS: f64 = 15.0;

/// How long the layout key is held for the list of layouts (ms).
pub const LAYOUT_HOLD_MS: u32 = 450;

fn ch(id: &'static str, code: u32) -> KeyDef {
    KeyDef { id, code, label: "", units: 1.0, kind: Kind::Char, part: Part::Whole }
}

fn key(id: &'static str, code: u32, label: &'static str, units: f64) -> KeyDef {
    KeyDef { id, code, label, units, kind: Kind::Key, part: Part::Whole }
}

fn modkey(id: &'static str, code: u32, label: &'static str, units: f64, kind: ModKind) -> KeyDef {
    KeyDef { id, code, label, units, kind: Kind::Mod(kind), part: Part::Whole }
}

use code::*;

/// The digit row's character keys (xkb `<TLDE>`, `<AE01>` ... `<AE12>`).
const DIGIT_KEYS: [(&str, u32); 13] = [
    ("tlde", GRAVE),
    ("ae01", N1),
    ("ae02", N2),
    ("ae03", N3),
    ("ae04", N4),
    ("ae05", N5),
    ("ae06", N6),
    ("ae07", N7),
    ("ae08", N8),
    ("ae09", N9),
    ("ae10", N0),
    ("ae11", MINUS),
    ("ae12", EQUAL),
];

/// The top letter row's (`<AD01>` ... `<AD12>`).
const Q_KEYS: [(&str, u32); 12] = [
    ("ad01", Q),
    ("ad02", W),
    ("ad03", E),
    ("ad04", R),
    ("ad05", T),
    ("ad06", Y),
    ("ad07", U),
    ("ad08", I),
    ("ad09", O),
    ("ad10", P),
    ("ad11", LEFTBRACE),
    ("ad12", RIGHTBRACE),
];

/// The home row's (`<AC01>` ... `<AC11>`).
const A_KEYS: [(&str, u32); 11] = [
    ("ac01", A),
    ("ac02", S),
    ("ac03", D),
    ("ac04", F),
    ("ac05", G),
    ("ac06", H),
    ("ac07", J),
    ("ac08", K),
    ("ac09", L),
    ("ac10", SEMICOLON),
    ("ac11", APOSTROPHE),
];

/// The bottom letter row's (`<AB01>` ... `<AB10>`).
const Z_KEYS: [(&str, u32); 10] = [
    ("ab01", Z),
    ("ab02", X),
    ("ab03", C),
    ("ab04", V),
    ("ab05", B),
    ("ab06", N),
    ("ab07", M),
    ("ab08", COMMA),
    ("ab09", DOT),
    ("ab10", SLASH),
];

/// Every character key's evdev code (both boards').
pub fn char_codes() -> impl Iterator<Item = u32> {
    DIGIT_KEYS
        .iter()
        .chain(Q_KEYS.iter())
        .chain(A_KEYS.iter())
        .chain(Z_KEYS.iter())
        .map(|(_, c)| *c)
        .chain([BACKSLASH, KEY_102ND])
}

/// Which board: ISO (102 keys: the key left of Z, a tall Enter, the key
/// left of it on the home row) or ANSI (101: a long left Shift, the
/// backslash key above Enter).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Form {
    /// ISO.
    pub iso: bool,
}

/// The keyboard's rows for a board, top (the hinge side in landscape) first.
pub fn rows(form: Form) -> Vec<Vec<KeyDef>> {
    let mut top = vec![key("esc", ESC, "Esc", 1.0)];
    let fkeys = [
        ("f1", F1, "F1"),
        ("f2", F2, "F2"),
        ("f3", F3, "F3"),
        ("f4", F4, "F4"),
        ("f5", F5, "F5"),
        ("f6", F6, "F6"),
        ("f7", F7, "F7"),
        ("f8", F8, "F8"),
        ("f9", F9, "F9"),
        ("f10", F10, "F10"),
        ("f11", F11, "F11"),
        ("f12", F12, "F12"),
    ];
    top.extend(fkeys.iter().map(|&(id, code, label)| key(id, code, label, 0.8)));
    top.extend([
        key("home", HOME, "Home", 1.1),
        key("end", END, "End", 1.1),
        key("delete", DELETE, "Del", 1.1),
    ]);
    top.push(KeyDef { id: "exit", code: 0, label: "⌨", units: 1.1, kind: Kind::Exit, part: Part::Whole });

    let mut digits: Vec<KeyDef> = DIGIT_KEYS.iter().map(|&(id, c)| ch(id, c)).collect();
    digits.push(key("backspace", BACKSPACE, "⌫", 2.0));

    let mut q = vec![key("tab", TAB, "Tab", 1.5)];
    q.extend(Q_KEYS.iter().map(|&(id, c)| ch(id, c)));
    let mut a = Vec::new();
    let mut z = Vec::new();
    if form.iso {
        q.push(KeyDef {
            id: "enter",
            code: ENTER,
            label: "Enter",
            units: 1.5,
            kind: Kind::Key,
            part: Part::Tall,
        });
        a.push(key("capslock", CAPSLOCK, "Caps", 1.5));
        a.extend(A_KEYS.iter().map(|&(id, c)| ch(id, c)));
        a.push(ch("bksl", BACKSLASH));
        // The Enter of the row above takes the last 1.5 units.
        z.push(modkey("shift_l", LEFTSHIFT, "Shift", 1.25, ModKind::Shift));
        z.push(ch("lsgt", KEY_102ND));
    } else {
        q.push(KeyDef { units: 1.5, ..ch("bksl", BACKSLASH) });
        a.push(key("capslock", CAPSLOCK, "Caps", 1.75));
        a.extend(A_KEYS.iter().map(|&(id, c)| ch(id, c)));
        a.push(key("enter", ENTER, "Enter", 2.25));
        z.push(modkey("shift_l", LEFTSHIFT, "Shift", 2.25, ModKind::Shift));
    }
    z.extend(Z_KEYS.iter().map(|&(id, c)| ch(id, c)));
    z.push(modkey("shift_r", RIGHTSHIFT, "Shift", 2.75, ModKind::Shift));

    let space = vec![
        modkey("ctrl_l", LEFTCTRL, "Ctrl", 1.25, ModKind::Ctrl),
        modkey("super", LEFTMETA, "Super", 1.25, ModKind::Super),
        modkey("alt_l", LEFTALT, "Alt", 1.25, ModKind::Alt),
        key("space", SPACE, "", 4.75),
        modkey("alt_r", RIGHTALT, "Alt", 1.25, ModKind::AltGr),
        KeyDef { id: "layout", code: 0, label: "", units: 1.0, kind: Kind::Layout, part: Part::Whole },
        modkey("ctrl_r", RIGHTCTRL, "Ctrl", 1.25, ModKind::Ctrl),
        key("left", LEFT, "←", 1.0),
        KeyDef { id: "up", code: UP, label: "↑", units: 1.0, kind: Kind::Key, part: Part::Top },
        KeyDef { id: "down", code: DOWN, label: "↓", units: 0.0, kind: Kind::Key, part: Part::Bottom },
        key("right", RIGHT, "→", 1.0),
    ];
    vec![top, digits, q, a, z, space]
}

/// How many rows the keyboard has.
pub const ROW_COUNT: usize = 6;

/// What a character key shows in one layout: its symbol plain, with Shift,
/// with AltGr (empty: none, or the same as plain / shifted).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Cap {
    /// Plain.
    pub base: String,
    /// With Shift.
    pub shift: String,
    /// With AltGr (the third level).
    pub altgr: String,
}

/// One layout (one group of the seat's keymap) as the keyboard panel
/// shows it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct GroupKeys {
    /// The layout (`hu`) and its variant.
    pub entry: crate::kbdlayout::Entry,
    /// Its board.
    pub form: Form,
    /// The right Alt key is AltGr (ISO_Level3_Shift) in this layout.
    pub altgr: bool,
    /// Each character key's cap, by evdev code.
    pub caps: HashMap<u32, Cap>,
}

use smithay::input::keyboard::xkb;

/// What a symbol looks like on a cap: its character; a dead key's accent
/// (`dead_acute`: ´); nothing for a symbol that is no character.
pub fn glyph(sym: xkb::Keysym) -> String {
    if sym == xkb::Keysym::NoSymbol {
        return String::new();
    }
    let name = xkb::keysym_get_name(sym);
    if let Some(dead) = name.strip_prefix("dead_") {
        return dead_glyph(dead).to_owned();
    }
    let text = xkb::keysym_to_utf8(sym);
    if text.is_empty() || text.chars().any(char::is_control) {
        String::new()
    } else {
        text
    }
}

fn dead_glyph(name: &str) -> &'static str {
    match name {
        "grave" => "`",
        "acute" => "´",
        "circumflex" => "^",
        "tilde" | "perispomeni" => "~",
        "macron" => "¯",
        "breve" => "˘",
        "abovedot" => "˙",
        "diaeresis" => "¨",
        "abovering" => "˚",
        "doubleacute" => "˝",
        "caron" => "ˇ",
        "cedilla" => "¸",
        "ogonek" => "˛",
        "iota" => "ͺ",
        "belowdot" => ".",
        "stroke" => "/",
        "currency" => "¤",
        "greek" => "α",
        _ => "◌",
    }
}

/// The xkb key code of an evdev code.
fn kc(code: u32) -> xkb::Keycode {
    xkb::Keycode::new(code + 8)
}

/// One layout of a keymap (group `group`) as the keyboard panel shows it:
/// each character key's symbols plain, with Shift (the left Shift key
/// pressed) and with AltGr (the right Alt key pressed, where it is
/// ISO_Level3_Shift in this layout), read from xkb's own state; the board
/// ISO when this layout has the key left of Z and it types a symbol no other
/// key types plainly or shifted (Hungarian í, German <), else ANSI (US: its
/// < is Shift + comma).
pub fn group_keys(keymap: &xkb::Keymap, group: u32, entry: crate::kbdlayout::Entry) -> GroupKeys {
    let state_with = |pressed: Option<u32>| {
        let mut state = xkb::State::new(keymap);
        state.update_mask(0, 0, 0, 0, 0, group);
        if let Some(code) = pressed {
            state.update_key(kc(code), xkb::KeyDirection::Down);
        }
        state
    };
    let plain = state_with(None);
    let shifted = state_with(Some(LEFTSHIFT));
    let altgr = keymap.key_get_syms_by_level(kc(RIGHTALT), group, 0).contains(&xkb::Keysym::ISO_Level3_Shift);
    let third = altgr.then(|| state_with(Some(RIGHTALT)));
    let mut caps = HashMap::new();
    for code in char_codes() {
        let base = glyph(plain.key_get_one_sym(kc(code)));
        let shift = glyph(shifted.key_get_one_sym(kc(code)));
        let mut alt = third.as_ref().map(|s| glyph(s.key_get_one_sym(kc(code)))).unwrap_or_default();
        if alt == base || alt == shift {
            alt.clear();
        }
        let shift = if shift == base { String::new() } else { shift };
        caps.insert(code, Cap { base, shift, altgr: alt });
    }
    // ISO: this layout has the key left of Z (not borrowed from the first
    // layout, as xkb does for a key a later layout leaves out: `hu,us`'s US
    // would type í there) and it types something of its own.
    let lsgt = plain.key_get_one_sym(kc(KEY_102ND));
    let iso = group < keymap.num_layouts_for_key(kc(KEY_102ND))
        && lsgt != xkb::Keysym::NoSymbol
        && !char_codes()
            .filter(|&c| c != KEY_102ND)
            .any(|c| plain.key_get_one_sym(kc(c)) == lsgt || shifted.key_get_one_sym(kc(c)) == lsgt);
    GroupKeys { entry, form: Form { iso }, altgr, caps }
}

/// Every layout of the seat's keymap as the keyboard panel shows it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Board {
    /// By group.
    pub groups: Vec<GroupKeys>,
}

impl Board {
    /// The board of the keymap these names compile to (the seat's own:
    /// [`crate::keymapsync::seat_xkb_config`]'s names), `entries` naming
    /// its groups. None when it does not compile.
    pub fn compile(names: &crate::config::Xkb, entries: &[crate::kbdlayout::Entry]) -> Option<Board> {
        let context = xkb::Context::new(xkb::CONTEXT_NO_FLAGS);
        let options = (!names.options.is_empty()).then(|| names.options.clone());
        let keymap = xkb::Keymap::new_from_names(
            &context,
            "",
            &names.model,
            &names.layout,
            &names.variant,
            options,
            xkb::KEYMAP_COMPILE_NO_FLAGS,
        )?;
        let groups = (0..keymap.num_layouts())
            .map(|g| group_keys(&keymap, g, entries.get(g as usize).cloned().unwrap_or_default()))
            .collect();
        Some(Board { groups })
    }

    /// Layout `group`'s keys (the first's for a group past the end).
    pub fn group(&self, group: u32) -> Option<&GroupKeys> {
        self.groups.get(group as usize).or_else(|| self.groups.first())
    }
}

/// A key where it is on the panel (panel-local logical px).
#[derive(Debug, Clone, PartialEq)]
pub struct Key {
    /// What it is.
    pub def: KeyDef,
    /// Its cell: where a finger hits it (the gaps belong to the keys).
    pub cell: Rect,
    /// Its cap: what is drawn ([`GAP`] inside the cell).
    pub cap: Rect,
}

/// The keyboard panel's picture and touch areas.
#[derive(Debug, Clone, PartialEq)]
pub struct Layout {
    /// The panel's size it was made for (logical px, as it reads).
    pub size: (f64, f64),
    /// The board it was made for.
    pub form: Form,
    /// The keys, row by row.
    pub keys: Vec<Key>,
    /// The keys' area (their cells' bounds).
    pub board: Rect,
    /// A row's height.
    pub row_h: f64,
    /// The touchpad.
    pub touchpad: Rect,
}

/// Room around the keys and the touchpad (logical px).
pub const MARGIN: f64 = 6.0;
/// Between two key caps.
pub const GAP: f64 = 5.0;
/// Between the keys and the touchpad.
pub const SPACING: f64 = 14.0;

/// The keyboard panel laid out for a panel of `w` x `h` logical px as it
/// reads: landscape (wider than tall) puts the keys at the top (along the
/// hinge) and the touchpad below, centred; portrait the keys at the bottom
/// and the touchpad above, across.
pub fn layout(w: f64, h: f64, form: Form) -> Layout {
    let landscape = w >= h;
    let unit = (w - 2.0 * MARGIN) / ROW_UNITS;
    let rows_n = ROW_COUNT as f64;
    let row_h = if landscape {
        ((h * 0.55 - 2.0 * MARGIN) / rows_n).clamp(30.0, 56.0)
    } else {
        ((h * 0.40 - 2.0 * MARGIN) / rows_n).clamp(30.0, 52.0)
    };
    let board_h = row_h * rows_n;
    let board_y = if landscape { MARGIN } else { h - MARGIN - board_h };
    let board = Rect::new(MARGIN, board_y, unit * ROW_UNITS, board_h);
    let mut keys = Vec::new();
    for (r, row) in rows(form).into_iter().enumerate() {
        let y = board_y + r as f64 * row_h;
        let mut x = MARGIN;
        let mut last_w = 0.0;
        for def in row {
            // A key of zero width shares the cell before it (the down arrow
            // under the up arrow).
            let (cx, cw) = if def.units == 0.0 { (x - last_w, last_w) } else { (x, def.units * unit) };
            let cell = match def.part {
                Part::Whole => Rect::new(cx, y, cw, row_h),
                Part::Tall => Rect::new(cx, y, cw, 2.0 * row_h),
                Part::Top => Rect::new(cx, y, cw, row_h / 2.0),
                Part::Bottom => Rect::new(cx, y + row_h / 2.0, cw, row_h / 2.0),
            };
            let half = GAP / 2.0;
            let cap = match def.part {
                Part::Whole | Part::Tall => cell.inset(half),
                Part::Top => Rect::new(cell.x + half, cell.y + half, cell.w - GAP, cell.h - half - 1.0),
                Part::Bottom => Rect::new(cell.x + half, cell.y + 1.0, cell.w - GAP, cell.h - half - 1.0),
            };
            if def.units > 0.0 {
                x += cw;
                last_w = cw;
            }
            keys.push(Key { def, cell, cap });
        }
    }
    let touchpad = if landscape {
        let top = board_y + board_h + SPACING;
        let tw = (w * 0.52).min(w - 2.0 * MARGIN);
        Rect::new((w - tw) / 2.0, top, tw, (h - MARGIN - 4.0 - top).max(0.0))
    } else {
        let bottom = board_y - SPACING;
        Rect::new(MARGIN + 2.0, MARGIN + 2.0, w - 2.0 * MARGIN - 4.0, (bottom - MARGIN - 2.0).max(0.0))
    };
    Layout { size: (w, h), form, keys, board, row_h, touchpad }
}

impl Layout {
    /// The key a finger at `(x, y)` (panel-local) is on: the cell it is in.
    pub fn key_at(&self, x: f64, y: f64) -> Option<usize> {
        if !self.board.contains(x, y) {
            return None;
        }
        self.keys.iter().position(|k| k.cell.contains(x, y)).or_else(|| {
            // Rounding at a row's end: the nearest cell.
            self.keys
                .iter()
                .enumerate()
                .min_by(|(_, a), (_, b)| {
                    dist2(a.cell, x, y).partial_cmp(&dist2(b.cell, x, y)).unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|(i, _)| i)
        })
    }

    /// Whether `(x, y)` is on the touchpad.
    pub fn on_touchpad(&self, x: f64, y: f64) -> bool {
        self.touchpad.contains(x, y)
    }

    /// The key with this id.
    pub fn index_of(&self, id: &str) -> Option<usize> {
        self.keys.iter().position(|k| k.def.id == id)
    }
}

fn dist2(r: Rect, x: f64, y: f64) -> f64 {
    let dx = (r.x - x).max(0.0).max(x - (r.x + r.w));
    let dy = (r.y - y).max(0.0).max(y - (r.y + r.h));
    dx * dx + dy * dy
}

/// The layouts the list offers besides the configured ones, in this
/// order: US English first, then the rest by their names in their own
/// language (Deutsch, English (UK), Español, Français, Italiano, Magyar,
/// Polski).
pub const COMMON_LAYOUTS: [&str; 8] = ["us", "de", "gb", "es", "fr", "it", "hu", "pl"];

/// One entry of the list of layouts.
#[derive(Debug, Clone, PartialEq)]
pub struct MenuItem {
    /// The layout.
    pub entry: crate::kbdlayout::Entry,
    /// In the seat's list already.
    pub configured: bool,
    /// The one in force.
    pub active: bool,
    /// Where a finger picks it.
    pub cell: Rect,
    /// What is drawn.
    pub cap: Rect,
}

/// The list of layouts the layout key brings up when held: the configured
/// ones (the one in force marked), then [`COMMON_LAYOUTS`] not among them
/// (picked: added to the list) while there is room for one more.
#[derive(Debug, Clone, PartialEq)]
pub struct Menu {
    /// Its panel (over the keys, above the bottom row).
    pub rect: Rect,
    /// The layouts.
    pub items: Vec<MenuItem>,
}

/// Room inside the list's panel around its entries.
const MENU_PAD: f64 = 6.0;

/// The list of layouts over this keyboard.
pub fn menu(layout: &Layout, configured: &[crate::kbdlayout::Entry], active: u32) -> Menu {
    let mut entries: Vec<(crate::kbdlayout::Entry, bool)> =
        configured.iter().map(|e| (e.clone(), true)).collect();
    if configured.len() < crate::kbdlayout::MAX_LAYOUTS {
        for name in COMMON_LAYOUTS {
            if !configured.iter().any(|e| e.layout == name) {
                entries.push((crate::kbdlayout::Entry::new(name, ""), false));
            }
        }
    }
    let cols = entries.len().clamp(1, 4);
    let rows = entries.len().div_ceil(cols).max(1);
    // Whole rows of keys covered (a row half under it would show caps
    // without labels), the entries inside.
    let h = rows as f64 * layout.row_h;
    let item_w = (layout.board.w - 2.0 * MENU_PAD) / 4.0;
    let item_h = (h - 2.0 * MENU_PAD) / rows as f64;
    let w = cols as f64 * item_w + 2.0 * MENU_PAD;
    // Above the bottom row (the layout key's), centred on the keys.
    let bottom = layout.board.y + layout.board.h - layout.row_h;
    let rect = Rect::new(layout.board.x + (layout.board.w - w) / 2.0, (bottom - h).max(layout.board.y), w, h);
    let items = entries
        .into_iter()
        .enumerate()
        .map(|(i, (entry, configured))| {
            let cell = Rect::new(
                rect.x + MENU_PAD + (i % cols) as f64 * item_w,
                rect.y + MENU_PAD + (i / cols) as f64 * item_h,
                item_w,
                item_h,
            );
            MenuItem {
                entry,
                configured,
                active: configured && i as u32 == active,
                cell,
                cap: cell.inset(GAP / 2.0),
            }
        })
        .collect();
    Menu { rect, items }
}

impl Menu {
    /// The entry a finger at `(x, y)` is on.
    pub fn item_at(&self, x: f64, y: f64) -> Option<usize> {
        self.items.iter().position(|i| i.cell.contains(x, y))
    }
}

/// A key event to send to the seat (evdev code).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ev {
    /// Pressed.
    Press(u32),
    /// Released.
    Release(u32),
}

/// One modifier kind's state on the touch keyboard.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ModState {
    /// The key pressed in the seat for it (the first of its keys touched).
    seat_code: u32,
    /// Fingers on its keys now.
    fingers: usize,
    /// Another key was pressed while a finger held it.
    used: bool,
    /// It was latched when a finger came down on it: that tap lets it go.
    unlatch: bool,
    /// Latched: down in the seat with no finger on it, for the next key.
    latched: bool,
}

/// The modifier keys of a touch keyboard.
///
/// A modifier held while another key is pressed works as on a keyboard. A
/// modifier tapped on its own (down and up, nothing else meanwhile) stays
/// down, latched, for the next key: after that key is let go, it is let go
/// too. A latched modifier tapped again is let go. Both keys of a kind
/// (two Shifts) press it once.
#[derive(Debug, Default, Clone)]
pub struct Mods {
    mods: HashMap<ModKind, ModState>,
}

impl Mods {
    /// A modifier key goes down.
    pub fn mod_down(&mut self, kind: ModKind, code: u32) -> Vec<Ev> {
        if let Some(s) = self.mods.get_mut(&kind) {
            // Down in the seat already (held, or latched).
            s.fingers += 1;
            if s.latched && s.fingers == 1 {
                s.unlatch = true;
            }
            return Vec::new();
        }
        self.mods.insert(
            kind,
            ModState { seat_code: code, fingers: 1, used: false, unlatch: false, latched: false },
        );
        vec![Ev::Press(code)]
    }

    /// A modifier key comes up.
    pub fn mod_up(&mut self, kind: ModKind, _code: u32) -> Vec<Ev> {
        let Some(s) = self.mods.get_mut(&kind) else {
            return Vec::new();
        };
        if s.fingers == 0 {
            return Vec::new();
        }
        s.fingers -= 1;
        if s.fingers > 0 {
            // Its other key is still held.
            return Vec::new();
        }
        if s.unlatch || s.used || s.latched {
            let code = s.seat_code;
            self.mods.remove(&kind);
            return vec![Ev::Release(code)];
        }
        // Tapped alone: it stays down for the next key.
        s.latched = true;
        Vec::new()
    }

    /// Any other key goes down.
    pub fn key_down(&mut self, code: u32) -> Vec<Ev> {
        for s in self.mods.values_mut().filter(|s| s.fingers > 0) {
            s.used = true;
            s.unlatch = false;
        }
        vec![Ev::Press(code)]
    }

    /// Any other key comes up: the latched modifiers it was typed with are
    /// let go after it.
    pub fn key_up(&mut self, code: u32) -> Vec<Ev> {
        let mut out = vec![Ev::Release(code)];
        let mut gone: Vec<ModKind> =
            self.mods.iter().filter(|(_, s)| s.latched && s.fingers == 0).map(|(k, _)| *k).collect();
        gone.sort();
        for kind in gone {
            if let Some(s) = self.mods.remove(&kind) {
                out.push(Ev::Release(s.seat_code));
            }
        }
        out
    }

    /// Every modifier let go (laptop mode ends, the touches are cancelled).
    pub fn release_all(&mut self) -> Vec<Ev> {
        let mut codes: Vec<u32> = self.mods.values().map(|s| s.seat_code).collect();
        codes.sort_unstable();
        self.mods.clear();
        codes.into_iter().map(Ev::Release).collect()
    }

    /// Whether a modifier is in force (held or latched).
    pub fn active(&self, kind: ModKind) -> bool {
        self.mods.contains_key(&kind)
    }

    /// The latched modifiers, sorted.
    pub fn latched(&self) -> Vec<ModKind> {
        let mut out: Vec<ModKind> = self.mods.iter().filter(|(_, s)| s.latched).map(|(k, _)| *k).collect();
        out.sort();
        out
    }
}

/// How much faster than the finger the pointer goes at a finger speed of
/// `speed` logical px per ms: slow is precise, fast crosses the desk.
pub fn accel(speed: f64) -> f64 {
    (0.75 + 0.9 * speed).clamp(0.75, 2.6)
}

/// The pointer's base gain on the internal desk (pointer px per finger
/// px, before [`accel`]).
pub const GAIN: f64 = 1.5;

/// A key as the shell draws its label.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct KeySummary {
    /// Its id.
    pub id: &'static str,
    /// Its cap (panel-local logical px).
    pub x: f64,
    /// Top.
    pub y: f64,
    /// Width.
    pub w: f64,
    /// Height.
    pub h: f64,
    /// Its label.
    pub label: String,
    /// Its label shifted (empty: the same, or none).
    pub shift: String,
    /// What it types with AltGr (drawn small; empty: nothing else).
    pub altgr: String,
    /// `key`, `mod`, `exit` or `layout`.
    pub kind: &'static str,
}

/// One entry of the list of layouts as the shell draws its name.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MenuItemSummary {
    /// The layout (`hu`): the shell names it.
    pub layout: String,
    /// Its variant.
    pub variant: String,
    /// In the list already.
    pub configured: bool,
    /// The one in force.
    pub active: bool,
    /// Its cap.
    pub x: f64,
    /// Top.
    pub y: f64,
    /// Width.
    pub w: f64,
    /// Height.
    pub h: f64,
}

/// The list of layouts as the shell gets it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MenuSummary {
    /// Its panel: x, y, w, h.
    pub rect: [f64; 4],
    /// The layouts.
    pub items: Vec<MenuItemSummary>,
}

fn r1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

impl MenuSummary {
    /// From a list of layouts.
    pub fn of(menu: &Menu) -> Self {
        MenuSummary {
            rect: [r1(menu.rect.x), r1(menu.rect.y), r1(menu.rect.w), r1(menu.rect.h)],
            items: menu
                .items
                .iter()
                .map(|i| MenuItemSummary {
                    layout: i.entry.layout.clone(),
                    variant: i.entry.variant.clone(),
                    configured: i.configured,
                    active: i.active,
                    x: r1(i.cap.x),
                    y: r1(i.cap.y),
                    w: r1(i.cap.w),
                    h: r1(i.cap.h),
                })
                .collect(),
        }
    }
}

/// The keyboard panel's layout as the shell gets it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LayoutSummary {
    /// The panel's size it is for.
    pub w: f64,
    /// Height.
    pub h: f64,
    /// The layout in force (`hu`), whose symbols the keys show.
    pub group: String,
    /// Its board is ISO.
    pub iso: bool,
    /// The keys.
    pub keys: Vec<KeySummary>,
    /// The touchpad: x, y, w, h.
    pub touchpad: [f64; 4],
}

/// The label a key shows in layout `group` (a character key's symbols,
/// the right Alt key's name, the layout key's layout).
pub fn labels_of(def: &KeyDef, group: Option<&GroupKeys>) -> (String, String, String) {
    match def.kind {
        Kind::Char => {
            let cap = group.and_then(|g| g.caps.get(&def.code)).cloned().unwrap_or_default();
            (cap.base, cap.shift, cap.altgr)
        }
        Kind::Layout => (group.map(|g| g.entry.short()).unwrap_or_default(), String::new(), String::new()),
        Kind::Mod(ModKind::AltGr) if group.is_some_and(|g| g.altgr) => {
            ("AltGr".into(), String::new(), String::new())
        }
        _ => (def.label.to_owned(), String::new(), String::new()),
    }
}

impl LayoutSummary {
    /// From a layout, with the symbols of layout `group`.
    pub fn of(layout: &Layout, group: Option<&GroupKeys>) -> Self {
        LayoutSummary {
            w: r1(layout.size.0),
            h: r1(layout.size.1),
            group: group.map(|g| g.entry.layout.clone()).unwrap_or_default(),
            iso: layout.form.iso,
            keys: layout
                .keys
                .iter()
                .map(|k| {
                    let (label, shift, altgr) = labels_of(&k.def, group);
                    KeySummary {
                        id: k.def.id,
                        x: r1(k.cap.x),
                        y: r1(k.cap.y),
                        w: r1(k.cap.w),
                        h: r1(k.cap.h),
                        label,
                        shift,
                        altgr,
                        kind: match k.def.kind {
                            Kind::Mod(_) => "mod",
                            Kind::Exit => "exit",
                            Kind::Layout => "layout",
                            Kind::Char | Kind::Key => "key",
                        },
                    }
                })
                .collect(),
            touchpad: [
                r1(layout.touchpad.x),
                r1(layout.touchpad.y),
                r1(layout.touchpad.w),
                r1(layout.touchpad.h),
            ],
        }
    }
}

/// Laptop mode as the event stream tells it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Summary {
    /// Laptop mode is on.
    pub on: bool,
    /// Who turned it on (`manual`, `auto`), while it is.
    pub origin: Option<Origin>,
    /// The setting: the laptop posture turns it on (`laptop_auto`).
    pub auto: bool,
    /// The setting: the keyboard panel when the posture does not say.
    pub keys_pref: String,
    /// The desk panel, while on.
    pub desk: Option<String>,
    /// The keyboard panel, while on.
    pub keys: Option<String>,
    /// The keyboard panel's layout, while on.
    pub layout: Option<LayoutSummary>,
    /// Shift is in force (held or latched).
    pub shift: bool,
    /// Caps Lock is on.
    pub caps: bool,
    /// AltGr is in force (the keys show their third symbol).
    pub altgr: bool,
    /// The latched modifiers.
    pub latched: Vec<ModKind>,
    /// The list of layouts, while it is up.
    pub menu: Option<MenuSummary>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kbdlayout::Entry;
    use std::collections::HashSet;

    #[test]
    fn every_row_is_fifteen_units() {
        for iso in [false, true] {
            let rows = rows(Form { iso });
            let mut carried = 0.0;
            for row in &rows {
                let units: f64 = row.iter().map(|k| k.units).sum::<f64>() + carried;
                assert!((units - ROW_UNITS).abs() < 1e-9, "iso {iso}, {:?}: {units}", row[0].id);
                carried = row.iter().filter(|k| k.part == Part::Tall).map(|k| k.units).sum();
            }
            assert_eq!(rows.len(), ROW_COUNT);
        }
    }

    #[test]
    fn ids_are_unique_and_codes_are_set() {
        for iso in [false, true] {
            let mut ids = HashSet::new();
            for row in rows(Form { iso }) {
                for k in row {
                    assert!(ids.insert(k.id), "{} twice", k.id);
                    match k.kind {
                        Kind::Exit | Kind::Layout => assert_eq!(k.code, 0),
                        _ => assert!(k.code > 0, "{}", k.id),
                    }
                }
            }
            assert_eq!(ids.contains("lsgt"), iso);
        }
        assert_eq!(char_codes().count(), 48);
    }

    #[test]
    fn the_keyboard_is_the_lower_panel_in_landscape() {
        assert_eq!(lower_panel((90, 90)), Some("DSI-2"));
        assert_eq!(lower_panel((270, 270)), Some("DSI-1"));
        assert_eq!(lower_panel((0, 0)), None);
        // Half way through duo-posed's pair of requests: not yet.
        assert_eq!(lower_panel((90, 0)), None);
        assert_eq!(panels("DSI-2", (270, 270)), ("DSI-2".into(), "DSI-1".into()));
        assert_eq!(panels("DSI-1", (90, 90)), ("DSI-1".into(), "DSI-2".into()));
        // Portrait: the user's choice.
        assert_eq!(panels("DSI-1", (0, 0)), ("DSI-2".into(), "DSI-1".into()));
        assert_eq!(panels("bogus", (0, 0)), ("DSI-1".into(), "DSI-2".into()));
    }

    #[test]
    fn the_posture_turns_it_on_and_off_only_when_it_may() {
        let mut auto = Auto::default();
        // Off in the settings: nothing.
        assert_eq!(auto.step(false, true, None), AutoStep::Nothing);
        // On: the posture turns it on, leaving it turns it off.
        assert_eq!(auto.step(true, true, None), AutoStep::TurnOn);
        assert_eq!(auto.step(true, true, Some(Origin::Auto)), AutoStep::Nothing);
        assert_eq!(auto.step(true, false, Some(Origin::Auto)), AutoStep::TurnOff);
        // Turned on by hand: the posture never turns it off.
        assert_eq!(auto.step(true, false, Some(Origin::Manual)), AutoStep::Nothing);
        // Turned off by hand in the posture: off until the posture is left.
        auto.user_off(true);
        assert_eq!(auto.step(true, true, None), AutoStep::Nothing);
        assert_eq!(auto.step(true, false, None), AutoStep::Nothing);
        assert_eq!(auto.step(true, true, None), AutoStep::TurnOn);
        // Turned off by hand elsewhere: nothing to remember.
        let mut auto = Auto::default();
        auto.user_off(false);
        assert_eq!(auto.step(true, true, None), AutoStep::TurnOn);
    }

    #[test]
    fn config_keys() {
        let mut c = Config::default();
        assert!(!c.auto);
        assert_eq!(c.keys, "DSI-2");
        c.set("laptop_auto", "on").unwrap();
        c.set("laptop_keys", "DSI-1").unwrap();
        assert_eq!(c, Config { auto: true, keys: "DSI-1".into() });
        assert!(c.set("laptop_auto", "yes").is_err());
        assert!(c.set("laptop_keys", "DP-1").is_err());
    }

    #[test]
    fn the_live_settings_are_kept_in_the_state_file() {
        let c = Config { auto: true, keys: "DSI-1".into() };
        assert_eq!(c.state_text(), "auto on\nkeys DSI-1\n");
        assert_eq!(Config::default().with_state(&c.state_text()), c);
        // Junk and unknown lines change nothing.
        assert_eq!(Config::default().with_state("keys DP-1\nfoo bar\n\nauto maybe\n"), Config::default());
        // Missing lines keep the config file's.
        let file = Config { auto: true, keys: "DSI-2".into() };
        assert_eq!(file.clone().with_state("keys DSI-1\n"), Config { auto: true, keys: "DSI-1".into() });
    }

    fn check_layout(l: &Layout, w: f64, h: f64) {
        for k in &l.keys {
            assert!(k.cap.w > 0.0 && k.cap.h > 0.0, "{}", k.def.id);
            assert!(k.cap.x >= k.cell.x && k.cap.x + k.cap.w <= k.cell.x + k.cell.w + 1e-9, "{}", k.def.id);
            assert!(k.cell.x >= 0.0 && k.cell.x + k.cell.w <= w + 1e-6, "{}", k.def.id);
            assert!(k.cell.y >= 0.0 && k.cell.y + k.cell.h <= h + 1e-6, "{}", k.def.id);
        }
        // No two cells overlap (the down arrow is under the up arrow).
        for (i, a) in l.keys.iter().enumerate() {
            for b in &l.keys[i + 1..] {
                let ox = (a.cell.x + a.cell.w).min(b.cell.x + b.cell.w) - a.cell.x.max(b.cell.x);
                let oy = (a.cell.y + a.cell.h).min(b.cell.y + b.cell.h) - a.cell.y.max(b.cell.y);
                assert!(ox <= 1e-6 || oy <= 1e-6, "{} and {} overlap", a.def.id, b.def.id);
            }
        }
    }

    #[test]
    fn landscape_has_the_keys_along_the_hinge_and_the_touchpad_below() {
        for iso in [false, true] {
            // A panel turned to landscape at scale 2.5.
            let l = layout(757.0, 538.0, Form { iso });
            assert_eq!(l.keys.len(), rows(Form { iso }).iter().map(|r| r.len()).sum::<usize>());
            assert!((l.board.y - MARGIN).abs() < 1e-9);
            assert!(l.touchpad.y >= l.board.y + l.board.h + SPACING - 1e-9);
            assert!(l.touchpad.y + l.touchpad.h <= 538.0);
            assert!(l.touchpad.h > 150.0 && l.touchpad.w > 300.0, "{:?}", l.touchpad);
            // Centred.
            assert!(((l.touchpad.x + l.touchpad.w / 2.0) - 757.0 / 2.0).abs() < 1e-6);
            check_layout(&l, 757.0, 538.0);
            // A letter about as wide as tall.
            let q = &l.keys[l.index_of("ad01").unwrap()];
            assert!((q.cell.w / q.cell.h - 1.0).abs() < 0.2, "{:?}", q.cell);
            // The F keys stay big enough for a finger.
            let f1 = &l.keys[l.index_of("f1").unwrap()];
            assert!(f1.cap.w >= 30.0, "{:?}", f1.cap);
        }
    }

    #[test]
    fn portrait_has_the_keys_at_the_bottom() {
        for iso in [false, true] {
            let l = layout(538.0, 757.0, Form { iso });
            assert!((l.board.y + l.board.h - (757.0 - MARGIN)).abs() < 1e-9);
            assert!(l.touchpad.y + l.touchpad.h <= l.board.y - SPACING + 1e-9);
            assert!(l.touchpad.h > 300.0);
            check_layout(&l, 538.0, 757.0);
            let a = &l.keys[l.index_of("ac01").unwrap()];
            assert!(a.cap.w >= 28.0 && a.cap.h >= 28.0, "{:?}", a.cap);
        }
    }

    #[test]
    fn the_iso_board_has_the_key_left_of_z_and_a_tall_enter() {
        let l = layout(757.0, 538.0, Form { iso: true });
        let enter = &l.keys[l.index_of("enter").unwrap()];
        let bksl = &l.keys[l.index_of("bksl").unwrap()];
        let ad12 = &l.keys[l.index_of("ad12").unwrap()];
        let ac11 = &l.keys[l.index_of("ac11").unwrap()];
        // Enter: two rows, at the right end of both letter rows.
        assert!((enter.cell.h - 2.0 * l.row_h).abs() < 1e-9);
        assert!((enter.cell.x + enter.cell.w - (l.board.x + l.board.w)).abs() < 1e-6);
        assert!((enter.cell.y - ad12.cell.y).abs() < 1e-9);
        // The ISO home row: ... ac11, the key that was backslash, Enter.
        assert!((bksl.cell.y - ac11.cell.y).abs() < 1e-9);
        assert!((bksl.cell.x - (ac11.cell.x + ac11.cell.w)).abs() < 1e-6);
        assert!((bksl.cell.x + bksl.cell.w - enter.cell.x).abs() < 1e-6);
        // Left of Z.
        let lsgt = &l.keys[l.index_of("lsgt").unwrap()];
        let z = &l.keys[l.index_of("ab01").unwrap()];
        assert!((lsgt.cell.x + lsgt.cell.w - z.cell.x).abs() < 1e-6);
        // A finger on Enter's lower half is on Enter.
        let (ex, _) = enter.cell.center();
        assert_eq!(l.key_at(ex, enter.cell.y + enter.cell.h * 0.75).map(|i| l.keys[i].def.id), Some("enter"));
        // ANSI: backslash above a wide Enter, no key left of Z.
        let l = layout(757.0, 538.0, Form { iso: false });
        assert!(l.index_of("lsgt").is_none());
        let enter = &l.keys[l.index_of("enter").unwrap()];
        let bksl = &l.keys[l.index_of("bksl").unwrap()];
        assert!(bksl.cell.y < enter.cell.y);
        assert!((enter.cell.h - l.row_h).abs() < 1e-9);
    }

    #[test]
    fn a_finger_hits_the_key_whose_cell_it_is_in() {
        let l = layout(757.0, 538.0, Form::default());
        let at = |id: &str| l.keys[l.index_of(id).unwrap()].cap.center();
        for id in ["ad01", "space", "enter", "shift_l", "f5", "up", "down", "left", "exit", "layout", "alt_r"]
        {
            let (x, y) = at(id);
            assert_eq!(l.key_at(x, y).map(|i| l.keys[i].def.id), Some(id));
        }
        // The gap between q and w belongs to one of them.
        let q = &l.keys[l.index_of("ad01").unwrap()];
        let gap_x = q.cap.x + q.cap.w + GAP / 4.0;
        let hit = l.key_at(gap_x, q.cap.center().1).map(|i| l.keys[i].def.id);
        assert!(hit == Some("ad01") || hit == Some("ad02"), "{hit:?}");
        // Up and down share one cell, one above the other.
        let up = &l.keys[l.index_of("up").unwrap()];
        let down = &l.keys[l.index_of("down").unwrap()];
        assert_eq!((up.cell.x, up.cell.w), (down.cell.x, down.cell.w));
        assert!(down.cell.y >= up.cell.y + up.cell.h - 1e-9);
        // The touchpad is no key.
        let (tx, ty) = l.touchpad.center();
        assert_eq!(l.key_at(tx, ty), None);
        assert!(l.on_touchpad(tx, ty));
        // Nor is the palm rest beside it.
        assert_eq!(l.key_at(3.0, 530.0), None);
        assert!(!l.on_touchpad(3.0, 530.0));
    }

    #[test]
    fn a_held_modifier_works_with_the_next_key() {
        let mut m = Mods::default();
        assert_eq!(m.mod_down(ModKind::Ctrl, LEFTCTRL), vec![Ev::Press(LEFTCTRL)]);
        assert!(m.active(ModKind::Ctrl));
        assert_eq!(m.key_down(C), vec![Ev::Press(C)]);
        assert_eq!(m.key_up(C), vec![Ev::Release(C)]);
        // Used: it goes up with the finger.
        assert_eq!(m.mod_up(ModKind::Ctrl, LEFTCTRL), vec![Ev::Release(LEFTCTRL)]);
        assert!(!m.active(ModKind::Ctrl));
    }

    #[test]
    fn a_tapped_modifier_is_latched_for_one_key() {
        let mut m = Mods::default();
        assert_eq!(m.mod_down(ModKind::Shift, LEFTSHIFT), vec![Ev::Press(LEFTSHIFT)]);
        // Tapped alone: stays down.
        assert!(m.mod_up(ModKind::Shift, LEFTSHIFT).is_empty());
        assert_eq!(m.latched(), vec![ModKind::Shift]);
        assert!(m.active(ModKind::Shift));
        // The next key, then Shift lets go.
        assert_eq!(m.key_down(A), vec![Ev::Press(A)]);
        assert_eq!(m.key_up(A), vec![Ev::Release(A), Ev::Release(LEFTSHIFT)]);
        assert!(!m.active(ModKind::Shift));
        // The next one is lower case again.
        assert_eq!(m.key_down(B), vec![Ev::Press(B)]);
        assert_eq!(m.key_up(B), vec![Ev::Release(B)]);
    }

    #[test]
    fn a_latched_modifier_tapped_again_lets_go() {
        let mut m = Mods::default();
        m.mod_down(ModKind::Alt, LEFTALT);
        m.mod_up(ModKind::Alt, LEFTALT);
        assert!(m.active(ModKind::Alt));
        // The tap: nothing on the way down, the release on the way up.
        assert!(m.mod_down(ModKind::Alt, LEFTALT).is_empty());
        assert_eq!(m.mod_up(ModKind::Alt, LEFTALT), vec![Ev::Release(LEFTALT)]);
        assert!(!m.active(ModKind::Alt));
        assert!(m.release_all().is_empty());
        // AltGr is a kind of its own: latched next to a latched Alt.
        m.mod_down(ModKind::Alt, LEFTALT);
        m.mod_up(ModKind::Alt, LEFTALT);
        assert_eq!(m.mod_down(ModKind::AltGr, RIGHTALT), vec![Ev::Press(RIGHTALT)]);
        m.mod_up(ModKind::AltGr, RIGHTALT);
        assert_eq!(m.latched(), vec![ModKind::Alt, ModKind::AltGr]);
        assert_eq!(m.release_all(), vec![Ev::Release(LEFTALT), Ev::Release(RIGHTALT)]);
    }

    #[test]
    fn two_latched_modifiers_go_together_and_a_held_one_stays() {
        let mut m = Mods::default();
        m.mod_down(ModKind::Ctrl, LEFTCTRL);
        m.mod_up(ModKind::Ctrl, LEFTCTRL);
        m.mod_down(ModKind::Shift, LEFTSHIFT);
        m.mod_up(ModKind::Shift, LEFTSHIFT);
        assert_eq!(m.latched(), vec![ModKind::Shift, ModKind::Ctrl]);
        m.key_down(T);
        assert_eq!(m.key_up(T), vec![Ev::Release(T), Ev::Release(LEFTSHIFT), Ev::Release(LEFTCTRL)]);
        // Held across two keys: still down after both.
        m.mod_down(ModKind::Super, LEFTMETA);
        m.key_down(LEFT);
        assert_eq!(m.key_up(LEFT), vec![Ev::Release(LEFT)]);
        m.key_down(RIGHT);
        assert_eq!(m.key_up(RIGHT), vec![Ev::Release(RIGHT)]);
        assert!(m.active(ModKind::Super));
        assert_eq!(m.release_all(), vec![Ev::Release(LEFTMETA)]);
    }

    #[test]
    fn both_shift_keys_press_shift_once() {
        let mut m = Mods::default();
        assert_eq!(m.mod_down(ModKind::Shift, LEFTSHIFT), vec![Ev::Press(LEFTSHIFT)]);
        assert!(m.mod_down(ModKind::Shift, RIGHTSHIFT).is_empty());
        // The first goes up, the second holds it.
        assert!(m.mod_up(ModKind::Shift, LEFTSHIFT).is_empty());
        assert!(m.active(ModKind::Shift));
        assert_eq!(m.release_all(), vec![Ev::Release(LEFTSHIFT)]);
    }

    #[test]
    fn the_pointer_speeds_up_with_the_finger() {
        assert_eq!(accel(0.0), 0.75);
        assert!(accel(1.0) > accel(0.3));
        assert_eq!(accel(100.0), 2.6);
    }

    /// The board of a real keymap (xkeyboard-config's).
    fn board(layouts: &str) -> Board {
        let list = crate::kbdlayout::parse(layouts, "").unwrap();
        let names = crate::kbdlayout::names(&crate::config::Xkb::default(), &list);
        Board::compile(&names, &list).expect("the keymap compiles")
    }

    fn cap(g: &GroupKeys, code: u32) -> (&str, &str, &str) {
        let c = &g.caps[&code];
        (c.base.as_str(), c.shift.as_str(), c.altgr.as_str())
    }

    #[test]
    fn us_is_an_ansi_qwerty() {
        let b = board("us");
        let us = b.group(0).unwrap();
        assert_eq!(us.entry, Entry::new("us", ""));
        assert!(!us.form.iso, "US has no key of its own left of Z");
        assert!(!us.altgr, "US's right Alt is Alt");
        assert_eq!(cap(us, Q), ("q", "Q", ""));
        assert_eq!(cap(us, Y), ("y", "Y", ""));
        assert_eq!(cap(us, Z), ("z", "Z", ""));
        assert_eq!(cap(us, GRAVE), ("`", "~", ""));
        assert_eq!(cap(us, N2), ("2", "@", ""));
        assert_eq!(cap(us, SEMICOLON), (";", ":", ""));
        assert_eq!(cap(us, BACKSLASH), ("\\", "|", ""));
        let s = LayoutSummary::of(&layout(757.0, 538.0, us.form), Some(us));
        let by_id = |id: &str| s.keys.iter().find(|k| k.id == id).unwrap().clone();
        assert_eq!(by_id("layout").label, "US");
        assert_eq!(by_id("alt_r").label, "Alt");
        assert_eq!(by_id("ad01").label, "q");
        assert_eq!(s.group, "us");
    }

    #[test]
    fn hu_is_the_hungarian_iso_qwertz() {
        let b = board("hu");
        let hu = b.group(0).unwrap();
        assert!(hu.form.iso, "í left of Y");
        assert!(hu.altgr);
        // The digit row: 0 ... 9 ö ü ó.
        assert_eq!(cap(hu, GRAVE).0, "0");
        assert_eq!(cap(hu, N1), ("1", "'", "~"));
        assert_eq!(cap(hu, N9), ("9", ")", "´"));
        assert_eq!(cap(hu, N0), ("ö", "Ö", "˝"), "AltGr + ö is the dead double acute");
        assert_eq!(cap(hu, MINUS).0, "ü");
        assert_eq!(cap(hu, EQUAL).0, "ó");
        // QWERTZ, ő ú right of P.
        assert_eq!(cap(hu, Y).0, "z");
        assert_eq!(cap(hu, Z).0, "y");
        assert_eq!(cap(hu, LEFTBRACE).0, "ő");
        assert_eq!(cap(hu, RIGHTBRACE).0, "ú");
        // é á right of L, ű next to them (the ISO key left of Enter).
        assert_eq!(cap(hu, SEMICOLON).0, "é");
        assert_eq!(cap(hu, APOSTROPHE).0, "á");
        assert_eq!(cap(hu, BACKSLASH).0, "ű");
        assert_eq!(cap(hu, KEY_102ND), ("í", "Í", "<"));
        assert_eq!(cap(hu, V).2, "@");
        assert_eq!(cap(hu, SLASH), ("-", "_", "*"));
        let s = LayoutSummary::of(&layout(757.0, 538.0, hu.form), Some(hu));
        let by_id = |id: &str| s.keys.iter().find(|k| k.id == id).unwrap().clone();
        assert_eq!(by_id("layout").label, "HU");
        assert_eq!(by_id("alt_r").label, "AltGr");
        assert_eq!(by_id("lsgt").label, "í");
        assert!(s.iso);
    }

    #[test]
    fn de_is_the_german_iso_qwertz_with_dead_keys() {
        let b = board("de");
        let de = b.group(0).unwrap();
        assert!(de.form.iso);
        assert!(de.altgr);
        assert_eq!(cap(de, Y).0, "z");
        assert_eq!(cap(de, MINUS).0, "ß");
        assert_eq!(cap(de, LEFTBRACE).0, "ü");
        assert_eq!(cap(de, SEMICOLON).0, "ö");
        assert_eq!(cap(de, APOSTROPHE).0, "ä");
        // Dead keys shown as their accents.
        assert_eq!(cap(de, GRAVE).0, "^");
        assert_eq!(cap(de, EQUAL).0, "´");
        assert_eq!(cap(de, KEY_102ND), ("<", ">", "|"));
        assert_eq!(cap(de, Q).2, "@");
        assert_eq!(cap(de, E).2, "€");
    }

    #[test]
    fn two_layouts_are_two_groups() {
        let b = board("hu,us");
        assert_eq!(b.groups.len(), 2);
        assert_eq!(b.group(0).unwrap().entry.layout, "hu");
        assert!(b.group(0).unwrap().form.iso);
        let us = b.group(1).unwrap();
        assert_eq!(us.entry.layout, "us");
        assert!(!us.form.iso, "the US group borrows hu's key left of Z: still ANSI");
        assert_eq!(cap(us, Y).0, "y");
        // The other way round too.
        // Past the end: the first.
        assert_eq!(b.group(7).unwrap().entry.layout, "hu");
        let b = board("us,hu");
        assert!(!b.group(0).unwrap().form.iso);
        assert!(b.group(1).unwrap().form.iso);
        assert_eq!(cap(b.group(1).unwrap(), KEY_102ND).0, "í");
    }

    #[test]
    fn the_layout_list_offers_the_configured_ones_then_common_ones() {
        let l = layout(757.0, 538.0, Form::default());
        let configured = vec![Entry::new("hu", ""), Entry::new("us", "")];
        let m = menu(&l, &configured, 1);
        let names: Vec<&str> = m.items.iter().map(|i| i.entry.layout.as_str()).collect();
        assert_eq!(names, ["hu", "us", "de", "gb", "es", "fr", "it", "pl"]);
        assert!(m.items[1].active && m.items[1].configured && !m.items[0].active);
        assert!(!m.items[2].configured);
        // Over the keys, above the bottom row, on whole rows, every entry
        // inside it.
        let space = &l.keys[l.index_of("space").unwrap()];
        assert!(m.rect.y + m.rect.h <= space.cell.y + 1e-6);
        let rows_up = (space.cell.y - m.rect.y) / l.row_h;
        assert!((rows_up - rows_up.round()).abs() < 1e-6, "{rows_up}");
        assert!(m.rect.y >= l.board.y - 1e-6);
        for item in &m.items {
            assert!(item.cell.x >= m.rect.x && item.cell.x + item.cell.w <= m.rect.x + m.rect.w + 1e-6);
            assert!(item.cell.y >= m.rect.y && item.cell.y + item.cell.h <= m.rect.y + m.rect.h + 1e-6);
            assert!(item.cap.w > 100.0 && item.cap.h > 30.0);
        }
        let (x, y) = m.items[3].cap.center();
        assert_eq!(m.item_at(x, y), Some(3));
        assert_eq!(m.item_at(m.rect.x + 1.0, m.rect.y + 1.0), None);
        // Full (four layouts): only those.
        let four: Vec<Entry> = ["hu", "us", "de", "fr"].iter().map(|n| Entry::new(n, "")).collect();
        assert_eq!(menu(&l, &four, 0).items.len(), 4);
        // Portrait fits too.
        let p = layout(538.0, 757.0, Form::default());
        let m = menu(&p, &configured, 0);
        assert!(m.rect.y >= p.board.y - 1e-6 && m.rect.x >= 0.0 && m.rect.x + m.rect.w <= 538.0);
    }

    #[test]
    fn glyphs_of_dead_and_odd_symbols() {
        assert_eq!(glyph(xkb::Keysym::NoSymbol), "");
        assert_eq!(glyph(xkb::Keysym::dead_acute), "´");
        assert_eq!(glyph(xkb::Keysym::dead_doubleacute), "˝");
        assert_eq!(glyph(xkb::Keysym::odoubleacute), "ő");
        assert_eq!(glyph(xkb::Keysym::Return), "");
        assert_eq!(glyph(xkb::Keysym::EuroSign), "€");
    }

    #[test]
    fn the_shell_gets_every_key() {
        let b = board("hu");
        let s = LayoutSummary::of(&layout(757.0, 538.0, Form { iso: true }), b.group(0));
        assert_eq!(s.keys.len(), rows(Form { iso: true }).iter().map(|r| r.len()).sum::<usize>());
        assert_eq!(s.keys.iter().filter(|k| k.kind == "mod").count(), 7);
        assert_eq!(s.keys.iter().filter(|k| k.kind == "layout").count(), 1);
        let json = serde_json::to_value(&s).unwrap();
        assert_eq!(json["keys"][0]["id"], "esc");
        assert_eq!(json["touchpad"].as_array().unwrap().len(), 4);
        let ae10 = s.keys.iter().find(|k| k.id == "ae10").unwrap();
        assert_eq!((ae10.label.as_str(), ae10.shift.as_str(), ae10.altgr.as_str()), ("ö", "Ö", "˝"));
        assert_eq!(serde_json::to_value(ModKind::AltGr).unwrap(), "altgr");
    }
}
