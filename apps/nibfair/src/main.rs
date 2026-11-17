//! Nibfair: three games that show off the Surface Slim Pen 2 on the
//! Surface Duo 2 (duo2-os): fishing (the hovering pen is the rod), slicing
//! (strokes and pressure, the eraser end a shield) and pottery (tilt and
//! pressure shape a spinning pot). SDL 3 for the window, the pen and the
//! GPU renderer; everything drawn is vector shapes made here (gfx.rs), the
//! text in the system's sans face (text.rs). The rules are in the library
//! (src/lib.rs), tested without a window.
//!
//!   nibfair                 play
//!   nibfair --bench SECS    run each scene SECS seconds unattended, print the frame rate
//!   nibfair --shots DIR     render every screen to DIR/*.bmp and quit (any video driver,
//!                           SDL_VIDEO_DRIVER=offscreen SDL_RENDER_DRIVER=software headless)

mod buzz;
mod gfx;
mod input;
mod menu;
mod pond;
mod range;
mod text;
mod ui;
mod wheel;

use gfx::Canvas;
use input::{Input, Tablet};
use nibfair::frames::{FrameStats, Phases};
use nibfair::save::Save;
use nibfair::strings::{self, Lang};
use sdl3_sys::everything::*;
use std::ffi::{CStr, CString};
use std::path::PathBuf;
use text::Font;

pub const APP_ID: &str = "org.duo2os.Nibfair";
/// The size the screens are designed for, in points: one panel of the Duo 2.
pub const DESIGN_W: f32 = 538.0;
pub const DESIGN_H: f32 = 757.0;

/// What every scene works with.
pub struct Ctx {
    pub cv: Canvas,
    pub font: Font,
    pub lang: Lang,
    pub save: Save,
    pub save_path: PathBuf,
    pub buzz: buzz::Buzz,
    /// The window in logical points.
    pub w: f32,
    pub h: f32,
    /// Seconds since the start (SDL's clock).
    pub now: f64,
    /// The pen comes from the Wayland tablet (pentools.c): its two ends told apart.
    pub pen_tablet: bool,
    pub seed: u64,
}

