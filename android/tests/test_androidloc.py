"""duo2-android-location's pure parts: the Parcel bytes, the calls into
Android, the policy (python3 -m unittest discover -s tests, in android/)."""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from androidloc import (
    locmgr,  # noqa: E402
    parcel,  # noqa: E402
)
from androidloc import policy as pol  # noqa: E402
from androidloc.parcel import Reader, Writer  # noqa: E402


class ParcelLayout(unittest.TestCase):
    def test_string16(self):
        b = Writer().string16("gps").bytes()
        self.assertEqual(b, struct.pack("<i", 3) + "gps".encode("utf-16-le") + b"\0\0")
        self.assertEqual(len(Writer().string16("passive").bytes()), 4 + 16)
        self.assertEqual(len(Writer().string16("").bytes()), 4 + 4)
        self.assertEqual(Writer().string16(None).bytes(), struct.pack("<i", -1))
        self.assertEqual(
            Reader(Writer().string16("Grüße 日本語").bytes()).read_string16(), "Grüße 日本語"
        )

    def test_string8(self):
        self.assertEqual(Writer().string8("gps").bytes(), struct.pack("<i", 3) + b"gps\0")
        self.assertEqual(Writer().string8("network").bytes(), struct.pack("<i", 7) + b"network\0")
        self.assertEqual(len(Writer().string8("abcd").bytes()), 4 + 8)
        self.assertEqual(Reader(Writer().string8("network").bytes()).read_string8(), "network")

    def test_every_item_is_padded(self):
        w = Writer().string16("a").string8("ab").boolean(True).int64(-2).double(1.5).float32(2.5)
        w.string_list(["x", "yz"]).string_list(None)
        self.assertEqual(len(w.bytes()) % 4, 0)
        r = Reader(w.bytes())
        self.assertEqual(r.read_string16(), "a")
        self.assertEqual(r.read_string8(), "ab")
        self.assertEqual(r.read_int32(), 1)
        self.assertEqual(r.read_int64(), -2)
        self.assertEqual(r.read_double(), 1.5)
        self.assertEqual(r.read_float(), 2.5)
        self.assertEqual(r.read_int32(), 2)
        self.assertEqual([r.read_string16(), r.read_string16()], ["x", "yz"])
        self.assertEqual(r.read_int32(), -1)
        self.assertTrue(r.at_end())

    def test_words(self):
        data = Writer().int32(-1).int32(7).bytes()
        self.assertEqual(parcel.words(data), [0xFFFFFFFF, 7])
        with self.assertRaises(ValueError):
            parcel.words(b"abc")


def read_location(r):
    """Location.createFromParcel (Android 13), as the service reads it."""
    out = {"provider": r.read_string8()}
    mask = r.read_int32()
    out["time_ms"] = r.read_int64()
    out["elapsed_ns"] = r.read_int64()
    if mask & (1 << 8):
        r.read_double()
    out["latitude"] = r.read_double()
    out["longitude"] = r.read_double()
    if mask & locmgr.HAS_ALTITUDE:
        out["altitude"] = r.read_double()
    if mask & locmgr.HAS_SPEED:
        out["speed"] = r.read_float()
    if mask & locmgr.HAS_BEARING:
        out["bearing"] = r.read_float()
    if mask & locmgr.HAS_HORIZONTAL_ACCURACY:
        out["accuracy"] = r.read_float()
    for bit in (5, 6, 7):
        if mask & (1 << bit):
            r.read_float()
    out["bundle"] = r.read_int32()
    out["mask"] = mask
    return out


FIX = {
    "latitude": 12.25,
    "longitude": -45.5,
    "accuracy": 8.0,
    "time_ms": 1700000000123,
    "altitude": 101.0,
    "speed": 3.5,
    "bearing": 270.0,
}


