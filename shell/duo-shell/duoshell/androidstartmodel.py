"""Starting an Android app while Android is not running, pure
(tests/test_androidstartmodel.py; the notice itself is androidstart.py).

An Android icon runs `waydroid app launch PKG`, which starts the Waydroid
session through D-Bus (id.waydro.Session, android/ in duo2-os) and waits
for Android to boot: the app's window came ~19 s after the tap on
2026-11-17, with nothing on the screen meanwhile, so the owner tapped
again. Now:

- **A notice** ("Starting Android apps...") shows on the panel of the
  launch while Android starts: from a tap on an Android app while the
  session is not on the bus (or while another notice of the same start
  shows), until that app's window is there or `NOTICE_S` have passed.
  The window counts once it has been in duo-wc's window list for
  `SETTLE_S`: Waydroid's first toplevel of a start is gone again 5 ms
  after it came.
- **A longer hint**: duo-wc's `expect_window` waits `COLD_HINT_MS` for the
  window then, not its default 10 s, or the window opened by the open rule
  (possibly on the other panel).
"""

# The Waydroid session's bus name: on the bus while the session runs (D-Bus
# activation starts it for `waydroid app launch`).
SESSION_NAME = "id.waydro.Session"

# How long the notice shows at most (s).
NOTICE_S = 30.0

# How long the app's window has to be there before the notice goes (s).
SETTLE_S = 0.5

# How long duo-wc waits for the window of an app launched while Android
# starts (ms; duo-wc takes up to 60 s).
COLD_HINT_MS = 40000

# Waydroid's entry for its whole Android screen and that window's app id.
FULL_UI_DESKTOP = "Waydroid"
FULL_UI_APP_ID = "Waydroid"

PREFIX = "waydroid."


def window_app_id(desktop_id):
    """The app id of the window an Android launcher entry opens, None for an
    entry that is not Android's: `waydroid.<package>.desktop` (Waydroid's
    entry for each app) opens `waydroid.<package>`, `Waydroid.desktop` the
    whole Android screen, `Waydroid`."""
    if not desktop_id:
        return None
    base = desktop_id[: -len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id
    if base == FULL_UI_DESKTOP:
        return FULL_UI_APP_ID
    if base.startswith(PREFIX) and len(base) > len(PREFIX):
        return base
    return None


def window_there(app_id, windows):
    """Whether duo-wc's window list (`state["windows"]`: dicts with `app_id`)
    has a window of `app_id`."""
    if not app_id or not isinstance(windows, list):
        return False
    return any(isinstance(w, dict) and w.get("app_id") == app_id for w in windows)


class Starting:
    """The notices shown while Android starts, by panel: the app id each
    waits for, since when, and since when that window has been there."""

    def __init__(self):
        self.notices = {}  # output -> {"app_id", "since", "seen"}

    def pending(self):
        return bool(self.notices)

    def launch(self, output, desktop_id, session_running, now):
        """An app is launched on `output`; `session_running`: the session is
        on the bus (None: not known, taken as running). Returns the
        expect_window timeout to ask for (ms), None for duo-wc's default;
        a notice for `output` is in `notices` when one is to show."""
        app_id = window_app_id(desktop_id)
        if app_id is None or not output:
            return None
        starting = session_running is False or self.pending()
        if not starting:
            return None
        if output not in self.notices or self.notices[output]["app_id"] != app_id:
            self.notices[output] = {"app_id": app_id, "since": now, "seen": None}
        return COLD_HINT_MS

    def tick(self, windows, now):
        """The windows duo-wc lists now: the panels whose notice ends (its
        app's window has been there for SETTLE_S, or NOTICE_S are over);
        they are taken out."""
        done = []
        for output, notice in list(self.notices.items()):
            if window_there(notice["app_id"], windows):
                if notice["seen"] is None:
                    notice["seen"] = now
            else:
                notice["seen"] = None
            settled = notice["seen"] is not None and now - notice["seen"] >= SETTLE_S
            if settled or now - notice["since"] >= NOTICE_S:
                done.append(output)
                del self.notices[output]
        return done

    def drop(self, output=None):
        """Forget the notice of a panel (gone), or every notice (None: the
        screen went dark)."""
        if output is None:
            self.notices.clear()
        else:
            self.notices.pop(output, None)
