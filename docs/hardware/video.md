# Video decoding

## Kernel and players

The SM8350 video core is driven by the mainline `iris` driver
(`qcom_iris`; the venus driver is not used) with the stock firmware
`vpu20_4v.mbn` (Gen1 HFI; `scripts/collect-firmware.sh` assembles it from
the phone's own vendor image; it is never committed). It provides
`/dev/video0` ("Iris Decoder", V4L2 stateful mem2mem: H.264, HEVC and VP9 in,
NV12 out) and `/dev/video1` ("Iris Encoder"). There is **no AV1** in this
decoder, and 10-bit streams (VP9 profile 2, HEVC Main 10) are rejected by the
Gen1 driver.

- The node needs `dma-coherent` in the device tree (kernel patches); without
  it a 1080p H.264 playback in Celluloid cost 92 % of a core, with it 32-43 %.
- mpv/Celluloid use `v4l2m2m-copy` for H.264 and HEVC
  (`rootfs/overlay/etc/mpv/mpv.conf`, `duo2-hwdec.lua`); GStreamer's
  `v4l2h264dec`, `v4l2h265dec` and `v4l2vp9dec` rank at `primary + 1`, so
  `playbin3` picks them. Output was bit-exact against software decoding for
  H.264 and HEVC at 1080p and 2160p, and for VP9 with GStreamer.
- The core suspends (runtime PM) about 1.5 s after decoding stops, with all
  clocks, power domains and bandwidth votes released.
- The encoder node stays `root:root 0600` (`72-duo2-video.rules`): browsers
  and players run as the GUI user, which must not open it. Browsers probe
  every `/dev/video*`, so never run one as root, and do not use
  `v4l2-ctl --list-devices` (it opens every node): read
  `/sys/class/video4linux/video*/name` instead.

## Browsers

- **Chromium (Debian):** the V4L2 stateful decoder is compiled in but needs
  `--enable-features=PreferV4L2VideoAcceleration` and, because the GPU
  sandbox on plain Linux does not allow `/dev/videoN`, `--disable-gpu-sandbox`.
  With both, H.264 plays on the video core with no dropped frames (about
  55 % of a core against 137 % in software; the Pi's builds avoid the
  sandbox switch with a patch). VP9 plays, but ending a session or changing
  resolution mid-stream crashes the Gen1 firmware (the driver restarts the
  core); the probe kernel with linux-next fixes did not show it. Until then
  use H.264.
- **Firefox ESR:** the V4L2 path exists but accepts only DRM-PRIME frames,
  which Debian's FFmpeg does not produce, so it stays in software (it needs
  the Raspberry Pi FFmpeg build and hardware WebRender).
- **Angelfish (QtWebEngine):** software only; no switch changes that.
- **YouTube serves AV1** to a browser that claims it can play it, and both
  Chromium and Firefox can in software, which leaves the hardware idle. The
  fixes are `media.av1.enabled = false` in Firefox and the small MV3 extension
  in `video/chromium-codecs` (two files, no third-party code) that answers
  "no" for `av01` (and, in the `-h264` variant, for VP9/VP8).
- `video/duo2-youtube` (+ `.desktop`) starts YouTube in an app window with its
  own profile and the extension. It is **not installed by default** (the
  owner does not want a browser with `--disable-gpu-sandbox`):
  `video/install.sh --launcher` installs it, no argument removes it. Measured
  on 1080p60 on battery: with the launcher H.264 on the video core and
  138-142 % of one core in total, against 334-347 % for AV1 in software.
- `video/fetch-rpi-builds.sh` downloads the Raspberry Pi archive's Chromium
  and FFmpeg builds for the development machine, verifying the signed
  `InRelease`, the `Packages` hash and every `.deb`'s hash. Installing them
  is the owner's decision (`scripts/duo2-video-test install-rpi`,
  `/opt` only, Debian packages untouched). They do not get Debian's
  security updates automatically.

## Test tools

`scripts/duo2-video-test` (on the development machine: `webclips` makes the
test clips, `push` copies pages, clips and tools to the phone; `browser NAME
[PAGE] [SECONDS]` runs a measured browser session) and
`video/browser-video-measure` (on the phone, as root: starts the browser as
the GUI user and reports CPU per process type, battery power, the video
core's runtime state and clock, and the page's rendered/decoded/dropped
frames). The test pages are `video/web/mse.html` (YouTube-like MSE playback,
including mid-stream 720p/1080p switches) and `video/web/codecs.html` (what
the browser claims for H.264, VP9, AV1 and HEVC).
