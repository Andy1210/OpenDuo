import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import mobilemodel as mm  # noqa: E402

LOCKED_PIN = {
    "present": True,
    "state": "locked",
    "lock": "sim-pin",
    "retries": {"sim-pin": 3, "sim-puk": 10},
}
LOCKED_PUK = {
    "present": True,
    "state": "locked",
    "lock": "sim-puk",
    "retries": {"sim-pin": 0, "sim-puk": 10},
}
ONLINE = {
    "present": True,
    "state": "connected",
    "lock": "none",
    "quality": 72,
    "operator": "Telekom.de",
    "sim_operator": "Example SIM",
    "code": "26201",
    "registration": "home",
    "tech": "lte",
    "data": "on",
    "connected": True,
}


class PinRequest(unittest.TestCase):
    def test_what_the_sim_waits_for(self):
        self.assertEqual(mm.pin_request(LOCKED_PIN), {"kind": "pin", "left": 3})
        self.assertEqual(mm.pin_request(LOCKED_PUK), {"kind": "puk", "left": 10})
        self.assertIsNone(mm.pin_request(ONLINE))
        self.assertIsNone(mm.pin_request({"present": False}))
        self.assertIsNone(mm.pin_request(None))
        # A network lock (not the SIM's) is not ours to ask for.
        self.assertIsNone(mm.pin_request(dict(LOCKED_PIN, lock="ph-net-pin")))
        self.assertEqual(mm.pin_request(dict(LOCKED_PIN, retries={}))["left"], None)


class Bar(unittest.TestCase):
    def test_signal_and_operator(self):
        b = mm.bar(ONLINE)
        self.assertEqual(b["text"], "Example SIM")
        self.assertEqual(b["icon"], "network-cellular-signal-good-symbolic")
        self.assertIn("4G", b["tip"])
        self.assertFalse(b["locked"])
        self.assertEqual(mm.bar(dict(ONLINE, sim_operator=""))["text"], "Telekom.de")
        self.assertEqual(mm.bar(dict(ONLINE, sim_operator="", operator=""))["text"], "26201")
        self.assertIn("roaming", mm.bar(dict(ONLINE, registration="roaming"))["tip"])
        self.assertEqual(
            mm.operator_label(dict(ONLINE, sim_operator="A Very Long Operator Name")),
            "A Very Long…",
        )

    def test_no_sim_locked_searching(self):
        self.assertIsNone(mm.bar({"present": False}))
        self.assertTrue(mm.bar(LOCKED_PIN)["locked"])
        self.assertEqual(mm.bar(LOCKED_PIN)["icon"], "auth-sim-locked-symbolic")
        self.assertEqual(mm.bar(dict(ONLINE, state="searching"))["text"], "")
        self.assertEqual(
            mm.bar(dict(ONLINE, state="disabled"))["icon"], "network-cellular-offline-symbolic"
        )

    def test_bars(self):
        self.assertEqual(
            [mm.signal_icon(q) for q in (None, 0, 10, 30, 55, 80, 100)],
            [mm.SIGNAL_ICONS[i] for i in (0, 0, 1, 2, 3, 4, 4)],
        )


class Airplane(unittest.TestCase):
    def test_bar_shows_the_airplane(self):
        for modem in (ONLINE, {"present": False}, None):
            b = mm.bar(modem, airplane=True)
            self.assertEqual(b["icon"], mm.AIRPLANE_ICON)
            self.assertEqual(b["text"], "")
            self.assertFalse(b["locked"])
        # A locked SIM still opens its PIN dialog from the icon.
        self.assertTrue(mm.bar(LOCKED_PIN, airplane=True)["locked"])
        self.assertIsNone(mm.bar({"present": False}, airplane=False))

    def test_tiles(self):
        self.assertEqual(mm.data_tile(ONLINE, airplane=True), (False, False, "Airplane mode"))
        self.assertEqual(
            mm.data_tile({"present": False}, airplane=True), (False, False, "Airplane mode")
        )
        self.assertEqual(mm.airplane_tile(True), (True, "On"))
        self.assertEqual(mm.airplane_tile(False), (False, "Off"))


class DataTile(unittest.TestCase):
    def test_states(self):
        self.assertEqual(mm.data_tile({"present": False}), (False, False, "No SIM"))
        self.assertEqual(mm.data_tile(LOCKED_PIN), (True, False, "SIM locked"))
        self.assertEqual(mm.data_tile(ONLINE), (True, True, "4G"))
        self.assertEqual(mm.data_tile(dict(ONLINE, data="off")), (True, False, "Off"))
        self.assertEqual(
            mm.data_tile(dict(ONLINE, connected=False, state="registered")), (True, True, "On")
        )
        self.assertEqual(
            mm.data_tile(dict(ONLINE, connected=False, state="searching"))[2],
            "Waiting for the network",
        )
        self.assertEqual(
            [mm.parse_switch(t) for t in ("on\n", " OFF", "", "maybe", None)],
            ["on", "off", None, None, None],
        )


class Entry(unittest.TestCase):
    def type(self, entry, digits):
        out = None
        for d in digits:
            out = entry.press(d)
        return out

    def test_pin(self):
        e = mm.PinEntry("pin")
        self.type(e, "12")
        self.assertIsNone(e.press("ok"))
        self.assertEqual(e.problem, "pin-length")
        self.type(e, "123456789")
        self.assertEqual(e.count, 8)  # no ninth digit
        e.press("back")
        self.assertEqual(e.count, 7)
        self.assertEqual(e.press("ok"), ("pin", "1234567"))
        self.assertEqual(e.count, 0)

    def test_puk_then_the_new_pin_twice(self):
        e = mm.PinEntry("puk")
        self.assertEqual(e.stage, "puk")
        self.type(e, "1234")
        self.assertIsNone(e.press("ok"))
        self.assertEqual(e.problem, "puk-length")
        self.type(e, "12345678")
        self.assertIsNone(e.press("ok"))
        self.assertEqual(e.stage, "new")
        self.type(e, "4321")
        self.assertIsNone(e.press("ok"))
        self.assertEqual(e.stage, "again")
        self.type(e, "4322")
        self.assertIsNone(e.press("ok"))
        self.assertEqual((e.stage, e.problem), ("new", "differ"))
        self.type(e, "4321")
        e.press("ok")
        self.type(e, "4321")
        self.assertEqual(e.press("ok"), ("puk", "12345678", "4321"))
        # Nothing of it is kept.
        self.assertEqual((e.puk, e.new, e.digits, e.stage), ("", "", "", "puk"))

    def test_other_keys_are_ignored(self):
        e = mm.PinEntry("pin")
        for k in ("a", "", None, "12", "#"):
            e.press(k)
        self.assertEqual(e.count, 0)


if __name__ == "__main__":
    unittest.main()
