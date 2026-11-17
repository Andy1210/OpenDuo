//! Input: each touchscreen drives its own panel.
//!
//! duo2-touchd makes two multitouch devices, "Surface Duo 2 Touchscreen Left" and
//! "... Right", and a udev rule names their panel in `WL_OUTPUT` (DSI-1 / DSI-2),
//! which libinput hands back as the device's output name. Each device's range is
//! its own panel, so a touch is the device's normalised position on THAT output's
//! rectangle in the global space.
//!
//! The kernel's own HID devices for the same digitizer ("spi 045E:0C1D
//! Touchscreen", single touch, both panels folded into one coordinate space with no
//! panel id; and the "Stylus") cannot be mapped to a panel, so any touch or tablet
//! device without an output name is switched off in libinput and never reaches a
//! client.
//!
//! A rotated output (`set_rotation`) turns its touches with it
//! ([`crate::rotation::touch_to_local`]).
//!
//! **Dark panels.** A touch that goes down on a panel that shows no picture
//! never reaches a client, nor does the rest of that touch point (motion, up):
//! a panel that is off, and also one that is lit for the power rules but whose
//! picture is not on the glass yet (waking, held black for the other panel,
//! its first frame since it came on not flipped yet; [`crate::power::
//! touch_on_panel`]). A panel that goes dark under a finger cancels the touch
//! for the clients. With `touch_wake = on` a touch on a dark SCREEN (no panel
//! lit) wakes it; the waking touch itself is still not delivered. Off by
//! default: on a phone only the power key wakes.
//!
//! **Keys.** The power key (KEY_POWER, the PMIC's "pmic_pwrkey") is the
//! compositor's: a short press (under [`crate::power::LONG_PRESS_MS`]) switches
//! the screen off or on, a long press does nothing (the PMIC's hardware reset
//! lives there), and it never reaches a client. While a call rings aloud the
//! first short press silences it instead ([`crate::power::RingHold`]). Other keys are delivered while
//! anything is lit and dropped (press and its release) while the screen is dark.
//! Every delivered touch or key restarts the idle timer.
//!
//! **Volume keys** (KEY_VOLUMEUP from gpio-keys, KEY_VOLUMEDOWN from the PMIC's
//! "pmic_resin", KEY_MUTE from a plugged keyboard) are the shell's: never
//! delivered to a client, announced on the event stream instead, press and
//! release (the shell repeats while a key is held), with the panel for the
//! volume display: the active panel while anything is lit, none while the
//! screen is dark. As on a phone they work in the dark and do NOT wake the
//! screen (the shell changes the volume and shows nothing); while lit a press
//! restarts the idle timer.
//!
//! **Edge swipes** ([`crate::gesture`]). A touch that goes down in a lit
//! panel's bottom edge zone is held back until it is clear whether it is a
//! swipe up or not (delivered after all, from where it went down). A swipe is
//! never delivered: it is followed until it stops and holds (the task
//! overview opens) or lifts (announced on the event stream as `edge_swipe`
//! with its kind, `short` or `long`, for the shell's dock or app grid).
//!
//! **The task overview** ([`crate::taskview`]) takes the touches on its
//! panels below the status bar (not those on overlay surfaces, the dock).
//!
//! **A split's divider** ([`crate::tiling`]) takes a touch that goes down on
//! it while the pair is the front of its panel: the finger shares the room.
//!
//! **The pen** ([`crate::tablet`]): duo2-touchd's tablet devices, one per
//! panel like the touchscreens; a tablet-v2 tool for the clients that take
//! one, the tip as a touch point (through the touch path here, edge swipes
//! included) for the others.
//!
//! **The pen's top button** ([`crate::penbutton`]): a Surface pen paired over
//! Bluetooth is a key device; every key of it is the compositor's (never the
//! seat's), its three presses go to the event stream for the shell.
//!
//! **Laptop mode's keyboard panel** ([`crate::laptopmode`]) takes every touch
//! on it (its keys go into the seat through [`key_input`], like a plugged
//! keyboard's; its touchpad drives the pointer); on its desk panel a finger
//! on a window's title bar moves the window ([`crate::desk`]).
//!
//! A pointer exists only while something drives one: a plugged mouse
//! ([`crate::pointer`]), an external display's touchpad panel, laptop
//! mode's touchpad.

use anyhow::{anyhow, Result};
use smithay::backend::input::{Event, InputEvent, KeyState, KeyboardKeyEvent};
use smithay::backend::libinput::{LibinputInputBackend, LibinputSessionInterface};
use smithay::backend::session::libseat::LibSeatSession;
use smithay::input::keyboard::{FilterResult, Keysym, ModifiersState};
use smithay::input::touch::{DownEvent, MotionEvent, UpEvent};
use smithay::reexports::calloop::{self, EventSource, Poll, PostAction, Readiness, Token, TokenFactory};
use smithay::reexports::input::event::touch::{TouchEventPosition, TouchEventSlot};
use smithay::reexports::input::{self as libinput, DeviceCapability, Libinput, SendEventsMode};
use smithay::utils::{Logical, Point, SERIAL_COUNTER};
use std::os::fd::OwnedFd;
use std::time::{Duration, Instant};
use tracing::{debug, info, warn};

use crate::gesture::{Area, Held, Swipe, SwipeKind, Verdict};
use crate::power::PowerPress;
use crate::rotation::Rotation;
use crate::state::{DuoWc, HeldTouch, SwipeTouch, TouchSlot};

/// The evdev code of the power key (linux/input-event-codes.h).
const KEY_POWER: u32 = 116;
/// The evdev codes of the volume keys.
const KEY_MUTE: u32 = 113;
const KEY_VOLUMEDOWN: u32 = 114;
const KEY_VOLUMEUP: u32 = 115;
/// xkb keycodes are evdev codes plus 8.
const XKB_OFFSET: u32 = 8;

/// Open libinput on the session's seat.
pub fn init(session: &LibSeatSession, seat_name: &str) -> Result<TimedInput> {
    let interface =
        SeatInterface { inner: LibinputSessionInterface::from(session.clone()), closer: LateCloser::new() };
    let mut context = Libinput::new_with_udev(interface);
    context
        .udev_assign_seat(seat_name)
        .map_err(|_| anyhow!("failed to assign the libinput seat {seat_name}"))?;
    Ok(TimedInput { inner: LibinputInputBackend::new(context) })
}

/// An open through the seat slower than this is logged (debug).
const SLOW_OPEN: Duration = Duration::from_millis(4);

/// libinput's way to the devices: the seat's (libseat), with the last close
/// of a device off the loop.
///
/// Closing the last descriptor of an evdev device waits in the kernel for an
/// RCU grace period (`evdev_release` -> `input_close_device` ->
/// `synchronize_rcu`), 15-24 ms measured on the phone, in whichever thread
/// closes it. libinput closes a device that went inside its dispatch, on the
/// loop: the pen's Bluetooth key device goes at every reconnect (each time
/// the pen comes near the screen), and every frame waited. Here the seat
/// still closes the device at once (seatd revokes it, the session forgets
/// it), but a duplicate of the descriptor keeps the file open, and the
/// [`LateCloser`]'s thread closes it: the wait is that thread's.
struct SeatInterface {
    inner: LibinputSessionInterface<LibSeatSession>,
    closer: LateCloser,
}

