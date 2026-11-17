"""duo2-hotspot (the helper): its words, which need the shell's grant, the
first-use settings and password (on temporary paths)."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from duo2hotspot import conf  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "duo2_hotspot_cli", os.path.join(ROOT, "duo2-hotspot")
)
spec = importlib.util.spec_from_loader("duo2_hotspot_cli", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class Words(unittest.TestCase):
    def test_known(self):
        self.assertEqual(cli.parse(["status"]), ("status", {"bytes": False}))
        self.assertEqual(cli.parse(["on", "--wifi-off"]), ("on", {"wifi_off": True}))
        self.assertEqual(cli.parse(["usb", "on", "--force"]), ("usb-on", {"force": True}))
        self.assertEqual(
            cli.parse(["set", "band", "2.4"]), ("set", {"key": "BAND", "value": "2.4"})
        )
        self.assertEqual(cli.parse(["password", "--new"]), ("password", {"mode": "new"}))
        self.assertEqual(cli.parse(["password"]), ("password", {"mode": "show"}))

    def test_unknown(self):
        for argv in (
            [],
            ["on", "now"],
            ["set", "password", "x"],
            ["set", "ssid"],
            ["usb"],
            ["password", "secret"],
            ["status", "--all"],
            ["rm", "-rf", "/"],
        ):
            self.assertIsNone(cli.parse(argv), argv)

    def test_grants(self):
        g = {
            tuple(a): cli.grant_action(*cli.parse(a))
            for a in (
                ["on"],
                ["on", "--wifi-off"],
                ["off"],
                ["usb", "on"],
                ["usb", "on", "--force"],
                ["usb", "off"],
                ["status"],
                ["password"],
                ["password", "--new"],
                ["password", "--set"],
                ["set", "band", "5"],
                ["wifi-client", "on"],
            )
        }
        self.assertEqual(g[("on",)], "hotspot on")
        self.assertEqual(g[("on", "--wifi-off")], "hotspot on wifi-off")
        self.assertEqual(g[("usb", "on", "--force")], "hotspot usb on force")
        self.assertEqual(g[("password", "--new")], "hotspot password new")
        for free in (("off",), ("usb", "off"), ("status",), ("password",), ("set", "band", "5")):
            self.assertIsNone(g[free], free)
        # duo-wc's grants take letters, digits and dashes in words (grants.rs valid_action)
        import re

        for action in filter(None, g.values()):
            self.assertRegex(action, r"^[A-Za-z0-9-]+( [A-Za-z0-9-]+)*$")
            self.assertLessEqual(len(action), 64)
            self.assertIn(action, cli.QUESTIONS)
        self.assertTrue(all(re.match("^[a-z]", q) for q in cli.QUESTIONS.values()))


class FirstUse(unittest.TestCase):
    def test_settings_and_password_made_once(self):
        with tempfile.TemporaryDirectory() as d:
            paths = {
                "settings": os.path.join(d, "hs", "settings"),
                "password": os.path.join(d, "hs", "password"),
                "admin": os.path.join(d, "none.conf"),
            }
            user, admin, pw = cli.ensure(paths)
            self.assertTrue(conf.valid_ssid(user["SSID"]))
            self.assertTrue(conf.valid_password(pw))
            self.assertEqual(os.stat(paths["password"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(paths["settings"]).st_mode & 0o777, 0o600)
            self.assertEqual(admin["NEVER_CUT_WIFI"], "yes")
            again = cli.ensure(paths)
            self.assertEqual((again[0]["SSID"], again[2]), (user["SSID"], pw))
            view = cli.settings_view(user, admin)
            self.assertNotIn(pw, repr(view))
            self.assertTrue(view["never_cut_wifi"])


if __name__ == "__main__":
    unittest.main()
