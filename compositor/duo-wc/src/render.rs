//! Building one output's picture.
//!
//! The list is front to back: upper layer-shell surfaces, then windows from the
//! front of the stack, then lower layers. Positions are relative to the output's
//! origin in the global space and converted with its (possibly fractional) scale,
//! so a window that spans both panels is cut at each panel's edge and the strip
//! behind the hinge (gap on) is simply never drawn.
//!
//! A fullscreen window in front on an output hides that output's top-layer
//! surfaces (the status bar), except the on-screen keyboard and the app
//! grid, and the shell's dock; the other overlay surfaces (the quick menu, banners) stay above
//! everything.
//!
//! While the task overview is open on an output ([`crate::taskview`]), its
//! cards and background come right after the overlay layer and the windows
//! are not drawn there (their pictures are in the cards); the top layer (the
//! status bar) shows above the overview's area.
//!
//! A panel shared by two windows ([`crate::tiling`]) cuts each of them to its
//! half (a client that draws bigger than asked does not spill into the other
//! half; its popups are not cut), and while the pair is in front the divider
//! is drawn over the gap between them.
//!
//! A window placed on one panel is drawn only there: one that draws bigger
//! than its panel is scaled down into it ([`crate::dialog`]), and whatever is
//! still past the edge is off that panel's picture, never on the other one.

use smithay::backend::renderer::element::memory::MemoryRenderBufferRenderElement;
use smithay::backend::renderer::element::solid::SolidColorRenderElement;
use smithay::backend::renderer::element::surface::WaylandSurfaceRenderElement;
use smithay::backend::renderer::element::utils::{CropRenderElement, RescaleRenderElement};
use smithay::backend::renderer::element::AsRenderElements;
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::desktop::{layer_map_for_output, Space, Window};
use smithay::output::Output;
use smithay::utils::{Logical, Physical, Point, Scale};
use smithay::wayland::seat::WaylandFocus as _;
use smithay::wayland::shell::wlr_layer::Layer;

use crate::state::{
    window_data, DuoWc, Placement, AMBIENT_NAMESPACES, FULLSCREEN_HIDES_OVERLAYS, FULLSCREEN_KEEPS_TOP,
    OSK_NAMESPACES,
};

smithay::backend::renderer::element::render_elements! {
    /// What the compositor hands to the DRM compositor, front to back.
    pub Element<=GlesRenderer>;
    /// A client surface where it is (windows, layer surfaces, popups).
    Surface=WaylandSurfaceRenderElement<GlesRenderer>,
    /// A window's surface scaled into its overview card.
    Thumb=CropRenderElement<RescaleRenderElement<WaylandSurfaceRenderElement<GlesRenderer>>>,
    /// A window's surface cut to its half of a split panel.
    Clipped=CropRenderElement<WaylandSurfaceRenderElement<GlesRenderer>>,
    /// A plain rectangle (the overview's background, frames).
    Solid=SolidColorRenderElement,
    /// A picture the compositor draws itself (the overview's buttons).
    Icon=MemoryRenderBufferRenderElement<GlesRenderer>,
    /// A rounded box (crate::fx): a snap preview, a shadow, the back arrow's ring.
    Shader=smithay::backend::renderer::gles::element::PixelShaderElement,
    /// The turning snapshot of a rotation (crate::rotanim), in front of all.
    Rotate=smithay::backend::renderer::gles::element::TextureShaderElement,
    /// A panel's picture on a display that mirrors it (crate::mirrorlive).
    Mirror=smithay::backend::renderer::element::texture::TextureRenderElement<smithay::backend::renderer::gles::GlesTexture>,
}

/// What an output shows of an ambient screen ([`crate::ambient`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ambient {
    /// None now: the normal picture.
    Off,
    /// One is on, the shell has not drawn its strip for it yet: black.
    Waiting,
    /// One is on and the strip is drawn: the shell's ambient surfaces.
    Strip,
}

