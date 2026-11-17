"""The wallpaper: which picture, and which part of it each panel shows.

~/.config/duo-shell/wallpaper.conf (key = value, written by Duo Settings):

    source = default | file      default: the shell's dark gradient
    file   = /path/to/picture    used when source = file
    mode   = same | span         same: the picture on each panel;
                                 span: one picture across both panels
    bing   = on | off            the Bing image of the day replaces the choice
    dim    = 28                  per cent of black over the picture

The Bing service (duo-wallpaper-bing) keeps its pictures in
~/.local/share/duo-shell/wallpapers/bing/ and names the current one in
current.json there. The functions up to `render_panel` are pure (tests/
test_wallpaper.py); `render_panel` and `pixbuf_to_bgrx` use GdkPixbuf, which
needs no display, so the lock client (no GTK) uses them too.
"""

import json
import os

DEFAULT_DIM = 28
MAX_DIM = 70
MODES = ("same", "span")


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell")


def conf_path():
    return os.path.join(config_dir(), "wallpaper.conf")


def bing_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "duo-shell", "wallpapers", "bing")


def parse(text):
    conf = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


def read_conf(path=None):
    try:
        with open(path or conf_path()) as f:
            return parse(f.read())
    except OSError:
        return {}


def is_on(value):
    return str(value or "").strip().lower() in ("on", "yes", "true", "1")


def bing_current(directory=None):
    """The current Bing picture: (path, meta) or (None, {}). current.json names
    the file; only a plain file name inside the directory counts."""
    directory = directory or bing_dir()
    try:
        with open(os.path.join(directory, "current.json")) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None, {}
    name = meta.get("file") if isinstance(meta, dict) else None
    if not isinstance(name, str) or name != os.path.basename(name) or name.startswith("."):
        return None, {}
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return None, {}
    return path, meta


def resolve(conf, bing_directory=None):
    """What to show: {"path": str | None, "mode": "same" | "span", "dim": 0-70}.
    path None = the default look."""
    mode = conf.get("mode", "same")
    if mode not in MODES:
        mode = "same"
    try:
        dim = max(0, min(MAX_DIM, int(conf.get("dim", DEFAULT_DIM))))
    except ValueError:
        dim = DEFAULT_DIM
    path = None
    if is_on(conf.get("bing")):
        path, _meta = bing_current(bing_directory)
    caption = None
    if path is not None:
        caption = bing_caption(_meta)
    if path is None and conf.get("source") == "file":
        candidate = conf.get("file", "")
        if candidate and os.path.isfile(candidate):
            path = candidate
    return {"path": path, "mode": mode, "dim": dim, "caption": caption}


def bing_caption(meta):
    """What the home screen's "i" says about the Bing picture shown (from
    current.json): {"title", "text", "link"}, or None. `text` is Bing's
    copyright line (what the picture is, and whose); `title` is left out
    when it says nothing ("Info", the same as the text); `link` only an
    https one (bing.parse_response checked it when it was stored)."""
    if not isinstance(meta, dict):
        return None
    title = meta.get("title") if isinstance(meta.get("title"), str) else ""
    text = meta.get("copyright") if isinstance(meta.get("copyright"), str) else ""
    link = meta.get("link") if isinstance(meta.get("link"), str) else ""
    title, text = title.strip(), text.strip()
    if title.lower() in ("", "info") or title == text:
        title = ""
    if not link.startswith("https://"):
        link = ""
    if not (title or text):
        return None
    return {"title": title, "text": text, "link": link}


# -- geometry ---------------------------------------------------------------------------


def panels_from_outputs(outputs):
    """duo-wc's get_outputs list -> {name: (x, y, w, h)} in logical px."""
    panels = {}
    for o in outputs or []:
        try:
            panels[o["name"]] = (float(o["x"]), float(o["y"]), float(o["w"]), float(o["h"]))
        except (KeyError, TypeError, ValueError):
            continue
    return panels


def panel_fits(panels, name, w, h):
    """Whether `panels` ({name: (x, y, w, h)}, panels_from_outputs) has panel
    `name` at the logical size w x h (within a pixel): a layout read before
    a rotation has the panel upright."""
    p = panels.get(name)
    return p is not None and abs(p[2] - w) <= 1 and abs(p[3] - h) <= 1


def bounding_box(panels):
    """The rectangle around every panel, the hinge gap inside it."""
    x0 = min(p[0] for p in panels.values())
    y0 = min(p[1] for p in panels.values())
    x1 = max(p[0] + p[2] for p in panels.values())
    y1 = max(p[1] + p[3] for p in panels.values())
    return (x0, y0, x1 - x0, y1 - y0)


def source_rect(img_w, img_h, panel, canvas):
    """The part of an img_w x img_h picture that `panel` (x, y, w, h) shows when
    the picture covers `canvas` (x, y, w, h) (scaled to cover, centred). In
    picture px, floats. Also the scale used (canvas px -> picture px is 1/s)."""
    cx, cy, cw, ch = canvas
    s = max(cw / img_w, ch / img_h)  # picture px -> canvas px
    off_x = cx + (cw - img_w * s) / 2
    off_y = cy + (ch - img_h * s) / 2
    px, py, pw, ph = panel
    return ((px - off_x) / s, (py - off_y) / s, pw / s, ph / s), s


