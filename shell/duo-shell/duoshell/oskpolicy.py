"""What the bar's keyboard button does (pure; tests/test_oskpolicy.py).

The button looks at the keyboard as it was just BEFORE the tap: shown on this
panel -> hide it; anything else -> show it here: the shell asks duo-wc to give
this panel the keyboard focus (`focus_output`; a touch on a status bar does
not move the keyboard since 2026-11-07), then the keyboard comes up where the
focus is. (Before that, the tap itself moved the focus, and the keyboard with
it, before the click handler ran: hence "before the tap".)
"""

# How far back "before the tap" is: longer than a tap and the keyboard's own
# reaction to the focus change, shorter than two deliberate taps.
SETTLE_S = 0.5


class History:
    """(time, visible, panel) of the on-screen keyboard, newest last."""

    def __init__(self, limit=32):
        self.items = []
        self.limit = limit

    def note(self, now, visible, panel):
        if self.items and self.items[-1][1:] == (visible, panel):
            return
        self.items.append((now, visible, panel))
        del self.items[: -self.limit]

    def at(self, when):
        """The state at time `when` (the oldest known if it is older)."""
        state = self.items[0] if self.items else (0.0, False, None)
        for item in self.items:
            if item[0] <= when:
                state = item
        return state[1], state[2]


def button_hides(history, now, panel):
    """True: the button on `panel` hides the keyboard; False: it shows it."""
    visible, where = history.at(now - SETTLE_S)
    return bool(visible) and where in (None, panel)


def button_lit(visible, away, osk_output, panel):
    """Whether the keyboard button on `panel` is lit: the keyboard says
    `visible` and duo-wc shows it there (`osk_output`; None while the task
    overview is open: lit on every panel). Put away by duo-wc (`away`: a tap
    beside the field, or the keyboard's blink when it starts, held back) it
    is shown nowhere, though the keyboard says visible."""
    return bool(visible) and not away and osk_output in (None, panel)
