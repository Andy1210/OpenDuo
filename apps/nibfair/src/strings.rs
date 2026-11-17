//! The words on screen, in English and Hungarian.
//!
//! The language comes from the save file (the menu's toggle), else from
//! NIBFAIR_LANG, else from the locale (LC_ALL, LC_MESSAGES, LANG: hu*).

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Lang {
    En,
    Hu,
}

impl Lang {
    pub fn code(self) -> &'static str {
        match self {
            Lang::En => "en",
            Lang::Hu => "hu",
        }
    }

    pub fn from_code(s: &str) -> Option<Lang> {
        let s = s.trim().to_ascii_lowercase();
        if s.starts_with("hu") {
            Some(Lang::Hu)
        } else if s.starts_with("en") {
            Some(Lang::En)
        } else {
            None
        }
    }

    /// From the environment: NIBFAIR_LANG, then the locale. Pure (the lookup is passed in).
    pub fn from_env(get: impl Fn(&str) -> Option<String>) -> Lang {
        if let Some(l) = get("NIBFAIR_LANG").and_then(|v| Lang::from_code(&v)) {
            return l;
        }
        for var in ["LC_ALL", "LC_MESSAGES", "LANG"] {
            if let Some(v) = get(var).filter(|v| !v.is_empty()) {
                return if v.starts_with("hu") {
                    Lang::Hu
                } else {
                    Lang::En
                };
            }
        }
        Lang::En
    }

    pub fn other(self) -> Lang {
        match self {
            Lang::En => Lang::Hu,
            Lang::Hu => Lang::En,
        }
    }
}

