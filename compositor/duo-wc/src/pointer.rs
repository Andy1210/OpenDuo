//! The pointer: a mouse or a touchpad (Bluetooth, or USB), mostly for the
//! desktop on an external display ([`crate::extout`], [`crate::desk`]).
//!
//! The phone itself has no pointer device: the built-in digitizer's own HID
//! collections ("spi 045E:0C1D ...") are never one (the touch and the pen
//! come from duo2-touchd's devices, [`crate::input`]). The seat gets its
//! `wl_pointer` capability only while a real pointer device is plugged, so
//! a phone without a mouse looks to the apps exactly as before.
//!
//! - **Where it goes:** anywhere on the lit outputs (the panels and the
//!   external display, laid out side by side, [`crate::layout`]); a motion
//!   that would leave them stops at the edge of the output it is on.
//! - **What it touches:** the surface under it, by the same rules as a
//!   touch ([`crate::state::DuoWc::surface_under`]: the lock screen alone
//!   while locked, layers and windows otherwise); a press focuses the window
//!   under it and raises it (click to focus) and makes its output the
//!   active one.
//! - **Desk windows** (on an external display) move with Super + the left
//!   button anywhere on them, or when their client asks (a title bar
//!   dragged: `xdg_toplevel.move`), and resize with Super + the right
//!   button or by their border (`xdg_toplevel.resize`). Let go at a display
//!   edge, a moved window snaps (a half, or maximized at the top); let go on
//!   another output, it goes there (a panel: filling it; another display:
//!   floating where the pointer is). A panel's window carried by the
//!   pointer (Super + left, or its title bar) goes to the output it is let
//!   go on.
//! - **The cursor** is drawn by the compositor in front of everything on
//!   the output it is on: the client's cursor surface when it sets one,
//!   the built-in arrow otherwise; hidden by a touch on a panel, shown
//!   again by the next motion.
//!
//! The pure parts (the clamp, the arrow picture, which devices count) are
//! tested here; the rest needs a seat and a display.

use std::collections::{HashMap, HashSet};

use smithay::backend::allocator::Fourcc;
use smithay::backend::input::{Axis, AxisSource, ButtonState};
use smithay::backend::renderer::element::memory::{MemoryRenderBuffer, MemoryRenderBufferRenderElement};
use smithay::backend::renderer::element::surface::{
    render_elements_from_surface_tree, WaylandSurfaceRenderElement,
};
use smithay::backend::renderer::element::Kind;
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::input::pointer::{
    AxisFrame, ButtonEvent, CursorImageStatus, CursorImageSurfaceData, MotionEvent,
};
use smithay::output::Output;
use smithay::reexports::input::{self as libinput, DeviceCapability};
use smithay::utils::IsAlive as _;
use smithay::utils::{Logical, Physical, Point, Rectangle, Size, Transform, SERIAL_COUNTER};
use tracing::{debug, info};

use crate::desk::{Drag, DragKind, Edges};
use crate::render::Element;
use crate::state::{window_data, DuoWc, Placement};

/// evdev button codes.
pub const BTN_LEFT: u32 = 0x110;
pub const BTN_RIGHT: u32 = 0x111;

/// The built-in arrow's size, logical px (scaled with the output).
pub const CURSOR_SIZE: f64 = 24.0;

/// The pointer's state.
#[derive(Default)]
pub struct Pointer {
    /// Where it is, in the global space.
    pub location: (f64, f64),
    /// The cursor is drawn (a pointer moved since the last touch).
    pub shown: bool,
    /// What the client under it asked to show.
    pub image: Option<CursorImageStatus>,
    /// The pointer devices plugged (libinput sysnames).
    pub devices: HashSet<String>,
    /// Buttons held now.
    pub held: HashSet<u32>,
    /// The serial of the last press (a client's move / resize request
    /// names it).
    pub press_serial: Option<smithay::utils::Serial>,
    /// The built-in arrow, by its size in physical px.
    arrows: HashMap<i32, MemoryRenderBuffer>,
    /// Desk windows re-placed at a new size (maximized, restored, a half):
    /// the size asked for. When one commits at that size, the surface under
    /// the still pointer gets it again (`pointer_refocus`): until then the
    /// client kept the pointer where its old layout had it, and a double
    /// click on the restored window's body was taken for its old title bar.
    pub refocus: HashMap<u64, smithay::utils::Size<i32, smithay::utils::Logical>>,
}

/// Whether a libinput device is the phone's own digitizer (its HID
/// collections, which libinput may see as a mouse): never a pointer here.
pub fn is_builtin(name: &str, vendor: u32, product: u32) -> bool {
    (vendor == 0x045e && product == 0x0c1d)
        || name.starts_with("spi 045E:0C1D")
        || name.starts_with("Surface Duo 2 ")
}