/// The elements of one output, front to back; `overview` is the task
/// overview's plan for it, when it is open there; `split` the panel's split,
/// when one is in force there.
#[allow(clippy::too_many_arguments)] // each is one layer of the frame, from one owner
pub fn elements(
    renderer: &mut GlesRenderer,
    space: &Space<Window>,
    output: &Output,
    overview: Option<(&mut crate::taskview::Gfx, &crate::taskview::Plan)>,
    split: Option<(&mut crate::tiling::SplitGfx, &crate::tiling::SplitPlan)>,
    locked: Option<Option<smithay::reexports::wayland_server::protocol::wl_surface::WlSurface>>,
    ambient: Ambient,
    carry: Option<(&mut crate::fx::Gfx, &crate::windrag::CarryPlan)>,
    front: Option<Element>,
    x11_popups: &[crate::xwayland::Overlay],
) -> Vec<Element> {
    let Some(output_geometry) = space.output_geometry(output) else {
        return Vec::new();
    };
    let scale = Scale::from(output.current_scale().fractional_scale());
    let mut elements = Vec::new();
    // A rotation's turning snapshot covers everything (crate::rotanim).
    elements.extend(front);

    if ambient != Ambient::Off {
        // The peek / glance screen (crate::ambient): the shell's ambient
        // surfaces and nothing else, on black (also over a lock). Until the
        // shell has drawn the strip for THIS ambient screen (a buffer
        // committed since it started: the surface stays mapped between
        // screens and holds the last one's picture, an old clock), plain
        // black: never the lock screen or home under it.
        if ambient == Ambient::Waiting {
            return elements;
        }
        let layers = layer_map_for_output(output);
        for wanted in [Layer::Overlay, Layer::Top, Layer::Bottom, Layer::Background] {
            for layer in layers.layers_on(wanted).rev() {
                if !AMBIENT_NAMESPACES.contains(&layer.namespace()) {
                    continue;
                }
                let Some(geometry) = layers.layer_geometry(layer) else {
                    continue;
                };
                let location: Point<i32, Physical> = geometry.loc.to_physical_precise_round(scale);
                elements.extend(AsRenderElements::<GlesRenderer>::render_elements::<Element>(
                    layer, renderer, location, scale, 1.0,
                ));
            }
        }
        return elements;
    }

    if let Some(lock_surface) = locked {
        // Locked: the lock surface and nothing else (no window, no layer).
        if let Some(surface) = lock_surface {
            elements.extend(
                smithay::backend::renderer::element::surface::render_elements_from_surface_tree::<
                    GlesRenderer,
                    WaylandSurfaceRenderElement<GlesRenderer>,
                >(
                    renderer,
                    &surface,
                    (0, 0),
                    scale,
                    1.0,
                    smithay::backend::renderer::element::Kind::Unspecified,
                )
                .into_iter()
                .map(Element::from),
            );
        }
        return elements;
    }

    let hide_top = overview.is_none() && fullscreen_in_front(space, output);
    // While the task overview is open the on-screen keyboard is not shown:
    // the overview holds the keyboard focus off the windows.
    let hide_osk = overview.is_some() || carry.as_ref().map(|(_, p)| p.hide_osk).unwrap_or(false);
    let layers = layer_map_for_output(output);
    let push_layers = |elements: &mut Vec<Element>, renderer: &mut GlesRenderer, wanted: Layer| {
        for layer in layers.layers_on(wanted).rev() {
            if AMBIENT_NAMESPACES.contains(&layer.namespace()) {
                // The shell keeps its peek / glance surfaces mapped, ready:
                // shown only during an ambient screen (above).
                continue;
            }
            if wanted == Layer::Top && hide_top && !FULLSCREEN_KEEPS_TOP.contains(&layer.namespace()) {
                continue;
            }
            if hide_osk && OSK_NAMESPACES.contains(&layer.namespace()) {
                continue;
            }
            if wanted == Layer::Overlay && hide_top && FULLSCREEN_HIDES_OVERLAYS.contains(&layer.namespace())
            {
                continue;
            }
            let Some(geometry) = layers.layer_geometry(layer) else {
                continue;
            };
            // Layer geometry is already relative to its output.
            let location: Point<i32, Physical> = geometry.loc.to_physical_precise_round(scale);
            elements.extend(AsRenderElements::<GlesRenderer>::render_elements::<Element>(
                layer, renderer, location, scale, 1.0,
            ));
        }
    };

    let scale_f = output.current_scale().fractional_scale();
    let name = output.name();
    let (mut fx_gfx, carry_plan) = match carry {
        Some((gfx, plan)) => (Some(gfx), Some(plan)),
        None => (None, None),
    };
    // In front of everything: the back arrow, then a carried window (with
    // its shadow).
    if let (Some(gfx), Some(plan)) = (fx_gfx.as_deref_mut(), carry_plan) {
        if let Some(arrow) = &plan.back {
            elements.extend(crate::fx::back_arrow_elements(renderer, gfx, arrow, plan.panel_w, scale_f));
        }
        if let Some((window, rect)) = &plan.ghost {
            elements.extend(crate::fx::window_in(renderer, window, *rect, scale_f, 0.96));
            elements.push(gfx.rounded(
                renderer,
                &format!("drag/{name}/shadow"),
                crate::fx::RoundedBox::shadow(*rect, 10.0, 18.0, 0.55),
                scale_f,
            ));
        }
    }

    push_layers(&mut elements, renderer, Layer::Overlay);
    let showing_overview = overview.is_some();
    if let Some((gfx, plan)) = overview {
        elements.extend(crate::taskview::elements(renderer, gfx, plan, output));
    }
    // X11's menus and tooltips (override-redirect, crate::xwayland), where
    // the app put them: in front of the windows and the status bar.
    if !showing_overview {
        for popup in x11_popups.iter().rev() {
            if !popup.rect.overlaps(output_geometry) {
                continue;
            }
            let location: Point<i32, Physical> =
                (popup.rect.loc - output_geometry.loc).to_physical_precise_round(scale);
            elements.extend(
                smithay::backend::renderer::element::surface::render_elements_from_surface_tree::<
                    GlesRenderer,
                    WaylandSurfaceRenderElement<GlesRenderer>,
                >(
                    renderer,
                    &popup.surface,
                    location,
                    scale,
                    1.0,
                    smithay::backend::renderer::element::Kind::Unspecified,
                )
                .into_iter()
                .map(Element::from),
            );
        }
    }
    push_layers(&mut elements, renderer, Layer::Top);

    // Where a carried window would go: over the windows, under the bar.
    if let (Some(gfx), Some(Some(preview))) = (fx_gfx.as_deref_mut(), carry_plan.map(|p| p.preview)) {
        let accent = crate::fx::ACCENT;
        elements.push(gfx.rounded(
            renderer,
            &format!("drag/{name}/preview"),
            crate::fx::RoundedBox {
                rect: preview.inset(6.0),
                radius: 18.0,
                fill: crate::fx::premul(accent, 0.22),
                edge: crate::fx::premul(accent, 0.85),
                border: 2.5,
                soft: 0.0,
                invert: false,
            },
            scale_f,
        ));
    }

    // The span pad while a window is carried: let go there, both panels.
    if let (Some(gfx), Some(Some((pad, hot)))) = (fx_gfx, carry_plan.map(|p| p.pad)) {
        let panel = (output_geometry.size.w as f64, output_geometry.size.h as f64);
        elements.extend(crate::fx::span_pad_elements(
            renderer,
            gfx,
            &format!("drag/{name}"),
            pad,
            hot,
            panel,
            scale_f,
        ));
    }

    let tiles = split.as_ref().map(|(_, plan)| (*plan).clone());
    if let (Some((gfx, plan)), false) = (split, showing_overview) {
        elements.extend(crate::tiling::elements(renderer, gfx, plan, output));
    }

    for window in space.elements().rev() {
        if showing_overview {
            break;
        }
        let id = window_data(window).id;
        // An external display shows its own windows only, a panel none of
        // a display's (a window may hang over its display's edge, which in
        // the global space is a panel: crate::desk).
        let display_window = match &*window_data(window).placement.borrow() {
            crate::state::Placement::Output(on) if crate::extout::is_external(on) => Some(*on == name),
            _ => None,
        };
        if crate::extout::is_external(&name) {
            if display_window != Some(true) {
                continue;
            }
        } else if display_window.is_some() {
            continue;
        }
        if let Some(plan) = carry_plan {
            if plan.hidden.contains(&id) {
                continue;
            }
            if let Some((rect, alpha)) = plan.moved.get(&id) {
                // Flying (dropped, opening, going home): drawn where the
                // spring is, from its own buffer.
                elements.extend(crate::fx::window_in(renderer, window, *rect, scale_f, *alpha));
                continue;
            }
        } else if *window_data(window).minimized.borrow() {
            continue;
        }
        let Some(bbox) = space.element_bbox(window) else {
            continue;
        };
        if !bbox.overlaps(output_geometry) || !shown_on(space, window, output) {
            continue;
        }
        let Some(location) = space.element_location(window) else {
            continue;
        };
        if let Some(fit) = window_data(window).fit.borrow().filter(|f| f.factor < 1.0) {
            // Bigger than its panel: drawn scaled down into it, popups and
            // all (crate::dialog), around its window geometry's corner.
            elements.extend(scaled_window(
                renderer,
                window,
                location - output_geometry.loc,
                fit.factor,
                scale,
                output_geometry.size,
            ));
            continue;
        }
        let render_location: Point<i32, Logical> = location - window.geometry().loc - output_geometry.loc;
        let mut tile = tiles.as_ref().and_then(|plan| plan.tile_of(window_data(window).id));
        if *window_data(window).android_full.borrow() {
            // An Android window in full screen: its caption bar reaches
            // above its panel, cut there (crate::waydroid).
            if let crate::state::Placement::Output(name) = &*window_data(window).placement.borrow() {
                if *name != output.name() {
                    continue;
                }
                tile = Some(smithay::utils::Rectangle::from_size(output_geometry.size));
            }
        }
        match tile {
            Some(tile) => elements.extend(clipped_window(
                renderer,
                window,
                render_location.to_physical_precise_round(scale),
                scale,
                tile.to_physical_precise_round(scale),
            )),
            None => elements.extend(AsRenderElements::<GlesRenderer>::render_elements::<Element>(
                window,
                renderer,
                render_location.to_physical_precise_round(scale),
                scale,
                1.0,
            )),
        }
    }

    push_layers(&mut elements, renderer, Layer::Bottom);
    push_layers(&mut elements, renderer, Layer::Background);

    elements
}

