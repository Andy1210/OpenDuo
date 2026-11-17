"""Emergency calls from the lock screen and the SIM PIN dialog (pure,
tests/test_emergencymodel.py): which numbers may be dialled there, what
the keypad does, where the parts go. lockclient.py draws the lock's
emergency keypad, simpin.py the PIN dialog's, mobile.py places the call
(telephony/README.md "Emergency calls").

From a locked phone (or one whose SIM waits for its PIN) only emergency
numbers are dialled, as on every phone: 112 and 911 always (3GPP TS
22.101), the numbers ModemManager lets through without a SIM (000, 08,
110, 999, 118, 119) when none is in, the SIM's own list (EF_ECC, as
ModemManager reads it; empty on this modem: ModemManager reads it with an
AT command and the Duo 2's modem has no AT port) and the national numbers
of the network's or the SIM's country (by MCC, below). ModemManager is
the last word: unregistered (no SIM, a locked SIM, airplane mode, no
coverage of the SIM's network) it refuses everything but 112, 911, the
SIM's list and, without a SIM, its no-SIM list; 112 works in every EU
country and in most others.

The lock sends {"do": "emergency", "number": "112"} on its call card's
socketpair (callsmodel.py); the shell checks the number again here
(dialable) and dials it (mobile.Mobile.dial).
"""

import json

ALWAYS = ("112", "911")
NO_SIM = ("000", "08", "110", "999", "118", "119")  # ModemManager's, mm-iface-modem-voice.c

EMERGENCY, POLICE, FIRE, AMBULANCE = "Emergency", "Police", "Fire", "Ambulance"

# Mobile country code -> the national emergency numbers beside 112 (and
# what each is for). Only well-known short numbers: a number missing here
# is still reached through 112.
NATIONAL = {
    "262": (("110", POLICE),),  # Germany
    "216": (("104", AMBULANCE), ("105", FIRE), ("107", POLICE)),  # Hungary
    "232": (("122", FIRE), ("133", POLICE), ("144", AMBULANCE)),  # Austria
    "228": (("117", POLICE), ("118", FIRE), ("144", AMBULANCE)),  # Switzerland
    "230": (("150", FIRE), ("155", AMBULANCE), ("158", POLICE)),  # Czechia
    "231": (("150", FIRE), ("155", AMBULANCE), ("158", POLICE)),  # Slovakia
    "260": (("997", POLICE), ("998", FIRE), ("999", AMBULANCE)),  # Poland
    "208": (("15", AMBULANCE), ("17", POLICE), ("18", FIRE)),  # France
    "222": (("113", POLICE), ("115", FIRE), ("118", AMBULANCE)),  # Italy
    "234": (("999", EMERGENCY),),  # United Kingdom
    "235": (("999", EMERGENCY),),
}
MAX_DIGITS = 6  # the longest emergency number anywhere is shorter
DIGITS = "0123456789"


def normalize(text):
    """The digits of what was typed or dialled ('1 1 2', '112-' -> '112');
    a '+' or anything after a pause ends an emergency number."""
    out = []
    for ch in (text or "").strip():
        if ch in DIGITS:
            out.append(ch)
        elif ch in " -().":
            continue
        else:
            return ""
    return "".join(out)


def mccs(context):
    """The country codes that count: the network's, then the SIM's."""
    out = []
    for key in ("network_mcc", "sim_mcc"):
        code = str(context.get(key) or "")[:3]
        if len(code) == 3 and code.isdigit() and code not in out:
            out.append(code)
    return out


def numbers(context):
    """[(number, what)] that may be dialled from a locked phone, 112 first.
    `context`: sim_present, sim_numbers, network_mcc, sim_mcc and
    emergency_only (ModemManager is not registered and not in airplane
    mode: it lets only its own list through, so the national numbers are
    not offered then; in airplane mode duo2-wwan registers first and dials
    again)."""
    out = [("112", EMERGENCY)]
    for mcc in [] if context.get("emergency_only") else mccs(context):
        for number, what in NATIONAL.get(mcc, ()):
            if number not in [n for n, _w in out]:
                out.append((number, what))
    extra = [("911", EMERGENCY)]
    if not context.get("sim_present", True):
        extra += [(n, EMERGENCY) for n in NO_SIM]
    for n in context.get("sim_numbers") or ():
        n = normalize(n)
        if n:
            extra.append((n, EMERGENCY))
    for number, what in extra:
        if number not in [n for n, _w in out]:
            out.append((number, what))
    return out


def dialable(number, context):
    """Whether `number` may be dialled from a locked phone."""
    n = normalize(number)
    return bool(n) and n in [m for m, _w in numbers(context)]


def suggestions(context, limit=4):
    """The one-tap numbers above the keypad: 112 and the country's own
    (not 911 or the no-SIM list: those are for whoever knows them)."""
    out = [("112", EMERGENCY)]
    for mcc in [] if context.get("emergency_only") else mccs(context):
        for number, what in NATIONAL.get(mcc, ()):
            if number not in [n for n, _w in out]:
                out.append((number, what))
    return out[:limit]


