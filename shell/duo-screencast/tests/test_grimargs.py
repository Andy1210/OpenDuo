import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoscreencast import grimargs as m  # noqa: E402

G = m.REAL_GRIM


class GrimArgs(unittest.TestCase):
    def test_the_portals_plain_screenshot(self):
        self.assertEqual(
            m.real_command(["--", "/tmp/out.png"], "DSI-2", 2.5),
            [G, "-s", "2.5", "-o", "DSI-2", "--", "/tmp/out.png"],
        )

    def test_both_panels(self):
        self.assertEqual(
            m.real_command(["/tmp/out.png"], "all", 2.5), [G, "-s", "2.5", "--", "/tmp/out.png"]
        )

    def test_the_empty_region_of_a_missing_slurp_is_dropped(self):
        self.assertEqual(
            m.real_command(["-g", "", "--", "/tmp/out.png"], "DSI-1"),
            [G, "-o", "DSI-1", "--", "/tmp/out.png"],
        )

    def test_colour_picking_is_refused(self):
        with self.assertRaises(m.Refused):
            m.real_command(["-g", "", "-t", "ppm", "-"], "DSI-1")

    def test_own_options_are_kept(self):
        self.assertEqual(
            m.real_command(["-s", "1", "-t", "jpeg", "-q", "80", "-c", "x.jpg"], "DSI-1", 2.5),
            [G, "-s", "1", "-t", "jpeg", "-q", "80", "-c", "-o", "DSI-1", "--", "x.jpg"],
        )

    def test_odd_command_lines_are_refused(self):
        for argv in (["-x", "a.png"], ["a.png", "b.png"], ["-g"], [], ["-h"]):
            with self.assertRaises(m.Refused, msg=argv):
                m.real_command(argv, "DSI-1")


if __name__ == "__main__":
    unittest.main()