class Calls(unittest.TestCase):
    def test_codes(self):
        # FIRST_CALL_TRANSACTION + index in the lineage-20.0 AIDL files
        self.assertEqual(
            (
                locmgr.ADD_PROVIDER_REQUEST_LISTENER,
                locmgr.REMOVE_PROVIDER_REQUEST_LISTENER,
                locmgr.HAS_PROVIDER,
                locmgr.GET_ALL_PROVIDERS,
            ),
            (30, 31, 36, 37),
        )
        self.assertEqual(
            (
                locmgr.ADD_TEST_PROVIDER,
                locmgr.REMOVE_TEST_PROVIDER,
                locmgr.SET_TEST_PROVIDER_LOCATION,
                locmgr.SET_TEST_PROVIDER_ENABLED,
            ),
            (54, 55, 56, 57),
        )
        self.assertEqual(
            (locmgr.CHECK_OPERATION, locmgr.SET_MODE, locmgr.OP_MOCK_LOCATION), (1, 31, 58)
        )

    def test_location(self):
        r = Reader(locmgr.set_test_provider_location("gps", FIX, 5_000_000_000))
        self.assertEqual(r.read_string16(), "gps")
        self.assertEqual(r.read_int32(), 1)  # non-null Location
        loc = read_location(r)
        self.assertEqual(loc["provider"], "gps")
        self.assertEqual(loc["mask"], 0b1111)
        self.assertEqual(
            (loc["latitude"], loc["longitude"], loc["altitude"]), (12.25, -45.5, 101.0)
        )
        self.assertEqual((loc["speed"], loc["bearing"], loc["accuracy"]), (3.5, 270.0, 8.0))
        self.assertEqual(
            (loc["time_ms"], loc["elapsed_ns"], loc["bundle"]), (1700000000123, 5_000_000_000, -1)
        )
        self.assertEqual(r.read_string16(), "android")
        self.assertIsNone(r.read_string16())  # attribution tag
        self.assertTrue(r.at_end())

    def test_location_without_optional_fields(self):
        fix = {"latitude": 1.0, "longitude": 2.0, "accuracy": 1500.0, "time_ms": 1}
        loc = read_location(Reader(locmgr.location("network", fix, 9)))
        self.assertEqual(loc["mask"], locmgr.HAS_HORIZONTAL_ACCURACY)
        self.assertEqual(loc["accuracy"], 1500.0)
        self.assertNotIn("altitude", loc)

    def test_add_test_provider(self):
        r = Reader(locmgr.add_test_provider("gps"))
        self.assertEqual(r.read_string16(), "gps")
        self.assertEqual(r.read_int32(), 1)
        props = [r.read_int32() for _ in range(9)]
        # network, satellite, cell, cost, altitude, speed, bearing, power, accuracy
        self.assertEqual(
            props, [0, 1, 0, 0, 1, 1, 1, locmgr.POWER_USAGE_HIGH, locmgr.ACCURACY_FINE]
        )
        self.assertEqual(r.read_int32(), 0)  # an empty tag list, not null
        self.assertEqual(r.read_string16(), "android")
        self.assertIsNone(r.read_string16())
        self.assertTrue(r.at_end())
        r = Reader(locmgr.add_test_provider("network"))
        r.read_string16(), r.read_int32()
        self.assertEqual(
            [r.read_int32() for _ in range(9)],
            [1, 0, 0, 0, 0, 0, 0, locmgr.POWER_USAGE_LOW, locmgr.ACCURACY_COARSE],
        )

    def test_small_calls(self):
        r = Reader(locmgr.set_test_provider_enabled("gps", True))
        self.assertEqual(
            (r.read_string16(), r.read_int32(), r.read_string16(), r.read_string16()),
            ("gps", 1, "android", None),
        )
        r = Reader(locmgr.remove_test_provider("network"))
        self.assertEqual(
            (r.read_string16(), r.read_string16(), r.read_string16()), ("network", "android", None)
        )
        r = Reader(locmgr.check_operation())
        self.assertEqual((r.read_int32(), r.read_int32(), r.read_string16()), (58, 1000, "android"))
        r = Reader(locmgr.set_mode_allowed())
        self.assertEqual(
            (r.read_int32(), r.read_int32(), r.read_string16(), r.read_int32()),
            (58, 1000, "android", 0),
        )
        self.assertEqual(Reader(locmgr.has_provider("passive")).read_string16(), "passive")

    def test_replies(self):
        self.assertTrue(locmgr.read_bool(Reader(Writer().int32(0).int32(1).bytes())))
        self.assertEqual(
            locmgr.read_string_list(
                Reader(Writer().int32(0).string_list(["passive", "fused"]).bytes())
            ),
            ["passive", "fused"],
        )
        self.assertEqual(locmgr.read_int(Reader(Writer().int32(0).int32(2).bytes())), 2)
        with self.assertRaises(locmgr.RemoteError) as e:
            locmgr.check_exception(
                Reader(Writer().int32(-1).string16("uid 1000 does not have mock").bytes())
            )
        self.assertIn("SecurityException", str(e.exception))
        # a StrictMode reply header: no exception, the value follows it
        fat = Writer().int32(-128).int32(12).int32(0).int32(0).int32(1).bytes()
        self.assertTrue(locmgr.read_bool(Reader(fat)))

    def test_self_check(self):
        self.assertTrue(locmgr.self_check_ok(True, ["passive", "fused", "network"]))
        self.assertFalse(locmgr.self_check_ok(False, ["passive"]))
        self.assertFalse(locmgr.self_check_ok(True, None))
        self.assertFalse(locmgr.self_check_ok(True, ["gps"]))

    def test_provider_request(self):
        req = Writer().string16("gps").int32(1).int64(1000).int32(100).int64(0).boolean(False)
        req.boolean(False).boolean(False).int32(0)
        self.assertEqual(locmgr.read_provider_request(Reader(req.bytes())), ("gps", True, 1000))
        off = Writer().string16("gps").int32(1).int64(locmgr.INTERVAL_DISABLED).bytes()
        self.assertEqual(locmgr.read_provider_request(Reader(off)), ("gps", False, None))
        null = Writer().string16("network").int32(0).bytes()
        self.assertEqual(locmgr.read_provider_request(Reader(null)), ("network", False, None))


