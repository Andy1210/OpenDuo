//! duo-wc: the Wayland compositor for the Surface Duo 2.
//!
//! Two portrait panels, one desktop: every connected DSI connector is an output
//! with its own CRTC and render loop, laid out side by side with the hinge gap
//! switchable at runtime ([`layout`]). Each panel's touchscreen drives its own
//! panel ([`input`]). Panels power off by pose, idle timeout or the power key
//! ([`power`], [`pose`]), and rotate ([`rotation`]). See README.md.

#![warn(missing_docs)]

pub mod ambient;
pub mod android;
pub mod cli;
pub mod config;
pub mod desk;
pub mod dialog;
pub mod events;
pub mod extout;
pub mod front;
pub mod gesture;
pub mod grants;
pub mod icons;
pub mod idle;
pub mod kbdlayout;
pub mod laptop;
pub mod layout;
pub mod mirror;
pub mod oskguard;
pub mod oskleave;
pub mod oskstart;
pub mod overview;
pub mod pacing;
pub mod pose;
pub mod power;
pub mod rotation;
pub mod snap;
pub mod split;
pub mod spring;
pub mod stitch;
pub mod trust;

mod backend;
mod capture;
mod devicestate;
mod frames;
mod fx;
mod inhibit;
mod input;
mod ipc;
mod keymapsync;
mod laptopmode;
mod lock;
mod mirrorlive;
mod penbutton;
mod pointer;
mod render;
mod rotanim;
mod screencopy;
mod screenshot;
mod session;
mod state;
mod tablet;
mod taskview;
mod teardown;
mod textspy;
mod tiling;
mod trackpad;
mod turnlog;
mod waydroid;
mod windrag;
mod wlrtoplevel;
mod xwayland;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, Context as _, Result};
use smithay::backend::session::libseat::LibSeatSession;
use smithay::backend::session::Session;
use smithay::input::SeatState;
use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::signals::{Signal, Signals};
use smithay::reexports::calloop::{EventLoop, Interest, Mode, PostAction};
use smithay::reexports::wayland_server::Display;
use smithay::wayland::compositor::CompositorState;
use smithay::wayland::dmabuf::{DmabufFeedbackBuilder, DmabufState};
use smithay::wayland::foreign_toplevel_list::ForeignToplevelListState;
use smithay::wayland::fractional_scale::FractionalScaleManagerState;
use smithay::wayland::input_method::InputMethodManagerState;
use smithay::wayland::output::OutputManagerState;
use smithay::wayland::selection::data_device::DataDeviceState;
use smithay::wayland::session_lock::SessionLockManagerState;
use smithay::wayland::shell::wlr_layer::WlrLayerShellState;
use smithay::wayland::shell::xdg::decoration::XdgDecorationState;
use smithay::wayland::shell::xdg::XdgShellState;
use smithay::wayland::shm::ShmState;
use smithay::wayland::single_pixel_buffer::SinglePixelBufferState;
use smithay::wayland::socket::ListeningSocketSource;
use smithay::wayland::text_input::TextInputManagerState;
use smithay::wayland::viewporter::ViewporterState;
use smithay::wayland::virtual_keyboard::VirtualKeyboardManagerState;
use smithay::wayland::xdg_activation::XdgActivationState;
use tracing::{info, warn};

use crate::backend::Tty;
use crate::state::{ClientState, DuoWc};

/// Key repeat for a plugged-in keyboard: a desktop's usual numbers.
const REPEAT_DELAY_MS: i32 = 300;
const REPEAT_RATE_HZ: i32 = 25;

