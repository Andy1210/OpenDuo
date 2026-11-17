"""The mobile network as the shell shows it, pure (tests/test_mobilemodel.py).

mobile.py reads ModemManager (system bus, the ModemManager GI) into a plain
dict, the "modem":

    present      a modem with a SIM (not "failed: sim-missing", not gone)
    state        ModemManager's state name: locked, disabled, enabled,
                 searching, registered, connected, failed, ...
    lock         what it waits for: sim-pin, sim-puk, sim-pin2, none ...
    retries      {lock name: attempts left} (UnlockRetries)
    quality      signal quality 0-100 (None: unknown)
    operator     the network's name; sim_operator: the SIM's (its SPN)
    code         the network's MCC+MNC ("26201")
    registration home | roaming | searching | idle | denied | unknown
    tech         the access technology ("lte", "umts", "gsm", "5gnr")
    data         the mobile data switch (on / off) and connected (bool)

and the airplane mode switch apart from it (/var/lib/duo2/airplane, True /
False), and this module decides what the status bar, the quick menu and the
SIM PIN dialog show. The PIN dialog's entry (PinEntry) checks the digits and says
what to send; it never shows, logs or keeps a PIN beyond the request.
"""

PIN_MIN, PIN_MAX = 4, 8
PUK_LEN = 8
# A PIN or PUK the dialog asks for: (kind it shows, the lock).
ASKS = {"sim-pin": "pin", "sim-puk": "puk"}
SIGNAL_ICONS = [
    "network-cellular-signal-none-symbolic",
    "network-cellular-signal-weak-symbolic",
    "network-cellular-signal-ok-symbolic",
    "network-cellular-signal-good-symbolic",
    "network-cellular-signal-excellent-symbolic",
]
TECH_LABELS = {
    "5gnr": "5G",
    "lte": "4G",
    "umts": "3G",
    "hsdpa": "3G",
    "hsupa": "3G",
    "hspa": "3G",
    "hspa-plus": "3G",
    "gprs": "2G",
    "edge": "2G",
    "gsm": "2G",
    "gsm-compact": "2G",
}
REGISTERED = ("registered", "connected", "connecting", "disconnecting")


def pin_request(modem):
    """What the SIM waits for, if the dialog must ask: None, or {"kind":
    "pin" | "puk", "left": attempts left or None}."""
    modem = modem or {}
    if not modem.get("present") or modem.get("state") != "locked":
        return None
    lock = modem.get("lock")
    kind = ASKS.get(lock)
    if kind is None:
        return None
    left = (modem.get("retries") or {}).get(lock)
    return {"kind": kind, "left": left if isinstance(left, int) and left >= 0 else None}


