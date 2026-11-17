//! Per-output rotation, and where a touch lands on a rotated panel.
//!
//! A rotation is the output's `wl_output` transform: `90` means the picture is
//! drawn turned 90 degrees COUNTER-clockwise on the panel, so it reads upright
//! when the device is turned 90 degrees clockwise (the panel's left edge up).
//! Smithay renders it (`OutputDamageTracker` maps the output's transformed space
//! to the framebuffer with the inverse transform); clients keep drawing upright
//! buffers (buffer transform normal) and the compositor turns them.
//!
//! The touch devices report positions in the panel's own, unrotated frame (the
//! frame the framebuffer is scanned out in). [`touch_to_local`] turns such a
//! position into the output's logical, rotated frame: exactly the inverse of what
//! the renderer does to a picture, which the tests check against Smithay's own
//! mapping, so a finger lands on what is drawn under it at every rotation.

use serde::Serialize;
use smithay::utils::{Logical, Physical, Point, Size, Transform};

/// The four upright rotations (no mirroring: a panel is never seen through a
/// mirror).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
pub enum Rotation {
    /// Native portrait.
    #[default]
    #[serde(rename = "0")]
    R0,
    /// Drawn 90 degrees counter-clockwise: read with the device turned clockwise.
    #[serde(rename = "90")]
    R90,
    /// Upside down.
    #[serde(rename = "180")]
    R180,
    /// Drawn 270 degrees counter-clockwise (90 clockwise).
    #[serde(rename = "270")]
    R270,
}

impl Rotation {
    /// From degrees: 0, 90, 180 or 270 (also -90 = 270, 360 = 0).
    pub fn from_degrees(degrees: i64) -> Option<Self> {
        match degrees.rem_euclid(360) {
            0 if degrees.abs() <= 360 => Some(Rotation::R0),
            90 if degrees.abs() <= 360 => Some(Rotation::R90),
            180 if degrees.abs() <= 360 => Some(Rotation::R180),
            270 if degrees.abs() <= 360 => Some(Rotation::R270),
            _ => None,
        }
    }

    /// The angle in degrees.
    pub fn degrees(self) -> u32 {
        match self {
            Rotation::R0 => 0,
            Rotation::R90 => 90,
            Rotation::R180 => 180,
            Rotation::R270 => 270,
        }
    }

    /// The output transform this is.
    pub fn transform(self) -> Transform {
        match self {
            Rotation::R0 => Transform::Normal,
            Rotation::R90 => Transform::_90,
            Rotation::R180 => Transform::_180,
            Rotation::R270 => Transform::_270,
        }
    }

    /// Back from an output transform (a mirrored one counts as its rotation).
    pub fn from_transform(transform: Transform) -> Self {
        match transform {
            Transform::Normal | Transform::Flipped => Rotation::R0,
            Transform::_90 | Transform::Flipped90 => Rotation::R90,
            Transform::_180 | Transform::Flipped180 => Rotation::R180,
            Transform::_270 | Transform::Flipped270 => Rotation::R270,
        }
    }

    /// Whether the output's width and height swap.
    pub fn is_sideways(self) -> bool {
        matches!(self, Rotation::R90 | Rotation::R270)
    }
}

/// A touch at `normalised` (0..1 across and down the PANEL, in its own unrotated
/// frame) as a point in the output's logical frame, relative to the output's
/// origin.
///
/// `mode` is the panel's mode in physical pixels (unrotated, 1344x1892), `scale`
/// the output scale. The result is clamped inside the output, so a finger on the
/// very edge still lands on it.
pub fn touch_to_local(
    normalised: (f64, f64),
    rotation: Rotation,
    mode: Size<i32, Physical>,
    scale: f64,
) -> Point<f64, Logical> {
    let panel: Size<f64, Physical> = (mode.w as f64, mode.h as f64).into();
    let on_panel: Point<f64, Physical> =
        (normalised.0.clamp(0.0, 1.0) * panel.w, normalised.1.clamp(0.0, 1.0) * panel.h).into();
    // The renderer maps the rotated frame to the panel with the INVERSE of the
    // output transform; the output transform itself maps the panel back.
    let rotated = rotation.transform().transform_point_in(on_panel, &panel);
    let size = rotation.transform().transform_size(panel);
    let scale = if scale > 0.0 { scale } else { 1.0 };
    let logical_w = (size.w / scale).ceil();
    let logical_h = (size.h / scale).ceil();
    ((rotated.x / scale).clamp(0.0, logical_w - 0.01), (rotated.y / scale).clamp(0.0, logical_h - 0.01))
        .into()
}

