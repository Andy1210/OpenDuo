//! The pond: fish, the bait, the bite and the line's tension.
//!
//! Units: `x` runs across the water (0 the near bank, 1 the far one),
//! `depth` from the surface (0) to the bottom (1). The scene maps them to
//! pixels and feeds the pen in: the hover's height on the screen is where
//! the bait hangs (or the pen's hover distance, on hardware that has one),
//! the tilt while the tip touches aims a cast, a tap hooks a biting fish, and
//! the pressure while hooked reels it in.

use crate::rng::Rng;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Kind {
    Minnow,
    Perch,
    Trout,
    Pike,
    Carp,
    Catfish,
    Eel,
    Goldfin,
    Boot,
}

pub struct KindInfo {
    pub kind: Kind,
    /// The strings key of its name.
    pub key: &'static str,
    pub depth: (f32, f32),
    pub size: (f32, f32),
    /// Points for the largest one (half for the smallest).
    pub points: f32,
    /// How hard it pulls, 0..1.
    pub strength: f32,
    /// Seconds of fight in it.
    pub stamina: f32,
    /// How often it surges, 0..1.
    pub erratic: f32,
    /// Swimming speed, water widths per second.
    pub speed: f32,
    /// How easily it is spooked by a moving bait, 0..1.
    pub wary: f32,
    /// How common it is.
    pub weight: f32,
    pub nibbles: (u32, u32),
    /// Seconds a bite lasts (the window to hook it).
    pub bite: f32,
    /// Body colour (RGB) and its fin / belly colour.
    pub color: (u8, u8, u8),
    pub accent: (u8, u8, u8),
}

pub const KINDS: [KindInfo; 9] = [
    KindInfo {
        kind: Kind::Minnow,
        key: "fish.minnow",
        depth: (0.04, 0.30),
        size: (4.0, 9.0),
        points: 12.0,
        strength: 0.18,
        stamina: 2.0,
        erratic: 0.6,
        speed: 0.16,
        wary: 0.8,
        weight: 3.0,
        nibbles: (1, 2),
        bite: 0.8,
        color: (150, 200, 220),
        accent: (230, 245, 250),
    },
    KindInfo {
        kind: Kind::Perch,
        key: "fish.perch",
        depth: (0.15, 0.50),
        size: (14.0, 32.0),
        points: 30.0,
        strength: 0.38,
        stamina: 4.0,
        erratic: 0.4,
        speed: 0.11,
        wary: 0.5,
        weight: 3.0,
        nibbles: (1, 3),
        bite: 0.85,
        color: (120, 170, 60),
        accent: (250, 140, 60),
    },
    KindInfo {
        kind: Kind::Trout,
        key: "fish.trout",
        depth: (0.30, 0.62),
        size: (24.0, 52.0),
        points: 45.0,
        strength: 0.52,
        stamina: 5.0,
        erratic: 0.5,
        speed: 0.12,
        wary: 0.6,
        weight: 2.4,
        nibbles: (2, 3),
        bite: 0.8,
        color: (170, 150, 190),
        accent: (240, 110, 140),
    },
    KindInfo {
        kind: Kind::Pike,
        key: "fish.pike",
        depth: (0.35, 0.72),
        size: (45.0, 95.0),
        points: 75.0,
        strength: 0.78,
        stamina: 6.0,
        erratic: 0.7,
        speed: 0.15,
        wary: 0.3,
        weight: 1.2,
        nibbles: (0, 1),
        bite: 0.65,
        color: (90, 130, 70),
        accent: (220, 230, 120),
    },
    KindInfo {
        kind: Kind::Carp,
        key: "fish.carp",
        depth: (0.55, 0.85),
        size: (30.0, 70.0),
        points: 55.0,
        strength: 0.62,
        stamina: 7.0,
        erratic: 0.2,
        speed: 0.07,
        wary: 0.7,
        weight: 2.0,
        nibbles: (2, 4),
        bite: 0.95,
        color: (190, 140, 70),
        accent: (240, 200, 120),
    },
    KindInfo {
        kind: Kind::Catfish,
        key: "fish.catfish",
        depth: (0.75, 0.97),
        size: (50.0, 130.0),
        points: 95.0,
        strength: 0.86,
        stamina: 8.0,
        erratic: 0.3,
        speed: 0.06,
        wary: 0.4,
        weight: 1.2,
        nibbles: (1, 3),
        bite: 0.9,
        color: (110, 100, 120),
        accent: (200, 190, 180),
    },
    KindInfo {
        kind: Kind::Eel,
        key: "fish.eel",
        depth: (0.70, 0.96),
        size: (40.0, 85.0),
        points: 85.0,
        strength: 0.66,
        stamina: 6.0,
        erratic: 1.0,
        speed: 0.10,
        wary: 0.5,
        weight: 1.0,
        nibbles: (1, 2),
        bite: 0.7,
        color: (80, 90, 60),
        accent: (170, 190, 90),
    },
    KindInfo {
        kind: Kind::Goldfin,
        key: "fish.goldfin",
        depth: (0.40, 0.80),
        size: (25.0, 55.0),
        points: 250.0,
        strength: 0.70,
        stamina: 6.0,
        erratic: 0.8,
        speed: 0.13,
        wary: 0.9,
        weight: 0.25,
        nibbles: (2, 3),
        bite: 0.6,
        color: (255, 190, 40),
        accent: (255, 240, 150),
    },
    KindInfo {
        kind: Kind::Boot,
        key: "fish.boot",
        depth: (0.93, 0.98),
        size: (38.0, 46.0),
        points: 6.0,
        strength: 0.05,
        stamina: 1.0,
        erratic: 0.0,
        speed: 0.0,
        wary: 0.0,
        weight: 0.35,
        nibbles: (0, 0),
        bite: 2.5,
        color: (120, 85, 60),
        accent: (60, 45, 35),
    },
];

