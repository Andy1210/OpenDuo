"""duo2-wwan and other programs' bearers (VoLTE: the IMS bearers 81voltd
asks ModemManager for), played through with a stand-in ModemManager: only
the bearer for our APN is adopted and configured, and the mobile data switch
disconnects only that one. python3 -m unittest discover -s tests (telephony/)."""

import enum
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_emergency import Clock, Modem, ModemState, Obj, Signals, Voice, fake_mm  # noqa: E402
from test_wwan import load_wwan  # noqa: E402
from test_wwan import wwan as WWAN

APN = "internet.v6.telekom"


class Registration(enum.IntEnum):
    IDLE, HOME, SEARCHING, DENIED, UNKNOWN, ROAMING = range(6)
    (
        HOME_SMS_ONLY,
        ROAMING_SMS_ONLY,
        EMERGENCY_ONLY,
        HOME_CSFB_NOT_PREFERRED,
        ROAMING_CSFB_NOT_PREFERRED,
        ATTACHED_RLOS,
    ) = range(6, 12)


class PacketService(enum.IntEnum):
    UNKNOWN, DETACHED, ATTACHED = range(3)


class Props:
    def __init__(self, apn):
        self.apn = apn

    def get_apn(self):
        return self.apn


class Bearer(Signals):
    def __init__(self, n, apn, connected=True, iface=None):
        super().__init__()
        self.path = "/org/freedesktop/ModemManager1/Bearer/%d" % n
        self.props = Props(apn)
        self.connected = connected
        self.iface = iface or "qmapmux0.%d" % n
        self.disconnects = 0

    def get_path(self):
        return self.path

    def get_properties(self):
        return self.props

    def get_connected(self):
        return self.connected

    def get_interface(self):
        return self.iface

    def disconnect(self, _c, cb):
        # MMBearer's D-Bus Disconnect (not GObject's handler disconnect)
        self.disconnects += 1
        self.connected = False
        cb(self, None)

    def disconnect_finish(self, _res):
        return True


class BearerModem(Modem):
    def __init__(self, bearers):
        super().__init__(ModemState.REGISTERED)
        self.bearers = bearers

    def list_bearers_sync(self, _c):
        return list(self.bearers)


class Eps:
    def __init__(self, apn):
        self.apn = apn

    def get_apn(self):
        return self.apn


class M3gpp(Signals):
    def __init__(self, apn=APN):
        super().__init__()
        self.eps = Eps(apn)

    def get_registration_state(self):
        return Registration.HOME

    def get_packet_service_state(self):
        return PacketService.ATTACHED

    def get_initial_eps_bearer_settings(self):
        return self.eps

    def get_operator_code(self):
        return "26201"


class ConnectProps:
    def __init__(self):
        self.apn = None

    def set_apn(self, apn):
        self.apn = apn

    def set_ip_type(self, _t):
        pass

    def set_allow_roaming(self, _r):
        pass

    def set_user(self, _u):
        pass

    def set_password(self, _p):
        pass


class Simple:
    def __init__(self):
        self.connects = []
        self.disconnects = 0

    def connect(self, props, _c, _cb):
        # left pending: what matters is that it was asked for, with which APN
        self.connects.append(props.apn)

    def disconnect(self, *_a):
        self.disconnects += 1


class BearerObj(Obj):
    def __init__(self, modem, m3gpp, simple):
        super().__init__(modem, Voice(), m3gpp)
        self.simple = simple

    def get_modem_simple(self):
        return self.simple


class Net:
    def __init__(self):
        self.ups = []
        self.downs = 0

    def up(self, bearer):
        self.ups.append(bearer.get_path())

    def down(self):
        self.downs += 1


class Daemon:
    def __init__(self, wwan, data=True, **conf):
        self.conf = dict(wwan.DEFAULTS)
        self.conf.update(conf)
        self.airplane = False
        self.provision_tries = 2
        self.data = data
        self.nr = types.SimpleNamespace(evaluate=lambda *a: None, modem_gone=lambda *a: None)
        self.net = Net()
        self.modems = {}

    def data_on(self):
        return self.data


