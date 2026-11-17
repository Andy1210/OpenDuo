//! The Smithay protocol handlers of [`DuoWc`] (the `*Handler` traits) and
//! the `delegate_*!` dispatch for the globals they serve. A child module of
//! `state`, so the handlers reach its private helpers.

use super::*;

impl CompositorHandler for DuoWc {
    fn compositor_state(&mut self) -> &mut CompositorState {
        &mut self.compositor_state
    }

    fn client_compositor_state<'a>(&self, client: &'a Client) -> &'a CompositorClientState {
        // Xwayland's client was made by Smithay (crate::xwayland).
        if let Some(xwayland) = client.get_data::<smithay::xwayland::XWaylandClientData>() {
            return &xwayland.compositor_state;
        }
        &client.get_data::<ClientState>().unwrap().compositor_state
    }

    fn new_surface(&mut self, surface: &WlSurface) {
        // wp_commit_timing_v1: an update held for a time is let go by a
        // timer of its own (crate::frames). Added before any commit timer
        // object exists, so it runs before Smithay's hook (which takes the
        // timestamp away).
        smithay::wayland::compositor::add_pre_commit_hook::<Self, _>(surface, |state, _, surface| {
            state.commit_timer_requested(surface);
        });
    }

    fn commit(&mut self, surface: &WlSurface) {
        on_commit_buffer_handler::<Self>(surface);
        if is_sync_subsurface(surface) {
            return;
        }

        let mut root = surface.clone();
        while let Some(parent) = get_parent(&root) {
            root = parent;
        }
        if !self.rotation_anim.anims.is_empty() {
            self.rotation_note_commit(&root);
        }
        // Both panels run at the same scale, so every surface is told the same:
        // the fractional one for clients that bind wp_fractional_scale_v1, the
        // next integer as buffer scale for the rest (scaled down on render).
        // Windows, layer surfaces (the shell, the keyboard) and popups alike.
        // Xwayland draws at the client scale already (crate::xwayland):
        // one buffer pixel per X pixel.
        let scale =
            if crate::xwayland::is_xwayland_surface(&root) { 1.0 } else { self.scale_for_surface(&root) };
        if let Some(window) = self.window_for_surface(&root) {
            window.on_commit();
            window.with_surfaces(|surface, states| {
                tell_scale(surface, states, scale);
            });
            update_foreign_toplevel(&window);
            // Android's windows: the keyboard's place, a task Android moves.
            self.android_commit(&window);
            // Asked to close and drawing: it answers (a dialog in it).
            self.close_answered(window_data(&window).id);
            // Drawn at a new size: a dialog stays centred, a window bigger
            // than its panel is scaled into it (crate::dialog).
            self.refit(&window);
            // A desk window drawn at the size it was re-placed at: the
            // pointer over it is told again (crate::pointer).
            if !self.pointer.refocus.is_empty() {
                self.pointer_window_committed(window_data(&window).id, window.geometry().size);
            }
            // Its first picture: it grows in (not Android's keyboard: it
            // just comes).
            if !*window_data(&window).opened.borrow() && window.bbox().size.w > 0 {
                *window_data(&window).opened.borrow_mut() = true;
                if !self.is_accessory(&window) {
                    self.animate_open(&window);
                    // In front on its panel: the shell's app grid and quick
                    // menu there make way (crate::front).
                    self.announce_front(&window, "opened");
                }
            }
            // Half of a split: its minimum size may have changed, which moves
            // the divider (configures go out only for sizes that change).
            let id = window_data(&window).id;
            if self.splits.iter().any(|s| s.has(id)) {
                self.place_all();
            }
            // Hidden (suspended): what it waits for comes from the slow
            // tick (crate::frames).
            self.pacing_committed(&window);
        } else {
            smithay::wayland::compositor::with_surface_tree_downward(
                &root,
                (),
                |_, _, _| smithay::wayland::compositor::TraversalAction::DoChildren(()),
                |surface, states, _| tell_scale(surface, states, scale),
                |_, _, _| true,
            );
        }

        self.ambient_committed(&root);
        self.popups.commit(surface);
        if let Some(PopupKind::Xdg(popup)) = self.popups.find_popup(surface) {
            // A popup (a GTK menu, a popover) waits for its first configure.
            if !popup.is_initial_configure_sent() {
                if let Err(err) = popup.send_configure() {
                    debug!(?err, "failed to configure a popup");
                }
            }
        }
        self.ensure_initial_configure(surface);
        self.queue_redraw_all();
    }
}

