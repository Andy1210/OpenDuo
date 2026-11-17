"""duo2-autosleep-switch: the timed hold, the charging setting, status.
python3 -m unittest discover -s tests (from power/)."""

import contextlib
import io
import os
import tempfile
import time
import unittest

from test_autosleep import bat
from test_netwatch import load

sw = load("duo2-autosleep-switch")


class TestDuration(unittest.TestCase):
    def test_good(self):
        for text, s in (
            ("2h", 7200),
            ("90m", 5400),
            ("1h30m", 5400),
            ("1h 30min", 5400),
            ("45s", 45),
            ("3min", 180),
            ("2hours", 7200),
            ("1H", 3600),
            ("10minutes", 600),
        ):
            self.assertEqual(sw.parse_duration(text), s, text)

    def test_bad(self):
        for text in ("", "2", "h", "2x", "-1h", "2h30", "1.5h", "2h-", "two hours", "1d"):
            self.assertIsNone(sw.parse_duration(text), text)


class TestHoldFile(unittest.TestCase):
    def setUp(self):
        self.asl = sw.daemon()

    def test_lengthens_never_shortens(self):
        now = 1_000_000
        self.assertEqual(sw.hold_until(self.asl, 7200, now, None), now + 7200)
        self.assertEqual(sw.hold_until(self.asl, 180, now, now + 7200), now + 7200)
        self.assertEqual(sw.hold_until(self.asl, 7200, now, now + 180), now + 7200)
        # a lapsed one or one too far ahead (a wrong clock) does not count
        self.assertEqual(sw.hold_until(self.asl, 180, now, now - 10), now + 180)
        self.assertEqual(sw.hold_until(self.asl, 180, now, now + 40 * 86400), now + 180)

    def test_written_and_read_back(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "until")
            with open(p, "w") as f:
                f.write(sw.hold_file_text(now + 180, now, "3m"))
            self.assertEqual(self.asl.read_hold_until(p), float(int(now + 180)))
            with open(p) as f:
                self.assertIn("hold 3m", f.read())


class TestChargingSetting(unittest.TestCase):
    def test_replaced_in_place(self):
        text = "# c\ndelay = 20\n# stay_awake_charging = off (a comment)\nstay_awake_charging = on\nuser = someone\n"
        out = sw.set_charging(text, "off")
        self.assertEqual(out, text.replace("stay_awake_charging = on", "stay_awake_charging = off"))

    def test_appended(self):
        asl = sw.daemon()
        for text in ("delay = 20\n", "delay = 20", ""):
            out = sw.set_charging(text, "off")
            self.assertTrue(out.endswith("stay_awake_charging = off\n"))
            with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
                f.write(out)
            try:
                c = asl.read_conf(f.name)
            finally:
                os.unlink(f.name)
            self.assertEqual(c["stay_awake_charging"], "off")
            if text:
                self.assertEqual(c["delay"], 20)


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.asl = sw.daemon()
        self.on = dict(self.asl.DEFAULTS)

    def lines(self, hold=False, until=None, conf=None, b=None, now=1_000_000):
        return sw.status_lines(self.asl, hold, until, conf or self.on, b or bat(60), now)

    def test_first_line_is_the_switch(self):
        self.assertEqual(self.lines()[0], "on")
        out = self.lines(hold=True)
        self.assertEqual(out[0], "off")
        self.assertIn("(holds the phone awake)", out[1])

    def test_charging_says_it_holds(self):
        out = self.lines(b=bat(60, external=True, status="Charging"))
        self.assertEqual(
            out[-1], "stay awake while charging: on; on external power now (holds the phone awake)"
        )
        self.assertEqual(self.lines()[-1], "stay awake while charging: on; on battery now (60 %)")
        off = dict(self.on, stay_awake_charging="off")
        self.assertEqual(
            self.lines(conf=off, b=bat(60, external=True))[-1], "stay awake while charging: off"
        )
        weak = self.lines(b=bat(8, external=True, status="Discharging"))[-1]
        self.assertIn("not charging at 8 %", weak)
        self.assertNotIn("holds the phone awake", weak)

    def test_timed_hold(self):
        now = 1_000_000
        self.assertEqual(self.lines(now=now)[1], "timed hold: none")
        out = self.lines(until=now + 6730, now=now)[1]
        self.assertTrue(out.startswith("timed hold: until "), out)
        self.assertTrue(out.endswith("1 h 52 min more (holds the phone awake)"), out)
        self.assertTrue(self.lines(until=now - 60, now=now)[1].startswith("timed hold: lapsed at "))


