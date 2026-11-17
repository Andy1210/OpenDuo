//! The pen's raw reports, made into what the games read (the rules that
//! need no window; input.rs applies them to the Wayland tablet's frames).

/// The tablet protocol's tool types (zwp_tablet_tool_v2.type).
pub mod tool {
    pub const PEN: u32 = 0x140;
    pub const ERASER: u32 = 0x141;
    pub const BRUSH: u32 = 0x142;
    pub const PENCIL: u32 = 0x143;
    pub const AIRBRUSH: u32 = 0x144;
    pub const FINGER: u32 = 0x145;
    pub const MOUSE: u32 = 0x146;
    pub const LENS: u32 = 0x147;
}

/// Which end of the pen a tool is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum End {
    Tip,
    Eraser,
}

impl End {
    /// The end a tool of this type is: the eraser type is the eraser end, every
    /// other (pen, pencil, brush, airbrush; and an unknown or unannounced 0) a tip.
    pub fn of_tool(tool_type: u32) -> End {
        if tool_type == tool::ERASER {
            End::Eraser
        } else {
            End::Tip
        }
    }
}

/// The tilt after a report, (x, y) in degrees. The Duo 2's digitizer has a
/// tilt only while the tip touches and reports exactly 0, 0 when it has
/// none (in hover, and in the reports around the touch and the lift), so
/// while the tip is down an exact 0, 0 keeps the tilt it had: a pen held
/// exactly upright to the hundredth of a degree is rare, and keeping a
/// near-zero tilt then costs nothing.
pub fn tilt_after(prev: (f32, f32), report: (f32, f32), down: bool) -> (f32, f32) {
    if down && report == (0.0, 0.0) {
        prev
    } else {
        report
    }
}

/// When a compositor event happened on the game's clock (s): `now` is the
/// game's clock at `mono_now_ms` (CLOCK_MONOTONIC, ms), `event_ms` the
/// event's 32-bit timestamp on that clock (0: none, taken as now). The
/// timestamp wraps every 49.7 days; an event is never in the future.
pub fn event_time(now: f64, mono_now_ms: f64, event_ms: u32) -> f64 {
    if event_ms == 0 {
        return now;
    }
    let age_ms = (mono_now_ms as u64 as u32).wrapping_sub(event_ms);
    // A timestamp "ahead" of now (clock skew of a ms or two) wraps to ~49 days: none.
    let age = if age_ms > u32::MAX / 2 {
        0.0
    } else {
        age_ms as f64 / 1000.0
    };
    now - age.min(1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_eraser_type_is_the_eraser_end_everything_else_the_tip() {
        assert_eq!(End::of_tool(tool::ERASER), End::Eraser);
        for t in [
            tool::PEN,
            tool::PENCIL,
            tool::BRUSH,
            tool::AIRBRUSH,
            tool::FINGER,
            tool::MOUSE,
            tool::LENS,
            0,
        ] {
            assert_eq!(End::of_tool(t), End::Tip, "type {t:#x}");
        }
    }

    #[test]
    fn a_zero_tilt_while_touching_is_a_dropout() {
        assert_eq!(tilt_after((24.0, -6.5), (0.0, 0.0), true), (24.0, -6.5));
        assert_eq!(tilt_after((24.0, -6.5), (25.0, -6.0), true), (25.0, -6.0));
        // A real tilt that merely crosses zero on one axis is taken.
        assert_eq!(tilt_after((24.0, -6.5), (0.0, -6.0), true), (0.0, -6.0));
        // Hovering: no tilt is no tilt.
        assert_eq!(tilt_after((24.0, -6.5), (0.0, 0.0), false), (0.0, 0.0));
    }

    #[test]
    fn event_times_are_their_age_before_now() {
        assert!((event_time(10.0, 5000.0, 4990) - 9.99).abs() < 1e-9);
        assert_eq!(event_time(10.0, 5000.0, 0), 10.0);
        // A ms ahead (skew): now.
        assert_eq!(event_time(10.0, 5000.0, 5001), 10.0);
        // Across the 32-bit wrap: the event 6 ms before, now 4 ms past the wrap.
        let mono = (1u64 << 32) as f64 + 4.0;
        assert!((event_time(10.0, mono, u32::MAX - 1) - 9.994).abs() < 1e-9);
        // Very old (a queue nobody read): at most a second back.
        assert_eq!(event_time(10.0, 100_000.0, 1000), 9.0);
    }
}
