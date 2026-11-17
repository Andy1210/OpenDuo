//! Splits, live: which windows share a panel, where they go, the divider under
//! a finger, and how it is drawn ([`crate::split`] is the pure part).
//!
//! A split is in force on a panel while both of its windows are placed there
//! (a pose that moves one away suspends it, [`crate::split`]). In force, each
//! window is sized to its half; the two are raised together, so touching
//! either brings the pair to the front with the touched one focused. While
//! the pair is the front of its panel the divider shows, and a finger on it
//! (the compositor's, never a client's) shares the room differently; let go
//! at the panel's edge it ends the split.

use std::collections::HashMap;

use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::element::memory::{MemoryRenderBuffer, MemoryRenderBufferRenderElement};
use smithay::backend::renderer::element::solid::SolidColorRenderElement;
use smithay::backend::renderer::element::{Id, Kind};
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::backend::renderer::utils::CommitCounter;
use smithay::desktop::Window;
use smithay::output::Output;
use smithay::utils::{Logical, Physical, Point, Rectangle, Size, Transform};
use tracing::{debug, info};

use crate::render::Element;
use crate::rotation::Rotation;
use crate::split::{self, Axis, DividerRelease, Side, Split, Tiles};
use crate::state::{window_data, DuoWc, Placement};

/// The gap between the halves: black, as the panels are AMOLED (black is off).
const GAP_COLOR: [f32; 4] = [0.0, 0.0, 0.0, 1.0];
/// The divider's handle: light grey at rest, the accent blue while dragged.
const HANDLE_REST: [u8; 3] = [196, 199, 205];
const HANDLE_DRAG: [u8; 3] = [54, 133, 227];
/// The handle's size along and across the divider (logical px).
const HANDLE_LONG: f64 = 48.0;
const HANDLE_SHORT: f64 = 4.0;

/// A finger on the divider.
#[derive(Debug, Clone)]
pub struct DividerTouch {
    /// The seat slot.
    pub slot: u32,
    /// The panel.
    pub output: String,
}

/// What the divider is drawn with, made once.
#[derive(Default)]
pub struct SplitGfx {
    gaps: HashMap<String, Id>,
    handles: HashMap<(i32, i32, bool), MemoryRenderBuffer>,
}

/// A panel's split as it is drawn this frame, in the panel's logical frame.
#[derive(Debug, Clone)]
pub struct SplitPlan {
    /// Each window's half (window id, rectangle).
    pub tiles: [(u64, Rectangle<i32, Logical>); 2],
    /// The gap between them.
    pub divider: Rectangle<i32, Logical>,
    /// How they sit.
    pub axis: Axis,
    /// The pair is the front of its panel: the divider shows.
    pub front: bool,
    /// A finger is on the divider.
    pub dragging: bool,
}

impl SplitPlan {
    /// The half a window is cut to, if it is one of the two.
    pub fn tile_of(&self, id: u64) -> Option<Rectangle<i32, Logical>> {
        self.tiles.iter().find(|(w, _)| *w == id).map(|(_, r)| *r)
    }
}

fn rect(r: crate::layout::Rect) -> Rectangle<i32, Logical> {
    Rectangle::new((r.0, r.1).into(), (r.2, r.3).into())
}

fn tuple(r: Rectangle<i32, Logical>) -> crate::layout::Rect {
    (r.loc.x, r.loc.y, r.size.w, r.size.h)
}

