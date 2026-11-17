//! The desktop on an external display: windows that float.
//!
//! On the phone's panels every window fills its panel (or half of one, or
//! both). An external display (a monitor or a TV on the USB-C port,
//! [`crate::extout`]) is a desk instead: each window there has a rectangle
//! of its own, which the pointer moves (by the title bar, or anywhere with
//! Super held) and resizes (by its border, or with Super and the right
//! button), and which the keyboard maximizes and halves (Super + arrows).
//!
//! This module is the geometry only: where a new window opens, how a drag
//! moves or resizes it, the snap at the display's edges, keeping it
//! reachable when the display changes. Rectangles are `(x, y, w, h)` in
//! logical px in the display's own frame (0, 0 is its top left corner),
//! `area` is the room windows get there (the display less its panels'
//! exclusive zones). Pure: no Smithay types.

use std::collections::HashMap;

use crate::layout::Rect;

/// The smallest a window is made by a resize (a client may ask for more).
pub const MIN_SIZE: (i32, i32) = (240, 160);

/// How much of a window's top strip (its title bar) stays on the display
/// whatever a drag does, so it can always be taken back.
pub const KEEP_VISIBLE: i32 = 64;

/// The title strip a window is held by in [`moved`] (logical px).
pub const TITLE_STRIP: i32 = 32;

/// How far apart new windows are cascaded.
pub const CASCADE: i32 = 32;

/// How much of laptop mode's desk panel (a small screen) a new window
/// takes, across and down (an external display's: 60 % x 70 %).
pub const PANEL_SHARE: (f64, f64) = (0.86, 0.86);

/// How close to a display edge the pointer lets a moved window go to snap
/// there (half on the left / right, maximized at the top).
pub const SNAP_BAND: f64 = 6.0;

/// Which edges a resize moves.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Edges {
    /// The top edge moves.
    pub top: bool,
    /// The bottom edge moves.
    pub bottom: bool,
    /// The left edge moves.
    pub left: bool,
    /// The right edge moves.
    pub right: bool,
}

impl Edges {
    /// The bottom right corner (a resize by Super + the right button in the
    /// middle of a window).
    pub const BOTTOM_RIGHT: Edges = Edges { top: false, bottom: true, left: false, right: true };

    /// From xdg_toplevel's resize_edge value (1 top, 2 bottom, 4 left, 8
    /// right, combined: 5 top left, 6 bottom left, 9 top right, 10 bottom
    /// right).
    pub fn from_xdg(bits: u32) -> Edges {
        Edges { top: bits & 1 != 0, bottom: bits & 2 != 0, left: bits & 4 != 0, right: bits & 8 != 0 }
    }

    /// Whether any edge moves.
    pub fn any(self) -> bool {
        self.top || self.bottom || self.left || self.right
    }
}

/// A snap at a display edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Snap {
    /// The left half.
    Left,
    /// The right half.
    Right,
    /// The whole room (maximized).
    Max,
}

/// A window on the desk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    /// Its own rectangle (what it goes back to when it is no longer
    /// maximized or snapped).
    pub rect: Rect,
    /// Maximized or snapped to a half: the rectangle then follows `area`.
    pub snap: Option<Snap>,
}

impl Frame {
    /// Where the window is now, in `area`.
    pub fn shown(&self, area: Rect) -> Rect {
        match self.snap {
            Some(snap) => snapped(area, snap),
            None => self.rect,
        }
    }
}

/// The rectangle of a snap.
pub fn snapped(area: Rect, snap: Snap) -> Rect {
    let (x, y, w, h) = area;
    match snap {
        Snap::Max => area,
        Snap::Left => (x, y, w / 2, h),
        Snap::Right => (x + w / 2, y, w - w / 2, h),
    }
}