def canvas_for(mode, name, panels):
    """The rectangle the picture covers for panel `name`."""
    if mode == "span" and len(panels) > 1:
        return bounding_box(panels)
    return panels[name]


def draw_rect(name, panels, mode, size, image):
    """Where a home screen of `size` (w, h, logical px) for panel `name` draws
    a picture of `image` (w, h) px so that it shows its part of it (the same
    part render_panel cuts): (x, y, w, h) in the widget's px, the panel's
    corner at 0, 0. A layout that does not have this panel at this size (it
    has not caught up yet) covers the panel alone, centred."""
    w, h = size
    panel = panels.get(name)
    if panel is None or abs(panel[2] - w) > 1.5 or abs(panel[3] - h) > 1.5:
        panels = {name: (0.0, 0.0, float(w), float(h))}
        mode = "same"
    panel = panels[name]
    canvas = canvas_for(mode, name, panels)
    (sx, sy, _sw, _sh), s = source_rect(image[0], image[1], panel, canvas)
    k = w / panel[2] * s  # widget px per picture px
    return (-sx * k, -sy * k, image[0] * k, image[1] * k)


# -- rendering (GdkPixbuf: no display needed) -------------------------------------------


def render_panel(path, name, panels, mode, scale, cache=None):
    """The picture for panel `name` as a GdkPixbuf of the panel's physical size
    (round(logical * scale)), or None when the file cannot be read. `cache`
    (a dict, optional) keeps the decoded picture between the panels of one
    update (a 4096 px JPEG XL takes ~2.5 s to decode on the phone)."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        _fmt, iw, ih = GdkPixbuf.Pixbuf.get_file_info(path)
        if not iw or not ih:
            return None
        panel = panels[name]
        pw, ph = max(1, round(panel[2] * scale)), max(1, round(panel[3] * scale))
        canvas = canvas_for(mode, name, panels)
        rect, s = source_rect(iw, ih, panel, canvas)
        phys = s * scale  # picture px -> physical px
        # Decode at the size it will be shown at (never bigger than the file).
        f = min(1.0, phys)
        tw, th = max(1, round(iw * f)), max(1, round(ih * f))
        pb = cache.get((path, tw, th)) if cache is not None else None
        if pb is None:
            if (tw, th) == (iw, ih):
                pb = GdkPixbuf.Pixbuf.new_from_file(path)
            else:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, tw, th, False)
            if cache is not None:
                cache[(path, tw, th)] = pb
        k = pb.get_width() / iw  # picture px -> decoded px
        sx, sy, sw, sh = (v * k for v in rect)
        x = max(0, min(pb.get_width() - 1, int(round(sx))))
        y = max(0, min(pb.get_height() - 1, int(round(sy))))
        w = max(1, min(pb.get_width() - x, int(round(sw))))
        h = max(1, min(pb.get_height() - y, int(round(sh))))
        part = pb.new_subpixbuf(x, y, w, h)
        if (w, h) != (pw, ph):
            part = part.scale_simple(pw, ph, GdkPixbuf.InterpType.BILINEAR)
        return part
    except Exception as e:  # a broken picture must not break the shell
        print("duo-shell: wallpaper %s: %s" % (path, e), flush=True)
        return None


def decode(path, max_side=4096):
    """The whole picture as a GdkPixbuf, at most `max_side` px on its longer
    side (the home screens cut their parts from it as they draw,
    wallpicture.py), or None when the file cannot be read."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        _fmt, iw, ih = GdkPixbuf.Pixbuf.get_file_info(path)
        if not iw or not ih:
            return None
        if max(iw, ih) <= max_side:
            return GdkPixbuf.Pixbuf.new_from_file(path)
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_side, max_side, True)
    except Exception as e:  # a broken picture must not break the shell
        print("duo-shell: wallpaper %s: %s" % (path, e), flush=True)
        return None


def pixbuf_to_bgrx(pixbuf):
    """(width, height, bytes) of a pixbuf as cairo's FORMAT_RGB24 / ARGB32 with
    alpha 255 (little endian: B, G, R, A), without a per-pixel loop."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    n_ch = pixbuf.get_n_channels()
    stride = pixbuf.get_rowstride()
    src = pixbuf.get_pixels()
    out = bytearray(w * h * 4)
    out[3::4] = b"\xff" * (w * h)
    for y in range(h):
        row = src[y * stride : y * stride + w * n_ch]
        o = y * w * 4
        out[o + 0 : o + w * 4 : 4] = row[2::n_ch]
        out[o + 1 : o + w * 4 : 4] = row[1::n_ch]
        out[o + 2 : o + w * 4 : 4] = row[0::n_ch]
    return w, h, bytes(out)
