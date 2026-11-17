import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import audioroutemodel as m  # noqa: E402

SPEAKER = {
    "name": "alsa_output.platform-sound.HiFi__Speaker__sink",
    "properties": {"device.api": "alsa", "device.bus": None, "media.class": "Audio/Sink"},
}
CALL = {
    "name": "alsa_output.platform-sound.Voice_Call__Earpiece__sink",
    "properties": {"device.api": "alsa"},
}
# made-up addresses
HEADSET = {
    "name": "bluez_output.02_00_00_00_00_01.1",
    "properties": {"device.api": "bluez5", "device.bus": "bluetooth"},
}
BUDS = {
    "name": "bluez_output.02_00_00_00_00_02.1",
    "properties": {"device.api": "bluez5"},
}
DAC = {
    "name": "alsa_output.usb-Made_Up_DAC-00.analog-stereo",
    "properties": {"device.api": "alsa", "device.bus": "usb"},
}
FILTER = {"name": "effect_input.eq", "properties": {"node.virtual": "true"}}


class Kinds(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(m.kind(HEADSET), "bluetooth")
        self.assertEqual(m.kind(BUDS), "bluetooth")
        self.assertEqual(m.kind(DAC), "usb")
        for s in (SPEAKER, CALL, FILTER, {}, None, {"properties": None}):
            self.assertIsNone(m.kind(s))

    def test_in_call(self):
        self.assertTrue(m.in_call(CALL["name"]))
        self.assertFalse(m.in_call(SPEAKER["name"]))
        self.assertFalse(m.in_call(None))


class Routing(unittest.TestCase):
    def test_a_headset_that_connects_becomes_the_output(self):
        r = m.Router()
        self.assertIsNone(r.update([SPEAKER], SPEAKER["name"]))  # the first look
        self.assertEqual(
            r.update([SPEAKER, HEADSET], SPEAKER["name"]), (HEADSET["name"], "bluetooth")
        )
        # once: the next look (its own default change) does nothing
        self.assertIsNone(r.update([SPEAKER, HEADSET], HEADSET["name"]))
        # it goes: WirePlumber falls back to the speaker by itself
        self.assertIsNone(r.update([SPEAKER], SPEAKER["name"]))
        # it comes back: the output again
        self.assertEqual(
            r.update([SPEAKER, HEADSET], SPEAKER["name"]), (HEADSET["name"], "bluetooth")
        )

    def test_what_is_there_at_the_start_stays(self):
        r = m.Router()
        self.assertIsNone(r.update([SPEAKER, HEADSET], SPEAKER["name"]))
        self.assertIsNone(r.update([SPEAKER, HEADSET], SPEAKER["name"]))

    def test_the_users_choice_stands_until_the_next_arrival(self):
        r = m.Router()
        r.update([SPEAKER], SPEAKER["name"])
        r.update([SPEAKER, HEADSET], SPEAKER["name"])
        # the user picks the speaker in Settings > Sound
        self.assertIsNone(r.update([SPEAKER, HEADSET], SPEAKER["name"]))
        self.assertEqual(r.update([SPEAKER, HEADSET, DAC], SPEAKER["name"]), (DAC["name"], "usb"))

    def test_not_virtual_or_the_phones_own(self):
        r = m.Router()
        r.update([SPEAKER], SPEAKER["name"])
        self.assertIsNone(r.update([SPEAKER, FILTER], SPEAKER["name"]))

    def test_newest_of_two_arrivals(self):
        r = m.Router()
        r.update([SPEAKER], SPEAKER["name"])
        self.assertEqual(r.update([SPEAKER, HEADSET, BUDS], SPEAKER["name"])[0], BUDS["name"])

    def test_already_the_default(self):
        r = m.Router()
        r.update([SPEAKER], SPEAKER["name"])
        self.assertIsNone(r.update([SPEAKER, HEADSET], HEADSET["name"]))

    def test_during_a_call_it_waits(self):
        r = m.Router()
        r.update([CALL], CALL["name"])
        self.assertIsNone(r.update([CALL, HEADSET], CALL["name"]))  # the call keeps its sink
        self.assertIsNone(r.update([CALL, HEADSET], CALL["name"]))
        # the call ends (the HiFi profile again): now
        self.assertEqual(
            r.update([SPEAKER, HEADSET], SPEAKER["name"]), (HEADSET["name"], "bluetooth")
        )

    def test_gone_before_the_call_ended(self):
        r = m.Router()
        r.update([CALL], CALL["name"])
        r.update([CALL, HEADSET], CALL["name"])
        r.update([CALL], CALL["name"])
        self.assertIsNone(r.update([SPEAKER], SPEAKER["name"]))

    def test_bad_input(self):
        r = m.Router()
        self.assertIsNone(r.update(None, None))
        self.assertIsNone(r.update([None, "x", {"name": None}], ""))
        self.assertEqual(r.update([HEADSET, 3], ""), (HEADSET["name"], "bluetooth"))


if __name__ == "__main__":
    unittest.main()