impl libinput::LibinputInterface for SeatInterface {
    fn open_restricted(&mut self, path: &std::path::Path, flags: i32) -> Result<OwnedFd, i32> {
        let started = Instant::now();
        let fd = self.inner.open_restricted(path, flags);
        let took = started.elapsed();
        if took > SLOW_OPEN {
            debug!(path = %path.display(), ms = took.as_secs_f64() * 1000.0, "a slow device open");
        }
        fd
    }

    fn close_restricted(&mut self, fd: OwnedFd) {
        let last = fd.try_clone().ok();
        self.inner.close_restricted(fd);
        if let Some(last) = last {
            self.closer.close(last);
        }
    }
}

/// Closes descriptors on a thread of its own ([`SeatInterface`]); in place
/// if that thread could not be started or has gone.
pub struct LateCloser {
    tx: Option<std::sync::mpsc::Sender<OwnedFd>>,
}

impl Default for LateCloser {
    fn default() -> Self {
        Self::new()
    }
}

impl LateCloser {
    pub fn new() -> Self {
        let (tx, rx) = std::sync::mpsc::channel::<OwnedFd>();
        let spawned = std::thread::Builder::new().name("fd-closer".into()).spawn(move || {
            for fd in rx {
                drop(fd);
            }
        });
        match spawned {
            Ok(_) => LateCloser { tx: Some(tx) },
            Err(err) => {
                warn!(?err, "no thread to close devices: they close on the loop");
                LateCloser { tx: None }
            }
        }
    }

    /// Close `fd` soon, not here.
    pub fn close(&self, fd: OwnedFd) {
        if let Some(tx) = &self.tx {
            if let Err(std::sync::mpsc::SendError(fd)) = tx.send(fd) {
                drop(fd);
            }
        }
    }
}

/// A turn of the input source that holds the loop longer than this is logged.
pub const SLOW_INPUT_TURN: Duration = Duration::from_millis(8);

/// What one turn of the input source did, and how long it held the loop:
/// libinput's own dispatch (reading the devices; udev hotplug, which opens a
/// new device through the seat and closes a gone one) and our handlers.
#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub struct InputTurn {
    pub events: u32,
    pub added: u32,
    pub removed: u32,
    pub handlers: Duration,
    pub total: Duration,
}

impl InputTurn {
    fn count<B: smithay::backend::input::InputBackend>(&mut self, event: &InputEvent<B>) {
        self.events += 1;
        match event {
            InputEvent::DeviceAdded { .. } => self.added += 1,
            InputEvent::DeviceRemoved { .. } => self.removed += 1,
            _ => {}
        }
    }

    /// The log line of a slow turn (over [`SLOW_INPUT_TURN`]), else None.
    pub fn slow(&self) -> Option<String> {
        if self.total <= SLOW_INPUT_TURN {
            return None;
        }
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        Some(format!(
            "input turn held the loop {:.1} ms (libinput {:.1}, handlers {:.1}): {} events, devices +{} -{}",
            ms(self.total),
            ms(self.total.saturating_sub(self.handlers)),
            ms(self.handlers),
            self.events,
            self.added,
            self.removed,
        ))
    }
}

/// libinput as the loop's input source, each turn timed ([`InputTurn`]).
pub struct TimedInput {
    inner: LibinputInputBackend,
}

impl EventSource for TimedInput {
    type Event = InputEvent<LibinputInputBackend>;
    type Metadata = ();
    type Ret = ();
    type Error = std::io::Error;

    fn process_events<F>(
        &mut self,
        readiness: Readiness,
        token: Token,
        mut callback: F,
    ) -> std::io::Result<PostAction>
    where
        F: FnMut(Self::Event, &mut ()),
    {
        let started = Instant::now();
        let mut turn = InputTurn::default();
        let ret = self.inner.process_events(readiness, token, |event, meta| {
            turn.count(&event);
            let t = Instant::now();
            callback(event, meta);
            turn.handlers += t.elapsed();
        });
        turn.total = started.elapsed();
        if let Some(line) = turn.slow() {
            info!("{line}");
        }
        ret
    }

    fn register(&mut self, poll: &mut Poll, factory: &mut TokenFactory) -> calloop::Result<()> {
        self.inner.register(poll, factory)
    }

    fn reregister(&mut self, poll: &mut Poll, factory: &mut TokenFactory) -> calloop::Result<()> {
        self.inner.reregister(poll, factory)
    }

    fn unregister(&mut self, poll: &mut Poll) -> calloop::Result<()> {
        self.inner.unregister(poll)
    }
}

