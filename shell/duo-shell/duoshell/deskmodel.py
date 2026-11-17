"""The desk: what a desk output shows instead of the phone's home screen.
Pure (tests/test_deskmodel.py); home.py, taskbar.py and deskmenu.py draw it.

A desk is an external display (a monitor or a TV on the USB-C port) or
laptop mode's desk panel (dockmodel.is_desk): windows float there in their
own frames. The phone's home screen (the clock, the weather and the
battery cards, pages of icons) and its status bar are a phone's; a desk
shows a computer's desktop instead:

- **the wallpaper** only (the home screen's picture, nothing on it); a right
  click (or a long press) on it opens a small menu: Settings, Wallpaper,
  and Leave laptop mode on laptop mode's desk;
- **a taskbar** along the bottom (taskbar.py): the app menu's button on
  the left, the pinned apps and the windows of this desk, and a tray on the
  right (WiFi, battery, the time and the date) that opens the quick menu;
- **the app menu** (deskmenu.py) above the taskbar's button: a search field
  that has the keyboard at once (laptop mode's keys type into it), every
  app below it (the app grid's list and search), Enter starts the first;
- **no status bar** at the top (the tray has its icons).

Leaving laptop mode (or unplugging the display) gives the panel its phone
home screen back as it was.
"""

from . import apps as applist
from . import dockmodel


def is_desk(state, output):
    return dockmodel.is_desk(state, output)


def shows_bar(state, output):
    """Whether the phone's status bar stands on an output: not on a desk."""
    return not is_desk(state, output)


def home_mode(state, output):
    """What the home surface of an output shows: "desk" (the wallpaper only)
    or "home" (the phone's home screen)."""
    return "desk" if is_desk(state, output) else "home"


# -- the app menu ---------------------------------------------------------------------


def menu_apps(apps, query):
    """The app menu's list for a search (`apps`: objects with `name` and
    `fields`, as apps.installed_apps gives): the matches, names that start
    with the query first, then alphabetical. An empty query lists them all,
    alphabetical."""
    found = [a for a in apps if applist.matches(query, a.fields)]
    return sorted(found, key=lambda a: (applist.rank(query, a.name), applist.sort_key(a.name)))


def top_hit(apps, query):
    """What Enter starts: the first of menu_apps, or None."""
    found = menu_apps(apps, query)
    return found[0] if found else None


# -- the taskbar ----------------------------------------------------------------------


def front_window(windows):
    """The id of a desk's front window (`windows`: that desk's, back to
    front, as duo-wc lists them): the last one not set aside."""
    for w in reversed(windows or []):
        if not w.get("minimized"):
            return w.get("id")
    return None


def app_windows(windows, app_id):
    """The windows of one app (by its Wayland app id), back to front."""
    return [w for w in windows or [] if app_id and w.get("app_id") == app_id]


def tap_action(windows, app_id):
    """What a tap on a taskbar app does, from that desk's windows (back to
    front): ("launch", None) when the app has no window there, ("minimize",
    id) when its window is the desk's front one (a second tap on it),
    ("activate", id) otherwise (its frontmost window, set aside or not,
    comes to the front)."""
    mine = app_windows(windows, app_id)
    if not mine:
        return ("launch", None)
    front = front_window(windows)
    shown = [w for w in mine if not w.get("minimized")]
    if shown and shown[-1].get("id") == front:
        return ("minimize", front)
    target = shown[-1] if shown else mine[-1]
    return ("activate", target.get("id"))


def is_front_app(windows, app_id):
    """Whether the desk's front window is this app's (its button is lit)."""
    front = front_window(windows)
    return front is not None and any(w.get("id") == front for w in app_windows(windows, app_id))


def taskbar_signature(items, windows):
    """What a taskbar row shows, to compare (dockmodel.signature plus which
    app is in front): the same signature, the buttons stay."""
    fronts = tuple(bool(i.app_id) and is_front_app(windows, i.app_id) for i in items)
    return (dockmodel.signature(items), fronts)


# -- the desk's own menu --------------------------------------------------------------


def desk_menu(state, output):
    """The entries of the menu on the wallpaper of a desk: (action, label
    key). "leave_laptop" only on laptop mode's desk panel."""
    entries = [("settings", "Settings"), ("wallpaper", "Wallpaper")]
    lt = (state or {}).get("laptop")
    if isinstance(lt, dict) and lt.get("on") and lt.get("desk") == output:
        entries.append(("leave_laptop", "Leave laptop mode"))
    return entries


def tray_battery(battery):
    """The tray's battery text (`battery`: (per cent, status) as the bar
    reads it): "57%" or "" when unknown."""
    percent = (battery or (None, None))[0]
    return "" if percent is None else "%d%%" % percent
