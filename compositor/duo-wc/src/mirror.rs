//! An external display as a desk or as a mirror of the phone (with a TV
//! plugged in: laptop mode, or just the phone's screen). Pure (tested); the live part is in [`crate::extout`] (the
//! choice, the windows) and `backend::render` (the mirror's frames).
//!
//! - **Desk** ([`DisplayUse::Desk`], the desktop mode before this): windows
//!   float on the display, the shell shows its desk there (wallpaper, a
//!   taskbar), the pointer goes there.
//! - **Mirror** ([`DisplayUse::Mirror`]): the display shows what a panel
//!   shows, scaled to fit with black bars ([`fit`]), upright as the panel
//!   reads; the phone goes on as a phone (no window, no pointer, no shell
//!   surface on the display: a Bluetooth keyboard and mouse work on the
//!   panels as usual). Which panels ([`MirrorSource`], `external_mirror`):
//!   the one touched last (`active`, following the focus), both side by side
//!   (`both`), or a fixed one.
//! - **On connect** ([`OnConnect`], `external_on_connect`): `ask` (the
//!   default: the display mirrors while the shell asks "Desktop / Mirror",
//!   [`DisplayUse::initial`]), `desk` or `mirror`. The answer can be kept
//!   (the shell's "Remember": `set_external on_connect ...`); the quick menu
//!   switches a connected display (`set_display`).
//!
//! The mirror's frames: the panel's own scene (as a screenshot renders it)
//! drawn into a GPU texture and that texture drawn on the display, at the
//! display's refresh, whenever the panel draws (no copy through the CPU).

use serde::Serialize;

/// What a connected display is used as.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DisplayUse {
    /// Windows float there (crate::desk); the shell's desk.
    Desk,
    /// It shows a panel's picture.
    Mirror,
}

impl DisplayUse {
    /// `desk` (or `desktop`) or `mirror`.
    pub fn parse(value: &str) -> Option<DisplayUse> {
        match value.trim().to_ascii_lowercase().as_str() {
            "desk" | "desktop" => Some(DisplayUse::Desk),
            "mirror" => Some(DisplayUse::Mirror),
            _ => None,
        }
    }

    /// Its name in the state and on the control socket.
    pub fn name(self) -> &'static str {
        match self {
            DisplayUse::Desk => "desk",
            DisplayUse::Mirror => "mirror",
        }
    }

    /// A display just connected: what it is used as, and whether the shell
    /// asks. Asking, it mirrors meanwhile: nothing on the phone moves until
    /// the answer.
    pub fn initial(on_connect: OnConnect) -> (DisplayUse, bool) {
        match on_connect {
            OnConnect::Ask => (DisplayUse::Mirror, true),
            OnConnect::Desk => (DisplayUse::Desk, false),
            OnConnect::Mirror => (DisplayUse::Mirror, false),
        }
    }
}

/// `external_on_connect`: what a display is used as when it is plugged.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum OnConnect {
    /// The shell asks (the display mirrors meanwhile).
    #[default]
    Ask,
    /// A desk at once.
    Desk,
    /// A mirror at once.
    Mirror,
}

impl OnConnect {
    /// `ask`, `desk` (or `desktop`) or `mirror`.
    pub fn parse(value: &str) -> Option<OnConnect> {
        match value.trim().to_ascii_lowercase().as_str() {
            "ask" => Some(OnConnect::Ask),
            "desk" | "desktop" => Some(OnConnect::Desk),
            "mirror" => Some(OnConnect::Mirror),
            _ => None,
        }
    }

    /// Its name in the state file and on the control socket.
    pub fn name(self) -> &'static str {
        match self {
            OnConnect::Ask => "ask",
            OnConnect::Desk => "desk",
            OnConnect::Mirror => "mirror",
        }
    }
}

/// `external_mirror`: which panels a mirroring display shows.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum MirrorSource {
    /// The panel touched last (the focus): the mirror follows it.
    #[default]
    Active,
    /// Both panels side by side (in their order on the canvas).
    Both,
    /// Always this panel.
    Panel(String),
}

