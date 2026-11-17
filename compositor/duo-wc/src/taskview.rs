//! The task overview, live: what [`crate::overview`] decides, done to the
//! windows, and drawn.
//!
//! Open, it covers every lit panel below its status bar with that panel's
//! windows as cards (their own buffers, scaled down, so the pictures are
//! live), takes the panels' touches there (the bar and the shell's overlay
//! surfaces, the dock, still get theirs), and holds the keyboard focus off
//! the windows (the on-screen keyboard goes away). It closes on a tap on a
//! card (switching to it), a card dropped on a half of its panel (a split,
//! [`crate::tiling`]), a tap on the background, Escape, a new window, the
//! screen going off, or `duoctl overview close`.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::element::memory::{MemoryRenderBuffer, MemoryRenderBufferRenderElement};
use smithay::backend::renderer::element::solid::SolidColorRenderElement;
use smithay::backend::renderer::element::surface::WaylandSurfaceRenderElement;
use smithay::backend::renderer::element::utils::{CropRenderElement, RescaleRenderElement};
use smithay::backend::renderer::element::{AsRenderElements, Id, Kind};
use smithay::backend::renderer::gles::GlesRenderer;
use smithay::backend::renderer::utils::CommitCounter;
use smithay::desktop::{layer_map_for_output, Window};
use smithay::output::Output;
use smithay::utils::{Logical, Physical, Point, Rectangle, Scale, Size, Transform};
use smithay::wayland::shell::wlr_layer::{Anchor, ExclusiveZone};
use tracing::{debug, info};

use crate::icons::Icon;
use crate::overview::{self, Card, Dir, Drag, Hit, Rect, Release};
use crate::render::Element;
use crate::state::{window_data, DuoWc, Placement, OSK_NAMESPACES};
use smithay::reexports::calloop::RegistrationToken;

/// A window asked to close and still there this long, having drawn nothing
/// since: it does not answer (its card offers to force it closed).
const STUCK_AFTER: Duration = Duration::from_millis(2500);
/// A window asked to close that drew something since and is still there
/// this long after the request: it answered with a question ("Save
/// changes?"): it is brought to the front.
const ANSWER_AFTER: Duration = Duration::from_millis(400);
/// After SIGTERM, how long a forced window has before SIGKILL.
const KILL_AFTER: Duration = Duration::from_secs(3);

/// A window asked to close (xdg_toplevel.close): a request, not a verdict.
/// Its card stays (dimmed, "Closing...") until the window really goes.
#[derive(Debug, Clone, Copy)]
pub struct Closing {
    /// When it was asked.
    pub at: Instant,
    /// It drew something since (an answer: a dialog in the window).
    pub answered: bool,
}

/// The overview's background: the compositor's own slate, so it reads as
/// "not an app".
const BACKGROUND: [f32; 4] = crate::backend::BACKGROUND;
/// Behind a window's picture until it has drawn.
const PLACEHOLDER: [f32; 4] = [0.09, 0.098, 0.12, 1.0];
/// The frame around each panel's front window (the one accent colour).
const ACCENT: [f32; 4] = crate::fx::ACCENT;
/// The half a carried card would go to (the accent, see-through;
/// premultiplied).
const FRAME: f64 = 3.0;
/// The cards' corner radius (the buttons are placed by it).
const CARD_RADIUS: f64 = overview::CARD_RADIUS;

/// A finger on the overview.
#[derive(Debug, Clone)]
pub struct CardTouch {
    /// The panel it went down on (whose grid the card is in).
    pub output: String,
    /// Where it went down (global).
    pub start: (f64, f64),
    /// Where it is now (global).
    pub at: (f64, f64),
    /// The panel it is on now (the other one after crossing the hinge).
    pub finger_output: String,
    /// What it went down on.
    pub hit: Hit,
    /// What it is doing.
    pub drag: Drag,
    /// Its hold timer's generation (picking the card up).
    pub gen: u64,
    /// The card it went down on (the home panel's frame), and where the
    /// finger holds it (from the card's top left).
    pub card: Option<(Card, (f64, f64))>,
    /// Let go at the hinge: where and when (its [`CardLanding`]).
    pub lifted: Option<crate::stitch::Contact>,
    /// The panel the finger has just come to across the hinge
    /// ([`crate::snap::Context::arrived`]).
    pub arrived: Option<String>,
    /// The carried card's picture (global), on springs: under the finger,
    /// or across the hinge ahead of it when the other panel is the target.
    pub ghost: Option<crate::spring::RectSpring>,
}

impl CardTouch {
    fn delta(&self) -> (f64, f64) {
        (self.at.0 - self.start.0, self.at.1 - self.start.1)
    }
}

/// A card let go at the hinge: dropped at once (it flies from where it was
/// let go to its place in the grid it went to), but for the stitch window
/// ([`crate::stitch::Config::window_ms`]) a finger coming down at the other
/// panel's hinge edge picks it up again ([`crate::stitch::Lift::DownOpen`],
/// as a carried window's [`crate::windrag::Landing`]). Until 2026-11-12 the
/// drop waited that long instead (360 ms from the lift to the card moving).
pub struct CardLanding {
    /// The finger as it lifted (its card, its grip, its picture's spring
    /// now flying to the card's new place).
    pub touch: CardTouch,
    timer: Option<RegistrationToken>,
    gen: u64,
}

/// How long a landed card's flight is drawn (it must be at rest by then;
/// at least the stitch window, while it may be picked up again).
const LANDING_MS: u64 = 650;

/// The overview's state.
#[derive(Default)]
pub struct TaskView {
    /// Shown now.
    pub open: bool,
    /// Fingers on it, by seat slot.
    pub touches: HashMap<u32, CardTouch>,
    /// A card let go at the hinge a moment ago.
    pub landing: Option<CardLanding>,
    /// The landings' generation counter.
    landing_gen: u64,
    /// Windows asked to close, by id.
    pub closing: HashMap<u64, Closing>,
    /// The hold timers' generation counter.
    lift_gen: u64,
    /// When it opened, for the cards' flight to their places.
    opened_at: Option<Instant>,
    /// What it draws with.
    pub gfx: Gfx,
}

/// Ids for the solid rectangles (so damage tracking sees the same element
/// from frame to frame) and the icon images, made once.
#[derive(Default)]
pub struct Gfx {
    solids: HashMap<String, Id>,
    icons: HashMap<(Icon, i32), MemoryRenderBuffer>,
    /// Rounded boxes (the drop preview).
    pub fx: crate::fx::Gfx,
}

impl Gfx {
    fn solid(&mut self, key: String, rect: Rectangle<i32, Physical>, color: [f32; 4]) -> Element {
        let id = self.solids.entry(key).or_insert_with(Id::new).clone();
        Element::Solid(SolidColorRenderElement::new(
            id,
            rect,
            CommitCounter::default(),
            color,
            Kind::Unspecified,
        ))
    }

    fn icon(&mut self, renderer: &mut GlesRenderer, icon: Icon, rect: Rect, scale: f64) -> Option<Element> {
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
            None,
            Some(Rectangle::from_size((px as f64, px as f64).into())),
            Some(Size::from((rect.w.round() as i32, rect.h.round() as i32))),
            Kind::Unspecified,
        )
        .ok()
        .map(Element::Icon)
    }
}

/// One card as it is drawn this frame.
pub struct PlannedCard {
    window: Window,
    card: Card,
    /// The drag offset applied to it.
    offset: (f64, f64),
    alpha: f32,
    move_dir: Option<Dir>,
    front: bool,
    dragged: bool,
    lifted: bool,
    /// In its place (the opening flight is over): its buttons and frame show.
    settled: bool,
    /// Asked to close; and if it did not answer.
    closing: bool,
    stuck: bool,
}

