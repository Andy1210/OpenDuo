//! The defaults the compositor starts with.
//!
//! A tiny `key = value` file, `#` starts a comment. Read once at startup; what can
//! change at runtime (the hinge gap, the idle timeout) changes over the control
//! socket, and the file only says what to start with.
//!
//! ```text
//! # /etc/duo-wc/duo-wc.conf
//! hinge_gap = on            # on | off
//! scale = 2.5               # output scale for both panels, 1 to 4 (fractional is fine)
//! idle_timeout = 60         # seconds without input until both panels go off; 0 = never
//! idle_inhibit = on         # off: idle inhibitors (a video) and the shell's idle hold do not count
//! touch_wake = off          # on: a touch on a dark screen wakes it (the touch is not delivered)
//! power_key_suspend = off   # on: the power key also suspends the phone (WiFi drops!)
//! full_redraw = off         # on: every frame is drawn whole (no damage reuse; diagnostics)
//! scanout = on              # off: never scan client buffers out on planes (diagnostics)
//! edge_swipe = on           # on: a swipe up from a panel's bottom edge is the shell's
//! atomic_wake = off         # on: panels come on in non-blocking commits (off: one modeset each)
//! atomic_off = on           # on: panels go off together in one non-blocking commit
//! session_lock = off        # on: lock clients (ext-session-lock-v1) may lock the phone
//! back_swipe = both         # both | outer | off: which side edges swipe back
//! edge_zone = 20            # how deep the edge strips are (logical px, 8 to 60)
//! animations = on           # off: windows appear and move without springs
//! rotate_anim_ms = 320       # a turn takes this long, eased in and out (0 = it jumps)
//! snap_hinge_band = 40      # a carried window this close to the hinge goes to the other panel (px)
//! snap_half_share = 0.22    # ... this close to a panel's edge takes that half (share)
//! snap_pad_from = 0.58      # the span pad (both panels) runs along the hinge from here ...
//! snap_pad_to = 0.80        # ... to here (shares of the panels' length along it)
//! stitch_edge = 56          # a drag lifting this close to the hinge may go on across it (px)
//! stitch_ms = 350           # ... if the other panel is touched within this long
//! stitch_offset = 120       # ... this close along the hinge (px)
//! osk_leave = on            # a tap beside the text field puts the keyboard away if the app says nothing
//! android_caption = 43      # Android's caption bar (px): how far a full-screen Android window moves up
//! android_shadow = 10       # the task layer's shadow margin above the bar (px)
//! android_task_helper = /usr/local/sbin/duo2-android-task   # sizes a task inside Android (sudo -n)
//! glance_panels = DSI-2     # the panels the glance lights (both | DSI-1 | DSI-2)
//! peek_panels = DSI-2       # the panels the peek lights (only the right one, like the glance)
//! admin_users = duo2admin   # users whose admin-socket requests are all allowed (root always)
//! sensor_users = duo-posed  # users who may set the posture, pose, rotation, power
//! confirm_users = polkitd   # users who may only ask the user (polkit: removing packages)
//! external = on             # an external display on the USB-C port is used (crate::extout)
//! external_scale = auto     # its scale: auto (from its size) or 1 to 4
//! external_mode = auto      # auto | preferred | 1920x1080[@60]
//! external_idle_timeout = 600  # the idle timeout while one is up (0 never; panels: idle_timeout)
//! external_trackpad = off   # DSI-1 | DSI-2: that panel is the display's touchpad (crate::trackpad)
//! xkb_layout = hu           # a plugged keyboard's layout (also xkb_variant, xkb_options, xkb_model)
//! laptop_auto = off         # on: the laptop posture turns laptop mode on (crate::laptopmode)
//! laptop_keys = DSI-2       # the keyboard panel when the posture does not say (DSI-1 | DSI-2)
//! external_on_connect = ask # a display plugged: ask, desk or mirror (crate::mirror)
//! external_mirror = active  # a mirror shows: active (the panel in use), both, DSI-1 or DSI-2
//! touchpad_drag_lock = off  # on: a tap-and-drag survives its finger lifted for 0.3 s (crate::trackpad)
//! pen_button = right_click  # right_click | off: the pen's barrel button for apps without tablets (crate::tablet)
//! pen_dot = on              # on: a dot shows where a hovering pen points
//! pen_input = apps          # apps: only the drawing apps get the pen as a pen (the rest a finger); all
//! pen_apps = rnote, krita   # those apps: app ids or their last part (default: crate::tablet::DEFAULT_APPS)
//! xwayland = lazy           # X11 apps: lazy (Xwayland starts with the first X11 client), on, off
//! ```