pub fn info(kind: Kind) -> &'static KindInfo {
    KINDS
        .iter()
        .find(|k| k.kind == kind)
        .expect("every kind has its info")
}

/// The points for a catch: half at the smallest size, all at the largest.
pub fn points_for(kind: Kind, size: f32) -> u32 {
    let k = info(kind);
    let f = ((size - k.size.0) / (k.size.1 - k.size.0)).clamp(0.0, 1.0);
    (k.points * (0.5 + 0.5 * f)).round() as u32
}

// --- the line: reeling by pressure ----------------------------------------------------

/// Above this tension the line strains; held for SNAP_TIME it snaps.
pub const SNAP_TENSION: f32 = 1.0;
pub const SNAP_TIME: f32 = 0.45;
/// Below this the line is slack; for SLACK_TIME the fish slips the hook.
pub const SLACK_TENSION: f32 = 0.2;
pub const SLACK_TIME: f32 = 1.6;
/// The fish runs off with the line when this much of it is out (x the start).
pub const RUN_OFF: f32 = 1.3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReelOutcome {
    Fighting,
    Landed,
    Snapped,
    Escaped,
}

/// A hooked fish on the line. The tension is the angler's pressure (how hard
/// the reel is wound) plus the fish's pull; too much for too long snaps the
/// line, too little lets the fish shake the hook, and the fish takes line
/// back while nobody reels.
#[derive(Clone, Debug)]
pub struct Reel {
    /// How much line is out (1 is a fish at the far, deep end); 0 = landed.
    pub line: f32,
    pub line0: f32,
    pub tension: f32,
    /// Seconds over SNAP_TENSION (falls back while under it).
    pub strain: f32,
    /// Seconds under SLACK_TENSION.
    pub slack: f32,
    /// The fish's pull now, 0..~1.3.
    pub pull: f32,
    /// True while it surges (a run).
    pub surging: bool,
    pub stamina: f32,
    stamina0: f32,
    strength: f32,
    erratic: f32,
    /// A heavy catch (the boot) reels slowly.
    heavy: f32,
    phase: f32,
    surge_in: f32,
    surge_left: f32,
    rng: Rng,
}

impl Reel {
    pub fn new(kind: Kind, size: f32, line: f32, seed: u64) -> Reel {
        let k = info(kind);
        let big = ((size - k.size.0) / (k.size.1 - k.size.0)).clamp(0.0, 1.0);
        let mut rng = Rng::new(seed);
        let strength = k.strength * (0.85 + 0.3 * big);
        let surge_in = rng.range(0.6, 1.6);
        Reel {
            line,
            line0: line,
            tension: 0.4,
            strain: 0.0,
            slack: 0.0,
            pull: strength * 0.5,
            surging: false,
            stamina: k.stamina * (0.8 + 0.4 * big),
            stamina0: k.stamina * (0.8 + 0.4 * big),
            strength,
            erratic: k.erratic,
            heavy: if kind == Kind::Boot { 2.2 } else { 1.0 },
            phase: rng.range(0.0, std::f32::consts::TAU),
            surge_in,
            surge_left: 0.0,
            rng,
        }
    }

    /// The tension a pressure gives against a pull (before smoothing). Pure.
    pub fn tension_for(pressure: f32, pull: f32) -> f32 {
        0.72 * pressure.clamp(0.0, 1.0) + 0.55 * pull + 0.08
    }

    pub fn step(&mut self, dt: f32, pressure: f32) -> ReelOutcome {
        let p = pressure.clamp(0.0, 1.0);
        let tired = 0.45 + 0.55 * (self.stamina / self.stamina0).clamp(0.0, 1.0);
        // Runs: now and then the fish surges for half a second or so.
        if self.surge_left > 0.0 {
            self.surge_left -= dt;
        } else {
            self.surge_in -= dt;
            if self.surge_in <= 0.0 {
                self.surge_left = self.rng.range(0.5, 1.1);
                self.surge_in = self.rng.range(1.4, 3.6) * (1.0 - 0.45 * self.erratic);
            }
        }
        self.surging = self.surge_left > 0.0;
        self.phase += dt * (1.3 + 2.5 * self.erratic);
        let wave = 0.5 + 0.5 * self.phase.sin();
        let surge = if self.surging { 1.0 } else { 0.0 };
        let target = self.strength * tired * (0.45 + 0.35 * wave + 0.55 * surge);
        self.pull = crate::tween::approach(self.pull, target, 6.0, dt);
        self.tension =
            crate::tween::approach(self.tension, Self::tension_for(p, self.pull), 12.0, dt);

        if self.tension > SNAP_TENSION {
            self.strain += dt;
        } else {
            self.strain = (self.strain - 0.8 * dt).max(0.0);
        }
        if self.tension < SLACK_TENSION {
            self.slack += dt;
        } else {
            self.slack = (self.slack - 1.5 * dt).max(0.0);
        }
        self.line -= dt * 0.32 * p * (1.0 - 0.65 * self.pull.min(1.0)) / self.heavy;
        self.line += dt * 0.12 * self.pull * (1.0 - p);
        self.stamina = (self.stamina - dt * (0.6 + self.tension)).max(0.0);

        if self.strain > SNAP_TIME {
            ReelOutcome::Snapped
        } else if self.line <= 0.0 {
            self.line = 0.0;
            ReelOutcome::Landed
        } else if self.slack > SLACK_TIME || self.line > self.line0 * RUN_OFF {
            ReelOutcome::Escaped
        } else {
            ReelOutcome::Fighting
        }
    }
}

