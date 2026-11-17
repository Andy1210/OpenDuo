"""The Bing image of the day: parsing, file names and retention (pure), and
the one fetch (duo-wallpaper-bing runs it once a day).

Only Bing's public JSON archive endpoint is used, over HTTPS, with a generic
User-Agent and no cookies, account or device data. The image comes from the
same host. Nothing is requested while the Bing switch in Duo Settings is off.
"""

import json
import re
import urllib.parse

HOST = "www.bing.com"
ENDPOINT = "https://%s/HPImageArchive.aspx" % HOST
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) duo2-os-wallpaper"
KEEP = 5
MAX_BYTES = 20 * 1024 * 1024
MIN_BYTES = 30 * 1024
# The sizes asked for, best first: UHD suits a spanned 2754 x 1892.
SIZES = ("UHD", "1920x1080")
MARKETS = [
    "en-US",
    "en-GB",
    "en-AU",
    "en-CA",
    "en-IN",
    "de-DE",
    "fr-FR",
    "fr-CA",
    "es-ES",
    "it-IT",
    "pt-BR",
    "nl-NL",
    "pl-PL",
    "ru-RU",
    "hu-HU",
    "ja-JP",
    "ko-KR",
    "zh-CN",
]
_MARKET = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_URLBASE = re.compile(r"^/th\?id=OHR\.[A-Za-z0-9_.\-]{1,120}$")
_STAMP = re.compile(r"^[0-9]{8}$")
_FILE = re.compile(r"^([0-9]{8})_([a-z]{2}-[A-Z]{2})\.jpg$")


def market_from_locale(lang, default="en-US"):
    """A Bing market from a locale name (hu_HU.UTF-8 -> hu-HU); a language
    alone picks the first listed market of that language."""
    m = re.match(r"^([a-z]{2})(?:_([A-Z]{2}))?", lang or "")
    if not m:
        return default
    if m.group(2):
        market = "%s-%s" % (m.group(1), m.group(2))
        if market in MARKETS:
            return market
    for market in MARKETS:
        if market.startswith(m.group(1) + "-"):
            return market
    return default


def valid_market(market):
    return isinstance(market, str) and bool(_MARKET.match(market))


def request_url(market):
    return (
        ENDPOINT + "?" + urllib.parse.urlencode({"format": "js", "idx": 0, "n": 1, "mkt": market})
    )


def parse_response(text):
    """Bing's JSON -> {"date", "urlbase", "title", "copyright", "link"} or None
    when it is not what the endpoint returns. Every field is checked: the
    urlbase ends up in a request, the date in a file name."""
    try:
        data = json.loads(text)
        item = data["images"][0]
        date = str(item["startdate"])
        urlbase = item["urlbase"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not _STAMP.match(date) or not isinstance(urlbase, str) or not _URLBASE.match(urlbase):
        return None

    def clean(value, limit):
        if not isinstance(value, str):
            return ""
        return "".join(ch for ch in value if ch.isprintable())[:limit]

    link = item.get("copyrightlink")
    if not (isinstance(link, str) and link.startswith("https://") and len(link) < 500):
        link = ""
    return {
        "date": date,
        "urlbase": urlbase,
        "title": clean(item.get("title"), 200),
        "copyright": clean(item.get("copyright"), 300),
        "link": link,
    }


def image_urls(urlbase):
    """The candidate image URLs, best first."""
    return ["https://%s%s_%s.jpg" % (HOST, urlbase, size) for size in SIZES]


def file_name(date, market):
    return "%s_%s.jpg" % (date, market)


def is_jpeg(data):
    return (
        MIN_BYTES <= len(data) <= MAX_BYTES
        and data[:3] == b"\xff\xd8\xff"
        and data[-2:] == b"\xff\xd9"
    )


def to_delete(names, keep=KEEP, current=None):
    """Which of the directory's file names to remove so only the newest `keep`
    pictures stay (by date in the name; the current one always stays). Names
    that are not ours are never touched."""
    ours = sorted(
        (n for n in names if _FILE.match(n)), key=lambda n: _FILE.match(n).group(1), reverse=True
    )
    doomed = [n for n in ours[max(keep, 1) :] if n != current]
    return doomed


def fresh(meta, market, now, hours=6):
    """Is the stored current picture recent enough (same market, fetched less
    than `hours` ago) that no request is needed?"""
    try:
        return (
            meta.get("market") == market and 0 <= now - float(meta.get("fetched", 0)) < hours * 3600
        )
    except (AttributeError, TypeError, ValueError):
        return False
