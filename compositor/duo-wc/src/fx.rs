//! What the compositor draws itself besides the overview: rounded,
//! see-through rectangles (the snap preview of a carried window, the shadow
//! under it), the back arrow at a side edge, and the windows' own pictures
//! where an animation puts them (a window flying to where it was dropped,
//! opening, going home).
//!
//! The rounded rectangles are one small GLES pixel shader (a signed distance
//! to a rounded box: fill, border, soft edge for shadows), compiled once on
//! the first frame that needs it. If it does not compile, plain rectangles
//! are drawn instead (nothing is lost but the corners).

use std::collections::HashMap;

use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::element::memory::{MemoryRenderBuffer, MemoryRenderBufferRenderElement};
use smithay::backend::renderer::element::solid::SolidColorRenderElement;
use smithay::backend::renderer::element::surface::WaylandSurfaceRenderElement;
use smithay::backend::renderer::element::utils::{CropRenderElement, RescaleRenderElement};
use smithay::backend::renderer::element::{AsRenderElements, Id, Kind};
use smithay::backend::renderer::gles::element::PixelShaderElement;
use smithay::backend::renderer::gles::{GlesPixelProgram, GlesRenderer, Uniform, UniformName, UniformType};
use smithay::backend::renderer::utils::CommitCounter;
use smithay::desktop::Window;
use smithay::utils::{Logical, Physical, Point, Rectangle, Scale, Size, Transform};
use tracing::warn;

use crate::icons::Icon;
use crate::overview::{Dir, Rect};
use crate::render::Element;

/// The one accent colour (Adwaita's blue 3, #3584e4: the apps' accent too),
/// straight RGB; use [`premul`] for a see-through one.
pub const ACCENT: [f32; 4] = [0.208, 0.518, 0.894, 1.0];

/// A colour at `alpha`, premultiplied (what the renderer blends with).
pub fn premul(c: [f32; 4], alpha: f32) -> [f32; 4] {
    [c[0] * alpha, c[1] * alpha, c[2] * alpha, alpha]
}

/// The rounded box shader. `size` (physical px), `alpha` come from the
/// renderer; the rest are ours.
const ROUNDED_SRC: &str = r#"
precision mediump float;
varying vec2 v_coords;
uniform vec2 size;
uniform float alpha;
uniform vec4 fill;
uniform vec4 edge;
uniform float radius;
uniform float border;
uniform float soft;
uniform float invert;
#if defined(DEBUG_FLAGS)
uniform float tint;
#endif

float box(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + vec2(r);
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

void main() {
    vec2 p = v_coords * size - size * 0.5;
    float r = min(radius, min(size.x, size.y) * 0.5 - soft);
    float d = box(p, size * 0.5 - vec2(soft), max(r, 0.0));
    float cover = soft > 0.5 ? 1.0 - smoothstep(-soft, soft, d) : clamp(0.5 - d, 0.0, 1.0);
    if (invert > 0.5) {
        // Only outside the rounded box (corner masks).
        gl_FragColor = fill * (1.0 - cover) * alpha;
        return;
    }
    float inner = clamp(0.5 - (d + border), 0.0, 1.0);
    vec4 c = border > 0.0 ? mix(edge, fill, inner) : fill;
    gl_FragColor = c * cover * alpha;
}
"#;

/// A rounded box to draw (output-local logical px).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RoundedBox {
    /// Where.
    pub rect: Rect,
    /// Corner radius (logical px).
    pub radius: f64,
    /// Fill, premultiplied.
    pub fill: [f32; 4],
    /// Border colour, premultiplied, and its width (logical px; 0 = none).
    pub edge: [f32; 4],
    /// Border width.
    pub border: f64,
    /// A soft edge this wide (logical px): a shadow. 0 = crisp.
    pub soft: f64,
    /// Fill only OUTSIDE the rounded box, inside its rectangle (a corner
    /// mask in a background colour, rounding what is under it).
    pub invert: bool,
}