def state(**kw):
    st = pol.State(android=True, frozen=False, allowed=True, requests={})
    for k, v in kw.items():
        setattr(st, k, v)
    return st


class Policy(unittest.TestCase):
    def test_nothing_asked_nothing_runs(self):
        p = pol.Policy()
        d = p.decide(state(), 0)
        self.assertEqual((d.level, d.providers_enabled, d.inhibit), (pol.NONE, True, False))

    def test_gps_runs_the_gnss_and_keeps_awake(self):
        p = pol.Policy()
        d = p.decide(state(requests={"gps": True, "network": True}), 0)
        self.assertEqual((d.level, d.inhibit), (pol.EXACT, True))
        d = p.decide(state(requests={"network": True}), 1)
        self.assertEqual(d.level, pol.EXACT)  # gps lingers
        self.assertFalse(d.inhibit)
        d = p.decide(state(requests={"network": True}), 11)
        self.assertEqual(d.level, pol.NEIGHBORHOOD)  # the cell only
        d = p.decide(state(requests={}), 15)
        self.assertEqual(d.level, pol.NEIGHBORHOOD)
        self.assertAlmostEqual(p.next_check(15), 6)
        self.assertEqual(p.decide(state(requests={}), 21.5).level, pol.NONE)
        self.assertIsNone(p.next_check(21.5))

    def test_frozen_off_or_refused_stops_at_once(self):
        for kw in ({"frozen": True}, {"allowed": False}, {"android": False}):
            p = pol.Policy()
            p.decide(state(requests={"gps": True}), 0)
            d = p.decide(state(requests={"gps": True}, **kw), 1)
            self.assertEqual((d.level, d.inhibit), (pol.NONE, False), kw)
            self.assertIsNone(p.next_check(1))
        self.assertTrue(pol.Policy().decide(state(frozen=True), 0).providers_enabled)
        self.assertFalse(pol.Policy().decide(state(allowed=False), 0).providers_enabled)

    def test_no_network_provider(self):
        p = pol.Policy(pol.Config(network=False))
        self.assertEqual(p.decide(state(requests={"network": True}), 0).level, pol.NONE)
        self.assertEqual(pol.providers(p.cfg), ["gps"])
        self.assertEqual(pol.providers(pol.Config()), ["gps", "network"])

    def test_keep_awake_off(self):
        p = pol.Policy(pol.Config(keep_awake=False))
        self.assertFalse(p.decide(state(requests={"gps": True}), 0).inhibit)

    def test_route(self):
        p = pol.Policy()
        both = state(requests={"gps": True, "network": True})
        self.assertEqual(p.route(both, 8.0), ["gps", "network"])
        self.assertEqual(p.route(both, 1500.0), ["network"])  # a cell fix is no GPS fix
        self.assertEqual(p.route(state(requests={"gps": True}), 1500.0), [])
        self.assertEqual(p.route(state(requests={"gps": True}, frozen=True), 8.0), [])
        self.assertEqual(p.route(both, 0.0), [])
        self.assertEqual(pol.Policy(pol.Config(network=False)).route(both, 1500.0), [])