def fake_mm_bearers():
    mm = fake_mm()
    mm.Modem3gppRegistrationState = Registration
    mm.Modem3gppPacketServiceState = PacketService
    mm.BearerIpFamily = types.SimpleNamespace(IPV4=1, IPV6=2, IPV4V6=4)
    mm.SimpleConnectProperties = types.SimpleNamespace(new=ConnectProps)
    return mm


@unittest.skipIf(WWAN is None, "no PyGObject")
class Bearers(unittest.TestCase):
    def setUp(self):
        self.wwan = load_wwan()
        self.wwan.Mm = fake_mm_bearers()
        self.clock = Clock()
        self.wwan.GLib = self.clock
        self.wwan.time = types.SimpleNamespace(monotonic=lambda: self.clock.now)
        # GObject's own connect (watch_bearer), on the stand-in's signals
        self.wwan.GObject = types.SimpleNamespace(
            Object=types.SimpleNamespace(
                connect=lambda obj, name, fn: Signals.connect(obj, name, fn)
            )
        )
        self.dir = tempfile.TemporaryDirectory()
        self.wwan.AIRPLANE_SWITCH = os.path.join(self.dir.name, "airplane")

    def tearDown(self):
        self.dir.cleanup()

    def make(self, bearers, data=True, eps_apn=APN, **conf):
        d = Daemon(self.wwan, data, **conf)
        simple = Simple()
        modem = BearerModem(bearers)
        m = self.wwan.Modem(d, BearerObj(modem, M3gpp(eps_apn), simple))
        d.modems[m.path] = m
        return d, m, simple

    def test_own_apn_rule(self):
        own = self.wwan.own_apn
        self.assertTrue(own(APN, APN))
        self.assertTrue(own("Internet.V6.Telekom", APN))  # APNs ignore case
        self.assertTrue(own(" %s " % APN, APN))
        self.assertFalse(own("ims", APN))
        self.assertFalse(own("sos", APN))
        self.assertFalse(own("", APN))
        self.assertFalse(own(APN, ""))  # no APN of ours: nothing is ours
        self.assertFalse(own(None, None))

    def test_ims_bearer_is_not_adopted(self):
        ims = Bearer(1, "ims")
        d, m, simple = self.make([ims])
        self.assertEqual(d.net.ups, [])  # its link is not configured
        self.assertIsNone(m.bearer)
        self.assertEqual(simple.connects, [APN])  # ours is connected beside it

    def test_own_bearer_is_adopted_beside_ims(self):
        ims, own = Bearer(1, "ims"), Bearer(2, APN)
        d, m, simple = self.make([ims, own])
        self.assertEqual(d.net.ups, [own.path])
        self.assertIs(m.bearer, own)
        self.assertEqual(simple.connects, [])

    def test_configured_apn_decides(self):
        other = Bearer(3, "internet.telekom")
        d, _m, simple = self.make([Bearer(1, "ims"), other], APN="internet.telekom")
        self.assertEqual(d.net.ups, [other.path])
        self.assertEqual(simple.connects, [])

    def test_data_off_disconnects_only_ours(self):
        ims, own = Bearer(1, "ims"), Bearer(2, APN)
        d, m, simple = self.make([ims, own])
        d.data = False
        m.data_switched()
        self.assertEqual(own.disconnects, 1)
        self.assertEqual(ims.disconnects, 0)
        self.assertTrue(ims.connected)
        self.assertEqual(simple.disconnects, 0)  # never Simple.Disconnect (all)
        self.assertIsNone(m.bearer)

    def test_data_off_with_only_ims_up(self):
        ims = Bearer(1, "ims")
        d, m, simple = self.make([ims], data=False)
        m.data_switched()
        self.assertEqual(ims.disconnects, 0)
        self.assertEqual(simple.disconnects, 0)
        self.assertEqual(simple.connects, [])

    def test_foreign_bearer_logged_once(self):
        ims = Bearer(1, "ims")
        d, m, simple = self.make([ims])
        m.connecting = False
        m.maybe_connect()
        self.assertEqual(m.foreign_seen, {ims.path})
        self.assertEqual(d.net.ups, [])


if __name__ == "__main__":
    unittest.main()
