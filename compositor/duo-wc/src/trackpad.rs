//! A panel as the touchpad of an external display (opt-in: `duoctl
//! set_trackpad DSI-1|DSI-2|off`, or `external_trackpad` in duo-wc.conf for
//! every display that is plugged).
//!
//! Without a mouse, the desk on a monitor or a TV ([`crate::desk`]) has no
//! pointer. With this on, the touches of one lit panel never reach its
//! clients; they drive the pointer instead ([`crate::pointer`]), as a
//! laptop's touchpad does:
//!
//! - one finger moving: the pointer moves ([`GAIN`] times the finger, the
//!   panel being small next to the display);
//! - a tap (down and up within [`TAP_MS`], moving less than [`TAP_SLOP`]):
//!   a left click; a tap with two fingers: a right click;
//! - tap, then down again within [`DRAG_MS`] and move: a drag (the left
//!   button held until that finger lifts), as libinput's tap-and-drag: the
//!   tap's press is sent when it lifts and held [`DRAG_MS`] for a finger
//!   that may come back, so the drag is ONE press (two presses in a row are
//!   the app's double click: a title bar maximized instead of moving); a
//!   tap and a quick tap again is a double click; with `drag_lock` a drag's
//!   finger may lift for [`DRAG_LOCK_MS`] and go on;
//! - two fingers moving: a scroll.
//!
//! The panel is shown dimmed under a dark veil meanwhile (render: one
//! solid rectangle), and the cursor appears on the display. Its clients go
//! on underneath; the edge swipes there are off while it is a touchpad.
//!
//! [`Pad`] is the gesture machine, pure (logical px of the panel, ms) and
//! tested; [`DuoWc`]'s side feeds it from the touch path and turns what it
//! says into pointer events.

use std::collections::HashMap;

use smithay::backend::input::{Axis, AxisSource};
use smithay::input::pointer::AxisFrame;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use tracing::{debug, info};

use crate::pointer::{BTN_LEFT, BTN_RIGHT};
use crate::state::DuoWc;

/// Pointer px per finger px.
pub const GAIN: f64 = 2.5;
/// The longest a tap lasts.
pub const TAP_MS: u32 = 220;
/// The farthest a tap's finger moves (logical px).
pub const TAP_SLOP: f64 = 8.0;
/// How soon after a tap a finger coming down again starts a drag: the
/// tap's left button is held that long (its release, the click's end, is
/// sent then if no finger came).
pub const DRAG_MS: u32 = 250;
/// Drag lock (`touchpad_drag_lock`): how long a drag's finger may be off
/// the pad before the button is let go.
pub const DRAG_LOCK_MS: u32 = 300;
/// Scroll px per finger px (two fingers).
pub const SCROLL_GAIN: f64 = 1.5;

/// What the touchpad does.
#[derive(Debug, Clone, PartialEq)]
pub enum Out {
    /// Move the pointer by this much (logical px of the outputs).
    Motion(f64, f64),
    /// A button (evdev code) pressed or released.
    Button(u32, bool),
    /// Scroll by this much (horizontal, vertical; px).
    Scroll(f64, f64),
}

#[derive(Debug, Clone, Copy)]
struct Finger {
    down_at: (f64, f64),
    at: (f64, f64),
    down_time: u32,
}

impl Finger {
    /// How far it moved from where it went down.
    fn travel(&self) -> f64 {
        ((self.at.0 - self.down_at.0).powi(2) + (self.at.1 - self.down_at.1).powi(2)).sqrt()
    }
}

/// The left button held by a tap or a drag.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
enum Held {
    /// Not held.
    #[default]
    No,
    /// A one-finger tap lifted at `since`: its press is out, its release
    /// waits [`DRAG_MS`] for a finger that drags.
    Tapped { since: u32 },
    /// A finger came down within that: a drag if it moves (or stays down
    /// longer than a tap), a double click if it taps.
    DragOrDouble { slot: u32 },
    /// A drag: the button held while `slot` moves.
    Dragging { slot: u32 },
    /// Drag lock: the drag's finger lifted at `since`; held [`DRAG_LOCK_MS`]
    /// for it to come back.
    Locked { since: u32 },
}

