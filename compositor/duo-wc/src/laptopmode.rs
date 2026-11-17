//! Laptop mode, the live part: one panel a desk, the other the keyboard and
//! the touchpad ([`crate::laptop`] has the rules and the layout).
//!
//! - **On** (`set_laptop on`, Duo Settings, the laptop posture with
//!   `laptop_auto`): only with both panels in use (pose `dual`). The
//!   keyboard panel's windows (and spanning ones) go to the desk panel,
//!   their own places kept for the way back; the desk panel's windows float
//!   ([`crate::desk`], as on an external display: their own title bars,
//!   Super + arrows, the pointer moves and resizes them, a finger on a
//!   title bar moves them); new windows open there; the shell's dock stands
//!   there as a taskbar. The seat gets a pointer (the touchpad's); the
//!   on-screen keyboard is never shown (the phone has a keyboard now: the
//!   device state says so, so phosh-osk-stub does not unfold either).
//! - **The keyboard panel** shows only duo-wc's keys and touchpad (the key
//!   caps, the pressed key in the accent colour, a latched modifier ringed)
//!   and the shell's labels on them (a see-through surface of the namespace
//!   [`crate::laptop::LABELS_NAMESPACE`], drawn from the layout on the event
//!   stream: duo-wc has no fonts). Every touch there is duo-wc's: a key goes
//!   to the seat as a hardware key would (the compositor's shortcuts too),
//!   the touchpad drives the pointer ([`crate::trackpad::Pad`]: one finger
//!   moves it, accelerated; a tap clicks, two fingers tapped right-click,
//!   two fingers scroll, tap and touch again drags).
//! - **The layout.** The keys are the seat's own layout in force (the
//!   system's list, [`crate::kbdlayout`]): their caps are read from the
//!   seat's keymap ([`crate::laptop::Board`]), a key goes into the seat by
//!   its place, and xkb types what that layout types there. The layout key
//!   shows the layout in force: tapped, the next one; held, a list of the
//!   layouts (the configured ones and common ones; one picked that is not
//!   configured is added to the list). The keymap is never changed for laptop
//!   mode: a Bluetooth keyboard types in the same layout.
//! - **Off** (by hand, the exit key held, the posture left when it had
//!   turned it on, any pose but `dual`): every key and modifier let go, the
//!   windows back where they were (those still on the desk panel), the
//!   phone's layout, pointer and on-screen keyboard as before.

use std::collections::HashMap;

use smithay::backend::input::KeyState;
use smithay::backend::renderer::element::solid::SolidColorRenderElement;
use smithay::backend::renderer::element::{AsRenderElements, Id, Kind as ElementKind};
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::backend::renderer::utils::CommitCounter;
use smithay::desktop::{layer_map_for_output, Window};
use smithay::input::keyboard::Keycode;
use smithay::output::Output;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::utils::{Logical, Physical, Point, Rectangle};
use tracing::{debug, info, warn};

use crate::fx::{premul, RoundedBox, ACCENT};
use crate::laptop::{self, AutoStep, Board, Ev, Kind, Layout, Menu, Mods, Origin};
use crate::render::Element;
use crate::state::{window_data, DuoWc, Placement};
use crate::trackpad::{Out, Pad};

/// The pointer "device" the keyboard panel's touchpad counts as.
pub const POINTER_DEVICE: &str = "duo-wc-laptop";

/// What a finger on the keyboard panel is doing.
#[derive(Debug, Clone, Copy)]
enum Touch {
    /// On a key (its index in the layout), since `time`.
    Key { index: usize, time: u32 },
    /// On the list of layouts (on an entry, or between them).
    Menu { item: Option<usize> },
    /// Closed the list of layouts by touching beside it: nothing else.
    Dismiss,
    /// On the touchpad.
    Pad,
    /// Beside them (a palm): nothing.
    Palm,
}

/// Laptop mode's state.
pub struct Laptop {
    /// duo-wc.conf's `laptop_*`, changed live by `set_laptop`.
    pub config: laptop::Config,
    /// On, and who turned it on.
    pub on: Option<Origin>,
    /// The desk panel (while on).
    pub desk: String,
    /// The keyboard panel (while on).
    pub keys: String,
    /// The posture rule's memory.
    pub auto: laptop::Auto,
    /// The places of the windows moved to the desk, for the way back.
    saved: HashMap<u64, Placement>,
    /// The keyboard panel's layout, for its size now.
    layout: Option<Layout>,
    /// The fingers on the keyboard panel.
    touches: HashMap<u32, Touch>,
    /// The modifier keys.
    pub mods: Mods,
    /// The touchpad's gestures.
    pub(crate) pad: Pad,
    /// Each touchpad finger's last place and time (its speed).
    pad_last: HashMap<u32, ((f64, f64), u32)>,
    /// Keys under a finger (index: fingers), drawn pressed.
    pressed: HashMap<usize, usize>,
    /// The rounded boxes (key caps, the touchpad).
    gfx: crate::fx::Gfx,
    /// The background's element id.
    background: Id,
    /// The seat keymap's layouts as the keys show them (while on).
    board: Option<Board>,
    /// The list of layouts, while it is up.
    menu: Option<Menu>,
    /// The layout in force when the keys last showed it.
    group_seen: u32,
}

