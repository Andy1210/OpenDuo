//! The rotation animation, the way phones turn (the first version, of
//! 2026-11-07, stretched the picture and felt laggy).
//!
//! On a rotation change the panel's picture as it was is kept (the OLD
//! picture: rendered off-screen once, like a screenshot). For
//! [`DEFAULT_ROTATE_MS`] it turns, eased in and out, from where it stood on
//! the glass to upright in the new frame, scaled UNIFORMLY so that at every
//! angle it fits inside the panel (black around it, never stretched): a
//! portrait picture ends letterboxed in the landscape frame. The clients are
//! configured at the new size at once and redraw meanwhile; once all of them
//! have drawn at the new size, their picture is captured once (the NEW
//! picture) and turns in with the same motion (same angle, its own fitting
//! scale: it ends filling the panel), while the old one fades out over it in
//! [`FADE`]. The fade starts when the new picture is there or at the end of
//! the turn, whichever comes first; at the end of the turn the real scene
//! takes over (the old picture, if still there, fades over it).
//!
//! During the turn the frame is the two pictures and nothing else: no client
//! surface is imported or drawn, so the clients' re-layout (new buffers to
//! import, uploads) cannot hold a frame up, and every frame is two textured
//! quads. Frames come from the vblank loop at the panel's rate; each frame's
//! progress is taken at the time it will be on the panel (the last flip plus
//! whole refresh periods), so the steps are even whenever the frame is
//! rendered. The frame timing of every turn is logged ([`crate::turnlog`]).
//!
//! A panel whose own rotation did not change (the other panel: turning one
//! may move the layout of both) keeps its old picture for [`HOLD`] (the
//! second request of duo-posed's pair comes within it, and then that panel
//! turns too, from its picture as it was), then fades it into the real one.
//! A rotation that comes during a turn keeps the old picture and turns it on
//! from the angle it was shown at.
//!
//! The geometry is pure ([`start_angle`], [`reframe`], [`ease_in_out`],
//! [`fit_scale`], [`frame`]; unit-tested); the live part keeps the pictures
//! as textures (spare ones are kept for the next turn) and draws each with a
//! small texture shader (any angle, any scale, an antialiased edge).

use std::collections::HashMap;
use std::time::Duration;

use smithay::backend::allocator::dmabuf::{Dmabuf, WeakDmabuf};
use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::damage::OutputDamageTracker;
use smithay::backend::renderer::element::texture::TextureRenderElement;
use smithay::backend::renderer::element::{Id, Kind};
use smithay::backend::renderer::gles::element::TextureShaderElement;
use smithay::backend::renderer::gles::{
    GlesRenderer, GlesTexProgram, GlesTexture, Uniform, UniformName, UniformType,
};
use smithay::backend::renderer::{Bind, Offscreen, Renderer as _, Texture as _};
use smithay::utils::{Buffer as BufferCoords, Logical, Rectangle, Size, Transform};
use tracing::warn;

/// How long the picture turns, by default (`rotate_anim_ms`).
pub const DEFAULT_ROTATE_MS: u64 = 320;
/// How long the old picture fades out.
pub const FADE: Duration = Duration::from_millis(120);
/// How long a panel that did not turn keeps its old picture before it
/// fades (the other half of a pair of rotation requests comes within it).
pub const HOLD: Duration = Duration::from_millis(200);
/// Pictures' textures kept for the next turn (two panels, old and new).
const SPARE_MAX: usize = 4;
/// During the turn the clients' picture is taken again (when they changed
/// it) at most this often: once the last one's fade is over.
pub const RETAKE_EVERY: Duration = FADE;
/// After the turn the pictures stay at rest, the scene not shown yet, while
/// the clients have not drawn at the new size, for at most this long: the
/// scene is never shown half drawn. (Clients that change their picture
/// after the first one at the new size are taken again during the turn,
/// [`RETAKE_EVERY`]; one that changes after the turn shows it live.)
pub const SETTLE: Duration = Duration::from_millis(300);
/// While a turn runs, whether it is over is looked at this often.
pub const RELEASE_EVERY: Duration = Duration::from_millis(20);
/// After it, the held client buffers are kept this much longer (the
/// clients settle, the GPU goes idle: a buffer let go then costs least)...
pub const RELEASE_DELAY: Duration = Duration::from_millis(400);
/// ... then let go every this often...
pub const RELEASE_STEP: Duration = Duration::from_millis(40);
/// ... as many as fit in this much loop time (one, mostly: letting go of a
/// buffer whose client has destroyed it frees it in the kernel, 2-40 ms).
pub const RELEASE_BUDGET: Duration = Duration::from_millis(2);

