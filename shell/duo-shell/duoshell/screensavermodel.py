"""org.freedesktop.ScreenSaver's inhibitors (screensaver.py serves the bus),
as duo-wc's idle hold (`set_idle_hold`). Pure: tests/test_screensavermodel.py.

An app asks the bus "keep the screen on" (Inhibit: its name and a reason,
a cookie back) and lets go (UnInhibit with the cookie); one that goes away
lets go of all of its own. Who asks: Chromium (no GNOME session manager
here, so the freedesktop one), Firefox (its first choice), and every GTK4
app (Celluloid, Showtime) through the Inhibit portal, whose GTK backend
uses this interface when there is no GNOME session manager (the caller is
then the portal backend, which speaks for the app).

duo-wc is told the holders (application name, the caller's process id):
a holder whose process has windows keeps the screen on while one of THOSE
is seen, one without (the portal backend) while any app window is; never a
dark, locked or home screen, and never against the power key
(compositor/duo-wc/src/idle.rs). The list goes again every HOLD_RENEW_S
while it is not empty and runs out by itself HOLD_TTL_MS after the last
time (a shell that died leaves nothing behind).
"""

HOLD_TTL_MS = 30000
HOLD_RENEW_S = 10

MAX_INHIBITS = 64  # in all: the rest are refused
MAX_PER_SENDER = 16  # per bus connection
MAX_NAME = 64  # characters of an application name kept


def clean_name(text):
    """An application name fit for the log and duo-wc: printable, one line,
    at most MAX_NAME characters ("" for nothing)."""
    text = "".join(" " if not ch.isprintable() else ch for ch in str(text or ""))
    return " ".join(text.split())[:MAX_NAME]


class Inhibits:
    """The inhibitors by cookie: sender (the unique bus name), app, reason,
    pid (None until the bus says)."""

    def __init__(self, first_cookie=1):
        self.items = {}
        self._next = max(1, int(first_cookie)) & 0xFFFFFFFF or 1

    def _cookie(self):
        while True:
            cookie = self._next
            self._next = (self._next % 0xFFFFFFFF) + 1
            if cookie not in self.items:
                return cookie

    def add(self, sender, app, reason, pid=None):
        """A new inhibitor; its cookie, or None when there are too many."""
        if len(self.items) >= MAX_INHIBITS:
            return None
        if sum(1 for i in self.items.values() if i["sender"] == sender) >= MAX_PER_SENDER:
            return None
        cookie = self._cookie()
        self.items[cookie] = {
            "sender": sender,
            "app": clean_name(app),
            "reason": clean_name(reason),
            "pid": pid,
        }
        return cookie

    def set_pid(self, cookie, pid):
        """The bus said whose it is (an int > 0); whether that changed anything."""
        item = self.items.get(cookie)
        pid = pid if isinstance(pid, int) and pid > 0 else None
        if item is None or item["pid"] == pid:
            return False
        item["pid"] = pid
        return True

    def set_fallback_name(self, cookie, name):
        """A name for an inhibitor whose app gave none (the process's)."""
        item = self.items.get(cookie)
        if item is None or item["app"]:
            return False
        item["app"] = clean_name(name)
        return bool(item["app"])

    def remove(self, cookie, sender):
        """UnInhibit: only the connection that took it lets it go."""
        item = self.items.get(cookie)
        if item is None or item["sender"] != sender:
            return False
        del self.items[cookie]
        return True

    def drop_sender(self, sender):
        """A connection left the bus: its inhibitors go. The cookies."""
        gone = [c for c, i in self.items.items() if i["sender"] == sender]
        for cookie in gone:
            del self.items[cookie]
        return gone

    def senders(self):
        return {i["sender"] for i in self.items.values()}

    def holds(self):
        """What duo-wc is told: one holder per (app, pid), sorted."""
        seen = set()
        out = []
        for item in self.items.values():
            key = (item["app"] or "unnamed", item["pid"])
            if key in seen:
                continue
            seen.add(key)
            hold = {"app": key[0]}
            if key[1] is not None:
                hold["pid"] = key[1]
            out.append(hold)
        out.sort(key=lambda h: (h["app"], h.get("pid") or 0))
        return out


def fallback_name(comm):
    """The label for an inhibitor whose app gave no name, from its process's
    name (/proc/PID/comm, at most 15 characters): the portal backend (which
    passes on a host app's empty app id) is "portal"."""
    comm = clean_name(comm)
    if comm.startswith("xdg-desktop-po"):
        return "portal"
    return comm


def active(state):
    """Whether "the screen saver is active" (GetActive, ActiveChanged) by
    duo-wc's state: the screen blanked or the session locked."""
    if not state:
        return False
    return bool(state.get("blanked") or state.get("locked"))