/// Where a desk window's picture is, for its animations (growing in as it
/// opens, going home and back, a flight): `shown`, its frame as shown, at
/// the size it drew (`drawn`) once it has caught up with the size asked of
/// it (`caught_up`; a client may draw another size than asked, bigger up to
/// its minimum size), else at the size asked (it is on its way there; its
/// old picture is stretched into it until the new one comes). Until
/// 2026-11-15 the animations aimed at the panel's whole room (the phone's
/// rule), so a window opening on laptop mode's desk grew in over the whole
/// panel and jumped to its own size when the spring came to rest.
pub fn picture_rect(shown: Rect, drawn: (i32, i32), caught_up: bool) -> Rect {
    let (x, y, w, h) = shown;
    if caught_up && drawn.0 > 0 && drawn.1 > 0 {
        (x, y, drawn.0, drawn.1)
    } else {
        (x, y, w, h)
    }
}

/// Where a new window opens: the size it asked for (its first commit's
/// geometry, if it sets one), else 60 % x 70 % of the room, never more than
/// the room or less than [`MIN_SIZE`]; centred, each further window
/// [`CASCADE`] px down and right of the last (`nth`: how many windows the
/// desk has), wrapping before it would leave the room.
pub fn initial_rect(area: Rect, wanted: Option<(i32, i32)>, nth: usize) -> Rect {
    let (ax, ay, aw, ah) = area;
    let (w, h) = wanted
        .filter(|(w, h)| *w > 0 && *h > 0)
        .unwrap_or(((aw as f64 * 0.6) as i32, (ah as f64 * 0.7) as i32));
    let w = w.clamp(MIN_SIZE.0.min(aw), aw.max(1));
    let h = h.clamp(MIN_SIZE.1.min(ah), ah.max(1));
    let x0 = ax + (aw - w) / 2;
    let y0 = ay + (ah - h) / 2;
    // How many cascade steps fit below and right of the centred spot.
    let room = ((ax + aw - (x0 + w)).min(ay + ah - (y0 + h)) / CASCADE).max(0) as usize;
    let step = if room == 0 { 0 } else { (nth % (room + 1)) as i32 };
    (x0 + step * CASCADE, y0 + step * CASCADE, w, h)
}

/// A window moved by `(dx, dy)` from `start`: free, except that its title
/// strip stays reachable (at least [`KEEP_VISIBLE`] px of it on the display
/// across, its top never above the room's top or below its bottom).
pub fn moved(start: Rect, dx: f64, dy: f64, area: Rect) -> Rect {
    let (sx, sy, w, h) = start;
    let x = (sx as f64 + dx).round() as i32;
    let y = (sy as f64 + dy).round() as i32;
    (x, y, w, h).clamp_reachable(area)
}

/// A window resized from `start` by a drag of `(dx, dy)` on `edges`: the
/// opposite edges stay put, the size never below `min` (the client's
/// minimum, at least [`MIN_SIZE`]), never past the room on the moved sides.
pub fn resized(start: Rect, edges: Edges, dx: f64, dy: f64, min: (i32, i32), area: Rect) -> Rect {
    let (sx, sy, sw, sh) = start;
    let (ax, ay, aw, ah) = area;
    let min_w = min.0.max(MIN_SIZE.0);
    let min_h = min.1.max(MIN_SIZE.1);
    let (dx, dy) = (dx.round() as i32, dy.round() as i32);
    let (mut left, mut top, mut right, mut bottom) = (sx, sy, sx + sw, sy + sh);
    if edges.left {
        left = (sx + dx).max(ax).min(right - min_w);
    }
    if edges.right {
        right = (sx + sw + dx).min(ax + aw).max(left + min_w);
    }
    if edges.top {
        top = (sy + dy).max(ay).min(bottom - min_h);
    }
    if edges.bottom {
        bottom = (sy + sh + dy).min(ay + ah).max(top + min_h);
    }
    (left, top, right - left, bottom - top)
}

/// The edges a resize by Super + the right button takes: those of the
/// quarter of the window the pointer is in (a third in the middle moves
/// only one edge).
pub fn edges_at(rect: Rect, x: f64, y: f64) -> Edges {
    let (rx, ry, rw, rh) = rect;
    let fx = (x - rx as f64) / rw.max(1) as f64;
    let fy = (y - ry as f64) / rh.max(1) as f64;
    let mut edges =
        Edges { left: fx < 1.0 / 3.0, right: fx > 2.0 / 3.0, top: fy < 1.0 / 3.0, bottom: fy > 2.0 / 3.0 };
    if !edges.any() {
        edges = Edges::BOTTOM_RIGHT;
    }
    edges
}