/// Where a motion from `from` to `to` ends, given the rectangles of the
/// outputs it may be on `(x, y, w, h)` (global, logical): `to` if it is on
/// one of them, else `to` held to the output `from` is on (to the nearest
/// output when `from` is on none).
pub fn clamp_to(rects: &[(f64, f64, f64, f64)], from: (f64, f64), to: (f64, f64)) -> (f64, f64) {
    let inside = |r: &(f64, f64, f64, f64), p: (f64, f64)| {
        p.0 >= r.0 && p.0 < r.0 + r.2 && p.1 >= r.1 && p.1 < r.1 + r.3
    };
    if rects.is_empty() || rects.iter().any(|r| inside(r, to)) {
        return to;
    }
    let hold = |r: &(f64, f64, f64, f64), p: (f64, f64)| {
        (p.0.clamp(r.0, r.0 + (r.2 - 1.0).max(0.0)), p.1.clamp(r.1, r.1 + (r.3 - 1.0).max(0.0)))
    };
    let home = rects.iter().find(|r| inside(r, from)).copied().unwrap_or_else(|| {
        // Nearest by the distance of the held point.
        *rects
            .iter()
            .min_by(|a, b| {
                let da = dist2(hold(a, to), to);
                let db = dist2(hold(b, to), to);
                da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
            })
            .expect("not empty")
    });
    hold(&home, to)
}

fn dist2(a: (f64, f64), b: (f64, f64)) -> f64 {
    (a.0 - b.0).powi(2) + (a.1 - b.1).powi(2)
}

/// The built-in arrow, `px` x `px`, ARGB8888 premultiplied (little endian:
/// B, G, R, A): white with a dark outline, its tip (the hot spot) at the
/// top left corner, antialiased by 4 x 4 supersampling.
pub fn arrow_pixels(px: usize) -> Vec<u8> {
    // The arrow in a unit square (tip at 0, 0), clockwise.
    const SHAPE: [(f64, f64); 7] =
        [(0.0, 0.0), (0.0, 0.82), (0.21, 0.63), (0.36, 0.96), (0.50, 0.90), (0.35, 0.58), (0.62, 0.58)];
    let inside = |x: f64, y: f64, grow: f64| -> bool {
        // Point in polygon, with the polygon grown by `grow` (the
        // outline): tested as "within `grow` of the shape".
        if point_in(&SHAPE, x, y) {
            return true;
        }
        grow > 0.0 && dist_to_edges(&SHAPE, x, y) <= grow
    };
    let outline = 1.4 / px as f64;
    let inset = 0.6 / px as f64; // keep the tip pixel itself inside
    let mut out = vec![0u8; px * px * 4];
    const SS: usize = 4;
    for py in 0..px {
        for pxx in 0..px {
            let (mut fill, mut edge) = (0usize, 0usize);
            for sy in 0..SS {
                for sx in 0..SS {
                    let x = (pxx as f64 + (sx as f64 + 0.5) / SS as f64) / px as f64 - inset;
                    let y = (py as f64 + (sy as f64 + 0.5) / SS as f64) / px as f64 - inset;
                    if point_in(&SHAPE, x, y) && dist_to_edges(&SHAPE, x, y) > outline {
                        fill += 1;
                    } else if inside(x, y, outline) {
                        edge += 1;
                    }
                }
            }
            let n = (SS * SS) as f64;
            let a = (fill + edge) as f64 / n;
            let white = fill as f64 / n; // premultiplied white
            let dark = 0.08 * edge as f64 / n;
            let v = ((white + dark) * 255.0).round().min(255.0) as u8;
            let i = (py * px + pxx) * 4;
            out[i] = v;
            out[i + 1] = v;
            out[i + 2] = v;
            out[i + 3] = (a * 255.0).round() as u8;
        }
    }
    out
}

