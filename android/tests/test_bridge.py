"""duo2-android-location's glue against a fake Android: libgbinder and
system_server's LocationManagerService / AppOpsService played by
FakeSystemServer from the same Parcel layouts (python3 -m unittest
discover -s tests, in android/; needs PyGObject, skipped without it).

What it proves: the setup sequence (self check, the app op, the test
providers, the listener, off-then-on), the provider-request reports
reaching the policy, geoclue's level and the sleep inhibitor following
them (fakes), fixes reaching Android as complete Locations, the linger,
the frozen container, and a status file without a position in it."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from androidloc import (
    locmgr,  # noqa: E402
    parcel,  # noqa: E402
)
from androidloc import policy as pol  # noqa: E402

try:
    from gi.repository import GLib
except ImportError:  # pragma: no cover
    GLib = None


def read_location(r):
    out = {"provider": r.read_string8()}
    mask = r.read_int32()
    out["time_ms"], out["elapsed_ns"] = r.read_int64(), r.read_int64()
    out["latitude"], out["longitude"] = r.read_double(), r.read_double()
    if mask & locmgr.HAS_ALTITUDE:
        out["altitude"] = r.read_double()
    if mask & locmgr.HAS_SPEED:
        out["speed"] = r.read_float()
    if mask & locmgr.HAS_BEARING:
        out["bearing"] = r.read_float()
    out["accuracy"] = r.read_float() if mask & locmgr.HAS_HORIZONTAL_ACCURACY else None
    r.read_int32()
    return out


class FakeSystemServer:
    def __init__(self, providers=("passive", "fused", "network"), mode=3):
        self.providers = set(providers)
        self.mode = mode
        self.tests = {}  # name -> enabled
        self.listener = None
        self.locations = []
        self.calls = []

    def ok(self):
        return parcel.Writer().int32(0)

    def handle(self, iface, code, data, obj):
        r = parcel.Reader(data)
        self.calls.append((iface, code))
        if iface == locmgr.APPOPS_IFACE:
            if code == locmgr.CHECK_OPERATION:
                assert (r.read_int32(), r.read_int32(), r.read_string16()) == (58, 1000, "android")
                return self.ok().int32(self.mode).bytes()
            if code == locmgr.SET_MODE:
                r.read_int32(), r.read_int32(), r.read_string16()
                self.mode = r.read_int32()
                return self.ok().bytes()
        if code == locmgr.HAS_PROVIDER:
            return self.ok().boolean(r.read_string16() in self.providers | set(self.tests)).bytes()
        if code == locmgr.GET_ALL_PROVIDERS:
            return self.ok().string_list(sorted(self.providers | set(self.tests))).bytes()
        if code == locmgr.ADD_TEST_PROVIDER:
            name = r.read_string16()
            if self.mode != 0:
                return (
                    parcel.Writer()
                    .int32(-1)
                    .string16("uid 1000 does not have android:mock_location")
                    .bytes()
                )
            self.tests[name] = False
            return self.ok().bytes()
        if code == locmgr.SET_TEST_PROVIDER_ENABLED:
            name, on = r.read_string16(), r.read_int32()
            self.tests[name] = bool(on)
            return self.ok().bytes()
        if code == locmgr.REMOVE_TEST_PROVIDER:
            self.tests.pop(r.read_string16(), None)
            return self.ok().bytes()
        if code == locmgr.ADD_PROVIDER_REQUEST_LISTENER:
            self.listener = obj
            return self.ok().bytes()
        if code == locmgr.REMOVE_PROVIDER_REQUEST_LISTENER:
            self.listener = None
            return self.ok().bytes()
        if code == locmgr.SET_TEST_PROVIDER_LOCATION:
            name = r.read_string16()
            assert r.read_int32() == 1
            loc = read_location(r)
            assert loc["provider"] == name and self.tests.get(name), (name, self.tests)
            self.locations.append(loc)
            return self.ok().bytes()
        raise AssertionError("unexpected call %s %d" % (iface, code))

    def report(self, provider, active):
        w = parcel.Writer().string16(provider).int32(1)
        if active:
            w.int64(1000).int32(100).int64(0).boolean(False).boolean(False).boolean(False).int32(0)
        else:
            w.int64(locmgr.INTERVAL_DISABLED)
        req = FakeRemoteRequest(w.bytes())
        self.listener.handler(req, locmgr.ON_PROVIDER_REQUEST_CHANGED, 1)


class FakeRemoteRequest:
    def __init__(self, data, euid=1000):
        self.data, self.euid = data, euid

    def sender_euid(self):
        return self.euid

    def init_reader(self):
        return parcel.Reader(self.data)


class FakeRequest:
    def __init__(self):
        self.words, self.obj = [], None

    def append_int32(self, w):
        assert 0 <= w < 2**32
        self.words.append(w)

    def append_local_object(self, obj):
        self.obj = obj

    def data(self):
        return b"".join(w.to_bytes(4, "little") for w in self.words)


class FakeReply:
    def __init__(self, data):
        self.data = data

    def init_reader(self):
        return parcel.Reader(self.data)


def fake_gbinder(server):
    m = types.ModuleType("gbinder")

    class LocalObject:
        def __init__(self, handler):
            self.handler = handler

        def new_reply(self):
            return object()

    class Remote:
        def __init__(self, name):
            self.name = name

        def is_dead(self):
            return False

        def add_death_handler(self, f):
            self.death = f

    class ServiceManager:
        def __init__(self, *a):
            pass

        def is_present(self):
            return True

        def get_service_sync(self, name):
            return Remote(name), 0

        def new_local_object(self, iface, handler):
            assert iface == locmgr.LISTENER_IFACE
            return LocalObject(handler)

    class Client:
        def __init__(self, remote, iface):
            self.iface = iface

        def new_request(self):
            return FakeRequest()

        def transact(self, code, flags, req, reply_func, destroy):
            data = server.handle(self.iface, code, req.data(), req.obj)
            GLib.idle_add(lambda: reply_func(FakeReply(data), 0) and False)
            return 1

        def transact_sync_oneway(self, code, req):
            server.handle(self.iface, code, req.data(), req.obj)
            return 0

    m.ServiceManager, m.Client = ServiceManager, Client
    return m


def load_bridge():
    path = os.path.join(os.path.dirname(HERE), "duo2-android-location")
    loader = importlib.machinery.SourceFileLoader("duo2_android_location", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def spin(cond, seconds=3.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakeGeoclue:
    def __init__(self, bus, on_fix, on_state):
        self.on_fix, self.levels, self.error = on_fix, [], None

    def want(self, level):
        if not self.levels or self.levels[-1] != level:
            self.levels.append(level)

    def stop_now(self):
        self.levels.append(0)


class FakeInhibitor:
    def __init__(self, bus):
        self.held = []

    def hold(self, on):
        if not self.held or self.held[-1] != on:
            self.held.append(on)


class FakeHost:
    def __init__(self, changed):
        self.allowed = True


@unittest.skipIf(GLib is None, "no PyGObject")
class Bridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeSystemServer()
        sys.modules["gbinder"] = fake_gbinder(self.server)
        self.mod = load_bridge()
        dev = os.path.join(self.tmp.name, "anbox-binder")
        open(dev, "w").close()
        self.frozen = False
        self.mod.binder_device = lambda: dev
        self.mod.system_server_id = lambda: "4242:99"
        self.mod.runtime_dir = lambda: self.tmp.name
        self.mod.container_frozen = lambda: self.frozen
        self.mod.Geoclue, self.mod.Inhibitor, self.mod.HostPermission = (
            FakeGeoclue,
            FakeInhibitor,
            FakeHost,
        )
        self.mod.Gio.bus_get_sync = lambda *a: None
        self.mod.CONFIGS = (os.path.join(self.tmp.name, "conf"),)
        with open(self.mod.CONFIGS[0], "w") as f:
            f.write("LINGER_S=0.3\n")
        self.mod.ANDROID_POLL_S = 3600  # the test ticks by hand

    def tearDown(self):
        sys.modules.pop("gbinder", None)
        self.tmp.cleanup()

    def bridge_up(self):
        b = self.mod.Bridge()
        self.assertTrue(spin(lambda: b.st.android), self.server.calls)
        self.assertTrue(spin(lambda: all(self.server.tests.values()) and self.server.tests))
        return b

    def test_setup(self):
        b = self.bridge_up()
        self.assertEqual(self.server.mode, 0)  # the app op was set
        self.assertEqual(set(self.server.tests), {"gps", "network"})
        self.assertIsNotNone(self.server.listener)
        self.assertEqual(b.android.mocked, ["gps", "network"])
        codes = [c for i, c in self.server.calls if i == locmgr.LOCATION_IFACE]
        self.assertEqual(codes[:2], [locmgr.HAS_PROVIDER, locmgr.GET_ALL_PROVIDERS])
        with open(os.path.join(self.tmp.name, "duo2-android-location.boot")) as f:
            self.assertEqual(f.read(), "4242:99")

    def test_real_gps_is_left_alone(self):
        self.server.providers.add("gps")
        b = self.bridge_up()
        self.assertEqual(b.android.mocked, ["network"])

    def test_codes_that_do_not_fit(self):
        self.server.handle_orig = self.server.handle

        def wrong(iface, code, data, obj):
            if code == locmgr.HAS_PROVIDER:
                return parcel.Writer().int32(0).int32(0).bytes()  # "passive" missing: wrong codes
            return self.server.handle_orig(iface, code, data, obj)

        self.server.handle = wrong
        b = self.mod.Bridge()
        self.assertTrue(spin(lambda: b.android.broken is not None))
        self.assertEqual(self.server.tests, {})
        self.assertNotIn((locmgr.APPOPS_IFACE, locmgr.SET_MODE), self.server.calls)

    def test_requests_fixes_linger_and_freeze(self):
        b = self.bridge_up()
        g, inh = b.geoclue, b.inhibitor
        self.assertEqual(g.levels[-1], pol.NONE)
        self.server.report("gps", True)
        self.assertTrue(spin(lambda: g.levels[-1] == pol.EXACT))
        self.assertTrue(inh.held[-1])
        now = time.time()
        props = {
            "Latitude": 10.5,
            "Longitude": 20.25,
            "Accuracy": 6.0,
            "Altitude": 120.0,
            "Speed": 1.5,
            "Heading": 90.0,
            "Timestamp": (int(now), 0),
        }
        b.fix(props)
        b.fix(dict(props, Accuracy=1500.0))  # a cell fix: not for gps, network is not asked
        self.assertTrue(spin(lambda: len(self.server.locations) == 1))
        loc = self.server.locations[0]
        self.assertEqual(
            (loc["provider"], loc["latitude"], loc["longitude"], loc["accuracy"]),
            ("gps", 10.5, 20.25, 6.0),
        )
        self.assertEqual((loc["altitude"], loc["speed"], loc["bearing"]), (120.0, 1.5, 90.0))
        self.assertGreater(loc["elapsed_ns"], 0)
        with open(b.status_path) as f:
            status = f.read()
        self.assertEqual(json.loads(status)["fixes_sent"], {"gps": 1})
        for word in ("10.5", "20.25", "latitude"):
            self.assertNotIn(word, status)
        self.server.report("gps", False)
        self.assertTrue(spin(lambda: inh.held[-1] is False))
        self.assertEqual(g.levels[-1], pol.EXACT)  # lingering
        self.assertTrue(spin(lambda: g.levels[-1] == pol.NONE, 2))
        self.server.report("network", True)
        self.assertTrue(spin(lambda: g.levels[-1] == pol.NEIGHBORHOOD))
        self.frozen = True
        b.tick()
        self.assertEqual(g.levels[-1], pol.NONE)
        self.frozen = False
        b.tick()
        self.assertEqual(g.levels[-1], pol.NEIGHBORHOOD)

    def test_host_switch_turns_the_providers_off(self):
        b = self.bridge_up()
        b.host.allowed = False
        b.evaluate()
        self.assertTrue(spin(lambda: not any(self.server.tests.values())))
        self.server.report("gps", True)
        spin(lambda: False, 0.2)
        self.assertEqual(b.geoclue.levels[-1], pol.NONE)

    def test_reports_from_others_ignored(self):
        b = self.bridge_up()
        w = parcel.Writer().string16("gps").int32(1).int64(1000).int32(100).int64(0)
        w.boolean(False).boolean(False).boolean(False).int32(0)
        self.server.listener.handler(FakeRemoteRequest(w.bytes(), euid=1234), 1, 1)
        spin(lambda: False, 0.2)
        self.assertFalse(b.st.requests.get("gps"))

    def test_shutdown(self):
        b = self.bridge_up()
        b.shutdown()
        self.assertEqual(self.server.tests, {"gps": False})  # gps off, our network removed
        self.assertIsNone(self.server.listener)
        self.assertFalse(os.path.exists(b.status_path))


if __name__ == "__main__":
    unittest.main()
