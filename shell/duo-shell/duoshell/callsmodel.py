"""GNOME Calls' calls as data (pure, tests/test_callsmodel.py): which call
rings, which one is up, and the lock screen's call card; calls.py watches
Calls on the session bus, lockclient.py draws the card.

Calls exports every call it knows on the session bus, the way Phosh's lock
screen reads them: the name org.gnome.Calls, an object manager at
/org/gnome/Calls, one object per call (/org/gnome/Calls/Call/N) with
org.gnome.Calls.Call: State (below), Inbound, Id (the number), DisplayName
(the contact's name, from the address book), Hints ("ui-active": Calls
shows a call 100 ms after it comes, the time its blocking rules take), and
Accept / Hangup / Silence.

An incoming call lights the screen (calls.py); Calls itself shows its call
window and rings. While the session is locked duo-wc shows nothing but the
lock screen, so the lock draws the call: a card with the caller and Decline /
Answer, and once the call is up (a PIN lock stays locked) Speaker / Hang up.
The closed phone's glance strip and the peek strip show who calls too
(strip_caller). The caller's name and number go to the lock and the strips
only, never into a log.

The power key or a volume key while a call rings aloud silences it, as on
any phone (ring_state, RingKeys): Calls' Silence ends its ringer's
feedbackd event (the ringtone and the vibration); the call goes on ringing
silently and can still be answered. Calls does not say on the bus which
calls are silenced, so the shell remembers the ones it silenced.

The shell and the lock talk over a socketpair the shell makes when it starts
duo-lock (its end's number in DUO_LOCK_CALL_FD), one JSON object per line:
the shell sends {"card": <card or null>, "speaker": <bool>}, the lock sends
{"do": "accept" | "hangup" | "speaker", "path": <call>, "on": <bool>}.
"""

import json

BUS = "org.gnome.Calls"
PATH = "/org/gnome/Calls"
IFACE = "org.gnome.Calls.Call"
CALL_PREFIX = PATH + "/"

# org.gnome.Calls.Call's State (libcall-ui's CuiCallState).
UNKNOWN, ACTIVE, HELD, DIALING, ALERTING, INCOMING, WAITING, DISCONNECTED = range(8)
ONGOING = frozenset({ACTIVE, HELD, DIALING, ALERTING})

MAX_CALLER = 64
ACTIONS = ("accept", "hangup", "speaker")
UNKNOWN_CALLER = "Unknown caller"


def _str(value):
    return value if isinstance(value, str) else ""


def call_from_props(props, old=None):
    """A call from its D-Bus properties (a dict of plain values), on top of
    what was known (`old`), for a PropertiesChanged."""
    call = dict(
        old or {"state": UNKNOWN, "inbound": False, "id": "", "name": "", "ui_active": True}
    )
    if "State" in props:
        try:
            call["state"] = int(props["State"])
        except (TypeError, ValueError):
            call["state"] = UNKNOWN
    if "Inbound" in props:
        call["inbound"] = bool(props["Inbound"])
    if "Id" in props:
        call["id"] = _str(props["Id"])
    if "DisplayName" in props:
        call["name"] = _str(props["DisplayName"])
    if "Hints" in props and isinstance(props["Hints"], dict):
        # Calls 48 always sends it; a Calls without it shows every call.
        call["ui_active"] = bool(props["Hints"].get("ui-active", True))
    return call


def is_ringing(call):
    return (
        bool(call.get("inbound")) and call.get("state") == INCOMING and call.get("ui_active", True)
    )


def ringing(calls):
    """The paths of the calls that ring in now (Calls shows them), sorted."""
    return sorted(p for p, c in calls.items() if is_ringing(c))


def ongoing(calls):
    return sorted(p for p, c in calls.items() if c.get("state") in ONGOING)


def caller(call):
    """What the card says about who calls: the contact, else the number."""
    for text in (call.get("name"), call.get("id")):
        text = " ".join(_str(text).split())
        if text:
            return text[:MAX_CALLER]
    return UNKNOWN_CALLER


def strip_caller(calls):
    """Who calls, for the glance and peek strips (the same words as the
    lock screen's card), while a call rings in (aloud or silenced); None
    once none does (answered, declined, missed): the strips show the
    clock again."""
    ring = ringing(calls)
    return caller(calls[ring[0]]) if ring else None


# -- silencing the ringing (the power key, the volume keys) ---------------------

# duo-wc's ring hold (set_ring) is renewed every RING_RENEW_S while a call
# rings and runs out by itself RING_TTL_MS after the last renewal (a shell
# that died during a ring leaves nothing behind).
RING_RENEW_S = 10
RING_TTL_MS = 30000


def forget_silenced(calls, silenced):
    """Forget the silenced calls that no longer ring (they cannot ring
    again). Changes `silenced` in place."""
    silenced.intersection_update(ringing(calls))
    return silenced


def loud(calls, silenced):
    """The calls whose ringing a key silences now: ringing in, not silenced
    yet, and no call up. Beside a call that is up Calls only buzzes for a
    waiting call (and stops by itself); the keys keep their call roles
    then (the volume, the screen)."""
    if ongoing(calls):
        return []
    return [p for p in ringing(calls) if p not in silenced]