/// duo-wc's state file of laptop mode's live settings:
/// `$XDG_STATE_HOME/duo-wc/laptop` (`~/.local/state/...`).
fn state_path() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/state")))?;
    Some(base.join("duo-wc/laptop"))
}

impl Laptop {
    /// Off, with the configuration (duo-wc.conf's, under the settings
    /// changed live and kept in the state file).
    pub fn new(config: laptop::Config, drag_lock: bool) -> Self {
        let config = match state_path().map(std::fs::read_to_string) {
            Some(Ok(text)) => config.with_state(&text),
            _ => config,
        };
        let mut pad = Pad::default();
        // The finger's own travel: the speed decides the gain (crate::laptop::accel).
        pad.gain = 1.0;
        pad.drag_lock = drag_lock;
        Laptop {
            config,
            on: None,
            desk: String::new(),
            keys: String::new(),
            auto: laptop::Auto::default(),
            saved: HashMap::new(),
            layout: None,
            touches: HashMap::new(),
            mods: Mods::default(),
            pad,
            pad_last: HashMap::new(),
            pressed: HashMap::new(),
            gfx: crate::fx::Gfx::default(),
            background: Id::new(),
            board: None,
            menu: None,
            group_seen: 0,
        }
    }
}

impl DuoWc {
    /// Whether laptop mode is on.
    pub fn laptop_on(&self) -> bool {
        self.laptop.on.is_some()
    }

    /// Whether an output is a desk: an external display, or the desk panel
    /// of laptop mode (windows float there, [`crate::desk`]).
    pub fn is_desk_output(&self, name: &str) -> bool {
        // A display that mirrors the phone is no desk (crate::mirror).
        (crate::extout::is_external(name) && !self.is_mirror(name))
            || (self.laptop_on() && self.laptop.desk == name)
    }

    /// Whether this panel is laptop mode's keyboard panel now.
    pub fn is_laptop_keys(&self, name: &str) -> bool {
        self.laptop_on() && self.laptop.keys == name
    }

    /// The desk panel, while laptop mode is on.
    pub fn laptop_desk(&self) -> Option<String> {
        self.laptop_on().then(|| self.laptop.desk.clone())
    }

    /// Whether the keyboard panel shows the keys now (not locked, no
    /// ambient screen).
    pub fn laptop_shows(&self, name: &str) -> bool {
        self.is_laptop_keys(name) && !self.lock.locked && self.ambient.is_none()
    }

    /// Whether the on-screen keyboard is out of sight and touch now: put
    /// away (crate::oskleave) or held back at its start, or laptop mode is
    /// on (its keyboard panel is the keyboard).
    pub fn osk_hidden(&self) -> bool {
        self.osk_put_away() || self.laptop_on()
    }

    /// Whether a touch going down on `output` is the keyboard panel's.
    pub fn laptop_takes(&self, output: &str) -> bool {
        self.laptop_shows(output)
    }

    /// Both panels' rotations (DSI-1's, DSI-2's), in degrees.
    fn panel_rotations(&self) -> (u32, u32) {
        let of = |name: &str| {
            self.output_by_name(name)
                .map(|o| crate::rotation::Rotation::from_transform(o.current_transform()).degrees())
                .unwrap_or(0)
        };
        (of("DSI-1"), of("DSI-2"))
    }

    fn in_laptop_posture(&self) -> bool {
        self.posture.as_deref() == Some(laptop::LAPTOP_POSTURE)
    }

    /// Turn laptop mode on or off.
    pub fn set_laptop(&mut self, on: bool, origin: Origin) -> anyhow::Result<()> {
        if on == self.laptop_on() {
            return Ok(());
        }
        if on {
            self.laptop_enter(origin)
        } else {
            if origin == Origin::Manual {
                let posture = self.in_laptop_posture();
                self.laptop.auto.user_off(posture);
            }
            self.laptop_leave("asked");
            Ok(())
        }
    }

    /// Change laptop mode's settings live (`laptop_auto`, `laptop_keys`).
    pub fn set_laptop_config(&mut self, auto: Option<bool>, keys: Option<String>) -> anyhow::Result<()> {
        if auto.is_none() && keys.is_none() {
            return Ok(());
        }
        if let Some(keys) = keys {
            if !laptop::is_panel_name(&keys) {
                anyhow::bail!("{keys} is not a panel (DSI-1 or DSI-2)");
            }
            self.laptop.config.keys = keys;
            if self.laptop_on() {
                self.laptop_follow_panels("keyboard panel chosen");
            }
        }
        if let Some(auto) = auto {
            self.laptop.config.auto = auto;
            info!(auto, "laptop mode by the posture");
            self.laptop_posture_changed();
        }
        if let Some(path) = state_path() {
            let written = path
                .parent()
                .map(std::fs::create_dir_all)
                .unwrap_or(Ok(()))
                .and_then(|_| std::fs::write(&path, self.laptop.config.state_text()));
            if let Err(err) = written {
                warn!(?err, path = %path.display(), "could not keep laptop mode's settings");
            }
        }
        self.events_dirty = true;
        Ok(())
    }

