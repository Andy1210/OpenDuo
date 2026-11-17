//! The Surface Pen's top button, which comes over Bluetooth.
//!
//! The tip, the eraser end and the barrel button reach the digitizer
//! ([`crate::tablet`], no pairing); the top button does not: the pen sends it
//! over Bluetooth LE, as a HID-over-GATT device. Paired (Duo Settings > Pen),
//! BlueZ's hog plugin makes a uhid HID device of it (bus 0x0005, Bluetooth),
//! and hid-input a key device; on Windows (and in the stock Android HAL) the
//! button is left Super + F20 for one click, F19 for a double click and F18
//! for a press and hold.
//!
//! duo-wc takes EVERY key of that device ([`is_pen_bluetooth`]): none reaches
//! a client, nor the seat's modifier state (the Super that comes with them);
//! a device of the pen with no keys (a pointer collection) is switched off.
//! The three presses ([`press_of`]) are announced on the event stream, like
//! the volume keys:
//!
//! ```text
//! {"event":"pen_button","press":"click","output":"DSI-2","locked":false}
//! ```
//!
//! `output` is the panel the button is for ([`button_output`]): the one the pen
//! is over, or was over in the last [`RECENT`] (the button is pressed with the
//! pen lifted), else the active panel; `null` while the screen is dark (the
//! ambient screen counts as dark): the button does not wake the screen, as the
//! tip does not. What a press does is the shell's (`shell/duo-shell`,
//! `penmodel.py`: Rnote, a screenshot of that panel, the quick menu), set in
//! Duo Settings > Pen.
//!
//! Every key of the device is logged (its evdev code), the first
//! [`LOG_KEYS`] of each connection at info level: which codes a pen sends is
//! read from the journal.

use std::cell::RefCell;
use std::collections::HashMap;
use std::time::Duration;

use smithay::backend::input::KeyState;
use smithay::reexports::input::{self as libinput, DeviceCapability};
use tracing::{debug, info};

use crate::state::DuoWc;

/// The input bus of a Bluetooth HID device (linux/input.h BUS_BLUETOOTH).
pub const BUS_BLUETOOTH: u32 = 0x05;
/// Microsoft's USB vendor id, and its Bluetooth SIG company id (a PnP ID may
/// name either).
pub const VENDORS_MICROSOFT: [u32; 2] = [0x045e, 0x0006];

/// The evdev codes of the presses (linux/input-event-codes.h).
pub const KEY_F18: u32 = 188;
pub const KEY_F19: u32 = 189;
pub const KEY_F20: u32 = 190;

/// How long after the pen left a panel's range a press is still for that
/// panel.
pub const RECENT: Duration = Duration::from_secs(5);

/// How many keys of a connection are logged at info level.
pub const LOG_KEYS: usize = 12;

/// xkb keycodes are evdev codes plus 8.
const XKB_OFFSET: u32 = 8;

/// One press of the top button.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Press {
    Click,
    DoubleClick,
    Hold,
}

impl Press {
    pub fn name(self) -> &'static str {
        match self {
            Press::Click => "click",
            Press::DoubleClick => "double_click",
            Press::Hold => "hold",
        }
    }
}

/// The press a key of the pen means (F20 one click, F19 double, F18 held);
/// anything else it sends (the Super with them) means nothing.
pub fn press_of(evdev_code: u32) -> Option<Press> {
    match evdev_code {
        KEY_F20 => Some(Press::Click),
        KEY_F19 => Some(Press::DoubleClick),
        KEY_F18 => Some(Press::Hold),
        _ => None,
    }
}

/// Whether a libinput device is one of a Surface pen's Bluetooth HID
/// devices: on the Bluetooth bus, with "pen" in its name (BlueZ names the
/// uhid device after the pen: "Surface Slim Pen 2"; hid-input may add a
/// collection's word), Microsoft's by either id or named "Surface". The
/// pen's digitizer devices (duo2-touchd's "Surface Duo 2 Pen Left / Right",
/// the kernel's "spi 045E:0C1D Stylus") are not on the Bluetooth bus.
pub fn is_pen_bluetooth(name: &str, vendor: u32, bustype: Option<u32>) -> bool {
    if bustype != Some(BUS_BLUETOOTH) {
        return false;
    }
    let name = name.to_ascii_lowercase();
    name.contains("pen") && (VENDORS_MICROSOFT.contains(&vendor) || name.contains("surface"))
}

