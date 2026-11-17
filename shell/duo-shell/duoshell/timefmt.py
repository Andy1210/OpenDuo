"""Clock and date formats that follow the session's locale (LC_TIME, set in
Duo Settings > Language & region): 24-hour where the locale's time format is
(en_GB, hu_HU, de_DE ...), 12-hour with am/pm where it is not (en_US). Formats
are strftime / GLib.DateTime.format strings."""

import locale

try:  # processes that never initialised GTK (the lock screen)
    locale.setlocale(locale.LC_TIME, "")
except locale.Error:
    pass


def uses_24h(t_fmt):
    """True when a nl_langinfo(T_FMT) string shows the hour on 24 hours. Pure."""
    return not any(c in t_fmt for c in ("%I", "%l", "%p", "%P", "%r"))


def month_first(d_fmt):
    """True when a locale writes the month before the day (en_US %m/%d/%y). Pure."""
    d_fmt = d_fmt.replace("%-", "%")
    i, j = d_fmt.find("%d"), d_fmt.find("%m")
    if i < 0 or j < 0:
        i, j = d_fmt.find("%e"), j
    y = d_fmt.find("%Y")
    if 0 <= y < min(i, j) if i >= 0 and j >= 0 else False:  # year first (ISO): day order
        return False
    return 0 <= j < i if i >= 0 and j >= 0 else False


def hm():
    """Hours and minutes."""
    return "%H:%M" if uses_24h(locale.nl_langinfo(locale.T_FMT)) else "%-I:%M %p"


def date_long(day="%-d"):
    """Weekday, day and month name, in the locale's order."""
    if month_first(locale.nl_langinfo(locale.D_FMT)):
        return "%A, %B " + day
    return "%A, " + day + " %B"


# The full date with the weekday, the way each language of the generated
# locales writes it: (with the year,
# without it). GLib.DateTime.format / strftime strings; %B is the month's
# genitive where the language has one (Polish: "8 listopada").
_FULL_DATES = {
    "en_US": ("%A, %B %-d, %Y", "%A, %B %-d"),
    "en": ("%A %-d %B %Y", "%A %-d %B"),
    "hu": ("%Y. %B %-d., %A", "%B %-d., %A"),
    "de": ("%A, %-d. %B %Y", "%A, %-d. %B"),
    "fr": ("%A %-d %B %Y", "%A %-d %B"),
    "es": ("%A, %-d de %B de %Y", "%A, %-d de %B"),
    "it": ("%A %-d %B %Y", "%A %-d %B"),
    "pt": ("%A, %-d de %B de %Y", "%A, %-d de %B"),
    "nl": ("%A %-d %B %Y", "%A %-d %B"),
    "pl": ("%A, %-d %B %Y", "%A, %-d %B"),
    "sv": ("%A %-d %B %Y", "%A %-d %B"),
    "tr": ("%-d %B %Y %A", "%-d %B %A"),
}


def full_date_formats():
    """full_dates() for the session's LC_TIME."""
    try:
        d_fmt = locale.nl_langinfo(locale.D_FMT)
    except (AttributeError, ValueError):
        d_fmt = ""
    return full_dates(lc_time_name(), d_fmt)


def lc_time_name():
    """The session's LC_TIME locale name (en_GB.UTF-8, hu_HU.UTF-8@mon ...)."""
    try:
        return locale.setlocale(locale.LC_TIME) or ""
    except locale.Error:
        return ""


def full_dates(name, d_fmt=None):
    """The full date with the weekday for locale `name`, longest first: the
    weekday and the date with the year, without the year, then with the
    weekday and the month abbreviated (for a strip that has no room). Pure;
    `d_fmt` (nl_langinfo(D_FMT)) orders the day and month of a language
    not in the table."""
    base = (name or "").split(".")[0].split("@")[0]
    lang = base.split("_")[0]
    pair = _FULL_DATES.get(base) or _FULL_DATES.get(lang)
    if pair is None:
        if month_first(d_fmt or ""):
            pair = ("%A, %B %-d, %Y", "%A, %B %-d")
        else:
            pair = ("%A %-d %B %Y", "%A %-d %B")
    with_year, without = pair
    short = without.replace("%A", "%a").replace("%B", "%b")
    return [with_year, without, short]
