//! zphoc_device_state_v1 (phoc's own protocol, version 2): whether a
//! hardware keyboard is attached. It is here for the on-screen keyboard
//! phosh-osk-stub (Debian trixie's 0.46), which does
//! not start without it: it makes its keyboard surface only once it has
//! bound this global AND the input method, the virtual keyboard, the layer
//! shell, wlr-foreign-toplevel-management ([`crate::wlrtoplevel`]) and
//! wlr-data-control (`phosh_osk_stub_has_wl_protcols`). It reads one thing
//! from it, the `keyboard` capability: while a real keyboard is attached (a
//! Bluetooth keyboard) it does not unfold by itself for a text field, unless
//! its `ignore-hw-keyboards` setting says so; the bar's button and the quick
//! menu's tile still bring it up (sm.puri.OSK0 SetVisible).
//!
//! A hardware keyboard is a libinput keyboard that can type
//! ([`is_typing_keyboard`]): the Duo's own key devices (the power and
//! volume keys, the headset jack's buttons) are keyboards to libinput too,
//! and must not count, or the on-screen keyboard would never unfold.
//!
//! The tablet-mode and lid switches are never announced (the Duo has
//! neither as a switch: how it is folded is duo-posed's), so asking for one
//! is the protocol's `missing_capability` error.
//!
//! The global is guarded ([`crate::trust::Guarded::DeviceState`]): only
//! trusted clients (the session's keyboard) see it.

use std::collections::BTreeSet;

use smithay::reexports::wayland_server::backend::ClientId;
use smithay::reexports::wayland_server::{
    Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
};
use tracing::info;

use crate::state::{ClientState, DuoWc};
use crate::trust::Guarded;

/// The protocol, generated from phosh-osk-stub's copy of the XML
/// (`protocols/phoc-device-state-unstable-v1.xml`).
#[allow(missing_docs, dead_code, non_camel_case_types, unused_unsafe, unused_variables)]
#[allow(non_upper_case_globals, non_snake_case, unused_imports, clippy::all)]
pub mod protocol {
    /// The server side.
    pub mod server {
        use smithay::reexports::wayland_server;
        use smithay::reexports::wayland_server::protocol::*;

        pub mod __interfaces {
            use smithay::reexports::wayland_server::backend as wayland_backend;
            use smithay::reexports::wayland_server::protocol::__interfaces::*;
            wayland_scanner::generate_interfaces!("protocols/phoc-device-state-unstable-v1.xml");
        }
        use self::__interfaces::*;

        wayland_scanner::generate_server_code!("protocols/phoc-device-state-unstable-v1.xml");
    }
}

use protocol::server::zphoc_device_state_v1::{self, ZphocDeviceStateV1};
use protocol::server::zphoc_lid_switch_v1::ZphocLidSwitchV1;
use protocol::server::zphoc_tablet_mode_switch_v1::ZphocTabletModeSwitchV1;

/// The version offered.
pub const VERSION: u32 = 2;

/// The `keyboard` capability bit (since version 2).
pub const CAPABILITY_KEYBOARD: u32 = 4;

/// evdev codes (linux/input-event-codes.h) a keyboard one can type on has.
const KEY_A: u32 = 30;
const KEY_Z: u32 = 44;
const KEY_SPACE: u32 = 57;
const KEY_ENTER: u32 = 28;

/// Whether a libinput device is a keyboard one can type on: it has the
/// keyboard capability and the letter, space and enter keys
/// (`keyboard_has_key`: None when libinput cannot tell). The power key,
/// the volume keys and a headset's buttons are keyboards with none of them.
pub fn is_typing_keyboard(keyboard: bool, has_key: impl Fn(u32) -> Option<bool>) -> bool {
    keyboard && [KEY_A, KEY_Z, KEY_SPACE, KEY_ENTER].into_iter().all(|key| has_key(key) == Some(true))
}

