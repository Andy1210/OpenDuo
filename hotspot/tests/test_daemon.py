"""duo2-hotspotd's start and stop played through with a stand-in system: the
commands it runs, the files it writes, what it puts back
(python3 -m unittest discover -s tests, in hotspot/). No network device,
kernel switch or service of the machine is touched: every command and
/proc write goes to the stand-in; hostapd is a `sleep`."""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from gi.repository import GLib  # noqa: F401
except ImportError:  # no PyGObject
    GLib = None

from duo2hotspot import conf, ctrl, state  # noqa: E402


def load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, os.path.join(ROOT, name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def data(name):
    with open(os.path.join(HERE, "data", name)) as f:
        return f.read()


ADDRS = [
    {
        "ifname": "wlan0",
        "addr_info": [
            {"family": "inet", "local": "192.0.2.50", "prefixlen": 24, "scope": "global"}
        ],
    },
    {
        "ifname": "usb0",
        "addr_info": [
            {"family": "inet", "local": "10.15.19.1", "prefixlen": 24, "scope": "global"}
        ],
    },
    {
        "ifname": "qmapmux0.0",
        "addr_info": [
            {"family": "inet", "local": "100.64.1.2", "prefixlen": 32, "scope": "global"},
            {"family": "inet6", "local": "2001:db8:1:2::5", "prefixlen": 64, "scope": "global"},
        ],
    },
]
LINKS = [
    {"ifname": "wlan0", "flags": ["UP", "LOWER_UP"], "operstate": "UP"},
    {"ifname": "qmapmux0.0", "flags": ["UP", "LOWER_UP"], "operstate": "UNKNOWN"},
]


class FakeSys:
    def __init__(
        self,
        files=None,
        connected=True,
        info=None,
        blocked=False,
        hostapd_dies=False,
        iface_add_rc=0,
    ):
        self.cmds = []
        self.files = dict(files or {})
        self.written = {}
        self.removed = []
        self.sysctls = {
            "/proc/sys/net/ipv4/conf/ap0/forwarding": "0",
            "/proc/sys/net/ipv6/conf/ap0/force_forwarding": "0",
            "/proc/sys/net/ipv4/conf/tether0/forwarding": "0",
            "/proc/sys/net/ipv6/conf/tether0/force_forwarding": "0",
            "/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding": "0",
            "/proc/sys/net/ipv6/conf/qmapmux0.0/force_forwarding": "0",
            "/proc/sys/net/ipv4/conf/all/rp_filter": "2",
            "/proc/sys/net/ipv4/conf/qmapmux0.0/rp_filter": "0",
        }
        self.connected = connected
        self.info = info if info is not None else data("iw-dev-info.txt")
        self.blocked = blocked
        self.rfkills = []
        self.hostapd_dies = hostapd_dies
        self.iface_add_rc = iface_add_rc
        self.links = list(LINKS)
        self.addrs = list(ADDRS)
        self.ss = ""
        self.procs = []
        self.clock = 0.0
        self.rules = set()
        self.table = False

    def run(self, argv, input=None, timeout=20):
        self.cmds.append((list(argv), input))
        a = list(argv)
        if a[:3] == ["iw", "phy", "phy0"] and a[3:] == ["info"]:
            return 0, data("iw-phy-info.txt"), ""
        if a == ["iw", "dev", "wlan0", "link"]:
            return 0, data("iw-link-connected.txt") if self.connected else "Not connected.\n", ""
        if a == ["iw", "dev", "wlan0", "info"]:
            return 0, self.info, ""
        if a[:5] == ["iw", "phy", "phy0", "interface", "add"]:
            if self.iface_add_rc == 0:
                self.files["/sys/class/net/" + a[5]] = ""
            return (
                self.iface_add_rc,
                "",
                "command failed: Device or resource busy (-16)" if self.iface_add_rc else "",
            )
        if a[:2] == ["iw", "dev"] and a[3:] == ["del"]:
            self.files.pop("/sys/class/net/" + a[2], None)
        if a[:3] == ["ip", "-4", "rule"] or a[:3] == ["ip", "-6", "rule"]:
            key = (a[1], a[a.index("iif") + 1])
            (self.rules.add if a[3] == "add" else self.rules.discard)(key)
        if a == ["nft", "-f", "-"]:
            self.table = "delete table inet duo2_hotspot\ntable inet duo2_hotspot {" in (
                input or ""
            )
        if a == ["nft", "list", "table", "inet", "duo2_hotspot"]:
            return (
                (0, "table inet duo2_hotspot {}", "")
                if self.table
                else (1, "", "No such file or directory")
            )
        if a == ["nft", "list", "table", "inet", "duo2_waydroid"]:
            return 1, "", "No such file or directory"
        if a[:2] == ["ss", "-Htn"]:
            return 0, self.ss, ""
        return 0, "", ""

    def json(self, argv):
        self.cmds.append((list(argv), None))
        a = list(argv)
        if a[:3] == ["ip", "-j", "addr"]:
            if a[3:] == ["show", "dev", "usb0"]:
                return [x for x in self.addrs if x["ifname"] == "usb0"]
            return self.addrs
        if a == ["ip", "-j", "link"]:
            return self.links
        if (
            a[:3] == ["ip", "-j", "-4"]
            and a[3:] == ["rule", "show"]
            or a[:3] == ["ip", "-j", "-6"]
            and a[3:] == ["rule", "show"]
        ):
            return [
                {"priority": 7240, "iif": i, "table": "7240"} for f, i in self.rules if f == a[2]
            ]
        if a == ["ip", "-j", "route", "show", "default"]:
            return [{"dst": "default", "dev": "wlan0", "gateway": "192.0.2.1"}]
        if a[:2] == ["networkctl", "--json=short"]:
            return {
                "DHCPServer": {
                    "PoolSize": 40,
                    "Leases": [
                        {
                            "Address": [10, 15, 43, 12],
                            "HardwareAddress": [2, 170, 0, 0, 0, 9],
                            "Hostname": "phone",
                        }
                    ],
                }
            }
        return []

    def read(self, path):
        return self.files.get(path)

    def write(self, path, text, mode=0o644):
        self.written[path] = (text, mode)
        self.files[path] = text

    def sysctl(self, path, value=None):
        if path not in self.sysctls:
            return None
        if value is not None:
            self.sysctls[path] = value
        return self.sysctls[path]

    def remove(self, path):
        self.removed.append(path)
        return self.files.pop(path, None) is not None

    def exists(self, path):
        return path in self.files or path.endswith("/hostapd") or "/hostapd/" in path

    def spawn(self, argv):
        self.cmds.append((list(argv), None))
        p = subprocess.Popen(
            ["true"] if self.hostapd_dies else ["sleep", "30"], stdin=subprocess.DEVNULL
        )
        if self.hostapd_dies:
            p.wait()
        self.procs.append(p)
        return p

    def sleep(self, s):
        self.clock += s

    def monotonic(self):
        return self.clock

    def now(self):
        return 1000000.0

    def rfkill_blocked(self, kind="wlan"):
        return self.blocked

    def rfkill(self, kind, block):
        self.rfkills.append((kind, block))
        self.blocked = block

    def ran(self, *prefix):
        return [c for c, _i in self.cmds if c[: len(prefix)] == list(prefix)]


class FakeCtrl:
    stations_list = []
    events = []  # what recv_event hands out (an exception is raised)

    def __init__(self, server, local, timeout=2.0):
        self.r, self.w = os.pipe()

    def request(self, cmd):
        return {"STATUS": "state=ENABLED\nchannel=36\n", "ATTACH": "OK\n"}.get(cmd, "FAIL\n")

    def stations(self):
        return list(self.stations_list)

    def fileno(self):
        return self.r

    def recv_event(self):
        if not FakeCtrl.events:
            return None
        e = FakeCtrl.events.pop(0)
        if isinstance(e, Exception):
            raise e
        return e

    def close(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeGadget:
    def __init__(self):
        self.added = None
        self.removed = False

    def present(self):
        return True

    def udc(self):
        return "a600000.usb"

    def add(self, dev, host):
        self.added = (dev, host)
        return True

    def remove(self):
        self.removed = True
        return True

    def netdev(self):
        return "tether0"


PASSWORD = "abcd-efgh-jkmn"


@unittest.skipIf(GLib is None, "no PyGObject")
class Daemon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = load("duo2-hotspotd", "duo2_hotspotd")
        self.saved = (state.STATE, state.WANT, self.d.CTRL_DIR, self.d.RUN, ctrl.Ctrl, self.d.SAVED)
        self.d.SAVED = os.path.join(self.tmp, "saved.json")
        state.STATE = os.path.join(self.tmp, "hotspot.json")
        state.WANT = os.path.join(self.tmp, "want.json")
        self.d.CTRL_DIR = os.path.join(self.tmp, "hostapd")
        self.d.RUN = self.tmp
        ctrl.Ctrl = FakeCtrl
        FakeCtrl.stations_list = []
        FakeCtrl.events = []

    def tearDown(self):
        state.STATE, state.WANT, self.d.CTRL_DIR, self.d.RUN, ctrl.Ctrl, self.d.SAVED = self.saved
        shutil.rmtree(self.tmp)

    def files(self, user="", admin=""):
        return {
            "/sys/class/net/wlan0/phy80211/name": "phy0\n",
            "/usr/sbin/hostapd": "",
            conf.SETTINGS: 'SSID="Duo test"\n' + user,
            conf.PASSWORD: PASSWORD + "\n",
            conf.ADMIN_CONF: admin,
            "/etc/machine-id": "0123456789abcdef0123456789abcdef\n",
        }

    def hotspot(self, fs, gadget=None):
        d = self.d

        class T(d.Hotspot):
            def inhibit(self):
                return os.open("/dev/null", os.O_RDONLY)

        self.quits = []
        return T(sysobj=fs, gadget=gadget or FakeGadget(), quit=lambda: self.quits.append(1))

    def want(self, **w):
        w.setdefault("request", "r1")
        state.write_json(state.WANT, w, 0o600)

    def st(self):
        with open(state.STATE) as f:
            return json.load(f)

    def stop(self, hs, fs):
        hs.shutdown()
        for p in fs.procs:
            if p.poll() is None:
                p.kill()
                p.wait()

    def test_beside_the_wifi_connection(self):
        fs = FakeSys(self.files())
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        st = self.st()
        self.assertEqual(st["state"], "on")
        self.assertEqual(st["result"], {"ok": True})
        self.assertEqual(
            (st["wifi"]["how"], st["wifi"]["channel"], st["wifi"]["width"]), ("shared", 36, 80)
        )
        self.assertEqual(st["wifi"]["client"], "kept")
        self.assertEqual(st["uplink"], {"iface": "qmapmux0.0", "ipv6": True})
        # the phone's own WiFi untouched
        self.assertEqual(fs.ran("wpa_cli"), [])
        self.assertEqual(fs.rfkills, [])
        self.assertEqual(
            fs.ran("iw", "phy", "phy0", "interface", "add"),
            [["iw", "phy", "phy0", "interface", "add", "ap0", "type", "__ap"]],
        )
        # networkd and udev files before the interface, hostapd's 0600
        order = [c for c, _i in fs.cmds]
        self.assertLess(
            order.index(["networkctl", "reload"]),
            order.index(["iw", "phy", "phy0", "interface", "add", "ap0", "type", "__ap"]),
        )
        text, mode = fs.written[self.d.HOSTAPD_CONF]
        self.assertEqual(mode, 0o600)
        self.assertIn("wpa_passphrase=" + PASSWORD, text)
        self.assertIn("channel=36", text)
        self.assertIn("/run/systemd/network/40-duo2-hotspot-ap0.network", fs.written)
        # the password never in the state file
        with open(state.STATE) as f:
            self.assertNotIn(PASSWORD, f.read())
        # firewall, policy routing, switches
        nft = [i for c, i in fs.cmds if c == ["nft", "-f", "-"]]
        self.assertTrue(nft and '"ap0"' in nft[-1] and '"qmapmux0.0"' in nft[-1])
        self.assertIn(
            ["ip", "-4", "rule", "add", "pref", "7240", "iif", "ap0", "lookup", "7240"],
            [c for c, _i in fs.cmds],
        )
        self.assertIn(
            [
                "ip",
                "-4",
                "route",
                "replace",
                "default",
                "dev",
                "qmapmux0.0",
                "metric",
                "1",
                "table",
                "7240",
            ],
            [c for c, _i in fs.cmds],
        )
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv4/conf/ap0/forwarding"], "1")
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv6/conf/qmapmux0.0/force_forwarding"], "1")
        self.assertIsNotNone(hs.inhibit_fd)
        self.stop(hs, fs)
        # everything back
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding"], "0")
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv6/conf/qmapmux0.0/force_forwarding"], "0")
        self.assertIn(["iw", "dev", "ap0", "del"], [c for c, _i in fs.cmds])
        self.assertIn(
            ["ip", "-4", "rule", "del", "pref", "7240", "iif", "ap0", "lookup", "7240"],
            [c for c, _i in fs.cmds],
        )
        self.assertIn(
            "delete table inet duo2_hotspot", [i for c, i in fs.cmds if c == ["nft", "-f", "-"]][-1]
        )
        for path in (
            "/run/systemd/network/40-duo2-hotspot-ap0.network",
            "/run/systemd/network/05-duo2-hotspot.link",
            self.d.HOSTAPD_CONF,
        ):
            self.assertIn(path, fs.removed)
        self.assertEqual(fs.ran("wpa_cli"), [])
        self.assertEqual(self.st()["state"], "off")
        self.assertIsNone(hs.inhibit_fd)

    def test_never_cut_by_default(self):
        radar = "\tchannel 52 (5260 MHz), width: 80 MHz, center1: 5290 MHz\n"
        fs = FakeSys(self.files(admin="OTHER_CHANNEL=no\n"), info=radar)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        st = self.st()
        self.assertEqual(st["result"]["code"], "channel-not-shared")
        self.assertEqual(fs.ran("iw", "phy", "phy0", "interface"), [])
        self.assertEqual(fs.ran("wpa_cli"), [])
        self.assertEqual(self.quits, [1])
        self.assertFalse(state.read_json(state.WANT)["wifi"])
        # asked to cut, but development mode never does
        self.want(wifi=True, wifi_off=True, request="r2")
        hs.reconcile()
        self.assertEqual(self.st()["result"]["code"], "cut-not-allowed")
        self.assertEqual(fs.ran("wpa_cli"), [])

    def test_cut_when_the_user_lets_it(self):
        radar = "\tchannel 52 (5260 MHz), width: 80 MHz, center1: 5290 MHz\n"
        fs = FakeSys(
            self.files(user="WIFI_CLIENT=ask\n", admin="NEVER_CUT_WIFI=no\nOTHER_CHANNEL=no\n"),
            info=radar,
        )
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.assertEqual(self.st()["result"]["code"], "needs-wifi-off")
        self.want(wifi=True, wifi_off=True, request="r2")
        hs.reconcile()
        st = self.st()
        self.assertTrue(st["result"]["ok"])
        self.assertEqual((st["wifi"]["how"], st["wifi"]["client"]), ("own", "cut"))
        self.assertEqual(fs.ran("wpa_cli"), [["wpa_cli", "-i", "wlan0", "disconnect"]])
        self.stop(hs, fs)
        self.assertEqual(fs.ran("wpa_cli")[-1], ["wpa_cli", "-i", "wlan0", "reconnect"])

    def test_wifi_switched_off_stays_off_as_a_client(self):
        fs = FakeSys(self.files(), blocked=True)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        st = self.st()
        self.assertEqual((st["wifi"]["how"], st["wifi"]["client"]), ("own", "radio"))
        self.assertEqual(fs.rfkills, [("wlan", False)])
        self.assertEqual(fs.ran("wpa_cli"), [["wpa_cli", "-i", "wlan0", "disconnect"]])
        self.stop(hs, fs)
        self.assertEqual(fs.rfkills, [("wlan", False), ("wlan", True)])
        self.assertEqual(fs.ran("wpa_cli")[-1], ["wpa_cli", "-i", "wlan0", "reconnect"])

    def test_wifi_client_back_beside(self):
        fs = FakeSys(self.files(), blocked=True)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.want(wifi=True, client_on="c1", request="r2")
        hs.reconcile()
        self.assertEqual(fs.ran("wpa_cli")[-1], ["wpa_cli", "-i", "wlan0", "reconnect"])
        self.assertIsNone(hs.wifi["hold"])
        self.stop(hs, fs)
        self.assertEqual(fs.rfkills, [("wlan", False)])  # not switched off again: the user wants it

    def test_airplane(self):
        files = self.files()
        files["/var/lib/duo2/airplane"] = "on\n"
        fs = FakeSys(files)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.assertEqual(self.st()["result"]["code"], "airplane")
        self.assertEqual(fs.ran("iw", "phy", "phy0", "interface"), [])

    def test_hostapd_fails_everything_undone(self):
        fs = FakeSys(self.files(), hostapd_dies=True)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        st = self.st()
        self.assertEqual(st["result"]["code"], "concurrent-failed")
        self.assertIn(["iw", "dev", "ap0", "del"], [c for c, _i in fs.cmds])
        self.assertIn("/run/systemd/network/40-duo2-hotspot-ap0.network", fs.removed)
        self.assertIn(self.d.HOSTAPD_CONF, fs.removed)
        self.assertEqual([c for c, _i in fs.cmds if c == ["nft", "-f", "-"]], [])
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding"], "0")
        self.assertIsNone(hs.wifi)

    def test_interface_busy(self):
        fs = FakeSys(self.files(), iface_add_rc=240)
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.assertEqual(self.st()["result"]["code"], "concurrent-failed")
        self.assertEqual(fs.procs, [])

    def test_settings_change_restarts_the_access_point(self):
        fs = FakeSys(self.files())
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        first = hs.wifi["proc"]
        fs.files[conf.SETTINGS] = 'SSID="Another"\n'
        self.want(wifi=True, request="r2")
        hs.reconcile()
        self.assertIsNot(hs.wifi["proc"], first)
        self.assertIsNotNone(first.poll())
        self.assertIn("ssid2=" + b"Another".hex(), fs.written[self.d.HOSTAPD_CONF][0])
        self.stop(hs, fs)

    def test_mobile_data_goes_and_comes(self):
        fs = FakeSys(self.files())
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        fs.addrs = [a for a in ADDRS if a["ifname"] != "qmapmux0.0"]
        fs.cmds = []
        hs.after_change()
        self.assertIsNone(self.st()["uplink"])
        self.assertIn(["ip", "-4", "route", "flush", "table", "7240"], [c for c, _i in fs.cmds])
        self.assertNotIn("qmapmux", [i for c, i in fs.cmds if c == ["nft", "-f", "-"]][-1])
        self.assertEqual(
            [c for c, _i in fs.cmds if c[:3] == ["ip", "-4", "route"] and "dev" in c], []
        )
        fs.addrs = list(ADDRS)
        hs.after_change()
        self.assertEqual(self.st()["uplink"]["iface"], "qmapmux0.0")
        self.stop(hs, fs)

    def test_clients_and_5g(self):
        fs = FakeSys(self.files())
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.assertFalse(self.st()["use_5g"])
        self.assertIsNotNone(self.st()["idle_off_at"])
        FakeCtrl.stations_list = [
            {"mac": "02:aa:00:00:00:09", "authorized": True, "connected_time": 5}
        ]
        hs.refresh_stations()
        st = self.st()
        self.assertEqual(st["client_count"], 1)
        self.assertEqual(st["clients"][0]["name"], "phone")
        self.assertTrue(st["use_5g"])
        self.assertIsNone(st["idle_off_at"])
        self.assertNotIn("02:aa", json.dumps(st))
        self.stop(hs, fs)

    def test_a_device_joins_through_hostapds_events(self):
        fs = FakeSys(self.files())
        hs = self.hotspot(fs)
        self.want(wifi=True)
        hs.reconcile()
        self.assertEqual(self.st()["client_count"], 0)
        self.assertIn("poll", hs.wifi["watches"])
        FakeCtrl.stations_list = [
            {"mac": "02:aa:00:00:00:09", "authorized": True, "connected_time": 2}
        ]
        FakeCtrl.events = [
            "<3>AP-STA-CONNECTED 02:aa:00:00:00:09",
            "<3>EAPOL-4WAY-HS-COMPLETED 02:aa:00:00:00:09",
        ]
        self.assertTrue(hs._hostapd_event(hs.wifi["events"].fileno(), 0))  # the watch stays
        st = self.st()
        self.assertEqual(st["client_count"], 1)
        self.assertEqual(st["clients"][0]["ip"], "10.15.43.12")
        self.assertEqual(len(hs.lease_timers), 3)  # the lease and its name, later
        # the periodic read writes only a change
        os.remove(state.STATE)
        self.assertTrue(hs._stations_poll())
        self.assertFalse(os.path.exists(state.STATE))
        FakeCtrl.stations_list = []
        self.assertTrue(hs._stations_poll())
        self.assertEqual(self.st()["client_count"], 0)
        # a broken socket: the event watch goes, the poll stays
        FakeCtrl.events = [OSError("connection refused")]
        self.assertFalse(hs._hostapd_event(hs.wifi["events"].fileno(), 0))
        self.assertNotIn("events", hs.wifi["watches"])
        self.assertIn("poll", hs.wifi["watches"])
        self.stop(hs, fs)

    def test_cleanup_after_a_crash(self):
        fs = FakeSys(self.files(user="WIFI_CLIENT=ask\n", admin="NEVER_CUT_WIFI=no\n"))
        g = FakeGadget()
        g.active = lambda: g.added is not None and not g.removed
        hs = self.hotspot(fs, g)
        self.want(wifi=True, wifi_off=True, usb=True, usb_force=True)
        hs.reconcile()
        self.assertEqual(self.st()["wifi"]["client"], "cut")
        self.assertTrue(fs.table and fs.rules and "/sys/class/net/ap0" in fs.files)
        # the daemon dies here: nothing of shutdown() runs
        for p in fs.procs:
            p.kill()
            p.wait()
        did = self.d.cleanup(fs, g)
        self.assertEqual(set(did), {"table", "rules", "ap0", "usb", "switches", "wifi client"})
        self.assertFalse(fs.table)
        self.assertEqual(fs.rules, set())
        self.assertNotIn("/sys/class/net/ap0", fs.files)
        self.assertTrue(g.removed)
        self.assertEqual(fs.sysctls["/proc/sys/net/ipv4/conf/qmapmux0.0/forwarding"], "0")
        self.assertEqual(fs.ran("wpa_cli")[-1], ["wpa_cli", "-i", "wlan0", "reconnect"])
        for path in (
            "/run/systemd/network/40-duo2-hotspot-ap0.network",
            "/run/systemd/network/40-duo2-hotspot-tether0.network",
        ):
            self.assertNotIn(path, fs.files)
        self.assertEqual(self.st()["state"], "off")
        # and once more: nothing left to do
        self.assertEqual(self.d.cleanup(fs, g), [])
        hs.shutdown()  # (its GLib watches of the dead hostapd)

    def test_clean_stop_leaves_nothing_to_clean(self):
        fs = FakeSys(self.files())
        g = FakeGadget()
        g.active = lambda: g.added is not None and not g.removed
        hs = self.hotspot(fs, g)
        self.want(wifi=True)
        hs.reconcile()
        self.stop(hs, fs)
        self.assertEqual(self.d.cleanup(fs, g), [])

    def test_usb_guards_the_ssh_session(self):
        fs = FakeSys(self.files())
        fs.ss = "0 0 10.15.19.1:22 10.15.19.12:53422\n"
        g = FakeGadget()
        hs = self.hotspot(fs, g)
        self.want(usb=True)
        hs.reconcile()
        self.assertEqual(self.st()["result"]["code"], "usb-ssh")
        self.assertIsNone(g.added)
        self.want(usb=True, usb_force=True, request="r2")
        hs.reconcile()
        st = self.st()
        self.assertTrue(st["result"]["ok"])
        self.assertEqual(st["usb"]["iface"], "tether0")
        self.assertIsNotNone(g.added)
        nft = [i for c, i in fs.cmds if c == ["nft", "-f", "-"]][-1]
        self.assertIn('"tether0"', nft)
        self.assertIn("10.15.44.0/24", nft)
        self.stop(hs, fs)
        self.assertTrue(g.removed)


if __name__ == "__main__":
    unittest.main()