/// Where a moved window snaps when it is let go with the pointer at
/// `(x, y)` (display frame): at the left or right edge a half, at the top
/// the whole room, else nowhere.
pub fn snap_at(x: f64, y: f64, display: (i32, i32)) -> Option<Snap> {
    let (w, h) = (display.0 as f64, display.1 as f64);
    if !(0.0..=w).contains(&x) || !(0.0..=h).contains(&y) {
        return None;
    }
    if y <= SNAP_BAND {
        Some(Snap::Max)
    } else if x <= SNAP_BAND {
        Some(Snap::Left)
    } else if x >= w - SNAP_BAND {
        Some(Snap::Right)
    } else {
        None
    }
}

/// What Super + an arrow does to a window on the desk: up maximizes (a
/// half goes to the whole room), down restores (a maximized window to its
/// own rectangle, a half too), left and right take that half (the other
/// half restores it).
pub fn arrow(snap: Option<Snap>, key: Arrow) -> Option<Snap> {
    match (key, snap) {
        (Arrow::Up, _) => Some(Snap::Max),
        (Arrow::Down, _) => None,
        (Arrow::Left, Some(Snap::Right)) | (Arrow::Right, Some(Snap::Left)) => None,
        (Arrow::Left, _) => Some(Snap::Left),
        (Arrow::Right, _) => Some(Snap::Right),
    }
}

/// An arrow key, for [`arrow`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Arrow {
    /// Up: maximize.
    Up,
    /// Down: restore.
    Down,
    /// Left: the left half.
    Left,
    /// Right: the right half.
    Right,
}

trait Reachable {
    fn clamp_reachable(self, area: Rect) -> Rect;
}

impl Reachable for Rect {
    fn clamp_reachable(self, area: Rect) -> Rect {
        let (x, y, w, h) = self;
        let (ax, ay, aw, ah) = area;
        let keep = KEEP_VISIBLE.min(w);
        let x = clamp(x, ax - w + keep, ax + aw - keep);
        let y = clamp(y, ay, ay + ah - TITLE_STRIP);
        (x, y, w, h)
    }
}

/// `v` within `lo..=hi`; `lo` if the range is empty (a room smaller than
/// what has to stay on it).
fn clamp(v: i32, lo: i32, hi: i32) -> i32 {
    if hi < lo {
        lo
    } else {
        v.clamp(lo, hi)
    }
}

/// A window kept reachable after the display changed (another mode, a
/// panel of the shell taking room): moved back in as [`moved`] would, and
/// made smaller if it is now larger than the room.
pub fn keep_inside(rect: Rect, area: Rect) -> Rect {
    let (x, y, w, h) = rect;
    let w = w.min(area.2.max(1));
    let h = h.min(area.3.max(1));
    (x, y, w, h).clamp_reachable(area)
}

/// A drag of the pointer on a window of the desk.
#[derive(Debug, Clone, PartialEq)]
pub struct Drag {
    /// The window (its control-socket id).
    pub window: u64,
    /// Move or resize.
    pub kind: DragKind,
    /// The pointer where the drag began (global space).
    pub from: (f64, f64),
    /// The window's rectangle then (display frame).
    pub start: Rect,
    /// The button that holds it (evdev code): its release ends the drag.
    pub button: u32,
    /// The finger that holds it instead (a touch slot: a title bar carried
    /// by a finger on laptop mode's desk panel): its lift ends the drag.
    pub touch: Option<u32>,
}

/// What a [`Drag`] does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DragKind {
    /// The window moves with the pointer.
    Move,
    /// These edges follow the pointer.
    Resize(Edges),
}

