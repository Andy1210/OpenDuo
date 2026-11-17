"""Unit tests for duo2-wwan's pure parts (python3 -m unittest discover -s tests,
in telephony/). The ModemManager GI is not needed: a stand-in is put in its
place before the script is loaded (only the module-level names are used)."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_wwan():
    import gi

    real = gi.require_version

    def require_version(name, version):
        if name != "ModemManager":
            real(name, version)

    gi.require_version = require_version
    sys.modules.setdefault("gi.repository.ModemManager", types.ModuleType("ModemManager"))
    import gi.repository

    gi.repository.ModemManager = sys.modules["gi.repository.ModemManager"]
    path = os.path.join(os.path.dirname(HERE), "duo2-wwan")
    loader = importlib.machinery.SourceFileLoader("duo2_wwan", path)
    spec = importlib.util.spec_from_loader("duo2_wwan", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    gi.require_version = real
    return mod


try:
    wwan = load_wwan()
except ImportError:  # no PyGObject at all
    wwan = None


@unittest.skipIf(wwan is None, "no PyGObject")
class Pure(unittest.TestCase):
    def test_link_mtu_is_the_smallest_given(self):
        self.assertEqual(wwan.link_mtu(1472, 1500), 1472)
        self.assertEqual(wwan.link_mtu(0, 1500), 1500)
        self.assertEqual(wwan.link_mtu(None, 1430), 1430)
        self.assertEqual(wwan.link_mtu(), 0)
        self.assertEqual(wwan.link_mtu(0, None), 0)

    def test_wifi_first(self):
        wifi = {"dst": "default", "gateway": "192.0.2.1", "dev": "wlan0", "metric": 1024}
        mobile = {"dst": "default", "dev": "qmapmux0.0", "metric": 4096}
        self.assertTrue(wwan.other_default([wifi, mobile], "qmapmux0.0"))
        self.assertFalse(wwan.other_default([mobile], "qmapmux0.0"))
        self.assertFalse(wwan.other_default([], "qmapmux0.0"))
        # Before the bearer has a device, any default route is WiFi's.
        self.assertTrue(wwan.other_default([wifi], None))
        self.assertFalse(wwan.other_default([{"dst": "default", "type": "unreachable"}], None))

    def test_radio_cycle_budget(self):
        self.assertIs(wwan.ps_cycle_due(1000.0, None, 0), True)
        # at most every 30 min: the seconds still to wait
        self.assertEqual(wwan.ps_cycle_due(1000.0 + 600, 1000.0, 1), 1201)
        self.assertIs(wwan.ps_cycle_due(1000.0 + 1800, 1000.0, 1), True)
        # three per modem appearance
        self.assertIs(wwan.ps_cycle_due(99999.0, 1000.0, 3), False)

    def test_switch_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "airplane")
            self.assertIsNone(wwan.read_switch(path))
            for text, want in (("on\n", True), ("off\n", False), ("OFF", False), ("maybe", None)):
                with open(path, "w") as f:
                    f.write(text)
                self.assertEqual(wwan.read_switch(path), want)

    def test_smart_5g_wanted(self):
        # NR only with data on, a panel lit and no WiFi default route
        self.assertTrue(wwan.nr_wanted(True, True, False))
        self.assertFalse(wwan.nr_wanted(True, False, False))
        self.assertFalse(wwan.nr_wanted(True, True, True))
        self.assertFalse(wwan.nr_wanted(False, True, False))
        # devices on the hotspot: data in use, dark panels and WiFi or not
        self.assertTrue(wwan.nr_wanted(True, False, False, sharing=True))
        self.assertTrue(wwan.nr_wanted(True, False, True, sharing=True))
        self.assertFalse(wwan.nr_wanted(False, False, False, sharing=True))

    def test_hotspot_state(self):
        self.assertTrue(wwan.hotspot_sharing({"state": "on", "use_5g": True, "client_count": 1}))
        self.assertFalse(wwan.hotspot_sharing({"state": "on", "use_5g": False}))
        self.assertFalse(wwan.hotspot_sharing({"state": "off", "use_5g": True}))
        self.assertFalse(wwan.hotspot_sharing({"state": "on", "use_5g": "yes"}))
        self.assertFalse(wwan.hotspot_sharing(None))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hotspot.json")
            self.assertFalse(wwan.read_hotspot(p))
            with open(p, "w") as f:
                f.write('{"state": "on", "use_5g": true}')
            self.assertTrue(wwan.read_hotspot(p))
            with open(p, "w") as f:
                f.write("{not json")
            self.assertFalse(wwan.read_hotspot(p))

    def test_smart_5g_mask(self):
        # 0x5c = GSM + UMTS + LTE + 5G NR (the Duo 2's), 0x1c without NR
        self.assertEqual(wwan.nr_mask(0x5C, True), 0x5C)
        self.assertEqual(wwan.nr_mask(0x5C, False), 0x1C)
        # NR switched off for good stays off
        self.assertEqual(wwan.nr_mask(0x1C, True), 0x1C)

    def test_smart_5g_base(self):
        # a fresh modem (or none saved): what it has is its own setting
        self.assertEqual(wwan.nr_base(0x5C, None), 0x5C)
        # a restart of duo2-wwan: the modem still has our change
        self.assertEqual(wwan.nr_base(0x1C, {"base": 0x5C, "applied": 0x1C}), 0x5C)
        # a restart of the modem: back to its own, whatever we had saved
        self.assertEqual(wwan.nr_base(0x5C, {"base": 0x5C, "applied": 0x1C}), 0x5C)
        self.assertEqual(wwan.nr_base(0x1C, {"base": "x", "applied": 0x1C}), 0x1C)

    def test_smart_5g_delays(self):
        self.assertEqual(wwan.nr_delay(True, True), wwan.NR_ALLOW_DELAY)
        self.assertEqual(wwan.nr_delay(False, True), wwan.NR_DENY_DELAY)
        self.assertEqual(wwan.nr_delay(False, False), wwan.NR_DATA_OFF_DELAY)
        self.assertLess(wwan.NR_ALLOW_DELAY, wwan.NR_DENY_DELAY)

    def test_emergency_numbers(self):
        # ModemManager's rule: 112 and 911 always
        for n in ("112", "911", "1 1 2", "112-"):
            self.assertTrue(wwan.is_emergency(n, True), n)
        # its no-SIM list only without a SIM
        self.assertTrue(wwan.is_emergency("999", False))
        self.assertFalse(wwan.is_emergency("999", True))
        self.assertTrue(wwan.is_emergency("110", False))
        # the SIM's own list, the national numbers by MCC
        self.assertTrue(wwan.is_emergency("19222", True, sim_numbers=("19222",)))
        self.assertTrue(wwan.is_emergency("110", True, mccs=("262",)))
        self.assertTrue(wwan.is_emergency("104", True, mccs=("216",)))
        self.assertFalse(wwan.is_emergency("104", True, mccs=("262",)))
        # never an ordinary number, an international one or a code
        for n in ("", "0301234567", "+49112", "*112#", "112p1", "11"):
            self.assertFalse(wwan.is_emergency(n, False), n)

    def test_emergency_radio_wanted(self):
        conf = dict(wwan.DEFAULTS)
        self.assertTrue(wwan.emergency_radio_wanted("failed", "sim-missing", False, conf))
        self.assertTrue(wwan.emergency_radio_wanted("locked", "", False, conf))
        # another failure (a broken modem) or a usable SIM: ModemManager's job
        self.assertFalse(wwan.emergency_radio_wanted("failed", "unknown", False, conf))
        self.assertFalse(wwan.emergency_radio_wanted("disabled", "", False, conf))
        # never in airplane mode, never against the configuration
        self.assertFalse(wwan.emergency_radio_wanted("locked", "", True, conf))
        self.assertFalse(
            wwan.emergency_radio_wanted("locked", "", False, dict(conf, EMERGENCY_RADIO="no"))
        )
        self.assertFalse(wwan.emergency_radio_wanted("locked", "", False, dict(conf, ENABLE="no")))

    def test_redial_rule(self):
        base = {
            "created": 100.0,
            "forced": True,
            "redialled": False,
            "states": {"dialing"},
            "ended": None,
        }
        self.assertEqual(wwan.redial_due(base, 101.0, True), "wait")  # still dialling
        failed = dict(base, ended="unknown")
        self.assertEqual(wwan.redial_due(failed, 105.0, False), "wait")  # radio not up yet
        self.assertEqual(wwan.redial_due(failed, 110.0, True), "redial")
        self.assertEqual(wwan.redial_due(dict(base, ended="error"), 110.0, True), "redial")
        # a hang-up, a busy or refused call: never again
        self.assertEqual(wwan.redial_due(dict(base, ended="terminated"), 110.0, True), "drop")
        self.assertEqual(wwan.redial_due(dict(base, ended="refused-or-busy"), 110.0, True), "drop")
        # it rang or got through: nothing to repair
        self.assertEqual(
            wwan.redial_due(dict(failed, states={"dialing", "ringing-out"}), 110.0, True), "drop"
        )
        self.assertEqual(wwan.redial_due(dict(failed, states={"active"}), 110.0, True), "drop")
        # only once, only for a radio we switched on, only soon after
        self.assertEqual(wwan.redial_due(dict(failed, redialled=True), 110.0, True), "drop")
        self.assertEqual(wwan.redial_due(dict(failed, forced=False), 110.0, True), "drop")
        self.assertEqual(wwan.redial_due(failed, 100.0 + wwan.REDIAL_WINDOW + 1, True), "drop")

    def test_radio_settled(self):
        self.assertTrue(wwan.radio_settled(None, 5.0, True))
        self.assertFalse(wwan.radio_settled(None, 5.0, False))
        self.assertFalse(wwan.radio_settled(100.0, 100.0 + wwan.RADIO_SETTLE - 1, False))
        self.assertTrue(wwan.radio_settled(100.0, 100.0 + wwan.RADIO_SETTLE, False))

    def test_emergency_table_matches_the_shell(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(HERE)),
            "shell",
            "duo-shell",
            "duoshell",
            "emergencymodel.py",
        )
        loader = importlib.machinery.SourceFileLoader("emergencymodel", path)
        spec = importlib.util.spec_from_loader("emergencymodel", loader)
        em = importlib.util.module_from_spec(spec)
        loader.exec_module(em)
        self.assertEqual(tuple(em.ALWAYS), wwan.EMERGENCY_ALWAYS)
        self.assertEqual(tuple(em.NO_SIM), wwan.EMERGENCY_NO_SIM)
        self.assertEqual(
            {mcc: tuple(n for n, _w in nums) for mcc, nums in em.NATIONAL.items()},
            wwan.EMERGENCY_NATIONAL,
        )


if __name__ == "__main__":
    unittest.main()
