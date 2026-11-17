"""The app drawer's list view, pure (tests/test_launchermodel.py).

The drawer shows the apps as a grid (the default) or, like Microsoft
Launcher, as an alphabetical list with a letter header over each section
and an A-Z index down the right edge (tap or scrub it: the list jumps to
that letter, a big letter shows by the finger). The choice is
~/.config/duo-shell/launcher.conf (`view = grid | list`), written by the
toggle in the drawer's header and by Duo Settings; read when the drawer
opens.
"""

import os

from .apps import fold

VIEWS = ("grid", "list")
# The index: "#" for names that do not start with a letter, then A-Z.
INDEX = ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "launcher.conf")


def read_view(path=None):
    """`grid` or `list` (anything else, or no file: grid)."""
    try:
        with open(path or config_path()) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    key, value = (p.strip() for p in line.split("=", 1))
                    if key == "view" and value in VIEWS:
                        return value
    except OSError:
        pass
    return "grid"


def write_view(view, path=None):
    if view not in VIEWS:
        raise ValueError(view)
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w") as f:
        f.write("# The app drawer: grid or list (duo-shell; Duo Settings writes it too)\n")
        f.write("view = %s\n" % view)
    os.replace(tmp, path)


def letter_of(name):
    """The section a name goes in: its first letter (accents dropped,
    upper case), or "#" when it does not start with A-Z."""
    folded = fold(name or "").lstrip()
    first = folded[:1].upper()
    return first if "A" <= first <= "Z" else "#"


def sections(names):
    """[(letter, [names...])] in index order, the names sorted within each
    section (folded); only letters that have names."""
    by = {}
    for name in names:
        by.setdefault(letter_of(name), []).append(name)
    return [(letter, sorted(by[letter], key=fold)) for letter in INDEX if letter in by]


def letter_at(y, height, letters=INDEX):
    """The index letter under a finger at `y` on an index `height` tall
    (clamped to the first and the last)."""
    if not letters:
        return None
    if height <= 0:
        return letters[0]
    i = int(y / height * len(letters))
    return letters[max(0, min(len(letters) - 1, i))]


def section_for(letter, present):
    """Where a tap on `letter` jumps: that letter's section, else the next
    one that exists, else the last one (`present`: the letters that have
    apps, in index order). None when there is nothing."""
    if not present:
        return None
    if letter in present:
        return letter
    try:
        at = INDEX.index(letter)
    except ValueError:
        return present[0]
    for candidate in INDEX[at + 1 :]:
        if candidate in present:
            return candidate
    return present[-1]


def front_outputs(message):
    """The panels a `window_front` event from duo-wc names (a window came
    to the front there by itself: a new window, an app's activation): the
    app grid and the quick menu there make way, or the window would sit
    under them (they are layer surfaces above the windows). Anything that
    is not a list of panel names: none."""
    outputs = message.get("outputs") if isinstance(message, dict) else None
    if not isinstance(outputs, list):
        return []
    return [o for o in outputs if isinstance(o, str) and o]
