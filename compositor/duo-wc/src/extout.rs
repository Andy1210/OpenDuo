//! External displays: a monitor or a TV on the USB-C port (DisplayPort alt
//! mode, through a USB-C to HDMI / DP adapter), the desktop mode.
//!
//! The panels never hotplug; an external display does. The kernel reports
//! it as a third connector (DP-1) that becomes connected when the adapter
//! is plugged and a sink answers (HPD from the ADSP's alt mode handling),
//! with a udev "change" event on the DRM device; duo-wc then scans the
//! connectors ([`DuoWc::ext_scan`], debounced) and:
//!
//! - **connected:** picks a mode ([`choose_mode`]: the preferred one, unless
//!   it is slower than 50 Hz and a smooth one exists), a free CRTC (never
//!   one of the panels', lit or not), a scale ([`auto_scale`] from the
//!   size the display reports, or `external_scale`), brings the output up
//!   beside the panels (to their right, at the top: [`crate::layout`]),
//!   announces it as a `wl_output` and lights it by the power rules (it is
//!   dark when the screen is blanked, lit otherwise, whatever the pose);
//! - **disconnected:** every window on it goes back to a panel (the one it
//!   came from if that is in use, else the active one, [`home_for`]), its
//!   layer surfaces are told to close, the CRTC is switched off and the
//!   output and its global are gone.
//!
//! On the display the windows float ([`crate::desk`]): each has its own
//! rectangle, the pointer moves and resizes them ([`crate::pointer`]), the
//! keyboard too (Super + arrows; Super + Shift + left / right moves the
//! focused window to the output beside its own, panels included). The
//! panels go on as a phone meanwhile; the poses, the overview, the splits
//! and the span are theirs alone ([`crate::pose::Pose::in_use`] is false
//! for a display, [`DuoWc::is_lit`] asks the power rules without the pose).
//!
//! While a display is connected, the idle timeout is `external_idle_timeout`
//! (10 minutes by default) instead of the panels' minute.

use std::collections::HashMap;
use std::time::Duration;

use anyhow::{anyhow, Context as _};
use smithay::desktop::{layer_map_for_output, Window};
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::RegistrationToken;
use smithay::reexports::drm::control::{connector, crtc, Device as _, ModeFlags, ModeTypeFlags};
use smithay::reexports::wayland_protocols::xdg::decoration::zv1::server::zxdg_toplevel_decoration_v1::Mode as DecorationMode;
use smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel;
use smithay::reexports::wayland_server::backend::GlobalId;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::utils::{Logical, Rectangle};
use tracing::{info, warn};

use crate::desk::{Arrow, Frame};
use crate::state::{window_data, DuoWc, Placement};

/// Whether an output is one of the phone's panels.
pub fn is_panel(name: &str) -> bool {
    name.starts_with("DSI-")
}

/// Whether an output is an external display (anything that is not a
/// panel; the writeback connector is never an output here).
pub fn is_external(name: &str) -> bool {
    !name.is_empty() && !is_panel(name) && !name.starts_with("Writeback") && !name.starts_with("Virtual")
}

/// How long the connectors settle after a hotplug event before they are
/// scanned (an HPD that bounces, the EDID read after it).
pub const SETTLE: Duration = Duration::from_millis(300);

/// The default idle timeout while a display is connected.
pub const DEFAULT_IDLE_TIMEOUT_S: u64 = 600;

/// The mode wanted for a display (`external_mode`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ModeWish {
    /// The display's preferred mode, unless it is slower than 50 Hz and a
    /// mode of at least 50 Hz exists (a 4K TV offering 30 Hz: the largest
    /// smooth mode instead).
    #[default]
    Auto,
    /// The display's preferred mode, whatever its refresh rate.
    Preferred,
    /// This size (and refresh rate, Hz, if given); the automatic choice if
    /// the display has no such mode.
    Size {
        /// Width, px.
        w: u16,
        /// Height, px.
        h: u16,
        /// Refresh rate, Hz (None: the fastest of that size).
        refresh: Option<u32>,
    },
}

impl ModeWish {
    /// `auto`, `preferred`, `1920x1080`, `1920x1080@60`.
    pub fn parse(value: &str) -> Option<ModeWish> {
        let v = value.trim().to_ascii_lowercase();
        match v.as_str() {
            "auto" => return Some(ModeWish::Auto),
            "preferred" => return Some(ModeWish::Preferred),
            _ => {}
        }
        let (size, refresh) = match v.split_once('@') {
            Some((size, refresh)) => (size, Some(refresh.trim().parse::<u32>().ok()?)),
            None => (v.as_str(), None),
        };
        let (w, h) = size.split_once('x')?;
        let (w, h) = (w.trim().parse::<u16>().ok()?, h.trim().parse::<u16>().ok()?);
        (w > 0 && h > 0).then_some(ModeWish::Size { w, h, refresh })
    }
}

/// What [`choose_mode`] needs of a mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModeInfo {
    /// Width, px.
    pub w: u16,
    /// Height, px.
    pub h: u16,
    /// Hz.
    pub refresh: u32,
    /// The display's preferred mode.
    pub preferred: bool,
    /// An interlaced mode (never chosen).
    pub interlaced: bool,
}

impl ModeInfo {
    /// What a DRM mode is.
    pub fn of(mode: &smithay::reexports::drm::control::Mode) -> ModeInfo {
        let (w, h) = mode.size();
        ModeInfo {
            w,
            h,
            refresh: mode.vrefresh(),
            preferred: mode.mode_type().contains(ModeTypeFlags::PREFERRED),
            interlaced: mode.flags().contains(ModeFlags::INTERLACE),
        }
    }
}