/// The gesture machine of one touchpad panel.
#[derive(Debug, Default)]
pub struct Pad {
    fingers: HashMap<u32, Finger>,
    /// The most fingers down at once in this gesture.
    most: usize,
    /// The gesture moved past the tap slop.
    moved: bool,
    /// The left button held by a tap or a drag.
    held: Held,
    /// Pointer px per finger px; 0 is [`GAIN`] (laptop mode's touchpad
    /// takes 1 and accelerates by speed itself, crate::laptop::accel).
    pub gain: f64,
    /// Drag lock (`touchpad_drag_lock`, off by default): a drag's finger
    /// lifted for less than [`DRAG_LOCK_MS`] does not let the button go.
    pub drag_lock: bool,
    /// A finger that came down after a tap, too late for a drag (ms after
    /// the tap, up to twice [`DRAG_MS`]): for the log, taken by the caller.
    pub near_miss: Option<u32>,
}

impl Pad {
    /// A finger down at `at` (panel px) at `time` (ms).
    pub fn down(&mut self, slot: u32, at: (f64, f64), time: u32) -> Vec<Out> {
        let mut out = Vec::new();
        if self.fingers.is_empty() {
            self.most = 0;
            self.moved = false;
            match self.held {
                Held::Tapped { since } => {
                    let gap = time.wrapping_sub(since);
                    if gap <= DRAG_MS {
                        // The tap's button is still down: this finger drags
                        // it (or taps again: a double click).
                        self.held = Held::DragOrDouble { slot };
                    } else {
                        // Its time ran out (the timer not yet run): the
                        // click ends first.
                        if gap <= 2 * DRAG_MS {
                            self.near_miss = Some(gap);
                        }
                        self.held = Held::No;
                        out.push(Out::Button(BTN_LEFT, false));
                    }
                }
                Held::Locked { since } => {
                    if time.wrapping_sub(since) <= DRAG_LOCK_MS {
                        self.held = Held::Dragging { slot };
                    } else {
                        self.held = Held::No;
                        out.push(Out::Button(BTN_LEFT, false));
                    }
                }
                _ => {}
            }
        } else if let Held::DragOrDouble { .. } = self.held {
            // A second finger before the drag began: no drag; the tap's
            // click ends, the fingers go on as a two-finger gesture.
            self.held = Held::No;
            out.push(Out::Button(BTN_LEFT, false));
        }
        self.fingers.insert(slot, Finger { down_at: at, at, down_time: time });
        self.most = self.most.max(self.fingers.len());
        out
    }

    /// A finger moved to `at`.
    pub fn motion(&mut self, slot: u32, at: (f64, f64)) -> Vec<Out> {
        let Some(finger) = self.fingers.get_mut(&slot) else {
            return Vec::new();
        };
        let (dx, dy) = (at.0 - finger.at.0, at.1 - finger.at.1);
        finger.at = at;
        let travel = finger.travel();
        if travel > TAP_SLOP {
            self.moved = true;
        }
        let gain = if self.gain > 0.0 { self.gain } else { GAIN };
        match self.held {
            Held::DragOrDouble { slot: s } | Held::Dragging { slot: s } => {
                if s != slot {
                    // Another finger on the pad during a drag: nothing.
                    return Vec::new();
                }
                if travel > TAP_SLOP {
                    self.held = Held::Dragging { slot };
                }
                return vec![Out::Motion(dx * gain, dy * gain)];
            }
            _ => {}
        }
        match self.fingers.len() {
            1 => vec![Out::Motion(dx * gain, dy * gain)],
            // Two fingers: each moves half of the scroll (their mean).
            2 => vec![Out::Scroll(-dx * SCROLL_GAIN / 2.0, -dy * SCROLL_GAIN / 2.0)],
            _ => Vec::new(),
        }
    }