/// The capability bits a client of `version` is told: the keyboard bit only
/// from version 2 (it did not exist before).
pub fn capabilities(hw_keyboard: bool, version: u32) -> u32 {
    if hw_keyboard && version >= 2 {
        CAPABILITY_KEYBOARD
    } else {
        0
    }
}

/// The hardware keyboards attached, and who listens.
#[derive(Default)]
pub struct DeviceState {
    /// The typing keyboards present, by libinput sysname (`event17`).
    keyboards: BTreeSet<String>,
    /// The bound zphoc_device_state_v1 objects.
    bound: Vec<ZphocDeviceStateV1>,
    /// Laptop mode's keyboard panel is up (a keyboard to type on, as a
    /// plugged one: crate::laptopmode).
    laptop: bool,
}

impl DeviceState {
    /// Create the global.
    pub fn init(display: &DisplayHandle) {
        display.create_global::<DuoWc, ZphocDeviceStateV1, ()>(VERSION, ());
    }

    /// Whether a hardware keyboard is attached.
    pub fn hw_keyboard(&self) -> bool {
        !self.keyboards.is_empty() || self.laptop
    }

    /// Laptop mode's keyboard panel came or went: it counts as a hardware
    /// keyboard (the on-screen keyboard does not unfold by itself).
    pub fn set_laptop_keyboard(&mut self, on: bool) {
        let before = self.hw_keyboard();
        self.laptop = on;
        if before != self.hw_keyboard() {
            info!(on, "laptop keyboard panel: a hardware keyboard for the on-screen keyboard");
            self.announce();
        }
    }

    /// A libinput device came (`typing`: [`is_typing_keyboard`]).
    pub fn device_added(&mut self, sysname: &str, name: &str, typing: bool) {
        if !typing {
            return;
        }
        let before = self.hw_keyboard();
        self.keyboards.insert(sysname.to_owned());
        info!(device = name, sysname, "hardware keyboard attached");
        if !before {
            self.announce();
        }
    }

    /// A libinput device went.
    pub fn device_removed(&mut self, sysname: &str) {
        if !self.keyboards.remove(sysname) {
            return;
        }
        info!(sysname, "hardware keyboard gone");
        if !self.hw_keyboard() {
            self.announce();
        }
    }

    /// Tell every listener the capabilities again.
    fn announce(&mut self) {
        let hw = self.hw_keyboard();
        self.bound.retain(|r| r.is_alive());
        for resource in &self.bound {
            send_capabilities(resource, hw);
        }
    }
}

/// Send one listener the capabilities.
fn send_capabilities(resource: &ZphocDeviceStateV1, hw_keyboard: bool) {
    let bits = capabilities(hw_keyboard, resource.version());
    resource.capabilities(zphoc_device_state_v1::Capability::from_bits_truncate(bits));
}

impl GlobalDispatch<ZphocDeviceStateV1, ()> for DuoWc {
    fn bind(
        state: &mut Self,
        _handle: &DisplayHandle,
        client: &Client,
        resource: New<ZphocDeviceStateV1>,
        _global_data: &(),
        data_init: &mut DataInit<'_, Self>,
    ) {
        let resource = data_init.init(resource, ());
        let hw = state.device_state.hw_keyboard();
        info!(client = ?client.id(), hw_keyboard = hw, "device state bound");
        send_capabilities(&resource, hw);
        state.device_state.bound.push(resource);
    }

    fn can_view(client: Client, _global_data: &()) -> bool {
        ClientState::may_bind(&client, Guarded::DeviceState)
    }
}

impl Dispatch<ZphocDeviceStateV1, ()> for DuoWc {
    fn request(
        _state: &mut Self,
        _client: &Client,
        _resource: &ZphocDeviceStateV1,
        request: zphoc_device_state_v1::Request,
        _data: &(),
        _dhandle: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        // Neither switch is ever announced: asking for one is an error.
        let error = zphoc_device_state_v1::Error::MissingCapability as u32;
        match request {
            zphoc_device_state_v1::Request::GetTabletModeSwitch { id } => {
                data_init.post_error(id, error, "no tablet mode switch");
            }
            zphoc_device_state_v1::Request::GetLidSwitch { id } => {
                data_init.post_error(id, error, "no lid switch");
            }
        }
    }