impl Ctx {
    pub fn t(&self, key: &str) -> &'static str {
        strings::t(self.lang, key)
    }
    pub fn tf(&self, key: &str, args: &[&dyn std::fmt::Display]) -> String {
        strings::tf(self.lang, key, args)
    }
    pub fn store(&mut self) {
        if std::env::var_os("NIBFAIR_NO_SAVE").is_none() {
            if let Err(e) = self.save.store(&self.save_path) {
                eprintln!("nibfair: saving {}: {e}", self.save_path.display());
            }
        }
    }
    /// Landscape (the two panels spanned, or a wide window).
    pub fn wide(&self) -> bool {
        self.w > self.h * 1.15
    }
    pub fn next_seed(&mut self) -> u64 {
        self.seed = self
            .seed
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.seed >> 11
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Go {
    Stay,
    Menu,
    Fishing,
    Slicing,
    Pottery,
    Quit,
}

enum Scene {
    Menu(menu::Menu),
    Fish(pond::FishScene),
    Slice(range::SliceScene),
    Pot(wheel::PotScene),
}

impl Scene {
    fn open(go: Go, cx: &mut Ctx) -> Scene {
        match go {
            Go::Fishing => Scene::Fish(pond::FishScene::new(cx)),
            Go::Slicing => Scene::Slice(range::SliceScene::new(cx)),
            Go::Pottery => Scene::Pot(wheel::PotScene::new(cx)),
            _ => Scene::Menu(menu::Menu::new(cx)),
        }
    }
    fn update(&mut self, cx: &mut Ctx, inp: &Input, dt: f32) -> Go {
        match self {
            Scene::Menu(s) => s.update(cx, inp, dt),
            Scene::Fish(s) => s.update(cx, inp, dt),
            Scene::Slice(s) => s.update(cx, inp, dt),
            Scene::Pot(s) => s.update(cx, inp, dt),
        }
    }
    fn draw(&mut self, cx: &mut Ctx, inp: &Input) {
        match self {
            Scene::Menu(s) => s.draw(cx, inp),
            Scene::Fish(s) => s.draw(cx, inp),
            Scene::Slice(s) => s.draw(cx, inp),
            Scene::Pot(s) => s.draw(cx, inp),
        }
    }
    fn stage(&mut self, cx: &mut Ctx, n: u32) {
        match self {
            Scene::Menu(_) => {}
            Scene::Fish(s) => s.stage(cx, n),
            Scene::Slice(s) => s.stage(cx, n),
            Scene::Pot(s) => s.stage(cx, n),
        }
    }
}

fn sdl_error() -> String {
    unsafe {
        CStr::from_ptr(SDL_GetError())
            .to_string_lossy()
            .into_owned()
    }
}

fn hint(name: *const core::ffi::c_char, value: &str) {
    let v = CString::new(value).unwrap();
    unsafe {
        SDL_SetHint(name, v.as_ptr());
    }
}

struct Gpu {
    window: *mut SDL_Window,
    renderer: *mut SDL_Renderer,
    atlas: *mut SDL_Texture,
    /// Device pixels per logical point.
    scale: f32,
    fixed_scale: Option<f32>,
    /// Canvas units per window point (for the events' positions).
    coords: f32,
}

impl Gpu {
    fn sizes(&mut self, cx: &mut Ctx) {
        let (mut w, mut h, mut pw, mut ph) = (0, 0, 0, 0);
        unsafe {
            SDL_GetWindowSize(self.window, &mut w, &mut h);
            SDL_GetWindowSizeInPixels(self.window, &mut pw, &mut ph);
        }
        // The design is a 538 x 757 point panel (one Duo 2 screen at scale 2.5): the scale makes
        // the window's shorter fit of that, whatever the compositor says its density is.
        let fit = (pw as f32 / DESIGN_W).min(ph as f32 / DESIGN_H);
        let s = self.fixed_scale.unwrap_or(fit).clamp(0.5, 6.0);
        self.scale = s;
        // Events come in window points: these many canvas units each.
        self.coords = if w > 0 { pw as f32 / w as f32 / s } else { 1.0 };
        let _ = h;
        cx.w = pw as f32 / s;
        cx.h = ph as f32 / s;
        unsafe {
            SDL_SetRenderScale(self.renderer, s, s);
        }
        cx.font.set_density(s);
        cx.cv.px = 1.0 / s;
    }

    fn present(&mut self, cx: &mut Ctx) {
        unsafe {
            // Only the glyphs added since the last frame (a rectangle of the 16 MB atlas).
            if let Some((x0, y0, x1, y1)) = cx.font.dirty.take() {
                let rect = SDL_Rect {
                    x: x0 as i32,
                    y: y0 as i32,
                    w: (x1 - x0) as i32,
                    h: (y1 - y0) as i32,
                };
                let ptr = cx.font.pixels.as_ptr().add((y0 * text::ATLAS + x0) * 4);
                SDL_UpdateTexture(self.atlas, &rect, ptr as *const _, (text::ATLAS * 4) as i32);
            }
            SDL_SetRenderDrawColor(self.renderer, 20, 16, 36, 255);
            SDL_RenderClear(self.renderer);
            if !cx.cv.i.is_empty() {
                SDL_RenderGeometry(
                    self.renderer,
                    self.atlas,
                    cx.cv.v.as_ptr(),
                    cx.cv.v.len() as i32,
                    cx.cv.i.as_ptr(),
                    cx.cv.i.len() as i32,
                );
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut shots: Option<PathBuf> = None;
    let mut bench: Option<f32> = None;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--shots" => {
                shots = args.get(i + 1).map(PathBuf::from);
                i += 1;
            }
            "--bench" => {
                bench = args.get(i + 1).and_then(|v| v.parse().ok());
                i += 1;
            }
            "--help" | "-h" => {
                println!("usage: nibfair [--bench SECS] [--shots DIR]");
                return;
            }
            other => {
                eprintln!("nibfair: unknown argument {other}");
                std::process::exit(2);
            }
        }
        i += 1;
    }
    if let Err(e) = run(shots, bench) {
        eprintln!("nibfair: {e}");
        std::process::exit(1);
    }
}

fn run(shots: Option<PathBuf>, bench: Option<f32>) -> Result<(), String> {
    hint(SDL_HINT_APP_ID, APP_ID);
    // The pen comes only as a pen (its own events), never as a mouse or a finger too.
    hint(SDL_HINT_PEN_MOUSE_EVENTS, "0");
    hint(SDL_HINT_PEN_TOUCH_EVENTS, "0");
    // A finger works the buttons through SDL's touch-mouse.
    hint(SDL_HINT_TOUCH_MOUSE_EVENTS, "1");
    // GLES 2 first: SDL's GL renderer hands Mesa the frame's vertices as client arrays, its GLES 2
    // one through vertex buffers (on the phone, 2026-11-16, at 90 Hz: the submit 3.2 -> 2.0 ms a
    // frame in the menu, 3.4 -> 2.3 in pottery). SDL_RENDER_DRIVER still picks one by hand.
    hint(SDL_HINT_RENDER_DRIVER, "opengles2,opengl");
    let name = CString::new("Nibfair").unwrap();
    let version = CString::new(env!("CARGO_PKG_VERSION")).unwrap();
    let ident = CString::new(APP_ID).unwrap();
    // On Wayland the pen is pentools.c's: it opens the connection and takes the tablet seat
    // before SDL starts (the compositor gives a tool's events to the first seat that asked),
    // then SDL runs on that connection.
    let mut tablet = Tablet::connect(shots.is_none());
    unsafe {
        if !tablet.display().is_null() {
            SDL_SetPointerProperty(
                SDL_GetGlobalProperties(),
                SDL_PROP_GLOBAL_VIDEO_WAYLAND_WL_DISPLAY_POINTER,
                tablet.display(),
            );
        }
        SDL_SetAppMetadata(name.as_ptr(), version.as_ptr(), ident.as_ptr());
        if !SDL_Init(SDL_INIT_VIDEO) {
            return Err(format!("SDL_Init: {}", sdl_error()));
        }
    }
    let shot_scale = 2.0;
    // A bench without a display (SDL's offscreen driver) draws at the phone's size anyway: one
    // panel, 1346 x 1771 pixels (NIBFAIR_BENCH_PX=WxH for another).
    let offscreen_bench =
        bench.is_some() && std::env::var("SDL_VIDEO_DRIVER").is_ok_and(|v| v == "offscreen");
    let bench_px = std::env::var("NIBFAIR_BENCH_PX")
        .ok()
        .and_then(|v| {
            v.split_once('x')
                .and_then(|(a, b)| Some((a.parse().ok()?, b.parse().ok()?)))
        })
        .unwrap_or((1346, 1771));
    let (ww, wh) = if shots.is_some() {
        ((538.0 * shot_scale) as i32, (757.0 * shot_scale) as i32)
    } else if offscreen_bench {
        bench_px
    } else {
        (538, 757)
    };
    let flags = if shots.is_some() {
        0
    } else {
        SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY
    };
    let window = unsafe { SDL_CreateWindow(name.as_ptr(), ww, wh, flags) };
    if window.is_null() {
        return Err(format!("SDL_CreateWindow: {}", sdl_error()));
    }
    let renderer = unsafe { SDL_CreateRenderer(window, std::ptr::null()) };
    if renderer.is_null() {
        return Err(format!("SDL_CreateRenderer: {}", sdl_error()));
    }
    unsafe {
        SDL_SetRenderVSync(renderer, 1);
    }
    let atlas = unsafe {
        SDL_CreateTexture(
            renderer,
            SDL_PIXELFORMAT_RGBA32,
            SDL_TEXTUREACCESS_STATIC,
            text::ATLAS as i32,
            text::ATLAS as i32,
        )
    };
    if atlas.is_null() {
        return Err(format!("SDL_CreateTexture: {}", sdl_error()));
    }
    unsafe {
        SDL_SetTextureBlendMode(atlas, SDL_BLENDMODE_BLEND);
        SDL_SetTextureScaleMode(atlas, SDL_SCALEMODE_LINEAR);
    }
    let driver = unsafe {
        let p = SDL_GetRendererName(renderer);
        if p.is_null() {
            String::from("?")
        } else {
            CStr::from_ptr(p).to_string_lossy().into_owned()
        }
    };
    let video = unsafe {
        let p = SDL_GetCurrentVideoDriver();
        if p.is_null() {
            String::from("?")
        } else {
            CStr::from_ptr(p).to_string_lossy().into_owned()
        }
    };

    // SDL on the connection pentools.c opened: the pen is the tablet's. Otherwise (X11, offscreen)
    // SDL's pen events.
    let wl_display = unsafe {
        let props = SDL_GetWindowProperties(window);
        SDL_GetPointerProperty(
            props,
            SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER,
            std::ptr::null_mut(),
        )
    };
    tablet.adopt(wl_display);

    let face = text::find_face().ok_or("no sans font found (fc-match sans:bold)")?;
    let font = Font::new(&face, 1.0)?;
    let save_path = Save::path();
    let save = Save::load(&save_path);
    let lang = save
        .get("lang")
        .and_then(Lang::from_code)
        .unwrap_or_else(|| Lang::from_env(|k| std::env::var(k).ok()));
    let seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(1);
    let mut cx = Ctx {
        cv: Canvas::new(text::white_uv()),
        font,
        lang,
        save,
        save_path,
        buzz: buzz::Buzz::new(),
        w: 538.0,
        h: 757.0,
        now: 0.0,
        pen_tablet: tablet.active(),
        seed: if shots.is_some() { 7 } else { seed },
    };
    let mut gpu = Gpu {
        window,
        renderer,
        atlas,
        scale: 1.0,
        fixed_scale: shots.as_ref().map(|_| shot_scale),
        coords: 1.0,
    };
    gpu.sizes(&mut cx);
    eprintln!(
        "nibfair: {video} / {driver}, {:.0}x{:.0} points at {:.2}x, the pen {}",
        cx.w,
        cx.h,
        gpu.scale,
        if tablet.active() {
            "from the tablet protocol (tip and eraser)"
        } else {
            "from SDL"
        },
    );

    if let Some(dir) = shots {
        return take_shots(&mut gpu, &mut cx, &dir);
    }

    let mut inp = Input::default();
    inp.coords = gpu.coords;
    let mut scene = Scene::Menu(menu::Menu::new(&mut cx));
    let t0 = unsafe { SDL_GetTicksNS() };
    let mut last = 0.0f64;
    let mut stats = FrameStats::default();
    let report = std::env::var_os("NIBFAIR_FPS").is_some();
    let mut bench_plan = bench.map(|s| (s, 0usize, 0.0f64));
    // Without vsync (SDL's offscreen driver) a bench runs at the panel's rate anyway, so the
    // CPU's clocks and the scheduler see what they see in play: NIBFAIR_BENCH_HZ (default 90).
    // Offscreen, SDL draws into an EGL pbuffer, whose swap does nothing: Mesa (freedreno) then
    // flushes nothing either and queues every frame's commands and vertices until the phone
    // runs out of memory (2026-11-16: 6 GB in 20 s, the OOM killer took the session's services).
    // So a bench there finishes each frame by hand (glFinish: the present phase includes the GPU).
    let gl_finish: Option<unsafe extern "C" fn()> = if offscreen_bench {
        let f = unsafe { SDL_GL_GetProcAddress(c"glFinish".as_ptr()) };
        if f.is_none() {
            return Err("offscreen bench: no glFinish (a GL renderer is needed)".into());
        }
        f
    } else {
        None
    };
    let pace_hz: f64 = if bench.is_some() && video == "offscreen" {
        std::env::var("NIBFAIR_BENCH_HZ")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(90.0)
    } else {
        0.0
    };
    // Every scene's frame times go to ~/.cache/nibfair/frames.log when it ends
    // (the last 40 lines), its slow frames to slow.log: how it ran on the phone, read afterwards.
    let mut scene_stats = FrameStats::default();
    let mut scene_name = "menu";
    let mut scene_t0 = 0.0f64;
    // The previous frame's phases, put down when this frame starts (the frame time is start to start).
    let mut prev: Option<Phases> = None;
    loop {
        let now = unsafe { SDL_GetTicksNS() - t0 } as f64 * 1e-9;
        let real_dt = (now - last) as f32;
        let dt = real_dt.clamp(0.0005, 0.05);
        last = now;
        cx.now = now;
        if let Some(ph) = prev.take() {
            let ms = real_dt * 1000.0;
            let wall = if ms > nibfair::frames::SLOW_MS {
                wall_ms()
            } else {
                0
            };
            stats.frame(Some(ms), ph, now - scene_t0, wall, cx.cv.v.len());
            scene_stats.frame(Some(ms), ph, now - scene_t0, wall, cx.cv.v.len());
        }
        inp.begin(now);
        let mut ev: SDL_Event = unsafe { std::mem::zeroed() };
        while unsafe { SDL_PollEvent(&mut ev) } {
            inp.event(&ev);
        }
        inp.tablet_events(&tablet, unsafe { SDL_GetTicksNS() });
        if inp.quit {
            break;
        }
        if inp.hidden {
            // Occluded (the compositor's `suspended`: a dark panel, a window set aside or
            // covered), hidden or minimized: nothing is drawn until SDL says it is seen again.
            // The event stays queued for the next turn.
            unsafe {
                SDL_WaitEventTimeout(std::ptr::null_mut(), 250);
            }
            last = unsafe { SDL_GetTicksNS() - t0 } as f64 * 1e-9;
            continue;
        }
        if inp.resized {
            gpu.sizes(&mut cx);
            inp.coords = gpu.coords;
        }
        let work0 = unsafe { SDL_GetTicksNS() };
        let events_ms = (work0 - t0) as f64 * 1e-6 - now * 1000.0;
        let go = scene.update(&mut cx, &inp, dt);
        match go {
            Go::Stay => {}
            Go::Quit => break,
            other => {
                log_frames(scene_name, &scene_stats);
                scene_stats = FrameStats::default();
                scene_name = scene_label(other);
                scene = Scene::open(other, &mut cx);
                scene_t0 = now;
            }
        }
        cx.buzz.poll(now);
        cx.cv.clear();
        scene.draw(&mut cx, &inp);
        let submit0 = unsafe { SDL_GetTicksNS() };
        gpu.present(&mut cx);
        let present0 = unsafe { SDL_GetTicksNS() };
        // Paced by the display (vsync): SDL waits for the compositor's frame callback.
        unsafe {
            SDL_RenderPresent(gpu.renderer);
            if let Some(f) = gl_finish {
                f();
            }
        }
        let present1 = unsafe { SDL_GetTicksNS() };
        prev = Some(Phases {
            events: events_ms as f32,
            work: (present0 - work0) as f32 * 1e-6,
            submit: (present0 - submit0) as f32 * 1e-6,
            present: (present1 - present0) as f32 * 1e-6,
        });
        if pace_hz > 0.0 {
            let next = t0 as f64 + (now + 1.0 / pace_hz) * 1e9;
            let left = next - present1 as f64;
            if left > 0.0 {
                unsafe { SDL_DelayPrecise(left as u64) };
            }
        }
        if let Some((secs, idx, started)) = bench_plan.as_mut() {
            let scenes = [Go::Menu, Go::Fishing, Go::Slicing, Go::Pottery];
            if now - *started > *secs as f64 || *idx == 0 && *started == 0.0 {
                if *started > 0.0 || *idx > 0 {
                    println!("bench {:?}: {}", scenes[*idx - 1], stats.line());
                    for sl in &stats.slow {
                        let ph = sl.phases;
                        println!(
                            "  slow at {:.2} s: {:.1} ms (events {:.1}, work {:.1}, submit {:.1}, present {:.1})",
                            sl.at, sl.frame, ph.events, ph.work, ph.submit, ph.present
                        );
                    }
                }
                if *idx >= scenes.len() {
                    break;
                }
                stats = FrameStats::default();
                scene = Scene::open(scenes[*idx], &mut cx);
                scene.stage(&mut cx, 100);
                *idx += 1;
                *started = now.max(1e-6);
                scene_t0 = now;
                prev = None;
            }
        } else if report && stats.time > 5.0 {
            eprintln!("nibfair: {}", stats.line());
            stats = FrameStats::default();
        }
    }
    if bench_plan.is_none() && scene_stats.time >= 3.0 {
        if let Some(home) = std::env::var_os("HOME") {
            write_frames(&home, scene_name, &scene_stats);
        }
    }
    cx.store();
    unsafe {
        SDL_DestroyTexture(atlas);
        SDL_DestroyRenderer(renderer);
        SDL_DestroyWindow(window);
        SDL_Quit();
    }
    tablet.close();
    Ok(())
}

fn wall_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn scene_label(go: Go) -> &'static str {
    match go {
        Go::Fishing => "fishing",
        Go::Slicing => "slicing",
        Go::Pottery => "pottery",
        _ => "menu",
    }
}