/// A window cut to `tile` (physical, in the output's frame): its popups whole
/// in front, then its own surfaces cropped.
fn clipped_window(
    renderer: &mut GlesRenderer,
    window: &Window,
    location: Point<i32, Physical>,
    scale: Scale<f64>,
    tile: smithay::utils::Rectangle<i32, Physical>,
) -> Vec<Element> {
    let mut out = Vec::new();
    // An xdg toplevel's or an X11 window's (crate::xwayland) surface.
    let Some(surface) = window.wl_surface().map(|s| s.into_owned()) else {
        return out;
    };
    let surface = &surface;
    for (popup, popup_offset) in smithay::desktop::PopupManager::popups_for_surface(surface) {
        let offset =
            (window.geometry().loc + popup_offset - popup.geometry().loc).to_physical_precise_round(scale);
        out.extend(
            smithay::backend::renderer::element::surface::render_elements_from_surface_tree::<
                GlesRenderer,
                WaylandSurfaceRenderElement<GlesRenderer>,
            >(
                renderer,
                popup.wl_surface(),
                location + offset,
                scale,
                1.0,
                smithay::backend::renderer::element::Kind::Unspecified,
            )
            .into_iter()
            .map(Element::from),
        );
    }
    let own: Vec<WaylandSurfaceRenderElement<GlesRenderer>> =
        smithay::backend::renderer::element::surface::render_elements_from_surface_tree(
            renderer,
            surface,
            location,
            scale,
            1.0,
            smithay::backend::renderer::element::Kind::Unspecified,
        );
    for element in own {
        if let Some(cropped) = CropRenderElement::from_element(element, scale, tile) {
            out.push(Element::Clipped(cropped));
        }
    }
    out
}

