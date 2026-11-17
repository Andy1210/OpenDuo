"""The WiFi country (regulatory domain): where it comes from and what to
set (hotspot/README.md "Regulatory domain"). Pure: duo2-wifi-country runs
the commands.

The chip's regulatory domain is the firmware's (ath11k, self-managed). It
boots in a world roaming domain (all of 5 GHz no-IR), 11d may pick the
country from the beacons it scans, and a user hint (`iw reg set XX`) sets
one; this module decides that hint from the country of the mobile network
(its MCC), like Android does.
"""

import re

# ITU-T E.212 mobile country codes -> ISO 3166 alpha-2. An MCC shared by
# several territories (340 French Antilles, 289 / 221) is left out: no
# guess, the country then comes from 11d.
MCC = {
    "202": "GR",
    "204": "NL",
    "206": "BE",
    "208": "FR",
    "212": "MC",
    "213": "AD",
    "214": "ES",
    "216": "HU",
    "218": "BA",
    "219": "HR",
    "220": "RS",
    "222": "IT",
    "225": "VA",
    "226": "RO",
    "228": "CH",
    "230": "CZ",
    "231": "SK",
    "232": "AT",
    "234": "GB",
    "235": "GB",
    "238": "DK",
    "240": "SE",
    "242": "NO",
    "244": "FI",
    "246": "LT",
    "247": "LV",
    "248": "EE",
    "250": "RU",
    "255": "UA",
    "257": "BY",
    "259": "MD",
    "260": "PL",
    "262": "DE",
    "266": "GI",
    "268": "PT",
    "270": "LU",
    "272": "IE",
    "274": "IS",
    "276": "AL",
    "278": "MT",
    "280": "CY",
    "282": "GE",
    "283": "AM",
    "284": "BG",
    "286": "TR",
    "288": "FO",
    "290": "GL",
    "292": "SM",
    "293": "SI",
    "294": "MK",
    "295": "LI",
    "297": "ME",
    "302": "CA",
    "308": "PM",
    "310": "US",
    "311": "US",
    "312": "US",
    "313": "US",
    "314": "US",
    "315": "US",
    "316": "US",
    "330": "PR",
    "332": "VI",
    "334": "MX",
    "338": "JM",
    "342": "BB",
    "344": "AG",
    "346": "KY",
    "348": "VG",
    "350": "BM",
    "352": "GD",
    "354": "MS",
    "356": "KN",
    "358": "LC",
    "360": "VC",
    "362": "CW",
    "363": "AW",
    "364": "BS",
    "365": "AI",
    "366": "DM",
    "368": "CU",
    "370": "DO",
    "372": "HT",
    "374": "TT",
    "376": "TC",
    "400": "AZ",
    "401": "KZ",
    "402": "BT",
    "404": "IN",
    "405": "IN",
    "406": "IN",
    "410": "PK",
    "412": "AF",
    "413": "LK",
    "414": "MM",
    "415": "LB",
    "416": "JO",
    "417": "SY",
    "418": "IQ",
    "419": "KW",
    "420": "SA",
    "421": "YE",
    "422": "OM",
    "424": "AE",
    "425": "IL",
    "426": "BH",
    "427": "QA",
    "428": "MN",
    "429": "NP",
    "430": "AE",
    "431": "AE",
    "432": "IR",
    "434": "UZ",
    "436": "TJ",
    "437": "KG",
    "438": "TM",
    "440": "JP",
    "441": "JP",
    "450": "KR",
    "452": "VN",
    "454": "HK",
    "455": "MO",
    "456": "KH",
    "457": "LA",
    "460": "CN",
    "461": "CN",
    "466": "TW",
    "467": "KP",
    "470": "BD",
    "472": "MV",
    "502": "MY",
    "505": "AU",
    "510": "ID",
    "514": "TL",
    "515": "PH",
    "520": "TH",
    "525": "SG",
    "528": "BN",
    "530": "NZ",
    "536": "NR",
    "537": "PG",
    "539": "TO",
    "540": "SB",
    "541": "VU",
    "542": "FJ",
    "543": "WF",
    "544": "AS",
    "545": "KI",
    "546": "NC",
    "547": "PF",
    "548": "CK",
    "549": "WS",
    "550": "FM",
    "551": "MH",
    "552": "PW",
    "553": "TV",
    "555": "NU",
    "602": "EG",
    "603": "DZ",
    "604": "MA",
    "605": "TN",
    "606": "LY",
    "607": "GM",
    "608": "SN",
    "609": "MR",
    "610": "ML",
    "611": "GN",
    "612": "CI",
    "613": "BF",
    "614": "NE",
    "615": "TG",
    "616": "BJ",
    "617": "MU",
    "618": "LR",
    "619": "SL",
    "620": "GH",
    "621": "NG",
    "622": "TD",
    "623": "CF",
    "624": "CM",
    "625": "CV",
    "626": "ST",
    "627": "GQ",
    "628": "GA",
    "629": "CG",
    "630": "CD",
    "631": "AO",
    "632": "GW",
    "633": "SC",
    "634": "SD",
    "635": "RW",
    "636": "ET",
    "637": "SO",
    "638": "DJ",
    "639": "KE",
    "640": "TZ",
    "641": "UG",
    "642": "BI",
    "643": "MZ",
    "645": "ZM",
    "646": "MG",
    "647": "RE",
    "648": "ZW",
    "649": "NA",
    "650": "MW",
    "651": "LS",
    "652": "BW",
    "653": "SZ",
    "654": "KM",
    "655": "ZA",
    "657": "ER",
    "658": "SH",
    "659": "SS",
    "702": "BZ",
    "704": "GT",
    "706": "SV",
    "708": "HN",
    "710": "NI",
    "712": "CR",
    "714": "PA",
    "716": "PE",
    "722": "AR",
    "724": "BR",
    "730": "CL",
    "732": "CO",
    "734": "VE",
    "736": "BO",
    "738": "GY",
    "740": "EC",
    "742": "GF",
    "744": "PY",
    "746": "SR",
    "748": "UY",
    "750": "FK",
}

