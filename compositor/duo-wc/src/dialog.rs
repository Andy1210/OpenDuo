//! Dialogs, and windows that do not fit their panel (the pure part).
//!
//! **A dialog** is a toplevel with a parent (`xdg_toplevel.set_parent`, or
//! across clients through xdg-foreign: the file chooser of the portal,
//! xdg-desktop-portal-gtk, is the dialog of the app that asked for it), or a
//! window of a portal backend whose app could not name itself as the parent.
//! It goes on ONE panel ([`dialog_panel`]): its parent's; for a parent that
//! spans both, the panel the user touched last (where the button that opened
//! it is), else the one with more room; never across the hinge, whatever the
//! pose would do with a new window.
//!
//! It is configured to at most that panel's room (the panel less the status
//! bar, a taskbar dock and the on-screen keyboard), smaller where its own
//! xdg `max_size` says so ([`dialog_size`]), and shown centred in that room
//! at the size it draws.
//!
//! **Too big for its panel.** A client cannot be made smaller than its xdg
//! `min_size`: the GTK 3 file chooser (the portal's) draws at least 593 x 373
//! logical px, the GTK 4 one 656 x 326 (measured, Cantarell 11), and a panel
//! is 538 px wide. Such a window (a dialog or not) that has taken its
//! configured size and still draws bigger is **scaled down to fit** the
//! panel's room less its bars ([`fit`]): its picture and its popups drawn
//! smaller, its touches mapped back ([`to_client`]), the way phoc's
//! scale-to-fit does it. Cutting it at the panel's edge (as a split half is
//! cut) would hide the dialog's own buttons (the file chooser's Open button
//! sits at its right edge). The on-screen keyboard does not count for the
//! scale: a window that cannot get shorter goes under the keyboard, as any
//! app does, instead of shrinking whenever the keyboard comes. Whatever is
//! still outside the panel (a client caught between two sizes) is cut at the
//! panel's edge: a window on one panel is never drawn or touched on the
//! other.

/// A rectangle (x, y, w, h), as in [`crate::layout`].
pub type Rect = (i32, i32, i32, i32);

/// The app ids of the portal backends whose windows are dialogs of the app
/// that asked (compared without case: GTK 3 capitalises its program class).
pub const PORTAL_APP_IDS: &[&str] = &[
    "xdg-desktop-portal-gtk",
    "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-kde",
    "org.freedesktop.impl.portal.desktop.kde",
];

/// Whether an app id is a portal backend's.
pub fn is_portal_app(app_id: Option<&str>) -> bool {
    app_id.map(|id| PORTAL_APP_IDS.iter().any(|p| p.eq_ignore_ascii_case(id))).unwrap_or(false)
}

/// Where a dialog's parent is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Parent {
    /// On the panel with this name.
    Panel(String),
    /// Across the panels.
    Span,
    /// Not known (a portal's window without a parent).
    Unknown,
}

/// The panel a dialog goes on. `panels` are the panels in use with the room a
/// window has on each (its maximized area: less the bars and the keyboard),
/// `active` the panel touched last.
///
/// The parent's panel if it is in use; for a spanning parent, or none known,
/// the active panel if it is in use, else the panel with the most room (the
/// first of equals: left to right).
pub fn dialog_panel(parent: &Parent, active: Option<&str>, panels: &[(&str, Rect)]) -> Option<String> {
    let in_use = |name: &str| panels.iter().any(|(n, _)| *n == name);
    if let Parent::Panel(name) = parent {
        if in_use(name) {
            return Some(name.clone());
        }
    }
    if let Some(active) = active.filter(|a| in_use(a)) {
        return Some(active.to_owned());
    }
    let area = |r: &Rect| r.2.max(0) as i64 * r.3.max(0) as i64;
    panels
        .iter()
        .fold(None::<&(&str, Rect)>, |best, p| match best {
            Some(b) if area(&b.1) >= area(&p.1) => Some(b),
            _ => Some(p),
        })
        .map(|(name, _)| (*name).to_owned())
}

