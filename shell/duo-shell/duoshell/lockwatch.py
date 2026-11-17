"""When to start duo-lock again: duo-wc says "locked" and no lock client runs.

A lock client that dies leaves the session locked (the protocol requires it),
black, with nothing to type a PIN into; duo-wc lets the next lock client take
over (lock.rs `lock_may_take`). This decides when the shell starts one. It
can only SHOW the lock screen again: it never unlocks, and it is silent while
duo-wc says the session is not locked.

Pure (no GLib, no process handling): `tick` gets the time and the two facts.
"""

# duo-lock exits right after a good unlock, and duo-wc's "unlocked" state
# event can trail the exit by a moment: wait before calling it a crash.
GRACE_S = 3.0
# After a restart: the next one waits 5 s, 10 s, 20 s ... up to a minute, so a
# lock screen that dies at once does not become a restart storm.
BACKOFF_FIRST_S = 5.0
BACKOFF_MAX_S = 60.0
# A lock client that stayed up this long was a working one: the backoff resets.
STABLE_S = 30.0


class RelockWatch:
    def __init__(self):
        self.gone_since = None  # locked and no client, since when
        self.alive_since = None
        self.next_ok = 0.0
        self.fails = 0

    def tick(self, now, locked, alive):
        """True when duo-lock should be started now."""
        if not locked:
            self.gone_since = None
            self.alive_since = None
            self.fails = 0
            return False
        if alive:
            self.gone_since = None
            if self.alive_since is None:
                self.alive_since = now
            elif now - self.alive_since >= STABLE_S:
                self.fails = 0
            return False
        self.alive_since = None
        if self.gone_since is None:
            self.gone_since = now
        if now - self.gone_since < GRACE_S or now < self.next_ok:
            return False
        self.next_ok = now + min(BACKOFF_FIRST_S * (2**self.fails), BACKOFF_MAX_S)
        self.fails += 1
        self.gone_since = None
        return True
