"""Settings > Keyboard, the pure part: the keyboard layouts.

The list is the system's: `org.gnome.desktop.input-sources` `sources`, which
the on-screen keyboard (phosh-osk-stub) reads and the shell sends to duo-wc
for a keyboard you connect and for laptop mode's keyboard (duo-shell
keyboardmodel). A source is `('xkb', 'hu')` (`'de+nodeadkeys'` with a
variant) or `('ibus', 'presage:en')`: phosh-osk-stub's word suggestions for a
language, typed on the US layout (so it counts as the US layout). The first
is in force at the start; the keyboards' layout keys switch.
"""

SCHEMA = "org.gnome.desktop.input-sources"
KEY = "sources"

# xkb's groups: a keyboard has at most four layouts.
MAX_LAYOUTS = 4

# Each layout's name in its own language.
NAMES = {
    "us": "English (US)",
    "gb": "English (UK)",
    "hu": "Magyar",
    "de": "Deutsch",
    "at": "Deutsch (Österreich)",
    "ch": "Deutsch (Schweiz)",
    "fr": "Français",
    "be": "Belgique",
    "es": "Español",
    "latam": "Español (Latinoamérica)",
    "it": "Italiano",
    "pt": "Português",
    "br": "Português (Brasil)",
    "pl": "Polski",
    "cz": "Čeština",
    "sk": "Slovenčina",
    "ro": "Română",
    "nl": "Nederlands",
    "se": "Svenska",
    "no": "Norsk",
    "dk": "Dansk",
    "fi": "Suomi",
    "ee": "Eesti",
    "lv": "Latviešu",
    "lt": "Lietuvių",
    "si": "Slovenščina",
    "hr": "Hrvatski",
    "rs": "Српски",
    "bg": "Български",
    "ru": "Русский",
    "ua": "Українська",
    "gr": "Ελληνικά",
    "tr": "Türkçe",
    "ie": "Gaeilge",
    "is": "Íslenska",
}

# Offered first when adding one: US English, then common ones by their names
# in their own language (the same order as duo-wc's COMMON_LAYOUTS).
FIRST = ("us", "de", "gb", "es", "fr", "it", "hu", "pl")

LANGUAGES = {"en": "English", "hu": "Magyar", "de": "Deutsch", "fr": "Français", "es": "Español"}


def layout_of(source):
    """(layout, variant) a source types in, or None (an input method that is
    no layout)."""
    try:
        kind, ident = source
    except (TypeError, ValueError):
        return None
    if kind == "xkb" and ident:
        layout, _plus, variant = ident.partition("+")
        return (layout, variant)
    if kind == "ibus" and isinstance(ident, str) and ident.startswith("presage:"):
        return ("us", "")
    return None


def name(layout, variant=""):
    base = NAMES.get(layout, (layout or "").upper())
    return "%s (%s)" % (base, variant) if variant else base


def describe(source):
    """(title, suggestions) of a source's row: its layout's name, and the
    language of its word suggestions (None: a plain layout)."""
    kind, ident = source
    if kind == "ibus" and ident.startswith("presage:"):
        lang = ident.split(":", 1)[1]
        return (name("us"), LANGUAGES.get(lang, lang))
    lt = layout_of(source)
    if lt is None:
        return (ident, None)
    return (name(*lt), None)


def layouts(sources):
    """The keyboard layouts of the list, each once."""
    out = []
    for source in sources or []:
        lt = layout_of(source)
        if lt and lt not in out:
            out.append(lt)
    return out


def move(sources, index, step):
    """The list with entry `index` moved by `step` (-1 up, +1 down); None if
    it cannot move."""
    j = index + step
    if not 0 <= index < len(sources) or not 0 <= j < len(sources):
        return None
    out = list(sources)
    out[index], out[j] = out[j], out[index]
    return out


def remove(sources, index):
    """The list without entry `index`; None when it is the last layout (a
    keyboard needs one)."""
    if not 0 <= index < len(sources):
        return None
    out = list(sources[:index]) + list(sources[index + 1 :])
    if not layouts(out):
        return None
    return out


def can_add(sources):
    return len(layouts(sources)) < MAX_LAYOUTS


def addable(sources):
    """[(layout, name)] that can be added: FIRST's order, then the rest by
    name; none when the list is full."""
    if not can_add(sources):
        return []
    have = {lt for lt, _v in layouts(sources)}
    rest = sorted((k for k in NAMES if k not in FIRST), key=lambda k: NAMES[k].lower())
    return [(k, NAMES[k]) for k in list(FIRST) + rest if k not in have]


def add(sources, layout):
    """The list with an xkb layout added at its end; None if it is there or
    the list is full."""
    if not layout or not can_add(sources) or (layout, "") in layouts(sources):
        return None
    return list(sources or []) + [("xkb", layout)]
