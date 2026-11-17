"""Which on-screen keyboard the session starts: compositor/duo-wc/dist/osk
(run with --print, which starts nothing)."""

import os
import shutil
import subprocess
import tempfile
import unittest

OSK = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "compositor", "duo-wc", "dist", "osk"
)

STEVIA = "env POS_DEBUG=force-completion phosh-osk-stub"
SQUEEKBOARD = "env GTK_THEME=Adwaita:dark squeekboard"


class OskChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "bin")
        self.config = os.path.join(self.tmp, "config")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.config, "duo2"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def install(self, name):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)

    def choose(self, text):
        with open(os.path.join(self.config, "duo2", "osk"), "w") as f:
            f.write(text)

    def run_osk(self, features=None):
        # The stubs first, then only the system's tools (no real keyboard
        # binary is on a build host; the stub decides "installed").
        env = {
            "PATH": self.bin + ":/usr/bin:/bin",
            "XDG_CONFIG_HOME": self.config,
            "HOME": self.tmp,
        }
        if features is not None:
            env["DUO_WC_FEATURES"] = features
        done = subprocess.run(
            ["sh", OSK, "--print"], env=env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip(), done.stderr

    def test_phosh_osk_stub_where_duo_wc_offers_it(self):
        self.install("phosh-osk-stub")
        self.assertEqual(self.run_osk("phosh-osk")[0], STEVIA)

    def test_an_older_duo_wc_gets_squeekboard(self):
        # No DUO_WC_FEATURES: phosh-osk-stub would wait for ever.
        self.install("phosh-osk-stub")
        self.assertEqual(self.run_osk()[0], SQUEEKBOARD)
        self.assertEqual(self.run_osk("")[0], SQUEEKBOARD)

    def test_not_installed_gets_squeekboard(self):
        if shutil.which("phosh-osk-stub", path="/usr/bin:/bin"):
            self.skipTest("phosh-osk-stub is installed on this host")
        self.assertEqual(self.run_osk("phosh-osk")[0], SQUEEKBOARD)

    def test_the_features_are_words(self):
        self.install("phosh-osk-stub")
        self.assertEqual(self.run_osk("capture phosh-osk later")[0], STEVIA)
        self.assertEqual(self.run_osk("phosh-osk-2")[0], SQUEEKBOARD)

    def test_squeekboard_by_hand(self):
        self.install("phosh-osk-stub")
        self.choose("squeekboard\n")
        self.assertEqual(self.run_osk("phosh-osk")[0], SQUEEKBOARD)

    def test_phosh_osk_stub_by_hand_only_where_it_works(self):
        self.install("phosh-osk-stub")
        self.choose(" phosh-osk-stub \r\n")
        self.assertEqual(self.run_osk("phosh-osk")[0], STEVIA)
        out, err = self.run_osk()
        self.assertEqual(out, SQUEEKBOARD)
        self.assertIn("does not offer", err)

    def test_an_unknown_choice_is_the_automatic_one(self):
        self.install("phosh-osk-stub")
        self.choose("florence\n")
        self.assertEqual(self.run_osk("phosh-osk")[0], STEVIA)
        self.choose("")
        self.assertEqual(self.run_osk("phosh-osk")[0], STEVIA)


if __name__ == "__main__":
    unittest.main()
