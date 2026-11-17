"""duo2-rtc-time's decisions: python3 -m unittest discover -s tests
(from power/)."""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(os.path.dirname(HERE), name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


rt = load("duo2-rtc-time")
WALL = 1794094794.25  # 2026-11-08
RTC = 179995


class Restore(unittest.TestCase):
    def saved(self, rtc=RTC, wall=WALL):
        return rt.plan_save(rtc, wall)

    def test_rtc_plus_offset(self):
        st = self.saved()
        # 1000 s later (the phone was off), the boot's clock is the clock file's
        wall, why = rt.plan_restore(st, RTC + 1000, WALL - 3600)
        self.assertAlmostEqual(wall, WALL + 1000, places=3)
        self.assertIn("offset", why)

    def test_nothing_saved(self):
        self.assertEqual(rt.plan_restore(None, RTC, 0)[0], None)
        self.assertEqual(rt.plan_restore({"offset": "x"}, RTC, 0)[0], None)

    def test_rtc_restarted(self):
        # the PMIC lost power: the RTC counts from 0 again, the offset is void
        wall, why = rt.plan_restore(self.saved(), 50, 0)
        self.assertIsNone(wall)
        self.assertIn("restarted", why)

    def test_never_far_back(self):
        st = self.saved()
        self.assertIsNone(rt.plan_restore(st, RTC + 10, WALL + 3600)[0])
        # a little behind (drift) is fine: the RTC is the better clock at boot
        self.assertIsNotNone(rt.plan_restore(st, RTC + 10, WALL + 40)[0])

    def test_not_before_the_save_or_2026(self):
        self.assertIsNone(rt.plan_restore({"offset": 0, "rtc": 10, "wall": 5}, 20, 0)[0])
        st = {"offset": WALL - RTC, "rtc": RTC, "wall": WALL + 99999}
        self.assertIsNone(rt.plan_restore(st, RTC + 1, 0)[0])


class Save(unittest.TestCase):
    def test_offset_and_drift(self):
        a = rt.plan_save(RTC, WALL)
        self.assertEqual(a, {"offset": round(WALL - RTC, 3), "rtc": RTC, "wall": round(WALL, 3)})
        b = rt.plan_save(RTC + 3600, WALL + 3600.1)
        self.assertAlmostEqual(rt.drift(a, b), 0.1, places=3)
        self.assertIsNone(rt.drift(None, b))

    def test_state_file_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "rtc-offset")
            st = rt.plan_save(RTC, WALL)
            rt.write_state(st, p)
            self.assertEqual(rt.load_state(p), st)
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o644")
            with open(p, "w") as f:
                f.write("{broken")
            self.assertIsNone(rt.load_state(p))
            self.assertIsNone(rt.load_state(os.path.join(d, "none")))
            json.dumps(st)


if __name__ == "__main__":
    unittest.main()
