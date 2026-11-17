//! Screen capture for the screen-cast portal (screen sharing in a video call,
//! a screen recorder, a screenshot tool).
//!
//! Two protocols, one engine:
//!
//! - **wlr-screencopy-unstable-v1** (version 3, [`crate::screencopy`]): what
//!   Debian trixie's xdg-desktop-portal-wlr 0.7.1 and grim 1.4 speak.
//! - **ext-image-copy-capture-v1** with **ext-output-image-capture-source-v1**
//!   (Smithay's handlers, here): their successor (xdg-desktop-portal-wlr 0.8,
//!   grim 1.5).
//!
//! Both capture one output (DSI-1 or DSI-2) at a time, into a client's wl_shm
//! buffer or DMA-BUF. The picture is the output's scene rendered again
//! off-screen with the same element list the panel gets
//! ([`crate::state::DuoWc::shown_elements`], as the IPC screenshot does), in
//! the output's **buffer space**: 1344 x 1892 whatever the rotation, with the
//! output's transform reported (wl_output, the ext frame's `transform`), so
//! a rotation does not change the stream's size. A dark panel can be
//! captured too (its scene), but nothing changes there: a stream of it gets
//! its first frame, then a failed one every [`MAX_WAIT`].
//!
//! **Damage.** Each wlr manager (per output) and each ext session keeps an
//! [`OutputDamageTracker`] that is asked what changed since its previous
//! frame ([`OutputDamageTracker::damage_output`]); a frame that waits for
//! damage (`copy_with_damage`, every ext frame but a session's first) is
//! served on the first look that finds some, or fails after [`MAX_WAIT`]
//! without (a portal ends its stream only when its last frame comes back). The
//! looks happen after the output has rendered a frame (`backend::render`
//! calls [`output_rendered`]), right after a frame is asked for, and when the
//! oldest waiting frame times out. The client's buffer is always drawn
//! whole (its contents are unknown: PipeWire rotates its buffers); the damage
//! is what the stream's consumer is told changed.
//!
//! **The lock screen is never captured.** While the session is locked a
//! capture gets black (no element at all, not the lock surface), also under
//! an ambient screen.
//!
//! **Who may.** The globals are guarded ([`crate::trust::Guarded::ScreenCapture`]):
//! only the session's trusted clients, the administrator's, and the
//! capture-only clients of a capture factory (the portal, `duoctl
//! exec-trusted --capture`) see them. The consent is the portal's chooser:
//! the user picks the panel to share.
//!
//! **Pacing.** A DMA-BUF frame is reported ready when the GPU has finished
//! drawing it (its fence, waited on the event loop, not blocking it). A
//! wl_shm frame is read back from the GPU on the loop: measured on the
//! phone (2026-11-09), a whole panel takes ~2 ms to draw and ~26 ms to read
//! back while the screen is busy, ~10 + ~100 ms at idle clocks (most of it
//! the CPU reading the GPU's write-combined staging memory; glReadPixels
//! straight into the client's buffer was no faster). The portal's `max_fps`
//! bounds it; a stream that browsers take as DMA-BUFs does not pay it. A
//! failed frame is answered after [`FAIL_DELAY`], so a client that asks again
//! at once does not spin.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use smithay::backend::allocator::{Format, Fourcc, Modifier};
use smithay::backend::drm::DrmNode;
use smithay::backend::renderer::damage::OutputDamageTracker;
use smithay::backend::renderer::gles::{GlesRenderbuffer, GlesRenderer};
use smithay::backend::renderer::sync::SyncPoint;
use smithay::backend::renderer::{Bind, ExportMem, Offscreen};
use smithay::output::{Output, WeakOutput};
use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::timer::{TimeoutAction, Timer};
use smithay::reexports::calloop::{Interest, Mode, PostAction, RegistrationToken};
use smithay::reexports::wayland_protocols_wlr::screencopy::v1::server::zwlr_screencopy_frame_v1::{
    self, ZwlrScreencopyFrameV1,
};
use smithay::reexports::wayland_server::protocol::{wl_buffer::WlBuffer, wl_shm};
use smithay::reexports::wayland_server::{DisplayHandle, Resource as _};
use smithay::utils::{Buffer as BufferCoords, IsAlive as _, Physical, Rectangle, Size, Transform};
use smithay::wayland::image_capture_source::{
    ImageCaptureSource, ImageCaptureSourceHandler, ImageCaptureSourceState, OutputCaptureSourceHandler,
    OutputCaptureSourceState,
};
use smithay::wayland::image_copy_capture::{
    BufferConstraints, CaptureFailureReason, DmabufConstraints, Frame, FrameRef, ImageCopyCaptureHandler,
    ImageCopyCaptureState, Session, SessionRef,
};
use tracing::{debug, info, warn};

use crate::state::{ClientState, DuoWc};
use crate::trust::Guarded;

/// A failed frame is answered this much later (a client that asks again at
/// once would otherwise spin).
pub const FAIL_DELAY: Duration = Duration::from_millis(100);

/// An output counts as captured (the event stream's `capturing`) for this
/// long after its last frame, and while a frame of it waits.
pub const RECENT: Duration = Duration::from_secs(3);

/// A frame that waits for damage fails after this long without any
/// (nothing is drawn: the client keeps the picture it has and asks again).
/// A portal keeps one frame waiting after its stream has stopped and ends
/// the stream (its notification, our `capturing`) only when that frame comes
/// back; on a still screen that was never. Drawing it instead would cost a
/// whole read-back for nothing (~100 ms on the loop for a wl_shm buffer while
/// the phone idles, measured 2026-11-09).
pub const MAX_WAIT: Duration = Duration::from_secs(2);

/// At most this many damage rectangles go out with a frame (the rest are
/// merged into the last one: xdg-desktop-portal-wlr keeps four).
pub const MAX_DAMAGE_RECTS: usize = 8;

/// The wl_shm formats a capture is copied into: XRGB8888 / ARGB8888 (B, G,
/// R, A bytes), the two every wl_shm offers (duo-wc's announces no other),
/// read back as BGRA (GL_EXT_read_format_bgra, which freedreno has), or as
/// RGBA and swapped while copying where that is missing.
pub const SHM_FORMATS: [wl_shm::Format; 2] = [wl_shm::Format::Xrgb8888, wl_shm::Format::Argb8888];

