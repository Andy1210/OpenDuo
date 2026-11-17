import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import swipe  # noqa: E402


class Swipe(unittest.TestCase):
    def test_direction(self):
        self.assertIsNone(swipe.decide(3, 4, up=True))
        self.assertEqual(swipe.decide(30, 5, up=False), "x")
        self.assertEqual(swipe.decide(-30, 5, up=False), "x")
        self.assertEqual(swipe.decide(2, -30, up=True), "y")
        # Vertical without `up`, or downwards: leave it to a scrolled list.
        self.assertEqual(swipe.decide(2, -30, up=False), "deny")
        self.assertEqual(swipe.decide(2, 30, up=True), "deny")

    def test_release(self):
        self.assertEqual(swipe.release(None, 2, 3, 300, 80), "tap")
        self.assertEqual(swipe.release(None, 20, 3, 300, 80), "back")
        self.assertEqual(swipe.release("x", 120, 0, 300, 80), "dismiss")
        self.assertEqual(swipe.release("x", -120, 0, 300, 80), "dismiss")
        self.assertEqual(swipe.release("x", 70, 0, 300, 80), "back")
        self.assertEqual(swipe.release("y", 0, -60, 300, 80), "dismiss")
        self.assertEqual(swipe.release("y", 0, -20, 300, 80), "back")
        self.assertEqual(swipe.release("deny", 0, 200, 300, 80), "back")

    def test_fades(self):
        self.assertEqual(swipe.opacity(0, 0, 300), 1.0)
        self.assertAlmostEqual(swipe.opacity(300, 0, 300), 0.2)


if __name__ == "__main__":
    unittest.main()
