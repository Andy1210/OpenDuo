import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import proximitymodel as m  # noqa: E402


def win(app_id, placement, minimized=False):
    return {"app_id": app_id, "placement": placement, "minimized": minimized}


CALLS_ON_1 = [
    win("org.gnome.Settings", {"output": "DSI-2"}),
    win("org.gnome.Calls", {"output": "DSI-1"}),
]


class CallWantsSensor(unittest.TestCase):
    def test_outgoing_and_active_calls_on_the_earpiece(self):
        for state in (m.CALL_DIALING, m.CALL_RINGING_OUT, m.CALL_ACTIVE):
            self.assertTrue(m.call_wants_sensor([state], speaker=False), state)
            # callaudiod not there: the earpiece is the default route
            self.assertTrue(m.call_wants_sensor([state], speaker=None), state)

    def test_not_on_the_speaker(self):
        self.assertFalse(m.call_wants_sensor([m.CALL_ACTIVE], speaker=True))

    def test_not_while_ringing_in_held_or_over(self):
        for state in (
            m.CALL_RINGING_IN,
            m.CALL_HELD,
            m.CALL_WAITING,
            m.CALL_TERMINATED,
            m.CALL_UNKNOWN,
        ):
            self.assertFalse(m.call_wants_sensor([state], speaker=False), state)
        self.assertFalse(m.call_wants_sensor([], speaker=False))

    def test_any_call_that_is_up_counts(self):
        # a second call ringing in while one is up: the one up is at the ear
        self.assertTrue(m.call_wants_sensor([m.CALL_RINGING_IN, m.CALL_ACTIVE], speaker=False))


class Panels(unittest.TestCase):
    def test_the_panel_of_the_calls_window(self):
        self.assertEqual(m.hold_panels(True, True, CALLS_ON_1, ["DSI-1", "DSI-2"], []), ["DSI-1"])

    def test_nothing_while_far_or_unwanted(self):
        self.assertEqual(m.hold_panels(True, False, CALLS_ON_1, ["DSI-1", "DSI-2"], []), [])
        self.assertEqual(m.hold_panels(False, True, CALLS_ON_1, ["DSI-1", "DSI-2"], []), [])

    def test_held_panels_stay_candidates(self):
        # held dark already: not in "lit" any more, still the call's panel
        self.assertEqual(m.hold_panels(True, True, CALLS_ON_1, ["DSI-2"], ["DSI-1"]), ["DSI-1"])

    def test_a_spanned_calls_window_covers_both(self):
        self.assertEqual(
            m.hold_panels(True, True, [win("org.gnome.Calls", "span")], ["DSI-1", "DSI-2"], []),
            ["DSI-1", "DSI-2"],
        )

    def test_no_calls_window_means_every_lit_panel(self):
        others = [win("org.gnome.Settings", {"output": "DSI-2"})]
        self.assertEqual(
            m.hold_panels(True, True, others, ["DSI-2", "DSI-1"], []), ["DSI-1", "DSI-2"]
        )
        minimized = [win("org.gnome.Calls", {"output": "DSI-1"}, minimized=True)]
        self.assertEqual(m.hold_panels(True, True, minimized, ["DSI-2"], []), ["DSI-2"])

    def test_a_calls_window_on_a_dark_panel_is_not_the_one(self):
        # single pose: DSI-2 off, the Calls window placed there (folded away)
        self.assertEqual(
            m.hold_panels(True, True, [win("org.gnome.Calls", {"output": "DSI-2"})], ["DSI-1"], []),
            ["DSI-1"],
        )

    def test_screen_off_holds_nothing_new(self):
        self.assertEqual(m.hold_panels(True, True, CALLS_ON_1, [], []), [])


class DebounceTest(unittest.TestCase):
    def test_near_at_once_far_after_the_delay(self):
        d = m.Debounce(far_delay=0.25)
        self.assertIsNone(d.feed(True, 10.0))
        self.assertTrue(d.near)
        wait = d.feed(False, 11.0)
        self.assertAlmostEqual(wait, 0.25)
        self.assertTrue(d.near)
        self.assertAlmostEqual(d.settle(11.1), 0.15)
        self.assertTrue(d.near)
        self.assertIsNone(d.settle(11.25))
        self.assertFalse(d.near)

    def test_a_flicker_keeps_it_near(self):
        d = m.Debounce(far_delay=0.25)
        d.feed(True, 0.0)
        d.feed(False, 1.0)
        d.feed(True, 1.1)
        self.assertIsNone(d.settle(1.5))
        self.assertTrue(d.near)

    def test_far_from_the_start_is_far(self):
        d = m.Debounce()
        self.assertIsNone(d.feed(False, 0.0))
        self.assertFalse(d.near)

    def test_reset(self):
        d = m.Debounce()
        d.feed(True, 0.0)
        d.reset()
        self.assertFalse(d.near)


if __name__ == "__main__":
    unittest.main()


class SensorRecheck(unittest.TestCase):
    def test_a_call_asks_again_once_when_the_answer_was_no(self):
        self.assertTrue(m.sensor_recheck(True, has_sensor=False, asked=False))
        self.assertFalse(m.sensor_recheck(True, has_sensor=False, asked=True))

    def test_not_when_the_sensor_is_known_or_no_call_wants_it(self):
        self.assertFalse(m.sensor_recheck(True, has_sensor=True, asked=False))
        self.assertFalse(m.sensor_recheck(False, has_sensor=False, asked=False))


class Describe(unittest.TestCase):
    def test_the_inputs_of_the_decision(self):
        self.assertEqual(
            m.describe([m.CALL_ACTIVE], False, True), "calls 4, route earpiece, sensor yes"
        )
        self.assertEqual(
            m.describe([m.CALL_RINGING_IN, m.CALL_ACTIVE], None, False),
            "calls 3,4, route unknown, sensor no",
        )
        self.assertEqual(m.describe([], True, True), "calls none, route loudspeaker, sensor yes")