/// One panel's overview this frame, in the panel's logical frame.
pub struct Plan {
    /// The status bar's height: the overview covers everything below it.
    top: f64,
    panel: (f64, f64),
    cards: Vec<PlannedCard>,
    /// Where a carried card would go (panel frame; this panel's part of it).
    preview: Option<Rect>,
    /// The span pad while a card is carried (panel frame), and whether it
    /// is the target.
    pad: Option<(Rect, bool)>,
}

fn phys(r: Rect, scale: f64) -> Rectangle<i32, Physical> {
    let x0 = (r.x * scale).round() as i32;
    let y0 = (r.y * scale).round() as i32;
    let x1 = ((r.x + r.w) * scale).round() as i32;
    let y1 = ((r.y + r.h) * scale).round() as i32;
    Rectangle::new((x0, y0).into(), (x1 - x0, y1 - y0).into())
}

/// The elements of a panel's overview, front to back.
pub fn elements(renderer: &mut GlesRenderer, gfx: &mut Gfx, plan: &Plan, output: &Output) -> Vec<Element> {
    let scale = output.current_scale().fractional_scale();
    let name = output.name();
    let mut out = Vec::new();

    // The dragged card in front of everything.
    let mut order: Vec<&PlannedCard> = plan.cards.iter().collect();
    order.sort_by_key(|c| !c.dragged);

    // Where the carried card's elements end (it is drawn first).
    let mut dragged_elements: Option<usize> = None;
    for planned in order {
        if !planned.dragged && dragged_elements.is_none() {
            dragged_elements = Some(out.len());
        }
        let id = window_data(&planned.window).id;
        let (dx, dy) = planned.offset;
        let thumb = planned.card.thumb.offset(dx, dy);
        if planned.stuck && planned.settled {
            if let Some(e) =
                gfx.icon(renderer, Icon::Force, planned.card.force_button().offset(dx, dy), scale)
            {
                out.push(e);
            }
        }
        if planned.alpha >= 0.99 && planned.settled && !planned.closing {
            if let Some(e) = gfx.icon(renderer, Icon::Close, planned.card.close.offset(dx, dy), scale) {
                out.push(e);
            }
            if let (Some(button), Some(dir)) = (planned.card.move_to, planned.move_dir) {
                if let Some(e) = gfx.icon(renderer, Icon::Arrow(dir), button.offset(dx, dy), scale) {
                    out.push(e);
                }
            }
        }

        // The frame of the panel's front window (and of a card picked up):
        // a rounded accent ring around the picture.
        if planned.settled && ((planned.front && !planned.dragged) || planned.lifted) {
            let f = FRAME;
            let ring = Rect::new(
                thumb.x - f - 1.0,
                thumb.y - f - 1.0,
                thumb.w + 2.0 * f + 2.0,
                thumb.h + 2.0 * f + 2.0,
            );
            out.push(gfx.fx.rounded(
                renderer,
                &format!("{name}/frame/{id}"),
                crate::fx::RoundedBox {
                    rect: ring,
                    radius: CARD_RADIUS + f + 1.0,
                    fill: [0.0; 4],
                    edge: ACCENT,
                    border: f,
                    soft: 0.0,
                    invert: false,
                },
                scale,
            ));
        }
        // Rounded corners: the overview's background over the picture's
        // corners (cheaper than a clipping shader; not for a carried card,
        // which may be over other cards).
        if !planned.dragged {
            out.push(gfx.fx.rounded(
                renderer,
                &format!("{name}/corners/{id}"),
                crate::fx::RoundedBox {
                    rect: thumb,
                    radius: CARD_RADIUS,
                    fill: BACKGROUND,
                    edge: [0.0; 4],
                    border: 0.0,
                    soft: 0.0,
                    invert: true,
                },
                scale,
            ));
        }

        // The window's own surfaces, scaled into the card and cut at its edge.
        let thumb_px = phys(thumb, scale);
        let geometry = planned.window.geometry();
        let origin = thumb_px.loc;
        let location = origin - geometry.loc.to_physical_precise_round(scale);
        let surfaces: Vec<WaylandSurfaceRenderElement<GlesRenderer>> =
            AsRenderElements::<GlesRenderer>::render_elements(
                &planned.window,
                renderer,
                location,
                Scale::from(scale),
                planned.alpha,
            );
        for surface in surfaces {
            let scaled = RescaleRenderElement::from_element(surface, origin, planned.card.scale);
            if let Some(cropped) = CropRenderElement::from_element(scaled, scale, thumb_px) {
                out.push(Element::Thumb(cropped));
            }
        }
        if planned.alpha >= 0.99 {
            out.push(gfx.solid(format!("{name}/thumb/{id}"), thumb_px, PLACEHOLDER));
        }
    }

    if let Some((pad, hot)) = plan.pad {
        // Behind the carried card and the preview, over the other cards.
        let at = dragged_elements.unwrap_or(out.len());
        let pad_elements = crate::fx::span_pad_elements(
            renderer,
            &mut gfx.fx,
            &format!("{name}/ov"),
            pad,
            hot,
            plan.panel,
            scale,
        );
        for (i, e) in pad_elements.into_iter().enumerate() {
            out.insert(at + i, e);
        }
    }
    if let Some(p) = plan.preview {
        // Behind the carried card (it is first), over the others.
        let at = dragged_elements.unwrap_or(out.len());
        let accent = crate::fx::ACCENT;
        let e = gfx.fx.rounded(
            renderer,
            &format!("{name}/preview"),
            crate::fx::RoundedBox {
                rect: p.inset(4.0),
                radius: 16.0,
                fill: crate::fx::premul(accent, 0.20),
                edge: crate::fx::premul(accent, 0.85),
                border: 2.5,
                soft: 0.0,
                invert: false,
            },
            scale,
        );
        out.insert(at, e);
    }

    // Everything below the status bar.
    let (w, h) = plan.panel;
    let background = Rect::new(0.0, plan.top, w, h - plan.top);
    out.push(gfx.solid(format!("{name}/background"), phys(background, scale), BACKGROUND));
    out
}

impl DuoWc {
    /// The panels the overview is shown on: the lit ones the pose uses, left
    /// to right.
    pub fn overview_panels(&self) -> Vec<Output> {
        self.layout_order()
            .into_iter()
            .filter(|o| self.pose.in_use(&o.name()) && self.is_lit(&o.name()))
            // Laptop mode's keyboard panel shows its keys, nothing else.
            .filter(|o| !self.is_laptop_keys(&o.name()))
            .collect()
    }

    /// Whether the cards are still flying to their places (every frame is
    /// drawn until they are).
    pub fn overview_animating(&self) -> bool {
        let now = Instant::now();
        self.taskview.open
            && (self
                .taskview
                .opened_at
                .map(|at| at.elapsed() < Duration::from_millis(overview::OPEN_MS + 20))
                .unwrap_or(false)
                || self
                    .taskview
                    .touches
                    .values()
                    .chain(self.taskview.landing.as_ref().map(|l| &l.touch))
                    .any(|t| t.ghost.map(|g| !g.done(now)).unwrap_or(false)))
    }

    /// Whether the overview is shown on this panel.
    pub fn overview_on(&self, output: &Output) -> bool {
        self.taskview.open && !self.lock.locked && self.overview_panels().contains(output)
    }

