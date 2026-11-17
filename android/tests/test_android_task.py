"""duo2-android-task's `fit`: a task Android placed on the shared display
beyond its own window is moved inside it (compositor/duo-wc/README.md
"Android apps", one display). The helper is root's and goes through
`waydroid shell`, so the bounds arithmetic (its fit_bounds function) is run
on its own here (python3 -m unittest discover -s tests, in android/)."""

import os
import re
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(os.path.dirname(HERE), "duo2-android-task")


def fit_bounds(*args):
    with open(HELPER) as f:
        text = f.read()
    body = re.search(r"^fit_bounds\(\) \{\n.*?^\}\n", text, re.S | re.M).group(0)
    out = subprocess.run(
        ["sh", "-c", body + 'fit_bounds "$@"', "sh"] + [str(a) for a in args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return tuple(int(v) for v in out.split())


class FitBounds(unittest.TestCase):
    def test_inside_stays(self):
        # A medium window on one panel's display (1345 x 1772 px).
        self.assertEqual(fit_bounds(157, 189, 1187, 1534, 1345, 1772), (157, 189, 1187, 1534))

    def test_placed_on_the_span_display_comes_inside_its_panel(self):
        # Centred by Android on the two panels' display (2755 px wide): it
        # keeps its size and comes to the panel's right edge.
        self.assertEqual(fit_bounds(862, 189, 1892, 1534, 1345, 1772), (315, 189, 1345, 1534))

    def test_bigger_than_the_panel_is_shrunk(self):
        self.assertEqual(fit_bounds(0, 0, 2755, 2000, 1345, 1772), (0, 0, 1345, 1772))
        self.assertEqual(fit_bounds(-50, -20, 400, 300, 1345, 1772), (0, 0, 450, 320))

    def test_lower_on_the_taller_display(self):
        # Placed on a full-screen panel's display (2000 px high).
        self.assertEqual(fit_bounds(157, 900, 1187, 1990, 1345, 1772), (157, 682, 1187, 1772))


if __name__ == "__main__":
    unittest.main()
