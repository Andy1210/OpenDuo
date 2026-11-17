"""The WiFi country (duo2hotspot/regdom.py, duo2-wifi-country): MCCs, the
setting, `iw reg get`, the firmware's country table, the decision, and the
command's apply / status with a stand-in system (no root, no phone)."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from duo2hotspot import regdom  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "duo2_wifi_country", os.path.join(ROOT, "duo2-wifi-country")
)
spec = importlib.util.spec_from_loader("duo2_wifi_country", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


def data(name):
    with open(os.path.join(HERE, "data", name)) as f:
        return f.read()


def regdb_blob(countries):
    """A regdb.bin-like table: 2-byte header, 16-byte records, zero end."""
    out = b"\x14\x85"
    for i, cc in enumerate(countries):
        out += (
            struct.pack("<HHB", 100 + i, 0x37, 0)
            + cc.encode()
            + b"\0"
            + cc.encode()
            + b"\0"
            + b"\x28\xa0\0\0\0"
        )
    return out + b"\0" * 64


class Mcc(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(regdom.country_of_mcc("262"), "DE")
        self.assertEqual(regdom.country_of_mcc("26201"), "DE")
        self.assertEqual(regdom.country_of_mcc("216"), "HU")
        self.assertEqual(regdom.country_of_mcc("310260"), "US")
        self.assertEqual(regdom.country_of_mcc("234"), regdom.country_of_mcc("235"))
        for bad in ("", None, "26", "abc", "340", "901", "999"):
            self.assertIsNone(regdom.country_of_mcc(bad), bad)

    def test_table_is_well_formed(self):
        for mcc, cc in regdom.MCC.items():
            self.assertRegex(mcc, r"^[2-7]\d\d$")
            self.assertRegex(cc, r"^[A-Z]{2}$")

    def test_network_not_sim(self):
        reg = {"modem": {"3gpp": {"operator-code": "26201", "registration-state": "home"}}}
        self.assertEqual(regdom.network_mcc(reg), "262")
        self.assertEqual(
            regdom.network_mcc({"modem": {"3gpp": {"operator-code": "310260"}}}), "310"
        )
        for none in (
            None,
            {},
            {"modem": {}},
            {"modem": {"3gpp": {"operator-code": "--"}}},
            {"modem": {"3gpp": {"operator-code": ""}}},
            {"modem": {"3gpp": None}},
        ):
            self.assertIsNone(regdom.network_mcc(none), none)


class Setting(unittest.TestCase):
    def test_values(self):
        self.assertEqual(regdom.parse_conf(None), "auto")
        self.assertEqual(regdom.parse_conf("# nothing\n"), "auto")
        self.assertEqual(regdom.parse_conf("COUNTRY=auto\n"), "auto")
        self.assertEqual(regdom.parse_conf("COUNTRY=off"), "off")
        self.assertEqual(regdom.parse_conf("COUNTRY=de\n"), "DE")
        self.assertEqual(regdom.parse_conf('COUNTRY="AT"\n'), "AT")
        # a value we cannot read never sets a country
        for typo in ("COUNTRY=Germany", "COUNTRY=00", "COUNTRY=D3", "COUNTRY=DEU"):
            self.assertEqual(regdom.parse_conf(typo), "off", typo)

    def test_shipped_file(self):
        with open(os.path.join(ROOT, "wifi-country.conf")) as f:
            self.assertEqual(regdom.parse_conf(f.read()), "auto")


class RegGet(unittest.TestCase):
    def test_measured_intersection(self):
        # what the phone ran on 2026-11-12: DE from 11d, every 5 GHz rule no-IR
        reg = regdom.parse_reg_get(data("iw-reg-get-intersected.txt"))
        self.assertEqual(reg["global"], "00")
        phy = reg["phys"]["phy0"]
        self.assertEqual(phy["country"], "DE")
        self.assertTrue(phy["self_managed"])
        self.assertEqual(regdom.no_ir_5ghz(phy), "all")
        self.assertIn((5170, 5250, {"NO-OUTDOOR", "AUTO-BW", "PASSIVE-SCAN"}), phy["rules"])

    def test_expected_after_the_patch(self):
        reg = regdom.parse_reg_get(data("iw-reg-get-de.txt"))
        self.assertEqual(reg["global"], "DE")
        self.assertEqual(regdom.no_ir_5ghz(reg["phys"]["phy0"]), "none")

    def test_empty(self):
        self.assertEqual(regdom.parse_reg_get(""), {"global": None, "phys": {}})
        self.assertIsNone(regdom.no_ir_5ghz({}))
        some = {"rules": [(5170, 5250, {"PASSIVE-SCAN"}), (5735, 5875, set())]}
        self.assertEqual(regdom.no_ir_5ghz(some), "some")


class Regdb(unittest.TestCase):
    def test_table(self):
        names = ["AF", "AT", "DE", "HU", "PR", "US", "GB", "FR", "IT"]
        self.assertEqual(regdom.regdb_countries(regdb_blob(names)), set(names))

    def test_not_a_table(self):
        self.assertIsNone(regdom.regdb_countries(b""))
        self.assertIsNone(
            regdom.regdb_countries(os.urandom(4096).replace(b"\0", b"\1")[:2] + b"\0" * 400)
        )
        self.assertIsNone(regdom.regdb_countries(regdb_blob(["DE", "AT"])))  # too short to trust

    def test_stock_file_when_present(self):
        path = os.path.join(
            ROOT, "..", "firmware", "linux", "ath11k", "WCN6855", "hw1.1", "regdb.bin"
        )
        if not os.path.exists(path):
            self.skipTest("no stock firmware here")
        with open(path, "rb") as f:
            known = regdom.regdb_countries(f.read())
        self.assertGreater(len(known), 150)
        self.assertTrue({"DE", "AT", "HU", "US", "PR"} <= known)


class Decide(unittest.TestCase):
    KNOWN = {"DE", "AT", "HU"}

    def d(self, mode="auto", mcc="262", phy="DE", known=KNOWN, last=None, now=1000.0, ap=False):
        return regdom.decide(mode, mcc, phy, known, last, now, ap)

    def test_network_country(self):
        self.assertEqual(self.d(phy="PR"), ("set", "DE", "network"))
        # 11d already runs it: still sent once, so it becomes the user's
        self.assertEqual(self.d(), ("set", "DE", "network, same as 11d"))
        self.assertEqual(self.d(last={"set": "DE", "at": 10.0}), ("keep", "DE", "already-set"))
        self.assertEqual(
            self.d(mcc="232", last={"set": "DE", "at": 10.0}), ("set", "AT", "network")
        )

    def test_nothing_to_go_by(self):
        self.assertEqual(self.d(mcc=None), ("keep", None, "no-network"))
        self.assertEqual(self.d(mcc="340"), ("keep", None, "unknown-mcc"))
        self.assertEqual(self.d(mode="off"), ("keep", None, "off"))
        self.assertEqual(self.d(mcc="310"), ("keep", "US", "unknown-to-firmware"))
        # no readable table: no filter
        self.assertEqual(self.d(mcc="310", known=None, phy="DE"), ("set", "US", "network"))

    def test_fixed_country(self):
        self.assertEqual(self.d(mode="AT", mcc=None), ("set", "AT", "config"))
        self.assertEqual(self.d(mode="AT", mcc="262"), ("set", "AT", "config"))

    def test_not_under_an_access_point(self):
        self.assertEqual(self.d(ap=True), ("defer", "DE", "hotspot"))
        # nothing to change: no reason to wait either
        self.assertEqual(
            self.d(ap=True, last={"set": "DE", "at": 1.0}), ("keep", "DE", "already-set")
        )

    def test_not_twice_a_minute(self):
        self.assertEqual(
            self.d(mcc="232", last={"set": "DE", "at": 990.0}), ("defer", "AT", "too-soon")
        )
        self.assertEqual(
            self.d(mcc="232", last={"set": "DE", "at": 900.0}), ("set", "AT", "network")
        )


class Command(unittest.TestCase):
    """duo2-wifi-country apply / status with stand-in commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.saved = {k: getattr(cli, k) for k in ("CONF", "STATE", "REGDB", "SETTLE_S", "run")}
        cli.CONF = os.path.join(d, "wifi-country.conf")
        cli.STATE = os.path.join(d, "run", "wifi-country.json")
        cli.REGDB = os.path.join(d, "regdb.bin")
        cli.SETTLE_S = 0
        with open(cli.REGDB, "wb") as f:
            f.write(regdb_blob(["AF", "AT", "DE", "HU", "PR", "GB", "FR", "IT"]))
        self.calls = []
        self.phy = "PR"
        self.takes = True  # the driver takes a user's country
        self.ap = False
        self.mcc = "26201"
        cli.run = self.fake_run
        self.real_geteuid = cli.os.geteuid
        cli.os.geteuid = lambda: 0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(cli, k, v)
        cli.os.geteuid = self.real_geteuid
        self.tmp.cleanup()

    def fake_run(self, argv, timeout=15):
        self.calls.append(argv)
        if argv[:2] == ["mmcli", "-m"]:
            if self.mcc is None:
                return 1, ""
            return 0, json.dumps({"modem": {"3gpp": {"operator-code": self.mcc}}})
        if argv == ["iw", "reg", "get"]:
            text = data("iw-reg-get-intersected.txt").replace(
                "country DE:", "country %s:" % self.phy
            )
            return 0, text
        if argv[:3] == ["iw", "reg", "set"]:
            if self.takes:
                self.phy = argv[3]
            return 0, ""
        if argv == ["iw", "dev"]:
            return 0, "phy#0\n\tInterface wlan0\n\t\ttype managed\n" + (
                "\tInterface ap0\n\t\ttype AP\n" if self.ap else ""
            )
        return 127, ""

    def apply(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.cmd_apply("--dry-run" in args)
        with open(cli.STATE) as f:
            return rc, json.load(f)

    def sets(self):
        return [c for c in self.calls if c[:3] == ["iw", "reg", "set"]]

    def test_sets_once_and_reads_back(self):
        rc, st = self.apply()
        self.assertEqual(rc, 0)
        self.assertEqual(self.sets(), [["iw", "reg", "set", "DE"]])
        self.assertEqual(st["result"], "applied")
        self.assertEqual(st["last"]["set"], "DE")
        rc, st = self.apply()
        self.assertEqual(self.sets(), [["iw", "reg", "set", "DE"]])  # not again
        self.assertEqual(st["checked"]["why"], "already-set")
        self.assertEqual(st["result"], "applied")

    def test_a_driver_that_ignores_it(self):
        self.takes = False
        rc, st = self.apply()
        self.assertEqual(rc, 0)
        self.assertEqual(st["result"], "ignored")

    def test_dry_run_and_hotspot_send_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_apply(True)
        self.assertEqual(self.sets(), [])
        self.assertFalse(os.path.exists(cli.STATE))
        self.ap = True
        rc, st = self.apply()
        self.assertEqual(self.sets(), [])
        self.assertEqual(st["checked"], {"action": "defer", "target": "DE", "why": "hotspot"})

    def test_off_and_no_network(self):
        with open(cli.CONF, "w") as f:
            f.write("COUNTRY=off\n")
        self.apply()
        with open(cli.CONF, "w") as f:
            f.write("COUNTRY=auto\n")
        self.mcc = None
        self.apply()
        self.assertEqual(self.sets(), [])

    def test_not_root(self):
        cli.os.geteuid = lambda: 1000
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_apply(False), 1)
        self.assertEqual(self.sets(), [])

    def test_state_has_no_identifiers(self):
        self.apply()
        with open(cli.STATE) as f:
            text = f.read()
        self.assertNotRegex(text, r"([0-9a-f]{2}:){5}")
        self.assertNotIn("26201", text)  # the operator (MNC) is not kept, only the country
        self.assertEqual(oct(os.stat(cli.STATE).st_mode & 0o777), "0o644")


if __name__ == "__main__":
    unittest.main()
