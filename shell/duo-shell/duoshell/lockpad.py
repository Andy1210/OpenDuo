"""The lock screen's rules, pure (tests/test_lockpad.py): the PIN and how it
is kept, the keypad's layout, what a press does, and the back-off after
wrong tries. duo-lock (lockclient.py) draws it and talks Wayland.

The PIN is kept as PBKDF2-SHA256 (salted, 200 000 rounds) by root, in
/var/lib/duo2-lock (lockstore.py: duo-lock-helper is the one door to it;
until 2026-11-07 it was ~/.config/duo-shell/lock.conf, where any app could
read or delete it):

    pbkdf2-sha256$200000$<salt hex>$<hash hex>

`duo-lock --set-pin` sets it through the helper (asks twice). Without a PIN the lock screen
is a **swipe** lock (2026-11-07, the owner: a PIN maybe later): a swipe up
anywhere on it unlocks, as does Enter on a keyboard. Nothing can lock the
owner out: no PIN is set by default, and `duoctl unlock` ends any lock.

    lock_on_close = on     # the shell locks when the phone is folded closed
                           # (duo-posed's posture, through duo-wc; default on)
"""

import hashlib
import hmac
import os

ROUNDS = 200_000
MIN_LEN = 4
MAX_LEN = 12
TRIES_BEFORE_WAIT = 5
WAIT_S = 30
# With the home encrypted (homecrypt/) the recovery key is typed on the same
# keypad: 36 digits (duohomecrypt/keys.py RECOVERY_DIGITS).
RECOVERY_LEN = 36
HOME_ENCRYPTED = "/run/duo2-home/encrypted"
HOME_LOCKED = "/run/duo2-home/locked"


def home_encrypted(path=HOME_ENCRYPTED):
    return os.path.exists(path)


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "lock.conf")


def hash_pin(pin, salt=None, rounds=ROUNDS):
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, rounds)
    return "pbkdf2-sha256$%d$%s$%s" % (rounds, salt.hex(), digest.hex())


def check_pin(pin, stored):
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2-sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


def valid_pin(pin):
    return pin.isascii() and pin.isdigit() and MIN_LEN <= len(pin) <= MAX_LEN


def parse_conf(text):
    conf = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


def read_conf(path=None):
    try:
        with open(path or config_path()) as f:
            return parse_conf(f.read())
    except OSError:
        return {}


def lock_on_blank(conf):
    """Whether the shell locks when the screen goes off: only with a PIN set
    and lock_on_blank on (both are needed; neither is there by default)."""
    return bool(conf.get("pin")) and conf.get("lock_on_blank", "off").lower() in (
        "on",
        "yes",
        "true",
        "1",
    )


def lock_on_close(conf):
    """Whether the shell locks a phone that is folded closed (default on; a
    swipe unlocks it without a PIN). It needs duo-posed acting and duo-wc's
    session_lock on (duo2-sensors-mode on sets both)."""
    return conf.get("lock_on_close", "on").lower() in ("on", "yes", "true", "1")


# -- the swipe ----------------------------------------------------------------------

SWIPE_MIN_PX = 90  # logical px up
SWIPE_MIN_SHARE = 0.12  # ... or this share of the panel's height, the larger


def swipe_unlocks(dx, dy, height):
    """A finger moved by (dx, dy) logical px (y down) from where it went down
    to where it lifted, on a panel `height` high: is that the unlocking
    swipe? Up far enough, and more up than sideways."""
    need = max(SWIPE_MIN_PX, SWIPE_MIN_SHARE * height)
    return -dy >= need and abs(dx) < -dy


# -- the keypad -------------------------------------------------------------------

KEYS = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["back", "0", "ok"]]


def keypad(width, height, top):
    """The keys' circles in a `width` x `height` panel (logical px), the pad
    starting at `top`: [(key, cx, cy, r)]."""
    cols, rows = 3, 4
    size = min(width / 3.6, (height - top) / 4.6, 110.0)
    r = size * 0.42
    gap_x = size * 1.12
    gap_y = size * 1.06
    x0 = width / 2 - gap_x
    y0 = top + size * 0.55
    out = []
    for row in range(rows):
        for col in range(cols):
            out.append((KEYS[row][col], x0 + col * gap_x, y0 + row * gap_y, r))
    return out


def key_at(keys, x, y):
    for key, cx, cy, r in keys:
        if (x - cx) ** 2 + (y - cy) ** 2 <= (r * 1.15) ** 2:
            return key
    return None


class Entry:
    """What has been typed, and the back-off."""

    def __init__(self, stored, verify=None):
        """`stored`: a hash to check against here (tests); `verify`: a
        callable that checks a PIN elsewhere (duo-lock-helper, as root,
        with root's back-off) and returns ("ok" | "wrong" | "wait",
        seconds to wait)."""
        self.stored = stored
        self.verify = verify
        self.max_len = MAX_LEN  # RECOVERY_LEN with the home encrypted
        self.digits = ""
        self.failures = 0
        self.wait_until = 0.0
        self.message = ""

    def waiting(self, now):
        return now < self.wait_until

    def press(self, key, now):
        """A key; returns 'unlock' when the PIN is right, else None."""
        if self.waiting(now):
            return None
        if key == "back":
            self.digits = self.digits[:-1]
            self.message = ""
        elif key == "ok":
            return self.submit(now)
        elif key is not None and key.isdigit() and len(key) == 1:
            if len(self.digits) < self.max_len:
                self.digits += key
                self.message = ""
        return None

    def submit(self, now):
        typed, self.digits = self.digits, ""
        if not typed:
            return None
        if self.verify is not None:
            result, wait = self.verify(typed)
            if result == "ok":
                self.failures = 0
                self.message = ""
                return "unlock"
            if result == "wait":
                self.wait_until = now + max(1, wait)
                self.message = "wait"
            else:
                self.message = "wrong"
            return None
        if self.stored and check_pin(typed, self.stored):
            self.failures = 0
            self.message = ""
            return "unlock"
        self.failures += 1
        if self.failures >= TRIES_BEFORE_WAIT:
            self.failures = 0
            self.wait_until = now + WAIT_S
            self.message = "wait"
        else:
            self.message = "wrong"
        return None
