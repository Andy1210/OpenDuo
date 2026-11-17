# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for brightnesspolicy.

python3 -I sensors/duo-brightness/test_brightnesspolicy.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brightnesspolicy as bp  # noqa: E402


class Curve(unittest.TestCase):
    def test_monotonic_and_bounded(self):
        prev = -1
        for lux in (0, 0.5, 1, 3, 10, 50, 100, 300, 1000, 5000, 20000, 1e6):
            b = bp.curve(lux)
            self.assertGreaterEqual(b, prev)
            self.assertTrue(0 <= b <= 100)
            prev = b

    def test_points(self):
        for lux, b in bp.CURVE:
            self.assertAlmostEqual(bp.curve(lux), b)
        self.assertEqual(bp.curve(-5), bp.CURVE[0][1])

    def test_perceptual_round_trip(self):
        for f in (0.01, 0.1, 0.5, 1.0):
            self.assertAlmostEqual(bp.to_fraction(bp.to_perceptual(f)), f, places=6)
        self.assertEqual(bp.to_fraction(0), bp.MIN_FRACTION)

    def test_offset(self):
        # what the user set at 100 lux comes back at 100 lux ...
        off = bp.offset_for(0.30, 100)
        self.assertAlmostEqual(bp.to_fraction(bp.target(100, off)), 0.30, places=6)
        # ... and moves with the light afterwards
        self.assertGreater(bp.target(1000, off), bp.target(100, off))
        self.assertLessEqual(abs(bp.offset_for(1.0, 0)), bp.OFFSET_LIMIT)

    def test_level(self):
        self.assertEqual(bp.level(0.0, 2047), 1)
        self.assertEqual(bp.level(1.0, 2047), 2047)
        self.assertEqual(bp.level(0.5, 100), 50)


class Hysteresis(unittest.TestCase):
    def feed(self, light, lux, t0, t1, step=0.25):
        t = t0
        ref = None
        while t <= t1 + 1e-9:
            light.feed(lux, t)
            ref = light.settled(t)
            t += step
        return ref

    def test_first_reading_is_the_reference(self):
        light = bp.Light()
        light.feed(42, 0)
        self.assertEqual(light.settled(0), 42)

    def test_small_changes_ignored(self):
        light = bp.Light()
        self.feed(light, 100, 0, 1)
        self.assertEqual(self.feed(light, 110, 1, 20), 100)  # +10 %: inside the band
        self.assertEqual(self.feed(light, 80, 20, 40), 100)  # -20 %: inside the band
        light2 = bp.Light()
        self.feed(light2, 1.0, 0, 1)
        self.assertEqual(self.feed(light2, 2.5, 1, 20), 1.0)  # < 2 lux: dim-room noise

    def test_brightening_follows_soon(self):
        light = bp.Light()
        self.feed(light, 50, 0, 1)
        ref = self.feed(light, 500, 1.25, 4.0)
        self.assertGreater(ref, 300)

    def test_darkening_waits(self):
        light = bp.Light()
        self.feed(light, 500, 0, 1)
        # a hand over the sensor for a second: nothing
        self.assertEqual(self.feed(light, 5, 1.25, 2.25), 500)
        self.assertEqual(self.feed(light, 500, 2.5, 10), 500)
        # the lights go out for good: it follows, later
        ref = self.feed(light, 5, 10.25, 30)
        self.assertLess(ref, 100)

    def test_reset(self):
        light = bp.Light()
        self.feed(light, 500, 0, 1)
        light.reset()
        light.feed(3, 2)
        self.assertEqual(light.settled(2), 3)


class Ramping(unittest.TestCase):
    def test_brighten_fast_darken_slow(self):
        r = bp.Ramp()
        r.jump(20)
        t = 0
        while r.step(80, 0.05):
            t += 0.05
        up = t
        t = 0
        while r.step(20, 0.05):
            t += 0.05
        self.assertLess(up, 2.0)
        self.assertGreater(t, up * 2)
        self.assertEqual(r.value, 20)

    def test_first_step_takes_the_target(self):
        r = bp.Ramp()
        self.assertFalse(r.step(55, 0.05))
        self.assertEqual(r.value, 55)


if __name__ == "__main__":
    unittest.main()