    /// The status bar's height on a panel: the largest exclusive zone of a
    /// layer surface anchored to its top edge (not the on-screen keyboard).
    pub fn top_inset(&self, output: &Output) -> f64 {
        let map = layer_map_for_output(output);
        map.layers()
            .filter(|l| !OSK_NAMESPACES.contains(&l.namespace()))
            .filter_map(|l| {
                let cached = l.cached_state();
                let anchor = cached.anchor;
                match cached.exclusive_zone {
                    ExclusiveZone::Exclusive(z)
                        if anchor.contains(Anchor::TOP) && !anchor.contains(Anchor::BOTTOM) =>
                    {
                        Some(z as f64)
                    }
                    _ => None,
                }
            })
            .fold(0.0, f64::max)
    }

    /// The other panel the overview is on, seen from this one.
    fn overview_other(&self, output: &Output) -> Option<Output> {
        self.overview_panels().into_iter().find(|o| o != output)
    }

    fn overview_toward(&self, output: &Output) -> Option<Dir> {
        let other = self.overview_other(output)?;
        let a = self.space.output_geometry(output)?;
        let b = self.space.output_geometry(&other)?;
        let rect = |g: Rectangle<i32, Logical>| {
            Rect::new(g.loc.x as f64, g.loc.y as f64, g.size.w as f64, g.size.h as f64)
        };
        Some(overview::direction(rect(a), rect(b)))
    }

    /// The windows a panel's overview shows, front first: its own, and on
    /// the first lit panel also every spanning window and every window whose
    /// panel is dark or not in use.
    fn overview_windows(&self, output: &Output) -> Vec<Window> {
        let panels = self.overview_panels();
        let first = panels.first() == Some(output);
        // With two panels the spanning windows have their own row across
        // both (overview_span_windows).
        let span_row = panels.len() >= 2;
        self.space
            .elements()
            .rev()
            .filter(|w| !self.is_accessory(w))
            .filter(|w| match &*window_data(w).placement.borrow() {
                Placement::Output(name) => {
                    *name == output.name() || (first && !panels.iter().any(|p| p.name() == *name))
                }
                Placement::Span => first && !span_row,
            })
            .cloned()
            .collect()
    }

    /// The windows that span both panels, front first, when the overview
    /// is on two panels (their cards go in a row across both).
    fn overview_span_windows(&self) -> Vec<Window> {
        if self.overview_panels().len() < 2 {
            return Vec::new();
        }
        self.space
            .elements()
            .rev()
            .filter(|w| !self.is_accessory(w) && *window_data(w).placement.borrow() == Placement::Span)
            .cloned()
            .collect()
    }

    fn card_size(&self, w: &Window, fallback: (f64, f64)) -> (u64, f64, f64) {
        let g = w.geometry();
        let (gw, gh) =
            if g.size.w > 0 && g.size.h > 0 { (g.size.w as f64, g.size.h as f64) } else { fallback };
        (window_data(w).id, gw, gh)
    }

    /// The cards of a panel's overview, and the windows they show.
    fn overview_cards(&self, output: &Output) -> (Rect, Vec<(Window, Card)>) {
        let Some(geometry) = self.space.output_geometry(output) else {
            return (Rect::new(0.0, 0.0, 0.0, 0.0), Vec::new());
        };
        let mut area = overview::area(geometry.size.w as f64, geometry.size.h as f64, self.top_inset(output));
        let panel_size = (geometry.size.w as f64, geometry.size.h as f64);
        let origin = (geometry.loc.x as f64, geometry.loc.y as f64);
        // The canvas: spanning windows in a row across both panels, over the
        // hinge; this panel's own windows below.
        let spanning = self.overview_span_windows();
        let mut span_cards: Vec<(Window, Card)> = Vec::new();
        if !spanning.is_empty() {
            let panels = self.overview_panels();
            let areas: Vec<Rect> = panels
                .iter()
                .filter_map(|p| {
                    let g = self.space.output_geometry(p)?;
                    let a = overview::area(g.size.w as f64, g.size.h as f64, self.top_inset(p));
                    Some(a.offset(g.loc.x as f64, g.loc.y as f64))
                })
                .collect();
            let (row, rest) = overview::split_for_span(&areas);
            if let Some(i) = panels.iter().position(|p| p == output) {
                if let Some(mine) = rest.get(i) {
                    area = mine.offset(-origin.0, -origin.1);
                }
            }
            let sizes: Vec<(u64, f64, f64)> =
                spanning.iter().map(|w| self.card_size(w, panel_size)).collect();
            for (window, mut card) in spanning.into_iter().zip(overview::layout(row, &sizes, false)) {
                card.thumb = card.thumb.offset(-origin.0, -origin.1);
                card.label = card.label.offset(-origin.0, -origin.1);
                card.close = card.close.offset(-origin.0, -origin.1);
                span_cards.push((window, card));
            }
        }
        let windows = self.overview_windows(output);
        let sizes: Vec<(u64, f64, f64)> = windows.iter().map(|w| self.card_size(w, panel_size)).collect();
        let movable = self.overview_other(output).is_some();
        let mut cards = overview::layout(area, &sizes, movable);
        // A spanning window has no "other panel" to go to; one that did not
        // answer its close offers to be forced.
        for (card, window) in cards.iter_mut().zip(&windows) {
            if *window_data(window).placement.borrow() == Placement::Span {
                card.move_to = None;
            }
            if self.close_stuck(window_data(window).id) {
                card.force = Some(card.force_button());
            }
        }
        for (window, card) in span_cards.iter_mut() {
            if self.close_stuck(window_data(window).id) {
                card.force = Some(card.force_button());
            }
        }
        let mut all: Vec<(Window, Card)> = span_cards;
        all.extend(windows.into_iter().zip(cards));
        (area, all)
    }

    /// The snap panels for a card carried in the overview: the zones are
    /// the overview's areas (what the preview covers), the halves are
    /// offered where another window is there to share with.
    fn overview_snap_panels(&self, carried: u64) -> Vec<crate::snap::Panel> {
        self.snap_panels(carried)
            .into_iter()
            .map(|mut p| {
                if let Some(o) = self.output_by_name(&p.name) {
                    let a = overview::area(p.rect.w, p.rect.h, self.top_inset(&o));
                    p.zone = a.offset(p.rect.x, p.rect.y);
                }
                p
            })
            .collect()
    }

    /// Where a carried card would go now, and that target's area (global).
    fn overview_target(&self, touch: &CardTouch) -> Option<(crate::snap::Target, Rect)> {
        let Hit::Card(id) = touch.hit else {
            return None;
        };
        let panels = self.overview_snap_panels(id);
        let ctx = crate::snap::Context {
            allow_top: touch.drag == Drag::Lifted,
            arrived: touch.arrived.as_ref().and_then(|n| panels.iter().position(|p| p.name == *n)),
        };
        let target = crate::snap::target(&panels, touch.at.0, touch.at.1, ctx, &self.carry.snap)?;
        let span = (panels.len() >= 2).then(|| {
            let x0 = panels.iter().map(|p| p.zone.x).fold(f64::INFINITY, f64::min);
            let y0 = panels.iter().map(|p| p.zone.y).fold(f64::INFINITY, f64::min);
            let x1 = panels.iter().map(|p| p.zone.x + p.zone.w).fold(f64::MIN, f64::max);
            let y1 = panels.iter().map(|p| p.zone.y + p.zone.h).fold(f64::MIN, f64::max);
            Rect::new(x0, y0, x1 - x0, y1 - y0)
        });
        if target == crate::snap::Target::Span && span.is_none() {
            return None;
        }
        let area = crate::snap::preview(&panels, &target, span)?;
        Some((target, area))
    }

