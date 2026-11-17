"""Where the lock screen's PIN and its rules live (docs/hardware/security.md): root's, not the user's.

Every app runs as the same user as the shell, so a PIN hash in
~/.config/duo-shell/lock.conf (where it was until 2026-11-07) could be read
by any app (and the few PINs there are tried offline) or deleted (the next
lock opened with a swipe). Now:

    /var/lib/duo2-lock/          root 0755
        pin                      root 0600: pbkdf2-sha256$ROUNDS$SALT$HASH (lockpad.hash_pin),
                                 or only "homekey" while the home is encrypted
                                 (homecrypt/: home.key's PIN protector checks it)
        policy                   root 0644: pin = yes|no, lock_on_blank = on|off,
                                 lock_on_close = on|off (no secret: the shell and
                                 duo-lock read it)
        tries                    root 0600: failures, until (the back-off)

and one door to it, `duo-lock-helper` (root through a sudoers rule for
exactly that file): `verify` (the PIN on stdin: right / wrong / wait), `set`
and `clear` (the CURRENT PIN first, if one is set), `policy` (a rule that
makes the phone less safe needs the current PIN too). The hash never leaves
root, and guessing through the helper is slowed by root's own back-off (five
wrong, then 30 s, doubling to 15 min), which no app can reset. Duo Settings
and `duo-lock --set-pin` go through the helper too.

The pure parts here (tests/test_lockstore.py): the policy text, the back-off,
what a request needs; the helper does the files.
"""

import os

STORE = "/var/lib/duo2-lock"
PIN_FILE = os.path.join(STORE, "pin")
POLICY_FILE = os.path.join(STORE, "policy")
TRIES_FILE = os.path.join(STORE, "tries")
HELPER = "/usr/local/lib/duo-shell/duo-lock-helper"

DEFAULT_POLICY = {"pin": "no", "lock_on_blank": "off", "lock_on_close": "on"}
POLICY_KEYS = ("lock_on_blank", "lock_on_close")
TRIES_BEFORE_WAIT = 5
FIRST_WAIT_S = 30
MAX_WAIT_S = 900

# The helper's exit codes.
OK, WRONG, INVALID, WAIT, NO_PIN = 0, 1, 2, 3, 4


def on(value):
    return str(value).strip().lower() in ("on", "yes", "true", "1")


def parse_policy(text):
    """The policy file's text -> dict (unknown keys and bad values dropped,
    defaults for the rest)."""
    policy = dict(DEFAULT_POLICY)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (p.strip() for p in line.split("=", 1))
        if key == "pin" and value in ("yes", "no"):
            policy[key] = value
        elif key in POLICY_KEYS and value in ("on", "off"):
            policy[key] = value
    return policy


def format_policy(policy):
    lines = ["# duo2-lock (duo-lock-helper writes this; root's): the lock screen's rules"]
    for key in ("pin",) + POLICY_KEYS:
        lines.append("%s = %s" % (key, policy.get(key, DEFAULT_POLICY[key])))
    return "\n".join(lines) + "\n"


def read_policy(path=POLICY_FILE):
    """The rules as the shell and duo-lock see them (no file: the defaults,
    no PIN: a swipe unlocks)."""
    try:
        with open(path) as f:
            return parse_policy(f.read())
    except OSError:
        return dict(DEFAULT_POLICY)


def has_pin(policy):
    return policy.get("pin") == "yes"


def lock_on_blank(policy):
    """Lock when the screen goes off: only with a PIN (a swipe lock there
    would only be in the way)."""
    return has_pin(policy) and on(policy.get("lock_on_blank"))


def lock_on_close(policy):
    return on(policy.get("lock_on_close", "on"))


def wait_after(failures):
    """Seconds to wait after this many wrong PINs in a row: none for the
    first four, then 30 s, doubling with every further one, at most 15 min."""
    if failures < TRIES_BEFORE_WAIT:
        return 0
    return min(MAX_WAIT_S, FIRST_WAIT_S * 2 ** (failures - TRIES_BEFORE_WAIT))


def parse_tries(text):
    """The tries file -> (failures, until epoch seconds)."""
    failures, until = 0, 0.0
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                if parts[0] == "failures":
                    failures = max(0, int(parts[1]))
                elif parts[0] == "until":
                    until = max(0.0, float(parts[1]))
            except ValueError:
                pass
    return failures, until


def format_tries(failures, until):
    return "failures %d\nuntil %.3f\n" % (failures, until)


def weakens(key, value, policy):
    """Whether setting `key` to `value` makes the phone less safe (then the
    current PIN is asked for, when one is set)."""
    return key in POLICY_KEYS and on(policy.get(key)) and not on(value)
