//! An external display as a desk or a mirror, the live part ([`crate::mirror`]
//! has the rules, the settings and the fit).
//!
//! - Each connected display has a use ([`DuoWc::display_use`]): set when it
//!   connects from `external_on_connect` (asking: it mirrors until the shell's
//!   answer), changed by `set_display` (the shell's prompt, its quick menu).
//! - A mirror is no desk ([`DuoWc::is_desk_output`] is false for it): windows
//!   placed there go back to a panel ([`DuoWc::mirror_rehome`], at every
//!   place), the pointer never goes there, the shell shows nothing there.
//! - Its frame ([`DuoWc::mirror_elements`], from `backend::render`): each
//!   source panel's scene, as a screenshot renders it (`shown_elements`: its
//!   windows, its bars, the cursor over it, the lock screen when locked),
//!   drawn into that panel's texture (kept and reused, on the GPU), then the
//!   textures drawn on the display, scaled to fit, upright, on black. A panel
//!   asked to draw asks its mirrors too (`state::queue_redraw`), so the
//!   display follows at its own refresh; a change of the source (the focus
//!   moved to the other panel) draws it again ([`DuoWc::mirror_check`]).

use std::collections::HashMap;

use anyhow::anyhow;
use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::damage::OutputDamageTracker;
use smithay::backend::renderer::element::texture::TextureRenderElement;
use smithay::backend::renderer::element::{Id, Kind};
use smithay::backend::renderer::gles::GlesTexture;
use smithay::backend::renderer::{Bind, Offscreen, Renderer as _, Texture as _};
use smithay::desktop::Window;
use smithay::output::Output;
use smithay::utils::{Buffer as BufferCoords, Physical, Rectangle, Size, Transform};
use tracing::{info, warn};

use crate::extout::{is_external, is_panel};
use crate::mirror::{self, DisplayUse, MirrorSource, OnConnect};
use crate::render::Element;
use crate::state::{window_data, DuoWc, Placement};

/// The mirror's textures, one per source panel, kept between frames.
#[derive(Default)]
pub struct MirrorGfx {
    textures: HashMap<String, GlesTexture>,
}

impl std::fmt::Debug for MirrorGfx {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MirrorGfx").field("textures", &self.textures.keys().collect::<Vec<_>>()).finish()
    }
}

/// duo-wc's state file of the displays' live settings:
/// `$XDG_STATE_HOME/duo-wc/external` (`~/.local/state/...`).
fn state_path() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/state")))?;
    Some(base.join("duo-wc/external"))
}

/// duo-wc.conf's settings under the ones changed live (the state file).
pub fn settings_at_start(config: &mirror::Settings) -> mirror::Settings {
    match state_path().map(std::fs::read_to_string) {
        Some(Ok(text)) => config.clone().with_state(&text),
        _ => config.clone(),
    }
}

impl DuoWc {
    /// What a connected display is used as (None: not a display that is up).
    pub fn display_use(&self, name: &str) -> Option<DisplayUse> {
        self.ext.uses.get(name).copied()
    }

    /// Whether a display mirrors the phone.
    pub fn is_mirror(&self, name: &str) -> bool {
        self.display_use(name) == Some(DisplayUse::Mirror)
    }

    /// Whether any display mirrors.
    pub fn mirroring(&self) -> bool {
        self.ext.uses.values().any(|u| *u == DisplayUse::Mirror)
    }

    /// A display came up: its use from `external_on_connect`.
    pub fn display_connected(&mut self, name: &str) -> (DisplayUse, bool) {
        let (used, asking) = DisplayUse::initial(self.ext.display.on_connect);
        self.ext.uses.insert(name.to_owned(), used);
        if asking {
            self.ext.asking.insert(name.to_owned());
        }
        info!(output = name, used = used.name(), asking, "display used");
        (used, asking)
    }

    /// A display went away.
    pub fn display_gone(&mut self, name: &str) {
        self.ext.uses.remove(name);
        self.ext.asking.remove(name);
        self.ext.mirror_shown.remove(name);
    }

    /// The panels a mirror shows now.
    pub fn mirror_sources(&self) -> Vec<String> {
        let lit: Vec<String> = self
            .layout_order()
            .into_iter()
            .map(|o| o.name())
            .filter(|n| is_panel(n) && self.is_lit(n))
            .collect();
        mirror::sources(&self.ext.display.mirror, self.active_output.as_deref(), &lit)
    }

    /// Draw every mirroring display again.
    pub fn mirror_redraw(&mut self) {
        let mirrors: Vec<Output> =
            self.outputs.iter().filter(|o| self.is_mirror(&o.name())).cloned().collect();
        for output in mirrors {
            self.queue_redraw_output(&output);
        }
    }

    /// After a turn of the loop: a mirror whose sources changed (the focus
    /// went to the other panel, a panel lit or dark) draws again.
    pub fn mirror_check(&mut self) {
        if !self.mirroring() {
            return;
        }
        let now = self.mirror_sources();
        let stale: Vec<Output> = self
            .outputs
            .iter()
            .filter(|o| self.is_mirror(&o.name()) && self.ext.mirror_shown.get(&o.name()) != Some(&now))
            .cloned()
            .collect();
        for output in stale {
            self.queue_redraw_output(&output);
        }
    }