impl Drag {
    /// The window's rectangle with the pointer at `at` (global space).
    pub fn rect(&self, at: (f64, f64), min: (i32, i32), area: Rect) -> Rect {
        let (dx, dy) = (at.0 - self.from.0, at.1 - self.from.1);
        match self.kind {
            DragKind::Move => moved(self.start, dx, dy, area),
            DragKind::Resize(edges) => resized(self.start, edges, dx, dy, min, area),
        }
    }
}

/// The windows of the desks, by window id (every external display's: a
/// window keeps its frame when it goes to a panel and comes back).
#[derive(Debug, Default)]
pub struct Desk {
    /// Each window's frame.
    pub frames: HashMap<u64, Frame>,
    /// The pointer drag in progress, if any.
    pub drag: Option<Drag>,
    /// New windows opened on a desk so far (for the cascade).
    pub opened: usize,
}

impl Desk {
    /// The frame of a window, made where a new window opens when it has none.
    pub fn frame_or_new(&mut self, id: u64, area: Rect, wanted: Option<(i32, i32)>) -> &mut Frame {
        let nth = self.opened;
        let mut made = false;
        let frame = self.frames.entry(id).or_insert_with(|| {
            made = true;
            Frame { rect: initial_rect(area, wanted, nth), snap: None }
        });
        if made {
            self.opened += 1;
        }
        frame
    }

