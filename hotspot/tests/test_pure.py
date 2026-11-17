"""The hotspot's pure parts: settings, iw's output, the channel plan,
hostapd's configuration, the network plan, the state file
(python3 -m unittest discover -s tests, in hotspot/)."""

import ipaddress
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duo2hotspot import conf, ctrl, gadget, netplan, state, wifi  # noqa: E402


def data(name):
    with open(os.path.join(HERE, "data", name)) as f:
        return f.read()


PHY = wifi.parse_phy(data("iw-phy-info.txt"))


class Settings(unittest.TestCase):
    def test_kv_round_trip(self):
        text = '# comment\nSSID="My Duo"\nBAND=5\n'
        kv = conf.parse_kv(text)
        self.assertEqual(kv, {"SSID": "My Duo", "BAND": "5"})
        text = conf.set_kv(text, "BAND", "2.4")
        text = conf.set_kv(text, "SECURITY", "wpa3")
        self.assertEqual(conf.parse_kv(text), {"SSID": "My Duo", "BAND": "2.4", "SECURITY": "wpa3"})
        self.assertIn("# comment", text)
        # a name with a quote at its start keeps it
        text = conf.set_kv("", "SSID", '"quoted" net')
        self.assertEqual(conf.parse_kv(text)["SSID"], '"quoted" net')

    def test_merged_keeps_only_known_keys(self):
        m = conf.merged(conf.USER_DEFAULTS, {"BAND": "2.4", "EVIL": "x"})
        self.assertEqual(m["BAND"], "2.4")
        self.assertNotIn("EVIL", m)

    def test_ssid(self):
        self.assertTrue(conf.valid_ssid("Surface Duo 2 0042"))
        self.assertTrue(conf.valid_ssid("Café Zoé"))
        self.assertFalse(conf.valid_ssid(""))
        self.assertFalse(conf.valid_ssid("   "))
        self.assertFalse(conf.valid_ssid("a" * 33))
        self.assertFalse(conf.valid_ssid("é" * 17))  # 34 bytes
        self.assertFalse(conf.valid_ssid("bad\nname"))
        self.assertFalse(conf.valid_ssid(None))

    def test_password(self):
        rng = random.Random(1)
        pw = conf.gen_password(rng)
        self.assertRegex(pw, r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")
        self.assertTrue(conf.valid_password(pw))
        for bad in ("short", "x" * 64, "tab\there1", "éééééééé", None):
            self.assertFalse(conf.valid_password(bad), bad)
        self.assertNotIn("0", conf.PASSWORD_ALPHABET)
        self.assertNotIn("l", conf.PASSWORD_ALPHABET)

    def test_validate(self):
        self.assertEqual(conf.validate("BAND", "2.4GHz"), "2.4")
        self.assertEqual(conf.validate("BAND", "5"), "5")
        self.assertEqual(conf.validate("IDLE_OFF_MIN", "010"), "10")
        self.assertEqual(conf.validate("USE_5G", "YES"), "yes")
        for key, bad in (
            ("BAND", "6"),
            ("SECURITY", "open"),
            ("WIFI_CLIENT", "cut"),
            ("IDLE_OFF_MIN", "-1"),
            ("IDLE_OFF_MIN", "999"),
            ("SSID", ""),
            ("NOPE", "x"),
        ):
            with self.assertRaises(ValueError):
                conf.validate(key, bad)

    def test_subnets_and_ula(self):
        nets = conf.subnets("10.15.43.0/24 garbage 192.0.2.1/24 192.168.43.0/24 198.18.0.0/15")
        self.assertEqual([str(n) for n in nets], ["10.15.43.0/24", "192.168.43.0/24"])
        ula = conf.ula_from_seed(bytes.fromhex("0102030405"))
        self.assertEqual(str(ula), "fd01:203:405::/48")
        self.assertTrue(ula.is_private)


class Iw(unittest.TestCase):
    def test_phy(self):
        self.assertIn("AP", PHY["modes"])
        self.assertIn("P2P-device", PHY["modes"])
        b24, b5 = PHY["bands"]["2.4"], PHY["bands"]["5"]
        self.assertTrue(b24["ht40"] and b24["he_ap"] and not b24["vht"])
        self.assertTrue(b5["ht40"] and b5["vht"] and b5["he_ap"])
        self.assertEqual(
            b5["channels"][5180], {"chan": 36, "disabled": False, "no_ir": False, "radar": False}
        )
        self.assertTrue(b5["channels"][5260]["radar"])
        self.assertTrue(b5["channels"][5745]["no_ir"])
        self.assertTrue(b24["channels"][2484]["disabled"])
        self.assertEqual(PHY["share_channels"], 2)

    def test_link_and_info(self):
        self.assertEqual(
            wifi.parse_link(data("iw-link-connected.txt")), {"connected": True, "freq": 5180}
        )
        self.assertEqual(wifi.parse_link("Not connected.\n"), {"connected": False, "freq": None})
        self.assertEqual(
            wifi.parse_dev_info(data("iw-dev-info.txt")),
            {"freq": 5180, "width": 80, "center1": 5210},
        )
        self.assertEqual(
            wifi.parse_dev_info("\tchannel 6 (2437 MHz), width: 20 MHz (no HT), center1: 2437 MHz"),
            {"freq": 2437, "width": 20, "center1": 2437},
        )
        self.assertIsNone(wifi.parse_dev_info("Interface wlan0\n\ttype managed\n"))

    def test_nothing_identifying_kept(self):
        parsed = repr(wifi.parse_link(data("iw-link-connected.txt"))) + repr(
            wifi.parse_dev_info(data("iw-dev-info.txt"))
        )
        self.assertNotIn("ExampleNet", parsed)
        self.assertNotIn("02:00:00", parsed)

    def test_channels(self):
        self.assertEqual(wifi.chan_of(2412), 1)
        self.assertEqual(wifi.chan_of(2484), 14)
        self.assertEqual(wifi.chan_of(5180), 36)
        self.assertEqual(wifi.freq_of("5", 149), 5745)
        self.assertEqual(wifi.freq_of("2.4", 6), 2437)
        self.assertTrue(wifi.lower_of_pair(36) and not wifi.lower_of_pair(40))
        self.assertTrue(wifi.lower_of_pair(149) and not wifi.lower_of_pair(153))
        self.assertEqual(wifi.block_80(44), (36, 42))
        self.assertEqual(wifi.block_80(161), (149, 155))


class Plan(unittest.TestCase):
    def test_chandef_80(self):
        cd = wifi.chandef(PHY, 5180, 80, 5210)
        self.assertEqual(
            cd, {"band": "5", "freq": 5180, "chan": 36, "width": 80, "ht40": "+", "seg0": 42}
        )
        cd = wifi.chandef(PHY, 5200, 80)
        self.assertEqual((cd["width"], cd["ht40"], cd["seg0"]), (80, "-", 42))

    def test_chandef_narrows(self):
        # 52 is a radar channel: not at all.
        self.assertIsNone(wifi.chandef(PHY, 5260, 80))
        # 2.4 GHz never wider than 40; 40 needs the other half.
        cd = wifi.chandef(PHY, 2437, 80, 2447)
        self.assertEqual((cd["width"], cd["ht40"]), (40, "+"))
        cd = wifi.chandef(PHY, 2462, 40, 2452)
        self.assertEqual((cd["width"], cd["ht40"]), (40, "-"))
        # 13 is no-IR: 11 with HT40+ would need it, so 20 MHz.
        cd = wifi.chandef(PHY, 2462, 40, 2472)
        self.assertEqual(cd["width"], 20)

    def test_plan_without_client(self):
        cd, how = wifi.plan_channel(PHY, "5", None, "36", True, [("2.4", "6")])
        self.assertEqual((how, cd["chan"], cd["width"]), ("own", 36, 80))
        cd, how = wifi.plan_channel(PHY, "2.4", None, "6", True, [("5", "36")])
        self.assertEqual((how, cd["chan"], cd["width"]), ("own", 6, 20))
        # a channel that may not be used: the alternative
        cd, how = wifi.plan_channel(PHY, "5", None, "149", True, [("2.4", "6")])
        self.assertEqual((cd["band"], cd["chan"]), ("2.4", 6))

    def test_plan_beside_client(self):
        sta = {"freq": 5180, "width": 80, "center1": 5210}
        cd, how = wifi.plan_channel(PHY, "2.4", sta, "6", True, [("5", "36")])
        # the client's channel wins over the band choice (one radio)
        self.assertEqual((how, cd["chan"], cd["width"]), ("shared", 36, 80))
        radar = {"freq": 5260, "width": 80, "center1": 5290}
        cd, how = wifi.plan_channel(PHY, "5", radar, "36", True, [("2.4", "6")])
        self.assertEqual((how, cd["chan"]), ("second", 36))
        cd, how = wifi.plan_channel(PHY, "5", radar, "36", False, [("2.4", "6")])
        self.assertEqual((cd, how), (None, "channel"))
        one = dict(PHY, share_channels=1)
        self.assertEqual(wifi.plan_channel(one, "5", radar, "36", True, []), (None, "channel"))
        none = dict(PHY, share_channels=0)
        self.assertEqual(
            wifi.plan_channel(none, "5", sta, "36", True, []), (None, "no-concurrency")
        )


class Hostapd(unittest.TestCase):
    def conf(self, security="wpa2-wpa3", cd=None):
        cd = cd or wifi.chandef(PHY, 5180, 80, 5210)
        return wifi.hostapd_conf(
            "ap0", "/run/duo2-hotspot/hostapd", "Café Duo", "abcd-efgh-jkmn", security, cd, PHY, 8
        )

    def test_5ghz_80(self):
        text = self.conf()
        lines = dict(
            l.split("=", 1) for l in text.splitlines() if "=" in l and not l.startswith("#")
        )
        self.assertEqual(lines["interface"], "ap0")
        self.assertEqual(bytes.fromhex(lines["ssid2"]).decode(), "Café Duo")
        self.assertEqual(lines["hw_mode"], "a")
        self.assertEqual(lines["channel"], "36")
        self.assertEqual(lines["ht_capab"], "[HT40+]")
        self.assertEqual(lines["vht_oper_chwidth"], "1")
        self.assertEqual(lines["vht_oper_centr_freq_seg0_idx"], "42")
        self.assertEqual(lines["he_oper_centr_freq_seg0_idx"], "42")
        self.assertEqual(lines["wpa_key_mgmt"], "WPA-PSK SAE")
        self.assertEqual(lines["ieee80211w"], "1")
        self.assertEqual(lines["sae_pwe"], "2")
        self.assertEqual(lines["max_num_sta"], "8")
        self.assertEqual(lines["wpa_passphrase"], "abcd-efgh-jkmn")
        self.assertNotIn("country_code", lines)

    def test_security_variants(self):
        wpa3 = self.conf("wpa3")
        self.assertIn("wpa_key_mgmt=SAE\n", wpa3)
        self.assertIn("ieee80211w=2\n", wpa3)
        wpa2 = self.conf("wpa2", wifi.chandef(PHY, 2437, 20))
        self.assertIn("wpa_key_mgmt=WPA-PSK\n", wpa2)
        self.assertIn("ieee80211w=0\n", wpa2)
        self.assertNotIn("sae_", wpa2)
        self.assertIn("hw_mode=g\n", wpa2)
        self.assertNotIn("ieee80211ac", wpa2)
        self.assertNotIn("ht_capab", wpa2)

    def test_events(self):
        self.assertEqual(
            wifi.parse_event("<3>AP-STA-CONNECTED 02:AA:00:00:00:09 auth_alg=sae"),
            ("AP-STA-CONNECTED", "02:aa:00:00:00:09"),
        )
        self.assertEqual(wifi.parse_event("<3>AP-ENABLED "), ("AP-ENABLED", None))
        self.assertEqual(wifi.parse_event("OK"), ("OK", None))
        self.assertEqual(wifi.parse_event(""), (None, None))

    def test_sta(self):
        reply = (
            "02:aa:00:00:00:09\nflags=[AUTH][ASSOC][AUTHORIZED]\nconnected_time=42\nrx_bytes=10\n"
        )
        self.assertEqual(
            wifi.parse_sta(reply),
            {
                "mac": "02:aa:00:00:00:09",
                "authorized": True,
                "connected_time": 42,
                "rx_bytes": 10,
                "tx_bytes": 0,
            },
        )
        self.assertIsNone(wifi.parse_sta("FAIL\n"))
        self.assertIsNone(wifi.parse_sta(""))

    def test_log_lines_carry_no_address(self):
        line = "ap0: STA 02:aa:00:00:00:09 IEEE 802.11: associated (aid 1)"
        a, b = wifi.mask_macs(line, b"s1"), wifi.mask_macs(line, b"s1")
        self.assertEqual(a, b)
        self.assertNotIn("02:aa", a)
        self.assertNotEqual(a, wifi.mask_macs(line, b"s2"))


class Net(unittest.TestCase):
    ADDRS = [
        {
            "ifname": "lo",
            "addr_info": [
                {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}
            ],
        },
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
                {"family": "inet", "local": "10.15.43.77", "prefixlen": 32, "scope": "global"},
                {"family": "inet6", "local": "2001:db8:1:2::5", "prefixlen": 64, "scope": "global"},
            ],
        },
    ]
    LINKS = [
        {"ifname": "lo", "flags": ["LOOPBACK", "UP"]},
        {"ifname": "wlan0", "flags": ["UP", "LOWER_UP"], "operstate": "UP"},
        {"ifname": "rmnet_ipa0", "flags": ["UP"], "operstate": "UNKNOWN"},
        {"ifname": "qmapmux0.0", "flags": ["UP", "LOWER_UP"], "operstate": "UNKNOWN"},
        {"ifname": "qmapmux0.1", "flags": ["UP"], "operstate": "UNKNOWN"},
    ]

    def test_subnet_avoids_what_is_used(self):
        used = netplan.used_networks(
            self.ADDRS, [{"dst": "192.168.43.0/24", "dev": "x"}, {"dst": "default"}]
        )
        cands = conf.subnets(conf.ADMIN_DEFAULTS["SUBNETS_WIFI"])
        # 10.15.43.0/24 has the carrier's address in it, 192.168.43.0/24 a route
        self.assertEqual(str(netplan.pick_subnet(cands, used)), "172.20.43.0/24")
        self.assertEqual(str(netplan.pick_subnet(cands, [])), "10.15.43.0/24")
        self.assertEqual(
            str(netplan.pick_subnet(cands, [], [ipaddress.ip_network("10.15.43.0/24")])),
            "192.168.43.0/24",
        )
        self.assertIsNone(netplan.pick_subnet(cands[:1], used))

    def test_downstream_and_network_file(self):
        ula = conf.ula_from_seed(bytes.fromhex("0102030405"))
        d = netplan.downstream("ap0", ipaddress.ip_network("10.15.43.0/24"), ula, 1)
        self.assertEqual(str(d["addr4"]), "10.15.43.1")
        self.assertEqual(str(d["net6"]), "fd01:203:405:1::/64")
        self.assertEqual(str(d["addr6"]), "fd01:203:405:1::1")
        text = netplan.network_file(d)
        for line in (
            "Name=ap0",
            "Address=10.15.43.1/24",
            "Address=fd01:203:405:1::1/64",
            "DHCPServer=yes",
            "IPv6SendRA=yes",
            "DNS=10.15.43.1",
            "DNS=fd01:203:405:1::1",
            "Prefix=fd01:203:405:1::/64",
            "EmitRouter=yes",
            "LLMNR=no",
        ):
            self.assertIn(line + "\n", text)
        self.assertNotIn("IPForward", text)  # forwarding is per device, ours (sysctls)
        self.assertNotIn("IPMasquerade", text)
        d4 = netplan.downstream("tether0", ipaddress.ip_network("10.15.44.0/24"), None, 2)
        text = netplan.network_file(d4)
        self.assertIn("IPv6SendRA=no\n", text)
        self.assertNotIn("[IPv6Prefix]", text)

    def test_link_file(self):
        text = netplan.link_file("ap0")
        self.assertIn("OriginalName=ap0\n", text)
        self.assertIn("NamePolicy=keep\n", text)
        self.assertIn("MACAddressPolicy=none\n", text)
        self.assertTrue(
            netplan.LINK_FILE.startswith("/run/")
            and os.path.basename(netplan.LINK_FILE) < "10-wifi.link"
        )

    def test_nft(self):
        ula = conf.ula_from_seed(bytes.fromhex("0102030405"))
        d = netplan.downstream("ap0", ipaddress.ip_network("10.15.43.0/24"), ula, 1)
        text = netplan.nft_ruleset([d], ["qmapmux0.0"])
        self.assertIn('elements = { "ap0" }', text)
        self.assertIn('elements = { "qmapmux0.0" }', text)
        self.assertIn("ip saddr { 10.15.43.0/24 } masquerade", text)
        self.assertIn("ip6 saddr { fd01:203:405:1::/64 } masquerade", text)
        self.assertIn("meta mark set meta mark | 0x10000000 accept", text)
        self.assertIn("delete table inet duo2_hotspot", text)
        empty = netplan.nft_ruleset([d], [])
        self.assertNotIn("qmapmux", empty)

    def test_waydroid_table_knows_the_mark(self):
        path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "android", "firewall.nft")
        with open(path) as f:
            text = f.read()
        self.assertIn("0x%08x" % netplan.MARK, text)

    def test_counters(self):
        j = {
            "nftables": [
                {"metainfo": {}},
                {"counter": {"name": "shared_out", "bytes": 123}},
                {"counter": {"name": "shared_in", "bytes": 4567}},
            ]
        }
        self.assertEqual(netplan.parse_counters(j), {"out": 123, "in": 4567})

    def test_uplink(self):
        routes = [{"dst": "default", "dev": "wlan0"}]
        self.assertEqual(
            netplan.find_uplink(self.LINKS, self.ADDRS, routes, "qmapmux* rmnet_data*"),
            "qmapmux0.0",
        )
        self.assertTrue(netplan.has_v6(self.ADDRS, "qmapmux0.0"))
        self.assertFalse(netplan.has_v6(self.ADDRS, "wlan0"))
        down = [
            dict(l, flags=["NO-CARRIER"]) if l["ifname"] == "qmapmux0.0" else l for l in self.LINKS
        ]
        self.assertIsNone(netplan.find_uplink(down, self.ADDRS, routes, "qmapmux*"))
        self.assertIsNone(netplan.find_uplink(self.LINKS, self.ADDRS, routes, "wwan*"))

    def test_rules_and_table(self):
        self.assertEqual(
            netplan.rule_cmds("ap0")[0],
            ["ip", "-4", "rule", "add", "pref", "7240", "iif", "ap0", "lookup", "7240"],
        )
        rules = [
            {"priority": 7240, "iif": "ap0", "table": "7240"},
            {"priority": 0, "table": "local"},
        ]
        self.assertTrue(netplan.rules_present(rules, "ap0"))
        self.assertFalse(netplan.rules_present(rules, "tether0"))
        cmds = netplan.table_cmds("qmapmux0.0", True)
        self.assertIn(
            [
                "ip",
                "-6",
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
            cmds,
        )
        self.assertEqual(len(netplan.table_cmds(None, False)), 2)  # only the unreachable defaults

    def test_sysctls(self):
        d = {"iface": "ap0"}
        s = dict(netplan.sysctls([d], "qmapmux0.0"))
        self.assertEqual(s["/proc/sys/net/ipv4/conf/ap0/forwarding"], "1")
        self.assertEqual(s["/proc/sys/net/ipv6/conf/qmapmux0.0/force_forwarding"], "1")
        self.assertFalse(any("/all/" in p for p in s))  # never the global switches
        self.assertEqual(netplan.rp_filter_fix("2", "1"), None)
        self.assertEqual(netplan.rp_filter_fix("1", "0"), "2")
        self.assertEqual(netplan.rp_filter_fix("0", "1"), "2")
        self.assertEqual(netplan.rp_filter_fix("0", "0"), None)


class State(unittest.TestCase):
    def test_leases(self):
        j = {
            "Interfaces": [
                {
                    "Name": "ap0",
                    "DHCPServer": {
                        "PoolOffset": 10,
                        "PoolSize": 40,
                        "Leases": [
                            {
                                "ClientId": [1, 2, 170, 0, 0, 0, 9],
                                "Address": [10, 15, 43, 12],
                                "Hostname": "pixel",
                            },
                            {"Address": "10.15.43.13", "HardwareAddress": [2, 170, 0, 0, 0, 10]},
                            {"Address": "bogus"},
                        ],
                    },
                }
            ]
        }
        self.assertEqual(
            state.parse_leases(j),
            [
                {"ip": "10.15.43.12", "mac": "02:aa:00:00:00:09", "name": "pixel"},
                {"ip": "10.15.43.13", "mac": "02:aa:00:00:00:0a", "name": None},
            ],
        )
        self.assertEqual(state.parse_leases(None), [])

    def test_leases_systemd_257(self):
        # systemd 257 (the phone's): the DHCP message's whole 16-byte chaddr
        # with its length beside it, and a client id that is not the MAC
        # (a DUID-style id: type 255), so only the chaddr can match the station
        chaddr = [2, 170, 0, 0, 0, 9] + [0] * 10
        j = {
            "Interfaces": [
                {
                    "Name": "ap0",
                    "DHCPServer": {
                        "PoolOffset": 10,
                        "PoolSize": 40,
                        "Leases": [
                            {
                                "ClientId": [255, 1, 2, 3, 4, 0, 1, 0, 1],
                                "Address": [10, 15, 43, 12],
                                "Hostname": "made-up-phone",
                                "HardwareAddressType": 1,
                                "HardwareAddressLength": 6,
                                "HardwareAddress": chaddr,
                                "ExpirationUSec": 123,
                            }
                        ],
                    },
                }
            ]
        }
        self.assertEqual(
            state.parse_leases(j),
            [{"ip": "10.15.43.12", "mac": "02:aa:00:00:00:09", "name": "made-up-phone"}],
        )
        cl = state.clients(
            [{"mac": "02:aa:00:00:00:09", "authorized": True, "connected_time": 30}],
            {"wifi": state.parse_leases(j)},
            1000,
        )
        self.assertEqual(cl[0]["name"], "made-up-phone")
        self.assertEqual(cl[0]["ip"], "10.15.43.12")

    def test_same_clients_ignores_since(self):
        a = [{"via": "wifi", "name": "x", "ip": "10.15.43.12", "since": 970}]
        b = [{"via": "wifi", "name": "x", "ip": "10.15.43.12", "since": 971}]
        self.assertTrue(state.same_clients(a, b))
        self.assertFalse(state.same_clients(a, [dict(b[0], ip=None)]))
        self.assertFalse(state.same_clients(a, []))
        self.assertTrue(state.same_clients([], None))

    def test_clients_carry_no_address(self):
        stations = [
            {"mac": "02:aa:00:00:00:09", "authorized": True, "connected_time": 30},
            {"mac": "02:aa:00:00:00:0b", "authorized": False, "connected_time": 1},
        ]
        leases = {
            "wifi": [{"ip": "10.15.43.12", "mac": "02:aa:00:00:00:09", "name": "pixel\x07"}],
            "usb": [{"ip": "10.15.44.10", "mac": "06:00:00:00:00:01", "name": None}],
        }
        cl = state.clients(stations, leases, 1000)
        self.assertEqual(
            cl,
            [
                {"via": "wifi", "name": "pixel", "ip": "10.15.43.12", "since": 970},
                {"via": "usb", "name": None, "ip": "10.15.44.10", "since": None},
            ],
        )
        self.assertNotIn("02:aa", repr(cl))

    def test_json_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "s.json")
            state.write_json(p, {"a": 1}, 0o600)
            self.assertEqual(state.read_json(p), {"a": 1})
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            self.assertEqual(state.read_json(os.path.join(d, "none")), {})


