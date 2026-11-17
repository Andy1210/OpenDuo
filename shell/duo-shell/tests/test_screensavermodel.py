import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import screensavermodel as m  # noqa: E402


class Inhibits(unittest.TestCase):
    def test_inhibit_and_let_go(self):
        inh = m.Inhibits(first_cookie=7)
        c = inh.add(":1.10", "Chromium", "Playing video")
        self.assertEqual(c, 7)
        self.assertEqual(inh.holds(), [{"app": "Chromium"}])
        self.assertTrue(inh.set_pid(c, 4321))
        self.assertFalse(inh.set_pid(c, 4321))
        self.assertEqual(inh.holds(), [{"app": "Chromium", "pid": 4321}])
        self.assertTrue(inh.remove(c, ":1.10"))
        self.assertEqual(inh.holds(), [])
        # Gone already.
        self.assertFalse(inh.remove(c, ":1.10"))

    def test_only_its_own_connection_lets_go(self):
        inh = m.Inhibits()
        c = inh.add(":1.10", "firefox", "video-playing")
        self.assertFalse(inh.remove(c, ":1.11"))
        self.assertEqual(len(inh.holds()), 1)

    def test_a_connection_that_leaves_takes_its_own_along(self):
        inh = m.Inhibits()
        a = inh.add(":1.10", "firefox", "video-playing")
        b = inh.add(":1.10", "firefox", "screen")
        c = inh.add(":1.20", "", "Playing")  # the portal's backend, for an app without an id
        self.assertEqual(sorted(inh.drop_sender(":1.10")), sorted([a, b]))
        self.assertEqual(list(inh.items), [c])
        self.assertEqual(inh.drop_sender(":1.99"), [])
        self.assertEqual(inh.senders(), {":1.20"})

    def test_one_holder_per_app_and_process(self):
        inh = m.Inhibits()
        for reason in ("screen", "video-playing"):
            inh.set_pid(inh.add(":1.10", "firefox", reason), 100)
        inh.set_pid(inh.add(":1.30", "Chromium", "x"), 300)
        unnamed = inh.add(":1.20", "", "Playing")
        self.assertEqual(
            inh.holds(),
            [{"app": "Chromium", "pid": 300}, {"app": "firefox", "pid": 100}, {"app": "unnamed"}],
        )
        # The process's name stands in for no name; a name given stays.
        # (/proc/PID/comm keeps 15 characters of xdg-desktop-portal-gtk)
        self.assertTrue(inh.set_fallback_name(unnamed, m.fallback_name("xdg-desktop-por")))
        self.assertEqual(inh.items[unnamed]["app"], "portal")
        self.assertFalse(inh.set_fallback_name(unnamed, "other"))
        self.assertEqual(m.fallback_name("celluloid"), "celluloid")

    def test_cookies_are_unique_and_never_zero(self):
        inh = m.Inhibits(first_cookie=0xFFFFFFFF)
        first = inh.add(":1.1", "a", "")
        second = inh.add(":1.1", "b", "")
        self.assertEqual(first, 0xFFFFFFFF)
        self.assertEqual(second, 1)
        self.assertNotIn(0, inh.items)

    def test_too_many_are_refused(self):
        inh = m.Inhibits()
        cookies = [inh.add(":1.1", "spam", "") for _ in range(m.MAX_PER_SENDER + 3)]
        self.assertEqual(sum(1 for c in cookies if c is not None), m.MAX_PER_SENDER)
        for i in range(m.MAX_INHIBITS):
            inh.add(":2.%d" % i, "app", "")
        self.assertEqual(len(inh.items), m.MAX_INHIBITS)
        self.assertIsNone(inh.add(":3.1", "late", ""))

    def test_names_are_cleaned(self):
        self.assertEqual(m.clean_name("Chromium\n\tvideo"), "Chromium video")
        self.assertEqual(m.clean_name(None), "")
        self.assertEqual(len(m.clean_name("x" * 500)), m.MAX_NAME)
        inh = m.Inhibits()
        c = inh.add(":1.1", "evil\x1b[31mapp", "r\x00eason")
        self.assertEqual(inh.items[c]["app"], "evil [31mapp")
        self.assertEqual(inh.items[c]["reason"], "r eason")


class Active(unittest.TestCase):
    def test_blanked_or_locked(self):
        self.assertFalse(m.active(None))
        self.assertFalse(m.active({"lit": ["DSI-1"], "blanked": False, "locked": False}))
        self.assertTrue(m.active({"lit": [], "blanked": True, "locked": False}))
        self.assertTrue(m.active({"lit": ["DSI-1"], "blanked": False, "locked": True}))


if __name__ == "__main__":
    unittest.main()
