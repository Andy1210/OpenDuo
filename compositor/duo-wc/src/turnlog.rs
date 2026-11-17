//! The frame timing of a rotation's turn (crate::rotanim), logged when the
//! turn is over: one `info` line per panel with the frames that reached the
//! panel during the turn, the intervals between their flips (the kernel's
//! timestamps), the vblanks that passed without a new frame, and the CPU
//! time each frame took to build, render and queue. The first turn felt
//! laggy (2026-11-07); this is how it is measured on the phone:
//! `journalctl -t duo-wc | grep 'rotation: frame timing'`.
//!
//! The same log times the flight of a carried window that was let go
//! (crate::windrag), from the lift to the window at rest:
//! `journalctl -t duo-wc | grep 'drop: frame timing'` (`first_ms`: the
//! lift to the first frame of the flight on the panel).

use std::collections::HashMap;
use std::time::Duration;

/// A frame interval longer than this is a late frame (the panels run at
/// 90 Hz: 11.1 ms; one missed vblank is 22.2 ms).
pub const LATE_MS: f64 = 13.0;

/// One panel's turn.
#[derive(Debug, Default, Clone)]
pub struct TurnLog {
    /// When the turn was asked for (CLOCK_MONOTONIC).
    pub asked: Duration,
    /// The work on the loop before the turn: the old pictures' capture (ms).
    pub capture_ms: f64,
    /// Each frame of the turn on the panel: when its flip completed
    /// (CLOCK_MONOTONIC, the kernel's timestamp) and the vblank sequence.
    pub flips: Vec<(Duration, u32)>,
    /// The CPU time each frame took to build, render and queue (ms).
    pub render_ms: Vec<f64>,
    /// ... of which building its elements (ms; a turn's capture of the new
    /// picture is in here).
    pub build_ms: Vec<f64>,
    /// How long after the panel's last flip each frame's render began (ms):
    /// the loop busy with something else shows here.
    pub lag_ms: Vec<f64>,
    /// ... of which DrmCompositor::render_frame and queue_frame (ms).
    pub draw_ms: Vec<f64>,
    pub commit_ms: Vec<f64>,
    /// Frames rendered ahead (queued behind the one in flight).
    pub ahead: u32,
    /// Frames the display refused as busy (tried again 8 ms later).
    pub busy: u32,
    /// The animation is over with the frame in flight: its flip closes the log.
    pub ending: bool,
}

/// What a [`TurnLog`] comes to.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    /// Frames of the turn that reached the panel.
    pub frames: usize,
    /// From the request to the first of them on the panel (ms).
    pub first_ms: f64,
    /// The longest interval between two of them (ms).
    pub max_gap_ms: f64,
    /// Intervals longer than [`LATE_MS`].
    pub late: usize,
    /// Vblanks without a new frame between the first and the last.
    pub missed_vblanks: u32,
    /// The longest and the mean CPU time of a frame (ms).
    pub max_render_ms: f64,
    pub mean_render_ms: f64,
    /// The longest element build, draw, commit (ms), and render start lag
    /// after the panel's last flip (ms; the first frame's, after an idle
    /// panel, left out).
    pub max_build_ms: f64,
    pub max_draw_ms: f64,
    pub max_commit_ms: f64,
    pub max_lag_ms: f64,
    /// The intervals, ms, rounded to 0.1 (for the log).
    pub gaps: Vec<f64>,
}

/// Sum up one turn's frames.
pub fn summarize(log: &TurnLog) -> Summary {
    let ms = |d: Duration| d.as_secs_f64() * 1000.0;
    let gaps: Vec<f64> = log.flips.windows(2).map(|w| ms(w[1].0.saturating_sub(w[0].0))).collect();
    let missed_vblanks = log.flips.windows(2).map(|w| w[1].1.wrapping_sub(w[0].1).saturating_sub(1)).sum();
    let first_ms = log.flips.first().map(|f| ms(f.0.saturating_sub(log.asked))).unwrap_or(0.0);
    let max_render_ms = log.render_ms.iter().cloned().fold(0.0, f64::max);
    let mean_render_ms = if log.render_ms.is_empty() {
        0.0
    } else {
        log.render_ms.iter().sum::<f64>() / log.render_ms.len() as f64
    };
    Summary {
        frames: log.flips.len(),
        first_ms,
        max_gap_ms: gaps.iter().cloned().fold(0.0, f64::max),
        late: gaps.iter().filter(|g| **g > LATE_MS).count(),
        missed_vblanks,
        max_render_ms,
        mean_render_ms,
        max_build_ms: log.build_ms.iter().cloned().fold(0.0, f64::max),
        max_draw_ms: log.draw_ms.iter().cloned().fold(0.0, f64::max),
        max_commit_ms: log.commit_ms.iter().cloned().fold(0.0, f64::max),
        max_lag_ms: log.lag_ms.iter().skip(1).cloned().fold(0.0, f64::max),
        gaps: gaps.iter().map(|g| (g * 10.0).round() / 10.0).collect(),
    }
}