impl MirrorSource {
    /// `active`, `both`, `DSI-1` or `DSI-2`.
    pub fn parse(value: &str) -> Option<MirrorSource> {
        match value.trim() {
            "active" => Some(MirrorSource::Active),
            "both" => Some(MirrorSource::Both),
            p if p == "DSI-1" || p == "DSI-2" => Some(MirrorSource::Panel(p.to_owned())),
            _ => None,
        }
    }

    /// Its name in the state file and on the control socket.
    pub fn name(&self) -> String {
        match self {
            MirrorSource::Active => "active".into(),
            MirrorSource::Both => "both".into(),
            MirrorSource::Panel(p) => p.clone(),
        }
    }
}

/// The settings (duo-wc.conf's `external_on_connect`, `external_mirror`,
/// changed live by `set_external` and kept in duo-wc's state file
/// `$XDG_STATE_HOME/duo-wc/external`, as laptop mode's are: an older duo-wc
/// put back never meets a key it does not know in duo-wc.conf).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Settings {
    /// What a display plugged in is used as.
    pub on_connect: OnConnect,
    /// What a mirror shows.
    pub mirror: MirrorSource,
}

impl Settings {
    /// The config keys this takes.
    pub const KEYS: &'static [&'static str] = &["external_on_connect", "external_mirror"];

    /// One `key = value` line of duo-wc.conf.
    pub fn set(&mut self, key: &str, value: &str) -> Result<(), String> {
        match key {
            "external_on_connect" => {
                self.on_connect = OnConnect::parse(value)
                    .ok_or_else(|| format!("external_on_connect is ask, desk or mirror, not {value}"))?;
            }
            "external_mirror" => {
                self.mirror = MirrorSource::parse(value)
                    .ok_or_else(|| format!("external_mirror is active, both, DSI-1 or DSI-2, not {value}"))?;
            }
            other => return Err(format!("not a display key: {other}")),
        }
        Ok(())
    }

    /// Over these, the state file's (unknown or bad lines are skipped).
    pub fn with_state(mut self, text: &str) -> Self {
        for line in text.lines() {
            let mut words = line.split_whitespace();
            match (words.next(), words.next()) {
                (Some("on_connect"), Some(v)) => {
                    if let Some(o) = OnConnect::parse(v) {
                        self.on_connect = o;
                    }
                }
                (Some("mirror"), Some(v)) => {
                    if let Some(m) = MirrorSource::parse(v) {
                        self.mirror = m;
                    }
                }
                _ => {}
            }
        }
        self
    }

    /// The state file's text.
    pub fn state_text(&self) -> String {
        format!("on_connect {}\nmirror {}\n", self.on_connect.name(), self.mirror.name())
    }
}

/// The panels a mirroring display shows now: `lit` are the lit panels in
/// their order on the canvas (left to right / top to bottom), `active` the
/// panel touched last. Empty: nothing lit (the display stays black).
pub fn sources(source: &MirrorSource, active: Option<&str>, lit: &[String]) -> Vec<String> {
    let first = || lit.first().cloned().into_iter().collect::<Vec<_>>();
    match source {
        MirrorSource::Both => lit.to_vec(),
        MirrorSource::Panel(p) if lit.iter().any(|l| l == p) => vec![p.clone()],
        MirrorSource::Panel(_) => first(),
        MirrorSource::Active => match active {
            Some(a) if lit.iter().any(|l| l == a) => vec![a.to_owned()],
            _ => first(),
        },
    }
}

/// Black between two pictures side by side (logical px of the display).
pub const GAP: f64 = 16.0;

/// Where pictures of these sizes go on a display of size `dst` (any one
/// unit: what comes back is in `dst`'s): side by side with [`GAP`] between,
/// scaled alike to fit (never past their own size times `max_scale`, 0 for
/// no limit), centred, black around (letterboxing). (x, y, w, h) each.
pub fn fit(pictures: &[(f64, f64)], dst: (f64, f64), max_scale: f64) -> Vec<(f64, f64, f64, f64)> {
    if pictures.is_empty() || dst.0 <= 0.0 || dst.1 <= 0.0 {
        return Vec::new();
    }
    let gaps = GAP * (pictures.len() - 1) as f64;
    let total_w: f64 = pictures.iter().map(|p| p.0).sum::<f64>();
    let total_h = pictures.iter().map(|p| p.1).fold(0.0, f64::max);
    if total_w <= 0.0 || total_h <= 0.0 {
        return Vec::new();
    }
    // The gap does not scale: it is the display's own black.
    let mut scale = ((dst.0 - gaps).max(1.0) / total_w).min(dst.1 / total_h);
    if max_scale > 0.0 {
        scale = scale.min(max_scale);
    }
    let width = total_w * scale + gaps;
    let mut x = (dst.0 - width) / 2.0;
    let mut out = Vec::with_capacity(pictures.len());
    for &(w, h) in pictures {
        let (sw, sh) = (w * scale, h * scale);
        out.push((x, (dst.1 - sh) / 2.0, sw, sh));
        x += sw + GAP;
    }
    out
}