/// The clients' buffers, held through a turn.
///
/// At a turn every client reallocates its buffers at the new size and
/// destroys the old ones; letting go of a buffer the compositor imported
/// destroys its EGLImage, a kernel call that unmaps it from the GPU
/// (measured on the phone: these were the 10-45 ms frames of a turn, inside
/// `GlesRenderer::cleanup` in the frame's render). So while a turn runs every
/// client buffer is kept, and afterwards they are let go a few at a time,
/// between frames ([`RELEASE_EVERY`], [`RELEASE_BUDGET`]).
#[derive(Default)]
pub struct BufferHold {
    /// Every client dmabuf imported (weak: it does not keep one).
    known: Vec<WeakDmabuf>,
    /// The ones held now.
    pub held: Vec<Dmabuf>,
    /// A turn runs: new buffers are held as well.
    pub holding: bool,
    /// The timer that lets them go after the turn runs.
    pub timer: bool,
    /// Imports of client buffers during turns: how many, and their time.
    pub imports: u32,
    pub import_ms: f64,
    pub import_max_ms: f64,
    /// Letting them go after the turn: buffers, ticks, the longest tick (ms).
    pub released: u32,
    pub release_ticks: u32,
    pub release_max_ms: f64,
}

impl BufferHold {
    /// A client dmabuf was imported (in `import_ms`).
    pub fn track(&mut self, dmabuf: &Dmabuf, import_ms: f64) {
        if self.known.len() >= 64 && self.known.len() % 32 == 0 {
            self.known.retain(|w| !w.is_gone());
        }
        self.known.push(dmabuf.weak());
        if self.holding {
            self.held.push(dmabuf.clone());
            self.imports += 1;
            self.import_ms += import_ms;
            self.import_max_ms = self.import_max_ms.max(import_ms);
        }
    }

    /// A turn starts: hold every client buffer there is (true: it was not
    /// held yet).
    pub fn hold(&mut self) -> bool {
        if self.holding {
            return false;
        }
        self.holding = true;
        self.known.retain(|w| !w.is_gone());
        let held = &self.held;
        let more: Vec<Dmabuf> = self
            .known
            .iter()
            .filter(|w| !held.iter().any(|h| h.weak() == **w))
            .filter_map(|w| w.upgrade())
            .collect();
        self.held.extend(more);
        true
    }
}

/// An angle in (-180, 180].
pub fn normalize(angle: f64) -> f64 {
    let mut a = angle % 360.0;
    if a > 180.0 {
        a -= 360.0;
    }
    if a <= -180.0 {
        a += 360.0;
    }
    a
}

/// The angle (degrees, clockwise as the picture reads) the old picture has
/// in the NEW frame at the moment of the turn, from the old and the new
/// rotation (wl_output degrees: the picture is drawn that many degrees
/// counter-clockwise on the panel). In (-180, 180]; 0 when nothing turned.
pub fn start_angle(old_deg: u32, new_deg: u32) -> f64 {
    normalize(new_deg as f64 - old_deg as f64)
}

/// A picture standing at `angle` in the frame of rotation `old_deg`, seen
/// in the frame of `new_deg` (a rotation during a turn: it turns on from
/// where it is).
pub fn reframe(angle: f64, old_deg: u32, new_deg: u32) -> f64 {
    normalize(angle + start_angle(old_deg, new_deg))
}

/// A CSS-style cubic Bézier timing curve through (0,0), (x1,y1), (x2,y2),
/// (1,1): the progress at time `t` (0..1).
pub fn cubic_bezier(x1: f64, y1: f64, x2: f64, y2: f64, t: f64) -> f64 {
    let t = t.clamp(0.0, 1.0);
    if t <= 0.0 || t >= 1.0 {
        return t;
    }
    let bez = |a: f64, b: f64, s: f64| {
        let u = 1.0 - s;
        3.0 * u * u * s * a + 3.0 * u * s * s * b + s * s * s
    };
    let slope = |a: f64, b: f64, s: f64| {
        let u = 1.0 - s;
        3.0 * u * u * a + 6.0 * u * s * (b - a) + 3.0 * s * s * (1.0 - b)
    };
    // Newton from s = t, bisection if it does not settle.
    let mut s = t;
    for _ in 0..8 {
        let err = bez(x1, x2, s) - t;
        if err.abs() < 1e-9 {
            return bez(y1, y2, s);
        }
        let d = slope(x1, x2, s);
        if d.abs() < 1e-9 {
            break;
        }
        s -= err / d;
        if !(0.0..=1.0).contains(&s) {
            break;
        }
    }
    let (mut lo, mut hi) = (0.0, 1.0);
    s = t;
    for _ in 0..60 {
        let x = bez(x1, x2, s);
        if (x - t).abs() < 1e-9 {
            break;
        }
        if x < t {
            lo = s;
        } else {
            hi = s;
        }
        s = 0.5 * (lo + hi);
    }
    bez(y1, y2, s)
}

/// Ease in and out: Material's standard curve (Android's fast_out_slow_in),
/// starting and ending at rest.
pub fn ease_in_out(t: f64) -> f64 {
    cubic_bezier(0.4, 0.0, 0.2, 1.0, t)
}

/// The uniform scale at which a picture of `pic` px, turned by `angle`
/// degrees, fits inside a panel of `panel` px (never more than 1: a picture
/// is never shown bigger than it was drawn).
pub fn fit_scale(pic: (f64, f64), panel: (f64, f64), angle: f64) -> f64 {
    let a = angle.to_radians();
    let (c, s) = (a.cos().abs(), a.sin().abs());
    let w = pic.0 * c + pic.1 * s;
    let h = pic.0 * s + pic.1 * c;
    if w <= 0.0 || h <= 0.0 {
        return 1.0;
    }
    (panel.0 / w).min(panel.1 / h).min(1.0)
}