    fn destroyed(state: &mut Self, _client: ClientId, resource: &ZphocDeviceStateV1, _data: &()) {
        // Bookkeeping only: nothing is sent from a destructor (crate::teardown).
        state.device_state.bound.retain(|r| r.id() != resource.id());
    }
}

// The switch objects are never made (the requests above fail), but their
// interfaces need a dispatcher to exist.
impl Dispatch<ZphocTabletModeSwitchV1, ()> for DuoWc {
    fn request(
        _state: &mut Self,
        _client: &Client,
        _resource: &ZphocTabletModeSwitchV1,
        _request: protocol::server::zphoc_tablet_mode_switch_v1::Request,
        _data: &(),
        _dhandle: &DisplayHandle,
        _data_init: &mut DataInit<'_, Self>,
    ) {
    }
}

impl Dispatch<ZphocLidSwitchV1, ()> for DuoWc {
    fn request(
        _state: &mut Self,
        _client: &Client,
        _resource: &ZphocLidSwitchV1,
        _request: protocol::server::zphoc_lid_switch_v1::Request,
        _data: &(),
        _dhandle: &DisplayHandle,
        _data_init: &mut DataInit<'_, Self>,
    ) {
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A device's keys, as libinput's keyboard_has_key would answer.
    fn keys(present: &[u32]) -> impl Fn(u32) -> Option<bool> + '_ {
        move |k| Some(present.contains(&k))
    }

    #[test]
    fn a_full_keyboard_counts() {
        // A Bluetooth keyboard: letters, space, enter.
        assert!(is_typing_keyboard(true, keys(&[KEY_A, KEY_Z, KEY_SPACE, KEY_ENTER, 1, 2])));
    }

    #[test]
    fn the_phones_own_keys_do_not() {
        // gpio-keys (volume up 115), pmic_pwrkey (power 116), pmic_resin
        // (volume down 114), the headset jack (play/pause 164, volume):
        // keyboards to libinput, none can type (/proc/bus/input/devices on
        // the phone, 2026-11-09).
        assert!(!is_typing_keyboard(true, keys(&[115])));
        assert!(!is_typing_keyboard(true, keys(&[116])));
        assert!(!is_typing_keyboard(true, keys(&[114])));
        assert!(!is_typing_keyboard(true, keys(&[113, 114, 115, 164, 226])));
    }

    #[test]
    fn a_number_pad_does_not() {
        // Digits and enter, no letters.
        assert!(!is_typing_keyboard(true, keys(&[KEY_ENTER, 71, 72, 73, 79, 80, 81, 82])));
    }

    #[test]
    fn not_a_keyboard_or_unknown_does_not() {
        assert!(!is_typing_keyboard(false, keys(&[KEY_A, KEY_Z, KEY_SPACE, KEY_ENTER])));
        assert!(!is_typing_keyboard(true, |_| None));
    }

    #[test]
    fn the_keyboard_bit_is_for_version_2() {
        assert_eq!(capabilities(true, 2), CAPABILITY_KEYBOARD);
        assert_eq!(capabilities(true, 1), 0);
        assert_eq!(capabilities(false, 2), 0);
    }

    #[test]
    fn keyboards_are_counted_by_device() {
        // No resources bound: the bookkeeping alone.
        let mut ds = DeviceState::default();
        ds.device_added("event0", "gpio-keys", false);
        assert!(!ds.hw_keyboard());
        ds.device_added("event17", "BT keyboard", true);
        ds.device_added("event18", "another", true);
        assert!(ds.hw_keyboard());
        ds.device_removed("event17");
        assert!(ds.hw_keyboard());
        ds.device_removed("event0");
        assert!(ds.hw_keyboard());
        ds.device_removed("event18");
        assert!(!ds.hw_keyboard());
    }
}
