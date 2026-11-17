//! The seat's keyboard layouts: a plugged keyboard's (Bluetooth, USB) and
//! laptop mode's keyboard panel's, one list (xkb layouts with their
//! variants, at most four: xkb's groups) and the one in force.
//!
//! - **The list** is the system's: the shell sends it from
//!   `org.gnome.desktop.input-sources` (the on-screen keyboard's list, which
//!   Duo Settings > Keyboard edits) with `set_keyboard layouts=...`, at its
//!   start and at every change. duo-wc keeps the last one in its state file
//!   (`$XDG_STATE_HOME/duo-wc/keyboard`), so it starts with it; before the
//!   first one, duo-wc.conf's `xkb_layout` / `xkb_variant` (empty: the
//!   environment's `XKB_DEFAULT_LAYOUT`, else `us`). `xkb_model` and
//!   `xkb_options` always come from duo-wc.conf.
//! - **A new list** compiles the seat's keymap again (every client gets it;
//!   the keymap sync, crate::keymapsync, sets this one back after an
//!   on-screen key) and keeps the layout in force if it is still there.
//! - **The layout in force** is the seat's group: laptop mode's layout key
//!   switches it (tapped: the next one; held: a list, crate::laptopmode),
//!   so does `set_keyboard active=next|<n>|<layout>`; a plugged keyboard
//!   types in it too. A layout picked from laptop mode's list that is not
//!   in the list yet is added (`set_keyboard add=de`) and the shell is told
//!   (`{"event":"keyboard_layout_added","layout":"de","variant":""}`): it
//!   adds it to the system's list.

use serde::Serialize;
use smithay::input::keyboard::Layout as XkbLayout;
use tracing::{info, warn};

use crate::config::Xkb;
use crate::state::DuoWc;

/// xkb's groups: at most four layouts.
pub const MAX_LAYOUTS: usize = 4;

/// One layout: an xkb layout (`hu`) and its variant (empty: the default).
#[derive(Debug, Clone, Default, PartialEq, Eq, Hash, Serialize)]
pub struct Entry {
    /// The xkb layout (`hu`, `us`, `de` ...).
    pub layout: String,
    /// Its variant (`nodeadkeys`; empty: the layout's default).
    pub variant: String,
}

impl Entry {
    /// A layout with its variant.
    pub fn new(layout: &str, variant: &str) -> Self {
        Entry { layout: layout.to_owned(), variant: variant.to_owned() }
    }

    /// What the layout key shows: `HU`, `US`.
    pub fn short(&self) -> String {
        self.layout.to_uppercase()
    }
}

/// A single xkb layout or variant name (no list separator).
fn name_ok(name: &str) -> bool {
    name.chars().all(|c| c.is_ascii_alphanumeric() || "_-()".contains(c))
}

/// The list from xkb's comma-separated names (`hu,us` and `,nodeadkeys`):
/// every layout named, at most [`MAX_LAYOUTS`], each one once (a repeat
/// is dropped), the variants aligned (fewer: the default).
pub fn parse(layouts: &str, variants: &str) -> Result<Vec<Entry>, String> {
    if layouts.trim().is_empty() {
        return Err("no layout".into());
    }
    let names: Vec<&str> = layouts.split(',').map(str::trim).collect();
    let vars: Vec<&str> =
        if variants.trim().is_empty() { Vec::new() } else { variants.split(',').map(str::trim).collect() };
    if vars.len() > names.len() {
        return Err(format!("{} variants for {} layouts", vars.len(), names.len()));
    }
    let mut out: Vec<Entry> = Vec::new();
    for (i, name) in names.iter().enumerate() {
        let variant = vars.get(i).copied().unwrap_or("");
        if name.is_empty() || !name_ok(name) || !name_ok(variant) {
            return Err(format!("not an xkb layout: {name}({variant})"));
        }
        let entry = Entry::new(name, variant);
        if !out.contains(&entry) {
            out.push(entry);
        }
    }
    if out.len() > MAX_LAYOUTS {
        return Err(format!("at most {MAX_LAYOUTS} layouts (xkb's groups), not {}", out.len()));
    }
    Ok(out)
}

/// The configured list (duo-wc.conf's `xkb_layout` / `xkb_variant`; empty:
/// the environment's default layout, else `us`).
pub fn of_names(xkb: &Xkb, env_default: Option<&str>) -> Vec<Entry> {
    let layout = if xkb.layout.trim().is_empty() {
        env_default.filter(|l| !l.trim().is_empty()).unwrap_or("us")
    } else {
        xkb.layout.as_str()
    };
    let variant = if xkb.layout.trim().is_empty() { "" } else { xkb.variant.as_str() };
    parse(layout, variant).unwrap_or_else(|_| vec![Entry::new("us", "")])
}