use crate::layout::HingeGap;

/// Where the file is looked for when the command line names none.
pub const DEFAULT_PATH: &str = "/etc/duo-wc/duo-wc.conf";

/// The scale both panels start at. The panels are ~400 ppi (5.8", 1344x1892):
/// at 1 a terminal's text is under a millimetre tall. 2.5 is close to the stock
/// Android density (2.625) and gives a 538x757 logical panel.
pub const DEFAULT_SCALE: f64 = 2.5;

/// Seconds without input until both panels go off.
pub const DEFAULT_IDLE_TIMEOUT_S: u64 = 60;

/// The longest idle timeout accepted: a day. Longer is "never" (0).
pub const MAX_IDLE_TIMEOUT_S: u64 = 86_400;

/// Which side edges swipe back.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BackSwipe {
    /// Both side edges of each panel (the hinge side too).
    #[default]
    Both,
    /// Only the outer edges (the left one of the left panel, the right one
    /// of the right panel; turned panels: both).
    Outer,
    /// None.
    Off,
}

impl BackSwipe {
    /// `both`, `outer` or `off`.
    pub fn parse(value: &str) -> Option<BackSwipe> {
        match value.trim().to_ascii_lowercase().as_str() {
            "both" | "on" => Some(BackSwipe::Both),
            "outer" => Some(BackSwipe::Outer),
            "off" | "none" => Some(BackSwipe::Off),
            _ => None,
        }
    }

    /// The name.
    pub fn name(self) -> &'static str {
        match self {
            BackSwipe::Both => "both",
            BackSwipe::Outer => "outer",
            BackSwipe::Off => "off",
        }
    }
}