    /// `set_display`: a connected display (the only one if None) used as a
    /// desk or a mirror, now. Its question is answered either way. Returns
    /// the display's name.
    pub fn set_display_use(&mut self, output: Option<String>, used: DisplayUse) -> anyhow::Result<String> {
        let name = match output {
            Some(name) => name,
            None => {
                let up: Vec<String> =
                    self.outputs.iter().map(|o| o.name()).filter(|n| is_external(n)).collect();
                match up.as_slice() {
                    [one] => one.clone(),
                    [] => anyhow::bail!("no display is connected"),
                    _ => anyhow::bail!("more than one display: name it"),
                }
            }
        };
        if !is_external(&name) || self.output_by_name(&name).is_none() {
            anyhow::bail!("{name} is not a connected display");
        }
        self.ext.asking.remove(&name);
        let before = self.display_use(&name);
        self.ext.uses.insert(name.clone(), used);
        if before != Some(used) {
            info!(output = name, used = used.name(), "display used as");
            if used == DisplayUse::Mirror {
                self.mirror_rehome_all(&name);
            }
            // A touchpad panel is for a desk.
            if used == DisplayUse::Mirror
                && self.trackpad.panel.is_some()
                && !self
                    .outputs
                    .iter()
                    .any(|o| is_external(&o.name()) && self.display_use(&o.name()) == Some(DisplayUse::Desk))
            {
                let _ = self.set_trackpad(None);
            }
            self.ext.mirror_shown.remove(&name);
            self.pointer_off_mirrors();
            self.place_all();
            self.refresh_keyboard_focus();
            self.queue_redraw_all();
        }
        self.events_dirty = true;
        crate::ipc::broadcast(
            self,
            &serde_json::json!({ "event": "display", "output": name, "use": used.name() }),
        );
        Ok(name)
    }

    /// `set_external`: the settings, live, kept in the state file.
    pub fn set_display_settings(
        &mut self,
        on_connect: Option<OnConnect>,
        source: Option<MirrorSource>,
    ) -> anyhow::Result<()> {
        if on_connect.is_none() && source.is_none() {
            return Ok(());
        }
        if let Some(on_connect) = on_connect {
            self.ext.display.on_connect = on_connect;
        }
        if let Some(source) = source {
            self.ext.display.mirror = source;
            self.mirror_redraw();
        }
        info!(
            on_connect = self.ext.display.on_connect.name(),
            mirror = self.ext.display.mirror.name(),
            "display settings"
        );
        if let Some(path) = state_path() {
            let written = path
                .parent()
                .map(std::fs::create_dir_all)
                .unwrap_or(Ok(()))
                .and_then(|_| std::fs::write(&path, self.ext.display.state_text()));
            if let Err(err) = written {
                warn!(?err, path = %path.display(), "could not keep the display settings");
            }
        }
        self.events_dirty = true;
        Ok(())
    }

    /// The displays as the state tells them.
    pub fn displays_summary(&self) -> mirror::Summary {
        let sources = if self.mirroring() { self.mirror_sources() } else { Vec::new() };
        let mut connected: Vec<mirror::DisplaySummary> = self
            .outputs
            .iter()
            .map(|o| o.name())
            .filter(|n| is_external(n))
            .map(|name| {
                let used = self.display_use(&name).unwrap_or(DisplayUse::Desk);
                mirror::DisplaySummary {
                    name: self.ext.names.get(&name).cloned(),
                    used_as: used,
                    asking: self.ext.asking.contains(&name),
                    mirrors: if used == DisplayUse::Mirror { sources.clone() } else { Vec::new() },
                    output: name,
                }
            })
            .collect();
        connected.sort_by(|a, b| a.output.cmp(&b.output));
        mirror::Summary {
            on_connect: self.ext.display.on_connect,
            mirror: self.ext.display.mirror.name(),
            connected,
        }
    }

    /// A window placed on a mirroring display goes back to a panel (a
    /// mirror shows the phone; nothing is put on it). True if it moved.
    pub fn mirror_rehome(&mut self, window: &Window) -> bool {
        let on = match &*window_data(window).placement.borrow() {
            Placement::Output(name) if self.is_mirror(name) => name.clone(),
            _ => return false,
        };
        let id = window_data(window).id;
        let lit: Vec<String> = self
            .layout_order()
            .into_iter()
            .map(|o| o.name())
            .filter(|n| is_panel(n) && self.is_lit(n))
            .collect();
        let came_from = self.ext.homes.remove(&id);
        let target = crate::extout::home_for(came_from.as_deref(), &lit, self.active_output.as_deref())
            .or(came_from)
            .or_else(|| self.layout_order().into_iter().map(|o| o.name()).find(|n| is_panel(n)));
        let Some(target) = target else {
            return false;
        };
        info!(id, from = on, to = target, "a mirror holds no window: back to a panel");
        *window_data(window).fullscreen.borrow_mut() = false;
        *window_data(window).placement.borrow_mut() = Placement::Output(target);
        true
    }

