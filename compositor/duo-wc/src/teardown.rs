//! A client going away, and what may not be sent while it does.
//!
//! When a client disconnects, libwayland tears it down in one call
//! (`wl_client_destroy`): first the client itself is marked gone, then its
//! objects are destroyed one by one, in object-id order, and each destructor
//! runs Smithay's handler and through it ours (`layer_destroyed`,
//! `toplevel_destroyed`, ...) at once. Until its turn comes, an object of
//! that client still looks alive, but its client can no longer be looked up,
//! and wayland-backend 0.3 then panics (`InvalidId`, `server_impl/mod.rs`
//! 1142, an `unwrap` of the sender's client) on any event that carries
//! another object as an argument: `wl_keyboard.enter/leave(surface)`,
//! `zwp_text_input_v3.enter/leave(surface)`, `wl_touch.down(surface)`. The
//! panic is inside an `extern "C"` destructor, so the compositor aborts.
//!
//! That is what killed duo-wc on 2026-11-07 14:38:18: duo-shell restarted
//! (SIGTERM) with the app grid open and its search field focused; one of its
//! layer surfaces was destroyed first, `layer_destroyed` moved the keyboard
//! focus, and the `leave` went to the still-undestroyed grid surface through
//! duo-shell's still-undestroyed keyboard (or text-input) object.
//!
//! The rules here: a focus change that would touch a client being torn down
//! is not made now ([`set_keyboard_focus`] says so, the caller tries again
//! from an idle callback, when the client is gone for good and every send to
//! it is a harmless no-op), and a surface of such a client is never picked as
//! a new focus ([`reachable`]).

use smithay::input::keyboard::KeyboardHandle;
use smithay::input::SeatHandler;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::reexports::wayland_server::Resource;
use smithay::utils::Serial;

/// The surface's client is being torn down right now: the surface is not
/// destroyed yet, but its client is already gone (see the module docs).
pub fn client_dying(surface: &WlSurface) -> bool {
    surface.is_alive() && surface.client().is_none()
}

/// Events may go to this surface: it is alive and its client is too.
pub fn reachable(surface: &WlSurface) -> bool {
    surface.client().is_some()
}

/// Move the keyboard focus to `target`, unless the surface that has it or the
/// one that would get it belongs to a client being torn down (the `leave` or
/// the `enter` would abort the compositor). Returns whether the focus was set;
/// on `false` nothing was sent and the caller tries again later.
pub fn set_keyboard_focus<D>(
    keyboard: &KeyboardHandle<D>,
    state: &mut D,
    target: Option<WlSurface>,
    serial: Serial,
) -> bool
where
    D: SeatHandler<KeyboardFocus = WlSurface> + 'static,
{
    let current = keyboard.current_focus();
    if current.as_ref().is_some_and(client_dying) || target.as_ref().is_some_and(client_dying) {
        return false;
    }
    keyboard.set_focus(state, target, serial);
    true
}

#[cfg(test)]
mod tests {
    //! A real libwayland server and a client speaking the wire protocol by
    //! hand (so the object ids, and with them the order of destruction, are
    //! the test's): a surface destroyed during the client's teardown moves
    //! the keyboard focus away from another surface of the same client.

    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;
    use std::sync::Arc;

    use smithay::input::keyboard::XkbConfig;
    use smithay::input::{Seat, SeatHandler, SeatState};
    use smithay::reexports::wayland_server::backend::{ClientData, ClientId, DisconnectReason};
    use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
    use smithay::reexports::wayland_server::{Client, Display, Resource};
    use smithay::utils::SERIAL_COUNTER;
    use smithay::wayland::compositor::{CompositorClientState, CompositorHandler, CompositorState};

    use super::{client_dying, reachable, set_keyboard_focus};

    #[derive(Default)]
    struct TestClient {
        compositor: CompositorClientState,
    }

    impl ClientData for TestClient {
        fn disconnected(&self, _: ClientId, _: DisconnectReason) {}
    }

    /// What the destructor hook saw.
    #[derive(Debug, Default, PartialEq)]
    struct Seen {
        /// The focused surface's client was being torn down.
        focus_dying: bool,
        /// ... and so it was not reachable.
        focus_reachable: bool,
        /// The guarded focus change went through.
        focus_set: Option<bool>,
    }

    struct TestState {
        compositor: CompositorState,
        seat_state: SeatState<TestState>,
        seat: Seat<TestState>,
        surfaces: Vec<WlSurface>,
        /// Move the focus unguarded (what duo-wc did before): aborts.
        unguarded: bool,
        seen: Option<Seen>,
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

        // Runs inside wl_client_destroy for a client that hung up, like
        // duo-wc's layer_destroyed: the focus moves somewhere else.
        fn destroyed(&mut self, surface: &WlSurface) {
            if self.seen.is_some() || Some(surface) != self.surfaces.first() {
                return;
            }
            let keyboard = self.seat.get_keyboard().unwrap();
            let focus = keyboard.current_focus().unwrap();
            let mut seen = Seen {
                focus_dying: client_dying(&focus),
                focus_reachable: reachable(&focus),
                focus_set: None,
            };
            let serial = SERIAL_COUNTER.next_serial();
            if self.unguarded {
                keyboard.set_focus(self, None, serial);
            } else {
                seen.focus_set = Some(set_keyboard_focus(&keyboard, self, None, serial));
            }
            self.seen = Some(seen);
        }
    }

