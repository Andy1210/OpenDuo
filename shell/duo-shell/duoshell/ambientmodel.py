"""The closed phone's ambient screens, pure (tests/test_ambientmodel.py).

duo-wc decides WHEN (its state's `ambient`: `peek` while the phone is opened
just a little, `glance` for 5 s after the power key of a closed phone) and
draws nothing but the shell's `duo-ambient` surfaces then, on black. This
module decides WHERE the strip goes and what it says:

- peek: on the panel's OUTER edge (the edge opposite the hinge: looking into
  the gap of a slightly opened phone, that is the part one sees): the clock,
  the battery and the notification icons;
- glance: in the sliver of the panel next to the HINGE that shows from
  outside on a closed phone (the display's curved edge around the hinge:
  the stock firmware's "spine"): one line of text running lengthwise along
  that edge, the time and the battery, sized to the sliver;
- glance ruler (`duoctl ambient glance-ruler`, 30 s): numbered coloured
  bands from the hinge-side edge inwards, so the owner can say from outside
  which ones show; that sets `glance_inset` / `glance_width`.

Both on the right panel (DSI-2) only by default (the owner, 2026-11-07); duo-wc
lights only that panel for the glance.

Where the sliver is, from the stock firmware (SurfaceSystemUI's
`spine_time_view`, at the stock 400 dpi = our scale 2.5, so dp = logical
px): a 34 dp "hinge" view centred over the 66 px (26.4 dp) hinge gap, the
time as a TextClock 80 dp long turned 90 degrees clockwise (reading top to
bottom) whose middle is 40 dp past that view's edge: ~34..54 logical px
from the panel's hinge-side edge, 14 sp bold, letter spacing 0.125, its
first ~80 dp along the edge from the top. Those are the defaults below
until the ruler says otherwise.

The panels are AMOLED: everything else stays black (unlit). Widths and the
panels used are the owner's to tune in ~/.config/duo-shell/ambient.conf:

    peek_width = 72          # logical px (2.5 px = 1 mm on the Duo 2's panel ~ 0.16 mm/px)
    peek_panels = DSI-2      # both | DSI-1 | DSI-2
    glance_panels = DSI-2    # (duo-wc's glance_panels lights the panel)
    glance_inset = -2.3      # where the strip starts, logical px from the hinge-side edge
                             # (may be negative / fractional: the text is centred in the strip)
    glance_width = 10        # how wide it is (the text is sized to it): measured with the
                             # owner (ruler): only the outermost ~24 physical px are seen
                             # from outside; these put the text at physical px 0..13
    glance_along = 28        # where the text starts along the edge, from the top
    glance_turn = 270        # 270 reads top to bottom (the stock way), 90 bottom to top
    ruler_band = 16          # the ruler's bands, PHYSICAL px each (~1 mm)
    ruler_bands = 12
"""

import os

DEFAULTS = {
    "peek_style": "line",
    "peek_turn": "270",
    "peek_inset": "-4",
    "peek_line_width": "34",
    "peek_along": "28",
    "peek_width": "72",
    "glance_width": "10",
    "peek_panels": "DSI-2",
    "glance_panels": "DSI-2",
    "glance_turn": "270",
    "glance_inset": "-2.3",
    "glance_notifications": "count",
    "peek_notifications": "icons",
    "glance_along": "28",
    "ruler_band": "16",
    "ruler_bands": "12",
    "ruler_edge": "hinge",
}

