"""The hotspot's settings (pure parts, tests/test_conf.py).

Three files, all root's:

- /var/lib/duo2/hotspot/settings: the user's choices (name, band, security,
  what happens to the phone's own WiFi, automatic off, 5G), written by
  `duo2-hotspot set` (Duo Settings runs it through sudo);
- /var/lib/duo2/hotspot/password: the password, 0600, generated on first use
  and never logged, printed only by `duo2-hotspot password`;
- /etc/duo2/hotspot.conf: the administrator's switches (the development
  phone's "never switch the WiFi client off", subnets, the uplink devices).
"""

import ipaddress
import re
import secrets

STATE_DIR = "/var/lib/duo2/hotspot"
SETTINGS = STATE_DIR + "/settings"
PASSWORD = STATE_DIR + "/password"
ULA_SEED = STATE_DIR + "/ula"
ADMIN_CONF = "/etc/duo2/hotspot.conf"

BANDS = ("5", "2.4")
SECURITIES = ("wpa2-wpa3", "wpa3", "wpa2")
WIFI_CLIENT = ("keep", "ask")

USER_DEFAULTS = {
    # The network's name; empty = made once (default_ssid) and saved.
    "SSID": "",
    # 5 = 5 GHz (faster, the usual choice), 2.4 = 2.4 GHz (reaches further,
    # every device has it). While the phone itself is on a WiFi network the
    # hotspot uses that network's channel (one radio).
    "BAND": "5",
    # wpa2-wpa3 = both (WPA3-SAE with WPA2 for older devices), wpa3 = WPA3
    # only, wpa2 = WPA2 only. No open hotspot.
    "SECURITY": "wpa2-wpa3",
    # The phone's own WiFi connection when the hotspot cannot run beside it:
    # keep = the hotspot does not start (WiFi is never cut), ask = the user
    # is asked to switch the WiFi connection off for the hotspot.
    "WIFI_CLIENT": "keep",
    # Off by itself after this many minutes without a connected device
    # (0 = never).
    "IDLE_OFF_MIN": "10",
    # Smart 5G may use 5G NR while devices are connected (duo2-wwan reads it
    # from the hotspot's state).
    "USE_5G": "yes",
}

ADMIN_DEFAULTS = {
    # yes = the phone's WiFi client is never disconnected for the hotspot,
    # whatever the user chose (a development phone reached over WiFi ssh).
    "NEVER_CUT_WIFI": "yes",
    # While the WiFi client is connected: yes = the hotspot may use another
    # channel than the client's network when that one cannot be shared
    # (radar / DFS, not allowed for an access point): the chip then switches
    # between the two channels; no = only the client's channel.
    "OTHER_CHANNEL": "yes",
    "MAX_CLIENTS": "8",
    "CHANNEL_5": "36",
    "CHANNEL_24": "6",
    # The first of these that is not in use already (an address or a route of
    # the phone's: the home LAN, the USB development network, the carrier's).
    "SUBNETS_WIFI": "10.15.43.0/24 192.168.43.0/24 172.20.43.0/24",
    "SUBNETS_USB": "10.15.44.0/24 192.168.44.0/24 172.20.44.0/24",
    # The mobile data devices (duo2-wwan's bearer: qmapmux0.N on rmnet_ipa0).
    "UPLINKS": "qmapmux* rmnet_data* wwan*",
    # IPv6 for the devices: nat66 (a private prefix of our own, masqueraded
    # to the phone's mobile address) or no.
    "IPV6": "nat66",
}

PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o, 1/l
_SSID_BAD = re.compile(r"[\x00-\x1f\x7f]")


