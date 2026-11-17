//! Rendering the scene to a file, so the screen can be read from a terminal.
//!
//! Not a screen capture protocol (`wlr-screencopy` is worth having later): it is
//! how a phone with nobody looking at it can be checked from a terminal.
//!
//! What it captures is one output's SCENE, rendered off-screen with the same
//! element list the display gets, not what the display engine scanned out. It
//! tells you whether clients are drawing and where; the DRM state
//! (`/sys/kernel/debug/dri/0/state`) tells you whether the panel got a frame.
//! A rotated output is captured upright, the way it reads with the device turned;
//! a dark output can be captured too (its scene, not the dark panel).

use std::path::Path;

use anyhow::{Context as _, Result};
use smithay::backend::allocator::Fourcc;
use smithay::backend::renderer::damage::OutputDamageTracker;
use smithay::backend::renderer::gles::{GlesRenderbuffer, GlesRenderer};
use smithay::backend::renderer::{Bind, ExportMem, Offscreen};
use smithay::output::Output;
use smithay::utils::{Buffer as BufferCoords, Rectangle, Size, Transform};

use crate::render::Element;

/// Render the scene off-screen and read it back: (width, height, RGBA
/// pixels). Done on duo-wc's loop (it needs the renderer: a few ms); the
/// PNG is written off it ([`write_png`] in a thread, ipc.rs), which took
/// ~4 s on the phone and held every client and every frame up meanwhile.
pub fn capture(
    renderer: &mut GlesRenderer,
    output: &Output,
    elements: &[Element],
) -> Result<(i32, i32, Vec<u8>)> {
    let mode = output.current_mode().context("the output has no mode")?;
    // The elements are laid out in the output's rotated frame; rendered into a
    // buffer of that size without a transform, the picture comes out upright.
    let size = output.current_transform().transform_size(mode.size);
    let scale = output.current_scale().fractional_scale();
    let buffer_size: Size<i32, BufferCoords> = (size.w, size.h).into();
    let mut target: GlesRenderbuffer = renderer
        .create_buffer(Fourcc::Abgr8888, buffer_size)
        .context("failed to create an off-screen buffer")?;
    let mut framebuffer = renderer.bind(&mut target).context("failed to bind the off-screen buffer")?;

    let mut damage = OutputDamageTracker::new(size, scale, Transform::Normal);
    damage
        .render_output(renderer, &mut framebuffer, 0, elements, crate::backend::BACKGROUND)
        .map_err(|err| anyhow::anyhow!("failed to render the scene: {err}"))?;

    let region = Rectangle::from_size(buffer_size);
    let mapping = renderer
        .copy_framebuffer(&framebuffer, region, Fourcc::Abgr8888)
        .map_err(|err| anyhow::anyhow!("failed to read the off-screen buffer: {err}"))?;
    let pixels = renderer
        .map_texture(&mapping)
        .map_err(|err| anyhow::anyhow!("failed to map the off-screen buffer: {err}"))?;

    tracing::debug!(bytes = pixels.len(), "read the off-screen buffer");

    Ok((size.w, size.h, pixels.to_vec()))
}

/// Write RGBA pixels out as a PNG (fast compression: the picture is for
/// looking at, not for keeping). Runs in any thread.
pub fn write_png(path: &Path, width: i32, height: i32, pixels: &[u8]) -> Result<()> {
    let file = std::fs::File::create(path).with_context(|| format!("failed to create {}", path.display()))?;
    let mut encoder = png::Encoder::new(std::io::BufWriter::new(file), width as u32, height as u32);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(png::Compression::Fast);
    let mut writer = encoder.write_header().context("failed to write the header")?;
    writer.write_image_data(pixels).context("failed to write the image")?;
    Ok(())
}