impl FractionalScaleHandler for DuoWc {
    fn new_fractional_scale(&mut self, surface: WlSurface) {
        let scale = self.scale_for_surface(&surface);
        with_states(&surface, |states| {
            with_fractional_scale(states, |fractional| fractional.set_preferred_scale(scale));
        });
    }
}

impl BufferHandler for DuoWc {
    fn buffer_destroyed(
        &mut self,
        _buffer: &smithay::reexports::wayland_server::protocol::wl_buffer::WlBuffer,
    ) {
    }
}

impl ShmHandler for DuoWc {
    fn shm_state(&self) -> &ShmState {
        &self.shm_state
    }
}

impl OutputHandler for DuoWc {}

impl SeatHandler for DuoWc {
    type KeyboardFocus = WlSurface;
    type PointerFocus = WlSurface;
    type TouchFocus = WlSurface;

    fn seat_state(&mut self) -> &mut SeatState<Self> {
        &mut self.seat_state
    }

    fn cursor_image(&mut self, _seat: &Seat<Self>, image: smithay::input::pointer::CursorImageStatus) {
        // Drawn by crate::pointer on the output the pointer is on.
        self.pointer.image = Some(image);
        if let Some(output) = self.pointer_output() {
            self.queue_redraw_output(&output);
        }
    }

    fn focus_changed(&mut self, _seat: &Seat<Self>, focused: Option<&WlSurface>) {
        // The X input focus follows onto an X11 window (crate::xwayland).
        self.xwl_focus_changed(focused);
        // The on-screen keyboard follows the focus to its panel; after this
        // dispatch, not inside the seat's focus change.
        self.loop_handle.insert_idle(|state| state.place_osk());
    }
}

impl SelectionHandler for DuoWc {
    type SelectionUserData = ();

    // The clipboard between Wayland and X11 (crate::xwayland): a Wayland
    // copy is offered to X11 ...
    fn new_selection(
        &mut self,
        ty: smithay::wayland::selection::SelectionTarget,
        source: Option<smithay::wayland::selection::SelectionSource>,
        _seat: Seat<Self>,
    ) {
        if ty != smithay::wayland::selection::SelectionTarget::Clipboard {
            return;
        }
        if let Some(xwm) = self.xwl.xwm.as_mut() {
            if let Err(err) = xwm.new_selection(ty, source.map(|s| s.mime_types())) {
                debug!(?err, "the clipboard for X11");
            }
        }
    }

    // ... and an X11 copy (the seat's selection is then the compositor's)
    // is read from X11 for a Wayland app.
    fn send_selection(
        &mut self,
        ty: smithay::wayland::selection::SelectionTarget,
        mime_type: String,
        fd: std::os::fd::OwnedFd,
        _seat: Seat<Self>,
        _user_data: &(),
    ) {
        if let Some(xwm) = self.xwl.xwm.as_mut() {
            if let Err(err) = xwm.send_selection(ty, mime_type, fd) {
                debug!(?err, "the X11 clipboard for Wayland");
            }
        }
    }
}

// Xwayland's window manager needs it; there is no drag and drop.
impl smithay::input::dnd::DndGrabHandler for DuoWc {}

impl DataDeviceHandler for DuoWc {
    fn data_device_state(&mut self) -> &mut DataDeviceState {
        &mut self.data_device_state
    }
}

impl smithay::wayland::selection::ext_data_control::DataControlHandler for DuoWc {
    fn data_control_state(&mut self) -> &mut smithay::wayland::selection::ext_data_control::DataControlState {
        &mut self.data_control_state
    }
}

impl smithay::wayland::selection::wlr_data_control::DataControlHandler for DuoWc {
    fn data_control_state(&mut self) -> &mut smithay::wayland::selection::wlr_data_control::DataControlState {
        &mut self.wlr_data_control_state
    }
}

// Nothing to drag yet; the default refuses the grab.
impl WaylandDndGrabHandler for DuoWc {}

/// Decorations are the compositor's, and there are none: every window is
/// maximized, so a title bar would only take room on a small panel.
impl XdgDecorationHandler for DuoWc {
    fn new_decoration(&mut self, toplevel: ToplevelSurface) {
        decorate(&toplevel);
        self.desk_decorate(&toplevel);
    }