/// Where a picture is drawn: turned by `angle` (degrees clockwise as it
/// reads), scaled by `scale` about the panel's centre, at `alpha`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pose {
    pub angle: f64,
    pub scale: f64,
    pub alpha: f64,
}

/// One frame of the animation.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    /// The angle both pictures stand at.
    pub angle: f64,
    /// The old picture, in front (None: faded out).
    pub old: Option<Pose>,
    /// The new picture, behind it (None: not captured yet, or the turn is
    /// over and the real scene shows).
    pub new: Option<Pose>,
    /// The turn is over: the real scene is drawn behind the old picture.
    pub live: bool,
}

/// How a panel's animation runs.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Timing {
    /// How long the pictures turn (0: the panel did not turn).
    pub turn: Duration,
    /// The old picture stays at least this long before it fades.
    pub hold: Duration,
    /// How long it fades.
    pub fade: Duration,
}

/// The frame `elapsed` after the first; None when the animation is over.
/// `from` is where the old picture stands at the start (degrees in the new
/// frame), `old` its size and `panel` the panel's in the new frame (both
/// physical px, as they read); `ready` when the new picture was captured
/// (None: not yet); `settle`: past the turn the pictures stay at rest (the
/// clients' picture is not there yet, or they changed it since).
pub fn frame(
    from: f64,
    old: (f64, f64),
    panel: (f64, f64),
    elapsed: Duration,
    timing: &Timing,
    ready: Option<Duration>,
    settle: bool,
) -> Option<Frame> {
    let p = if timing.turn.is_zero() {
        1.0
    } else {
        ease_in_out(elapsed.as_secs_f64() / timing.turn.as_secs_f64())
    };
    let angle = from * (1.0 - p);
    let covered = elapsed < timing.turn || settle;
    // The cross-fade starts when the new picture is there or when the turn
    // ends, whichever comes first (while settling without it: not yet).
    let fade_from = match ready {
        Some(r) => r,
        None if settle => elapsed,
        None => timing.turn,
    }
    .max(timing.hold);
    let alpha = if elapsed <= fade_from {
        1.0
    } else if timing.fade.is_zero() {
        0.0
    } else {
        (1.0 - (elapsed - fade_from).as_secs_f64() / timing.fade.as_secs_f64()).clamp(0.0, 1.0)
    };
    if !covered && alpha <= 0.0 {
        return None;
    }
    let old = (alpha > 0.0).then(|| Pose { angle, scale: fit_scale(old, panel, angle), alpha });
    let new = if covered {
        ready.filter(|r| *r <= elapsed).map(|_| Pose {
            angle,
            scale: fit_scale(panel, panel, angle),
            alpha: 1.0,
        })
    } else {
        None
    };
    Some(Frame { angle, old, new, live: !covered })
}

/// The display time of a frame rendered `now`: the first vblank after it,
/// counted in whole refresh periods from the panel's last flip (`now` when
/// there was none). All CLOCK_MONOTONIC.
pub fn display_time(last_flip: Option<Duration>, now: Duration, period: Duration) -> Duration {
    match last_flip {
        Some(last) if !period.is_zero() => {
            if now <= last {
                last + period
            } else {
                let n = ((now - last).as_secs_f64() / period.as_secs_f64()).floor() + 1.0;
                last + period.mul_f64(n)
            }
        }
        _ => now,
    }
}

/// The picture shader: the destination is the whole panel; each pixel looks
/// up where it falls in the turned, uniformly scaled picture (black outside
/// it, the edge antialiased over one panel pixel).
const SRC: &str = r#"#version 100
//_DEFINES_
#if defined(EXTERNAL)
#extension GL_OES_EGL_image_external : require
#endif
precision highp float;
#if defined(EXTERNAL)
uniform samplerExternalOES tex;
#else
uniform sampler2D tex;
#endif
uniform float alpha;
varying vec2 v_coords;
#if defined(DEBUG_FLAGS)
uniform float tint;
#endif
uniform vec2 dst_size;
uniform vec2 pic_size;
uniform float scale;
uniform float angle;
void main() {
    vec2 p = (v_coords - 0.5) * dst_size;
    float c = cos(angle);
    float s = sin(angle);
    // The inverse of: scale, then turn clockwise (y down) by angle.
    vec2 u = vec2(c * p.x + s * p.y, -s * p.x + c * p.y) / scale;
    vec2 inside = (0.5 * pic_size - abs(u)) * scale;
    float cover = clamp(min(inside.x, inside.y) + 0.5, 0.0, 1.0);
    vec2 uv = clamp(u / pic_size + 0.5, 0.0, 1.0);
    vec3 rgb = texture2D(tex, uv).rgb * cover;
    gl_FragColor = vec4(rgb, 1.0) * alpha;
}
"#;

/// A picture: a texture and its size (physical px, upright as it reads).
pub struct Picture {
    pub tex: GlesTexture,
    pub size: (i32, i32),
}