# The ruler's colours, band 1 (at the hinge-side edge) first: names the
# owner can say from outside.
RULER_COLOURS = [
    ("red", (1.0, 0.0, 0.0)),
    ("orange", (1.0, 0.55, 0.0)),
    ("yellow", (1.0, 1.0, 0.0)),
    ("lime", (0.6, 1.0, 0.0)),
    ("green", (0.0, 0.8, 0.0)),
    ("cyan", (0.0, 1.0, 1.0)),
    ("blue", (0.1, 0.3, 1.0)),
    ("purple", (0.55, 0.1, 0.9)),
    ("magenta", (1.0, 0.0, 1.0)),
    ("white", (1.0, 1.0, 1.0)),
    ("grey", (0.5, 0.5, 0.5)),
    ("pink", (1.0, 0.6, 0.75)),
]
# The left panel is DSI-1: its outer edge is its left one, the hinge is on
# its right; the right panel (DSI-2) the other way round.
OUTER = {"DSI-1": "left", "DSI-2": "right"}
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
# Where an edge of the panel (in its own, unrotated frame) ends up in the
# picture at a rotation (duo-wc's: 90 = the panel's left edge up).
TURN = {
    0: {"left": "left", "right": "right"},
    90: {"left": "top", "right": "bottom"},
    180: {"left": "right", "right": "left"},
    270: {"left": "bottom", "right": "top"},
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "ambient.conf")


def read_conf(path=None):
    conf = dict(DEFAULTS)
    try:
        with open(path or config_path()) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key.strip() in DEFAULTS:
                        conf[key.strip()] = value.strip()
    except OSError:
        pass
    return conf


def _num(value, default, low, high):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _int(value, default, low=16, high=300):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def plan(state, output, conf=None):
    """What the ambient surface of `output` shows now, from duo-wc's state:
    None (nothing: no ambient screen, or not on this panel), or a dict with
    `kind` (peek / glance), `edge` (left / right / top / bottom of the
    picture), `width` (logical px across the strip), `notifications` (show
    the icons) and `big` (the larger clock)."""
    conf = dict(DEFAULTS, **(conf or {}))
    state = state or {}
    kind = state.get("ambient")
    if kind == "glance-ruler":
        kind = "ruler"
    if kind not in ("peek", "glance", "ruler") or output not in (state.get("lit") or []):
        return None
    panels = conf.get("%s_panels" % ("glance" if kind == "ruler" else kind), "both")
    if panels != "both" and panels != output:
        return None
    outer = OUTER.get(output)
    if outer is None:
        return None
    # the ruler measures the glance's (hinge-side) edge, or with ruler_edge =
    # outer the peek's
    ruler_outer = kind == "ruler" and str(conf.get("ruler_edge", "hinge")).strip() == "outer"
    edge = outer if (kind == "peek" or ruler_outer) else OPPOSITE[outer]
    rotation = (state.get("rotation") or {}).get(output, 0)
    edge = TURN.get(rotation, TURN[0])[edge]
    if kind == "ruler":
        band = _int(conf.get("ruler_band"), 16, 2, 100)
        bands = _int(conf.get("ruler_bands"), 12, 1, len(RULER_COLOURS) * 3)
        return {
            "kind": "ruler",
            "edge": edge,
            "band_px": band,
            "bands": bands,
            "notifications": False,
            "big": False,
            "turn": 0,
        }
    if kind == "peek" and str(conf.get("peek_style", "line")).strip() == "column":
        width = _int(conf.get("peek_width"), 72)
        return {
            "kind": kind,
            "edge": edge,
            "width": width,
            "notifications": True,
            "big": True,
            "turn": 0,
        }
    if kind == "peek":
        # Owner (2026-11-07): like the glance, one line flush with the edge:
        # opened a little, only a sliver along the outer edge shows.
        pturn = 90 if str(conf.get("peek_turn", "270")).strip() == "90" else 270
        return {
            "kind": kind,
            "edge": edge,
            "style": "line",
            "inset": _num(conf.get("peek_inset"), -4.0, -50.0, 300.0),
            "width": _int(conf.get("peek_line_width"), 34, 4, 300),
            "along": _int(conf.get("peek_along"), 28, 0, 1000),
            "notifications": False,
            "big": False,
            "turn": pturn,
            "notes": notes_mode(conf.get("peek_notifications"), "icons"),
            "battery": "inside",
        }
    turn = 90 if str(conf.get("glance_turn", "270")).strip() == "90" else 270
    return {
        "kind": kind,
        "edge": edge,
        # may be negative (a strip partly past the edge puts the text,
        # sized to the strip, nearer the edge) and fractional
        "inset": _num(conf.get("glance_inset"), -2.3, -50.0, 300.0),
        "width": _int(conf.get("glance_width"), 10, 4, 300),
        "along": _int(conf.get("glance_along"), 28, 0, 1000),
        "notifications": False,
        "big": False,
        "notes": notes_mode(conf.get("glance_notifications"), "count"),
        "battery": "beside",
        # The glance text runs along the edge, turned this much
        # counter-clockwise (270: reads top to bottom): one line.
        "turn": turn,
    }


