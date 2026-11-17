"""The installed apps for the app grid, and how the search matches them.

`matches` and `sort_key` are pure (tests/test_apps.py); `installed_apps`
reads the .desktop files through Gio (only apps that want to be shown:
NoDisplay, Hidden, OnlyShowIn other desktops are left out by
Gio.AppInfo.should_show).
"""

import unicodedata

# Never in the grid: the shell's own pieces and things that are not apps.
HIDDEN_IDS = {
    "sm.puri.Squeekboard.desktop",
    "sm.puri.OSK0.desktop",
    "foot-server.desktop",
    "footclient.desktop",
}


# Folded names that an Android app (Waydroid's waydroid.<package>.desktop)
# shares with a host app: "Contacts" twice in the grid. Those Android apps
# are labelled "Contacts (Android)"; an Android app with a name of its own
# keeps it. Kept up to date by installed_apps().
_android_clashes = None


def is_android(app_id):
    return (app_id or "").startswith("waydroid.")


def android_clashes(pairs):
    """The folded names an Android app shares with a host app; `pairs` are
    (desktop id, name) (pure)."""
    host = {fold(name) for app_id, name in pairs if not is_android(app_id)}
    return {fold(name) for app_id, name in pairs if is_android(app_id) and fold(name) in host}


def label(app_id, name, clashes=None):
    """The name to show for an app: an Android app whose name a host app
    has too gets " (Android)"."""
    if clashes is None:
        if _android_clashes is None:
            installed_apps()
        clashes = _android_clashes
    if is_android(app_id) and fold(name) in clashes:
        from .i18n import _

        # Translators: an Android app (Waydroid) named like a host app.
        return _("%s (Android)") % name
    return name


def info_label(info, fallback=""):
    """label() for a Gio.AppInfo (None: the fallback)."""
    if info is None:
        return fallback
    return label(info.get_id() or "", info.get_display_name() or fallback)


def fold(text):
    """Lower case, accents dropped: what search compares."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def matches(query, fields):
    """Whether every word of the query is in one of the fields (name, generic
    name, keywords, executable...)."""
    words = fold(query).split()
    if not words:
        return True
    hay = [fold(f) for f in fields if f]
    return all(any(w in h for h in hay) for w in words)


def rank(query, name):
    """Lower is better: a name that starts with the query first."""
    q, n = fold(query).strip(), fold(name)
    if not q:
        return 0
    if n.startswith(q):
        return 0
    if any(part.startswith(q) for part in n.split()):
        return 1
    return 2


def sort_key(name):
    return fold(name)


def short_name(name, display_name=None, generic=None):
    """The label under an icon: the shorter of the desktop file's Name and
    its display name (X-GNOME-FullName: "Firefox ESR Web Browser" for
    "Firefox ESR"), without a trailing generic name ("Chromium Web
    Browser" with GenericName "Web Browser": "Chromium"); empty ones do
    not count."""
    names = [n.strip() for n in (name, display_name) if isinstance(n, str) and n.strip()]
    if not names:
        return ""
    short = min(names, key=len)
    generic = generic.strip() if isinstance(generic, str) else ""
    if generic and fold(short).endswith(" " + fold(generic)):
        rest = short[: len(short) - len(generic)].strip()
        if rest:
            short = rest
    return short


class App:
    """One launchable app."""

    def __init__(self, app_id, name, icon, fields, info, short=None):
        self.app_id = app_id
        self.name = name
        self.short = short or name
        self.icon = icon
        self.fields = fields
        self.info = info


def installed_apps():
    from gi.repository import Gio

    apps = []
    for info in Gio.AppInfo.get_all():
        try:
            if not info.should_show():
                continue
            app_id = info.get_id() or ""
            if app_id in HIDDEN_IDS:
                continue
            name = info.get_display_name() or info.get_name() or app_id
            fields = [name, info.get_name(), info.get_description(), info.get_executable()]
            if isinstance(info, Gio.DesktopAppInfo):
                fields.append(info.get_generic_name())
                fields.extend(info.get_keywords() or [])
            generic = info.get_generic_name() if isinstance(info, Gio.DesktopAppInfo) else None
            apps.append(
                App(
                    app_id,
                    name,
                    info.get_icon(),
                    fields,
                    info,
                    short_name(info.get_name(), name, generic) or name,
                )
            )
        except Exception:  # one broken .desktop file must not empty the grid
            continue
    global _android_clashes
    _android_clashes = android_clashes([(a.app_id, a.name) for a in apps])
    for a in apps:
        shown = label(a.app_id, a.name)
        if shown != a.name:
            a.fields.append(shown)
            a.name = a.short = shown
    apps.sort(key=lambda a: sort_key(a.name))
    return apps
