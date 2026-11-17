# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for posepolicy and the protobuf codec of duo2_ssc.

python3 -I sensors/duo-posed/test_posepolicy.py
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duo2_ssc as ssc  # noqa: E402
import posepolicy as pp  # noqa: E402

G = pp.G


def up(deg, tilt=1.0):
    """Gravity reading of an upright panel whose up direction is turned by
    `deg` (0 = top edge up, 90 = right edge up, -90 = left edge up), standing
    up by `tilt` (1 upright, 0 lying flat face up)."""
    r = math.radians(deg)
    s = math.sqrt(max(0.0, 1 - tilt * tilt))
    return (G * tilt * math.sin(r), G * tilt * math.cos(r), G * s)


def halves(hinge, tilt_axis_deg=0.0):
    """Gravity in the left and right panel frames for a hinge angle, the left
    half lying flat face up but turned about the hinge axis (y) by
    tilt_axis_deg."""
    t = math.radians(tilt_axis_deg)
    left = (G * math.sin(t), 0.0, G * math.cos(t))
    alpha = math.radians(180.0 - hinge)
    gx, _, gz = left
    right = (
        gx * math.cos(alpha) + gz * math.sin(alpha),
        0.0,
        -gx * math.sin(alpha) + gz * math.cos(alpha),
    )
    return left, right


class Rotation(unittest.TestCase):
    def test_quarters(self):
        self.assertEqual(pp.rotation_candidate(up(0), None), 0)
        self.assertEqual(pp.rotation_candidate(up(-90), None), 90)  # left edge up
        self.assertEqual(pp.rotation_candidate(up(90), None), 270)  # right edge up
        self.assertEqual(pp.rotation_candidate(up(180), None), 180)

    def test_flat_says_nothing(self):
        self.assertIsNone(pp.rotation_candidate((0.1, 0.2, 9.8), 0))
        self.assertIsNone(pp.rotation_candidate(up(-90, tilt=0.3), 0))
        self.assertIsNone(pp.rotation_candidate(None, 0))

    def test_hysteresis(self):
        # 40 degrees towards 90 from 0: not yet (needs within 30 of 90's centre)
        self.assertEqual(pp.rotation_candidate(up(-50), 0), 0)
        self.assertEqual(pp.rotation_candidate(up(-62), 0), 90)
        # and back: from 90, 50 degrees off is not enough to return to 0
        self.assertEqual(pp.rotation_candidate(up(-40), 90), 90)
        self.assertEqual(pp.rotation_candidate(up(-25), 90), 0)

    def test_tilt(self):
        self.assertAlmostEqual(pp.tilt(up(0)), 1.0)
        self.assertAlmostEqual(pp.tilt((0, 0, G)), 0.0)


class DebounceT(unittest.TestCase):
    def test_settle(self):
        d = pp.Debounce(0.5)
        self.assertIsNone(d.feed("dual", 0.0))
        self.assertIsNone(d.feed("dual", 0.3))
        self.assertEqual(d.feed("dual", 0.6), "dual")
        self.assertIsNone(d.feed("dual", 0.7))  # no change: nothing new
        self.assertIsNone(d.feed("blank", 1.0))
        self.assertIsNone(d.feed("dual", 1.2))  # flap back: pending reset
        self.assertIsNone(d.feed("blank", 1.3))
        self.assertIsNone(d.feed("blank", 1.7))
        self.assertEqual(d.feed("blank", 1.9), "blank")

    def test_none_keeps(self):
        d = pp.Debounce(0.0)
        self.assertEqual(d.feed("dual", 0.0), "dual")
        self.assertIsNone(d.feed(None, 1.0))
        self.assertEqual(d.value, "dual")


class Hinge(unittest.TestCase):
    def test_estimate(self):
        for hinge in (10, 45, 90, 120, 180, 200, 270, 300, 350):
            for axis_tilt in (0.0, 30.0, -40.0):
                left, right = halves(hinge, axis_tilt)
                self.assertAlmostEqual(
                    pp.hinge_from_accels(left, right),
                    hinge,
                    places=3,
                    msg="hinge %d tilt %d" % (hinge, axis_tilt),
                )

    def test_swapped_halves_mirror(self):
        left, right = halves(60)
        self.assertAlmostEqual(pp.hinge_from_accels(right, left), 300.0, places=3)

    def test_hinge_vertical_is_unknown(self):
        # a book held upright: gravity along the hinge (y) in both halves
        self.assertIsNone(pp.hinge_from_accels((0.3, G, 0.5), (-0.2, G, 0.4)))

    def test_classifier_bands(self):
        c = pp.HingeClassifier(margin=6)
        self.assertEqual(c.classify(3), "closed")
        self.assertEqual(c.classify(15), "closed")  # within the margin
        self.assertEqual(c.classify(20), "peek")
        self.assertEqual(c.classify(180), "open")
        self.assertEqual(c.classify(218), "open")  # margin
        self.assertEqual(c.classify(240), "convex")
        self.assertEqual(c.classify(355), "flipped")
        self.assertEqual(c.classify(None), "flipped")  # unknown: keep

    def test_pose_from_hinge(self):
        self.assertEqual(pp.pose_from_hinge("closed", "DSI-1"), "blank")
        self.assertEqual(pp.pose_from_hinge("open", "DSI-1"), "dual")
        self.assertEqual(pp.pose_from_hinge("flipped", "DSI-2"), "single DSI-2")
        self.assertIsNone(pp.pose_from_hinge("peek", "DSI-2"))


