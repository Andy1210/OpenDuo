# Nibfair

Three games in one app that show off the Surface Slim Pen 2 on duo2-os: a
night fair with three booths. Original designs and art: everything on screen
is vector shapes drawn by the game itself (gfx.rs); the only thing it borrows
from the system is the bold sans font (fontconfig's `sans:bold`, DejaVu Sans
Bold on the phone). English and Hungarian (the menu's button switches; the
first run follows the locale, `NIBFAIR_LANG=hu|en` forces one).

Rust + SDL 3 (Debian trixie's 3.2.10): the window, the pen's own events and
the GPU renderer (GLES 2 through SDL_Renderer, OpenGL where there is none). A whole frame is one
`SDL_RenderGeometry` call: every shape and glyph is a triangle of one vertex
list that samples one texture, the glyph atlas (fontdue rasterises a glyph the
first time it is needed, at the panel's pixel size), shapes using its white
texel; edges are antialiased by a one-pixel feather.

## The games

**Fishing** (the hovering pen is the rod)
- Touch the screen: an arc shows where the bait will land; tilting the pen
  (tilt arrives only while the tip touches) swings it, moving the pen moves it
  too. Lift to cast: the bait lands where the arc showed (the lift itself has
  no tilt any more).
- Hover over the water: how far down the screen the pen hovers is how deep the
  bait hangs (the Duo 2's digitizer reports no hover distance, so the hover's
  height on the screen stands in for it; a pen and digitizer that report one
  (SDL's distance axis) drive the depth instead). A jerky bait spooks fish.
- Fish swim at their own depths (minnow, perch, trout, pike, carp, catfish,
  eel, the rare goldfin, and an old boot on the bottom). One that likes the
  bait nibbles (a soft buzz), then bites: the pen buzzes hard and the bobber
  dunks. Tap within the bite to hook it; a tap before the bite spooks it.
- Reel by pressure: the gauge on top is the line's tension. Too hard (in the
  red, worst while the fish surges) and the line snaps; too soft (blue) and the
  fish slips the hook or runs off with the line. Each bit of line wound in
  clicks in the pen.
- A round is one afternoon (2.5 minutes, the sun sets); the catch log lists
  the kinds and the biggest of each. Holding the tip down while fishing casts
  again; the side button winds the bait in.

**Slicing** (strokes, pressure, the eraser end)
- Things fly up from below: lanterns, gourds, logs, ice, stones, a rare gold
  star. A fast stroke of the tip cuts what it crosses; each cut clicks in the
  pen. The dots over a thing are its hardness, and the pressure is the force
  (the gauge on the right shows the pressure against the hardness marks): a
  stone needs a firm stroke, a light one glances off.
- Ink blots are the hazard: the blade bursts one (a heart lost), and one that
  falls back stains the paper (a heart lost). Turn the pen over: the eraser end
  is a round shield that wipes ink away (points) and shoves everything else.
- Several cuts in one stroke are a combo; cuts in a row without a drop raise a
  multiplier; cuts fill the focus bar, and the side button then slows time for
  3 seconds. Ten rounds, harder each time; three hearts.

**Pottery** (tilt and pressure shape a spinning pot)
- A lump of clay spins on the wheel; an order asks for a shape (its outline is
  drawn over the clay, green where the clay already matches; the side button
  hides or shows it; the shelf shows the eight orders).
- The tip in the clay cuts: the pressure is how much clay it takes per second
  (a light touch shaves and polishes, a hard one gouges and leaves it rough).
  The tilt is the tool's angle: upright it is a round point that cuts a
  groove, tilted it is a straight rib that leaves a straight wall at the pen's
  lean.
- The pen hovering beside the clay is a wet sponge: it smooths bumps and
  roughness. The pen buzzes in step with the roughness under it (faster and
  stronger on a rough surface, quiet on a smooth one). The eraser end puts clay
  back.
- "Fire it" scores the pot: 85 % the shape's match, 15 % the smoothness, and
  stars at 60 / 78 / 90 points. Eight orders; the workshop total is the best.

Best scores, the fish records and the language are kept in
`~/.config/nibfair/save.txt`. Each scene's frame times go to
`~/.cache/nibfair/frames.log` (the last 40 visits): the frame time's p50 /
p95 / p99 / max, each phase's worst (events; work, and of it the submit to
SDL; present: SDL's wait for the compositor's frame callback and the swap)
and the frames over 25 ms by the phase that took them; those frames
themselves, with the wall clock (to set beside the journal), go to
`~/.cache/nibfair/slow.log` (the last 400).

## The pen on duo2-os

- duo-wc gives the pen as a pen (pressure, tilt, eraser, hover) only to the
  drawing apps of its `pen_apps` list (`pen_input = apps`); Nibfair's app id
  `org.duo2os.Nibfair` is in the default list. Elsewhere the pen arrives as a
  finger, and the menu then says to add Nibfair in Settings > Pen > Apps.
- SDL 3.2 takes Wayland by itself only from a compositor with `wp_fifo_v1`;
  duo-wc has it (since 2026-11-16 evening), so Nibfair asks for no video
  driver (`SDL_VIDEO_DRIVER` in the environment chooses one by hand).
- SDL on Wayland reports every tablet tool as a pen and never sets the eraser
  flag, so the pen is csrc/pentools.c's: it opens the Wayland connection,
  creates its tablet seat and only then starts SDL on that connection
  (`SDL_PROP_GLOBAL_VIDEO_WAYLAND_WL_DISPLAY_POINTER`). The compositor
  (Smithay's tablet manager) sends a tool's events to one instance of it per
  client, the first one created: a seat made after SDL's (as until
  2026-11-16) never hears the pen, and the eraser end played as the tip.
  Each tool frame (its type: pen or eraser; position, pressure, tilt,
  distance, the barrel button) waits on pentools' own event queue, read by
  SDL's event pump and handled without blocking. A tilt of exactly 0, 0
  while touching is the digitizer having none (around the touch and the
  lift) and keeps the tilt it had.
- The loop is SDL's vsync (SDL waits for the compositor's frame callback)
  and nothing else. A window drawn nowhere (a dark panel, home, covered by
  a fullscreen window) is `suspended` by duo-wc, which SDL reports as
  SDL_EVENT_WINDOW_OCCLUDED: the game then waits for events (0.1 % CPU)
  until it is seen again. Until 2026-11-16 it watched a frame callback of
  its own and guessed, because duo-wc said nothing.
- The buzz: a datagram `pulse <waveform> <intensity 1-255>` to duo2-pend's
  socket `/run/duo2-pen/haptics` (rootfs/overlay/usr/local/sbin/duo2-pend:
  the pen's discrete waveforms only, one every 40 ms at most). The game keeps
  that pace itself and lets the more important pulse take a contested slot
  (src/haptic.rs). Pulses are a nice-to-have: the pen drops its Bluetooth link
  for about a second each time it comes near the screen, and nothing waits on
  one. `NIBFAIR_HAPTICS=0` turns them off.
- With the two panels spanned (a wide window) the menu is a book (the title
  on the left, the booths on the right), the pot stands in the right panel and
  the words and panels keep to the left one, so nothing sits under the hinge.

## Files

    src/lib.rs ...      the rules, no SDL: fish.rs (the pond, the bite, the reel's tension),
                        slice.rs (strokes, force, the eraser's shield, rounds), pottery.rs
                        (the profile, the tool, the sponge, orders and their score),
                        haptic.rs (pulse requests and their pace), pen.rs (the tool type, the
                        tilt rule), frames.rs (frame times by phase), strings.rs, save.rs, ...
    src/main.rs         SDL: the window, the loop, scenes, --bench, --shots
    src/menu.rs, pond.rs, range.rs, wheel.rs   the four scenes
    src/gfx.rs, text.rs, ui.rs, input.rs, buzz.rs   drawing, text, widgets, the pen, the buzz
    csrc/pentools.c     the pen, from the Wayland tablet protocol (both ends told apart)

## Building, testing, installing

    ./build-aarch64.sh              out/nibfair for the phone (Docker: Debian trixie, arm64 SDL)
    ./build-aarch64.sh test         the rules' unit tests (host)
    ./build-aarch64.sh shots DIR    every screen as BMPs, rendered headless (SDL offscreen)
    ./install.sh                    onto the phone (scripts/lib/remote.sh): libsdl3-0 if missing,
                                    /usr/local/bin/nibfair, the .desktop and the icon. Files only.
    nibfair --bench 5               each scene 5 s unattended: the frame times (as in frames.log)

A bench needs no display: `SDL_VIDEO_DRIVER=offscreen nibfair --bench 15`
on the phone (as the GUI user, for the GPU) draws at one panel's size
(1346 x 1771 px, `NIBFAIR_BENCH_PX`), paces itself at 90 Hz
(`NIBFAIR_BENCH_HZ`) and finishes each frame with glFinish: an EGL
pbuffer's swap flushes nothing, and without it Mesa queued every frame
until the phone ran out of memory. Run it with a memory cap anyway
(`systemd-run --user --scope -p MemoryMax=500M ...`).

Runtime packages: libsdl3-0 (and what the base image has: libwayland-client0,
fontconfig, fonts-dejavu-core). Not in the image (rootfs/install-components.sh)
yet: install it with `./install.sh`.

Nibfair replaced Pen Hopper (apps/pen-game, Python + Cairo, too slow on the
CPU): `install-files.sh` removes Pen Hopper's files from the phone.
