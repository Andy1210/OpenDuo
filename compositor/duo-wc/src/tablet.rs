//! The pen: a Wayland tablet tool for the clients that take one, a touch
//! point for the others.
//!
//! duo2-touchd turns the heat processor's pen reports (the Surface Pen on the
//! Duo 2's digitizer) into one tablet device per panel, "Surface Duo 2 Pen
//! Left" / "... Right", on the same panel coordinates as that panel's
//! touchscreen, and a udev rule names their panel in `WL_OUTPUT`. It also does
//! the palm rejection (touches held back while the pen is in range). A
//! libinput quirk (`90-duo2-pen.quirks`) makes libinput's tip follow the heat
//! processor's tip bit (by default libinput wants 5 % of the pressure range
//! before a pen touches) and switches its smoothing off.
//!
//! **Tablet clients.** A client that bound `zwp_tablet_manager_v2` (GTK 3 and
//! 4, Qt, ...) gets the tool (tablet-v2): proximity in / out with hover
//! motion, the tip with pressure, the tilt (turned with the panel's
//! rotation, [`crate::rotation::tilt_to_local`]), the barrel button
//! (BTN_STYLUS: GTK makes it the secondary button, a right click), and the
//! eraser end as a tool of its own (libinput makes BTN_TOOL_RUBBER an
//! eraser). While the tip is down the tool stays with the surface it went
//! down on (an implicit grab: a stroke that leaves a canvas still ends on
//! it).
//!
//! **Which clients are tablet clients** (`pen_input`): with `apps` (the
//! default) only the drawing apps of `pen_apps` (Rnote, Xournal++, Krita,
//! GIMP, Inkscape, MyPaint, the Pen test, Nibfair; X11 ones by their
//! WM_CLASS, [`drawing_app`]) get the tool; every other app gets the pen
//! as a finger, so it scrolls, taps and long-presses everywhere (GTK and
//! Qt drag-scroll only with a touchscreen: a GTK list dragged with a
//! tablet tool selects or does nothing). With `all` every client that
//! bound the tablet manager gets the tool (GTK, Qt, Firefox, Xwayland).
//!
//! **The compositor's own UI takes the pen as a finger, whatever is under
//! it** ([`compositor_takes`]): the overview (its cards drag, close and
//! split with the pen too, and no app under it gets the pen's hover), a
//! split's divider, and while a window is carried or has just been let go
//! at the hinge (the pen picks it up on the other panel, as a finger
//! would). A drawing app's own title bar carries its window with the pen
//! ([`move_request`]: its `xdg_toplevel.move` with the tip's serial ends
//! the stroke for the app, and the pen goes on as the carrying finger).
//! Edge swipes come with the pen wherever it is a finger; over a drawing
//! app the pen draws to the edge.
//!
//! **Everyone else** (foot, squeekboard, Waydroid, ...): the tip is a touch
//! point, through the very path a finger takes ([`crate::input::touch_down`]
//! and the rest: edge swipes, the overview, the keyboard rules), in its own
//! seat slot ([`PEN_TOUCH_SLOT`]). Hovering does nothing for them. The
//! barrel button is a right click where the seat has a pointer (laptop
//! mode, a display's touchpad, a mouse: [`crate::pointer`]): the pointer
//! goes to the pen and its right button follows the barrel's; while it is
//! held the tip does nothing (the tap that usually comes with it would hit
//! the menu the click opened). With no pointer (the phone as a phone) the
//! barrel does nothing there: a long press is the touch way to a menu.
//!
//! Which of the two is decided when the tip goes down (the barrel: when it
//! is pressed), by the client of the surface under it, and kept until it
//! lifts.
//!
//! **The hover dot.** While the pen is in range of a panel that shows its
//! picture, a small dot (a ring for the eraser end) is drawn where it
//! points, in front of everything ([`dot_elements`]); tablet clients'
//! cursors (`zwp_tablet_tool_v2.set_cursor`) are not drawn.
//!
//! **Settings** (`pen_button = right_click | off`, `pen_dot = on | off`,
//! `pen_input = apps | all`, `pen_apps = rnote, krita, ...` in duo-wc.conf;
//! live with `set_pen`, Duo Settings > Pen): `off` drops the barrel button
//! for every client.
//!
//! **Dark panels, the ambient screen:** as for a finger, nothing is delivered
//! and no dot is drawn (the pen does not wake the screen). Locked: only the
//! lock surface ([`crate::state::DuoWc::surface_under`]).

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use serde::Serialize;
use smithay::backend::allocator::Fourcc;
use smithay::backend::input::{
    AbsolutePositionEvent, ButtonState, Event, InputBackend, ProximityState, TabletToolButtonEvent,
    TabletToolDescriptor, TabletToolEvent, TabletToolProximityEvent, TabletToolTipEvent, TabletToolTipState,
    TabletToolType,
};
use smithay::backend::libinput::LibinputInputBackend;
use smithay::backend::renderer::element::memory::{MemoryRenderBuffer, MemoryRenderBufferRenderElement};
use smithay::backend::renderer::element::Kind;
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::desktop::Window;
use smithay::output::Output;
use smithay::reexports::input::{self as libinput, DeviceCapability};
use smithay::reexports::wayland_protocols::wp::tablet::zv2::server::{
    zwp_tablet_manager_v2::ZwpTabletManagerV2, zwp_tablet_seat_v2::ZwpTabletSeatV2,
    zwp_tablet_tool_v2::ZwpTabletToolV2, zwp_tablet_v2::ZwpTabletV2,
};
use smithay::reexports::wayland_server::backend::ClientId;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::{
    delegate_dispatch, Client, DataInit, DisplayHandle, GlobalDispatch, New, Resource,
};
use smithay::utils::{Logical, Physical, Point, Rectangle, Serial, Size, Transform, SERIAL_COUNTER};
use smithay::wayland::tablet_manager::{
    TabletDescriptor, TabletManagerState, TabletSeatHandler, TabletSeatTrait, TabletSeatUserData,
    TabletToolUserData, TabletUserData,
};
use tracing::{debug, info};

