"""duo2-boot health's unit facts against the systemd this test runs under (a
real `systemctl show`, not a fixture): a running unit must come out active,
an unknown one missing. The first packaged kernel on a phone was marked bad
because the properties were read in the order they were asked for, which is
not the order systemctl prints them in. Skipped without a running systemd.
python3 -m unittest discover -s tests (from maint/)."""

import importlib.machinery
import importlib.util
import os
import shutil
import unittest

MAINT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load_duo2_boot():
    loader = importlib.machinery.SourceFileLoader("duo2_boot", os.path.join(MAINT, "duo2-boot"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@unittest.skipUnless(
    os.path.isdir("/run/systemd/system") and shutil.which("systemctl"), "no running systemd"
)
class RealSystemd(unittest.TestCase):
    def setUp(self):
        self.boot = load_duo2_boot()

    def test_running_units_are_active(self):
        # PID 1's scope and the journal run wherever systemd does
        self.assertEqual(self.boot.show("init.scope"), "active")
        self.assertEqual(self.boot.show("systemd-journald.service"), "active")

    def test_unknown_unit_is_missing(self):
        self.assertEqual(self.boot.show("duo2-no-such-unit-for-the-test.service"), "missing")


if __name__ == "__main__":
    unittest.main()
