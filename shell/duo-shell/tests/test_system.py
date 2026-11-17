import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from duoshell import system  # noqa: E402


def load_helper():
    path = os.path.join(HERE, "..", "duo-shell-helper")
    loader = importlib.machinery.SourceFileLoader("duo_shell_helper", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_helper()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Radios(unittest.TestCase):
    def test_rfkill(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(system.rfkill_blocked("wlan", root))
            write(os.path.join(root, "rfkill0", "type"), "bluetooth\n")
            write(os.path.join(root, "rfkill0", "soft"), "1\n")
            write(os.path.join(root, "rfkill0", "hard"), "0\n")
            write(os.path.join(root, "rfkill1", "type"), "wlan\n")
            write(os.path.join(root, "rfkill1", "soft"), "0\n")
            write(os.path.join(root, "rfkill1", "hard"), "0\n")
            self.assertFalse(system.rfkill_blocked("wlan", root))
            self.assertTrue(system.rfkill_blocked("bluetooth", root))
            write(os.path.join(root, "rfkill1", "hard"), "1\n")
            self.assertTrue(system.rfkill_blocked("wlan", root))


def radio_root(root, wlan="0", bluetooth="0"):
    for entry, kind, name, soft in (
        ("rfkill0", "bluetooth", "hci0", bluetooth),
        ("rfkill1", "wlan", "phy0", wlan),
    ):
        write(os.path.join(root, entry, "type"), kind + "\n")
        write(os.path.join(root, entry, "name"), name + "\n")
        write(os.path.join(root, entry, "soft"), soft + "\n")
        write(os.path.join(root, entry, "hard"), "0\n")


def soft(root, entry):
    with open(os.path.join(root, entry, "soft")) as f:
        return f.read().strip()


class Airplane(unittest.TestCase):
    """duo-shell-helper airplane on|off on a fake /sys/class/rfkill."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "rfkill")
        self.state = os.path.join(self.tmp.name, "duo2", "airplane")
        self.radios = os.path.join(self.tmp.name, "duo2", "airplane-radios")
        self.bt = {"hci0": True}
        self.powered_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def powered(self, hci, value=None):
        if value is None:
            return self.bt.get(hci)
        self.powered_calls.append((hci, value))
        self.bt[hci] = value
        return True

    def switch(self, on):
        return helper.airplane(on, self.root, self.state, self.radios, self.powered)

    def test_on_blocks_both_and_off_brings_back_what_was_on(self):
        radio_root(self.root)
        self.assertTrue(self.switch(True))
        self.assertEqual((soft(self.root, "rfkill0"), soft(self.root, "rfkill1")), ("1", "1"))
        self.assertTrue(system.read_airplane(self.state))
        with open(self.radios) as f:
            self.assertEqual(helper.parse_radios(f.read()), ["bluetooth", "wlan"])
        self.bt["hci0"] = False  # BlueZ: blocked = not powered
        self.assertTrue(self.switch(False))
        self.assertEqual((soft(self.root, "rfkill0"), soft(self.root, "rfkill1")), ("0", "0"))
        self.assertFalse(system.read_airplane(self.state))
        self.assertFalse(os.path.exists(self.radios))
        self.assertEqual(self.powered_calls, [("hci0", True)])

    def test_a_radio_that_was_off_stays_off(self):
        radio_root(self.root, wlan="1")
        self.bt["hci0"] = False  # free, but BlueZ had it switched off
        self.switch(True)
        self.switch(False)
        # Neither comes back: WiFi was blocked, Bluetooth unpowered (BlueZ would
        # power it up the moment its block went).
        self.assertEqual((soft(self.root, "rfkill0"), soft(self.root, "rfkill1")), ("1", "1"))
        self.assertEqual(self.powered_calls, [])

    def test_wifi_switched_on_in_airplane_mode_stays_on(self):
        radio_root(self.root)
        self.switch(True)
        helper.rfkill("wlan", False, self.root)  # the WiFi tile
        self.switch(True)  # again: the first note is kept
        with open(self.radios) as f:
            self.assertEqual(helper.parse_radios(f.read()), ["bluetooth", "wlan"])
        self.switch(False)
        self.assertEqual(soft(self.root, "rfkill1"), "0")

    def test_parse_radios_and_arguments(self):
        self.assertEqual(helper.parse_radios("wlan bluetooth nfc\n"), ["wlan", "bluetooth"])
        self.assertEqual(helper.parse_radios(None), [])
        self.assertIsNone(helper.bluez_powered("../evil"))
        self.assertEqual(helper.main(["airplane", "maybe"]), 2)
        self.assertEqual(helper.main(["bluetooth"]), 2)
        self.assertFalse(system.read_airplane(os.path.join(self.tmp.name, "none")))


class Backlight(unittest.TestCase):
    def test_percent_both_ways(self):
        self.assertEqual(system.level_to_percent(1415, 2047), 69)
        self.assertEqual(system.level_to_percent(0, 2047), 1)
        self.assertIsNone(system.level_to_percent(5, 0))
        self.assertEqual(helper.percent_to_level(69, 2047), 1412)
        self.assertEqual(helper.percent_to_level(0, 2047), 20)
        self.assertEqual(helper.percent_to_level(100, 2047), 2047)
        self.assertEqual(helper.percent_to_level(500, 2047), 2047)
        self.assertEqual(helper.percent_to_level(1, 50), 1)

    def test_reads_the_first_panel(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(system.backlight_percent(root))
            write(os.path.join(root, "ae94000.dsi.0", "brightness"), "1023\n")
            write(os.path.join(root, "ae94000.dsi.0", "max_brightness"), "2047\n")
            self.assertEqual(system.backlight_percent(root), 50)


POSED = """# duo-posed
enabled = off   # dry run
auto_pose = on
auto_rotate = on
#posture.read = dual
"""


class Posed(unittest.TestCase):
    def test_parse_and_state(self):
        conf = system.parse_conf(POSED)
        self.assertEqual(conf["enabled"], "off")
        self.assertNotIn("posture.read", conf)
        self.assertEqual(system.auto_rotate_state(conf), "dry")
        self.assertEqual(system.auto_rotate_state({"enabled": "on", "auto_rotate": "on"}), "on")
        self.assertEqual(system.auto_rotate_state({"enabled": "on", "auto_rotate": "off"}), "off")

    def test_panels_choice_cycles(self):
        self.assertEqual(system.panels_choice({}), "both")
        self.assertEqual(system.panels_choice({"panels": "Left"}), "left")
        self.assertEqual(system.panels_choice({"panels": "dual"}), "both")
        self.assertEqual(
            [system.next_panels(p) for p in ("both", "left", "right", "x")],
            ["left", "right", "both", "both"],
        )
        self.assertIsNone(system.read_panels("/nonexistent/duo-posed.conf"))
        self.assertEqual(system.auto_rotate_state({}), "dry")

    def test_helper_sets_only_that_key(self):
        new = helper.set_conf_key(POSED, "auto_rotate", "off")
        self.assertIn("auto_rotate = off\n", new)
        self.assertEqual(system.parse_conf(new)["enabled"], "off")
        self.assertEqual(new.count("auto_rotate"), 1)
        # A commented-out key is taken over; a missing one appended.
        new = helper.set_conf_key("#auto_rotate = on\nx = 1\n", "auto_rotate", "off")
        self.assertEqual(new, "auto_rotate = off\nx = 1\n")
        self.assertEqual(
            helper.set_conf_key("x = 1\n", "auto_rotate", "on"), "x = 1\nauto_rotate = on\n"
        )
        # Nothing else in the file changes.
        self.assertEqual(helper.set_conf_key(POSED, "auto_rotate", "on"), POSED)

    def test_helper_refuses_anything_else(self):
        self.assertEqual(helper.main(["wifi", "maybe"]), 2)
        self.assertEqual(helper.main(["brightness", "-1"]), 2)
        # Digits of other scripts and superscripts pass str.isdigit(): not here.
        self.assertEqual(helper.main(["brightness", "\u00b2"]), 2)
        self.assertEqual(helper.main(["brightness", "\u0665\u0660"]), 2)
        self.assertEqual(helper.main(["rm", "-rf"]), 2)
        self.assertEqual(helper.main([]), 2)


class AutoBrightness(unittest.TestCase):
    """duo-brightness's switch: /etc/duo2/brightness.conf, auto = on|off."""

    def test_state(self):
        self.assertFalse(system.auto_brightness_on({}))
        self.assertTrue(system.auto_brightness_on({"auto": "On"}))
        self.assertFalse(system.auto_brightness_on(system.parse_conf("# auto = on\nauto = off\n")))

    def test_helper_writes_the_switch(self):
        with tempfile.TemporaryDirectory() as root:
            old = helper.BRIGHTNESS_CONF
            helper.BRIGHTNESS_CONF = os.path.join(root, "duo2", "brightness.conf")
            try:
                self.assertTrue(helper.auto_brightness(True))
                with open(helper.BRIGHTNESS_CONF) as f:
                    self.assertTrue(system.auto_brightness_on(system.parse_conf(f.read())))
                self.assertTrue(helper.auto_brightness(False))
                with open(helper.BRIGHTNESS_CONF) as f:
                    text = f.read()
                self.assertEqual(text.count("auto"), 1)
                self.assertFalse(system.auto_brightness_on(system.parse_conf(text)))
            finally:
                helper.BRIGHTNESS_CONF = old
        self.assertIsNotNone(helper.plan(["auto-brightness", "on"]))
        self.assertIsNone(helper.plan(["auto-brightness", "maybe"]))
        self.assertFalse(helper.needs_grant(["auto-brightness", "on"]))


class TouchRetry(unittest.TestCase):
    """duo-shell-helper touch retry: the touch daemon's notes go, it restarts."""

    def test_notes_removed_and_daemon_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            notes = (os.path.join(d, "duo2-touchd.fallback"), os.path.join(d, "duo2-touchd.reinit"))
            write(notes[0], "why\n")  # the re-init count is absent: fine
            calls = []

            class Done:
                returncode = 0

            ok = helper.touch_retry(notes, run=lambda argv, check: calls.append(argv) or Done())
            self.assertTrue(ok)
            self.assertFalse(any(os.path.exists(p) for p in notes))
            self.assertEqual(calls, [["/usr/bin/systemctl", "restart", "duo2-touchd.service"]])

    def test_only_the_fixed_form_is_accepted(self):
        self.assertEqual(helper.main(["touch", "anything"]), 2)


class Grants(unittest.TestCase):
    """The actions that matter need the shell's grant (redeemed on duo-wc's
    admin socket); without one nothing is done."""

    def test_which_need_one(self):
        for words in (
            ["wifi", "off"],
            ["bluetooth", "on"],
            ["mobile-data", "off"],
            ["airplane", "on"],
            ["touch", "retry"],
            ["reboot"],
            ["poweroff"],
        ):
            self.assertTrue(helper.needs_grant(words), words)
        for words in (["brightness", "40"], ["auto-rotate", "on"], ["panels", "left"]):
            self.assertFalse(helper.needs_grant(words), words)

    def test_no_grant_nothing_done(self):
        done = []
        old_plan, old_redeem, old_read, old_confirm = (
            helper.plan,
            helper.redeem,
            helper.read_token,
            helper.confirm,
        )
        helper.plan = lambda argv: lambda: done.append(argv) or True
        helper.read_token = lambda: "deadbeef"
        helper.confirm = lambda detail: False
        try:
            helper.redeem = lambda action, token: False
            self.assertEqual(helper.main(["reboot"]), 3)
            self.assertEqual(done, [])
            helper.redeem = lambda action, token: (action, token) == ("mobile-data off", "deadbeef")
            self.assertEqual(helper.main(["mobile-data", "off"]), 0)
            self.assertEqual(helper.main(["mobile-data", "on"]), 3)
            self.assertEqual(done, [["mobile-data", "off"]])
            # no grant (Duo Settings' WiFi switch): the user's yes will do
            helper.redeem = lambda action, token: False
            helper.confirm = lambda detail: detail == "An app asks to switch WiFi off."
            self.assertEqual(helper.main(["wifi", "off"]), 0)
            self.assertEqual(helper.main(["poweroff"]), 3)
            self.assertEqual(done[-1], ["wifi", "off"])
        finally:
            helper.plan, helper.redeem, helper.read_token, helper.confirm = (
                old_plan,
                old_redeem,
                old_read,
                old_confirm,
            )

    def test_redeem_without_duo_wc_is_no(self):
        old = helper.WC_ADMIN
        helper.WC_ADMIN = "/nonexistent/admin.sock"
        try:
            self.assertFalse(helper.redeem("reboot", "00" * 16))
            self.assertFalse(helper.redeem("reboot", ""))
        finally:
            helper.WC_ADMIN = old


if __name__ == "__main__":
    unittest.main()