/// The smallest refresh rate [`ModeWish::Auto`] calls smooth.
pub const SMOOTH_HZ: u32 = 50;

/// Which of a display's modes to use (an index into `modes`, the
/// connector's list, which the kernel has already cut to what the link
/// carries). Interlaced modes are never chosen.
pub fn choose_mode(modes: &[ModeInfo], wish: &ModeWish) -> Option<usize> {
    let usable: Vec<usize> = (0..modes.len()).filter(|&i| !modes[i].interlaced).collect();
    if usable.is_empty() {
        return None;
    }
    let preferred = usable.iter().copied().find(|&i| modes[i].preferred).unwrap_or(usable[0]);
    match wish {
        ModeWish::Preferred => Some(preferred),
        ModeWish::Size { w, h, refresh } => {
            let exact = usable.iter().copied().filter(|&i| modes[i].w == *w && modes[i].h == *h);
            let found = match refresh {
                Some(r) => exact.min_by_key(|&i| modes[i].refresh.abs_diff(*r)),
                None => exact.max_by_key(|&i| (modes[i].refresh, modes[i].preferred)),
            };
            found.or_else(|| choose_mode(modes, &ModeWish::Auto))
        }
        ModeWish::Auto => {
            let p = modes[preferred];
            if p.refresh >= SMOOTH_HZ {
                return Some(preferred);
            }
            // The largest smooth mode no larger than the preferred one (the
            // panel's own size), the faster of two equal ones.
            usable
                .iter()
                .copied()
                .filter(|&i| modes[i].refresh >= SMOOTH_HZ && modes[i].w <= p.w && modes[i].h <= p.h)
                .max_by_key(|&i| (modes[i].w as u32 * modes[i].h as u32, modes[i].refresh))
                .or(Some(preferred))
        }
    }
}

/// The scale for a display of `px` physical pixels that reports `mm`
/// millimetres (EDID; None or tiny: unknown):
///
/// - a TV (40" and up, wider than 900 mm): by its pixels alone, as a
///   1080p screen seen from a sofa: 1 at 1080p, 2 at 2160p;
/// - a monitor: its density over 110 dpi (a 24" 1080p, a 27" 1440p: 1; a
///   27" 4K: 1.5; a 15.6" 4K portable: 2.5);
/// - unknown: 2 from 2160 lines up, else 1.
///
/// Rounded to quarters, from 1 to 3.
pub fn auto_scale(px: (u16, u16), mm: Option<(u32, u32)>) -> f64 {
    let (w, h) = (px.0 as f64, px.1 as f64);
    let raw = match mm.filter(|(wm, hm)| *wm >= 100 && *hm >= 60) {
        Some((wm, _)) if wm > 900 => h / 1080.0,
        Some((wm, _)) => (w / (wm as f64 / 25.4)) / 110.0,
        None => {
            if h >= 2000.0 {
                2.0
            } else {
                1.0
            }
        }
    };
    ((raw * 4.0).round() / 4.0).clamp(1.0, 3.0)
}

/// The display's name from its EDID (the monitor name descriptor, 0xFC),
/// if it has one.
pub fn edid_name(edid: &[u8]) -> Option<String> {
    if edid.len() < 128 || edid[0..8] != [0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00] {
        return None;
    }
    for d in 0..4 {
        let at = 54 + d * 18;
        let desc = &edid[at..at + 18];
        if desc[0] == 0 && desc[1] == 0 && desc[3] == 0xfc {
            let text: String = desc[5..18]
                .iter()
                .take_while(|&&b| b != 0x0a && b != 0)
                .map(|&b| if b.is_ascii_graphic() || b == b' ' { b as char } else { '?' })
                .collect();
            let text = text.trim().to_owned();
            return (!text.is_empty()).then_some(text);
        }
    }
    None
}

/// Where a window goes when its display goes away: the panel it came from
/// if that one is lit, else the active output if it is a lit panel, else
/// the first lit panel; none when no panel is lit (it waits for one,
/// placed on the active output's name all the same by the caller).
pub fn home_for(came_from: Option<&str>, lit_panels: &[String], active: Option<&str>) -> Option<String> {
    let lit = |n: &str| lit_panels.iter().any(|p| p == n);
    came_from
        .filter(|n| lit(n))
        .or(active.filter(|n| is_panel(n) && lit(n)))
        .map(str::to_owned)
        .or_else(|| lit_panels.first().cloned())
}

/// The external displays' settings (duo-wc.conf).
#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// `external = on | off`: whether a display on the USB-C port is used.
    pub enabled: bool,
    /// `external_scale = auto | 1 .. 4`.
    pub scale: Option<f64>,
    /// `external_mode = auto | preferred | WxH[@Hz]`.
    pub mode: ModeWish,
    /// `external_idle_timeout = seconds | panels` (0: never; `panels`: the
    /// panels' `idle_timeout`).
    pub idle_timeout_s: Option<u64>,
    /// `external_trackpad = off | DSI-1 | DSI-2`: a panel as the display's
    /// touchpad while one is up (crate::trackpad).
    pub trackpad: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            enabled: true,
            scale: None,
            mode: ModeWish::Auto,
            idle_timeout_s: Some(DEFAULT_IDLE_TIMEOUT_S),
            trackpad: None,
        }
    }
}

