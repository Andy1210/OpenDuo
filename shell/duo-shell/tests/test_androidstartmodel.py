import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import androidstartmodel as m

STORE = "waydroid.com.android.vending.desktop"
STORE_ID = "waydroid.com.android.vending"


def win(app_id):
    return {"id": 1, "app_id": app_id, "placement": {"output": "DSI-2"}, "minimized": False}


class WindowAppId(unittest.TestCase):
    def test_android_entries_and_others(self):
        self.assertEqual(m.window_app_id(STORE), STORE_ID)
        self.assertEqual(m.window_app_id(STORE_ID), STORE_ID)
        self.assertEqual(m.window_app_id("Waydroid.desktop"), "Waydroid")
        self.assertIsNone(m.window_app_id("org.gnome.TextEditor.desktop"))
        self.assertIsNone(m.window_app_id("waydroid"))
        self.assertIsNone(m.window_app_id("waydroid.desktop"))
        self.assertIsNone(m.window_app_id(""))
        self.assertIsNone(m.window_app_id(None))

    def test_window_there(self):
        self.assertTrue(m.window_there(STORE_ID, [win("foot"), win(STORE_ID)]))
        self.assertFalse(m.window_there(STORE_ID, [win("waydroid.com.android.settings")]))
        self.assertFalse(m.window_there(STORE_ID, None))
        self.assertFalse(m.window_there(STORE_ID, [None, "x"]))


class Starting(unittest.TestCase):
    def test_a_cold_start_shows_the_notice_and_asks_a_longer_hint(self):
        s = m.Starting()
        self.assertEqual(s.launch("DSI-2", STORE, False, 0.0), m.COLD_HINT_MS)
        self.assertEqual(s.notices["DSI-2"]["app_id"], STORE_ID)
        self.assertTrue(s.pending())

    def test_android_running_or_not_known_no_notice(self):
        s = m.Starting()
        self.assertIsNone(s.launch("DSI-2", STORE, True, 0.0))
        self.assertIsNone(s.launch("DSI-2", STORE, None, 0.0))
        self.assertFalse(s.pending())

    def test_a_linux_app_never(self):
        s = m.Starting()
        self.assertIsNone(s.launch("DSI-2", "org.gnome.TextEditor.desktop", False, 0.0))
        self.assertIsNone(s.launch("", STORE, False, 0.0))
        self.assertFalse(s.pending())

    def test_tapped_again_while_android_starts(self):
        # The session is on the bus 0.5 s after the tap, Android boots for
        # ~19 s: a second tap keeps the notice (from the first tap) and
        # asks the longer hint again.
        s = m.Starting()
        s.launch("DSI-2", STORE, False, 0.0)
        self.assertEqual(s.launch("DSI-2", STORE, True, 15.0), m.COLD_HINT_MS)
        self.assertEqual(s.notices["DSI-2"]["since"], 0.0)
        # Another Android app on the other panel meanwhile: a notice there.
        self.assertEqual(
            s.launch("DSI-1", "waydroid.org.fdroid.fdroid.desktop", True, 16.0), m.COLD_HINT_MS
        )
        self.assertIn("DSI-1", s.notices)
        # A Linux app meanwhile: its own default hint, no notice.
        self.assertIsNone(s.launch("DSI-1", "org.gnome.Calculator.desktop", True, 16.5))

    def test_the_notice_goes_once_the_window_has_stayed(self):
        s = m.Starting()
        s.launch("DSI-2", STORE, False, 0.0)
        self.assertEqual(s.tick([], 1.0), [])
        # Waydroid's first toplevel, gone at once: no end.
        self.assertEqual(s.tick([win(STORE_ID)], 3.0), [])
        self.assertEqual(s.tick([], 3.25), [])
        # The app's own window, there for SETTLE_S.
        self.assertEqual(s.tick([win(STORE_ID)], 19.0), [])
        self.assertEqual(s.tick([win(STORE_ID)], 19.0 + m.SETTLE_S), ["DSI-2"])
        self.assertFalse(s.pending())

    def test_the_notice_goes_after_at_most_notice_s(self):
        s = m.Starting()
        s.launch("DSI-2", STORE, False, 0.0)
        self.assertEqual(s.tick([], m.NOTICE_S - 0.1), [])
        self.assertEqual(s.tick([], m.NOTICE_S), ["DSI-2"])
        self.assertFalse(s.pending())

    def test_dropped(self):
        s = m.Starting()
        s.launch("DSI-1", STORE, False, 0.0)
        s.launch("DSI-2", STORE, False, 0.0)
        s.drop("DSI-1")
        self.assertEqual(list(s.notices), ["DSI-2"])
        s.drop()
        self.assertFalse(s.pending())


if __name__ == "__main__":
    unittest.main()
