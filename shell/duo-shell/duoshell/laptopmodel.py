"""Laptop mode as the shell sees it (pure).

duo-wc's laptop mode makes one panel a desk and the other a keyboard and a
touchpad. duo-wc draws the key caps and the touchpad and reads every touch
there; it has no fonts, so the shell draws the keys' labels on a
see-through surface over the keyboard panel, from the layout duo-wc
publishes in its state (`laptop`: `keys` the keyboard panel, `layout` its
keys' caps in the panel's own frame with what each shows in the layout in
force, `shift` / `caps` / `altgr` the modifiers in force, `menu` the list of
layouts while it is up). On the desk panel the dock stands as a taskbar
(dockmodel); on the keyboard panel the shell shows nothing else.
"""

from . import keyboardmodel

# The quick menu tile's icon: the shell's own (icons/hicolor/symbolic/apps:
# Adwaita has no laptop, its computer is the Panels tile's monitor).
ICON = "duo-laptop-symbolic"


def summary(state):
    """duo-wc's `laptop` while laptop mode is on, else None."""
    lt = (state or {}).get("laptop")
    if isinstance(lt, dict) and lt.get("on"):
        return lt
    return None


def keys_panel(state):
    """The keyboard panel while laptop mode is on, else None."""
    lt = summary(state)
    return lt.get("keys") if lt else None


def desk_panel(state):
    """The desk panel while laptop mode is on, else None."""
    lt = summary(state)
    return lt.get("desk") if lt else None


def tile(state):
    """The quick menu's Laptop mode tile: (sensitive, on, label), the label
    one of "On", "Off", "Needs both panels" (not on, and the pose is not
    dual: duo-wc would refuse) or "Not available" (a duo-wc without laptop
    mode)."""
    state = state or {}
    lt = state.get("laptop")
    if not isinstance(lt, dict):
        return False, False, "Not available"
    if lt.get("on"):
        return True, True, "On"
    if state.get("pose") not in (None, "dual"):
        return False, False, "Needs both panels"
    return True, False, "Off"


def is_letter(text):
    return len(text) == 1 and text.isalpha()


class Label:
    """What one key shows: `main` in the middle, `corner` small at the top
    left (a digit key's symbol, the hint on the exit key), `third` small at
    the bottom right (what it types with AltGr), `size` the main text's
    height as a share of the cap's (words smaller than signs)."""

    def __init__(self, key_id, x, y, w, h, main, corner, kind, size, third=""):
        self.key_id = key_id
        self.x, self.y, self.w, self.h = x, y, w, h
        self.main = main
        self.corner = corner
        self.third = third
        self.kind = kind
        self.size = size

    def key(self):
        return (
            self.key_id,
            self.x,
            self.y,
            self.w,
            self.h,
            self.main,
            self.corner,
            self.third,
            self.kind,
            self.size,
        )

    def __repr__(self):
        return "Label(%r %r/%r)" % (self.key_id, self.main, self.corner)


def label_for(key, shift=False, caps=False, altgr=False):
    """A key's Label with the modifiers in force: letters (the accented ones
    too) in capitals with Shift or Caps Lock (not both), a digit's or a
    sign's shifted symbol big with Shift (the other small in the corner),
    the AltGr symbol (small at the bottom right) big with AltGr."""
    label = key.get("label") or ""
    shifted = key.get("shift") or ""
    third = key.get("altgr") or ""
    kind = key.get("kind") or "key"
    corner = ""
    if altgr and third:
        main, third = third, ""
        size = 0.44
    elif is_letter(label) and shifted:
        main = shifted if shift != caps else label
        size = 0.46
    elif shifted:
        main, corner = (shifted, label) if shift else (label, shifted)
        size = 0.42
    elif kind == "exit":
        main, corner = label, "×"
        size = 0.46
    elif kind == "layout":
        # The layout in force (HU, US); held, the list of layouts.
        main = label
        size = 0.34
    else:
        main = label
        # Words (Esc, Shift, Enter) smaller than signs (arrows, the delete key).
        size = 0.30 if len(label) > 1 else 0.44
    return Label(
        key.get("id"),
        key.get("x", 0),
        key.get("y", 0),
        key.get("w", 0),
        key.get("h", 0),
        main,
        corner,
        kind,
        size,
        third,
    )


def menu_rect(state):
    """The list of layouts' panel (x, y, w, h) while it is up, else None."""
    lt = summary(state)
    menu = lt.get("menu") if lt else None
    if not isinstance(menu, dict):
        return None
    rect = menu.get("rect") or []
    return tuple(rect) if len(rect) == 4 else None


def _overlaps(label, rect):
    x, y, w, h = rect
    return label.x < x + w and x < label.x + label.w and label.y < y + h and y < label.y + label.h


def labels(state):
    """[Label] for the keyboard panel's keys while laptop mode is on (not
    those under the list of layouts while it is up)."""
    lt = summary(state)
    if not lt or not isinstance(lt.get("layout"), dict):
        return []
    shift, caps, altgr = bool(lt.get("shift")), bool(lt.get("caps")), bool(lt.get("altgr"))
    keys = lt["layout"].get("keys") or []
    out = [label_for(k, shift, caps, altgr) for k in keys]
    # A key two rows tall (an ISO board's Enter) keeps a one-row key's text.
    heights = sorted(k.get("h", 0) for k in keys)
    row = heights[len(heights) // 2] if heights else 0
    for label in out:
        if row and label.h > 1.5 * row:
            label.size *= row / label.h
    rect = menu_rect(state)
    if rect:
        out = [label for label in out if not _overlaps(label, rect)]
    return out


def menu_labels(state):
    """[Label] for the list of layouts' entries while it is up: each one's
    name in its own language, one not in the list yet with a "+"."""
    lt = summary(state)
    menu = lt.get("menu") if lt else None
    if not isinstance(menu, dict):
        return []
    out = []
    for item in menu.get("items") or []:
        text = keyboardmodel.name(item.get("layout") or "", item.get("variant") or "")
        if not item.get("configured"):
            text = "+ " + text
        kind = (
            "menu-active"
            if item.get("active")
            else ("menu" if item.get("configured") else "menu-add")
        )
        out.append(
            Label(
                "menu:%s:%s" % (item.get("layout"), item.get("variant") or ""),
                item.get("x", 0),
                item.get("y", 0),
                item.get("w", 0),
                item.get("h", 0),
                text,
                "",
                kind,
                0.36,
            )
        )
    return out


def signature(state):
    """Equal when nothing the labels show changed."""
    lt = summary(state)
    if not lt:
        return None
    layout = lt.get("layout") or {}
    return (
        lt.get("keys"),
        layout.get("w"),
        layout.get("h"),
        tuple(label.key() for label in labels(state)),
        tuple(label.key() for label in menu_labels(state)),
    )