_ALPHA2 = re.compile(r"^[A-Z]{2}$")


def country_of_mcc(mcc):
    """'262' (or '26201', the operator code) -> 'DE'; None when unknown."""
    code = str(mcc or "")[:3]
    return MCC.get(code) if code.isdigit() and len(code) == 3 else None


def network_mcc(modem):
    """The MCC of the network the modem is registered on (or camps on for
    emergency calls), from `mmcli -m any -J` (parsed JSON); None without
    one. The SIM's own country is never used: abroad it is not where the
    phone is."""
    try:
        g3 = modem["modem"]["3gpp"]
    except (KeyError, TypeError):
        return None
    if not isinstance(g3, dict):
        return None
    code = str(g3.get("operator-code") or "")
    if not re.fullmatch(r"\d{5,6}", code):
        return None
    return code[:3]


def parse_conf(text):
    """/etc/duo2/wifi-country.conf -> 'auto' | 'off' | 'XX'. Anything else
    (a typo) is 'off': no country is set from a file we cannot read."""
    mode = "auto"
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "COUNTRY":
            continue
        value = value.strip().strip("\"'")
        if value.lower() in ("auto", ""):
            mode = "auto"
        elif value.lower() == "off":
            mode = "off"
        elif _ALPHA2.match(value.upper()):
            mode = value.upper()
        else:
            mode = "off"
    return mode


_RULE = re.compile(r"^\s*\((\d+)\s*-\s*(\d+)\s*@\s*\d+\)(.*)$")


def parse_reg_get(text):
    """`iw reg get` -> {"global": "00", "phys": {"phy0": {"country": "DE",
    "self_managed": True, "rules": [(start, end, {flags})]}}}."""
    out = {"global": None, "phys": {}}
    cur = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "global":
            cur = {"country": None, "rules": []}
            out["_global"] = cur
            continue
        m = re.match(r"^phy#(\d+)(\s*\(self-managed\))?", line)
        if m:
            cur = {"country": None, "self_managed": bool(m.group(2)), "rules": []}
            out["phys"]["phy" + m.group(1)] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"^country (\S\S):", line)
        if m:
            cur["country"] = m.group(1)
            continue
        m = _RULE.match(raw)
        if m:
            rest = re.sub(r"\([^)]*\)", "", m.group(3))  # the power and CAC groups
            flags = {f.strip() for f in rest.split(",") if f.strip()}
            cur["rules"].append((int(m.group(1)), int(m.group(2)), flags))
    g = out.pop("_global", None)
    out["global"] = g["country"] if g else None
    return out


def no_ir_5ghz(phy):
    """How many of phy's 5 GHz rules are no-IR (iw: PASSIVE-SCAN or NO-IR,
    no access point there): 'all' / 'some' / 'none'; None without 5 GHz
    rules."""
    rules = [r for r in (phy or {}).get("rules", []) if 5150 <= r[0] < 5925]
    if not rules:
        return None
    no_ir = [r for r in rules if {"PASSIVE-SCAN", "NO-IR"} & r[2]]
    if len(no_ir) == len(rules):
        return "all"
    return "some" if no_ir else "none"


def regdb_countries(blob):
    """The countries the WLAN firmware knows: its regdb.bin (the one the
    firmware loads) starts with a 2-byte header and a table of 16-byte
    records {u16 numeric code, u16 regdomain pair, u8, alpha2 'XX\\0', alpha2
    again, ...}. None when the file does not look like that (then nothing is
    filtered)."""
    if not blob or len(blob) < 2 + 16 * 8:
        return None
    out = set()
    off = 2
    while off + 16 <= len(blob):
        a = blob[off + 5 : off + 8]
        if not (65 <= a[0] <= 90 and 65 <= a[1] <= 90 and a[2] == 0):
            break
        out.add(a[:2].decode())
        off += 16
    return out if len(out) >= 8 else None


def decide(mode, mcc, phy_country, known, last, now, ap_up, min_interval=60):
    """What to do now -> (action, target, why). action: 'set' (iw reg set
    target), 'keep' (nothing to do) or 'defer' (later: the hotspot runs).

    mode: parse_conf's; mcc: network_mcc's; phy_country: what phy0 runs;
    known: regdb_countries' (None = unknown, no filter); last: {"set": XX,
    "at": seconds} of the last hint this boot, or None; ap_up: an access
    point interface exists."""
    if mode == "off":
        return "keep", None, "off"
    if mode == "auto":
        target = country_of_mcc(mcc)
        if not target:
            return "keep", None, "no-network" if not mcc else "unknown-mcc"
    else:
        target = mode
    if known is not None and target not in known:
        return "keep", target, "unknown-to-firmware"
    if last and last.get("set") == target:
        return "keep", target, "already-set"
    if ap_up:
        # A new country under a running access point could take its
        # channel away (cfg80211 then stops it): after the hotspot.
        return "defer", target, "hotspot"
    if last and now - last.get("at", 0) < min_interval:
        return "defer", target, "too-soon"
    why = "config" if mode != "auto" else "network"
    if phy_country == target:
        why += ", same as 11d"
    return "set", target, why