class CtrlEvents(unittest.TestCase):
    """hostapd's attached control socket, with a real UNIX datagram socket
    standing in for hostapd."""

    def test_recv_event_never_waits(self):
        import socket
        import time

        d = tempfile.mkdtemp()
        try:
            server_path = os.path.join(d, "ap0")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            server.bind(server_path)
            c = ctrl.Ctrl(server_path, d, timeout=2.0)
            self.assertIsNone(c.recv_event())
            server.sendto(b"<3>AP-STA-CONNECTED 02:aa:00:00:00:09", c.local)
            server.sendto(b"<3>EAPOL-4WAY-HS-COMPLETED 02:aa:00:00:00:09", c.local)
            t0 = time.monotonic()
            got = []
            while True:
                msg = c.recv_event()
                if msg is None:
                    break
                got.append(wifi.parse_event(msg)[0])
            # the socket's 2 s timeout must not apply: the daemon's main
            # loop reads until nothing waits (it used to raise TimeoutError)
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual(got, ["AP-STA-CONNECTED", "EAPOL-4WAY-HS-COMPLETED"])
            # a request still waits for its answer
            server.settimeout(2)

            def answer():
                data, addr = server.recvfrom(100)
                server.sendto(b"OK\n" if data == b"PING" else b"FAIL\n", addr)

            import threading

            t = threading.Thread(target=answer)
            t.start()
            self.assertEqual(c.request("PING"), "OK\n")
            t.join()
            c.close()
            server.close()
        finally:
            shutil.rmtree(d)