/// Forward an input event.
pub fn handle(state: &mut DuoWc, event: InputEvent<LibinputInputBackend>) {
    match event {
        InputEvent::DeviceAdded { mut device } => {
            device_added(&mut device);
            // A Surface pen's Bluetooth device (its top button): the
            // compositor's, nothing else's (crate::penbutton): never a
            // pointer, a tablet or a hardware keyboard of the seat.
            if crate::penbutton::device_added(&mut device) {
                return;
            }
            crate::tablet::device_added(state, &device);
            // A keyboard one can type on (a Bluetooth one): the on-screen
            // keyboard stays folded while it is there (crate::devicestate).
            let typing = crate::devicestate::is_typing_keyboard(
                device.has_capability(DeviceCapability::Keyboard),
                |key| device.keyboard_has_key(key).ok(),
            );
            state.device_state.device_added(device.sysname(), &device.name(), typing);
            // A mouse or a touchpad: the seat's pointer (crate::pointer).
            state.pointer_device_added(&device);
        }
        InputEvent::DeviceRemoved { device } => {
            crate::tablet::device_removed(state, &device);
            crate::penbutton::device_removed(&device);
            state.device_state.device_removed(device.sysname());
            state.pointer_device_removed(&device);
        }
        // A mouse or a touchpad (crate::pointer); only the devices taken as
        // one (never the digitizer's own HID collections).
        InputEvent::PointerMotion { event } => {
            if state.pointer.devices.contains(Event::device(&event).sysname()) {
                let delta = smithay::backend::input::PointerMotionEvent::delta(&event);
                state.pointer_motion(delta.x, delta.y, Event::time_msec(&event));
            }
        }
        InputEvent::PointerMotionAbsolute { event } => {
            if state.pointer.devices.contains(Event::device(&event).sysname()) {
                let fx =
                    smithay::backend::input::AbsolutePositionEvent::x_transformed(&event, 1_000_000) / 1e6;
                let fy =
                    smithay::backend::input::AbsolutePositionEvent::y_transformed(&event, 1_000_000) / 1e6;
                state.pointer_motion_absolute(fx, fy, Event::time_msec(&event));
            }
        }
        InputEvent::PointerButton { event } => {
            if state.pointer.devices.contains(Event::device(&event).sysname()) {
                let button = smithay::backend::input::PointerButtonEvent::button_code(&event);
                let pressed = smithay::backend::input::PointerButtonEvent::state(&event)
                    == smithay::backend::input::ButtonState::Pressed;
                state.pointer_button(button, pressed, Event::time_msec(&event));
            }
        }
        InputEvent::PointerAxis { event } => {
            if state.pointer.devices.contains(Event::device(&event).sysname()) {
                state.pointer_axis(crate::pointer::axis_frame(&event));
            }
        }
        InputEvent::TabletToolProximity { event } => crate::tablet::proximity(state, event),
        InputEvent::TabletToolAxis { event } => crate::tablet::axis(state, event),
        InputEvent::TabletToolTip { event } => crate::tablet::tip(state, event),
        InputEvent::TabletToolButton { event } => crate::tablet::button(state, event),
        InputEvent::Keyboard { event } => keyboard(state, event),
        InputEvent::TouchDown { event } => {
            let output = Event::device(&event).output_name().map(|o| o.into_owned());
            let (fx, fy) = (event.x_transformed(1_000_000) / 1e6, event.y_transformed(1_000_000) / 1e6);
            touch_down(state, output, event.seat_slot(), Event::time_msec(&event), fx, fy);
        }
        InputEvent::TouchMotion { event } => {
            let (fx, fy) = (event.x_transformed(1_000_000) / 1e6, event.y_transformed(1_000_000) / 1e6);
            touch_motion(state, event.seat_slot(), Event::time_msec(&event), fx, fy);
        }
        InputEvent::TouchUp { event } => touch_up(state, event.seat_slot(), Event::time_msec(&event)),
        InputEvent::TouchFrame { .. } => touch_frame(state),
        InputEvent::TouchCancel { .. } => {
            state.trackpad_cancel();
            state.laptop_touch_cancel();
            if state.desk.drag.as_ref().map(|d| d.touch.is_some()) == Some(true) {
                state.desk_drag_cancel();
            }
            state.cancel_all_touches();
            state.touch_slots.clear();
            state.pending_focus.clear();
            state.osk_keys.clear();
            state.held_touches.clear();
            for (_, touch) in state.swipes.drain() {
                if let Some(timer) = touch.timer {
                    state.loop_handle.remove(timer);
                }
            }
            state.taskview.touches.clear();
            if state.divider.take().is_some() {
                state.queue_redraw_all();
            }
            state.carry.holds.clear();
            state.carry.ignored.clear();
            state.carry.back = None;
            state.tent_taps.clear();
            state.tent_double.reset();
            if let Some(drag) = state.carry.drag.as_mut() {
                // Back where it was.
                drag.target = None;
                state.drag_drop("touch cancelled");
            }
            // A window let go at the hinge: no finger picks it up again.
            state.landing_forget();
            state.queue_redraw_all();
        }
        _ => {}
    }
}

/// A touch point goes down on `output` (the device's panel; `None`: a device
/// with no panel), at `fx`, `fy`: the fraction (0..1) of the device's range.
/// Also the pen's tip for a client that takes no tablets ([`crate::tablet`]).
pub(crate) fn touch_down(
    state: &mut DuoWc,
    output: Option<String>,
    slot_id: u32,
    time: u32,
    fx: f64,
    fy: f64,
) {
    let Some(output) = output else {
        return;
    };
    // A finger on a panel: the mouse cursor goes away until the mouse moves
    // (not on the panel that is the display's touchpad, crate::trackpad).
    if !state.trackpad_takes(&output) && !state.laptop_takes(&output) {
        state.pointer_hide_for_touch();
    }
    let lit = state.is_lit(&output);
    let verdict =
        crate::power::touch_on_panel(state.panel_shows(&output), lit, state.screen_dark(), state.touch_wake);
    if verdict != crate::power::TouchOnPanel::Deliver {
        let wake = verdict == crate::power::TouchOnPanel::SwallowAndWake;
        if lit {
            // Lit for duo-wc but black on the glass (waking, or its
            // picture not up yet): nobody gets this finger, to its lift.
            info!(
                output,
                slot = slot_id,
                "touch on a panel whose picture is not on screen yet, not delivered"
            );
            if let Some(crtc) = state.tty.surfaces().iter().find(|s| s.name == output).map(|s| s.crtc) {
                state.queue_redraw(crtc);
            }
        } else {
            debug!(output, slot = slot_id, wake, "touch on a dark panel, not delivered");
        }
        if !lit && state.tent_tap_wanted(&output) {
            // A tent or a brochure: a double tap on the dark panel
            // makes it the one in use (pose::DoubleTap).
            let at = dark_point(state, &output, fx * 1_000_000.0, fy * 1_000_000.0);
            state.tent_taps.insert(slot_id, (output.clone(), time, at));
        }
        state.touch_slots.insert(slot_id, TouchSlot::held(&output, Point::default(), time));
        if wake {
            state.wake("touch");
        }
        return;
    }
    if state.ambient.is_some() {
        // The peek / glance screen: a hand holds a closed phone.
        debug!(output, slot = slot_id, "touch on the ambient screen, not delivered");
        state.touch_slots.insert(slot_id, TouchSlot::held(&output, Point::default(), time));
        return;
    }
    let Some(location) = touch_location(state, &output, |w| fx * w as f64, |h| fy * h as f64) else {
        return;
    };
    state.note_activity();
    // The panel is the display's touchpad: the finger drives the pointer.
    if state.trackpad_takes(&output) {
        state.trackpad_down(slot_id, (location.x, location.y), time);
        return;
    }
    // Laptop mode's keyboard panel: its keys and its touchpad, never an
    // app's (crate::laptopmode).
    if state.laptop_takes(&output) {
        state.laptop_touch_down(slot_id, location, time);
        return;
    }
    // A carried window crossing the hinge: this finger goes on with it.
    if state.drag_stitch(slot_id, &output, location, time)
        || state.overview_stitch(slot_id, &output, location, time)
    {
        state.touch_slots.insert(slot_id, TouchSlot::held(&output, location, time));
        return;
    }
    if state.edge_swipe && !state.lock.locked {
        let arms = edge_arms(state, &output);
        let zone = state.edge_zone;
        if let Some(edge) = output_area(state, &output)
            .and_then(|area| crate::gesture::edge_at_zone(area, location.x, location.y, arms, zone))
        {
            debug!(output, slot = slot_id, edge = edge.name(), "touch held at an edge");
            state.touch_slots.insert(slot_id, TouchSlot::held(&output, location, time));
            state.held_touches.insert(
                slot_id,
                HeldTouch {
                    held: Held::new(edge, (location.x, location.y)),
                    output,
                    start: location,
                    start_time: time,
                    at: location,
                },
            );
            arm_held_expiry(state, slot_id, time);
            return;
        }
    }
    begin_touch(state, slot_id, &output, location, time);
}