class Entry:
    """The emergency keypad: what has been typed. press() returns
    ("call", number) for a call that may go out, else None; `message` says
    why not ("not-emergency"). `allowed`: the numbers that may be dialled
    (numbers(context), or the list the shell sent the lock)."""

    def __init__(self, allowed=("112", "911")):
        self.allowed = set(allowed)
        self.digits = ""
        self.message = ""

    def press(self, key):
        self.message = ""
        if key == "back":
            self.digits = self.digits[:-1]
        elif key == "call":
            if not self.digits:
                return None
            if self.digits in self.allowed:
                return ("call", self.digits)
            self.message = "not-emergency"
        elif isinstance(key, str) and len(key) == 1 and key in DIGITS:
            if len(self.digits) < MAX_DIGITS:
                self.digits += key
        return None

    def choose(self, number):
        """A suggestion tapped: the number is typed in (one more tap on the
        call key dials it: a pocket cannot call 112 by brushing one key)."""
        n = normalize(number)
        if n:
            self.digits = n[:MAX_DIGITS]
            self.message = ""

    def reset(self):
        self.digits = ""
        self.message = ""


# -- the wire between the lock and the shell -----------------------------------------


def encode_request(number):
    return (
        json.dumps({"do": "emergency", "number": normalize(number)}, separators=(",", ":")) + "\n"
    ).encode()


def parse_request(line, context):
    """The shell's side: a lock line asking for an emergency call, as the
    number, or None (not that request, or not an emergency number)."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or msg.get("do") != "emergency":
        return None
    number = msg.get("number")
    if not isinstance(number, str) or not dialable(number, context):
        return None
    return normalize(number)


def offer(context):
    """What the shell tells the lock (beside the call card): the numbers it
    may dial and the one-tap suggestions."""
    return {
        "numbers": [n for n, _w in numbers(context)],
        "suggestions": [[n, w] for n, w in suggestions(context)],
    }


def parse_offer(line):
    """The lock's side: the shell's offer from a line, or None (none in it,
    or not valid: then the lock keeps what it had)."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("emergency"), dict):
        return None
    o = msg["emergency"]
    nums = [normalize(n) for n in o.get("numbers") or [] if isinstance(n, str)]
    nums = [n for n in nums if n]
    sugg = []
    for item in o.get("suggestions") or []:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
            and normalize(item[0]) in nums
        ):
            sugg.append((normalize(item[0]), item[1][:24]))
    if not nums:
        return None
    return {"numbers": nums, "suggestions": sugg[:4]}


DEFAULT_OFFER = {"numbers": ["112", "911"], "suggestions": [("112", EMERGENCY)]}


# -- the lock's layout (logical px) ---------------------------------------------------

KEYS = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["back", "0", "call"]]


def button(width, height, pin_lock):
    """The lock screen's "Emergency call" pill: (cx, cy, w, h). Below the
    PIN keypad (on its side: at the left, beside it), above the swipe
    lock's hint."""
    pw, ph = 168.0, 36.0
    if pin_lock:
        if width > height:
            return (width * 0.13 + 10, height * 0.62, pw, ph)
        return (width / 2, height - ph / 2 - 8, pw, ph)
    return (width / 2, height * (0.78 if width <= height else 0.74), pw, ph)


def in_box(box, x, y, pad=6):
    cx, cy, w, h = box
    return abs(x - cx) <= w / 2 + pad and abs(y - cy) <= h / 2 + pad


def keypad(width, height):
    """The emergency keypad: [(key, cx, cy, r)], below the typed number and
    the suggestions."""
    top = height * 0.36
    size = min(width / 3.6, (height - top) / 4.7, 100.0)
    r = size * 0.42
    gap_x, gap_y = size * 1.12, size * 1.04
    x0 = width / 2 - gap_x
    y0 = top + size * 0.5
    return [
        (KEYS[row][col], x0 + col * gap_x, y0 + row * gap_y, r)
        for row in range(4)
        for col in range(3)
    ]


def layout(width, height, n_suggestions):
    """Where the emergency keypad's parts go (the lock's clock steps aside):
    the title, the typed number, the suggestion pills [(index, cx, cy, w,
    h)] (`labelled`: wide enough for "112 Emergency", else the number
    alone), Cancel, the keys."""
    keys = keypad(width, height)
    n = max(1, n_suggestions)
    pw = min(140.0, (width - 32) / n - 8)
    y = height * 0.30
    x0 = width / 2 - (n - 1) * (pw + 8) / 2
    pills = [(i, x0 + i * (pw + 8), y, pw, 34.0) for i in range(n_suggestions)]
    return {
        "title_y": height * 0.12,
        "number_y": height * 0.22,
        "pills": pills,
        "labelled": pw >= 120,
        "cancel": (width - 52.0, 28.0, 84.0, 32.0),
        "keys": keys,
    }


def key_at(keys, x, y):
    for key, cx, cy, r in keys:
        if (x - cx) ** 2 + (y - cy) ** 2 <= (r * 1.15) ** 2:
            return key
    return None