impl RoundedBox {
    /// A soft shadow around `rect` (grown by `soft`).
    pub fn shadow(rect: Rect, radius: f64, soft: f64, opacity: f32) -> Self {
        RoundedBox {
            rect: Rect::new(
                rect.x - soft,
                rect.y - soft + soft * 0.35,
                rect.w + 2.0 * soft,
                rect.h + 2.0 * soft,
            ),
            radius: radius + soft,
            fill: [0.0, 0.0, 0.0, opacity],
            edge: [0.0; 4],
            border: 0.0,
            soft,
            invert: false,
        }
    }
}

/// What the effects draw with, kept across frames.
#[derive(Default)]
pub struct Gfx {
    rounded: Option<GlesPixelProgram>,
    compile_failed: bool,
    /// Elements by name, reused while what they show stays the same (so
    /// damage tracking sees nothing new).
    boxes: HashMap<String, (RoundedBox, PixelShaderElement)>,
    solids: HashMap<String, Id>,
    icons: HashMap<(Icon, i32), MemoryRenderBuffer>,
}

fn phys(r: Rect, scale: f64) -> Rectangle<i32, Physical> {
    let x0 = (r.x * scale).round() as i32;
    let y0 = (r.y * scale).round() as i32;
    let x1 = ((r.x + r.w) * scale).round() as i32;
    let y1 = ((r.y + r.h) * scale).round() as i32;
    Rectangle::new((x0, y0).into(), (x1 - x0, y1 - y0).into())
}

impl Gfx {
    fn program(&mut self, renderer: &mut GlesRenderer) -> Option<GlesPixelProgram> {
        if self.rounded.is_none() && !self.compile_failed {
            match renderer.compile_custom_pixel_shader(
                ROUNDED_SRC,
                &[
                    UniformName::new("fill", UniformType::_4f),
                    UniformName::new("edge", UniformType::_4f),
                    UniformName::new("radius", UniformType::_1f),
                    UniformName::new("border", UniformType::_1f),
                    UniformName::new("soft", UniformType::_1f),
                    UniformName::new("invert", UniformType::_1f),
                ],
            ) {
                Ok(program) => self.rounded = Some(program),
                Err(err) => {
                    warn!(?err, "the rounded-box shader did not compile: plain rectangles instead");
                    self.compile_failed = true;
                }
            }
        }
        self.rounded.clone()
    }

    /// A rounded box (the key keeps one element per thing drawn).
    pub fn rounded(&mut self, renderer: &mut GlesRenderer, key: &str, b: RoundedBox, scale: f64) -> Element {
        let Some(program) = self.program(renderer) else {
            let id = self.solids.entry(key.to_owned()).or_insert_with(Id::new).clone();
            return Element::Solid(SolidColorRenderElement::new(
                id,
                phys(b.rect, scale),
                CommitCounter::default(),
                b.fill,
                Kind::Unspecified,
            ));
        };
        let area: Rectangle<i32, Logical> = Rectangle::new(
            (b.rect.x.round() as i32, b.rect.y.round() as i32).into(),
            (b.rect.w.round().max(1.0) as i32, b.rect.h.round().max(1.0) as i32).into(),
        );
        let uniforms = || {
            vec![
                Uniform::new("fill", (b.fill[0], b.fill[1], b.fill[2], b.fill[3])),
                Uniform::new("edge", (b.edge[0], b.edge[1], b.edge[2], b.edge[3])),
                Uniform::new("radius", (b.radius * scale) as f32),
                Uniform::new("border", (b.border * scale) as f32),
                Uniform::new("soft", (b.soft * scale) as f32),
                Uniform::new("invert", if b.invert { 1.0f32 } else { 0.0 }),
            ]
        };
        match self.boxes.get_mut(key) {
            Some((last, element)) => {
                if *last != b {
                    element.resize(area, None);
                    element.update_uniforms(uniforms());
                    *last = b;
                }
                Element::Shader(element.clone())
            }
            None => {
                let element =
                    PixelShaderElement::new(program, area, None, 1.0, uniforms(), Kind::Unspecified);
                self.boxes.insert(key.to_owned(), (b, element.clone()));
                Element::Shader(element)
            }
        }
    }