/// The DMA-BUF formats a capture is drawn into, if the renderer can draw into
/// them (XRGB8888: what the panels scan out and what PipeWire consumers
/// import most readily).
pub const DMA_FORMATS: [Fourcc; 2] = [Fourcc::Xrgb8888, Fourcc::Argb8888];

/// What a frame was asked for in: whose damage tracker it uses.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum TrackerKey {
    /// A zwlr_screencopy_manager_v1 instance (its own number) and the output:
    /// wlr damage is "since the last copy derived from this manager".
    Wlr { manager: u64, output: String },
    /// An ext capture session (its own number).
    Ext(u64),
}

/// How a frame is answered.
pub enum Done {
    /// A zwlr_screencopy_frame_v1.
    Wlr {
        frame: ZwlrScreencopyFrameV1,
        /// `copy_with_damage` on a version 2+ object: damage events go out.
        damage_events: bool,
    },
    /// An ext_image_copy_capture_frame_v1 (Smithay's).
    Ext { frame: Frame },
}

/// A frame asked for and not answered yet.
pub struct Pending {
    /// The output (connector name).
    pub output: String,
    /// The client's buffer.
    pub buffer: WlBuffer,
    /// It is a DMA-BUF (else wl_shm).
    pub dma: bool,
    /// The part of the output's buffer to copy (the whole output, or a wlr
    /// region), in the output's buffer coordinates.
    pub region: Rectangle<i32, BufferCoords>,
    /// Served only once the output has changed (unless it is the tracker's
    /// first frame).
    pub wait_damage: bool,
    /// Whose damage tracker.
    pub key: TrackerKey,
    /// How it is answered.
    pub done: Done,
    /// When it was asked for (the log).
    pub asked: Instant,
}

/// A damage tracker and what it was made for.
struct Tracker {
    damage: OutputDamageTracker,
    /// The output's mode size, scale and transform it tracks.
    mode: (Size<i32, Physical>, f64, Transform),
    /// The clear colour of its last look (locking turns the background
    /// black, which the element damage does not show).
    clear: [f32; 4],
}

/// An ext capture session we hold (dropping it sends `stopped`).
struct ExtSession {
    session: Session,
    key: u64,
    output: WeakOutput,
}

/// Its number, kept in the session's user data.
struct ExtKey(u64);

/// The capture state, kept in [`DuoWc::capture`].
/// The DMA-BUF formats a capture may use, with their modifiers.
type DmaFormats = Vec<(Fourcc, Vec<Modifier>)>;

pub struct Capture {
    /// Frames not answered yet, oldest first.
    pub pending: Vec<Pending>,
    trackers: HashMap<TrackerKey, Tracker>,
    service_queued: bool,
    /// Wakes the service when the oldest waiting frame reaches [`MAX_WAIT`].
    wait_timer: Option<RegistrationToken>,
    /// When each output was last captured.
    recent: HashMap<String, Instant>,
    /// Re-checks `recent` for the event stream while it is not empty.
    indicator_timer: Option<RegistrationToken>,
    /// The off-screen picture a wl_shm frame is read back from.
    shm_scratch: Option<(Size<i32, BufferCoords>, GlesRenderbuffer)>,
    next_id: u64,
    /// What a DMA-BUF capture may be (from the renderer), and the device.
    dma: Option<(DrmNode, DmaFormats)>,
    /// ext-image-copy-capture-v1.
    pub ext: ImageCopyCaptureState,
    /// ext-image-capture-source-v1 (no global of its own; held like the
    /// others).
    #[allow(dead_code)]
    pub sources: ImageCaptureSourceState,
    /// ext-output-image-capture-source-v1.
    pub output_sources: OutputCaptureSourceState,
    sessions: Vec<ExtSession>,
}

impl Capture {
    /// The globals (guarded: crate::trust::Guarded::ScreenCapture). The wlr
    /// manager is created by [`crate::screencopy::init`].
    pub fn new(display: &DisplayHandle) -> Self {
        Capture {
            pending: Vec::new(),
            trackers: HashMap::new(),
            service_queued: false,
            wait_timer: None,
            recent: HashMap::new(),
            indicator_timer: None,
            shm_scratch: None,
            next_id: 1,
            dma: None,
            ext: ImageCopyCaptureState::new_with_filter::<DuoWc, _>(display, |client| {
                ClientState::may_bind(client, Guarded::ScreenCapture)
            }),
            sources: ImageCaptureSourceState::new(),
            output_sources: OutputCaptureSourceState::new_with_filter::<DuoWc, _>(display, |client| {
                ClientState::may_bind(client, Guarded::ScreenCapture)
            }),
            sessions: Vec::new(),
        }
    }

    /// A fresh number (wlr managers, ext sessions).
    pub fn next_id(&mut self) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    /// The renderer's drawable DMA-BUF formats and the render node, once
    /// known (lib.rs, after the renderer is up).
    pub fn set_dma(&mut self, node: Option<DrmNode>, render_formats: &[Format]) {
        let formats = dma_formats(render_formats);
        info!(node = ?node, formats = ?formats.iter().map(|(f, m)| (f, m.len())).collect::<Vec<_>>(), "screen capture: DMA-BUF formats");
        self.dma = match node {
            Some(node) if !formats.is_empty() => Some((node, formats)),
            _ => None,
        };
    }

    /// The DMA-BUF format a wlr frame announces (`linux_dmabuf`), if any.
    pub fn wlr_dma_format(&self) -> Option<Fourcc> {
        self.dma.as_ref().and_then(|(_, formats)| formats.first().map(|(f, _)| *f))
    }

    /// Whether a DMA-BUF of this format and modifier may be captured into.
    pub fn dma_allows(&self, code: Fourcc, modifier: Modifier) -> bool {
        self.dma.as_ref().is_some_and(|(_, formats)| {
            formats.iter().any(|(f, mods)| *f == code && mods.contains(&modifier))
        })
    }

