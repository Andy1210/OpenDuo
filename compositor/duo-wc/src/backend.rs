//! The display hardware: one DRM device, one output per connected connector.
//!
//! The Duo 2 has one display controller (msm DPU) with two DSI connectors, one
//! per panel, each 1344x1892 portrait in command mode. Every connected connector
//! gets its own CRTC, its own `DrmCompositor` (swapchain, page flips) and its own
//! render/vblank path; the GPU (freedreno) and the one `GlesRenderer` are shared.
//!
//! Everything is composited on the GPU. Client buffers are not scanned out
//! directly (the framebuffer exporter imports nothing): on this phone that is
//! a power question for later, not a correctness one, and composition is the path
//! that works for every client.

use std::os::unix::io::AsFd as _;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context as _, Result};
use smithay::backend::allocator::dmabuf::Dmabuf;
use smithay::backend::allocator::gbm::{GbmAllocator, GbmBufferFlags, GbmDevice};
use smithay::backend::allocator::Fourcc;
use smithay::backend::drm::compositor::{DrmCompositor, FrameFlags};
use smithay::backend::drm::exporter::gbm::{GbmFramebufferExporter, NodeFilter};
use smithay::backend::drm::{DrmDevice, DrmDeviceFd, DrmEvent, DrmNode, NodeType};
use smithay::backend::egl::{EGLContext, EGLDisplay};
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::backend::renderer::{ImportDma, ImportEgl};
use smithay::backend::session::libseat::LibSeatSession;
use smithay::backend::session::Session;
use smithay::output::{Mode as OutputMode, Output, PhysicalProperties, Scale, Subpixel};
use smithay::reexports::drm::control::atomic::AtomicModeReq;
use smithay::reexports::drm::control::{
    connector, crtc, property, AtomicCommitFlags, Device as _, Mode as DrmMode, ModeTypeFlags, ResourceHandle,
};
use smithay::reexports::rustix::fs::OFlags;
use smithay::utils::{DeviceFd, Transform};
use tracing::{debug, info, trace, warn};

use crate::state::DuoWc;

/// What `DrmCompositor` is instantiated with here.
pub type Compositor = DrmCompositor<
    GbmAllocator<DrmDeviceFd>,
    GbmFramebufferExporter<DrmDeviceFd>,
    Option<smithay::desktop::utils::OutputPresentationFeedback>,
    DrmDeviceFd,
>;

/// What the screen shows where no window is: a dark slate rather than black, so
/// "the compositor runs, nothing is mapped" looks different from "the panel is
/// off" (the panels are AMOLED: black is unlit).
pub const BACKGROUND: [f32; 4] = [0.043, 0.051, 0.067, 1.0];

/// One output's scan-out state.
pub struct Surface {
    /// The connector name (`DSI-1`), which is also the output's name and what the
    /// touch devices' `WL_OUTPUT` names.
    pub name: String,
    /// The CRTC this output is driven by.
    pub crtc: crtc::Handle,
    /// The connector.
    pub connector: connector::Handle,
    /// The output.
    pub output: Output,
    /// Plane assignment, swapchain and page flips.
    pub compositor: Compositor,
    /// A page flip is in flight; the next render waits for its vblank.
    pub frame_pending: bool,
    /// Something changed since the last frame was queued.
    pub redraw_needed: bool,
    /// A render is already scheduled for this turn of the event loop.
    pub redraw_queued: bool,
    /// Frames queued to the display since start, for the control socket.
    pub frames_queued: u64,
    /// Page flips completed since start.
    pub frames_presented: u64,
    /// The panel should be lit (what the power rules want).
    pub want_on: bool,
    /// The CRTC is enabled, or will be by the next commit. False after
    /// `DrmCompositor::clear`: CRTC inactive, connector detached, so the panel
    /// driver has switched the panel off.
    pub powered: bool,
    /// Times the panel was switched back on, for the control socket.
    pub power_cycles: u64,
    /// When the panel was last asked to come on, until its first frame is on
    /// screen (the page flip of the modeset that switched it on).
    pub power_on_at: Option<std::time::Instant>,
    /// The mode it was brought up with (for a wake commit of our own).
    pub drm_mode: DrmMode,
    /// How it was taken over at the start ([`takeover`]): a kept panel's
    /// first frame is a page flip; if the driver refuses that, one modeset.
    pub takeover: Takeover,
    /// When the output was brought up (the first frame's log).
    pub started_at: std::time::Instant,
    /// A wake commit of our own ([`wake_together`]) is under way: its
    /// completion event, not a frame of Smithay's, is the next vblank.
    pub waking: bool,
    /// An off commit of our own ([`power_off_together`]) is under way: its
    /// completion event is the next vblank.
    pub powering_off: bool,
    /// When that off commit was made, for the log.
    pub off_at: Option<std::time::Instant>,
    /// Going dark with the other panel ([`start_blackout`]): a black frame
    /// first, the off commit when both show black.
    pub blackout: bool,
    /// The black frame is queued / on screen.
    pub blackout_queued: bool,
    pub blackout_shown: bool,
    /// Woken with the other panel ([`wake_together`]): black until both are
    /// awake, then the picture on both at once.
    pub wake_group: bool,
    /// Woken with the other panel, its own wake commit not made yet: it
    /// waits until the panel before it is awake ([`wake_together`]).
    pub wake_queued: bool,
    /// When the wake commit was made, until the picture is on screen (log).
    pub wake_at: Option<std::time::Instant>,
    /// The frame in flight is the first picture after a group wake (log).
    pub content_queued: bool,
    /// The next frame is committed whole even if nothing changed: the first
    /// picture after a power-on, again ([`crate::power::REPEAT_SOON`]).
    pub repeat_full: bool,
    /// A frame is queued behind the one in flight (DrmCompositor commits it
    /// at that one's flip): a rotation's frames are rendered one ahead
    /// (crate::rotanim).
    pub ahead: bool,
}

impl Surface {
    /// The power-relevant state of the panel's hardware.
    pub fn hw(&self) -> crate::power::PanelHw {
        crate::power::PanelHw {
            want_on: self.want_on,
            powered: self.powered,
            waking: self.waking,
            powering_off: self.powering_off,
            blackout: self.blackout,
            wake_group: self.wake_group,
            first_picture_pending: self.power_on_at.is_some(),
            frames_presented: self.frames_presented,
        }
    }

    /// Whether the panel shows duo-wc's picture now
    /// ([`crate::power::shows_picture`]): touches there may reach clients.
    pub fn shows_picture(&self) -> bool {
        crate::power::shows_picture(self.hw())
    }
}

/// An opened DRM device and everything hanging off it.
pub struct Device {
    /// The DRM device itself.
    pub drm: DrmDevice,
    /// Buffer allocation, and the EGL display's platform.
    pub gbm: GbmDevice<DrmDeviceFd>,
    /// The renderer, shared by every output.
    pub renderer: GlesRenderer,
    /// One per connected connector.
    pub surfaces: Vec<Surface>,
}

/// What [`Tty::add_surface`] brings up.
pub struct NewSurface {
    /// The connector name (`DSI-1`, `DP-1`): the output's name.
    pub name: String,
    pub crtc: crtc::Handle,
    pub connector: connector::Handle,
    pub mode: DrmMode,
    /// How it is taken over (a panel the boot splash left lit is kept).
    pub takeover: Takeover,
    /// Its size in millimetres, as the connector reports it.
    pub physical_mm: (u32, u32),
    pub make: String,
    pub model: String,
    /// The output scale.
    pub scale: f64,
    /// Lit from the start (the panels); false: dark until the power rules
    /// switch it on (an external display, set_power).
    pub lit: bool,
}

/// A connector's blob property (the EDID), if it has one.
pub fn connector_blob(drm: &DrmDevice, handle: connector::Handle, name: &str) -> Option<Vec<u8>> {
    let (_, blob) = property_value(drm, handle, name)?;
    (blob != 0).then(|| drm.get_property_blob(blob).ok()).flatten()
}

/// The display side of the compositor.
pub struct Tty {
    session: LibSeatSession,
    /// The DRM device, once opened.
    pub device: Option<Device>,
    /// The DRM event source, handed to the event loop once after opening.
    pub notifier: Option<smithay::backend::drm::DrmDeviceNotifier>,
}

impl Tty {
    /// The display side, before anything is opened.
    pub fn new(session: LibSeatSession) -> Self {
        Tty { session, device: None, notifier: None }
    }

    /// Open the DRM device that has connectors, and bring up a renderer on it.
    pub fn open_device(&mut self) -> Result<()> {
        let path = display_device().context("no DRM device with a connector")?;
        info!(?path, "opening the display device");

        let fd = self
            .session
            .open(&path, OFlags::RDWR | OFlags::CLOEXEC | OFlags::NOCTTY | OFlags::NONBLOCK)
            .context("failed to open the DRM device through the session")?;
        let fd = DrmDeviceFd::new(DeviceFd::from(fd));

        // `false`: the connectors stay as they are. `true` had Smithay switch
        // every CRTC off first (one blocking commit: both panels' off
        // sequences), and each panel came back with a modeset of its own, so
        // the boot splash went black ~1.1 s before duo-wc's first frame
        // (2026-11-08: CRTCs off at 7.37 s, display on 8.46 / 8.67 s). Kept,
        // a panel the splash (or fbcon) left lit in the mode duo-wc picks
        // gets duo-wc's first frame as a plain page flip, no power cycle
        // (init_outputs keeps the CRTC's own mode); a CRTC that is off or in
        // another mode gets its modeset with the first frame, as before.
        let (drm, notifier) = DrmDevice::new(fd.clone(), false).context("DrmDevice::new")?;
        let gbm = GbmDevice::new(fd).context("GbmDevice::new")?;

        let egl_display = unsafe { EGLDisplay::new(gbm.clone()) }.context("EGLDisplay::new")?;
        let egl_context = EGLContext::new(&egl_display).context("EGLContext::new")?;
        let renderer = unsafe { GlesRenderer::new(egl_context) }.context("GlesRenderer::new")?;

        self.device = Some(Device { drm, gbm, renderer, surfaces: Vec::new() });
        self.notifier = Some(notifier);
        Ok(())
    }

