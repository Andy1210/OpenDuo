import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoscreencast import panels as m  # noqa: E402

LEFT = {"name": "DSI-1", "x": 0, "power": "on", "scale": 2.5}
RIGHT = {"name": "DSI-2", "x": 564, "power": "on", "scale": 2.5}


class Panels(unittest.TestCase):
    def test_left_and_right_by_position(self):
        self.assertEqual(
            m.panels([RIGHT, LEFT]), [("DSI-1", "left", True), ("DSI-2", "right", True)]
        )

    def test_a_dark_panel_is_offered_but_marked(self):
        dark = dict(RIGHT, power="off")
        self.assertEqual(
            m.choices([LEFT, dark]), [("DSI-1", "Left panel"), ("DSI-2", "Right panel (dark)")]
        )

    def test_screenshots_may_take_both(self):
        self.assertEqual(m.choices([LEFT, RIGHT], screenshot=True)[-1], (m.ALL, "Both panels"))
        self.assertNotIn(m.ALL, [a for a, _ in m.choices([LEFT, RIGHT])])
        self.assertEqual(m.choices([LEFT], screenshot=True), [("DSI-1", "Panel DSI-1")])

    def test_rubbish_is_ignored(self):
        self.assertEqual(m.choices([None, {"x": 3}, "DSI-1"]), [])
        self.assertEqual(m.choices([]), [])

    def test_only_offered_answers_are_valid(self):
        self.assertTrue(m.valid_answer("DSI-2", [LEFT, RIGHT]))
        self.assertFalse(m.valid_answer("all", [LEFT, RIGHT]))
        self.assertTrue(m.valid_answer("all", [LEFT, RIGHT], screenshot=True))
        self.assertFalse(m.valid_answer("HDMI-A-1", [LEFT, RIGHT], screenshot=True))
        self.assertFalse(m.valid_answer("", [LEFT, RIGHT]))

    def test_no_socket_no_outputs(self):
        self.assertEqual(m.query_outputs("/nonexistent/duo-wc.sock", timeout=0.2), [])


if __name__ == "__main__":
    unittest.main()
