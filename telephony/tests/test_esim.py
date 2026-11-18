import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


esim = load("duo2_esim", os.path.join(os.path.dirname(HERE), "duo2-esim"))
REAL_LPAC_ARGV = esim.lpac_argv  # WithTheFake replaces it with the bare fake


class Inputs(unittest.TestCase):
    def test_iccid(self):
        self.assertTrue(esim.valid_iccid("89000000000000000011"))
        self.assertTrue(esim.valid_iccid("8900000000000000001F"))
        for bad in (
            "",
            "8900",
            "89000000000000000011; rm",
            "-89000000000000000011",
            "8900000000000000001X",
        ):
            self.assertFalse(esim.valid_iccid(bad), bad)

    def test_activation_codes(self):
        self.assertEqual(
            esim.parse_activation("LPA:1$rsp.example.com$QR-G-5C-1LS-1W1Z9P7"),
            ("rsp.example.com", "QR-G-5C-1LS-1W1Z9P7"),
        )
        self.assertEqual(
            esim.parse_activation("1$smdp.example.net:443$ABC"), ("smdp.example.net:443", "ABC")
        )
        self.assertEqual(esim.parse_activation(" LPA:1$a.b$X$1.3.6$1 "), ("a.b", "X"))
        for bad in ("", "LPA:2$a$b", "LPA:1$a b$c", "LPA:1$$x", "LPA:1$a.b$x;y", "http://x"):
            self.assertIsNone(esim.parse_activation(bad), bad)

    def test_codes_are_normalized(self):
        self.assertEqual(esim.normalize_code(" lpa:1$a.b$X "), "LPA:1$a.b$X")
        self.assertEqual(esim.normalize_code("1$a.b$X"), "LPA:1$a.b$X")
        self.assertEqual(esim.parse_activation("lpa:1$a.b$X"), ("a.b", "X"))

    def test_nicknames(self):
        self.assertTrue(esim.valid_nickname(""))
        self.assertTrue(esim.valid_nickname("Travel (EU)"))
        self.assertFalse(esim.valid_nickname("a\nb"))
        self.assertFalse(esim.valid_nickname("x" * 65))

    def test_mask(self):
        self.assertEqual(esim.mask("89049032000000000000000000000042"), "•••• 0042")
        self.assertEqual(esim.mask("12"), "12")


class Output(unittest.TestCase):
    def test_last_lpa_line_wins(self):
        text = (
            '{"type":"progress","payload":{"code":0,"message":"x","data":null}}\n'
            'garbage\n{"type":"lpa","payload":{"code":0,"message":"success","data":[1]}}\n'
        )
        self.assertEqual(esim.lpac_result(text), (0, "success", [1]))
        self.assertEqual(esim.lpac_result("")[0], -1)

    def test_profiles_drop_icons(self):
        p = esim.profiles(
            [{"iccid": "1", "profileName": "A", "icon": "iVBO", "profileState": "enabled"}]
        )
        self.assertEqual(
            p,
            [
                {
                    "iccid": "1",
                    "name": "A",
                    "nickname": "",
                    "provider": "",
                    "state": "enabled",
                    "class": "",
                }
            ],
        )

    def test_questions_say_what_happens_without_the_matching_id(self):
        q = esim.describe("add", ["LPA:1$rsp.example.com$SECRET-ID"], {})
        self.assertIn("rsp.example.com", q)
        self.assertNotIn("SECRET-ID", q)
        q = esim.describe("delete", ["89000000000000000022"], {"89000000000000000022": "Travel"})
        self.assertIn("Travel", q)
        self.assertIn("for good", q)
        self.assertIn("0022", esim.describe("enable", ["89000000000000000022"], {}))


