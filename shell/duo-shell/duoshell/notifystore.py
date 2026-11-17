"""Notifications, as data: what org.freedesktop.Notifications was told, and
what the shell shows of it. Pure (tests/test_notifystore.py); the D-Bus
service and the windows are in notifications.py.

Phone rules:
- every notification goes into the list (the quick menu) and stays there
  until it is dismissed (a swipe, "Clear all", the app's CloseNotification,
  or an action), except `transient` ones, which go when their banner does;
- a banner shows the newest one for a few seconds (`expire_timeout` when the
  app gave one, else BANNER_MS); a critical one stays until it is dismissed;
- the status bar shows the icons of the newest few distinct apps.
"""

import html
import re

BANNER_MS = 5000
BAR_ICONS = 3

# NotificationClosed reasons (the spec's numbers).
EXPIRED, DISMISSED, CLOSED_BY_CALL, UNDEFINED = 1, 2, 3, 4

URGENCY_LOW, URGENCY_NORMAL, URGENCY_CRITICAL = 0, 1, 2

_TAG = re.compile(r"<[^>]*>")


def plain(text):
    """Body markup (the spec's b/i/u/a/img subset) as plain text."""
    if not text:
        return ""
    return html.unescape(_TAG.sub("", text)).strip()


class Notification:
    def __init__(self, nid, app_name, app_icon, summary, body, actions, hints, expire_timeout, at):
        self.id = nid
        self.app_name = app_name or ""
        self.app_icon = app_icon or ""
        self.summary = summary or ""
        self.body = plain(body)
        # actions come as [key, label, key, label, ...]
        self.actions = list(zip(actions[0::2], actions[1::2])) if actions else []
        self.hints = hints or {}
        self.expire_timeout = expire_timeout
        self.at = at

    @property
    def urgency(self):
        try:
            return int(self.hints.get("urgency", URGENCY_NORMAL))
        except (TypeError, ValueError):
            return URGENCY_NORMAL

    @property
    def transient(self):
        return bool(self.hints.get("transient", False))

    @property
    def desktop_entry(self):
        value = self.hints.get("desktop-entry")
        return value if isinstance(value, str) and value else None

    def banner_ms(self):
        """How long its banner shows, in ms; None = until dismissed."""
        if self.urgency == URGENCY_CRITICAL:
            return None
        if self.expire_timeout and self.expire_timeout > 0:
            return max(1500, min(self.expire_timeout, 30000))
        return BANNER_MS

    def has_default_action(self):
        return any(key == "default" for key, _label in self.actions)

    def icon(self):
        """What to show for it: ('file', path), ('name', icon-name),
        ('desktop', desktop-id), or None."""
        for value in (self.hints.get("image-path"), self.hints.get("image_path"), self.app_icon):
            if not isinstance(value, str) or not value:
                continue
            if value.startswith("file://"):
                return ("file", value[len("file://") :])
            if value.startswith("/"):
                return ("file", value)
            return ("name", value)
        if self.desktop_entry:
            return ("desktop", self.desktop_entry)
        return None

    def key(self):
        """What makes two notifications 'the same app' for the bar's icons."""
        return self.desktop_entry or self.app_name or self.app_icon or str(self.id)


class Store:
    """The notifications, newest last."""

    def __init__(self):
        self.items = []
        self.next_id = 1

    def notify(
        self, app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout, at
    ):
        """Add (or replace) one; returns (notification, replaced)."""
        old = self.get(replaces_id) if replaces_id else None
        if old is not None:
            nid = old.id
            self.items.remove(old)
        else:
            nid = self.next_id
            self.next_id += 1
            if self.next_id > 0xFFFFFFFF:
                self.next_id = 1
        n = Notification(nid, app_name, app_icon, summary, body, actions, hints, expire_timeout, at)
        self.items.append(n)
        return n, old is not None

    def get(self, nid):
        for n in self.items:
            if n.id == nid:
                return n
        return None

    def remove(self, nid):
        n = self.get(nid)
        if n is not None:
            self.items.remove(n)
        return n

    def clear(self):
        gone, self.items = self.items, []
        return gone

    def newest_first(self):
        return list(reversed(self.items))

    def bar_icons(self, limit=BAR_ICONS):
        """The newest notifications of up to `limit` distinct apps."""
        seen, out = set(), []
        for n in self.newest_first():
            key = n.key()
            if key in seen:
                continue
            seen.add(key)
            out.append(n)
            if len(out) == limit:
                break
        return out


# Apps whose notifications are alarms (a ringing alarm, a finished timer).
ALARM_APPS = {"org.gnome.clocks", "gnome-clocks", "clocks"}


def wakes_screen(n):
    """Whether a notification lights a dark screen, as a phone does for an
    alarm: critical urgency (GNOME Clocks' alarms and timers are urgent
    GNotifications, sent with critical urgency), or one from the clock app."""
    if n.urgency == URGENCY_CRITICAL:
        return True
    entry = (n.desktop_entry or "").lower()
    return entry in ALARM_APPS or (n.app_name or "").lower() in ALARM_APPS
