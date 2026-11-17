//! Input: the pen (the Wayland tablet through pentools.c, or SDL's pen
//! events elsewhere), and the fingers and the mouse for the buttons.
//!
//! On Wayland the pen comes from csrc/pentools.c, which owns the tablet
//! seat (SDL never says which end of the pen is near; see there) and tells
//! each frame's tool: the tip or the eraser end. SDL is told not to turn a
//! pen into mouse or touch events, so a pen comes only as itself; a finger
//! comes as SDL's touch-mouse (buttons only). With no pen, a real mouse
//! plays as one (desktop testing): the left button is the tip (pressure
//! 0.6, the wheel changes it), the right one the eraser, the keys A / D
//! tilt it, and a mouse over the window is a hovering pen.

use nibfair::geom::{v2, V2};
use nibfair::pen::{event_time, tilt_after, End};
use sdl3_sys::events::*;
use sdl3_sys::pen::*;

/// One tool frame from pentools.c (struct pentools_event).
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
struct PenEvent {
    flags: u32,
    time_ms: u32,
    tool_type: u32,
    buttons: u32,
    button_mask: u32,
    x: f32,
    y: f32,
    pressure: f32,
    tilt_x: f32,
    tilt_y: f32,
    distance: f32,
}

const PT_IN: u32 = 1 << 0;
const PT_OUT: u32 = 1 << 1;
const PT_MOTION: u32 = 1 << 2;
const PT_DOWN: u32 = 1 << 3;
const PT_UP: u32 = 1 << 4;
const PT_PRESSURE: u32 = 1 << 5;
const PT_TILT: u32 = 1 << 6;
const PT_DISTANCE: u32 = 1 << 7;
const PT_BUTTON: u32 = 1 << 8;

extern "C" {
    fn pentools_connect() -> *mut core::ffi::c_void;
    fn pentools_disconnect();
    fn pentools_has_tablet() -> core::ffi::c_int;
    fn pentools_dispatch();
    fn pentools_next(ev: *mut PenEvent) -> core::ffi::c_int;
    fn pentools_monotonic_ms() -> f64;
}

/// The pen from the Wayland tablet protocol (pentools.c), when SDL runs on
/// the connection it opened.
pub struct Tablet {
    display: *mut core::ffi::c_void,
    on: bool,
}

impl Tablet {
    /// Before SDL_Init: on Wayland (unless SDL_VIDEO_DRIVER asks for something
    /// else), open the connection and take the tablet seat; SDL is then given
    /// the connection.
    pub fn connect(wanted: bool) -> Tablet {
        let wayland = std::env::var_os("WAYLAND_DISPLAY").is_some()
            && std::env::var("SDL_VIDEO_DRIVER")
                .map(|v| v.trim_start().starts_with("wayland"))
                .unwrap_or(true);
        let display = if wanted && wayland {
            unsafe { pentools_connect() }
        } else {
            std::ptr::null_mut()
        };
        Tablet { display, on: false }
    }

    /// The connection for SDL (null: none).
    pub fn display(&self) -> *mut core::ffi::c_void {
        self.display
    }

    /// After the window: SDL's wl_display (null: not Wayland). The pen comes
    /// from here only if SDL took this connection; otherwise it is closed.
    pub fn adopt(&mut self, sdl_display: *mut core::ffi::c_void) {
        if self.display.is_null() {
            return;
        }
        if sdl_display == self.display {
            self.on = unsafe { pentools_has_tablet() } != 0;
        } else {
            self.close();
        }
    }

    /// The pen comes from the tablet protocol (both ends told apart).
    pub fn active(&self) -> bool {
        self.on
    }

    /// After SDL_Quit (SDL leaves a connection it was given open).
    pub fn close(&mut self) {
        if !self.display.is_null() {
            unsafe { pentools_disconnect() };
            self.display = std::ptr::null_mut();
        }
        self.on = false;
    }
}

/// One piece of the pen's travel this frame, with what it was doing.
#[derive(Clone, Copy, Debug)]
pub struct Move {
    pub from: V2,
    pub to: V2,
    /// Seconds between the two events.
    pub dt: f32,
    pub down: bool,
    pub eraser: bool,
    pub pressure: f32,
}

#[derive(Clone, Debug, Default)]
pub struct Pen {
    /// A real pen has been seen (else a mouse plays its part).
    pub seen: bool,
    pub near: bool,
    pub down: bool,
    pub eraser: bool,
    pub pos: V2,
    pub pressure: f32,
    /// Degrees; only while the tip touches (the digitizer's).
    pub tilt: V2,
    /// The hover distance (0..1), on hardware that reports one.
    pub distance: Option<f32>,
    pub button: bool,
    // This frame:
    pub pressed: bool,
    pub released: bool,
    pub button_pressed: bool,
    pub moves: Vec<Move>,
    last_t: f64,
    /// When the pen last moved (s, SDL's clock): a mouse fallback waits for it to be quiet.
    pub last_event: f64,
    mouse_pressure: f32,
}