    /// Every window on a display that now mirrors: back to the panels.
    fn mirror_rehome_all(&mut self, name: &str) {
        if self.desk.drag.is_some() {
            self.desk.drag = None;
        }
        let on_it: Vec<Window> = self
            .space
            .elements()
            .filter(|w| *window_data(w).placement.borrow() == Placement::Output(name.to_owned()))
            .cloned()
            .collect();
        for window in on_it {
            self.mirror_rehome(&window);
        }
    }

    /// The pointer is never on a mirror: to the active panel.
    fn pointer_off_mirrors(&mut self) {
        let on_mirror = self.pointer_output().map(|o| self.is_mirror(&o.name())).unwrap_or(false);
        if on_mirror {
            let target = self
                .active_output()
                .filter(|o| !self.is_mirror(&o.name()))
                .or_else(|| self.outputs.iter().find(|o| is_panel(&o.name())).cloned());
            if let Some(g) = target.and_then(|o| self.space.output_geometry(&o)) {
                self.pointer.location =
                    (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
            }
        }
    }

    /// A mirroring display's frame: its source panels' pictures, fitted.
    /// None: not a mirror (or nothing could be drawn: the frame is black).
    pub fn mirror_elements(&mut self, display: &Output) -> Option<Vec<Element>> {
        let name = display.name();
        if !self.is_mirror(&name) {
            return None;
        }
        let sources = self.mirror_sources();
        self.ext.mirror_shown.insert(name.clone(), sources.clone());
        let mut pictures: Vec<(GlesTexture, Size<i32, Physical>)> = Vec::new();
        for panel_name in &sources {
            let Some(panel) = self.output_by_name(panel_name) else {
                continue;
            };
            match self.mirror_capture(&panel) {
                Ok(picture) => pictures.push(picture),
                Err(err) => warn!(?err, panel = panel_name, "mirror: the panel could not be drawn"),
            }
        }
        let mode = display.current_mode()?;
        let dst = display.current_transform().transform_size(mode.size);
        let scale = display.current_scale().fractional_scale();
        let sizes: Vec<(f64, f64)> = pictures.iter().map(|(_, s)| (s.w as f64, s.h as f64)).collect();
        // Never blown up past twice the panel's own pixels (a 4K TV).
        let rects = mirror::fit(&sizes, (dst.w as f64, dst.h as f64), 2.0);
        let device = self.tty.device.as_mut()?;
        let context = device.renderer.context_id();
        let mut elements = Vec::with_capacity(pictures.len());
        for ((tex, size), (x, y, w, h)) in pictures.into_iter().zip(rects) {
            let logical: Size<i32, smithay::utils::Logical> =
                ((w / scale).round() as i32, (h / scale).round() as i32).into();
            let element = TextureRenderElement::from_static_texture(
                Id::new(),
                context.clone(),
                (x.round(), y.round()),
                tex,
                1,
                Transform::Normal,
                None,
                Some(Rectangle::from_size((size.w as f64, size.h as f64).into())),
                Some(logical),
                Some(vec![Rectangle::<i32, BufferCoords>::from_size((size.w, size.h).into())]),
                Kind::Unspecified,
            );
            elements.push(Element::Mirror(element));
        }
        Some(elements)
    }

    /// One panel's picture as its next frame shows it (upright as it reads,
    /// physical px), rendered into the panel's mirror texture.
    fn mirror_capture(&mut self, panel: &Output) -> anyhow::Result<(GlesTexture, Size<i32, Physical>)> {
        let elements = self.shown_elements(panel)?;
        let mode = panel.current_mode().ok_or_else(|| anyhow!("no mode"))?;
        let size = panel.current_transform().transform_size(mode.size);
        let scale = panel.current_scale().fractional_scale();
        let device = self.tty.device.as_mut().ok_or_else(|| anyhow!("no device opened"))?;
        let wanted: Size<i32, BufferCoords> = (size.w, size.h).into();
        let name = panel.name();
        let reuse = self.ext.mirror_gfx.textures.get(&name).filter(|t| t.size() == wanted).cloned();
        let mut tex = match reuse {
            Some(tex) => tex,
            None => {
                let tex: GlesTexture = device
                    .renderer
                    .create_buffer(Fourcc::Abgr8888, wanted)
                    .map_err(|err| anyhow!("no texture: {err}"))?;
                self.ext.mirror_gfx.textures.insert(name, tex.clone());
                tex
            }
        };
        {
            let mut target = device.renderer.bind(&mut tex).map_err(|err| anyhow!("cannot bind: {err}"))?;
            let mut damage = OutputDamageTracker::new(size, scale, Transform::Normal);
            damage
                .render_output(&mut device.renderer, &mut target, 0, &elements, crate::backend::BACKGROUND)
                .map_err(|err| anyhow!("cannot render: {err:?}"))?;
        }
        Ok((tex, size))
    }
}
