import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import callsmodel as m  # noqa: E402

P1 = "/org/gnome/Calls/Call/1"
P2 = "/org/gnome/Calls/Call/2"


def call(state, inbound=True, number="", name="", ui_active=True):
    return m.call_from_props(
        {
            "State": state,
            "Inbound": inbound,
            "Id": number,
            "DisplayName": name,
            "Hints": {"ui-active": ui_active},
        }
    )


class Calls(unittest.TestCase):
    def test_props_update_what_was_known(self):
        c = call(m.INCOMING, number="0000", name="Anna")
        c2 = m.call_from_props({"State": m.ACTIVE}, c)
        self.assertEqual(
            (c2["state"], c2["name"], c2["id"], c2["inbound"]), (m.ACTIVE, "Anna", "0000", True)
        )
        self.assertEqual(m.call_from_props({"State": "x"})["state"], m.UNKNOWN)
        # no Hints at all: shown at once
        self.assertTrue(m.call_from_props({"State": m.INCOMING, "Inbound": True})["ui_active"])

    def test_ringing_is_inbound_incoming_and_shown(self):
        calls = {P1: call(m.INCOMING), P2: call(m.INCOMING, inbound=False)}
        self.assertEqual(m.ringing(calls), [P1])
        # Calls holds the UI back for 100 ms (its blocking rules)
        self.assertEqual(m.ringing({P1: call(m.INCOMING, ui_active=False)}), [])
        self.assertEqual(m.ringing({P1: call(m.ACTIVE)}), [])

    def test_caller(self):
        self.assertEqual(
            m.caller(call(m.INCOMING, number="0000", name="  Anna  Nagy ")), "Anna Nagy"
        )
        self.assertEqual(m.caller(call(m.INCOMING, number="0000")), "0000")
        self.assertEqual(m.caller(call(m.INCOMING)), m.UNKNOWN_CALLER)
        self.assertEqual(len(m.caller(call(m.INCOMING, name="x" * 200))), m.MAX_CALLER)


class Strips(unittest.TestCase):
    def test_who_calls_while_it_rings(self):
        self.assertEqual(m.strip_caller({P1: call(m.INCOMING, number="0000", name="Anna")}), "Anna")
        self.assertEqual(m.strip_caller({P1: call(m.INCOMING, number="0000")}), "0000")
        self.assertEqual(m.strip_caller({P1: call(m.INCOMING)}), m.UNKNOWN_CALLER)
        # answered (here or elsewhere), declined or missed: the clock again
        self.assertIsNone(m.strip_caller({P1: call(m.ACTIVE, name="Anna")}))
        self.assertIsNone(m.strip_caller({P1: call(m.DISCONNECTED, name="Anna")}))
        self.assertIsNone(m.strip_caller({}))
        # an outgoing call is not "who calls"
        self.assertIsNone(m.strip_caller({P1: call(m.DIALING, inbound=False, name="Anna")}))
        # not shown by Calls yet (its blocking rules): nothing either
        self.assertIsNone(m.strip_caller({P1: call(m.INCOMING, name="Anna", ui_active=False)}))


class Silence(unittest.TestCase):
    def test_a_ringing_call_is_loud_until_silenced(self):
        calls = {P1: call(m.INCOMING, name="Anna")}
        silenced = set()
        self.assertEqual(m.ring_state(calls, silenced), "loud")
        self.assertEqual(m.loud(calls, silenced), [P1])
        silenced.add(P1)
        # still ringing (silently): the strips keep the caller, the keys are back
        self.assertEqual(m.ring_state(calls, silenced), "silent")
        self.assertEqual(m.loud(calls, silenced), [])
        self.assertEqual(m.strip_caller(calls), "Anna")

    def test_no_ring_without_a_ringing_call(self):
        self.assertIsNone(m.ring_state({}, set()))
        self.assertIsNone(m.ring_state({P1: call(m.ACTIVE)}, set()))
        self.assertIsNone(m.ring_state({P1: call(m.INCOMING, inbound=False)}, set()))

    def test_a_call_waiting_beside_one_that_is_up_keeps_the_keys(self):
        calls = {P1: call(m.ACTIVE, name="Bob"), P2: call(m.INCOMING, name="Anna")}
        self.assertEqual(m.loud(calls, set()), [])
        self.assertEqual(m.ring_state(calls, set()), "silent")

    def test_silenced_calls_are_forgotten_once_they_stop_ringing(self):
        silenced = {P1, P2}
        calls = {P1: call(m.INCOMING), P2: call(m.ACTIVE)}
        m.forget_silenced(calls, silenced)
        self.assertEqual(silenced, {P1})
        m.forget_silenced({}, silenced)
        self.assertEqual(silenced, set())

    def test_a_second_call_rings_aloud_again(self):
        calls = {P1: call(m.INCOMING), P2: call(m.INCOMING)}
        self.assertEqual(m.loud(calls, {P1}), [P2])
        self.assertEqual(m.ring_state(calls, {P1}), "loud")


class Keys(unittest.TestCase):
    def test_a_volume_key_silences_a_loud_ring_and_keeps_its_release(self):
        keys = m.RingKeys()
        self.assertTrue(keys.takes("up", True, True))
        # silenced now: the release is still the ring's, not the volume's
        self.assertTrue(keys.takes("up", False, False))
        # the next press is the volume's again
        self.assertFalse(keys.takes("up", True, False))
        self.assertFalse(keys.takes("up", False, False))

    def test_without_a_loud_ring_the_keys_are_the_volumes(self):
        keys = m.RingKeys()
        self.assertFalse(keys.takes("down", True, False))
        self.assertFalse(keys.takes("down", False, True))
        # a release of a key pressed before the ring: the volume's
        self.assertFalse(keys.takes("mute", False, True))