/// One panel's running animation.
pub struct Anim {
    /// The picture as it was (upright in the frame it was taken in).
    pub old: Picture,
    /// The clients' picture at the new size, once they drew it.
    pub new: Option<Picture>,
    /// Where the old picture stands when the clock starts (degrees, in the
    /// panel's frame now).
    pub from: f64,
    /// The panel's own rotation changed (otherwise: it only holds, fades).
    pub turned: bool,
    /// The display time of its first frame (CLOCK_MONOTONIC); None until
    /// the first frame is rendered.
    pub t0: Option<Duration>,
    /// When the new picture was captured (since t0).
    pub ready: Option<Duration>,
    /// The surface commit count it was taken at (taken again when the
    /// clients changed).
    pub new_serial: u64,
    /// Capturing the new picture failed: the fade waits for the end.
    pub capture_failed: bool,
    /// The angle of the last frame shown.
    pub shown: f64,
    /// When it began (CLOCK_MONOTONIC).
    pub created: Duration,
}

/// The animations and their shader.
pub struct Gfx {
    program: Option<GlesTexProgram>,
    failed: bool,
    /// By output name.
    pub anims: HashMap<String, Anim>,
    /// How long a turn takes (ms; 0 = no animation): `rotate_anim_ms`.
    pub rotate_ms: u64,
    /// The frame timing of the turns running (crate::turnlog).
    pub logs: crate::turnlog::TurnLogs,
    /// Each panel's last flip (CLOCK_MONOTONIC), for the frames' display time.
    pub last_flip: HashMap<String, Duration>,
    /// Textures of finished animations, for the next pictures.
    spare: Vec<GlesTexture>,
    /// A turn's frames are rendered one ahead (queued behind the frame in
    /// flight): `duoctl set_rotate_anim MS ahead on|off`.
    pub render_ahead: bool,
    /// The clients' buffers, held through a turn.
    pub hold: BufferHold,
    /// Surface commits per panel while a turn runs there.
    pub changes: HashMap<String, u64>,
    /// A 1x1 target: rendering into it runs the renderer's clean-up (where
    /// a let-go buffer's import is destroyed) between frames.
    cleanup_target: Option<GlesTexture>,
}

impl Default for Gfx {
    fn default() -> Self {
        Gfx {
            program: None,
            failed: false,
            anims: HashMap::new(),
            rotate_ms: DEFAULT_ROTATE_MS,
            logs: Default::default(),
            last_flip: HashMap::new(),
            spare: Vec::new(),
            render_ahead: true,
            hold: BufferHold::default(),
            changes: HashMap::new(),
            cleanup_target: None,
        }
    }
}

impl Gfx {
    /// With turns of `rotate_ms` (0: none).
    pub fn new(rotate_ms: u64) -> Self {
        Gfx { rotate_ms, ..Default::default() }
    }

    /// The picture shader (compiled on first use; None: it does not compile).
    pub fn program(&mut self, renderer: &mut GlesRenderer) -> Option<GlesTexProgram> {
        if self.program.is_none() && !self.failed {
            match renderer.compile_custom_texture_shader(
                SRC,
                &[
                    UniformName::new("dst_size", UniformType::_2f),
                    UniformName::new("pic_size", UniformType::_2f),
                    UniformName::new("scale", UniformType::_1f),
                    UniformName::new("angle", UniformType::_1f),
                ],
            ) {
                Ok(program) => self.program = Some(program),
                Err(err) => {
                    warn!(?err, "the rotation shader did not compile: no rotation animation");
                    self.failed = true;
                }
            }
        }
        self.program.clone()
    }

    /// Whether the animation can run (its shader compiled, or not tried).
    pub fn usable(&self) -> bool {
        !self.failed && self.rotate_ms > 0
    }

    /// How an animation runs.
    pub fn timing(&self, anim: &Anim) -> Timing {
        if anim.turned {
            Timing { turn: Duration::from_millis(self.rotate_ms), hold: Duration::ZERO, fade: FADE }
        } else {
            Timing { turn: Duration::ZERO, hold: HOLD, fade: FADE }
        }
    }

    /// Whether `output`'s animation still shows only its old picture
    /// turning or holding (a new rotation then keeps it and turns it on);
    /// false when there is none or the new picture or the real scene
    /// already shows.
    pub fn in_turn(&self, output: &str, now: Duration) -> bool {
        let Some(anim) = self.anims.get(output) else {
            return false;
        };
        let Some(t0) = anim.t0 else {
            return true;
        };
        if anim.new.is_some() {
            return false;
        }
        let timing = self.timing(anim);
        now.saturating_sub(t0) < timing.turn.max(timing.hold)
    }

    /// A new animation on `output` from its picture now (one running there
    /// ends: its textures are kept for later).
    pub fn begin(&mut self, output: &str, old: Picture) {
        self.finish(output);
        self.anims.insert(
            output.to_owned(),
            Anim {
                old,
                new: None,
                from: 0.0,
                turned: false,
                t0: None,
                ready: None,
                new_serial: 0,
                capture_failed: false,
                shown: 0.0,
                created: crate::turnlog::monotonic_now(),
            },
        );
    }

    /// The animations that can no longer run to their end: on a panel that
    /// went dark (it renders nothing), or long past their time (they would
    /// keep the clients' buffers held).
    pub fn stale(&self, now: Duration, lit: impl Fn(&str) -> bool) -> Vec<String> {
        let limit = Duration::from_millis(self.rotate_ms) + FADE + HOLD + Duration::from_secs(2);
        self.anims
            .iter()
            .filter(|(name, anim)| !lit(name) || now.saturating_sub(anim.created) > limit)
            .map(|(name, _)| name.clone())
            .collect()
    }

