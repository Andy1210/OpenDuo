"""Settings > Updates, the pure parts (duosettings/updates.py):
python3 -m unittest discover -s tests (from shell/duo-settings/)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duosettings import updates as up  # noqa: E402

NOW = time.mktime((2026, 11, 17, 15, 0, 0, 0, 0, -1))


def state(**kw):
    st = {
        "ok": True,
        "conf": {
            "auto_install": "off",
            "notify": "on",
            "channel": "stable",
            "check_on_mobile_data": "off",
        },
        "developer_mode": False,
        "job": None,
        "last": {
            "time": NOW - 3600,
            "ours": [
                {
                    "name": "openduo-duo-wc",
                    "old": "0.1.0~dev+20261116233800.g0e1a4ac",
                    "new": "0.1.0~dev+20261117120000.gabcdef1",
                    "security": False,
                },
                {
                    "name": "openduo-kernel-surface-duo2-7.3.5-duo2-gaaaa111",
                    "old": None,
                    "new": "7.3.5+20261117100000.gaaaa111",
                    "security": False,
                },
            ],
            "debian": [
                {"name": "openssl", "old": "3.5.1-1", "new": "3.5.1-1+deb13u1", "security": True},
                {"name": "curl", "old": "8.14.1-2", "new": "8.14.1-2+deb13u1", "security": False},
            ],
            "refuse": [],
            "changes": [
                {
                    "version": "Unreleased",
                    "date": "",
                    "text": "### Fixed\n- the dock\n  on both panels\n- the clock\n\n### Added\n- updates",
                },
                {"version": "0.1.0", "date": "2026-12-01", "text": "- first"},
            ],
        },
        "reboot_required": False,
        "may_install": None,
        "kernel": {
            "running": {"release": "7.3.4-duo2-g3ba7db140", "entry": None, "state": "known-good"},
            "kernels": [],
        },
    }
    st.update(kw)
    return st


class Status(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(up.status(state()), ("updates", 4))
        st = state()
        st["last"]["ours"], st["last"]["debian"] = [], []
        self.assertEqual(up.status(st), ("up-to-date",))
        self.assertEqual(up.status(state(last=None)), ("never",))
        self.assertEqual(up.status(state(job={"job": "check", "state": "running"})), ("checking",))
        self.assertEqual(
            up.status(state(job={"job": "install", "state": "running", "step": "snapshot"})),
            ("installing", "snapshot"),
        )
        self.assertEqual(up.status(state(reboot_required=True)), ("restart",))
        # an older helper's free-text step is not shown, only "Installing..."
        self.assertEqual(
            up.status(state(job={"job": "install", "state": "running", "step": "snapshot, then"})),
            ("installing", ""),
        )
        failed = state(job={"job": "install", "state": "failed", "error": "apt", "finished": NOW})
        self.assertEqual(up.status(failed), ("failed", "apt"))
        old_fail = state(
            job={"job": "check", "state": "failed", "error": "x", "finished": NOW - 7200}
        )
        self.assertEqual(up.status(old_fail), ("updates", 4))

    def test_counts_and_rows(self):
        self.assertEqual(up.counts(state()), (2, 2, 1))
        rows = up.package_rows(state())
        self.assertEqual(rows[0], ("openduo-duo-wc", "0.1.0-dev (20261116) → 0.1.0-dev (20261117)"))
        self.assertEqual(rows[1][1], "7.3.5 (20261117)")
        self.assertEqual(up.debian_names(state()), ["curl", "openssl"])
        self.assertEqual(up.short("1.2-3"), "1.2-3")

    def test_when(self):
        self.assertEqual(up.when(NOW - 3600, NOW), ("today", "14:00"))
        self.assertEqual(up.when(NOW - 86400, NOW), ("yesterday", "15:00"))
        self.assertEqual(up.when(NOW - 5 * 86400, NOW)[0], "date")
        self.assertEqual(up.when(None, NOW), ("never", ""))


class Install(unittest.TestCase):
    def test_when_install_may_be_pressed(self):
        self.assertEqual(up.can_install(state()), (True, None))
        self.assertEqual(
            up.can_install(state(job={"job": "check", "state": "running"})), (False, ("busy",))
        )
        self.assertEqual(up.can_install(state(may_install="the battery is at 30 %"))[1][0], "power")
        st = state()
        st["kernel"]["running"]["state"] = "trying"
        self.assertEqual(up.can_install(st), (False, ("kernel",)))
        st = state()
        st["last"]["refuse"] = ["linux-image-arm64 would be installed"]
        self.assertEqual(up.can_install(st)[1][0], "refused")
        st = state()
        st["last"]["ours"], st["last"]["debian"] = [], []
        self.assertEqual(up.can_install(st), (False, ("none",)))


class Changes(unittest.TestCase):
    def test_sections(self):
        s = up.sections(state())
        self.assertEqual(s[0]["title"], "In development")
        self.assertEqual(
            s[0]["items"],
            [("Fixed", ["the dock on both panels", "the clock"]), ("Added", ["updates"])],
        )
        self.assertEqual(s[1], {"title": "0.1.0, 2026-12-01", "items": [(None, ["first"])]})


class Kernel(unittest.TestCase):
    def test_notes(self):
        self.assertIsNone(up.kernel_note(state()))
        st = state(
            kernel={
                "running": {"release": "7.3.5", "state": "trying"},
                "kernels": [{"release": "7.3.5", "state": "trying", "tries_left": 2}],
            }
        )
        self.assertEqual(up.kernel_note(st), ("trying", "7.3.5", 2))
        st = state(
            kernel={
                "running": {"release": "7.3.4", "state": "good"},
                "kernels": [
                    {"release": "7.3.5", "state": "bad"},
                    {"release": "7.3.4", "state": "good"},
                ],
            }
        )
        self.assertEqual(up.kernel_note(st), ("failed", "7.3.5"))
        self.assertEqual(up.poll_seconds(state(job={"job": "install", "state": "running"})), 2)


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(
            up.parse('noise\n{"ok": true, "job": "check"}\n'), {"ok": True, "job": "check"}
        )
        self.assertFalse(up.parse("")["ok"])
        self.assertFalse(up.parse("[1]")["ok"])


class InstallRunning(unittest.TestCase):
    """While the install job runs (the helper reports it as running from the
    moment Install was pressed), the page says which phase it is in and
    Install cannot be pressed again; when it ends, the result."""

    def test_every_phase_is_shown_and_install_is_off(self):
        for ph in up.PHASES:
            st = state(job={"job": "install", "state": "running", "step": ph})
            self.assertEqual(up.status(st), ("installing", ph))
            self.assertEqual(up.can_install(st), (False, ("busy",)))
            self.assertEqual(up.poll_seconds(st), 2)

    def test_the_result(self):
        done = state(job={"job": "install", "state": "done", "finished": NOW}, reboot_required=True)
        self.assertEqual(up.status(done), ("restart",))
        failed = state(
            job={"job": "install", "state": "failed", "error": "the job stopped", "finished": NOW}
        )
        self.assertEqual(up.status(failed), ("failed", "the job stopped"))
        self.assertEqual(up.poll_seconds(failed), 30)


class PhasesMatchTheHelper(unittest.TestCase):
    def test_every_step_the_helper_writes_has_a_text(self):
        here = os.path.dirname(os.path.abspath(__file__))
        maint_dir = os.path.normpath(os.path.join(here, "..", "..", "..", "maint"))
        if not os.path.isdir(os.path.join(maint_dir, "duo2maint")):
            self.skipTest("no maint/ beside shell/")
        sys.path.insert(0, maint_dir)
        try:
            from duo2maint import updates as maint
        finally:
            sys.path.remove(maint_dir)
        helper = {p for _head, p in maint.PHASES} | {"starting", "plan", "cleanup", "recheck"}
        self.assertLessEqual(helper, set(up.PHASES))


if __name__ == "__main__":
    unittest.main()
