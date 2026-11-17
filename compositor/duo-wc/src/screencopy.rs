//! wlr-screencopy-unstable-v1, version 3: the capture protocol of Debian
//! trixie's xdg-desktop-portal-wlr (0.7.1) and grim (1.4). The frames are
//! drawn by [`crate::capture`]; this is the protocol's bookkeeping.
//!
//! A frame announces one wl_shm format (XRGB8888,
//! [`crate::capture::SHM_FORMATS`]) at the output's
//! buffer size, and from version 3 also a DMA-BUF format (XRGB8888, when the
//! renderer can draw into it) and `buffer_done`. `copy` is answered on the
//! next turn of the loop, `copy_with_damage` once the output has changed
//! since the last copy derived from the same manager (its first copy at
//! once). `overlay_cursor` is ignored: there is no cursor. A region capture
//! (`capture_output_region`) is offered as wl_shm only, and on a turned
//! output only as the whole output.
//!
//! The global is guarded ([`crate::trust::Guarded::ScreenCapture`]): only
//! trusted and capture clients see it.

use std::sync::Mutex;
use std::time::Instant;

use smithay::backend::allocator::Buffer as _;
use smithay::output::Output;
use smithay::reexports::wayland_protocols_wlr::screencopy::v1::server::{
    zwlr_screencopy_frame_v1::{self, ZwlrScreencopyFrameV1},
    zwlr_screencopy_manager_v1::{self, ZwlrScreencopyManagerV1},
};
use smithay::reexports::wayland_server::backend::ClientId;
use smithay::reexports::wayland_server::protocol::wl_buffer::WlBuffer;
use smithay::reexports::wayland_server::protocol::wl_output::WlOutput;
use smithay::reexports::wayland_server::protocol::wl_shm;
use smithay::reexports::wayland_server::{
    Client, DataInit, Dispatch, DisplayHandle, GlobalDispatch, New, Resource,
};
use smithay::utils::{Buffer as BufferCoords, Rectangle, Size};
use tracing::{debug, info};

use crate::capture::{Done, Pending, TrackerKey};
use crate::state::{ClientState, DuoWc};
use crate::trust::Guarded;

/// The version offered.
pub const VERSION: u32 = 3;

/// Create the global.
pub fn init(display: &DisplayHandle) {
    display.create_global::<DuoWc, ZwlrScreencopyManagerV1, ()>(VERSION, ());
}

/// A manager instance: its number (the damage trackers are per manager and
/// output).
pub struct ManagerData {
    id: u64,
}

/// What a frame announced, and whether it was used.
pub struct FrameData {
    inner: Mutex<FrameInner>,
}

struct FrameInner {
    manager: u64,
    output: String,
    /// What is copied, in the output's buffer coordinates.
    region: Rectangle<i32, BufferCoords>,
    /// A DMA-BUF was offered (`linux_dmabuf`).
    dma: Option<smithay::backend::allocator::Fourcc>,
    /// `copy` or `copy_with_damage` came already.
    used: bool,
    /// The frame failed before a copy (no such output): `failed` again.
    dead: bool,
}

impl GlobalDispatch<ZwlrScreencopyManagerV1, ()> for DuoWc {
    fn bind(
        state: &mut Self,
        _handle: &DisplayHandle,
        client: &Client,
        resource: New<ZwlrScreencopyManagerV1>,
        _global_data: &(),
        data_init: &mut DataInit<'_, Self>,
    ) {
        let id = state.capture.next_id();
        info!(client = ?client.id(), manager = id, "screencopy manager bound");
        data_init.init(resource, ManagerData { id });
    }

    fn can_view(client: Client, _global_data: &()) -> bool {
        ClientState::may_bind(&client, Guarded::ScreenCapture)
    }
}

impl Dispatch<ZwlrScreencopyManagerV1, ManagerData> for DuoWc {
    fn request(
        state: &mut Self,
        _client: &Client,
        _resource: &ZwlrScreencopyManagerV1,
        request: zwlr_screencopy_manager_v1::Request,
        data: &ManagerData,
        _dhandle: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        match request {
            zwlr_screencopy_manager_v1::Request::CaptureOutput { frame, overlay_cursor: _, output } => {
                new_frame(state, data.id, frame, &output, None, data_init);
            }
            zwlr_screencopy_manager_v1::Request::CaptureOutputRegion {
                frame,
                overlay_cursor: _,
                output,
                x,
                y,
                width,
                height,
            } => {
                new_frame(state, data.id, frame, &output, Some((x, y, width, height)), data_init);
            }
            zwlr_screencopy_manager_v1::Request::Destroy => {}
            _ => {}
        }
    }

    fn destroyed(
        state: &mut Self,
        _client: ClientId,
        _resource: &ZwlrScreencopyManagerV1,
        data: &ManagerData,
    ) {
        state.capture.manager_gone(data.id);
    }
}