def signal_icon(quality):
    """The bars for a signal quality (per cent)."""
    if quality is None:
        return SIGNAL_ICONS[0]
    q = max(0, min(100, int(quality)))
    if q == 0:
        return SIGNAL_ICONS[0]
    return SIGNAL_ICONS[min(4, 1 + q * 4 // 101)]


def operator_label(modem, limit=12):
    """The operator for the bar: the SIM's name (Example SIM) before the
    network's (Telekom.de), else the network code; '' when not registered."""
    modem = modem or {}
    if modem.get("state") not in REGISTERED:
        return ""
    name = (modem.get("sim_operator") or modem.get("operator") or modem.get("code") or "").strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


AIRPLANE_ICON = "airplane-mode-symbolic"


def bar(modem, airplane=False):
    """What the status bar shows: None (no SIM: nothing), or {"icon",
    "text" (the operator), "tip", "locked"}. In airplane mode the airplane,
    with or without a SIM (a locked SIM keeps its tap: the PIN dialog)."""
    modem = modem or {}
    locked = bool(modem.get("present")) and (
        pin_request(modem) is not None or modem.get("state") == "locked"
    )
    if airplane:
        return {"icon": AIRPLANE_ICON, "text": "", "tip": "Airplane mode", "locked": locked}
    if not modem.get("present"):
        return None
    if locked:
        return {"icon": "auth-sim-locked-symbolic", "text": "", "tip": "SIM locked", "locked": True}
    state = modem.get("state")
    if state in REGISTERED:
        tech = TECH_LABELS.get(modem.get("tech") or "", "")
        roaming = modem.get("registration") == "roaming"
        tip = "%s%s%s" % (
            operator_label(modem, 40),
            (", " + tech) if tech else "",
            " (roaming)" if roaming else "",
        )
        return {
            "icon": signal_icon(modem.get("quality")),
            "text": operator_label(modem),
            "tip": tip,
            "locked": False,
            "tech": tech,
        }
    if state in ("searching", "enabled", "enabling"):
        return {
            "icon": "network-cellular-acquiring-symbolic",
            "text": "",
            "tip": "Searching",
            "locked": False,
        }
    return {
        "icon": "network-cellular-offline-symbolic",
        "text": "",
        "tip": "Mobile network off",
        "locked": False,
    }


def data_tile(modem, airplane=False):
    """The quick menu's Mobile data tile: (sensitive, on, status)."""
    modem = modem or {}
    if airplane:
        return False, False, "Airplane mode"
    if not modem.get("present"):
        return False, False, "No SIM"
    if modem.get("state") == "locked":
        return True, False, "SIM locked"
    on = modem.get("data") == "on"
    if not on:
        return True, False, "Off"
    if modem.get("connected"):
        return True, True, TECH_LABELS.get(modem.get("tech") or "", "") or "Connected"
    if modem.get("state") in REGISTERED:
        return True, True, "On"
    return True, True, "Waiting for the network"


def airplane_tile(airplane):
    """The quick menu's Airplane mode tile: (on, status)."""
    return (True, "On") if airplane else (False, "Off")


def parse_switch(text):
    """The mobile data switch file's text: 'on', 'off' or None (no choice:
    duo2-wwan's DATA)."""
    value = (text or "").strip().lower()
    return value if value in ("on", "off") else None


class PinEntry:
    """The PIN dialog's typing: the PIN, or the PUK and a new PIN (twice).

    press(key) with "0".."9", "back" or "ok"; returns None while typing, or
    the request to send: ("pin", PIN) or ("puk", PUK, NEW_PIN). `stage`
    says what is being typed (pin, puk, new, again), `count` how many digits
    (the dots), `problem` the last refusal (short, too long, differ)."""

    def __init__(self, kind):
        self.kind = kind
        self.stage = "pin" if kind == "pin" else "puk"
        self.digits = ""
        self.puk = ""
        self.new = ""
        self.problem = None

    @property
    def count(self):
        return len(self.digits)

    def limit(self):
        return PUK_LEN if self.stage == "puk" else PIN_MAX

    def press(self, key):
        if key == "back":
            self.digits = self.digits[:-1]
            self.problem = None
            return None
        if key == "ok":
            return self._submit()
        if isinstance(key, str) and len(key) == 1 and key.isdigit():
            if len(self.digits) < self.limit():
                self.digits += key
                self.problem = None
        return None

    def _submit(self):
        typed, self.digits = self.digits, ""
        if self.stage == "puk":
            if len(typed) != PUK_LEN:
                self.problem = "puk-length"
                return None
            self.puk, self.stage = typed, "new"
            return None
        if not PIN_MIN <= len(typed) <= PIN_MAX:
            self.problem = "pin-length"
            return None
        if self.stage == "pin":
            return ("pin", typed)
        if self.stage == "new":
            self.new, self.stage = typed, "again"
            return None
        # again
        if typed != self.new:
            self.new, self.stage, self.problem = "", "new", "differ"
            return None
        puk, new = self.puk, self.new
        self.puk = self.new = ""
        self.stage = "puk"
        return ("puk", puk, new)

    def reset(self, kind=None):
        """Start over (after a wrong PIN, or the SIM now wants its PUK)."""
        self.__init__(kind or self.kind)