/// A touch point moves (`fx`, `fy` as in [`touch_down`]).
pub(crate) fn touch_motion(state: &mut DuoWc, slot_id: u32, time: u32, fx: f64, fy: f64) {
    if state.laptop_on() {
        let keys = state.laptop.keys.clone();
        if let Some(at) = touch_location(state, &keys, |w| fx * w as f64, |h| fy * h as f64) {
            if state.laptop_touch_motion(slot_id, at, time) {
                return;
            }
        }
    }
    // A window of a desk carried by a finger (crate::desk).
    if state.desk.drag.as_ref().and_then(|d| d.touch) == Some(slot_id) {
        if let Some(output) = state.touch_slots.get(&slot_id).map(|s| s.output.clone()) {
            if let Some(at) = touch_location(state, &output, |w| fx * w as f64, |h| fy * h as f64) {
                if let Some(slot) = state.touch_slots.get_mut(&slot_id) {
                    slot.location = at;
                }
                state.desk_drag_motion((at.x, at.y));
            }
        }
        return;
    }
    if state.trackpad.pad.owns(slot_id) {
        if let Some(panel) = state.trackpad.panel.clone() {
            if let Some(at) = touch_location(state, &panel, |w| fx * w as f64, |h| fy * h as f64) {
                state.trackpad_motion(slot_id, (at.x, at.y), time);
            }
        }
        return;
    }
    if state.carry.ignored.contains(&slot_id) {
        return;
    }
    let carrying = state.carry.drag.as_ref().map(|d| d.slot == slot_id) == Some(true);
    if carrying {
        let Some(output) = state.touch_slots.get(&slot_id).map(|s| s.output.clone()) else {
            return;
        };
        if let Some(location) = touch_location(state, &output, |w| fx * w as f64, |h| fy * h as f64) {
            state.drag_motion(slot_id, location, &output);
        }
        return;
    }
    let claimed = state
        .taskview
        .touches
        .get(&slot_id)
        .map(|t| t.finger_output.clone())
        .or_else(|| state.swipes.get(&slot_id).map(|s| s.output.clone()))
        .or_else(|| state.divider.as_ref().filter(|d| d.slot == slot_id).map(|d| d.output.clone()));
    if let Some(output) = claimed {
        let Some(location) = touch_location(state, &output, |w| fx * w as f64, |h| fy * h as f64) else {
            return;
        };
        if state.overview_touch_motion(slot_id, location) || state.divider_touch_motion(slot_id, location) {
            return;
        }
        state.note_activity();
        let restart = match state.swipes.get_mut(&slot_id) {
            Some(touch) => {
                let moved = touch.swipe.moved(location.x, location.y) && !touch.done;
                touch.furthest = touch.furthest.max(touch.swipe.travel());
                moved && touch.swipe.edge == crate::gesture::Edge::Bottom
            }
            None => false,
        };
        if restart {
            arm_hold(state, slot_id);
        }
        update_back_arrow(state, slot_id);
        return;
    }
    if let Some(held) = state.held_touches.get(&slot_id).cloned() {
        let Some(location) = touch_location(state, &held.output, |w| fx * w as f64, |h| fy * h as f64) else {
            return;
        };
        state.note_activity();
        match held.held.moved(location.x, location.y) {
            Verdict::Holding => {
                if let Some(h) = state.held_touches.get_mut(&slot_id) {
                    h.at = location;
                }
                return;
            }
            Verdict::Swipe => {
                state.held_touches.remove(&slot_id);
                debug!(output = held.output, edge = held.held.edge.name(), "edge swipe");
                let edge = held.held.edge;
                // The keyboard does not move yet: a swipe that
                // becomes the dock or the overview leaves it where it
                // is (moving it here woke the on-screen keyboard
                // under the opening overview); the app grid takes
                // the panel when the swipe is decided (swipe_finished).
                let panel_h = output_area(state, &held.output).map(|a| a.h).unwrap_or(0.0);
                let swipe = Swipe::new(&held.held, (location.x, location.y));
                state.swipes.insert(
                    slot_id,
                    SwipeTouch {
                        swipe,
                        output: held.output.clone(),
                        panel_h,
                        gen: 0,
                        // A swipe down from the top is decided now:
                        // the quick menu comes at once.
                        done: edge == crate::gesture::Edge::Top,
                        timer: None,
                        furthest: swipe.travel(),
                    },
                );
                match edge {
                    crate::gesture::Edge::Bottom => arm_hold(state, slot_id),
                    crate::gesture::Edge::Top => {
                        if let Some(touch) = state.swipes.get(&slot_id).cloned() {
                            swipe_finished(state, &touch, SwipeKind::Down);
                        }
                    }
                    crate::gesture::Edge::Left | crate::gesture::Edge::Right => {
                        update_back_arrow(state, slot_id);
                    }
                }
                // The slot stays undelivered: the rest of it is the
                // compositor's.
                return;
            }
            Verdict::Release => {
                state.held_touches.remove(&slot_id);
                if !state.panel_shows(&held.output) {
                    return;
                }
                debug!(slot = slot_id, "edge touch released: not a swipe");
                if held.at != held.start {
                    // Went on from where it was let go of before:
                    // nothing to do, the motion below takes it there.
                }
                if begin_touch(state, slot_id, &held.output, held.start, held.start_time) {
                    if !state.overview_touch_motion(slot_id, location) {
                        state.divider_touch_motion(slot_id, location);
                    }
                    return;
                }
                // ... and on to the motion below, from where it went down.
            }
        }
    }
    let output = match state.touch_slots.get(&slot_id) {
        Some(slot) if slot.delivered => slot.output.clone(),
        _ => return,
    };
    let Some(location) = touch_location(state, &output, |w| fx * w as f64, |h| fy * h as f64) else {
        return;
    };
    state.note_activity();
    state.touch_frame_delivered = true;
    if let Some(slot) = state.touch_slots.get_mut(&slot_id) {
        slot.location = location;
        slot.time = time;
    }
    if let Some(pending) = state.pending_focus.get_mut(&slot_id) {
        pending.tentative.moved(location.x, location.y);
    }
    state.top_hold_motion(slot_id, location);
    state.android_tap_motion(slot_id, location);
    let Some(touch) = state.seat.get_touch() else {
        return;
    };
    let location = state.client_touch_location(slot_id, location);
    touch.motion(state, None, &MotionEvent { slot: Some(slot_id).into(), location, time });
}