    /// Where a carried card's picture belongs now (global): under the
    /// finger where it holds it, or, while the target is the other panel,
    /// across the hinge ahead of the finger (the finger cannot follow it
    /// there: the touchscreens are separate).
    fn carried_card_rect(&self, touch: &CardTouch) -> Option<Rect> {
        let (card, grip) = touch.card?;
        let lift = if touch.drag == Drag::Lifted { 1.04 } else { 1.0 };
        let under = Rect::new(
            touch.at.0 - grip.0 * lift,
            touch.at.1 - grip.1 * lift,
            card.thumb.w * lift,
            card.thumb.h * lift,
        );
        let moved = touch.delta().0.hypot(touch.delta().1) >= overview::TAP_SLOP;
        if let (true, Some((crate::snap::Target::Panel(name), _))) = (moved, self.overview_target(touch)) {
            if name != touch.finger_output {
                let rects: Vec<Rect> = self.overview_snap_panels(card.id).iter().map(|p| p.rect).collect();
                let names: Vec<String> =
                    self.overview_snap_panels(card.id).into_iter().map(|p| p.name).collect();
                if let Some(from) = names.iter().position(|n| *n == touch.finger_output) {
                    return Some(crate::snap::lead_across(&rects, from, under, crate::windrag::LEAD_MARGIN));
                }
            }
        }
        Some(under)
    }

    /// A card being swiped up on the lower of two stacked panels whose
    /// finger has come to the hinge: it goes on as a carry
    /// ([`overview::dismiss_goes_across`]; the reach is where a lift puts a
    /// carried card on the other panel).
    fn overview_dismiss_goes_across(&self, touch: &CardTouch) -> bool {
        if touch.drag != Drag::Dismiss || touch.finger_output != touch.output {
            return false;
        }
        let Some(output) = self.output_by_name(&touch.output) else {
            return false;
        };
        let Some(g) = self.space.output_geometry(&output) else {
            return false;
        };
        let reach = self.carry.stitch.edge_band.max(self.carry.snap.hinge_band);
        let from_top = touch.at.1 - g.loc.y as f64;
        overview::dismiss_goes_across(touch.drag, self.overview_toward(&output), from_top, reach)
    }

    /// Where a card being swiped away is drawn now (global).
    fn dismissed_card_rect(&self, touch: &CardTouch) -> Option<Rect> {
        let (card, grip) = touch.card?;
        let dy = overview::dismiss_offset(touch.delta().1);
        Some(Rect::new(touch.start.0 - grip.0, touch.start.1 - grip.1 + dy, card.thumb.w, card.thumb.h))
    }

    /// Slide a carried card's picture to where it belongs now.
    fn retarget_card(&self, touch: &mut CardTouch) {
        if !touch.drag.carries() {
            return;
        }
        let Some(to) = self.carried_card_rect(touch) else {
            return;
        };
        let now = Instant::now();
        let t = (to.x, to.y, to.w, to.h);
        match touch.ghost.as_mut() {
            Some(g) => g.retarget(t, now),
            None => {
                touch.ghost = Some(crate::spring::RectSpring::new(t, t, now, crate::spring::Params::SNAPPY))
            }
        }
    }

    /// What a panel's overview draws this frame, if it is open there.
    pub fn overview_plan(&self, output: &Output) -> Option<Plan> {
        if !self.overview_on(output) {
            return None;
        }
        let geometry = self.space.output_geometry(output)?;
        let panel = (geometry.size.w as f64, geometry.size.h as f64);
        let (gx, gy) = (geometry.loc.x as f64, geometry.loc.y as f64);
        let (_, cards) = self.overview_cards(output);
        let toward = self.overview_toward(output);
        let front = self.topmost_on(output).map(|w| window_data(&w).id);
        let mut preview = None;
        // The opening flight: each card from its window's own place.
        let progress = self
            .taskview
            .opened_at
            .map(|at| at.elapsed().as_secs_f64() * 1000.0 / overview::OPEN_MS as f64)
            .unwrap_or(1.0);
        let settled = progress >= 1.0;
        let origin = geometry.loc;
        let mut planned: Vec<PlannedCard> = Vec::new();
        let landing = self.overview_landing_card();
        for (window, card) in cards {
            let id = window_data(&window).id;
            if landing == Some(id) {
                // Flying to its place from where it was let go (below).
                continue;
            }
            let touch = self
                .taskview
                .touches
                .values()
                .find(|t| t.output == output.name() && t.hit == Hit::Card(id) && t.drag != Drag::Pending);
            if touch.map(|t| t.drag.carries()).unwrap_or(false) {
                // Carried: drawn under the finger, wherever it is (below).
                continue;
            }
            let closing = self.taskview.closing.contains_key(&id);
            let stuck = self.close_stuck(id);
            let (offset, alpha, dragged) = match touch {
                Some(t) => {
                    let dy = overview::dismiss_offset(t.delta().1);
                    ((0.0, dy), overview::dismiss_alpha(dy, card.thumb.h), true)
                }
                None if closing => ((0.0, 0.0), 0.45, false),
                None => ((0.0, 0.0), 1.0, false),
            };
            let mut card = card;
            if !settled {
                let size = window.geometry().size;
                let from =
                    self.space.element_location(&window).filter(|_| size.w > 0 && size.h > 0).map(|loc| {
                        Rect::new(
                            (loc.x - origin.x) as f64,
                            (loc.y - origin.y) as f64,
                            size.w as f64,
                            size.h as f64,
                        )
                    });
                if let Some(from) = from {
                    card.thumb = overview::opening(from, card.thumb, progress);
                    card.scale = card.thumb.w / size.w as f64;
                }
            }
            planned.push(PlannedCard {
                window,
                card,
                offset,
                alpha,
                move_dir: toward,
                front: Some(id) == front,
                dragged,
                lifted: false,
                settled,
                closing,
                stuck,
            });
        }
        // Carried cards, on every panel they are over (one going across the
        // hinge shows on both while it slides, then on the other one).
        let now = Instant::now();
        let mut pad = None;
        let panel_rect = Rect::new(gx, gy, panel.0, panel.1);
        for touch in self.taskview.touches.values() {
            if !touch.drag.carries() {
                continue;
            }
            let (Hit::Card(id), Some((card, _))) = (touch.hit, touch.card) else {
                continue;
            };
            let Some(window) = self.window_by_id(id) else {
                continue;
            };
            let moved = touch.delta().0.hypot(touch.delta().1) >= overview::TAP_SLOP;
            if moved && pad.is_none() {
                let rects: Vec<Rect> = self.overview_snap_panels(id).iter().map(|p| p.rect).collect();
                let hot = self.overview_target(touch).map(|(t, _)| t) == Some(crate::snap::Target::Span);
                pad = crate::snap::span_pad(&rects, &self.carry.snap).map(|p| (p.offset(-gx, -gy), hot));
            }
            let rect = match touch.ghost {
                Some(g) => {
                    let (x, y, w, h) = g.value(now);
                    Rect::new(x, y, w, h)
                }
                None => match self.carried_card_rect(touch) {
                    Some(r) => r,
                    None => continue,
                },
            };
            if !rect.overlaps(panel_rect) {
                continue;
            }
            let mut card = card;
            let lift = rect.w / card.thumb.w.max(1.0);
            card.thumb = rect.offset(-gx, -gy);
            card.scale *= lift;
            planned.insert(
                0,
                PlannedCard {
                    window,
                    card,
                    offset: (0.0, 0.0),
                    alpha: 1.0,
                    move_dir: None,
                    front: false,
                    dragged: true,
                    lifted: touch.drag == Drag::Lifted,
                    settled: true,
                    closing: false,
                    stuck: false,
                },
            );
            if moved {
                if let Some((_, target)) = self.overview_target(touch) {
                    preview = Some(target);
                }
            }
        }
        // A card let go at the hinge, flying to its place: on every panel
        // it is over, in front.
        if let Some(l) = self.taskview.landing.as_ref() {
            let flying = match (l.touch.hit, l.touch.card, l.touch.ghost) {
                (Hit::Card(id), Some((card, _)), Some(g)) => self.window_by_id(id).map(|w| (w, card, g)),
                _ => None,
            };
            if let Some((window, card, g)) = flying {
                let (x, y, w, h) = g.value(now);
                let rect = Rect::new(x, y, w, h);
                let size = window.geometry().size;
                if rect.overlaps(panel_rect) && size.w > 0 {
                    let mut card = card;
                    card.thumb = rect.offset(-gx, -gy);
                    card.scale = rect.w / size.w as f64;
                    planned.insert(
                        0,
                        PlannedCard {
                            window,
                            card,
                            offset: (0.0, 0.0),
                            alpha: 1.0,
                            move_dir: None,
                            front: false,
                            dragged: true,
                            lifted: false,
                            settled: true,
                            closing: false,
                            stuck: false,
                        },
                    );
                }
            }
        }
        // A finger carrying a card on the OTHER panel may target this one.
        if preview.is_none() {
            for touch in self.taskview.touches.values() {
                if touch.drag.carries() && touch.delta().0.hypot(touch.delta().1) >= overview::TAP_SLOP {
                    if let Some((_, target)) = self.overview_target(touch) {
                        preview = Some(target);
                    }
                }
            }
        }
        // This panel's part of the target.
        let preview = preview.and_then(|p| {
            let x0 = p.x.max(gx);
            let y0 = p.y.max(gy);
            let x1 = (p.x + p.w).min(gx + panel.0);
            let y1 = (p.y + p.h).min(gy + panel.1);
            (x1 - x0 > 1.0 && y1 - y0 > 1.0).then(|| Rect::new(x0 - gx, y0 - gy, x1 - x0, y1 - y0))
        });
        Some(Plan { top: self.top_inset(output), panel, cards: planned, preview, pad })
    }