/// A window drawn `factor` times its size around its window geometry's
/// corner `corner` (logical, in the output's frame), popups included, cut
/// to the output. Smithay's surface elements take their size from the
/// output's scale when they are drawn (a smaller `Scale` given when they are
/// made moves only their subsurfaces and popups: the window was drawn at
/// full size, cut at the panel's edge, 2026-11-12), so each is rescaled
/// around the corner, as the overview's cards and the flights do.
fn scaled_window(
    renderer: &mut GlesRenderer,
    window: &Window,
    corner: Point<i32, Logical>,
    factor: f64,
    scale: Scale<f64>,
    panel: smithay::utils::Size<i32, Logical>,
) -> Vec<Element> {
    let origin: Point<i32, Physical> = corner.to_f64().to_physical(scale).to_i32_round();
    let location: Point<i32, Physical> = (corner - window.geometry().loc).to_physical_precise_round(scale);
    let panel = smithay::utils::Rectangle::from_size(panel.to_f64().to_physical(scale).to_i32_round());
    let surfaces: Vec<WaylandSurfaceRenderElement<GlesRenderer>> =
        AsRenderElements::<GlesRenderer>::render_elements(window, renderer, location, scale, 1.0);
    surfaces
        .into_iter()
        .filter_map(|surface| {
            let scaled = RescaleRenderElement::from_element(surface, origin, factor);
            CropRenderElement::from_element(scaled, scale, panel).map(Element::Thumb)
        })
        .collect()
}

