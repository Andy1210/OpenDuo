"""Language & region: reading /etc/locale.conf, the names to show, the preview.
Pure parts are tested (tests/test_settings.py); the preview asks libc."""

import locale
import re
import subprocess
import time

LOCALE_CONF = "/etc/locale.conf"
REGION_CONF = "/etc/duo-settings/region.conf"

# name in its own language
NATIVE = {
    "en_US": "English (United States)",
    "en_GB": "English (United Kingdom)",
    "hu_HU": "Magyar (Magyarország)",
    "de_DE": "Deutsch (Deutschland)",
    "fr_FR": "Français (France)",
    "es_ES": "Español (España)",
    "it_IT": "Italiano (Italia)",
    "pt_BR": "Português (Brasil)",
    "nl_NL": "Nederlands (Nederland)",
    "pl_PL": "Polski (Polska)",
    "sv_SE": "Svenska (Sverige)",
    "tr_TR": "Türkçe (Türkiye)",
    "de_AT": "Deutsch (Österreich)",
    "de_CH": "Deutsch (Schweiz)",
    "fr_CA": "Français (Canada)",
    "en_CA": "English (Canada)",
    "en_AU": "English (Australia)",
    "es_MX": "Español (México)",
    "pt_PT": "Português (Portugal)",
    "ru_RU": "Русский (Россия)",
    "cs_CZ": "Čeština (Česko)",
    "sk_SK": "Slovenčina (Slovensko)",
    "ro_RO": "Română (România)",
    "da_DK": "Dansk (Danmark)",
    "nb_NO": "Norsk bokmål (Norge)",
    "fi_FI": "Suomi (Suomi)",
    "el_GR": "Ελληνικά (Ελλάδα)",
}

# first_weekday as `locale first_weekday` prints it
DAY_VALUES = {"mon": "2", "sun": "1", "sat": "7"}


def parse_conf(text):
    """KEY=VALUE lines of a locale.conf (quotes stripped). Pure."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def code(name):
    """'en_GB.UTF-8@mon' -> 'en_GB'. Pure."""
    return re.split(r"[.@]", name or "", maxsplit=1)[0]


def base_locale(name):
    """'en_GB.UTF-8@mon' -> 'en_GB.UTF-8' (the generated locale behind a custom
    first-day one). Pure."""
    return (name or "").split("@", 1)[0]


def native_name(loc):
    c = code(loc)
    return NATIVE.get(c, c)


def choices(generated):
    """[(locale, label)] sorted by label. Pure."""
    return sorted(((g, native_name(g)) for g in generated), key=lambda t: t[1].lower())


def state(conf_text, region_text, generated):
    """The saved choice: (lang, region, day) with day auto|mon|sun|sat; an
    unknown or missing value falls back to the shipped default. Pure."""
    c = parse_conf(conf_text)
    lang = c.get("LANG", "")
    region = base_locale(c.get("LC_TIME", "")) or lang
    if lang not in generated:
        lang = (
            "en_US.UTF-8" if "en_US.UTF-8" in generated else (generated[0] if generated else lang)
        )
    if region not in generated:
        region = "en_GB.UTF-8" if "en_GB.UTF-8" in generated else lang
    day = parse_conf(region_text).get("first_weekday", "auto")
    if day not in ("auto", "mon", "sun", "sat"):
        day = "auto"
    return lang, region, day


def locale_value(region, key):
    """`locale KEY` in that locale (e.g. first_weekday, measurement)."""
    try:
        r = subprocess.run(
            ["locale", key],
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": region, "PATH": "/usr/bin:/bin"},
        )
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def weekday_name(region, first):
    """Name of weekday `first` (1 Sunday, 2 Monday, 7 Saturday) in `region`."""
    # 2001-01-07 was a Sunday
    day = {"1": 0, "2": 1, "7": 6}.get(str(first), 1)
    return strftime_in(
        region, "%A", (2001, 1, 7 + day, 12, 0, 0, (day + 6) % 7, 0, 0)
    )  # tm_wday: Monday 0


def strftime_in(region, fmt, tm):
    old = locale.setlocale(locale.LC_TIME)
    try:
        locale.setlocale(locale.LC_TIME, region)
        return time.strftime(fmt, tm)
    except locale.Error:
        return ""
    finally:
        locale.setlocale(locale.LC_TIME, old)


def number_in(region, value):
    old = locale.setlocale(locale.LC_NUMERIC)
    try:
        locale.setlocale(locale.LC_NUMERIC, region)
        return locale.format_string("%.2f", value, grouping=True)
    except locale.Error:
        return ""
    finally:
        locale.setlocale(locale.LC_NUMERIC, old)


def preview(region, day, now=None):
    """[(label key, text)] for the formats region: date, time, number, first day,
    units. `day` is auto|mon|sun|sat."""
    tm = time.localtime(now)
    first = DAY_VALUES.get(day) or locale_value(region, "first_weekday") or "2"
    meas = locale_value(region, "measurement")
    paper = locale_value(region, "height")
    return [
        ("date", strftime_in(region, "%A, %x", tm)),
        ("time", strftime_in(region, "%X", tm)),
        ("number", number_in(region, 1234567.89)),
        ("weekday", weekday_name(region, first)),
        (
            "units",
            ("metric" if meas == "1" else "imperial") + (", A4" if paper == "297" else ", Letter"),
        ),
    ]
