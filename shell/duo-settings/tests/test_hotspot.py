import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duosettings import hotspot as hs  # noqa: E402

HOTSPOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "hotspot")


class Hotspot(unittest.TestCase):
    def test_status(self):
        self.assertEqual(hs.status_parts({}), [("off",)])
        st = {
            "state": "on",
            "wifi": {"band": "5", "channel": 36, "how": "shared", "client": "kept"},
            "uplink": {"iface": "qmapmux0.0"},
        }
        self.assertEqual(hs.status_parts(st), [("on", "5", 36), ("beside",)])
        st = {
            "state": "on",
            "wifi": {"band": "2.4", "channel": 6, "how": "own", "client": "radio"},
            "uplink": None,
        }
        self.assertEqual(hs.status_parts(st), [("on", "2.4", 6), ("held",), ("no-data",)])
        self.assertTrue(hs.wifi_on({"state": "on", "wifi": {"band": "5"}}))
        self.assertFalse(hs.wifi_on({"state": "on", "wifi": None, "usb": {"iface": "tether0"}}))
        self.assertTrue(hs.usb_on({"state": "on", "wifi": None, "usb": {"iface": "tether0"}}))

    def test_devices(self):
        st = {
            "clients": [
                {"via": "wifi", "name": "pixel", "ip": "10.15.43.12", "since": 1000},
                {"via": "usb", "name": None, "ip": "10.15.44.10", "since": None},
            ]
        }
        self.assertEqual(
            hs.device_rows(st, 1000 + 305),
            [("pixel", ("10.15.43.12", "wifi", 5)), (None, ("10.15.44.10", "usb", None))],
        )
        self.assertEqual(hs.device_rows({}, 0), [])

    def test_choices(self):
        self.assertEqual(hs.index_of(hs.BANDS, "2.4"), 1)
        self.assertEqual(hs.index_of(hs.SECURITIES, "nope"), 0)
        self.assertEqual(hs.idle_index(30), hs.IDLE_OFF.index(30))
        self.assertEqual(hs.idle_index("x"), hs.IDLE_OFF.index(10))
        self.assertEqual(hs.fmt_bytes(999), "999 B")
        self.assertEqual(hs.fmt_bytes(1234567), "1.2 MB")
        self.assertEqual(hs.fmt_bytes(None), "0 B")
        self.assertEqual(hs.masked("abcd-efgh-jkmn"), "•" * 14)
        self.assertEqual(hs.masked(""), "•" * 8)

    def test_password_problem(self):
        self.assertIsNone(hs.password_problem("abcd-efgh-jkmn"))
        self.assertIsNone(hs.password_problem("my own pass 1"))
        self.assertIsNone(hs.password_problem("x" * 8))
        self.assertIsNone(hs.password_problem("~" * 63))
        self.assertEqual(hs.password_problem(""), "short")
        self.assertEqual(hs.password_problem("x" * 7), "short")
        self.assertEqual(hs.password_problem("x" * 64), "long")
        self.assertEqual(hs.password_problem("pässwörd12"), "chars")
        self.assertEqual(hs.password_problem("tab\there12"), "chars")
        self.assertEqual(hs.password_problem("é"), "chars")  # the accent first
        self.assertEqual(hs.password_problem(None), "short")
        self.assertTrue(hs.valid_password("abcd-efgh-jkmn"))
        self.assertFalse(hs.valid_password("short"))

    def test_parse(self):
        self.assertEqual(hs.parse('{"ok": true}'), {"ok": True})
        self.assertEqual(hs.parse(""), {"ok": False, "error": "no answer"})

    def test_same_rules_as_the_helper(self):
        # the page's checks and choices are the helper's (hotspot/duo2hotspot/conf.py)
        sys.path.insert(0, HOTSPOT)
        from duo2hotspot import conf

        for s in ("Duo", "", " ", "a" * 32, "a" * 33, "é" * 16, "é" * 17, "x\ty"):
            self.assertEqual(hs.valid_ssid(s), conf.valid_ssid(s), repr(s))
        for p in (
            "short",
            "abcd-efgh-jkmn",
            "x" * 63,
            "x" * 64,
            "pässwörd12",
            "tab\there",
            "my own pass 1",
            "",
        ):
            self.assertEqual(hs.valid_password(p), conf.valid_password(p), repr(p))
        self.assertEqual([v for v, _l in hs.BANDS], list(conf.BANDS))
        self.assertEqual([v for v, _l in hs.SECURITIES], list(conf.SECURITIES))
        self.assertEqual([v for v, _l in hs.WIFI_CLIENT], list(conf.WIFI_CLIENT))
        for m in hs.IDLE_OFF:
            conf.validate("IDLE_OFF_MIN", str(m))


if __name__ == "__main__":
    unittest.main()