#[derive(Default)]
pub struct Input {
    pub pen: Pen,
    /// Completed taps this frame (finger, mouse or the pen's tip): (press, release) positions.
    pub taps: Vec<(V2, V2)>,
    /// Where a finger / mouse / the pen's tip pressed this frame.
    pub presses: Vec<V2>,
    /// A finger or mouse button held, where.
    pub pointer: Option<V2>,
    pub back: bool,
    pub quit: bool,
    pub resized: bool,
    /// The window is hidden, minimized or occluded (as SDL says).
    pub hidden: bool,
    /// Something came from the player this frame (pen, finger, key).
    pub active: bool,
    /// A touch arrived while a pen is in this app's window... the pen as a finger (pen_input).
    pub finger_seen: bool,
    press_at: Option<V2>,
    pen_press_at: Option<V2>,
    mouse_tilt: V2,
    t_now: f64,
    /// Canvas units per window point (SDL's event positions are in points).
    pub coords: f32,
}

fn ts(e: &SDL_Event) -> f64 {
    unsafe { e.common.timestamp as f64 * 1e-9 }
}

impl Input {
    fn at(&self, x: f32, y: f32) -> V2 {
        let k = if self.coords > 0.0 { self.coords } else { 1.0 };
        v2(x * k, y * k)
    }

    /// Before the frame's events.
    pub fn begin(&mut self, now: f64) {
        self.t_now = now;
        let p = &mut self.pen;
        p.pressed = false;
        p.released = false;
        p.button_pressed = false;
        p.moves.clear();
        self.taps.clear();
        self.presses.clear();
        self.back = false;
        self.resized = false;
        self.active = false;
    }

    fn pen_move(&mut self, to: V2, t: f64) {
        let p = &mut self.pen;
        let dt = if p.last_t > 0.0 {
            (t - p.last_t).clamp(0.0005, 0.1) as f32
        } else {
            1.0 / 120.0
        };
        if p.near || p.down {
            p.moves.push(Move {
                from: p.pos,
                to,
                dt,
                down: p.down,
                eraser: p.eraser,
                pressure: p.pressure,
            });
        }
        p.pos = to;
        p.last_t = t;
        p.last_event = t;
    }

    /// The pen's frames from the Wayland tablet (pentools.c), after SDL's
    /// event pump has read them; `sdl_now`: SDL_GetTicksNS() now (the clock
    /// of SDL's event timestamps).
    pub fn tablet_events(&mut self, tab: &Tablet, sdl_now: u64) {
        if !tab.active() {
            return;
        }
        unsafe { pentools_dispatch() };
        let now = sdl_now as f64 * 1e-9;
        let mono = unsafe { pentools_monotonic_ms() };
        let mut ev = PenEvent::default();
        while unsafe { pentools_next(&mut ev) } != 0 {
            self.active = true;
            self.pen_frame(&ev, event_time(now, mono, ev.time_ms));
        }
    }

    /// One tool frame: what changed in it, in the order the pen did it.
    fn pen_frame(&mut self, ev: &PenEvent, t: f64) {
        let f = ev.flags;
        let p = self.at(ev.x, ev.y);
        let eraser = End::of_tool(ev.tool_type) == End::Eraser;
        self.pen.seen = true;
        if f & PT_IN != 0 {
            self.pen.near = true;
            self.pen.eraser = eraser;
            self.pen.last_t = 0.0;
            self.pen.last_event = t;
            if f & PT_MOTION != 0 {
                self.pen.pos = p;
            }
        }
        if f & PT_OUT == 0 {
            // Every frame is the tool's: the end can only change with a new proximity.
            self.pen.near = true;
            self.pen.eraser = eraser;
        }
        if f & PT_TILT != 0 {
            let (x, y) = tilt_after(
                (self.pen.tilt.x, self.pen.tilt.y),
                (ev.tilt_x, ev.tilt_y),
                self.pen.down,
            );
            self.pen.tilt = v2(x, y);
        }
        if f & PT_DISTANCE != 0 && (ev.distance > 0.001 || self.pen.distance.is_some()) {
            self.pen.distance = Some(ev.distance.clamp(0.0, 1.0));
        }
        if f & PT_UP != 0 {
            // The lift: the last stretch still touching, then up (its pressure, 0, is the lift's).
            if f & PT_MOTION != 0 {
                self.pen_move(p, t);
            }
            self.pen_lift(self.pen.pos);
        } else {
            if f & PT_PRESSURE != 0 {
                self.pen.pressure = ev.pressure.clamp(0.0, 1.0);
                if self.pen.down || f & PT_DOWN != 0 {
                    self.pen.pressure = self.pen.pressure.max(0.02);
                }
            }
            if f & PT_MOTION != 0 && f & PT_IN == 0 {
                self.pen_move(p, t);
            }
            if f & PT_DOWN != 0 {
                self.pen_touch(self.pen.pos);
            }
        }
        if f & PT_BUTTON != 0 && ev.button_mask & 1 != 0 {
            self.pen.button = ev.buttons & 1 != 0;
            if self.pen.button {
                self.pen.button_pressed = true;
            }
        }
        if f & PT_OUT != 0 {
            self.pen_away();
        }
    }

