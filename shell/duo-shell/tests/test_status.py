import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import status  # noqa: E402


class Battery(unittest.TestCase):
    def test_icons(self):
        self.assertEqual(status.battery_icon(86, "Charging"), "battery-level-90-charging-symbolic")
        self.assertEqual(status.battery_icon(86, "Discharging"), "battery-level-90-symbolic")
        self.assertEqual(status.battery_icon(4, "Discharging"), "battery-level-0-symbolic")
        self.assertEqual(status.battery_icon(100, "Full"), "battery-level-100-charged-symbolic")
        self.assertEqual(status.battery_icon(100, "Discharging"), "battery-level-100-symbolic")
        self.assertEqual(status.battery_icon(None, None), "battery-missing-symbolic")
        self.assertEqual(status.battery_icon(140, "Unknown"), "battery-level-100-symbolic")

    def test_reads_the_battery_supply(self):
        with tempfile.TemporaryDirectory() as root:
            for name, kind in (("qcom-battmgr-usb", "USB"), ("qcom-battmgr-bat", "Battery")):
                os.makedirs(os.path.join(root, name))
                with open(os.path.join(root, name, "type"), "w") as f:
                    f.write(kind + "\n")
            bat = os.path.join(root, "qcom-battmgr-bat")
            with open(os.path.join(bat, "capacity"), "w") as f:
                f.write("86\n")
            with open(os.path.join(bat, "status"), "w") as f:
                f.write("Charging\n")
            self.assertEqual(status.find_battery(root), bat)
            self.assertEqual(status.read_battery(bat), (86, "Charging"))
        self.assertEqual(status.read_battery(None), (None, None))


class Wifi(unittest.TestCase):
    STATUS = (
        "bssid=00:11:22:33:44:55\nfreq=5180\nssid=home\nid=0\nmode=station\n"
        "wpa_state=COMPLETED\nip_address=192.0.2.203\n"
    )

    def test_connected_with_signal(self):
        wifi = status.wifi_from_wpa(
            self.STATUS, "RSSI=-58\nLINKSPEED=866\nNOISE=9999\nFREQUENCY=5180\n"
        )
        self.assertEqual(wifi, {"state": "connected", "ssid": "home", "rssi": -58})
        self.assertEqual(status.wifi_icon(wifi), "network-wireless-signal-good-symbolic")

    def test_states(self):
        self.assertEqual(status.wifi_from_wpa("wpa_state=SCANNING\n", None)["state"], "connecting")
        self.assertEqual(
            status.wifi_from_wpa("wpa_state=DISCONNECTED\n", "FAIL\n")["state"], "disconnected"
        )
        self.assertIsNone(status.wifi_from_wpa("FAIL\n", None)["state"])
        self.assertEqual(
            status.wifi_icon({"state": "disconnected"}), "network-wireless-offline-symbolic"
        )
        self.assertEqual(
            status.wifi_icon({"state": "connected", "rssi": None}),
            "network-wireless-connected-symbolic",
        )
        self.assertEqual(
            status.wifi_icon({"state": "absent"}), "network-wireless-hardware-disabled-symbolic"
        )

    def test_bars(self):
        self.assertEqual([status.wifi_bars(r) for r in (-40, -60, -70, -80, -95)], [4, 3, 2, 1, 0])
        self.assertIsNone(status.wifi_bars(None))
        self.assertIsNone(status.wifi_from_wpa(self.STATUS, "RSSI=-9999\n")["rssi"])


if __name__ == "__main__":
    unittest.main()