use crate::render::Element;
use crate::rotation::Rotation;
use crate::state::DuoWc;

/// The seat slot the pen's tip takes when it goes to a client as a touch
/// point: far above libinput's seat slots (0, 1, ...).
pub const PEN_TOUCH_SLOT: u32 = 0x7000;

/// The hover dot's size, logical px (scaled with the output).
pub const DOT_SIZE: f64 = 9.0;

type ProximityEv = <LibinputInputBackend as InputBackend>::TabletToolProximityEvent;
type AxisEv = <LibinputInputBackend as InputBackend>::TabletToolAxisEvent;
type TipEv = <LibinputInputBackend as InputBackend>::TabletToolTipEvent;
type ButtonEv = <LibinputInputBackend as InputBackend>::TabletToolButtonEvent;

/// What the barrel button does for a client that takes no tablets.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PenButton {
    /// A right click where the seat has a pointer (tablet clients get
    /// BTN_STYLUS, as always).
    #[default]
    RightClick,
    /// Nothing, for anyone (a hand that presses it by holding the pen).
    Off,
}

impl PenButton {
    /// `right_click` or `off`.
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "right_click" => Some(PenButton::RightClick),
            "off" => Some(PenButton::Off),
            _ => None,
        }
    }
}

/// Which clients get the pen as a tablet tool (`pen_input`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PenInput {
    /// Only the drawing apps of `pen_apps`; everyone else gets a finger.
    #[default]
    Apps,
    /// Every client that bound `zwp_tablet_manager_v2`.
    All,
}

impl PenInput {
    /// `apps` or `all`.
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "apps" => Some(PenInput::Apps),
            "all" => Some(PenInput::All),
            _ => None,
        }
    }
}

/// The drawing apps by default (`pen_apps`): an app id or its last
/// dot-separated part, any case ([`drawing_app`]).
pub const DEFAULT_APPS: &[&str] = &[
    "rnote",
    "xournalpp",
    "krita",
    "gimp",
    "inkscape",
    "mypaint",
    "org.duo2os.PenTest",
    "org.duo2os.Nibfair",
];

/// `pen_apps`'s text -> the list: comma or space separated, empty parts
/// dropped, no duplicates (the first kept). Pure.
pub fn parse_apps(value: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for part in value.split(|c: char| c == ',' || c.is_whitespace()) {
        let part = part.trim();
        if !part.is_empty() && !out.iter().any(|a| a.eq_ignore_ascii_case(part)) {
            out.push(part.to_owned());
        }
    }
    out
}

/// Whether `app_id` is one of the drawing apps in `list`: the same id, the
/// same last dot-separated part ("krita" is org.kde.krita and the X11 class
/// krita, "gimp" org.gimp.GIMP and Gimp), or a versioned X11 class
/// ("Gimp-2.10" is gimp), any case. Pure.
pub fn drawing_app(app_id: &str, list: &[String]) -> bool {
    let id = app_id.trim().to_ascii_lowercase();
    if id.is_empty() {
        return false;
    }
    let last = |s: &str| s.rsplit('.').next().unwrap_or(s).to_owned();
    let unversioned = match id.split_once('-') {
        Some((name, version)) if version.starts_with(|c: char| c.is_ascii_digit()) => name.to_owned(),
        _ => String::new(),
    };
    let names = [id.clone(), last(&id), unversioned];
    list.iter().map(|a| a.trim().to_ascii_lowercase()).any(|a| {
        let short = last(&a);
        names.iter().any(|n| !n.is_empty() && (*n == a || *n == short))
    })
}

/// The pen's settings (duo-wc.conf, `set_pen`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Settings {
    /// `pen_button`.
    pub button: PenButton,
    /// `pen_dot`: the hover dot.
    pub dot: bool,
    /// `pen_input`.
    pub input: PenInput,
    /// `pen_apps`: the drawing apps for `pen_input = apps`.
    pub apps: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            button: PenButton::RightClick,
            dot: true,
            input: PenInput::Apps,
            apps: DEFAULT_APPS.iter().map(|a| (*a).to_owned()).collect(),
        }
    }
}

/// Where the tip goes when it touches down (pure, for the tests): a tablet
/// client gets the tool unless the compositor takes the point
/// (`compositor`) or, with `pen_input = apps`, it is not a drawing app.
pub fn tip_is_tablet(input: PenInput, tablet_client: bool, drawing: bool, compositor: bool) -> bool {
    tablet_client
        && !compositor
        && match input {
            PenInput::All => true,
            PenInput::Apps => drawing,
        }
}

