import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import emergencymodel as em  # noqa: E402
from duoshell import lockpad  # noqa: E402

DE = {"sim_present": True, "network_mcc": "262", "sim_mcc": "262", "sim_numbers": ()}
HU = {"sim_present": True, "network_mcc": "216", "sim_mcc": "216", "sim_numbers": ()}


class Numbers(unittest.TestCase):
    def test_112_first_everywhere(self):
        for ctx in ({}, DE, HU, {"sim_present": False}, dict(DE, emergency_only=True)):
            self.assertEqual(em.numbers(ctx)[0], ("112", em.EMERGENCY))
            self.assertTrue(em.dialable("112", ctx))
            self.assertTrue(em.dialable("911", ctx))

    def test_national_numbers(self):
        self.assertTrue(em.dialable("110", DE))
        self.assertFalse(em.dialable("104", DE))
        for n in ("104", "105", "107"):
            self.assertTrue(em.dialable(n, HU), n)
        # roaming: the network's country counts, the SIM's too
        self.assertTrue(em.dialable("133", dict(DE, network_mcc="232")))
        self.assertTrue(em.dialable("110", dict(DE, network_mcc="232")))

    def test_not_registered_only_modemmanagers_list(self):
        # ModemManager lets 112, 911 (+ the SIM's list) through unregistered
        locked = dict(DE, emergency_only=True)
        self.assertFalse(em.dialable("110", locked))
        self.assertTrue(em.dialable("112", locked))
        # ... and without a SIM its no-SIM list (110 is in it)
        nosim = {"sim_present": False, "network_mcc": "262", "emergency_only": True}
        for n in ("000", "08", "110", "999", "118", "119"):
            self.assertTrue(em.dialable(n, nosim), n)
        self.assertFalse(em.dialable("999", DE))

    def test_never_an_ordinary_number(self):
        for n in ("", "0301234567", "+49112", "*112#", "11", "1122", "06301234567", "112;", "3311"):
            self.assertFalse(em.dialable(n, DE), n)
        self.assertEqual(em.normalize("1 1-2"), "112")
        self.assertEqual(em.normalize("+112"), "")

    def test_suggestions(self):
        self.assertEqual([n for n, _w in em.suggestions(DE)], ["112", "110"])
        self.assertEqual([n for n, _w in em.suggestions(HU)], ["112", "104", "105", "107"])
        self.assertEqual([n for n, _w in em.suggestions(dict(HU, emergency_only=True))], ["112"])
        self.assertEqual([n for n, _w in em.suggestions({})], ["112"])


class Keypad(unittest.TestCase):
    def test_entry(self):
        e = em.Entry(em.offer(DE)["numbers"])
        self.assertIsNone(e.press("call"))  # nothing typed
        for k in "110":
            e.press(k)
        self.assertEqual(e.press("call"), ("call", "110"))
        e.reset()
        for k in "3311":
            e.press(k)
        self.assertIsNone(e.press("call"))
        self.assertEqual(e.message, "not-emergency")
        e.press("back")
        self.assertEqual(e.digits, "331")
        self.assertEqual(e.message, "")
        for _ in range(10):
            e.press("9")
        self.assertEqual(len(e.digits), em.MAX_DIGITS)

    def test_a_suggestion_needs_the_call_key(self):
        e = em.Entry(em.offer(DE)["numbers"])
        e.choose("112")
        self.assertEqual(e.digits, "112")
        self.assertEqual(e.press("call"), ("call", "112"))

    def test_layout_fits(self):
        for w, h in ((538, 757), (757, 538)):
            lay = em.layout(w, h, 4)
            for key, cx, cy, r in lay["keys"]:
                self.assertTrue(0 <= cx - r and cx + r <= w, (w, key))
                self.assertTrue(cy + r <= h, (w, key))
                self.assertEqual(em.key_at(lay["keys"], cx, cy), key)
            top = min(cy - r for _k, _x, cy, r in lay["keys"])
            for _i, cx, cy, pw, ph in lay["pills"]:
                self.assertTrue(0 <= cx - pw / 2 and cx + pw / 2 <= w)
                self.assertLess(cy + ph / 2, top)
                self.assertGreater(cy - ph / 2, lay["number_y"])
            self.assertEqual([k for k, *_r in lay["keys"]][-1], "call")

    def test_the_lock_button_clears_the_pin_keypad(self):
        for w, h in ((538, 757), (757, 538)):
            keys = lockpad.keypad(w, h, top=h * 0.36)
            cx, cy, bw, bh = em.button(w, h, pin_lock=True)
            self.assertTrue(0 <= cx - bw / 2 and cx + bw / 2 <= w and cy + bh / 2 <= h)
            for key, kx, ky, r in keys:
                # no key's circle reaches into the pill
                nx = min(max(kx, cx - bw / 2), cx + bw / 2)
                ny = min(max(ky, cy - bh / 2), cy + bh / 2)
                self.assertGreater((kx - nx) ** 2 + (ky - ny) ** 2, r * r, (w, key))
            cx, cy, bw, bh = em.button(w, h, pin_lock=False)
            self.assertLess(cy + bh / 2, h * 0.88 - 16)  # above the swipe hint


class Wire(unittest.TestCase):
    def test_request(self):
        line = em.encode_request("112").decode()
        self.assertEqual(em.parse_request(line, DE), "112")
        self.assertIsNone(em.parse_request(em.encode_request("3311").decode(), DE))
        self.assertIsNone(em.parse_request('{"do":"emergency","number":3}', DE))
        self.assertIsNone(em.parse_request('{"do":"accept","path":"/x"}', DE))
        self.assertIsNone(em.parse_request("garbage", DE))

    def test_offer(self):
        import json

        line = json.dumps({"card": None, "speaker": False, "emergency": em.offer(HU)})
        got = em.parse_offer(line)
        self.assertEqual(got["numbers"][:4], ["112", "104", "105", "107"])
        self.assertEqual(got["suggestions"][0], ("112", em.EMERGENCY))
        self.assertIsNone(em.parse_offer('{"card": null}'))
        # a suggestion that is not among the numbers is dropped
        bad = json.dumps({"emergency": {"numbers": ["112"], "suggestions": [["3311", "Mailbox"]]}})
        self.assertEqual(em.parse_offer(bad)["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
