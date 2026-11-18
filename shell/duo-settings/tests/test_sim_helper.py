import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_helper():
    path = os.path.join(os.path.dirname(HERE), "duo-settings-helper")
    loader = importlib.machinery.SourceFileLoader("duo_settings_helper_sim", path)
    spec = importlib.util.spec_from_loader("duo_settings_helper_sim", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load_helper()


class SimUse(unittest.TestCase):
    """`sim use` switches only after the user's yes, runs duo2-sim in its
    own service, and answers JSON."""

    def setUp(self):
        self.saved = helper.ask_user, helper.sim_tool
        self.ran = []
        helper.sim_tool = lambda args, timeout: (
            self.ran.append(args),
            (0, "slot 2 is in use now\n", ""),
        )[1]

    def tearDown(self):
        helper.ask_user, helper.sim_tool = self.saved

    def run_main(self, *argv):
        old, sys.stdout = sys.stdout, io.StringIO()
        try:
            rc = helper.main(list(argv))
            return rc, sys.stdout.getvalue()
        finally:
            sys.stdout = old

    def test_no_yes_no_switch(self):
        helper.ask_user = lambda detail: False
        rc, out = self.run_main("sim", "use", "2")
        self.assertEqual((rc, json.loads(out)), (1, {"ok": False, "error": "not confirmed"}))
        self.assertEqual(self.ran, [])

    def test_a_yes_switches(self):
        asked = []
        helper.ask_user = lambda detail: asked.append(detail) or True
        rc, out = self.run_main("sim", "use", "2")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertEqual(self.ran, [["use", "2"]])
        self.assertIn("eSIM", asked[0])

    def test_a_failure_says_why(self):
        helper.ask_user = lambda detail: True
        helper.sim_tool = lambda args, timeout: (
            1,
            "",
            "no USIM application on the card in slot 1\n",
        )
        rc, out = self.run_main("sim", "use", "1")
        self.assertEqual(rc, 1)
        self.assertIn("no USIM", json.loads(out)["error"])

    def test_only_slots_1_and_2(self):
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            for bad in (["sim", "use", "3"], ["sim", "use", "1; reboot"], ["sim", "use"]):
                self.assertEqual(helper.main(bad), 2, bad)
        finally:
            sys.stderr = old
        self.assertEqual(self.ran, [])


if __name__ == "__main__":
    unittest.main()
