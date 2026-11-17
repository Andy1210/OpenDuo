import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import notifystore as ns  # noqa: E402


def add(
    store, summary="hi", app="app", replaces=0, icon="", body="", actions=(), hints=None, timeout=-1
):
    return store.notify(
        app, replaces, icon, summary, body, list(actions), hints or {}, timeout, 1000.0
    )


class Store(unittest.TestCase):
    def test_ids_count_up_and_replace_keeps_the_id(self):
        s = ns.Store()
        a, replaced = add(s, "one")
        b, _ = add(s, "two")
        self.assertEqual((a.id, b.id, replaced), (1, 2, False))
        c, replaced = add(s, "one again", replaces=a.id)
        self.assertTrue(replaced)
        self.assertEqual(c.id, a.id)
        self.assertEqual([n.summary for n in s.newest_first()], ["one again", "two"])
        # Replacing an id that is gone makes a new one.
        d, replaced = add(s, "x", replaces=99)
        self.assertFalse(replaced)
        self.assertEqual(d.id, 3)

    def test_remove_and_clear(self):
        s = ns.Store()
        a, _ = add(s)
        add(s)
        self.assertIs(s.remove(a.id), a)
        self.assertIsNone(s.remove(a.id))
        self.assertEqual(len(s.clear()), 1)
        self.assertEqual(s.items, [])

    def test_bar_icons_are_distinct_apps_newest_first(self):
        s = ns.Store()
        add(s, app="mail", icon="mail-unread")
        add(s, app="chat", icon="chat")
        add(s, app="mail", icon="mail-unread")
        add(s, app="cal")
        add(s, app="news")
        icons = s.bar_icons()
        self.assertEqual([n.app_name for n in icons], ["news", "cal", "mail"])


class Notification(unittest.TestCase):
    def test_banner_time(self):
        s = ns.Store()
        self.assertEqual(add(s)[0].banner_ms(), ns.BANNER_MS)
        self.assertEqual(add(s, timeout=8000)[0].banner_ms(), 8000)
        self.assertEqual(add(s, timeout=100)[0].banner_ms(), 1500)
        self.assertIsNone(add(s, hints={"urgency": 2})[0].banner_ms())

    def test_body_markup_is_plain(self):
        s = ns.Store()
        n, _ = add(s, body="<b>Bold</b> &amp; <i>it</i> <a href='x'>link</a>")
        self.assertEqual(n.body, "Bold & it link")

    def test_actions_pair_up(self):
        s = ns.Store()
        n, _ = add(s, actions=["default", "Open", "reply", "Reply"])
        self.assertEqual(n.actions, [("default", "Open"), ("reply", "Reply")])
        self.assertTrue(n.has_default_action())
        self.assertFalse(add(s, actions=["reply", "Reply"])[0].has_default_action())

    def test_icons(self):
        s = ns.Store()
        self.assertEqual(add(s, icon="mail-unread")[0].icon(), ("name", "mail-unread"))
        self.assertEqual(add(s, icon="file:///tmp/a.png")[0].icon(), ("file", "/tmp/a.png"))
        self.assertEqual(add(s, icon="/tmp/b.png")[0].icon(), ("file", "/tmp/b.png"))
        self.assertEqual(
            add(s, hints={"image-path": "/tmp/c.png"}, icon="x")[0].icon(), ("file", "/tmp/c.png")
        )
        self.assertEqual(add(s, hints={"desktop-entry": "foot"})[0].icon(), ("desktop", "foot"))
        self.assertIsNone(add(s)[0].icon())

    def test_flags(self):
        s = ns.Store()
        n, _ = add(s, hints={"transient": True, "urgency": 0})
        self.assertTrue(n.transient)
        self.assertEqual(n.urgency, ns.URGENCY_LOW)
        self.assertEqual(add(s, hints={"urgency": "bogus"})[0].urgency, ns.URGENCY_NORMAL)


if __name__ == "__main__":
    unittest.main()


class WakesTheScreen(unittest.TestCase):
    def test_alarms_and_critical_ones_wake_others_do_not(self):
        s = ns.Store()
        normal, _ = add(s, "mail")
        self.assertFalse(ns.wakes_screen(normal))
        critical, _ = add(s, "battery low", hints={"urgency": 2})
        self.assertTrue(ns.wakes_screen(critical))
        alarm, _ = add(s, "Alarm", app="Clocks", hints={"desktop-entry": "org.gnome.clocks"})
        self.assertTrue(ns.wakes_screen(alarm))