class Posture(unittest.TestCase):
    OUT = {"left": "DSI-1", "right": "DSI-2"}

    def test_table(self):
        self.assertEqual(pp.pose_from_posture("closed", self.OUT), "blank")
        self.assertEqual(pp.pose_from_posture("read", self.OUT), "dual")
        self.assertEqual(pp.pose_from_posture("flip_p_right", self.OUT), "single DSI-2")
        # tent: the posture cannot tell which panel faces the user
        self.assertEqual(
            pp.pose_from_posture("tent_left", dict(self.OUT, active="DSI-2")), "single DSI-2"
        )
        self.assertEqual(
            pp.pose_from_posture("tent_right", dict(self.OUT, active="DSI-1")), "single DSI-1"
        )
        self.assertIsNone(pp.pose_from_posture("tent_right", self.OUT))
        self.assertIsNone(pp.pose_from_posture("peek_left", self.OUT))
        self.assertIsNone(pp.pose_from_posture("undetermined", self.OUT))

    def test_override(self):
        self.assertEqual(pp.pose_from_posture("open_dlm", self.OUT, {"open_dlm": "span"}), "span")
        self.assertIsNone(pp.pose_from_posture("read", self.OUT, {"read": "keep"}))


class Facing(unittest.TestCase):
    def test_face_up_wins(self):
        acc = {"DSI-1": (0, 0, G), "DSI-2": (0, 0, -G)}
        self.assertEqual(pp.facing_output(acc, "DSI-2"), "DSI-1")

    def test_unclear_keeps_fallback(self):
        acc = {"DSI-1": (0, G, 0.5), "DSI-2": (0, G, -0.5)}
        self.assertEqual(pp.facing_output(acc, "DSI-2"), "DSI-2")

    def test_rotation_source(self):
        acc = {"DSI-1": (0, 0, G), "DSI-2": up(-90)}
        self.assertEqual(pp.rotation_source("dual", acc), "DSI-2")  # the upright one
        self.assertEqual(pp.rotation_source("single DSI-1", acc), "DSI-1")


class Protobuf(unittest.TestCase):
    def test_roundtrip_request(self):
        msg = ssc.client_request((1, 2), 513, ssc.pb_float(1, 26.0), batch_period_us=500000)
        d = ssc.pb_decode(msg)
        suid = ssc.pb_msgs(d, 1)[0]
        self.assertEqual((ssc.pb_u64(suid, 1), ssc.pb_u64(suid, 2)), (1, 2))
        self.assertEqual(ssc.pb_int(d, 2), 513)
        req = ssc.pb_msgs(d, 4)[0]
        self.assertEqual(ssc.pb_int(ssc.pb_msgs(req, 1)[0], 1), 500000)
        self.assertEqual(ssc.pb_floats(ssc.pb_decode(req[2][0][1]), 1), [26.0])

    def test_std_event_packed_and_not(self):
        plain = ssc.pb_float(1, 1.5) + ssc.pb_float(1, -2.0) + ssc.pb_varint(2, 3)
        self.assertEqual(ssc.std_event(plain), ([1.5, -2.0], 3))
        import struct

        packed = ssc.pb_bytes(1, struct.pack("<2f", 0.5, 9.75)) + ssc.pb_varint(2, 1)
        self.assertEqual(ssc.std_event(packed), ([0.5, 9.75], 1))

    def test_negative_enum(self):
        self.assertEqual(ssc.state_event(ssc.pb_varint(1, -1)), -1)
        self.assertEqual(ssc.state_event(ssc.pb_varint(1, 14)), 14)


