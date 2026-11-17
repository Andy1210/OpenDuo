import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import hotspotmodel as hm  # noqa: E402

ON = {
    "state": "on",
    "wifi": {"band": "5", "channel": 36, "client": "kept"},
    "uplink": {"iface": "qmapmux0.0", "ipv6": True},
    "client_count": 2,
    "clients": [{"via": "wifi", "name": "a"}, {"via": "wifi", "name": None}],
}


class Tile(unittest.TestCase):
    def test_states(self):
        self.assertEqual(hm.tile({}, False, False), (False, False, "Not installed", 0))
        self.assertEqual(hm.tile(ON, True, True), (False, False, "Airplane mode", 0))
        self.assertEqual(hm.tile({}, True, False), (True, False, "Off", 0))
        self.assertEqual(
            hm.tile({"state": "off", "wifi": None}, True, False), (True, False, "Off", 0)
        )
        self.assertEqual(hm.tile(ON, True, False), (True, True, "devices", 2))
        self.assertEqual(
            hm.tile(dict(ON, clients=[], client_count=0), True, False), (True, True, "On", 0)
        )
        self.assertEqual(
            hm.tile(dict(ON, uplink=None, clients=[], client_count=0), True, False),
            (True, True, "No mobile data", 0),
        )
        # USB tethering alone: the WiFi tile is off
        usb = {
            "state": "on",
            "wifi": None,
            "usb": {"iface": "tether0"},
            "client_count": 1,
            "clients": [{"via": "usb"}],
        }
        self.assertEqual(hm.tile(usb, True, False)[:3], (True, False, "Off"))
        # a USB host is not one of the WiFi tile's devices
        mixed = dict(ON, clients=[{"via": "wifi"}, {"via": "usb"}], client_count=2)
        self.assertEqual(hm.tile(mixed, True, False), (True, True, "devices", 1))

    def test_wifi_held_and_words(self):
        self.assertFalse(hm.wifi_held(ON))
        for client in ("radio", "cut"):
            held = dict(ON, wifi=dict(ON["wifi"], client=client))
            self.assertTrue(hm.wifi_held(held))
        self.assertFalse(hm.wifi_held({}))
        self.assertEqual(hm.toggle_words(ON), ["off"])
        self.assertEqual(hm.toggle_words({}), ["on"])

    def test_replies(self):
        self.assertTrue(hm.needs_wifi_off({"ok": False, "code": "needs-wifi-off"}))
        self.assertFalse(hm.needs_wifi_off({"ok": False, "code": "channel-not-shared"}))
        self.assertFalse(hm.needs_wifi_off({"ok": True}))
        self.assertEqual(
            hm.parse_reply('noise\n{"ok": true, "state": {}}\n'), {"ok": True, "state": {}}
        )
        self.assertEqual(hm.parse_reply(""), {"ok": False, "error": "no answer"})

    def test_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hotspot.json")
            self.assertEqual(hm.read_state(p), {})
            with open(p, "w") as f:
                f.write('{"state": "on"}')
            self.assertEqual(hm.read_state(p), {"state": "on"})
            with open(p, "w") as f:
                f.write("[1]")
            self.assertEqual(hm.read_state(p), {})

    def test_grants_match_the_helper(self):
        # the words the shell sends with a grant are the helper's grant actions
        import importlib.machinery
        import importlib.util

        root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "hotspot")
        sys.path.insert(0, root)
        loader = importlib.machinery.SourceFileLoader("hs_cli", os.path.join(root, "duo2-hotspot"))
        spec = importlib.util.spec_from_loader("hs_cli", loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        try:
            from gi.repository import Gio  # noqa: F401
        except ImportError:
            self.skipTest("no PyGObject")
        from duoshell import hotspot

        for words, action in hotspot.GRANTED.items():
            self.assertEqual(cli.grant_action(*cli.parse(list(words))), action)


if __name__ == "__main__":
    unittest.main()