/// Whether a window is shown on an output at all: a window placed on one
/// panel only there (a client that draws past its panel does not reach over
/// the hinge: crate::dialog), unless that panel is gone (then wherever it
/// fell back to); a spanning one wherever it is.
pub(crate) fn shown_on(space: &Space<Window>, window: &Window, output: &Output) -> bool {
    match &*window_data(window).placement.borrow() {
        Placement::Output(name) => *name == output.name() || !space.outputs().any(|o| o.name() == *name),
        Placement::Span => true,
    }
}

/// Whether the frontmost window on an output is fullscreen there: then the
/// top layer (status bars) is not shown or touched on it.
pub fn fullscreen_in_front(space: &Space<Window>, output: &Output) -> bool {
    let Some(output_geometry) = space.output_geometry(output) else {
        return false;
    };
    space
        .elements()
        .rev()
        .filter(|w| !*window_data(w).minimized.borrow())
        .find(|w| {
            shown_on(space, w, output)
                && space.element_bbox(w).map(|bbox| bbox.overlaps(output_geometry)).unwrap_or(false)
        })
        .map(|w| *window_data(w).fullscreen.borrow())
        .unwrap_or(false)
}

/// Tell the clients on this output they may draw the next frame, and
/// release the fifo barriers (wp_fifo_v1) of what it paces: their next
/// content update may come now. -> the clients an update of which was held
/// by a barrier ([`DuoWc::blockers_cleared`], once this frame is out).
///
/// Each window is paced by ONE output: its own, or the first LIT one for a
/// window that spans them all (otherwise it would be asked for frames twice a
/// vblank, or never while the leftmost panel is off; `crate::pacing`). A
/// hidden window (a dark panel, home, covered by a fullscreen window, an
/// ambient screen, the lock) is paced by none: it gets its frames from the
/// slow tick (`crate::frames`), at most once a second.
pub fn send_frames(state: &DuoWc, output: &Output) -> Vec<smithay::reexports::wayland_server::Client> {
    let time = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let name = output.name();
    let pace = state.pacing_decide();
    let mut held = Vec::new();
    let mut release = |surface: &smithay::reexports::wayland_server::protocol::wl_surface::WlSurface,
                       states: &smithay::wayland::compositor::SurfaceData| {
        if let Some(client) = crate::frames::release_fifo(surface, states) {
            held.push(client);
        }
    };

    for window in state.space.elements() {
        let paced_here = pace
            .get(&window_data(window).id)
            .and_then(|p| p.paced_by.as_deref())
            .is_some_and(|by| by == name);
        if paced_here {
            window.send_frame(output, time, None, |_, _| Some(output.clone()));
            window.with_surfaces(&mut release);
        }
    }

    // X11's popups on this output (crate::xwayland).
    for popup in state.x11_overlays() {
        let on = state.space.output_geometry(output).map(|g| popup.rect.overlaps(g)).unwrap_or(false);
        if on {
            smithay::desktop::utils::send_frames_surface_tree(&popup.surface, output, time, None, |_, _| {
                Some(output.clone())
            });
            smithay::wayland::compositor::with_surface_tree_downward(
                &popup.surface,
                (),
                |_, _, _| smithay::wayland::compositor::TraversalAction::DoChildren(()),
                |surface, states, _| release(surface, states),
                |_, _, _| true,
            );
        }
    }

    // Laptop mode's keyboard panel draws only the shell's labels there
    // (crate::laptopmode::keyboard_scene): the panel's other layer surfaces
    // (its bar, the home screen) are not shown, so not asked to draw.
    let keys_only = state.laptop_shows(&output.name());
    let layers = layer_map_for_output(output);
    for layer in layers.layers() {
        if keys_only && layer.namespace() != crate::laptop::LABELS_NAMESPACE {
            continue;
        }
        layer.send_frame(output, time, None, |_, _| Some(output.clone()));
        layer.with_surfaces(&mut release);
    }
    drop(layers);
    if let Some(surface) = state.lock_surface(output) {
        smithay::desktop::utils::send_frames_surface_tree(&surface, output, time, None, |_, _| {
            Some(output.clone())
        });
        smithay::wayland::compositor::with_surface_tree_downward(
            &surface,
            (),
            |_, _, _| smithay::wayland::compositor::TraversalAction::DoChildren(()),
            |surface, states, _| release(surface, states),
            |_, _, _| true,
        );
    }
    held
}

