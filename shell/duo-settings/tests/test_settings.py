"""Unit tests for Duo Settings' pure parts (python3 -m unittest discover -s tests)."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import (
    battery,
    conf,
    region,
    sim,
    sleep,
    soundmode,
    system,
    touchcal,
    wallpaper,
    wifi,
)  # noqa: E402


def load_helper():
    path = os.path.join(os.path.dirname(HERE), "duo-settings-helper")
    loader = importlib.machinery.SourceFileLoader("duo_settings_helper", path)
    spec = importlib.util.spec_from_loader("duo_settings_helper", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load_helper()


READY = {
    "modem": True,
    "present": True,
    "state": "ready",
    "lock": None,
    "facility": False,
    "left": 3,
}


class SimLockView(unittest.TestCase):
    def test_ready_sim(self):
        v = sim.lock_view(READY)
        self.assertEqual(
            (v["status"], v["lock_on"], v["can_toggle"], v["can_change"], v["left"]),
            ("Ready", False, True, False, 3),
        )
        v = sim.lock_view(dict(READY, facility=True, left=2))
        self.assertEqual(
            (v["lock_on"], v["can_toggle"], v["can_change"], v["left"]), (True, True, True, 2)
        )

    def test_nothing_to_switch(self):
        self.assertEqual(sim.lock_view(None)["status"], "No modem")
        self.assertEqual(sim.lock_view({"modem": True, "present": False})["status"], "No SIM")
        # unknown lock state, no attempt left, a modem not ready: no switch
        self.assertFalse(sim.lock_view(dict(READY, facility=None))["can_toggle"])
        self.assertFalse(sim.lock_view(dict(READY, left=0))["can_toggle"])
        self.assertFalse(sim.lock_view(dict(READY, state="failed"))["can_toggle"])

    def test_a_waiting_sim_is_the_shells(self):
        v = sim.lock_view(dict(READY, state="locked", lock="sim-pin", facility=None))
        self.assertEqual(
            (v["status"], v["lock_on"], v["can_toggle"]), ("Waiting for its PIN", True, False)
        )
        self.assertEqual(
            sim.lock_view(dict(READY, state="locked", lock="sim-puk"))["status"],
            "Waiting for its PUK",
        )

    def test_pins(self):
        self.assertEqual(
            [
                sim.valid_sim_pin(p)
                for p in ("1234", "12345678", "123", "123456789", "12a4", "", None)
            ],
            [True, True, False, False, False, False, False],
        )
        self.assertTrue(
            sim.wrong_pin(
                "GDBus.Error:org.freedesktop.ModemManager1.Error.MobileEquipment."
                "IncorrectPassword: Incorrect password"
            )
        )
        self.assertFalse(sim.wrong_pin("WrongState: The SIM is locked"))
        self.assertFalse(sim.wrong_pin(None))


class SimSlots(unittest.TestCase):
    # duo2-sim slots after `use 2`: the tray inactive, the eSIM logical slot 1
    SLOTS = [
        {"slot": 1, "card": True, "esim": False, "active": False, "logical": None, "in_use": False},
        {"slot": 2, "card": True, "esim": True, "active": True, "logical": 1, "in_use": True},
    ]

    def test_in_use_and_off(self):
        v = sim.slot_view(self.SLOTS)
        self.assertEqual(v["2"], {"card": True, "in_use": True, "state": "in use"})
        self.assertEqual(v["1"]["state"], "off")

    def test_no_card(self):
        slots = [dict(self.SLOTS[0], card=False), self.SLOTS[1]]
        self.assertEqual(sim.slot_view(slots)["1"]["state"], "no card")

    def test_unreadable(self):
        for bad in (None, [], "x", [{"slot": 3}], [1]):
            self.assertIsNone(sim.slot_view(bad), bad)


class Conf(unittest.TestCase):
    def test_replace_keeps_comments(self):
        text = "# scale of the panels\nscale = 2.5\nhinge_gap = on\n"
        self.assertEqual(
            conf.set_key(text, "scale", "3"), "# scale of the panels\nscale = 3\nhinge_gap = on\n"
        )

    def test_commented_then_appended(self):
        self.assertEqual(
            conf.set_key("#idle_timeout = 60\n", "idle_timeout", "120"), "idle_timeout = 120\n"
        )
        self.assertEqual(conf.set_key("a = 1\n", "b", "2"), "a = 1\nb = 2\n")

    def test_helper_twin_agrees(self):
        for text in ("", "x = 1\n", "# y = 2\nx = 1\n", "y=5\n"):
            self.assertEqual(conf.set_key(text, "y", "3"), helper.set_conf_key(text, "y", "3"))

    def test_is_on(self):
        self.assertTrue(conf.is_on("On"))
        self.assertFalse(conf.is_on("off"))
        self.assertTrue(conf.is_on(None, True))


class Wifi(unittest.TestCase):
    SCAN = (
        "bssid / frequency / signal level / flags / ssid\n"
        "aa:bb:cc:dd:ee:01\t5180\t-48\t[WPA2-PSK-CCMP][ESS]\tHome\n"
        "aa:bb:cc:dd:ee:02\t2412\t-70\t[WPA2-PSK-CCMP][ESS]\tHome\n"
        "aa:bb:cc:dd:ee:03\t2437\t-80\t[ESS]\tCaf\\xc3\\xa9 Free\n"
        "aa:bb:cc:dd:ee:04\t2462\t-60\t[WPA2-PSK+SAE-CCMP][ESS]\tMixed\n"
        "aa:bb:cc:dd:ee:05\t2462\t-61\t[WPA2-EAP-CCMP][ESS]\tWork\n"
        "aa:bb:cc:dd:ee:06\t2462\t-62\t[WPA2-PSK-CCMP][ESS]\t\n"
    )

    def test_scan_dedup_and_order(self):
        nets = wifi.parse_scan(self.SCAN)
        self.assertEqual([n["ssid"] for n in nets], ["Home", "Mixed", "Work", "Café Free"])
        self.assertEqual(nets[0]["signal"], -48)
        self.assertEqual([n["security"] for n in nets], ["wpa2", "wpa2/wpa3", "enterprise", "open"])

    def test_security(self):
        self.assertEqual(wifi.security("[SAE-CCMP][ESS]"), "wpa3")
        self.assertEqual(wifi.security("[WEP][ESS]"), "wep")

    def test_networks_and_status(self):
        nets = wifi.parse_networks(
            "network id / ssid / bssid / flags\n0\tHome\tany\t[CURRENT]\n"
            "1\tOld\tany\t[DISABLED]\n2\tBad\tany\t[TEMP-DISABLED]\n"
        )
        self.assertEqual(
            [(n["id"], n["current"], n["disabled"], n["temp_disabled"]) for n in nets],
            [(0, True, False, False), (1, False, True, False), (2, False, False, True)],
        )
        st = wifi.parse_status("wpa_state=COMPLETED\nssid=Caf\\xc3\\xa9\nip_address=192.0.2.2\n")
        self.assertEqual(st["ssid"], "Café")

    def test_psk_vector(self):
        # IEEE 802.11i annex H test vector.
        self.assertEqual(
            wifi.wpa_psk("IEEE", "password"),
            "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e",
        )

    def test_commands(self):
        cmds = wifi.network_commands(3, "Home", "wpa2", "secret12")
        self.assertIn("SET_NETWORK 3 ssid 486f6d65", cmds)
        self.assertIn("SET_NETWORK 3 key_mgmt WPA-PSK", cmds)
        self.assertFalse(any("secret12" in c for c in cmds))  # only the derived key
        self.assertIn("SET_NETWORK 1 key_mgmt NONE", wifi.network_commands(1, "x", "open", ""))
        self.assertTrue(
            any(
                c.startswith("SET_NETWORK 1 sae_password")
                for c in wifi.network_commands(1, "x", "wpa3", "abcdefgh")
            )
        )
        with self.assertRaises(ValueError):
            wifi.network_commands(1, "x", "enterprise", "")

    def test_passphrase_rules(self):
        self.assertEqual(wifi.passphrase_problem("short", "wpa2"), "length")
        self.assertIsNone(wifi.passphrase_problem("long enough", "wpa2"))
        self.assertEqual(wifi.passphrase_problem('with"quote', "wpa3"), "chars")
        self.assertIsNone(wifi.passphrase_problem("", "open"))

    def test_bars(self):
        self.assertEqual([wifi.bars(v) for v in (-40, -60, -70, -80, -90)], [4, 3, 2, 1, 0])


class System(unittest.TestCase):
    def test_volume(self):
        self.assertEqual(system.parse_wpctl_volume("Volume: 0.25"), (25, False))
        self.assertEqual(system.parse_wpctl_volume("Volume: 0.40 [MUTED]"), (40, True))
        self.assertEqual(system.parse_wpctl_volume(""), (None, False))

    def test_sinks(self):
        self.assertEqual(
            system.parse_sinks('[{"name": "a", "description": "Speakers"}, {"name": "b"}]'),
            [("a", "Speakers"), ("b", "b")],
        )
        self.assertEqual(system.parse_sinks("nonsense"), [])

    def test_busctl(self):
        self.assertEqual(
            system.parse_busctl_props(("Timezone", "NTP"), 's "Europe/Berlin"\nb true\n'),
            {"Timezone": "Europe/Berlin", "NTP": True},
        )

    def test_battery_health(self):
        self.assertEqual(system.battery_health("4000", "4500"), 89)
        self.assertIsNone(system.battery_health(None, "4500"))

    def test_pin(self):
        self.assertTrue(system.valid_pin("1234"))
        self.assertFalse(system.valid_pin("123"))
        self.assertFalse(system.valid_pin("12ab"))
        self.assertFalse(system.valid_pin("\u0661\u0662\u0663\u0664"))

    def test_lock_policy(self):
        self.assertEqual(system.parse_lock_policy(""), system.LOCK_DEFAULTS)
        p = system.parse_lock_policy("pin = yes\nlock_on_blank = on\nlock_on_close = off\nx = 1\n")
        self.assertEqual(p, {"pin": "yes", "lock_on_blank": "on", "lock_on_close": "off"})
        self.assertEqual(system.parse_lock_policy("pin = maybe")["pin"], "no")
        self.assertEqual(system.lock_policy("/nonexistent/policy"), system.LOCK_DEFAULTS)

    def test_lock_answers(self):
        self.assertEqual(system.lock_answer(0, ""), (True, None))
        self.assertEqual(system.lock_answer(4, "no-pin"), (True, None))
        self.assertEqual(system.lock_answer(1, ""), (False, "wrong"))
        self.assertEqual(system.lock_answer(3, "wait 60\n"), (False, "wait 60"))
        self.assertEqual(system.lock_answer(3, ""), (False, "wait 30"))
        self.assertEqual(system.lock_answer(2, ""), (False, "invalid"))
        self.assertEqual(system.lock_answer(-1, ""), (False, "failed"))
        self.assertEqual(system.lock_answer(5, "x\n"), (False, "encrypted"))
        self.assertEqual(
            system.lock_answer(6, "a line\nthe home is not encrypted\n"),
            (False, "refused the home is not encrypted"),
        )
        self.assertEqual(system.lock_answer(6, ""), (False, "refused"))

    def test_home_encryption_view(self):
        st = system.parse_home_state(
            "# x\nstate = encrypting\nprogress = 42\nrecovery = yes\ndetail = a # b\nnope = 1\n"
        )
        self.assertEqual(
            st, {"state": "encrypting", "progress": 42, "detail": "a # b", "recovery": "yes"}
        )
        self.assertEqual(system.parse_home_state("state = weird")["state"], "plain")
        plain = {"state": "plain", "supported": True}
        v = system.home_view(plain, True)
        self.assertEqual((v["active"], v["sensitive"], v["restart"]), (False, True, False))
        self.assertFalse(system.home_view(plain, False)["sensitive"])  # a PIN first
        self.assertFalse(system.home_view({"state": "plain"}, True)["sensitive"])  # no kernel
        v = system.home_view({"state": "encrypt-pending"}, True)
        self.assertTrue(v["active"] and v["restart"] and v["cancel"] and v["recovery_row"])
        v = system.home_view({"state": "encrypting", "progress": 7}, True)
        self.assertFalse(v["sensitive"])
        self.assertIn("7", v["subtitle"])
        v = system.home_view({"state": "encrypted", "detail": "Waiting: low battery."}, True)
        self.assertTrue(v["active"] and not v["restart"])
        self.assertIn("Waiting: low battery.", v["subtitle"])
        v = system.home_view({"state": "decrypt-pending"}, True)
        self.assertTrue(v["restart"] and not v["active"])

    def test_home_state_matches_homecrypt(self):
        top = os.path.join(os.path.dirname(HERE), "..", "..", "homecrypt")
        if not os.path.isdir(top):
            self.skipTest("no homecrypt/")
        sys.path.insert(0, os.path.abspath(top))
        try:
            from duohomecrypt import keys
            from duohomecrypt import store as hstore
        finally:
            sys.path.pop(0)
        self.assertEqual(system.HOME_STATES, hstore.STATES)
        self.assertEqual(system.RECOVERY_DIGITS, keys.RECOVERY_DIGITS)
        text = hstore.format_state(
            dict(hstore.parse_state(""), state="encrypted", progress=100, recovery="yes")
        )
        st = system.parse_home_state(text)
        self.assertEqual((st["state"], st["progress"], st["recovery"]), ("encrypted", 100, "yes"))
        self.assertEqual(system.HOME_STATE, hstore.PATHS.state_file)

    def test_lock_policy_matches_duo_lock(self):
        shell_dir = os.path.join(os.path.dirname(HERE), "..", "duo-shell")
        sys.path.insert(0, os.path.abspath(shell_dir))
        try:
            from duoshell import lockstore
        except ImportError:
            self.skipTest("duo-shell not next to duo-settings")
        self.assertEqual(system.LOCK_POLICY, lockstore.POLICY_FILE)
        self.assertEqual(system.LOCK_HELPER, lockstore.HELPER)
        for text in ("", "pin = yes\nlock_on_blank = on\n", "lock_on_close = off\n"):
            self.assertEqual(system.parse_lock_policy(text), lockstore.parse_policy(text))
        self.assertEqual(
            (
                system.LOCK_OK,
                system.LOCK_WRONG,
                system.LOCK_INVALID,
                system.LOCK_WAIT,
                system.LOCK_NO_PIN,
            ),
            (lockstore.OK, lockstore.WRONG, lockstore.INVALID, lockstore.WAIT, lockstore.NO_PIN),
        )

    def test_drawer_view_matches_the_shell(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "launcher.conf")
            self.assertEqual(system.drawer_view(path), "grid")
            system.set_drawer_view("list", path)
            self.assertEqual(system.drawer_view(path), "list")
            shell_dir = os.path.join(os.path.dirname(HERE), "..", "duo-shell")
            sys.path.insert(0, os.path.abspath(shell_dir))
            try:
                from duoshell import launchermodel
            except ImportError:
                self.skipTest("duo-shell not next to duo-settings")
            self.assertEqual(launchermodel.read_view(path), "list")
            launchermodel.write_view("grid", path)
            self.assertEqual(system.drawer_view(path), "grid")

    def test_scale_and_idle(self):
        self.assertEqual(system.normalise_scale(2.5), "2.5")
        self.assertEqual(system.normalise_scale("2.6"), "2.5")
        self.assertEqual(system.normalise_scale(None), "2.5")
        self.assertEqual(system.idle_label(0), "Never")
        self.assertEqual(system.idle_label(120), "2 minutes")
        self.assertTrue(system.zone_matches("America/New_York", "new york"))
        self.assertFalse(system.zone_matches("Europe/Budapest", "york"))
        self.assertEqual(system.human_bytes(6_300_000_000), "6.3 GB")


class Touch(unittest.TestCase):
    def taps(self, true_map, current, noise=0.0):
        """Taps on five targets as touch-calib.py logs them, for a panel
        whose raw touch is `true_map`^-1 of the target, seen through `current`."""
        w, h = 538.0, 757.0
        inv = touchcal.invert(true_map)
        rows = []
        k = 0
        for fx, fy in [(0.1, 0.1), (0.9, 0.1), (0.5, 0.5), (0.1, 0.9), (0.9, 0.9)]:
            for _ in range(2):
                raw = touchcal.apply(inv, fx, fy)
                px, py = touchcal.apply(current, *raw)
                d = noise * (1 if k % 2 else -1)
                k += 1
                rows.append((fx * w, fy * h, px * w + d, py * h - d, w, h))
        return rows

    def test_recovers_stretch(self):
        truth = (0.9565, 0.0252, 0.0043, 0.0, 1.0, 0.0)
        new, report = touchcal.fit(self.taps(truth, touchcal.IDENTITY), touchcal.IDENTITY)
        for a, b in zip(new[:3], truth[:3]):
            self.assertAlmostEqual(a, b, places=4)
        self.assertTrue(report["x"][2])
        self.assertFalse(report["y"][2])  # y was right: kept
        self.assertEqual(new[3:], (0.0, 1.0, 0.0))

    def test_already_calibrated_is_kept(self):
        m = (0.95, 0.02, 0.004, 0, 1, 0)
        new, report = touchcal.fit(self.taps(m, m, noise=1.5), m)
        self.assertEqual(new, m)
        self.assertFalse(report["x"][2] or report["y"][2])

    def test_parse(self):
        rows = touchcal.parse_log("53.8 75.7 60.1 80.0 538 757\nbad line\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(touchcal.parse_matrix("0.95651 0.02520 0.00433 0 1 0")[0], 0.95651)
        self.assertEqual(touchcal.parse_matrix(""), touchcal.IDENTITY)
        self.assertEqual(
            touchcal.format_matrix((0.95651, 0.0252, 0.00433, 0, 1, 0)),
            "0.95651 0.0252 0.00433 0 1 0",
        )
        with self.assertRaises(ValueError):
            touchcal.fit(rows, touchcal.IDENTITY)


class Helper(unittest.TestCase):
    def test_zone(self):
        self.assertFalse(helper.valid_zone("../../etc/passwd"))
        self.assertFalse(helper.valid_zone("Europe/Budapest; reboot"))
        if os.path.exists("/usr/share/zoneinfo/Europe/Budapest"):
            self.assertTrue(helper.valid_zone("Europe/Budapest"))

    def test_matrix(self):
        self.assertTrue(helper.valid_matrix("0.95651 0.0252 0.00433 0 1 0".split()))
        self.assertFalse(helper.valid_matrix("0 1 0 1 0 0".split()))  # a swap, not a fix
        self.assertFalse(helper.valid_matrix("1 0 0 0 1 0 0".split()))
        self.assertFalse(helper.valid_matrix("1e0 0 0 0 1 0".split()))

    def test_rules(self):
        text = helper.touch_rules("", "left", ["0.95", "0", "0.01", "0", "1", "0"])
        self.assertIn('ATTRS{name}=="Surface Duo 2 Touchscreen Left"', text)
        self.assertIn('LIBINPUT_CALIBRATION_MATRIX}="0.95 0 0.01 0 1 0"', text)
        both = helper.touch_rules(text, "right", ["1", "0", "0", "0", "1", "0"])
        self.assertEqual(both.count("ACTION"), 2)
        self.assertEqual(
            helper.touch_rules(helper.touch_rules(both, "left", None), "right", None), ""
        )

    def test_laptop_requests(self):
        self.assertEqual(helper.laptop_request(["on"]), {"laptop": "on"})
        self.assertEqual(helper.laptop_request(["toggle"]), {"laptop": "toggle"})
        self.assertEqual(helper.laptop_request(["auto", "off"]), {"auto": "off"})
        self.assertEqual(helper.laptop_request(["keys", "DSI-1"]), {"keys": "DSI-1"})
        for bad in (
            [],
            ["maybe"],
            ["keys", "DP-1"],
            ["auto", "yes"],
            ["on", "off"],
            ["keys", "DSI-1", "x"],
        ):
            self.assertIsNone(helper.laptop_request(bad), bad)

    def test_display_requests(self):
        self.assertEqual(helper.display_request(["on_connect", "ask"]), {"on_connect": "ask"})
        self.assertEqual(helper.display_request(["on_connect", "mirror"]), {"on_connect": "mirror"})
        self.assertEqual(helper.display_request(["mirror", "both"]), {"mirror": "both"})
        self.assertEqual(helper.display_request(["mirror", "DSI-2"]), {"mirror": "DSI-2"})
        for bad in (
            [],
            ["on_connect"],
            ["on_connect", "span"],
            ["mirror", "DP-1"],
            ["mirror", "both", "x"],
            ["use", "desk"],
        ):
            self.assertIsNone(helper.display_request(bad), bad)

    def test_wc_keys(self):
        self.assertTrue(helper.WC_KEYS["scale"]("2.25"))
        self.assertFalse(helper.WC_KEYS["scale"]("9"))
        self.assertFalse(helper.WC_KEYS["idle_timeout"]("-1"))
        # Only ASCII digits reach duo-wc.conf (a superscript passes isdigit()).
        self.assertTrue(helper.WC_KEYS["idle_timeout"]("120"))
        self.assertFalse(helper.WC_KEYS["idle_timeout"]("\u00b2"))
        self.assertFalse(helper.WC_KEYS["idle_timeout"]("\u0661\u0662"))
        self.assertFalse(helper.WC_KEYS["idle_timeout"]("9" * 30))
        self.assertEqual(helper.main(["pen", "strength", "\u00b2"]), 2)


GEN = ["de_DE.UTF-8", "en_GB.UTF-8", "en_US.UTF-8", "hu_HU.UTF-8"]


class LocaleHelper(unittest.TestCase):
    def test_generated_list(self):
        listing = (
            "C\nC.utf8\nPOSIX\nen_GB.utf8\nen_GB.utf8@mon\nen_US.UTF-8\nhu_HU.utf8\nsr_RS@latin\n"
        )
        self.assertEqual(
            helper.generated_locales(listing), ["en_GB.UTF-8", "en_US.UTF-8", "hu_HU.UTF-8"]
        )

    def test_args_accepted(self):
        self.assertTrue(helper.valid_locale_args("en_US.UTF-8", "en_GB.UTF-8", "mon", GEN))
        self.assertTrue(helper.valid_locale_args("hu_HU.UTF-8", "hu_HU.UTF-8", "auto", GEN))

    def test_args_refused(self):
        bad = [
            ("fr_FR.UTF-8", "en_GB.UTF-8", "mon"),  # not generated
            ("en_US.UTF-8", "en_GB.UTF-8", "tue"),  # not a day
            ("../../etc/passwd", "en_GB.UTF-8", "mon"),
            ("en_US.UTF-8", "en_GB.UTF-8@mon", "mon"),  # a modifier is ours to add
            ("en_US.UTF-8", "en_GB.UTF-8\nLD_PRELOAD=x", "mon"),
            ("en_US.UTF-8", "en_GB", "mon"),
            ("C.UTF-8", "en_GB.UTF-8", "auto"),
            ("", "", ""),
            ("en_US.UTF-8 ", "en_GB.UTF-8", "mon"),
        ]
        for lang, reg, day in bad:
            self.assertFalse(helper.valid_locale_args(lang, reg, day, GEN), (lang, reg, day))

    def test_time_source(self):
        files = {
            "en_GB": 'comment_char %\nescape_char /\nLC_CTYPE\ncopy "i18n"\nEND LC_CTYPE\n'
            'LC_TIME\nabday "Sun"\nweek    7;19971130;4\nfirst_weekday 2\nEND LC_TIME\n',
            "xx_XX": 'LC_TIME\ncopy "en_GB"\nEND LC_TIME\n',
        }
        src = helper.time_source(files.__getitem__, "xx_XX", "1")
        self.assertIn("first_weekday 1", src)
        self.assertNotIn("first_weekday 2", src)
        self.assertIn("week    7;19971130;4", src)
        self.assertTrue(src.endswith("END LC_TIME\n"))
        self.assertNotIn("LC_CTYPE", src)
        with self.assertRaises(ValueError):
            helper.time_source({"a": "LC_CTYPE\nEND LC_CTYPE\n"}.__getitem__, "a", "1")

    def test_conf_text(self):
        text = helper.locale_conf_text("en_US.UTF-8", "en_GB.UTF-8", "en_GB.UTF-8@sun")
        self.assertIn("LANG=en_US.UTF-8\n", text)
        self.assertIn("LC_TIME=en_GB.UTF-8@sun\n", text)
        self.assertIn("LC_MEASUREMENT=en_GB.UTF-8\n", text)


class Region(unittest.TestCase):
    def test_state(self):
        conf_text = "# c\nLANG=en_US.UTF-8\nLC_TIME=hu_HU.UTF-8@mon\n"
        self.assertEqual(
            region.state(conf_text, "first_weekday = mon\n", GEN),
            ("en_US.UTF-8", "hu_HU.UTF-8", "mon"),
        )

    def test_state_fallbacks(self):
        self.assertEqual(region.state("", "", GEN), ("en_US.UTF-8", "en_GB.UTF-8", "auto"))
        self.assertEqual(region.state("LANG=C.UTF-8\n", "first_weekday = xx\n", GEN)[2], "auto")

    def test_names(self):
        self.assertEqual(region.code("en_GB.UTF-8@mon"), "en_GB")
        self.assertEqual(region.native_name("hu_HU.UTF-8"), "Magyar (Magyarország)")
        self.assertEqual(region.native_name("xx_YY.UTF-8"), "xx_YY")
        self.assertEqual(
            [l for l, _n in region.choices(["hu_HU.UTF-8", "de_DE.UTF-8"])],
            ["de_DE.UTF-8", "hu_HU.UTF-8"],
        )


class Wallpaper(unittest.TestCase):
    def test_write_keeps_other_keys_and_is_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "wallpaper.conf")
            wallpaper.write({"source": "file", "file": "/a.jpg"}, path)
            wallpaper.write({"mode": "span", "file": "/b.jpg"}, path)
            self.assertEqual(
                wallpaper.read(path), {"source": "file", "file": "/b.jpg", "mode": "span"}
            )
            self.assertEqual(os.listdir(os.path.dirname(path)), ["wallpaper.conf"])

    def test_selection(self):
        items = ["/x/a.jpg"]
        self.assertEqual(wallpaper.selection({}, items), "default")
        self.assertEqual(
            wallpaper.selection({"source": "file", "file": "/x/a.jpg"}, items), "/x/a.jpg"
        )
        self.assertEqual(
            wallpaper.selection({"source": "file", "file": "/home/u/mine.png"}, items), "file"
        )
        self.assertEqual(wallpaper.mode_index({"mode": "span"}), 1)
        self.assertEqual(wallpaper.mode_index({"mode": "x"}), 0)

    def test_market_from_locale(self):
        self.assertEqual(wallpaper.market_from_locale("hu_HU.UTF-8"), "hu-HU")
        self.assertEqual(wallpaper.market_from_locale("en_NZ.UTF-8"), "en-US")
        self.assertEqual(wallpaper.market_from_locale("fr_BE"), "fr-FR")
        self.assertEqual(wallpaper.market_from_locale(None), "en-US")
        self.assertEqual(wallpaper.current_market({"bing_market": "ja-JP"}, "hu_HU"), "ja-JP")
        self.assertEqual(wallpaper.current_market({"bing_market": "zz-ZZ"}, "hu_HU"), "hu-HU")

    def test_builtin_lists_only_what_exists(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "amber-d.jxl"), "wb").close()
            self.assertEqual(wallpaper.builtin(d), [(os.path.join(d, "amber-d.jxl"), "Amber")])

    def test_caption(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(wallpaper.bing_caption(d), ("", ""))
            with open(os.path.join(d, "current.json"), "w") as f:
                f.write('{"title": "T", "copyright": "C"}')
            self.assertEqual(wallpaper.bing_caption(d), ("T", "C"))


class HelperTrust(unittest.TestCase):
    """What the root helper lets ANY app of the user do (it is reachable with
    sudo -n by every program of the GUI user): display preferences, never
    switching the lock off (docs/hardware/security.md)."""

    def calls(self, argv):
        done = []
        saved = (helper.edit_conf, helper.wc_admin)
        helper.edit_conf = lambda path, key, value, mode=0o644: done.append(("conf", key, value))
        helper.wc_admin = lambda request, **args: done.append((request, args)) or True
        try:
            code = helper.main(argv)
        finally:
            helper.edit_conf, helper.wc_admin = saved
        return code, done

    def test_the_lock_cannot_be_switched_off(self):
        code, done = self.calls(["wc-conf", "session_lock", "off"])
        self.assertEqual(code, 2)
        self.assertEqual(done, [])

    def test_the_lock_can_be_switched_on_live(self):
        code, done = self.calls(["wc-conf", "session_lock", "on"])
        self.assertEqual(code, 0)
        self.assertEqual(
            done, [("conf", "session_lock", "on"), ("set_session_lock", {"session_lock": "on"})]
        )

    def test_preferences_apply_live(self):
        self.assertEqual(
            self.calls(["wc-conf", "idle_timeout", "120"])[1][1],
            ("set_idle_timeout", {"seconds": 120}),
        )
        self.assertEqual(
            self.calls(["wc-conf", "hinge_gap", "off"])[1][1], ("set_hinge_gap", {"gap": "off"})
        )
        # The scale is only saved (a restart applies it).
        self.assertEqual(self.calls(["wc-conf", "scale", "2.5"])[1], [("conf", "scale", "2.5")])

    def test_pose(self):
        self.assertEqual(
            self.calls(["pose", "single", "DSI-2"]),
            (0, [("set_pose", {"pose": "single", "output": "DSI-2"})]),
        )
        self.assertEqual(self.calls(["pose", "dual"])[1], [("set_pose", {"pose": "dual"})])
        self.assertEqual(self.calls(["pose", "single", "DSI-3"]), (2, []))
        self.assertEqual(self.calls(["pose", "unlock"]), (2, []))

    def test_nothing_else_reaches_duo_wc(self):
        for argv in (
            ["wc", "unlock"],
            ["wc-conf", "admin_users", "alice"],
            ["wc-conf", "sensor_users", "alice"],
            ["unlock"],
            ["screenshot", "DSI-1"],
        ):
            self.assertEqual(self.calls(argv), (2, []), argv)


class BatteryUsage(unittest.TestCase):
    """The Battery page's reading of the collector's report (battery/)."""

    REPORT = {
        "version": 1,
        "generated": 1_800_000_000.0,
        "window": {"start": 1_799_990_000.0, "reason": "full"},
        "battery": {
            "level": 64,
            "charger": False,
            "status": "Discharging",
            "on_battery_s": 9000,
            "used_pct": 36.0,
            "used_mah": 200.0,
            "charger_mah": 50.0,
            "remaining_h": 4.5,
        },
        "entries": {
            "battery": [
                {"key": "comp:screen", "mah": 100.0, "extra": {"panel0_s": 3600}},
                {
                    "key": "app:org.gnome.Calls",
                    "mah": 30.0,
                    "cpu_mah": 30.0,
                    "extra": {"fg_s": 60, "cpu0_s": 10},
                },
                {
                    "key": "exe:gnome-calls",
                    "mah": 20.0,
                    "cpu_mah": 20.0,
                    "extra": {"cpu0_s": 30, "bg_s": 900},
                },
                {
                    "key": "android:com.example.game",
                    "mah": 49.5,
                    "gpu_mah": 40.0,
                    "extra": {"gpu_s": 600},
                },
                {"key": "system:kernel", "mah": 0.5, "extra": {"irq_s": 3}},
            ],
            "charger": [{"key": "comp:screen", "mah": 50.0, "extra": {"panel0_s": 600}}],
        },
        "calibration": {"battery": {"groups": 12, "ratio": 1.25}},
    }

    @staticmethod
    def lookup(kind, value):
        known = {
            ("id", "org.gnome.Calls"): ("org.gnome.Calls", "Calls", "phone-symbolic"),
            ("exe", "gnome-calls"): ("org.gnome.Calls", "Calls", "phone-symbolic"),
        }
        return known.get((kind, value))

    def rows(self, include_charger=False):
        merged = battery.merge_entries(self.REPORT["entries"], include_charger)
        return battery.ranked(battery.resolve(merged, self.lookup))

    def test_an_app_in_front_and_behind_is_one_row(self):
        rows, total = self.rows()
        self.assertEqual(total, 200.0)
        calls = [r for r in rows if r["name"] == "Calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["mah"], calls[0]["share"]), (50.0, 25.0))
        self.assertEqual(calls[0]["extra"]["cpu0_s"], 40)
        self.assertEqual(sorted(calls[0]["keys"]), ["app:org.gnome.Calls", "exe:gnome-calls"])

    def test_shares_sum_to_100_and_sections(self):
        rows, _t = self.rows()
        self.assertAlmostEqual(sum(r["share"] for r in rows), 100.0)
        self.assertEqual(
            [r["name"] for r in rows], ["Screen", "Calls", "com.example.game", "Kernel"]
        )
        self.assertEqual([r["section"] for r in rows], ["system", "apps", "apps", "system"])
        self.assertEqual(
            [battery.pct_label(r["share"]) for r in rows], ["50 %", "25 %", "25 %", "<1 %"]
        )

    def test_the_charger_time_when_asked(self):
        rows, total = self.rows(include_charger=True)
        self.assertEqual(total, 250.0)
        self.assertEqual(rows[0]["mah"], 150.0)

    def test_texts(self):
        rows, _t = self.rows()
        calls = next(r for r in rows if r["name"] == "Calls")
        self.assertEqual(battery.row_subtitle(calls), "1 min on screen, CPU 40 s")
        self.assertIn(("Active in the background", "15 min"), battery.details(calls))
        self.assertIn(("  on the big cores", "0 s"), battery.details(calls))
        self.assertEqual(
            battery.summary_line(self.REPORT),
            ("36 % used", "2 h 30 min on battery since the last full charge, 200 mAh"),
        )
        self.assertEqual(
            battery.status_line(self.REPORT),
            ("64 %", "On battery", "About 4 h 30 min left at the recent rate"),
        )
        self.assertIn("80 %", battery.quality_line(self.REPORT))
        self.assertEqual(
            [battery.duration(s) for s in (5, 125, 3600, 3720, 200000)],
            ["5 s", "2 min", "1 h", "1 h 2 min", "2 d 7 h"],
        )
        self.assertEqual(battery.nbytes(350e6), "350.0 MB")

    def test_graph(self):
        rep = dict(
            self.REPORT,
            graph={
                "points": [[1_799_990_000, 100], [1_800_000_000, 64]],
                "charging": [],
                "screen": [[1_799_995_000, 1_800_000_000]],
            },
        )
        g = battery.graph_geometry(rep, 200, 100)
        self.assertEqual(g["line"], [(0.0, 0.0), (200.0, 36.0)])
        self.assertEqual(g["screen"], [(100.0, 200.0)])
        short = dict(rep, window={"start": 1_799_999_000.0})  # at least an hour on the axis
        self.assertEqual(battery.graph_geometry(short, 360, 100)["t0"], 1_800_000_000.0 - 3600)

    def test_exec_program(self):
        self.assertEqual(
            [
                battery.exec_program(c)
                for c in (
                    "gnome-calls %U",
                    "env GDK_BACKEND=wayland /usr/bin/foot",
                    "/usr/bin/python3 -sE /usr/local/bin/duo-settings",
                    "/usr/bin/flatpak run --branch=stable org.gnome.Maps",
                    "",
                )
            ],
            ["gnome-calls", "foot", "duo-settings", None, None],
        )

    def test_panels_lit(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(battery.panels_lit(d))  # no panels known: refresh
            for name, state in (
                ("card0-DSI-1", "Off"),
                ("card0-DSI-2", "Off"),
                ("card0-Writeback-1", "On"),
            ):
                os.makedirs(os.path.join(d, name))
                with open(os.path.join(d, name, "dpms"), "w") as f:
                    f.write(state + "\n")
            self.assertFalse(battery.panels_lit(d))
            with open(os.path.join(d, "card0-DSI-2", "dpms"), "w") as f:
                f.write("On\n")
            self.assertTrue(battery.panels_lit(d))

    def test_report_loading(self):
        self.assertIsNone(battery.load_report(text="{}"))
        self.assertIsNone(battery.load_report(text="not json"))
        self.assertIsNone(battery.load_report(path="/nonexistent/report.json"))
        self.assertEqual(battery.load_report(text='{"version": 1}'), {"version": 1})

    def test_helper_refresh_prints_the_new_report(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.json")
            calls = []

            class Done:
                returncode = 0

            def fake_run(argv, **_kw):
                calls.append(argv)
                with open(path, "w") as f:
                    f.write('{"version": 1}')
                return Done()

            saved = (helper.subprocess.run, helper.BATTERY_REPORT, sys.stdout)
            helper.subprocess.run, helper.BATTERY_REPORT = fake_run, path
            import io

            sys.stdout = out = io.StringIO()
            try:
                code = helper.main(["battery", "refresh"])
            finally:
                helper.subprocess.run, helper.BATTERY_REPORT, sys.stdout = saved
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), '{"version": 1}')
            self.assertEqual(
                calls,
                [
                    [
                        "systemctl",
                        "kill",
                        "--kill-whom=main",
                        "--signal=USR1",
                        "duo2-battery-stats.service",
                    ]
                ],
            )
            self.assertEqual(helper.main(["battery", "delete"]), 2)


class SoundMode(unittest.TestCase):
    def test_same_rule_as_the_shell(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "duo-shell"))
        from duoshell import hapticsmodel

        self.assertEqual(soundmode.MODES, hapticsmodel.MODES)
        for profile in ("full", "quiet", "silent", ""):
            for strength in (0.0, 0.6, 1.0):
                self.assertEqual(
                    soundmode.mode_of(profile, strength), hapticsmodel.mode_of(profile, strength)
                )
        for mode in soundmode.MODES:
            self.assertEqual(
                soundmode.settings_for(mode, 0.8), hapticsmodel.settings_for(mode, 0.8)
            )
            for on in (False, True):
                self.assertEqual(
                    soundmode.with_vibration(mode, on), hapticsmodel.with_vibration(mode, on)
                )

    def test_round_trip(self):
        for mode in soundmode.MODES:
            self.assertEqual(soundmode.mode_of(*soundmode.settings_for(mode, 0.6)), mode)
        self.assertEqual(soundmode.mode_of("full", 0.0), soundmode.SOUND)
        self.assertEqual(soundmode.with_vibration(soundmode.SILENT, True), soundmode.VIBRATION)


class DesktopActions(unittest.TestCase):
    """The desktop file's actions open a page (`duo-settings PAGE`; the home
    screen's battery card uses "battery"): each names a page the window can
    open (Window.open_tag: the page class's name, less "Page")."""

    def test_actions_open_existing_pages(self):
        import configparser
        import glob
        import re

        root = os.path.dirname(HERE)
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str
        ini.read(os.path.join(root, "org.duo2os.Settings.desktop"), encoding="utf-8")
        actions = [a for a in ini["Desktop Entry"].get("Actions", "").split(";") if a]
        self.assertIn("battery", actions)
        classes = set()
        for path in glob.glob(os.path.join(root, "duosettings", "*.py")):
            with open(path, encoding="utf-8") as f:
                classes.update(
                    m.lower() for m in re.findall(r"^class (\w+)Page\(Page\)", f.read(), re.M)
                )
        for action in actions:
            section = ini["Desktop Action %s" % action]
            self.assertTrue(section.get("Name"))
            prog, page = section["Exec"].split()
            self.assertEqual(prog, "duo-settings")
            self.assertIn(page.lower(), classes, action)


class AndroidKeepRunning(unittest.TestCase):
    """Settings > Android apps: the switch's file, as android/'s readers see it."""

    def idle_script(self):
        path = os.path.join(os.path.dirname(HERE), "..", "..", "android", "duo2-waydroid-idle")
        if not os.path.exists(path):
            self.skipTest("android/ not next to shell/")
        loader = importlib.machinery.SourceFileLoader("duo2_waydroid_idle", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod

    def test_off_by_default_and_saved(self):
        from duosettings import android

        with tempfile.TemporaryDirectory() as d:
            etc, user = os.path.join(d, "etc.conf"), os.path.join(d, "u", "duo2", "waydroid.conf")
            with open(etc, "w") as f:
                f.write("IDLE_MIN=5\n")
            self.assertFalse(android.keep_running(etc, user))
            android.set_keep_running(True, user)
            self.assertTrue(android.keep_running(etc, user))
            with open(user) as f:
                text = f.read()
            self.assertTrue(text.startswith("# duo2-os"))
            self.assertIn("KEEP_RUNNING = yes", text)
            with open(user, "a") as f:
                f.write("IDLE_MIN = 10\n")
            android.set_keep_running(False, user)
            with open(user) as f:
                text = f.read()
            self.assertIn("KEEP_RUNNING = no", text)
            self.assertIn("IDLE_MIN = 10", text)
            self.assertEqual(text.count("KEEP_RUNNING"), 1)
            self.assertFalse(android.keep_running(etc, user))

    def test_system_default_and_the_users_word(self):
        from duosettings import android

        with tempfile.TemporaryDirectory() as d:
            etc, user = os.path.join(d, "etc.conf"), os.path.join(d, "user.conf")
            with open(etc, "w") as f:
                f.write("KEEP_RUNNING=yes\n")
            self.assertTrue(android.keep_running(etc, user))
            android.set_keep_running(False, user)
            self.assertFalse(android.keep_running(etc, user))

    def test_the_idle_check_reads_the_same(self):
        from duosettings import android

        idle = self.idle_script()
        with tempfile.TemporaryDirectory() as d:
            etc, user = os.path.join(d, "etc.conf"), os.path.join(d, "user.conf")
            with open(etc, "w") as f:
                f.write("# x\nIDLE_MIN=5\n")
            for on in (True, False, True):
                android.set_keep_running(on, user)
                conf_ = idle.read_conf([etc, user])
                self.assertEqual(idle.keep_running(conf_), on)
                self.assertEqual(idle.idle_minutes(conf_), 5)

    def test_state(self):
        from duosettings import android

        self.assertTrue(android.frozen("populated 1\nfrozen 1\n"))
        self.assertFalse(android.frozen("populated 1\nfrozen 0\n"))
        self.assertFalse(android.frozen(""))
        with tempfile.TemporaryDirectory() as d:
            cg = os.path.join(d, "lxc.payload.waydroid")
            self.assertEqual(android.state(cg), "stopped")
            os.makedirs(cg)
            self.assertEqual(android.state(cg), "running")
            with open(os.path.join(cg, "cgroup.events"), "w") as f:
                f.write("populated 1\nfrozen 1\n")
            self.assertEqual(android.state(cg), "paused")


class Appearance(unittest.TestCase):
    """Settings > Appearance: the choice, the time of day, the Qt file."""

    COLORS = (
        "[ColorEffects:Disabled]\nColor=56,56,56\n\n[Colors:View]\nBackgroundNormal=255,255,255\n\n"
        "[Colors:Window]\nBackgroundNormal=239,240,241\n\n[General]\nName=Breeze Light\n\n[KDE]\nx=1\n"
    )

    def test_file_round_trip_and_defaults(self):
        from duosettings import appearance as ap

        self.assertEqual(ap.parse(""), ap.DEFAULT)
        prefs = ap.parse("mode = auto\nlight_from = 06:30 # morning\ndark_from=20:00\nmode=bogus\n")
        self.assertEqual(prefs, {"mode": "auto", "light_from": 390, "dark_from": 1200})
        self.assertEqual(ap.parse(ap.text_of(prefs)), prefs)
        self.assertEqual(ap.parse("light_from = 25:00\n")["light_from"], ap.DEFAULT["light_from"])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo2", "appearance.conf")
            self.assertEqual(ap.load(path), ap.DEFAULT)
            ap.save(prefs, path)
            self.assertEqual(ap.load(path), prefs)

    def test_scheme_by_time(self):
        from duosettings import appearance as ap

        auto = {"mode": "auto", "light_from": 7 * 60, "dark_from": 19 * 60}
        self.assertEqual(ap.scheme_for(auto, 6 * 60 + 59), "dark")
        self.assertEqual(ap.scheme_for(auto, 7 * 60), "light")
        self.assertEqual(ap.scheme_for(auto, 18 * 60 + 59), "light")
        self.assertEqual(ap.scheme_for(auto, 19 * 60), "dark")
        night_light = dict(auto, light_from=22 * 60, dark_from=6 * 60)  # over midnight
        self.assertEqual(ap.scheme_for(night_light, 23 * 60), "light")
        self.assertEqual(ap.scheme_for(night_light, 12 * 60), "dark")
        self.assertEqual(ap.scheme_for(dict(auto, mode="light"), 23 * 60), "light")
        self.assertEqual(ap.scheme_for(dict(auto, mode="dark"), 12 * 60), "dark")
        self.assertEqual(ap.gsettings_for("light")["color-scheme"], "prefer-light")
        self.assertEqual(
            ap.gsettings_for("dark"), {"color-scheme": "prefer-dark", "gtk-theme": "Adwaita-dark"}
        )

    def test_qt_file(self):
        from duosettings import appearance as ap

        text = ap.kdeglobals_light(self.COLORS)
        self.assertTrue(ap.is_ours(text))
        self.assertIn("ColorScheme=BreezeLight", text)
        self.assertIn("[Colors:View]\nBackgroundNormal=255,255,255", text)
        self.assertNotIn("ColorEffects", text)
        self.assertNotIn("Name=Breeze Light", text)
        self.assertEqual(ap.kde_action("light", None, self.COLORS), ("write", text))
        self.assertEqual(ap.kde_action("light", text, self.COLORS)[0], "keep")
        self.assertEqual(ap.kde_action("dark", text, self.COLORS), ("remove", None))
        self.assertEqual(ap.kde_action("dark", None, self.COLORS)[0], "keep")
        # a file the user made is never touched
        self.assertEqual(
            ap.kde_action("light", "[General]\nColorScheme=Mine\n", self.COLORS)[0], "keep"
        )
        self.assertEqual(
            ap.kde_action("dark", "[General]\nColorScheme=Mine\n", self.COLORS)[0], "keep"
        )
        self.assertEqual(ap.kde_action("light", None, None)[0], "keep")

    def test_tool(self):
        """duo-appearance's commands with the system parts stood in."""
        path = os.path.join(os.path.dirname(HERE), "duo-appearance")
        loader = importlib.machinery.SourceFileLoader("duo_appearance", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        tool = importlib.util.module_from_spec(spec)
        loader.exec_module(tool)
        from duosettings import appearance as ap

        calls = []
        tool.apply_gsettings = lambda scheme: calls.append(("gtk", scheme)) or True
        tool.apply_kde = lambda scheme: calls.append(("qt", scheme)) or False
        tool.apply_android = lambda scheme, force=False: (
            calls.append(("android", scheme)) or "not running"
        )
        tool.timer = lambda on: calls.append(("timer", on))
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "appearance.conf")
            real = ap.config_path
            ap.config_path = lambda: conf
            try:
                self.assertEqual(tool.main(["set", "light"]), 0)
                self.assertEqual(ap.load(conf)["mode"], "light")
                self.assertIn(("timer", False), calls)
                self.assertIn(("gtk", "light"), calls)
                calls.clear()
                self.assertEqual(
                    tool.main(["set", "auto", "--light-from", "06:00", "--dark-from", "21:00"]), 0
                )
                self.assertEqual(
                    ap.load(conf), {"mode": "auto", "light_from": 360, "dark_from": 1260}
                )
                self.assertIn(("timer", True), calls)
                self.assertEqual(tool.main(["set", "auto", "--light-from", "6"]), 2)
                self.assertEqual(tool.main(["set", "purple"]), 2)
                self.assertEqual(tool.main(["android"]), 3)
            finally:
                ap.config_path = real


class StayAwakeCharging(unittest.TestCase):
    """Settings > Battery > Sleep: the setting and the holds, read from
    duo2-autosleep's files (power/)."""

    def test_setting(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "autosleep.conf")
            self.assertTrue(sleep.stay_awake_charging(p))  # no file: the daemon's default
            with open(p, "w") as f:
                f.write("delay = 20\n# stay_awake_charging = on\n")
            self.assertTrue(sleep.stay_awake_charging(p))  # no key either
            with open(p, "w") as f:
                f.write("delay = 20\nstay_awake_charging = off\n")
            self.assertFalse(sleep.stay_awake_charging(p))
            with open(p, "w") as f:
                f.write("stay_awake_charging = on  # a note\n")
            self.assertTrue(sleep.stay_awake_charging(p))

    def test_switch_argv(self):
        self.assertEqual(
            sleep.switch_argv(True),
            ["sudo", "-n", "/usr/local/sbin/duo2-autosleep-switch", "charging", "on"],
        )
        self.assertEqual(sleep.switch_argv(False)[-2:], ["charging", "off"])

    def test_holds(self):
        now = 1_000_000
        with tempfile.TemporaryDirectory() as d:
            hold, until = os.path.join(d, "hold"), os.path.join(d, "until")
            self.assertEqual(sleep.held(hold, until, now), (None, None))
            with open(until, "w") as f:
                f.write("%d\n# a comment\n" % (now + 3600))
            self.assertEqual(sleep.held(hold, until, now), ("until", now + 3600))
            self.assertEqual(sleep.held(hold, until, now + 3601), (None, None))  # lapsed
            self.assertEqual(
                sleep.held(hold, until, now - 2 * 86400), (None, None)
            )  # a wrong clock
            with open(hold, "w") as f:
                f.write("off\n")
            self.assertEqual(sleep.held(hold, until, now), ("off", None))
            with open(until, "w") as f:
                f.write("soon\n")
            os.remove(hold)
            self.assertEqual(sleep.held(hold, until, now), (None, None))


if __name__ == "__main__":
    unittest.main()