impl Config {
    /// The keys this takes.
    pub const KEYS: &'static [&'static str] =
        &["external", "external_scale", "external_mode", "external_idle_timeout", "external_trackpad"];

    /// Set a key from its value in the file.
    pub fn set(&mut self, key: &str, value: &str) -> Result<(), String> {
        let v = value.trim();
        match key {
            "external" => {
                self.enabled = match v.to_ascii_lowercase().as_str() {
                    "on" | "true" | "yes" | "1" => true,
                    "off" | "false" | "no" | "0" => false,
                    _ => return Err(format!("external is on or off, not {value}")),
                };
            }
            "external_scale" => {
                self.scale = if v.eq_ignore_ascii_case("auto") {
                    None
                } else {
                    match v.parse::<f64>() {
                        Ok(s) if (1.0..=4.0).contains(&s) => Some(s),
                        _ => {
                            return Err(format!(
                                "external_scale is auto or a number from 1 to 4, not {value}"
                            ))
                        }
                    }
                };
            }
            "external_mode" => {
                self.mode = ModeWish::parse(v)
                    .ok_or_else(|| format!("external_mode is auto, preferred or WxH[@Hz], not {value}"))?;
            }
            "external_idle_timeout" => {
                self.idle_timeout_s = if v.eq_ignore_ascii_case("panels") {
                    None
                } else {
                    match v.parse::<u64>() {
                        Ok(s) if s <= 86_400 => Some(s),
                        _ => {
                            return Err(format!(
                                "external_idle_timeout is seconds (0 to 86400) or panels, not {value}"
                            ))
                        }
                    }
                };
            }
            "external_trackpad" => {
                self.trackpad = match v {
                    "off" | "" => None,
                    panel if is_panel(panel) => Some(panel.to_owned()),
                    _ => return Err(format!("external_trackpad is off, DSI-1 or DSI-2, not {value}")),
                };
            }
            other => return Err(format!("not an external display key: {other}")),
        }
        Ok(())
    }
}

/// The external displays' state.
#[derive(Debug, Default)]
pub struct Ext {
    /// The settings.
    pub config: Config,
    /// The `wl_output` global of each display that is up.
    pub globals: HashMap<String, GlobalId>,
    /// The name each display gives itself (EDID), for the log and the
    /// control socket.
    pub names: HashMap<String, String>,
    /// The panel each window on a display came from (where it goes back).
    pub homes: HashMap<u64, String>,
    /// The settle timer after a hotplug event.
    pub settle: Option<RegistrationToken>,
    /// The panels' idle timeout (seconds), kept while a display has its own.
    pub panels_idle_s: Option<u64>,
    /// A display as a desk or a mirror: the settings (crate::mirror).
    pub display: crate::mirror::Settings,
    /// What each connected display is used as.
    pub uses: HashMap<String, crate::mirror::DisplayUse>,
    /// The displays the shell is to ask about (they mirror meanwhile).
    pub asking: std::collections::HashSet<String>,
    /// The panels each mirror showed in its last frame.
    pub mirror_shown: HashMap<String, Vec<String>>,
    /// The mirror's textures (crate::mirrorlive).
    pub mirror_gfx: crate::mirrorlive::MirrorGfx,
}

/// A window's minimum size as its client last committed it (0 = none).
pub fn min_size(window: &Window) -> (i32, i32) {
    if let Some(x11) = window.x11_surface() {
        return x11.min_size().map(|s| (s.w.max(0), s.h.max(0))).unwrap_or((0, 0));
    }
    let Some(toplevel) = window.toplevel() else {
        return (0, 0);
    };
    smithay::wayland::compositor::with_states(toplevel.wl_surface(), |states| {
        let size =
            states.cached_state.get::<smithay::wayland::shell::xdg::SurfaceCachedState>().current().min_size;
        (size.w.max(0), size.h.max(0))
    })
}

/// Where a desk window with `frame` is shown on the output at `g`
/// (global): the frame as shown in `area` (the room there, the output's own
/// frame), the whole output when it is fullscreen.
fn desk_shown(
    g: Rectangle<i32, Logical>,
    area: crate::layout::Rect,
    frame: &Frame,
    fullscreen: bool,
) -> Rectangle<i32, Logical> {
    let local = if fullscreen { (0, 0, g.size.w, g.size.h) } else { frame.shown(area) };
    Rectangle::new((g.loc.x + local.0, g.loc.y + local.1).into(), (local.2, local.3).into())
}

impl DuoWc {
    /// Whether a display is up.
    pub fn ext_connected(&self) -> bool {
        self.outputs.iter().any(|o| is_external(&o.name()))
    }

    /// A udev change on the DRM device: scan the connectors once they have
    /// settled.
    pub fn ext_hotplug(&mut self) {
        if let Some(token) = self.ext.settle.take() {
            self.loop_handle.remove(token);
        }
        let inserted =
            self.loop_handle.insert_source(Timer::from_duration(SETTLE), |_, _, state: &mut DuoWc| {
                state.ext.settle = None;
                state.ext_scan("hotplug");
                TimeoutAction::Drop
            });
        match inserted {
            Ok(token) => self.ext.settle = Some(token),
            Err(err) => {
                warn!(?err, "no settle timer; scanning now");
                self.ext_scan("hotplug");
            }
        }
    }