/// What the compositor starts with.
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// Whether the hinge takes room in the global space.
    pub hinge_gap: HingeGap,
    /// Output scale, applied to every output. Fractional values reach clients
    /// through wp_fractional_scale_v1; the rest get the next integer and are
    /// scaled down.
    pub scale: f64,
    /// Seconds without input until both panels go off; 0 never blanks.
    pub idle_timeout_s: u64,
    /// Whether idle inhibitors (zwp_idle_inhibit_manager_v1) and the shell's
    /// idle hold keep the screen from blanking by itself ([`crate::idle`]).
    pub idle_inhibit: bool,
    /// Whether a touch on a dark screen wakes it. Off by default: on a phone only
    /// the power key wakes (a pocket is full of touches).
    pub touch_wake: bool,
    /// Whether the power key, after switching the panels off, also suspends the
    /// phone. Off by default: the phone is reached over WiFi, and an unattended
    /// suspend loses it.
    pub power_key_suspend: bool,
    /// Draw every frame whole, never reusing an older buffer's content (damage
    /// tracking off). A diagnostic switch for picture glitches.
    pub full_redraw: bool,
    /// Let the display controller scan client buffers out on planes. Off
    /// composites everything into the primary plane (a diagnostic switch).
    pub scanout: bool,
    /// A swipe up from a panel's bottom edge is the compositor's (the shell
    /// opens its app grid); off delivers every touch to the clients.
    pub edge_swipe: bool,
    /// Panels that come on together (waking the screen) do so in ONE atomic
    /// commit that does not block the event loop; off: one blocking modeset
    /// per panel through Smithay, as before.
    pub atomic_wake: bool,
    /// Panels that go off together (blanking the screen) do so in ONE atomic
    /// commit that does not block the event loop; off: one blocking
    /// switch-off per panel through Smithay (`DrmCompositor::clear`).
    pub atomic_off: bool,
    /// Whether the ext-session-lock global is offered (a lock screen may lock
    /// the phone). Off by default: nothing can lock until the user chose a
    /// PIN and switched it on.
    pub session_lock: bool,
    /// Which side edges swipe back.
    pub back_swipe: BackSwipe,
    /// How deep the edge strips are (logical px).
    pub edge_zone: f64,
    /// Windows move on springs (open, drop, home).
    pub animations: bool,
    /// How long a turned panel's picture turns (ms; 0 = no animation).
    pub rotate_anim_ms: u64,
    /// Where a carried window snaps (crate::snap).
    pub snap: crate::snap::Config,
    /// When a drag goes on across the hinge (crate::stitch).
    pub stitch: crate::stitch::Config,
    /// Android apps' windows (crate::android).
    pub android: crate::android::Config,
    /// A tap beside the text field puts the on-screen keyboard away when the
    /// app says nothing (crate::oskleave).
    pub osk_leave: bool,
    /// The panels the glance (and its ruler) light: `both` or a connector.
    pub glance_panels: String,
    /// The panels the peek lights.
    pub peek_panels: String,
    /// The users (names) whose requests on the admin socket are all allowed
    /// (root's always are): the administrator's tooling ([`crate::trust`]).
    pub admin_users: Vec<String>,
    /// The users who may drive the posture, pose, rotation and power on the
    /// admin socket (duo-posed's), and read the state.
    pub sensor_users: Vec<String>,
    /// Users whose admin-socket `confirm` is answered (only that).
    pub confirm_users: Vec<String>,
    /// External displays on the USB-C port (`external*`, crate::extout).
    pub external: crate::extout::Config,
    /// A display as a desk or a mirror (`external_on_connect`,
    /// `external_mirror`, crate::mirror).
    pub display: crate::mirror::Settings,
    /// A plugged keyboard's layout (`xkb_*`).
    pub xkb: Xkb,
    /// Laptop mode (`laptop_*`, crate::laptopmode).
    pub laptop: crate::laptop::Config,
    /// `touchpad_drag_lock`: a touchpad's tap-and-drag goes on when its
    /// finger lifts briefly (crate::trackpad, both touchpads).
    pub touchpad_drag_lock: bool,
    /// The pen (`pen_button`, `pen_dot`, `pen_input`, `pen_apps`, crate::tablet).
    pub pen: crate::tablet::Settings,
    /// When Xwayland runs (`xwayland`, crate::xwayland).
    pub xwayland: crate::xwayland::XMode,
}

/// A plugged keyboard's layout: `xkb_layout = hu`, `xkb_variant`,
/// `xkb_options = grp:alt_shift_toggle`, `xkb_model`; empty: xkbcommon's
/// default (us, or XKB_DEFAULT_* from the environment).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Xkb {
    /// `xkb_model` (pc105, ...).
    pub model: String,
    /// `xkb_layout` (hu, or hu,us for two).
    pub layout: String,
    /// `xkb_variant`.
    pub variant: String,
    /// `xkb_options` (grp:alt_shift_toggle switches the layouts).
    pub options: String,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            hinge_gap: HingeGap::default(),
            scale: DEFAULT_SCALE,
            idle_timeout_s: DEFAULT_IDLE_TIMEOUT_S,
            idle_inhibit: true,
            touch_wake: false,
            power_key_suspend: false,
            full_redraw: false,
            scanout: true,
            edge_swipe: true,
            // Off since 2026-11-08: woken by a wake commit (no planes), a
            // panel left on its black first frame while the other one came up
            // stayed dark (backend::wake_together).
            atomic_wake: false,
            atomic_off: true,
            session_lock: false,
            back_swipe: BackSwipe::Both,
            edge_zone: crate::gesture::EDGE_ZONE,
            animations: true,
            rotate_anim_ms: 320,
            snap: crate::snap::Config::default(),
            stitch: crate::stitch::Config::default(),
            android: crate::android::Config::default(),
            osk_leave: true,
            glance_panels: "DSI-2".to_owned(),
            peek_panels: "DSI-2".to_owned(),
            admin_users: vec!["duo2admin".to_owned()],
            sensor_users: vec!["duo-posed".to_owned()],
            confirm_users: vec!["polkitd".to_owned()],
            external: crate::extout::Config::default(),
            display: crate::mirror::Settings::default(),
            xkb: Xkb::default(),
            laptop: crate::laptop::Config::default(),
            touchpad_drag_lock: false,
            pen: crate::tablet::Settings::default(),
            xwayland: crate::xwayland::XMode::Lazy,
        }
    }
}