    /// The outputs being captured now (a frame waits, a frame was answered
    /// within [`RECENT`], or an ext session is open on it), sorted.
    pub fn capturing(&self, now: Instant) -> Vec<String> {
        let mut names: Vec<String> = self
            .pending
            .iter()
            .map(|p| p.output.clone())
            .chain(
                self.recent
                    .iter()
                    .filter(|(_, at)| now.saturating_duration_since(**at) < RECENT)
                    .map(|(name, _)| name.clone()),
            )
            .chain(
                self.sessions
                    .iter()
                    .filter(|s| s.session.alive())
                    .filter_map(|s| s.output.upgrade().map(|o| o.name())),
            )
            .collect();
        names.sort();
        names.dedup();
        names
    }

    /// Forget the trackers of a wlr manager that is gone.
    pub fn manager_gone(&mut self, manager: u64) {
        self.trackers.retain(|key, _| !matches!(key, TrackerKey::Wlr { manager: m, .. } if *m == manager));
    }
}

/// The capture formats among the renderer's drawable ones, in
/// [`DMA_FORMATS`]' order (pure).
pub fn dma_formats(render_formats: &[Format]) -> Vec<(Fourcc, Vec<Modifier>)> {
    DMA_FORMATS
        .iter()
        .filter_map(|code| {
            let mut mods: Vec<Modifier> =
                render_formats.iter().filter(|f| f.code == *code).map(|f| f.modifier).collect();
            mods.dedup();
            (!mods.is_empty()).then_some((*code, mods))
        })
        .collect()
}

/// Seconds (high and low 32 bits) and nanoseconds, the way both protocols
/// carry a timestamp (pure).
pub fn split_time(time: Duration) -> (u32, u32, u32) {
    let secs = time.as_secs();
    ((secs >> 32) as u32, (secs & 0xffff_ffff) as u32, time.subsec_nanos())
}

/// The damage a tracker reported, in the output's buffer coordinates
/// (pure). The tracker works in the output's upright frame; for an
/// untransformed output that is the buffer, otherwise the whole buffer is
/// reported (a turned panel: a wrong rectangle would leave stale parts in the
/// consumer's picture, a whole one costs only bandwidth).
pub fn buffer_damage(
    damage: &[Rectangle<i32, Physical>],
    transform: Transform,
    buffer: Size<i32, BufferCoords>,
) -> Vec<Rectangle<i32, BufferCoords>> {
    let whole = Rectangle::from_size(buffer);
    if transform != Transform::Normal {
        return vec![whole];
    }
    damage
        .iter()
        .map(|r| Rectangle::<i32, BufferCoords>::new((r.loc.x, r.loc.y).into(), (r.size.w, r.size.h).into()))
        .filter_map(|r| r.intersection(whole))
        .filter(|r| r.size.w > 0 && r.size.h > 0)
        .collect()
}

/// Damage inside a captured region, relative to the region (pure): what a
/// region capture's client is told.
pub fn damage_in_region(
    damage: &[Rectangle<i32, BufferCoords>],
    region: Rectangle<i32, BufferCoords>,
) -> Vec<Rectangle<i32, BufferCoords>> {
    damage
        .iter()
        .filter_map(|r| r.intersection(region))
        .filter(|r| r.size.w > 0 && r.size.h > 0)
        .map(|r| Rectangle::new((r.loc.x - region.loc.x, r.loc.y - region.loc.y).into(), r.size))
        .collect()
}

/// At most `max` rectangles: the ones past `max - 1` are merged into one box
/// with the last kept (pure).
pub fn limit_damage(
    mut rects: Vec<Rectangle<i32, BufferCoords>>,
    max: usize,
) -> Vec<Rectangle<i32, BufferCoords>> {
    if max == 0 || rects.len() <= max {
        return rects;
    }
    let tail = rects.split_off(max - 1);
    let merged = tail.into_iter().reduce(|a, b| a.merge(b));
    rects.extend(merged);
    rects
}

/// A wlr region (output logical coordinates, as the protocol has it) in the
/// output's buffer coordinates, clipped to it; None when nothing of it is on
/// the output (pure). `logical` is the output's logical size (upright),
/// `buffer` its buffer size. On a turned output only the whole output is
/// offered (nobody asks for part of one: grim and the portal take whole
/// outputs), a part of it is None.
pub fn region_to_buffer(
    region: (i32, i32, i32, i32),
    logical: Size<f64, smithay::utils::Logical>,
    scale: f64,
    transform: Transform,
    buffer: Size<i32, BufferCoords>,
) -> Option<Rectangle<i32, BufferCoords>> {
    let (x, y, w, h) = region;
    if w <= 0 || h <= 0 {
        return None;
    }
    let wanted = Rectangle::<f64, smithay::utils::Logical>::new(
        (x as f64, y as f64).into(),
        (w as f64, h as f64).into(),
    );
    let clipped = wanted.intersection(Rectangle::from_size(logical))?;
    let whole = Rectangle::from_size(buffer);
    if transform != Transform::Normal {
        let covers = clipped.loc.x <= 0.0
            && clipped.loc.y <= 0.0
            && clipped.size.w >= logical.w - 0.5
            && clipped.size.h >= logical.h - 0.5;
        return covers.then_some(whole);
    }
    let x0 = (clipped.loc.x * scale).floor() as i32;
    let y0 = (clipped.loc.y * scale).floor() as i32;
    let x1 = ((clipped.loc.x + clipped.size.w) * scale).ceil() as i32;
    let y1 = ((clipped.loc.y + clipped.size.h) * scale).ceil() as i32;
    Rectangle::<i32, BufferCoords>::new((x0, y0).into(), (x1 - x0, y1 - y0).into())
        .intersection(whole)
        .filter(|r| r.size.w > 0 && r.size.h > 0)
}

