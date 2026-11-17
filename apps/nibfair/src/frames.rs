//! Frame timing: how a scene ran, where its slow frames spent their time.
//!
//! Every frame is split into its phases: the events (SDL's pump, the pen),
//! the work (the scene's update and draw; of it, `submit` is the vertex list
//! handed to SDL and the glyph atlas' upload) and the present (SDL_RenderPresent: the GL submission, the wait for the
//! compositor's frame callback, the buffer swap). A frame is the time from
//! one frame's start to the next one's. A scene's line in frames.log gives
//! the frame time's median, 95th / 99th percentile and maximum, each phase's
//! worst, and the slow frames (over 25 ms) by the phase that took most of
//! them; the slow frames themselves (with the wall clock, to set beside the
//! system log) go to slow.log.

/// A frame slower than this is slow (at 90 Hz: two refreshes missed).
pub const SLOW_MS: f32 = 25.0;

/// One frame's phases, ms.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Phases {
    pub events: f32,
    pub work: f32,
    /// The part of `work` in SDL: the atlas upload, the clear, SDL_RenderGeometry.
    pub submit: f32,
    pub present: f32,
}

impl Phases {
    /// The phase that took the most of the frame: "events", "work", "present",
    /// or "between" when none of them explains half of it (the time went
    /// elsewhere: the loop itself, the process not running).
    pub fn culprit(&self, frame: f32) -> &'static str {
        let (name, ms) = [
            ("events", self.events),
            ("work", self.work),
            ("present", self.present),
        ]
        .into_iter()
        .fold(
            ("between", 0.0f32),
            |best, (n, v)| if v > best.1 { (n, v) } else { best },
        );
        if ms >= frame * 0.5 {
            name
        } else {
            "between"
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Slow {
    /// When the frame ended (s since the scene started).
    pub at: f64,
    /// Wall clock (UNIX ms) at the frame's end.
    pub wall_ms: u64,
    pub frame: f32,
    pub phases: Phases,
}

#[derive(Clone, Debug, Default)]
pub struct FrameStats {
    /// Frame times, ms (the first frame of a scene has none).
    frames: Vec<f32>,
    pub time: f64,
    worst: Phases,
    work_total: f64,
    submit_total: f64,
    work_n: u32,
    pub slow: Vec<Slow>,
    pub verts: usize,
}

/// The p-th percentile (0..100) of sorted values, nearest rank; 0 when empty.
pub fn percentile(sorted: &[f32], p: f32) -> f32 {
    if sorted.is_empty() {
        return 0.0;
    }
    let rank = ((p / 100.0) * sorted.len() as f32).ceil() as usize;
    sorted[rank.clamp(1, sorted.len()) - 1]
}

impl FrameStats {
    /// One frame: `frame_ms` since the previous frame's start (None for the
    /// first), its phases, when it ended.
    pub fn frame(
        &mut self,
        frame_ms: Option<f32>,
        ph: Phases,
        at: f64,
        wall_ms: u64,
        verts: usize,
    ) {
        self.work_total += ph.work as f64;
        self.submit_total += ph.submit as f64;
        self.work_n += 1;
        self.worst.submit = self.worst.submit.max(ph.submit);
        self.worst.events = self.worst.events.max(ph.events);
        self.worst.work = self.worst.work.max(ph.work);
        self.worst.present = self.worst.present.max(ph.present);
        self.verts = self.verts.max(verts);
        if let Some(f) = frame_ms {
            self.frames.push(f);
            self.time += f as f64 / 1000.0;
            if f > SLOW_MS && self.slow.len() < 2000 {
                self.slow.push(Slow {
                    at,
                    wall_ms,
                    frame: f,
                    phases: ph,
                });
            }
        }
    }

    pub fn count(&self) -> usize {
        self.frames.len()
    }

    /// (p50, p95, p99, max) of the frame time, ms.
    pub fn spread(&self) -> (f32, f32, f32, f32) {
        let mut s = self.frames.clone();
        s.sort_by(|a, b| a.total_cmp(b));
        (
            percentile(&s, 50.0),
            percentile(&s, 95.0),
            percentile(&s, 99.0),
            s.last().copied().unwrap_or(0.0),
        )
    }

    /// The slow frames by the phase that took them: (events, work, present, between).
    pub fn slow_by_phase(&self) -> (u32, u32, u32, u32) {
        let mut c = (0, 0, 0, 0);
        for s in &self.slow {
            match s.phases.culprit(s.frame) {
                "events" => c.0 += 1,
                "work" => c.1 += 1,
                "present" => c.2 += 1,
                _ => c.3 += 1,
            }
        }
        c
    }

    pub fn line(&self) -> String {
        let (p50, p95, p99, max) = self.spread();
        let (e, w, p, b) = self.slow_by_phase();
        format!(
            "{:.1} fps over {:.1} s; frame p50 {:.1} p95 {:.1} p99 {:.1} max {:.1} ms; {} frames over {:.0} ms \
             (events {e}, work {w}, present {p}, between {b}); worst events {:.1} work {:.1} present {:.1} ms; \
             work {:.2} ms a frame (submit {:.2}, max {:.1}); up to {} vertices",
            self.count() as f64 / self.time.max(1e-6),
            self.time,
            p50,
            p95,
            p99,
            max,
            self.slow.len(),
            SLOW_MS,
            self.worst.events,
            self.worst.work,
            self.worst.present,
            self.work_total / self.work_n.max(1) as f64,
            self.submit_total / self.work_n.max(1) as f64,
            self.worst.submit,
            self.verts,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_by_nearest_rank() {
        let v: Vec<f32> = (1..=100).map(|k| k as f32).collect();
        assert_eq!(percentile(&v, 50.0), 50.0);
        assert_eq!(percentile(&v, 95.0), 95.0);
        assert_eq!(percentile(&v, 99.0), 99.0);
        assert_eq!(percentile(&v, 100.0), 100.0);
        assert_eq!(percentile(&[7.0], 99.0), 7.0);
        assert_eq!(percentile(&[], 50.0), 0.0);
    }

    #[test]
    fn slow_frames_are_put_down_to_their_phase() {
        let mut st = FrameStats::default();
        let ph = |e, w, p| Phases {
            events: e,
            work: w,
            submit: 0.0,
            present: p,
        };
        st.frame(None, ph(0.1, 2.0, 9.0), 0.0, 0, 10);
        for _ in 0..97 {
            st.frame(Some(11.1), ph(0.1, 2.0, 9.0), 0.0, 0, 10);
        }
        st.frame(Some(60.0), ph(0.2, 2.0, 55.0), 1.0, 0, 10);
        st.frame(Some(40.0), ph(0.2, 30.0, 9.0), 2.0, 0, 10);
        st.frame(Some(30.0), ph(0.2, 2.0, 9.0), 3.0, 0, 10);
        assert_eq!(st.count(), 100);
        assert_eq!(st.slow.len(), 3);
        assert_eq!(st.slow_by_phase(), (0, 1, 1, 1));
        let (p50, _, p99, max) = st.spread();
        assert_eq!((p50, p99, max), (11.1, 40.0, 60.0));
        assert!(st.line().contains("3 frames over 25 ms"), "{}", st.line());
    }
}
