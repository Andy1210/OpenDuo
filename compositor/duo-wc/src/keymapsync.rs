//! The keymap behind an on-screen keyboard's keys, for every app.
//!
//! phosh-osk-stub types Return, Tab, the arrows and Escape through
//! zwp_virtual_keyboard_v1 with a keymap of its own (letters go through the
//! input method). Smithay (ff5fa7d) keeps one "active" keymap per seat: a
//! virtual keyboard's key makes its keymap active and sends it to every
//! wl_keyboard known THEN, once (`KeyboardHandle::send_keymap` only sends
//! when the active keymap's id changes). A wl_keyboard bound later gets the
//! SEAT's keymap (`new_kbd`) while the virtual keyboard's stays active, so
//! the next virtual key reaches it with no keymap first, and the key code
//! means something else in the seat's keymap: Pipeline got key 171 and saw
//! no Enter (found with WAYLAND_DEBUG, 2026-11-12). Every app started after
//! the on-screen keyboard's first such key was broken for those keys until
//! duo-wc restarted.
//!
//! **The fix**, with Smithay's public API: a wl_keyboard bound after a
//! virtual key ([`KeymapSync`]) makes the seat's keymap active again
//! (`set_xkb_config` with duo-wc's own config, [`seat_xkb_config`]: the
//! same keymap; it differs from the active one, so Smithay sends it to
//! every keyboard), and the virtual keyboard's next key sends ITS keymap to
//! every keyboard, the new one included. The hook is the bind itself
//! (`wl_seat.get_keyboard`, [`seat_request`]), so a client that binds its
//! keyboard late is covered too. Once per bound keyboard, and only if a
//! virtual key came since the seat's keymap was last active (a key of a
//! real keyboard forwarded to a client makes it active by itself): no
//! keymap storm on focus changes. A key held on a hardware keyboard stays
//! held (Smithay replays the pressed keys into the new state); a Caps or
//! Num Lock it had locked is reset in that one case.
//!
//! Upstream: `new_kbd` should send the ACTIVE keymap (or reset the active
//! id), not the seat's.

use smithay::input::keyboard::XkbConfig;
use smithay::input::{Seat, SeatHandler, SeatState};
use smithay::reexports::wayland_protocols_misc::zwp_virtual_keyboard_v1::server::zwp_virtual_keyboard_v1::{
    self, ZwpVirtualKeyboardV1,
};
use smithay::reexports::wayland_server::protocol::wl_keyboard::WlKeyboard;
use smithay::reexports::wayland_server::protocol::wl_pointer::WlPointer;
use smithay::reexports::wayland_server::protocol::wl_seat::{self, WlSeat};
use smithay::reexports::wayland_server::protocol::wl_touch::WlTouch;
use smithay::reexports::wayland_server::{Client, DataInit, Dispatch, DisplayHandle};
use smithay::wayland::compositor::CompositorHandler;
use smithay::wayland::seat::{KeyboardUserData, PointerUserData, SeatUserData, TouchUserData, WaylandFocus};
use smithay::wayland::virtual_keyboard::{VirtualKeyboardManagerState, VirtualKeyboardUserData};
use tracing::{info, warn};

/// The seat keyboard's xkb names: the keyboard layouts' (crate::kbdlayout:
/// the system's list, else duo-wc.conf's `xkb_*`), set again whenever that
/// list changes ([`set_seat_xkb`]).
static SEAT_XKB: std::sync::RwLock<Option<crate::config::Xkb>> = std::sync::RwLock::new(None);

/// Leaked copies of the names handed out as `XkbConfig<'static>` (one per
/// distinct set: one per list of layouts the session has had).
static NAMES: std::sync::Mutex<Vec<&'static crate::config::Xkb>> = std::sync::Mutex::new(Vec::new());

/// Sets the seat's xkb names (`xkb_layout = hu`, ...): what
/// [`seat_xkb_config`] gives from then on (the caller sets the keyboard's
/// keymap itself).
pub fn set_seat_xkb(xkb: &crate::config::Xkb) {
    if let Ok(mut slot) = SEAT_XKB.write() {
        *slot = Some(xkb.clone());
    }
}