/// Make the frame object and announce its buffer types.
fn new_frame(
    state: &mut DuoWc,
    manager: u64,
    frame: New<ZwlrScreencopyFrameV1>,
    wl_output: &WlOutput,
    region: Option<(i32, i32, i32, i32)>,
    data_init: &mut DataInit<'_, DuoWc>,
) {
    let output = Output::from_resource(wl_output);
    let mode = output.as_ref().and_then(|o| o.current_mode());
    let (Some(output), Some(mode)) = (output, mode) else {
        let frame = data_init.init(
            frame,
            FrameData {
                inner: Mutex::new(FrameInner {
                    manager,
                    output: String::new(),
                    region: Rectangle::default(),
                    dma: None,
                    used: false,
                    dead: true,
                }),
            },
        );
        frame.failed();
        return;
    };
    let buffer: Size<i32, BufferCoords> = (mode.size.w, mode.size.h).into();
    let transform = output.current_transform();
    let scale = output.current_scale().fractional_scale();
    let rect = match region {
        None => Some(Rectangle::from_size(buffer)),
        Some(region) => {
            let upright = transform.transform_size(mode.size);
            let logical = Size::from((upright.w as f64 / scale, upright.h as f64 / scale));
            crate::capture::region_to_buffer(region, logical, scale, transform, buffer)
        }
    };
    let dma = match (region, rect) {
        (None, Some(_)) => state.capture.wlr_dma_format(),
        _ => None,
    };
    let frame = data_init.init(
        frame,
        FrameData {
            inner: Mutex::new(FrameInner {
                manager,
                output: output.name(),
                region: rect.unwrap_or_default(),
                dma,
                used: false,
                dead: rect.is_none(),
            }),
        },
    );
    let Some(rect) = rect else {
        debug!(output = output.name(), ?region, "screencopy: the region is not on the output");
        frame.failed();
        return;
    };
    let (w, h) = (rect.size.w as u32, rect.size.h as u32);
    frame.buffer(wl_shm::Format::Xrgb8888, w, h, w * 4);
    if frame.version() >= 3 {
        if let Some(fourcc) = dma {
            frame.linux_dmabuf(fourcc as u32, w, h);
        }
        frame.buffer_done();
    }
}

impl Dispatch<ZwlrScreencopyFrameV1, FrameData> for DuoWc {
    fn request(
        state: &mut Self,
        _client: &Client,
        resource: &ZwlrScreencopyFrameV1,
        request: zwlr_screencopy_frame_v1::Request,
        data: &FrameData,
        _dhandle: &DisplayHandle,
        _data_init: &mut DataInit<'_, Self>,
    ) {
        let (buffer, with_damage) = match request {
            zwlr_screencopy_frame_v1::Request::Copy { buffer } => (buffer, false),
            zwlr_screencopy_frame_v1::Request::CopyWithDamage { buffer } => (buffer, true),
            zwlr_screencopy_frame_v1::Request::Destroy => return,
            _ => return,
        };
        let mut inner = data.inner.lock().unwrap();
        if inner.used {
            resource.post_error(zwlr_screencopy_frame_v1::Error::AlreadyUsed, "the frame was copied already");
            return;
        }
        inner.used = true;
        if inner.dead {
            resource.failed();
            return;
        }
        let dma = match check_buffer(&buffer, &inner, &state.capture) {
            Ok(dma) => dma,
            Err(Bad::Invalid(why)) => {
                debug!(output = inner.output, why, "screencopy: invalid buffer");
                resource.post_error(zwlr_screencopy_frame_v1::Error::InvalidBuffer, why);
                return;
            }
            Err(Bad::Unusable(why)) => {
                // Of the announced kind and size, but nothing we can draw
                // into (a modifier the renderer only samples): `failed`,
                // not a protocol error that would end the portal.
                debug!(output = inner.output, why, "screencopy: buffer not drawable");
                let output = inner.output.clone();
                drop(inner);
                crate::capture::fail_frame_later(state, resource.clone(), &output);
                return;
            }
        };
        let pending = Pending {
            output: inner.output.clone(),
            buffer,
            dma,
            region: inner.region,
            wait_damage: with_damage,
            key: TrackerKey::Wlr { manager: inner.manager, output: inner.output.clone() },
            done: Done::Wlr {
                frame: resource.clone(),
                damage_events: with_damage && resource.version() >= 2,
            },
            asked: Instant::now(),
        };
        drop(inner);
        crate::capture::add(state, pending);
    }

    fn destroyed(state: &mut Self, _client: ClientId, resource: &ZwlrScreencopyFrameV1, _data: &FrameData) {
        crate::capture::forget_wlr_frame(state, resource);
    }
}

/// Why a buffer is not taken.
enum Bad {
    /// Not what the frame announced: a protocol error (invalid_buffer).
    Invalid(&'static str),
    /// What was announced, but not drawable here: the frame fails.
    Unusable(&'static str),
}

/// Whether a buffer is one the frame announced: Ok(true) for a DMA-BUF,
/// Ok(false) for wl_shm.
fn check_buffer(
    buffer: &WlBuffer,
    frame: &FrameInner,
    capture: &crate::capture::Capture,
) -> Result<bool, Bad> {
    if let Ok(dmabuf) = smithay::wayland::dmabuf::get_dmabuf(buffer) {
        let Some(announced) = frame.dma else {
            return Err(Bad::Invalid("no DMA-BUF was offered for this frame"));
        };
        let size = dmabuf.size();
        if size.w != frame.region.size.w || size.h != frame.region.size.h {
            return Err(Bad::Invalid("the DMA-BUF has the wrong size"));
        }
        // The announced format, or the other one a capture is drawn into.
        let format = dmabuf.format();
        if !crate::capture::DMA_FORMATS.contains(&format.code) {
            return Err(Bad::Invalid("the DMA-BUF has the wrong format"));
        }
        if !capture.dma_allows(format.code, format.modifier) {
            debug!(announced = ?announced, got = ?format, "screencopy: DMA-BUF modifier");
            return Err(Bad::Unusable("the DMA-BUF's modifier cannot be drawn into"));
        }
        return Ok(true);
    }
    let fits = smithay::wayland::shm::with_buffer_contents(buffer, |_, len, data| {
        crate::capture::shm_fits(
            data.format,
            data.width,
            data.height,
            data.stride,
            data.offset,
            len,
            frame.region.size,
        )
    })
    .map_err(|_| Bad::Invalid("not a wl_shm or DMA-BUF buffer"))?;
    if fits {
        Ok(false)
    } else {
        Err(Bad::Invalid("the wl_shm buffer has the wrong size, format or stride"))
    }
}