/// What the pen is doing now.
#[derive(Default)]
struct Pen {
    settings: Settings,
    /// The clients that bound `zwp_tablet_manager_v2`.
    clients: HashSet<ClientId>,
    /// The panel the pen is in range of (and that panel was lit when it came).
    output: Option<String>,
    /// Where it points, in the global space (for the dot and the barrel).
    at: Option<Point<f64, Logical>>,
    /// The eraser end is the one near the screen.
    rubber: bool,
    /// The tip is down (whoever has it).
    tip: bool,
    /// The tip is down, as a touch point ([`PEN_TOUCH_SLOT`]).
    as_touch: bool,
    /// The tip is down for a tablet client, on this surface (with its origin
    /// in the global space), which keeps the tool until the tip lifts.
    grab: Option<(WlSurface, Point<f64, Logical>)>,
    /// The serial of that tip's `down` (a title bar's move request names it).
    tip_serial: Option<Serial>,
    /// The tool that is in range (its descriptor) and the time of its last event.
    tool: Option<TabletToolDescriptor>,
    time: u32,
    /// The barrel button holds the pointer's right button.
    right_click: bool,
    /// The tool's buttons given to a tablet client, pressed now (their
    /// release goes there too, whatever the settings say meanwhile).
    forwarded: HashSet<u32>,
    /// The panel the pen was last in range of, and when it left
    /// ([`whereabouts`]: the top button's panel, crate::penbutton).
    left: Option<(String, Instant)>,
}

thread_local! {
    // The compositor runs on one thread: the state lives beside DuoWc.
    static PEN: RefCell<Pen> = RefCell::new(Pen::default());
    // The dot's pictures, by size in physical px and kind (true: the eraser's ring).
    static DOTS: RefCell<HashMap<(i32, bool), MemoryRenderBuffer>> = RefCell::new(HashMap::new());
}

/// Create the `zwp_tablet_manager_v2` global, with the config's settings.
pub fn init(display: &DisplayHandle, settings: Settings) {
    info!(input = ?settings.input, apps = settings.apps.join(","), "pen settings");
    PEN.with(|p| p.borrow_mut().settings = settings);
    let _ = TabletManagerState::new::<DuoWc>(display);
}

/// The settings now.
pub fn settings() -> Settings {
    PEN.with(|p| p.borrow().settings.clone())
}

/// Change the settings live (`set_pen`); a dot that goes is drawn away.
pub fn set_settings(state: &mut DuoWc, settings: Settings) {
    info!(
        button = ?settings.button,
        dot = settings.dot,
        input = ?settings.input,
        apps = settings.apps.join(","),
        "pen settings"
    );
    let output = PEN.with(|p| {
        let mut p = p.borrow_mut();
        let dot_changed = p.settings.dot != settings.dot;
        p.settings = settings;
        dot_changed.then(|| p.output.clone()).flatten()
    });
    if let Some(output) = output.and_then(|o| state.output_by_name(&o)) {
        state.queue_redraw_output(&output);
    }
}

/// Where the pen is: the panel it is in range of, and the one it last left
/// with how long ago (for the top button, crate::penbutton).
pub fn whereabouts() -> (Option<String>, Option<(String, Duration)>) {
    PEN.with(|p| {
        let p = p.borrow();
        (p.output.clone(), p.left.as_ref().map(|(o, at)| (o.clone(), at.elapsed())))
    })
}

/// The pen for `get_state`.
pub fn summary() -> serde_json::Value {
    PEN.with(|p| {
        let p = p.borrow();
        serde_json::json!({
            "in_range": p.output.is_some(),
            "output": p.output,
            "tool": p.output.as_ref().map(|_| if p.rubber { "eraser" } else { "pen" }),
            "tip": p.tip,
            "as_touch": p.as_touch,
            "right_click": p.right_click,
            "tablet_clients": p.clients.len(),
            "button": p.settings.button,
            "dot": p.settings.dot,
            "input": p.settings.input,
            "apps": p.settings.apps,
            "top_button": crate::penbutton::devices() > 0,
        })
    })
}

// The global as smithay's, noting which clients bind it.
impl GlobalDispatch<ZwpTabletManagerV2, ()> for DuoWc {
    fn bind(
        state: &mut Self,
        handle: &DisplayHandle,
        client: &Client,
        resource: New<ZwpTabletManagerV2>,
        data: &(),
        data_init: &mut DataInit<'_, Self>,
    ) {
        PEN.with(|p| p.borrow_mut().clients.insert(client.id()));
        <TabletManagerState as GlobalDispatch<ZwpTabletManagerV2, (), DuoWc>>::bind(
            state, handle, client, resource, data, data_init,
        );
    }
}
delegate_dispatch!(DuoWc: [ZwpTabletManagerV2: ()] => TabletManagerState);
delegate_dispatch!(DuoWc: [ZwpTabletSeatV2: TabletSeatUserData] => TabletManagerState);
delegate_dispatch!(DuoWc: [ZwpTabletToolV2: TabletToolUserData] => TabletManagerState);
delegate_dispatch!(DuoWc: [ZwpTabletV2: TabletUserData] => TabletManagerState);

// The clients' tool cursors are not drawn: the compositor's dot is the
// pen's cursor (dot_elements).
impl TabletSeatHandler for DuoWc {}

/// Whether a client takes tablet tools.
fn is_tablet_client(surface: &WlSurface) -> bool {
    let Some(client) = surface.client() else {
        return false;
    };
    PEN.with(|p| p.borrow().clients.contains(&client.id()))
}

/// A tablet device of a panel (with an output name; the kernel's own pen
/// device has none and is switched off by [`crate::input`]).
fn is_pen(device: &libinput::Device) -> bool {
    device.has_capability(DeviceCapability::TabletTool) && device.output_name().is_some()
}

pub fn device_added(state: &mut DuoWc, device: &libinput::Device) {
    if !is_pen(device) {
        return;
    }
    let handle = state.display_handle.clone();
    state.seat.tablet_seat().add_tablet::<DuoWc>(&handle, &TabletDescriptor::from(device));
    info!(device = %device.name(), output = ?device.output_name(), "pen (tablet) device");
}

