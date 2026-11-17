"""The home screen as data: each panel's pages of app icons. Pure
(tests/test_homemodel.py); home.py draws it.

Apps are named by their desktop file id. The layout lives in
~/.config/duo-shell/home.json:

    {"panels": {"DSI-1": [["org.gnome.TextEditor.desktop", ...], [...]],
                "DSI-2": [[...]]}}

The first page of a panel has the widgets (clock, weather, battery) above
its icons, so it holds fewer of them ([`capacity`]). Pages fill in order: an
icon pushed off a full page goes to the next one, empty pages at the end go
away (one page always stays).
"""

import json
import os

COLUMNS = 4
FIRST_ROWS = 3
ROWS = 5
# A panel on its side (757 x 538 logical at scale 2.5): the bar, the dock's
# room and the dots leave ~380 px, three rows of icons. The first page puts
# the widgets beside its 4 x 3 icons, the others hold their 20 in 7 x 3.
LANDSCAPE_COLUMNS = 7
LANDSCAPE_ROWS = 3

# The first launch's home, per panel: the first of these that are installed.
DEFAULTS = {
    "DSI-1": [
        "org.gnome.TextEditor.desktop",
        "org.gnome.Nautilus.desktop",
        "org.gnome.Calendar.desktop",
        "org.gnome.clocks.desktop",
        "org.gnome.Weather.desktop",
        "org.gnome.Calculator.desktop",
        "org.gnome.Loupe.desktop",
        "org.gnome.Papers.desktop",
        "io.bassi.Amberol.desktop",
        "io.github.celluloid_player.Celluloid.desktop",
    ],
    "DSI-2": [
        "firefox-esr.desktop",
        "firefox.desktop",
        "chromium.desktop",
        "foot.desktop",
        "org.gnome.Software.desktop",
        "org.duo2os.Settings.desktop",
        "dev.tchx84.Portfolio.desktop",
        "yelp.desktop",
    ],
}

# What a tap on a widget of the first page opens: (desktop file id, its
# desktop action or None). The battery card opens Duo Settings on its Battery
# page (the desktop file's "battery" action: `duo-settings battery`).
CARDS = {
    "weather": ("org.gnome.Weather.desktop", None),
    "battery": ("org.duo2os.Settings.desktop", "battery"),
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "home.json")


def capacity(page):
    """How many icons page `page` (0 = the first) holds."""
    return COLUMNS * (FIRST_ROWS if page == 0 else ROWS)


def grid_shape(page, landscape=False):
    """(columns, rows) of page `page`'s icon grid; icon i of the page sits
    at (i % columns, i // columns). The pages hold as many icons either
    way (capacity): a rotation only lays them out again."""
    if page == 0:
        return COLUMNS, FIRST_ROWS
    if landscape:
        return LANDSCAPE_COLUMNS, LANDSCAPE_ROWS
    return COLUMNS, ROWS


def normalize(pages):
    """Pages that fit: duplicates (the later ones) and empty pages go, the
    overflow of a page goes on at the start of the next one; at least one
    page stays."""
    seen, clean = set(), []
    for page in pages:
        kept = []
        for app in page:
            if isinstance(app, str) and app not in seen:
                seen.add(app)
                kept.append(app)
        if kept:
            clean.append(kept)
    out, carry = [], []
    for page in clean:
        page, carry = carry + page, []
        cap = capacity(len(out))
        if len(page) > cap:
            page, carry = page[:cap], page[cap:]
        out.append(page)
    while carry:
        cap = capacity(len(out))
        out.append(carry[:cap])
        carry = carry[cap:]
    return out or [[]]


def default_layout(panels, available):
    """Each panel's first page from DEFAULTS (installed ones only)."""
    return {p: normalize([[a for a in DEFAULTS.get(p, []) if a in available]]) for p in panels}


def load(path, panels, available):
    """The saved layout (apps no longer installed dropped, panels missing
    from it get their defaults), else the defaults."""
    layout = {}
    try:
        with open(path) as f:
            data = json.load(f)
        saved = data.get("panels") if isinstance(data, dict) else None
        if isinstance(saved, dict):
            for panel, pages in saved.items():
                if isinstance(pages, list):
                    layout[panel] = normalize(
                        [
                            [a for a in page if a in available]
                            for page in pages
                            if isinstance(page, list)
                        ]
                    )
    except (OSError, ValueError, AttributeError):
        pass
    defaults = default_layout(panels, available)
    for panel in panels:
        if panel not in layout:
            layout[panel] = defaults[panel]
    return layout


def save(path, layout):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump({"panels": layout}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def find(layout, app):
    """(panel, page, index) of an app, or None."""
    for panel, pages in layout.items():
        for p, page in enumerate(pages):
            if app in page:
                return panel, p, page.index(app)
    return None


def move(layout, app, panel, page, index):
    """The layout with `app` at `index` of `page` on `panel` (taken from
    wherever it was). An index past the end puts it last; a page past the
    last one is a new page."""
    out = {p: [list(pg) for pg in pages] for p, pages in layout.items()}
    for pages in out.values():
        for pg in pages:
            if app in pg:
                pg.remove(app)
    pages = out.setdefault(panel, [[]])
    while len(pages) <= page:
        pages.append([])
    target = pages[page]
    target.insert(max(0, min(index, len(target))), app)
    return {name: normalize(pgs) for name, pgs in out.items()}


def remove(layout, app):
    """The layout without `app` (wherever it was)."""
    return {
        p: normalize([[a for a in page if a != app] for page in pages])
        for p, pages in layout.items()
    }


def add(layout, panel, app):
    """The layout with `app` added to `panel`: on the first page with room
    (a new page if none). Already on that panel: unchanged."""
    pages = layout.get(panel) or [[]]
    if any(app in page for page in pages):
        return layout
    for p, page in enumerate(pages):
        if len(page) < capacity(p):
            return move(layout, app, panel, p, len(page))
    return move(layout, app, panel, len(pages), 0)


def slot_at(x, y, width, height, columns, rows):
    """The cell index under (x, y) in a grid of `columns` x `rows` cells
    filling width x height (clamped to the grid)."""
    if width <= 0 or height <= 0:
        return 0
    col = min(columns - 1, max(0, int(x / (width / columns))))
    row = min(rows - 1, max(0, int(y / (height / rows))))
    return row * columns + col


def edge_flip(x, width, margin=28):
    """-1 / 1 when a carried icon is held at the left / right edge of the
    page (flip to that page), else 0."""
    if x < margin:
        return -1
    if x > width - margin:
        return 1
    return 0