/// Whether a wl_shm buffer can take a capture of `size` (pure): one of
/// [`SHM_FORMATS`], exactly that size, rows at least as wide as the
/// picture, all inside its pool.
pub fn shm_fits(
    format: wl_shm::Format,
    width: i32,
    height: i32,
    stride: i32,
    offset: i32,
    pool_len: usize,
    size: Size<i32, BufferCoords>,
) -> bool {
    if !SHM_FORMATS.contains(&format) || width != size.w || height != size.h || width <= 0 || height <= 0 {
        return false;
    }
    let row = width as i64 * 4;
    if (stride as i64) < row || offset < 0 {
        return false;
    }
    let end = offset as i64 + stride as i64 * (height as i64 - 1) + row;
    end <= pool_len as i64
}

/// Copy `rows` rows of `row_bytes` from a packed picture into a buffer with
/// its own stride; `swap_rb` turns RGBA bytes into BGRA on the way (pure;
/// the caller checked the sizes with [`shm_fits`]).
pub fn copy_rows(
    src: &[u8],
    dst: &mut [u8],
    dst_stride: usize,
    row_bytes: usize,
    rows: usize,
    swap_rb: bool,
) {
    for row in 0..rows {
        let from = &src[row * row_bytes..(row + 1) * row_bytes];
        let to = &mut dst[row * dst_stride..row * dst_stride + row_bytes];
        if swap_rb {
            for (d, s) in to.chunks_exact_mut(4).zip(from.chunks_exact(4)) {
                d[0] = s[2];
                d[1] = s[1];
                d[2] = s[0];
                d[3] = s[3];
            }
        } else {
            to.copy_from_slice(from);
        }
    }
}

/// The clear colour of a capture (pure): black while locked or under an
/// ambient screen (as the panel shows), the desktop's background otherwise.
pub fn clear_colour(locked: bool, ambient: bool) -> [f32; 4] {
    if locked || ambient {
        [0.0, 0.0, 0.0, 1.0]
    } else {
        crate::backend::BACKGROUND
    }
}

/// Look again soon at the frames waiting (an idle callback, once).
pub fn queue_service(state: &mut DuoWc) {
    if state.capture.service_queued || state.capture.pending.is_empty() {
        return;
    }
    state.capture.service_queued = true;
    state.loop_handle.insert_idle(service);
}

/// The output has rendered a frame (backend::render): waiting frames of it
/// may have damage now.
pub fn output_rendered(state: &mut DuoWc, output: &Output) {
    let name = output.name();
    if state.capture.pending.iter().any(|p| p.output == name) {
        queue_service(state);
    }
}

/// A new frame to answer.
pub fn add(state: &mut DuoWc, pending: Pending) {
    debug!(
        output = pending.output,
        dma = pending.dma,
        wait_damage = pending.wait_damage,
        key = ?pending.key,
        "capture frame asked for"
    );
    state.capture.pending.push(pending);
    note_capture(state);
    queue_service(state);
}

/// Forget a frame its client destroyed (wlr: by the frame object).
pub fn forget_wlr_frame(state: &mut DuoWc, frame: &ZwlrScreencopyFrameV1) {
    state.capture.pending.retain(|p| !matches!(&p.done, Done::Wlr { frame: f, .. } if f == frame));
}

/// The event stream shows `capturing`; keep it current while an output
/// counts as captured.
fn note_capture(state: &mut DuoWc) {
    state.events_dirty = true;
    if state.capture.indicator_timer.is_some() {
        return;
    }
    let token = state.loop_handle.insert_source(
        Timer::from_duration(Duration::from_secs(1)),
        |_, _, state: &mut DuoWc| {
            state.events_dirty = true;
            let now = Instant::now();
            state.capture.recent.retain(|_, at| now.saturating_duration_since(*at) < RECENT);
            if state.capture.capturing(now).is_empty() {
                // The event stream takes its look after this dispatch:
                // `capturing` is empty now.
                state.capture.indicator_timer = None;
                TimeoutAction::Drop
            } else {
                TimeoutAction::ToDuration(Duration::from_secs(1))
            }
        },
    );
    match token {
        Ok(token) => state.capture.indicator_timer = Some(token),
        Err(err) => warn!(?err, "capture: no indicator timer"),
    }
}

