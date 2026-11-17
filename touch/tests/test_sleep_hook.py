"""The digitizer's system-sleep hook (rootfs/overlay/usr/lib/systemd/
system-sleep/duo2-touchd), with a stand-in systemctl: who is told what, and
that a suspend never waits more than ~3 s for it.

    touch/tests/run.sh   (or: python3 -m unittest discover -s touch/tests)
"""

import os
import pathlib
import subprocess
import tempfile
import time
import unittest

HOOK = (
    pathlib.Path(__file__).resolve().parents[2]
    / "rootfs/overlay/usr/lib/systemd/system-sleep/duo2-touchd"
)

# The stand-in: logs its arguments; "kill ... SIGURG" makes the "daemon" answer
# (writes $ANSWER into the state file) unless $ANSWER is empty; $FAIL fails it.
FAKE = """#!/bin/sh
echo "$*" >> "$LOG"
[ -n "$FAIL" ] && exit 1
case "$*" in
*SIGURG*) [ -n "$ANSWER" ] && (sleep 0.2; echo "$ANSWER" > "$STATE") & ;;
esac
exit 0
"""


class SleepHook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.dir.name)
        (d / "bin").mkdir()
        fake = d / "bin/systemctl"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.state = d / "duo2-touchd.digitizer"
        self.log = d / "systemctl.log"
        self.env = dict(
            os.environ,
            PATH="%s:/usr/bin:/bin" % (d / "bin"),
            DUO2_TOUCHD_POWER=str(self.state),
            STATE=str(self.state),
            LOG=str(self.log),
            ANSWER="off",
            FAIL="",
        )

    def tearDown(self):
        self.dir.cleanup()

    def run_hook(self, *args, **env):
        t0 = time.monotonic()
        p = subprocess.run(
            ["sh", str(HOOK), *args],
            env=dict(self.env, **env),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return p, time.monotonic() - t0

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_pre_tells_the_daemon_and_waits_until_off(self):
        self.state.write_text("on\n")
        p, took = self.run_hook("pre", "suspend")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(
            self.calls(), ["kill --kill-whom=main --signal=SIGURG duo2-touchd.service"]
        )
        self.assertEqual(self.state.read_text(), "off\n")
        self.assertLess(took, 2.0)
        self.assertGreaterEqual(took, 0.2)
        self.assertEqual(p.stderr, "")

    def test_pre_waits_at_most_3_s(self):
        self.state.write_text("on\n")
        p, took = self.run_hook("pre", "suspend", ANSWER="")
        self.assertEqual(p.returncode, 0)
        self.assertGreater(took, 2.5)
        self.assertLess(took, 5.0)
        self.assertIn("not off after 3 s (state: on)", p.stderr)

    def test_pre_already_off(self):
        self.state.write_text("off\n")
        p, took = self.run_hook("pre", "suspend", ANSWER="")
        self.assertEqual(p.returncode, 0)
        self.assertLess(took, 1.0)

    def test_post_tells_the_daemon_without_waiting(self):
        self.state.write_text("off\n")
        p, took = self.run_hook("post", "suspend", ANSWER="")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(
            self.calls(), ["kill --kill-whom=main --signal=SIGWINCH duo2-touchd.service"]
        )
        self.assertLess(took, 1.0)

    def test_nobody_to_tell_without_the_state_file(self):
        for phase in ("pre", "post"):
            p, took = self.run_hook(phase, "suspend")
            self.assertEqual(p.returncode, 0)
            self.assertLess(took, 1.0)
        self.assertEqual(self.calls(), [])

    def test_daemon_not_running(self):
        # a state file left behind, the service stopped: systemctl kill fails
        self.state.write_text("on\n")
        p, took = self.run_hook("pre", "suspend", FAIL="1")
        self.assertEqual(p.returncode, 0)
        self.assertLess(took, 1.0)
        self.assertEqual(p.stderr, "")

    def test_other_operations(self):
        self.state.write_text("on\n")
        p, _ = self.run_hook("something-else", "suspend")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