    fn request_mode(&mut self, toplevel: ToplevelSurface, _mode: DecorationMode) {
        decorate(&toplevel);
        self.desk_decorate(&toplevel);
    }

    fn unset_mode(&mut self, toplevel: ToplevelSurface) {
        decorate(&toplevel);
    }
}

fn decorate(toplevel: &ToplevelSurface) {
    toplevel.with_pending_state(|state| {
        state.decoration_mode = Some(DecorationMode::ServerSide);
    });
    if toplevel.is_initial_configure_sent() {
        toplevel.send_pending_configure();
    }
}

impl XdgShellHandler for DuoWc {
    fn xdg_shell_state(&mut self) -> &mut XdgShellState {
        &mut self.xdg_shell_state
    }

    fn new_toplevel(&mut self, surface: ToplevelSurface) {
        // A new window is what the user wants to see now.
        self.overview_close("new window");
        // A dialog's parent is not known yet (xdg_toplevel.set_parent, and
        // xdg-foreign's set_parent_of, come after get_toplevel): it is put
        // on its parent's panel before its first configure
        // (settle_dialog, from ensure_initial_configure).
        let Some((output, hint)) = self.output_for_new_window() else {
            warn!("a toplevel arrived before any output; it will not be shown");
            return;
        };
        let window = Window::new_wayland_window(surface);
        let id = self.next_window_id;
        self.next_window_id += 1;
        let (placement, saved) = crate::pose::new_window(&self.pose, Placement::Output(output.name()));
        let loc = self.placement_rect(&placement, false).map(|r| r.loc).unwrap_or_default();
        info!(id, output = output.name(), ?placement, "new toplevel");
        let foreign = self.foreign_toplevels.new_toplevel::<DuoWc>(String::new(), String::new());
        window.user_data().insert_if_missing(|| WindowData::new(id, placement, saved, foreign, hint));
        // Mapped on its output at once, so the surface enters that output (and
        // learns its scale) before it draws its first buffer; the size and the
        // maximized state go out in the first configure.
        self.space.map_element(window.clone(), loc, true);
        self.place(&window);
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    fn parent_changed(&mut self, surface: ToplevelSurface) {
        let Some(window) = self.find_toplevel(&surface) else {
            return;
        };
        // Before the first configure: decided then, with everything known.
        if !surface.is_initial_configure_sent() {
            return;
        }
        info!(id = window_data(&window).id, parent = surface.parent().is_some(), "window's parent changed");
        if self.is_dialog(&window) {
            self.settle_dialog(&window);
        } else {
            self.place(&window);
            self.queue_redraw_all();
        }
    }

    fn new_popup(&mut self, surface: PopupSurface, _positioner: PositionerState) {
        // Fitted before its first configure, which carries the geometry.
        self.unconstrain_popup(&surface);
        if let Err(err) = self.popups.track_popup(PopupKind::Xdg(surface)) {
            warn!(?err, "failed to track a popup");
        }
    }

    fn grab(&mut self, _surface: PopupSurface, _seat: wl_seat::WlSeat, _serial: Serial) {}

    fn reposition_request(&mut self, surface: PopupSurface, positioner: PositionerState, token: u32) {
        surface.with_pending_state(|state| {
            state.geometry = positioner.get_geometry();
            state.positioner = positioner;
        });
        self.unconstrain_popup(&surface);
        surface.send_repositioned(token);
    }

    fn toplevel_destroyed(&mut self, surface: ToplevelSurface) {
        if let Some(window) = self.find_toplevel(&surface) {
            self.window_gone(&window, "toplevel");
            return;
        }
        self.after_destroyed("toplevel", false);
    }

    fn fullscreen_request(&mut self, surface: ToplevelSurface, wl_output: Option<WlOutput>) {
        let Some(window) = self.find_toplevel(&surface) else {
            return;
        };
        // Full screen where the window is: its panel, or both panels when it
        // spans; out of it, it is where it was. The output a client names is
        // the one it thinks it is on (Qt names one panel for a spanning
        // window: the video went to that panel only, and the window stayed
        // there afterwards), so it moves nothing.
        let id = window_data(&window).id;
        let asked = wl_output.as_ref().and_then(Output::from_resource).map(|o| o.name());
        // An external display named on purpose (a presentation, a player
        // told which screen): the window goes there (crate::extout). Only a
        // display: a client naming a panel only says where it thinks it is.
        if let Some(name) =
            asked.as_ref().filter(|n| crate::extout::is_external(n) && self.output_by_name(n).is_some())
        {
            if *window_data(&window).placement.borrow() != Placement::Output(name.clone()) {
                info!(id, output = name, "full screen on the display it names");
                self.ext_remember_home(&window);
                *window_data(&window).placement.borrow_mut() = Placement::Output(name.clone());
                *window_data(&window).saved.borrow_mut() = None;
            }
        }
        info!(id, ?asked, placement = ?*window_data(&window).placement.borrow(), "window full screen where it is");
        *window_data(&window).fullscreen.borrow_mut() = true;
        self.place(&window);
        self.queue_redraw_all();
    }

    fn unfullscreen_request(&mut self, surface: ToplevelSurface) {
        let Some(window) = self.find_toplevel(&surface) else {
            return;
        };
        info!(id = window_data(&window).id, "window out of full screen");
        *window_data(&window).fullscreen.borrow_mut() = false;
        self.place(&window);
        self.queue_redraw_all();
    }

    // Everything is maximized already; a request either way is answered with the
    // state it is in, so the client is not left waiting for a configure. An
    // unmaximize that follows a touch on the window is a double tap on its
    // title bar (GTK's "toggle maximized"): one panel <-> both panels.
    fn maximize_request(&mut self, surface: ToplevelSurface) {
        if let Some(window) = self.find_toplevel(&surface).filter(|w| self.on_desk(w)) {
            let id = window_data(&window).id;
            if let Some(frame) = self.desk.frames.get_mut(&id) {
                frame.snap = Some(crate::desk::Snap::Max);
            }
            self.place(&window);
            self.queue_redraw_all();
        }
        if surface.is_initial_configure_sent() {
            surface.send_configure();
        }
    }

    fn unmaximize_request(&mut self, surface: ToplevelSurface) {
        if let Some(window) = self.find_toplevel(&surface).filter(|w| self.on_desk(w)) {
            // On a display: back to its own frame.
            let id = window_data(&window).id;
            if let Some(frame) = self.desk.frames.get_mut(&id) {
                frame.snap = None;
            }
            self.place(&window);
            self.queue_redraw_all();
        } else if let Some(window) = self.find_toplevel(&surface) {
            self.client_toggle_request(&window);
        }
        if surface.is_initial_configure_sent() {
            surface.send_configure();
        }
    }

    // A client's title bar is dragged: the window is carried
    // (crate::windrag).
    fn move_request(&mut self, surface: ToplevelSurface, _seat: wl_seat::WlSeat, serial: Serial) {
        if let Some(window) = self.find_toplevel(&surface) {
            // The pen's tip on a drawing app's title bar (crate::tablet), the
            // pointer's press (a mouse on a title bar), else a finger's.
            if crate::tablet::move_request(self, &window, serial) {
                return;
            }
            if !self.pointer_move_request(&window, serial) {
                self.client_move_request(&window, serial);
            }
        }
    }

    // A border dragged with the pointer: a window on a display resizes
    // (crate::desk); everything else is maximized and stays so.
    fn resize_request(
        &mut self,
        surface: ToplevelSurface,
        _seat: wl_seat::WlSeat,
        serial: Serial,
        edges: xdg_toplevel::ResizeEdge,
    ) {
        if let Some(window) = self.find_toplevel(&surface) {
            let edges = crate::desk::Edges::from_xdg(u32::from(edges));
            if !self.pointer_resize_request(&window, serial, edges) {
                // A finger on its border (a desk window on laptop mode's panel).
                self.touch_resize_request(&window, serial, edges);
            }
        }
    }

    fn minimize_request(&mut self, surface: ToplevelSurface) {
        // A client asking to be minimized is set aside alone (home is for
        // a whole panel).
        if let Some(window) = self.find_toplevel(&surface) {
            self.minimize_window(&window, "the client minimized itself");
        }
    }
}

impl WlrLayerShellHandler for DuoWc {
    fn shell_state(&mut self) -> &mut WlrLayerShellState {
        &mut self.layer_shell_state
    }