impl Config {
    /// Parse a config file's text. Unknown keys and bad values are errors, so a typo
    /// does not look like a setting that does nothing.
    pub fn parse(text: &str) -> Result<Self, String> {
        let mut config = Config::default();
        for (number, raw) in text.lines().enumerate() {
            let line = raw.split('#').next().unwrap_or("").trim();
            if line.is_empty() {
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                return Err(format!("line {}: expected key = value", number + 1));
            };
            let (key, value) = (key.trim(), value.trim());
            match key {
                "hinge_gap" => {
                    config.hinge_gap = HingeGap::parse(value)
                        .ok_or_else(|| format!("line {}: hinge_gap is on or off, not {value}", number + 1))?;
                }
                "scale" => {
                    config.scale = parse_scale(value).map_err(|err| format!("line {}: {err}", number + 1))?;
                }
                "idle_timeout" => {
                    config.idle_timeout_s =
                        parse_idle_timeout(value).map_err(|err| format!("line {}: {err}", number + 1))?;
                }
                "idle_inhibit" => {
                    config.idle_inhibit = parse_switch(value).ok_or_else(|| {
                        format!("line {}: idle_inhibit is on or off, not {value}", number + 1)
                    })?;
                }
                "touch_wake" => {
                    config.touch_wake = parse_switch(value).ok_or_else(|| {
                        format!("line {}: touch_wake is on or off, not {value}", number + 1)
                    })?;
                }
                "full_redraw" => {
                    config.full_redraw = parse_switch(value).ok_or_else(|| {
                        format!("line {}: full_redraw is on or off, not {value}", number + 1)
                    })?;
                }
                "edge_swipe" => {
                    config.edge_swipe = parse_switch(value).ok_or_else(|| {
                        format!("line {}: edge_swipe is on or off, not {value}", number + 1)
                    })?;
                }
                "session_lock" => {
                    config.session_lock = parse_switch(value).ok_or_else(|| {
                        format!("line {}: session_lock is on or off, not {value}", number + 1)
                    })?;
                }
                "atomic_wake" => {
                    config.atomic_wake = parse_switch(value).ok_or_else(|| {
                        format!("line {}: atomic_wake is on or off, not {value}", number + 1)
                    })?;
                }
                "atomic_off" => {
                    config.atomic_off = parse_switch(value).ok_or_else(|| {
                        format!("line {}: atomic_off is on or off, not {value}", number + 1)
                    })?;
                }
                "scanout" => {
                    config.scanout = parse_switch(value)
                        .ok_or_else(|| format!("line {}: scanout is on or off, not {value}", number + 1))?;
                }
                "power_key_suspend" => {
                    config.power_key_suspend = parse_switch(value).ok_or_else(|| {
                        format!("line {}: power_key_suspend is on or off, not {value}", number + 1)
                    })?;
                }
                "back_swipe" => {
                    config.back_swipe = BackSwipe::parse(value).ok_or_else(|| {
                        format!("line {}: back_swipe is both, outer or off, not {value}", number + 1)
                    })?;
                }
                "animations" => {
                    config.animations = parse_switch(value).ok_or_else(|| {
                        format!("line {}: animations is on or off, not {value}", number + 1)
                    })?;
                }
                "rotate_anim_ms" => {
                    config.rotate_anim_ms =
                        value.parse::<u64>().ok().filter(|ms| *ms <= 5000).ok_or_else(|| {
                            format!("line {}: rotate_anim_ms is 0 to 5000, not {value}", number + 1)
                        })?;
                }
                "glance_panels" | "peek_panels" => {
                    let v = value.trim();
                    if v.is_empty() || v.contains(char::is_whitespace) {
                        return Err(format!(
                            "line {}: {key} is both or an output name, not {value}",
                            number + 1
                        ));
                    }
                    if key == "glance_panels" {
                        config.glance_panels = v.to_owned();
                    } else {
                        config.peek_panels = v.to_owned();
                    }
                }
                "admin_users" | "sensor_users" | "confirm_users" => {
                    let names: Vec<String> = value
                        .split(|c: char| c == ',' || c.is_whitespace())
                        .filter(|n| !n.is_empty())
                        .map(str::to_owned)
                        .collect();
                    if names.iter().any(|n| !crate::trust::valid_user_name(n)) {
                        return Err(format!(
                            "line {}: {key} is a list of user names, not {value}",
                            number + 1
                        ));
                    }
                    if key == "admin_users" {
                        config.admin_users = names;
                    } else if key == "confirm_users" {
                        config.confirm_users = names;
                    } else {
                        config.sensor_users = names;
                    }
                }
                "edge_zone" => config.edge_zone = number_in(value, 8.0, 60.0, number, key)?,
                "snap_hinge_band" => config.snap.hinge_band = number_in(value, 0.0, 200.0, number, key)?,
                "snap_half_share" => config.snap.half_share = number_in(value, 0.0, 0.5, number, key)?,
                "snap_pad_from" => config.snap.pad_from = number_in(value, 0.0, 1.0, number, key)?,
                "snap_pad_to" => config.snap.pad_to = number_in(value, 0.0, 1.0, number, key)?,
                "stitch_edge" => config.stitch.edge_band = number_in(value, 4.0, 300.0, number, key)?,
                "stitch_ms" => config.stitch.window_ms = number_in(value, 0.0, 2000.0, number, key)? as u32,
                "stitch_offset" => config.stitch.max_offset = number_in(value, 4.0, 800.0, number, key)?,
                "osk_leave" => {
                    config.osk_leave = parse_switch(value)
                        .ok_or_else(|| format!("line {}: osk_leave is on or off, not {value}", number + 1))?;
                }
                "touchpad_drag_lock" => {
                    config.touchpad_drag_lock = parse_switch(value).ok_or_else(|| {
                        format!("line {}: touchpad_drag_lock is on or off, not {value}", number + 1)
                    })?;
                }
                "pen_button" => {
                    config.pen.button = crate::tablet::PenButton::parse(value).ok_or_else(|| {
                        format!("line {}: pen_button is right_click or off, not {value}", number + 1)
                    })?;
                }
                "pen_dot" => {
                    config.pen.dot = parse_switch(value)
                        .ok_or_else(|| format!("line {}: pen_dot is on or off, not {value}", number + 1))?;
                }
                "pen_input" => {
                    config.pen.input = crate::tablet::PenInput::parse(value).ok_or_else(|| {
                        format!("line {}: pen_input is apps or all, not {value}", number + 1)
                    })?;
                }
                "pen_apps" => config.pen.apps = crate::tablet::parse_apps(value),
                "xwayland" => {
                    config.xwayland = crate::xwayland::XMode::parse(value).ok_or_else(|| {
                        format!("line {}: xwayland is lazy, on or off, not {value}", number + 1)
                    })?;
                }
                "android_caption" => config.android.caption = number_in(value, 0.0, 120.0, number, key)?,
                "android_shadow" => config.android.shadow = number_in(value, 0.0, 60.0, number, key)?,
                "android_task_helper" => {
                    if !value.starts_with('/') {
                        return Err(format!("line {}: android_task_helper is an absolute path", number + 1));
                    }
                    config.android.helper = value.to_owned();
                }
                k if crate::laptop::Config::KEYS.contains(&k) => {
                    config.laptop.set(k, value).map_err(|err| format!("line {}: {err}", number + 1))?
                }
                k if crate::mirror::Settings::KEYS.contains(&k) => {
                    config.display.set(k, value).map_err(|err| format!("line {}: {err}", number + 1))?
                }
                k if crate::extout::Config::KEYS.contains(&k) => {
                    config.external.set(k, value).map_err(|err| format!("line {}: {err}", number + 1))?
                }
                "xkb_model" | "xkb_layout" | "xkb_variant" | "xkb_options" => {
                    let v = value.trim();
                    if !v.chars().all(|c| c.is_ascii_alphanumeric() || "_-,:()+".contains(c)) {
                        return Err(format!(
                            "line {}: {key} has a character xkb names do not use: {value}",
                            number + 1
                        ));
                    }
                    let slot = match key {
                        "xkb_model" => &mut config.xkb.model,
                        "xkb_layout" => &mut config.xkb.layout,
                        "xkb_variant" => &mut config.xkb.variant,
                        _ => &mut config.xkb.options,
                    };
                    *slot = v.to_owned();
                }
                other => return Err(format!("line {}: unknown key {other}", number + 1)),
            }
        }
        Ok(config)
    }

