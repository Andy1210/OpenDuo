"""Symbolic icons from the icon theme for surfaces drawn with plain cairo
(the lock screen has no GTK and no librsvg binding): the icon's SVG path
data is read from the theme's file and filled in one colour, as GTK draws a
symbolic icon. Pure apart from reading the file; tests/test_svgicon.py.

Only what symbolic icons use is understood: paths (M L H V C S Q T Z, upper
and lower case) in a viewBox. An icon with anything else (an arc, a
transform, a shape element other than <path>) is not drawn: the caller
falls back to its own drawing."""

import os
import re
import xml.etree.ElementTree as ET

THEME_DIRS = ("/usr/share/icons/Adwaita/symbolic", "/usr/share/icons/hicolor/symbolic")
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtZzAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "Z": 0}
_cache = {}


class Unsupported(ValueError):
    pass


def parse_path(d):
    """SVG path data -> [("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y)
    | ("Z",)], absolute; quadratic curves become cubic ones."""
    tokens = _TOKEN.findall(d or "")
    if len(" ".join(tokens).replace(" ", "")) < len(re.sub(r"[\s,]", "", d or "")):
        raise Unsupported("unexpected characters in the path")
    ops = []
    x = y = sx = sy = 0.0
    last_c2 = last_q = None  # the previous curve's control point (S / T)
    cmd = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            if t.upper() == "A":
                raise Unsupported("arcs are not drawn")
            cmd = t
            i += 1
            if cmd in "Zz":
                ops.append(("Z",))
                x, y = sx, sy
                last_c2 = last_q = None
                continue
        elif cmd is None:
            raise Unsupported("a number before the first command")
        n = _ARGS[cmd.upper()]
        vals = tokens[i : i + n]
        if len(vals) < n or any(v.isalpha() for v in vals):
            raise Unsupported("too few numbers after %s" % cmd)
        v = [float(a) for a in vals]
        i += n
        rel = cmd.islower()
        c = cmd.upper()
        if c in "MLT":
            nx, ny = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            if c == "M":
                ops.append(("M", nx, ny))
                sx, sy = nx, ny
                cmd = "l" if rel else "L"  # further pairs are lines
                last_c2 = last_q = None
            elif c == "L":
                ops.append(("L", nx, ny))
                last_c2 = last_q = None
            else:  # T: the reflected quadratic control point
                qx, qy = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                ops.append(_quad(x, y, qx, qy, nx, ny))
                last_q, last_c2 = (qx, qy), None
            x, y = nx, ny
        elif c == "H":
            x = x + v[0] if rel else v[0]
            ops.append(("L", x, y))
            last_c2 = last_q = None
        elif c == "V":
            y = y + v[0] if rel else v[0]
            ops.append(("L", x, y))
            last_c2 = last_q = None
        elif c == "C":
            p = [v[k] + (x if k % 2 == 0 else y) if rel else v[k] for k in range(6)]
            ops.append(("C", *p))
            last_c2, last_q = (p[2], p[3]), None
            x, y = p[4], p[5]
        elif c == "S":
            p = [v[k] + (x if k % 2 == 0 else y) if rel else v[k] for k in range(4)]
            c1 = (2 * x - last_c2[0], 2 * y - last_c2[1]) if last_c2 else (x, y)
            ops.append(("C", c1[0], c1[1], *p))
            last_c2, last_q = (p[0], p[1]), None
            x, y = p[2], p[3]
        elif c == "Q":
            p = [v[k] + (x if k % 2 == 0 else y) if rel else v[k] for k in range(4)]
            ops.append(_quad(x, y, p[0], p[1], p[2], p[3]))
            last_q, last_c2 = (p[0], p[1]), None
            x, y = p[2], p[3]
    return ops


def _quad(x0, y0, qx, qy, x, y):
    return (
        "C",
        x0 + 2 / 3 * (qx - x0),
        y0 + 2 / 3 * (qy - y0),
        x + 2 / 3 * (qx - x),
        y + 2 / 3 * (qy - y),
        x,
        y,
    )


def parse_svg(text):
    """(width, height, [ops...]) of a symbolic icon's SVG; Unsupported for
    anything this does not draw."""
    root = ET.fromstring(text)
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) == 4:
        w, h = float(vb[2]), float(vb[3])
        ox, oy = float(vb[0]), float(vb[1])
    else:
        w = float(re.sub(r"px$", "", root.get("width") or "16"))
        h = float(re.sub(r"px$", "", root.get("height") or "16"))
        ox = oy = 0.0
    ops = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("svg", "g", "defs", "title", "desc", "metadata"):
            if el.get("transform"):
                raise Unsupported("transforms are not drawn")
            continue
        if tag != "path":
            raise Unsupported("<%s> is not drawn" % tag)
        if el.get("transform"):
            raise Unsupported("transforms are not drawn")
        if (el.get("opacity") or "1").strip() not in ("1", "1.0"):
            continue  # a faded part (an "off" state): left out
        for op in parse_path(el.get("d")):
            if ox or oy:
                op = (op[0],) + tuple(a - (ox if k % 2 == 0 else oy) for k, a in enumerate(op[1:]))
            ops.append(op)
    if not ops:
        raise Unsupported("no path")
    return w, h, ops


def find(name, dirs=THEME_DIRS):
    for base in dirs:
        try:
            subs = sorted(os.listdir(base))
        except OSError:
            continue
        for sub in subs:
            path = os.path.join(base, sub, name + ".svg")
            if os.path.isfile(path):
                return path
    return None


def load(name, dirs=THEME_DIRS):
    """The parsed icon, or None (not installed, or not drawable here). Kept."""
    key = (name, tuple(dirs))
    if key not in _cache:
        icon = None
        path = find(name, dirs)
        if path:
            try:
                with open(path) as f:
                    icon = parse_svg(f.read(65536))
            except (OSError, ValueError, ET.ParseError):
                icon = None
        _cache[key] = icon
    return _cache[key]


def draw(ctx, icon, cx, cy, size):
    """Fill `icon` (load()'s) centred on (cx, cy), `size` px for its longer
    side, in the context's current source (white for the lock). The path
    only: the caller's ctx.save()/restore() are not needed."""
    w, h, ops = icon
    k = size / max(w, h)
    x0, y0 = cx - w * k / 2, cy - h * k / 2
    ctx.new_path()
    for op in ops:
        if op[0] == "M":
            ctx.move_to(x0 + op[1] * k, y0 + op[2] * k)
        elif op[0] == "L":
            ctx.line_to(x0 + op[1] * k, y0 + op[2] * k)
        elif op[0] == "C":
            ctx.curve_to(
                x0 + op[1] * k,
                y0 + op[2] * k,
                x0 + op[3] * k,
                y0 + op[4] * k,
                x0 + op[5] * k,
                y0 + op[6] * k,
            )
        else:
            ctx.close_path()
    ctx.fill()
