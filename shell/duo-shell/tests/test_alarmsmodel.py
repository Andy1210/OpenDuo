import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import alarmsmodel as m  # noqa: E402

CLOCKS = {"id": 3, "app_id": "org.gnome.clocks", "placement": {"output": "DSI-1"}}
OTHER = {"id": 4, "app_id": "org.gnome.TextEditor", "placement": {"output": "DSI-2"}}


class Plan(unittest.TestCase):
    def test_no_window_launches(self):
        self.assertEqual(m.plan([], False), "launch")
        self.assertEqual(m.plan(None, False), "launch")
        self.assertEqual(m.plan([OTHER], True), "launch")

    def test_an_open_window_is_closed_first(self):
        self.assertEqual(m.plan([OTHER, CLOCKS], False), "close")

    def test_a_ringing_alarm_is_never_closed(self):
        self.assertEqual(m.plan([CLOCKS], True), "front")

    def test_clocks_windows(self):
        self.assertEqual(m.clocks_windows([OTHER, CLOCKS]), [CLOCKS])


if __name__ == "__main__":
    unittest.main()