/// Answer what can be answered: for every output with waiting frames, build
/// its picture once, ask each tracker for its damage, draw the frames that
/// are due into their buffers.
fn service(state: &mut DuoWc) {
    state.capture.service_queued = false;
    state.capture.sessions.retain(|s| s.session.alive());
    let mut names: Vec<String> = state.capture.pending.iter().map(|p| p.output.clone()).collect();
    names.sort();
    names.dedup();
    for name in names {
        let mine: Vec<Pending>;
        (mine, state.capture.pending) =
            std::mem::take(&mut state.capture.pending).into_iter().partition(|p| p.output == name);
        let Some(output) = state.output_by_name(&name) else {
            for p in mine {
                fail_later(state, p, CaptureFailureReason::Stopped);
            }
            continue;
        };
        let Some(mode) = output.current_mode() else {
            for p in mine {
                fail_later(state, p, CaptureFailureReason::Unknown);
            }
            continue;
        };
        let transform = output.current_transform();
        let scale = output.current_scale().fractional_scale();
        let locked = state.lock.locked;
        let clear = clear_colour(locked, state.ambient.is_some());
        // Locked: nothing at all, not even the lock surface.
        let elements = if locked {
            Vec::new()
        } else {
            match state.shown_elements(&output) {
                Ok(elements) => elements,
                Err(err) => {
                    warn!(output = name, ?err, "capture: the scene could not be built");
                    for p in mine {
                        fail_later(state, p, CaptureFailureReason::Unknown);
                    }
                    continue;
                }
            }
        };
        let buffer_size: Size<i32, BufferCoords> = (mode.size.w, mode.size.h).into();
        let want_mode = (mode.size, scale, transform);

        // Each tracker is asked once: None = nothing changed.
        let mut damage_of: HashMap<TrackerKey, Option<Vec<Rectangle<i32, BufferCoords>>>> = HashMap::new();
        let mut due = Vec::new();
        for p in mine {
            let damage = damage_of
                .entry(p.key.clone())
                .or_insert_with(|| {
                    let tracker = state.capture.trackers.entry(p.key.clone()).or_insert_with(|| Tracker {
                        damage: OutputDamageTracker::new(mode.size, scale, transform),
                        mode: want_mode,
                        clear,
                    });
                    let mut fresh = false;
                    if tracker.mode != want_mode || tracker.clear != clear {
                        // A new mode, rotation or background: everything.
                        *tracker = Tracker {
                            damage: OutputDamageTracker::new(mode.size, scale, transform),
                            mode: want_mode,
                            clear,
                        };
                        fresh = true;
                    }
                    match tracker.damage.damage_output(1, &elements) {
                        Ok((Some(rects), _)) => {
                            let rects = buffer_damage(rects, transform, buffer_size);
                            if fresh {
                                Some(vec![Rectangle::from_size(buffer_size)])
                            } else {
                                Some(rects)
                            }
                        }
                        Ok((None, _)) if fresh => Some(vec![Rectangle::from_size(buffer_size)]),
                        Ok((None, _)) => None,
                        Err(_) => Some(vec![Rectangle::from_size(buffer_size)]),
                    }
                })
                .clone();
            match damage {
                None if p.wait_damage && p.asked.elapsed() < MAX_WAIT => state.capture.pending.push(p),
                // Nothing changed for MAX_WAIT: back to the client, undrawn.
                None if p.wait_damage => fail(p, CaptureFailureReason::Unknown),
                damage => due.push((p, damage.unwrap_or_default())),
            }
        }

        for (p, damage) in due {
            if !buffer_matches(&state.capture, &p, buffer_size) {
                // The output changed size since the frame was announced.
                fail_later(state, p, CaptureFailureReason::BufferConstraints);
                continue;
            }
            let Some(device) = state.tty.device.as_mut() else {
                fail_later(state, p, CaptureFailureReason::Unknown);
                continue;
            };
            let started = Instant::now();
            let drawn = if p.dma {
                draw_dma(&mut device.renderer, &p.buffer, mode.size, scale, transform, &elements, clear)
            } else {
                draw_shm(
                    &mut device.renderer,
                    &mut state.capture.shm_scratch,
                    &p.buffer,
                    p.region,
                    mode.size,
                    scale,
                    transform,
                    &elements,
                    clear,
                )
                .map(|()| SyncPoint::signaled())
            };
            match drawn {
                Ok(sync) => {
                    debug!(
                        output = name,
                        dma = p.dma,
                        draw_ms = started.elapsed().as_secs_f64() * 1000.0,
                        waited_ms = p.asked.elapsed().as_millis() as u64,
                        rects = damage.len(),
                        "capture frame drawn"
                    );
                    state.capture.recent.insert(name.clone(), Instant::now());
                    finish_when_done(state, sync, p, damage, transform);
                }
                Err(err) => {
                    warn!(output = name, dma = p.dma, ?err, "capture: drawing the frame failed");
                    fail_later(state, p, CaptureFailureReason::Unknown);
                }
            }
        }
    }
    arm_wait_timer(state);
    note_capture(state);
}

/// A look again when the oldest waiting frame reaches [`MAX_WAIT`].
fn arm_wait_timer(state: &mut DuoWc) {
    if state.capture.wait_timer.is_some() {
        return;
    }
    let Some(oldest) = state.capture.pending.iter().map(|p| p.asked).min() else {
        return;
    };
    let left = MAX_WAIT.saturating_sub(oldest.elapsed()).max(Duration::from_millis(20));
    let token = state.loop_handle.insert_source(Timer::from_duration(left), |_, _, state: &mut DuoWc| {
        state.capture.wait_timer = None;
        queue_service(state);
        TimeoutAction::Drop
    });
    match token {
        Ok(token) => state.capture.wait_timer = Some(token),
        Err(err) => warn!(?err, "capture: no timer for waiting frames"),
    }
}

/// Whether the frame's buffer still fits the output (the ext constraints
/// are checked by Smithay against what the session was told; a mode change
/// since then is caught here).
fn buffer_matches(capture: &Capture, p: &Pending, buffer_size: Size<i32, BufferCoords>) -> bool {
    if p.dma {
        let Ok(dmabuf) = smithay::wayland::dmabuf::get_dmabuf(&p.buffer) else {
            return false;
        };
        use smithay::backend::allocator::Buffer as _;
        let size = dmabuf.size();
        p.region == Rectangle::from_size(buffer_size)
            && size.w == buffer_size.w
            && size.h == buffer_size.h
            && capture.dma_allows(dmabuf.format().code, dmabuf.format().modifier)
    } else {
        smithay::wayland::shm::with_buffer_contents(&p.buffer, |_, len, data| {
            shm_fits(data.format, data.width, data.height, data.stride, data.offset, len, p.region.size)
        })
        .unwrap_or(false)
            && Rectangle::from_size(buffer_size).contains_rect(p.region)
    }
}

/// Draw the output into a client's DMA-BUF (in buffer space).
fn draw_dma(
    renderer: &mut GlesRenderer,
    buffer: &WlBuffer,
    mode_size: Size<i32, Physical>,
    scale: f64,
    transform: Transform,
    elements: &[crate::render::Element],
    clear: [f32; 4],
) -> anyhow::Result<SyncPoint> {
    let mut dmabuf = smithay::wayland::dmabuf::get_dmabuf(buffer)
        .map_err(|err| anyhow::anyhow!("not a dmabuf: {err:?}"))?
        .clone();
    let mut target =
        renderer.bind(&mut dmabuf).map_err(|err| anyhow::anyhow!("cannot draw into the dmabuf: {err:?}"))?;
    let mut tracker = OutputDamageTracker::new(mode_size, scale, transform);
    let result = tracker
        .render_output(renderer, &mut target, 0, elements, clear)
        .map_err(|err| anyhow::anyhow!("rendering failed: {err:?}"))?;
    Ok(result.sync)
}