// --- the fish and the bait ----------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum FishState {
    Wander {
        turn_in: f32,
    },
    /// Swimming up to the bait.
    Curious,
    /// At the bait, tugging at it `left` more times.
    Nibble {
        left: u32,
        t: f32,
    },
    /// It took the bait: tap within `t` s.
    Bite {
        t: f32,
    },
    /// Off and away (spooked, or it stole the bait), wandering again after `t`.
    Flee {
        t: f32,
    },
    Hooked,
}

#[derive(Clone, Debug)]
pub struct Fish {
    pub id: u32,
    pub kind: Kind,
    pub size: f32,
    pub x: f32,
    pub depth: f32,
    pub home: f32,
    pub vx: f32,
    /// +1 facing the far bank, -1 the near one.
    pub dir: f32,
    pub state: FishState,
    /// A clock for the tail's wiggle and the drift.
    pub wiggle: f32,
    /// 0..1 while it fades in (spawned at an edge).
    pub shown: f32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bait {
    pub x: f32,
    pub depth: f32,
    pub target: f32,
    /// How fast it moves now (depth units per s, smoothed): fish shy away from a jerky bait.
    pub speed: f32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Catch {
    pub kind: Kind,
    pub size: f32,
    pub points: u32,
}

#[derive(Clone, Debug)]
pub enum Phase {
    /// No bait in the water: touch to aim a cast.
    Ready,
    /// The tip is down: the tilt aims (`land`), lifting casts.
    Aim { land: f32, held: f32 },
    /// The bait in the air.
    Fly { t: f32, to: f32 },
    /// The bait in the water.
    Fishing,
    /// A fish on the line.
    Fight {
        fish: u32,
        reel: Reel,
        from: (f32, f32),
    },
    /// The catch shown for a moment.
    Landed { t: f32, catch: Catch },
}

pub const FLY_TIME: f32 = 0.55;
/// The tilt (degrees, since the touch) that swings a cast across the whole water.
pub const AIM_SWING: f32 = 45.0;

/// Where a cast lands (0..1 across the water): the pen's place across the
/// water (0..1) moved by its tilt since the tip touched (degrees, + to the
/// right, away from the angler).
pub fn aim(x: f32, tilt_since_touch: f32) -> f32 {
    (x + tilt_since_touch / AIM_SWING).clamp(0.04, 0.98)
}
/// Holding the tip this long while fishing starts a new cast (a tap does nothing).
pub const RECAST_HOLD: f32 = 0.22;
pub const ROUND_TIME: f32 = 150.0;

#[derive(Clone, Copy, Debug, Default)]
pub struct PondInput {
    /// Where the pen says the bait should hang (0..1), while it is in range.
    pub depth: Option<f32>,
    pub tip: bool,
    pub tip_pressed: bool,
    pub tip_released: bool,
    pub pressure: f32,
    /// Where a cast would land now (0..1 across the water), from the tilt.
    pub aim: f32,
    /// The barrel button: wind the bait in.
    pub reel_in: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub enum PondEvent {
    Cast,
    Splash {
        x: f32,
    },
    Nibble {
        strength: f32,
    },
    Bite,
    TooEarly,
    /// The bite passed: the fish took the bait.
    Stolen,
    Spooked,
    Hooked {
        kind: Kind,
    },
    Surge,
    /// Line tension crossed into the red.
    Strain,
    /// A click of the reel (every bit of line wound in).
    ReelTick {
        tension: f32,
    },
    Snapped,
    Escaped,
    Caught(Catch),
    TimeUp,
}

pub struct Pond {
    pub fish: Vec<Fish>,
    pub bait: Option<Bait>,
    pub phase: Phase,
    pub score: u32,
    pub log: Vec<Catch>,
    pub time_left: f32,
    pub over: bool,
    /// The one fish that has noticed the bait.
    pub engaged: Option<u32>,
    pub population: usize,
    rng: Rng,
    next_id: u32,
    spawn_in: f32,
    tick_line: f32,
    strained: bool,
    hold: f32,
    seed: u64,
}

impl Pond {
    pub fn new(seed: u64) -> Pond {
        let mut p = Pond {
            fish: Vec::new(),
            bait: None,
            phase: Phase::Ready,
            score: 0,
            log: Vec::new(),
            time_left: ROUND_TIME,
            over: false,
            engaged: None,
            population: 7,
            rng: Rng::new(seed),
            next_id: 1,
            spawn_in: 0.0,
            tick_line: 0.0,
            strained: false,
            hold: 0.0,
            seed,
        };
        for _ in 0..p.population {
            p.spawn(false);
        }
        p
    }

    fn spawn(&mut self, at_edge: bool) {
        let weights: Vec<f32> = KINDS.iter().map(|k| k.weight).collect();
        let k = &KINDS[self.rng.weighted(&weights)];
        let size = self.rng.range(k.size.0, k.size.1);
        let home = self.rng.range(k.depth.0, k.depth.1);
        let dir = if self.rng.chance(0.5) { 1.0 } else { -1.0 };
        let x = if k.kind == Kind::Boot {
            self.rng.range(0.25, 0.9)
        } else if at_edge {
            if dir > 0.0 {
                -0.04
            } else {
                1.04
            }
        } else {
            self.rng.range(0.1, 0.9)
        };
        let id = self.next_id;
        self.next_id += 1;
        let turn_in = self.rng.range(2.0, 6.0);
        self.fish.push(Fish {
            id,
            kind: k.kind,
            size,
            x,
            depth: home,
            home,
            vx: dir * k.speed * self.rng.range(0.6, 1.0),
            dir,
            state: FishState::Wander { turn_in },
            wiggle: self.rng.range(0.0, 6.0),
            shown: if at_edge { 0.0 } else { 1.0 },
        });
    }

    pub fn fish_mut(&mut self, id: u32) -> Option<&mut Fish> {
        self.fish.iter_mut().find(|f| f.id == id)
    }

    pub fn fish_by_id(&self, id: u32) -> Option<&Fish> {
        self.fish.iter().find(|f| f.id == id)
    }

    /// The line out for a fish hooked at (x, depth): the far, deep ones are a longer fight.
    pub fn line_for(x: f32, depth: f32) -> f32 {
        0.35 + 0.35 * x.clamp(0.0, 1.0) + 0.3 * depth.clamp(0.0, 1.0)
    }

    pub fn update(&mut self, dt: f32, inp: &PondInput) -> Vec<PondEvent> {
        let mut ev = Vec::new();
        let fighting = matches!(self.phase, Phase::Fight { .. });
        if !self.over {
            self.time_left -= dt;
            if self.time_left <= 0.0 && !fighting {
                self.time_left = 0.0;
                self.over = true;
                ev.push(PondEvent::TimeUp);
            }
        }
        self.update_phase(dt, inp, &mut ev);
        self.update_fish(dt, inp, &mut ev);
        // Keep the pond stocked.
        let free = self
            .fish
            .iter()
            .filter(|f| f.state != FishState::Hooked)
            .count();
        if free < self.population {
            self.spawn_in -= dt;
            if self.spawn_in <= 0.0 {
                self.spawn(true);
                self.spawn_in = self.rng.range(1.5, 4.0);
            }
        }
        ev
    }

    fn update_phase(&mut self, dt: f32, inp: &PondInput, ev: &mut Vec<PondEvent>) {
        let phase = std::mem::replace(&mut self.phase, Phase::Ready);
        self.phase = match phase {
            Phase::Ready => {
                if inp.tip_pressed && !self.over {
                    Phase::Aim {
                        land: inp.aim,
                        held: 0.0,
                    }
                } else {
                    Phase::Ready
                }
            }
            Phase::Aim { land, held } => {
                if inp.tip_released || !inp.tip {
                    if self.over {
                        Phase::Ready
                    } else {
                        // Where the arc showed it, the last frame the tip touched: in the frame of
                        // the lift the pen has no tilt any more (the digitizer gives one only
                        // while touching) and has moved off as it rose.
                        ev.push(PondEvent::Cast);
                        Phase::Fly { t: 0.0, to: land }
                    }
                } else {
                    Phase::Aim {
                        land: inp.aim.clamp(0.04, 0.98),
                        held: held + dt,
                    }
                }
            }
            Phase::Fly { t, to } => {
                let t = t + dt;
                if t >= FLY_TIME {
                    ev.push(PondEvent::Splash { x: to });
                    let target = inp.depth.unwrap_or(0.2);
                    self.bait = Some(Bait {
                        x: to,
                        depth: 0.0,
                        target,
                        speed: 0.0,
                    });
                    Phase::Fishing
                } else {
                    Phase::Fly { t, to }
                }
            }
            Phase::Fishing => self.fishing(dt, inp, ev),
            Phase::Fight {
                fish,
                mut reel,
                from,
            } => {
                let before = reel.line;
                let was_surging = reel.surging;
                let out = reel.step(dt, if inp.tip { inp.pressure } else { 0.0 });
                if reel.surging && !was_surging {
                    ev.push(PondEvent::Surge);
                }
                let strained = reel.tension > SNAP_TENSION * 0.92;
                if strained && !self.strained {
                    ev.push(PondEvent::Strain);
                }
                self.strained = strained;
                if before > reel.line {
                    self.tick_line += before - reel.line;
                    if self.tick_line > 0.045 {
                        self.tick_line = 0.0;
                        ev.push(PondEvent::ReelTick {
                            tension: reel.tension,
                        });
                    }
                }
                // The fish follows the line in towards the angler (near bank, surface).
                let f = (reel.line / reel.line0).clamp(0.0, RUN_OFF);
                if let Some(fsh) = self.fish_mut(fish) {
                    fsh.x = from.0 * f;
                    fsh.depth = (from.1 * f).min(0.98);
                    fsh.dir = -1.0;
                }
                match out {
                    ReelOutcome::Fighting => Phase::Fight { fish, reel, from },
                    ReelOutcome::Landed => {
                        let c = self.fish_by_id(fish).map(|f| (f.kind, f.size));
                        self.fish.retain(|f| f.id != fish);
                        self.engaged = None;
                        self.bait = None;
                        match c {
                            Some((kind, size)) => {
                                let catch = Catch {
                                    kind,
                                    size,
                                    points: points_for(kind, size),
                                };
                                self.score += catch.points;
                                self.log.push(catch);
                                ev.push(PondEvent::Caught(catch));
                                Phase::Landed { t: 0.0, catch }
                            }
                            None => Phase::Ready,
                        }
                    }
                    ReelOutcome::Snapped | ReelOutcome::Escaped => {
                        ev.push(if out == ReelOutcome::Snapped {
                            PondEvent::Snapped
                        } else {
                            PondEvent::Escaped
                        });
                        let t = self.rng.range(2.0, 3.0);
                        if let Some(fsh) = self.fish_mut(fish) {
                            fsh.state = FishState::Flee { t };
                            fsh.dir = 1.0;
                        }
                        self.engaged = None;
                        self.bait = None;
                        Phase::Ready
                    }
                }
            }
            Phase::Landed { t, catch } => {
                let t = t + dt;
                if t > 1.6 || (t > 0.4 && inp.tip_pressed) {
                    Phase::Ready
                } else {
                    Phase::Landed { t, catch }
                }
            }
        };
    }

    fn fishing(&mut self, dt: f32, inp: &PondInput, ev: &mut Vec<PondEvent>) -> Phase {
        let Some(mut bait) = self.bait else {
            return Phase::Ready;
        };
        if inp.reel_in || self.over {
            self.bait = None;
            self.release_engaged(false);
            return Phase::Ready;
        }
        // The bait hangs where the pen says (sinks slower than it rises).
        if let Some(d) = inp.depth {
            bait.target = d.clamp(0.0, 0.98);
        }
        let before = bait.depth;
        let diff = bait.target - bait.depth;
        let rate = if diff > 0.0 { 0.38 } else { 0.6 };
        bait.depth += diff.signum() * (rate * dt).min(diff.abs());
        let v = (bait.depth - before).abs() / dt.max(1e-4);
        bait.speed = crate::tween::approach(bait.speed, v, 8.0, dt);
        self.bait = Some(bait);

        // A fish at the bait: a tap is the strike.
        let engaged = self.engaged.and_then(|id| {
            self.fish_by_id(id)
                .map(|f| (id, f.state, f.x, f.depth, f.kind))
        });
        if let Some((id, state, x, depth, kind)) = engaged {
            if inp.tip_pressed {
                match state {
                    FishState::Bite { .. } => {
                        if let Some(f) = self.fish_mut(id) {
                            f.state = FishState::Hooked;
                        }
                        ev.push(PondEvent::Hooked { kind });
                        self.strained = false;
                        self.tick_line = 0.0;
                        let seed = self.seed ^ (id as u64) << 17 ^ self.rng.next_u32() as u64;
                        let size = self.fish_by_id(id).map(|f| f.size).unwrap_or(10.0);
                        return Phase::Fight {
                            fish: id,
                            reel: Reel::new(kind, size, Self::line_for(x, depth), seed),
                            from: (x, depth),
                        };
                    }
                    FishState::Curious | FishState::Nibble { .. } => {
                        ev.push(PondEvent::TooEarly);
                        self.release_engaged(true);
                    }
                    _ => {}
                }
            }
        }
        // Holding the tip down (not a tap): the bait comes out for a new cast.
        if inp.tip {
            self.hold += dt;
            if self.hold >= RECAST_HOLD && self.engaged.is_none() {
                self.hold = 0.0;
                self.bait = None;
                return Phase::Aim {
                    land: inp.aim,
                    held: RECAST_HOLD,
                };
            }
        } else {
            self.hold = 0.0;
        }
        Phase::Fishing
    }

    /// The engaged fish lets go (spooked: it darts away).
    fn release_engaged(&mut self, spooked: bool) {
        if let Some(id) = self.engaged.take() {
            let t = self.rng.range(2.0, 4.0);
            if let Some(f) = self.fish_mut(id) {
                if f.state != FishState::Hooked {
                    f.state = if spooked {
                        FishState::Flee { t }
                    } else {
                        FishState::Wander { turn_in: 1.0 }
                    };
                    if spooked {
                        f.dir = -f.dir;
                        f.vx = -f.vx;
                    }
                }
            }
        }
    }

    fn update_fish(&mut self, dt: f32, _inp: &PondInput, ev: &mut Vec<PondEvent>) {
        let bait = self.bait.filter(|_| matches!(self.phase, Phase::Fishing));
        // Someone new notices a still bait.
        if let (Some(b), None) = (bait, self.engaged) {
            let mut best: Option<(u32, f32)> = None;
            for f in &self.fish {
                if !matches!(f.state, FishState::Wander { .. }) || f.shown < 1.0 {
                    continue;
                }
                let k = info(f.kind);
                let still = b.speed < 0.35 * (1.0 - 0.5 * k.wary);
                let dx = (f.x - b.x).abs();
                let dd = (f.depth - b.depth).abs();
                let reach = if f.kind == Kind::Boot { 0.05 } else { 0.18 };
                if still && dx < reach && dd < if f.kind == Kind::Boot { 0.05 } else { 0.11 } {
                    let d = dx + dd * 1.5;
                    if best.is_none_or(|(_, bd)| d < bd) {
                        best = Some((f.id, d));
                    }
                }
            }
            if let Some((id, _)) = best {
                self.engaged = Some(id);
                if let Some(f) = self.fish_mut(id) {
                    f.state = FishState::Curious;
                }
            }
        }

        let mut spooked = false;
        let mut stolen = false;
        let n = self.fish.len();
        for i in 0..n {
            let id = self.fish[i].id;
            let k = info(self.fish[i].kind);
            let mut f = self.fish[i].clone();
            f.wiggle += dt * (2.0 + 6.0 * k.speed / 0.16);
            f.shown = (f.shown + dt * 1.5).min(1.0);
            match f.state {
                FishState::Wander { turn_in } => {
                    let mut turn_in = turn_in - dt;
                    if k.speed > 0.0 {
                        f.x += f.vx * dt;
                        if (f.x < 0.03 && f.vx < 0.0)
                            || (f.x > 0.97 && f.vx > 0.0)
                            || turn_in <= 0.0
                        {
                            f.vx = -f.vx;
                            turn_in = self.rng.range(3.0, 8.0);
                        }
                        f.dir = f.vx.signum();
                        let drift = (f.wiggle * 0.13).sin() * 0.04;
                        let target = (f.home + drift).clamp(k.depth.0, k.depth.1);
                        f.depth = crate::tween::approach(f.depth, target, 1.0, dt);
                    }
                    f.state = FishState::Wander { turn_in };
                }
                FishState::Curious => {
                    if let Some(b) = bait {
                        if b.speed > 0.45 * (1.0 - 0.5 * k.wary) + 0.1 {
                            spooked = true;
                        } else {
                            // It faces the bait (decided with some slack, or it would
                            // flip back and forth right under it).
                            let side = b.x - f.x;
                            if side.abs() > 0.045 {
                                f.dir = side.signum();
                            }
                            let mouth = b.x - f.dir * 0.035;
                            let dx = mouth - f.x;
                            let dd = b.depth - f.depth;
                            let dist = (dx * dx + dd * dd).sqrt();
                            let sp = (k.speed.max(0.05) * 0.8 * dt).min(dist);
                            if dist > 1e-4 {
                                f.x += dx / dist * sp;
                                f.depth += dd / dist * sp;
                            }
                            if dist < 0.02 {
                                let (lo, hi) = k.nibbles;
                                let left = lo + self.rng.below((hi - lo + 1) as usize) as u32;
                                f.state = FishState::Nibble {
                                    left,
                                    t: self.rng.range(0.5, 1.0),
                                };
                            }
                        }
                    } else {
                        f.state = FishState::Wander { turn_in: 1.0 };
                    }
                }
                FishState::Nibble { left, t } => {
                    if let Some(b) = bait {
                        f.x = crate::tween::approach(f.x, b.x - f.dir * 0.035, 6.0, dt);
                        f.depth = crate::tween::approach(f.depth, b.depth, 6.0, dt);
                        if b.speed > 0.5 * (1.0 - 0.5 * k.wary) + 0.12 {
                            spooked = true;
                        }
                    }
                    let t = t - dt;
                    f.state = if t > 0.0 {
                        FishState::Nibble { left, t }
                    } else if left > 0 {
                        ev.push(PondEvent::Nibble {
                            strength: 0.3 + 0.4 * k.strength,
                        });
                        FishState::Nibble {
                            left: left - 1,
                            t: self.rng.range(0.45, 0.9),
                        }
                    } else {
                        ev.push(PondEvent::Bite);
                        FishState::Bite { t: k.bite }
                    };
                }
                FishState::Bite { t } => {
                    if let Some(b) = bait {
                        f.x = crate::tween::approach(f.x, b.x - f.dir * 0.03, 8.0, dt);
                        f.depth = crate::tween::approach(f.depth, b.depth + 0.01, 8.0, dt);
                    }
                    let t = t - dt;
                    if t > 0.0 {
                        f.state = FishState::Bite { t };
                    } else {
                        stolen = true;
                    }
                }
                FishState::Flee { t } => {
                    let sp = k.speed.max(0.05) * 2.2;
                    f.x += f.dir * sp * dt;
                    let t = t - dt;
                    f.state = if t > 0.0 {
                        FishState::Flee { t }
                    } else {
                        FishState::Wander { turn_in: 2.0 }
                    };
                    f.vx = f.dir * k.speed;
                }
                FishState::Hooked => {}
            }
            self.fish[i] = f;
            let _ = id;
        }
        if spooked {
            ev.push(PondEvent::Spooked);
            self.release_engaged(true);
        } else if stolen {
            ev.push(PondEvent::Stolen);
            self.release_engaged(true);
            self.bait = None;
            if matches!(self.phase, Phase::Fishing) {
                self.phase = Phase::Ready;
            }
        }
        // Fish that swam off the sides are gone (others come).
        self.fish
            .retain(|f| f.state == FishState::Hooked || (-0.1..=1.1).contains(&f.x));
        if let Some(id) = self.engaged {
            if self.fish_by_id(id).is_none() {
                self.engaged = None;
            }
        }
    }

    /// The fish on the line now, and its reel.
    pub fn fight(&self) -> Option<(&Fish, &Reel)> {
        match &self.phase {
            Phase::Fight { fish, reel, .. } => self.fish_by_id(*fish).map(|f| (f, reel)),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reel_with(
        kind: Kind,
        size: f32,
        pressure: impl Fn(&Reel, f32) -> f32,
    ) -> (ReelOutcome, f32) {
        let mut r = Reel::new(kind, size, 0.8, 42);
        let dt = 1.0 / 60.0;
        let mut t = 0.0;
        while t < 60.0 {
            let p = pressure(&r, t);
            let out = r.step(dt, p);
            t += dt;
            if out != ReelOutcome::Fighting {
                return (out, t);
            }
        }
        (ReelOutcome::Fighting, t)
    }

    #[test]
    fn a_steady_moderate_pressure_lands_a_medium_fish() {
        let (out, t) = reel_with(Kind::Perch, 25.0, |_, _| 0.55);
        assert_eq!(out, ReelOutcome::Landed, "after {t} s");
        assert!(t < 20.0, "{t}");
    }

    #[test]
    fn too_hard_snaps_the_line_of_a_strong_fish() {
        let (out, _) = reel_with(Kind::Catfish, 120.0, |_, _| 1.0);
        assert_eq!(out, ReelOutcome::Snapped);
    }

    #[test]
    fn no_pressure_loses_every_fish() {
        for k in KINDS.iter().filter(|k| k.kind != Kind::Boot) {
            let (out, t) = reel_with(k.kind, k.size.1, |_, _| 0.0);
            assert_eq!(out, ReelOutcome::Escaped, "{:?} after {t} s", k.kind);
            assert!(t < 10.0, "{:?} {t}", k.kind);
        }
    }

    #[test]
    fn easing_off_in_the_runs_lands_even_the_strongest() {
        // A player who watches the tension: firm, lighter while it surges or nears the red.
        for k in KINDS.iter() {
            let (out, t) = reel_with(k.kind, k.size.1, |r, _| {
                if r.tension > 0.88 || r.surging {
                    0.3
                } else if r.tension < 0.35 {
                    0.7
                } else {
                    0.55
                }
            });
            assert_eq!(out, ReelOutcome::Landed, "{:?} after {t} s", k.kind);
        }
    }

    #[test]
    fn tension_rises_with_pressure_and_pull() {
        assert!(Reel::tension_for(0.8, 0.3) > Reel::tension_for(0.4, 0.3));
        assert!(Reel::tension_for(0.4, 0.8) > Reel::tension_for(0.4, 0.3));
        assert!(
            Reel::tension_for(0.0, 0.1) < SLACK_TENSION,
            "no pressure, a weak fish: slack"
        );
    }

    #[test]
    fn points_grow_with_size() {
        assert_eq!(points_for(Kind::Goldfin, 55.0), 250);
        assert_eq!(points_for(Kind::Goldfin, 25.0), 125);
        assert!(points_for(Kind::Pike, 90.0) > points_for(Kind::Pike, 50.0));
    }

    /// Drive the pond: cast to `x`, hold the bait at `depth`, and do what `act` says.
    fn fish_until(
        pond: &mut Pond,
        x: f32,
        depth: f32,
        secs: f32,
        mut act: impl FnMut(&PondEvent) -> bool,
    ) -> Vec<PondEvent> {
        let dt = 1.0 / 60.0;
        let mut all = Vec::new();
        let mut inp = PondInput {
            aim: x,
            depth: Some(depth),
            tip: true,
            tip_pressed: true,
            ..Default::default()
        };
        all.extend(pond.update(dt, &inp));
        inp.tip_pressed = false;
        inp.tip = false;
        inp.tip_released = true;
        all.extend(pond.update(dt, &inp));
        inp.tip_released = false;
        let mut t = 0.0;
        let mut tap = false;
        while t < secs {
            inp.tip_pressed = tap;
            inp.tip = tap;
            tap = false;
            let ev = pond.update(dt, &inp);
            for e in &ev {
                if act(e) {
                    tap = true;
                }
            }
            all.extend(ev);
            t += dt;
        }
        all
    }

    #[test]
    fn a_cast_lands_where_aimed_and_the_bait_sinks_to_the_pen() {
        let mut pond = Pond::new(1);
        pond.fish.clear();
        pond.population = 0;
        let ev = fish_until(&mut pond, 0.6, 0.5, 3.0, |_| false);
        assert!(ev.contains(&PondEvent::Cast));
        assert!(ev.contains(&PondEvent::Splash { x: 0.6 }));
        let b = pond.bait.expect("the bait is in the water");
        assert!((b.x - 0.6).abs() < 1e-6);
        assert!((b.depth - 0.5).abs() < 0.01, "{}", b.depth);
    }

    #[test]
    fn the_cast_goes_where_the_arc_showed_not_where_the_lift_points() {
        let mut pond = Pond::new(1);
        pond.fish.clear();
        pond.population = 0;
        let dt = 1.0 / 60.0;
        // The tip touches near the bank (0.2) with the pen held at 20 degrees, then leans
        // 25 degrees further right: the arc shows a far cast.
        let x = 0.2;
        let t0 = 20.0;
        let mut inp = PondInput {
            aim: aim(x, 0.0),
            depth: Some(0.3),
            tip: true,
            tip_pressed: true,
            ..Default::default()
        };
        pond.update(dt, &inp);
        inp.tip_pressed = false;
        for k in 1..=30 {
            let tilt = t0 + 25.0 * k as f32 / 30.0;
            inp.aim = aim(x, tilt - t0);
            pond.update(dt, &inp);
        }
        let shown = match pond.phase {
            Phase::Aim { land, .. } => land,
            ref p => panic!("aiming: {p:?}"),
        };
        assert!(
            (shown - aim(x, 25.0)).abs() < 1e-6 && shown > 0.7,
            "the arc shows {shown}"
        );
        // The lift: the tilt reads 0 (none while not touching), and the pen has drifted left.
        inp.tip = false;
        inp.tip_released = true;
        inp.aim = aim(x - 0.05, 0.0 - t0);
        let ev = pond.update(dt, &inp);
        assert!(ev.contains(&PondEvent::Cast));
        match pond.phase {
            Phase::Fly { to, .. } => assert_eq!(to, shown, "lands where the arc showed"),
            ref p => panic!("flying: {p:?}"),
        }
        inp.tip_released = false;
        let mut splash = None;
        for _ in 0..60 {
            for e in pond.update(dt, &inp) {
                if let PondEvent::Splash { x } = e {
                    splash = Some(x);
                }
            }
        }
        assert_eq!(splash, Some(shown));
    }

    #[test]
    fn aim_is_the_place_moved_by_the_tilt() {
        assert_eq!(aim(0.5, 0.0), 0.5);
        assert!((aim(0.2, AIM_SWING / 2.0) - 0.7).abs() < 1e-6);
        assert_eq!(aim(0.5, 90.0), 0.98);
        assert_eq!(aim(0.5, -90.0), 0.04);
    }

    #[test]
    fn a_still_bait_near_a_fish_gets_a_bite_and_a_tap_hooks_it() {
        let mut pond = Pond::new(5);
        pond.fish.clear();
        pond.population = 0;
        pond.spawn(false);
        let (x, d) = {
            let f = &mut pond.fish[0];
            f.kind = Kind::Perch;
            f.vx = 0.0;
            f.x = 0.5;
            f.home = 0.3;
            f.depth = 0.3;
            (f.x, f.depth)
        };
        let ev = fish_until(&mut pond, x + 0.05, d, 8.0, |e| *e == PondEvent::Bite);
        assert!(ev.contains(&PondEvent::Bite), "{ev:?}");
        assert!(
            ev.iter()
                .any(|e| matches!(e, PondEvent::Hooked { kind: Kind::Perch })),
            "{ev:?}"
        );
        assert!(pond.fight().is_some() || ev.iter().any(|e| matches!(e, PondEvent::Escaped)));
    }

    #[test]
    fn a_tap_before_the_bite_spooks_the_fish() {
        let mut pond = Pond::new(9);
        pond.fish.clear();
        pond.population = 0;
        pond.spawn(false);
        {
            let f = &mut pond.fish[0];
            f.kind = Kind::Carp; // 2..4 nibbles before it bites
            f.vx = 0.0;
            f.x = 0.4;
            f.home = 0.6;
            f.depth = 0.6;
        }
        let ev = fish_until(&mut pond, 0.43, 0.6, 8.0, |e| {
            matches!(e, PondEvent::Nibble { .. })
        });
        assert!(ev.contains(&PondEvent::TooEarly), "{ev:?}");
        assert!(!ev.contains(&PondEvent::Bite), "{ev:?}");
    }

    #[test]
    fn an_unanswered_bite_steals_the_bait() {
        let mut pond = Pond::new(11);
        pond.fish.clear();
        pond.population = 0;
        pond.spawn(false);
        {
            let f = &mut pond.fish[0];
            f.kind = Kind::Pike;
            f.vx = 0.0;
            f.x = 0.5;
            f.home = 0.5;
            f.depth = 0.5;
        }
        let ev = fish_until(&mut pond, 0.52, 0.5, 8.0, |_| false);
        assert!(ev.contains(&PondEvent::Bite), "{ev:?}");
        assert!(ev.contains(&PondEvent::Stolen), "{ev:?}");
        assert!(pond.bait.is_none());
    }

    #[test]
    fn the_round_ends_and_the_pond_restocks() {
        let mut pond = Pond::new(3);
        let n = pond.fish.len();
        assert_eq!(n, pond.population);
        let inp = PondInput::default();
        let mut over = false;
        for _ in 0..(ROUND_TIME * 60.0) as usize + 10 {
            if pond.update(1.0 / 60.0, &inp).contains(&PondEvent::TimeUp) {
                over = true;
            }
        }
        assert!(over && pond.over);
        assert!(
            pond.fish.len() >= pond.population - 2,
            "{}",
            pond.fish.len()
        );
    }
}