/// The size a dialog is configured to: the room, or smaller on an axis
/// where its `max_size` (0 = none) is smaller. Its `min_size` does not
/// count: asked for less than it can be, the client draws at its minimum,
/// and [`fit`] scales that down.
pub fn dialog_size(room: (i32, i32), max: (i32, i32)) -> (i32, i32) {
    let axis = |room: i32, max: i32| if max > 0 { room.min(max) } else { room };
    (axis(room.0, max.0), axis(room.1, max.1))
}

/// Where a window is shown and how much smaller than it draws.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fit {
    /// The top left corner of its window geometry (global, logical): the
    /// window's place in the space. Its touches are mapped around it.
    pub at: (i32, i32),
    /// The scale its picture is drawn at (1 = as it draws, less = smaller).
    pub factor: f64,
}

impl Fit {
    /// Its picture's rectangle (global, logical) for a window geometry of
    /// `size`.
    pub fn shown(&self, size: (i32, i32)) -> (f64, f64, f64, f64) {
        (self.at.0 as f64, self.at.1 as f64, size.0 as f64 * self.factor, size.1 as f64 * self.factor)
    }
}

/// How a window of `size` (its committed window geometry) is shown in
/// `area` (the room it was configured to: global, less the bars and the
/// keyboard) without leaving `room` (the panel less its bars, keyboard or
/// not; for a fullscreen window the panel).
///
/// - Bigger than `room` on an axis: scaled down (both axes alike) to fit it,
///   when `settled` (the client drew at the size last asked of it: a window
///   still on its way to a new size is not shrunk for those frames, it is
///   only cut at the panel's edge).
/// - A dialog (`centre`) is centred in `area`; another window keeps its top
///   left corner where its area starts (the maximized place), and only a
///   scaled one is centred across.
/// - Wherever it ends up, its picture stays inside `room` as far as it fits.
pub fn fit(area: Rect, room: Rect, size: (i32, i32), centre: bool, settled: bool) -> Fit {
    let (w, h) = (size.0.max(1) as f64, size.1.max(1) as f64);
    let factor =
        if settled { (room.2.max(1) as f64 / w).min(room.3.max(1) as f64 / h).min(1.0) } else { 1.0 };
    // The picture's size, rounded down so that it never grows past the room.
    let (sw, sh) = ((w * factor).floor() as i32, (h * factor).floor() as i32);
    let mut x = area.0;
    let mut y = area.1;
    if centre || factor < 1.0 {
        x = area.0 + (area.2 - sw) / 2;
    }
    if centre {
        y = area.1 + ((area.3 - sh) / 2).max(0);
    }
    // Inside the room as far as it fits (left / top first).
    let clamp = |v: i32, lo: i32, len: i32, size: i32| {
        let hi = lo + len - size;
        if hi < lo {
            lo
        } else {
            v.clamp(lo, hi)
        }
    };
    if centre || factor < 1.0 {
        x = clamp(x, room.0, room.2, sw);
    }
    if centre {
        y = clamp(y, room.1, room.3, sh);
    }
    Fit { at: (x, y), factor }
}

/// Where a point of the screen (global, logical) is for the client of a
/// window shown at `fit`: around its place, divided by the factor.
pub fn to_client(fit: &Fit, point: (f64, f64)) -> (f64, f64) {
    if fit.factor >= 1.0 {
        return point;
    }
    let (ax, ay) = (fit.at.0 as f64, fit.at.1 as f64);
    (ax + (point.0 - ax) / fit.factor, ay + (point.1 - ay) / fit.factor)
}

/// Where a point of the client's (global, logical, as [`to_client`] gives
/// it) is on the screen.
pub fn to_screen(fit: &Fit, point: (f64, f64)) -> (f64, f64) {
    if fit.factor >= 1.0 {
        return point;
    }
    let (ax, ay) = (fit.at.0 as f64, fit.at.1 as f64);
    (ax + (point.0 - ax) * fit.factor, ay + (point.1 - ay) * fit.factor)
}

