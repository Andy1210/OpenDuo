import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import timefmt  # noqa: E402


class TimeFmt(unittest.TestCase):
    def test_hours(self):
        self.assertTrue(timefmt.uses_24h("%T"))  # en_GB
        self.assertTrue(timefmt.uses_24h("%H:%M:%S"))
        self.assertFalse(timefmt.uses_24h("%r"))  # en_US
        self.assertFalse(timefmt.uses_24h("%I:%M:%S %p"))

    def test_month_first(self):
        self.assertTrue(timefmt.month_first("%m/%d/%y"))  # en_US
        self.assertFalse(timefmt.month_first("%d/%m/%y"))  # en_GB
        self.assertFalse(timefmt.month_first("%Y-%m-%d"))
        self.assertFalse(timefmt.month_first("%d.%m.%Y"))

    def test_full_dates_follow_the_language(self):
        self.assertEqual(timefmt.full_dates("hu_HU.UTF-8")[0], "%Y. %B %-d., %A")
        self.assertEqual(timefmt.full_dates("en_GB.UTF-8@sun")[0], "%A %-d %B %Y")
        self.assertEqual(timefmt.full_dates("en_US.UTF-8")[0], "%A, %B %-d, %Y")
        self.assertEqual(timefmt.full_dates("de_DE.UTF-8")[0], "%A, %-d. %B %Y")
        # longest first: the year goes, then the names are abbreviated
        self.assertEqual(
            timefmt.full_dates("hu_HU.UTF-8"), ["%Y. %B %-d., %A", "%B %-d., %A", "%b %-d., %a"]
        )

    def test_full_dates_of_an_unknown_language_follow_its_date_order(self):
        self.assertEqual(timefmt.full_dates("xx_YY.UTF-8", "%m/%d/%y")[0], "%A, %B %-d, %Y")
        self.assertEqual(timefmt.full_dates("xx_YY.UTF-8", "%d.%m.%Y")[0], "%A %-d %B %Y")
        self.assertEqual(timefmt.full_dates("", None)[0], "%A %-d %B %Y")

    def test_full_dates_print_in_the_c_locale(self):
        import time

        t = time.strptime("2026-11-08", "%Y-%m-%d")
        out = [time.strftime(p, t) for p in timefmt.full_dates("en_GB.UTF-8")]
        self.assertEqual(out, ["Sunday 8 November 2026", "Sunday 8 November", "Sun 8 Nov"])


if __name__ == "__main__":
    unittest.main()