def ring_state(calls, silenced):
    """What duo-wc is told (set_ring): "loud" while a key silences the
    ringing (the power key's first press does that instead of switching the
    screen), "silent" while a call rings in otherwise (a glance stays lit
    with who calls, the screen does not blank by itself), None while no call
    rings in."""
    if not ringing(calls):
        return None
    return "loud" if loud(calls, silenced) else "silent"


class RingKeys:
    """The volume keys while a call rings aloud: a press silences it (the
    volume stays as it was) and its release belongs to the ring too, held
    or not; any other key event is the volume's."""

    def __init__(self):
        self.held = set()

    def takes(self, key, pressed, ringing_loud):
        """Whether this key event is the ring's (not the volume's)."""
        if pressed:
            if ringing_loud:
                self.held.add(key)
                return True
            return False
        if key in self.held:
            self.held.discard(key)
            return True
        return False


def note_since(calls, since, now):
    """Remember when each call came up (the card's timer counts from it);
    forget the calls that are gone. Changes `since` in place."""
    for path in list(since):
        if path not in calls:
            del since[path]
    for path, call in calls.items():
        if call.get("state") == ACTIVE and path not in since:
            since[path] = now
    return since


def card(calls, since):
    """What the lock screen shows: an incoming call first (Decline / Answer),
    else a call that is up or being dialled (Speaker / Hang up), else None."""
    ring = ringing(calls)
    if ring:
        path = ring[0]
        return {"kind": "incoming", "path": path, "caller": caller(calls[path])}
    up = ongoing(calls)
    if up:
        # The one that is up before a held one, before one being dialled.
        order = {ACTIVE: 0, HELD: 1, DIALING: 2, ALERTING: 2}
        path = sorted(up, key=lambda p: (order.get(calls[p].get("state"), 3), p))[0]
        state = calls[path].get("state")
        return {
            "kind": "active",
            "path": path,
            "caller": caller(calls[path]),
            "state": {ACTIVE: "active", HELD: "held"}.get(state, "dialing"),
            "since": since.get(path),
        }
    return None


# -- the wire between the shell and the lock ------------------------------------


def encode(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


def parse_card(line):
    """The lock's side: a line from the shell as (card or None, speaker), or
    None when it is not a valid message (then nothing changes)."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or "card" not in msg:
        return None
    speaker = msg.get("speaker") is True
    c = msg["card"]
    if c is None:
        return None, speaker
    if not isinstance(c, dict) or c.get("kind") not in ("incoming", "active"):
        return None
    path = c.get("path")
    if not isinstance(path, str) or not path.startswith(CALL_PREFIX):
        return None
    out = {"kind": c["kind"], "path": path, "caller": caller({"name": c.get("caller")})}
    if c["kind"] == "active":
        out["state"] = (
            c.get("state") if c.get("state") in ("active", "held", "dialing") else "active"
        )
        since = c.get("since")
        out["since"] = (
            float(since)
            if isinstance(since, (int, float)) and not isinstance(since, bool)
            else None
        )
    return out, speaker


def parse_action(line, known_paths):
    """The shell's side: a line from the lock as (action, path, on), or None
    when it is not valid or names a call Calls does not have."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or msg.get("do") not in ACTIONS:
        return None
    path = msg.get("path")
    if path not in known_paths:
        return None
    return msg["do"], path, msg.get("on") is True


def duration(seconds):
    """A call's running time: 0:07, 12:34, 1:02:03."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


# -- the card's layout (the lock draws it, logical px) ----------------------------


def card_layout(kind, width, height, pin_lock):
    """Where the card's parts go on a `width` x `height` panel: the caller's
    line, the status line and the buttons [(name, cx, cy, r)]. The card takes
    the lower part of the lock screen (the clock and the date stay above);
    a PIN lock's call that is up also gets "Enter PIN", which shows the
    keypad (the call stays up, its line above the keypad brings it back)."""
    short = min(width, height)
    r = max(28.0, min(42.0, short * 0.075))
    landscape = width > height
    y_buttons = height * (0.72 if not landscape else 0.70)
    left, right = width / 2 - max(r * 2.2, width * 0.2), width / 2 + max(r * 2.2, width * 0.2)
    if kind == "incoming":
        buttons = [("decline", left, y_buttons, r), ("accept", right, y_buttons, r)]
    else:
        buttons = [("speaker", left, y_buttons, r), ("hangup", right, y_buttons, r)]
        if pin_lock:
            buttons.append(("pin", width / 2, height * (0.88 if not landscape else 0.89), r * 0.8))
    return {
        "caller_y": height * (0.42 if not landscape else 0.43),
        "status_y": height * (0.42 if not landscape else 0.43) + 30,
        "label_dy": r + 22,
        "buttons": buttons,
    }


def call_line_box(width, height):
    """The band above a PIN lock's keypad that shows the call that is up
    while the keypad is out (a tap brings the card back): (x, y, w, h)."""
    bw = min(width * 0.8, 320.0)
    return (width / 2 - bw / 2, height * 0.30, bw, height * 0.05)


def button_at(buttons, x, y):
    for name, cx, cy, r in buttons:
        if (x - cx) ** 2 + (y - cy) ** 2 <= (r * 1.25) ** 2:
            return name
    return None