    /// Read the file at `path`. A missing DEFAULT file is the defaults; a missing
    /// file that was asked for by name is an error.
    pub fn load(path: Option<&std::path::Path>) -> Result<Self, String> {
        let (path, required) = match path {
            Some(path) => (path.to_path_buf(), true),
            None => (std::path::PathBuf::from(DEFAULT_PATH), false),
        };
        match std::fs::read_to_string(&path) {
            Ok(text) => Config::parse(&text).map_err(|err| format!("{}: {err}", path.display())),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound && !required => Ok(Config::default()),
            Err(err) => Err(format!("{}: {err}", path.display())),
        }
    }
}

/// A scale from 1 to 4. Kept to the 1/120 steps the fractional-scale protocol
/// can carry, so what a client is told is exactly what the compositor renders at.
pub fn parse_scale(value: &str) -> Result<f64, String> {
    match value.trim().parse::<f64>() {
        Ok(scale) if (1.0..=4.0).contains(&scale) => Ok((scale * 120.0).round() / 120.0),
        _ => Err(format!("scale is a number from 1 to 4, not {value}")),
    }
}

/// Seconds, 0 (never) to a day.
pub fn parse_idle_timeout(value: &str) -> Result<u64, String> {
    match value.trim().parse::<u64>() {
        Ok(seconds) if seconds <= MAX_IDLE_TIMEOUT_S => Ok(seconds),
        _ => {
            Err(format!("idle_timeout is whole seconds from 0 (never) to {MAX_IDLE_TIMEOUT_S}, not {value}"))
        }
    }
}