/// The rectangle a popup of a scaled window may use, in the client's
/// coordinates (global): `usable` (on the screen) mapped back around the
/// window's place.
pub fn usable_for_client(fit: &Fit, usable: Rect) -> Rect {
    if fit.factor >= 1.0 {
        return usable;
    }
    let (x, y) = to_client(fit, (usable.0 as f64, usable.1 as f64));
    let w = (usable.2 as f64 / fit.factor).floor() as i32;
    let h = (usable.3 as f64 / fit.factor).floor() as i32;
    (x.ceil() as i32, y.ceil() as i32, w, h)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// DSI-1 at scale 2.5: 538 x 757, a 48 px status bar.
    const DSI1: Rect = (0, 0, 538, 757);
    const ROOM1: Rect = (0, 48, 538, 709);
    /// DSI-2 with the hinge gap on (66 physical px = 27 logical).
    const ROOM2: Rect = (565, 48, 538, 709);

    #[test]
    fn a_dialog_goes_on_its_parents_panel() {
        let panels = [("DSI-1", ROOM1), ("DSI-2", ROOM2)];
        let parent = Parent::Panel("DSI-2".into());
        // Wherever the user touched last.
        assert_eq!(dialog_panel(&parent, Some("DSI-1"), &panels).as_deref(), Some("DSI-2"));
        assert_eq!(dialog_panel(&parent, None, &panels).as_deref(), Some("DSI-2"));
    }

    #[test]
    fn a_spanning_parents_dialog_goes_where_the_user_touched() {
        let panels = [("DSI-1", ROOM1), ("DSI-2", ROOM2)];
        assert_eq!(dialog_panel(&Parent::Span, Some("DSI-2"), &panels).as_deref(), Some("DSI-2"));
        assert_eq!(dialog_panel(&Parent::Span, Some("DSI-1"), &panels).as_deref(), Some("DSI-1"));
    }

    #[test]
    fn otherwise_the_panel_with_more_room() {
        // The keyboard is up on DSI-1: DSI-2 has more room.
        let panels = [("DSI-1", (0, 48, 538, 400)), ("DSI-2", ROOM2)];
        assert_eq!(dialog_panel(&Parent::Span, None, &panels).as_deref(), Some("DSI-2"));
        // A touch on a panel that is off (single pose) does not count.
        let single = [("DSI-2", ROOM2)];
        assert_eq!(dialog_panel(&Parent::Span, Some("DSI-1"), &single).as_deref(), Some("DSI-2"));
        // Equal: the first, left to right.
        let panels = [("DSI-1", ROOM1), ("DSI-2", ROOM2)];
        assert_eq!(dialog_panel(&Parent::Unknown, None, &panels).as_deref(), Some("DSI-1"));
        assert_eq!(dialog_panel(&Parent::Unknown, None, &[]), None);
    }

    #[test]
    fn a_parent_on_a_panel_out_of_use_falls_back_to_the_active_one() {
        let single = [("DSI-1", ROOM1)];
        let parent = Parent::Panel("DSI-2".into());
        assert_eq!(dialog_panel(&parent, Some("DSI-1"), &single).as_deref(), Some("DSI-1"));
    }

    #[test]
    fn portal_backends_by_app_id() {
        assert!(is_portal_app(Some("xdg-desktop-portal-gtk")));
        // GTK 3's program class is capitalised.
        assert!(is_portal_app(Some("Xdg-desktop-portal-gtk")));
        assert!(!is_portal_app(Some("org.gnome.TextEditor")));
        assert!(!is_portal_app(None));
    }

    #[test]
    fn a_dialog_is_configured_to_the_room_or_its_max_size() {
        assert_eq!(dialog_size((538, 709), (0, 0)), (538, 709));
        // Fixed size (min = max), smaller than the room: its own.
        assert_eq!(dialog_size((538, 709), (360, 200)), (360, 200));
        // A larger preferred size is not given.
        assert_eq!(dialog_size((538, 709), (800, 600)), (538, 600));
        assert_eq!(dialog_size((538, 709), (0, 900)), (538, 709));
    }

    #[test]
    fn a_dialog_that_fits_is_centred_in_its_room() {
        let f = fit(ROOM1, ROOM1, (360, 200), true, true);
        assert_eq!(f.factor, 1.0);
        assert_eq!(f.at, (89, 48 + (709 - 200) / 2));
        // On DSI-2.
        let f = fit(ROOM2, ROOM2, (360, 200), true, true);
        assert_eq!(f.at, (565 + 89, 48 + 254));
        // Filling the room: at its corner.
        let f = fit(ROOM1, ROOM1, (538, 709), true, true);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
    }

    #[test]
    fn the_gtk3_file_chooser_is_scaled_into_one_panel() {
        // Configured 538 x 709, it draws its minimum width.
        let f = fit(ROOM2, ROOM2, (593, 709), true, true);
        assert!((f.factor - 538.0 / 593.0).abs() < 1e-9);
        // Inside DSI-2's room, not over the hinge.
        assert!(inside(f.shown((593, 709)), ROOM2), "{:?}", f.shown((593, 709)));
        // Centred: the spare height above and below.
        assert_eq!(f.at.1, 48 + (709 - (709.0 * f.factor).floor() as i32) / 2);
    }

    /// Whether a shown rectangle is inside a room (to rounding).
    fn inside(shown: (f64, f64, f64, f64), room: Rect) -> bool {
        let e = 1e-6;
        shown.0 >= room.0 as f64 - e
            && shown.1 >= room.1 as f64 - e
            && shown.0 + shown.2 <= (room.0 + room.2) as f64 + e
            && shown.1 + shown.3 <= (room.1 + room.3) as f64 + e
    }

    #[test]
    fn taller_and_wider_both_count() {
        let f = fit(ROOM1, ROOM1, (656, 900), true, true);
        assert!((f.factor - 709.0 / 900.0).abs() < 1e-9);
        assert!(inside(f.shown((656, 900)), ROOM1));
    }

    #[test]
    fn a_window_on_its_way_to_a_new_size_is_not_shrunk() {
        // Back from span: configured 538 wide, still drawing 1103 for a frame.
        let f = fit(ROOM1, ROOM1, (1103, 709), false, false);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
    }

    #[test]
    fn an_app_keeps_its_place_and_is_scaled_only_if_it_must() {
        // A maximized app that draws its configured size.
        let f = fit(ROOM1, ROOM1, (538, 709), false, true);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
        // One that rounds itself down (a terminal by cells): not centred.
        let f = fit(ROOM1, ROOM1, (530, 700), false, true);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
        // One that cannot be narrower than 600: scaled, top kept, centred across.
        let f = fit(ROOM1, ROOM1, (600, 709), false, true);
        assert!((f.factor - 538.0 / 600.0).abs() < 1e-9);
        assert_eq!(f.at.1, 48);
        assert!(inside(f.shown((600, 709)), ROOM1));
    }

    #[test]
    fn the_keyboard_does_not_shrink_a_window() {
        // The keyboard takes 300 px: the area shrinks, the room (bars only) not.
        let area = (0, 48, 538, 409);
        let f = fit(area, ROOM1, (538, 600), false, true);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
        // A dialog taller than the area left by the keyboard: from its top.
        let f = fit(area, ROOM1, (538, 500), true, true);
        assert_eq!((f.at, f.factor), ((0, 48), 1.0));
    }

    #[test]
    fn fullscreen_uses_the_whole_panel() {
        let f = fit(DSI1, DSI1, (700, 757), false, true);
        assert!(f.factor < 1.0);
        assert!(inside(f.shown((700, 757)), DSI1));
    }

    #[test]
    fn touches_map_back_to_the_client_and_forth() {
        let f = Fit { at: (565, 100), factor: 0.5 };
        // Its corner stays, a point 10 px in is 20 px in for the client.
        assert_eq!(to_client(&f, (565.0, 100.0)), (565.0, 100.0));
        assert_eq!(to_client(&f, (575.0, 130.0)), (585.0, 160.0));
        assert_eq!(to_screen(&f, to_client(&f, (700.5, 333.25))), (700.5, 333.25));
        // Not scaled: as is.
        let one = Fit { at: (0, 48), factor: 1.0 };
        assert_eq!(to_client(&one, (12.5, 60.0)), (12.5, 60.0));
    }

    #[test]
    fn a_scaled_windows_popups_get_the_room_in_its_coordinates() {
        let f = Fit { at: (565, 48), factor: 0.5 };
        let usable = (565, 48, 538, 409);
        assert_eq!(usable_for_client(&f, usable), (565, 48, 1076, 818));
        let one = Fit { at: (0, 48), factor: 1.0 };
        assert_eq!(usable_for_client(&one, usable), usable);
    }
}
