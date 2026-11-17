"""An external display as a desk or a mirror of the phone: what the shell
shows and asks. Pure (tests/test_displaymodel.py); displayprompt.py, the
quick menu's Display tile and main.Shell use it.

duo-wc's state carries `displays` (compositor/duo-wc/src/mirror.rs):
`on_connect` (ask / desk / mirror), `mirror` (active / both / DSI-1 /
DSI-2), and `connected`, each with `output`, `name`, `use` (desk / mirror),
`asking` and `mirrors` (the panels it shows). A display duo-wc is asking
about mirrors meanwhile; the shell asks "Desktop or Mirror" on the phone,
with "Remember my choice" (that keeps the answer as `on_connect`). A
display in use as a desk gets the shell's desk (deskmodel); a mirror gets
nothing (duo-wc draws the panels there).
"""


def displays(state):
    d = (state or {}).get("displays")
    return d if isinstance(d, dict) else {}


def connected(state):
    """The connected displays, as duo-wc lists them."""
    return [
        c for c in displays(state).get("connected") or [] if isinstance(c, dict) and c.get("output")
    ]


def display(state, output):
    for c in connected(state):
        if c.get("output") == output:
            return c
    return None


def use_of(state, output):
    """ "desk", "mirror", or None (not a display duo-wc reports: an older
    duo-wc, which knows only desks)."""
    c = display(state, output)
    return c.get("use") if c else None


def is_mirror(state, output):
    return use_of(state, output) == "mirror"


def asking(state):
    """The display to ask about now (the first that asks), or None."""
    for c in connected(state):
        if c.get("asking"):
            return c
    return None


def prompt_panel(state):
    """The panel the question shows on: the one in use (active), else the
    first lit panel; a laptop mode keyboard panel never."""
    state = state or {}
    lit = [o for o in state.get("lit") or [] if isinstance(o, str) and o.startswith("DSI-")]
    lt = state.get("laptop") if isinstance(state.get("laptop"), dict) else {}
    keys = lt.get("keys") if lt.get("on") else None
    lit = [o for o in lit if o != keys]
    active = state.get("active_output")
    if active in lit:
        return active
    return lit[0] if lit else None


def answer(output, mode, remember):
    """The requests for an answer: [(request, args)]. `mode` is "desk" or
    "mirror"; remembered, it is what the next display is used as too."""
    if mode not in ("desk", "mirror"):
        raise ValueError(mode)
    out = [("set_display", {"output": output, "mode": mode})]
    if remember:
        out.append(("set_external", {"on_connect": mode}))
    return out


def tile(state):
    """The quick menu's Display tile: None while no display is connected,
    else (on, label): on = in use as a desk, the label "Desktop" or
    "Mirror" (several displays: the first one's)."""
    c = connected(state)
    if not c:
        return None
    desk = c[0].get("use") != "mirror"
    return desk, "Desktop" if desk else "Mirror"


def toggle(state):
    """The quick menu's tap on the Display tile: (request, args) switching
    the first display to the other use, or None."""
    c = connected(state)
    if not c:
        return None
    mode = "desk" if c[0].get("use") == "mirror" else "mirror"
    return "set_display", {"output": c[0]["output"], "mode": mode}


def signature(state):
    """What the question shows, to compare (redraw only on a change)."""
    c = asking(state)
    return None if c is None else (c.get("output"), c.get("name"), prompt_panel(state))