pub fn device_removed(state: &mut DuoWc, device: &libinput::Device) {
    if !is_pen(device) {
        return;
    }
    end(state, 0, "pen device removed");
    let tablet_seat = state.seat.tablet_seat();
    tablet_seat.remove_tablet(&TabletDescriptor::from(device));
    if tablet_seat.count_tablets() == 0 {
        tablet_seat.clear_tools();
    }
}

/// The device position as fractions (0..1) of its range.
fn fraction<E: AbsolutePositionEvent<LibinputInputBackend>>(event: &E) -> (f64, f64) {
    (
        AbsolutePositionEvent::x_transformed(event, 1_000_000) / 1e6,
        AbsolutePositionEvent::y_transformed(event, 1_000_000) / 1e6,
    )
}

/// The pen's position in the global space, on `output`.
fn location(state: &DuoWc, output: &str, (fx, fy): (f64, f64)) -> Option<Point<f64, Logical>> {
    crate::input::touch_location(state, output, |w| fx * w as f64, |h| fy * h as f64)
}

/// The tilt as the client on `output` sees it (the panel's rotation).
fn tilt_on(state: &DuoWc, output: &str, tilt: (f64, f64)) -> (f64, f64) {
    let rotation = state
        .output_by_name(output)
        .map(|o| Rotation::from_transform(o.current_transform()))
        .unwrap_or_default();
    crate::rotation::tilt_to_local(tilt, rotation)
}

/// The app id of the window a surface belongs to (None: a layer surface,
/// the lock screen, a window with no app id). A popup or an X11 menu has no
/// window of its own: the window under it.
fn app_of(state: &DuoWc, surface: &WlSurface, at: Point<f64, Logical>) -> Option<String> {
    let mut root = surface.clone();
    while let Some(parent) = smithay::wayland::compositor::get_parent(&root) {
        root = parent;
    }
    if state.layer_of_surface(&root).is_some() {
        return None;
    }
    let window: Window =
        state.window_for_surface(&root).or_else(|| state.window_under(at).map(|(w, _)| w))?;
    crate::state::app_id_and_title(&window).0
}

/// The surface under the pen on `output`, if it gets the pen as a tablet
/// tool: its client takes tablet tools and, with `pen_input = apps`, it is
/// a drawing app's. None on a panel whose touches are the compositor's
/// (laptop mode's keyboard panel, an external display's touchpad panel)
/// and under the overview: the pen is a finger there, and never reaches
/// what is mapped under it (the home screen's surfaces stay mapped on the
/// keyboard panel, not shown; the windows stay where they are under the
/// overview's cards).
fn tablet_focus(
    state: &DuoWc,
    output: &str,
    at: Point<f64, Logical>,
) -> Option<(WlSurface, Point<f64, Logical>)> {
    if state.laptop_takes(output) || state.trackpad_takes(output) {
        return None;
    }
    if state.output_by_name(output).is_some_and(|o| state.overview_on(&o)) {
        return None;
    }
    let (surface, origin) = state.surface_under(at)?;
    let tablet = is_tablet_client(&surface);
    let (input, drawing) = PEN.with(|p| {
        let p = p.borrow();
        let drawing = tablet
            && p.settings.input == PenInput::Apps
            && app_of(state, &surface, at).is_some_and(|app| drawing_app(&app, &p.settings.apps));
        (p.settings.input, drawing)
    });
    tip_is_tablet(input, tablet, drawing, false).then_some((surface, origin))
}

/// Whether the compositor takes a pen tip at `at` on `output` as a finger,
/// whatever client is under it: a carried window (or one just let go at the
/// hinge, which a touch on the other panel picks up again), a split's
/// divider. (The overview: [`tablet_focus`].)
pub fn compositor_takes(state: &DuoWc, output: &str, at: Point<f64, Logical>) -> bool {
    if state.carry.drag.is_some() || state.carry.landing.is_some() || state.desk.drag.is_some() {
        return true;
    }
    state.output_by_name(output).is_some_and(|o| state.divider_takes(&o, at))
}

/// Whether the pen may reach the clients on `output`: as a touch, only on a
/// panel that shows its picture (not one still waking), with no ambient
/// screen up.
fn reachable(state: &DuoWc, output: &str) -> bool {
    state.panel_shows(output) && state.ambient.is_none()
}

/// The pen points somewhere new on `output` (None: nowhere): the dot moves.
fn moved_to(state: &mut DuoWc, output: &str, at: Option<Point<f64, Logical>>) {
    let dot = PEN.with(|p| {
        let mut p = p.borrow_mut();
        p.at = at;
        p.settings.dot
    });
    if dot {
        if let Some(o) = state.output_by_name(output) {
            state.queue_redraw_output(&o);
        }
    }
}

