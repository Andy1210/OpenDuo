"""Which Duo Settings page a quick-menu tile's long press opens (pure;
tests/test_quickpages.py). A short press keeps the tile's own toggle; a
long press closes the menu and opens Settings on the page where that
switch and its details live (`duo-settings PAGE`: Settings' page tags,
shell/duo-settings/duosettings/pages.py and the *page.py modules).
"""

import re
import shlex

# tile key (quickmenu.QuickMenu) -> duo-settings PAGE; a tile not here has
# no page (Airplane mode, Flashlight, Windows): a long press is a tap.
TILE_PAGES = {
    "wifi": "wifi",
    "data": "sim",  # SIM card: mobile data, the SIM PIN, the mailbox
    "hotspot": "hotspot",
    "bluetooth": "bluetooth",
    "location": "privacy",  # Location on / off and the apps that may use it
    "sound": "sound",  # the sound modes' vibration and the ringtone
    "dnd": "notifications",  # Do not disturb and the calls that ring anyway
    "rotate": "rotation",  # Rotation & poses: Auto-rotate itself
    "keyboard": "keyboard",
    "gap": "display",  # Display: the hinge gap beside scale and refresh rate
    "panels": "rotation",  # Rotation & poses: both / left only / right only
    "laptop": "laptop",
    "display": "externaldisplay",
}

# A click that arrives this soon after a long press is the end of that
# press, not a tap of its own.
HOLD_SWALLOW_S = 1.5

_PAGE = re.compile(r"[a-z]+")


def page_for(tile):
    """The Settings page of a tile (its key), or None."""
    return TILE_PAGES.get(tile)


def commandline(executable, page):
    """Duo Settings on `page`: the desktop file's program and the page (a
    tag of letters only, so nothing reaches a shell but these two words)."""
    if not executable or not page or not _PAGE.fullmatch(page):
        return None
    return "%s %s" % (shlex.quote(executable), page)


def swallow_click(held_at, now):
    """True when a click at `now` (monotonic s) ends the long press made
    at `held_at` (or None)."""
    return held_at is not None and 0 <= now - held_at < HOLD_SWALLOW_S
