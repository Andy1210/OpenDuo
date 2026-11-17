//! The save file: best scores, the catch records, the language.
//!
//! Plain `key = value` lines (~/.config/nibfair/save.txt), so a person can
//! read it; unknown or broken lines are kept out, never fatal.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Save {
    map: BTreeMap<String, String>,
}

impl Save {
    pub fn parse(text: &str) -> Save {
        let mut map = BTreeMap::new();
        for line in text.lines() {
            let line = line.split('#').next().unwrap_or("").trim();
            if let Some((k, v)) = line.split_once('=') {
                let (k, v) = (k.trim(), v.trim());
                if !k.is_empty()
                    && k.chars()
                        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_')
                {
                    map.insert(k.to_owned(), v.to_owned());
                }
            }
        }
        Save { map }
    }

    pub fn text(&self) -> String {
        let mut s = String::from("# Nibfair: best scores and settings\n");
        for (k, v) in &self.map {
            s.push_str(&format!("{k} = {v}\n"));
        }
        s
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.map.get(key).map(|s| s.as_str())
    }

    pub fn set(&mut self, key: &str, value: impl ToString) {
        self.map.insert(key.to_owned(), value.to_string());
    }

    pub fn num(&self, key: &str) -> u32 {
        self.get(key).and_then(|v| v.parse().ok()).unwrap_or(0)
    }

    pub fn float(&self, key: &str) -> f32 {
        self.get(key).and_then(|v| v.parse().ok()).unwrap_or(0.0)
    }

    /// Keep `value` if it beats the stored one. -> true when it did.
    pub fn best(&mut self, key: &str, value: u32) -> bool {
        if value > self.num(key) {
            self.set(key, value);
            true
        } else {
            false
        }
    }

    pub fn path() -> PathBuf {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| Path::new(&h).join(".config")))
            .unwrap_or_else(|| PathBuf::from("."));
        base.join("nibfair").join("save.txt")
    }

    pub fn load(path: &Path) -> Save {
        std::fs::read_to_string(path)
            .map(|t| Save::parse(&t))
            .unwrap_or_default()
    }

    /// Written whole to a temporary file, then renamed over the old one.
    pub fn store(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, self.text())?;
        std::fs::rename(&tmp, path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_bad_lines() {
        let mut s =
            Save::parse("best.fishing = 120\n# a comment\nnonsense\nlang=hu\nbad key = 3\n");
        assert_eq!(s.num("best.fishing"), 120);
        assert_eq!(s.get("lang"), Some("hu"));
        assert_eq!(s.get("bad key"), None);
        assert!(!s.best("best.fishing", 100));
        assert!(s.best("best.fishing", 130));
        s.set("fish.pike.max", 61.5);
        let again = Save::parse(&s.text());
        assert_eq!(again, s);
        assert_eq!(again.float("fish.pike.max"), 61.5);
        assert_eq!(again.num("missing"), 0);
    }

    #[test]
    fn store_and_load() {
        let dir = std::env::temp_dir().join(format!("nibfair-test-{}", std::process::id()));
        let path = dir.join("save.txt");
        let mut s = Save::default();
        s.set("best.slicing", 999);
        s.store(&path).unwrap();
        assert_eq!(Save::load(&path).num("best.slicing"), 999);
        let _ = std::fs::remove_dir_all(dir);
    }
}