/// CLOCK_MONOTONIC now (the clock of the kernel's flip timestamps).
pub fn monotonic_now() -> Duration {
    Duration::from(smithay::utils::Clock::<smithay::utils::Monotonic>::new().now())
}

/// The turns being measured, by output name.
#[derive(Debug, Default)]
pub struct TurnLogs {
    pub logs: HashMap<String, TurnLog>,
}

impl TurnLogs {
    /// A new turn starts on `output` (one still logged there, never
    /// finished: the panel went dark meanwhile, is dropped).
    pub fn start(&mut self, output: &str, asked: Duration, capture_ms: f64) {
        self.logs.insert(output.to_owned(), TurnLog { asked, capture_ms, ..Default::default() });
    }

    /// A frame of `output` was queued (`render_ms` of CPU time, `build_ms`
    /// of it building the elements, begun `lag_ms` after the last flip);
    /// `busy`: the display refused it; `running`: the animation still runs
    /// there after this frame. The frame that ends the turn is counted, and
    /// its flip closes the log.
    pub fn frame_queued(
        &mut self,
        output: &str,
        render_ms: f64,
        build_ms: f64,
        lag_ms: f64,
        busy: bool,
        running: bool,
    ) {
        let Some(log) = self.logs.get_mut(output) else {
            return;
        };
        if busy {
            log.busy += 1;
            return;
        }
        if log.ending {
            return;
        }
        log.render_ms.push(render_ms);
        log.build_ms.push(build_ms);
        log.lag_ms.push(lag_ms);
        if !running {
            log.ending = true;
        }
    }

    /// The parts of the frame just counted ([`Self::frame_queued`]): its
    /// draw and commit time (ms), and whether it went ahead.
    pub fn frame_parts(&mut self, output: &str, draw_ms: f64, commit_ms: f64, ahead: bool) {
        let Some(log) = self.logs.get_mut(output) else {
            return;
        };
        if log.draw_ms.len() >= log.render_ms.len() {
            return;
        }
        log.draw_ms.push(draw_ms);
        log.commit_ms.push(commit_ms);
        if ahead {
            log.ahead += 1;
        }
    }

    /// A flip of `output` completed at `at` (vblank `sequence`): counted
    /// while a turn runs there; the turn's summary when it was the last
    /// frame (with frames rendered ahead, the last flip is that of the
    /// last frame queued).
    pub fn flipped(&mut self, output: &str, at: Duration, sequence: u32) -> Option<(TurnLog, Summary)> {
        let log = self.logs.get_mut(output)?;
        if log.render_ms.len() <= log.flips.len() {
            // The flip of a frame queued before the turn.
            return None;
        }
        log.flips.push((at, sequence));
        if !log.ending || log.flips.len() < log.render_ms.len() {
            return None;
        }
        let log = self.logs.remove(output)?;
        let summary = summarize(&log);
        Some((log, summary))
    }