    /// Bring up every connected connector at its preferred mode, each on a CRTC
    /// of its own, and return the new outputs.
    ///
    /// A connector keeps the CRTC the console left it on when that one is free,
    /// so the takeover from fbcon does not shuffle the display pipes; otherwise it
    /// takes the first free CRTC its encoders can drive.
    pub fn init_outputs(&mut self, scale: f64) -> Result<Vec<Output>> {
        let handles: Vec<connector::Handle> = {
            let device = self.device.as_ref().ok_or_else(|| anyhow!("no device opened"))?;
            device.drm.resource_handles().context("resource_handles")?.connectors().to_vec()
        };
        let mut outputs = Vec::new();
        for handle in handles {
            if let Some(new) = self.plan_panel(handle, scale) {
                outputs.push(self.add_surface(new)?);
            }
        }
        Ok(outputs)
    }

    /// How a connected panel is brought up at the start (its mode, its CRTC,
    /// the takeover), or None: not connected, an external display, already
    /// up, no mode or no CRTC.
    fn plan_panel(&self, handle: connector::Handle, scale: f64) -> Option<NewSurface> {
        let device = self.device.as_ref()?;
        let resources = device.drm.resource_handles().ok()?;
        let Ok(connector) = device.drm.get_connector(handle, true) else {
            return None;
        };
        if connector.state() != connector::State::Connected {
            return None;
        }
        let name = format!("{}-{}", connector.interface().as_str(), connector.interface_id());
        if device.surfaces.iter().any(|s| s.connector == handle) {
            return None;
        }
        if crate::extout::is_external(&name) {
            // An external display comes up through crate::extout (its
            // own mode, scale and CRTC rules), right after the panels.
            return None;
        }

        let Some(preferred) = connector
            .modes()
            .iter()
            .find(|mode| mode.mode_type().contains(ModeTypeFlags::PREFERRED))
            .or_else(|| connector.modes().first())
            .copied()
        else {
            warn!(connector = name, "connected but advertises no mode");
            return None;
        };

        let used: Vec<crtc::Handle> = device.surfaces.iter().map(|s| s.crtc).collect();
        let current = connector
            .current_encoder()
            .and_then(|e| device.drm.get_encoder(e).ok())
            .and_then(|e| e.crtc())
            .filter(|crtc| !used.contains(crtc));
        let crtc = current.or_else(|| {
            connector
                .encoders()
                .iter()
                .filter_map(|handle| device.drm.get_encoder(*handle).ok())
                .flat_map(|encoder| resources.filter_crtcs(encoder.possible_crtcs()))
                .find(|crtc| !used.contains(crtc))
        });
        let Some(crtc) = crtc else {
            warn!(connector = name, "no free CRTC for the connector");
            return None;
        };

        // What the boot splash (or fbcon, or a duo-wc before this one)
        // left on the CRTC: lit with the connector in the mode picked
        // here, the CRTC's own mode is taken word for word, so Smithay
        // finds nothing to change and the first frame is a page flip.
        let on_crtc = current == Some(crtc);
        let active = property_value(&device.drm, crtc, "ACTIVE").map(|(_, v)| v != 0) == Some(true);
        let crtc_mode = device.drm.get_crtc(crtc).ok().and_then(|info| info.mode()).filter(|_| active);
        let (mode, takeover) = takeover_mode(preferred, crtc_mode, on_crtc);
        info!(
            connector = name,
            ?crtc,
            mode = format!("{}x{}@{}", mode.size().0, mode.size().1, mode.vrefresh()),
            takeover = takeover.describe(),
            "bringing up the output"
        );
        Some(NewSurface {
            name: name.clone(),
            crtc,
            connector: connector.handle(),
            mode,
            takeover,
            physical_mm: connector.size().unwrap_or((0, 0)),
            make: "Microsoft".into(),
            model: format!("Surface Duo 2 {name}"),
            scale,
            lit: true,
        })
    }

    /// Bring one output up on its CRTC: its DRM surface, its `Output`, its
    /// `DrmCompositor` (the panels at the start; an external display when it
    /// is plugged, crate::extout). Nothing is committed here: the first frame
    /// (or, for one that starts unlit, the first after the power rules light
    /// it) does that.
    pub fn add_surface(&mut self, new: NewSurface) -> Result<Output> {
        let device = self.device.as_mut().ok_or_else(|| anyhow!("no device opened"))?;
        let NewSurface { name, crtc, connector, mode, takeover, physical_mm, make, model, scale, lit } = new;
        let drm_surface = device
            .drm
            .create_surface(crtc, mode, &[connector])
            .with_context(|| format!("create_surface for {name}"))?;

        let output = Output::new(
            name.clone(),
            PhysicalProperties {
                size: (physical_mm.0 as i32, physical_mm.1 as i32).into(),
                subpixel: Subpixel::Unknown,
                make,
                model,
                serial_number: "Unknown".into(),
            },
        );
        let output_mode = OutputMode {
            size: (mode.size().0 as i32, mode.size().1 as i32).into(),
            refresh: (mode.vrefresh() * 1000) as i32,
        };
        output.change_current_state(
            Some(output_mode),
            Some(Transform::Normal),
            Some(output_scale(scale)),
            Some((0, 0).into()),
        );
        output.set_preferred(output_mode);

        let allocator =
            GbmAllocator::new(device.gbm.clone(), GbmBufferFlags::RENDERING | GbmBufferFlags::SCANOUT);
        let compositor = DrmCompositor::new(
            &output,
            drm_surface,
            None,
            allocator,
            GbmFramebufferExporter::new(device.gbm.clone(), NodeFilter::None),
            [Fourcc::Xrgb8888, Fourcc::Argb8888],
            device.renderer.dmabuf_formats(),
            device.drm.cursor_size(),
            Some(device.gbm.clone()),
        )
        .with_context(|| format!("DrmCompositor::new for {name}"))?;

        device.surfaces.push(Surface {
            name,
            crtc,
            connector,
            output: output.clone(),
            compositor,
            frame_pending: false,
            redraw_needed: lit,
            redraw_queued: false,
            frames_queued: 0,
            frames_presented: 0,
            want_on: lit,
            powered: lit,
            power_cycles: 0,
            // A panel that is off comes on with the first frame (its
            // modeset): that frame is repeated like any after a power-on.
            power_on_at: (lit && takeover != Takeover::Keep).then(std::time::Instant::now),
            drm_mode: mode,
            takeover,
            started_at: std::time::Instant::now(),
            repeat_full: false,
            waking: false,
            powering_off: false,
            blackout: false,
            blackout_queued: false,
            blackout_shown: false,
            wake_queued: false,
            wake_group: false,
            wake_at: None,
            content_queued: false,
            ahead: false,
            off_at: None,
        });
        Ok(output)
    }

    /// Take an output's scan-out down for good (an external display that
    /// went away): its CRTC switched off (a blocking commit, waiting for a
    /// frame in flight), then the surface dropped.
    pub fn remove_surface(&mut self, name: &str) {
        let Some(device) = self.device.as_mut() else {
            return;
        };
        let Some(index) = device.surfaces.iter().position(|s| s.name == name) else {
            return;
        };
        let mut surface = device.surfaces.remove(index);
        if surface.powered {
            if let Err(err) = surface.compositor.clear() {
                warn!(output = name, ?err, "could not switch the CRTC off");
            }
        }
        info!(output = name, crtc = ?surface.crtc, "scan-out gone");
        drop(surface);
    }

    /// The CRTCs of every output.
    pub fn crtcs(&self) -> Vec<crtc::Handle> {
        self.device.as_ref().map(|d| d.surfaces.iter().map(|s| s.crtc).collect()).unwrap_or_default()
    }

    /// An output's scan-out state, by CRTC.
    pub fn surface_mut(&mut self, crtc: crtc::Handle) -> Option<&mut Surface> {
        self.device.as_mut()?.surfaces.iter_mut().find(|s| s.crtc == crtc)
    }

    /// The scan-out state of every output.
    pub fn surfaces(&self) -> &[Surface] {
        self.device.as_ref().map(|d| d.surfaces.as_slice()).unwrap_or(&[])
    }

    /// The formats clients may hand us.
    pub fn renderer_formats(&self) -> Vec<smithay::backend::allocator::Format> {
        self.device
            .as_ref()
            .map(|device| device.renderer.dmabuf_formats().into_iter().collect())
            .unwrap_or_default()
    }

    /// The formats the renderer can draw into (a screen capture's
    /// DMA-BUFs, crate::capture).
    pub fn render_formats(&self) -> Vec<smithay::backend::allocator::Format> {
        self.device
            .as_ref()
            .map(|device| device.renderer.egl_context().dmabuf_render_formats().iter().copied().collect())
            .unwrap_or_default()
    }

