"""Unit tests for duo2-callaudio's pure parts (python3 -m unittest discover -s
tests, in telephony/). ModemManager's GI is replaced by a stand-in before the
script is loaded (only Mm.CallState is used at module level)."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_callaudio():
    import gi

    real = gi.require_version

    def require_version(name, version):
        if name != "ModemManager":
            real(name, version)

    gi.require_version = require_version
    mm = types.ModuleType("ModemManager")
    mm.CallState = types.SimpleNamespace(
        DIALING=1, RINGING_OUT=2, RINGING_IN=3, ACTIVE=4, HELD=5, WAITING=6, TERMINATED=7
    )
    sys.modules["gi.repository.ModemManager"] = mm
    import gi.repository

    gi.repository.ModemManager = mm
    path = os.path.join(os.path.dirname(HERE), "duo2-callaudio")
    loader = importlib.machinery.SourceFileLoader("duo2_callaudio", path)
    spec = importlib.util.spec_from_loader("duo2_callaudio", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ca = load_callaudio()


class Gain(unittest.TestCase):
    def test_full_volume_is_the_cap(self):
        self.assertEqual(ca.gain_for(1.0, False, 52, 40), 52)

    def test_above_full_is_still_the_cap(self):
        self.assertEqual(ca.gain_for(1.5, False, 75, 40), 75)

    def test_steps_are_range_over_hundred_db(self):
        # 5 % (one volume key step) = 2 dB with a 40 dB range
        self.assertEqual(ca.gain_for(0.95, False, 52, 40), 50)
        self.assertEqual(ca.gain_for(0.30, False, 52, 40), 24)
        self.assertEqual(ca.gain_for(0.50, False, 75, 40), 55)

    def test_silent(self):
        self.assertEqual(ca.gain_for(0.0, False, 52, 40), 0)
        self.assertEqual(ca.gain_for(0.8, True, 52, 40), 0)

    def test_never_below_one_while_audible(self):
        self.assertEqual(ca.gain_for(0.01, False, 20, 40), 1)

    def test_never_above_the_cap(self):
        for v in range(0, 101):
            g = ca.gain_for(v / 100, False, 52, 40)
            self.assertLessEqual(g, 52)
            self.assertGreaterEqual(g, 0)


class Conf(unittest.TestCase):
    def test_defaults_and_overrides(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write('# comment\nEARPIECE_MAX=48\nVOICE_PCMS="3"\nUNKNOWN=1\n')
        try:
            c = ca.read_conf(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c["EARPIECE_MAX"], "48")
        self.assertEqual(c["VOICE_PCMS"], "3")
        self.assertEqual(c["SPEAKER_MAX"], ca.DEFAULTS["SPEAKER_MAX"])
        self.assertNotIn("UNKNOWN", c)

    def test_missing_file(self):
        self.assertEqual(ca.read_conf("/nonexistent/callaudio.conf"), ca.DEFAULTS)

    def test_active_states(self):
        self.assertIn(ca.Mm.CallState.ACTIVE, ca.ACTIVE_STATES)
        self.assertNotIn(ca.Mm.CallState.RINGING_IN, ca.ACTIVE_STATES)
        self.assertNotIn(ca.Mm.CallState.HELD, ca.ACTIVE_STATES)


class FakeCall:
    def __init__(self, state):
        self.state = state

    def get_state(self):
        return self.state


class Reopen(unittest.TestCase):
    """The voice path opened while dialing is opened anew once the call is
    connected; one opened when it already was is left alone."""

    def setUp(self):
        self.events = []
        self.app = ca.CallAudio(dict(ca.DEFAULTS))
        self.app.alsa = types.SimpleNamespace(
            open_voice=lambda name: self.events.append(("open", name)) or name,
            close=lambda h: self.events.append(("close", h)),
        )
        self.app.mode = ca.MODE_CALL
        self.call = FakeCall(ca.Mm.CallState.DIALING)
        self.app.calls = {"/c/0": (self.call, 0, "/m/0")}
        self.saved = (ca.card_state, ca.amixer, self.app.apply, self.app.make_default)
        ca.card_state = lambda card: {
            "profile": "Voice Call (Earpiece, Mic)",
            "sink": None,
            "source": None,
        }
        ca.amixer = lambda *a: None
        self.app.apply = lambda sink, source: None
        self.app.make_default = lambda st: None

    def tearDown(self):
        ca.card_state, ca.amixer, self.app.apply, self.app.make_default = self.saved

    def opens(self):
        return sum(1 for e in self.events if e[0] == "open")

    def test_dialing_then_connected_reopens_once(self):
        self.app.update()
        self.assertEqual(self.opens(), 2)  # VoiceMMode1 + VoiceMMode2
        self.call.state = ca.Mm.CallState.ACTIVE
        self.app.update()
        self.assertEqual(self.opens(), 4)
        self.app.update()  # nothing new: no more reopens
        self.assertEqual(self.opens(), 4)

    def test_opened_when_connected_stays(self):
        self.call.state = ca.Mm.CallState.ACTIVE  # an answered incoming call
        self.app.update()
        self.app.update()
        self.assertEqual(self.opens(), 2)
        self.assertFalse(any(e[0] == "close" for e in self.events))


if __name__ == "__main__":
    unittest.main()