/// A window's minimum size as its client last committed it (0 = none).
fn min_size(window: &Window) -> (i32, i32) {
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

/// The divider's elements, front to back: the handle, then the gap.
pub fn elements(
    renderer: &mut GlesRenderer,
    gfx: &mut SplitGfx,
    plan: &SplitPlan,
    output: &Output,
) -> Vec<Element> {
    if !plan.front {
        return Vec::new();
    }
    let scale = output.current_scale().fractional_scale();
    let d = plan.divider;
    let to_phys = |x: f64, y: f64, w: f64, h: f64| -> Rectangle<i32, Physical> {
        let x0 = (x * scale).round() as i32;
        let y0 = (y * scale).round() as i32;
        let x1 = ((x + w) * scale).round() as i32;
        let y1 = ((y + h) * scale).round() as i32;
        Rectangle::new((x0, y0).into(), (x1 - x0, y1 - y0).into())
    };
    let mut out = Vec::new();

    let (cx, cy) = (d.loc.x as f64 + d.size.w as f64 / 2.0, d.loc.y as f64 + d.size.h as f64 / 2.0);
    let (hw, hh) = match plan.axis {
        Axis::SideBySide => (HANDLE_SHORT, HANDLE_LONG),
        Axis::Stacked => (HANDLE_LONG, HANDLE_SHORT),
    };
    let handle = to_phys(cx - hw / 2.0, cy - hh / 2.0, hw, hh);
    let (pw, ph) = (handle.size.w.max(1), handle.size.h.max(1));
    let buffer = gfx.handles.entry((pw, ph, plan.dragging)).or_insert_with(|| {
        let color = if plan.dragging { HANDLE_DRAG } else { HANDLE_REST };
        let pixels = crate::icons::pill(pw as usize, ph as usize, color);
        MemoryRenderBuffer::from_slice(&pixels, Fourcc::Argb8888, (pw, ph), 1, Transform::Normal, None)
    });
    if let Ok(element) = MemoryRenderBufferRenderElement::from_buffer(
        renderer,
        handle.loc.to_f64(),
        buffer,
        None,
        None,
        // The destination size is logical (the output's scale applies).
        Some(Size::from((hw.round() as i32, hh.round() as i32))),
        Kind::Unspecified,
    ) {
        out.push(Element::Icon(element));
    }

    let id = gfx.gaps.entry(output.name()).or_insert_with(Id::new).clone();
    let gap = to_phys(d.loc.x as f64, d.loc.y as f64, d.size.w as f64, d.size.h as f64);
    out.push(Element::Solid(SolidColorRenderElement::new(
        id,
        gap,
        CommitCounter::default(),
        GAP_COLOR,
        Kind::Unspecified,
    )));
    out
}

impl DuoWc {
    /// How an output is turned.
    pub fn rotation_of(&self, output: &Output) -> Rotation {
        Rotation::from_transform(output.current_transform())
    }

    /// The split of a panel while it is in force: both of its windows are
    /// mapped and placed on that panel, and the pose uses the panel.
    pub fn active_split(&self, output: &Output) -> Option<Split> {
        let name = output.name();
        if !self.pose.in_use(&name) {
            return None;
        }
        // On a desk the windows float (laptop mode's desk panel): the split
        // waits, kept, until the panel is a panel again.
        if self.is_desk_output(&name) {
            return None;
        }
        let split = self.splits.iter().find(|s| s.output == name)?;
        for id in [split.first, split.second] {
            let window = self.window_by_id(id)?;
            if *window_data(&window).placement.borrow() != Placement::Output(name.clone()) {
                return None;
            }
        }
        Some(split.clone())
    }

    /// A split's halves on its panel, in the panel's own frame; a window
    /// that cannot be smaller than some size (its xdg min_size) gets it when
    /// the room allows.
    fn local_tiles(&self, output: &Output, split: &Split) -> Tiles {
        let zone = self.window_zone(output);
        split::tiles_fitting(split, self.rotation_of(output), tuple(zone), |id| {
            self.window_by_id(id).map(|w| min_size(&w)).unwrap_or((0, 0))
        })
    }

    /// The rectangle a window gets as half of a split in force (global).
    pub fn tile_rect(&self, window: &Window) -> Option<Rectangle<i32, Logical>> {
        let id = window_data(window).id;
        let split = self.splits.iter().find(|s| s.has(id))?;
        let output = self.output_by_name(&split.output)?;
        let split = self.active_split(&output)?;
        let origin = self.space.output_geometry(&output)?.loc;
        let mut r = rect(self.local_tiles(&output, &split).rect_of(id)?);
        r.loc += origin;
        Some(r)
    }

    /// The split shown at the front of its panel: in force, its two windows
    /// the two frontmost there, and neither fullscreen.
    pub fn front_split(&self, output: &Output) -> Option<(Split, Tiles)> {
        let split = self.active_split(output)?;
        let front: Vec<Window> = self
            .space
            .elements()
            .rev()
            .filter(|w| self.is_on(w, output) && !self.is_minimized(w))
            .take(2)
            .cloned()
            .collect();
        let ids: Vec<u64> = front.iter().map(|w| window_data(w).id).collect();
        if ids.len() != 2 || !split.has(ids[0]) || !split.has(ids[1]) {
            return None;
        }
        if front.iter().any(|w| *window_data(w).fullscreen.borrow()) {
            return None;
        }
        let tiles = self.local_tiles(output, &split);
        Some((split, tiles))
    }

    /// What the renderer needs of a panel's split, if one is in force.
    pub fn split_plan(&self, output: &Output) -> Option<SplitPlan> {
        let split = self.active_split(output)?;
        let tiles = self.local_tiles(output, &split);
        let front = self.front_split(output).is_some() && !self.overview_on(output);
        Some(SplitPlan {
            tiles: [(tiles.first.0, rect(tiles.first.1)), (tiles.second.0, rect(tiles.second.1))],
            divider: rect(tiles.divider),
            axis: tiles.axis,
            front,
            dragging: self.divider.as_ref().map(|d| d.output == output.name()).unwrap_or(false),
        })
    }

    /// Bring a window to the front of its panel; if it is half of a split in
    /// force, its partner comes along just behind it.
    pub fn raise_window(&mut self, window: &Window, activate: bool) {
        let id = window_data(window).id;
        let partner = self
            .splits
            .iter()
            .find(|s| s.has(id))
            .and_then(|s| {
                let output = self.output_by_name(&s.output)?;
                self.active_split(&output)?.partner(id)
            })
            .and_then(|p| self.window_by_id(p));
        if let Some(partner) = partner {
            self.space.raise_element(&partner, false);
        }
        self.space.raise_element(window, activate);
        // Android's keyboard stays in front of the app typed in.
        self.follow_android_ime();
    }

    /// Forget the split a window is half of. True if there was one; the
    /// caller re-places the windows.
    pub fn forget_split_of(&mut self, id: u64) -> bool {
        let before = self.splits.len();
        self.splits.retain(|s| !s.has(id));
        if self.splits.len() != before {
            info!(id, "split ended");
            self.events_dirty = true;
            true
        } else {
            false
        }
    }

    /// End the split of a panel (by name): both windows fill the panel again,
    /// the front one stays in front. False if it had none.
    pub fn unsplit(&mut self, output: &str) -> bool {
        let before = self.splits.len();
        self.splits.retain(|s| s.output != output);
        if self.splits.len() == before {
            return false;
        }
        info!(output, "split ended");
        self.events_dirty = true;
        self.place_all();
        self.queue_redraw_all();
        true
    }

    /// Put `window` on the `side` half of `output` and another window on
    /// the other half.
    ///
    /// The other window: `with` if given; if the panel is split already, the
    /// window's partner when it is one of the two (it changes sides), else
    /// the one across from the half it is put on (the one on that half
    /// leaves the split and stays behind, filling the panel); otherwise the
    /// panel's front window, or `fallback`. Both windows become the panel's
    /// own (a pose no longer brings them anywhere), the pair comes to the
    /// front and `window` gets the keyboard.
    pub fn snap_window(
        &mut self,
        window: &Window,
        output: &Output,
        side: Side,
        with: Option<Window>,
        fallback: Option<Window>,
    ) -> anyhow::Result<u64> {
        let name = output.name();
        if !self.pose.in_use(&name) {
            anyhow::bail!("{name} is not in use in pose {}", self.pose.describe());
        }
        let id = window_data(window).id;
        let rotation = self.rotation_of(output);
        let active = self.active_split(output);
        let partner = match with {
            Some(partner) => Some(partner),
            None => match &active {
                Some(split) if split.has(id) => split.partner(id).and_then(|p| self.window_by_id(p)),
                Some(split) => {
                    let across = if split.axis(rotation) == side.axis() {
                        let (a, b, _) = split.reading_order(rotation);
                        if side.is_first() {
                            b
                        } else {
                            a
                        }
                    } else {
                        // The pair's front window.
                        self.space
                            .elements()
                            .rev()
                            .map(|w| window_data(w).id)
                            .find(|w| split.has(*w))
                            .unwrap_or(split.first)
                    };
                    self.window_by_id(across)
                }
                None => self
                    .space
                    .elements()
                    .rev()
                    .find(|w| {
                        *w != window && *window_data(w).placement.borrow() == Placement::Output(name.clone())
                    })
                    .cloned(),
            },
        }
        .or(fallback)
        .ok_or_else(|| anyhow::anyhow!("no other window on {name} to share it with"))?;
        let partner_id = window_data(&partner).id;
        if partner_id == id {
            anyhow::bail!("a window cannot share a panel with itself");
        }

        // The same two on the same axis keep how they shared the room.
        let share = active
            .as_ref()
            .filter(|s| s.has(id) && s.has(partner_id) && s.axis(rotation) == side.axis())
            .map(|s| {
                let (a, _, share) = s.reading_order(rotation);
                let window_share = if a == id { share } else { 1.0 - share };
                // It changes sides: it takes the share of the side it goes to.
                let now_first = s.side_of(id, rotation).map(|x| x.is_first()).unwrap_or(true);
                if now_first == side.is_first() {
                    window_share
                } else {
                    1.0 - window_share
                }
            })
            .unwrap_or(0.5);

        // One split per panel, one split per window.
        self.splits.retain(|s| s.output != name && !s.has(id) && !s.has(partner_id));
        for w in [window, &partner] {
            let data = window_data(w);
            *data.placement.borrow_mut() = Placement::Output(name.clone());
            *data.saved.borrow_mut() = None;
        }
        let split = Split::new(&name, id, partner_id, side, rotation, share);
        info!(output = name, window = id, partner = partner_id, side = side.name(), "split");
        self.splits.push(split);
        self.events_dirty = true;
        // An Android window in a half: the display takes the half's size
        // (crate::android).
        self.android_laid_out(window);

        self.place_all();
        self.raise_window(window, true);
        if self.is_lit(&name) {
            self.active_output = Some(name);
        }
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
        Ok(partner_id)
    }

    /// Set how a panel's split shares the room: `share` is the window that
    /// reads first's (left or top), as the panel reads now.
    pub fn set_split_share(&mut self, output: &Output, share: f64) -> anyhow::Result<()> {
        let rotation = self.rotation_of(output);
        let Some(split) = self.splits.iter_mut().find(|s| s.output == output.name()) else {
            anyhow::bail!("{} has no split", output.name());
        };
        let share = share.clamp(split::MIN_SHARE, 1.0 - split::MIN_SHARE);
        let (_, flipped) = split::orient(split.columns, rotation);
        split.ratio = if flipped { 1.0 - share } else { share };
        self.events_dirty = true;
        self.place_all();
        self.queue_redraw_all();
        Ok(())
    }

    /// Whether a touch going down at `location` (global) grabs a divider: the
    /// split is at the front of that panel and the finger is on its line.
    pub fn divider_takes(&self, output: &Output, location: Point<f64, Logical>) -> bool {
        if self.lock.locked || self.overview_on(output) || self.divider.is_some() {
            return false;
        }
        let Some((_, tiles)) = self.front_split(output) else {
            return false;
        };
        let Some(origin) = self.space.output_geometry(output).map(|g| g.loc.to_f64()) else {
            return false;
        };
        let local = location - origin;
        split::divider_hit(&tiles, local.x, local.y)
    }

    /// A finger went down on a divider.
    pub fn divider_touch_down(&mut self, slot: u32, output: &Output) {
        debug!(output = output.name(), slot, "divider grabbed");
        self.note_activity();
        self.divider = Some(DividerTouch { slot, output: output.name() });
        self.queue_redraw_all();
    }

    /// The finger on the divider moved; true if it was that finger.
    pub fn divider_touch_motion(&mut self, slot: u32, location: Point<f64, Logical>) -> bool {
        let Some(touch) = self.divider.clone().filter(|d| d.slot == slot) else {
            return false;
        };
        self.note_activity();
        if let Some(slot) = self.touch_slots.get_mut(&slot) {
            slot.location = location;
        }
        let Some((output, split, zone, local)) = self.divider_frame(&touch, location) else {
            self.divider = None;
            return true;
        };
        let moved = split::dragged(&split, self.rotation_of(&output), zone, local.x, local.y);
        if moved.ratio != split.ratio {
            if let Some(s) = self.splits.iter_mut().find(|s| s.output == split.output) {
                s.ratio = moved.ratio;
            }
            self.place_all();
            self.queue_redraw_all();
        }
        true
    }

    /// The finger left the divider; true if it was that finger.
    pub fn divider_touch_up(&mut self, slot: u32) -> bool {
        let Some(touch) = self.divider.clone().filter(|d| d.slot == slot) else {
            return false;
        };
        self.divider = None;
        let location = self.touch_slots.get(&slot).map(|s| s.location).unwrap_or_default();
        if let Some((output, split, zone, local)) = self.divider_frame(&touch, location) {
            match split::release(&split, self.rotation_of(&output), zone, local.x, local.y) {
                DividerRelease::Keep(kept) => {
                    info!(output = kept.output, ratio = kept.ratio, "divider moved");
                    if let Some(s) = self.splits.iter_mut().find(|s| s.output == kept.output) {
                        s.ratio = kept.ratio;
                    }
                    self.events_dirty = true;
                    self.place_all();
                }
                DividerRelease::Dissolve { keep } => {
                    info!(output = split.output, keep, "divider dragged to the edge");
                    self.unsplit(&split.output);
                    if let Some(window) = self.window_by_id(keep) {
                        self.raise_window(&window, true);
                        self.refresh_keyboard_focus();
                    }
                }
            }
        }
        self.queue_redraw_all();
        true
    }

    /// The divider touch's panel, split, zone (panel frame) and the finger
    /// in the panel's frame.
    fn divider_frame(
        &self,
        touch: &DividerTouch,
        location: Point<f64, Logical>,
    ) -> Option<(Output, Split, crate::layout::Rect, Point<f64, Logical>)> {
        let output = self.output_by_name(&touch.output)?;
        let split = self.active_split(&output)?;
        let geometry = self.space.output_geometry(&output)?;
        let zone = tuple(self.window_zone(&output));
        Some((output, split, zone, location - geometry.loc.to_f64()))
    }

    /// The window a point is on when its panel shows a split at the front:
    /// a popup of either window first (a menu may cross the divider), then
    /// the window whose half it is. `None` if no split is shown there.
    pub fn split_window_under(&self, output: &Output, point: Point<f64, Logical>) -> Option<Option<Window>> {
        let (split, tiles) = self.front_split(output)?;
        let origin = self.space.output_geometry(output)?.loc;
        let members: Vec<Window> =
            self.space.elements().rev().filter(|w| split.has(window_data(w).id)).cloned().collect();
        for window in &members {
            let Some(loc) = self.space.element_location(window) else {
                continue;
            };
            let render_loc = loc - window.geometry().loc;
            if window
                .surface_under(point - render_loc.to_f64(), smithay::desktop::WindowSurfaceType::POPUP)
                .is_some()
            {
                return Some(Some(window.clone()));
            }
        }
        let local = point - origin.to_f64();
        for (id, r) in [tiles.first, tiles.second] {
            if rect(r).to_f64().contains(local) {
                return Some(members.iter().find(|w| window_data(w).id == id).cloned());
            }
        }
        // The divider's gap: nobody's.
        Some(None)
    }

    /// The panel's split, as the control socket and the event stream report
    /// it (in reading order, as the panel reads now).
    pub fn split_summaries(&self) -> Vec<crate::events::SplitSummary> {
        self.splits
            .iter()
            .filter_map(|s| {
                let output = self.output_by_name(&s.output)?;
                let rotation = self.rotation_of(&output);
                let (a, b, share) = s.reading_order(rotation);
                Some(crate::events::SplitSummary {
                    output: s.output.clone(),
                    axis: s.axis(rotation),
                    windows: [a, b],
                    share: (share * 1000.0).round() / 1000.0,
                    active: self.active_split(&output).is_some(),
                    front: self.front_split(&output).is_some(),
                })
            })
            .collect()
    }
}