def parse_kv(text):
    """`KEY=value` lines (comments, blanks, quotes around the value) -> dict."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if re.fullmatch(r"[A-Z0-9_]+", k):
            out[k] = v
    return out


def set_kv(text, key, value):
    """The text with KEY=value set (the first line for the key replaced,
    else appended). The value must already be valid (validate)."""
    lines = (text or "").splitlines()
    line = "%s=%s" % (key, quote(value))
    for i, old in enumerate(lines):
        bare = old.strip()
        if (
            bare
            and not bare.startswith("#")
            and "=" in bare
            and bare.split("=", 1)[0].strip() == key
        ):
            lines[i] = line
            return "\n".join(lines) + "\n"
    lines.append(line)
    return "\n".join(lines) + "\n"


def quote(value):
    """A value as written: quoted when it has spaces or a quote at an end."""
    if value == "" or value != value.strip() or " " in value or value[:1] in "\"'#":
        return '"%s"' % value
    return value


def merged(defaults, values):
    out = dict(defaults)
    out.update({k: v for k, v in (values or {}).items() if k in defaults})
    return out


def yes(v):
    return str(v).strip().lower() in ("yes", "y", "true", "1", "on")


def valid_ssid(s):
    """1..32 bytes of UTF-8, no control characters."""
    if not isinstance(s, str) or not s.strip():
        return False
    return 1 <= len(s.encode("utf-8")) <= 32 and not _SSID_BAD.search(s)


def valid_password(p):
    """A WPA passphrase: 8..63 printable ASCII characters."""
    return isinstance(p, str) and 8 <= len(p) <= 63 and all(0x20 <= ord(c) <= 0x7E for c in p)


def gen_password(rng=None):
    """Twelve letters and digits in three groups (60 bits; typed easily on
    another phone): `abcd-efgh-jkmn`."""
    rng = rng or secrets.SystemRandom()
    chars = [rng.choice(PASSWORD_ALPHABET) for _ in range(12)]
    return "-".join("".join(chars[i : i + 4]) for i in (0, 4, 8))


def default_ssid(rng=None):
    rng = rng or secrets.SystemRandom()
    return "Surface Duo 2 %04d" % rng.randrange(10000)


def validate(key, value):
    """A user setting's value as stored, or ValueError."""
    value = (value or "").strip() if key != "SSID" else (value or "")
    if key == "SSID":
        if not valid_ssid(value):
            raise ValueError("a name of 1 to 32 bytes, no control characters")
        return value
    if key == "BAND":
        value = {"5ghz": "5", "2.4ghz": "2.4", "24": "2.4", "2g": "2.4", "5g": "5"}.get(
            value.lower(), value
        )
        if value not in BANDS:
            raise ValueError("band 5 or 2.4")
        return value
    if key == "SECURITY":
        if value not in SECURITIES:
            raise ValueError("security: " + ", ".join(SECURITIES))
        return value
    if key == "WIFI_CLIENT":
        if value not in WIFI_CLIENT:
            raise ValueError("wifi-client keep or ask")
        return value
    if key == "IDLE_OFF_MIN":
        if not value.isdigit() or int(value) > 240:
            raise ValueError("minutes 0..240")
        return str(int(value))
    if key == "USE_5G":
        if value.lower() not in ("yes", "no"):
            raise ValueError("yes or no")
        return value.lower()
    raise ValueError("unknown setting")


# `duo2-hotspot set NAME VALUE`: the command line's names.
SET_KEYS = {
    "ssid": "SSID",
    "band": "BAND",
    "security": "SECURITY",
    "wifi-client": "WIFI_CLIENT",
    "idle-off": "IDLE_OFF_MIN",
    "use-5g": "USE_5G",
}


def subnets(text):
    """'10.15.43.0/24 ...' -> [IPv4Network] (bad entries skipped)."""
    out = []
    for word in (text or "").split():
        try:
            net = ipaddress.ip_network(word, strict=True)
        except ValueError:
            continue
        if net.version == 4 and 16 <= net.prefixlen <= 29:
            out.append(net)
    return out


def ula_from_seed(seed):
    """The /48 private IPv6 prefix (RFC 4193: fd + 40 random bits) of 5
    seed bytes."""
    if len(seed) < 5:
        raise ValueError("seed too short")
    b = bytes([0xFD]) + bytes(seed[:5]) + bytes(10)
    return ipaddress.IPv6Network((b, 48))
