"""The wallpaper settings: ~/.config/duo-shell/wallpaper.conf (the shell and
duo-lock read it, see shell/duo-shell/duoshell/wallpaper.py), the built-in
pictures, thumbnails, and the Bing markets. Everything but `thumbnail` is
pure (tests/test_settings.py)."""

import hashlib
import json
import os
import re
import tempfile

from . import conf

GNOME_DIR = "/usr/share/backgrounds/gnome"
# The built-in pictures: gnome-backgrounds' dark variants (CC BY-SA 3.0, see
# shell/duo-shell/README.md). (file, title); only the ones installed show.
BUILTIN = [
    ("adwaita-d.jpg", "Adwaita"),
    ("amber-d.jxl", "Amber"),
    ("swoosh-d.jxl", "Swoosh"),
    ("ring-d.jxl", "Ring"),
    ("pills-d.jxl", "Pills"),
    ("sheet-d.jxl", "Sheet"),
    ("geometrics-d.jxl", "Geometrics"),
    ("glass-chip-d.jxl", "Glass"),
    ("neogeo-d.jxl", "Neo Geo"),
    ("symbolic-soup-d.jxl", "Soup"),
    ("blobs-d.svg", "Blobs"),
    ("morphogenesis-d.svg", "Morphogenesis"),
]
MARKETS = [
    ("en-US", "English (United States)"),
    ("en-GB", "English (United Kingdom)"),
    ("en-AU", "English (Australia)"),
    ("en-CA", "English (Canada)"),
    ("en-IN", "English (India)"),
    ("de-DE", "Deutsch"),
    ("fr-FR", "Français"),
    ("fr-CA", "Français (Canada)"),
    ("es-ES", "Español"),
    ("it-IT", "Italiano"),
    ("pt-BR", "Português (Brasil)"),
    ("nl-NL", "Nederlands"),
    ("pl-PL", "Polski"),
    ("ru-RU", "Русский"),
    ("hu-HU", "Magyar"),
    ("ja-JP", "日本語"),
    ("ko-KR", "한국어"),
    ("zh-CN", "中文"),
]
MODES = ["same", "span"]


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


def read(path=None):
    return conf.read(path or conf_path())


def write(changes, path=None):
    """Change keys of the file (atomic, comments kept)."""
    path = path or conf_path()
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        text = "# The wallpaper (Duo Settings writes this file; the shell watches it).\n"
    for key, value in changes.items():
        text = conf.set_key(text, key, value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def builtin(directory=GNOME_DIR):
    return [
        (os.path.join(directory, f), title)
        for f, title in BUILTIN
        if os.path.isfile(os.path.join(directory, f))
    ]


def market_from_locale(lang, default="en-US"):
    m = re.match(r"^([a-z]{2})(?:_([A-Z]{2}))?", lang or "")
    if not m:
        return default
    codes = [c for c, _n in MARKETS]
    if m.group(2) and "%s-%s" % (m.group(1), m.group(2)) in codes:
        return "%s-%s" % (m.group(1), m.group(2))
    for c in codes:
        if c.startswith(m.group(1) + "-"):
            return c
    return default


def current_market(c, lang):
    market = c.get("bing_market", "")
    return market if market in [m for m, _n in MARKETS] else market_from_locale(lang)


def selection(c, items):
    """Which tile is chosen: "default", a path of `items`, or "file" (a picture
    of the owner's own)."""
    if c.get("source") != "file":
        return "default"
    return c.get("file") if c.get("file") in items else "file"


def mode_index(c):
    return MODES.index(c["mode"]) if c.get("mode") in MODES else 0


def bing_caption(directory=None):
    """(title, copyright) of the current Bing picture, or ("", "")."""
    try:
        with open(os.path.join(directory or bing_dir(), "current.json")) as f:
            meta = json.load(f)
        return str(meta.get("title", "")), str(meta.get("copyright", ""))
    except (OSError, ValueError, AttributeError):
        return "", ""


def thumb_path(path, mtime, w, h):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    key = hashlib.sha256(("%s|%s|%dx%d" % (path, mtime, w, h)).encode()).hexdigest()[:24]
    return os.path.join(base, "duo-shell", "wallpaper-thumbs", key + ".png")


def thumbnail(path, w=140, h=190):
    """A w x h crop (cover) of the picture as a GdkPixbuf, cached on disk;
    None if unreadable. Slow for a big JPEG XL: call it off the main thread."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        cache = thumb_path(path, os.stat(path).st_mtime, w, h)
        if os.path.isfile(cache):
            return GdkPixbuf.Pixbuf.new_from_file(cache)
        _fmt, iw, ih = GdkPixbuf.Pixbuf.get_file_info(path)
        if not iw or not ih:
            return None
        s = max(w / iw, h / ih)
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, max(w, round(iw * s)), max(h, round(ih * s)), False
        )
        x, y = max(0, (pb.get_width() - w) // 2), max(0, (pb.get_height() - h) // 2)
        out = pb.new_subpixbuf(x, y, min(w, pb.get_width()), min(h, pb.get_height())).copy()
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            out.savev(cache, "png", [], [])
        except (OSError, Exception):
            pass
        return out
    except Exception:
        return None