    /// A window is gone.
    pub fn forget(&mut self, id: u64) {
        self.frames.remove(&id);
        if self.drag.as_ref().map(|d| d.window) == Some(id) {
            self.drag = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const AREA: Rect = (0, 0, 1920, 1040);

    #[test]
    fn a_new_window_is_centred_at_a_share_of_the_room() {
        assert_eq!(initial_rect(AREA, None, 0), (384, 156, 1152, 728));
        // What it asked for, centred.
        assert_eq!(initial_rect(AREA, Some((800, 600)), 0), (560, 220, 800, 600));
        // Never larger than the room, never smaller than the minimum.
        assert_eq!(initial_rect(AREA, Some((4000, 3000)), 0), (0, 0, 1920, 1040));
        assert_eq!(initial_rect(AREA, Some((10, 10)), 0).2, MIN_SIZE.0);
    }

    #[test]
    fn a_window_s_picture_is_its_frame_at_the_size_it_drew() {
        let shown = (40, 30, 651, 462);
        // Drawn as asked, or bigger (its minimum size): that size.
        assert_eq!(picture_rect(shown, (651, 462), true), shown);
        assert_eq!(picture_rect(shown, (700, 462), true), (40, 30, 700, 462));
        // On its way to a new size, or nothing drawn yet: the size asked.
        assert_eq!(picture_rect(shown, (300, 200), false), shown);
        assert_eq!(picture_rect(shown, (0, 0), true), shown);
    }

    #[test]
    fn a_new_window_on_laptop_mode_s_desk_is_never_the_whole_room() {
        // A window opening on the desk showed full size, then jumped to its
        // own (2026-11-15). Its frame, and the picture its
        // grow-in aims at, is 86 % of the room, upright and turned (the
        // desk panel less the taskbar); maximized it is the room.
        for room in [(0, 0, 538, 709), (0, 0, 757, 490)] {
            let wanted = ((room.2 as f64 * PANEL_SHARE.0) as i32, (room.3 as f64 * PANEL_SHARE.1) as i32);
            let mut desk = Desk::default();
            let frame = desk.frame_or_new(1, room, Some(wanted)).clone();
            let shown = frame.shown(room);
            assert_eq!((shown.2, shown.3), wanted, "{room:?}");
            assert!(shown.0 > room.0 && shown.1 > room.1, "{room:?}: centred, not at the corner");
            assert!(shown.0 + shown.2 < room.0 + room.2 && shown.1 + shown.3 < room.1 + room.3);
            let picture = picture_rect(shown, wanted, true);
            assert_ne!(picture, room, "{room:?}");
            assert_eq!(picture, shown);
            // An app that asks to be maximized before it shows opens so.
            let max = Frame { snap: Some(Snap::Max), ..frame };
            assert_eq!(picture_rect(max.shown(room), (room.2, room.3), true), room);
        }
    }

    #[test]
    fn a_window_on_the_turned_desk_moves_every_way() {
        // Laptop mode on its side: the desk panel is 757 x 490 of room.
        let room = (0, 0, 757, 490);
        let start = (53, 34, 651, 421);
        assert_eq!(moved(start, 0.0, -20.0, room), (53, 14, 651, 421));
        assert_eq!(moved(start, 0.0, 30.0, room), (53, 64, 651, 421));
        assert_eq!(moved(start, -40.0, 0.0, room), (13, 34, 651, 421));
        assert_eq!(moved(start, 40.0, 0.0, room), (93, 34, 651, 421));
        // Up to the top and no further; down until only its title is left.
        assert_eq!(moved(start, 0.0, -400.0, room).1, 0);
        assert_eq!(moved(start, 0.0, 900.0, room).1, 490 - TITLE_STRIP);
    }

    #[test]
    fn new_windows_cascade_and_wrap() {
        let first = initial_rect(AREA, Some((800, 600)), 0);
        let second = initial_rect(AREA, Some((800, 600)), 1);
        assert_eq!((second.0 - first.0, second.1 - first.1), (CASCADE, CASCADE));
        // 220 px below the centred spot: 6 steps fit, the 8th wraps to the
        // centre again.
        assert_eq!(initial_rect(AREA, Some((800, 600)), 7), first);
        // A full-size window does not cascade.
        assert_eq!(initial_rect(AREA, Some((1920, 1040)), 3), AREA);
    }

    #[test]
    fn a_move_keeps_the_title_strip_reachable() {
        let start = (100, 100, 800, 600);
        assert_eq!(moved(start, 50.0, 20.0, AREA), (150, 120, 800, 600));
        // Far left: 64 px stay on the display.
        assert_eq!(moved(start, -5000.0, 0.0, AREA).0, -800 + KEEP_VISIBLE);
        // Far right likewise.
        assert_eq!(moved(start, 5000.0, 0.0, AREA).0, 1920 - KEEP_VISIBLE);
        // Never above the room's top, never with the title below its bottom.
        assert_eq!(moved(start, 0.0, -500.0, AREA).1, 0);
        assert_eq!(moved(start, 0.0, 5000.0, AREA).1, 1040 - TITLE_STRIP);
        // A room that starts lower (a panel on top): its top is the limit.
        assert_eq!(moved(start, 0.0, -500.0, (0, 40, 1920, 1000)).1, 40);
    }

    #[test]
    fn a_resize_moves_only_its_edges_and_keeps_the_minimum() {
        let start = (100, 100, 800, 600);
        assert_eq!(resized(start, Edges::BOTTOM_RIGHT, 40.0, 30.0, (0, 0), AREA), (100, 100, 840, 630));
        let top_left = Edges { top: true, left: true, ..Default::default() };
        assert_eq!(resized(start, top_left, -40.0, -30.0, (0, 0), AREA), (60, 70, 840, 630));
        // Shrunk past the minimum: held at it, the opposite edge still put.
        assert_eq!(resized(start, top_left, 2000.0, 2000.0, (0, 0), AREA), (660, 540, 240, 160));
        // The client's own minimum counts when it is larger.
        assert_eq!(
            resized(start, Edges::BOTTOM_RIGHT, -2000.0, -2000.0, (400, 300), AREA),
            (100, 100, 400, 300)
        );
        // Not past the room.
        assert_eq!(resized(start, Edges::BOTTOM_RIGHT, 5000.0, 5000.0, (0, 0), AREA), (100, 100, 1820, 940));
        assert_eq!(resized(start, top_left, -5000.0, -5000.0, (0, 0), AREA), (0, 0, 900, 700));
    }

    #[test]
    fn xdg_resize_edges() {
        assert_eq!(Edges::from_xdg(10), Edges::BOTTOM_RIGHT);
        assert_eq!(Edges::from_xdg(5), Edges { top: true, left: true, ..Default::default() });
        assert!(!Edges::from_xdg(0).any());
    }

    #[test]
    fn super_right_drag_takes_the_nearest_edges() {
        let r = (0, 0, 900, 900);
        assert_eq!(edges_at(r, 850.0, 850.0), Edges::BOTTOM_RIGHT);
        assert_eq!(edges_at(r, 50.0, 50.0), Edges { top: true, left: true, ..Default::default() });
        assert_eq!(edges_at(r, 450.0, 50.0), Edges { top: true, ..Default::default() });
        // The very middle: bottom right, as a plain resize.
        assert_eq!(edges_at(r, 450.0, 450.0), Edges::BOTTOM_RIGHT);
    }

    #[test]
    fn snaps_at_the_display_edges() {
        let d = (1920, 1080);
        assert_eq!(snap_at(900.0, 2.0, d), Some(Snap::Max));
        assert_eq!(snap_at(1.0, 500.0, d), Some(Snap::Left));
        assert_eq!(snap_at(1919.0, 500.0, d), Some(Snap::Right));
        assert_eq!(snap_at(900.0, 500.0, d), None);
        // Off the display (on a panel): no snap here.
        assert_eq!(snap_at(-10.0, 500.0, d), None);
        assert_eq!(snapped(AREA, Snap::Left), (0, 0, 960, 1040));
        assert_eq!(snapped((0, 0, 1921, 1040), Snap::Right), (960, 0, 961, 1040));
    }

    #[test]
    fn super_arrows() {
        assert_eq!(arrow(None, Arrow::Up), Some(Snap::Max));
        assert_eq!(arrow(Some(Snap::Left), Arrow::Up), Some(Snap::Max));
        assert_eq!(arrow(Some(Snap::Max), Arrow::Down), None);
        assert_eq!(arrow(None, Arrow::Left), Some(Snap::Left));
        assert_eq!(arrow(Some(Snap::Right), Arrow::Left), None);
        assert_eq!(arrow(Some(Snap::Max), Arrow::Right), Some(Snap::Right));
    }

    #[test]
    fn a_frame_shows_its_snap_in_the_current_room() {
        let frame = Frame { rect: (10, 10, 500, 400), snap: Some(Snap::Max) };
        assert_eq!(frame.shown((0, 30, 1280, 690)), (0, 30, 1280, 690));
        let frame = Frame { snap: None, ..frame };
        assert_eq!(frame.shown(AREA), (10, 10, 500, 400));
    }

    #[test]
    fn a_tiny_room_does_not_panic() {
        assert_eq!(moved((0, 0, 50, 50), 10.0, 10.0, (0, 0, 0, 0)), (0, 0, 50, 50));
        assert_eq!(keep_inside((5, 5, 300, 300), (0, 0, 10, 10)).2, 10);
    }

    #[test]
    fn keep_inside_after_the_display_got_smaller() {
        // A 1920 wide window on a display that is now 1280 x 720.
        assert_eq!(keep_inside((1500, 900, 1900, 1000), (0, 0, 1280, 720)), (1216, 688, 1280, 720));
    }

    #[test]
    fn a_drag_follows_the_pointer() {
        let drag = Drag {
            window: 3,
            kind: DragKind::Move,
            from: (500.0, 500.0),
            start: (100, 100, 800, 600),
            button: 0x110,
            touch: None,
        };
        assert_eq!(drag.rect((520.0, 480.0), (0, 0), AREA), (120, 80, 800, 600));
        let drag = Drag { kind: DragKind::Resize(Edges::BOTTOM_RIGHT), ..drag };
        assert_eq!(drag.rect((520.0, 480.0), (0, 0), AREA), (100, 100, 820, 580));
    }

    #[test]
    fn the_desk_remembers_frames_and_cascades() {
        let mut desk = Desk::default();
        let a = desk.frame_or_new(1, AREA, Some((800, 600))).rect;
        let again = desk.frame_or_new(1, AREA, None).rect;
        assert_eq!(a, again);
        let b = desk.frame_or_new(2, AREA, Some((800, 600))).rect;
        assert_eq!((b.0 - a.0, b.1 - a.1), (CASCADE, CASCADE));
        desk.forget(1);
        assert!(!desk.frames.contains_key(&1));
    }
}