pub fn proximity(state: &mut DuoWc, event: ProximityEv) {
    let time = Event::time_msec(&event);
    let tool_desc = TabletToolEvent::tool(&event);
    match TabletToolProximityEvent::state(&event) {
        ProximityState::In => {
            let device = Event::device(&event);
            let Some(output) = device.output_name().map(|o| o.into_owned()) else {
                return;
            };
            if !reachable(state, &output) {
                debug!(output, "pen in range of a dark panel: nothing delivered");
                return;
            }
            let Some(at) = location(state, &output, fraction(&event)) else {
                return;
            };
            state.note_activity();
            let handle = state.display_handle.clone();
            let tablet_seat = state.seat.tablet_seat();
            let tool = tablet_seat.add_tool::<DuoWc>(state, &handle, &tool_desc);
            debug!(output, tool = ?tool_desc.tool_type, x = at.x, y = at.y, "pen in range");
            let focus = tablet_focus(state, &output, at);
            PEN.with(|p| {
                let mut p = p.borrow_mut();
                p.output = Some(output.clone());
                p.rubber = tool_desc.tool_type == TabletToolType::Eraser;
                p.tool = Some(tool_desc.clone());
                p.time = time;
            });
            moved_to(state, &output, Some(at));
            if let (Some(tablet), Some(focus)) =
                (tablet_seat.get_tablet(&TabletDescriptor::from(&device)), focus)
            {
                let at = state.client_location(&focus.0, at);
                tool.proximity_in(at, focus, &tablet, SERIAL_COUNTER.next_serial(), time);
            }
        }
        ProximityState::Out => {
            end(state, time, "pen out of range");
            if let Some(tool) = state.seat.tablet_seat().get_tool(&tool_desc) {
                tool.proximity_out(time);
            }
        }
    }
}

/// The pen is gone (out of range, its panel went dark, its device): a tip
/// still down lifts, a right click lets go, the dot goes.
fn end(state: &mut DuoWc, time: u32, why: &str) {
    let (as_touch, right_click, was) = PEN.with(|p| {
        let mut p = p.borrow_mut();
        let as_touch = std::mem::take(&mut p.as_touch);
        let right_click = std::mem::take(&mut p.right_click);
        p.grab = None;
        p.tip_serial = None;
        p.tip = false;
        p.forwarded.clear();
        let was = p.output.take();
        if let Some(output) = &was {
            p.left = Some((output.clone(), Instant::now()));
        }
        (as_touch, right_click, was)
    });
    if as_touch {
        crate::input::touch_up(state, PEN_TOUCH_SLOT, time);
        crate::input::touch_frame(state);
    }
    if right_click {
        state.pointer_button(crate::pointer::BTN_RIGHT, false, time);
    }
    if let Some(output) = &was {
        moved_to(state, output, None);
        debug!(why, "pen done");
    }
}

pub fn axis(state: &mut DuoWc, event: AxisEv) {
    let time = Event::time_msec(&event);
    let Some(output) = PEN.with(|p| p.borrow().output.clone()) else {
        return;
    };
    if !reachable(state, &output) {
        // The panel went dark under the pen.
        end(state, time, "panel dark");
        if let Some(tool) = state.seat.tablet_seat().get_tool(&TabletToolEvent::tool(&event)) {
            tool.proximity_out(time);
        }
        return;
    }
    let frac = fraction(&event);
    let Some(at) = location(state, &output, frac) else {
        return;
    };
    PEN.with(|p| p.borrow_mut().time = time);
    state.note_activity();
    moved_to(state, &output, Some(at));
    let (as_touch, grab, right_click) = PEN.with(|p| {
        let p = p.borrow();
        (p.as_touch, p.grab.clone(), p.right_click)
    });
    if as_touch {
        crate::input::touch_motion(state, PEN_TOUCH_SLOT, time, frac.0, frac.1);
        crate::input::touch_frame(state);
        return;
    }
    if right_click {
        // The pointer follows the pen while the barrel holds its button.
        state.pointer_warp((at.x, at.y), time);
        return;
    }
    let tablet_seat = state.seat.tablet_seat();
    let (Some(tablet), Some(tool)) = (
        tablet_seat.get_tablet(&TabletDescriptor::from(&Event::device(&event))),
        tablet_seat.get_tool(&TabletToolEvent::tool(&event)),
    ) else {
        return;
    };
    if TabletToolEvent::pressure_has_changed(&event) {
        tool.pressure(TabletToolEvent::pressure(&event));
    }
    if TabletToolEvent::tilt_has_changed(&event) {
        tool.tilt(tilt_on(state, &output, TabletToolEvent::tilt(&event)));
    }
    // Hovering: whatever tablet client's surface is under it; tip down: the
    // one it went down on.
    let focus = grab.or_else(|| tablet_focus(state, &output, at));
    // A window shown scaled down takes it in its own coordinates.
    let at = match &focus {
        Some((surface, _)) => state.client_location(surface, at),
        None => at,
    };
    tool.motion(at, focus, &tablet, SERIAL_COUNTER.next_serial(), time);
}