/// A pen's tilt (degrees towards the panel's +x and +y, in its own unrotated
/// frame: libinput's, from duo2-touchd's tablet devices) as a client on the
/// rotated output sees it: the same turn as a point's ([`touch_to_local`]),
/// without the move.
pub fn tilt_to_local(tilt: (f64, f64), rotation: Rotation) -> (f64, f64) {
    let (x, y) = tilt;
    match rotation {
        Rotation::R0 => (x, y),
        // The panel's left edge is the top: +x of the panel points down,
        // +y of the panel points left.
        Rotation::R90 => (-y, x),
        Rotation::R180 => (-x, -y),
        Rotation::R270 => (y, -x),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MODE: (i32, i32) = (1344, 1892);

    #[test]
    fn a_tilt_turns_as_a_point_does() {
        // The direction from one panel point to another, mapped point by
        // point, is the tilt's turn.
        for rotation in [Rotation::R0, Rotation::R90, Rotation::R180, Rotation::R270] {
            for tilt in [(20.0, 0.0), (0.0, 35.0), (-10.0, 15.0)] {
                let a = touch_to_local((0.5, 0.5), rotation, mode(), 1.0);
                let b = touch_to_local((0.5 + tilt.0 / 1344.0, 0.5 + tilt.1 / 1892.0), rotation, mode(), 1.0);
                let want = (b.x - a.x, b.y - a.y);
                let got = tilt_to_local(tilt, rotation);
                assert!(
                    (got.0 - want.0).abs() < 0.01 && (got.1 - want.1).abs() < 0.01,
                    "{rotation:?} {tilt:?}: {got:?}, the points say {want:?}"
                );
            }
        }
    }

    fn mode() -> Size<i32, Physical> {
        MODE.into()
    }

    /// Where the renderer puts a point of the rotated output frame on the panel:
    /// the inverse output transform inside the rotated size, which is what
    /// Smithay's damage tracker and its direct scan-out path both do.
    fn drawn_at(point: Point<f64, Physical>, rotation: Rotation) -> Point<f64, Physical> {
        let panel: Size<f64, Physical> = (MODE.0 as f64, MODE.1 as f64).into();
        let rotated = rotation.transform().transform_size(panel);
        rotation.transform().invert().transform_point_in(point, &rotated)
    }

    fn close(a: Point<f64, Logical>, b: (f64, f64)) -> bool {
        (a.x - b.0).abs() < 0.02 && (a.y - b.1).abs() < 0.02
    }

    #[test]
    fn a_finger_lands_on_what_is_drawn_under_it_at_every_rotation() {
        for rotation in [Rotation::R0, Rotation::R90, Rotation::R180, Rotation::R270] {
            // A few points of the rotated frame, in physical pixels.
            let rotated_size =
                rotation.transform().transform_size(Size::<f64, Physical>::from((1344.0, 1892.0)));
            for (fx, fy) in [(0.1, 0.2), (0.5, 0.5), (0.9, 0.05), (0.33, 0.77)] {
                let point: Point<f64, Physical> = (fx * rotated_size.w, fy * rotated_size.h).into();
                let on_panel = drawn_at(point, rotation);
                let touch = (on_panel.x / 1344.0, on_panel.y / 1892.0);
                let local = touch_to_local(touch, rotation, mode(), 2.5);
                assert!(
                    close(local, (point.x / 2.5, point.y / 2.5)),
                    "{rotation:?}: drawn at {point:?}, touched at {local:?}"
                );
            }
        }
    }

    #[test]
    fn unrotated_is_the_plain_scaled_position() {
        let local = touch_to_local((0.5, 0.5), Rotation::R0, mode(), 2.5);
        assert!(close(local, (268.8, 378.4)));
    }

    #[test]
    fn at_90_the_panels_left_edge_is_the_top() {
        // Turned clockwise to read: the panel's top-left corner is now the
        // top-right of the picture, its bottom-left the top-left.
        let top_left = touch_to_local((0.0, 0.0), Rotation::R90, mode(), 2.5);
        assert!(close(top_left, (1892.0 / 2.5, 0.0)), "{top_left:?}");
        let bottom_left = touch_to_local((0.0, 1.0), Rotation::R90, mode(), 2.5);
        assert!(close(bottom_left, (0.0, 0.0)), "{bottom_left:?}");
        // The rotated output is landscape: 757 x 538 logical.
        let bottom_right = touch_to_local((1.0, 1.0), Rotation::R90, mode(), 2.5);
        assert!(close(bottom_right, (0.0, 537.6)), "{bottom_right:?}");
    }

    #[test]
    fn at_180_the_corners_swap() {
        let local = touch_to_local((0.0, 0.0), Rotation::R180, mode(), 1.0);
        assert!(close(local, (1343.99, 1891.99)), "{local:?}");
    }

    #[test]
    fn degrees() {
        assert_eq!(Rotation::from_degrees(0), Some(Rotation::R0));
        assert_eq!(Rotation::from_degrees(90), Some(Rotation::R90));
        assert_eq!(Rotation::from_degrees(-90), Some(Rotation::R270));
        assert_eq!(Rotation::from_degrees(360), Some(Rotation::R0));
        assert_eq!(Rotation::from_degrees(45), None);
        assert_eq!(Rotation::from_degrees(720), None);
        for r in [Rotation::R0, Rotation::R90, Rotation::R180, Rotation::R270] {
            assert_eq!(Rotation::from_transform(r.transform()), r);
            assert_eq!(Rotation::from_degrees(r.degrees() as i64), Some(r));
        }
        assert!(Rotation::R90.is_sideways() && !Rotation::R180.is_sideways());
    }
}
