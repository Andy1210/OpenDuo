"""duo2-pen-charger: the stock rule, the readings, the daemon's one look,
the uevents (python3 -m unittest discover -s tests, from power/)."""

import os
import tempfile
import unittest

from test_netwatch import load

pc = load("duo2-pen-charger")


def make_supply(root, name, **props):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for k, v in props.items():
        with open(os.path.join(d, k), "w") as f:
            f.write("%s\n" % v)


class Tree:
    """A made-up /etc/duo2/pen-charger, connector and power_supply class."""

    def __init__(self, capacity=50, usb_online=0, setting="on", connector=True):
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.setting = os.path.join(base, "pen-charger")
        self.output = os.path.join(base, "pen-charger-output")
        self.root = os.path.join(base, "power_supply")
        if setting is not None:
            with open(self.setting, "w") as f:
                f.write(setting + "\n")
        if connector:
            os.makedirs(self.output)
            self.write_state("disabled")
        make_supply(self.root, "qcom-battmgr-bat", type="Battery", capacity=capacity)
        make_supply(self.root, "qcom-battmgr-usb", type="USB", online=usb_online)
        make_supply(self.root, "qcom-battmgr-wls", type="Wireless", online=0)
        make_supply(self.root, "ucsi-source-psy-pmic_glink.ucsi.01", type="USB", online=usb_online)

    def write_state(self, text):
        with open(os.path.join(self.output, "state"), "w") as f:
            f.write(text + "\n")

    def state(self):
        with open(os.path.join(self.output, "state")) as f:
            return f.read().strip()

    def daemon(self):
        return pc.Daemon(self.setting, self.output, self.root)

    def close(self):
        self.tmp.cleanup()


class TestRule(unittest.TestCase):
    def test_the_stock_rule(self):
        # batteryd: off below 20 % unless the USB supply is online; on otherwise,
        # while the setting is on
        self.assertTrue(pc.decide(True, 20, False)[0])
        self.assertTrue(pc.decide(True, 100, False)[0])
        self.assertFalse(pc.decide(True, 19, False)[0])
        self.assertTrue(pc.decide(True, 19, True)[0])
        self.assertTrue(pc.decide(True, 0, True)[0])
        for cap, ext in ((50, False), (50, True), (5, True)):
            self.assertFalse(pc.decide(False, cap, ext)[0])

    def test_unknown_level_is_off(self):
        # stock exited (and left the line as it was); here it is off
        self.assertEqual(pc.decide(True, None, True), (False, "battery level unknown"))

    def test_reasons(self):
        self.assertEqual(pc.decide(True, 54, False)[1], "battery 54 %")
        self.assertEqual(pc.decide(True, 12, True)[1], "battery 12 %, external power")
        self.assertIn("below 20 %", pc.decide(True, 12, False)[1])
        self.assertEqual(pc.decide(False, 54, False)[1], "switched off")