class Geoclue(unittest.TestCase):
    NOW = 1_700_000_000_000

    def props(self, **kw):
        p = {
            "Latitude": 10.0,
            "Longitude": 20.0,
            "Accuracy": 5.0,
            "Altitude": -1.7976931348623157e308,
            "Speed": -1.0,
            "Heading": -1.0,
            "Timestamp": (self.NOW // 1000, 500000),
        }
        p.update(kw)
        return p

    def test_unknowns(self):
        fix = pol.fix_from_geoclue(self.props(), self.NOW)
        self.assertEqual(set(fix), {"latitude", "longitude", "accuracy", "time_ms"})
        self.assertEqual(fix["time_ms"], (self.NOW // 1000) * 1000 + 500)

    def test_knowns(self):
        fix = pol.fix_from_geoclue(self.props(Altitude=120.0, Speed=0.0, Heading=360.0), self.NOW)
        self.assertEqual((fix["altitude"], fix["speed"], fix["bearing"]), (120.0, 0.0, 0.0))

    def test_bad_fixes_and_stamps(self):
        self.assertIsNone(pol.fix_from_geoclue(self.props(Accuracy=0.0), self.NOW))
        self.assertIsNone(pol.fix_from_geoclue(self.props(Latitude=91.0), self.NOW))
        self.assertIsNone(pol.fix_from_geoclue({"Latitude": 1.0}, self.NOW))
        future = pol.fix_from_geoclue(self.props(Timestamp=(self.NOW // 1000 + 60, 0)), self.NOW)
        self.assertEqual(future["time_ms"], self.NOW)
        old = pol.fix_from_geoclue(self.props(Timestamp=(self.NOW // 1000 - 3600, 0)), self.NOW)
        self.assertEqual(old["time_ms"], self.NOW)
        self.assertEqual(pol.age_ns({"time_ms": self.NOW - 2}, self.NOW), 2_000_000)
        self.assertEqual(pol.age_ns({"time_ms": self.NOW + 2}, self.NOW), 0)


class Host(unittest.TestCase):
    def test_permission(self):
        self.assertTrue(pol.host_allowed(True, set()))
        self.assertFalse(pol.host_allowed(False, set()))
        self.assertFalse(pol.host_allowed(True, {"duo2-android-location"}))
        conf = "[org.gnome.Weather]\nallowed=false\n\n[duo2-android-location]\nallowed=false\nsystem=false\n"
        self.assertEqual(pol.parse_denied(conf), {"org.gnome.Weather", "duo2-android-location"})
        self.assertEqual(pol.parse_denied("[x]\nallowed=true\n"), set())
        self.assertTrue(pol.parse_store_enabled('{"enabled": true, "apps": {}}'))
        self.assertFalse(pol.parse_store_enabled('{"enabled": false}'))
        self.assertTrue(pol.parse_store_enabled("broken"))

    def test_config(self):
        cfg = pol.apply_config(
            pol.Config(),
            pol.parse_config(
                "NETWORK=no\nLINGER_S=99999\n# KEEP_AWAKE=no\nGPS_MAX_ACCURACY_M=abc\nkeep_awake = off\n"
            ),
        )
        self.assertEqual((cfg.network, cfg.linger_s, cfg.keep_awake), (False, 600.0, False))
        self.assertEqual(cfg.gps_max_accuracy_m, 200.0)
        self.assertEqual(pol.apply_config(pol.Config(), {}), pol.Config())

    def test_status_has_no_position(self):
        d = pol.Decision(pol.EXACT, True, True)
        s = pol.status(state(requests={"gps": True}), d, {"gps": 3}, 41.26, 7.6, None)
        self.assertEqual(s["first_fix_s"], 41.3)
        self.assertEqual(s["last_accuracy_m"], 8)
        text = repr(s).lower()
        for word in ("latitude", "longitude", "altitude", "bearing"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