    fn new_layer_surface(
        &mut self,
        surface: WlrLayerSurface,
        wl_output: Option<WlOutput>,
        _layer: Layer,
        namespace: String,
    ) {
        let asked = wl_output.as_ref().and_then(Output::from_resource);
        let output = if OSK_NAMESPACES.contains(&namespace.as_str()) {
            // The keyboard goes where the text field is, not where it asked.
            self.osk_target_output().or(asked)
        } else {
            asked.or_else(|| self.active_output())
        };
        let Some(output) = output else {
            warn!(namespace, "a layer surface arrived before any output");
            return;
        };
        info!(namespace, output = output.name(), layer = ?_layer, "new layer surface");
        if OSK_NAMESPACES.contains(&namespace.as_str()) {
            // squeekboard's blink when it starts, with no text field: not
            // shown (crate::oskstart). Decided before it is mapped, so it
            // never takes room from the windows either.
            self.osk_start_check(surface.wl_surface());
        }
        let mut layers = layer_map_for_output(&output);
        if let Err(err) = layers.map_layer(&LayerSurface::new(surface, namespace)) {
            warn!(?err, "failed to map a layer surface");
        }
        drop(layers);
        self.refresh_keyboard_focus();
        self.queue_redraw_all();
    }

    /// A layer surface's popup (a GTK popover on the home screen, the
    /// dock): its parent is set only now (xdg_surface.get_popup came
    /// without one), so it is fitted into its panel here, before its first
    /// configure. Fitted in XdgShellHandler::new_popup it had no root yet,
    /// and a popover at the panel's edge ran off the glass.
    fn new_popup(&mut self, _parent: WlrLayerSurface, popup: PopupSurface) {
        self.unconstrain_popup(&popup);
    }