/// The seat keyboard's xkb configuration (the keyboard layouts', crate::
/// kbdlayout; empty names: the environment's XKB_DEFAULT_*, else
/// libxkbcommon's defaults): what the seat starts with and what it is set
/// back to.
pub fn seat_xkb_config() -> XkbConfig<'static> {
    let Some(current) = SEAT_XKB.read().ok().and_then(|slot| slot.clone()) else {
        return XkbConfig::default();
    };
    // XkbConfig borrows its names: one leaked copy per distinct set (a
    // few in practice), never one per call.
    let names: &'static crate::config::Xkb = {
        let mut kept = NAMES.lock().unwrap_or_else(|e| e.into_inner());
        match kept.iter().find(|k| ***k == current) {
            Some(k) => k,
            None => {
                let leaked: &'static crate::config::Xkb = Box::leak(Box::new(current));
                kept.push(leaked);
                leaked
            }
        }
    };
    XkbConfig {
        rules: "",
        model: &names.model,
        layout: &names.layout,
        variant: &names.variant,
        options: (!names.options.is_empty()).then(|| names.options.clone()),
    }
}

/// Whether the active keymap may be a virtual keyboard's (pure).
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct KeymapSync {
    /// A virtual keyboard sent a key or modifiers since the seat's keymap
    /// was last made active.
    virtual_active: bool,
    /// Never act (a test's "before").
    off: bool,
}

impl KeymapSync {
    /// Does nothing (to show what happens without it).
    #[cfg(test)]
    pub fn off() -> Self {
        KeymapSync { virtual_active: false, off: true }
    }

    /// A virtual keyboard sent a key or its modifiers: its keymap is the
    /// active one now.
    pub fn virtual_key(&mut self) {
        self.virtual_active = true;
    }

    /// A key of the seat (a hardware keyboard, duo-wc's own) went to a
    /// client: the seat's keymap is the active one.
    pub fn seat_key(&mut self) {
        self.virtual_active = false;
    }

    /// A wl_keyboard was bound (it got the seat's keymap): whether the seat's
    /// keymap must be made active again; then it is.
    pub fn keyboard_bound(&mut self) -> bool {
        !self.off && std::mem::take(&mut self.virtual_active)
    }
}

/// What the dispatch wrappers need of the compositor's state.
pub trait KeymapSyncHandler: SeatHandler + Sized + 'static {
    /// Its [`KeymapSync`].
    fn keymap_sync(&mut self) -> &mut KeymapSync;
}

/// `wl_seat` requests: Smithay's handling, then, for `get_keyboard`, the
/// seat's keymap made active again if a virtual keyboard's may be.
pub fn seat_request<D>(
    state: &mut D,
    client: &Client,
    resource: &WlSeat,
    request: wl_seat::Request,
    data: &SeatUserData<D>,
    dh: &DisplayHandle,
    data_init: &mut DataInit<'_, D>,
) where
    D: KeymapSyncHandler + CompositorHandler,
    D: Dispatch<WlSeat, SeatUserData<D>>,
    D: Dispatch<WlKeyboard, KeyboardUserData<D>>,
    D: Dispatch<WlPointer, PointerUserData<D>>,
    D: Dispatch<WlTouch, TouchUserData<D>>,
    <D as SeatHandler>::KeyboardFocus: WaylandFocus,
{
    let keyboard = matches!(request, wl_seat::Request::GetKeyboard { .. });
    <SeatState<D> as Dispatch<WlSeat, SeatUserData<D>, D>>::request(
        state, client, resource, request, data, dh, data_init,
    );
    if !keyboard || !state.keymap_sync().keyboard_bound() {
        return;
    }
    let Some(keyboard) = Seat::<D>::from_resource(resource).and_then(|seat| seat.get_keyboard()) else {
        return;
    };
    match keyboard.set_xkb_config(state, seat_xkb_config()) {
        Ok(()) => info!(
            client = ?client.id(),
            "keyboard bound after an on-screen key: the seat keymap is active again \
             (the next on-screen key sends its keymap to every keyboard)"
        ),
        Err(err) => {
            warn!(?err, "keyboard bound after an on-screen key: the seat keymap could not be set again")
        }
    }
}