/// The seat's xkb names for a list: its layouts and variants, the model and
/// the options of `base` (duo-wc.conf's).
pub fn names(base: &Xkb, list: &[Entry]) -> Xkb {
    let variant = if list.iter().all(|e| e.variant.is_empty()) {
        String::new()
    } else {
        list.iter().map(|e| e.variant.as_str()).collect::<Vec<_>>().join(",")
    };
    Xkb {
        model: base.model.clone(),
        layout: list.iter().map(|e| e.layout.as_str()).collect::<Vec<_>>().join(","),
        variant,
        options: base.options.clone(),
    }
}

/// The state file's text: `layouts hu,us` and `variants ,` lines.
pub fn state_text(list: &[Entry]) -> String {
    let n = names(&Xkb::default(), list);
    format!("layouts {}\nvariants {}\n", n.layout, n.variant)
}

/// The list in a state file's text (None: no valid list in it).
pub fn from_state(text: &str) -> Option<Vec<Entry>> {
    let mut layouts = None;
    let mut variants = String::new();
    for line in text.lines() {
        let mut words = line.splitn(2, ' ');
        match (words.next(), words.next()) {
            (Some("layouts"), Some(v)) => layouts = Some(v.trim().to_owned()),
            (Some("variants"), Some(v)) => variants = v.trim().to_owned(),
            _ => {}
        }
    }
    parse(&layouts?, &variants).ok()
}

/// The seat's layouts and where they came from.
#[derive(Debug, Clone)]
pub struct Layouts {
    /// duo-wc.conf's names (the model and the options, and the start list).
    pub base: Xkb,
    /// The list in force.
    pub list: Vec<Entry>,
}

/// `$XDG_STATE_HOME/duo-wc/keyboard`.
fn state_path() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/state")))?;
    Some(base.join("duo-wc/keyboard"))
}

impl Layouts {
    /// The list at the start: the state file's (the last one the system
    /// sent), else duo-wc.conf's.
    pub fn load(base: Xkb) -> Self {
        let env = std::env::var("XKB_DEFAULT_LAYOUT").ok();
        let kept = state_path().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|t| from_state(&t));
        let list = kept.unwrap_or_else(|| of_names(&base, env.as_deref()));
        Layouts { base, list }
    }

    /// The seat's xkb names now.
    pub fn names(&self) -> Xkb {
        names(&self.base, &self.list)
    }

    fn save(&self) {
        let Some(path) = state_path() else {
            return;
        };
        let written = path
            .parent()
            .map(std::fs::create_dir_all)
            .unwrap_or(Ok(()))
            .and_then(|_| std::fs::write(&path, state_text(&self.list)));
        if let Err(err) = written {
            warn!(?err, path = %path.display(), "could not keep the keyboard layouts");
        }
    }
}

/// The layouts as the event stream tells them (`keyboard`).
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Summary {
    /// The list, in order.
    pub layouts: Vec<Entry>,
    /// The one in force (an index into `layouts`).
    pub active: u32,
}

impl DuoWc {
    /// The layout in force (the seat's group).
    pub fn keyboard_active(&self) -> u32 {
        self.seat.get_keyboard().map(|k| k.modifier_state().serialized.layout_effective).unwrap_or(0)
    }

    /// The layouts as the event stream tells them.
    pub fn keyboard_summary(&self) -> Summary {
        Summary { layouts: self.kbd.list.clone(), active: self.keyboard_active() }
    }

    /// A new list (the system's): the seat's keymap compiled again, the
    /// layout in force kept if it is still there (else the first).
    pub fn set_keyboard_layouts(&mut self, list: Vec<Entry>, why: &str) -> anyhow::Result<()> {
        if list.is_empty() || list.len() > MAX_LAYOUTS {
            anyhow::bail!("1 to {MAX_LAYOUTS} layouts, not {}", list.len());
        }
        if list == self.kbd.list {
            return Ok(());
        }
        let Some(keyboard) = self.seat.get_keyboard() else {
            anyhow::bail!("no keyboard");
        };
        let before = self.kbd.list.get(self.keyboard_active() as usize).cloned();
        let old = self.kbd.names();
        let new = names(&self.kbd.base, &list);
        crate::keymapsync::set_seat_xkb(&new);
        if let Err(err) = keyboard.set_xkb_config(self, crate::keymapsync::seat_xkb_config()) {
            crate::keymapsync::set_seat_xkb(&old);
            anyhow::bail!("the keymap {} could not be compiled: {err:?}", new.layout);
        }
        // The seat's keymap is the active one again (crate::keymapsync).
        self.keymap_sync.seat_key();
        self.kbd.list = list;
        self.kbd.save();
        let keep = before.and_then(|e| self.kbd.list.iter().position(|x| *x == e)).unwrap_or(0);
        if keep != 0 {
            keyboard.with_xkb_state(self, |mut ctx| ctx.set_layout(XkbLayout(keep as u32)));
        }
        info!(why, layouts = new.layout, variants = new.variant, active = keep, "keyboard layouts");
        self.laptop_keymap_changed();
        self.events_dirty = true;
        Ok(())
    }