    /// A finger lifted at `time`.
    pub fn up(&mut self, slot: u32, time: u32) -> Vec<Out> {
        let Some(finger) = self.fingers.remove(&slot) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let quick = time.wrapping_sub(finger.down_time) <= TAP_MS;
        match self.held {
            Held::DragOrDouble { slot: s } if s == slot => {
                if quick && finger.travel() <= TAP_SLOP {
                    // Tapped again: a double click (the first click's
                    // release, then a whole second one).
                    self.held = Held::No;
                    out.push(Out::Button(BTN_LEFT, false));
                    out.push(Out::Button(BTN_LEFT, true));
                    out.push(Out::Button(BTN_LEFT, false));
                } else {
                    // Held still longer than a tap: a drag that did not move.
                    out.extend(self.drag_lifted(time));
                }
                return out;
            }
            Held::Dragging { slot: s } if s == slot => {
                out.extend(self.drag_lifted(time));
                return out;
            }
            Held::DragOrDouble { .. } | Held::Dragging { .. } => {
                // Another finger of a drag: the drag goes on.
                return out;
            }
            _ => {}
        }
        if !self.fingers.is_empty() {
            return out;
        }
        // The last finger of the gesture: was it a tap?
        if quick && !self.moved {
            if self.most >= 2 {
                out.push(Out::Button(BTN_RIGHT, true));
                out.push(Out::Button(BTN_RIGHT, false));
            } else if self.most == 1 {
                // The press now, the release when no drag follows
                // ([`Pad::deadline`]).
                out.push(Out::Button(BTN_LEFT, true));
                self.held = Held::Tapped { since: time };
            }
        }
        out
    }

    /// The drag's finger lifted: the button goes, or with drag lock waits.
    fn drag_lifted(&mut self, time: u32) -> Vec<Out> {
        if self.drag_lock {
            self.held = Held::Locked { since: time };
            Vec::new()
        } else {
            self.held = Held::No;
            vec![Out::Button(BTN_LEFT, false)]
        }
    }

    /// When [`Pad::timeout`] is due (ms, the touch events' clock): a tap's
    /// held button waiting for a drag, a drag lock.
    pub fn deadline(&self) -> Option<u32> {
        match self.held {
            Held::Tapped { since } => Some(since.wrapping_add(DRAG_MS)),
            Held::Locked { since } => Some(since.wrapping_add(DRAG_LOCK_MS)),
            _ => None,
        }
    }

    /// The time is `now`: a tap no drag followed ends its click, a drag
    /// lock lets go.
    pub fn timeout(&mut self, now: u32) -> Vec<Out> {
        match self.deadline() {
            Some(due) if now.wrapping_sub(due) as i32 >= 0 => self.flush(),
            _ => Vec::new(),
        }
    }

    /// Something else happened (a key typed): a tap's held button or a
    /// drag lock lets go now, no drag follows.
    pub fn flush(&mut self) -> Vec<Out> {
        match self.held {
            Held::Tapped { .. } | Held::Locked { .. } => {
                self.held = Held::No;
                vec![Out::Button(BTN_LEFT, false)]
            }
            _ => Vec::new(),
        }
    }

    /// Every finger gone at once (a cancel): a held button is let go.
    pub fn cancel(&mut self) -> Vec<Out> {
        self.fingers.clear();
        match std::mem::take(&mut self.held) {
            Held::No => Vec::new(),
            _ => vec![Out::Button(BTN_LEFT, false)],
        }
    }

    /// Whether a touch point is one of the touchpad's.
    pub fn owns(&self, slot: u32) -> bool {
        self.fingers.contains_key(&slot)
    }
}

/// The touchpad panel's state.
#[derive(Debug, Default)]
pub struct Trackpad {
    /// The panel that is the touchpad, if any.
    pub panel: Option<String>,
    /// Its gestures.
    pub pad: Pad,
    /// The veil's element id (one, so damage tracking keeps it).
    pub veil: Option<smithay::backend::renderer::element::Id>,
}

impl Trackpad {
    /// No panel yet; `drag_lock`: duo-wc.conf's `touchpad_drag_lock`.
    pub fn new(drag_lock: bool) -> Self {
        let mut trackpad = Trackpad::default();
        trackpad.pad.drag_lock = drag_lock;
        trackpad
    }
}