    fn layer_destroyed(&mut self, surface: WlrLayerSurface) {
        for output in self.outputs.clone() {
            let mut layers = layer_map_for_output(&output);
            let layer = layers.layers().find(|layer| layer.layer_surface() == &surface).cloned();
            if let Some(layer) = layer {
                layers.unmap_layer(&layer);
            }
        }
        // The keyboard went for real: nothing is put away any more.
        let osk_left = self
            .outputs
            .iter()
            .any(|o| layer_map_for_output(o).layers().any(|l| OSK_NAMESPACES.contains(&l.namespace())));
        if !osk_left {
            self.osk_away.away = None;
        }
        self.osk_start_gone(surface.wl_surface());
        self.after_destroyed("layer surface", true);
    }
}

/// Input-method popups (candidate lists) are not shown yet: squeekboard has
/// none. The text field's place is still told, so one could be placed later.
impl InputMethodHandler for DuoWc {
    fn new_popup(&mut self, _surface: InputPopupSurface) {
        debug!("input-method popup (not shown)");
    }

    fn dismiss_popup(&mut self, _surface: InputPopupSurface) {}

    fn popup_repositioned(&mut self, _surface: InputPopupSurface) {}

    fn parent_geometry(&self, parent: &WlSurface) -> Rectangle<i32, Logical> {
        self.window_for_surface(parent).and_then(|w| self.space.element_geometry(&w)).unwrap_or_default()
    }
}

impl crate::keymapsync::KeymapSyncHandler for DuoWc {
    fn keymap_sync(&mut self) -> &mut crate::keymapsync::KeymapSync {
        &mut self.keymap_sync
    }
}

/// xdg-foreign (v2): an app exports its window as a handle, another client
/// (the portal's file chooser, xdg-desktop-portal-gtk) imports it and makes
/// it its window's parent; Smithay sets the parent and tells
/// `XdgShellHandler::parent_changed`. GTK 3 and 4 speak v2.
impl smithay::wayland::xdg_foreign::XdgForeignHandler for DuoWc {
    fn xdg_foreign_state(&mut self) -> &mut smithay::wayland::xdg_foreign::XdgForeignState {
        &mut self.xdg_foreign_state
    }
}

/// xdg-activation: a client hands over the token it got (from a launcher's
/// launch context, a notification) to bring a window forward. Every token is
/// honoured while it is fresh (one user, no focus stealing to fear yet). A
/// token made on another panel (the launcher's) moves the window there.
impl XdgActivationHandler for DuoWc {
    fn activation_state(&mut self) -> &mut XdgActivationState {
        &mut self.activation_state
    }