    /// `output`'s rotation changed from `old_deg` to `new_deg`: its old
    /// picture turns from where it is shown now, the clock starts again, a
    /// new picture captured for another frame is dropped.
    pub fn turned(&mut self, output: &str, old_deg: u32, new_deg: u32) {
        let Some(anim) = self.anims.get_mut(output) else {
            return;
        };
        anim.from = reframe(anim.shown, old_deg, new_deg);
        anim.shown = anim.from;
        anim.turned = true;
        anim.t0 = None;
        anim.ready = None;
        anim.capture_failed = false;
        if let Some(new) = anim.new.take() {
            self.keep(new.tex);
        }
    }

    /// A client on `output` changed its picture.
    pub fn changed(&mut self, output: &str) {
        match self.changes.get_mut(output) {
            Some(n) => *n = n.wrapping_add(1),
            None => {
                self.changes.insert(output.to_owned(), 1);
            }
        }
    }

    /// The clients' picture of `output` taken again `elapsed` into the turn
    /// (at commit count `serial`): the last one becomes the picture that
    /// fades out (at the same angle and scale: the same size), the first
    /// old one, faded by now, is kept for later.
    pub fn retaken(&mut self, output: &str, picture: Picture, elapsed: Duration, serial: u64) {
        let Some(anim) = self.anims.get_mut(output) else {
            self.keep(picture.tex);
            return;
        };
        let Some(last) = anim.new.replace(picture) else {
            return;
        };
        let old = std::mem::replace(&mut anim.old, last);
        anim.ready = Some(elapsed);
        anim.new_serial = serial;
        self.keep(old.tex);
    }

    /// The animation on `output` is over (or replaced).
    pub fn finish(&mut self, output: &str) {
        if let Some(anim) = self.anims.remove(output) {
            self.keep(anim.old.tex);
            if let Some(new) = anim.new {
                self.keep(new.tex);
            }
        }
    }

    /// Before a turn's first frame: the shader compiled, and a texture for
    /// the new picture of `size` ready (allocated and touched by the GPU
    /// once), so that neither happens during the turn.
    pub fn prepare(
        &mut self,
        renderer: &mut GlesRenderer,
        size: Size<i32, smithay::utils::Physical>,
        scale: f64,
    ) {
        let _ = self.program(renderer);
        let wanted: Size<i32, BufferCoords> = (size.w, size.h).into();
        if self.spare.len() >= SPARE_MAX || self.spare.iter().any(|t| t.size() == wanted) {
            return;
        }
        match self.capture(renderer, size, scale, &[]) {
            Ok(picture) => self.keep(picture.tex),
            Err(err) => warn!(?err, "rotation: no texture for the new picture"),
        }
    }

    /// After a turn: let go of held client buffers, as many as fit in
    /// [`RELEASE_BUDGET`], each followed by the renderer's clean-up (a 1x1
    /// render) so its import is destroyed now, not inside a frame. True
    /// while some are still held.
    pub fn release_some(&mut self, renderer: &mut GlesRenderer) -> bool {
        let started = std::time::Instant::now();
        while let Some(dmabuf) = self.hold.held.pop() {
            drop(dmabuf);
            self.hold.released += 1;
            self.run_cleanup(renderer);
            if started.elapsed() >= RELEASE_BUDGET {
                break;
            }
        }
        self.hold.release_ticks += 1;
        let ms = started.elapsed().as_secs_f64() * 1000.0;
        self.hold.release_max_ms = self.hold.release_max_ms.max(ms);
        !self.hold.held.is_empty()
    }

    fn run_cleanup(&mut self, renderer: &mut GlesRenderer) {
        if self.cleanup_target.is_none() {
            match renderer.create_buffer(Fourcc::Abgr8888, (1, 1).into()) {
                Ok(tex) => self.cleanup_target = Some(tex),
                Err(err) => {
                    warn!(?err, "rotation: no clean-up target");
                    return;
                }
            }
        }
        let Some(tex) = self.cleanup_target.as_mut() else {
            return;
        };
        let Ok(mut target) = renderer.bind(tex) else {
            return;
        };
        let frame = renderer.render(&mut target, (1, 1).into(), Transform::Normal);
        if let Ok(frame) = frame {
            let _ = smithay::backend::renderer::Frame::finish(frame);
        }
    }

    fn keep(&mut self, tex: GlesTexture) {
        if self.spare.len() < SPARE_MAX {
            self.spare.push(tex);
        }
    }