/// The pointer "device" the touchpad counts as.
pub const DEVICE: &str = "duo-wc-trackpad";

/// Which touchpad's gesture machine: an external display's touchpad panel
/// or laptop mode's touchpad.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PadId {
    Trackpad,
    Laptop,
}

impl DuoWc {
    fn pad_of(&mut self, id: PadId) -> &mut Pad {
        match id {
            PadId::Trackpad => &mut self.trackpad.pad,
            PadId::Laptop => &mut self.laptop.pad,
        }
    }

    /// After a touchpad event at `now` (the touch clock, ms): a tap's held
    /// button or a drag lock waits for a finger ([`Pad::deadline`]); a
    /// timer lets it go when no finger came. A touch that came too late
    /// for a drag is logged (the window may want tuning).
    pub(crate) fn pad_arm(&mut self, id: PadId, now: u32) {
        let pad = self.pad_of(id);
        if let Some(gap) = pad.near_miss.take() {
            info!(pad = ?id, gap_ms = gap, window_ms = DRAG_MS, "touchpad: a touch came too late after a tap for a drag");
        }
        let Some(due) = pad.deadline() else {
            return;
        };
        let wait = (due.wrapping_sub(now) as i32).max(0) as u64;
        let timer = Timer::from_duration(std::time::Duration::from_millis(wait));
        let _ = self.loop_handle.insert_source(timer, move |_, _, state: &mut DuoWc| {
            if state.pad_of(id).deadline() == Some(due) {
                let outs = state.pad_of(id).timeout(due);
                if !outs.is_empty() {
                    debug!(pad = ?id, "touchpad: no drag after the tap, the click ends");
                }
                for out in outs {
                    match id {
                        PadId::Trackpad => state.trackpad_apply(out, due),
                        PadId::Laptop => state.laptop_pad_apply(out, due, 1.0),
                    }
                }
            }
            TimeoutAction::Drop
        });
    }

    /// Make a panel the touchpad (`None`: none). Only a panel, and only
    /// while an external display is up (the cursor has nowhere else to be
    /// useful); it ends by itself when the last display goes.
    pub fn set_trackpad(&mut self, panel: Option<String>) -> anyhow::Result<()> {
        if let Some(name) = &panel {
            if !crate::extout::is_panel(name) || self.output_by_name(name).is_none() {
                anyhow::bail!("{name} is not a panel");
            }
            if !self.ext_connected() {
                anyhow::bail!("no external display is up");
            }
            if self.laptop_on() {
                anyhow::bail!("laptop mode is on (its keyboard panel has a touchpad)");
            }
        }
        if self.trackpad.panel == panel {
            return Ok(());
        }
        info!(?panel, "touchpad panel");
        for out in self.trackpad.pad.cancel() {
            self.trackpad_apply(out, 0);
        }
        self.trackpad.panel = panel.clone();
        if panel.is_some() {
            self.cancel_all_touches();
            self.pointer.devices.insert(DEVICE.to_owned());
            if self.seat.get_pointer().is_none() {
                self.seat.add_pointer();
            }
            // The cursor starts on the display.
            if let Some(g) = self
                .outputs
                .iter()
                .find(|o| crate::extout::is_external(&o.name()))
                .and_then(|o| self.space.output_geometry(o))
            {
                self.pointer.location =
                    (g.loc.x as f64 + g.size.w as f64 / 2.0, g.loc.y as f64 + g.size.h as f64 / 2.0);
            }
            self.pointer.shown = true;
        } else {
            self.pointer.devices.remove(DEVICE);
            if self.pointer.devices.is_empty() && self.seat.get_pointer().is_some() {
                self.pointer.shown = false;
                self.seat.remove_pointer();
            }
        }
        self.events_dirty = true;
        self.queue_redraw_all();
        Ok(())
    }

    /// Whether a touch going down on `output` is the touchpad's.
    pub fn trackpad_takes(&self, output: &str) -> bool {
        self.trackpad.panel.as_deref() == Some(output) && !self.lock.locked
    }