    /// The animation is over, whether or not a frame of `output` said so (a
    /// panel with nothing to redraw at its end queues none): a log without
    /// a frame of it is dropped; one whose frames have all flipped is closed
    /// now (its summary); else the flip of its last frame closes it.
    pub fn end(&mut self, output: &str) -> Option<(TurnLog, Summary)> {
        let log = self.logs.get_mut(output)?;
        if log.render_ms.is_empty() {
            self.logs.remove(output);
            return None;
        }
        log.ending = true;
        if log.flips.len() < log.render_ms.len() {
            return None;
        }
        let log = self.logs.remove(output)?;
        let summary = summarize(&log);
        Some((log, summary))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(v: f64) -> Duration {
        Duration::from_secs_f64(v / 1000.0)
    }

    #[test]
    fn a_smooth_turn_has_no_late_frame() {
        let mut logs = TurnLogs::default();
        logs.start("DSI-1", ms(1000.0), 4.0);
        // A flip of the frame queued before the turn: not counted.
        assert!(logs.flipped("DSI-1", ms(1001.0), 9).is_none());
        for i in 0..4 {
            logs.frame_queued("DSI-1", 2.0 + i as f64, 1.0, 0.5 * i as f64, false, i < 3);
            let done = logs.flipped("DSI-1", ms(1010.0 + 11.1 * i as f64), 10 + i);
            assert_eq!(done.is_some(), i == 3);
            if let Some((_, s)) = done {
                assert_eq!(s.frames, 4);
                assert!((s.first_ms - 10.0).abs() < 1e-6, "{s:?}");
                assert!((s.max_gap_ms - 11.1).abs() < 1e-6, "{s:?}");
                assert_eq!(s.late, 0);
                assert_eq!(s.missed_vblanks, 0);
                assert_eq!(s.max_render_ms, 5.0);
                assert_eq!(s.mean_render_ms, 3.5);
                assert_eq!(s.gaps, vec![11.1, 11.1, 11.1]);
                assert_eq!((s.max_build_ms, s.max_lag_ms), (1.0, 1.5));
                assert_eq!(s.max_draw_ms, 0.0);
            }
        }
        assert!(logs.logs.is_empty());
    }

    #[test]
    fn a_missed_vblank_is_a_late_frame() {
        let log = TurnLog {
            asked: ms(0.0),
            flips: vec![(ms(10.0), 1), (ms(21.1), 2), (ms(43.3), 4), (ms(54.4), 5)],
            render_ms: vec![1.0, 15.0, 1.0, 1.0],
            ..Default::default()
        };
        let s = summarize(&log);
        assert_eq!(s.late, 1);
        assert_eq!(s.missed_vblanks, 1);
        assert!((s.max_gap_ms - 22.2).abs() < 1e-6);
        assert_eq!(s.max_render_ms, 15.0);
    }

    #[test]
    fn with_frames_ahead_the_last_flip_closes_the_log() {
        let mut logs = TurnLogs::default();
        logs.start("DSI-1", ms(0.0), 0.0);
        logs.frame_queued("DSI-1", 1.0, 0.5, 0.0, false, true);
        logs.frame_parts("DSI-1", 0.3, 0.2, false);
        logs.frame_queued("DSI-1", 1.0, 0.5, 0.0, false, true);
        logs.frame_parts("DSI-1", 0.3, 0.2, true);
        assert!(logs.flipped("DSI-1", ms(11.0), 1).is_none());
        // The last frame queued ahead of its predecessor's flip.
        logs.frame_queued("DSI-1", 9.0, 0.5, 0.0, false, false);
        logs.frame_parts("DSI-1", 8.0, 0.5, true);
        assert!(logs.flipped("DSI-1", ms(22.0), 2).is_none());
        let (log, s) = logs.flipped("DSI-1", ms(33.0), 3).unwrap();
        assert_eq!((s.frames, log.ahead), (3, 2));
        assert_eq!((s.max_draw_ms, s.max_commit_ms), (8.0, 0.5));
    }

    #[test]
    fn an_end_without_a_last_frame_closes_the_log() {
        let mut logs = TurnLogs::default();
        // A drop's flight on both panels: frames on DSI-2, none on DSI-1.
        logs.start("DSI-1", ms(100.0), 0.0);
        logs.start("DSI-2", ms(100.0), 0.0);
        logs.frame_queued("DSI-2", 2.0, 1.0, 0.0, false, true);
        assert!(logs.flipped("DSI-2", ms(108.0), 1).is_none());
        logs.frame_queued("DSI-2", 2.0, 1.0, 0.0, false, true);
        // Over, with every frame on the glass: summed up now, from the lift.
        assert!(logs.flipped("DSI-2", ms(119.1), 2).is_none());
        let (_, s) = logs.end("DSI-2").unwrap();
        assert_eq!(s.frames, 2);
        assert!((s.first_ms - 8.0).abs() < 1e-6, "{s:?}");
        // The panel that drew nothing of it: no line.
        assert!(logs.end("DSI-1").is_none());
        assert!(logs.logs.is_empty());
        // A frame still in flight at the end: its flip closes the log, and a
        // frame after the end is not one of the animation's.
        logs.start("DSI-1", ms(0.0), 0.0);
        logs.frame_queued("DSI-1", 1.0, 0.5, 0.0, false, true);
        assert!(logs.end("DSI-1").is_none());
        logs.frame_queued("DSI-1", 1.0, 0.5, 0.0, false, true);
        let (_, s) = logs.flipped("DSI-1", ms(20.0), 5).unwrap();
        assert_eq!(s.frames, 1);
        assert!(logs.end("DSI-1").is_none());
    }

    #[test]
    fn busy_frames_are_counted_apart() {
        let mut logs = TurnLogs::default();
        logs.start("DSI-2", ms(0.0), 0.0);
        logs.frame_queued("DSI-2", 1.0, 0.0, 0.0, true, true);
        logs.frame_queued("DSI-2", 1.0, 0.0, 0.0, false, false);
        let (log, s) = logs.flipped("DSI-2", ms(12.0), 3).unwrap();
        assert_eq!(log.busy, 1);
        assert_eq!(s.frames, 1);
        // A new start replaces a log left open.
        logs.start("DSI-1", ms(5.0), 3.0);
        logs.frame_queued("DSI-1", 1.0, 0.0, 0.0, false, true);
        logs.start("DSI-1", ms(9.0), 7.0);
        assert_eq!(logs.logs["DSI-1"].asked, ms(9.0));
        assert!(logs.logs["DSI-1"].render_ms.is_empty());
    }
}