/// (key, English, Hungarian). `{}` is filled by `tf`.
const TABLE: &[(&str, &str, &str)] = &[
    ("app.name", "Nibfair", "Nibfair"),
    (
        "app.tagline",
        "Three games for the pen",
        "Három játék a tollnak",
    ),
    ("menu.fishing", "Fishing", "Horgászat"),
    ("menu.slicing", "Slicing", "Vágás"),
    ("menu.pottery", "Pottery", "Fazekasság"),
    (
        "menu.fishing.sub",
        "Hover the pen: down it sinks the bait",
        "Lebegtesd a tollat: lejjebb mélyebbre merül a csali",
    ),
    (
        "menu.slicing.sub",
        "Fast strokes cut, firm ones cut stone",
        "Gyors vonás vág, erős vonás a követ is",
    ),
    (
        "menu.pottery.sub",
        "Tilt and press to shape the clay",
        "Dönts és nyomj: formáld az agyagot",
    ),
    ("menu.best", "Best {}", "Rekord {}"),
    ("menu.lang", "Magyar", "English"),
    ("menu.quit", "Quit", "Kilépés"),
    (
        "pen.missing",
        "Hover the Slim Pen over the screen",
        "Tartsd a Slim Pent a képernyő fölé",
    ),
    (
        "pen.finger",
        "The pen arrives as a finger: add Nibfair in Settings > Pen > Apps",
        "A toll ujjként érkezik: vedd fel a Nibfairt a Beállítások > Pen > Apps listába",
    ),
    ("common.back", "Menu", "Menü"),
    ("common.again", "Play again", "Újra"),
    ("common.score", "Score", "Pont"),
    ("common.newbest", "New best!", "Új rekord!"),
    ("common.best", "Best: {}", "Rekord: {}"),
    // Fishing
    (
        "fish.hint.cast",
        "Touch and tilt the pen to aim, lift to cast",
        "Érintsd le, döntsd a célzáshoz, emeld fel a dobáshoz",
    ),
    (
        "fish.hint.depth",
        "Hover lower to sink the bait, higher to lift it",
        "Lejjebb lebegtetve merül a csali, feljebb emelkedik",
    ),
    (
        "fish.hint.wait",
        "Keep it still... wait for a bite",
        "Tartsd nyugodtan... várj a kapásra",
    ),
    (
        "fish.hint.reel",
        "Press to reel: not too hard, not too soft",
        "Nyomással tekersz: ne túl erősen, ne túl lazán",
    ),
    (
        "fish.hint.recast",
        "Hold the tip down to cast again",
        "Tartsd lenyomva a hegyet az új dobáshoz",
    ),
    ("fish.bite", "Bite! Tap!", "Kapás! Koppints!"),
    ("fish.early", "Too early!", "Túl korán!"),
    ("fish.stolen", "It stole the bait", "Elvitte a csalit"),
    ("fish.spooked", "Spooked!", "Elriadt!"),
    ("fish.snapped", "The line snapped!", "Elszakadt a zsinór!"),
    ("fish.escaped", "It got away", "Elúszott"),
    ("fish.easy", "Ease off!", "Engedj!"),
    ("fish.slack", "Slack! Press harder", "Laza! Nyomd jobban"),
    ("fish.timeup", "The sun is down", "Lement a nap"),
    ("fish.log", "Catch log", "Fogási napló"),
    (
        "fish.none",
        "Nothing caught today",
        "Ma semmi nem akadt horogra",
    ),
    ("fish.depth", "{} m", "{} m"),
    ("fish.minnow", "Minnow", "Fürge cselle"),
    ("fish.perch", "Perch", "Sügér"),
    ("fish.trout", "Trout", "Pisztráng"),
    ("fish.pike", "Pike", "Csuka"),
    ("fish.carp", "Carp", "Ponty"),
    ("fish.catfish", "Catfish", "Harcsa"),
    ("fish.eel", "Eel", "Angolna"),
    ("fish.goldfin", "Goldfin", "Aranyúszó"),
    ("fish.boot", "Old boot", "Régi bakancs"),
    // Slicing
    (
        "slice.hint.cut",
        "Swipe fast through things to cut them",
        "Gyors vonással vágd ketté őket",
    ),
    (
        "slice.hint.hard",
        "Dots = hardness: press firmly for those",
        "A pöttyök a keménységet mutatják: erősen nyomd",
    ),
    (
        "slice.hint.ink",
        "Ink! Turn the pen over, wipe it with the eraser",
        "Tinta! Fordítsd meg a tollat, radírozd le",
    ),
    ("slice.round", "Round {}", "{}. kör"),
    ("slice.perfect", "Perfect round +{}", "Hibátlan kör +{}"),
    ("slice.combo", "{} in one stroke!", "{} egy vonással!"),
    ("slice.soft", "Press harder!", "Nyomd erősebben!"),
    (
        "slice.focus",
        "Focus ready: side button",
        "Fókusz kész: oldalgomb",
    ),
    ("slice.slow", "Slow time!", "Lassítás!"),
    (
        "slice.over",
        "Too much ink! Game over",
        "Túl sok a tinta! Vége a játéknak",
    ),
    (
        "slice.victory",
        "All ten rounds cleared!",
        "Mind a tíz kör megvan!",
    ),
    ("slice.combobest", "Best combo: {}", "Legjobb kombó: {}"),
    // Pottery
    ("pot.order", "Order {} of {}", "{}. rendelés ({} közül)"),
    (
        "pot.hint.cut",
        "Press into the clay to cut: harder takes more",
        "Nyomd az agyagba: erősebben többet vág",
    ),
    (
        "pot.hint.tilt",
        "Tilt the pen for a straight wall",
        "Döntsd a tollat az egyenes falhoz",
    ),
    (
        "pot.hint.smooth",
        "Smooth it: touch lightly, or hover beside the clay",
        "Simítsd: érintsd finoman, vagy lebegtesd mellette",
    ),
    (
        "pot.hint.eraser",
        "The eraser end puts clay back",
        "A radírvég visszarak agyagot",
    ),
    ("pot.fire", "Fire it", "Kiégetés"),
    ("pot.match", "Match {}%", "Egyezés {}%"),
    ("pot.shape", "Shape {}%", "Forma {}%"),
    ("pot.smooth", "Smooth {}%", "Simaság {}%"),
    ("pot.next", "Next order", "Következő"),
    ("pot.done", "Workshop done!", "Kész a műhely!"),
    ("pot.total", "Total {}", "Összesen {}"),
    ("pot.rough", "Rough", "Durva"),
    ("order.cup", "a mug", "Bögre"),
    ("order.flowerpot", "a flower pot", "Virágcserép"),
    ("order.bowl", "a bowl", "Tál"),
    ("order.vase", "a vase", "Váza"),
    ("order.hourglass", "an hourglass", "Homokóra"),
    ("order.bottle", "a bottle", "Palack"),
    ("order.tower", "a tower", "Torony"),
    ("order.amphora", "an amphora", "Amfora"),
    ("pot.make", "Make {}", "Készíts: {}"),
];

