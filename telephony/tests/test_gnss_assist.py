"""duo2-gnss-assist's pure parts: python3 -m unittest discover -s tests
(from telephony/)."""

import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(os.path.dirname(HERE), name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ga = load("duo2-gnss-assist")

ARRAYS = """modem.location.capabilities.length                   : 5
modem.location.capabilities.value[1]                 : 3gpp-lac-ci
modem.location.enabled.length                        : 1
modem.location.enabled.value[1]                      : 3gpp-lac-ci
modem.location.signals                               : no
modem.location.gps.refresh-rate                      : 30
modem.location.gps.supl-server                       : --
modem.location.gps.supported-assistance-data.length  : 1
modem.location.gps.supported-assistance-data.value[1] : xtra
modem.location.gps.assistance-servers.length         : 2
modem.location.gps.assistance-servers.value[2]       : https://b.example.net/xtra3.bin
modem.location.gps.assistance-servers.value[1]       : https://a.example.net/xtra3.bin
"""


class Parse(unittest.TestCase):
    def test_arrays(self):
        st = ga.parse_location_status(ARRAYS)
        self.assertEqual(st["supported"], ["xtra"])
        self.assertEqual(
            st["servers"], ["https://a.example.net/xtra3.bin", "https://b.example.net/xtra3.bin"]
        )

    def test_plain_and_empty(self):
        st = ga.parse_location_status(
            "modem.location.gps.supported-assistance-data : xtra\n"
            "modem.location.gps.assistance-servers : --\n"
        )
        self.assertEqual(st, {"supported": ["xtra"], "servers": []})
        self.assertEqual(ga.parse_location_status(""), {"supported": [], "servers": []})

    def test_servers_filtered(self):
        self.assertEqual(
            ga.usable_servers(
                [
                    "https://a.example.net/x.bin",
                    "file:///etc/shadow",
                    "https://a b/x",
                    "http://h.example:80/p/x3.bin",
                ]
            ),
            ["https://a.example.net/x.bin", "http://h.example:80/p/x3.bin"],
        )


class Decide(unittest.TestCase):
    def test_route(self):
        self.assertEqual(
            ga.default_route_dev("1.1.1.1 via 192.0.2.1 dev wlan0 src 192.0.2.9 uid 0"), "wlan0"
        )
        self.assertEqual(
            ga.default_route_dev("1.1.1.1 dev qmapmux0.0 src 198.51.100.2"), "qmapmux0.0"
        )
        self.assertIsNone(ga.default_route_dev("RTNETLINK answers: Network is unreachable"))
        self.assertTrue(ga.on_wifi("wlan0"))
        self.assertFalse(ga.on_wifi("qmapmux0.0"))
        self.assertFalse(ga.on_wifi(None))

    def test_age_and_size(self):
        self.assertTrue(ga.is_stale(None, 1000))
        self.assertTrue(ga.is_stale({"fetched_at": "x"}, 1000))
        self.assertFalse(ga.is_stale({"fetched_at": 1000}, 1000 + 23 * 3600))
        self.assertTrue(ga.is_stale({"fetched_at": 1000}, 1000 + 25 * 3600))
        self.assertFalse(ga.plausible(b"x" * 100))
        self.assertTrue(ga.plausible(b"x" * 60000))
        self.assertFalse(ga.plausible(b"x" * (2 << 20)))


if __name__ == "__main__":
    unittest.main()