/// One line per scene played: its frame times (nothing for a visit under 3 s),
/// and its slow frames to slow.log (the last 400), each with the wall clock.
fn log_frames(scene: &'static str, st: &FrameStats) {
    if st.time < 3.0 {
        return;
    }
    let Some(home) = std::env::var_os("HOME") else {
        return;
    };
    // Written beside the game (a scene change is when the next one makes its entrance).
    let st = st.clone();
    std::thread::spawn(move || write_frames(&home, scene, &st));
}

fn write_frames(home: &std::ffi::OsStr, scene: &str, st: &FrameStats) {
    let dir = std::path::Path::new(&home).join(".cache/nibfair");
    let _ = std::fs::create_dir_all(&dir);
    let stamp = wall_ms() / 1000;
    keep_last(
        &dir.join("frames.log"),
        40,
        std::iter::once(format!("{stamp} {scene}: {}", st.line())),
    );
    keep_last(
        &dir.join("slow.log"),
        400,
        st.slow.iter().map(|s| {
            format!(
                "{} {scene} +{:.1}s frame {:.1} ms: events {:.1} work {:.1} (submit {:.1}) present {:.1} ({})",
                s.wall_ms,
                s.at,
                s.frame,
                s.phases.events,
                s.phases.work,
                s.phases.submit,
                s.phases.present,
                s.phases.culprit(s.frame)
            )
        }),
    );
}