    /// Where the cards' labels go on every panel the overview is on, for
    /// the event stream (the shell draws them).
    pub fn overview_labels(&self) -> Vec<crate::events::CardLabel> {
        if !self.taskview.open || self.lock.locked {
            return Vec::new();
        }
        let mut out = Vec::new();
        for output in self.overview_panels() {
            let (_, cards) = self.overview_cards(&output);
            for (window, card) in cards {
                let id = window_data(&window).id;
                let dragged = self.overview_landing_card() == Some(id)
                    || self.taskview.touches.values().any(|t| {
                        t.output == output.name() && t.hit == Hit::Card(id) && t.drag != Drag::Pending
                    });
                let l = card.label;
                out.push(crate::events::CardLabel {
                    output: output.name(),
                    window: id,
                    x: l.x.round() as i32,
                    y: l.y.round() as i32,
                    w: l.w.round() as i32,
                    h: l.h.round() as i32,
                    dragged,
                    closing: self.taskview.closing.contains_key(&id),
                    stuck: self.close_stuck(id),
                });
            }
        }
        out
    }

    /// Open the overview on every lit panel.
    pub fn overview_open(&mut self, reason: &str) {
        if self.taskview.open || self.lock.locked || self.overview_panels().is_empty() {
            return;
        }
        info!(reason, "overview open");
        self.taskview.open = true;
        self.taskview.opened_at = Some(Instant::now());
        self.taskview.touches.clear();
        self.overview_landing_forget();
        self.events_dirty = true;
        self.cancel_all_touches();
        self.refresh_keyboard_focus();
        // The on-screen keyboard's zone no longer counts (window_zone).
        self.place_all();
        self.queue_redraw_all();
    }

    /// Close the overview.
    pub fn overview_close(&mut self, reason: &str) {
        if !self.taskview.open {
            return;
        }
        info!(reason, "overview closed");
        // Each panel's front window grows back out of its card (the
        // overview zooms back into the windows); a window that is about to
        // fly elsewhere (a switch, a drop) is given its own flight after.
        let mut flights: Vec<(Window, Rect)> = Vec::new();
        for output in self.overview_panels() {
            let Some(front) = self.topmost_on(&output) else {
                continue;
            };
            let Some(g) = self.space.output_geometry(&output) else {
                continue;
            };
            let (_, cards) = self.overview_cards(&output);
            if let Some((_, card)) = cards.iter().find(|(w, _)| *w == front) {
                if !flights.iter().any(|(w, _)| *w == front) {
                    flights.push((front.clone(), card.thumb.offset(g.loc.x as f64, g.loc.y as f64)));
                }
            }
        }
        self.taskview.open = false;
        self.taskview.touches.clear();
        self.overview_landing_forget();
        self.taskview.gfx.solids.clear();
        self.taskview.gfx.fx.forget("");
        self.events_dirty = true;
        self.refresh_keyboard_focus();
        self.place_all();
        for (window, from) in flights {
            self.animate_from(&window, from, 1.0, 1.0, crate::spring::Params::SNAPPY);
        }
        self.queue_redraw_all();
    }

    /// Whether a touch going down at `location` (global) belongs to the
    /// overview: the overview is open on that panel, and the point is below
    /// the status bar and not on an overlay surface (the dock, a banner).
    pub fn overview_takes(&self, output: &Output, location: Point<f64, Logical>) -> bool {
        if !self.overview_on(output) {
            return false;
        }
        let Some(geometry) = self.space.output_geometry(output) else {
            return false;
        };
        let local = location - geometry.loc.to_f64();
        if local.y < self.top_inset(output) {
            return false;
        }
        // An overlay surface that takes input there (the dock, a banner);
        // one with an empty input region (the shell's card labels) does not.
        !crate::state::overlay_takes_input(output, local)
    }

    /// A finger went down on the overview.
    pub fn overview_touch_down(&mut self, slot: u32, output: &Output, location: Point<f64, Logical>) {
        let Some(geometry) = self.space.output_geometry(output) else {
            return;
        };
        let local = location - geometry.loc.to_f64();
        let (_, cards) = self.overview_cards(output);
        let cards: Vec<Card> = cards.into_iter().map(|(_, c)| c).collect();
        let hit = overview::hit(&cards, local.x, local.y);
        let card = match hit {
            Hit::Card(id) => {
                cards.iter().find(|c| c.id == id).map(|c| (*c, (local.x - c.thumb.x, local.y - c.thumb.y)))
            }
            _ => None,
        };
        debug!(output = output.name(), slot, ?hit, "overview touch");
        self.note_activity();
        self.taskview.lift_gen += 1;
        let gen = self.taskview.lift_gen;
        self.taskview.touches.insert(
            slot,
            CardTouch {
                output: output.name(),
                start: (location.x, location.y),
                at: (location.x, location.y),
                finger_output: output.name(),
                hit,
                drag: Drag::Pending,
                gen,
                card,
                lifted: None,
                arrived: None,
                ghost: None,
            },
        );
        if matches!(hit, Hit::Card(_)) {
            // Held still long enough, the card is picked up.
            let _ = self.loop_handle.insert_source(
                smithay::reexports::calloop::timer::Timer::from_duration(Duration::from_millis(
                    overview::LIFT_MS,
                )),
                move |_, _, state: &mut DuoWc| {
                    if let Some(touch) = state.taskview.touches.get_mut(&slot) {
                        if touch.gen == gen && touch.drag == Drag::Pending {
                            debug!(slot, "overview: card picked up");
                            touch.drag = Drag::Lifted;
                            state.events_dirty = true;
                            state.queue_redraw_all();
                        }
                    }
                    smithay::reexports::calloop::timer::TimeoutAction::Drop
                },
            );
        }
    }