    /// The render node clients should allocate on.
    pub fn render_node(&self) -> Option<DrmNode> {
        let card = DrmNode::from_file(self.device.as_ref()?.gbm.as_fd()).ok();
        if let Some(node) =
            card.and_then(|node| node.node_with_type(NodeType::Render)).and_then(|node| node.ok())
        {
            return Some(node);
        }
        card
    }

    /// Check that a client's dmabuf is renderable.
    pub fn import_dmabuf(&mut self, dmabuf: &Dmabuf) -> Result<()> {
        let device = self.device.as_mut().ok_or_else(|| anyhow!("no device opened"))?;
        device.renderer.import_dmabuf(dmabuf, None).map(|_| ()).context("the renderer refused the dmabuf")
    }

    /// Bind the renderer to the display so clients can use EGL (wl_drm).
    pub fn bind_wl_display(&mut self, display: &smithay::reexports::wayland_server::DisplayHandle) {
        if let Some(device) = self.device.as_mut() {
            if let Err(err) = device.renderer.bind_wl_display(display) {
                warn!(?err, "EGL hardware acceleration is unavailable to clients");
            }
        }
    }

    /// Whether the session currently owns the device.
    pub fn is_active(&self) -> bool {
        self.session.is_active()
    }
}

/// The scale in the form Smithay wants: an integer stays an integer, so clients
/// without fractional-scale support are not scaled down needlessly.
pub fn output_scale(scale: f64) -> Scale {
    if scale.fract() == 0.0 {
        Scale::Integer(scale as i32)
    } else {
        Scale::Fractional(scale)
    }
}

/// A mode's timings: what makes two modes the same picture (not their name
/// or their type bits).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Timing {
    /// Pixel clock, kHz.
    pub clock: u32,
    /// Active width and height.
    pub size: (u16, u16),
    /// Horizontal sync start, end, total.
    pub hsync: (u16, u16, u16),
    /// Vertical sync start, end, total.
    pub vsync: (u16, u16, u16),
    /// Horizontal skew.
    pub hskew: u16,
    /// Vertical scan.
    pub vscan: u16,
    /// Refresh rate, Hz.
    pub vrefresh: u32,
    /// The mode flags (sync polarity, interlace...).
    pub flags: u32,
}

impl Timing {
    /// The timings of a DRM mode.
    pub fn of(mode: &DrmMode) -> Self {
        Timing {
            clock: mode.clock(),
            size: mode.size(),
            hsync: mode.hsync(),
            vsync: mode.vsync(),
            hskew: mode.hskew(),
            vscan: mode.vscan(),
            vrefresh: mode.vrefresh(),
            flags: mode.flags().bits(),
        }
    }
}

/// How duo-wc takes a panel over at its start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Takeover {
    /// Lit, on this connector, in the mode duo-wc picks (the boot splash's,
    /// fbcon's): kept, the first frame is a page flip, no power cycle.
    Keep,
    /// Lit in another mode (a refresh rate chosen in Duo Settings after the
    /// splash came up): the first frame is a modeset.
    OtherMode,
    /// Off, or driving another connector: the first frame is a modeset
    /// (a start without the splash, after `systemctl stop duo-wc`).
    Off,
}

impl Takeover {
    /// For the log.
    pub fn describe(self) -> &'static str {
        match self {
            Takeover::Keep => "lit in this mode: kept (page flip, no power cycle)",
            Takeover::OtherMode => "lit in another mode: modeset",
            Takeover::Off => "off: modeset",
        }
    }
}

/// How a panel is taken over: `preferred` is the mode duo-wc picks (the
/// driver's preferred one, which the refresh rate in Duo Settings decides),
/// `crtc` the timings the CRTC is active with now (None: inactive),
/// `on_crtc` whether the connector is on that CRTC.
pub fn takeover(preferred: Timing, crtc: Option<Timing>, on_crtc: bool) -> Takeover {
    match crtc {
        Some(current) if on_crtc && current == preferred => Takeover::Keep,
        Some(_) if on_crtc => Takeover::OtherMode,
        _ => Takeover::Off,
    }
}

/// The mode to bring a panel up with: the CRTC's own (word for word, so
/// Smithay sees no change: its mode comparison takes the name and the type
/// bits too) when [`takeover`] keeps it, else the preferred one.
fn takeover_mode(preferred: DrmMode, crtc: Option<DrmMode>, on_crtc: bool) -> (DrmMode, Takeover) {
    let how = takeover(Timing::of(&preferred), crtc.as_ref().map(Timing::of), on_crtc);
    match (how, crtc) {
        (Takeover::Keep, Some(current)) => (current, how),
        _ => (preferred, how),
    }
}

/// The DRM device that has connectors.
fn display_device() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = std::fs::read_dir("/dev/dri")
        .ok()?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(|name| name.starts_with("card"))
                .unwrap_or(false)
        })
        .collect();
    candidates.sort();
    candidates.into_iter().find(|path| has_connector(path))
}

/// Whether a card has connectors: from sysfs (`/sys/class/drm/card0-DSI-1`),
/// not by opening the card. With nobody else holding the device (no boot
/// splash, a restart of duo-wc), closing it again was the device's last
/// close, and the kernel then put its console's picture back on the panels
/// (the fbdev restore, a commit on every CRTC) right before duo-wc took them.
fn has_connector(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|card| has_connector_in(Path::new("/sys/class/drm"), card))
}

fn has_connector_in(sysfs: &Path, card: &str) -> bool {
    let prefix = format!("{card}-");
    std::fs::read_dir(sysfs)
        .map(|dir| {
            dir.filter_map(Result::ok)
                .any(|entry| entry.file_name().to_str().is_some_and(|n| n.starts_with(&prefix)))
        })
        .unwrap_or(false)
}

/// How long to wait before trying a frame again after one failed.
const RETRY_AFTER: std::time::Duration = std::time::Duration::from_millis(50);

/// ... and after the display said it is busy (EBUSY: a commit in flight).
const RETRY_BUSY: std::time::Duration = std::time::Duration::from_millis(8);

/// Whether a frame error is the kernel's EBUSY (the display is finishing an
/// earlier commit), which passes by itself.
fn is_busy(err: &impl std::fmt::Debug) -> bool {
    format!("{err:?}").contains("ResourceBusy")
}

