"""Settings > Pen's pure parts and its helper commands (python3 -m unittest discover -s tests)."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import pen as penpage  # noqa: E402


def load_helper():
    path = os.path.join(os.path.dirname(HERE), "duo-settings-helper")
    loader = importlib.machinery.SourceFileLoader("duo_settings_helper_pen", path)
    spec = importlib.util.spec_from_loader("duo_settings_helper_pen", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load_helper()
SHIPPED = os.path.join(
    os.path.dirname(HERE), "..", "..", "rootfs", "overlay", "etc", "duo2", "pen.conf"
)


def load_shell_penmodel():
    """duo-shell's reader of the same pen.conf (shell/duo-shell/duoshell/penmodel.py)."""
    path = os.path.join(os.path.dirname(HERE), "..", "duo-shell", "duoshell", "penmodel.py")
    loader = importlib.machinery.SourceFileLoader("duo_shell_penmodel", path)
    spec = importlib.util.spec_from_loader("duo_shell_penmodel", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


shell = load_shell_penmodel()


def load_pend():
    """duo2-pend, the daemon that reads the haptic keys of the same pen.conf."""
    path = os.path.join(
        os.path.dirname(HERE), "..", "..", "rootfs", "overlay", "usr", "local", "sbin", "duo2-pend"
    )
    loader = importlib.machinery.SourceFileLoader("duo2_pend_for_settings", path)
    spec = importlib.util.spec_from_loader("duo2_pend_for_settings", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


pend = load_pend()


def load_pen_fw():
    path = os.path.join(os.path.dirname(HERE), "..", "..", "touch", "tools", "pen-fw.py")
    loader = importlib.machinery.SourceFileLoader("pen_fw_for_settings", path)
    spec = importlib.util.spec_from_loader("pen_fw_for_settings", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


MS = {6: b"\x03\x00\x80"}


class PenPageParts(unittest.TestCase):
    def test_palm_from_the_file_as_touchd_reads_it(self):
        self.assertEqual(penpage.palm_from_conf(""), "both")
        self.assertEqual(penpage.palm_from_conf("palm_rejection = panel\n"), "panel")
        self.assertEqual(penpage.palm_from_conf("# palm_rejection = off\n"), "both")
        self.assertEqual(penpage.palm_from_conf("palm_rejection = on  # old word\n"), "both")
        self.assertEqual(
            penpage.palm_from_conf("palm_rejection = off\npalm_rejection = maybe\n"), "off"
        )
        with open(SHIPPED) as f:
            self.assertEqual(penpage.palm_from_conf(f.read()), "both")

    def test_now(self):
        self.assertIn("no pen", penpage.now_text(None))
        self.assertEqual(penpage.now_text({"in_range": False}), "Not near the screen")
        self.assertEqual(
            penpage.now_text({"in_range": True, "output": "DSI-2", "tool": "pen"}),
            "Hovering over the right screen",
        )
        self.assertEqual(
            penpage.now_text({"in_range": True, "output": "DSI-1", "tool": "pen", "tip": True}),
            "Writing on the left screen",
        )
        self.assertEqual(
            penpage.now_text({"in_range": True, "output": "DSI-1", "tool": "eraser", "tip": True}),
            "Erasing on the left screen",
        )


class TopButton(unittest.TestCase):
    def test_the_same_words_as_the_shell(self):
        self.assertEqual(penpage.PRESSES, shell.PRESSES)
        self.assertEqual(penpage.ACTIONS, shell.ACTIONS)
        self.assertEqual(penpage.DEFAULTS, shell.DEFAULTS)
        for a in penpage.ACTIONS:
            self.assertIn(a, penpage.ACTION_TITLES)
        for p in penpage.PRESSES:
            self.assertIn(p, penpage.PRESS_TITLES)

    def test_written_here_read_by_the_shell(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "pen.conf")
            self.assertEqual(penpage.read_buttons(path), penpage.DEFAULTS)
            penpage.set_button("click", "screenshot", path)
            penpage.set_button("hold", "off", path)
            with self.assertRaises(ValueError):
                penpage.set_button("click", "rockets", path)
            want = {"click": "screenshot", "double_click": "screenshot", "hold": "off"}
            self.assertEqual(penpage.read_buttons(path), want)
            with open(path) as f:
                self.assertEqual(shell.parse(f.read()), want)

    def test_unknown_values_keep_the_default(self):
        self.assertEqual(
            penpage.buttons_from_conf("click = rockets\nhold=off\n"),
            {"click": "notes", "double_click": "screenshot", "hold": "off"},
        )


class Bluetooth(unittest.TestCase):
    CASES = [
        ({"Name": "Surface Slim Pen 2"}, True),
        ({"Alias": "Surface Pen"}, True),
        ({"Name": "Slim Pen", "ManufacturerData": MS}, True),
        ({"Name": "MS Pen", "Modalias": "usb:v045Ep0C01d0001"}, True),
        ({"Appearance": 0x03C7, "ManufacturerData": MS}, True),
        ({"Appearance": 0x03C7}, False),
        ({"Name": "Surface Headphones", "ManufacturerData": MS}, False),
        ({"Name": "Some Pen", "ManufacturerData": {76: b""}}, False),
        ({}, False),
    ]

    def test_which_devices_are_surface_pens_here_and_in_the_shell(self):
        for device, want in self.CASES:
            self.assertEqual(penpage.is_surface_pen(device), want, device)
            self.assertEqual(shell.is_surface_pen(device), want, device)

    def test_texts(self):
        self.assertEqual(
            penpage.device_subtitle({"connected": True, "battery": 64}), "Connected · Battery 64 %"
        )
        self.assertEqual(penpage.device_subtitle({"connected": True, "battery": None}), "Connected")
        self.assertEqual(
            penpage.device_subtitle({"connected": False, "battery": 64}), "Not connected"
        )
        self.assertEqual(
            penpage.pen_status({"connected": True, "battery": 9}), "Connected · Battery 9 %"
        )
        self.assertIn("press the top button", penpage.pen_status({"connected": False}))


def dev(path, pen=True, paired=False, connected=False, seen=None, rssi=-60):
    return {
        "path": path,
        "pen": pen,
        "paired": paired,
        "connected": connected,
        "seen": seen,
        "rssi": rssi,
    }


class Pairing(unittest.TestCase):
    def test_only_a_pen_heard_since_the_start_the_strongest(self):
        devices = [
            dev("/a", seen=5.0, rssi=-80),
            dev("/b", seen=11.0, rssi=-70),
            dev("/c", seen=12.0, rssi=-40),
            dev("/d", pen=False, seen=12.0, rssi=-30),
        ]
        self.assertEqual(penpage.choose_pen(devices, 10.0)["path"], "/c")
        self.assertIsNone(penpage.choose_pen(devices, 13.0))
        self.assertIsNone(penpage.choose_pen([dev("/e", seen=None)], 0.0))

    def test_a_new_pen_is_paired(self):
        f = penpage.PairFlow(100.0)
        self.assertIsNone(f.step([dev("/old", seen=50.0)], 101.0))
        self.assertEqual(f.step([dev("/p", seen=102.0)], 102.5), ("pair", "/p"))
        self.assertEqual(f.state, "pairing")
        self.assertIsNone(f.step([dev("/p", seen=103.0)], 103.0))  # no second Pair
        f.paired(None)
        self.assertEqual(f.state, "done")

    def test_a_pen_paired_before_is_forgotten_then_paired(self):
        f = penpage.PairFlow(0.0)
        self.assertEqual(f.step([dev("/p", paired=True, seen=1.0)], 1.0), ("forget", "/p"))
        self.assertIsNone(f.step([dev("/p", paired=True, seen=2.0)], 2.0))
        f.forgot(None)
        self.assertEqual(f.step([dev("/p", seen=3.0)], 3.0), ("pair", "/p"))
        f.paired("org.bluez.Error.AuthenticationFailed")
        self.assertEqual((f.state, f.error), ("failed", "org.bluez.Error.AuthenticationFailed"))

    def test_connected_already_and_the_timeout(self):
        f = penpage.PairFlow(0.0)
        self.assertEqual(
            f.step([dev("/p", paired=True, connected=True, seen=1.0)], 1.0), ("connected", "/p")
        )
        g = penpage.PairFlow(0.0)
        self.assertIsNone(g.step([], 30.0))
        self.assertEqual(g.step([], penpage.PairFlow.TIMEOUT + 1), ("timeout", None))
        self.assertEqual(g.state, "failed")
        self.assertIsNone(g.step([dev("/p", seen=200.0)], 200.0))


class Haptics(unittest.TestCase):
    TEXTS = (
        "",
        "writing_feel = ink\n",
        "writing_feel = pencil\nfeel_strength = 80\nbutton_click = on\n",
        "writing_feel = loud\nfeel_strength = 0\nbutton_click = yes\n",
        "writing_feel = brush # comment\nwriting_feel = chisel\nfeel_strength = 100\n",
        "palm_rejection = both\nbutton_click = off\nwriting_feel = off\n",
    )

    def test_read_here_as_the_daemon_reads(self):
        for text in self.TEXTS:
            self.assertEqual(penpage.haptics_from_conf(text), pend.haptic_conf(text), text)
        self.assertEqual(set(penpage.FEELS), {"off"} | set(pend.FEELS))
        self.assertEqual(penpage.FEELS, helper.FEELS)

    def test_the_shipped_file_says_off(self):
        with open(SHIPPED) as f:
            self.assertEqual(penpage.haptics_from_conf(f.read()), ("off", 50, False))


CACHE = {
    "read_at": 1,
    "components": [
        {"component": 17, "name": "BLE", "version": "5.148.139", "raw": 0x0500948B, "bank": 1},
        {"component": 16, "name": "MCU", "version": "5.133.139", "raw": 0x0500858B, "bank": 0},
        {"component": 18, "name": "0x12", "version": "5.133.139", "raw": 0x0500858B, "bank": 0},
        {"component": 19, "name": "HAPTIC", "version": "5.117.139", "raw": 0x0500758B, "bank": 0},
    ],
}
FILES = {
    "set": "Microsoft 1.22.139.0",
    "known": True,
    "parts": [
        {
            "label": "BLE-HI",
            "component": 17,
            "name": "BLE",
            "version": "5.150.139",
            "raw": 0x0500968B,
        },
        {
            "label": "BLE-LO",
            "component": 17,
            "name": "BLE",
            "version": "5.150.139",
            "raw": 0x0500968B,
        },
        {
            "label": "MCU-B0",
            "component": 16,
            "name": "MCU",
            "version": "5.142.139",
            "raw": 0x05008E8B,
        },
        {
            "label": "HAPTIC",
            "component": 19,
            "name": "HAPTIC",
            "version": "5.117.139",
            "raw": 0x0500758B,
        },
    ],
}


class Firmware(unittest.TestCase):
    def test_versions_and_newer_parts(self):
        self.assertEqual(
            penpage.versions_text(CACHE["components"]),
            "Bluetooth 5.148 · Pen 5.133 · Haptics 5.117",
        )
        self.assertEqual(penpage.newer_parts(CACHE["components"], FILES["parts"]), ["BLE", "MCU"])
        self.assertEqual(penpage.newer_parts(FILES["parts"], FILES["parts"]), [])
        self.assertEqual(penpage.newer_parts([], FILES["parts"]), [])  # nothing read: nothing newer

    def test_view(self):
        v = penpage.fw_view(CACHE, FILES, None, False)
        self.assertEqual(v["current"], "Bluetooth 5.148 · Pen 5.133 · Haptics 5.117")
        self.assertIn("Newer: Microsoft 1.22.139.0", v["available"])
        self.assertTrue(v["can_update"])
        self.assertEqual(v["status"], "")
        # running: no second start
        self.assertFalse(penpage.fw_view(CACHE, FILES, {"state": "sending"}, True)["can_update"])
        # never read: the pen has to connect first
        v = penpage.fw_view(None, FILES, None, False)
        self.assertIn("press the pen's top button", v["current"])
        self.assertFalse(v["can_update"])
        # no files, files not read yet, up to date
        self.assertEqual(
            penpage.fw_view(CACHE, {}, None, False)["available"], "No firmware files on this phone"
        )
        self.assertEqual(penpage.fw_view(CACHE, None, None, False)["available"], "")
        new = dict(CACHE, components=[dict(c, raw=c["raw"] + 0x10000) for c in CACHE["components"]])
        v = penpage.fw_view(new, FILES, None, False)
        self.assertTrue(v["available"].startswith("Up to date"))
        self.assertFalse(v["can_update"])
        self.assertFalse(
            penpage.fw_view(CACHE, dict(FILES, known=False), None, False)["can_update"]
        )

    def test_a_finished_update_shows_its_versions_at_once(self):
        # duo2-pend's cache still has the old versions (it reads again at the pen's next
        # connection); the update's own last read is newer: the page shows that
        new = [dict(c) for c in FILES["parts"] if c["label"] != "BLE-HI"]
        done = {
            "state": "done",
            "time": 50,
            "versions": new,
            "delivered": [
                {"label": "BLE-LO", "name": "BLE", "version": "5.150.139"},
                {"label": "MCU-B0", "name": "MCU", "version": "5.142.139"},
            ],
        }
        v = penpage.fw_view(CACHE, FILES, done, False)
        self.assertEqual(v["current"], "Bluetooth 5.150 · Pen 5.142 · Haptics 5.117")
        self.assertTrue(v["available"].startswith("Up to date"))
        self.assertFalse(v["can_update"])
        self.assertEqual(v["status"], "Updated to Bluetooth 5.150 · Pen 5.142")
        # a cache read after the update wins again; a run that did not finish never counts
        later = dict(CACHE, read_at=60)
        self.assertEqual(penpage.pen_components(later, done), later["components"])
        self.assertEqual(
            penpage.pen_components(CACHE, dict(done, state="failed")), CACHE["components"]
        )
        self.assertEqual(penpage.pen_components(None, done), new)
        self.assertIsNone(penpage.pen_components(None, None))

    def test_status_texts(self):
        t = penpage.fw_status_text
        self.assertIn("top button", t({"state": "waiting-link"}))
        self.assertEqual(
            t(
                {
                    "state": "sending",
                    "label": "BLE-LO",
                    "version": "5.150.139",
                    "sent": 1101,
                    "total": 2203,
                }
            ),
            "Sending Bluetooth 5.150: 49 %. Keep the pen near the phone",
        )
        self.assertIn("Press its top button", t({"state": "interrupted"}))
        self.assertIn("restarts", t({"state": "restarting", "label": "BLE-LO"}))
        self.assertEqual(
            t(
                {
                    "state": "refused",
                    "problems": [
                        {"code": "charger", "text": "x"},
                        {"code": "battery", "text": "y"},
                    ],
                }
            ),
            "Not started: put the phone on its charger; charge the pen to at least 50 %",
        )
        self.assertEqual(
            t(
                {
                    "state": "done",
                    "delivered": [
                        {"name": "BLE", "version": "5.150.139"},
                        {"name": "MCU", "version": "5.142.139"},
                    ],
                }
            ),
            "Updated to Bluetooth 5.150 · Pen 5.142",
        )
        self.assertEqual(t({"state": "done"}), "The pen's firmware is up to date")
        self.assertIn(
            "previous firmware", t({"state": "failed", "message": "content 3/10: no response"})
        )
        self.assertIn("did not connect", t({"state": "failed", "code": "no-link"}))
        self.assertIn("previous firmware", t({"state": "stopped"}))
        self.assertEqual(t(None), "")

    def test_running_and_fraction(self):
        alive = lambda pid: pid == 42  # noqa: E731
        self.assertTrue(penpage.fw_running({"state": "sending", "pid": 42}, alive))
        self.assertFalse(penpage.fw_running({"state": "sending", "pid": 43}, alive))  # died
        self.assertFalse(penpage.fw_running({"state": "done", "pid": 42}, alive))
        self.assertFalse(penpage.fw_running(None, alive))
        self.assertEqual(penpage.fw_fraction({"state": "sending", "sent": 5, "total": 10}), 0.5)
        self.assertIsNone(penpage.fw_fraction({"state": "restarting"}))

    def test_the_status_pen_fw_writes_is_what_the_page_reads(self):
        fw = load_pen_fw()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "status.json")
            st = fw.StatusFile(path)
            st.set(state="sending", label="MCU-B0", version="5.142.139", sent=934, total=1868)
            status = penpage.read_json(path)
        self.assertTrue(penpage.fw_running(status))  # our own pid
        self.assertEqual(penpage.fw_fraction(status), 0.5)
        self.assertEqual(
            penpage.fw_status_text(status), "Sending Pen 5.142: 50 %. Keep the pen near the phone"
        )

    def test_the_newest_firmware_set_ignores_non_ascii_digits(self):
        # "٣" (Arabic-indic three) is isdigit() but int() would read it as 3 and a
        # superscript "²" is isdigit() and makes int() raise: neither may crash or win.
        with tempfile.TemporaryDirectory() as d:
            for name in ("5.9", "5.10", "5.\u00b2", "5.\u0663x"):
                os.mkdir(os.path.join(d, name))
            old = helper.PEN_FW_SETS
            helper.PEN_FW_SETS = d
            try:
                self.assertEqual(helper.pen_fw_set(), os.path.join(d, "5.10"))
            finally:
                helper.PEN_FW_SETS = old


class PenHelper(unittest.TestCase):
    def test_the_duo_wc_keys(self):
        for key, good, bad in (
            ("pen_button", ("right_click", "off"), ("on", "middle", "")),
            ("pen_dot", ("on", "off"), ("yes", "")),
        ):
            for v in good:
                self.assertTrue(helper.WC_KEYS[key](v), (key, v))
            for v in bad:
                self.assertFalse(helper.WC_KEYS[key](v), (key, v))
            request, build = helper.WC_LIVE[key]
            self.assertEqual(request, "set_pen")
        self.assertEqual(helper.WC_LIVE["pen_button"][1]("off"), {"button": "off"})
        self.assertEqual(helper.WC_LIVE["pen_dot"][1]("on"), {"dot": "on"})
        for key, good, bad in (
            ("pen_input", ("apps", "all"), ("some", "")),
            (
                "pen_apps",
                ("rnote, krita", "org.duo2os.PenTest", ""),
                ("rnote # x", "a\nb", "a=b", "x" * 1001),
            ),
        ):
            for v in good:
                self.assertTrue(helper.WC_KEYS[key](v), (key, v))
            for v in bad:
                self.assertFalse(helper.WC_KEYS[key](v), (key, v))
        self.assertEqual(helper.WC_LIVE["pen_input"][1]("all"), {"input": "all"})
        self.assertEqual(helper.WC_LIVE["pen_apps"][1]("rnote, krita"), {"apps": "rnote, krita"})

    def test_new_duo_wc_keys_only_when_duo_wc_takes_them(self):
        # An older duo-wc does not know pen_input / pen_apps (it would not start with
        # them in its config): they are written only after the running one took them.
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "duo-wc.conf")
            with open(conf, "w") as f:
                f.write("pen_dot = on\n")
            replies = []
            saved = helper.WC_CONF, helper.wc_admin_reply
            helper.WC_CONF = conf
            helper.wc_admin_reply = lambda request, **args: replies.pop(0)
            try:
                replies.append({"button": "right_click", "dot": True})  # an older duo-wc
                self.assertEqual(helper.main(["wc-conf", "pen_input", "all"]), 1)
                replies.append(None)  # none running
                self.assertEqual(helper.main(["wc-conf", "pen_apps", "rnote"]), 1)
                with open(conf) as f:
                    self.assertEqual(f.read(), "pen_dot = on\n")
                replies.append({"input": "all", "apps": ["rnote"]})
                self.assertEqual(helper.main(["wc-conf", "pen_input", "all"]), 0)
                replies.append({"input": "all", "apps": ["rnote", "krita"]})
                self.assertEqual(helper.main(["wc-conf", "pen_apps", "rnote, krita"]), 0)
            finally:
                helper.WC_CONF, helper.wc_admin_reply = saved
            with open(conf) as f:
                text = f.read()
            self.assertIn("pen_input = all\n", text)
            self.assertIn("pen_apps = rnote, krita\n", text)

    def test_the_drawing_apps_as_typed(self):
        self.assertEqual(penpage.pen_apps_value("rnote,krita  gimp, Krita"), "rnote, krita, gimp")
        self.assertEqual(penpage.pen_apps_value("  "), "")
        self.assertIsNone(penpage.pen_apps_value("rnote; rm"))
        self.assertIsNone(penpage.pen_apps_value("a#b"))
        # what the page writes, the helper takes
        self.assertTrue(
            helper.WC_KEYS["pen_apps"](penpage.pen_apps_value(", ".join(penpage.DEFAULT_PEN_APPS)))
        )
        self.assertEqual(penpage.PEN_INPUT, ("apps", "all"))

    def test_palm_writes_pen_conf(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pen.conf")
            with open(SHIPPED) as f:
                shipped = f.read()
            with open(path, "w") as f:
                f.write(shipped)
            old = helper.PEN_CONF
            helper.PEN_CONF = path
            try:
                self.assertEqual(helper.main(["pen", "palm", "panel"]), 0)
                self.assertEqual(helper.main(["pen", "palm", "sometimes"]), 2)
            finally:
                helper.PEN_CONF = old
            with open(path) as f:
                text = f.read()
            self.assertIn("palm_rejection = panel\n", text)
            # the comments kept, one active line
            self.assertIn("#   panel   only on the screen the pen is over", text)
            self.assertEqual(
                sum(1 for ln in text.splitlines() if ln.startswith("palm_rejection")), 1
            )

    def test_haptics_write_pen_conf(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pen.conf")
            with open(SHIPPED) as f:
                shipped = f.read()
            with open(path, "w") as f:
                f.write(shipped)
            old = helper.PEN_CONF
            helper.PEN_CONF = path
            try:
                self.assertEqual(helper.main(["pen", "feel", "marker"]), 0)
                self.assertEqual(helper.main(["pen", "strength", "75"]), 0)
                self.assertEqual(helper.main(["pen", "click", "on"]), 0)
                for bad in (
                    ["pen", "feel", "loud"],
                    ["pen", "strength", "0"],
                    ["pen", "strength", "101"],
                    ["pen", "strength", "-5"],
                    ["pen", "click", "yes"],
                ):
                    self.assertEqual(helper.main(bad), 2, bad)
            finally:
                helper.PEN_CONF = old
            with open(path) as f:
                text = f.read()
            self.assertEqual(pend.haptic_conf(text), ("marker", 75, True))
            self.assertIn("palm_rejection = both\n", text)

    def test_charge_runs_the_pen_charger(self):
        ran = []
        old_run, old_path = helper.subprocess.run, helper.PEN_CHARGER

        class Done:
            returncode = 0

        def fake_run(argv, **_kw):
            ran.append(argv)
            return Done()

        helper.subprocess.run = fake_run
        helper.PEN_CHARGER = "/x/duo2-pen-charger"
        try:
            self.assertEqual(helper.main(["pen", "charge", "on"]), 0)
            self.assertEqual(helper.main(["pen", "charge", "off"]), 0)
            self.assertEqual(helper.main(["pen", "charge", "always"]), 2)
            self.assertEqual(helper.main(["pen", "charge"]), 2)
        finally:
            helper.subprocess.run, helper.PEN_CHARGER = old_run, old_path
        self.assertEqual(ran, [["/x/duo2-pen-charger", "on"], ["/x/duo2-pen-charger", "off"]])


class PenCharger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.setting = os.path.join(self.tmp.name, "pen-charger")
        self.output = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text + "\n")

    def test_hidden_without_the_kernel_output(self):
        self.write(self.setting, "on")
        self.assertIsNone(penpage.charger(self.setting, self.output))

    def test_off_by_default(self):
        self.write(os.path.join(self.output, "state"), "disabled")
        self.assertEqual(penpage.charger(self.setting, self.output), (False, False))

    def test_on_and_the_transmitter(self):
        self.write(self.setting, "on")
        self.write(os.path.join(self.output, "state"), "enabled")
        self.assertEqual(penpage.charger(self.setting, self.output), (True, True))
        self.write(os.path.join(self.output, "state"), "disabled")
        self.assertEqual(penpage.charger(self.setting, self.output), (True, False))
        self.write(self.setting, "off")
        self.assertEqual(penpage.charger(self.setting, self.output), (False, False))

    def test_subtitle(self):
        self.assertIn("Pen Cover", penpage.charger_subtitle(False, False))
        self.assertIn("Pen Cover", penpage.charger_subtitle(True, True))
        self.assertTrue(penpage.charger_subtitle(True, False).startswith("Paused"))


if __name__ == "__main__":
    unittest.main()