class TestCommands(unittest.TestCase):
    """main() on files in a temporary directory."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        p = lambda n: os.path.join(self.d.name, n)  # noqa: E731
        self.asl = sw.daemon()
        self.asl.CONF, self.asl.RUN_CONF, self.asl.HOLD_UNTIL = (
            p("autosleep.conf"),
            p("run.conf"),
            p("until"),
        )
        self.asl.read_cmdline = lambda: ""
        self.battery = bat(60, external=True, status="Charging")
        self.asl.read_battery = lambda root=None: self.battery
        self.saved = (sw.daemon, sw.HOLD)
        sw.daemon = lambda path=None: self.asl
        sw.HOLD = p("hold")
        with open(self.asl.CONF, "w") as f:
            f.write("delay = 20\nstay_awake_charging = on\n")

    def tearDown(self):
        sw.daemon, sw.HOLD = self.saved
        self.d.cleanup()

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = sw.main(list(argv))
        return rc, out.getvalue().splitlines()

    def test_on_off_as_before(self):
        self.assertEqual(self.run_main("off"), (0, ["off"]))
        with open(sw.HOLD) as f:
            self.assertIn("Automatic sleep is off while this file exists", f.read())
        self.assertEqual(self.run_main("status")[1][0], "off")
        self.assertEqual(self.run_main("on"), (0, ["on"]))
        self.assertFalse(os.path.exists(sw.HOLD))
        self.assertEqual(self.run_main("on"), (0, ["on"]))  # already on: fine
        self.assertEqual(self.run_main()[1][0], "on")  # status by default

    def test_hold(self):
        rc, out = self.run_main("hold", "2h")
        self.assertEqual(rc, 0)
        self.assertTrue(out[0].startswith("timed hold: until "), out)
        end = self.asl.read_hold_until()
        self.assertAlmostEqual(end, time.time() + 7200, delta=5)
        self.run_main("hold", "3m")  # a shorter one: the 2 h stays
        self.assertEqual(self.asl.read_hold_until(), end)
        self.assertIn("(holds the phone awake)", self.run_main("status")[1][1])
        self.assertTrue(self.run_main("hold")[1][0].startswith("timed hold: until "))
        self.assertEqual(self.run_main("hold", "clear"), (0, ["timed hold: none"]))
        self.assertIsNone(self.asl.read_hold_until())
        self.assertEqual(self.run_main("hold", "clear")[0], 0)  # nothing to clear: fine
        for bad in ("30s", "25h", "2", "soon"):
            self.assertEqual(self.run_main("hold", bad)[0], 2, bad)
        self.assertIsNone(self.asl.read_hold_until())

    def test_hold_does_not_touch_the_switch(self):
        self.run_main("off")
        self.run_main("hold", "1h")
        self.run_main("on")
        self.assertIsNotNone(self.asl.read_hold_until())

    def test_charging(self):
        self.assertEqual(self.run_main("charging"), (0, ["on"]))
        self.assertIn("(holds the phone awake)", self.run_main("status")[1][-1])
        self.assertEqual(self.run_main("charging", "off"), (0, ["off"]))
        self.assertEqual(self.asl.read_conf(self.asl.CONF)["stay_awake_charging"], "off")
        self.assertEqual(self.asl.read_conf(self.asl.CONF)["delay"], 20)
        self.assertEqual(self.run_main("charging"), (0, ["off"]))
        self.assertEqual(self.run_main("status")[1][-1], "stay awake while charging: off")
        self.assertEqual(self.run_main("charging", "maybe")[0], 2)
        self.run_main("charging", "on")
        self.assertEqual(self.run_main("charging"), (0, ["on"]))

    def test_usage(self):
        for argv in (("bogus",), ("on", "now"), ("hold", "1h", "2h"), ("charging", "on", "x")):
            self.assertEqual(self.run_main(*argv)[0], 2, argv)


if __name__ == "__main__":
    unittest.main()