pub fn tip(state: &mut DuoWc, event: TipEv) {
    let time = Event::time_msec(&event);
    let Some(output) = PEN.with(|p| p.borrow().output.clone()) else {
        return;
    };
    let tool = state.seat.tablet_seat().get_tool(&TabletToolEvent::tool(&event));
    PEN.with(|p| p.borrow_mut().time = time);
    match TabletToolTipEvent::tip_state(&event) {
        TabletToolTipState::Down => {
            if !reachable(state, &output) {
                return;
            }
            if PEN.with(|p| p.borrow().right_click) {
                // The barrel holds a right click: the tap that comes with it
                // would hit the menu it opened.
                debug!(output, "pen tip down while the barrel right-clicks: ignored");
                return;
            }
            let frac = fraction(&event);
            let Some(at) = location(state, &output, frac) else {
                return;
            };
            PEN.with(|p| p.borrow_mut().tip = true);
            let focus =
                if compositor_takes(state, &output, at) { None } else { tablet_focus(state, &output, at) };
            match focus {
                Some(focus) => {
                    debug!(output, target = state.describe_surface(&focus.0), "pen tip down (tablet)");
                    focus_on_tip(state, &output, &focus.0, at);
                    PEN.with(|p| p.borrow_mut().grab = Some(focus.clone()));
                    if let Some(tool) = tool {
                        // The tip event carries the axes that changed with it
                        // (libinput: the pressure at the touch): a motion
                        // with them first, then the tip.
                        let tablet = state
                            .seat
                            .tablet_seat()
                            .get_tablet(&TabletDescriptor::from(&Event::device(&event)));
                        if let Some(tablet) = tablet {
                            if TabletToolEvent::pressure_has_changed(&event) {
                                tool.pressure(TabletToolEvent::pressure(&event));
                            }
                            if TabletToolEvent::tilt_has_changed(&event) {
                                tool.tilt(tilt_on(state, &output, TabletToolEvent::tilt(&event)));
                            }
                            let at = state.client_location(&focus.0, at);
                            tool.motion(at, Some(focus), &tablet, SERIAL_COUNTER.next_serial(), time);
                        }
                        let serial = SERIAL_COUNTER.next_serial();
                        PEN.with(|p| p.borrow_mut().tip_serial = Some(serial));
                        tool.tip_down(serial, time);
                    }
                }
                _ => {
                    // No tablet client under it: the tip is a finger.
                    debug!(output, "pen tip down (as a touch)");
                    PEN.with(|p| p.borrow_mut().as_touch = true);
                    crate::input::touch_down(state, Some(output), PEN_TOUCH_SLOT, time, frac.0, frac.1);
                    crate::input::touch_frame(state);
                }
            }
        }
        TabletToolTipState::Up => {
            let (as_touch, had_grab) = PEN.with(|p| {
                let mut p = p.borrow_mut();
                p.tip = false;
                p.tip_serial = None;
                (std::mem::take(&mut p.as_touch), p.grab.take().is_some())
            });
            if as_touch {
                crate::input::touch_up(state, PEN_TOUCH_SLOT, time);
                crate::input::touch_frame(state);
            } else if let (true, Some(tool)) = (had_grab, tool) {
                tool.tip_up(time);
            }
        }
    }
}

/// `xdg_toplevel.move` from a tablet client: a drawing app's title bar
/// dragged with the pen (the tip's serial, or the tip down on that
/// window). The stroke ends for the app (tip up, out of proximity) and the
/// pen carries the window on as a finger would ([`crate::windrag`], its
/// own touch slot): the hinge, the snap targets, the drop. -> false when it
/// was not the pen's (a finger's or a mouse's request).
pub fn move_request(state: &mut DuoWc, window: &Window, serial: Serial) -> bool {
    let (grab, tip_serial, output, at) = PEN.with(|p| {
        let p = p.borrow();
        (p.grab.clone(), p.tip_serial, p.output.clone(), p.at)
    });
    let (Some((surface, _)), Some(output), Some(at)) = (grab, output, at) else {
        return false;
    };
    let mut root = surface.clone();
    while let Some(parent) = smithay::wayland::compositor::get_parent(&root) {
        root = parent;
    }
    let on_window = state.window_for_surface(&root).as_ref() == Some(window);
    let a_finger = state.touch_slots.values().any(|s| s.delivered && s.serial == Some(serial));
    if a_finger || (tip_serial != Some(serial) && !on_window) {
        return false;
    }
    let (tool, time) = PEN.with(|p| {
        let p = p.borrow();
        (p.tool.clone(), p.time)
    });
    if let Some(tool) = tool.and_then(|t| state.seat.tablet_seat().get_tool(&t)) {
        tool.tip_up(time);
        tool.proximity_out(time);
    }
    PEN.with(|p| {
        let mut p = p.borrow_mut();
        p.grab = None;
        p.tip_serial = None;
        p.as_touch = true;
    });
    let id = crate::state::window_data(window).id;
    debug!(id, output, "the pen carries a tablet client's window (its move request)");
    state.touch_slots.insert(PEN_TOUCH_SLOT, crate::state::TouchSlot::held(&output, at, time));
    state.drag_begin(PEN_TOUCH_SLOT, window, at, &output);
    true
}

/// The tip went down on a tablet client's surface: the panel becomes the
/// active one and the window comes to the front with the keyboard, as for a
/// finger (`deliver_down`), except over a layer surface that takes no keyboard.
fn focus_on_tip(state: &mut DuoWc, output: &str, surface: &WlSurface, at: Point<f64, Logical>) {
    let mut root = surface.clone();
    while let Some(parent) = smithay::wayland::compositor::get_parent(&root) {
        root = parent;
    }
    if let Some(layer) = state.layer_of_surface(&root) {
        if layer.can_receive_keyboard_focus() {
            state.focus_by_touch(output, None, false);
        }
        return;
    }
    let window = state.window_for_surface(&root).or_else(|| state.window_under(at).map(|(w, _)| w));
    let on_window = window.is_some();
    state.focus_by_touch(output, window.as_ref(), on_window);
}

/// What a barrel press goes to (pure, for the tests): the tablet client
/// under the pen (its own BTN_STYLUS), the pointer's right button, or
/// nobody.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BarrelTo {
    /// The tablet client under the pen (or the one the tip is down on).
    Tablet,
    /// A right click at the pen, by the pointer.
    Pointer,
    /// Dropped.
    Nobody,
}