    /// Put layout `index` in force.
    pub fn keyboard_select(&mut self, index: u32) -> anyhow::Result<()> {
        if index as usize >= self.kbd.list.len() {
            anyhow::bail!("there are {} layouts, no {index}", self.kbd.list.len());
        }
        let Some(keyboard) = self.seat.get_keyboard() else {
            anyhow::bail!("no keyboard");
        };
        if self.keyboard_active() != index {
            keyboard.with_xkb_state(self, |mut ctx| ctx.set_layout(XkbLayout(index)));
            info!(layout = self.kbd.list[index as usize].layout, "keyboard layout in force");
        }
        self.laptop_group_changed();
        self.events_dirty = true;
        Ok(())
    }

    /// The next layout in force (after the last: the first).
    pub fn keyboard_next(&mut self) {
        let n = self.kbd.list.len().max(1) as u32;
        let next = (self.keyboard_active() + 1) % n;
        let _ = self.keyboard_select(next);
    }

    /// A layout in force; one not in the list is added to it first (at its
    /// end; refused when the list is full) and the shell told, which adds it
    /// to the system's list. Returns its index.
    pub fn keyboard_pick(&mut self, entry: Entry) -> anyhow::Result<u32> {
        if let Some(i) = self.kbd.list.iter().position(|e| *e == entry) {
            self.keyboard_select(i as u32)?;
            return Ok(i as u32);
        }
        if !name_ok(&entry.layout) || entry.layout.is_empty() || !name_ok(&entry.variant) {
            anyhow::bail!("not an xkb layout: {}", entry.layout);
        }
        if self.kbd.list.len() >= MAX_LAYOUTS {
            anyhow::bail!("{MAX_LAYOUTS} layouts already (xkb's groups)");
        }
        let mut list = self.kbd.list.clone();
        list.push(entry.clone());
        self.set_keyboard_layouts(list, "a layout picked")?;
        let index = (self.kbd.list.len() - 1) as u32;
        self.keyboard_select(index)?;
        crate::ipc::broadcast(
            self,
            &serde_json::json!({ "event": "keyboard_layout_added", "layout": entry.layout, "variant": entry.variant }),
        );
        Ok(index)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_list_is_parsed_from_xkb_names() {
        assert_eq!(parse("hu,us", "").unwrap(), vec![Entry::new("hu", ""), Entry::new("us", "")]);
        assert_eq!(
            parse("de, us", "nodeadkeys").unwrap(),
            vec![Entry::new("de", "nodeadkeys"), Entry::new("us", "")]
        );
        assert_eq!(parse("us,hu,us", ",,").unwrap().len(), 2, "a repeat is dropped");
        assert_eq!(parse("us,us", ",dvorak").unwrap().len(), 2, "another variant is another layout");
        assert!(parse("", "").is_err());
        assert!(parse("hu,", "").is_err());
        assert!(parse("hu", ",x").is_err());
        assert!(parse("h u", "").is_err());
        assert!(parse("us,hu,de,fr,it", "").is_err());
    }

    #[test]
    fn the_configured_list_and_its_names() {
        let conf = |layout: &str, variant: &str| Xkb {
            layout: layout.into(),
            variant: variant.into(),
            options: "grp:alt_shift_toggle".into(),
            ..Xkb::default()
        };
        assert_eq!(of_names(&conf("", ""), None), vec![Entry::new("us", "")]);
        assert_eq!(of_names(&conf("", ""), Some("de")), vec![Entry::new("de", "")]);
        assert_eq!(of_names(&conf("hu,us", ""), Some("de")).len(), 2);
        assert_eq!(of_names(&conf("bad name", ""), None), vec![Entry::new("us", "")]);
        let list = vec![Entry::new("hu", ""), Entry::new("de", "nodeadkeys")];
        let n = names(&conf("us", ""), &list);
        assert_eq!(
            (n.layout.as_str(), n.variant.as_str(), n.options.as_str()),
            ("hu,de", ",nodeadkeys", "grp:alt_shift_toggle")
        );
        assert_eq!(names(&Xkb::default(), &[Entry::new("hu", ""), Entry::new("us", "")]).variant, "");
    }

    #[test]
    fn the_state_file_keeps_the_list() {
        let list = vec![Entry::new("hu", ""), Entry::new("de", "nodeadkeys")];
        assert_eq!(state_text(&list), "layouts hu,de\nvariants ,nodeadkeys\n");
        assert_eq!(from_state(&state_text(&list)), Some(list));
        assert_eq!(from_state("layouts us\n"), Some(vec![Entry::new("us", "")]));
        assert_eq!(from_state("junk\n"), None);
        assert_eq!(from_state("layouts a b\n"), None);
        assert_eq!(Entry::new("hu", "").short(), "HU");
    }
}