fn point_in(poly: &[(f64, f64)], x: f64, y: f64) -> bool {
    let mut inside = false;
    let mut j = poly.len() - 1;
    for i in 0..poly.len() {
        let (xi, yi) = poly[i];
        let (xj, yj) = poly[j];
        if (yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi {
            inside = !inside;
        }
        j = i;
    }
    inside
}

fn dist_to_edges(poly: &[(f64, f64)], x: f64, y: f64) -> f64 {
    let mut best = f64::MAX;
    for i in 0..poly.len() {
        let (ax, ay) = poly[i];
        let (bx, by) = poly[(i + 1) % poly.len()];
        let (dx, dy) = (bx - ax, by - ay);
        let len2 = dx * dx + dy * dy;
        let t = if len2 > 0.0 { (((x - ax) * dx + (y - ay) * dy) / len2).clamp(0.0, 1.0) } else { 0.0 };
        let (cx, cy) = (ax + t * dx, ay + t * dy);
        best = best.min(((x - cx).powi(2) + (y - cy).powi(2)).sqrt());
    }
    best
}

impl DuoWc {
    /// A libinput device came: a pointer device gives the seat its
    /// pointer (the first one).
    pub fn pointer_device_added(&mut self, device: &libinput::Device) {
        if !device.has_capability(DeviceCapability::Pointer) {
            return;
        }
        let name = device.name().into_owned();
        if is_builtin(&name, device.id_vendor(), device.id_product()) {
            debug!(device = name, "the digitizer's pointer collection: not a pointer");
            return;
        }
        self.pointer.devices.insert(device.sysname().to_owned());
        if self.seat.get_pointer().is_none() {
            self.seat.add_pointer();
            // It starts in the middle of the active output.
            if let Some(g) = self.active_output().and_then(|o| self.space.output_geometry(&o)) {
                self.pointer.location =
                    (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
            }
        }
        info!(device = name, "pointer device");
    }

    /// A libinput device went: the last pointer device takes the seat's
    /// pointer with it.
    pub fn pointer_device_removed(&mut self, device: &libinput::Device) {
        if !self.pointer.devices.remove(device.sysname()) {
            return;
        }
        info!(device = %device.name(), "pointer device gone");
        if self.pointer.devices.is_empty() && self.seat.get_pointer().is_some() {
            self.desk.drag = None;
            self.pointer.held.clear();
            self.pointer.shown = false;
            self.seat.remove_pointer();
            self.queue_redraw_all();
        }
    }

    /// The rectangles the pointer may be on: the lit outputs.
    fn pointer_rects(&self) -> Vec<(f64, f64, f64, f64)> {
        self.outputs
            .iter()
            .filter(|o| {
                self.is_lit(&o.name()) && !self.is_laptop_keys(&o.name()) && !self.is_mirror(&o.name())
            })
            .filter_map(|o| self.space.output_geometry(o))
            .map(|g| (g.loc.x as f64, g.loc.y as f64, g.size.w as f64, g.size.h as f64))
            .collect()
    }

    /// The output the pointer is on.
    pub fn pointer_output(&self) -> Option<Output> {
        let (x, y) = self.pointer.location;
        self.output_at((x, y).into())
    }

    /// A relative motion (a mouse, a touchpad), logical px, accelerated.
    pub fn pointer_motion(&mut self, dx: f64, dy: f64, time: u32) {
        let from = self.pointer.location;
        let to = clamp_to(&self.pointer_rects(), from, (from.0 + dx, from.1 + dy));
        self.pointer_moved_to(to, time);
    }

    /// An absolute motion: `fx`, `fy` the fraction (0..1) of the device's
    /// range, mapped to the external display if one is up, else to the
    /// active output.
    pub fn pointer_motion_absolute(&mut self, fx: f64, fy: f64, time: u32) {
        let target = self
            .outputs
            .iter()
            .find(|o| {
                crate::extout::is_external(&o.name()) && self.is_lit(&o.name()) && !self.is_mirror(&o.name())
            })
            .cloned()
            .or_else(|| self.active_output());
        let Some(g) = target.and_then(|o| self.space.output_geometry(&o)) else {
            return;
        };
        let to = (
            g.loc.x as f64 + fx.clamp(0.0, 1.0) * g.size.w as f64,
            g.loc.y as f64 + fy.clamp(0.0, 1.0) * g.size.h as f64,
        );
        self.pointer_moved_to(to, time);
    }

    /// The pointer jumps to `to` (global, logical): the pen's barrel
    /// button right-clicks where the pen is (crate::tablet).
    pub fn pointer_warp(&mut self, to: (f64, f64), time: u32) {
        self.pointer_moved_to(to, time);
    }

    fn pointer_moved_to(&mut self, to: (f64, f64), time: u32) {
        let Some(pointer) = self.seat.get_pointer() else {
            return;
        };
        if self.screen_dark() && self.ext_connected() && self.ambient.is_none() {
            // At a desk: the mouse wakes the screen, as on a computer.
            self.wake("pointer, external display");
        }
        let before = self.pointer_output();
        self.pointer.location = to;
        self.pointer.shown = true;
        self.note_activity();
        let after = self.pointer_output();
        for output in [before, after].into_iter().flatten() {
            self.queue_redraw_output(&output);
        }
        if self.desk.drag.as_ref().map(|d| d.touch.is_none()) == Some(true) {
            self.desk_drag_motion(to);
            return;
        }
        let focus =
            if self.screen_dark() || self.ambient.is_some() { None } else { self.surface_under(to.into()) };
        let serial = SERIAL_COUNTER.next_serial();
        pointer.motion(self, focus, &MotionEvent { location: to.into(), serial, time });
        pointer.frame(self);
    }

    /// A desk window was re-placed (`moved`, or `resized` to `size`): the
    /// pointer's surface and place are told again, at once for a move, at
    /// the window's commit at its new size for a resize.
    pub fn pointer_window_replaced(
        &mut self,
        id: u64,
        moved: bool,
        resized: Option<smithay::utils::Size<i32, smithay::utils::Logical>>,
    ) {
        if self.seat.get_pointer().is_none() {
            return;
        }
        match resized {
            Some(size) => {
                self.pointer.refocus.insert(id, size);
            }
            None if moved => {
                self.pointer.refocus.remove(&id);
                self.pointer_refocus(crate::turnlog::monotonic_now().as_millis() as u32);
            }
            None => {}
        }
    }

    /// A desk window committed: at the size it was re-placed at, the
    /// pointer is refocused (`pointer_window_replaced`).
    pub fn pointer_window_committed(
        &mut self,
        id: u64,
        size: smithay::utils::Size<i32, smithay::utils::Logical>,
    ) {
        if self.pointer.refocus.get(&id) == Some(&size) {
            self.pointer.refocus.remove(&id);
            self.pointer_refocus(crate::turnlog::monotonic_now().as_millis() as u32);
        }
    }

    /// The surface under the pointer gets the pointer again (after a drag,
    /// a window moved or a layout change under a still pointer).
    pub fn pointer_refocus(&mut self, time: u32) {
        if self.seat.get_pointer().is_none() || self.desk.drag.is_some() {
            return;
        }
        let to = self.pointer.location;
        let Some(pointer) = self.seat.get_pointer() else {
            return;
        };
        let focus = if self.screen_dark() { None } else { self.surface_under(to.into()) };
        let serial = SERIAL_COUNTER.next_serial();
        pointer.motion(self, focus, &MotionEvent { location: to.into(), serial, time });
        pointer.frame(self);
    }

    /// A button.
    pub fn pointer_button(&mut self, button: u32, pressed: bool, time: u32) {
        let Some(pointer) = self.seat.get_pointer() else {
            return;
        };
        if self.screen_dark() {
            // Not delivered on a dark screen; at a desk a press wakes it.
            if pressed && self.ext_connected() && self.ambient.is_none() {
                self.wake("pointer button, external display");
            }
            if !pressed {
                self.pointer.held.remove(&button);
            }
            return;
        }
        self.note_activity();
        self.pointer.shown = true;
        let serial = SERIAL_COUNTER.next_serial();
        if pressed {
            self.pointer.held.insert(button);
            self.pointer.press_serial = Some(serial);
            if self.desk.drag.is_none() && !self.lock.locked && self.pointer_press(button, time) {
                // Taken by the compositor (Super + a button: a drag).
                return;
            }
        } else {
            self.pointer.held.remove(&button);
            if self.desk.drag.as_ref().map(|d| d.button) == Some(button) {
                self.desk_drag_end(time);
                return;
            }
        }
        let state = if pressed { ButtonState::Pressed } else { ButtonState::Released };
        pointer.button(self, &ButtonEvent { button, state, serial, time });
        pointer.frame(self);
    }

    /// A press before it goes to the client: the window under it is focused
    /// and raised; with Super held, a drag begins instead (true: taken).
    fn pointer_press(&mut self, button: u32, time: u32) -> bool {
        let at = self.pointer.location;
        let Some(output) = self.output_at(at.into()) else {
            return false;
        };
        let name = output.name();
        let under = self.window_under(at.into()).map(|(w, _)| w);
        let logo = self.seat.get_keyboard().map(|k| k.modifier_state().logo).unwrap_or(false);
        if logo {
            if let Some(window) = under.clone() {
                let kind = match button {
                    BTN_LEFT => Some(DragKind::Move),
                    BTN_RIGHT => {
                        let rect = self.window_rect_of(&window);
                        Some(DragKind::Resize(crate::desk::edges_at(rect, at.0, at.1)))
                    }
                    _ => None,
                };
                if let Some(kind) = kind {
                    self.focus_by_pointer(&name, Some(&window));
                    self.desk_drag_begin(&window, kind, button, None, at, time);
                    return true;
                }
            }
        }
        if self.is_lit(&name) && self.ambient.is_none() {
            self.focus_by_pointer(&name, under.as_ref());
        }
        false
    }

    /// Click to focus: the output becomes the active one, the window (if
    /// any) goes to the front with the keyboard.
    pub fn focus_by_pointer(&mut self, output: &str, window: Option<&smithay::desktop::Window>) {
        if self.active_output.as_deref() != Some(output) {
            self.active_output = Some(output.to_owned());
            self.events_dirty = true;
        }
        if let Some(window) = window {
            if self.space.elements().any(|w| w == window) {
                self.raise_window(window, true);
                self.queue_redraw_all();
            }
        }
        self.refresh_keyboard_focus();
    }

    /// A scroll.
    pub fn pointer_axis(&mut self, frame: AxisFrame) {
        let Some(pointer) = self.seat.get_pointer() else {
            return;
        };
        self.note_activity();
        if self.desk.drag.is_some() {
            return;
        }
        pointer.axis(self, frame);
        pointer.frame(self);
    }

    /// A window's rectangle in the global space (where it is drawn).
    pub fn window_rect_of(&self, window: &smithay::desktop::Window) -> crate::layout::Rect {
        let loc = self.space.element_location(window).unwrap_or_default();
        let size = window.geometry().size;
        (loc.x, loc.y, size.w, size.h)
    }

    /// A client asked to be moved (its title bar dragged): with the
    /// pointer's button held for that press, a pointer drag. False: not the
    /// pointer's (a finger's: crate::windrag).
    pub fn pointer_move_request(
        &mut self,
        window: &smithay::desktop::Window,
        serial: smithay::utils::Serial,
    ) -> bool {
        if !self.pointer_owns(serial) {
            return false;
        }
        let button = self.pointer.held.iter().copied().min().unwrap_or(BTN_LEFT);
        let at = self.pointer.location;
        self.desk_drag_begin(window, DragKind::Move, button, None, at, 0);
        true
    }

    /// A client asked to be resized by an edge (its border dragged).
    pub fn pointer_resize_request(
        &mut self,
        window: &smithay::desktop::Window,
        serial: smithay::utils::Serial,
        edges: Edges,
    ) -> bool {
        if !self.pointer_owns(serial) || !self.on_desk(window) || !edges.any() {
            return false;
        }
        let button = self.pointer.held.iter().copied().min().unwrap_or(BTN_LEFT);
        let at = self.pointer.location;
        self.desk_drag_begin(window, DragKind::Resize(edges), button, None, at, 0);
        true
    }

    /// A client asked to be resized by an edge from a finger's press (its
    /// border dragged by touch on a desk): that finger resizes it. False:
    /// not on a desk, or no finger with that press.
    pub fn touch_resize_request(
        &mut self,
        window: &smithay::desktop::Window,
        serial: smithay::utils::Serial,
        edges: Edges,
    ) -> bool {
        if !self.on_desk(window) || !edges.any() || self.desk.drag.is_some() {
            return false;
        }
        let id = window_data(window).id;
        let Some(slot) = self
            .touch_slots
            .iter()
            .filter(|(_, s)| s.delivered)
            .find(|(_, s)| s.serial == Some(serial))
            .map(|(slot, _)| *slot)
            .or_else(|| {
                self.touch_slots
                    .iter()
                    .filter(|(_, s)| s.delivered && s.window == Some(id))
                    .max_by_key(|(_, s)| s.time)
                    .map(|(slot, _)| *slot)
            })
        else {
            return false;
        };
        self.end_touch_for_client(slot);
        let Some(at) = self.touch_slots.get(&slot).map(|s| (s.location.x, s.location.y)) else {
            return false;
        };
        self.desk_drag_begin(window, DragKind::Resize(edges), 0, Some(slot), at, 0);
        true
    }

    /// A finger carries a desk window (its title bar dragged, or the top
    /// of a window without one held): it moves with the finger.
    pub fn desk_touch_drag_begin(&mut self, slot: u32, window: &smithay::desktop::Window, at: (f64, f64)) {
        self.desk_drag_begin(window, DragKind::Move, 0, Some(slot), at, 0);
    }

    /// Whether a request's serial is the pointer's press, still held.
    fn pointer_owns(&self, serial: smithay::utils::Serial) -> bool {
        self.seat.get_pointer().is_some()
            && !self.pointer.held.is_empty()
            && self.pointer.press_serial == Some(serial)
            && self.desk.drag.is_none()
    }

    /// Whether a window is on an external display's desk.
    /// An Android window is not one: they all share one Android display
    /// and one size (crate::android), so it fills its output as on a panel.
    pub fn on_desk(&self, window: &smithay::desktop::Window) -> bool {
        let on = matches!(&*window_data(window).placement.borrow(),
            Placement::Output(name) if self.is_desk_output(name) && self.output_by_name(name).is_some());
        on && !crate::android::is_waydroid(crate::state::app_id_and_title(window).0.as_deref())
    }

    fn desk_drag_begin(
        &mut self,
        window: &smithay::desktop::Window,
        kind: DragKind,
        button: u32,
        touch: Option<u32>,
        at: (f64, f64),
        time: u32,
    ) {
        let id = window_data(window).id;
        let start = if self.on_desk(window) {
            // In its display's frame.
            let name = match &*window_data(window).placement.borrow() {
                Placement::Output(name) => name.clone(),
                Placement::Span => return,
            };
            let origin = self
                .output_by_name(&name)
                .and_then(|o| self.space.output_geometry(&o))
                .map(|g| (g.loc.x, g.loc.y))
                .unwrap_or((0, 0));
            let (x, y, w, h) = self.window_rect_of(window);
            // A maximized / snapped window is let go of its snap: it comes
            // off at its own size under the pointer.
            let unsnapped = match self.desk.frames.get_mut(&id) {
                Some(frame) if kind == DragKind::Move && frame.snap.is_some() => {
                    frame.snap = None;
                    let (fw, fh) = (frame.rect.2, frame.rect.3);
                    let px = at.0 - origin.0 as f64;
                    let share = ((px - (x - origin.0) as f64) / w.max(1) as f64).clamp(0.0, 1.0);
                    frame.rect = ((px - share * fw as f64).round() as i32, y - origin.1, fw, fh);
                    Some(frame.rect)
                }
                _ => None,
            };
            match unsnapped {
                Some(rect) => {
                    self.place(window);
                    rect
                }
                None => (x - origin.0, y - origin.1, w, h),
            }
        } else {
            let (x, y, w, h) = self.window_rect_of(window);
            (x, y, w, h)
        };
        info!(id, ?kind, finger = touch.is_some(), "desk drag");
        self.desk.drag = Some(Drag { window: id, kind, from: at, start, button, touch });
        if let DragKind::Resize(_) = kind {
            set_resizing(window, true);
        }
        // The client sees no pointer motion while it is dragged.
        if let Some(pointer) = self.seat.get_pointer().filter(|_| touch.is_none()) {
            let serial = SERIAL_COUNTER.next_serial();
            let location = self.pointer.location.into();
            pointer.motion(self, None, &MotionEvent { location, serial, time });
            pointer.frame(self);
        }
    }

    pub fn desk_drag_motion(&mut self, to: (f64, f64)) {
        let Some(drag) = self.desk.drag.clone() else {
            return;
        };
        let Some(window) = self.window_by_id(drag.window) else {
            self.desk.drag = None;
            return;
        };
        if !self.on_desk(&window) {
            // A panel's window: it stays where it is until it is let go.
            return;
        }
        let Some((origin, area)) = self.desk_area_of(&window) else {
            return;
        };
        let rect = drag.rect(to, crate::extout::min_size(&window), area);
        let _ = origin;
        if let Some(frame) = self.desk.frames.get_mut(&drag.window) {
            frame.rect = rect;
            frame.snap = None;
        }
        self.place(&window);
        self.queue_redraw_all();
    }

    fn desk_drag_end(&mut self, time: u32) {
        let at = self.pointer.location;
        self.desk_drag_end_at(at, time);
    }

    /// A finger's desk drag cancelled: the window stays where it got to.
    pub fn desk_drag_cancel(&mut self) {
        if let Some(drag) = self.desk.drag.take() {
            if let Some(window) = self.window_by_id(drag.window) {
                set_resizing(&window, false);
            }
            self.queue_redraw_all();
        }
    }

    /// The desk drag ends with the pointer (or the finger) at `at`.
    pub fn desk_drag_end_at(&mut self, at: (f64, f64), time: u32) {
        let Some(drag) = self.desk.drag.take() else {
            return;
        };
        let Some(window) = self.window_by_id(drag.window) else {
            return;
        };
        set_resizing(&window, false);
        let target = self.output_at(at.into());
        let here = match &*window_data(&window).placement.borrow() {
            Placement::Output(name) => Some(name.clone()),
            Placement::Span => None,
        };
        match target {
            Some(output) if Some(output.name()) != here && drag.kind == DragKind::Move => {
                let name = output.name();
                info!(id = drag.window, to = name, "let go on another output");
                if self.is_desk_output(&name) {
                    // Floating where the pointer is: its own frame there,
                    // its top under the pointer.
                    if let Some(g) = self.space.output_geometry(&output) {
                        let (w, h) = self
                            .desk
                            .frames
                            .get(&drag.window)
                            .map(|f| (f.rect.2, f.rect.3))
                            .unwrap_or((drag.start.2, drag.start.3));
                        let x = (at.0 - g.loc.x as f64 - w as f64 / 2.0).round() as i32;
                        let y = (at.1 - g.loc.y as f64 - 16.0).round() as i32;
                        let area = (0, 0, g.size.w, g.size.h);
                        self.desk.frames.insert(
                            drag.window,
                            crate::desk::Frame {
                                rect: crate::desk::keep_inside((x, y, w, h), area),
                                snap: None,
                            },
                        );
                    }
                    if crate::extout::is_external(&name) {
                        self.ext_remember_home(&window);
                    }
                }
                self.apply_target(&window, Some(&crate::snap::Target::Panel(name)));
            }
            Some(output) if drag.kind == DragKind::Move && self.on_desk(&window) => {
                // On its own display: a snap at an edge.
                if let Some(g) = self.space.output_geometry(&output) {
                    let local = (at.0 - g.loc.x as f64, at.1 - g.loc.y as f64);
                    if let Some(snap) = crate::desk::snap_at(local.0, local.1, (g.size.w, g.size.h)) {
                        if let Some(frame) = self.desk.frames.get_mut(&drag.window) {
                            frame.rect = drag.start;
                            frame.snap = Some(snap);
                        }
                        self.place(&window);
                    }
                }
            }
            _ => {}
        }
        self.queue_redraw_all();
        self.pointer_refocus(time);
    }

    /// A window's display (its origin in the global space) and the room on
    /// it, in its frame.
    pub fn desk_area_of(
        &self,
        window: &smithay::desktop::Window,
    ) -> Option<((i32, i32), crate::layout::Rect)> {
        let name = match &*window_data(window).placement.borrow() {
            Placement::Output(name) => name.clone(),
            Placement::Span => return None,
        };
        let output = self.output_by_name(&name)?;
        let g = self.space.output_geometry(&output)?;
        let zone = self.zone_without_osk(&output);
        Some(((g.loc.x, g.loc.y), (zone.loc.x, zone.loc.y, zone.size.w, zone.size.h)))
    }

    /// A touch hides the cursor (shown again by the next motion).
    pub fn pointer_hide_for_touch(&mut self) {
        if self.pointer.shown {
            self.pointer.shown = false;
            if let Some(output) = self.pointer_output() {
                self.queue_redraw_output(&output);
            }
        }
    }

    /// Ask for a frame on the output with this name / object.
    pub fn queue_redraw_output(&mut self, output: &Output) {
        let crtc = self.tty.surfaces().iter().find(|s| s.output == *output).map(|s| s.crtc);
        if let Some(crtc) = crtc {
            self.queue_redraw(crtc);
        }
    }
}

/// The cursor's elements on an output, front first (none when it is not
/// shown or not on that output). A free function: it is called where the
/// renderer is already borrowed out of the state.
pub fn cursor_elements(
    pointer: &mut Pointer,
    space: &smithay::desktop::Space<smithay::desktop::Window>,
    renderer: &mut GlesRenderer,
    output: &Output,
    has_pointer: bool,
) -> Vec<Element> {
    if !pointer.shown || !has_pointer {
        return Vec::new();
    }
    let Some(g) = space.output_geometry(output) else {
        return Vec::new();
    };
    let (x, y) = pointer.location;
    if !g.to_f64().contains((x, y)) {
        return Vec::new();
    }
    let scale = output.current_scale().fractional_scale();
    let local = (x - g.loc.x as f64, y - g.loc.y as f64);
    match pointer.image.clone() {
        Some(CursorImageStatus::Hidden) => Vec::new(),
        Some(CursorImageStatus::Surface(surface)) if surface.alive() => {
            let hotspot = smithay::wayland::compositor::with_states(&surface, |states| {
                states
                    .data_map
                    .get::<CursorImageSurfaceData>()
                    .map(|d| d.lock().map(|a| a.hotspot).unwrap_or_default())
                    .unwrap_or_default()
            });
            let at: Point<f64, Logical> = (local.0 - hotspot.x as f64, local.1 - hotspot.y as f64).into();
            let at: Point<i32, Physical> = at.to_physical_precise_round(scale);
            render_elements_from_surface_tree::<GlesRenderer, WaylandSurfaceRenderElement<GlesRenderer>>(
                renderer,
                &surface,
                at,
                scale,
                1.0,
                Kind::Cursor,
            )
            .into_iter()
            .map(Element::from)
            .collect()
        }
        _ => {
            let px = (CURSOR_SIZE * scale).round().max(8.0) as i32;
            let buffer = pointer.arrows.entry(px).or_insert_with(|| {
                let pixels = arrow_pixels(px as usize);
                MemoryRenderBuffer::from_slice(
                    &pixels,
                    Fourcc::Argb8888,
                    (px, px),
                    1,
                    Transform::Normal,
                    None,
                )
            });
            let location: Point<f64, Physical> =
                ((local.0 * scale).round(), (local.1 * scale).round()).into();
            let logical = CURSOR_SIZE.round() as i32;
            MemoryRenderBufferRenderElement::from_buffer(
                renderer,
                location,
                buffer,
                None,
                Some(Rectangle::from_size((px as f64, px as f64).into())),
                Some(Size::from((logical, logical))),
                Kind::Cursor,
            )
            .ok()
            .map(Element::Icon)
            .into_iter()
            .collect()
        }
    }
}

/// The xdg `resizing` state while a resize drag runs.
fn set_resizing(window: &smithay::desktop::Window, on: bool) {
    use smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel;
    if let Some(toplevel) = window.toplevel() {
        toplevel.with_pending_state(|state| {
            if on {
                state.states.set(xdg_toplevel::State::Resizing);
            } else {
                state.states.unset(xdg_toplevel::State::Resizing);
            }
        });
        if toplevel.is_initial_configure_sent() {
            toplevel.send_pending_configure();
        }
    }
}

/// The axis frame of a libinput scroll event (as Smithay's anvil builds it).
pub fn axis_frame<
    E: smithay::backend::input::PointerAxisEvent<smithay::backend::libinput::LibinputInputBackend>,
>(
    event: &E,
) -> AxisFrame {
    let h = event
        .amount(Axis::Horizontal)
        .unwrap_or_else(|| event.amount_v120(Axis::Horizontal).unwrap_or(0.0) * 15.0 / 120.0);
    let v = event
        .amount(Axis::Vertical)
        .unwrap_or_else(|| event.amount_v120(Axis::Vertical).unwrap_or(0.0) * 15.0 / 120.0);
    let mut frame = AxisFrame::new(event.time_msec()).source(event.source());
    if h != 0.0 {
        frame = frame
            .relative_direction(Axis::Horizontal, event.relative_direction(Axis::Horizontal))
            .value(Axis::Horizontal, h);
        if let Some(d) = event.amount_v120(Axis::Horizontal) {
            frame = frame.v120(Axis::Horizontal, d as i32);
        }
    }
    if v != 0.0 {
        frame = frame
            .relative_direction(Axis::Vertical, event.relative_direction(Axis::Vertical))
            .value(Axis::Vertical, v);
        if let Some(d) = event.amount_v120(Axis::Vertical) {
            frame = frame.v120(Axis::Vertical, d as i32);
        }
    }
    if event.source() == AxisSource::Finger {
        if event.amount(Axis::Horizontal) == Some(0.0) {
            frame = frame.stop(Axis::Horizontal);
        }
        if event.amount(Axis::Vertical) == Some(0.0) {
            frame = frame.stop(Axis::Vertical);
        }
    }
    frame
}

#[cfg(test)]
mod tests {
    use super::*;

    // DSI-1, DSI-2 (gap on, scale 2.5) and a 1920 x 1080 display after them.
    const RECTS: [(f64, f64, f64, f64); 3] =
        [(0.0, 0.0, 538.0, 757.0), (564.0, 0.0, 538.0, 757.0), (1102.0, 0.0, 1920.0, 1080.0)];

    #[test]
    fn a_motion_on_the_outputs_goes_where_it_goes() {
        assert_eq!(clamp_to(&RECTS, (1500.0, 500.0), (1520.0, 510.0)), (1520.0, 510.0));
        // From the display onto the right panel, at a height the panel has.
        assert_eq!(clamp_to(&RECTS, (1110.0, 300.0), (1090.0, 300.0)), (1090.0, 300.0));
    }

    #[test]
    fn a_motion_off_the_outputs_stops_at_the_edge() {
        // Left off the display below the panel's height: held at its edge.
        assert_eq!(clamp_to(&RECTS, (1110.0, 900.0), (1090.0, 905.0)), (1102.0, 905.0));
        // Past the right and the bottom.
        assert_eq!(clamp_to(&RECTS, (3000.0, 1000.0), (3100.0, 1200.0)), (3021.0, 1079.0));
        // Into the hinge gap: held on the panel it was on.
        assert_eq!(clamp_to(&RECTS, (530.0, 100.0), (550.0, 100.0)), (537.0, 100.0));
        // From nowhere (an output went away): to the nearest.
        assert_eq!(clamp_to(&RECTS, (-50.0, -50.0), (-40.0, -40.0)), (0.0, 0.0));
        // No outputs: anything goes (nothing is drawn).
        assert_eq!(clamp_to(&[], (1.0, 1.0), (-5.0, 7.0)), (-5.0, 7.0));
    }

    #[test]
    fn the_arrow_has_its_tip_at_the_corner() {
        let px = 24;
        let p = arrow_pixels(px);
        assert_eq!(p.len(), px * px * 4);
        let alpha = |x: usize, y: usize| p[(y * px + x) * 4 + 3];
        // The tip (hot spot) is drawn, the opposite corner is not.
        assert!(alpha(0, 0) > 0, "tip alpha {}", alpha(0, 0));
        assert_eq!(alpha(px - 1, px - 1), 0);
        assert_eq!(alpha(px - 1, 0), 0);
        // Inside the body: opaque white.
        let i = (8 * px + 3) * 4;
        assert_eq!((p[i], p[i + 3]), (255, 255));
        // Premultiplied: no channel above alpha.
        for c in p.chunks(4) {
            assert!(c[0] <= c[3] && c[1] <= c[3] && c[2] <= c[3]);
        }
    }

    #[test]
    fn the_digitizer_is_never_a_pointer() {
        assert!(is_builtin("spi 045E:0C1D", 0x045e, 0x0c1d));
        assert!(is_builtin("spi 045E:0C1D Mouse", 0, 0));
        assert!(is_builtin("Surface Duo 2 Touchscreen Left", 0, 0));
        assert!(!is_builtin("Logitech MX Master 3", 0x046d, 0xb023));
    }
}