/// Where a barrel press goes: `button` the setting, `as_touch` the tip is
/// down as a finger, `tablet` a tablet client is under the pen (or has its
/// tip), `pointer` the seat has a pointer, `panel_apps` the panel's
/// touches go to the apps (not laptop mode's keyboard panel, not a
/// touchpad panel).
pub fn barrel_to(
    button: PenButton,
    as_touch: bool,
    tablet: bool,
    pointer: bool,
    panel_apps: bool,
) -> BarrelTo {
    match button {
        PenButton::Off => BarrelTo::Nobody,
        _ if as_touch => BarrelTo::Nobody,
        _ if tablet => BarrelTo::Tablet,
        PenButton::RightClick if pointer && panel_apps => BarrelTo::Pointer,
        PenButton::RightClick => BarrelTo::Nobody,
    }
}

pub fn button(state: &mut DuoWc, event: ButtonEv) {
    let time = Event::time_msec(&event);
    let code = TabletToolButtonEvent::button(&event);
    let pressed = TabletToolButtonEvent::button_state(&event) == ButtonState::Pressed;
    let tool = state.seat.tablet_seat().get_tool(&TabletToolEvent::tool(&event));
    if !pressed {
        let (forwarded, right_click) = PEN.with(|p| {
            let mut p = p.borrow_mut();
            (p.forwarded.remove(&code), std::mem::take(&mut p.right_click))
        });
        if forwarded {
            if let Some(tool) = tool {
                tool.button(code, ButtonState::Released, SERIAL_COUNTER.next_serial(), time);
            }
        }
        if right_click {
            debug!("pen barrel released: right button up");
            state.pointer_button(crate::pointer::BTN_RIGHT, false, time);
        }
        return;
    }
    let (output, at, as_touch, grab, pen_button) = PEN.with(|p| {
        let p = p.borrow();
        (p.output.clone(), p.at, p.as_touch, p.grab.clone(), p.settings.button)
    });
    let (Some(output), Some(at)) = (output, at) else {
        return;
    };
    let tablet = grab.is_some() || tablet_focus(state, &output, at).is_some();
    let pointer = state.seat.get_pointer().is_some();
    let panel_apps = !state.laptop_takes(&output) && !state.trackpad_takes(&output);
    match barrel_to(pen_button, as_touch, tablet, pointer, panel_apps) {
        BarrelTo::Tablet => {
            if let Some(tool) = tool {
                PEN.with(|p| p.borrow_mut().forwarded.insert(code));
                tool.button(code, ButtonState::Pressed, SERIAL_COUNTER.next_serial(), time);
            }
        }
        BarrelTo::Pointer => {
            debug!(output, x = at.x, y = at.y, "pen barrel: a right click");
            PEN.with(|p| p.borrow_mut().right_click = true);
            state.pointer_warp((at.x, at.y), time);
            state.pointer_button(crate::pointer::BTN_RIGHT, true, time);
        }
        BarrelTo::Nobody => debug!(output, as_touch, "pen barrel: nobody takes it"),
    }
}

/// The dot's picture, `px` x `px`, ARGB8888 premultiplied (B, G, R, A): a
/// white disc with a dark rim (seen on light and dark pages alike), or for
/// the eraser end a white ring with dark edges; antialiased by 4 x 4
/// supersampling.
pub fn dot_pixels(px: usize, ring: bool) -> Vec<u8> {
    let r = px as f64 / 2.0;
    // The dark edges and (the ring's) white band, in px.
    let rim = (r * if ring { 0.18 } else { 0.22 }).max(1.2);
    let band = r * 0.28;
    let mut out = vec![0u8; px * px * 4];
    const SS: usize = 4;
    for y in 0..px {
        for x in 0..px {
            let (mut white, mut dark) = (0usize, 0usize);
            for sy in 0..SS {
                for sx in 0..SS {
                    let dx = x as f64 + (sx as f64 + 0.5) / SS as f64 - r;
                    let dy = y as f64 + (sy as f64 + 0.5) / SS as f64 - r;
                    let d = (dx * dx + dy * dy).sqrt();
                    if d > r || (ring && d < r - 2.0 * rim - band) {
                        continue;
                    }
                    if d > r - rim || (ring && d < r - rim - band) {
                        dark += 1;
                    } else {
                        white += 1;
                    }
                }
            }
            let n = (SS * SS) as f64;
            let (w, k) = (white as f64 / n, dark as f64 / n);
            // 90 % opaque; the edges a near black.
            let a = (w + k) * 0.9;
            let v = (w + k * 0.15) * 0.9;
            let i = (y * px + x) * 4;
            let v = (v * 255.0).round().min(255.0) as u8;
            out[i] = v;
            out[i + 1] = v;
            out[i + 2] = v;
            out[i + 3] = (a * 255.0).round().min(255.0) as u8;
        }
    }
    out
}