/// One connected display, as the state tells it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DisplaySummary {
    /// The connector (DP-1).
    pub output: String,
    /// What the display calls itself (EDID), if it said.
    pub name: Option<String>,
    /// `desk` or `mirror`.
    #[serde(rename = "use")]
    pub used_as: DisplayUse,
    /// The shell is to ask "Desktop or Mirror" (it mirrors meanwhile).
    pub asking: bool,
    /// The panels it shows now, while it mirrors.
    pub mirrors: Vec<String>,
}

/// The displays and their settings, as the state tells them (`displays`,
/// change `displays`).
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
pub struct Summary {
    /// `external_on_connect` now.
    pub on_connect: OnConnect,
    /// `external_mirror` now.
    pub mirror: String,
    /// The displays connected.
    pub connected: Vec<DisplaySummary>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lit(names: &[&str]) -> Vec<String> {
        names.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn asking_mirrors_meanwhile() {
        assert_eq!(DisplayUse::initial(OnConnect::Ask), (DisplayUse::Mirror, true));
        assert_eq!(DisplayUse::initial(OnConnect::Desk), (DisplayUse::Desk, false));
        assert_eq!(DisplayUse::initial(OnConnect::Mirror), (DisplayUse::Mirror, false));
        assert_eq!(Settings::default().on_connect, OnConnect::Ask);
    }

    #[test]
    fn the_settings_parse_and_keep() {
        let mut s = Settings::default();
        s.set("external_on_connect", "desk").unwrap();
        s.set("external_mirror", "both").unwrap();
        assert_eq!(s, Settings { on_connect: OnConnect::Desk, mirror: MirrorSource::Both });
        assert!(s.set("external_on_connect", "maybe").is_err());
        assert!(s.set("external_mirror", "DP-1").is_err());
        let back = Settings::default().with_state(&s.state_text());
        assert_eq!(back, s);
        let fixed = Settings::default().with_state("on_connect mirror\nmirror DSI-2\nnonsense here\n");
        assert_eq!(
            fixed,
            Settings { on_connect: OnConnect::Mirror, mirror: MirrorSource::Panel("DSI-2".into()) }
        );
        // A bad line keeps what was there.
        assert_eq!(Settings::default().with_state("on_connect sometimes\n"), Settings::default());
        assert_eq!(DisplayUse::parse("Desktop"), Some(DisplayUse::Desk));
        assert_eq!(DisplayUse::parse("mirror"), Some(DisplayUse::Mirror));
        assert_eq!(DisplayUse::parse("span"), None);
    }

    #[test]
    fn the_mirror_follows_the_panel_in_use() {
        let both = lit(&["DSI-1", "DSI-2"]);
        assert_eq!(sources(&MirrorSource::Active, Some("DSI-2"), &both), lit(&["DSI-2"]));
        assert_eq!(sources(&MirrorSource::Active, Some("DSI-1"), &both), lit(&["DSI-1"]));
        // The panel touched last is dark (single pose): the lit one.
        assert_eq!(sources(&MirrorSource::Active, Some("DSI-1"), &lit(&["DSI-2"])), lit(&["DSI-2"]));
        // The active output is the display itself or nothing: the first lit.
        assert_eq!(sources(&MirrorSource::Active, Some("DP-1"), &both), lit(&["DSI-1"]));
        assert_eq!(sources(&MirrorSource::Active, None, &both), lit(&["DSI-1"]));
        assert_eq!(sources(&MirrorSource::Both, Some("DSI-2"), &both), both);
        assert_eq!(sources(&MirrorSource::Both, None, &lit(&["DSI-2"])), lit(&["DSI-2"]));
        assert_eq!(sources(&MirrorSource::Panel("DSI-2".into()), Some("DSI-1"), &both), lit(&["DSI-2"]));
        assert_eq!(sources(&MirrorSource::Panel("DSI-2".into()), None, &lit(&["DSI-1"])), lit(&["DSI-1"]));
        assert!(sources(&MirrorSource::Active, Some("DSI-1"), &[]).is_empty());
    }

    fn close(a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)) -> bool {
        (a.0 - b.0).abs() < 0.01
            && (a.1 - b.1).abs() < 0.01
            && (a.2 - b.2).abs() < 0.01
            && (a.3 - b.3).abs() < 0.01
    }