/// Render one output, if anything changed and no frame is in flight there.
pub fn render(state: &mut DuoWc, crtc: crtc::Handle) {
    if !state.tty.is_active() {
        return;
    }
    let (full_redraw, scanout) = (state.full_redraw, state.scanout);
    // The frame's CPU time and when it began, for a rotation's frame timing
    // (crate::turnlog).
    let render_started = std::time::Instant::now();
    let render_started_mono = crate::turnlog::monotonic_now();
    // The overview's cards are flying to their places: the next vblank
    // draws again.
    state.carry_tick();
    let mut animating = state.overview_animating() || state.carry_animating();
    let carry = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .map(|s| s.output.clone())
        .and_then(|output| state.carry_plan(&output));
    let plan = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .map(|s| s.output.clone())
        .and_then(|output| state.overview_plan(&output));
    let locked = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .map(|s| s.output.clone())
        .and_then(|output| state.lock.locked.then(|| state.lock_surface(&output)));
    let split = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .map(|s| s.output.clone())
        .and_then(|output| state.split_plan(&output));
    let ambient_name = state.tty.surfaces().iter().find(|s| s.crtc == crtc).map(|s| s.output.name());
    let ambient_now =
        ambient_name.as_deref().map(|n| state.ambient_for(n)).unwrap_or(crate::render::Ambient::Off);
    let ambient = ambient_now != crate::render::Ambient::Off;
    // X11's menus and tooltips (crate::xwayland).
    let x11_popups = state.x11_overlays();
    // An ambient screen shows over a lock (only the shell's strip, on black).
    let locked = if ambient { None } else { locked };
    // A spanning window is paced (frame callbacks, presentation feedback) by
    // the first lit panel.
    let spans_paced_here = state
        .layout_order()
        .into_iter()
        .find(|o| state.is_lit(&o.name()))
        .map(|o| state.tty.surfaces().iter().any(|s| s.crtc == crtc && s.output == o))
        .unwrap_or(false);
    // A rotation turning this panel's old picture (crate::rotanim). Its
    // frames are rendered one ahead: while one is in flight, the next is
    // rendered and queued behind it (committed at its flip), so a frame
    // that takes long to render is not late on the panel.
    let render_ahead = state.rotation_anim.render_ahead;
    let rotating = state.tty.surfaces().iter().find(|s| s.crtc == crtc).and_then(|s| {
        let may_ahead = render_ahead
            && state.rotation_anim.anims.contains_key(&s.name)
            && !s.ahead
            && !s.blackout
            && !s.waking
            && !s.wake_group;
        (s.want_on && s.powered && s.redraw_needed && (!s.frame_pending || may_ahead))
            .then(|| (s.output.clone(), s.frame_pending))
    });
    // During the turn its pictures are the whole frame (`cover`: the scene
    // is not built, no client surface imported); after it, the old picture
    // fades in front of the scene (`front`).
    let (front, cover) = match rotating.map(|(o, ahead)| state.rotation_frame(&o, true, ahead)) {
        Some(crate::state::RotationFrame::Front(front)) => (Some(*front), None),
        Some(crate::state::RotationFrame::Cover(cover)) => (None, Some(cover)),
        _ => (None, None),
    };
    // A panel woken with the other one stays black until that one is awake
    // too (wake_done), so the two show the picture together.
    let group_waking = state.tty.surfaces().iter().any(|s| s.wake_group && s.waking);
    if front.is_some() || cover.is_some() {
        animating = true;
    }
    // Laptop mode's keyboard panel shows its keys and touchpad only
    // (crate::laptopmode::keyboard_scene): never the windows, the bars,
    // the home screen or the wallpaper mapped on that panel.
    let laptop_keys = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .is_some_and(|s| state.laptop_shows(&s.output.name()));
    let laptop_caps = laptop_keys && state.laptop_caps();
    if laptop_keys {
        state.laptop_prepare();
    }
    // A display that mirrors a panel (crate::mirrorlive): the panel's
    // picture, built only for a frame that is going to be rendered.
    let mirroring =
        state.tty.surfaces().iter().find(|s| s.crtc == crtc).is_some_and(|s| state.is_mirror(&s.name));
    let mirror_output = state
        .tty
        .surfaces()
        .iter()
        .find(|s| s.crtc == crtc)
        .filter(|s| mirroring && s.want_on && s.powered && s.redraw_needed && !s.frame_pending)
        .map(|s| s.output.clone());
    let mirror = mirror_output.and_then(|o| state.mirror_elements(&o));
    let Some(device) = state.tty.device.as_mut() else {
        return;
    };
    let Some(surface) = device.surfaces.iter_mut().find(|s| s.crtc == crtc) else {
        return;
    };
    if (!surface.want_on && !surface.blackout) || !surface.powered {
        // A dark panel draws nothing, and its clients get no frame callbacks,
        // so they stop drawing too.
        return;
    }
    if surface.blackout && surface.blackout_queued {
        // Its black frame is out: nothing more before the off commit.
        return;
    }
    if surface.wake_group && group_waking {
        // Woken before the other panel: its CRTC has no planes yet, so it
        // shows black (unlit) until both are awake; then both get their
        // first picture in the same turn (wake_done). A black frame here
        // would hold its picture back by one frame.
        surface.redraw_needed = true;
        return;
    }
    let black = surface.blackout;
    let rotating = front.is_some() || cover.is_some();
    let may_ahead = render_ahead
        && rotating
        && !surface.ahead
        && !surface.blackout
        && !surface.waking
        && !surface.wake_group;
    if (surface.frame_pending && !may_ahead) || !surface.redraw_needed {
        trace!(
            output = surface.name,
            frame_pending = surface.frame_pending,
            redraw_needed = surface.redraw_needed,
            "skipping a render"
        );
        return;
    }

    let output = surface.output.clone();
    let overview = plan.as_ref().map(|plan| (&mut state.taskview.gfx, plan));
    let split = split.as_ref().map(|plan| (&mut state.split_gfx, plan));
    let carry = carry.as_ref().map(|plan| (&mut state.carry.gfx, plan));
    let elements = if black {
        Vec::new()
    } else if let Some(cover) = cover {
        cover
    } else if laptop_keys {
        let size = state.space.output_geometry(&output).map(|g| g.size);
        crate::laptopmode::keyboard_scene(
            &mut state.laptop,
            &mut device.renderer,
            &output,
            size,
            laptop_caps,
            front,
        )
    } else if mirroring {
        // Never the display's own (desk) scene: black when no picture.
        mirror.unwrap_or_default()
    } else {
        let mut elements = crate::render::elements(
            &mut device.renderer,
            &state.space,
            &output,
            overview,
            split,
            locked,
            ambient_now,
            carry,
            front,
            &x11_popups,
        );
        // The touchpad panel's veil (crate::trackpad), in front of its scene.
        if let Some(size) = state.space.output_geometry(&output).map(|g| g.size) {
            let size = size.to_physical_precise_round(output.current_scale().fractional_scale());
            if let Some(veil) = crate::trackpad::veil_element(&mut state.trackpad, &output, size) {
                elements.insert(0, veil);
            }
        }
        // The pen's hover dot (crate::tablet), and the pointer's cursor in
        // front of everything (crate::pointer).
        let dot = crate::tablet::dot_elements(&state.space, &mut device.renderer, &output);
        if !dot.is_empty() {
            elements.splice(0..0, dot);
        }
        let has_pointer = state.seat.get_pointer().is_some();
        let cursor = crate::pointer::cursor_elements(
            &mut state.pointer,
            &state.space,
            &mut device.renderer,
            &output,
            has_pointer,
        );
        if !cursor.is_empty() {
            elements.splice(0..0, cursor);
        }
        elements
    };
    let build_ms = render_started.elapsed().as_secs_f64() * 1000.0;
    let mut retry = false;
    let mut retry_after = RETRY_AFTER;
    // A frame queued (Some(false)) or refused as busy (Some(true)), for a
    // rotation's frame timing.
    let mut queued = None;
    let mut draw_ms = 0.0;
    let mut went_ahead = false;
    // Queued straight onto the panel during a turn: render the next one
    // ahead at once.
    let mut fill_ahead = false;
    if full_redraw {
        // Ages to 0: nothing of an older frame is reused.
        surface.compositor.reset_buffer_ages();
    }
    let flags = if scanout { FrameFlags::DEFAULT } else { FrameFlags::empty() };
    if surface.repeat_full && !surface.frame_pending {
        // The first picture after a power-on again (power::REPEAT_SOON):
        // reset_state makes the next frame a full one, never "empty".
        surface.repeat_full = false;
        debug!(output = surface.name, "the first picture since the panel came on, committed again");
        if let Err(err) = surface.compositor.reset_state() {
            warn!(output = surface.name, ?err, "failed to re-read the CRTC state");
        }
    }

    // Black around an ambient strip (the panels are AMOLED: unlit).
    // ... and around a mirror's picture (letterboxing).
    let clear = if ambient || black || mirroring { [0.0, 0.0, 0.0, 1.0] } else { BACKGROUND };
    let draw_started = std::time::Instant::now();
    match surface.compositor.render_frame(&mut device.renderer, &elements, clear, flags) {
        Ok(result) => {
            draw_ms = draw_started.elapsed().as_secs_f64() * 1000.0;
            let feedback = (!result.is_empty).then(|| {
                crate::render::take_feedback(&state.space, &output, spans_paced_here, &result.states)
            });
            // The commit that carries this frame is a modeset (the CRTC
            // is off or in another mode for Smithay), not a page flip.
            let modeset = surface.compositor.surface().commit_pending();
            if result.is_empty && surface.power_on_at.is_some() && surface.compositor.reset_state().is_ok() {
                // Nothing changed for Smithay, but the panel came on and has
                // not had a picture since: commit it whole (reset_state: the
                // next frame is a full one), or the panel would stay black
                // on a still screen.
                debug!(
                    output = surface.name,
                    "an empty frame on a panel that just came on: a full one instead"
                );
                retry = true;
                retry_after = RETRY_BUSY;
            } else if result.is_empty {
                surface.redraw_needed = false;
            } else if let Err(err) = surface.compositor.queue_frame(feedback) {
                if is_busy(&err) {
                    // The display is still finishing another commit (two
                    // panels' first frames after one wake commit): soon.
                    debug!(output = surface.name, "display busy; frame again shortly");
                    retry_after = RETRY_BUSY;
                    queued = Some(true);
                } else if surface.frames_queued == 0 && surface.takeover == Takeover::Keep && !modeset {
                    // The first frame onto a panel kept lit from the boot
                    // splash, as a page flip, refused: switch it off and
                    // let the next frame bring it up with a modeset, as a
                    // start without the splash does.
                    warn!(
                        output = surface.name,
                        ?err,
                        "the first frame without a modeset was refused: one modeset instead"
                    );
                    surface.takeover = Takeover::Off;
                    if let Err(err) = surface.compositor.clear() {
                        warn!(output = surface.name, ?err, "failed to switch the panel off for the modeset");
                    }
                    surface.power_on_at = Some(std::time::Instant::now());
                    retry_after = RETRY_BUSY;
                } else {
                    warn!(output = surface.name, ?err, "failed to queue a frame");
                }
                retry = true;
            } else {
                if surface.frame_pending {
                    // Behind the frame in flight (render ahead).
                    surface.ahead = true;
                    went_ahead = true;
                } else {
                    surface.frame_pending = true;
                    fill_ahead = may_ahead;
                }
                surface.redraw_needed = animating;
                surface.frames_queued += 1;
                queued = Some(false);
                if ambient_now == crate::render::Ambient::Strip && !black {
                    state.ambient_strip.entry(output.name()).or_insert(1);
                }
                if surface.blackout {
                    surface.blackout_queued = true;
                    surface.redraw_needed = false;
                }
                if !black && surface.wake_at.is_some() && !surface.wake_group {
                    surface.content_queued = true;
                }
                if surface.frames_queued == 1 {
                    info!(
                        output = surface.name,
                        modeset,
                        ms = surface.started_at.elapsed().as_millis() as u64,
                        "first frame queued"
                    );
                }
            }
        }
        Err(err) => {
            warn!(output = surface.name, ?err, "failed to render a frame");
            retry = true;
        }
    }

    if let Some(busy) = queued {
        let name = output.name();
        let running = state.rotation_anim.anims.contains_key(&name);
        let ms = render_started.elapsed().as_secs_f64() * 1000.0;
        let lag_ms = state
            .rotation_anim
            .last_flip
            .get(&name)
            .map(|flip| render_started_mono.saturating_sub(*flip).as_secs_f64() * 1000.0)
            .unwrap_or(0.0);
        state.rotation_anim.logs.frame_queued(&name, ms, build_ms, lag_ms, busy, running);
        // A dropped window's flight (crate::windrag), timed the same way.
        let flying = state.flight_running();
        state.carry.flight_logs.frame_queued(&name, ms, build_ms, lag_ms, busy, flying);
        if !busy {
            let commit_ms = (ms - build_ms - draw_ms).max(0.0);
            state.rotation_anim.logs.frame_parts(&name, draw_ms, commit_ms, went_ahead);
            state.carry.flight_logs.frame_parts(&name, draw_ms, commit_ms, went_ahead);
        }
    }
    state.flight_check();

    let held = crate::render::send_frames(state, &output);
    // Their fifo barriers are released with the frame callbacks: the
    // content updates that waited on them are applied now (and drawn at the
    // next refresh).
    state.blockers_cleared(held);
    // Screen captures of this output waiting for damage may have some now.
    crate::capture::output_rendered(state, &output);

    if fill_ahead {
        state.queue_redraw(crtc);
    }

    if retry {
        let timer = smithay::reexports::calloop::timer::Timer::from_duration(retry_after);
        let _ = state.loop_handle.insert_source(timer, move |_, _, state| {
            state.queue_redraw(crtc);
            smithay::reexports::calloop::timer::TimeoutAction::Drop
        });
    }
}