class TouchPose(unittest.TestCase):
    """What duo2-touchd is told (sensors/duo-posed -> /run/duo-posed/touch)."""

    def test_display(self):
        self.assertEqual(pp.touch_display(["DSI-1", "DSI-2"], False, "DSI-1", "DSI-2"), "both")
        self.assertEqual(pp.touch_display(["DSI-1"], False, "DSI-1", "DSI-2"), "left")
        self.assertEqual(pp.touch_display(["DSI-2"], False, "DSI-1", "DSI-2"), "right")
        # tent / brochure: the dark panel's taps must arrive (tap to switch)
        self.assertEqual(pp.touch_display(["DSI-2"], False, "DSI-1", "DSI-2", "tent_right"), "both")
        self.assertEqual(
            pp.touch_display(["DSI-1"], False, "DSI-1", "DSI-2", "brochure_left"), "both"
        )
        # folded back: the back panel stays off for touch
        self.assertEqual(
            pp.touch_display(["DSI-2"], False, "DSI-1", "DSI-2", "flip_p_right"), "right"
        )
        # dark: no opinion, the daemon keeps the last state (touch wakes)
        self.assertIsNone(pp.touch_display(["DSI-1", "DSI-2"], True, "DSI-1", "DSI-2"))
        self.assertIsNone(pp.touch_display([], False, "DSI-1", "DSI-2"))
        self.assertIsNone(pp.touch_display(None, False, "DSI-1", "DSI-2"))

    def test_rotation_is_android_s(self):
        self.assertEqual([pp.android_rotation(r) for r in (0, 90, 180, 270)], [0, 270, 180, 90])
        self.assertIsNone(pp.android_rotation(None))

    def test_text_leaves_out_the_unknown(self):
        t = pp.touch_pose_text(pp.touch_pose("both", 179.96, None, None))
        self.assertIn("display both\n", t)
        self.assertIn("hinge 180.0\n", t)
        self.assertNotIn("rotation", t)
        self.assertNotIn("hand", t)
        t = pp.touch_pose_text(pp.touch_pose(None, None, 90, "right"))
        self.assertIn("rotation 270\n", t)
        self.assertIn("hand right\n", t)
        self.assertNotIn("display", t)

    def test_changed(self):
        a = pp.touch_pose("both", 180.0, 0, None)
        self.assertTrue(pp.touch_pose_changed(None, a))
        self.assertFalse(pp.touch_pose_changed(None, pp.touch_pose(None, None, None, None)))
        self.assertFalse(pp.touch_pose_changed(a, pp.touch_pose("both", 180.6, 0, None)))
        self.assertTrue(pp.touch_pose_changed(a, pp.touch_pose("both", 181.0, 0, None)))
        self.assertTrue(pp.touch_pose_changed(a, pp.touch_pose("left", 180.0, 0, None)))
        self.assertTrue(pp.touch_pose_changed(a, pp.touch_pose("both", 180.0, 90, None)))
        # a field going unknown (the screen went dark) is not a change
        self.assertFalse(pp.touch_pose_changed(a, pp.touch_pose(None, 180.0, 0, None)))


class PanelOverride(unittest.TestCase):
    def test_the_users_panel_wins_but_closed_still_blanks(self):
        outs = {"left": "DSI-1", "right": "DSI-2"}
        self.assertEqual(pp.panel_override("dual", "both", outs), "dual")
        self.assertEqual(pp.panel_override("dual", "left", outs), "single DSI-1")
        self.assertEqual(pp.panel_override("single DSI-1", "right", outs), "single DSI-2")
        self.assertEqual(pp.panel_override("blank", "left", outs), "blank")
        self.assertIsNone(pp.panel_override(None, "left", outs))
        self.assertEqual(pp.panel_override("dual", "sideways", outs), "dual")


class StreamState(unittest.TestCase):
    def test_only_the_snapshot_is_the_state(self):
        snap = {"pose": "dual", "lit": ["DSI-1"]}
        self.assertEqual(
            pp.stream_state({"id": 1, "ok": {"subscribed": True, "state": snap}}), snap
        )
        self.assertEqual(
            pp.stream_state({"event": "state", "changed": ["power"], "state": snap}), snap
        )
        # another event with a "state" field of its own (pressing a volume key)
        self.assertIsNone(
            pp.stream_state(
                {"event": "volume_key", "key": "up", "state": "pressed", "output": "DSI-2"}
            )
        )
        self.assertIsNone(pp.stream_state({"event": "state", "state": "odd"}))
        self.assertIsNone(pp.stream_state({"id": 2, "ok": "fine"}))
        self.assertIsNone(pp.stream_state({}))
        self.assertIsNone(pp.stream_state(["not", "a", "dict"]))


class FreshSession(unittest.TestCase):
    def test_once_per_reconnect(self):
        seen = 0
        seen, fresh = pp.fresh_session(seen, 0)
        self.assertFalse(fresh)
        seen, fresh = pp.fresh_session(seen, 1)  # first connect
        self.assertTrue(fresh)
        for _ in range(5):  # ticks while connected
            seen, fresh = pp.fresh_session(seen, 1)
            self.assertFalse(fresh)
        seen, fresh = pp.fresh_session(seen, 2)  # duo-wc restarted
        self.assertTrue(fresh)
        seen, fresh = pp.fresh_session(seen, 2)
        self.assertFalse(fresh)


if __name__ == "__main__":
    unittest.main()