class Failures(unittest.TestCase):
    """lpac's failing step and reason, said in words."""

    def test_no_internet(self):
        for step in ("es9p_initiate_authentication", "es9p_get_bound_profile_package"):
            self.assertIn("internet", esim.explain(step, "HTTP transport failed"))

    def test_the_servers_answer_is_shown_without_long_numbers(self):
        text = esim.explain("es9p_authenticate_client", "Matching ID 1234567890 refused")
        self.assertIn("Matching ID", text)
        self.assertNotIn("1234567890", text)
        self.assertIn("…90", text)

    def test_chip_and_code_failures(self):
        self.assertIn("confirmation code", esim.explain("confirmation_code", "required"))
        self.assertIn(
            "memory",
            esim.explain(
                "es10b_load_bound_profile_package",
                "loadProfileElements,installFailedDueToInsufficientMemoryForProfile",
            ),
        )
        self.assertIn(
            "weirdReason", esim.explain("es10b_load_bound_profile_package", "x,weirdReason")
        )
        self.assertIn("something_new", esim.explain("something_new", ""))

    def test_progress_and_metadata(self):
        text = (
            '{"type":"progress","payload":{"code":0,"message":"es9p_initiate_authentication",'
            '"data":"s"}}\n'
            '{"type":"progress","payload":{"code":0,"message":"es8p_meatadata_parse",'
            '"data":{"iccid":"8900","profileName":"P"}}}\n'
            '{"type":"lpa","payload":{"code":0,"message":"success","data":{}}}\n'
        )
        self.assertEqual(
            esim.lpac_steps(text), ["es9p_initiate_authentication", "es8p_meatadata_parse"]
        )
        self.assertEqual(esim.lpac_metadata(text)["profileName"], "P")
        self.assertEqual(esim.lpac_metadata(""), {})


class OwnService(unittest.TestCase):
    """lpac runs as a service systemd starts (the GUI's QRTR filter is not
    inherited), with the environment and the time limit given."""

    def test_unit_command_line(self):
        cmd = esim.unit_argv(
            ["/usr/local/bin/lpac"], {"LPAC_APDU": "qmi_qrtr", "A": "b"}, 180, "duo2-esim-lpac-7-1"
        )
        self.assertEqual(cmd[0], "/usr/bin/systemd-run")
        for opt in (
            "--pipe",
            "--wait",
            "--collect",
            "--unit=duo2-esim-lpac-7-1",
            "--property=RuntimeMaxSec=180",
            "--setenv=A=b",
            "--setenv=LPAC_APDU=qmi_qrtr",
        ):
            self.assertIn(opt, cmd)
        families = [c for c in cmd if c.startswith("--property=RestrictAddressFamilies=")]
        self.assertEqual(len(families), 1)
        self.assertIn("AF_QIPCRTR", families[0])
        self.assertNotIn("~", families[0])
        self.assertEqual(cmd[-2:], ["--", "/usr/local/bin/lpac"])
        self.assertLess(cmd.index("--setenv=A=b"), cmd.index("--"))

    def test_each_call_its_own_unit(self):
        a = REAL_LPAC_ARGV({}, 10)
        b = REAL_LPAC_ARGV({}, 10)

        def name(cmd):
            return [c for c in cmd if c.startswith("--unit=")][0]

        self.assertNotEqual(name(a), name(b))