class TestReadings(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.close()

    def test_setting(self):
        self.assertTrue(pc.setting_on(self.t.setting))
        for text in ("off", "", "yes", "1", "On"):
            with open(self.t.setting, "w") as f:
                f.write(text + "\n")
            self.assertFalse(pc.setting_on(self.t.setting), text)
        os.remove(self.t.setting)
        self.assertFalse(pc.setting_on(self.t.setting))  # no file: off

    def test_battery_and_external(self):
        found = pc.supplies(self.t.root)
        self.assertEqual(pc.battery_capacity(found), 50)
        self.assertFalse(pc.external_power(found))
        make_supply(self.t.root, "qcom-battmgr-usb", online=1)
        self.assertTrue(pc.external_power(pc.supplies(self.t.root)))

    def test_a_device_battery_is_not_the_phone(self):
        # a Bluetooth pen or mouse reports a Battery of scope Device; it sorts
        # before qcom-battmgr-bat here and must not be taken for the phone's
        make_supply(self.t.root, "hid-aa:bb-battery", type="Battery", scope="Device", capacity=3)
        self.assertEqual(pc.battery_capacity(pc.supplies(self.t.root)), 50)

    def test_bad_capacity(self):
        for bad in ("", "abc", "101", "-1"):
            make_supply(self.t.root, "qcom-battmgr-bat", capacity=bad)
            self.assertIsNone(pc.battery_capacity(pc.supplies(self.t.root)), bad)
        self.assertIsNone(pc.battery_capacity(pc.supplies(os.path.join(self.t.tmp.name, "none"))))

    def test_output_state(self):
        self.assertFalse(pc.output_state(self.t.output))
        self.t.write_state("enabled")
        self.assertTrue(pc.output_state(self.t.output))
        self.t.write_state("rubbish")
        self.assertIsNone(pc.output_state(self.t.output))
        self.assertIsNone(pc.output_state(self.t.root))  # no connector there


class TestLook(unittest.TestCase):
    def test_on_off_by_level_and_power(self):
        t = Tree(capacity=50)
        try:
            d = t.daemon()
            self.assertTrue(d.look())
            self.assertEqual(t.state(), "enabled")
            make_supply(t.root, "qcom-battmgr-bat", capacity=19)
            d.look()
            self.assertEqual(t.state(), "disabled")
            make_supply(t.root, "qcom-battmgr-usb", online=1)
            d.look()
            self.assertEqual(t.state(), "enabled")
        finally:
            t.close()

    def test_setting_off_ends_and_switches_off(self):
        t = Tree(capacity=80)
        try:
            t.write_state("enabled")
            with open(t.setting, "w") as f:
                f.write("off\n")
            self.assertFalse(t.daemon().look())
            self.assertEqual(t.state(), "disabled")
        finally:
            t.close()

    def test_no_setting_is_off(self):
        t = Tree(capacity=80, setting=None)
        try:
            self.assertFalse(t.daemon().look())
            self.assertEqual(t.state(), "disabled")
        finally:
            t.close()

    def test_write_only_on_a_change(self):
        t = Tree(capacity=80)
        try:
            d = t.daemon()
            d.look()
            st = os.path.join(t.output, "state")
            os.chmod(st, 0o444)  # a second write would fail
            try:
                self.assertTrue(d.look())
            finally:
                os.chmod(st, 0o644)
            self.assertEqual(t.state(), "enabled")
        finally:
            t.close()

    def test_run_without_connector_is_unsupported(self):
        t = Tree(connector=False)
        old = pc.OUTPUT_WAIT_S
        pc.OUTPUT_WAIT_S = 0
        try:
            self.assertEqual(t.daemon().run(), 0)
            self.assertEqual(pc.status_text(False, t.setting, t.output, t.root), "unsupported")
        finally:
            pc.OUTPUT_WAIT_S = old
            t.close()

    def test_run_ends_off_when_switched_off(self):
        # the setting off: one look, the transmitter off, the loop ends
        t = Tree(capacity=80, setting="off")
        try:
            t.write_state("enabled")
            self.assertEqual(t.daemon().run(), 0)
            self.assertEqual(t.state(), "disabled")
        finally:
            t.close()

    def test_status(self):
        t = Tree(capacity=12, usb_online=0)
        try:
            self.assertEqual(pc.status_text(False, t.setting, t.output, t.root), "on")
            v = pc.status_text(True, t.setting, t.output, t.root)
            self.assertIn("transmitter: off", v)
            self.assertIn("rule: off (battery 12 % (below 20 %) without external power)", v)
        finally:
            t.close()


class TestUevents(unittest.TestCase):
    def test_parse(self):
        data = (
            b"change@/devices/platform/pmic-glink/power_supply/qcom-battmgr-usb\0"
            b"ACTION=change\0DEVPATH=/devices/x\0SUBSYSTEM=power_supply\0"
            b"POWER_SUPPLY_NAME=qcom-battmgr-usb\0POWER_SUPPLY_ONLINE=1\0"
        )
        ev = pc.parse_uevent(data)
        self.assertEqual(ev["SUBSYSTEM"], "power_supply")
        self.assertEqual(ev["POWER_SUPPLY_ONLINE"], "1")
        self.assertTrue(pc.wants_look(ev))

    def test_which(self):
        self.assertTrue(pc.wants_look({"ACTION": "add", "SUBSYSTEM": "typec"}))
        self.assertTrue(pc.wants_look({"ACTION": "remove", "SUBSYSTEM": "typec"}))
        self.assertFalse(pc.wants_look({"ACTION": "change", "SUBSYSTEM": "drm"}))
        self.assertFalse(pc.wants_look({"ACTION": "bind", "SUBSYSTEM": "typec"}))
        self.assertFalse(pc.wants_look(pc.parse_uevent(b"libudev\0garbage")))


if __name__ == "__main__":
    unittest.main()