/// Draw the output off-screen, read the region back and copy it into a
/// client's wl_shm buffer.
#[allow(clippy::too_many_arguments)]
fn draw_shm(
    renderer: &mut GlesRenderer,
    scratch: &mut Option<(Size<i32, BufferCoords>, GlesRenderbuffer)>,
    buffer: &WlBuffer,
    region: Rectangle<i32, BufferCoords>,
    mode_size: Size<i32, Physical>,
    scale: f64,
    transform: Transform,
    elements: &[crate::render::Element],
    clear: [f32; 4],
) -> anyhow::Result<()> {
    let size: Size<i32, BufferCoords> = (mode_size.w, mode_size.h).into();
    if scratch.as_ref().is_none_or(|(s, _)| *s != size) {
        let made: GlesRenderbuffer = renderer
            .create_buffer(Fourcc::Abgr8888, size)
            .map_err(|err| anyhow::anyhow!("no off-screen buffer: {err:?}"))?;
        *scratch = Some((size, made));
    }
    let (_, renderbuffer) = scratch.as_mut().expect("made above");
    let mut target = renderer
        .bind(renderbuffer)
        .map_err(|err| anyhow::anyhow!("cannot bind the off-screen buffer: {err:?}"))?;
    let mut tracker = OutputDamageTracker::new(mode_size, scale, transform);
    let t0 = Instant::now();
    let result = tracker
        .render_output(renderer, &mut target, 0, elements, clear)
        .map_err(|err| anyhow::anyhow!("rendering failed: {err:?}"))?;
    let _ = result.sync.wait();
    let t_render = t0.elapsed();
    // BGRA straight from the GPU; RGBA and a swap where it cannot.
    let (mapping, swap_rb) = match renderer.copy_framebuffer(&target, region, Fourcc::Argb8888) {
        Ok(mapping) => (mapping, false),
        Err(_) => (
            renderer
                .copy_framebuffer(&target, region, Fourcc::Abgr8888)
                .map_err(|err| anyhow::anyhow!("read-back failed: {err:?}"))?,
            true,
        ),
    };
    let t_read = t0.elapsed();
    let pixels = renderer
        .map_texture(&mapping)
        .map_err(|err| anyhow::anyhow!("mapping the read-back failed: {err:?}"))?;
    let t_map = t0.elapsed();
    let row_bytes = region.size.w as usize * 4;
    let rows = region.size.h as usize;
    if pixels.len() < row_bytes * rows {
        anyhow::bail!("the read-back is short ({} bytes)", pixels.len());
    }
    smithay::wayland::shm::with_buffer_contents_mut(buffer, |ptr, len, data| {
        if !shm_fits(data.format, data.width, data.height, data.stride, data.offset, len, region.size) {
            anyhow::bail!("the wl_shm buffer does not fit");
        }
        // Safety: shm_fits checked offset + stride * (h - 1) + row <= len;
        // the pool stays mapped for the closure (Smithay catches SIGBUS).
        let dst = unsafe {
            std::slice::from_raw_parts_mut(ptr.add(data.offset as usize), len - data.offset as usize)
        };
        copy_rows(pixels, dst, data.stride as usize, row_bytes, rows, swap_rb);
        Ok(())
    })
    .inspect(|_| {
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        debug!(
            render_ms = ms(t_render),
            readback_ms = ms(t_read - t_render),
            map_ms = ms(t_map - t_read),
            copy_ms = ms(t0.elapsed() - t_map),
            swap_rb,
            "capture: wl_shm frame"
        );
    })
    .map_err(|err| anyhow::anyhow!("the wl_shm buffer is not readable: {err:?}"))??;
    Ok(())
}

/// Answer the frame when the GPU is done with it: at once if it is, else
/// when its fence signals (on the loop), else after waiting for it.
fn finish_when_done(
    state: &mut DuoWc,
    sync: SyncPoint,
    p: Pending,
    damage: Vec<Rectangle<i32, BufferCoords>>,
    transform: Transform,
) {
    if sync.is_reached() {
        succeed(p, damage, transform);
        return;
    }
    if let Some(fence) = sync.export() {
        let mut job = Some((p, damage));
        let inserted = state.loop_handle.insert_source(
            Generic::new(fence, Interest::READ, Mode::OneShot),
            move |_, _fence, _state: &mut DuoWc| {
                if let Some((p, damage)) = job.take() {
                    succeed(p, damage, transform);
                }
                Ok(PostAction::Remove)
            },
        );
        match inserted {
            Ok(_) => return,
            Err(err) => {
                warn!(?err, "capture: cannot wait for the fence on the loop");
                // The job went with the closure: the client sees its frame
                // fail when the frame object dies; nothing else to do.
                return;
            }
        }
    }
    let _ = sync.wait();
    succeed(p, damage, transform);
}

/// Send `ready` (and what goes before it).
fn succeed(p: Pending, damage: Vec<Rectangle<i32, BufferCoords>>, transform: Transform) {
    let now = crate::turnlog::monotonic_now();
    match p.done {
        Done::Wlr { frame, damage_events } => {
            if !frame.is_alive() {
                return;
            }
            frame.flags(zwlr_screencopy_frame_v1::Flags::empty());
            if damage_events {
                // No rectangle: nothing changed (a frame that waited
                // MAX_WAIT); a tracker's first frame carries the whole.
                let rects = limit_damage(damage_in_region(&damage, p.region), MAX_DAMAGE_RECTS);
                for r in rects {
                    frame.damage(r.loc.x as u32, r.loc.y as u32, r.size.w as u32, r.size.h as u32);
                }
            }
            let (hi, lo, nsec) = split_time(now);
            frame.ready(hi, lo, nsec);
        }
        Done::Ext { frame } => {
            frame.success(transform, Some(limit_damage(damage, MAX_DAMAGE_RECTS)), now);
        }
    }
}

/// Answer `failed`, [`FAIL_DELAY`] from now.
fn fail_later(state: &mut DuoWc, p: Pending, reason: CaptureFailureReason) {
    let mut job = Some(p);
    let inserted = state.loop_handle.insert_source(Timer::from_duration(FAIL_DELAY), move |_, _, _state| {
        if let Some(p) = job.take() {
            fail(p, reason);
        }
        TimeoutAction::Drop
    });
    if let Err(err) = inserted {
        warn!(?err, "capture: no timer for a failure");
    }
}