    impl SeatHandler for TestState {
        type KeyboardFocus = WlSurface;
        type PointerFocus = WlSurface;
        type TouchFocus = WlSurface;

        fn seat_state(&mut self) -> &mut SeatState<Self> {
            &mut self.seat_state
        }
    }

    smithay::delegate_compositor!(TestState);
    smithay::delegate_seat!(TestState);

    /// Writes requests in the wire format: object id, size << 16 | opcode,
    /// the arguments (32-bit words; strings length-prefixed, NUL, padded).
    struct Wire(UnixStream);

    enum Arg<'a> {
        U(u32),
        S(&'a str),
    }

    impl Wire {
        fn send(&mut self, object: u32, opcode: u32, args: &[Arg]) {
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
            self.0.write_all(&message).unwrap();
        }

        /// The globals the registry (object 2) announced: (name, interface).
        fn globals(&mut self) -> Vec<(u32, String)> {
            let mut bytes = Vec::new();
            let mut chunk = [0u8; 4096];
            loop {
                match self.0.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => bytes.extend_from_slice(&chunk[..n]),
                    Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(err) => panic!("{err}"),
                }
            }
            let word = |at: usize| u32::from_ne_bytes(bytes[at..at + 4].try_into().unwrap());
            let mut globals = Vec::new();
            let mut at = 0;
            while at + 8 <= bytes.len() {
                let (object, size_opcode) = (word(at), word(at + 4));
                let size = (size_opcode >> 16) as usize;
                if object == 2 && size_opcode & 0xffff == 0 {
                    let name = word(at + 8);
                    let len = word(at + 12) as usize;
                    let interface = String::from_utf8_lossy(&bytes[at + 16..at + 16 + len - 1]);
                    globals.push((name, interface.into_owned()));
                }
                at += size;
            }
            globals
        }
    }

    /// Run the scenario; returns what the destructor hook saw.
    fn teardown_moves_the_focus(unguarded: bool) -> Option<Seen> {
        let mut display: Display<TestState> = Display::new().unwrap();
        let mut dh = display.handle();
        let mut seat_state = SeatState::new();
        let mut seat = seat_state.new_wl_seat(&dh, "seat0");
        seat.add_keyboard(XkbConfig::default(), 200, 25).unwrap();
        let mut state = TestState {
            compositor: CompositorState::new::<TestState>(&dh),
            seat_state,
            seat,
            surfaces: Vec::new(),
            unguarded,
            seen: None,
        };
        let (client, server) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        dh.insert_client(server, Arc::new(TestClient::default())).unwrap();
        let mut wire = Wire(client);
        let pump = |display: &mut Display<TestState>, state: &mut TestState| {
            display.dispatch_clients(state).unwrap();
            display.flush_clients().unwrap();
        };

        wire.send(1, 1, &[Arg::U(2)]); // wl_display.get_registry -> 2
        pump(&mut display, &mut state);
        let globals = wire.globals();
        let name = |interface: &str| globals.iter().find(|g| g.1 == interface).unwrap().0;
        // The surface destroyed first (id 4) comes before the keyboard (6) and
        // the focused surface (7): both are still alive in its destructor.
        let bind = |wire: &mut Wire, name: u32, interface: &str, version: u32, id: u32| {
            wire.send(2, 0, &[Arg::U(name), Arg::S(interface), Arg::U(version), Arg::U(id)]);
        };
        bind(&mut wire, name("wl_compositor"), "wl_compositor", 4, 3);
        wire.send(3, 0, &[Arg::U(4)]); // create_surface -> 4
        bind(&mut wire, name("wl_seat"), "wl_seat", 5, 5);
        wire.send(5, 1, &[Arg::U(6)]); // get_keyboard -> 6
        wire.send(3, 0, &[Arg::U(7)]); // create_surface -> 7
        pump(&mut display, &mut state);
        assert_eq!(state.surfaces.len(), 2);

        let keyboard = state.seat.get_keyboard().unwrap();
        let focused = state.surfaces[1].clone();
        keyboard.set_focus(&mut state, Some(focused.clone()), SERIAL_COUNTER.next_serial());
        pump(&mut display, &mut state);
        assert!(reachable(&focused) && !client_dying(&focused));

        // The client hangs up (duo-shell restarted): libwayland destroys it
        // inside this dispatch.
        drop(wire);
        pump(&mut display, &mut state);
        assert!(!focused.is_alive());

        // Afterwards the deferred focus change is harmless.
        assert!(set_keyboard_focus(&keyboard, &mut state, None, SERIAL_COUNTER.next_serial()));
        state.seen.take()
    }

    #[test]
    fn a_focus_change_inside_a_dying_clients_teardown_waits() {
        let seen = teardown_moves_the_focus(false);
        assert_eq!(seen, Some(Seen { focus_dying: true, focus_reachable: false, focus_set: Some(false) }));
    }

    /// What duo-wc did before 2026-11-07: the `leave` to the dying client's
    /// surface panics in wayland-backend's destructor and the process aborts
    /// (`cargo test -- --ignored --exact teardown::tests::unguarded_focus_change_aborts`;
    /// the test binary dies of SIGABRT while wayland-backend 0.3.16 is in use).
    #[test]
    #[ignore = "aborts the test process: run alone to see the panic it guards against"]
    fn unguarded_focus_change_aborts() {
        teardown_moves_the_focus(true);
    }
}