/// `on` / `off`.
/// A number from `min` to `max` for `key` on line `number` (0-based).
fn number_in(value: &str, min: f64, max: f64, number: usize, key: &str) -> Result<f64, String> {
    match value.trim().parse::<f64>() {
        Ok(v) if (min..=max).contains(&v) => Ok(v),
        _ => Err(format!("line {}: {key} is a number from {min} to {max}, not {value}", number + 1)),
    }
}

fn parse_switch(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "on" | "true" | "yes" | "1" => Some(true),
        "off" | "false" | "no" | "0" => Some(false),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_file_is_the_defaults() {
        assert_eq!(Config::parse("").unwrap(), Config::default());
        assert_eq!(Config::default().hinge_gap, HingeGap::On);
        assert_eq!(Config::default().scale, 2.5);
        assert_eq!(Config::default().idle_timeout_s, 60);
        assert!(!Config::default().touch_wake);
        assert!(!Config::default().power_key_suspend);
    }

    #[test]
    fn power_settings() {
        let config = Config::parse("idle_timeout = 0\ntouch_wake = on\npower_key_suspend = off\n").unwrap();
        assert_eq!(config.idle_timeout_s, 0);
        assert!(config.touch_wake);
        assert!(!config.power_key_suspend);
        assert_eq!(Config::parse("idle_timeout = 300").unwrap().idle_timeout_s, 300);
        assert!(Config::parse("idle_timeout = -1").is_err());
        assert!(Config::parse("idle_timeout = 1.5").is_err());
        assert!(Config::parse("idle_timeout = 999999").is_err());
        assert!(Config::parse("touch_wake = sometimes").is_err());
    }

    #[test]
    fn idle_inhibit_is_on_unless_switched_off() {
        assert!(Config::default().idle_inhibit);
        assert!(!Config::parse("idle_inhibit = off").unwrap().idle_inhibit);
        assert!(Config::parse("idle_inhibit = video").is_err());
    }

    #[test]
    fn session_lock_is_off_unless_switched_on() {
        assert!(!Config::default().session_lock);
        assert!(Config::parse("session_lock = on").unwrap().session_lock);
    }

    #[test]
    fn atomic_off_is_on_unless_switched_off() {
        assert!(Config::default().atomic_off);
        assert!(!Config::parse("atomic_off = off").unwrap().atomic_off);
        assert!(Config::parse("atomic_off = maybe").is_err());
    }

    #[test]
    fn atomic_wake_is_off_unless_switched_on() {
        assert!(!Config::default().atomic_wake);
        assert!(Config::parse("atomic_wake = on").unwrap().atomic_wake);
        assert!(!Config::parse("atomic_wake = off").unwrap().atomic_wake);
        assert!(Config::parse("atomic_wake = sometimes").is_err());
    }

    #[test]
    fn edge_swipes_are_on_unless_switched_off() {
        assert!(Config::default().edge_swipe);
        assert!(!Config::parse("edge_swipe = off").unwrap().edge_swipe);
        assert!(Config::parse("edge_swipe = maybe").is_err());
    }

    #[test]
    fn gestures_snapping_and_stitching() {
        let c = Config::parse("").unwrap();
        assert_eq!(c.back_swipe, BackSwipe::Both);
        assert!(c.animations);
        assert_eq!(c.stitch.window_ms, 350);
        assert_eq!((c.snap.pad_from, c.snap.pad_to), (0.58, 0.80));
        assert_eq!(Config::parse("snap_pad_from = 0.1\nsnap_pad_to = 0.3").unwrap().snap.pad_to, 0.3);
        assert!(Config::parse("snap_pad_to = 1.5").is_err());
        let c = Config::parse(
            "back_swipe = outer\nanimations = off\nedge_zone = 30\nsnap_hinge_band = 60\n\
             snap_half_share = 0.3\nstitch_edge = 80\nstitch_ms = 300\nstitch_offset = 120\n",
        )
        .unwrap();
        assert_eq!(c.back_swipe, BackSwipe::Outer);
        assert!(!c.animations);
        assert_eq!(Config::parse("").unwrap().rotate_anim_ms, 320);
        assert_eq!(Config::parse("rotate_anim_ms = 0").unwrap().rotate_anim_ms, 0);
        assert!(Config::parse("rotate_anim_ms = 9000").is_err());
        assert_eq!(c.edge_zone, 30.0);
        assert_eq!(c.snap.hinge_band, 60.0);
        assert_eq!(c.snap.half_share, 0.3);
        assert_eq!(c.stitch.edge_band, 80.0);
        assert_eq!(c.stitch.window_ms, 300);
        assert_eq!(c.stitch.max_offset, 120.0);
        assert!(Config::parse("back_swipe = sideways").is_err());
        assert!(Config::parse("stitch_ms = -1").is_err());
        assert!(Config::parse("snap_half_share = 0.9").is_err());
        assert_eq!(BackSwipe::parse("off"), Some(BackSwipe::Off));
        let c = Config::parse("").unwrap();
        assert_eq!((c.glance_panels.as_str(), c.peek_panels.as_str()), ("DSI-2", "DSI-2"));
        assert_eq!(c.android, crate::android::Config::default());
        assert!(c.osk_leave);
        assert!(!Config::parse("osk_leave = off").unwrap().osk_leave);
        let a =
            Config::parse("android_caption = 38\nandroid_shadow = 8\nandroid_task_helper = /x/y\n").unwrap();
        assert_eq!((a.android.caption, a.android.shadow, a.android.helper.as_str()), (38.0, 8.0, "/x/y"));
        assert!(Config::parse("android_task_helper = relative").is_err());
        assert!(Config::parse("android_caption = 500").is_err());
        let c = Config::parse("glance_panels = both\npeek_panels = both\n").unwrap();
        assert_eq!((c.glance_panels.as_str(), c.peek_panels.as_str()), ("both", "both"));
    }

    #[test]
    fn keys_values_and_comments() {
        let config = Config::parse("# duo\nhinge_gap = off  # adjacent\n\nscale=2\n").unwrap();
        assert_eq!(config, Config { hinge_gap: HingeGap::Off, scale: 2.0, ..Config::default() });
    }

    #[test]
    fn typos_are_refused() {
        assert!(Config::parse("hinge-gap = off").is_err());
        assert!(Config::parse("hinge_gap = sometimes").is_err());
        assert!(Config::parse("scale = 0.5").is_err());
        assert!(Config::parse("scale = 4.5").is_err());
        assert!(Config::parse("scale = big").is_err());
        assert!(Config::parse("just words").is_err());
    }

    #[test]
    fn fractional_scales_are_kept() {
        assert_eq!(parse_scale("2.5"), Ok(2.5));
        assert_eq!(parse_scale("1.75"), Ok(1.75));
    }

    #[test]
    fn a_missing_default_file_is_fine_but_a_named_one_is_not() {
        let missing = std::path::Path::new("/nonexistent/duo-wc.conf");
        assert!(Config::load(Some(missing)).is_err());
    }

    #[test]
    fn external_display_and_keyboard_layout_keys() {
        let c = Config::parse(
            "external_scale = 1.25\nexternal_mode = 1920x1080@60\nexternal_idle_timeout = 0\n\
             xkb_layout = hu,us\nxkb_options = grp:alt_shift_toggle\n",
        )
        .unwrap();
        assert_eq!(c.external.scale, Some(1.25));
        assert_eq!(c.external.idle_timeout_s, Some(0));
        assert_eq!(c.xkb.layout, "hu,us");
        assert_eq!(c.xkb.options, "grp:alt_shift_toggle");
        assert!(c.external.enabled);
        assert!(Config::parse("external_scale = big\n").is_err());
        assert!(Config::parse("xkb_layout = hu us\n").is_err());
        // The defaults: a display is used, its idle timeout is ten minutes.
        assert_eq!(Config::default().external, crate::extout::Config::default());
        assert_eq!(Config::default().xkb, Xkb::default());
        let c = Config::parse("laptop_auto = on\nlaptop_keys = DSI-1\n").unwrap();
        assert_eq!(c.laptop, crate::laptop::Config { auto: true, keys: "DSI-1".into() });
        assert!(!c.touchpad_drag_lock);
        assert!(Config::parse("touchpad_drag_lock = on\n").unwrap().touchpad_drag_lock);
        assert!(Config::parse("touchpad_drag_lock = maybe\n").is_err());
    }

    #[test]
    fn the_pen_settings() {
        let c = Config::default();
        assert_eq!(c.pen.button, crate::tablet::PenButton::RightClick);
        assert!(c.pen.dot);
        let c = Config::parse("pen_button = off\npen_dot = off\n").unwrap();
        assert_eq!(c.pen.button, crate::tablet::PenButton::Off);
        assert!(!c.pen.dot);
        assert!(Config::parse("pen_button = middle\n").is_err());
        assert!(Config::parse("pen_dot = sometimes\n").is_err());
        assert_eq!(c.pen.input, crate::tablet::PenInput::Apps);
        assert!(c.pen.apps.iter().any(|a| a == "rnote"));
        let c = Config::parse("pen_input = all\npen_apps = krita, com.example.Sketch\n").unwrap();
        assert_eq!(c.pen.input, crate::tablet::PenInput::All);
        assert_eq!(c.pen.apps, vec!["krita", "com.example.Sketch"]);
        assert!(Config::parse("pen_apps =\n").unwrap().pen.apps.is_empty());
        assert!(Config::parse("pen_input = some\n").is_err());
        assert_eq!(Config::default().xwayland, crate::xwayland::XMode::Lazy);
        assert_eq!(Config::parse("xwayland = off\n").unwrap().xwayland, crate::xwayland::XMode::Off);
        assert!(Config::parse("xwayland = maybe\n").is_err());
        let d = Config::parse("external_on_connect = mirror\nexternal_mirror = both\n").unwrap();
        assert_eq!(d.display.on_connect, crate::mirror::OnConnect::Mirror);
        assert_eq!(d.display.mirror, crate::mirror::MirrorSource::Both);
        assert_eq!(Config::default().display.on_connect, crate::mirror::OnConnect::Ask);
        assert!(Config::parse("external_on_connect = sometimes\n").is_err());
        assert!(Config::parse("laptop_keys = DP-1\n").is_err());
    }
}