    /// A touch on the touchpad panel (`at`: panel-local logical px).
    pub fn trackpad_down(&mut self, slot: u32, at: (f64, f64), time: u32) {
        self.note_activity();
        for out in self.trackpad.pad.down(slot, at, time) {
            self.trackpad_apply(out, time);
        }
        self.pad_arm(PadId::Trackpad, time);
    }

    /// A touchpad finger moved (`at`: panel-local logical px). False: not
    /// one of its fingers.
    pub fn trackpad_motion(&mut self, slot: u32, at: (f64, f64), time: u32) -> bool {
        if !self.trackpad.pad.owns(slot) {
            return false;
        }
        for out in self.trackpad.pad.motion(slot, at) {
            self.trackpad_apply(out, time);
        }
        true
    }

    /// A touchpad finger lifted. False: not one of its fingers.
    pub fn trackpad_up(&mut self, slot: u32, time: u32) -> bool {
        if !self.trackpad.pad.owns(slot) {
            return false;
        }
        for out in self.trackpad.pad.up(slot, time) {
            self.trackpad_apply(out, time);
        }
        self.pad_arm(PadId::Trackpad, time);
        true
    }

    /// Every touch cancelled.
    pub fn trackpad_cancel(&mut self) {
        for out in self.trackpad.pad.cancel() {
            self.trackpad_apply(out, 0);
        }
    }

    pub(crate) fn trackpad_apply(&mut self, out: Out, time: u32) {
        match out {
            Out::Motion(dx, dy) => self.pointer_motion(dx, dy, time),
            Out::Button(button, pressed) => self.pointer_button(button, pressed, time),
            Out::Scroll(dx, dy) => {
                let mut frame = AxisFrame::new(time).source(AxisSource::Finger);
                if dx != 0.0 {
                    frame = frame.value(Axis::Horizontal, dx);
                }
                if dy != 0.0 {
                    frame = frame.value(Axis::Vertical, dy);
                }
                self.pointer_axis(frame);
            }
        }
    }

    /// The display went away: no touchpad without one.
    pub fn trackpad_display_gone(&mut self) {
        if self.trackpad.panel.is_some() && !self.ext_connected() {
            let _ = self.set_trackpad(None);
        }
    }
}