/// A wlr frame that never became a [`Pending`] fails, [`FAIL_DELAY`] from now.
pub fn fail_frame_later(state: &mut DuoWc, frame: ZwlrScreencopyFrameV1, output: &str) {
    debug!(output, "capture frame refused");
    let inserted = state.loop_handle.insert_source(Timer::from_duration(FAIL_DELAY), move |_, _, _state| {
        if frame.is_alive() {
            frame.failed();
        }
        TimeoutAction::Drop
    });
    if let Err(err) = inserted {
        warn!(?err, "capture: no timer for a failure");
    }
}

fn fail(p: Pending, reason: CaptureFailureReason) {
    debug!(output = p.output, ?reason, "capture frame failed");
    match p.done {
        Done::Wlr { frame, .. } => {
            if frame.is_alive() {
                frame.failed();
            }
        }
        Done::Ext { frame } => frame.fail(reason),
    }
}

/// The ext constraints of an output: its buffer size, the wl_shm formats,
/// the DMA-BUF device and formats.
fn constraints_for(capture: &Capture, output: &Output) -> Option<BufferConstraints> {
    let mode = output.current_mode()?;
    Some(BufferConstraints {
        size: (mode.size.w, mode.size.h).into(),
        shm: SHM_FORMATS.to_vec(),
        dma: capture
            .dma
            .as_ref()
            .map(|(node, formats)| DmabufConstraints { node: *node, formats: formats.clone() }),
    })
}

impl ImageCaptureSourceHandler for DuoWc {}

impl OutputCaptureSourceHandler for DuoWc {
    fn output_capture_source_state(&mut self) -> &mut OutputCaptureSourceState {
        &mut self.capture.output_sources
    }

    fn output_source_created(&mut self, source: ImageCaptureSource, output: &Output) {
        source.user_data().insert_if_missing(|| output.downgrade());
    }
}

impl ImageCopyCaptureHandler for DuoWc {
    fn image_copy_capture_state(&mut self) -> &mut ImageCopyCaptureState {
        &mut self.capture.ext
    }

    fn capture_constraints(&mut self, source: &ImageCaptureSource) -> Option<BufferConstraints> {
        let output = source.user_data().get::<WeakOutput>()?.upgrade()?;
        constraints_for(&self.capture, &output)
    }

    fn new_session(&mut self, session: Session) {
        let Some(output) = session.source().user_data().get::<WeakOutput>().cloned() else {
            return;
        };
        let key = self.capture.next_id();
        session.user_data().insert_if_missing(|| ExtKey(key));
        info!(output = ?output.upgrade().map(|o| o.name()), key, "capture session opened (ext-image-copy-capture)");
        self.capture.sessions.push(ExtSession { session, key, output });
        note_capture(self);
    }

    fn frame(&mut self, session: &SessionRef, frame: Frame) {
        let key = session.user_data().get::<ExtKey>().map(|k| k.0);
        let output = session.source().user_data().get::<WeakOutput>().and_then(|o| o.upgrade());
        let (Some(key), Some(output)) = (key, output) else {
            frame.fail(CaptureFailureReason::Stopped);
            return;
        };
        // A mode change since the session was told its constraints: tell
        // it again, the frame fails with buffer_constraints (it reallocates).
        if let (Some(now), Some(told)) =
            (constraints_for(&self.capture, &output), session.current_constraints())
        {
            if now.size != told.size {
                session.update_constraints(now);
                frame.fail(CaptureFailureReason::BufferConstraints);
                return;
            }
        }
        let buffer = frame.buffer();
        let dma = smithay::wayland::dmabuf::get_dmabuf(&buffer).is_ok();
        let Some(mode) = output.current_mode() else {
            frame.fail(CaptureFailureReason::Unknown);
            return;
        };
        let region = Rectangle::from_size((mode.size.w, mode.size.h).into());
        let first = !self.capture.trackers.contains_key(&TrackerKey::Ext(key));
        add(
            self,
            Pending {
                output: output.name(),
                buffer,
                dma,
                region,
                wait_damage: !first,
                key: TrackerKey::Ext(key),
                done: Done::Ext { frame },
                asked: Instant::now(),
            },
        );
    }

    fn frame_aborted(&mut self, frame: FrameRef) {
        self.capture.pending.retain(|p| !matches!(&p.done, Done::Ext { frame: f } if *f == frame));
    }

    fn session_destroyed(&mut self, session: SessionRef) {
        let key = session.user_data().get::<ExtKey>().map(|k| k.0);
        if let Some(key) = key {
            self.capture.trackers.remove(&TrackerKey::Ext(key));
            self.capture.sessions.retain(|s| s.key != key);
            info!(key, "capture session closed");
        }
        self.events_dirty = true;
    }
}

smithay::delegate_image_capture_source!(DuoWc);
smithay::delegate_output_capture_source!(DuoWc);
smithay::delegate_image_copy_capture!(DuoWc);

#[cfg(test)]
mod tests {
    use super::*;

    fn r(x: i32, y: i32, w: i32, h: i32) -> Rectangle<i32, BufferCoords> {
        Rectangle::new((x, y).into(), (w, h).into())
    }

    fn p(x: i32, y: i32, w: i32, h: i32) -> Rectangle<i32, Physical> {
        Rectangle::new((x, y).into(), (w, h).into())
    }

    const PANEL: (i32, i32) = (1344, 1892);

    #[test]
    fn timestamps_split_like_the_protocols_want() {
        assert_eq!(split_time(Duration::new(5, 7)), (0, 5, 7));
        assert_eq!(split_time(Duration::new((3u64 << 32) + 9, 999_999_999)), (3, 9, 999_999_999));
    }

    #[test]
    fn damage_of_an_upright_panel_is_kept_and_clipped() {
        let size: Size<i32, BufferCoords> = PANEL.into();
        let damage = buffer_damage(
            &[p(10, 20, 30, 40), p(1300, 1880, 100, 100), p(2000, 0, 5, 5)],
            Transform::Normal,
            size,
        );
        assert_eq!(damage, vec![r(10, 20, 30, 40), r(1300, 1880, 44, 12)]);
    }