    /// One of the compositor's round icons at `rect` (output-local logical).
    pub fn icon(
        &mut self,
        renderer: &mut GlesRenderer,
        icon: Icon,
        rect: Rect,
        scale: f64,
        alpha: f32,
    ) -> Option<Element> {
        let px = (rect.w * scale).round().max(1.0) as i32;
        let buffer = self.icons.entry((icon, px)).or_insert_with(|| {
            let pixels = crate::icons::draw(icon, px as usize);
            MemoryRenderBuffer::from_slice(&pixels, Fourcc::Argb8888, (px, px), 1, Transform::Normal, None)
        });
        let location: Point<f64, Physical> = (rect.x * scale, rect.y * scale).into();
        MemoryRenderBufferRenderElement::from_buffer(
            renderer,
            location.to_i32_round::<i32>().to_f64(),
            buffer,
            Some(alpha),
            Some(Rectangle::from_size((px as f64, px as f64).into())),
            Some(Size::from((rect.w.round() as i32, rect.h.round() as i32))),
            Kind::Unspecified,
        )
        .ok()
        .map(Element::Icon)
    }

    /// Forget the elements of things no longer drawn (keys starting with
    /// `prefix`).
    pub fn forget(&mut self, prefix: &str) {
        self.boxes.retain(|k, _| !k.starts_with(prefix));
        self.solids.retain(|k, _| !k.starts_with(prefix));
    }
}

/// A window's own surfaces (popups too) scaled into `rect` (output-local
/// logical) and cut to it, at `alpha`.
pub fn window_in(
    renderer: &mut GlesRenderer,
    window: &Window,
    rect: Rect,
    scale: f64,
    alpha: f32,
) -> Vec<Element> {
    let geometry = window.geometry();
    if geometry.size.w <= 0 || geometry.size.h <= 0 || rect.w < 1.0 || rect.h < 1.0 {
        return Vec::new();
    }
    let target = phys(rect, scale);
    let origin = target.loc;
    let location = origin - geometry.loc.to_physical_precise_round(scale);
    let sx = rect.w / geometry.size.w as f64;
    let sy = rect.h / geometry.size.h as f64;
    let surfaces: Vec<WaylandSurfaceRenderElement<GlesRenderer>> =
        AsRenderElements::<GlesRenderer>::render_elements(
            window,
            renderer,
            location,
            Scale::from(scale),
            alpha,
        );
    surfaces
        .into_iter()
        .filter_map(|surface| {
            let scaled = RescaleRenderElement::from_element(surface, origin, Scale { x: sx, y: sy });
            CropRenderElement::from_element(scaled, scale, target).map(Element::Thumb)
        })
        .collect()
}

/// The back arrow at a side edge while a swipe from it goes on.
#[derive(Debug, Clone, PartialEq)]
pub struct BackArrow {
    /// The panel.
    pub output: String,
    /// Which side (Left: from the left edge).
    pub from_left: bool,
    /// The finger's height on the panel (output-local logical y).
    pub y: f64,
    /// How far the swipe has come.
    pub travel: f64,
    /// Far enough: letting go goes back.
    pub armed: bool,
}

/// The size of the back arrow's disc.
const BACK_DISC: f64 = 44.0;

/// The back arrow's disc (output-local logical): it comes out of the edge
/// with the finger, at most a disc and a half in.
pub fn back_arrow_rect(arrow: &BackArrow, panel_w: f64) -> Rect {
    let reach = (arrow.travel * 0.45).min(BACK_DISC * 1.6);
    let size = BACK_DISC * (0.7 + 0.3 * (arrow.travel / crate::gesture::BACK_TRIGGER).min(1.0));
    let x = if arrow.from_left { reach - size * 0.5 } else { panel_w - reach - size * 0.5 };
    let x = x.clamp(2.0, (panel_w - size - 2.0).max(2.0));
    Rect::new(x, arrow.y - size / 2.0, size, size)
}