/// The veil over the touchpad panel: one dark rectangle in front of its
/// scene (render), so the panel reads as a pad, not as its apps.
pub fn veil_element(
    trackpad: &mut Trackpad,
    output: &smithay::output::Output,
    size: smithay::utils::Size<i32, smithay::utils::Physical>,
) -> Option<crate::render::Element> {
    if trackpad.panel.as_deref() != Some(output.name().as_str()) {
        return None;
    }
    let id = trackpad.veil.get_or_insert_with(smithay::backend::renderer::element::Id::new).clone();
    Some(crate::render::Element::Solid(
        smithay::backend::renderer::element::solid::SolidColorRenderElement::new(
            id,
            smithay::utils::Rectangle::from_size(size),
            smithay::backend::renderer::utils::CommitCounter::default(),
            [0.02, 0.025, 0.035, 0.9],
            smithay::backend::renderer::element::Kind::Unspecified,
        ),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_finger_moves_the_pointer() {
        let mut pad = Pad::default();
        assert!(pad.down(0, (100.0, 100.0), 0).is_empty());
        assert_eq!(pad.motion(0, (110.0, 104.0)), vec![Out::Motion(10.0 * GAIN, 4.0 * GAIN)]);
        // A long, moving touch is no click.
        assert!(pad.up(0, 900).is_empty());
    }

    #[test]
    fn a_tap_clicks_and_two_fingers_click_right() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        pad.motion(0, (102.0, 101.0));
        // The press at the lift, the release when no drag came.
        assert_eq!(pad.up(0, 120), vec![Out::Button(BTN_LEFT, true)]);
        assert_eq!(pad.deadline(), Some(120 + DRAG_MS));
        assert!(pad.timeout(120 + DRAG_MS - 1).is_empty());
        assert_eq!(pad.timeout(120 + DRAG_MS), vec![Out::Button(BTN_LEFT, false)]);
        assert_eq!(pad.deadline(), None);
        assert!(pad.timeout(9000).is_empty());
        // Two fingers, tapped (far later: no drag): at once.
        pad.down(1, (100.0, 100.0), 5000);
        pad.down(2, (160.0, 100.0), 5010);
        assert!(pad.up(1, 5100).is_empty());
        assert_eq!(pad.up(2, 5110), vec![Out::Button(BTN_RIGHT, true), Out::Button(BTN_RIGHT, false)]);
        assert_eq!(pad.deadline(), None);
    }

    #[test]
    fn tap_and_a_half_drags_with_one_press() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        assert_eq!(pad.up(0, 100), vec![Out::Button(BTN_LEFT, true)]);
        // Down again soon: no second press (that would be a double click:
        // a title bar maximizes), the tap's button stays held.
        assert!(pad.down(1, (100.0, 100.0), 100 + DRAG_MS).is_empty());
        assert_eq!(pad.deadline(), None);
        // A wobble within the slop moves the pointer, the button still held.
        assert_eq!(pad.motion(1, (103.0, 100.0)), vec![Out::Motion(3.0 * GAIN, 0.0)]);
        assert_eq!(pad.motion(1, (130.0, 100.0)), vec![Out::Motion(27.0 * GAIN, 0.0)]);
        assert_eq!(pad.up(1, 900), vec![Out::Button(BTN_LEFT, false)]);
        assert_eq!(pad.deadline(), None);
        // Too late after a tap: the click ends, an ordinary move.
        assert!(pad.down(2, (0.0, 0.0), 2000).is_empty());
        assert_eq!(pad.up(2, 2100), vec![Out::Button(BTN_LEFT, true)]);
        assert_eq!(pad.down(3, (0.0, 0.0), 2100 + DRAG_MS + 50), vec![Out::Button(BTN_LEFT, false)]);
        assert_eq!(pad.near_miss.take(), Some(DRAG_MS + 50));
        assert_eq!(pad.motion(3, (20.0, 0.0)), vec![Out::Motion(20.0 * GAIN, 0.0)]);
        assert!(pad.up(3, 2900).is_empty());
    }

    #[test]
    fn a_held_second_touch_drags_without_moving() {
        // Tap, then down again and held still past a tap: the button stays
        // down (the press already out), lifted it goes.
        let mut pad = Pad::default();
        pad.down(0, (50.0, 50.0), 0);
        pad.up(0, 80);
        assert!(pad.down(1, (50.0, 50.0), 200).is_empty());
        assert_eq!(pad.up(1, 200 + TAP_MS + 1), vec![Out::Button(BTN_LEFT, false)]);
    }

    #[test]
    fn a_double_tap_is_a_double_click() {
        let mut pad = Pad::default();
        pad.down(0, (50.0, 50.0), 0);
        assert_eq!(pad.up(0, 80), vec![Out::Button(BTN_LEFT, true)]);
        assert!(pad.down(1, (51.0, 50.0), 200).is_empty());
        pad.motion(1, (52.0, 51.0));
        assert_eq!(
            pad.up(1, 280),
            vec![Out::Button(BTN_LEFT, false), Out::Button(BTN_LEFT, true), Out::Button(BTN_LEFT, false)]
        );
        assert_eq!(pad.deadline(), None);
        // A third quick tap is a click again (no drag waits on a double click).
        assert!(pad.down(2, (52.0, 51.0), 350).is_empty());
        assert_eq!(pad.up(2, 400), vec![Out::Button(BTN_LEFT, true)]);
    }

    #[test]
    fn a_second_finger_after_a_tap_scrolls() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        pad.up(0, 80);
        assert!(pad.down(1, (100.0, 100.0), 150).is_empty());
        // A second finger before the drag began: the click ends, a scroll.
        assert_eq!(pad.down(2, (160.0, 100.0), 160), vec![Out::Button(BTN_LEFT, false)]);
        assert_eq!(pad.motion(1, (100.0, 80.0)), vec![Out::Scroll(0.0, 20.0 * SCROLL_GAIN / 2.0)]);
        assert!(pad.up(1, 600).is_empty());
        assert!(pad.up(2, 610).is_empty());
        assert_eq!(pad.deadline(), None);
    }

    #[test]
    fn a_drag_ignores_another_finger() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        pad.up(0, 80);
        pad.down(1, (100.0, 100.0), 150);
        assert_eq!(pad.motion(1, (120.0, 100.0)), vec![Out::Motion(20.0 * GAIN, 0.0)]);
        // A thumb resting on the pad meanwhile: neither a scroll nor a release.
        assert!(pad.down(2, (300.0, 100.0), 300).is_empty());
        assert!(pad.motion(2, (310.0, 100.0)).is_empty());
        assert_eq!(pad.motion(1, (130.0, 100.0)), vec![Out::Motion(10.0 * GAIN, 0.0)]);
        assert!(pad.up(2, 400).is_empty());
        assert_eq!(pad.up(1, 500), vec![Out::Button(BTN_LEFT, false)]);
    }

    #[test]
    fn drag_lock_holds_a_short_lift() {
        let mut pad = Pad { drag_lock: true, ..Pad::default() };
        pad.down(0, (100.0, 100.0), 0);
        pad.up(0, 80);
        pad.down(1, (100.0, 100.0), 150);
        pad.motion(1, (140.0, 100.0));
        // Lifted: still held, for a while.
        assert!(pad.up(1, 600).is_empty());
        assert_eq!(pad.deadline(), Some(600 + DRAG_LOCK_MS));
        // Back soon: the same drag goes on.
        assert!(pad.down(2, (20.0, 100.0), 800).is_empty());
        assert_eq!(pad.motion(2, (60.0, 100.0)), vec![Out::Motion(40.0 * GAIN, 0.0)]);
        assert!(pad.up(2, 1000).is_empty());
        // Not back: let go when the lock runs out.
        assert_eq!(pad.timeout(1000 + DRAG_LOCK_MS), vec![Out::Button(BTN_LEFT, false)]);
        assert_eq!(pad.deadline(), None);
    }

    #[test]
    fn a_key_typed_ends_a_waiting_tap() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        pad.up(0, 80);
        assert_eq!(pad.flush(), vec![Out::Button(BTN_LEFT, false)]);
        assert!(pad.flush().is_empty());
        assert!(pad.down(1, (100.0, 100.0), 150).is_empty());
    }

    #[test]
    fn the_clock_wrapping_is_no_trouble() {
        let mut pad = Pad::default();
        let t = u32::MAX - 50;
        pad.down(0, (1.0, 1.0), t);
        assert_eq!(pad.up(0, t.wrapping_add(60)), vec![Out::Button(BTN_LEFT, true)]);
        assert!(pad.timeout(t.wrapping_add(100)).is_empty());
        assert_eq!(pad.timeout(t.wrapping_add(60 + DRAG_MS)), vec![Out::Button(BTN_LEFT, false)]);
    }

    #[test]
    fn two_fingers_scroll() {
        let mut pad = Pad::default();
        pad.down(0, (100.0, 100.0), 0);
        pad.down(1, (160.0, 100.0), 5);
        // Fingers going up scroll the content down (natural scrolling).
        assert_eq!(pad.motion(0, (100.0, 90.0)), vec![Out::Scroll(0.0, 10.0 * SCROLL_GAIN / 2.0)]);
        assert!(pad.up(0, 400).is_empty());
        assert!(pad.up(1, 410).is_empty());
    }

    #[test]
    fn a_cancel_lets_a_drag_go() {
        let mut pad = Pad::default();
        pad.down(0, (1.0, 1.0), 0);
        pad.up(0, 50);
        assert!(pad.down(1, (1.0, 1.0), 100).is_empty());
        assert_eq!(pad.cancel(), vec![Out::Button(BTN_LEFT, false)]);
        assert!(!pad.owns(1));
        assert!(pad.cancel().is_empty());
    }
}