/// The presentation feedback (wp_presentation) of the surfaces in one
/// output's frame, to be told when that frame reaches the panel: the windows
/// paced by this output (their own panel, or for a spanning one the first
/// lit panel, `spans_paced_here`, as with frame callbacks) and its layer
/// surfaces.
pub fn take_feedback(
    space: &Space<Window>,
    output: &Output,
    spans_paced_here: bool,
    states: &smithay::backend::renderer::element::RenderElementStates,
) -> smithay::desktop::utils::OutputPresentationFeedback {
    use smithay::desktop::utils::{
        surface_presentation_feedback_flags_from_states, OutputPresentationFeedback,
    };
    let mut feedback = OutputPresentationFeedback::new(output);
    for window in space.elements() {
        let paced_here = match &*window_data(window).placement.borrow() {
            Placement::Output(name) => *name == output.name(),
            Placement::Span => spans_paced_here,
        };
        if !paced_here {
            continue;
        }
        window.take_presentation_feedback(
            &mut feedback,
            |_, _| Some(output.clone()),
            |surface, _| surface_presentation_feedback_flags_from_states(surface, None, states),
        );
    }
    let layers = layer_map_for_output(output);
    for layer in layers.layers() {
        layer.take_presentation_feedback(
            &mut feedback,
            |_, _| Some(output.clone()),
            |surface, _| surface_presentation_feedback_flags_from_states(surface, None, states),
        );
    }
    feedback
}