/// `zwp_virtual_keyboard_v1` requests: Smithay's handling; a key or
/// modifiers makes the virtual keyboard's keymap the active one.
pub fn virtual_keyboard_request<D>(
    state: &mut D,
    client: &Client,
    resource: &ZwpVirtualKeyboardV1,
    request: zwp_virtual_keyboard_v1::Request,
    data: &VirtualKeyboardUserData<D>,
    dh: &DisplayHandle,
    data_init: &mut DataInit<'_, D>,
) where
    D: KeymapSyncHandler,
    D: Dispatch<ZwpVirtualKeyboardV1, VirtualKeyboardUserData<D>>,
    <D as SeatHandler>::KeyboardFocus: WaylandFocus,
{
    let typed = matches!(
        request,
        zwp_virtual_keyboard_v1::Request::Key { .. } | zwp_virtual_keyboard_v1::Request::Modifiers { .. }
    );
    <VirtualKeyboardManagerState as Dispatch<ZwpVirtualKeyboardV1, VirtualKeyboardUserData<D>, D>>::request(
        state, client, resource, request, data, dh, data_init,
    );
    if typed {
        state.keymap_sync().virtual_key();
    }
}

/// Smithay's `delegate_seat!` and `delegate_virtual_keyboard_manager!`, with
/// `wl_seat` and `zwp_virtual_keyboard_v1` going through [`seat_request`]
/// and [`virtual_keyboard_request`].
macro_rules! delegate_seat_and_virtual_keyboard {
    ($ty: ty) => {
        const _: () = {
            use smithay::input::SeatState;
            use smithay::reexports::wayland_protocols_misc::zwp_virtual_keyboard_v1::server::{
                zwp_virtual_keyboard_manager_v1::ZwpVirtualKeyboardManagerV1,
                zwp_virtual_keyboard_v1::{self, ZwpVirtualKeyboardV1},
            };
            use smithay::reexports::wayland_server::backend::ClientId;
            use smithay::reexports::wayland_server::protocol::{
                wl_keyboard::WlKeyboard, wl_pointer::WlPointer, wl_seat::{self, WlSeat}, wl_touch::WlTouch,
            };
            use smithay::reexports::wayland_server::{
                delegate_dispatch, delegate_global_dispatch, Client, DataInit, Dispatch, DisplayHandle,
            };
            use smithay::wayland::seat::{
                KeyboardUserData, PointerUserData, SeatGlobalData, SeatUserData, TouchUserData,
            };
            use smithay::wayland::virtual_keyboard::{
                VirtualKeyboardManagerGlobalData, VirtualKeyboardManagerState, VirtualKeyboardUserData,
            };

            delegate_global_dispatch!($ty: [WlSeat: SeatGlobalData<$ty>] => SeatState<$ty>);
            delegate_dispatch!($ty: [WlPointer: PointerUserData<$ty>] => SeatState<$ty>);
            delegate_dispatch!($ty: [WlKeyboard: KeyboardUserData<$ty>] => SeatState<$ty>);
            delegate_dispatch!($ty: [WlTouch: TouchUserData<$ty>] => SeatState<$ty>);
            delegate_global_dispatch!(
                $ty: [ZwpVirtualKeyboardManagerV1: VirtualKeyboardManagerGlobalData] => VirtualKeyboardManagerState
            );
            delegate_dispatch!($ty: [ZwpVirtualKeyboardManagerV1: ()] => VirtualKeyboardManagerState);

            impl Dispatch<WlSeat, SeatUserData<$ty>> for $ty {
                fn request(
                    state: &mut $ty,
                    client: &Client,
                    resource: &WlSeat,
                    request: wl_seat::Request,
                    data: &SeatUserData<$ty>,
                    dh: &DisplayHandle,
                    data_init: &mut DataInit<'_, $ty>,
                ) {
                    $crate::keymapsync::seat_request(state, client, resource, request, data, dh, data_init);
                }

                fn destroyed(state: &mut $ty, client: ClientId, resource: &WlSeat, data: &SeatUserData<$ty>) {
                    <SeatState<$ty> as Dispatch<WlSeat, SeatUserData<$ty>, $ty>>::destroyed(
                        state, client, resource, data,
                    );
                }
            }

            impl Dispatch<ZwpVirtualKeyboardV1, VirtualKeyboardUserData<$ty>> for $ty {
                fn request(
                    state: &mut $ty,
                    client: &Client,
                    resource: &ZwpVirtualKeyboardV1,
                    request: zwp_virtual_keyboard_v1::Request,
                    data: &VirtualKeyboardUserData<$ty>,
                    dh: &DisplayHandle,
                    data_init: &mut DataInit<'_, $ty>,
                ) {
                    $crate::keymapsync::virtual_keyboard_request(
                        state, client, resource, request, data, dh, data_init,
                    );
                }

                fn destroyed(
                    state: &mut $ty,
                    client: ClientId,
                    resource: &ZwpVirtualKeyboardV1,
                    data: &VirtualKeyboardUserData<$ty>,
                ) {
                    <VirtualKeyboardManagerState as Dispatch<
                        ZwpVirtualKeyboardV1,
                        VirtualKeyboardUserData<$ty>,
                        $ty,
                    >>::destroyed(state, client, resource, data);
                }
            }
        };
    };
}
pub(crate) use delegate_seat_and_virtual_keyboard;