/// The hover dot's element on `output`, front first (none when the pen is
/// not in range of it, or the dot is off). A free function, as
/// [`crate::pointer::cursor_elements`]: called where the renderer is
/// already borrowed out of the state.
pub fn dot_elements(
    space: &smithay::desktop::Space<smithay::desktop::Window>,
    renderer: &mut GlesRenderer,
    output: &Output,
) -> Vec<Element> {
    let (at, rubber) = PEN.with(|p| {
        let p = p.borrow();
        let here = p.settings.dot && p.output.as_deref() == Some(output.name().as_str());
        (if here { p.at } else { None }, p.rubber)
    });
    let Some(at) = at else {
        return Vec::new();
    };
    let Some(g) = space.output_geometry(output) else {
        return Vec::new();
    };
    let scale = output.current_scale().fractional_scale();
    let px = (DOT_SIZE * scale).round().max(6.0) as i32;
    let local = (at.x - g.loc.x as f64 - DOT_SIZE / 2.0, at.y - g.loc.y as f64 - DOT_SIZE / 2.0);
    let location: Point<f64, Physical> = ((local.0 * scale).round(), (local.1 * scale).round()).into();
    let logical = DOT_SIZE.round() as i32;
    DOTS.with(|dots| {
        let mut dots = dots.borrow_mut();
        let buffer = dots.entry((px, rubber)).or_insert_with(|| {
            let pixels = dot_pixels(px as usize, rubber);
            MemoryRenderBuffer::from_slice(&pixels, Fourcc::Argb8888, (px, px), 1, Transform::Normal, None)
        });
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
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_barrel_goes_to_the_tablet_client_or_a_pointer() {
        use BarrelTo::*;
        use PenButton::*;
        // A tablet client under the pen takes it, with or without a pointer.
        assert_eq!(barrel_to(RightClick, false, true, false, true), Tablet);
        assert_eq!(barrel_to(RightClick, false, true, true, true), Tablet);
        // Anyone else: a right click where the seat has a pointer...
        assert_eq!(barrel_to(RightClick, false, false, true, true), Pointer);
        // ... and nothing on the phone without one.
        assert_eq!(barrel_to(RightClick, false, false, false, true), Nobody);
        // The keyboard panel or a touchpad panel: the pen is a finger there.
        assert_eq!(barrel_to(RightClick, false, false, true, false), Nobody);
        // The tip already down as a finger: a finger has no buttons.
        assert_eq!(barrel_to(RightClick, true, false, true, true), Nobody);
        // Off: nobody, a tablet client neither.
        assert_eq!(barrel_to(Off, false, true, true, true), Nobody);
        assert_eq!(barrel_to(Off, false, false, true, true), Nobody);
    }

    #[test]
    fn drawing_apps_by_id_or_its_last_part() {
        let list = Settings::default().apps;
        let drawing = [
            "com.github.flxzt.rnote",
            "com.github.xournalpp.xournalpp",
            "org.kde.krita",
            "krita",
            "org.gimp.GIMP",
            "Gimp",
            "Gimp-2.10",
            "org.inkscape.Inkscape",
            "org.duo2os.PenTest",
            "org.duo2os.Nibfair",
        ];
        for app in drawing {
            assert!(drawing_app(app, &list), "{app}");
        }
        for app in ["org.gnome.Nautilus", "firefox", "org.duo2os.Settings", "foot", "", "XTerm", "krita-like"]
        {
            assert!(!drawing_app(app, &list), "{app}");
        }
        assert!(drawing_app("com.example.Sketch", &parse_apps("sketch")));
        assert!(!drawing_app("org.kde.krita", &[]));
    }

    #[test]
    fn the_apps_list_parses() {
        assert_eq!(parse_apps("rnote, krita  gimp,,Krita"), vec!["rnote", "krita", "gimp"]);
        assert!(parse_apps("").is_empty());
        assert_eq!(PenInput::parse("apps"), Some(PenInput::Apps));
        assert_eq!(PenInput::parse("all"), Some(PenInput::All));
        assert_eq!(PenInput::parse("some"), None);
        assert_eq!(Settings::default().input, PenInput::Apps);
    }

    #[test]
    fn the_tip_goes_to_a_drawing_app_or_as_a_finger() {
        // apps: only a drawing app's tablet client gets the tool
        assert!(tip_is_tablet(PenInput::Apps, true, true, false));
        assert!(!tip_is_tablet(PenInput::Apps, true, false, false));
        // all: every tablet client
        assert!(tip_is_tablet(PenInput::All, true, false, false));
        // no tablets bound: a finger, whatever the setting
        assert!(!tip_is_tablet(PenInput::All, false, true, false));
        // the compositor's own UI (a carry, the divider): a finger, even over a drawing app
        assert!(!tip_is_tablet(PenInput::Apps, true, true, true));
        assert!(!tip_is_tablet(PenInput::All, true, true, true));
    }

    #[test]
    fn the_button_setting_parses() {
        assert_eq!(PenButton::parse("right_click"), Some(PenButton::RightClick));
        assert_eq!(PenButton::parse("off"), Some(PenButton::Off));
        assert_eq!(PenButton::parse("left_click"), None);
        assert_eq!(PenButton::default(), PenButton::RightClick);
        assert_eq!(serde_json::to_value(PenButton::RightClick).unwrap(), "right_click");
        assert!(Settings::default().dot);
    }

    #[test]
    fn the_dot_is_a_disc_and_the_eraser_a_ring() {
        let px = 22;
        let alpha = |pixels: &[u8], x: usize, y: usize| pixels[(y * px + x) * 4 + 3];
        let white = |pixels: &[u8], x: usize, y: usize| pixels[(y * px + x) * 4];
        let dot = dot_pixels(px, false);
        // Opaque white in the middle, a dark rim, nothing in the corners.
        assert!(alpha(&dot, 11, 11) > 200 && white(&dot, 11, 11) > 200);
        assert!(alpha(&dot, 1, 11) > 100 && white(&dot, 1, 11) < 100);
        assert_eq!(alpha(&dot, 0, 0), 0);
        assert_eq!(alpha(&dot, px - 1, px - 1), 0);
        // Premultiplied: no colour above its alpha.
        assert!(dot.chunks(4).all(|p| p[0] <= p[3] && p[1] <= p[3] && p[2] <= p[3]));
        let ring = dot_pixels(px, true);
        assert_eq!(alpha(&ring, 11, 11), 0, "the eraser's ring is open in the middle");
        assert!(alpha(&ring, 3, 11) > 100);
        assert!(ring.chunks(4).all(|p| p[0] <= p[3]));
    }
}