/// A page flip completed on one of the CRTCs. `metadata` carries the
/// kernel's timestamp of the flip and its sequence number, which go to the
/// clients' presentation feedback.
pub fn on_drm_event(
    state: &mut DuoWc,
    event: DrmEvent,
    metadata: &mut Option<smithay::backend::drm::DrmEventMetadata>,
) {
    match event {
        DrmEvent::VBlank(crtc) => {
            trace!(?crtc, "vblank");
            if state.tty.surface_mut(crtc).map(|s| s.waking).unwrap_or(false) {
                wake_done(state, crtc, "its event");
                return;
            }
            if state.tty.surface_mut(crtc).map(|s| s.powering_off).unwrap_or(false) {
                off_done(state, crtc, "its event");
                return;
            }
            if let Some(name) = state.tty.surface_mut(crtc).map(|s| s.name.clone()) {
                if state.ambient_strip.get(&name) == Some(&1) {
                    state.ambient_strip.insert(name.clone(), 2);
                    let ms = state.ambient_started.map(|t| t.elapsed().as_millis()).unwrap_or(0);
                    info!(output = name, ms, "ambient strip on screen");
                }
            }
            if let Some(name) = state.tty.surface_mut(crtc).map(|s| s.name.clone()) {
                // A rotation's frame timing (crate::turnlog): the kernel's
                // flip time and vblank sequence.
                let (at, sequence) = match metadata.as_ref() {
                    Some(m) => match m.time {
                        smithay::backend::drm::DrmEventTime::Monotonic(tp) if !tp.is_zero() => {
                            (tp, m.sequence)
                        }
                        _ => (crate::turnlog::monotonic_now(), m.sequence),
                    },
                    None => (crate::turnlog::monotonic_now(), 0),
                };
                // The frames of a turn are timed from the last flip.
                match state.rotation_anim.last_flip.get_mut(&name) {
                    Some(last) => *last = at,
                    None => {
                        state.rotation_anim.last_flip.insert(name.clone(), at);
                    }
                }
                if let Some((log, s)) = state.rotation_anim.logs.flipped(&name, at, sequence) {
                    info!(
                        output = name,
                        frames = s.frames,
                        first_ms = format!("{:.1}", s.first_ms),
                        capture_ms = format!("{:.1}", log.capture_ms),
                        max_gap_ms = format!("{:.1}", s.max_gap_ms),
                        late = s.late,
                        missed_vblanks = s.missed_vblanks,
                        busy = log.busy,
                        max_render_ms = format!("{:.1}", s.max_render_ms),
                        mean_render_ms = format!("{:.1}", s.mean_render_ms),
                        max_build_ms = format!("{:.1}", s.max_build_ms),
                        max_draw_ms = format!("{:.1}", s.max_draw_ms),
                        max_commit_ms = format!("{:.1}", s.max_commit_ms),
                        max_lag_ms = format!("{:.1}", s.max_lag_ms),
                        ahead = log.ahead,
                        gaps = ?s.gaps,
                        render = ?log.render_ms.iter().map(|v| (v * 10.0).round() / 10.0).collect::<Vec<_>>(),
                        build = ?log.build_ms.iter().map(|v| (v * 10.0).round() / 10.0).collect::<Vec<_>>(),
                        draw = ?log.draw_ms.iter().map(|v| (v * 10.0).round() / 10.0).collect::<Vec<_>>(),
                        commit = ?log.commit_ms.iter().map(|v| (v * 10.0).round() / 10.0).collect::<Vec<_>>(),
                        lag = ?log.lag_ms.iter().map(|v| (v * 10.0).round() / 10.0).collect::<Vec<_>>(),
                        "rotation: frame timing"
                    );
                }
                state.flight_flipped(&name, at, sequence);
            }
            let mut blackout_step = None;
            // The first picture since the panel came on is on screen: it is
            // committed again, twice (power::REPEAT_SOON, REPEAT_LATER).
            let mut repeat = false;
            if let Some(surface) = state.tty.surface_mut(crtc) {
                surface.frame_pending = false;
                surface.frames_presented += 1;
                if surface.content_queued {
                    surface.content_queued = false;
                    if let Some(at) = surface.wake_at.take() {
                        info!(
                            output = surface.name,
                            ms = at.elapsed().as_millis() as u64,
                            "panel on: the picture on screen (with the other panel)"
                        );
                    }
                }
                if surface.frames_presented == 1 {
                    info!(output = surface.name, "first frame on screen");
                }
                if let Some(at) = surface.power_on_at.take() {
                    info!(
                        output = surface.name,
                        ms = at.elapsed().as_millis() as u64,
                        "panel on: first frame on screen"
                    );
                    repeat = true;
                }
                match surface.compositor.frame_submitted() {
                    Ok(Some(Some(mut feedback))) => {
                        // wp_presentation: when the frame reached the panel
                        // (the flip's timestamp, CLOCK_MONOTONIC) and the
                        // refresh it runs at.
                        let refresh = surface
                            .output
                            .current_mode()
                            .filter(|m| m.refresh > 0)
                            .map(|m| std::time::Duration::from_secs_f64(1000.0 / m.refresh as f64))
                            .map(smithay::wayland::presentation::Refresh::fixed)
                            .unwrap_or(smithay::wayland::presentation::Refresh::Unknown);
                        let stamp = metadata.as_ref().and_then(|m| match m.time {
                            smithay::backend::drm::DrmEventTime::Monotonic(tp) if !tp.is_zero() => Some(tp),
                            _ => None,
                        });
                        let seq = metadata.as_ref().map(|m| m.sequence as u64).unwrap_or(0);
                        use smithay::reexports::wayland_protocols::wp::presentation_time::server::wp_presentation_feedback::Kind;
                        match stamp {
                            Some(tp) => feedback.presented::<_, smithay::utils::Monotonic>(
                                tp,
                                refresh,
                                seq,
                                Kind::Vsync | Kind::HwClock | Kind::HwCompletion,
                            ),
                            None => feedback.presented(
                                smithay::utils::Clock::<smithay::utils::Monotonic>::new().now(),
                                refresh,
                                seq,
                                Kind::Vsync,
                            ),
                        }
                    }
                    Ok(_) => {}
                    Err(err) => {
                        warn!(output = surface.name, ?err, "frame_submitted failed");
                        if surface.ahead {
                            // The frame queued behind it was not committed.
                            surface.ahead = false;
                            surface.redraw_needed = true;
                        }
                    }
                }
                if surface.ahead {
                    // The frame queued behind it is in flight now.
                    surface.ahead = false;
                    surface.frame_pending = true;
                }
                if surface.blackout {
                    if surface.blackout_queued {
                        surface.blackout_shown = true;
                    }
                    blackout_step = Some(surface.blackout_shown);
                } else if !surface.want_on && surface.powered && !surface.frame_pending {
                    // The switch-off waited for this flip (and for a frame
                    // queued behind it, now in flight: its flip).
                    switch_off_after_wait(state, crtc);
                    return;
                }
            }
            if repeat {
                repeat_picture_after(state, crtc, crate::power::REPEAT_SOON);
                repeat_picture_after(state, crtc, crate::power::REPEAT_LATER);
            }
            // Every lit panel has shown a picture: the boot splash may go
            // (systemd's READY=1; crate::lock::notify_ready).
            if state.tty.surfaces().iter().any(|s| s.frames_presented > 0)
                && state.tty.surfaces().iter().filter(|s| s.want_on).all(|s| s.frames_presented > 0)
            {
                crate::lock::notify_ready("first frame on every lit panel");
            }
            match blackout_step {
                Some(true) => blackout_check(state, false),
                Some(false) => render(state, crtc),
                None => render(state, crtc),
            }
        }
        DrmEvent::Error(err) => {
            warn!(?err, "DRM error");
        }
    }
}