    /// The tip (or the eraser end) touches at p.
    fn pen_touch(&mut self, p: V2) {
        self.pen.down = true;
        self.pen.pressed = true;
        self.pen.pressure = self.pen.pressure.max(0.05);
        self.presses.push(p);
        self.pen_press_at = Some(p);
    }

    /// It lifts at p.
    fn pen_lift(&mut self, p: V2) {
        if !self.pen.down {
            return;
        }
        self.pen.down = false;
        self.pen.released = true;
        self.pen.pressure = 0.0;
        self.pen.tilt = v2(0.0, 0.0);
        if let Some(a) = self.pen_press_at.take() {
            self.taps.push((a, p));
        }
    }

    /// Out of range.
    fn pen_away(&mut self) {
        self.pen.near = false;
        if self.pen.down {
            self.pen.down = false;
            self.pen.released = true;
            self.pen_press_at = None;
        }
        self.pen.pressure = 0.0;
        self.pen.tilt = v2(0.0, 0.0);
    }

    pub fn event(&mut self, e: &SDL_Event) {
        let ty = SDL_EventType(unsafe { e.r#type });
        let t = ts(e);
        // Keys, the mouse, fingers (0x300-0x8ff) and the pen (0x1300-0x13ff): the player.
        if (0x300..0x900).contains(&ty.0) || (0x1300..0x1400).contains(&ty.0) {
            self.active = true;
        }
        match ty {
            SDL_EVENT_QUIT | SDL_EVENT_WINDOW_CLOSE_REQUESTED => self.quit = true,
            SDL_EVENT_WINDOW_RESIZED
            | SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED
            | SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED => self.resized = true,
            SDL_EVENT_WINDOW_OCCLUDED | SDL_EVENT_WINDOW_HIDDEN | SDL_EVENT_WINDOW_MINIMIZED => {
                self.hidden = true
            }
            SDL_EVENT_WINDOW_EXPOSED | SDL_EVENT_WINDOW_SHOWN | SDL_EVENT_WINDOW_RESTORED => {
                self.hidden = false
            }
            SDL_EVENT_KEY_DOWN => {
                let k = unsafe { e.key };
                match k.key {
                    sdl3_sys::keycode::SDLK_ESCAPE | sdl3_sys::keycode::SDLK_AC_BACK => {
                        self.back = true
                    }
                    sdl3_sys::keycode::SDLK_A => {
                        self.mouse_tilt.x = (self.mouse_tilt.x - 10.0).max(-50.0)
                    }
                    sdl3_sys::keycode::SDLK_D => {
                        self.mouse_tilt.x = (self.mouse_tilt.x + 10.0).min(50.0)
                    }
                    _ => {}
                }
            }
            // SDL's own pen events: not on Wayland, where pentools.c has the pen (see Tablet).
            // SDL tells the eraser end only where its backend knows it (X11's XInput 2).
            SDL_EVENT_PEN_PROXIMITY_IN => {
                self.pen.seen = true;
                self.pen.near = true;
                self.pen.eraser = false;
                self.pen.last_t = 0.0;
                self.pen.last_event = t;
            }
            SDL_EVENT_PEN_PROXIMITY_OUT => self.pen_away(),
            SDL_EVENT_PEN_MOTION => {
                let ev = unsafe { e.pmotion };
                self.pen.seen = true;
                self.pen.near = true;
                self.pen.eraser = ev.pen_state & SDL_PEN_INPUT_ERASER_TIP != 0;
                self.pen_move(self.at(ev.x, ev.y), t);
            }
            SDL_EVENT_PEN_DOWN | SDL_EVENT_PEN_UP => {
                let ev = unsafe { e.ptouch };
                self.pen.seen = true;
                self.pen.near = true;
                self.pen.eraser = ev.eraser;
                let p = self.at(ev.x, ev.y);
                self.pen_move(p, t);
                if ev.down {
                    self.pen_touch(p);
                } else {
                    self.pen_lift(p);
                }
            }
            SDL_EVENT_PEN_BUTTON_DOWN | SDL_EVENT_PEN_BUTTON_UP => {
                let ev = unsafe { e.pbutton };
                if ev.button == 1 {
                    self.pen.button = ev.down;
                    if ev.down {
                        self.pen.button_pressed = true;
                    }
                }
            }
            SDL_EVENT_PEN_AXIS => {
                let ev = unsafe { e.paxis };
                match ev.axis {
                    SDL_PEN_AXIS_PRESSURE => {
                        self.pen.pressure = ev.value.clamp(0.0, 1.0);
                        if self.pen.down {
                            self.pen.pressure = self.pen.pressure.max(0.02);
                        }
                    }
                    SDL_PEN_AXIS_XTILT => self.pen.tilt.x = ev.value,
                    SDL_PEN_AXIS_YTILT => self.pen.tilt.y = ev.value,
                    SDL_PEN_AXIS_DISTANCE if ev.value > 0.001 || self.pen.distance.is_some() => {
                        self.pen.distance = Some(ev.value.clamp(0.0, 1.0));
                    }
                    _ => {}
                }
            }
            SDL_EVENT_MOUSE_BUTTON_DOWN | SDL_EVENT_MOUSE_BUTTON_UP => {
                let ev = unsafe { e.button };
                let p = self.at(ev.x, ev.y);
                let touch = ev.which == sdl3_sys::touch::SDL_TOUCH_MOUSEID;
                if touch {
                    self.finger_seen = true;
                }
                if ev.down {
                    self.presses.push(p);
                    self.press_at = Some(p);
                    self.pointer = Some(p);
                } else {
                    if let Some(a) = self.press_at.take() {
                        self.taps.push((a, p));
                    }
                    self.pointer = None;
                }
                if !touch && self.mouse_plays() {
                    // The mouse as a pen (no pen here).
                    let eraser = ev.button as i32 == sdl3_sys::mouse::SDL_BUTTON_RIGHT;
                    self.pen.eraser = eraser;
                    self.pen_move(p, t);
                    self.pen.near = true;
                    if ev.down {
                        if self.pen.mouse_pressure <= 0.0 {
                            self.pen.mouse_pressure = 0.6;
                        }
                        self.pen.down = true;
                        self.pen.pressed = true;
                        self.pen.pressure = self.pen.mouse_pressure;
                        self.pen.tilt = self.mouse_tilt;
                    } else {
                        self.pen.down = false;
                        self.pen.released = true;
                        self.pen.pressure = 0.0;
                        self.pen.tilt = v2(0.0, 0.0);
                    }
                }
            }
            SDL_EVENT_MOUSE_MOTION => {
                let ev = unsafe { e.motion };
                let p = self.at(ev.x, ev.y);
                if self.pointer.is_some() {
                    self.pointer = Some(p);
                }
                if ev.which != sdl3_sys::touch::SDL_TOUCH_MOUSEID && self.mouse_plays() {
                    self.pen.near = true;
                    if self.pen.down {
                        self.pen.tilt = self.mouse_tilt;
                    }
                    self.pen_move(p, t);
                }
            }
            SDL_EVENT_MOUSE_WHEEL => {
                if self.mouse_plays() {
                    let ev = unsafe { e.wheel };
                    let base = if self.pen.mouse_pressure <= 0.0 {
                        0.6
                    } else {
                        self.pen.mouse_pressure
                    };
                    self.pen.mouse_pressure = (base + ev.y * 0.05).clamp(0.05, 1.0);
                    if self.pen.down {
                        self.pen.pressure = self.pen.mouse_pressure;
                    }
                }
            }
            SDL_EVENT_WINDOW_MOUSE_LEAVE if self.mouse_plays() && !self.pen.down => {
                self.pen.near = false;
            }
            _ => {}
        }
    }

    fn mouse_plays(&self) -> bool {
        !self.pen.seen
    }

    /// Was this tap inside the rectangle (both its press and its release)?
    pub fn tapped(&self, r: (f32, f32, f32, f32)) -> bool {
        let inside = |p: V2| p.x >= r.0 && p.x <= r.0 + r.2 && p.y >= r.1 && p.y <= r.1 + r.3;
        self.taps.iter().any(|(a, b)| inside(*a) && inside(*b))
    }

    pub fn held_in(&self, r: (f32, f32, f32, f32)) -> bool {
        let inside = |p: V2| p.x >= r.0 && p.x <= r.0 + r.2 && p.y >= r.1 && p.y <= r.1 + r.3;
        self.pointer.is_some_and(inside) || (self.pen.down && inside(self.pen.pos))
    }
}
