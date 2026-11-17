"""The keyboard layouts (pure).

The system's list of keyboard layouts is `org.gnome.desktop.input-sources`
`sources`: the on-screen keyboard's (phosh-osk-stub) and what Duo Settings >
Keyboard edits. The shell sends it to duo-wc (`set_keyboard layouts=...`)
for the seat: a plugged keyboard (Bluetooth, USB) and laptop mode's keyboard
panel type in the same layouts. A source is `('xkb', 'hu')` (an xkb layout,
`'de+nodeadkeys'` with a variant) or `('ibus', 'presage:en')` (phosh-osk-stub's
word completion for a language, on the US layout: duo-wc gets `us` for it).
A layout picked on laptop mode's keyboard that is not in the list is added
there by duo-wc, which tells the shell (`keyboard_layout_added`): it is
added to the system's list too.
"""

SCHEMA = "org.gnome.desktop.input-sources"
KEY = "sources"

# xkb's groups: duo-wc takes at most four layouts.
MAX_LAYOUTS = 4

# A layout's name in its own language (the list on laptop mode's keyboard).
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


def name(layout, variant=""):
    """A layout's name to show: in its own language, the variant after it."""
    base = NAMES.get(layout, (layout or "").upper())
    return "%s (%s)" % (base, variant) if variant else base


def xkb_of_source(source):
    """(layout, variant) for duo-wc of one input source, or None (an input
    method that is no layout)."""
    try:
        kind, ident = source
    except (TypeError, ValueError):
        return None
    if kind == "xkb" and ident:
        layout, _plus, variant = ident.partition("+")
        return (layout, variant)
    if kind == "ibus" and isinstance(ident, str) and ident.startswith("presage:"):
        # phosh-osk-stub's word completion types on the US layout.
        return ("us", "")
    return None


def layouts(sources):
    """The xkb layouts of a list of input sources, in order, each once, at
    most MAX_LAYOUTS."""
    out = []
    for source in sources or []:
        entry = xkb_of_source(source)
        if entry and entry not in out:
            out.append(entry)
    return out[:MAX_LAYOUTS]


def request_args(sources):
    """duo-wc's `set_keyboard` arguments for the system's list, or None (no
    layout in it: duo-wc keeps its own)."""
    entries = layouts(sources)
    if not entries:
        return None
    variants = [v for _l, v in entries]
    return {
        "layouts": ",".join(layout for layout, _v in entries),
        "variants": ",".join(variants) if any(variants) else "",
    }


def wc_layouts(state):
    """duo-wc's list now: [(layout, variant)] (empty: none told)."""
    kb = (state or {}).get("keyboard")
    if not isinstance(kb, dict):
        return []
    return [
        (e.get("layout") or "", e.get("variant") or "")
        for e in kb.get("layouts") or []
        if isinstance(e, dict)
    ]


def needs_push(sources, state):
    """Whether duo-wc's list differs from the system's (so it is sent)."""
    entries = layouts(sources)
    return bool(entries) and entries != wc_layouts(state)


def source_of(layout, variant=""):
    """The input source for an xkb layout."""
    return ("xkb", "%s+%s" % (layout, variant) if variant else layout)


def with_added(sources, layout, variant=""):
    """The system's list with a layout added at its end (an xkb source), or
    None when it is there already (as itself, or as the US layout of a
    word-completion source) or the name is not a layout's."""
    if not layout or not all(c.isalnum() or c in "_-()" for c in layout + variant):
        return None
    if (layout, variant) in layouts(sources):
        return None
    return list(sources or []) + [source_of(layout, variant)]