    fn request_activation(
        &mut self,
        token: XdgActivationToken,
        token_data: XdgActivationTokenData,
        surface: WlSurface,
    ) {
        self.activation_state.remove_token(&token);
        if token_data.timestamp.elapsed() > ACTIVATION_TOKEN_MAX_AGE {
            debug!("activation token too old, ignored");
            return;
        }
        let Some(window) = self.window_for_surface(&surface) else {
            return;
        };
        // Where it goes (crate::front): the launcher's word first (a window
        // its hint placed stays; a hint still waiting says where a running
        // app goes), the token's surface's panel only without one.
        let token = token_data
            .surface
            .as_ref()
            .filter(|s| self.window_for_surface(s).as_ref() != Some(&window))
            .and_then(|s| self.output_of_surface(s))
            .map(|o| o.name());
        let hint = self.launch_hint.as_ref().filter(|h| Instant::now() < h.until).map(|h| h.output.clone());
        // (Its own first activation: GTK activates a launched window at
        // once with the launch's token; long after, it is not the launch.)
        let launched = window_data(&window).launched.replace(false)
            && window_data(&window).mapped_at.elapsed() < Duration::from_secs(10);
        let current = match &*window_data(&window).placement.borrow() {
            Placement::Output(name) => Some(name.clone()),
            Placement::Span => None,
        };
        let in_use = self.in_use();
        let in_use: Vec<&str> = in_use.iter().map(String::as_str).collect();
        let wanted = crate::front::activation_panel(
            current.as_deref(),
            launched,
            hint.as_deref(),
            token.as_deref(),
            &in_use,
        );
        if hint.is_some() && current.is_some() && !launched {
            // The launch it was waiting for is this one.
            self.launch_hint = None;
        }
        if launched && token.is_some() && token != current {
            info!(
                id = window_data(&window).id,
                token = token.as_deref().unwrap_or_default(),
                "activation: the window stays where the launcher put it"
            );
        }
        if let Some(wanted) = wanted {
            info!(id = window_data(&window).id, to = wanted, "activation moves the window");
            self.set_placement(&window, Placement::Output(wanted));
        }
        info!(id = window_data(&window).id, "window activated");
        self.activate_window(&window);
        self.announce_front(&window, "activated");
    }
}

impl ForeignToplevelListHandler for DuoWc {
    fn foreign_toplevel_list_state(&mut self) -> &mut ForeignToplevelListState {
        &mut self.foreign_toplevels
    }
}

impl DmabufHandler for DuoWc {
    fn dmabuf_state(&mut self) -> &mut DmabufState {
        &mut self.dmabuf_state
    }

    fn dmabuf_imported(
        &mut self,
        _global: &DmabufGlobal,
        dmabuf: smithay::backend::allocator::dmabuf::Dmabuf,
        notifier: ImportNotifier,
    ) {
        let started = Instant::now();
        match self.tty.import_dmabuf(&dmabuf) {
            Ok(()) => {
                // Held through a rotation's turn (crate::rotanim::BufferHold).
                let ms = started.elapsed().as_secs_f64() * 1000.0;
                self.rotation_anim.hold.track(&dmabuf, ms);
                let _ = notifier.successful::<DuoWc>();
            }
            Err(err) => {
                debug!(?err, "rejected a client dmabuf");
                notifier.failed();
            }
        }
    }
}

delegate_compositor!(DuoWc);
delegate_shm!(DuoWc);
smithay::delegate_viewporter!(DuoWc);
smithay::delegate_presentation!(DuoWc);
smithay::delegate_fifo!(DuoWc);
smithay::delegate_commit_timing!(DuoWc);
smithay::delegate_fractional_scale!(DuoWc);
smithay::delegate_single_pixel_buffer!(DuoWc);
// The seat and the virtual keyboard through crate::keymapsync (an app
// started after an on-screen key gets the on-screen keyboard's keymap).
crate::keymapsync::delegate_seat_and_virtual_keyboard!(DuoWc);
delegate_data_device!(DuoWc);
delegate_output!(DuoWc);
delegate_xdg_shell!(DuoWc);
delegate_xdg_decoration!(DuoWc);
delegate_layer_shell!(DuoWc);
delegate_dmabuf!(DuoWc);
// zwp_text_input_v3 is dispatched by crate::textspy (it notes the requests
// and hands them to Smithay's TextInputManagerState).
smithay::delegate_input_method_manager!(DuoWc);
smithay::delegate_xdg_activation!(DuoWc);
smithay::delegate_xdg_foreign!(DuoWc);
smithay::delegate_foreign_toplevel_list!(DuoWc);
smithay::delegate_ext_data_control!(DuoWc);
smithay::delegate_data_control!(DuoWc);
smithay::delegate_xwayland_shell!(DuoWc);