class WithTheFake(unittest.TestCase):
    """The helper's commands against the fake lpac (no confirm socket: a
    change is refused as not confirmed, which is the safe failure)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["FAKE_LPAC_STATE"] = os.path.join(self.dir.name, "state.json")
        esim.lpac_argv = lambda env, timeout: [sys.executable, os.path.join(HERE, "fake_lpac.py")]
        esim.WC_ADMIN = os.path.join(self.dir.name, "no-such.sock")
        esim.LOCK = os.path.join(self.dir.name, "lock")
        self.logged = []
        self.saved_log = esim.log
        esim.log = self.logged.append

    def tearDown(self):
        esim.log = self.saved_log
        os.environ.pop("FAKE_LPAC_STATE", None)
        self.dir.cleanup()

    def run_main(self, *argv):
        from io import StringIO

        old, sys.stdout = sys.stdout, StringIO()
        try:
            rc = esim.main(list(argv))
            self.lines = sys.stdout.getvalue().splitlines()
            return rc, json.loads(self.lines[-1] if self.lines else "{}")
        finally:
            sys.stdout = old

    def test_info_and_list(self):
        rc, info = self.run_main("info")
        self.assertEqual(rc, 0)
        self.assertTrue(info["eid"].endswith("42"))
        rc, lst = self.run_main("list")
        self.assertEqual([p["state"] for p in lst["profiles"]], ["enabled", "disabled"])

    def test_a_change_without_a_yes_is_not_done(self):
        rc, res = self.run_main("delete", "89000000000000000022")
        self.assertEqual((rc, res), (1, {"ok": False, "error": "not confirmed"}))
        _rc, lst = self.run_main("list")
        self.assertEqual(len(lst["profiles"]), 2)

    def test_a_change_with_a_yes(self):
        saved = esim.confirm
        esim.confirm = lambda detail: True
        try:
            self.assertEqual(self.run_main("enable", "89000000000000000022")[0], 0)
            self.assertEqual(self.run_main("rename", "89000000000000000022", "Home")[0], 0)
            _rc, lst = self.run_main("list")
            b = lst["profiles"][1]
            self.assertEqual((b["state"], b["nickname"]), ("enabled", "Home"))
            self.assertEqual(self.run_main("delete", "89000000000000000011")[0], 0)
            self.assertEqual(self.run_main("add", "LPA:1$rsp.example.com$ABC")[0], 0)
            _rc, lst = self.run_main("list")
            self.assertEqual(len(lst["profiles"]), 2)
        finally:
            esim.confirm = saved

    def add(self, code, *conf):
        saved = esim.confirm
        esim.confirm = lambda detail: True
        try:
            return self.run_main("add", code, *conf)
        finally:
            esim.confirm = saved

    def test_a_download_names_the_new_profile(self):
        rc, res = self.add("LPA:1$rsp.example.com$ABC")
        self.assertEqual(rc, 0)
        self.assertEqual(res["profile"]["name"], "Test C")
        _rc, lst = self.run_main("list")
        self.assertIn(res["profile"]["iccid"], [p["iccid"] for p in lst["profiles"]])
        self.assertTrue(any("es10b_load_bound_profile_package" in line for line in self.logged))

    def test_a_download_reports_its_steps_first(self):
        rc, res = self.add("LPA:1$rsp.example.com$ABC")
        steps = [json.loads(line)["progress"] for line in self.lines[:-1]]
        self.assertEqual(steps[0], "confirmed")
        self.assertIn("es9p_initiate_authentication", steps)
        self.assertLess(
            steps.index("es9p_get_bound_profile_package"),
            steps.index("es10b_load_bound_profile_package"),
        )
        self.assertEqual(steps[-1], "checking")
        self.assertTrue(res["ok"])

    def test_a_download_without_internet(self):
        rc, res = self.add("lpa:1$rsp.example.com$OFFLINE")
        self.assertEqual(rc, 1)
        self.assertIn("internet", res["error"])
        self.assertEqual(res["step"], "es9p_initiate_authentication")
        _rc, lst = self.run_main("list")
        self.assertEqual(len(lst["profiles"]), 2)

    def test_a_refused_download_logs_no_numbers_and_no_code(self):
        rc, res = self.add("LPA:1$rsp.example.com$REFUSED")
        self.assertEqual(rc, 1)
        self.assertIn("refused", res["error"])
        self.assertNotIn("12345678", res["error"] + res["detail"])
        joined = "\n".join(self.logged)
        self.assertIn("es9p_authenticate_client", joined)
        self.assertNotIn("12345678", joined)
        self.assertNotIn("REFUSED", joined)
        self.assertNotIn("rsp.example.com", joined)

    def test_a_confirmation_code(self):
        rc, res = self.add("LPA:1$rsp.example.com$NEEDCONF$$1")
        self.assertEqual(rc, 1)
        self.assertIn("confirmation code", res["error"])
        self.assertEqual(self.add("LPA:1$rsp.example.com$NEEDCONF$$1", "1234")[0], 0)

    def test_done_is_not_believed_without_the_profile(self):
        rc, res = self.add("LPA:1$rsp.example.com$GHOST")
        self.assertEqual(rc, 1)
        self.assertIn("not on the chip", res["error"])

    def test_one_lpac_at_a_time(self):
        import fcntl
        import threading

        with open(esim.LOCK, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            got = []
            t = threading.Thread(target=lambda: got.append(esim.lpac("profile", "list")))
            t.start()
            t.join(0.5)
            self.assertEqual(got, [])  # waits while another lpac runs
        t.join(5)
        self.assertEqual(got[0][0], 0)

    def test_bad_arguments_are_refused(self):
        from io import StringIO

        old, sys.stderr = sys.stderr, StringIO()
        try:
            self.assertEqual(esim.main(["delete", "1; reboot"]), 2)
            self.assertEqual(esim.main(["add", "not a code"]), 2)
            self.assertEqual(esim.main(["purge"]), 2)
        finally:
            sys.stderr = old


if __name__ == "__main__":
    unittest.main()