/// The session gained or lost the device (a VT switch, logind).
pub fn on_session_event(state: &mut DuoWc, active: bool) {
    debug!(active, "session activity changed");
    let Some(device) = state.tty.device.as_mut() else {
        return;
    };
    if active {
        if let Err(err) = device.drm.activate(false) {
            warn!(?err, "failed to activate the DRM device");
        }
        for surface in device.surfaces.iter_mut() {
            surface.frame_pending = false;
            surface.ahead = false;
            surface.redraw_needed = true;
            if let Err(err) = surface.compositor.reset_state() {
                warn!(output = surface.name, ?err, "failed to reset the compositor state");
            }
            if !surface.want_on {
                // Whoever had the device may have lit it again (fbcon).
                surface.powered = true;
                power_off_now(surface);
            }
        }
        state.queue_redraw_all();
    } else {
        device.drm.pause();
    }
}

/// Switch one panel on or off.
///
/// Off is `DrmCompositor::clear`: one atomic modeset that detaches the planes and
/// the connector and makes the CRTC inactive, so the DSI encoder is disabled and
/// the panel driver switches the panel off (display off, sleep in, reset held,
/// its supplies released). It blocks for the panel's own off sequence (~150 ms).
/// A flip still in flight is waited for: the modeset would be refused (EBUSY)
/// while it is pending, so the switch-off then happens at its vblank.
///
/// On re-reads the CRTC state and queues a full frame; the commit that carries
/// it is a modeset again (the panel driver's on sequence, ~300 ms), and its
/// page-flip event is the proof the panel took the frame.
pub fn set_power(state: &mut DuoWc, crtc: crtc::Handle, on: bool) {
    let Some(surface) = state.tty.surface_mut(crtc) else {
        return;
    };
    if surface.want_on == on {
        return;
    }
    surface.want_on = on;
    if !on && surface.wake_queued {
        // Its wake commit was not made yet (wake_together): it simply stays
        // dark, and the panels before it do not wait for it.
        surface.wake_queued = false;
        surface.waking = false;
        surface.wake_group = false;
        debug!(output = surface.name, "asked off before its wake commit");
        return;
    }
    if !on {
        if surface.frame_pending {
            debug!(output = surface.name, "switching off after the frame in flight");
        } else {
            power_off_now(surface);
        }
        return;
    }
    if !surface.powered {
        if let Err(err) = surface.compositor.reset_state() {
            warn!(output = surface.name, ?err, "failed to re-read the CRTC state");
        }
        surface.powered = true;
        surface.power_cycles += 1;
        surface.power_on_at = Some(std::time::Instant::now());
        info!(output = surface.name, "panel switching on");
    }
    surface.redraw_needed = true;
    state.queue_redraw(crtc);
}

/// Switch dark panels on with atomic commits that do not block: each CRTC
/// active with its mode, its connector on it, no planes (the first frame
/// follows as an ordinary page flip). The kernel runs a panel's power-on
/// sequence in its commit worker while the event loop goes on; the commit's
/// page-flip event says it is done ([`on_drm_event`]).
///
/// **Off by default since 2026-11-08** (`atomic_wake`): a panel left on the
/// black first frame of its wake commit (no planes) while the other panel
/// came up often stayed dark, the left one (DSI-1), until a new frame or a
/// brightness change seconds later, although every frame was sent and every
/// display register was as on a good wake. Both panels in ONE commit: dark
/// in 5 of 7 wakes; one panel per commit, the next when the one before is
/// awake (this function since d3659f1, the first one held black for the
/// picture at once): still about 2 in 3; the left alone through a wake
/// commit: 4 of 4 good; one modeset per panel with its picture in it (off):
/// 4 of 4 good. What the panel does there is not known (only a DCS read of
/// its power mode would tell). The picture comes on every panel at once: the
/// first one awake shows black (no planes, an OLED's black is unlit) until
/// the last is awake ([`wake_done`]).
///
/// Checked with TEST_ONLY first; false (nothing changed) if the driver
/// refuses it or anything is missing, and the caller falls back to one
/// blocking Smithay modeset per panel.
///
/// `hold`: lit panels that wait for these (power::hold_for_wake): they keep
/// what they show until the waking ones are awake, then all get the picture
/// in the same round.
pub fn wake_together(state: &mut DuoWc, crtcs: &[crtc::Handle], hold: &[crtc::Handle]) -> bool {
    if crtcs.is_empty() || !state.tty.is_active() {
        return false;
    }
    let Some(device) = state.tty.device.as_ref() else {
        return false;
    };
    for crtc in crtcs {
        let Some(surface) = device.surfaces.iter().find(|s| s.crtc == *crtc) else {
            return false;
        };
        if surface.powered || surface.frame_pending || surface.waking {
            return false;
        }
    }
    let started = std::time::Instant::now();
    if !wake_commit(state, crtcs[0]) {
        return false;
    }
    let device = state.tty.device.as_mut().expect("checked above");
    let hold: Vec<crtc::Handle> = hold
        .iter()
        .copied()
        .filter(|c| !crtcs.contains(c))
        .filter(|c| {
            device.surfaces.iter().any(|s| {
                s.crtc == *c && s.want_on && s.powered && !s.waking && !s.powering_off && !s.blackout
            })
        })
        .collect();
    let group = crtcs.len() + hold.len() > 1;
    for surface in device.surfaces.iter_mut().filter(|s| hold.contains(&s.crtc)) {
        // Lit already: its picture waits for the others (render).
        surface.wake_group = true;
        surface.wake_at = Some(started);
        surface.content_queued = false;
    }
    if !hold.is_empty() {
        info!(held = hold.len(), "the lit panel waits for the waking one: the picture on both at once");
    }
    for surface in device.surfaces.iter_mut().filter(|s| crtcs.contains(&s.crtc)) {
        surface.wake_group = group;
        surface.wake_at = group.then_some(started);
        surface.content_queued = false;
        // Waking until its own commit is done; the ones after the first
        // wait for theirs (wake_queued, started by wake_done).
        surface.waking = true;
        surface.wake_queued = surface.crtc != crtcs[0];
    }
    info!(
        panels = crtcs.len(),
        us = started.elapsed().as_micros() as u64,
        "panels switching on (non-blocking commits, one panel after the other)"
    );
    true
}

/// The wake commit of one panel ([`wake_together`]): TEST_ONLY, then
/// NONBLOCK with a page-flip event; the panel is powered and waking from
/// here. False if the driver refused it (nothing changed).
fn wake_commit(state: &mut DuoWc, crtc: crtc::Handle) -> bool {
    let Some(device) = state.tty.device.as_mut() else {
        return false;
    };
    let mut req = AtomicModeReq::new();
    let Some(surface) = device.surfaces.iter().find(|s| s.crtc == crtc) else {
        return false;
    };
    let (Some(active), Some(mode_id), Some(crtc_id)) = (
        find_property(&device.drm, crtc, "ACTIVE"),
        find_property(&device.drm, crtc, "MODE_ID"),
        find_property(&device.drm, surface.connector, "CRTC_ID"),
    ) else {
        warn!(output = surface.name, "wake commit: a property is missing");
        return false;
    };
    let blob = match device.drm.create_property_blob(&surface.drm_mode) {
        Ok(blob) => blob,
        Err(err) => {
            warn!(output = surface.name, ?err, "wake commit: no mode blob");
            return false;
        }
    };
    req.add_property(crtc, active, property::Value::Boolean(true));
    req.add_property(crtc, mode_id, blob);
    req.add_property(surface.connector, crtc_id, property::Value::CRTC(Some(crtc)));
    let name = surface.name.clone();
    let destroy = |device: &mut Device| {
        if let property::Value::Blob(id) = blob {
            let _ = device.drm.destroy_property_blob(id);
        }
    };
    let test = device
        .drm
        .atomic_commit(AtomicCommitFlags::ALLOW_MODESET | AtomicCommitFlags::TEST_ONLY, req.clone());
    if let Err(err) = test {
        warn!(output = name, ?err, "wake commit refused by the driver (TEST_ONLY); a modeset instead");
        destroy(device);
        return false;
    }
    let started = std::time::Instant::now();
    let commit = device.drm.atomic_commit(
        AtomicCommitFlags::ALLOW_MODESET | AtomicCommitFlags::NONBLOCK | AtomicCommitFlags::PAGE_FLIP_EVENT,
        req,
    );
    // The kernel holds its own reference to the mode blob it uses.
    destroy(device);
    if let Err(err) = commit {
        warn!(output = name, ?err, "wake commit failed; a modeset instead");
        return false;
    }
    if let Some(surface) = device.surfaces.iter_mut().find(|s| s.crtc == crtc) {
        surface.powered = true;
        surface.waking = true;
        surface.wake_queued = false;
        surface.frame_pending = true;
        surface.redraw_needed = true;
        surface.power_cycles += 1;
        surface.power_on_at = Some(started);
    }
    debug!(output = name, "wake commit made");
    // If a completion event never came, the panel would stay dark: after
    // WAKE_TIMEOUT go on as if it had (a page flip refused meanwhile is
    // retried by render).
    let _ = state.loop_handle.insert_source(
        smithay::reexports::calloop::timer::Timer::from_duration(WAKE_TIMEOUT),
        move |_, _, state: &mut DuoWc| {
            let stuck = state
                .tty
                .surface_mut(crtc)
                .map(|s| s.waking && !s.wake_queued && s.power_on_at == Some(started))
                .unwrap_or(false);
            if stuck {
                warn!(?crtc, "wake commit: no completion event; going on");
                wake_done(state, crtc, "the timeout");
            }
            smithay::reexports::calloop::timer::TimeoutAction::Drop
        },
    );
    true
}