/// Run the compositor until it is asked to stop.
pub fn run(options: cli::Options) -> Result<()> {
    // First, before any descriptor of ours exists: systemd's handed-back
    // descriptors (the stored lock) are read and closed.
    let stored_lock = lock::take_stored_flag();
    // No other process of this user may attach to the compositor (there is no
    // Yama in this kernel: same-user ptrace is otherwise allowed).
    if let Err(err) = trust::make_undumpable() {
        warn!(?err, "could not make duo-wc non-dumpable");
    }
    let mut config = config::Config::load(options.config.as_deref()).map_err(|err| anyhow!(err))?;
    if let Some(gap) = options.hinge_gap {
        config.hinge_gap = gap;
    }
    if let Some(scale) = options.scale {
        config.scale = scale;
    }
    info!(
        version = env!("CARGO_PKG_VERSION"),
        hinge_gap = ?config.hinge_gap,
        scale = config.scale,
        idle_timeout_s = config.idle_timeout_s,
        idle_inhibit = config.idle_inhibit,
        touch_wake = config.touch_wake,
        power_key_suspend = config.power_key_suspend,
        edge_swipe = config.edge_swipe,
        atomic_wake = config.atomic_wake,
        atomic_off = config.atomic_off,
        session_lock = config.session_lock,
        back_swipe = config.back_swipe.name(),
        animations = config.animations,
        "duo-wc starting"
    );

    let mut event_loop: EventLoop<DuoWc> = EventLoop::try_new().context("EventLoop::try_new")?;
    // systemd's READY=1 comes with the first picture on every lit panel
    // (backend.rs), at the latest after lock::READY_FALLBACK.
    event_loop
        .handle()
        .insert_source(
            smithay::reexports::calloop::timer::Timer::from_duration(lock::READY_FALLBACK),
            |_, _, _: &mut DuoWc| {
                lock::notify_ready("fallback timer");
                smithay::reexports::calloop::timer::TimeoutAction::Drop
            },
        )
        .map_err(|e| anyhow::anyhow!("ready timer: {e}"))?;
    let display: Display<DuoWc> = Display::new().context("Display::new")?;
    let display_handle = display.handle();

    let (session, session_notifier) = LibSeatSession::new().context("failed to open a libseat session")?;
    let seat_name = session.seat();
    info!(seat = seat_name, "session opened");

    let mut tty = Tty::new(session.clone());
    tty.open_device()?;

    let mut seat_state = SeatState::<DuoWc>::new();
    let mut seat = seat_state.new_wl_seat(&display_handle, seat_name.clone());
    // The keyboard layouts (a plugged keyboard's, laptop mode's): the
    // system's list as last sent (crate::kbdlayout), else duo-wc.conf's
    // xkb_* (empty: XKB_DEFAULT_LAYOUT, else us); kept where the keymap sync
    // sets it back (crate::keymapsync).
    let kbd = kbdlayout::Layouts::load(config.xkb.clone());
    keymapsync::set_seat_xkb(&kbd.names());
    seat.add_keyboard(keymapsync::seat_xkb_config(), REPEAT_DELAY_MS, REPEAT_RATE_HZ)
        .context("failed to add a keyboard")?;
    seat.add_touch();

    let lock = lock::Lock::new(config.session_lock, stored_lock);
    let mut state = DuoWc {
        running: Arc::new(AtomicBool::new(true)),
        display_handle: display_handle.clone(),
        loop_handle: event_loop.handle(),
        tty,
        outputs: Vec::new(),
        hinge_gap: config.hinge_gap,
        scale: config.scale,
        active_output: None,
        field_output: None,
        space: Default::default(),
        popups: Default::default(),
        next_window_id: 1,
        compositor_state: CompositorState::new::<DuoWc>(&display_handle),
        shm_state: ShmState::new::<DuoWc>(&display_handle, Vec::new()),
        seat_state,
        data_device_state: DataDeviceState::new::<DuoWc>(&display_handle),
        xdg_shell_state: XdgShellState::new::<DuoWc>(&display_handle),
        xdg_decoration_state: XdgDecorationState::new::<DuoWc>(&display_handle),
        // The shell's bars, grid, home and lock pieces and the keyboard:
        // trusted clients only (an app's overlay could pose as the lock).
        layer_shell_state: WlrLayerShellState::new_with_filter::<DuoWc, _>(&display_handle, |client| {
            ClientState::may_bind(client, trust::Guarded::LayerShell)
        }),
        dmabuf_state: DmabufState::new(),
        dmabuf_global: None,
        seat,
        pose: Default::default(),
        power: Default::default(),
        proximity: Default::default(),
        proximity_timer: None,
        ring: Default::default(),
        ring_timer: None,
        grants: Default::default(),
        idle: power::IdleTimer::new(
            (config.idle_timeout_s > 0).then(|| Duration::from_secs(config.idle_timeout_s)),
            std::time::Instant::now(),
        ),
        idle_timer: None,
        touch_wake: config.touch_wake,
        wake_whole_screen: false,
        power_key_suspend: config.power_key_suspend,
        full_redraw: config.full_redraw,
        scanout: config.scanout,
        atomic_wake: config.atomic_wake,
        atomic_off: config.atomic_off,
        power_key: Default::default(),
        suppressed_keys: Default::default(),
        touch_slots: Default::default(),
        touch_frame_delivered: false,
        pending_focus: Default::default(),
        posture: None,
        ambient: None,
        ambient_fresh: Default::default(),
        ambient_started: None,
        ambient_strip: Default::default(),
        ambient_timer: None,
        fifo_state: smithay::wayland::fifo::FifoManagerState::new::<DuoWc>(&display_handle),
        commit_timing_state: smithay::wayland::commit_timing::CommitTimingManagerState::new::<DuoWc>(
            &display_handle,
        ),
        pacing: Default::default(),
        // CLOCK_MONOTONIC (1): the clock of the DRM page-flip timestamps.
        presentation_state: smithay::wayland::presentation::PresentationState::new::<DuoWc>(
            &display_handle,
            1,
        ),
        osk_keys: Default::default(),
        last_osk_touch: None,
        // The on-screen keyboard: clients' text fields (text-input-v3, public)
        // talk to the input method (squeekboard, input-method-v2), which types
        // with a virtual keyboard. Those two (they read and type into
        // everything) are only for trusted clients: the session's (crate::trust).
        text_input_state: TextInputManagerState::new::<DuoWc>(&display_handle),
        input_method_state: InputMethodManagerState::new::<DuoWc, _>(&display_handle, |client| {
            ClientState::may_bind(client, trust::Guarded::InputMethod)
        }),
        virtual_keyboard_state: VirtualKeyboardManagerState::new::<DuoWc, _>(&display_handle, |client| {
            ClientState::may_bind(client, trust::Guarded::VirtualKeyboard)
        }),
        activation_state: XdgActivationState::new::<DuoWc>(&display_handle),
        keymap_sync: Default::default(),
        xdg_foreign_state: smithay::wayland::xdg_foreign::XdgForeignState::new::<DuoWc>(&display_handle),
        foreign_toplevels: ForeignToplevelListState::new_with_filter::<DuoWc>(&display_handle, |client| {
            ClientState::may_bind(client, trust::Guarded::ForeignToplevels)
        }),
        // The clipboard at any time (the Android bridge, shell/duo-shell's
        // duo-clipboard-bridge): trusted clients only, like the others.
        data_control_state: smithay::wayland::selection::ext_data_control::DataControlState::new::<DuoWc, _>(
            &display_handle,
            None,
            |client| ClientState::may_bind(client, trust::Guarded::DataControl),
        ),
        // The on-screen keyboard phosh-osk-stub starts only with these three
        // besides the input method, the virtual keyboard and the layer
        // shell (crate::devicestate): its paste key reads the clipboard
        // (wlr-data-control), its ignore-activation list the focused app
        // (crate::wlrtoplevel), and it stays folded while a hardware
        // keyboard is attached (crate::devicestate). Trusted clients only.
        wlr_data_control_state: smithay::wayland::selection::wlr_data_control::DataControlState::new::<
            DuoWc,
            _,
        >(&display_handle, None, |client| {
            ClientState::may_bind(client, trust::Guarded::DataControl)
        }),
        wlr_toplevels: Default::default(),
        device_state: Default::default(),
        edge_swipe: config.edge_swipe,
        held_touches: Default::default(),
        swipes: Default::default(),
        swipe_gen: 0,
        taskview: Default::default(),
        splits: Vec::new(),
        divider: None,
        split_gfx: Default::default(),
        session_lock_state: SessionLockManagerState::new::<DuoWc, _>(&display_handle, {
            // The global is offered only while session_lock is on, and only
            // to trusted clients (duo-lock, started by the shell).
            let enabled = lock.enabled.clone();
            move |client| {
                enabled.load(Ordering::SeqCst) && ClientState::may_bind(client, trust::Guarded::SessionLock)
            }
        }),
        lock,
        launch_hint: None,
        subscribers: Vec::new(),
        last_snapshot: None,
        events_dirty: true,
        carry: windrag::Carry::new(config.snap, config.stitch, config.animations),
        android: waydroid::Android::new(config.android.clone()),
        textspy: Default::default(),
        osk_away: textspy::OskAway::new(config.osk_leave),
        osk_start: Default::default(),
        pending_confirm: None,
        confirm_serial: 0,
        rotation_anim: rotanim::Gfx::new(config.rotate_anim_ms),
        back_swipe: config.back_swipe,
        edge_zone: config.edge_zone,
        tent_taps: Default::default(),
        tent_double: Default::default(),
        glance_panels: config.glance_panels.clone(),
        peek_panels: config.peek_panels.clone(),
        focus_refresh_queued: false,
        // The screen capture (crate::capture): guarded globals.
        capture: capture::Capture::new(&display_handle),
        // Idle inhibitors and idle notifications (crate::inhibit): public
        // globals; the shell's idle hold.
        inhibit: inhibit::IdleInhibit::new(&display_handle, event_loop.handle(), config.idle_inhibit),
        ext: extout::Ext {
            config: config.external.clone(),
            // duo-wc.conf's display settings under the live ones (crate::mirror).
            display: mirrorlive::settings_at_start(&config.display),
            ..Default::default()
        },
        desk: Default::default(),
        pointer: Default::default(),
        // Laptop mode (crate::laptopmode): off at the start.
        laptop: laptopmode::Laptop::new(config.laptop.clone(), config.touchpad_drag_lock),
        kbd,
        trackpad: crate::trackpad::Trackpad::new(config.touchpad_drag_lock),
        xwl: Default::default(),
        // X11 apps (crate::xwayland): the global only Xwayland sees.
        xwayland_shell_state: smithay::wayland::xwayland_shell::XWaylandShellState::new::<DuoWc>(
            &display_handle,
        ),
    };

    // Every connected connector, each on its own CRTC. The panels never hotplug,
    // so this happens once.
    let outputs = state.tty.init_outputs(config.scale)?;
    if outputs.is_empty() {
        anyhow::bail!("no connected connector");
    }
    for output in outputs {
        output.create_global::<DuoWc>(&display_handle);
        state.space.map_output(&output, (0, 0));
        state.outputs.push(output);
    }
    state.arrange_outputs();
    state.active_output = state.layout_order().first().map(|o| o.name());
    // An external display plugged before the start comes up now, the same
    // way as one plugged later (crate::extout).
    state.ext_scan("start");

    let _output_manager = OutputManagerState::new_with_xdg_output::<DuoWc>(&display_handle);
    let _viewporter = ViewporterState::new::<DuoWc>(&display_handle);
    // The scale is 2.5 by default: clients that speak wp_fractional_scale_v1 draw
    // at exactly that (with a viewport), the rest at 3 and are scaled down.
    let _fractional_scale = FractionalScaleManagerState::new::<DuoWc>(&display_handle);
    let _single_pixel = SinglePixelBufferState::new::<DuoWc>(&display_handle);
    // The pen (zwp_tablet_manager_v2), for every client.
    tablet::init(&display_handle, config.pen.clone());

    state.tty.bind_wl_display(&display_handle);
    if let Some(node) = state.tty.render_node() {
        let formats = state.tty.renderer_formats();
        info!(node = ?node, formats = formats.len(), "advertising dmabuf formats");
        match DmabufFeedbackBuilder::new(node.dev_id(), formats).build() {
            Ok(feedback) => {
                let global = state
                    .dmabuf_state
                    .create_global_with_default_feedback::<DuoWc>(&display_handle, &feedback);
                state.dmabuf_global = Some(global);
            }
            Err(err) => warn!(?err, "failed to build dmabuf feedback"),
        }
    }
    // Screen capture for the screen-cast portal (guarded globals): the
    // DMA-BUF formats the renderer can draw into, wlr-screencopy.
    let render_formats = state.tty.render_formats();
    let render_node = state.tty.render_node();
    state.capture.set_dma(render_node, &render_formats);
    screencopy::init(&display_handle);
    // The on-screen keyboard phosh-osk-stub's other two (guarded) globals.
    wlrtoplevel::WlrToplevels::init(&display_handle);
    devicestate::DeviceState::init(&display_handle);

    // Event sources.
    if let Some(notifier) = state.tty.notifier.take() {
        event_loop
            .handle()
            .insert_source(notifier, |event, metadata, state| {
                state.events_dirty = true;
                backend::on_drm_event(state, event, metadata);
            })
            .map_err(|err| anyhow!("failed to insert the DRM source: {err}"))?;
    }

    // Hotplug: a udev "change" on the DRM device (an external display on the
    // USB-C port, crate::extout).
    match smithay::backend::udev::UdevBackend::new(&seat_name) {
        Ok(udev) => {
            event_loop
                .handle()
                .insert_source(udev, |event, _, state| {
                    if let smithay::backend::udev::UdevEvent::Changed { .. } = event {
                        state.ext_hotplug();
                    }
                })
                .map_err(|err| anyhow!("failed to insert the udev source: {err}"))?;
        }
        Err(err) => warn!(?err, "no udev monitor: external displays are seen only at the start"),
    }

    event_loop
        .handle()
        .insert_source(session_notifier, |event, _, state| {
            state.events_dirty = true;
            backend::on_session_event(
                state,
                matches!(event, smithay::backend::session::Event::ActivateSession),
            );
        })
        .map_err(|err| anyhow!("failed to insert the session source: {err}"))?;

    let libinput = input::init(&session, &seat_name)?;
    event_loop
        .handle()
        .insert_source(libinput, |event, _, state| {
            state.events_dirty = true;
            input::handle(state, event);
        })
        .map_err(|err| anyhow!("failed to insert the input source: {err}"))?;

    let socket = ListeningSocketSource::new_auto().context("failed to bind a wayland socket")?;
    let socket_name = socket.socket_name().to_os_string();
    event_loop
        .handle()
        .insert_source(socket, move |stream, _, state| {
            if let Err(err) = state.display_handle.insert_client(stream, Arc::new(ClientState::default())) {
                warn!(?err, "failed to accept a client");
            }
        })
        .map_err(|err| anyhow!("failed to insert the socket source: {err}"))?;

    event_loop
        .handle()
        .insert_source(Generic::new(display, Interest::READ, Mode::Level), |_, display, state| {
            state.events_dirty = true;
            // Safety: the display is not dropped while the loop runs.
            unsafe { display.get_mut().dispatch_clients(state) }?;
            Ok(PostAction::Continue)
        })
        .map_err(|err| anyhow!("failed to insert the wayland source: {err}"))?;

    let runtime_dir = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".to_owned());
    let keeper = ipc::Doorkeeper {
        admin_users: config.admin_users.clone(),
        sensor_users: config.sensor_users.clone(),
        confirm_users: config.confirm_users.clone(),
    };
    let control_socket = ipc::listen(
        &event_loop.handle(),
        std::path::PathBuf::from(&runtime_dir).join("duo-wc.sock"),
        trust::Door::User,
        keeper.clone(),
    )?;
    let admin_socket = ipc::listen(
        &event_loop.handle(),
        std::path::PathBuf::from(&runtime_dir).join("admin.sock"),
        trust::Door::Admin,
        keeper,
    )?;

    unsafe { std::env::set_var("WAYLAND_DISPLAY", &socket_name) };
    unsafe { std::env::set_var("DUO_WC_SOCKET", &control_socket) };
    // X11 apps (crate::xwayland): duo-wc's X display, DISPLAY for the
    // session; Xwayland starts with the first X11 client (or now).
    state.xwl_init(config.xwayland);
    info!(socket = ?socket_name, control = ?control_socket, admin = ?admin_socket, "duo-wc is up");

    // The first frame on every panel: the background, before any client. The
    // idle timer starts counting now.
    state.queue_redraw_all();
    state.apply_power();

    // The session starts only now: a socket that is not listening yet is a client
    // that exits.
    // The session gets the trust factory (its fd 3): the shell, the lock and
    // the on-screen keyboard get their trusted connections from it.
    let session = match options.session {
        Some(command) => {
            let factory = ipc::factory(&event_loop.handle(), trust::FactoryLevel::Full)?;
            Some(session::spawn(&event_loop.handle(), command, state.running.clone(), factory)?)
        }
        None => None,
    };

    // SIGTERM and SIGINT end the loop, so the session's Drop runs.
    {
        let running = state.running.clone();
        event_loop
            .handle()
            .insert_source(
                Signals::new(&[Signal::SIGTERM, Signal::SIGINT]).context("failed to watch for signals")?,
                move |event, _, _state| {
                    info!(signal = ?event.signal(), "asked to stop");
                    running.store(false, Ordering::SeqCst);
                },
            )
            .map_err(|err| anyhow!("failed to insert the signal source: {err}"))?;
    }

    let running = state.running.clone();
    let signal = event_loop.get_signal();
    // No timeout: the loop sleeps until something happens (a client, input, a
    // page flip, a timer, a signal, the control socket). It used to wake every
    // 16 ms for nothing: ~60 wake-ups a second even with both panels off.
    let result = event_loop
        .run(None, &mut state, |state| {
            if !running.load(Ordering::SeqCst) {
                signal.stop();
                state.loop_handle.insert_idle(|_| {});
            }
            ipc::register_pending(state);
            state.space.refresh();
            state.popups.cleanup();
            // The X11 stacking order follows the space's (crate::xwayland).
            state.xwl_sync();
            wlrtoplevel::sync(state);
            if state.events_dirty {
                // Who keeps the screen on (crate::inhibit), before the
                // event stream compares its snapshot.
                state.refresh_idle_inhibit();
            }
            // A mirroring display whose panel changed draws again (crate::mirrorlive).
            state.mirror_check();
            // Windows that became hidden or shown: `suspended`, the slow
            // frames (crate::frames).
            state.pacing_refresh();
            ipc::publish(state);
            let _ = state.display_handle.flush_clients();
        })
        .context("the event loop failed");

    drop(session);
    result?;
    Ok(())
}