    #[test]
    fn a_turned_panel_reports_the_whole_buffer() {
        let size: Size<i32, BufferCoords> = PANEL.into();
        for t in [Transform::_90, Transform::_180, Transform::_270, Transform::Flipped] {
            assert_eq!(buffer_damage(&[p(1, 1, 2, 2)], t, size), vec![r(0, 0, 1344, 1892)], "{t:?}");
        }
    }

    #[test]
    fn region_damage_is_relative_to_the_region() {
        let region = r(100, 200, 300, 400);
        let damage = damage_in_region(&[r(0, 0, 150, 250), r(390, 590, 50, 50), r(0, 0, 10, 10)], region);
        assert_eq!(damage, vec![r(0, 0, 50, 50), r(290, 390, 10, 10)]);
    }

    #[test]
    fn many_rectangles_merge_into_the_last() {
        let rects: Vec<_> = (0..10).map(|i| r(i * 10, 0, 5, 5)).collect();
        let limited = limit_damage(rects.clone(), 4);
        assert_eq!(limited.len(), 4);
        assert_eq!(&limited[..3], &rects[..3]);
        assert_eq!(limited[3], r(30, 0, 65, 5));
        assert_eq!(limit_damage(rects[..3].to_vec(), 4), rects[..3].to_vec());
    }

    #[test]
    fn a_region_is_scaled_and_clipped_to_the_panel() {
        // 1344 x 1892 at 2.5: 537.6 x 756.8 logical.
        let logical = Size::from((537.6, 756.8));
        let buffer: Size<i32, BufferCoords> = PANEL.into();
        let n = Transform::Normal;
        assert_eq!(region_to_buffer((10, 20, 100, 40), logical, 2.5, n, buffer), Some(r(25, 50, 250, 100)));
        // Past the right edge: cut there.
        assert_eq!(region_to_buffer((500, 0, 100, 10), logical, 2.5, n, buffer), Some(r(1250, 0, 94, 25)));
        // The whole output.
        assert_eq!(region_to_buffer((0, 0, 538, 757), logical, 2.5, n, buffer), Some(r(0, 0, 1344, 1892)));
        // Off the panel or empty: nothing.
        assert_eq!(region_to_buffer((600, 0, 10, 10), logical, 2.5, n, buffer), None);
        assert_eq!(region_to_buffer((0, 0, 0, 10), logical, 2.5, n, buffer), None);
    }

    #[test]
    fn a_turned_panel_is_captured_whole_or_not_at_all() {
        // Turned 90 degrees: the upright panel is 756.8 x 537.6 logical,
        // the buffer stays 1344 x 1892.
        let logical = Size::from((756.8, 537.6));
        let buffer: Size<i32, BufferCoords> = PANEL.into();
        let t = Transform::_90;
        assert_eq!(region_to_buffer((0, 0, 757, 538), logical, 2.5, t, buffer), Some(r(0, 0, 1344, 1892)));
        assert_eq!(region_to_buffer((-5, -5, 800, 600), logical, 2.5, t, buffer), Some(r(0, 0, 1344, 1892)));
        assert_eq!(region_to_buffer((0, 0, 100, 40), logical, 2.5, t, buffer), None);
    }

    #[test]
    fn shm_buffers_must_fit_exactly() {
        let size: Size<i32, BufferCoords> = (4, 2).into();
        let x = wl_shm::Format::Xrgb8888;
        assert!(shm_fits(x, 4, 2, 16, 0, 32, size));
        assert!(shm_fits(wl_shm::Format::Argb8888, 4, 2, 20, 8, 8 + 20 + 16, size));
        // Wrong size, format, stride, or past the pool.
        assert!(!shm_fits(x, 4, 3, 16, 0, 64, size));
        assert!(!shm_fits(wl_shm::Format::Xbgr8888, 4, 2, 16, 0, 32, size));
        assert!(!shm_fits(x, 4, 2, 15, 0, 32, size));
        assert!(!shm_fits(x, 4, 2, 16, 4, 32, size));
        assert!(!shm_fits(x, 4, 2, 16, -1, 32, size));
        assert!(!shm_fits(x, 0, 0, 0, 0, 0, (0, 0).into()));
    }

    #[test]
    fn rows_land_at_the_buffers_stride() {
        let src: Vec<u8> = (0..16).collect();
        let mut dst = vec![0xffu8; 2 * 12];
        copy_rows(&src, &mut dst, 12, 8, 2, false);
        assert_eq!(&dst[..8], &src[..8]);
        assert_eq!(&dst[8..12], &[0xff; 4]);
        assert_eq!(&dst[12..20], &src[8..16]);
        // RGBA in, BGRA out.
        let mut dst = vec![0u8; 8];
        copy_rows(&[1, 2, 3, 4, 5, 6, 7, 8], &mut dst, 8, 8, 1, true);
        assert_eq!(dst, vec![3, 2, 1, 4, 7, 6, 5, 8]);
    }

    #[test]
    fn locked_captures_are_black() {
        assert_eq!(clear_colour(true, false), [0.0, 0.0, 0.0, 1.0]);
        assert_eq!(clear_colour(true, true), [0.0, 0.0, 0.0, 1.0]);
        assert_eq!(clear_colour(false, true), [0.0, 0.0, 0.0, 1.0]);
        assert_eq!(clear_colour(false, false), crate::backend::BACKGROUND);
    }

    #[test]
    fn dma_formats_in_order_with_their_modifiers() {
        let f = |code, modifier| Format { code, modifier };
        let formats = [
            f(Fourcc::Argb8888, Modifier::Linear),
            f(Fourcc::Xrgb8888, Modifier::Linear),
            f(Fourcc::Xrgb8888, Modifier::from(0x0500_0000_0000_0001u64)),
            f(Fourcc::Nv12, Modifier::Linear),
        ];
        let got = dma_formats(&formats);
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].0, Fourcc::Xrgb8888);
        assert_eq!(got[0].1.len(), 2);
        assert_eq!(got[1], (Fourcc::Argb8888, vec![Modifier::Linear]));
        assert!(dma_formats(&[f(Fourcc::Nv12, Modifier::Linear)]).is_empty());
    }
}