/// The next panel waiting for its wake commit ([`wake_together`]), now that
/// the one before it is awake: its commit, or, asked off meanwhile, it
/// leaves the group; if the driver refuses the commit, a Smithay modeset.
fn wake_next_queued(state: &mut DuoWc) {
    loop {
        let Some((crtc, want_on)) =
            state.tty.surfaces().iter().find(|s| s.wake_queued).map(|s| (s.crtc, s.want_on))
        else {
            return;
        };
        if want_on && wake_commit(state, crtc) {
            return;
        }
        let Some(surface) = state.tty.surface_mut(crtc) else {
            return;
        };
        surface.wake_queued = false;
        surface.waking = false;
        surface.wake_group = false;
        if !want_on {
            debug!(output = surface.name, "asked off before its wake commit");
            continue;
        }
        // The driver refused the commit: the old way, a modeset with the
        // first frame (it does not wait for the group).
        if let Err(err) = surface.compositor.reset_state() {
            warn!(output = surface.name, ?err, "failed to re-read the CRTC state");
        }
        surface.powered = true;
        surface.power_cycles += 1;
        surface.power_on_at = Some(std::time::Instant::now());
        surface.redraw_needed = true;
        info!(output = surface.name, "panel switching on (modeset)");
        state.queue_redraw(crtc);
    }
}

/// How long a wake commit may take before the compositor goes on without
/// its event (a panel takes ~200 ms; the next one's commit has its own).
const WAKE_TIMEOUT: std::time::Duration = std::time::Duration::from_millis(1500);

/// A wake commit of ours finished on `crtc`: the CRTC is active with its mode
/// and connector. Smithay re-reads that, so its next frame is a plain page
/// flip, not another modeset.
fn wake_done(state: &mut DuoWc, crtc: crtc::Handle, by: &str) {
    let Some(surface) = state.tty.surface_mut(crtc) else {
        return;
    };
    surface.waking = false;
    surface.frame_pending = false;
    surface.ahead = false;
    if let Err(err) = surface.compositor.reset_state() {
        warn!(output = surface.name, ?err, "failed to re-read the CRTC state after waking");
    }
    if let Some(at) = surface.power_on_at {
        info!(output = surface.name, ms = at.elapsed().as_millis() as u64, by, "panel on: wake commit done");
    }
    surface.redraw_needed = true;
    let grouped = surface.wake_group;
    // The next panel of the wake, now that this one is on (wake_together).
    wake_next_queued(state);
    if grouped && !state.tty.surfaces().iter().any(|s| s.wake_group && s.waking) {
        // The last of the panels woken together: the picture on all of them
        // now (the first one has shown black meanwhile: an OLED's black is
        // unlit, so its earlier wake was not seen).
        let crtcs: Vec<crtc::Handle> =
            state.tty.surfaces().iter().filter(|s| s.wake_group).map(|s| s.crtc).collect();
        for c in &crtcs {
            if let Some(s) = state.tty.surface_mut(*c) {
                s.wake_group = false;
                s.redraw_needed = true;
            }
        }
        info!(panels = crtcs.len(), "panels awake: the picture on all of them at once");
        let want_on = state.tty.surface_mut(crtc).map(|s| s.want_on).unwrap_or(false);
        if !want_on {
            switch_off_after_wait(state, crtc);
        } else {
            render(state, crtc);
        }
        for c in crtcs {
            if c != crtc {
                state.queue_redraw(c);
            }
        }
        return;
    }
    let want_on = state.tty.surface_mut(crtc).map(|s| s.want_on).unwrap_or(false);
    if !want_on {
        // Asked off again meanwhile.
        switch_off_after_wait(state, crtc);
        return;
    }
    render(state, crtc);
}

/// A repeat of the first picture after a power-on, `after` from it
/// ([`crate::power::REPEAT_SOON`], [`crate::power::REPEAT_LATER`]): a panel
/// that missed the first commit gets its picture here, on a still screen
/// too. Never while another panel still waits for its own first picture:
/// the display takes one commit at a time (EBUSY), and a repeat there held
/// the other panel's picture back by two retries (~20 ms).
fn repeat_picture_after(state: &mut DuoWc, crtc: crtc::Handle, after: std::time::Duration) {
    let give_up = std::time::Instant::now() + after + REPEAT_WAIT_MAX;
    let _ = state.loop_handle.insert_source(
        smithay::reexports::calloop::timer::Timer::from_duration(after),
        move |_, _, state: &mut DuoWc| {
            let other_waits =
                state.tty.surfaces().iter().any(|s| s.crtc != crtc && s.want_on && s.power_on_at.is_some());
            if other_waits && std::time::Instant::now() < give_up {
                return smithay::reexports::calloop::timer::TimeoutAction::ToDuration(REPEAT_RETRY);
            }
            if let Some(surface) = state.tty.surface_mut(crtc) {
                if surface.shows_picture() {
                    surface.repeat_full = true;
                    surface.redraw_needed = true;
                }
            }
            state.queue_redraw(crtc);
            smithay::reexports::calloop::timer::TimeoutAction::Drop
        },
    );
}

/// How long a repeat waits again while another panel's first picture is
/// not up yet ...
const REPEAT_RETRY: std::time::Duration = std::time::Duration::from_millis(10);
/// ... and at most, in all (a wake times out after 1.5 s).
const REPEAT_WAIT_MAX: std::time::Duration = std::time::Duration::from_secs(2);

/// Several lit panels go dark together: each first shows a black frame (an
/// OLED's black is unlit), and only when all of them do does the off commit
/// go out ([`blackout_check`]). The kernel switches the panels off one after
/// the other inside that commit (~170 ms each); before, the second panel's
/// picture stayed on that long after the first went dark. False when a
/// panel is busy (the caller switches them off as before).
pub fn start_blackout(state: &mut DuoWc, crtcs: &[crtc::Handle]) -> bool {
    let Some(device) = state.tty.device.as_ref() else {
        return false;
    };
    let ok = crtcs.iter().all(|c| {
        device
            .surfaces
            .iter()
            .any(|s| s.crtc == *c && s.powered && !s.waking && !s.powering_off && !s.blackout)
    });
    if !ok || crtcs.is_empty() {
        return false;
    }
    for c in crtcs {
        if let Some(s) = state.tty.surface_mut(*c) {
            s.blackout = true;
            s.blackout_queued = false;
            s.blackout_shown = false;
        }
        state.queue_redraw(*c);
    }
    debug!(panels = crtcs.len(), "going dark: a black frame on each panel first");
    // Whatever happens to the frames, the panels go off soon.
    let _ = state.loop_handle.insert_source(
        smithay::reexports::calloop::timer::Timer::from_duration(BLACKOUT_TIMEOUT),
        |_, _, state: &mut DuoWc| {
            if state.tty.surfaces().iter().any(|s| s.blackout) {
                warn!("going dark: the black frames took too long; switching off");
                blackout_check(state, true);
            }
            smithay::reexports::calloop::timer::TimeoutAction::Drop
        },
    );
    true
}

/// How long the black frames before an off commit may take.
const BLACKOUT_TIMEOUT: std::time::Duration = std::time::Duration::from_millis(250);

/// The off commit, once every panel going dark shows its black frame
/// (`force`: now, whatever they show). A panel asked on again meanwhile
/// stays on.
pub fn blackout_check(state: &mut DuoWc, force: bool) {
    let pending: Vec<(crtc::Handle, bool, bool, bool)> = state
        .tty
        .surfaces()
        .iter()
        .filter(|s| s.blackout)
        .map(|s| (s.crtc, s.blackout_shown, s.want_on, s.frame_pending))
        .collect();
    if pending.is_empty() || (!force && pending.iter().any(|(_, shown, _, _)| !shown)) {
        return;
    }
    if force && pending.iter().any(|(_, _, _, in_flight)| *in_flight) {
        // A flip still in flight: its vblank comes back here.
        for (c, _, _, in_flight) in &pending {
            if let Some(s) = state.tty.surface_mut(*c) {
                if !*in_flight {
                    s.blackout_shown = true;
                }
            }
        }
        return;
    }
    let mut off = Vec::new();
    for (c, _, want_on, _) in &pending {
        if let Some(s) = state.tty.surface_mut(*c) {
            s.blackout = false;
            s.blackout_queued = false;
            s.blackout_shown = false;
            if *want_on {
                s.redraw_needed = true;
            } else {
                off.push(*c);
            }
        }
    }
    for (c, _, want_on, _) in &pending {
        if *want_on {
            state.queue_redraw(*c);
        }
    }
    if off.is_empty() {
        return;
    }
    info!(panels = off.len(), "both panels show black: switching them off");
    if !(state.atomic_off && power_off_together(state, &off)) {
        for c in off {
            if let Some(s) = state.tty.surface_mut(c) {
                power_off_now(s);
            }
        }
    }
}

/// A panel asked off while a commit was in flight (a frame, a wake) goes off
/// now that it is done: in a non-blocking commit of its own if allowed and
/// accepted, else Smithay's blocking one.
fn switch_off_after_wait(state: &mut DuoWc, crtc: crtc::Handle) {
    if state.atomic_off && power_off_together(state, &[crtc]) {
        return;
    }
    if let Some(surface) = state.tty.surface_mut(crtc) {
        power_off_now(surface);
    }
}