/// Append lines to a log file, keeping its last `keep`.
fn keep_last(path: &std::path::Path, keep: usize, new: impl Iterator<Item = String>) {
    let mut lines: Vec<String> = std::fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .map(String::from)
        .collect();
    let n = lines.len();
    lines.extend(new);
    if lines.len() == n {
        return;
    }
    let skip = lines.len().saturating_sub(keep);
    let _ = std::fs::write(path, lines[skip..].join("\n") + "\n");
}

/// Every screen, staged, as BMPs (the README's pictures, the visual check).
fn take_shots(gpu: &mut Gpu, cx: &mut Ctx, dir: &std::path::Path) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let list: &[(&str, Go, u32, Lang)] = &[
        ("menu-wide", Go::Menu, 0, Lang::En),
        ("pot-wide", Go::Pottery, 1, Lang::Hu),
        ("slice-wide", Go::Slicing, 1, Lang::En),
        ("fish-wide", Go::Fishing, 2, Lang::En),
        ("menu", Go::Menu, 0, Lang::En),
        ("menu-hu", Go::Menu, 0, Lang::Hu),
        ("fish-aim", Go::Fishing, 1, Lang::En),
        ("fish-wait", Go::Fishing, 2, Lang::En),
        ("fish-fight", Go::Fishing, 3, Lang::Hu),
        ("fish-end", Go::Fishing, 4, Lang::En),
        ("slice-play", Go::Slicing, 1, Lang::En),
        ("slice-ink", Go::Slicing, 2, Lang::Hu),
        ("slice-end", Go::Slicing, 3, Lang::En),
        ("pot-carve", Go::Pottery, 1, Lang::En),
        ("pot-result", Go::Pottery, 2, Lang::Hu),
        ("pot-done", Go::Pottery, 3, Lang::En),
    ];
    let inp = Input::default();
    for (name, go, stage, lang) in list {
        cx.lang = *lang;
        // The two panels spanned: about 1100 x 757 points. Drawn into a texture of
        // that size (a window resize does not reach every headless renderer).
        let wide = name.ends_with("-wide");
        let (lw, lh) = if wide {
            (1100.0, 757.0)
        } else {
            (538.0, 757.0)
        };
        let target = unsafe {
            SDL_CreateTexture(
                gpu.renderer,
                SDL_PIXELFORMAT_RGBA32,
                SDL_TEXTUREACCESS_TARGET,
                (lw * gpu.scale) as i32,
                (lh * gpu.scale) as i32,
            )
        };
        if target.is_null() {
            return Err(format!("SDL_CreateTexture (target): {}", sdl_error()));
        }
        unsafe {
            SDL_SetRenderTarget(gpu.renderer, target);
            SDL_SetRenderScale(gpu.renderer, gpu.scale, gpu.scale);
        }
        cx.w = lw;
        cx.h = lh;
        let mut scene = Scene::open(*go, cx);
        scene.stage(cx, *stage);
        // A second of play (the entrances settle), then the picture.
        for k in 0..60 {
            cx.now = 10.0 + k as f64 / 60.0;
            scene.update(cx, &inp, 1.0 / 60.0);
        }
        cx.cv.clear();
        scene.draw(cx, &inp);
        gpu.present(cx);
        unsafe {
            let surf = SDL_RenderReadPixels(gpu.renderer, std::ptr::null());
            if surf.is_null() {
                return Err(format!("SDL_RenderReadPixels: {}", sdl_error()));
            }
            let path = CString::new(
                dir.join(format!("{name}.bmp"))
                    .to_string_lossy()
                    .into_owned(),
            )
            .unwrap();
            SDL_SaveBMP(surf, path.as_ptr());
            SDL_DestroySurface(surf);
            SDL_SetRenderTarget(gpu.renderer, std::ptr::null_mut());
            SDL_DestroyTexture(target);
        }
        println!("{name}");
    }
    Ok(())
}