/// A touch point lifts.
pub(crate) fn touch_up(state: &mut DuoWc, slot_id: u32, time: u32) {
    state.osk_keys.remove(&slot_id);
    if state.trackpad_up(slot_id, time) || state.laptop_touch_up(slot_id, time) {
        return;
    }
    if state.desk.drag.as_ref().and_then(|d| d.touch) == Some(slot_id) {
        let at = state.touch_slots.remove(&slot_id).map(|s| (s.location.x, s.location.y));
        if let Some(at) = at {
            state.desk_drag_end_at(at, time);
        } else {
            state.desk.drag = None;
        }
        return;
    }
    if let Some((output, down, at)) = state.tent_taps.remove(&slot_id) {
        if state.tent_tap_wanted(&output) {
            if state.tent_double.tap(&output, down, time, at) {
                info!(output, "double tap on the tent's dark panel: switching to it");
                state.touch_slots.remove(&slot_id);
                state.tent_switch(&output);
                return;
            }
            debug!(output, "a tap on the tent's dark panel (a second one switches)");
        }
    }
    if state.drag_up(slot_id, time) {
        state.touch_slots.remove(&slot_id);
        return;
    }
    if state.overview_touch_up(slot_id, time) || state.divider_touch_up(slot_id) {
        state.touch_slots.remove(&slot_id);
        return;
    }
    if let Some(touch) = state.swipes.remove(&slot_id) {
        state.touch_slots.remove(&slot_id);
        if let Some(timer) = touch.timer {
            state.loop_handle.remove(timer);
        }
        if touch.swipe.edge.is_side() {
            let back = touch.swipe.is_back(touch.furthest);
            state.carry.back = None;
            state.queue_redraw_all();
            if back {
                swipe_finished(state, &touch, SwipeKind::Back);
            } else {
                debug!(output = touch.output, "side swipe let go short: no back");
            }
        } else if !touch.done {
            swipe_finished(state, &touch, touch.swipe.lifted(touch.panel_h));
        }
        return;
    }
    if let Some(held) = state.held_touches.remove(&slot_id) {
        // Lifted before anything was decided: a tap at the edge.
        if held.held.lifted() == Verdict::Release && state.panel_shows(&held.output) {
            debug!(slot = slot_id, "edge touch lifted: delivered as a tap");
            if begin_touch(state, slot_id, &held.output, held.start, held.start_time) {
                if !state.overview_touch_up(slot_id, time) {
                    state.divider_touch_up(slot_id);
                }
                state.touch_slots.remove(&slot_id);
                return;
            }
        }
    }
    let pending = state.pending_focus.remove(&slot_id);
    let slot = match state.touch_slots.remove(&slot_id) {
        Some(slot) if slot.delivered => slot,
        _ => return,
    };
    if let Some(pending) = pending {
        // A touch that would take the keyboard elsewhere while the
        // on-screen keyboard is up: only a tap does (oskguard).
        if pending.tentative.commits(time) {
            info!(output = pending.output, "a tap takes the keyboard there");
            state.focus_by_touch(&pending.output, pending.window.as_ref(), pending.on_window);
        } else {
            info!(output = pending.output, "not a tap (or amid typing): the keyboard stays");
        }
    }
    state.top_hold_up(slot_id, time);
    state.android_tap_up(slot_id, time);
    // A tap beside a text field: the on-screen keyboard may go away
    // (crate::oskleave).
    let moved = (slot.location.x - slot.start.x).hypot(slot.location.y - slot.start.y);
    if time.wrapping_sub(slot.down) <= crate::oskguard::TAP_MS && moved <= crate::oskguard::TAP_SLOP {
        if let Some((surface, _)) = &slot.surface {
            state.osk_tap(surface, slot.start);
        }
    }
    let Some(touch) = state.seat.get_touch() else {
        return;
    };
    debug!(slot = slot_id, "touch up");
    state.touch_frame_delivered = true;
    touch.up(state, &UpEvent { slot: Some(slot_id).into(), serial: SERIAL_COUNTER.next_serial(), time });
}

/// The end of a touch frame: the clients' frame, if anything went to them.
pub(crate) fn touch_frame(state: &mut DuoWc) {
    if !std::mem::take(&mut state.touch_frame_delivered) {
        return;
    }
    if let Some(touch) = state.seat.get_touch() {
        touch.frame(state);
    }
}

/// (Re)start a swipe's hold timer: if the finger is still down and has not
/// moved on when it fires, the overview opens.
fn arm_hold(state: &mut DuoWc, slot_id: u32) {
    state.swipe_gen += 1;
    let gen = state.swipe_gen;
    let Some(touch) = state.swipes.get_mut(&slot_id) else {
        return;
    };
    touch.gen = gen;
    let old = touch.timer.take();
    if let Some(old) = old {
        state.loop_handle.remove(old);
    }
    let timer = smithay::reexports::calloop::timer::Timer::from_duration(std::time::Duration::from_millis(
        crate::gesture::HOLD_MS,
    ));
    let inserted = state.loop_handle.insert_source(timer, move |_, _, state: &mut DuoWc| {
        state.events_dirty = true;
        let held = match state.swipes.get_mut(&slot_id) {
            Some(touch) if touch.gen == gen && !touch.done => {
                touch.done = true;
                touch.timer = None;
                Some(touch.clone())
            }
            _ => None,
        };
        if let Some(touch) = held {
            swipe_finished(state, &touch, SwipeKind::Hold);
            state.overview_open("swipe and hold");
        }
        smithay::reexports::calloop::timer::TimeoutAction::Drop
    });
    match inserted {
        Ok(token) => {
            if let Some(touch) = state.swipes.get_mut(&slot_id) {
                touch.timer = Some(token);
            }
        }
        Err(err) => warn!(?err, "failed to start the hold timer"),
    }
}

/// An edge swipe is over: tell the event stream what it was. A long one (the
/// app grid) closes the overview first, so the grid is not under it.
fn swipe_finished(state: &mut DuoWc, touch: &SwipeTouch, kind: SwipeKind) {
    let mut kind = kind;
    let mut to = None;
    let shade = state.layer_shown(&touch.output, SHADE_NAMESPACES);
    match kind {
        SwipeKind::Long if !shade => {
            // Home, or on a panel that shows home already, the app grid
            // (which takes the keyboard there).
            state.overview_close("home");
            let gone_home = state.output_by_name(&touch.output).map(|o| state.go_home(&o)).unwrap_or(false);
            if gone_home {
                kind = SwipeKind::Home;
            } else {
                state.active_output = Some(touch.output.clone());
            }
        }
        SwipeKind::Back => to = Some(go_back(state, &touch.output)),
        _ => {}
    }
    info!(
        output = touch.output,
        edge = touch.swipe.edge.name(),
        kind = kind.name(),
        travel = touch.swipe.travel(),
        shade,
        "edge swipe"
    );
    crate::ipc::broadcast(
        state,
        &serde_json::json!({
            "event": "edge_swipe",
            "edge": touch.swipe.edge.name(),
            "output": touch.output,
            "kind": kind.name(),
            "travel": (touch.swipe.travel() * 10.0).round() / 10.0,
            "to": to,
        }),
    );
}

/// The shell's surfaces a bottom swipe closes instead of doing its own
/// thing (the quick menu, pulled down over the panel).
const SHADE_NAMESPACES: &[&str] = &["duo-quick"];

/// The shell's surfaces a back swipe closes (the quick menu, the app grid,
/// a question). The home screen's edit mode ends on a back on a panel with
/// no window (`to: none`).
const BACK_CLOSES: &[&str] = &["duo-quick", "duo-launcher", "duo-confirm", "duo-sim-pin"];

/// The evdev code of the Back key (XF86Back).
const KEY_BACK: u32 = 158;