pub fn t(lang: Lang, key: &str) -> &'static str {
    match TABLE.iter().find(|e| e.0 == key) {
        Some(e) => match lang {
            Lang::En => e.1,
            Lang::Hu => e.2,
        },
        None => "?",
    }
}

/// `t` with each `{}` filled from `args`, in order.
pub fn tf(lang: Lang, key: &str, args: &[&dyn std::fmt::Display]) -> String {
    let mut out = String::new();
    let mut rest = t(lang, key);
    let mut i = 0;
    while let Some(p) = rest.find("{}") {
        out.push_str(&rest[..p]);
        if let Some(a) = args.get(i) {
            out.push_str(&a.to_string());
        }
        i += 1;
        rest = &rest[p + 2..];
    }
    out.push_str(rest);
    out
}

/// Every key, for the tests (and the glyph cache's warm-up).
pub fn all_text() -> impl Iterator<Item = &'static str> {
    TABLE.iter().flat_map(|e| [e.1, e.2])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_key_has_both_languages_and_matching_placeholders() {
        for (k, en, hu) in TABLE {
            assert!(!en.is_empty() && !hu.is_empty(), "{k}");
            assert_eq!(en.matches("{}").count(), hu.matches("{}").count(), "{k}");
            assert!(
                !en.contains('\u{2014}') && !hu.contains('\u{2014}'),
                "{k}: no em dash on screen"
            );
        }
        let mut keys: Vec<_> = TABLE.iter().map(|e| e.0).collect();
        keys.sort();
        let n = keys.len();
        keys.dedup();
        assert_eq!(keys.len(), n, "a key twice");
    }

    #[test]
    fn the_rules_names_exist() {
        for k in crate::fish::KINDS.iter() {
            assert_ne!(t(Lang::Hu, k.key), "?", "{}", k.key);
        }
        for o in crate::pottery::ORDERS.iter() {
            assert_ne!(t(Lang::En, o.key), "?", "{}", o.key);
        }
    }

    #[test]
    fn formatting_and_the_locale() {
        assert_eq!(tf(Lang::En, "slice.round", &[&3]), "Round 3");
        assert_eq!(tf(Lang::Hu, "slice.round", &[&3]), "3. kör");
        assert_eq!(
            tf(Lang::Hu, "pot.order", &[&2, &8]),
            "2. rendelés (8 közül)"
        );
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| pairs.iter().find(|p| p.0 == k).map(|p| p.1.to_string())
        };
        assert_eq!(Lang::from_env(env(&[("LANG", "hu_HU.UTF-8")])), Lang::Hu);
        assert_eq!(
            Lang::from_env(env(&[("LANG", "hu_HU.UTF-8"), ("LC_ALL", "C.UTF-8")])),
            Lang::En
        );
        assert_eq!(
            Lang::from_env(env(&[("NIBFAIR_LANG", "hu"), ("LANG", "en_US")])),
            Lang::Hu
        );
        assert_eq!(Lang::from_env(env(&[])), Lang::En);
    }
}