#[cfg(test)]
mod tests {
    //! A real libwayland server with Smithay's seat and virtual keyboard,
    //! and clients speaking the wire protocol by hand (the on-screen
    //! keyboard passes its keymap as a file descriptor): an app that binds
    //! its keyboard after the on-screen keyboard's first key gets that
    //! keyboard's keymap before its next key.

    use std::io::{Read, Write};
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
    use std::os::unix::net::UnixStream;
    use std::sync::Arc;

    use smithay::input::{Seat, SeatHandler, SeatState};
    use smithay::reexports::wayland_server::backend::{ClientData, ClientId, DisconnectReason};
    use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
    use smithay::reexports::wayland_server::{Client, Display};
    use smithay::utils::SERIAL_COUNTER;
    use smithay::wayland::compositor::{CompositorClientState, CompositorHandler, CompositorState};
    use smithay::wayland::virtual_keyboard::VirtualKeyboardManagerState;

    use super::{seat_xkb_config, KeymapSync, KeymapSyncHandler};

    #[test]
    fn the_sync_acts_once_per_keyboard_after_a_virtual_key() {
        let mut sync = KeymapSync::default();
        // Nothing typed on the on-screen keyboard: nothing to do.
        assert!(!sync.keyboard_bound());
        sync.virtual_key();
        sync.virtual_key();
        assert!(sync.keyboard_bound());
        // Once: the seat's keymap is active now.
        assert!(!sync.keyboard_bound());
        // A real key made the seat's keymap active by itself.
        sync.virtual_key();
        sync.seat_key();
        assert!(!sync.keyboard_bound());
        assert!(!KeymapSync::off().keyboard_bound());
    }

    #[derive(Default)]
    struct TestClient {
        compositor: CompositorClientState,
    }

    impl ClientData for TestClient {
        fn disconnected(&self, _: ClientId, _: DisconnectReason) {}
    }

    struct TestState {
        compositor: CompositorState,
        seat_state: SeatState<TestState>,
        seat: Seat<TestState>,
        _virtual_keyboard: VirtualKeyboardManagerState,
        sync: KeymapSync,
        surfaces: Vec<WlSurface>,
    }

    impl CompositorHandler for TestState {
        fn compositor_state(&mut self) -> &mut CompositorState {
            &mut self.compositor
        }

