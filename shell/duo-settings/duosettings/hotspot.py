"""The Hotspot page's pure parts (tests/test_hotspot.py): the state file of
duo2-hotspotd (hotspot/README.md: no password, no device address in it),
the helper's answers, what the rows say."""

import json

HELPER = "/usr/local/sbin/duo2-hotspot"
STATE = "/run/duo2/hotspot.json"

BANDS = [("5", "5 GHz"), ("2.4", "2.4 GHz")]
SECURITIES = [("wpa2-wpa3", "WPA2 / WPA3"), ("wpa3", "WPA3 only"), ("wpa2", "WPA2 only")]
WIFI_CLIENT = [("keep", "Don't start the hotspot"), ("ask", "Ask to switch WiFi off")]
IDLE_OFF = [0, 5, 10, 30, 60]


def read_state(path=STATE):
    try:
        with open(path) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def parse(out):
    """The helper's one JSON line (the last line of its output)."""
    try:
        data = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "no answer"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}


def index_of(pairs, value, default=0):
    for i, (v, _label) in enumerate(pairs):
        if v == value:
            return i
    return default


def idle_index(minutes):
    try:
        return IDLE_OFF.index(int(minutes))
    except (ValueError, TypeError):
        return IDLE_OFF.index(10)


def wifi_on(st):
    return st.get("state") == "on" and bool(st.get("wifi"))


def usb_on(st):
    return st.get("state") == "on" and bool(st.get("usb"))


def status_parts(st):
    """What the switch row's subtitle says, as (key, values) pairs the page
    translates: ("off",), ("on", band, channel), ("beside",), ("own",),
    ("second",), ("no-data",), ("held",)."""
    if not wifi_on(st):
        return [("off",)]
    w = st["wifi"]
    parts = [("on", w.get("band"), w.get("channel"))]
    how = w.get("how")
    if how == "shared":
        parts.append(("beside",))
    elif how == "second":
        parts.append(("second",))
    if w.get("client") in ("radio", "cut"):
        parts.append(("held",))
    if not st.get("uplink"):
        parts.append(("no-data",))
    return parts


def device_rows(st, now):
    """(title, details) per device: its own name or None (the page says
    "Device"), and (address, via, minutes connected or None)."""
    rows = []
    for c in st.get("clients") or []:
        since = c.get("since")
        minutes = max(0, int((now - since) // 60)) if isinstance(since, (int, float)) else None
        rows.append((c.get("name"), (c.get("ip") or "", c.get("via") or "wifi", minutes)))
    return rows


def fmt_bytes(n):
    """1234567 -> '1.2 MB' (decimal units, as phones show data use)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1000.0
    return "%.1f TB" % n


def password_problem(p):
    """None for a password the helper takes (a WPA passphrase: 8 to 63
    printable ASCII characters), else what is wrong with it: "chars" (an
    accent, a tab ...: hostapd would refuse it), "short" or "long"."""
    if not isinstance(p, str):
        return "short"
    if any(not 0x20 <= ord(c) <= 0x7E for c in p):
        return "chars"
    if len(p) < 8:
        return "short"
    if len(p) > 63:
        return "long"
    return None


def valid_password(p):
    return password_problem(p) is None


def valid_ssid(s):
    return (
        isinstance(s, str)
        and bool(s.strip())
        and len(s.encode("utf-8")) <= 32
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)
    )


def masked(password):
    return "•" * max(8, min(len(password or ""), 16))