    #[test]
    fn a_portrait_panel_on_a_tv_is_pillarboxed() {
        // 1344 x 1892 (a panel upright) on 1920 x 1080: as tall as the TV.
        let r = fit(&[(1344.0, 1892.0)], (1920.0, 1080.0), 0.0);
        let s = 1080.0 / 1892.0;
        assert!(close(r[0], ((1920.0 - 1344.0 * s) / 2.0, 0.0, 1344.0 * s, 1080.0)), "{r:?}");
    }

    #[test]
    fn a_landscape_panel_is_letterboxed() {
        // 1892 x 1344 (turned) on 1920 x 1080: the height decides here too.
        let r = fit(&[(1892.0, 1344.0)], (1920.0, 1080.0), 0.0);
        let s = 1080.0 / 1344.0;
        assert!(close(r[0], ((1920.0 - 1892.0 * s) / 2.0, 0.0, 1892.0 * s, 1080.0)), "{r:?}");
        // On a 4:3 display the width decides: bars above and below.
        let r = fit(&[(1892.0, 1344.0)], (1024.0, 768.0), 0.0);
        let s = 1024.0 / 1892.0;
        assert!(close(r[0], (0.0, (768.0 - 1344.0 * s) / 2.0, 1024.0, 1344.0 * s)), "{r:?}");
    }

    #[test]
    fn both_panels_side_by_side_with_a_gap() {
        let r = fit(&[(1344.0, 1892.0), (1344.0, 1892.0)], (1920.0, 1080.0), 0.0);
        assert_eq!(r.len(), 2);
        let s = 1080.0 / 1892.0;
        let w = 1344.0 * s;
        let x0 = (1920.0 - (2.0 * w + GAP)) / 2.0;
        assert!(close(r[0], (x0, 0.0, w, 1080.0)), "{r:?}");
        assert!(close(r[1], (x0 + w + GAP, 0.0, w, 1080.0)), "{r:?}");
        // Wide enough: the two never overlap and stay inside.
        assert!(r[1].0 + r[1].2 <= 1920.0 + 1e-6 && r[0].0 >= -1e-6);
    }

    #[test]
    fn fitting_never_blows_a_picture_up_past_its_limit() {
        let r = fit(&[(100.0, 100.0)], (1920.0, 1080.0), 2.0);
        assert!(close(r[0], (860.0, 440.0, 200.0, 200.0)), "{r:?}");
        assert!(fit(&[], (1920.0, 1080.0), 0.0).is_empty());
        assert!(fit(&[(100.0, 100.0)], (0.0, 1080.0), 0.0).is_empty());
    }

    #[test]
    fn the_summary_reads_as_the_shell_wants() {
        let s = Summary {
            on_connect: OnConnect::Ask,
            mirror: "active".into(),
            connected: vec![DisplaySummary {
                output: "DP-1".into(),
                name: Some("TV".into()),
                used_as: DisplayUse::Mirror,
                asking: true,
                mirrors: vec!["DSI-1".into()],
            }],
        };
        let v = serde_json::to_value(&s).unwrap();
        assert_eq!(v["on_connect"], "ask");
        assert_eq!(v["connected"][0]["use"], "mirror");
        assert_eq!(v["connected"][0]["asking"], true);
        assert_eq!(v["connected"][0]["mirrors"][0], "DSI-1");
    }
}
