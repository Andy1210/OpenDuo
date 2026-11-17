import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import dndmodel as dnd  # noqa: E402
from duoshell import hapticsmodel  # noqa: E402


class Dnd(unittest.TestCase):
    def test_feedbackd_path_for_calls(self):
        # fbd-feedback-manager.c: munge_app_id + APP_PREFIX
        self.assertEqual(
            dnd.app_profile_path("org.gnome.Calls"),
            "/org/sigxcpu/feedbackd/application/org-gnome-calls/",
        )
        self.assertEqual(dnd.munge_app_id("sm.puri.Chatty_1"), "sm-puri-chatty-1")

    def test_prefs_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "dnd.json")
            self.assertEqual(dnd.load(path), dnd.DEFAULTS)
            dnd.save({"repeat_callers": False, "contacts": True}, path)
            self.assertEqual(dnd.load(path), {"repeat_callers": False, "contacts": True})
            with open(path, "w") as f:
                f.write('{"contacts": "yes"}')
            self.assertEqual(dnd.load(path), dnd.DEFAULTS)  # not a bool: the default

    def test_repeated_caller_rings_through(self):
        prefs = dict(dnd.DEFAULTS)
        history = {}
        call = {"id": "+49 151 1234567", "name": ""}
        self.assertIsNone(dnd.allowed(call, history, prefs, 1000.0))
        dnd.remember(history, call, 1000.0)
        self.assertEqual(dnd.allowed(call, history, prefs, 1000.0 + 10 * 60), "repeat")
        # the same number written differently is the same caller
        self.assertEqual(dnd.allowed({"id": "+491511234567"}, history, prefs, 1100.0), "repeat")
        # too late, or the rule off
        self.assertIsNone(dnd.allowed(call, history, prefs, 1000.0 + dnd.REPEAT_WINDOW_S + 1))
        self.assertIsNone(dnd.allowed(call, history, dict(prefs, repeat_callers=False), 1100.0))
        # another caller does not count
        self.assertIsNone(dnd.allowed({"id": "+491517654321"}, history, prefs, 1100.0))

    def test_hidden_number_never_repeats(self):
        history = {}
        for _ in range(3):
            dnd.remember(history, {"id": ""}, 1000.0)
            self.assertIsNone(dnd.allowed({"id": ""}, history, dict(dnd.DEFAULTS), 1001.0))
        self.assertEqual(history, {})

    def test_contacts(self):
        prefs = dict(dnd.DEFAULTS, contacts=True)
        self.assertEqual(dnd.allowed({"id": "123456", "name": "Anna"}, {}, prefs, 0), "contact")
        self.assertIsNone(dnd.allowed({"id": "123456", "name": " "}, {}, prefs, 0))
        self.assertIsNone(dnd.allowed({"id": "123456", "name": "Anna"}, {}, dict(dnd.DEFAULTS), 0))

    def test_history_is_pruned(self):
        history = {}
        dnd.remember(history, {"id": "111222"}, 0.0)
        dnd.remember(history, {"id": "333444"}, dnd.REPEAT_WINDOW_S + 5)
        self.assertEqual(list(history), ["333444"])

    def test_notifications_are_quiet(self):
        # do not disturb (banners off): no feedback but for critical ones
        self.assertIsNone(hapticsmodel.notification_event("im.received", 1, {}, False, False))
        self.assertEqual(
            hapticsmodel.notification_event("x-gnome.alarm", 2, {}, False, False),
            "alarm-clock-elapsed",
        )


if __name__ == "__main__":
    unittest.main()