/// The span pad's look (the accent, faint; brighter while it is the
/// target) on one panel: the pad (`pad`, this panel's frame; it reaches
/// over the hinge, so only its part on the panel shows) with the two-panels
/// icon in the middle of that part. Front to back. `key` names the elements
/// (one set per panel and per user: the carry, the overview).
pub fn span_pad_elements(
    renderer: &mut GlesRenderer,
    gfx: &mut Gfx,
    key: &str,
    pad: Rect,
    hot: bool,
    panel: (f64, f64),
    scale: f64,
) -> Vec<Element> {
    let mut out = Vec::new();
    let Some(part) = pad.intersect(Rect::new(0.0, 0.0, panel.0, panel.1)) else {
        return out;
    };
    let side = (part.w.min(part.h) - 8.0).clamp(0.0, 36.0);
    if side >= 12.0 {
        let (cx, cy) = part.center();
        let icon = Rect::new(cx - side / 2.0, cy - side / 2.0, side, side);
        if let Some(e) = gfx.icon(renderer, Icon::Span, icon, scale, if hot { 1.0 } else { 0.8 }) {
            out.push(e);
        }
    }
    out.push(gfx.rounded(
        renderer,
        &format!("{key}/pad"),
        RoundedBox {
            rect: pad.inset(2.0),
            radius: 14.0,
            fill: premul(ACCENT, if hot { 0.30 } else { 0.12 }),
            edge: premul(ACCENT, if hot { 0.95 } else { 0.55 }),
            border: 2.0,
            soft: 0.0,
            invert: false,
        },
        scale,
    ));
    out
}

/// The elements of the back arrow (front to back).
pub fn back_arrow_elements(
    renderer: &mut GlesRenderer,
    gfx: &mut Gfx,
    arrow: &BackArrow,
    panel_w: f64,
    scale: f64,
) -> Vec<Element> {
    let r = back_arrow_rect(arrow, panel_w);
    let mut out = Vec::new();
    let alpha = (arrow.travel / crate::gesture::BACK_TRIGGER).clamp(0.35, 1.0) as f32;
    if let Some(e) = gfx.icon(renderer, Icon::Arrow(Dir::Left), r, scale, alpha) {
        out.push(e);
    }
    let ring = Rect::new(r.x - 3.0, r.y - 3.0, r.w + 6.0, r.h + 6.0);
    let fill = if arrow.armed { premul(ACCENT, 1.0) } else { [0.0, 0.0, 0.0, 0.0] };
    out.push(gfx.rounded(
        renderer,
        &format!("{}/back", arrow.output),
        RoundedBox {
            rect: ring,
            radius: ring.w / 2.0,
            fill,
            edge: premul([1.0, 1.0, 1.0, 1.0], 0.25 * alpha),
            border: if arrow.armed { 0.0 } else { 1.5 },
            soft: 0.0,
            invert: false,
        },
        scale,
    ));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn premultiplied_colours() {
        assert_eq!(premul([1.0, 0.5, 0.0, 1.0], 0.5), [0.5, 0.25, 0.0, 0.5]);
    }

    #[test]
    fn the_back_arrow_comes_out_with_the_finger_and_stays_on_the_panel() {
        let mut a =
            BackArrow { output: "DSI-1".into(), from_left: true, y: 300.0, travel: 10.0, armed: false };
        let near = back_arrow_rect(&a, 538.0);
        a.travel = 120.0;
        let far = back_arrow_rect(&a, 538.0);
        assert!(far.x > near.x && far.w > near.w);
        assert!(far.x + far.w < 538.0 / 2.0);
        assert!(near.x >= 2.0);
        a.from_left = false;
        let right = back_arrow_rect(&a, 538.0);
        assert!(right.x > 538.0 / 2.0 && right.x + right.w <= 536.0);
        assert!((right.y + right.h / 2.0 - 300.0).abs() < 1e-9);
        // A long way in: it stops.
        a.travel = 900.0;
        assert_eq!(
            back_arrow_rect(&a, 538.0).x,
            back_arrow_rect(&BackArrow { travel: 400.0, ..a.clone() }, 538.0).x
        );
    }

    #[test]
    fn a_shadow_grows_around_its_box() {
        let s = RoundedBox::shadow(Rect::new(10.0, 10.0, 100.0, 50.0), 12.0, 8.0, 0.4);
        assert!(s.rect.x < 10.0 && s.rect.w > 100.0 && s.soft == 8.0);
        assert_eq!(s.fill[3], 0.4);
    }
}