    fn laptop_enter(&mut self, origin: Origin) -> anyhow::Result<()> {
        if self.pose != crate::pose::Pose::Dual {
            anyhow::bail!("laptop mode needs both panels (the pose is {})", self.pose.describe());
        }
        let (desk, keys) = laptop::panels(&self.laptop.config.keys, self.panel_rotations());
        if self.output_by_name(&desk).is_none() || self.output_by_name(&keys).is_none() {
            anyhow::bail!("laptop mode needs both panels");
        }
        info!(?origin, desk, keys, "laptop mode on");
        // A panel as an external display's touchpad gives way.
        if self.trackpad.panel.is_some() {
            let _ = self.set_trackpad(None);
        }
        if self.taskview.open {
            self.overview_close("laptop mode");
        }
        self.cancel_all_touches();
        self.laptop.on = Some(origin);
        self.laptop.desk = desk.clone();
        self.laptop.keys = keys.clone();
        self.laptop.layout = None;
        self.laptop.menu = None;
        self.laptop.gfx.forget("lt/");
        self.laptop_move_to_desk();
        if self.active_output.as_deref() != Some(desk.as_str())
            && !self.active_output.as_deref().map(crate::extout::is_external).unwrap_or(false)
        {
            self.active_output = Some(desk.clone());
        }
        self.laptop_build_board();
        self.laptop.group_seen = self.keyboard_active();
        self.device_state.set_laptop_keyboard(true);
        // The touchpad's pointer, in the middle of the desk.
        self.pointer.devices.insert(POINTER_DEVICE.to_owned());
        if self.seat.get_pointer().is_none() {
            self.seat.add_pointer();
        }
        if let Some(g) = self.output_by_name(&desk).and_then(|o| self.space.output_geometry(&o)) {
            self.pointer.location =
                (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
        }
        self.desk_idle_update();
        self.place_all();
        self.refresh_keyboard_focus();
        self.laptop_prepare();
        self.events_dirty = true;
        self.queue_redraw_all();
        crate::ipc::broadcast(
            self,
            &serde_json::json!({ "event": "laptop", "on": true, "desk": desk, "keys": keys }),
        );
        Ok(())
    }

    /// Every window on the keyboard panel or across both panels to the
    /// desk panel (their places kept for the way back).
    fn laptop_move_to_desk(&mut self) {
        let desk = self.laptop.desk.clone();
        let keys = self.laptop.keys.clone();
        let windows: Vec<Window> = self.space.elements().cloned().collect();
        for window in &windows {
            let data = window_data(window);
            let placement = data.placement.borrow().clone();
            let away = match &placement {
                Placement::Output(name) => *name == keys,
                Placement::Span => true,
            };
            if !away {
                continue;
            }
            self.laptop.saved.entry(data.id).or_insert(placement);
            *data.placement.borrow_mut() = Placement::Output(desk.clone());
            if self.android_kind(window).on_display() {
                self.android_laid_out(window);
            }
        }
    }

    fn laptop_leave(&mut self, why: &str) {
        let Some(origin) = self.laptop.on else {
            return;
        };
        info!(why, ?origin, "laptop mode off");
        self.laptop_touch_cancel();
        for ev in self.laptop.mods.release_all() {
            self.laptop_emit(ev, 0);
        }
        self.desk.drag = None;
        let desk = self.laptop.desk.clone();
        self.laptop.on = None;
        self.laptop.layout = None;
        self.laptop.menu = None;
        self.laptop.board = None;
        self.laptop.gfx.forget("lt/");
        self.laptop.touches.clear();
        self.laptop.pressed.clear();
        // The windows that are still on the desk panel back where they were.
        let saved = std::mem::take(&mut self.laptop.saved);
        let windows: Vec<Window> = self.space.elements().cloned().collect();
        for window in &windows {
            let data = window_data(window);
            let Some(place) = saved.get(&data.id) else {
                continue;
            };
            if *data.placement.borrow() != Placement::Output(desk.clone()) {
                continue;
            }
            if *place == Placement::Span && self.in_use().len() < 2 {
                continue;
            }
            *data.placement.borrow_mut() = place.clone();
            if self.android_kind(window).on_display() {
                self.android_laid_out(window);
            }
        }
        self.device_state.set_laptop_keyboard(false);
        self.pointer.devices.remove(POINTER_DEVICE);
        if self.pointer.devices.is_empty() && self.seat.get_pointer().is_some() {
            self.pointer.shown = false;
            self.pointer.held.clear();
            self.seat.remove_pointer();
        }
        self.desk_idle_update();
        self.place_all();
        self.refresh_keyboard_focus();
        self.events_dirty = true;
        self.queue_redraw_all();
        crate::ipc::broadcast(self, &serde_json::json!({ "event": "laptop", "on": false, "why": why }));
    }

    /// The posture changed (or `laptop_auto`): the laptop posture may turn
    /// laptop mode on or off.
    pub fn laptop_posture_changed(&mut self) {
        let posture = self.in_laptop_posture();
        match self.laptop.auto.step(self.laptop.config.auto, posture, self.laptop.on) {
            AutoStep::TurnOn => {
                if let Err(err) = self.laptop_enter(Origin::Auto) {
                    info!(%err, "the laptop posture: laptop mode not turned on");
                }
            }
            AutoStep::TurnOff => self.laptop_leave("the laptop posture left"),
            AutoStep::Nothing => {}
        }
    }

    /// The pose is changing to `pose`: laptop mode needs both panels.
    pub fn laptop_pose_changing(&mut self, pose: &crate::pose::Pose) {
        if self.laptop_on() && *pose != crate::pose::Pose::Dual {
            self.laptop_leave("the pose is not dual");
        }
    }

    /// A panel turned: the keyboard is the lower panel when both are in
    /// landscape (the other panel turned the phone round, say), and the
    /// layout follows the keyboard panel's new shape.
    pub fn laptop_rotation_changed(&mut self) {
        if self.laptop_on() {
            self.laptop_follow_panels("rotation");
        }
    }

    /// Desk and keyboard as the rules say now; swapped if they changed.
    fn laptop_follow_panels(&mut self, why: &str) {
        let (desk, keys) = laptop::panels(&self.laptop.config.keys, self.panel_rotations());
        self.laptop.layout = None;
        self.laptop.menu = None;
        self.laptop.gfx.forget("lt/");
        if desk == self.laptop.desk {
            self.laptop_prepare();
            self.events_dirty = true;
            self.queue_redraw_all();
            return;
        }
        info!(why, desk, keys, "laptop mode: the panels swap");
        self.laptop_touch_cancel();
        self.desk.drag = None;
        let old_desk = std::mem::replace(&mut self.laptop.desk, desk.clone());
        self.laptop.keys = keys;
        // The old desk's windows go to the new desk (they float there).
        let windows: Vec<Window> = self.space.elements().cloned().collect();
        for window in &windows {
            let data = window_data(window);
            if *data.placement.borrow() == Placement::Output(old_desk.clone()) {
                self.laptop.saved.entry(data.id).or_insert_with(|| Placement::Output(old_desk.clone()));
                *data.placement.borrow_mut() = Placement::Output(desk.clone());
            }
        }
        if self.active_output.as_deref() == Some(old_desk.as_str()) {
            self.active_output = Some(desk.clone());
        }
        if let Some(g) = self.output_by_name(&desk).and_then(|o| self.space.output_geometry(&o)) {
            self.pointer.location =
                (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
        }
        self.place_all();
        self.refresh_keyboard_focus();
        self.laptop_prepare();
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// Where a target of a carry or of the overview goes in laptop mode:
    /// never onto the keyboard panel, never across both panels, no halves
    /// on the desk (its windows float). Other outputs as asked.
    pub fn laptop_target(&self, target: Option<&crate::snap::Target>) -> Option<crate::snap::Target> {
        use crate::snap::Target;
        let target = target?.clone();
        if !self.laptop_on() {
            return Some(target);
        }
        let (keys, desk_name) = (&self.laptop.keys, &self.laptop.desk);
        let desk = Target::Panel(desk_name.clone());
        Some(match target {
            Target::Span => desk,
            Target::Panel(ref name) | Target::Half(ref name, _) if name == keys || name == desk_name => desk,
            other => other,
        })
    }

    /// The panel a launch hint names, in laptop mode: the desk instead of
    /// the keyboard panel.
    pub fn laptop_hint_output(&self, name: &str) -> String {
        if self.is_laptop_keys(name) {
            self.laptop.desk.clone()
        } else {
            name.to_owned()
        }
    }

    /// The layout in force as the keys show it.
    fn laptop_group(&self) -> Option<&laptop::GroupKeys> {
        self.laptop.board.as_ref().and_then(|b| b.group(self.keyboard_active()))
    }

    /// The board of the layout in force (ISO or ANSI).
    fn laptop_form(&self) -> laptop::Form {
        self.laptop_group().map(|g| g.form).unwrap_or_default()
    }

    /// The keyboard panel's layout for its size and the layout in force now.
    fn laptop_layout(&mut self) -> Option<&Layout> {
        let output = self.output_by_name(&self.laptop.keys)?;
        let g = self.space.output_geometry(&output)?;
        let size = (g.size.w as f64, g.size.h as f64);
        let form = self.laptop_form();
        if self.laptop.layout.as_ref().map(|l| (l.size, l.form)) != Some((size, form)) {
            debug!(w = size.0, h = size.1, iso = form.iso, "laptop keyboard laid out");
            let layout = laptop::layout(size.0, size.1, form);
            if self.laptop.menu.is_some() {
                let active = self.keyboard_active();
                self.laptop.menu = Some(laptop::menu(&layout, &self.kbd.list, active));
            }
            self.laptop.layout = Some(layout);
            self.laptop.gfx.forget("lt/");
            self.laptop.touches.retain(|_, t| !matches!(t, Touch::Key { .. }));
            self.laptop.pressed.clear();
            self.events_dirty = true;
        }
        self.laptop.layout.as_ref()
    }

    /// After a key of the seat: if the layout in force changed, the keys
    /// show the new one.
    pub fn laptop_note_group(&mut self) {
        if self.laptop_on() && self.keyboard_active() != self.laptop.group_seen {
            self.laptop_group_changed();
        }
    }

    /// The seat keymap's layouts read again (laptop mode turned on).
    fn laptop_build_board(&mut self) {
        let names = self.kbd.names();
        self.laptop.board = laptop::Board::compile(&names, &self.kbd.list);
        match &self.laptop.board {
            Some(b) => {
                info!(layouts = names.layout, groups = b.groups.len(), "laptop keyboard: the layouts' keys")
            }
            None => warn!(
                layouts = names.layout,
                "laptop keyboard: the keymap did not compile, no symbols on the keys"
            ),
        }
    }

    /// The seat's keymap changed (a new list of layouts): the keys read it
    /// again.
    pub fn laptop_keymap_changed(&mut self) {
        if self.laptop_on() {
            self.laptop_build_board();
            self.laptop_group_changed();
        }
    }

    /// The layout in force may have changed (the layout key, a plugged
    /// keyboard's layout switch): the keys show it.
    pub fn laptop_group_changed(&mut self) {
        if !self.laptop_on() {
            return;
        }
        self.laptop.group_seen = self.keyboard_active();
        let active = self.keyboard_active();
        if let Some(menu) = &mut self.laptop.menu {
            for (i, item) in menu.items.iter_mut().enumerate() {
                item.active = item.configured && i as u32 == active;
            }
        }
        self.laptop_prepare();
        self.events_dirty = true;
        self.laptop_redraw();
    }

    /// A global point on the keyboard panel in its own frame.
    fn laptop_local(&self, at: Point<f64, Logical>) -> Option<(f64, f64)> {
        let output = self.output_by_name(&self.laptop.keys)?;
        let g = self.space.output_geometry(&output)?;
        Some((at.x - g.loc.x as f64, at.y - g.loc.y as f64))
    }

    fn laptop_redraw(&mut self) {
        if let Some(output) = self.output_by_name(&self.laptop.keys) {
            self.queue_redraw_output(&output);
        }
    }

    /// A finger down on the keyboard panel (`at`: global).
    pub fn laptop_touch_down(&mut self, slot: u32, at: Point<f64, Logical>, time: u32) {
        let Some(local) = self.laptop_local(at) else {
            return;
        };
        if self.laptop_layout().is_none() {
            return;
        }
        if let Some((item, inside)) = self
            .laptop
            .menu
            .as_ref()
            .map(|m| (m.item_at(local.0, local.1), m.rect.contains(local.0, local.1)))
        {
            // The list of layouts is up: a touch on it picks, beside it
            // closes it (and types nothing).
            let touch = match (item, inside) {
                (Some(item), _) => Touch::Menu { item: Some(item) },
                (None, true) => Touch::Menu { item: None },
                (None, false) => {
                    self.laptop_close_menu();
                    Touch::Dismiss
                }
            };
            self.laptop.touches.insert(slot, touch);
            self.laptop_redraw();
            return;
        }
        let Some(layout) = self.laptop.layout.as_ref() else {
            return;
        };
        let touch = if let Some(index) = layout.key_at(local.0, local.1) {
            Touch::Key { index, time }
        } else if layout.on_touchpad(local.0, local.1) {
            Touch::Pad
        } else {
            Touch::Palm
        };
        self.laptop.touches.insert(slot, touch);
        match touch {
            Touch::Key { index, .. } => {
                // Typing: a tap's button waiting for a drag lets go first.
                for out in self.laptop.pad.flush() {
                    self.laptop_pad_apply(out, time, 1.0);
                }
                *self.laptop.pressed.entry(index).or_default() += 1;
                let is_layout = self
                    .laptop
                    .layout
                    .as_ref()
                    .and_then(|l| l.keys.get(index))
                    .is_some_and(|k| k.def.kind == Kind::Layout);
                if is_layout {
                    self.laptop_arm_hold(slot, index, time);
                } else {
                    self.laptop_key(index, true, time);
                }
            }
            Touch::Pad => {
                self.laptop.pad_last.insert(slot, (local, time));
                let outs = self.laptop.pad.down(slot, local, time);
                for out in outs {
                    self.laptop_pad_apply(out, time, 1.0);
                }
                self.pad_arm(crate::trackpad::PadId::Laptop, time);
            }
            Touch::Menu { .. } | Touch::Dismiss | Touch::Palm => {}
        }
        self.laptop_redraw();
    }

    /// A finger on the keyboard panel moved. False: not one of its fingers.
    pub fn laptop_touch_motion(&mut self, slot: u32, at: Point<f64, Logical>, time: u32) -> bool {
        let Some(touch) = self.laptop.touches.get(&slot).copied() else {
            return false;
        };
        if let Touch::Menu { item } = touch {
            // A finger on the list follows the entry under it (the one that
            // held the layout key slides onto a layout).
            let under = self
                .laptop_local(at)
                .and_then(|(x, y)| self.laptop.menu.as_ref().and_then(|m| m.item_at(x, y)));
            if under != item {
                self.laptop.touches.insert(slot, Touch::Menu { item: under });
                self.laptop_redraw();
            }
            return true;
        }
        if !matches!(touch, Touch::Pad) {
            // A key stays the key it went down on; a palm stays a palm.
            return true;
        }
        let Some(local) = self.laptop_local(at) else {
            return true;
        };
        let factor = match self.laptop.pad_last.insert(slot, (local, time)) {
            Some((last, then)) => {
                let d = ((local.0 - last.0).powi(2) + (local.1 - last.1).powi(2)).sqrt();
                let dt = time.wrapping_sub(then).clamp(1, 100) as f64;
                laptop::GAIN * laptop::accel(d / dt)
            }
            None => laptop::GAIN,
        };
        let outs = self.laptop.pad.motion(slot, local);
        for out in outs {
            self.laptop_pad_apply(out, time, factor);
        }
        true
    }

    /// A finger on the keyboard panel lifted. False: not one of its fingers.
    pub fn laptop_touch_up(&mut self, slot: u32, time: u32) -> bool {
        let Some(touch) = self.laptop.touches.remove(&slot) else {
            return false;
        };
        match touch {
            Touch::Key { index, time: down } => {
                if let Some(n) = self.laptop.pressed.get_mut(&index) {
                    *n -= 1;
                    if *n == 0 {
                        self.laptop.pressed.remove(&index);
                    }
                }
                let kind = self.laptop.layout.as_ref().and_then(|l| l.keys.get(index)).map(|k| k.def.kind);
                match kind {
                    Some(Kind::Exit) => {
                        if time.wrapping_sub(down) >= laptop::EXIT_HOLD_MS {
                            let _ = self.set_laptop(false, Origin::Manual);
                            return true;
                        }
                        debug!("the exit key, not held long enough");
                    }
                    // Tapped (held, it became the list): the next layout.
                    Some(Kind::Layout) => self.keyboard_next(),
                    _ => self.laptop_key(index, false, time),
                }
            }
            Touch::Menu { item } => {
                if let Some(item) = item {
                    self.laptop_pick(item);
                }
            }
            Touch::Dismiss => {}
            Touch::Pad => {
                self.laptop.pad_last.remove(&slot);
                let outs = self.laptop.pad.up(slot, time);
                for out in outs {
                    self.laptop_pad_apply(out, time, 1.0);
                }
                self.pad_arm(crate::trackpad::PadId::Laptop, time);
            }
            Touch::Palm => {}
        }
        self.laptop_redraw();
        true
    }

    /// Every finger on the keyboard panel gone at once (a cancel): the keys
    /// under them let go, a touchpad drag ends.
    pub fn laptop_touch_cancel(&mut self) {
        let touches: Vec<Touch> = self.laptop.touches.drain().map(|(_, t)| t).collect();
        for touch in touches {
            if let Touch::Key { index, .. } = touch {
                self.laptop_key(index, false, 0);
            }
        }
        self.laptop.menu = None;
        self.laptop.pressed.clear();
        self.laptop.pad_last.clear();
        let outs = self.laptop.pad.cancel();
        for out in outs {
            self.laptop_pad_apply(out, 0, 1.0);
        }
        self.laptop_redraw();
    }

    pub(crate) fn laptop_pad_apply(&mut self, out: Out, time: u32, factor: f64) {
        match out {
            Out::Motion(dx, dy) => self.pointer_motion(dx * factor, dy * factor, time),
            other => self.trackpad_apply(other, time),
        }
    }

    /// A key of the keyboard panel pressed or let go.
    fn laptop_key(&mut self, index: usize, pressed: bool, time: u32) {
        let Some(def) = self.laptop.layout.as_ref().and_then(|l| l.keys.get(index)).map(|k| k.def.clone())
        else {
            return;
        };
        match def.kind {
            Kind::Exit => {}
            Kind::Mod(kind) => {
                let evs = if pressed {
                    self.laptop.mods.mod_down(kind, def.code)
                } else {
                    self.laptop.mods.mod_up(kind, def.code)
                };
                for ev in evs {
                    self.laptop_emit(ev, time);
                }
                self.events_dirty = true;
            }
            Kind::Layout => {}
            Kind::Char | Kind::Key => {
                let code = def.code;
                let latched_before = !self.laptop.mods.latched().is_empty();
                let evs =
                    if pressed { self.laptop.mods.key_down(code) } else { self.laptop.mods.key_up(code) };
                for ev in evs {
                    self.laptop_emit(ev, time);
                }
                if latched_before != !self.laptop.mods.latched().is_empty()
                    || def.code == laptop::code::CAPSLOCK
                {
                    self.events_dirty = true;
                }
            }
        }
    }

    /// One key event into the seat, as a hardware key's.
    fn laptop_emit(&mut self, ev: Ev, time: u32) {
        let (code, state) = match ev {
            Ev::Press(code) => (code, KeyState::Pressed),
            Ev::Release(code) => (code, KeyState::Released),
        };
        crate::input::key_input(self, Keycode::new(code + 8), state, time);
    }

    /// The layout key went down: held [`laptop::LAYOUT_HOLD_MS`], it brings
    /// up the list of layouts (the finger then slides onto one, or lifts and
    /// taps one).
    fn laptop_arm_hold(&mut self, slot: u32, index: usize, down: u32) {
        let timer = Timer::from_duration(std::time::Duration::from_millis(laptop::LAYOUT_HOLD_MS as u64));
        let _ = self.loop_handle.insert_source(timer, move |_, _, state: &mut DuoWc| {
            if let Some(Touch::Key { index: i, time }) = state.laptop.touches.get(&slot).copied() {
                if i == index && time == down && state.laptop_on() {
                    state.laptop_open_menu(slot, index);
                }
            }
            TimeoutAction::Drop
        });
    }

    /// The list of layouts up, the finger `slot` (on the layout key `index`)
    /// now on it.
    fn laptop_open_menu(&mut self, slot: u32, index: usize) {
        let Some(layout) = self.laptop.layout.as_ref() else {
            return;
        };
        let active = self.keyboard_active();
        let menu = laptop::menu(layout, &self.kbd.list, active);
        info!(layouts = menu.items.len(), "laptop keyboard: the list of layouts");
        self.laptop.menu = Some(menu);
        self.laptop.touches.insert(slot, Touch::Menu { item: None });
        if let Some(n) = self.laptop.pressed.get_mut(&index) {
            *n -= 1;
            if *n == 0 {
                self.laptop.pressed.remove(&index);
            }
        }
        self.events_dirty = true;
        self.laptop_redraw();
    }

    fn laptop_close_menu(&mut self) {
        if self.laptop.menu.take().is_some() {
            self.events_dirty = true;
            self.laptop_redraw();
        }
    }

    /// An entry of the list picked: that layout in force (added to the list
    /// first if it is not in it).
    fn laptop_pick(&mut self, item: usize) {
        let Some(entry) = self.laptop.menu.as_ref().and_then(|m| m.items.get(item)).map(|i| i.entry.clone())
        else {
            return;
        };
        self.laptop_close_menu();
        if let Err(err) = self.keyboard_pick(entry.clone()) {
            warn!(%err, layout = entry.layout, "laptop keyboard: the layout was not picked");
        }
    }

    /// Laptop mode as the event stream tells it.
    pub fn laptop_summary(&self) -> laptop::Summary {
        let on = self.laptop_on();
        let caps = self.seat.get_keyboard().map(|k| k.modifier_state().caps_lock).unwrap_or(false);
        let group = if on { self.laptop_group() } else { None };
        let layout = if on {
            // Laid out for the layout in force (a plugged keyboard may have
            // switched it since the keys were last laid out).
            let form = group.map(|g| g.form).unwrap_or_default();
            self.laptop.layout.as_ref().map(|l| {
                if l.form == form {
                    laptop::LayoutSummary::of(l, group)
                } else {
                    laptop::LayoutSummary::of(&laptop::layout(l.size.0, l.size.1, form), group)
                }
            })
        } else {
            None
        };
        let altgr = on
            && group.is_some_and(|g| g.altgr)
            && self.seat.get_keyboard().map(|k| k.modifier_state().iso_level3_shift).unwrap_or(false);
        laptop::Summary {
            on,
            origin: self.laptop.on,
            auto: self.laptop.config.auto,
            keys_pref: self.laptop.config.keys.clone(),
            desk: on.then(|| self.laptop.desk.clone()),
            keys: on.then(|| self.laptop.keys.clone()),
            layout,
            shift: on && self.laptop.mods.active(laptop::ModKind::Shift),
            caps: on && caps,
            altgr,
            latched: if on { self.laptop.mods.latched() } else { Vec::new() },
            menu: if on { self.laptop.menu.as_ref().map(laptop::MenuSummary::of) } else { None },
        }
    }

    /// Lay the keyboard panel out now if it is not (before a snapshot: the
    /// shell gets the layout with the first state of laptop mode).
    pub fn laptop_prepare(&mut self) {
        if self.laptop_on() {
            let _ = self.laptop_layout();
        }
    }

    /// Whether Caps Lock is on (the keyboard panel draws its key ringed).
    pub fn laptop_caps(&self) -> bool {
        self.seat.get_keyboard().map(|k| k.modifier_state().caps_lock).unwrap_or(false)
    }

    /// The keyboard panel's picture ([`keyboard_scene`]), for a look (a
    /// screenshot, a screen capture, a rotation's snapshot).
    pub fn laptop_scene(&mut self, output: &Output, front: Option<Element>) -> Vec<Element> {
        self.laptop_prepare();
        let caps = self.laptop_caps();
        let size = self.space.output_geometry(output).map(|g| g.size);
        let Some(device) = self.tty.device.as_mut() else {
            return front.into_iter().collect();
        };
        keyboard_scene(&mut self.laptop, &mut device.renderer, output, size, caps, front)
    }
}

/// The keyboard panel's picture, front to back: `front` (a rotation's
/// turning picture), the shell's labels, the key caps, the touchpad, the
/// opaque background; nothing else of the panel (no window, no bar, no
/// home, no wallpaper: its layer surfaces stay mapped, not drawn). The live
/// render (`backend::render`) and every look ([`DuoWc::laptop_scene`]) draw
/// THIS, so a screenshot shows what the glass shows. `size`: the panel's
/// logical size; the layout must be ready ([`DuoWc::laptop_prepare`]).
pub fn keyboard_scene(
    laptop: &mut Laptop,
    renderer: &mut GlesRenderer,
    output: &Output,
    size: Option<smithay::utils::Size<i32, Logical>>,
    caps: bool,
    front: Option<Element>,
) -> Vec<Element> {
    let mut elements: Vec<Element> = front.into_iter().collect();
    let Some(size) = size else {
        return elements;
    };
    let scale = output.current_scale().fractional_scale();
    if laptop.layout.is_some() {
        let pad_touched = laptop.touches.values().any(|t| matches!(t, Touch::Pad));
        // The shell's labels, in front.
        {
            let layers = layer_map_for_output(output);
            for layer in layers.layers() {
                if layer.namespace() != laptop::LABELS_NAMESPACE {
                    continue;
                }
                let Some(geometry) = layers.layer_geometry(layer) else {
                    continue;
                };
                let location: Point<i32, Physical> = geometry.loc.to_physical_precise_round(scale);
                elements.extend(AsRenderElements::<GlesRenderer>::render_elements::<Element>(
                    layer,
                    renderer,
                    location,
                    smithay::utils::Scale::from(scale),
                    1.0,
                ));
            }
        }
        elements.extend(key_elements(laptop, renderer, scale, caps, pad_touched));
    }
    // Opaque: nothing of the panel's own scene shows through.
    let physical: Rectangle<i32, Physical> =
        Rectangle::from_size(size.to_f64().to_physical(scale).to_i32_round());
    elements.push(Element::Solid(SolidColorRenderElement::new(
        laptop.background.clone(),
        physical,
        CommitCounter::default(),
        BACKGROUND,
        ElementKind::Unspecified,
    )));
    elements
}

/// The keyboard panel's background (straight black would be an OLED's
/// "off": a faint blue-black reads as a surface).
const BACKGROUND: [f32; 4] = [0.028, 0.032, 0.042, 1.0];

/// White at `alpha`, premultiplied.
fn white(alpha: f32) -> [f32; 4] {
    [alpha, alpha, alpha, alpha]
}

/// The key caps and the touchpad, front to back (the pressed keys' caps in
/// the accent colour, the latched modifiers and Caps Lock ringed).
fn key_elements(
    laptop: &mut Laptop,
    renderer: &mut GlesRenderer,
    scale: f64,
    caps: bool,
    pad_touched: bool,
) -> Vec<Element> {
    let Some(layout) = laptop.layout.clone() else {
        return Vec::new();
    };
    let latched = laptop.mods.latched();
    let mut out = Vec::with_capacity(layout.keys.len() + 12);
    if let Some(menu) = laptop.menu.clone() {
        // The list of layouts, in front of the keys: its entries (the one
        // in force in the accent colour, the configured ones brighter, the
        // one under a finger lit), on an opaque panel.
        let touched: Vec<usize> = laptop
            .touches
            .values()
            .filter_map(|t| if let Touch::Menu { item } = t { *item } else { None })
            .collect();
        for (i, item) in menu.items.iter().enumerate() {
            let (fill, edge) = if touched.contains(&i) {
                (premul(ACCENT, 0.85), premul(ACCENT, 1.0))
            } else if item.active {
                (premul(ACCENT, 0.45), premul(ACCENT, 1.0))
            } else if item.configured {
                (white(0.12), white(0.30))
            } else {
                (white(0.05), white(0.12))
            };
            out.push(laptop.gfx.rounded(
                renderer,
                &format!("lt/menu/{i}"),
                RoundedBox { rect: item.cap, radius: 9.0, fill, edge, border: 1.0, soft: 0.0, invert: false },
                scale,
            ));
        }
        out.push(laptop.gfx.rounded(
            renderer,
            "lt/menu",
            RoundedBox {
                rect: menu.rect,
                radius: 14.0,
                fill: [0.045, 0.05, 0.065, 1.0],
                edge: premul(ACCENT, 0.6),
                border: 1.5,
                soft: 0.0,
                invert: false,
            },
            scale,
        ));
    }
    for (i, key) in layout.keys.iter().enumerate() {
        let pressed = laptop.pressed.contains_key(&i);
        let on = match key.def.kind {
            Kind::Mod(kind) => latched.contains(&kind),
            Kind::Key => caps && key.def.code == laptop::code::CAPSLOCK,
            Kind::Char | Kind::Exit | Kind::Layout => false,
        };
        let special = !matches!(key.def.kind, Kind::Char);
        let (fill, edge, border) = if pressed {
            (premul(ACCENT, 0.85), premul(ACCENT, 1.0), 1.0)
        } else if on {
            (premul(ACCENT, 0.32), premul(ACCENT, 0.95), 1.5)
        } else if key.def.kind == Kind::Layout {
            (premul(ACCENT, 0.16), premul(ACCENT, 0.38), 1.0)
        } else if special {
            (white(0.05), white(0.10), 1.0)
        } else {
            (white(0.085), white(0.13), 1.0)
        };
        out.push(laptop.gfx.rounded(
            renderer,
            &format!("lt/{i}"),
            RoundedBox { rect: key.cap, radius: 7.0, fill, edge, border, soft: 0.0, invert: false },
            scale,
        ));
    }
    let (fill, edge) = if pad_touched { (white(0.08), white(0.22)) } else { (white(0.05), white(0.15)) };
    out.push(laptop.gfx.rounded(
        renderer,
        "lt/pad",
        RoundedBox { rect: layout.touchpad, radius: 18.0, fill, edge, border: 1.0, soft: 0.0, invert: false },
        scale,
    ));
    out
}
