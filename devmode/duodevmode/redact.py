"""Masking for a bug report: what identifies the phone, its owner or where
they are is replaced by a placeholder before anything is written.

The patterns are scripts/check-personal-data's (installed beside this as
check_personal_data.py): IMEI / IMSI / ICCID / EID-like numbers,
international phone numbers, MAC and Bluetooth addresses, private IPv4
addresses, /home/NAME, e-mail addresses, keys and tokens. On top of them,
for logs: every other IP address but loopback and the project's own subnets
(public addresses say where the phone is), SSIDs (the known networks' names
and whatever a log line calls an SSID), literal names the phone knows (the
users, the host name, Bluetooth devices' names, the serial number, the
machine id), coordinates on a line about a location, and digit runs on a
line about a call or a message.

Each value gets one placeholder for the whole report ([mac-1], [ip-2]...):
the same address stays recognisable from line to line, never readable.
"""

import importlib.machinery
import importlib.util
import ipaddress
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# Installed: beside this package; in the repository: scripts/.
CHECKER_PATHS = (
    os.path.join(os.path.dirname(HERE), "check_personal_data.py"),
    os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts", "check-personal-data"),
)

IPV4 = re.compile(r"(?<![0-9.])((?:\d{1,3}\.){3}\d{1,3})(?![0-9]|\.\d)")
IPV6 = re.compile(
    r"(?<![0-9A-Fa-f:.])((?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}|(?:[0-9A-Fa-f]{1,4}:){1,6}:"
    r"(?:[0-9A-Fa-f]{1,4}:?){0,6}[0-9A-Fa-f]{0,4})(?![0-9A-Fa-f:])"
)
SSID_IN_LINE = re.compile(
    r"(?i)\bssid\b[\s=:]{0,3}(?:'([^'\n]{1,32})'|\"([^\"\n]{1,32})\"|(\S{1,32}))"
)
LOCATION_LINE = re.compile(
    r"(?i)latitude|longitude|\blat\b|\blon\b|\blng\b|location|position|\bgeo"
)
COORD = re.compile(r"(?<![\d.])-?\d{1,3}\.\d{4,}(?![\d.])")
CALL_LINE = re.compile(r"(?i)number|caller|callee|msisdn|tel:|\bsms\b|\bcall\b|voicemail|contact")
DIGIT_RUN = re.compile(r"(?<![\w.:])\+?\d[\d \-]{5,}\d(?![\w.:])")

KEEP_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "0.0.0.0/32",
        "255.255.255.255/32",
        "224.0.0.0/4",
        "10.15.19.0/24",  # the USB development network
        "10.15.43.0/24",
        "10.15.44.0/24",  # the hotspot's subnets
        "192.168.43.0/24",
        "192.168.44.0/24",
        "172.20.43.0/24",
        "172.20.44.0/24",
        "192.168.240.0/24",  # Waydroid
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",  # documentation
        "::1/128",
        "::/128",
        "ff00::/8",
        "2001:db8::/32",
    )
]


def load_checker(paths=CHECKER_PATHS):
    for path in paths:
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("check_personal_data", path)
            spec = importlib.util.spec_from_loader("check_personal_data", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise ImportError("check-personal-data not found (%s)" % ", ".join(paths))


# Names that are the same on every phone: never masked as a literal.
GENERIC_NAMES = {"duo", "duo2", "duo2admin", "root", "localhost", "openduo", "debian", "user"}


def _bounded(value):
    """A pattern for value as a whole word: not inside a longer name or number."""
    head = r"(?<![A-Za-z0-9_])" if value[:1].isalnum() or value[:1] == "_" else ""
    tail = r"(?![A-Za-z0-9_])" if value[-1:].isalnum() or value[-1:] == "_" else ""
    return re.compile(head + re.escape(value) + tail)


def _keep_ip(text):
    try:
        addr = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return True  # not an address (a version number, a time)
    return any(addr in net for net in KEEP_NETS if net.version == addr.version)


class Redactor:
    """mask(line) -> the line with every finding replaced by its placeholder."""

    def __init__(self, literals=(), checker=None):
        self.checker = checker or load_checker()
        # The checker's local list is the developer's, not this phone's: none.
        self.rules = self.checker.Rules()
        generic = {g.lower() for g in getattr(self.checker, "HOME_OK", ())} | GENERIC_NAMES
        lits = {x.strip() for x in literals if x and len(x.strip()) >= 3}
        lits = sorted((x for x in lits if x.lower() not in generic), key=len, reverse=True)
        self.literals = [_bounded(x) for x in lits]
        self.names = {}
        self.counts = {}

    def placeholder(self, category, value):
        key = (category, value)
        if key not in self.names:
            self.counts[category] = self.counts.get(category, 0) + 1
            self.names[key] = "[%s-%d]" % (category, self.counts[category])
        return self.names[key]

    def findings(self, line):
        out = []
        for category, text in self.rules.scan_line(line):
            if category == "secret":
                text = text.split(" ", 2)[2] if text.count(" ") >= 2 else text
            elif category == "home":
                text = text[len("/home/") :]
                category = "user"
            out.append((category, text))
        for rx in self.literals:
            for m in rx.finditer(line):
                out.append(("name", m.group(0)))
        for m in IPV4.finditer(line):
            if not _keep_ip(m.group(1)):
                out.append(("ip", m.group(1)))
        for m in IPV6.finditer(line):
            value = m.group(1)
            if value.count(":") >= 2 and not _keep_ip(value):
                out.append(("ip", value))
        for m in SSID_IN_LINE.finditer(line):
            value = next(g for g in m.groups() if g)
            if value.strip("'\",;") and value not in ("any", "(null)"):
                out.append(("ssid", value.strip("'\",;")))
        if LOCATION_LINE.search(line):
            for m in COORD.finditer(line):
                out.append(("location", m.group(0)))
        if CALL_LINE.search(line):
            for m in DIGIT_RUN.finditer(line):
                if sum(c.isdigit() for c in m.group(0)) >= 7:
                    out.append(("phone", m.group(0)))
        return out

    def mask(self, line):
        found = self.findings(line)
        # longest first: a value inside another is replaced with it
        for category, value in sorted(found, key=lambda f: len(f[1]), reverse=True):
            if value and value in line:
                token = self.placeholder(category, value)
                line = _bounded(value).sub(lambda _m, t=token: t, line)
        return line

    def mask_text(self, text):
        return "\n".join(self.mask(line) for line in text.split("\n"))
