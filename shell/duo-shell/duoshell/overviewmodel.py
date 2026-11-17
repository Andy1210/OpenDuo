"""What the labels on duo-wc's task overview cards say, and where (pure).

duo-wc draws the overview (live pictures of the windows) but no text: it
keeps a band above each card for a label and publishes the bands on its
event stream (`overview_cards`: output, window id, x, y, w, h in the panel's
own logical frame, and `dragged` while a finger carries the card). The
shell draws the app's icon and the window's title there.
"""


class Label:
    def __init__(self, window, x, y, w, h, desktop_id, text, state=None):
        self.window = window
        self.x, self.y, self.w, self.h = x, y, w, h
        self.desktop_id = desktop_id
        self.text = text
        # None, "closing" (asked to close, not gone yet) or "stuck" (it does
        # not answer: its card has a force-close button).
        self.state = state

    def key(self):
        return (self.window, self.x, self.y, self.w, self.h, self.desktop_id, self.text, self.state)

    def __repr__(self):
        return "Label(%r, %r at %r,%r %rx%r)" % (
            self.window,
            self.text,
            self.x,
            self.y,
            self.w,
            self.h,
        )


def labels(state, index, names=None):
    """{output: [Label]} for the cards of an open overview (none while it is
    closed, and none for a card being carried). `index` matches app ids to
    desktop ids (dockmodel.match_app); `names` maps a desktop id to the
    app's name, used when a window has no title."""
    from . import dockmodel

    out = {}
    if not state or not state.get("overview"):
        return out
    windows = {w.get("id"): w for w in state.get("windows") or []}
    for card in state.get("overview_cards") or []:
        if card.get("dragged"):
            continue
        window = windows.get(card.get("window"), {})
        app_id = window.get("app_id")
        desktop_id = dockmodel.match_app(app_id, index)
        title = (window.get("title") or "").strip()
        text = title or (names or {}).get(desktop_id) or app_id or ""
        state = "stuck" if card.get("stuck") else "closing" if card.get("closing") else None
        out.setdefault(card.get("output"), []).append(
            Label(
                card.get("window"),
                card.get("x", 0),
                card.get("y", 0),
                card.get("w", 0),
                card.get("h", 0),
                desktop_id,
                text,
                state,
            )
        )
    return out


def signature(per_output):
    """Equal when nothing a panel's labels show changed."""
    return {o: tuple(label.key() for label in labels) for o, labels in per_output.items()}