        fn client_compositor_state<'a>(&self, client: &'a Client) -> &'a CompositorClientState {
            &client.get_data::<TestClient>().unwrap().compositor
        }

        fn new_surface(&mut self, surface: &WlSurface) {
            self.surfaces.push(surface.clone());
        }

        fn commit(&mut self, _surface: &WlSurface) {}
    }

    impl SeatHandler for TestState {
        type KeyboardFocus = WlSurface;
        type PointerFocus = WlSurface;
        type TouchFocus = WlSurface;

        fn seat_state(&mut self) -> &mut SeatState<Self> {
            &mut self.seat_state
        }
    }

    impl KeymapSyncHandler for TestState {
        fn keymap_sync(&mut self) -> &mut KeymapSync {
            &mut self.sync
        }
    }

    smithay::delegate_compositor!(TestState);
    super::delegate_seat_and_virtual_keyboard!(TestState);

    /// A client end: requests in the wire format (object id, size << 16 |
    /// opcode, 32-bit words; strings length-prefixed, NUL, padded), a file
    /// descriptor alongside when a request carries one; events read back.
    struct Wire {
        socket: UnixStream,
        received: Vec<u8>,
    }

    enum Arg<'a> {
        U(u32),
        S(&'a str),
    }

    impl Wire {
        fn message(object: u32, opcode: u32, args: &[Arg]) -> Vec<u8> {
            let mut body = Vec::new();
            for arg in args {
                match arg {
                    Arg::U(v) => body.extend_from_slice(&v.to_ne_bytes()),
                    Arg::S(s) => {
                        body.extend_from_slice(&(s.len() as u32 + 1).to_ne_bytes());
                        body.extend_from_slice(s.as_bytes());
                        body.push(0);
                        while body.len() % 4 != 0 {
                            body.push(0);
                        }
                    }
                }
            }
            let mut message = Vec::new();
            message.extend_from_slice(&object.to_ne_bytes());
            message.extend_from_slice(&((((8 + body.len()) as u32) << 16) | opcode).to_ne_bytes());
            message.extend_from_slice(&body);
            message
        }

        fn send(&mut self, object: u32, opcode: u32, args: &[Arg]) {
            self.socket.write_all(&Self::message(object, opcode, args)).unwrap();
        }

        /// A request with one file descriptor (SCM_RIGHTS).
        fn send_fd(&mut self, object: u32, opcode: u32, args: &[Arg], fd: &OwnedFd) {
            let message = Self::message(object, opcode, args);
            let mut iov =
                libc::iovec { iov_base: message.as_ptr() as *mut libc::c_void, iov_len: message.len() };
            let space = unsafe { libc::CMSG_SPACE(std::mem::size_of::<libc::c_int>() as u32) } as usize;
            let mut control = vec![0u8; space];
            let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
            msg.msg_iov = &mut iov;
            msg.msg_iovlen = 1;
            msg.msg_control = control.as_mut_ptr() as *mut libc::c_void;
            msg.msg_controllen = space as _;
            unsafe {
                let cmsg = libc::CMSG_FIRSTHDR(&msg);
                (*cmsg).cmsg_level = libc::SOL_SOCKET;
                (*cmsg).cmsg_type = libc::SCM_RIGHTS;
                (*cmsg).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<libc::c_int>() as u32) as _;
                std::ptr::write_unaligned(libc::CMSG_DATA(cmsg) as *mut libc::c_int, fd.as_raw_fd());
                let sent = libc::sendmsg(self.socket.as_raw_fd(), &msg, 0);
                assert_eq!(sent, message.len() as isize, "sendmsg");
            }
        }

        /// Read what has arrived (the descriptors that came with it are
        /// closed by the kernel: only the messages count here).
        fn read(&mut self) {
            let mut chunk = [0u8; 65536];
            loop {
                match self.socket.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => self.received.extend_from_slice(&chunk[..n]),
                    Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(err) => panic!("{err}"),
                }
            }
        }

        /// Every event received so far: (object, opcode, arguments).
        fn events(&self) -> Vec<(u32, u32, Vec<u8>)> {
            let bytes = &self.received;
            let word = |at: usize| u32::from_ne_bytes(bytes[at..at + 4].try_into().unwrap());
            let mut out = Vec::new();
            let mut at = 0;
            while at + 8 <= bytes.len() {
                let (object, size_opcode) = (word(at), word(at + 4));
                let size = (size_opcode >> 16) as usize;
                out.push((object, size_opcode & 0xffff, bytes[at + 8..at + size].to_vec()));
                at += size;
            }
            out
        }

        /// The globals the registry (object 2) announced: (name, interface).
        fn globals(&mut self) -> Vec<(u32, String)> {
            self.read();
            self.events()
                .into_iter()
                .filter(|(object, opcode, _)| *object == 2 && *opcode == 0)
                .map(|(_, _, args)| {
                    let name = u32::from_ne_bytes(args[0..4].try_into().unwrap());
                    let len = u32::from_ne_bytes(args[4..8].try_into().unwrap()) as usize;
                    (name, String::from_utf8_lossy(&args[8..8 + len - 1]).into_owned())
                })
                .collect()
        }

        /// The keymap size each `wl_keyboard.keymap` event (opcode 0) on
        /// `keyboard` announced, and whether a key (opcode 3) came after it:
        /// the keymap in force at the first key.
        fn keymap_at_first_key(&self, keyboard: u32) -> (Vec<u32>, Option<u32>) {
            let mut sizes = Vec::new();
            for (object, opcode, args) in self.events() {
                if object != keyboard {
                    continue;
                }
                match opcode {
                    0 => sizes.push(u32::from_ne_bytes(args[4..8].try_into().unwrap())),
                    3 => return (sizes.clone(), sizes.last().copied()),
                    _ => {}
                }
            }
            (sizes, None)
        }
    }

    /// The on-screen keyboard's keymap: the seat's layout with one key of
    /// its own (phosh-osk-stub sends Return as such a key).
    const OSK_KEYMAP: &str = "xkb_keymap {\n\
        xkb_keycodes { include \"evdev\" };\n\
        xkb_types { include \"complete\" };\n\
        xkb_compat { include \"complete\" };\n\
        xkb_symbols { include \"pc+us\" key <I171> { [ Return ] }; };\n\
        };\n";
    /// Its key code (evdev; the keymap's <I171> less 8).
    const OSK_KEY: u32 = 163;

    fn keymap_fd(text: &str) -> (OwnedFd, u32) {
        let name = std::ffi::CString::new("osk-keymap").unwrap();
        let raw = unsafe { libc::memfd_create(name.as_ptr(), 0) };
        assert!(raw >= 0, "memfd_create");
        let fd = unsafe { OwnedFd::from_raw_fd(raw) };
        let mut bytes = text.as_bytes().to_vec();
        bytes.push(0);
        let mut file = std::fs::File::from(fd.try_clone().unwrap());
        file.write_all(&bytes).unwrap();
        (fd, bytes.len() as u32)
    }

    /// The scenario: app A's keyboard is there, the on-screen keyboard
    /// types a key into A, then app B binds its keyboard, gets the focus
    /// and is typed into. Returns the keymap sizes (the seat's, the
    /// on-screen keyboard's as A got it) and B's keymap at its first key.
    fn osk_key_into_a_later_app(sync: KeymapSync) -> (u32, u32, Option<u32>) {
        let mut display: Display<TestState> = Display::new().unwrap();
        let mut dh = display.handle();
        let mut seat_state = SeatState::new();
        let mut seat = seat_state.new_wl_seat(&dh, "seat0");
        seat.add_keyboard(seat_xkb_config(), 200, 25).unwrap();
        let mut state = TestState {
            compositor: CompositorState::new::<TestState>(&dh),
            seat_state,
            seat,
            _virtual_keyboard: VirtualKeyboardManagerState::new::<TestState, _>(&dh, |_| true),
            sync,
            surfaces: Vec::new(),
        };
        let connect = |dh: &mut smithay::reexports::wayland_server::DisplayHandle| {
            let (client, server) = UnixStream::pair().unwrap();
            client.set_nonblocking(true).unwrap();
            dh.insert_client(server, Arc::new(TestClient::default())).unwrap();
            Wire { socket: client, received: Vec::new() }
        };
        let mut osk = connect(&mut dh);
        let mut a = connect(&mut dh);
        let mut b = connect(&mut dh);
        let pump = |display: &mut Display<TestState>, state: &mut TestState| {
            display.dispatch_clients(state).unwrap();
            display.flush_clients().unwrap();
        };
        for wire in [&mut osk, &mut a, &mut b] {
            wire.send(1, 1, &[Arg::U(2)]); // wl_display.get_registry -> 2
        }
        pump(&mut display, &mut state);
        let globals = a.globals();
        osk.globals();
        b.globals();
        let name = |interface: &str| globals.iter().find(|g| g.1 == interface).unwrap().0;
        let bind = |wire: &mut Wire, interface: &str, version: u32, id: u32| {
            wire.send(2, 0, &[Arg::U(name(interface)), Arg::S(interface), Arg::U(version), Arg::U(id)]);
        };

        // App A: a surface (4) and a keyboard (6).
        bind(&mut a, "wl_compositor", 4, 3);
        a.send(3, 0, &[Arg::U(4)]);
        bind(&mut a, "wl_seat", 7, 5);
        a.send(5, 1, &[Arg::U(6)]);
        // The on-screen keyboard: a virtual keyboard (5) with its keymap.
        bind(&mut osk, "wl_seat", 7, 3);
        bind(&mut osk, "zwp_virtual_keyboard_manager_v1", 1, 4);
        osk.send(4, 0, &[Arg::U(3), Arg::U(5)]); // create_virtual_keyboard(seat, id)
        let (fd, size) = keymap_fd(OSK_KEYMAP);
        osk.send_fd(5, 0, &[Arg::U(1), Arg::U(size)], &fd); // keymap(xkb_v1, fd, size)
        pump(&mut display, &mut state);

        // A is focused, the on-screen keyboard types into it.
        let keyboard = state.seat.get_keyboard().unwrap();
        let a_surface = state.surfaces[0].clone();
        keyboard.set_focus(&mut state, Some(a_surface), SERIAL_COUNTER.next_serial());
        osk.send(5, 1, &[Arg::U(1000), Arg::U(OSK_KEY), Arg::U(1)]); // key(time, key, pressed)
        osk.send(5, 1, &[Arg::U(1010), Arg::U(OSK_KEY), Arg::U(0)]);
        pump(&mut display, &mut state);

        // App B starts now: its keyboard (6) is bound after that key.
        bind(&mut b, "wl_compositor", 4, 3);
        b.send(3, 0, &[Arg::U(4)]);
        bind(&mut b, "wl_seat", 7, 5);
        b.send(5, 1, &[Arg::U(6)]);
        pump(&mut display, &mut state);
        let b_surface = state.surfaces[1].clone();
        keyboard.set_focus(&mut state, Some(b_surface), SERIAL_COUNTER.next_serial());
        osk.send(5, 1, &[Arg::U(2000), Arg::U(OSK_KEY), Arg::U(1)]);
        osk.send(5, 1, &[Arg::U(2010), Arg::U(OSK_KEY), Arg::U(0)]);
        pump(&mut display, &mut state);

        a.read();
        b.read();
        let (a_sizes, a_at_key) = a.keymap_at_first_key(6);
        let seat_size = a_sizes[0];
        let osk_size = a_at_key.expect("A got the key");
        let (_, b_at_key) = b.keymap_at_first_key(6);
        drop(fd);
        (seat_size, osk_size, b_at_key)
    }

    #[test]
    fn an_app_started_after_an_osk_key_gets_the_osk_keymap() {
        let (seat, osk, b) = osk_key_into_a_later_app(KeymapSync::default());
        assert_ne!(seat, osk, "the two keymaps must be told apart");
        assert_eq!(b, Some(osk));
    }

    /// What Smithay does by itself (Pipeline, 2026-11-12): the key reaches
    /// the later app with the seat's keymap in force.
    #[test]
    fn without_it_the_later_app_has_the_seat_keymap() {
        let (seat, osk, b) = osk_key_into_a_later_app(KeymapSync::off());
        assert_ne!(seat, osk);
        assert_eq!(b, Some(seat));
    }
}