/// A sysfs id attribute ("0005\n", hex) as a number.
pub fn parse_hex(text: &str) -> Option<u32> {
    u32::from_str_radix(text.trim().trim_start_matches("0x"), 16).ok()
}

/// The input bus of an event device (`/sys/class/input/eventN/device/id/bustype`).
fn bustype(sysname: &str) -> Option<u32> {
    std::fs::read_to_string(format!("/sys/class/input/{sysname}/device/id/bustype"))
        .ok()
        .and_then(|t| parse_hex(&t))
}

/// The panel a press is for: the one the pen is over, else the one it left
/// less than [`RECENT`] ago, else the active one; only a lit panel
/// (`lit`). None while the screen is dark.
pub fn button_output(
    dark: bool,
    over: Option<&str>,
    left: Option<(&str, Duration)>,
    active: Option<&str>,
    lit: impl Fn(&str) -> bool,
) -> Option<String> {
    if dark {
        return None;
    }
    let recent = left.filter(|(_, ago)| *ago <= RECENT).map(|(o, _)| o);
    [over, recent, active].into_iter().flatten().find(|o| lit(o)).map(str::to_owned)
}

/// The event-stream line for a press.
pub fn event(press: Press, output: Option<&str>, locked: bool) -> serde_json::Value {
    serde_json::json!({
        "event": "pen_button",
        "press": press.name(),
        "output": output,
        "locked": locked,
    })
}

thread_local! {
    // The pen key devices present, by libinput sysname, with the keys
    // logged at info level so far.
    static DEVICES: RefCell<HashMap<String, usize>> = RefCell::new(HashMap::new());
}

/// A device came. A pen's Bluetooth device is the compositor's whole: its
/// keys are taken from now on ([`key`]); anything else of it (a pointer
/// collection, say) is switched off in libinput, so it never becomes the
/// seat's pointer or a hardware keyboard. True for a pen's device: the
/// caller does nothing else with it.
pub fn device_added(device: &mut libinput::Device) -> bool {
    let sysname = device.sysname().to_owned();
    let name = device.name().into_owned();
    if !is_pen_bluetooth(&name, device.id_vendor(), bustype(&sysname)) {
        return false;
    }
    let vendor = format!("{:04x}", device.id_vendor());
    let product = format!("{:04x}", device.id_product());
    if device.has_capability(DeviceCapability::Keyboard) {
        info!(
            device = name,
            sysname, vendor, product, "the pen's Bluetooth keys (its top button): taken by the compositor"
        );
        DEVICES.with(|d| d.borrow_mut().insert(sysname, 0));
    } else {
        let off = device.config_send_events_set_mode(libinput::SendEventsMode::DISABLED);
        info!(
            device = name,
            sysname,
            vendor,
            product,
            ?off,
            "a pen's Bluetooth device with no keys: switched off"
        );
    }
    true
}

pub fn device_removed(device: &libinput::Device) {
    if DEVICES.with(|d| d.borrow_mut().remove(device.sysname()).is_some()) {
        info!(device = %device.name(), "the pen's Bluetooth keys gone (disconnected)");
    }
}

/// Whether a key device is a pen's (its keys go to [`key`], nowhere else).
pub fn takes(sysname: &str) -> bool {
    DEVICES.with(|d| d.borrow().contains_key(sysname))
}

/// How many pen key devices are up (for `get_state`'s `pen`).
pub fn devices() -> usize {
    DEVICES.with(|d| d.borrow().len())
}

