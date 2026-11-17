"""pen-hover-timing's two kinds of interval (touch/tools/pen-hover-timing.py):
between new positions (what moves the cursor) and between any frames (what
libinput's 50 ms proximity timer sees), and libinput's made-up proximity-outs.
Fed synthetic evdev events, no device needed.

    touch/tests/run.sh   (or: python3 -m unittest discover -s touch/tests)
"""

import importlib.util
import pathlib
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools/pen-hover-timing.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("pen_hover_timing", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_tool()


def frame(tr, t, x=None, y=None, msc=True, touch=None):
    """One frame: the changed values, MSC_TIMESTAMP (as duo2-touchd sends it), SYN."""
    if touch is not None:
        tr.event(t, T.EV_KEY, T.BTN_TOUCH, int(touch))
    if x is not None:
        tr.event(t, T.EV_ABS, T.ABS_X, x)
    if y is not None:
        tr.event(t, T.EV_ABS, T.ABS_Y, y)
    if msc:
        tr.event(t, T.EV_MSC, 5, int(t * 1e6) & 0x7FFFFFFF)
    tr.event(t, T.EV_SYN, 0, 0)


def prox(tr, t, on):
    tr.event(t, T.EV_KEY, T.BTN_TOOL_PEN, int(on))


class Intervals(unittest.TestCase):
    def test_positions_and_frames_apart(self):
        # Hover: a new position every 15 ms, a repeat (timestamp only) 7.5 ms
        # after each; then 200 ms still with keep-alives every 30 ms.
        tr = T.Track(100, 100)
        prox(tr, 0.0, True)
        t = 0.0
        for i in range(20):
            frame(tr, t, x=1000 + i, y=2000)
            frame(tr, t + 0.0075)
            t += 0.015
        for k in range(7):
            frame(tr, t + k * 0.030)
        t += 0.200
        frame(tr, t, x=1100, y=2000)
        self.assertEqual(len(tr.iv["hover"]), 20)
        self.assertAlmostEqual(max(tr.iv["hover"]), 215.0, places=3)
        self.assertEqual(len(tr.gaps), 1)
        # the frames never stop for 50 ms: no gap for libinput's timer
        self.assertEqual(tr.frame_gaps, [])
        self.assertLessEqual(max(tr.fiv["hover"]), 30.0 + 1e-6)
        self.assertEqual(tr.still["hover"], 20 + 7)
        self.assertEqual(len(tr.step["hover"]), 20)

    def test_old_daemon_silence_is_a_frame_gap(self):
        # Before MSC_TIMESTAMP: a repeat is no frame at all, so a still pen is
        # silence, and libinput's timer runs out.
        tr = T.Track(100, 100)
        prox(tr, 0.0, True)
        frame(tr, 0.000, x=10, y=10, msc=False)
        frame(tr, 0.015, x=11, y=10, msc=False)
        frame(tr, 0.105, x=12, y=10, msc=False)
        self.assertEqual(len(tr.frame_gaps), 1)
        self.assertAlmostEqual(tr.frame_gaps[0][1], 90.0, places=3)
        self.assertEqual(len(tr.gaps), 1)
        lines = "\n".join(tr.report())
        self.assertIn("hover frames (libinput's timer): 2, 0 without a new position", lines)
        self.assertIn(">50 ms 1", lines)
        self.assertIn("libinput's timer runs out", lines)

    def test_modes_and_proximity_reset(self):
        tr = T.Track(100, 100)
        prox(tr, 0.0, True)
        frame(tr, 0.000, x=10, y=10)
        frame(tr, 0.0075, x=11, y=10, touch=True)
        frame(tr, 0.015, x=12, y=10)
        self.assertEqual(len(tr.iv["touch"]), 2)
        self.assertEqual(tr.iv["hover"], [])
        # out and back in a second later: the time away is no interval
        frame(tr, 0.020, touch=False)
        prox(tr, 0.020, False)
        tr.event(0.020, T.EV_SYN, 0, 0)
        prox(tr, 1.020, True)
        frame(tr, 1.020, x=50, y=50)
        frame(tr, 1.035, x=51, y=50)
        self.assertEqual(len(tr.iv["hover"]), 1)
        self.assertAlmostEqual(tr.iv["hover"][0], 15.0, places=3)
        self.assertEqual(tr.frame_gaps, [])
        self.assertEqual([p for _, p in tr.prox_changes], [True, False, True])

    def test_clear_keeps_the_state(self):
        tr = T.Track()
        prox(tr, 0.0, True)
        frame(tr, 0.0, x=1, y=1)
        frame(tr, 0.1, x=2, y=1)
        tr.clear()
        self.assertEqual(tr.iv["hover"], [])
        self.assertEqual(tr.gaps, [])
        frame(tr, 0.115, x=3, y=1)
        self.assertEqual(len(tr.iv["hover"]), 1)


class LibinputView(unittest.TestCase):
    def test_proximity_lines(self):
        line_in = (
            " event16  TABLET_TOOL_PROXIMITY   +1.234s\t\t12.34*/56.78*\tpen      (0, id 0)"
            " proximity-in\taxes:pt\tbtn:TS"
        )
        line_out = " event16  TABLET_TOOL_PROXIMITY   +1.300s\t\t12.34/56.78\tpen      (0, id 0) proximity-out"
        self.assertEqual(T.libinput_proximity(line_in), "in")
        self.assertEqual(T.libinput_proximity(line_out), "out")
        self.assertIsNone(T.libinput_proximity(" event16  TABLET_TOOL_AXIS  +1.2s  12.3/45.6"))
        self.assertIsNone(T.libinput_proximity("-event16  DEVICE_ADDED  Surface Duo 2 Pen Right"))

    def test_made_up_outs(self):
        # The device sent one in and one out; libinput said 4 in, 4 out.
        device = [(0.0, True), (9.0, False)]
        lib = [
            (0.0, "in"),
            (0.2, "out"),
            (0.3, "in"),
            (0.6, "out"),
            (0.7, "in"),
            (1.0, "out"),
            (1.1, "in"),
            (9.0, "out"),
        ]
        self.assertEqual(T.libinput_summary(lib, device), (4, 4, 3))
        self.assertEqual(T.libinput_summary([(0.0, "in"), (9.0, "out")], device), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