/// Back on a panel: the overview closes if it is open; else a shell
/// surface there closes (the shell does it, told on the event stream);
/// else the panel's front window gets the keyboard and a Back key.
fn go_back(state: &mut DuoWc, output: &str) -> &'static str {
    if state.taskview.open {
        state.overview_close("back");
        return "overview";
    }
    if state.layer_shown(output, BACK_CLOSES) {
        return "shell";
    }
    let Some(window) = state.output_by_name(output).and_then(|o| state.topmost_on(&o)) else {
        return "none";
    };
    if state.is_lit(output) {
        state.active_output = Some(output.to_owned());
    }
    state.raise_window(&window, true);
    state.refresh_keyboard_focus();
    let Some(keyboard) = state.seat.get_keyboard() else {
        return "none";
    };
    let time = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u32)
        .unwrap_or(0);
    let code = smithay::input::keyboard::Keycode::new(KEY_BACK + XKB_OFFSET);
    for (key_state, t) in [(KeyState::Pressed, time), (KeyState::Released, time.wrapping_add(1))] {
        keyboard.input::<(), _>(state, code, key_state, SERIAL_COUNTER.next_serial(), t, |_, _, _| {
            FilterResult::Forward
        });
    }
    debug!(output, id = crate::state::window_data(&window).id, "Back key sent");
    "app"
}

/// Which edges of a panel are armed: the bottom and the top always, the
/// sides as `back_swipe` says (outer: only the edge away from the hinge;
/// panels one above the other, or one panel alone, have two outer sides).
fn edge_arms(state: &DuoWc, output: &str) -> crate::gesture::Arms {
    use crate::config::BackSwipe;
    let mut arms = crate::gesture::Arms { bottom: true, top: true, left: false, right: false };
    match state.back_swipe {
        BackSwipe::Off => {}
        BackSwipe::Both => {
            arms.left = true;
            arms.right = true;
        }
        BackSwipe::Outer => {
            let panels = state.snap_panels(u64::MAX);
            let rects: Vec<crate::overview::Rect> = panels.iter().map(|p| p.rect).collect();
            let index = panels.iter().position(|p| p.name == output);
            match (crate::snap::hinge(&rects), index) {
                (Some(h @ crate::snap::Hinge::Vertical { .. }), Some(i)) => {
                    let facing = h.facing_side(i);
                    arms.left = facing != Some(crate::split::Side::Left);
                    arms.right = facing != Some(crate::split::Side::Right);
                }
                _ => {
                    arms.left = true;
                    arms.right = true;
                }
            }
        }
    }
    arms
}

/// A held side or top touch that has not become a swipe in time is given to
/// the clients after all (a resting hand never becomes a back).
fn arm_held_expiry(state: &mut DuoWc, slot_id: u32, start_time: u32) {
    let Some(held) = state.held_touches.get(&slot_id) else {
        return;
    };
    if !held.held.expired(u64::MAX) {
        return;
    }
    let timer = smithay::reexports::calloop::timer::Timer::from_duration(std::time::Duration::from_millis(
        crate::gesture::SIDE_HOLD_MAX_MS,
    ));
    let _ = state.loop_handle.insert_source(timer, move |_, _, state: &mut DuoWc| {
        state.events_dirty = true;
        let still = state.held_touches.get(&slot_id).map(|h| h.start_time == start_time).unwrap_or(false);
        if still {
            if let Some(held) = state.held_touches.remove(&slot_id) {
                if state.panel_shows(&held.output) {
                    debug!(slot = slot_id, edge = held.held.edge.name(), "edge touch held still: delivered");
                    if !begin_touch(state, slot_id, &held.output, held.start, held.start_time)
                        && held.at != held.start
                    {
                        deliver_motion(state, slot_id, held.at, held.start_time);
                    }
                }
            }
        }
        smithay::reexports::calloop::timer::TimeoutAction::Drop
    });
}

/// A motion of a delivered touch point, sent by the compositor itself.
fn deliver_motion(state: &mut DuoWc, slot_id: u32, location: Point<f64, Logical>, time: u32) {
    if let Some(slot) = state.touch_slots.get_mut(&slot_id) {
        slot.location = location;
    }
    let Some(touch) = state.seat.get_touch() else {
        return;
    };
    let location = state.client_touch_location(slot_id, location);
    touch.motion(state, None, &MotionEvent { slot: Some(slot_id).into(), location, time });
    touch.frame(state);
}

/// The back arrow follows a side swipe.
fn update_back_arrow(state: &mut DuoWc, slot_id: u32) {
    let Some(touch) = state.swipes.get(&slot_id) else {
        return;
    };
    if !touch.swipe.edge.is_side() {
        return;
    }
    let Some(area) = output_area(state, &touch.output) else {
        return;
    };
    let travel = touch.swipe.travel().max(0.0);
    state.carry.back = Some(crate::fx::BackArrow {
        output: touch.output.clone(),
        from_left: touch.swipe.edge == crate::gesture::Edge::Left,
        y: touch.swipe.at.1 - area.y,
        travel,
        armed: touch.swipe.is_back(touch.furthest),
    });
    state.queue_redraw_all();
}

/// A touch point starts where nothing held it back: on the overview if it is
/// open there, or on a split's divider (true: the compositor's), otherwise
/// for the clients.
fn begin_touch(
    state: &mut DuoWc,
    slot_id: u32,
    output: &str,
    location: Point<f64, Logical>,
    time: u32,
) -> bool {
    if let Some(out) = state.output_by_name(output) {
        if state.overview_takes(&out, location) {
            state.touch_slots.insert(slot_id, TouchSlot::held(output, location, time));
            state.overview_touch_down(slot_id, &out, location);
            return true;
        }
        if state.divider_takes(&out, location) {
            state.touch_slots.insert(slot_id, TouchSlot::held(output, location, time));
            state.divider_touch_down(slot_id, &out);
            return true;
        }
    }
    deliver_down(state, slot_id, output, location, time);
    false
}