    /// Bring the external displays in line with the connectors: up for a
    /// connected one that is not up, gone for one that is up and is no
    /// longer connected.
    pub fn ext_scan(&mut self, why: &str) {
        let seen = match self.ext_connectors() {
            Ok(seen) => seen,
            Err(err) => {
                warn!(?err, why, "could not scan the connectors");
                return;
            }
        };
        let up: Vec<String> = self.outputs.iter().map(|o| o.name()).filter(|n| is_external(n)).collect();
        for name in &up {
            if !seen.iter().any(|(n, _, connected)| n == name && *connected) {
                self.ext_remove(name, why);
            }
        }
        if !self.ext.config.enabled {
            return;
        }
        for (name, handle, connected) in seen {
            if connected && !up.contains(&name) {
                if let Err(err) = self.ext_add(&name, handle) {
                    warn!(output = name, ?err, "could not bring the display up");
                }
            }
        }
    }

    /// The external connectors: (name, handle, connected). A connected one
    /// is probed (its modes, its EDID); the panels are not touched.
    fn ext_connectors(&self) -> anyhow::Result<Vec<(String, connector::Handle, bool)>> {
        let device = self.tty.device.as_ref().ok_or_else(|| anyhow!("no device opened"))?;
        let resources = device.drm.resource_handles().context("resource_handles")?;
        let mut out = Vec::new();
        for handle in resources.connectors() {
            // Not probed first: the interface name says whether it is ours.
            let Ok(info) = device.drm.get_connector(*handle, false) else {
                continue;
            };
            let name = format!("{}-{}", info.interface().as_str(), info.interface_id());
            if !is_external(&name) {
                continue;
            }
            let Ok(info) = device.drm.get_connector(*handle, true) else {
                continue;
            };
            out.push((name, *handle, info.state() == connector::State::Connected));
        }
        Ok(out)
    }

    fn ext_add(&mut self, name: &str, handle: connector::Handle) -> anyhow::Result<()> {
        let wish = self.ext.config.mode.clone();
        let (mode, crtc, mm, edid) = {
            let device = self.tty.device.as_ref().ok_or_else(|| anyhow!("no device opened"))?;
            let info = device.drm.get_connector(handle, false).context("get_connector")?;
            let modes: Vec<ModeInfo> = info.modes().iter().map(ModeInfo::of).collect();
            let index = choose_mode(&modes, &wish).ok_or_else(|| anyhow!("connected but no usable mode"))?;
            let mode = info.modes()[index];
            let resources = device.drm.resource_handles().context("resource_handles")?;
            // Never a panel's CRTC, lit or off: each panel keeps its own.
            let used: Vec<crtc::Handle> = device.surfaces.iter().map(|s| s.crtc).collect();
            let crtc = info
                .encoders()
                .iter()
                .filter_map(|e| device.drm.get_encoder(*e).ok())
                .flat_map(|e| resources.filter_crtcs(e.possible_crtcs()))
                .find(|c| !used.contains(c))
                .ok_or_else(|| anyhow!("no free CRTC"))?;
            let edid = crate::backend::connector_blob(&device.drm, handle, "EDID");
            (mode, crtc, info.size(), edid)
        };
        let label = edid.as_deref().and_then(edid_name);
        let scale = self.ext.config.scale.unwrap_or_else(|| auto_scale(mode.size(), mm));
        info!(
            output = name,
            display = label.as_deref().unwrap_or("?"),
            mode = format!("{}x{}@{}", mode.size().0, mode.size().1, mode.vrefresh()),
            ?crtc,
            scale,
            mm = ?mm,
            "display connected"
        );
        let output = self.tty.add_surface(crate::backend::NewSurface {
            name: name.to_owned(),
            crtc,
            connector: handle,
            mode,
            takeover: crate::backend::Takeover::Off,
            physical_mm: mm.unwrap_or((0, 0)),
            make: "External".into(),
            model: label.clone().unwrap_or_else(|| name.to_owned()),
            scale,
            // Off until the power rules light it (apply_power): the first
            // frame after that is its modeset.
            lit: false,
        })?;
        let global = output.create_global::<DuoWc>(&self.display_handle);
        self.ext.globals.insert(name.to_owned(), global);
        if let Some(label) = label.clone() {
            self.ext.names.insert(name.to_owned(), label);
        }
        self.space.map_output(&output, (0, 0));
        self.outputs.push(output);
        // A desk or a mirror (external_on_connect; asking: it mirrors
        // until the shell's answer, crate::mirror).
        let (used, asking) = self.display_connected(name);
        self.arrange_outputs();
        // The idle timeout of a desk, not of a phone in a pocket.
        self.desk_idle_update();
        self.events_dirty = true;
        self.apply_power();
        if let (Some(panel), crate::mirror::DisplayUse::Desk) = (self.ext.config.trackpad.clone(), used) {
            if self.trackpad.panel.is_none() {
                if let Err(err) = self.set_trackpad(Some(panel)) {
                    warn!(?err, "no touchpad panel");
                }
            }
        }
        self.queue_redraw_all();
        crate::ipc::broadcast(
            self,
            &serde_json::json!({
                "event": "display", "output": name, "connected": true, "name": label,
                "use": used.name(), "asking": asking,
            }),
        );
        Ok(())
    }