def ruler_bands(plan):
    """The ruler's bands: [(number, colour name, rgb, from_px, to_px)] in
    PHYSICAL px from the hinge-side edge (divide by the scale for logical)."""
    out = []
    for i in range(plan["bands"]):
        name, rgb = RULER_COLOURS[i % len(RULER_COLOURS)]
        out.append((i + 1, name, rgb, i * plan["band_px"], (i + 1) * plan["band_px"]))
    return out


NOTE_ICONS = 4


def notes_mode(value, default):
    """A notification-marker setting: icons | count | off."""
    value = str(value if value is not None else default).strip().lower()
    return value if value in ("icons", "count", "off") else default


def notes_marker(mode, notes):
    """What the one-line strips add after their text for `notes` (the newest
    notifications, one per app): (text, icons) -- `text` is appended to the
    line, `icons` (at most NOTE_ICONS notification icon tuples) are drawn
    after it. Nothing at all without notifications or when off."""
    notes = list(notes or [])
    if not notes or mode == "off":
        return "", []
    if mode == "count":
        return "   \u2022 %d" % len(notes), []
    return "", [n.icon() for n in notes[:NOTE_ICONS]]


def strip_lines(clock, dates):
    """The one-line strips' text, longest first: the time and each form of
    the date (timefmt.full_dates, formatted), then the time alone; the
    drawing takes the first that fits the strip."""
    return ["%s   %s" % (clock, d) for d in dates if d] + [clock]


# -- an incoming call on the strips ---------------------------------------------

CALL_SEP = "   "
ELLIPSIS = "\u2026"


def cut_to_fit(text, fits):
    """The longest start of `text`, cut short with an ellipsis, for which
    `fits(candidate)` is true; None when not even one character fits.
    The widths grow with the length, so the cut is found by halving."""
    lo, hi, best = 1, len(text) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ELLIPSIS
        if fits(candidate):
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    return best


def call_line(caller, clock, room, measure):
    """The strips' one line while a call rings in: who calls (the contact,
    else the number: callsmodel.strip_caller), then the time. `room` is the
    length along the strip that is free for the text, `measure(text)` its
    drawn length. Who calls matters more than the time: the time goes first,
    and only a name (or number) too long for the strip on its own is cut
    short, with an ellipsis."""
    name = " ".join(str(caller or "").split())
    if clock and measure(name + CALL_SEP + clock) <= room:
        return name + CALL_SEP + clock
    if measure(name) <= room:
        return name
    return cut_to_fit(name, lambda t: measure(t) <= room) or name[:1] + ELLIPSIS


def strip_room(plan, w, h):
    """The length along the strip that a line may take (the same at both
    ends, `along`), on a `w` x `h` picture: the strip runs along its edge,
    so a turned panel (a strip on the top or bottom edge) gives its width."""
    length = h if plan["edge"] in ("left", "right") else w
    return length - 2 * plan["along"]


def glance_text(clock, percent):
    """The glance strip's one line: the time, then the battery."""
    if percent is None:
        return clock
    return "%s   %d%%" % (clock, percent)