/// A touch point goes down for the clients: the panel becomes the active one,
/// the window under it comes to the front and gets the keyboard.
fn deliver_down(state: &mut DuoWc, slot_id: u32, output: &str, location: Point<f64, Logical>, time: u32) {
    let serial = SERIAL_COUNTER.next_serial();
    state.touch_slots.insert(
        slot_id,
        TouchSlot {
            output: output.to_owned(),
            delivered: true,
            location,
            time,
            serial: Some(serial),
            window: None,
            start: location,
            down: time,
            surface: None,
        },
    );
    state.touch_frame_delivered = true;
    debug!(output, slot = slot_id, x = location.x, y = location.y, "touch down");

    let focus = state.surface_under(location);
    // Where the surface under the finger is now: its motion goes to the
    // client relative to where it is then (it may move under the finger).
    let anchor =
        focus.as_ref().and_then(|(surface, _)| state.surface_origin(surface).map(|o| (surface.clone(), o)));
    if let Some(slot) = state.touch_slots.get_mut(&slot_id) {
        slot.surface = anchor;
    }
    let root = focus.as_ref().map(|(surface, _)| {
        let mut root = surface.clone();
        while let Some(parent) = smithay::wayland::compositor::get_parent(&root) {
            root = parent;
        }
        root
    });
    let layer = root.as_ref().and_then(|root| state.layer_of_surface(root));
    let on_layer = layer.is_some();
    if layer.as_ref().map(|l| crate::state::OSK_NAMESPACES.contains(&l.namespace())) == Some(true) {
        // A key: typing goes on (a touch elsewhere now is the other hand).
        state.osk_keys.insert(slot_id);
        state.last_osk_touch = Some(time);
    }
    // A layer surface that takes no keyboard (a status bar, the dock, a
    // banner, the on-screen keyboard itself): touching it moves nothing,
    // neither the active panel nor the keyboard (a tap on the other panel's
    // bar used to take the on-screen keyboard there).
    let layer_wants_keyboard = layer.as_ref().map(|l| l.can_receive_keyboard_focus()).unwrap_or(false);
    // Touching a window (not a layer surface over it) says where its text
    // field is: in span the on-screen keyboard goes to the other panel.
    let on_window =
        !on_layer && root.as_ref().map(|root| state.window_for_surface(root).is_some()).unwrap_or(false);
    // The window touched comes to the front (with its split partner): the
    // one the surface under the finger belongs to (a popup: the window under
    // it). Not when the finger is on a layer surface (the dock, the bar, the
    // keyboard): the window below it was not touched, and raising it would
    // change the window list under the shell's finger (the dock rebuilt its
    // buttons mid-tap and lost the tap).
    let touched = match &root {
        _ if on_layer => None,
        None => None,
        Some(root) => state.window_for_surface(root).or_else(|| state.window_under(location).map(|(w, _)| w)),
    };
    if let Some(window) = &touched {
        let id = crate::state::window_data(window).id;
        state.carry.last_touch = Some((id, std::time::Instant::now()));
        if let Some(slot) = state.touch_slots.get_mut(&slot_id) {
            slot.window = Some(id);
        }
        // A window without a title bar of its own: held on its top, it lifts.
        if on_window && !state.taskview.open && state.on_top_strip(window, location) {
            state.top_hold_start(slot_id, window, location, time);
        }
        // An Android app's caption bar: a double tap there is full screen.
        if on_window && !state.taskview.open {
            state.android_tap_down(slot_id, window, location, time);
        }
    }
    if on_layer && !layer_wants_keyboard {
        // Nothing moves.
    } else if state.osk_output().is_some() && state.touch_moves_keyboard(output, touched.as_ref(), on_window)
    {
        // The on-screen keyboard is up and this touch would take the keyboard
        // to another window (or the field to the other panel): only if it is
        // a tap, decided when it lifts (crate::oskguard). It reaches the app
        // now either way.
        let typing = crate::oskguard::typing(time, state.last_osk_touch, !state.osk_keys.is_empty());
        debug!(output, slot = slot_id, typing, "touch would move the keyboard: decided on its lift");
        state.pending_focus.insert(
            slot_id,
            crate::state::PendingFocus {
                tentative: crate::oskguard::Tentative::new((location.x, location.y), time, typing),
                output: output.to_owned(),
                window: touched.clone(),
                on_window,
            },
        );
    } else {
        state.focus_by_touch(output, touched.as_ref(), on_window);
    }
    // Where it is for the client: a window shown scaled down takes it in
    // its own coordinates (crate::dialog), as the focus origin is.
    let client =
        focus.as_ref().map(|(surface, _)| state.client_location(surface, location)).unwrap_or(location);
    match &focus {
        None => debug!(output, "touch down on nothing"),
        Some((surface, origin)) => debug!(
            output,
            target = state.describe_surface(surface),
            local_x = client.x - origin.x,
            local_y = client.y - origin.y,
            "touch goes to"
        ),
    }

    let Some(touch) = state.seat.get_touch() else {
        return;
    };
    touch.down(state, focus, &DownEvent { slot: Some(slot_id).into(), location: client, serial, time });
}

/// An output's rectangle in the global space, for the edge test.
fn output_area(state: &DuoWc, name: &str) -> Option<Area> {
    let output = state.output_by_name(name)?;
    let g = state.space.output_geometry(&output)?;
    Some(Area { x: g.loc.x as f64, y: g.loc.y as f64, w: g.size.w as f64, h: g.size.h as f64 })
}

/// Decide what a new device is for.
fn device_added(device: &mut libinput::Device) {
    let name = device.name().into_owned();
    let output = device.output_name().map(|o| o.into_owned());
    let touch = device.has_capability(DeviceCapability::Touch);
    let tablet = device.has_capability(DeviceCapability::TabletTool);

    if (touch || tablet) && output.is_none() {
        // The kernel's whole-digitizer devices: no panel to map them to.
        match device.config_send_events_set_mode(SendEventsMode::DISABLED) {
            Ok(()) => info!(device = name, "ignoring a touch device with no output (WL_OUTPUT)"),
            Err(err) => warn!(device = name, ?err, "could not switch off an unmapped touch device"),
        }
        return;
    }
    info!(device = name, output = ?output, touch, tablet, "input device");
}

/// Where a touch on a dark panel is, in that panel's own (unrotated) frame
/// in logical px: for the tent's double tap, which only compares two such
/// points. `x`, `y` are the device position scaled to 0..1_000_000.
fn dark_point(state: &DuoWc, name: &str, x: f64, y: f64) -> (f64, f64) {
    let (w, h) = state
        .output_by_name(name)
        .and_then(|o| o.current_mode().map(|m| (m.size.w as f64, m.size.h as f64)))
        .unwrap_or((1344.0, 1892.0));
    let scale = state.scale.max(1.0);
    (x / 1_000_000.0 * w / scale, y / 1_000_000.0 * h / scale)
}

/// A touch position in the global space, on the output the device belongs to.
///
/// The device's range is the panel in its own, unrotated frame; the position is
/// taken as a fraction of it and turned with the output's rotation.
pub(crate) fn touch_location(
    state: &DuoWc,
    name: &str,
    x: impl Fn(u32) -> f64,
    y: impl Fn(u32) -> f64,
) -> Option<Point<f64, Logical>> {
    let Some(output) = state.output_by_name(name) else {
        debug!(output = name, "touch on an output that is not up");
        return None;
    };
    let geometry = state.space.output_geometry(&output)?;
    let mode = output.current_mode()?.size;
    if mode.w <= 0 || mode.h <= 0 {
        return None;
    }
    let normalised = (x(mode.w as u32) / mode.w as f64, y(mode.h as u32) / mode.h as f64);
    let local = crate::rotation::touch_to_local(
        normalised,
        Rotation::from_transform(output.current_transform()),
        mode,
        output.current_scale().fractional_scale(),
    );
    Some(geometry.loc.to_f64() + local)
}