    /// Render `elements` into a picture of `size` (physical px, upright as
    /// it reads, at `scale`): a spare texture of that size, or a new one.
    pub fn capture(
        &mut self,
        renderer: &mut GlesRenderer,
        size: Size<i32, smithay::utils::Physical>,
        scale: f64,
        elements: &[crate::render::Element],
    ) -> anyhow::Result<Picture> {
        let wanted: Size<i32, BufferCoords> = (size.w, size.h).into();
        let mut tex = match self.spare.iter().position(|t| t.size() == wanted) {
            Some(i) => self.spare.swap_remove(i),
            None => renderer
                .create_buffer(Fourcc::Abgr8888, wanted)
                .map_err(|err| anyhow::anyhow!("no texture: {err}"))?,
        };
        {
            let mut target = renderer.bind(&mut tex).map_err(|err| anyhow::anyhow!("cannot bind: {err}"))?;
            let mut damage = OutputDamageTracker::new(size, scale, Transform::Normal);
            damage
                .render_output(renderer, &mut target, 0, elements, crate::backend::BACKGROUND)
                .map_err(|err| anyhow::anyhow!("cannot render: {err:?}"))?;
        }
        Ok(Picture { tex, size: (size.w, size.h) })
    }
}

/// The element drawing `picture` at `pose` over the whole panel (`phys` its
/// size as it reads, physical; `logical` the same in logical px), with the
/// picture shader `program` ([`Gfx::program`]).
pub fn picture_element(
    renderer: &mut GlesRenderer,
    program: &GlesTexProgram,
    picture: &Picture,
    pose: &Pose,
    phys: (i32, i32),
    logical: Size<i32, Logical>,
) -> TextureShaderElement {
    let size = picture.size;
    // It covers the panel (black around the picture): opaque unless it
    // fades.
    let opaque =
        (pose.alpha >= 1.0).then(|| vec![Rectangle::<i32, BufferCoords>::from_size((size.0, size.1).into())]);
    let inner = TextureRenderElement::from_static_texture(
        Id::new(),
        renderer.context_id(),
        (0.0, 0.0),
        picture.tex.clone(),
        1,
        Transform::Normal,
        Some(pose.alpha as f32),
        // The whole texture (left out, the source would be the
        // destination's logical size: a corner of the picture).
        Some(Rectangle::from_size((size.0 as f64, size.1 as f64).into())),
        Some(logical),
        opaque,
        Kind::Unspecified,
    );
    TextureShaderElement::new(
        inner,
        program.clone(),
        vec![
            Uniform::new("dst_size", (phys.0 as f32, phys.1 as f32)),
            Uniform::new("pic_size", (size.0 as f32, size.1 as f32)),
            Uniform::new("scale", pose.scale as f32),
            Uniform::new("angle", pose.angle.to_radians() as f32),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const PORTRAIT: (f64, f64) = (1344.0, 1892.0);
    const LANDSCAPE: (f64, f64) = (1892.0, 1344.0);

    fn ms(v: u64) -> Duration {
        Duration::from_millis(v)
    }

    fn turn(t: u64) -> Timing {
        Timing { turn: ms(t), hold: Duration::ZERO, fade: FADE }
    }

    #[test]
    fn the_old_picture_starts_where_it_stood() {
        // wl_output degrees are counter-clockwise on the glass: turned to
        // 90, the old upright picture stands 90 degrees clockwise in the
        // new frame, and turns back from there.
        assert_eq!(start_angle(0, 90), 90.0);
        assert_eq!(start_angle(90, 0), -90.0);
        assert_eq!(start_angle(270, 0), 90.0);
        assert_eq!(start_angle(0, 270), -90.0);
        assert_eq!(start_angle(0, 180), 180.0);
        assert_eq!(start_angle(180, 0), 180.0);
        assert_eq!(start_angle(90, 90), 0.0);
    }

    #[test]
    fn a_turn_during_a_turn_goes_on_from_where_it_is() {
        // Shown at 40 degrees in the 90 frame, turned back to 0: in the 0
        // frame it stands at -50 and turns back up from there.
        assert_eq!(reframe(40.0, 90, 0), -50.0);
        // The other panel of a pair: it held (0), then its own turn.
        assert_eq!(reframe(0.0, 0, 90), 90.0);
        assert_eq!(reframe(170.0, 0, 90), -100.0);
        assert_eq!(normalize(-180.0), 180.0);
        assert_eq!(normalize(540.0), 180.0);
    }

    #[test]
    fn ease_in_out_starts_and_ends_at_rest() {
        assert_eq!(ease_in_out(0.0), 0.0);
        assert_eq!(ease_in_out(1.0), 1.0);
        assert_eq!(ease_in_out(-1.0), 0.0);
        assert_eq!(ease_in_out(2.0), 1.0);
        let mut last = 0.0;
        for i in 1..=100 {
            let v = ease_in_out(i as f64 / 100.0);
            assert!(v >= last, "not monotonic at {i}: {v} < {last}");
            last = v;
        }
        // At rest at both ends: the first and the last hundredth move little.
        assert!(ease_in_out(0.01) < 0.002, "{}", ease_in_out(0.01));
        assert!(1.0 - ease_in_out(0.99) < 0.002, "{}", ease_in_out(0.99));
        // Material's standard curve: past half way at half the time.
        let mid = ease_in_out(0.5);
        assert!(mid > 0.7 && mid < 0.85, "{mid}");
        // The solver against the curve's own points: x(s), y(s) at s = 0.5.
        let x = 3.0 * 0.25 * 0.5 * 0.4 + 3.0 * 0.5 * 0.25 * 0.2 + 0.125;
        let y = 3.0 * 0.5 * 0.25 * 1.0 + 0.125;
        assert!((ease_in_out(x) - y).abs() < 1e-6);
    }

    #[test]
    fn the_scale_is_uniform_and_fits_at_every_angle() {
        // Turned a quarter, the portrait picture covers the landscape frame.
        assert!((fit_scale(PORTRAIT, LANDSCAPE, 90.0) - 1.0).abs() < 1e-9);
        // Upright in it, letterboxed: the height decides.
        assert!((fit_scale(PORTRAIT, LANDSCAPE, 0.0) - 1344.0 / 1892.0).abs() < 1e-9);
        // The new landscape picture fills its own frame upright.
        assert_eq!(fit_scale(LANDSCAPE, LANDSCAPE, 0.0), 1.0);
        assert!((fit_scale(LANDSCAPE, LANDSCAPE, 90.0) - 1344.0 / 1892.0).abs() < 1e-9);
        // In between, the turned picture's bounding box stays inside the
        // panel, touching it.
        for i in 0..=90 {
            let a = i as f64;
            for (pic, panel) in [(PORTRAIT, LANDSCAPE), (LANDSCAPE, LANDSCAPE), (PORTRAIT, PORTRAIT)] {
                let k = fit_scale(pic, panel, a);
                let r = a.to_radians();
                let w = k * (pic.0 * r.cos().abs() + pic.1 * r.sin().abs());
                let h = k * (pic.0 * r.sin().abs() + pic.1 * r.cos().abs());
                assert!(w <= panel.0 + 1e-6 && h <= panel.1 + 1e-6, "{a}: {w}x{h}");
                assert!(k >= 1.0 - 1e-9 || (panel.0 - w).abs() < 1e-6 || (panel.1 - h).abs() < 1e-6, "{a}");
            }
        }
        // Never bigger than drawn.
        assert_eq!(fit_scale((100.0, 100.0), (400.0, 400.0), 0.0), 1.0);
    }

    #[test]
    fn both_pictures_turn_together_and_never_stretch() {
        let t = turn(320);
        let f0 = frame(90.0, PORTRAIT, LANDSCAPE, Duration::ZERO, &t, None, false).unwrap();
        assert_eq!(f0.angle, 90.0);
        let old = f0.old.unwrap();
        assert!((old.scale - 1.0).abs() < 1e-9 && old.alpha == 1.0, "{f0:?}");
        assert_eq!((f0.new, f0.live), (None, false));
        // Eased in: hardly moved after the first frame (11 ms).
        let f1 = frame(90.0, PORTRAIT, LANDSCAPE, ms(11), &t, None, false).unwrap();
        assert!(f1.angle > 89.0, "{f1:?}");
        // The new picture captured at 80 ms: it turns at the same angle,
        // fitting by itself, while the old one fades.
        let f2 = frame(90.0, PORTRAIT, LANDSCAPE, ms(140), &t, Some(ms(80)), false).unwrap();
        let (o, n) = (f2.old.unwrap(), f2.new.unwrap());
        assert_eq!(o.angle, n.angle);
        assert!((o.alpha - 0.5).abs() < 1e-9, "{f2:?}");
        assert_eq!(n.alpha, 1.0);
        assert!((o.scale - fit_scale(PORTRAIT, LANDSCAPE, f2.angle)).abs() < 1e-12);
        assert!((n.scale - fit_scale(LANDSCAPE, LANDSCAPE, f2.angle)).abs() < 1e-12);
        // Faded out, the new picture alone turns on.
        let f3 = frame(90.0, PORTRAIT, LANDSCAPE, ms(250), &t, Some(ms(80)), false).unwrap();
        assert_eq!(f3.old, None);
        assert!(f3.new.is_some() && !f3.live);
        // The turn over: the real scene; nothing left to draw: over.
        assert!(frame(90.0, PORTRAIT, LANDSCAPE, ms(320), &t, Some(ms(80)), false).is_none());
    }

    #[test]
    fn without_the_new_picture_the_old_one_fades_over_the_real_scene_at_the_end() {
        let t = turn(320);
        let f = frame(90.0, PORTRAIT, LANDSCAPE, ms(300), &t, None, false).unwrap();
        assert_eq!((f.new, f.live, f.old.unwrap().alpha), (None, false, 1.0));
        // The end: upright, letterboxed, fading over the live scene.
        let f = frame(90.0, PORTRAIT, LANDSCAPE, ms(380), &t, None, false).unwrap();
        assert!(f.live && f.new.is_none());
        let o = f.old.unwrap();
        assert!(o.angle.abs() < 1e-9);
        assert!((o.scale - 1344.0 / 1892.0).abs() < 1e-9);
        assert!((o.alpha - 0.5).abs() < 1e-9, "{o:?}");
        assert!(frame(90.0, PORTRAIT, LANDSCAPE, ms(441), &t, None, false).is_none());
        // Taken after the end (while it settled): its fade starts then.
        let late = frame(90.0, PORTRAIT, LANDSCAPE, ms(380), &t, Some(ms(350)), false).unwrap();
        assert!(late.live && (late.old.unwrap().alpha - 0.75).abs() < 1e-9, "{late:?}");
    }

    #[test]
    fn after_the_turn_the_pictures_stay_until_the_clients_settle() {
        let t = turn(320);
        // The clients' picture not there yet: the old one stays, at rest,
        // letterboxed, the scene not shown.
        let f = frame(90.0, PORTRAIT, LANDSCAPE, ms(450), &t, None, true).unwrap();
        let o = f.old.unwrap();
        assert!(!f.live && f.new.is_none());
        assert!(o.angle.abs() < 1e-9 && o.alpha == 1.0);
        assert!((o.scale - 1344.0 / 1892.0).abs() < 1e-9);
        // Taken at 400: at rest, filling the panel, the old one fading.
        let f = frame(90.0, PORTRAIT, LANDSCAPE, ms(460), &t, Some(ms(400)), true).unwrap();
        let n = f.new.unwrap();
        assert!(!f.live && n.scale == 1.0 && n.angle.abs() < 1e-9);
        assert!((f.old.unwrap().alpha - 0.5).abs() < 1e-9);
        // Settled: the scene, nothing left over it: over.
        assert!(frame(90.0, PORTRAIT, LANDSCAPE, ms(530), &t, Some(ms(400)), false).is_none());
    }

    #[test]
    fn the_angle_follows_the_curve_and_ends_upright() {
        let t = turn(320);
        for i in 0..=32 {
            let e = ms(i * 10);
            let f = frame(-90.0, PORTRAIT, LANDSCAPE, e, &t, None, false).unwrap();
            let want = -90.0 * (1.0 - ease_in_out(i as f64 / 32.0));
            assert!((f.angle - want).abs() < 1e-9, "{i}: {f:?}");
        }
        // Half a turn as well.
        let f = frame(180.0, PORTRAIT, PORTRAIT, ms(160), &t, None, false).unwrap();
        assert!(f.angle > 0.0 && f.angle < 90.0, "{f:?}");
        assert!(f.old.unwrap().scale < 1.0);
    }

    #[test]
    fn a_panel_that_did_not_turn_holds_then_fades() {
        let t = Timing { turn: Duration::ZERO, hold: HOLD, fade: FADE };
        let f = frame(0.0, PORTRAIT, PORTRAIT, ms(50), &t, None, false).unwrap();
        let o = f.old.unwrap();
        assert!(f.live && f.new.is_none());
        assert_eq!((o.angle, o.scale, o.alpha), (0.0, 1.0, 1.0));
        let f = frame(0.0, PORTRAIT, PORTRAIT, HOLD + ms(60), &t, Some(ms(5)), false).unwrap();
        assert!((f.old.unwrap().alpha - 0.5).abs() < 1e-9);
        assert!(frame(0.0, PORTRAIT, PORTRAIT, HOLD + FADE + ms(1), &t, None, false).is_none());
    }

    fn fake_dmabuf() -> Dmabuf {
        use smithay::backend::allocator::dmabuf::DmabufFlags;
        use smithay::backend::allocator::Modifier;
        let fd = std::fs::File::open("/dev/null").unwrap();
        let mut builder = Dmabuf::builder((4, 4), Fourcc::Abgr8888, Modifier::Linear, DmabufFlags::empty());
        builder.add_plane(fd.into(), 0, 0, 16);
        builder.build().unwrap()
    }

    #[test]
    fn client_buffers_are_held_through_a_turn() {
        let mut hold = BufferHold::default();
        let a = fake_dmabuf();
        let b = fake_dmabuf();
        hold.track(&a, 1.0);
        hold.track(&b, 1.0);
        // Not during a turn: nothing held, the imports not counted.
        assert!(hold.held.is_empty() && hold.imports == 0);
        assert!(hold.hold());
        assert!(!hold.hold(), "held once per turn");
        assert_eq!(hold.held.len(), 2);
        // The client destroys its buffer: the hold keeps it (and its import).
        let weak = a.weak();
        drop(a);
        assert!(!weak.is_gone());
        // A buffer made during the turn is held too, its import counted.
        let c = fake_dmabuf();
        hold.track(&c, 4.0);
        assert_eq!((hold.held.len(), hold.imports, hold.import_max_ms), (3, 1, 4.0));
        // Let go: gone.
        hold.holding = false;
        hold.held.clear();
        assert!(weak.is_gone());
        drop(b);
        // Only what is still alive is held at the next turn.
        assert!(hold.hold());
        assert_eq!(hold.held.len(), 1);
        // A turn while the last one's are still being let go: none twice.
        hold.holding = false;
        assert!(hold.hold());
        assert_eq!(hold.held.len(), 1);
    }

    #[test]
    fn frames_are_timed_at_the_vblank_they_show_on() {
        let p = Duration::from_micros(11_111);
        let last = ms(1000);
        // Right after the flip: the next vblank.
        assert_eq!(display_time(Some(last), last + ms(1), p), last + p);
        // A late render: the vblank after it, in whole periods.
        assert_eq!(display_time(Some(last), last + ms(12), p), last + p * 2);
        assert_eq!(display_time(Some(last), last + ms(50), p), last + p.mul_f64(5.0));
        // No flip yet: now.
        assert_eq!(display_time(None, ms(7), p), ms(7));
        assert_eq!(display_time(Some(last), ms(990), p), last + p);
    }
}