/// Switch several lit panels off with ONE atomic commit that does not block:
/// every plane on their CRTCs detached, each connector off its CRTC, each
/// CRTC inactive without a mode. The kernel runs the panels' off sequences
/// in its commit worker (~170 ms each, one after the other) while the event
/// loop goes on; the commit's event (one per CRTC) says it is done
/// ([`on_drm_event`], [`off_done`]).
///
/// Checked with TEST_ONLY first; false (nothing changed) if the driver
/// refuses it or anything is missing, and the caller falls back to Smithay's
/// blocking switch-off (`DrmCompositor::clear`) per panel.
pub fn power_off_together(state: &mut DuoWc, crtcs: &[crtc::Handle]) -> bool {
    if crtcs.is_empty() || !state.tty.is_active() {
        return false;
    }
    let Some(device) = state.tty.device.as_mut() else {
        return false;
    };
    let planes = match device.drm.plane_handles() {
        Ok(planes) => planes,
        Err(err) => {
            warn!(?err, "off commit: no plane list");
            return false;
        }
    };
    let mut req = AtomicModeReq::new();
    for crtc in crtcs {
        let Some(surface) = device.surfaces.iter().find(|s| s.crtc == *crtc) else {
            return false;
        };
        if !surface.powered || surface.frame_pending || surface.waking || surface.powering_off {
            return false;
        }
        let (Some(active), Some(mode_id), Some(crtc_id)) = (
            find_property(&device.drm, *crtc, "ACTIVE"),
            find_property(&device.drm, *crtc, "MODE_ID"),
            find_property(&device.drm, surface.connector, "CRTC_ID"),
        ) else {
            warn!(output = surface.name, "off commit: a property is missing");
            return false;
        };
        // The planes scanning out on this CRTC now (whatever Smithay put there).
        for plane in &planes {
            let Some((plane_crtc, current)) = property_value(&device.drm, *plane, "CRTC_ID") else {
                continue;
            };
            if current != u64::from(u32::from(*crtc)) {
                continue;
            }
            let Some(fb_id) = find_property(&device.drm, *plane, "FB_ID") else {
                warn!(output = surface.name, "off commit: a plane without FB_ID");
                return false;
            };
            req.add_property(*plane, fb_id, property::Value::Framebuffer(None));
            req.add_property(*plane, plane_crtc, property::Value::CRTC(None));
        }
        req.add_property(surface.connector, crtc_id, property::Value::CRTC(None));
        req.add_property(*crtc, active, property::Value::Boolean(false));
        req.add_property(*crtc, mode_id, property::Value::Blob(0));
    }
    let test = device
        .drm
        .atomic_commit(AtomicCommitFlags::ALLOW_MODESET | AtomicCommitFlags::TEST_ONLY, req.clone());
    if let Err(err) = test {
        warn!(
            ?err,
            "off commit refused by the driver (TEST_ONLY); one blocking switch-off per panel instead"
        );
        return false;
    }
    let started = std::time::Instant::now();
    let commit = device.drm.atomic_commit(
        AtomicCommitFlags::ALLOW_MODESET | AtomicCommitFlags::NONBLOCK | AtomicCommitFlags::PAGE_FLIP_EVENT,
        req,
    );
    if let Err(err) = commit {
        warn!(?err, "off commit failed; one blocking switch-off per panel instead");
        return false;
    }
    for surface in device.surfaces.iter_mut().filter(|s| crtcs.contains(&s.crtc)) {
        surface.powered = false;
        surface.powering_off = true;
        surface.frame_pending = true;
        surface.redraw_needed = false;
        surface.power_on_at = None;
        surface.off_at = Some(started);
    }
    info!(
        panels = crtcs.len(),
        us = started.elapsed().as_micros() as u64,
        "panels switching off (one non-blocking commit)"
    );
    // If the completion event never came, go on as if it had: the next
    // commit on the CRTC waits for the kernel anyway.
    let crtcs = crtcs.to_vec();
    let _ = state.loop_handle.insert_source(
        smithay::reexports::calloop::timer::Timer::from_duration(WAKE_TIMEOUT),
        move |_, _, state: &mut DuoWc| {
            for crtc in &crtcs {
                if state.tty.surface_mut(*crtc).map(|s| s.powering_off).unwrap_or(false) {
                    warn!(?crtc, "off commit: no completion event; going on");
                    off_done(state, *crtc, "the timeout");
                }
            }
            smithay::reexports::calloop::timer::TimeoutAction::Drop
        },
    );
    true
}

/// An off commit of ours finished on `crtc`: the panel is dark. Smithay
/// re-reads the CRTC when the panel comes back (both wake paths do). If the
/// panel was asked on meanwhile, it wakes now.
fn off_done(state: &mut DuoWc, crtc: crtc::Handle, by: &str) {
    let atomic_wake = state.atomic_wake;
    let Some(surface) = state.tty.surface_mut(crtc) else {
        return;
    };
    surface.powering_off = false;
    surface.frame_pending = false;
    surface.ahead = false;
    if let Some(at) = surface.off_at.take() {
        info!(output = surface.name, ms = at.elapsed().as_millis() as u64, by, "panel off");
    }
    // Panels asked on again meanwhile wake when the last off is done, all
    // in one go (power::wake_after_off, wake_together), so they come on together.
    let panels: Vec<crate::power::OffPanel> = state
        .tty
        .surfaces()
        .iter()
        .map(|s| crate::power::OffPanel {
            name: s.name.clone(),
            want_on: s.want_on,
            powered: s.powered,
            powering_off: s.powering_off,
            waking: s.waking,
        })
        .collect();
    let names = crate::power::wake_after_off(&panels);
    let crtcs: Vec<crtc::Handle> =
        state.tty.surfaces().iter().filter(|s| names.contains(&s.name)).map(|s| s.crtc).collect();
    if crtcs.is_empty() {
        return;
    }
    if atomic_wake && wake_together(state, &crtcs, &[]) {
        return;
    }
    for c in crtcs {
        let Some(surface) = state.tty.surface_mut(c) else {
            continue;
        };
        if let Err(err) = surface.compositor.reset_state() {
            warn!(output = surface.name, ?err, "failed to re-read the CRTC state");
        }
        surface.powered = true;
        surface.power_cycles += 1;
        surface.power_on_at = Some(std::time::Instant::now());
        surface.redraw_needed = true;
        info!(output = surface.name, "panel switching on");
        state.queue_redraw(c);
    }
}

/// A property of a DRM object and its current value, by name.
fn property_value<T: ResourceHandle>(
    drm: &DrmDevice,
    handle: T,
    name: &str,
) -> Option<(property::Handle, u64)> {
    let props = drm.get_properties(handle).ok()?;
    let (ids, values) = props.as_props_and_values();
    ids.iter().zip(values.iter()).find_map(|(id, value)| {
        let info = drm.get_property(*id).ok()?;
        (info.name().to_str().ok()? == name).then_some((*id, *value))
    })
}

/// A property of a DRM object, by name.
fn find_property<T: ResourceHandle>(drm: &DrmDevice, handle: T, name: &str) -> Option<property::Handle> {
    let props = drm.get_properties(handle).ok()?;
    let (ids, _) = props.as_props_and_values();
    ids.iter().copied().find(|id| {
        drm.get_property(*id)
            .map(|info| info.name().to_str().map(|n| n == name).unwrap_or(false))
            .unwrap_or(false)
    })
}

fn power_off_now(surface: &mut Surface) {
    let started = std::time::Instant::now();
    match surface.compositor.clear() {
        Ok(()) => {
            surface.powered = false;
            surface.redraw_needed = false;
            surface.power_on_at = None;
            info!(output = surface.name, ms = started.elapsed().as_millis() as u64, "panel off");
        }
        Err(err) => {
            warn!(output = surface.name, ?err, "failed to switch the panel off");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn timing(vrefresh: u32) -> Timing {
        Timing {
            clock: if vrefresh == 90 { 285_227 } else { 190_151 },
            size: (1344, 1892),
            hsync: (1548, 1580, 1612),
            vsync: (1924, 1934, 1966),
            hskew: 0,
            vscan: 0,
            vrefresh,
            flags: 0,
        }
    }

    #[test]
    fn a_card_has_connectors_by_sysfs() {
        let dir = std::env::temp_dir().join(format!("duo-wc-sysfs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("card0-DSI-1")).unwrap();
        std::fs::create_dir_all(dir.join("card1")).unwrap();
        std::fs::create_dir_all(dir.join("renderD128")).unwrap();
        assert!(has_connector_in(&dir, "card0"));
        assert!(!has_connector_in(&dir, "card1"));
        assert!(!has_connector_in(&dir.join("missing"), "card0"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_boot_splash_panel_is_kept_in_its_mode() {
        // The splash (or fbcon) left the panel lit at 90 Hz, on its
        // connector, and duo-wc picks 90 Hz: kept, a page flip, no power cycle.
        assert_eq!(takeover(timing(90), Some(timing(90)), true), Takeover::Keep);
        // The refresh chosen in Duo Settings is another: one modeset.
        assert_eq!(takeover(timing(60), Some(timing(90)), true), Takeover::OtherMode);
        // Off (no splash, a restart after `systemctl stop`), or the CRTC is
        // lit for another connector: the modeset as before.
        assert_eq!(takeover(timing(90), None, true), Takeover::Off);
        assert_eq!(takeover(timing(90), None, false), Takeover::Off);
        assert_eq!(takeover(timing(90), Some(timing(90)), false), Takeover::Off);
        assert!(Takeover::Keep.describe().contains("no power cycle"));
    }
}