class UsbGadget(unittest.TestCase):
    def make(self, d, bound=True):
        g = os.path.join(d, "duo2")
        os.makedirs(os.path.join(g, "configs", "c.1"))
        os.makedirs(os.path.join(g, "functions", "acm.GS0"))
        os.makedirs(os.path.join(g, "functions", "ncm.usb0"))
        os.symlink(
            os.path.join(g, "functions", "acm.GS0"), os.path.join(g, "configs", "c.1", "acm.GS0")
        )
        os.symlink(
            os.path.join(g, "functions", "ncm.usb0"), os.path.join(g, "configs", "c.1", "ncm.usb0")
        )
        with open(os.path.join(g, "UDC"), "w") as f:
            f.write("a600000.usb\n" if bound else "\n")
        return g

    def test_add_remove_keeps_the_development_functions(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make(d)
            gd = gadget.Gadget(root, rmdir=shutil.rmtree)
            self.assertFalse(gd.active())
            self.assertTrue(gd.add("02:11:22:33:44:55", "02:11:22:33:44:54"))
            self.assertTrue(gd.active())
            self.assertFalse(gd.add("02:11:22:33:44:55", "02:11:22:33:44:54"))  # already
            links = sorted(os.listdir(os.path.join(root, "configs", "c.1")))
            self.assertEqual(links, ["acm.GS0", "ncm.tether", "ncm.usb0"])
            with open(os.path.join(root, "functions", "ncm.tether", "ifname")) as f:
                self.assertEqual(f.read().strip(), "tether%d")
            self.assertEqual(gd.udc(), "a600000.usb")  # bound again
            self.assertTrue(gd.remove())
            self.assertEqual(
                sorted(os.listdir(os.path.join(root, "configs", "c.1"))), ["acm.GS0", "ncm.usb0"]
            )
            self.assertFalse(os.path.exists(os.path.join(root, "functions", "ncm.tether")))
            self.assertEqual(gd.udc(), "a600000.usb")

    def test_unbound_or_missing(self):
        with tempfile.TemporaryDirectory() as d:
            gd = gadget.Gadget(self.make(d, bound=False), rmdir=shutil.rmtree)
            with self.assertRaises(gadget.GadgetError):
                gd.add("02:00:00:00:00:01", "02:00:00:00:00:02")
            self.assertFalse(os.path.exists(os.path.join(d, "duo2", "functions", "ncm.tether")))
            missing = gadget.Gadget(os.path.join(d, "nope"))
            with self.assertRaises(gadget.GadgetError):
                missing.add("02:00:00:00:00:01", "02:00:00:00:00:02")

    def test_failed_change_binds_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make(d)
            # the link's place is taken: the symlink fails
            os.makedirs(os.path.join(root, "configs", "c.1", "ncm.tether"))
            gd = gadget.Gadget(root, rmdir=shutil.rmtree)
            os.rmdir(os.path.join(root, "configs", "c.1", "ncm.tether"))
            with open(os.path.join(root, "configs", "c.1", "ncm.tether"), "w") as f:
                f.write("in the way")
            with self.assertRaises(gadget.GadgetError):
                gd.add("02:00:00:00:00:01", "02:00:00:00:00:02")
            self.assertEqual(gd.udc(), "a600000.usb")
            self.assertFalse(os.path.isdir(os.path.join(root, "functions", "ncm.tether")))

    def test_macs(self):
        dev, host = gadget.macs("0123456789abcdef0123456789abcdef")
        self.assertEqual((dev, host), gadget.macs("0123456789abcdef0123456789abcdef\n"))
        self.assertNotEqual(dev, host)
        first = int(dev.split(":")[0], 16)
        self.assertEqual(first & 0x03, 0x02)  # unicast, locally administered
        self.assertNotEqual(gadget.macs("other")[0], dev)

    def test_ssh_over_usb(self):
        ss = (
            "0      0          10.15.19.1:22       10.15.19.12:53422\n"
            "0      0     192.0.2.50:22     192.0.2.20:40000\n"
        )
        self.assertTrue(gadget.ssh_over(ss, {"10.15.19.1"}))
        self.assertFalse(gadget.ssh_over(ss.splitlines()[1], {"10.15.19.1"}))
        self.assertFalse(gadget.ssh_over("0 0 10.15.19.1:8080 10.15.19.12:1\n", {"10.15.19.1"}))


if __name__ == "__main__":
    unittest.main()