/// A key of a pen's device (xkb keycode): logged, and a press announced.
pub fn key(state: &mut DuoWc, sysname: &str, keycode: u32, key_state: KeyState) {
    let code = keycode.saturating_sub(XKB_OFFSET);
    let pressed = key_state == KeyState::Pressed;
    let loud = DEVICES.with(|d| {
        let mut d = d.borrow_mut();
        let n = d.entry(sysname.to_owned()).or_insert(0);
        *n += 1;
        *n <= LOG_KEYS
    });
    if loud {
        info!(code, pressed, "pen key");
    } else {
        debug!(code, pressed, "pen key");
    }
    if !pressed {
        return;
    }
    let Some(press) = press_of(code) else {
        return;
    };
    let dark = state.screen_dark() || state.ambient.is_some();
    let (over, left) = crate::tablet::whereabouts();
    let active = state.active_output().map(|o| o.name());
    let output = button_output(
        dark,
        over.as_deref(),
        left.as_ref().map(|(o, ago)| (o.as_str(), *ago)),
        active.as_deref(),
        |o| state.is_lit(o),
    );
    if output.is_some() {
        state.note_activity();
    }
    info!(press = press.name(), ?output, locked = state.lock.locked, "pen button");
    let line = event(press, output.as_deref(), state.lock.locked);
    crate::ipc::broadcast(state, &line);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_presses_by_evdev_code() {
        assert_eq!(press_of(190), Some(Press::Click));
        assert_eq!(press_of(189), Some(Press::DoubleClick));
        assert_eq!(press_of(188), Some(Press::Hold));
        // KEY_LEFTMETA, which comes with them, and the rest: nothing.
        assert_eq!(press_of(125), None);
        assert_eq!(press_of(187), None);
        assert_eq!(press_of(191), None);
        assert_eq!(Press::DoubleClick.name(), "double_click");
    }

    #[test]
    fn only_a_pens_bluetooth_devices() {
        let bt = Some(BUS_BLUETOOTH);
        assert!(is_pen_bluetooth("Surface Slim Pen 2", 0x045e, bt));
        assert!(is_pen_bluetooth("Surface Slim Pen 2 Keyboard", 0x0006, bt));
        assert!(is_pen_bluetooth("Surface Slim Pen 2 Mouse", 0x045e, bt));
        assert!(is_pen_bluetooth("Surface Pen", 0x1234, bt));
        // Not Bluetooth, not a pen, not Microsoft's.
        assert!(!is_pen_bluetooth("Surface Slim Pen 2", 0x045e, Some(0x03)));
        assert!(!is_pen_bluetooth("Surface Slim Pen 2", 0x045e, None));
        assert!(!is_pen_bluetooth("Microsoft Bluetooth Keyboard", 0x045e, bt));
        assert!(!is_pen_bluetooth("Some Pen Mouse", 0x1234, bt));
        // The digitizer's devices.
        assert!(!is_pen_bluetooth("Surface Duo 2 Pen Left", 0, Some(0x06)));
        assert!(!is_pen_bluetooth("spi 045E:0C1D Stylus", 0x045e, Some(0x1c)));
    }

    #[test]
    fn sysfs_ids() {
        assert_eq!(parse_hex("0005\n"), Some(5));
        assert_eq!(parse_hex("045e"), Some(0x045e));
        assert_eq!(parse_hex("0x001c"), Some(0x1c));
        assert_eq!(parse_hex("zz"), None);
    }

    #[test]
    fn the_panel_a_press_is_for() {
        let both = |_: &str| true;
        let left_only = |o: &str| o == "DSI-1";
        let s = Duration::from_secs;
        // Over a panel, or just lifted from one.
        assert_eq!(button_output(false, Some("DSI-2"), None, Some("DSI-1"), both).as_deref(), Some("DSI-2"));
        assert_eq!(
            button_output(false, None, Some(("DSI-2", s(3))), Some("DSI-1"), both).as_deref(),
            Some("DSI-2")
        );
        // Lifted long ago: the active panel.
        assert_eq!(
            button_output(false, None, Some(("DSI-2", s(30))), Some("DSI-1"), both).as_deref(),
            Some("DSI-1")
        );
        assert_eq!(button_output(false, None, None, Some("DSI-1"), both).as_deref(), Some("DSI-1"));
        // Only a lit panel.
        assert_eq!(
            button_output(false, None, Some(("DSI-2", s(1))), Some("DSI-1"), left_only).as_deref(),
            Some("DSI-1")
        );
        assert_eq!(button_output(false, None, None, Some("DSI-2"), left_only), None);
        // Dark: for nobody.
        assert_eq!(button_output(true, Some("DSI-2"), None, Some("DSI-1"), both), None);
    }

    #[test]
    fn the_event_line() {
        let e = event(Press::Hold, Some("DSI-1"), false);
        assert_eq!(e["event"], "pen_button");
        assert_eq!(e["press"], "hold");
        assert_eq!(e["output"], "DSI-1");
        assert_eq!(e["locked"], false);
        let dark = event(Press::Click, None, true);
        assert!(dark["output"].is_null());
        assert_eq!(dark["locked"], true);
    }
}