fn keyboard(
    state: &mut DuoWc,
    event: <LibinputInputBackend as smithay::backend::input::InputBackend>::KeyboardKeyEvent,
) {
    // A Surface pen's top button: never the seat's (crate::penbutton).
    let device = Event::device(&event);
    if crate::penbutton::takes(device.sysname()) {
        crate::penbutton::key(state, device.sysname(), event.key_code().raw(), event.state());
        return;
    }
    key_input(state, event.key_code(), event.state(), event.time_msec());
}

/// One key of the seat (xkb keycode: evdev + 8): a plugged keyboard's, or
/// laptop mode's keyboard panel's (crate::laptopmode), through the same
/// rules (the power and volume keys, a dark screen, the compositor's keys).
pub(crate) fn key_input(
    state: &mut DuoWc,
    keycode: smithay::input::keyboard::Keycode,
    key_state: KeyState,
    time: u32,
) {
    let Some(keyboard) = state.seat.get_keyboard() else {
        return;
    };
    let serial = SERIAL_COUNTER.next_serial();
    let running = state.running.clone();
    let code = keycode.raw();

    if code == KEY_POWER + XKB_OFFSET {
        match key_state {
            KeyState::Pressed => state.power_key.press(time),
            KeyState::Released => match state.power_key.release(time) {
                PowerPress::Short => state.power_key_pressed(),
                PowerPress::Long => info!("power key held: ignored (long presses are not bound)"),
                PowerPress::Stray => {}
            },
        }
        return;
    }
    if let Some(key) = code.checked_sub(XKB_OFFSET).and_then(volume_key) {
        // The ambient screen counts as dark: the volume changes, nothing shows.
        let dark = state.screen_dark() || state.ambient.is_some();
        let pressed = key_state == KeyState::Pressed;
        if pressed && !dark {
            state.note_activity();
        }
        let output = if dark { None } else { state.active_output().map(|o| o.name()) };
        debug!(key, pressed, dark, ?output, "volume key");
        crate::ipc::broadcast(state, &volume_key_event(key, pressed, output.as_deref()));
        return;
    }
    match key_state {
        KeyState::Pressed if state.screen_dark() || state.ambient.is_some() => {
            if state.ext_connected() && state.ambient.is_none() {
                // At a desk (an external display up): a key wakes the
                // screen, as on a computer (the key itself is not delivered).
                state.wake("key, external display");
            }
            debug!(code, "key on a dark screen, not delivered");
            state.suppressed_keys.insert(code);
            return;
        }
        KeyState::Pressed => state.note_activity(),
        KeyState::Released if state.suppressed_keys.remove(&code) => return,
        KeyState::Released => {}
    }

    let intercepted =
        keyboard.input::<(), _>(state, keycode, key_state, serial, time, |state, modifiers, handle| {
            if key_state == KeyState::Pressed
                && state.taskview.open
                && handle.modified_sym() == Keysym::Escape
            {
                state.overview_close("Escape");
                return FilterResult::Intercept(());
            }
            // The compositor's keys for windows and outputs (crate::extout):
            // Super + arrows, Super + Shift + left / right, Alt + Tab.
            if key_state == KeyState::Pressed
                && !state.lock.locked
                && state.desk_shortcut(
                    modifiers.logo,
                    modifiers.shift,
                    modifiers.alt,
                    handle.modified_sym().raw(),
                )
            {
                return FilterResult::Intercept(());
            }
            if key_state == KeyState::Pressed && is_quit(modifiers, handle.modified_sym()) {
                info!("quit combination pressed");
                running.store(false, std::sync::atomic::Ordering::SeqCst);
                return FilterResult::Intercept(());
            }
            FilterResult::Forward
        });
    // A key of the seat went to a client: the seat's keymap is the active one
    // (crate::keymapsync).
    if intercepted.is_none() && keyboard.current_focus().is_some() {
        state.keymap_sync.seat_key();
    }
    // A plugged keyboard may have switched the layout (xkb_options grp:...):
    // laptop mode's keys show the one in force.
    state.laptop_note_group();
}

/// Ctrl+Alt+Backspace, the way out with a keyboard plugged in.
fn is_quit(modifiers: &ModifiersState, keysym: Keysym) -> bool {
    modifiers.ctrl && modifiers.alt && keysym == Keysym::BackSpace
}

/// A volume key by its evdev code: `up`, `down` or `mute`.
fn volume_key(evdev_code: u32) -> Option<&'static str> {
    match evdev_code {
        KEY_VOLUMEUP => Some("up"),
        KEY_VOLUMEDOWN => Some("down"),
        KEY_MUTE => Some("mute"),
        _ => None,
    }
}

/// The event-stream line for a volume key; `output` is the panel for the
/// volume display, none while the screen is dark (show nothing, wake nothing).
fn volume_key_event(key: &str, pressed: bool, output: Option<&str>) -> serde_json::Value {
    serde_json::json!({
        "event": "volume_key",
        "key": key,
        "state": if pressed { "pressed" } else { "released" },
        "output": output,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn volume_keys_by_evdev_code() {
        assert_eq!(volume_key(115), Some("up"));
        assert_eq!(volume_key(114), Some("down"));
        assert_eq!(volume_key(113), Some("mute"));
        assert_eq!(volume_key(KEY_POWER), None);
        // KEY_ENTER, KEY_PLAYPAUSE: not ours.
        assert_eq!(volume_key(28), None);
        assert_eq!(volume_key(164), None);
    }

    #[test]
    fn volume_key_events_name_the_panel_or_none() {
        let lit = volume_key_event("up", true, Some("DSI-2"));
        assert_eq!(lit["event"], "volume_key");
        assert_eq!(lit["key"], "up");
        assert_eq!(lit["state"], "pressed");
        assert_eq!(lit["output"], "DSI-2");
        let dark = volume_key_event("down", false, None);
        assert_eq!(dark["state"], "released");
        assert!(dark["output"].is_null());
    }

    #[test]
    fn the_late_closer_closes_off_the_caller() {
        use std::io::Read as _;
        let (mine, theirs) = std::os::unix::net::UnixStream::pair().unwrap();
        mine.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let closer = LateCloser::new();
        closer.close(OwnedFd::from(theirs));
        // The other end closed: end of stream, not a timeout.
        let mut buf = [0u8; 1];
        assert_eq!((&mine).read(&mut buf).unwrap(), 0);
    }

    #[test]
    fn a_slow_input_turn_says_where_the_time_went() {
        let quick = InputTurn { events: 3, total: Duration::from_millis(2), ..Default::default() };
        assert_eq!(quick.slow(), None);
        let at_limit = InputTurn { total: SLOW_INPUT_TURN, ..Default::default() };
        assert_eq!(at_limit.slow(), None);
        let hotplug = InputTurn {
            events: 2,
            added: 1,
            removed: 1,
            handlers: Duration::from_micros(500),
            total: Duration::from_micros(30_500),
        };
        assert_eq!(
            hotplug.slow().as_deref(),
            Some("input turn held the loop 30.5 ms (libinput 30.0, handlers 0.5): 2 events, devices +1 -1")
        );
    }
}