class Card(unittest.TestCase):
    def test_incoming_first(self):
        calls = {P1: call(m.ACTIVE, name="Bob"), P2: call(m.INCOMING, name="Anna")}
        self.assertEqual(m.card(calls, {}), {"kind": "incoming", "path": P2, "caller": "Anna"})

    def test_a_call_that_is_up_with_its_time(self):
        since = m.note_since({P1: call(m.ACTIVE, name="Bob")}, {}, 100.0)
        # seen again later: the time it came up stays
        since = m.note_since({P1: call(m.ACTIVE, name="Bob")}, since, 130.0)
        self.assertEqual(
            m.card({P1: call(m.ACTIVE, name="Bob")}, since),
            {"kind": "active", "path": P1, "caller": "Bob", "state": "active", "since": 100.0},
        )
        # gone: forgotten
        self.assertEqual(m.note_since({}, since, 140.0), {})

    def test_dialling_and_held(self):
        self.assertEqual(m.card({P1: call(m.DIALING, inbound=False)}, {})["state"], "dialing")
        self.assertEqual(m.card({P1: call(m.HELD)}, {})["state"], "held")
        # the one that is up wins over a held one
        self.assertEqual(m.card({P1: call(m.HELD), P2: call(m.ACTIVE)}, {})["path"], P2)

    def test_nothing(self):
        self.assertIsNone(m.card({}, {}))
        self.assertIsNone(m.card({P1: call(m.DISCONNECTED)}, {}))


class Wire(unittest.TestCase):
    def test_card_round_trip(self):
        card = {"kind": "active", "path": P1, "caller": "Bob", "state": "active", "since": 100.0}
        line = m.encode({"card": card, "speaker": True}).decode()
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(m.parse_card(line), (card, True))
        self.assertEqual(m.parse_card(json.dumps({"card": None})), (None, False))

    def test_bad_cards_change_nothing(self):
        for bad in (
            "",
            "{",
            "[]",
            json.dumps({"x": 1}),
            json.dumps({"card": {"kind": "boom", "path": P1}}),
            json.dumps({"card": {"kind": "incoming", "path": "/elsewhere"}}),
            json.dumps({"card": {"kind": "incoming"}}),
        ):
            self.assertIsNone(m.parse_card(bad), bad)

    def test_a_card_is_cleaned(self):
        card, _sp = m.parse_card(
            json.dumps(
                {
                    "card": {
                        "kind": "active",
                        "path": P1,
                        "caller": 5,
                        "state": "evil",
                        "since": True,
                    }
                }
            )
        )
        self.assertEqual(
            card,
            {
                "kind": "active",
                "path": P1,
                "caller": m.UNKNOWN_CALLER,
                "state": "active",
                "since": None,
            },
        )

    def test_actions(self):
        known = {P1}
        self.assertEqual(
            m.parse_action('{"do": "accept", "path": "%s"}' % P1, known), ("accept", P1, False)
        )
        self.assertEqual(
            m.parse_action('{"do": "speaker", "path": "%s", "on": true}' % P1, known),
            ("speaker", P1, True),
        )
        self.assertIsNone(m.parse_action('{"do": "accept", "path": "%s"}' % P2, known))
        self.assertIsNone(m.parse_action('{"do": "dial", "path": "%s"}' % P1, known))
        self.assertIsNone(m.parse_action("nope", known))


class Layout(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(m.duration(7), "0:07")
        self.assertEqual(m.duration(754), "12:34")
        self.assertEqual(m.duration(3723), "1:02:03")
        self.assertEqual(m.duration(-3), "0:00")

    def test_buttons_fit_the_panel_and_do_not_overlap(self):
        for w, h in ((538, 757), (757, 538)):
            for kind, pin in (
                ("incoming", False),
                ("incoming", True),
                ("active", False),
                ("active", True),
            ):
                lay = m.card_layout(kind, w, h, pin)
                names = [b[0] for b in lay["buttons"]]
                self.assertEqual(
                    names[:2],
                    ["decline", "accept"] if kind == "incoming" else ["speaker", "hangup"],
                )
                self.assertEqual("pin" in names, kind == "active" and pin)
                for _n, cx, cy, r in lay["buttons"]:
                    self.assertTrue(r <= cx <= w - r and r <= cy <= h - r, (w, h, kind))
                    self.assertGreater(cy - r, lay["status_y"])
                (_a, x1, y1, r1), (_b, x2, y2, r2) = lay["buttons"][:2]
                self.assertGreater(abs(x2 - x1), r1 + r2 + 40)

    def test_button_at(self):
        lay = m.card_layout("incoming", 538, 757, False)
        _n, cx, cy, r = lay["buttons"][1]
        self.assertEqual(m.button_at(lay["buttons"], cx + r, cy), "accept")
        self.assertIsNone(m.button_at(lay["buttons"], 269, 50))

    def test_call_line_sits_between_the_dots_and_the_keypad(self):
        from duoshell import lockpad

        for w, h in ((538, 757), (757, 538)):
            x, y, bw, bh = m.call_line_box(w, h)
            self.assertGreater(y, h * 0.27)
            keys = lockpad.keypad(w, h, top=h * 0.36)
            top_of_keys = min(cy - r for _k, _cx, cy, r in keys)
            self.assertLess(y + bh, top_of_keys)
            self.assertTrue(0 <= x and x + bw <= w)


if __name__ == "__main__":
    unittest.main()
