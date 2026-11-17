"""The pen scrolls our GTK windows (duosettings/penscroll.py): the pure parts, and the
shell's copy is the same file.  python3 -m unittest discover -s tests"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import penscroll  # noqa: E402

SHELL_COPY = os.path.join(os.path.dirname(HERE), "..", "duo-shell", "duoshell", "penscroll.py")


class PenScroll(unittest.TestCase):
    def test_threshold_is_gtks_square(self):
        self.assertFalse(penscroll.past_threshold(8, -8, 8))
        self.assertTrue(penscroll.past_threshold(0, 8.5, 8))
        self.assertTrue(penscroll.past_threshold(-9, 0, 8))

    def test_clamp_and_room(self):
        self.assertEqual(penscroll.clamp_value(-5, 0, 1000, 400), 0)
        self.assertEqual(penscroll.clamp_value(700, 0, 1000, 400), 600)
        self.assertEqual(penscroll.clamp_value(300, 0, 1000, 400), 300)
        self.assertEqual(penscroll.clamp_value(50, 0, 300, 400), 0)  # all of it fits
        self.assertTrue(penscroll.can_move(0, 1000, 400))
        self.assertFalse(penscroll.can_move(0, 400, 400))

    def test_velocity_from_the_last_100_ms(self):
        samples = [(0.0, 0, 0), (0.5, 0, -10)] + [
            (1.0 + i * 0.01, 0, -10 - 20 * i) for i in range(11)
        ]
        vx, vy = penscroll.velocity(samples)
        self.assertAlmostEqual(vx, 0.0)
        self.assertAlmostEqual(vy, -2000.0, delta=1)  # 20 px per 10 ms upwards
        self.assertEqual(penscroll.velocity([(0, 0, 0)]), (0.0, 0.0))
        self.assertEqual(penscroll.velocity([(1, 0, 0), (1, 5, 5)]), (0.0, 0.0))
        fast = penscroll.velocity([(0, 0, 0), (0.01, 0, 1000)])
        self.assertAlmostEqual(math.hypot(*fast), penscroll.MAX_SPEED)

    def test_a_flick_glides_and_stops(self):
        d0, v0 = penscroll.glide(1000.0, 0.0)
        self.assertEqual((d0, v0), (0.0, 1000.0))
        d1, v1 = penscroll.glide(1000.0, 0.25)
        d2, v2 = penscroll.glide(1000.0, 2.0)
        self.assertTrue(0 < d1 < d2 < 1000.0 / penscroll.FRICTION)
        self.assertTrue(v2 < v1 < 1000.0)
        self.assertLess(penscroll.glide(1000.0, 1.25)[1], penscroll.STOP_SPEED + 1000)
        # it ends: under STOP_SPEED within a few seconds
        t = math.log(1000.0 / penscroll.STOP_SPEED) / penscroll.FRICTION
        self.assertLess(t, 2.0)

    def test_the_pen_is_its_tool_on_wayland(self):
        # Wayland: the tablet's logical pointer (source mouse), the tool says pen
        self.assertTrue(penscroll.pen_press("mouse", "pen"))
        self.assertTrue(penscroll.pen_press("mouse", "eraser"))
        self.assertTrue(penscroll.pen_press("pen", None))  # X11: a pen device
        self.assertFalse(penscroll.pen_press("mouse", None))  # a mouse
        self.assertFalse(penscroll.pen_press("mouse", "mouse"))  # a tablet's puck
        self.assertFalse(penscroll.pen_press("touchscreen", None))

    def test_a_canvas_opts_out(self):
        self.assertTrue(penscroll.opted_out([["card"], ["pen-input"], []]))
        self.assertFalse(penscroll.opted_out([["card"], ["boxed-list"]]))

    def test_the_shells_copy_is_the_same(self):
        with (
            open(os.path.join(os.path.dirname(HERE), "duosettings", "penscroll.py"), "rb") as a,
            open(SHELL_COPY, "rb") as b,
        ):
            self.assertEqual(a.read(), b.read(), "shell/duo-shell/duoshell/penscroll.py differs")


if __name__ == "__main__":
    unittest.main()