    /// A finger on the overview moved; true if it was one.
    pub fn overview_touch_motion(&mut self, slot: u32, location: Point<f64, Logical>) -> bool {
        let Some(touch) = self.taskview.touches.get(&slot).cloned() else {
            return false;
        };
        if touch.lifted.is_some() {
            return true;
        }
        self.note_activity();
        let mut touch = touch;
        touch.at = (location.x, location.y);
        if touch.drag == Drag::Pending && matches!(touch.hit, Hit::Card(_)) {
            let (dx, dy) = touch.delta();
            touch.drag = overview::classify(dx, dy);
            if touch.drag != Drag::Pending {
                self.events_dirty = true;
            }
        }
        if self.overview_dismiss_goes_across(&touch) {
            // The lower of two stacked panels: swiped up to the hinge, the
            // card goes on as a carry (to the other panel), from where the
            // swipe has drawn it.
            info!(output = touch.output, "overview: a card swiped up to the hinge: carried across");
            touch.drag = Drag::Carry;
            if let Some(from) = self.dismissed_card_rect(&touch) {
                let t = (from.x, from.y, from.w, from.h);
                touch.ghost =
                    Some(crate::spring::RectSpring::new(t, t, Instant::now(), crate::spring::Params::SNAPPY));
            }
            self.events_dirty = true;
        }
        let moving = touch.drag != Drag::Pending;
        if touch.arrived.is_some() {
            let rects: Vec<Rect> = self.snap_panels(u64::MAX).iter().map(|p| p.rect).collect();
            if !crate::snap::in_hinge_band(&rects, touch.at.0, touch.at.1, &self.carry.snap) {
                touch.arrived = None;
            }
        }
        self.retarget_card(&mut touch);
        self.taskview.touches.insert(slot, touch);
        if moving {
            self.queue_redraw_all();
        }
        true
    }

    /// A finger came down on `output` at `location`: if it continues a card
    /// carried across the hinge, it takes the card over (true).
    pub fn overview_stitch(
        &mut self,
        slot: u32,
        output: &str,
        location: Point<f64, Logical>,
        time: u32,
    ) -> bool {
        if !self.taskview.open {
            return false;
        }
        let panels = self.snap_panels(u64::MAX);
        let rects: Vec<Rect> = panels.iter().map(|p| p.rect).collect();
        let Some(hinge) = crate::snap::hinge(&rects) else {
            return false;
        };
        let index_of = |name: &str| panels.iter().position(|p| p.name == name);
        let Some(down_panel) = index_of(output) else {
            return false;
        };
        let down = crate::stitch::Contact { panel: down_panel, at: (location.x, location.y), time };
        let cfg = self.carry.stitch;
        let found = self.taskview.touches.iter().find_map(|(old, t)| {
            if !t.drag.carries() {
                return None;
            }
            let (contact, lifted) = match t.lifted {
                Some(c) => (c, true),
                None => {
                    (crate::stitch::Contact { panel: index_of(&t.finger_output)?, at: t.at, time }, false)
                }
            };
            crate::stitch::continues(&hinge, &rects, &contact, lifted, &down, &cfg).then_some(*old)
        });
        let Some(old) = found else {
            return self.overview_landing_pick_up(slot, output, location, &hinge, &rects, &down);
        };
        let Some(mut touch) = self.taskview.touches.remove(&old) else {
            return false;
        };
        info!(from = touch.finger_output, to = output, "overview: card carried across the hinge");
        if touch.lifted.is_none() {
            self.carry.ignored.insert(old);
        }
        touch.lifted = None;
        touch.gen = touch.gen.wrapping_add(1_000_000);
        touch.finger_output = output.to_owned();
        touch.at = (location.x, location.y);
        touch.arrived = Some(output.to_owned());
        self.retarget_card(&mut touch);
        self.taskview.touches.insert(slot, touch);
        self.queue_redraw_all();
        true
    }

    /// A finger left the overview; true if it was one. A card let go at the
    /// hinge is dropped at once and stays open for the other side a moment
    /// ([`CardLanding`]).
    pub fn overview_touch_up(&mut self, slot: u32, time: u32) -> bool {
        let Some(mut touch) = self.taskview.touches.remove(&slot) else {
            return false;
        };
        // The same rule as a carried window's lift (crate::stitch::lift):
        // every lift drops; one at the hinge stays open for the other side.
        let lift = if touch.drag.carries() {
            let on_pad = self.overview_target(&touch).map(|(t, _)| t) == Some(crate::snap::Target::Span);
            let panels = self.snap_panels(u64::MAX);
            let rects: Vec<Rect> = panels.iter().map(|p| p.rect).collect();
            let index = panels.iter().position(|p| p.name == touch.finger_output);
            let hinge = crate::snap::hinge(&rects);
            crate::stitch::lift(hinge.as_ref(), &rects, index, touch.at, time, on_pad, &self.carry.stitch)
        } else {
            crate::stitch::Lift::Down
        };
        match lift {
            crate::stitch::Lift::Down => self.overview_release(touch),
            crate::stitch::Lift::DownOpen(contact) => {
                debug!(slot, "overview: card let go at the hinge: dropped, open for the other side");
                touch.lifted = Some(contact);
                self.overview_release(touch.clone());
                self.overview_landing_open(touch);
            }
        }
        true
    }

    /// The card let go at the hinge, if one is flying to its place.
    fn overview_landing_card(&self) -> Option<u64> {
        match self.taskview.landing.as_ref()?.touch.hit {
            Hit::Card(id) => Some(id),
            _ => None,
        }
    }

    /// Where a window's card is in its grid now (global), on whichever panel
    /// the overview shows it.
    fn overview_card_rect(&self, id: u64) -> Option<(String, Card, Rect)> {
        self.overview_panels().into_iter().find_map(|output| {
            let g = self.space.output_geometry(&output)?;
            let (_, cards) = self.overview_cards(&output);
            cards
                .into_iter()
                .find(|(w, _)| window_data(w).id == id)
                .map(|(_, card)| (output.name(), card, card.thumb.offset(g.loc.x as f64, g.loc.y as f64)))
        })
    }

