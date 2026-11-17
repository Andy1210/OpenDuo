"""Swipe-to-dismiss rules for the shell's cards (banners, list rows). Pure
(tests/test_swipe.py): widgets.Swipeable does the GTK side."""

DECIDE = 10.0  # px a finger moves before the direction counts
TAP_SLOP = 10.0  # px under which a touch is a tap
DISMISS_SHARE = 0.35  # of the width (sideways) or height x 0.6 (up)
DISMISS_MIN = 60.0


def decide(dx, dy, up):
    """None (not yet), 'x' (sideways: follows the finger), 'y' (up, only
    when `up`), or 'deny' (vertical: leave it to a scrolled list)."""
    if (dx * dx + dy * dy) ** 0.5 < DECIDE:
        return None
    if abs(dx) >= abs(dy):
        return "x"
    if up and dy < 0:
        return "y"
    return "deny"


def opacity(dx, dy, width):
    t = min(1.0, max(abs(dx), abs(dy)) / max(1.0, width))
    return 1.0 - 0.8 * t


def release(axis, dx, dy, width, height):
    """'tap', 'dismiss' or 'back' when the finger lets go."""
    if axis is None:
        return "tap" if (dx * dx + dy * dy) ** 0.5 < TAP_SLOP else "back"
    if axis == "x":
        return "dismiss" if abs(dx) >= max(DISMISS_MIN, width * DISMISS_SHARE) else "back"
    if axis == "y":
        return "dismiss" if -dy >= max(DISMISS_MIN * 0.6, height * 0.6) else "back"
    return "back"
