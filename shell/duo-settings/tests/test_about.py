"""Settings > About's pure parts, with made-up values only."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import about  # noqa: E402


def load(name, file):
    path = os.path.join(os.path.dirname(HERE), file)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load("duo_settings_helper_about", "duo-settings-helper")
qmi = load("duo_settings_qmi", "duo-settings-qmi")

MODEM = {
    "modem": {
        "generic": {
            "revision": "MPSS.FAKE.1",
            "equipment-identifier": "000000000000000",
            "primary-sim-slot": "2",
            "sim-slots": ["/SIM/0", "/"],
            "sim": "/SIM/1",
            "own-numbers": ["+00 000 0000000"],
            "access-technologies": ["lte", "5gnr"],
            "signal-quality": {"value": 90, "recent": "yes"},
            "state": "connected",
        },
        "3gpp": {
            "operator-name": "Example Mobile",
            "registration-state": "home",
            "imei": "000000000000000",
        },
    }
}
SIMS = {"/SIM/0": {"operator-name": "--"}, "/SIM/1": {"operator-name": "Example Virtual"}}


class Parsers(unittest.TestCase):
    def test_release(self):
        self.assertEqual(
            about.parse_release("version=v1-2-gabc\n# c\nbuilt=2026-01-01T00:00:00Z\n"),
            {"version": "v1-2-gabc", "built": "2026-01-01T00:00:00Z"},
        )

    def test_header(self):
        h = about.os_header(
            {"openduo": "0.1.0-dev", "build": "20261116.71fc19a"}, {"NAME": "OpenDuo"}, "7.3.4-duo2"
        )
        self.assertEqual(h["name"], "OpenDuo")
        self.assertEqual(h["version"], "0.1.0-dev")
        self.assertEqual(
            h["lines"], ["Build 20261116.71fc19a", "Kernel 7.3.4-duo2", "Based on Debian 13"]
        )
        self.assertEqual(about.os_header({}, {}, "k")["version"], "?")

    def test_kernel_build(self):
        self.assertEqual(
            about.parse_kernel_build("#17 SMP PREEMPT Sun Nov  8 01:00:09 UTC 2026"), "8 Nov 2026"
        )
        self.assertEqual(about.parse_kernel_build("odd"), "odd")

    def test_last_upgrade_ignores_installs(self):
        a = "Start-Date: 2026-01-01  10:00:00\nInstall: foo:arm64 (1)\nEnd-Date: 2026-01-01  10:00:05"
        b = "Start-Date: 2026-02-02  10:00:00\nUpgrade: bar:arm64 (1, 2)\nEnd-Date: 2026-02-02  10:00:05"
        c = "Start-Date: 2026-03-03  10:00:00\nInstall: baz:arm64 (1)\nEnd-Date: 2026-03-03  10:00:05"
        self.assertEqual(about.last_upgrade([a + "\n\n" + b + "\n\n" + c]), "2026-02-02")
        self.assertIsNone(about.last_upgrade([a]))
        self.assertEqual(about.format_date("2026-02-02"), "2 Feb 2026")

    def test_uptime(self):
        self.assertEqual(about.format_uptime(59), "0 min")
        self.assertEqual(about.format_uptime(3 * 3600 + 120), "3 h 2 min")
        self.assertEqual(about.format_uptime(90000), "1 d 1 h 0 min")

    def test_network_type(self):
        self.assertEqual(about.network_type(["lte", "5gnr"]), "5G (with LTE)")
        self.assertEqual(about.network_type(["lte"]), "4G (LTE)")
        self.assertEqual(about.network_type(["gsm"]), "2G (GSM)")
        self.assertIsNone(about.network_type([]))

    def test_modem_slots(self):
        info = about.parse_modem(MODEM, SIMS)
        s1, s2 = info["slots"]
        self.assertFalse(s1["active"])
        self.assertTrue(s2["active"] and s2["present"])
        self.assertEqual(
            (s2["operator"], s2["network"], s2["signal"], s2["roaming"]),
            ("Example Mobile", "5G (with LTE)", 90, False),
        )
        self.assertEqual(s2["number"], "+00 000 0000000")
        self.assertEqual(info["modem"]["revision"], "MPSS.FAKE.1")
        roam = json.loads(json.dumps(MODEM))
        roam["modem"]["3gpp"]["registration-state"] = "roaming"
        self.assertTrue(about.parse_modem(roam, SIMS)["slots"][1]["roaming"])

    def test_ip(self):
        text = json.dumps(
            [
                {
                    "ifname": "wlan0",
                    "addr_info": [
                        {"local": "192.0.2.5", "scope": "global"},
                        {"local": "fe80::1", "scope": "link"},
                    ],
                },
                {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
            ]
        )
        self.assertEqual(about.parse_ip(text), {"wlan0": ["192.0.2.5"]})
        self.assertEqual(about.parse_ip("not json"), {})

    def test_cpus(self):
        cores = [("0xd05", 1804800)] * 4 + [("0xd41", 2419200)] * 3 + [("0xd44", 2841600)]
        self.assertEqual(
            about.group_cpus(cores),
            [
                "4 x Cortex-A55 at 1.80 GHz",
                "3 x Cortex-A78 at 2.42 GHz",
                "1 x Cortex-X1 at 2.84 GHz",
            ],
        )

    def test_battery_design(self):
        self.assertEqual(about.design_capacity_mah("CELL_3600MAH_X"), 3600)
        self.assertIsNone(about.design_capacity_mah("cell"))

    def test_sensors(self):
        self.assertEqual(
            about.group_sensors(["imx481 1-0010", "imx481 2-001a", "imx563 4-001a"]),
            ["IMX481 x 2", "IMX563"],
        )
        self.assertTrue(about.SENSOR_NAME.match("imx481 1-0010"))
        self.assertFalse(about.SENSOR_NAME.match("msm_tpg0"))

    def test_licence(self):
        text = "Files: *\nLicense: GPL-2+\n\nFiles: x\nLicense: MIT\n\nLicense: GPL-2+\n"
        self.assertEqual(about.parse_licence(text), ["GPL-2+", "MIT"])
        self.assertEqual(
            about.parse_licence("see /usr/share/common-licenses/GPL-3 for it"), ["GPL-3"]
        )


class FakeMode(unittest.TestCase):
    def setUp(self):
        os.environ[about.FAKE_ENV] = "1"

    def tearDown(self):
        del os.environ[about.FAKE_ENV]

    def test_fake_data_is_made_up(self):
        flat = (
            repr(about.status_groups())
            + repr(about.hardware_groups())
            + repr(about.software_groups())
        )
        self.assertIn("000000000000000", flat)
        self.assertIn(about.FAKE_SERIAL, flat)

    def test_status_labels(self):
        labels = [k for _t, rows in about.status_groups() for k, _v in rows]
        for want in (
            "Operator",
            "Network type",
            "Signal strength",
            "Roaming",
            "Phone number",
            "IMEI",
            "IMEI SV",
            "Wi-Fi IP address",
            "Mobile IP address",
            "Wi-Fi MAC address",
            "Bluetooth address",
            "Serial number",
            "Up time",
        ):
            self.assertIn(want, labels)


class RealStatusAssembly(unittest.TestCase):
    """status_groups with made-up modem and identity (no command run: the
    ip / bluetooth readers return nothing here)."""

    def test_two_imeis(self):
        info = about.parse_modem(MODEM, SIMS)
        ident = {
            "serial": "ABC123XYZ",
            "imeis": [
                {"sub": 0, "imei": "000000000000001", "sv": "01"},
                {"sub": 1, "imei": "000000000000002", "sv": "01"},
            ],
        }
        rows = dict(r for _t, rs in about.status_groups(ident, info) for r in rs)
        self.assertEqual(rows["IMEI (SIM 1)"], "000000000000001")
        self.assertEqual(rows["IMEI (SIM 2)"], "000000000000002")
        self.assertEqual(rows["Serial number"], "ABC123XYZ")

    def test_one_imei_and_fallback(self):
        info = about.parse_modem(MODEM, SIMS)
        one = {"serial": None, "imeis": [{"sub": 0, "imei": "000000000000001", "sv": "07"}]}
        rows = dict(r for _t, rs in about.status_groups(one, info) for r in rs)
        self.assertEqual((rows["IMEI"], rows["IMEI SV"]), ("000000000000001", "07"))
        self.assertEqual(rows["Serial number"], "Unavailable")
        rows = dict(r for _t, rs in about.status_groups({}, info) for r in rs)
        self.assertEqual(rows["IMEI"], "000000000000000")  # ModemManager's, the fallback
        rows = dict(r for _t, rs in about.status_groups({}, None) for r in rs)
        self.assertEqual(rows["SIM"], "No modem")


class HelperIdentity(unittest.TestCase):
    def test_serial_filter(self):
        self.assertIsNone(helper.clean_serial("To be filled by O.E.M."))
        self.assertIsNone(helper.clean_serial("0"))
        self.assertIsNone(helper.clean_serial("a b; rm"))
        self.assertEqual(helper.clean_serial("  ABC-1234 \n"), "ABC-1234")

    def test_serial_reads_first_good_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            for name, text in (
                ("product_serial", "Default string\n"),
                ("board_serial", "ZZ99XX11\n"),
            ):
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            self.assertEqual(helper.read_serial(d), "ZZ99XX11")
            self.assertIsNone(helper.read_serial(d + "/none"))

    def test_qmi_runs_outside_the_session(self):
        # The reader goes through systemd-run (PID 1), restricted to QRTR + unix.
        calls = []

        class R:
            returncode = 0
            stdout = (
                json.dumps(
                    {
                        "ids": [
                            {"sub": 0, "imei": "000000000000001", "sv": "01"},
                            {"sub": 1, "imei": "bad", "sv": "x"},
                        ],
                        "bind": True,
                    }
                )
                + "\n"
            )

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return R()

        real = helper.subprocess.run
        helper.subprocess.run = fake_run
        try:
            ids = helper.qmi_ids()
        finally:
            helper.subprocess.run = real
        self.assertEqual(ids, [{"sub": 0, "imei": "000000000000001", "sv": "01"}])
        cmd = calls[0]
        self.assertEqual(cmd[0], helper.SYSTEMD_RUN)
        self.assertIn("--property=RestrictAddressFamilies=AF_UNIX AF_QIPCRTR", cmd)
        self.assertEqual(cmd[-1], helper.QMI_SCRIPT)

    def test_qmi_tlvs(self):
        import struct

        body = struct.pack("<BH", 0x11, 3) + b"123" + struct.pack("<BH", 0x13, 2) + b"07"
        self.assertEqual(qmi.parse_tlvs(body), {0x11: b"123", 0x13: b"07"})


if __name__ == "__main__":
    unittest.main()