    /// A card let go at the hinge (already dropped): its picture flies from
    /// where it was to its place in the grid it is in now, and for the
    /// stitch window a finger at the other panel's hinge edge may pick it
    /// up again ([`Self::overview_landing_pick_up`]).
    fn overview_landing_open(&mut self, mut touch: CardTouch) {
        self.overview_landing_forget();
        let Hit::Card(id) = touch.hit else {
            return;
        };
        if !self.taskview.open || self.window_by_id(id).is_none() {
            return;
        }
        let now = Instant::now();
        let Some((_, _, to)) = self.overview_card_rect(id) else {
            return;
        };
        let to = (to.x, to.y, to.w, to.h);
        match touch.ghost.as_mut() {
            Some(g) => g.retarget(to, now),
            None => {
                let from = self.carried_card_rect(&touch).map(|r| (r.x, r.y, r.w, r.h)).unwrap_or(to);
                touch.ghost =
                    Some(crate::spring::RectSpring::new(from, to, now, crate::spring::Params::SNAPPY));
            }
        }
        self.taskview.landing_gen += 1;
        let gen = self.taskview.landing_gen;
        let after = Duration::from_millis(LANDING_MS.max(self.carry.stitch.window_ms as u64 + 10));
        let timer = self
            .loop_handle
            .insert_source(
                smithay::reexports::calloop::timer::Timer::from_duration(after),
                move |_, _, state: &mut DuoWc| {
                    if state.taskview.landing.as_ref().map(|l| l.gen == gen) == Some(true) {
                        state.taskview.landing = None;
                        state.events_dirty = true;
                        state.queue_redraw_all();
                    }
                    smithay::reexports::calloop::timer::TimeoutAction::Drop
                },
            )
            .ok();
        self.taskview.landing = Some(CardLanding { touch, timer, gen });
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// No card let go at the hinge is flying or open for the other side.
    fn overview_landing_forget(&mut self) {
        if let Some(landing) = self.taskview.landing.take() {
            if let Some(timer) = landing.timer {
                self.loop_handle.remove(timer);
            }
        }
    }

    /// A finger came down at `location` on `output` (`down`): if it is at
    /// the other panel's hinge edge in time and in line with where a card
    /// was let go at the hinge, it picks that card up again from where its
    /// flight has got to (true). The card is in the grid it was dropped
    /// into; let go again, it goes where its new target says.
    fn overview_landing_pick_up(
        &mut self,
        slot: u32,
        output: &str,
        location: Point<f64, Logical>,
        hinge: &crate::snap::Hinge,
        rects: &[Rect],
        down: &crate::stitch::Contact,
    ) -> bool {
        let cfg = self.carry.stitch;
        let Some(lift) = self.taskview.landing.as_ref().and_then(|l| l.touch.lifted) else {
            return false;
        };
        if !crate::stitch::continues(hinge, rects, &lift, true, down, &cfg) {
            return false;
        }
        let Some(landing) = self.taskview.landing.take() else {
            return false;
        };
        if let Some(timer) = landing.timer {
            self.loop_handle.remove(timer);
        }
        let mut touch = landing.touch;
        let Hit::Card(id) = touch.hit else {
            return false;
        };
        // Its card is in the grid it went to: that panel is its home now.
        let Some((home, card, _)) = self.overview_card_rect(id) else {
            return false;
        };
        info!(id, to = output, "overview: card let go at the hinge picked up again");
        // Where the finger held it, on the card as it is in its new grid
        // (the grids may scale their cards differently).
        let grip = touch
            .card
            .map(|(_, (gx, gy))| (gx.min(card.thumb.w), gy.min(card.thumb.h)))
            .unwrap_or((card.thumb.w / 2.0, card.thumb.h / 2.0));
        touch.output = home;
        touch.card = Some((card, grip));
        touch.lifted = None;
        touch.gen = touch.gen.wrapping_add(1_000_000);
        touch.finger_output = output.to_owned();
        touch.at = (location.x, location.y);
        touch.arrived = Some(output.to_owned());
        self.retarget_card(&mut touch);
        self.taskview.touches.insert(slot, touch);
        self.events_dirty = true;
        self.queue_redraw_all();
        true
    }

    /// What a finger let go of on the overview does.
    fn overview_release(&mut self, touch: CardTouch) {
        self.events_dirty = true;
        let Some(output) = self.output_by_name(&touch.output) else {
            return;
        };
        let Some(geometry) = self.space.output_geometry(&output) else {
            return;
        };
        let (dx, dy) = touch.delta();
        let moved = dx.hypot(dy) >= overview::TAP_SLOP;
        let local = (touch.at.0 - geometry.loc.x as f64, touch.at.1 - geometry.loc.y as f64);
        let (_, cards) = self.overview_cards(&output);
        let find = |id: u64| cards.iter().find(|(w, _)| window_data(w).id == id).cloned();
        let now_hit = || overview::hit(&cards.iter().map(|(_, c)| *c).collect::<Vec<_>>(), local.0, local.1);
        match touch.hit {
            Hit::Empty => {
                if !moved {
                    self.overview_close("tap on the background");
                }
            }
            Hit::Close(id) => {
                if now_hit() == Hit::Close(id) && touch.finger_output == touch.output {
                    if let Some((window, _)) = find(id) {
                        self.close_window(&window);
                    }
                }
            }
            Hit::Force(id) => {
                if now_hit() == Hit::Force(id) && touch.finger_output == touch.output {
                    if let Some((window, _)) = find(id) {
                        self.ask_force_close(&window, &output);
                    }
                }
            }
            Hit::Move(id) => {
                if now_hit() == Hit::Move(id) && touch.finger_output == touch.output {
                    if let Some((window, _)) = find(id) {
                        self.overview_move_across(&window, &output);
                    }
                }
            }
            Hit::Card(id) => {
                let Some((window, card)) = find(id) else {
                    self.queue_redraw_all();
                    return;
                };
                let card_global = card.thumb.offset(geometry.loc.x as f64, geometry.loc.y as f64);
                match overview::release(touch.drag, dx, dy, card.thumb.h) {
                    Release::Activate => {
                        self.overview_switch_to(&window, &output);
                        // The window grows out of its card into its place.
                        self.animate_from(&window, card_global, 1.0, 1.0, crate::spring::Params::SNAPPY);
                    }
                    Release::Close => self.close_window(&window),
                    Release::Drop => {
                        let target = self.overview_target(&touch).map(|(t, _)| t);
                        let closes = matches!(
                            target,
                            Some(crate::snap::Target::Half(..)) | Some(crate::snap::Target::Span)
                        );
                        let lift = if touch.drag == Drag::Lifted { 1.04 } else { 1.0 };
                        let from = match touch.ghost {
                            Some(g) => {
                                let (x, y, w, h) = g.value(Instant::now());
                                Some(Rect::new(x, y, w, h))
                            }
                            None => touch.card.map(|(c, grip)| {
                                Rect::new(
                                    touch.at.0 - grip.0 * lift,
                                    touch.at.1 - grip.1 * lift,
                                    c.thumb.w * lift,
                                    c.thumb.h * lift,
                                )
                            }),
                        };
                        self.overview_drop(&window, target);
                        if let (true, Some(from)) = (closes, from) {
                            // Out of the overview: it flies from where it was let go.
                            self.animate_from(&window, from, 1.0, 1.0, crate::spring::Params::SNAPPY);
                        }
                    }
                    Release::SnapBack => {}
                }
            }
        }
        self.queue_redraw_all();
    }

    /// A card carried to a snap target and let go.
    fn overview_drop(&mut self, window: &Window, target: Option<crate::snap::Target>) {
        use crate::snap::Target;
        let id = window_data(window).id;
        let placement = window_data(window).placement.borrow().clone();
        let in_split = self.splits.iter().any(|s| s.has(id));
        info!(id, target = target.as_ref().map(|t| t.describe()), "overview: card dropped");
        match target {
            None => {}
            Some(Target::Panel(name)) => {
                if placement == Placement::Output(name.clone()) && !in_split {
                    return;
                }
                // Moved (or out of its split): the overview stays open, the
                // card shows up in that panel's grid.
                self.set_placement(window, Placement::Output(name));
                self.refresh_keyboard_focus();
            }
            Some(Target::Half(name, side)) => {
                let Some(output) = self.output_by_name(&name) else {
                    return;
                };
                let fallback =
                    self.space.elements().rev().find(|w| *w != window && self.is_on(w, &output)).cloned();
                self.overview_snap(window, &output, side, fallback);
            }
            Some(Target::Span) => {
                self.overview_close("card dropped on the hinge");
                self.apply_target(window, Some(&Target::Span));
            }
        }
    }

    /// Switch to a window from its card on `output`, and close the overview.
    fn overview_switch_to(&mut self, window: &Window, output: &Output) {
        let id = window_data(window).id;
        let placement = window_data(window).placement.borrow().clone();
        info!(id, output = output.name(), "overview: switch to the window");
        self.overview_close("switched to a window");
        // A window shown here because its own panel is dark comes here.
        if let Placement::Output(name) = &placement {
            if *name != output.name() && !self.is_lit(name) {
                self.set_placement(window, Placement::Output(output.name()));
            }
        }
        if self.is_lit(&output.name()) {
            self.active_output = Some(output.name());
        }
        self.activate_window(window);
    }

    /// Put a window on a half of `output` (dropped there from its card) and
    /// close the overview on the result.
    fn overview_snap(
        &mut self,
        window: &Window,
        output: &Output,
        side: crate::split::Side,
        fallback: Option<Window>,
    ) {
        info!(id = window_data(window).id, output = output.name(), side = side.name(), "overview: split");
        self.overview_close("split");
        if let Err(err) = self.snap_window(window, output, side, None, fallback) {
            tracing::warn!(?err, "overview: no split");
        }
    }

    /// Move a window from `output` to the other panel the overview is on.
    fn overview_move_across(&mut self, window: &Window, output: &Output) {
        let Some(other) = self.overview_other(output) else {
            return;
        };
        info!(id = window_data(window).id, from = output.name(), to = other.name(), "overview: window moved");
        self.set_placement(window, Placement::Output(other.name()));
        // set_placement gave it the keyboard; not while the overview is open.
        self.refresh_keyboard_focus();
    }

    /// Ask a window to close (xdg_toplevel.close). A request: its card
    /// stays, dimmed ("Closing..." from the shell), until the window really
    /// goes. An app that answers with a question (it draws something and
    /// stays, or opens a dialog) is brought to the front, out of the
    /// overview; one that does not answer at all gets a force-close button
    /// on its card after [`STUCK_AFTER`].
    pub fn close_window(&mut self, window: &Window) {
        let id = window_data(window).id;
        info!(id, "asking the window to close");
        self.taskview.closing.insert(id, Closing { at: Instant::now(), answered: false });
        if let Some(toplevel) = window.toplevel() {
            toplevel.send_close();
        }
        // An X11 window: WM_DELETE_WINDOW (crate::xwayland).
        DuoWc::x11_close(window);
        for (after, what) in [(ANSWER_AFTER, "answer"), (STUCK_AFTER, "stuck")] {
            let _ = self.loop_handle.insert_source(
                smithay::reexports::calloop::timer::Timer::from_duration(after),
                move |_, _, state: &mut DuoWc| {
                    state.events_dirty = true;
                    if what == "answer" {
                        state.close_check(id);
                    } else if state.taskview.closing.contains_key(&id) {
                        info!(id, "asked to close, no answer: it may be forced");
                    }
                    state.queue_redraw_all();
                    smithay::reexports::calloop::timer::TimeoutAction::Drop
                },
            );
        }
        self.events_dirty = true;
        self.queue_redraw_all();
    }

    /// A window asked to close drew something: an answer, if it stays.
    pub fn close_answered(&mut self, id: u64) {
        let Some(closing) = self.taskview.closing.get_mut(&id) else {
            return;
        };
        if closing.answered {
            return;
        }
        closing.answered = true;
        if closing.at.elapsed() >= ANSWER_AFTER {
            self.loop_handle.insert_idle(move |state| state.close_check(id));
        }
    }

    /// The window asked to close is still there and answered: it asks
    /// something; the overview leaves it to the user.
    pub fn close_check(&mut self, id: u64) {
        let answered = self.taskview.closing.get(&id).map(|c| c.answered).unwrap_or(false);
        if !answered {
            return;
        }
        let Some(window) = self.window_by_id(id) else {
            self.taskview.closing.remove(&id);
            return;
        };
        self.taskview.closing.remove(&id);
        info!(id, "the window answered its close: brought to the front");
        self.overview_close("the app answered the close");
        self.activate_window(&window);
    }

    /// Whether a window asked to close has not answered for a while.
    pub fn close_stuck(&self, id: u64) -> bool {
        self.taskview.closing.get(&id).map(|c| !c.answered && c.at.elapsed() >= STUCK_AFTER).unwrap_or(false)
    }

    /// The force-close button of a card was tapped: the shell asks the user
    /// (unsaved data is lost) and then calls `force_close_window`.
    fn ask_force_close(&mut self, window: &Window, output: &Output) {
        let (app_id, title) = crate::state::app_id_and_title(window);
        info!(id = window_data(window).id, "force close asked for");
        crate::ipc::broadcast(
            self,
            &serde_json::json!({
                "event": "force_close_request",
                "window": window_data(window).id,
                "app_id": app_id,
                "title": title,
                "output": output.name(),
            }),
        );
    }

    /// Kill the client a window belongs to: SIGTERM, and SIGKILL if it is
    /// still there after [`KILL_AFTER`]. The process is the Wayland
    /// client's, by its socket credentials; never duo-wc itself.
    pub fn force_close(&mut self, window: &Window) -> anyhow::Result<i32> {
        if window.is_x11() {
            // Every X11 window has Xwayland's socket credentials: the X
            // client's own pid instead (crate::xwayland).
            let pid = self
                .x11_pid(window)
                .ok_or_else(|| anyhow::anyhow!("no process known for this X11 window"))?;
            return Ok(self.kill_window_process(window, pid));
        }
        let surface = window
            .toplevel()
            .map(|t| t.wl_surface().clone())
            .ok_or_else(|| anyhow::anyhow!("not a toplevel"))?;
        use smithay::reexports::wayland_server::Resource as _;
        let client = surface.client().ok_or_else(|| anyhow::anyhow!("the window has no client"))?;
        let credentials = client
            .get_credentials(&self.display_handle)
            .map_err(|err| anyhow::anyhow!("no client credentials: {err:?}"))?;
        let pid = credentials.pid;
        if pid <= 1 || pid as u32 == std::process::id() || Some(pid) == self.xwl.pid {
            anyhow::bail!("refusing to kill pid {pid}");
        }
        Ok(self.kill_window_process(window, pid))
    }

    /// SIGTERM to a window's process, SIGKILL after [`KILL_AFTER`] if the
    /// window is still there.
    fn kill_window_process(&mut self, window: &Window, pid: i32) -> i32 {
        let id = window_data(window).id;
        info!(id, pid, "force close: SIGTERM");
        // SAFETY: kill(2) with a pid from the client's socket credentials.
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        let _ = self.loop_handle.insert_source(
            smithay::reexports::calloop::timer::Timer::from_duration(KILL_AFTER),
            move |_, _, state: &mut DuoWc| {
                if state.window_by_id(id).is_some() {
                    info!(id, pid, "force close: still there, SIGKILL");
                    // SAFETY: as above.
                    unsafe {
                        libc::kill(pid, libc::SIGKILL);
                    }
                }
                smithay::reexports::calloop::timer::TimeoutAction::Drop
            },
        );
        pid
    }
}
