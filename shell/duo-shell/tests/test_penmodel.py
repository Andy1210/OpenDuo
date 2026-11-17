"""The pen's top button and battery rules (duoshell/penmodel.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import penmodel  # noqa: E402

PEN = {
    "Name": "Surface Slim Pen 2",
    "Alias": "Surface Slim Pen 2",
    "Paired": True,
    "Connected": True,
}


class Config(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(
            penmodel.parse(""),
            {"click": "notes", "double_click": "screenshot", "hold": "quick_menu"},
        )
        self.assertEqual(penmodel.load("/nonexistent/pen.conf"), penmodel.DEFAULTS)

    def test_the_file(self):
        text = (
            "# what the top button does\n"
            "click = screenshot\n"
            "double_click=off   # nothing\n"
            "hold = launch_rockets\n"  # unknown: the default stays
            "palm = off\n"
        )  # not ours
        self.assertEqual(
            penmodel.parse(text),
            {"click": "screenshot", "double_click": "off", "hold": "quick_menu"},
        )


class Decide(unittest.TestCase):
    config = penmodel.DEFAULTS

    def test_each_press(self):
        for press, action in (
            ("click", "notes"),
            ("double_click", "screenshot"),
            ("hold", "quick_menu"),
        ):
            self.assertEqual(
                penmodel.decide({"press": press, "output": "DSI-2"}, self.config), (action, "DSI-2")
            )

    def test_nothing_dark_locked_off_or_unknown(self):
        self.assertIsNone(penmodel.decide({"press": "click", "output": None}, self.config))
        self.assertIsNone(penmodel.decide({"press": "click"}, self.config))
        self.assertIsNone(
            penmodel.decide({"press": "click", "output": "DSI-1", "locked": True}, self.config)
        )
        off = dict(self.config, click="off")
        self.assertIsNone(penmodel.decide({"press": "click", "output": "DSI-1"}, off))
        self.assertIsNone(penmodel.decide({"press": "triple", "output": "DSI-1"}, self.config))


class Notes(unittest.TestCase):
    def test_rnote_first_then_the_others(self):
        self.assertEqual(
            penmodel.notes_app({"com.github.flxzt.rnote.desktop", "org.gnome.TextEditor.desktop"}),
            "com.github.flxzt.rnote.desktop",
        )
        self.assertEqual(
            penmodel.notes_app(
                {"com.github.xournalpp.xournalpp.desktop", "org.gnome.TextEditor.desktop"}
            ),
            "com.github.xournalpp.xournalpp.desktop",
        )
        self.assertEqual(
            penmodel.notes_app({"org.gnome.TextEditor.desktop"}), "org.gnome.TextEditor.desktop"
        )
        self.assertIsNone(penmodel.notes_app({"org.gnome.Calculator.desktop"}))

    def test_a_running_one_is_brought_forward(self):
        windows = [
            {"id": 3, "app_id": "com.github.flxzt.rnote"},
            {"id": 5, "app_id": "foot"},
            {"id": 7, "app_id": "com.github.flxzt.rnote"},
        ]
        self.assertEqual(penmodel.running_window(windows, "com.github.flxzt.rnote.desktop"), 7)
        self.assertIsNone(penmodel.running_window(windows[1:2], "com.github.flxzt.rnote.desktop"))
        self.assertIsNone(penmodel.running_window(None, "com.github.flxzt.rnote.desktop"))


class Screenshot(unittest.TestCase):
    def test_named_like_gnome_and_never_over_another(self):
        os.environ["TZ"] = "UTC"
        import time

        time.tzset()
        taken = set()
        first = penmodel.screenshot_path("/p", now=0, exists=lambda p: p in taken)
        self.assertEqual(first, "/p/Screenshot from 1970-01-01 00-00-00.png")
        taken.add(first)
        second = penmodel.screenshot_path("/p", now=0, exists=lambda p: p in taken)
        self.assertEqual(second, "/p/Screenshot from 1970-01-01 00-00-00 (2).png")
        self.assertEqual(penmodel.screenshot_dir("/home/x"), "/home/x/Pictures/Screenshots")


class Pens(unittest.TestCase):
    def test_which_devices_are_surface_pens(self):
        self.assertTrue(penmodel.is_surface_pen({"Name": "Surface Slim Pen 2"}))
        self.assertTrue(penmodel.is_surface_pen({"Alias": "Surface Pen"}))
        self.assertTrue(
            penmodel.is_surface_pen({"Name": "Slim Pen", "ManufacturerData": {6: b"\x03"}})
        )
        self.assertTrue(
            penmodel.is_surface_pen({"Name": "MS Pen", "Modalias": "usb:v045Ep0C01d0001"})
        )
        self.assertFalse(penmodel.is_surface_pen({"Name": "Surface Headphones"}))
        self.assertFalse(
            penmodel.is_surface_pen({"Name": "Some Pen", "ManufacturerData": {76: b""}})
        )
        self.assertFalse(penmodel.is_surface_pen({}))

    def test_the_paired_pens_with_their_battery(self):
        objects = {
            "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": True}},
            "/org/bluez/hci0/dev_A": {
                "org.bluez.Device1": PEN,
                "org.bluez.Battery1": {"Percentage": 64},
            },
            "/org/bluez/hci0/dev_B": {"org.bluez.Device1": dict(PEN, Paired=False)},
            "/org/bluez/hci0/dev_C": {"org.bluez.Device1": {"Name": "Headset", "Paired": True}},
            "/org/bluez/hci0/dev_D": {"org.bluez.Device1": dict(PEN, Connected=False)},
        }
        self.assertEqual(
            penmodel.pens(objects),
            [
                {
                    "path": "/org/bluez/hci0/dev_A",
                    "name": "Surface Slim Pen 2",
                    "connected": True,
                    "battery": 64,
                },
                {
                    "path": "/org/bluez/hci0/dev_D",
                    "name": "Surface Slim Pen 2",
                    "connected": False,
                    "battery": None,
                },
            ],
        )

    def test_keys_carry_no_address(self):
        key = penmodel.key_of("/org/bluez/hci0/dev_00_11_22_33_44_55")
        self.assertEqual(len(key), 16)
        self.assertEqual(key, penmodel.key_of("/org/bluez/hci0/dev_00_11_22_33_44_55"))
        self.assertNotEqual(key, penmodel.key_of("/org/bluez/hci0/dev_00_11_22_33_44_56"))
        self.assertNotIn("00_11", key)


class Battery(unittest.TestCase):
    def test_once_at_or_below_ten(self):
        n = penmodel.BatteryNotice()
        self.assertFalse(n.update("p", 50))
        self.assertFalse(n.update("p", 11))
        self.assertTrue(n.update("p", 10))
        self.assertFalse(n.update("p", 9))
        self.assertFalse(n.update("p", 5))
        self.assertFalse(n.update("p", None))

    def test_again_only_after_a_charge(self):
        n = penmodel.BatteryNotice()
        self.assertTrue(n.update("p", 8))
        self.assertFalse(n.update("p", 15))  # not charged enough
        self.assertFalse(n.update("p", 8))
        self.assertFalse(n.update("p", 20))  # charged: armed again
        self.assertTrue(n.update("p", 9))

    def test_kept_between_runs_and_per_pen(self):
        n = penmodel.BatteryNotice(told=["p"])
        self.assertFalse(n.update("p", 7))
        self.assertTrue(n.update("q", 7))
        self.assertEqual(n.told, {"p", "q"})

    def test_text(self):
        self.assertEqual(
            penmodel.battery_text("Surface Slim Pen 2", 9),
            ("Pen battery low", "Surface Slim Pen 2: 9 %. Charge it soon."),
        )


if __name__ == "__main__":
    unittest.main()