    /// A display went away (or is switched off for good): its windows go
    /// back to the panels, the rest of it is gone.
    pub fn ext_remove(&mut self, name: &str, why: &str) {
        let Some(output) = self.output_by_name(name) else {
            return;
        };
        info!(output = name, why, "display disconnected");
        // A drag in progress ends (its window may be one of these).
        self.desk.drag = None;
        // Its windows: back to a panel.
        let lit_panels: Vec<String> = self
            .layout_order()
            .into_iter()
            .map(|o| o.name())
            .filter(|n| is_panel(n) && self.is_lit(n))
            .collect();
        let active = self.active_output.clone();
        let on_it: Vec<Window> = self
            .space
            .elements()
            .filter(|w| *window_data(w).placement.borrow() == Placement::Output(name.to_owned()))
            .cloned()
            .collect();
        for window in on_it {
            let id = window_data(&window).id;
            let came_from = self.ext.homes.remove(&id);
            let target = home_for(came_from.as_deref(), &lit_panels, active.as_deref())
                .or(came_from)
                .or_else(|| self.layout_order().into_iter().map(|o| o.name()).find(|n| is_panel(n)));
            if let Some(target) = target {
                info!(id, to = target, "window back from the display");
                *window_data(&window).fullscreen.borrow_mut() = false;
                self.set_placement(&window, Placement::Output(target));
            }
        }
        // Its layer surfaces (the shell's panel there) are told to close.
        let layers: Vec<_> = layer_map_for_output(&output).layers().cloned().collect();
        for layer in layers {
            layer_map_for_output(&output).unmap_layer(&layer);
            layer.layer_surface().send_close();
        }
        // The scan-out: CRTC off, then the surface and the output are gone.
        self.tty.remove_surface(name);
        output.leave_all();
        if let Some(global) = self.ext.globals.remove(name) {
            self.display_handle.remove_global::<DuoWc>(global);
        }
        self.ext.names.remove(name);
        self.display_gone(name);
        self.space.unmap_output(&output);
        self.outputs.retain(|o| o.name() != name);
        if self.active_output.as_deref() == Some(name) {
            self.active_output = self
                .layout_order()
                .into_iter()
                .map(|o| o.name())
                .find(|n| is_panel(n) && self.is_lit(n))
                .or_else(|| self.layout_order().first().map(|o| o.name()));
        }
        // The pointer, if it was there, to the active panel.
        let pointer_lost = self.pointer_output().is_none();
        if pointer_lost {
            if let Some(g) = self.active_output().and_then(|o| self.space.output_geometry(&o)) {
                self.pointer.location =
                    (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
            }
        }
        self.desk_idle_update();
        self.arrange_outputs();
        self.trackpad_display_gone();
        self.refresh_keyboard_focus();
        self.events_dirty = true;
        self.apply_power();
        self.queue_redraw_all();
        crate::ipc::broadcast(
            self,
            &serde_json::json!({ "event": "display", "output": name, "connected": false }),
        );
    }

    /// The idle timeout of a desk (`external_idle_timeout`) while an
    /// external display is up or laptop mode is on; the panels' own when
    /// neither is.
    pub fn desk_idle_update(&mut self) {
        let desk = self.ext_connected() || self.laptop_on();
        match (desk, self.ext.panels_idle_s) {
            (true, None) => {
                if let Some(seconds) = self.ext.config.idle_timeout_s {
                    self.ext.panels_idle_s = Some(self.idle.timeout().map(|t| t.as_secs()).unwrap_or(0));
                    self.set_idle_timeout(seconds);
                }
            }
            (false, Some(seconds)) => {
                self.ext.panels_idle_s = None;
                self.set_idle_timeout(seconds);
            }
            _ => {}
        }
    }

    /// A window goes onto a display from a panel: remember the panel (it
    /// goes back there when the display goes away).
    pub fn ext_remember_home(&mut self, window: &Window) {
        if let Placement::Output(name) = &*window_data(window).placement.borrow() {
            if is_panel(name) {
                self.ext.homes.insert(window_data(window).id, name.clone());
            }
        }
    }

    /// The scale a surface should draw at: its output's (a display's own,
    /// the panels' otherwise).
    pub fn scale_for_surface(&self, root: &WlSurface) -> f64 {
        if !self.ext_connected() {
            return self.scale;
        }
        let output = self
            .window_for_surface(root)
            .and_then(|w| match &*window_data(&w).placement.borrow() {
                Placement::Output(name) => self.output_by_name(name),
                Placement::Span => None,
            })
            .or_else(|| {
                self.outputs
                    .iter()
                    .find(|o| {
                        layer_map_for_output(o)
                            .layer_for_surface(root, smithay::desktop::WindowSurfaceType::ALL)
                            .is_some()
                    })
                    .cloned()
            });
        match output {
            Some(o) if is_external(&o.name()) => o.current_scale().fractional_scale(),
            _ => self.scale,
        }
    }

    /// Where a window of a desk is shown (global): its frame there,
    /// maximized or a half when it is snapped, the display whole when it is
    /// fullscreen ([`DuoWc::desk_place`] put it there). None: not a desk
    /// window, or it has no frame yet.
    pub fn desk_rect(&self, window: &Window) -> Option<Rectangle<i32, Logical>> {
        if !self.on_desk(window) {
            return None;
        }
        let Placement::Output(name) = &*window_data(window).placement.borrow() else {
            return None;
        };
        let output = self.output_by_name(name)?;
        let g = self.space.output_geometry(&output)?;
        let zone = self.zone_without_osk(&output);
        let area = (zone.loc.x, zone.loc.y, zone.size.w, zone.size.h);
        let frame = self.desk.frames.get(&window_data(window).id)?;
        Some(desk_shown(g, area, frame, *window_data(window).fullscreen.borrow()))
    }

    /// Place a window of a display's desk (state::place): its own frame
    /// there, maximized or a half when it is snapped, the display whole
    /// when it is fullscreen. False: not a desk window.
    pub fn desk_place(&mut self, window: &Window, placement: &Placement, fullscreen: bool) -> bool {
        let Placement::Output(name) = placement else {
            return false;
        };
        if !self.on_desk(window) {
            // Not a desk, or an Android window (one Android display, one
            // size: it fills its output as on a panel, crate::android).
            return false;
        }
        let Some(output) = self.output_by_name(name) else {
            return false;
        };
        let Some(g) = self.space.output_geometry(&output) else {
            return false;
        };
        let zone = self.zone_without_osk(&output);
        let area = (zone.loc.x, zone.loc.y, zone.size.w, zone.size.h);
        let id = window_data(window).id;
        // A window new on the desk opens at the desk's default size (one
        // carried over from a panel would otherwise keep a panel's size);
        // on laptop mode's desk panel, a small screen, most of it.
        let wanted = is_panel(name).then_some((
            (area.2 as f64 * crate::desk::PANEL_SHARE.0) as i32,
            (area.3 as f64 * crate::desk::PANEL_SHARE.1) as i32,
        ));
        let frame: Frame = {
            let frame = self.desk.frame_or_new(id, area, wanted);
            // Kept reachable on this desk (a frame made on a bigger one, a
            // turned panel).
            frame.rect = crate::desk::keep_inside(frame.rect, area);
            frame.clone()
        };
        // Never scaled down to fit here (crate::dialog): it floats.
        *window_data(window).fit.borrow_mut() = None;
        *window_data(window).fit_from.borrow_mut() = None;
        let rect = desk_shown(g, area, &frame, fullscreen);
        if let Some(toplevel) = window.toplevel() {
            toplevel.with_pending_state(|state| {
                state.size = Some(rect.size);
                // On a desk a window draws its own title bar (to be taken by
                // the pointer); on a panel it has none (state::decorate).
                if state.decoration_mode.is_some() {
                    state.decoration_mode = Some(DecorationMode::ClientSide);
                }
                let (max, left, right) = match (fullscreen, frame.snap) {
                    (true, _) => (false, false, false),
                    (_, Some(crate::desk::Snap::Max)) => (true, false, false),
                    (_, Some(crate::desk::Snap::Left)) => (false, true, false),
                    (_, Some(crate::desk::Snap::Right)) => (false, false, true),
                    (_, None) => (false, false, false),
                };
                let mut set = |s: xdg_toplevel::State, on: bool| {
                    if on {
                        state.states.set(s);
                    } else {
                        state.states.unset(s);
                    }
                };
                set(xdg_toplevel::State::Fullscreen, fullscreen);
                set(xdg_toplevel::State::Maximized, max);
                // A half: tiled on every edge (no rounded corners, no
                // shadow: GTK's reading of it).
                let tiled = left || right;
                set(xdg_toplevel::State::TiledTop, tiled);
                set(xdg_toplevel::State::TiledBottom, tiled);
                set(xdg_toplevel::State::TiledLeft, tiled);
                set(xdg_toplevel::State::TiledRight, tiled);
            });
            if toplevel.is_initial_configure_sent() {
                toplevel.send_pending_configure();
            }
        }
        let moved = self.space.element_location(window) != Some(rect.loc);
        if moved {
            self.space.map_element(window.clone(), rect.loc, false);
        }
        // An X11 window floats where its frame is (crate::xwayland).
        let maximized = !fullscreen && frame.snap == Some(crate::desk::Snap::Max);
        self.x11_configure(window, Some(rect.size), Some((fullscreen, maximized)));
        // The pointer over it is told again where it is (a click right
        // after a maximize under a still pointer).
        let resized = (window.geometry().size != rect.size).then_some(rect.size);
        if moved || resized.is_some() {
            self.pointer_window_replaced(id, moved, resized);
        }
        true
    }

    /// A client's decoration object came or asked for a mode: a window on
    /// a desk draws its own title bar (desk_place sets it).
    pub fn desk_decorate(&mut self, toplevel: &smithay::wayland::shell::xdg::ToplevelSurface) {
        let window =
            self.space.elements().find(|w| w.toplevel().map(|t| t == toplevel).unwrap_or(false)).cloned();
        if let Some(window) = window.filter(|w| self.on_desk(w)) {
            self.place(&window);
        }
    }

    /// The compositor's keys for windows and outputs (true: taken, not for
    /// the client):
    ///
    /// - Super + Up / Down / Left / Right: on a display, maximize, restore,
    ///   the left / right half ([`crate::desk::arrow`]);
    /// - Super + Shift + Left / Right: the focused window to the output
    ///   beside its own (DSI-1, DSI-2, then the displays, left to right);
    /// - Alt + Tab (+ Shift): the next (previous) window of the active
    ///   output to the front, with the keyboard.
    pub fn desk_shortcut(&mut self, logo: bool, shift: bool, alt: bool, keysym: u32) -> bool {
        use smithay::input::keyboard::keysyms;
        const ARROWS: [(u32, Arrow); 4] = [
            (keysyms::KEY_Up, Arrow::Up),
            (keysyms::KEY_Down, Arrow::Down),
            (keysyms::KEY_Left, Arrow::Left),
            (keysyms::KEY_Right, Arrow::Right),
        ];
        if alt && !logo && (keysym == keysyms::KEY_Tab || keysym == keysyms::KEY_ISO_Left_Tab) {
            return self.cycle_windows(shift || keysym == keysyms::KEY_ISO_Left_Tab);
        }
        if !logo {
            return false;
        }
        let Some(arrow) = ARROWS.iter().find(|(k, _)| *k == keysym).map(|(_, a)| *a) else {
            return false;
        };
        let Some(window) = self.focused_window() else {
            return false;
        };
        if shift {
            if let Arrow::Left | Arrow::Right = arrow {
                return self.window_to_neighbour(&window, arrow == Arrow::Right);
            }
            return false;
        }
        if !self.on_desk(&window) {
            return false;
        }
        let id = window_data(&window).id;
        if let Some(frame) = self.desk.frames.get_mut(&id) {
            frame.snap = crate::desk::arrow(frame.snap, arrow);
        }
        self.place(&window);
        self.queue_redraw_all();
        true
    }

    /// The window with the keyboard focus.
    pub fn focused_window(&self) -> Option<Window> {
        self.seat.get_keyboard().and_then(|k| k.current_focus()).and_then(|s| self.window_for_surface(&s))
    }

    /// The focused window to the output beside its own (false: none there).
    fn window_to_neighbour(&mut self, window: &Window, right: bool) -> bool {
        let order: Vec<String> = self
            .layout_order()
            .into_iter()
            .map(|o| o.name())
            .filter(|n| self.is_lit(n) && (!is_panel(n) || self.pose.in_use(n)))
            .collect();
        let here = match &*window_data(window).placement.borrow() {
            Placement::Output(name) => name.clone(),
            Placement::Span => return false,
        };
        let Some(i) = order.iter().position(|n| *n == here) else {
            return false;
        };
        let j = if right { i + 1 } else { i.wrapping_sub(1) };
        let Some(target) = order.get(j).cloned() else {
            return false;
        };
        info!(id = window_data(window).id, from = here, to = target, "window to the output beside");
        if is_external(&target) {
            self.ext_remember_home(window);
        }
        self.apply_target(window, Some(&crate::snap::Target::Panel(target.clone())));
        self.active_output = Some(target);
        self.refresh_keyboard_focus();
        true
    }

    /// Alt + Tab: the active output's windows, front first; the next one
    /// (or the last, `back`) to the front.
    fn cycle_windows(&mut self, back: bool) -> bool {
        let Some(output) = self.active_output() else {
            return false;
        };
        let mut windows: Vec<Window> = self
            .space
            .elements()
            .rev()
            .filter(|w| self.is_on(w, &output) && !self.is_minimized(w) && !self.is_accessory(w))
            .cloned()
            .collect();
        if windows.len() < 2 {
            return !windows.is_empty();
        }
        let next = if back { windows.pop() } else { windows.get(1).cloned() };
        if let Some(next) = next {
            self.raise_window(&next, true);
            self.refresh_keyboard_focus();
            self.queue_redraw_all();
        }
        true
    }

    /// The control socket's view of the displays.
    pub fn ext_describe(&self) -> serde_json::Value {
        let displays: Vec<serde_json::Value> = self
            .outputs
            .iter()
            .filter(|o| is_external(&o.name()))
            .map(|o| {
                let name = o.name();
                serde_json::json!({
                    "output": name,
                    "name": self.ext.names.get(&name),
                    "scale": o.current_scale().fractional_scale(),
                    "windows": self.space.elements()
                        .filter(|w| *window_data(w).placement.borrow() == Placement::Output(name.clone()))
                        .map(|w| window_data(w).id)
                        .collect::<Vec<_>>(),
                })
            })
            .collect();
        serde_json::json!({
            "displays": displays,
            "pointer": self.seat.get_pointer().is_some(),
            "trackpad": self.trackpad.panel,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(w: u16, h: u16, refresh: u32, preferred: bool) -> ModeInfo {
        ModeInfo { w, h, refresh, preferred, interlaced: false }
    }

    #[test]
    fn panels_and_displays_by_name() {
        assert!(is_panel("DSI-1") && is_panel("DSI-2"));
        assert!(is_external("DP-1") && is_external("HDMI-A-1"));
        assert!(!is_external("DSI-2") && !is_external("Writeback-1") && !is_external(""));
    }

    #[test]
    fn the_preferred_mode_when_it_is_smooth() {
        let modes = [m(1920, 1080, 60, true), m(1280, 720, 60, false), m(3840, 2160, 30, false)];
        assert_eq!(choose_mode(&modes, &ModeWish::Auto), Some(0));
        assert_eq!(choose_mode(&modes, &ModeWish::Preferred), Some(0));
    }

    #[test]
    fn a_30_hz_preferred_mode_gives_way_to_the_largest_smooth_one() {
        // A 4K TV over two lanes: 2160p only at 30 Hz.
        let modes = [
            m(3840, 2160, 30, true),
            m(1920, 1080, 60, false),
            m(2560, 1440, 60, false),
            m(1920, 1080, 50, false),
        ];
        assert_eq!(choose_mode(&modes, &ModeWish::Auto), Some(2));
        assert_eq!(choose_mode(&modes, &ModeWish::Preferred), Some(0));
        // Nothing smooth: the preferred one after all.
        let slow = [m(3840, 2160, 30, true), m(3840, 2160, 24, false)];
        assert_eq!(choose_mode(&slow, &ModeWish::Auto), Some(0));
    }

    #[test]
    fn a_mode_asked_for_by_size() {
        let modes = [m(1920, 1080, 60, true), m(1280, 720, 60, false), m(1280, 720, 50, false)];
        let want = |s: &str| ModeWish::parse(s).unwrap();
        assert_eq!(choose_mode(&modes, &want("1280x720")), Some(1));
        assert_eq!(choose_mode(&modes, &want("1280x720@50")), Some(2));
        // Not there: the automatic choice.
        assert_eq!(choose_mode(&modes, &want("800x600")), Some(0));
        // Interlaced modes are never taken; no mode at all: none.
        let i = [ModeInfo { interlaced: true, ..m(1920, 1080, 60, true) }, m(1280, 720, 60, false)];
        assert_eq!(choose_mode(&i, &ModeWish::Auto), Some(1));
        assert_eq!(choose_mode(&[], &ModeWish::Auto), None);
    }

    #[test]
    fn mode_wishes_parse() {
        assert_eq!(ModeWish::parse(" Auto "), Some(ModeWish::Auto));
        assert_eq!(ModeWish::parse("preferred"), Some(ModeWish::Preferred));
        assert_eq!(
            ModeWish::parse("1920x1080@60"),
            Some(ModeWish::Size { w: 1920, h: 1080, refresh: Some(60) })
        );
        assert_eq!(ModeWish::parse("2560X1440"), Some(ModeWish::Size { w: 2560, h: 1440, refresh: None }));
        assert_eq!(ModeWish::parse("1920x"), None);
        assert_eq!(ModeWish::parse("0x0"), None);
        assert_eq!(ModeWish::parse("big"), None);
    }

    #[test]
    fn scales_for_monitors_tvs_and_unknown_displays() {
        // 24" 1080p (531 x 299 mm): 1.
        assert_eq!(auto_scale((1920, 1080), Some((531, 299))), 1.0);
        // 27" 1440p (597 x 336): 1.
        assert_eq!(auto_scale((2560, 1440), Some((597, 336))), 1.0);
        // 27" 4K: 1.5.
        assert_eq!(auto_scale((3840, 2160), Some((597, 336))), 1.5);
        // 15.6" 4K portable (344 x 194): 2.5.
        assert_eq!(auto_scale((3840, 2160), Some((344, 194))), 2.5);
        // A 55" TV: by its lines.
        assert_eq!(auto_scale((3840, 2160), Some((1210, 680))), 2.0);
        assert_eq!(auto_scale((1920, 1080), Some((1210, 680))), 1.0);
        // EDID with only an aspect ratio (16 x 9 cm) or nothing.
        assert_eq!(auto_scale((1920, 1080), Some((16, 9))), 1.0);
        assert_eq!(auto_scale((3840, 2160), None), 2.0);
        // Never below 1 (a small old monitor).
        assert_eq!(auto_scale((1024, 768), Some((360, 270))), 1.0);
    }

    #[test]
    fn the_name_from_the_edid() {
        let mut edid = vec![0u8; 128];
        edid[0..8].copy_from_slice(&[0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00]);
        // Descriptor 2 (offset 72): the monitor name.
        edid[72..77].copy_from_slice(&[0, 0, 0, 0xfc, 0]);
        edid[77..90].copy_from_slice(b"SAMSUNG\n     ");
        assert_eq!(edid_name(&edid).as_deref(), Some("SAMSUNG"));
        // No name descriptor, or not an EDID.
        edid[75] = 0xfe;
        assert_eq!(edid_name(&edid), None);
        assert_eq!(edid_name(&[1, 2, 3]), None);
    }

    #[test]
    fn windows_go_home_to_a_lit_panel() {
        let both = vec!["DSI-1".to_owned(), "DSI-2".to_owned()];
        assert_eq!(home_for(Some("DSI-2"), &both, Some("DP-1")).as_deref(), Some("DSI-2"));
        // Its panel is off (single pose on the other): the active panel.
        let left = vec!["DSI-1".to_owned()];
        assert_eq!(home_for(Some("DSI-2"), &left, Some("DSI-1")).as_deref(), Some("DSI-1"));
        // Born on the display, the display was active: the first lit panel.
        assert_eq!(home_for(None, &both, Some("DP-1")).as_deref(), Some("DSI-1"));
        assert_eq!(home_for(None, &both, Some("DSI-2")).as_deref(), Some("DSI-2"));
        // Nothing lit: none (the caller keeps it on its old panel name).
        assert_eq!(home_for(Some("DSI-2"), &[], Some("DSI-2")), None);
    }

    #[test]
    fn config_keys() {
        let mut c = Config::default();
        assert!(c.enabled && c.scale.is_none() && c.idle_timeout_s == Some(DEFAULT_IDLE_TIMEOUT_S));
        c.set("external", "off").unwrap();
        c.set("external_scale", "1.5").unwrap();
        c.set("external_mode", "1920x1080@60").unwrap();
        c.set("external_idle_timeout", "panels").unwrap();
        assert!(!c.enabled);
        assert_eq!(c.scale, Some(1.5));
        assert_eq!(c.mode, ModeWish::Size { w: 1920, h: 1080, refresh: Some(60) });
        assert_eq!(c.idle_timeout_s, None);
        c.set("external_scale", "auto").unwrap();
        assert_eq!(c.scale, None);
        assert!(c.set("external_scale", "9").is_err());
        assert!(c.set("external_mode", "huge").is_err());
        assert!(c.set("external_idle_timeout", "-1").is_err());
        assert!(c.set("external", "maybe").is_err());
        c.set("external_trackpad", "DSI-1").unwrap();
        assert_eq!(c.trackpad.as_deref(), Some("DSI-1"));
        assert!(c.set("external_trackpad", "DP-1").is_err());
        c.set("external_trackpad", "off").unwrap();
        assert_eq!(c.trackpad, None);
        assert!(Config::KEYS.contains(&"external_mode"));
    }
}
