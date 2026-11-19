"""The OTA parts of maint, pure: the boot entries (duo2-boot), the health
rules, the updates' settings and changes (duo2-updates).
python3 -m unittest discover -s tests (from maint/)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duo2maint import bootentry as be  # noqa: E402
from duo2maint import health, notify  # noqa: E402
from duo2maint import updates as up  # noqa: E402

REL = "7.3.4-duo2-g3ba7db140"


class EntryNames(unittest.TestCase):
    def test_id_and_release(self):
        self.assertEqual(be.entry_id(REL), "openduo-" + REL)
        self.assertEqual(be.release_of("openduo-" + REL), REL)
        self.assertIsNone(be.release_of("duo2"))
        for bad in ("", "7.3.4+", "a b", "../x", "-7.3"):
            self.assertFalse(be.valid_release(bad), bad)
            with self.assertRaises(ValueError):
                be.entry_id(bad)
        self.assertTrue(be.valid_release("7.3.4-duo2-g3ba7db140-dirty"))

    def test_counter_names(self):
        eid = be.entry_id(REL)
        self.assertEqual(be.file_name(eid, 3), eid + "+3.conf")
        self.assertEqual(be.file_name(eid, 2, 1), eid + "+2-1.conf")
        self.assertEqual(be.file_name(eid), eid + ".conf")
        self.assertEqual(be.parse_name(eid + "+3.conf"), {"id": eid, "left": 3, "done": 0})
        self.assertEqual(be.parse_name(eid + "+0-3.conf"), {"id": eid, "left": 0, "done": 3})
        self.assertEqual(be.parse_name(eid + ".conf"), {"id": eid, "left": None, "done": None})
        self.assertIsNone(be.parse_name("loader.conf.bak"))
        self.assertEqual(be.state(be.parse_name(eid + ".conf")), "good")
        self.assertEqual(be.state(be.parse_name(eid + "+1-2.conf")), "trying")
        self.assertEqual(be.state(be.parse_name(eid + "+0-3.conf")), "bad")

    def test_bless_and_bad(self):
        eid = be.entry_id(REL)
        self.assertEqual(be.blessed_name(eid + "+2-1.conf"), eid + ".conf")
        self.assertEqual(be.bad_name(eid + "+2-1.conf"), eid + "+0-3.conf")
        self.assertEqual(be.bad_name(eid + "+3.conf"), eid + "+0-3.conf")
        self.assertEqual(be.bad_name(eid + ".conf"), eid + "+0-1.conf")

    def test_only_ours(self):
        files = [
            "duo2.conf",
            "rescue.conf",
            "duo2-test+0-1.conf",
            "openduo-%s+3.conf" % REL,
            "openduo-7.3.5-duo2-gaaaa.conf",
            "openduo-.conf",
        ]
        ours = be.our_entries(files)
        self.assertEqual(
            sorted(ours), ["openduo-7.3.4-duo2-g3ba7db140", "openduo-7.3.5-duo2-gaaaa"]
        )


class EntryText(unittest.TestCase):
    def test_text(self):
        t = be.entry_text(
            REL,
            4,
            "quiet duo2.try=x hung_task_timeout_secs=60 openduo.entry=old panic=30",
            "/duo2/initrd-boot.img",
            "sm8350-microsoft-surface-duo2-bringup-j.dtb",
            "0.1.0-dev",
        )
        e = be.parse_entry(t)
        self.assertEqual(e["title"], "OpenDuo 0.1.0-dev (%s)" % REL)
        self.assertEqual(e["sort-key"], "duo2")
        self.assertEqual(e["version"], "10.4")
        self.assertEqual(e["linux"], "/openduo/%s/Image" % REL)
        self.assertEqual(e["initrd"], "/duo2/initrd-boot.img")
        self.assertEqual(
            e["devicetree"], "/openduo/%s/sm8350-microsoft-surface-duo2-bringup-j.dtb" % REL
        )
        self.assertEqual(e["options"], "quiet panic=30 openduo.entry=openduo-" + REL)
        self.assertEqual(
            be.booted_entry("root=x " + "openduo.entry=openduo-" + REL), "openduo-" + REL
        )
        self.assertIsNone(be.booted_entry("quiet duo2.try=abc"))
        self.assertEqual(be.seq_of("10.17"), 17)
        self.assertIsNone(be.seq_of("1"))


class BootOrder(unittest.TestCase):
    def order(self, entries):
        return [f for f, _e in be.boot_order(entries)]

    def test_new_kernel_first_then_previous_then_known_good(self):
        new = ("openduo-7.3.5-duo2-gbbbb+3.conf", {"sort-key": "duo2", "version": "10.12"})
        prev = ("openduo-7.3.4-duo2-gaaaa.conf", {"sort-key": "duo2", "version": "10.11"})
        kg = ("duo2.conf", {"sort-key": "duo2", "version": "1"})
        rescue = ("rescue.conf", {"sort-key": "rescue", "version": "1"})
        self.assertEqual(self.order([rescue, kg, prev, new]), [new[0], prev[0], kg[0], rescue[0]])

    def test_used_up_kernel_goes_last(self):
        bad = ("openduo-7.3.5-duo2-gbbbb+0-3.conf", {"sort-key": "duo2", "version": "10.12"})
        prev = ("openduo-7.3.4-duo2-gaaaa.conf", {"sort-key": "duo2", "version": "10.11"})
        kg = ("duo2.conf", {"sort-key": "duo2", "version": "1"})
        rescue = ("rescue.conf", {"sort-key": "rescue", "version": "1"})
        self.assertEqual(self.order([bad, kg, rescue, prev])[0], prev[0])
        self.assertEqual(self.order([bad, kg, rescue, prev])[-1], bad[0])

    def test_duo2_try_goes_before_everything_once(self):
        tr = ("duo2-test+1.conf", {"sort-key": "duo2", "version": be.TRY_VERSION})
        new = ("openduo-7.3.5-duo2-gbbbb+3.conf", {"sort-key": "duo2", "version": "10.12"})
        kg = ("duo2.conf", {"sort-key": "duo2", "version": "1"})
        self.assertEqual(self.order([kg, new, tr])[0], tr[0])
        used = ("duo2-test+0-1.conf", {"sort-key": "duo2", "version": be.TRY_VERSION})
        self.assertEqual(self.order([kg, new, used])[0], new[0])

    def test_numbers_compare_as_numbers(self):
        a = ("openduo-a.conf", {"sort-key": "duo2", "version": "10.9"})
        b = ("openduo-b.conf", {"sort-key": "duo2", "version": "10.10"})
        self.assertEqual(self.order([a, b])[0], b[0])


class KeepPrune(unittest.TestCase):
    def test_keeps_running_trying_two_good_newest_bad(self):
        e = {
            "openduo-a": be.parse_name("openduo-a.conf"),
            "openduo-b": be.parse_name("openduo-b.conf"),
            "openduo-c": be.parse_name("openduo-c.conf"),
            "openduo-d": be.parse_name("openduo-d+0-3.conf"),
            "openduo-e": be.parse_name("openduo-e+0-3.conf"),
            "openduo-f": be.parse_name("openduo-f+2-1.conf"),
        }
        seqs = {
            "openduo-a": 1,
            "openduo-b": 2,
            "openduo-c": 3,
            "openduo-d": 4,
            "openduo-e": 5,
            "openduo-f": 6,
        }
        self.assertEqual(be.prune_set(e, seqs, running="openduo-c"), ["openduo-a", "openduo-d"])
        self.assertEqual(be.prune_set(e, seqs, running="openduo-a"), ["openduo-d"])
        self.assertEqual(
            be.prune_set(e, seqs, running=None, keep=1), ["openduo-a", "openduo-b", "openduo-d"]
        )


class Health(unittest.TestCase):
    OK = {"compositor": "active", "touch": "active", "wifi_device": True, "wifi_connected": True}

    def test_pass(self):
        v, _r = health.evaluate(self.OK, 30)
        self.assertEqual(v, "pass")
        f = dict(self.OK, wifi_connected=False, modem_present=True, modem_registered=True)
        self.assertEqual(health.evaluate(f, 30)[0], "pass")

    def test_no_touch_runtime_is_not_the_kernels_fault(self):
        self.assertEqual(health.evaluate(dict(self.OK, touch="skipped"), 30)[0], "pass")

    def test_wait_then_hard_fail_without_picture(self):
        f = dict(self.OK, compositor="starting")
        self.assertEqual(health.evaluate(f, 60)[0], "wait")
        self.assertEqual(health.evaluate(f, 301)[0], "fail-hard")
        self.assertEqual(health.evaluate(dict(self.OK, compositor="failed"), 10)[0], "fail-hard")
        self.assertEqual(health.evaluate(dict(self.OK, touch="failed"), 10)[0], "fail-hard")

    def test_network_expected_but_missing_is_soft(self):
        f = dict(self.OK, wifi_connected=False, wifi_configured=True, modem_present=True)
        self.assertEqual(health.evaluate(f, 100)[0], "wait")
        self.assertEqual(health.evaluate(f, 300)[0], "fail-soft")

    def test_nothing_to_connect_to_passes(self):
        f = dict(
            self.OK,
            wifi_connected=False,
            wifi_configured=False,
            modem_present=True,
            modem_unusable=True,
        )
        self.assertEqual(health.evaluate(f, 10)[0], "pass")
        f = dict(self.OK, wifi_connected=False, wifi_configured=True, wifi_blocked=True)
        self.assertEqual(health.evaluate(f, 10)[0], "pass")

    def test_no_radio_at_all_is_hard(self):
        f = dict(self.OK, wifi_device=False, wifi_connected=False)
        self.assertEqual(health.evaluate(f, 60)[0], "wait")
        self.assertEqual(health.evaluate(f, health.RADIO_GRACE)[0], "fail-hard")

    def test_unit_state(self):
        self.assertEqual(health.unit_state("active"), "active")
        self.assertEqual(health.unit_state("inactive", "no"), "skipped")
        self.assertEqual(health.unit_state("inactive", "yes", "not-found"), "missing")
        self.assertEqual(health.unit_state("failed"), "failed")
        self.assertEqual(health.unit_state("activating"), "starting")
        self.assertEqual(health.unit_state("inactive", "yes", "loaded", "exit-code"), "failed")
        # restarting after a crash: not up yet, no verdict
        self.assertEqual(health.unit_state("activating", "yes", "loaded", "exit-code"), "starting")

    def test_unit_state_of_show(self):
        # systemd 257 prints the asked properties in its own order: Result
        # before LoadState before ActiveState (read by position, a running
        # duo-wc came out "failed" and the first packaged kernel was marked bad)
        running = "Result=success\nLoadState=loaded\nActiveState=active\nSubState=running\nConditionResult=yes\n"
        self.assertEqual(health.unit_state_of_show(running), "active")
        crashed = "Result=exit-code\nLoadState=loaded\nActiveState=failed\nSubState=failed\nConditionResult=yes\n"
        self.assertEqual(health.unit_state_of_show(crashed), "failed")
        again = "Result=exit-code\nLoadState=loaded\nActiveState=activating\nSubState=auto-restart\nConditionResult=yes\n"
        self.assertEqual(health.unit_state_of_show(again), "starting")
        skipped = "Result=success\nLoadState=loaded\nActiveState=inactive\nSubState=dead\nConditionResult=no\n"
        self.assertEqual(health.unit_state_of_show(skipped), "skipped")
        gone = "Result=success\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nConditionResult=yes\n"
        self.assertEqual(health.unit_state_of_show(gone), "missing")
        self.assertEqual(health.unit_state_of_show(""), "starting")


class Settings(unittest.TestCase):
    def test_conf_round_trip_and_defaults(self):
        cfg = up.parse_conf("")
        self.assertEqual(cfg["auto_install"], "off")
        self.assertEqual(cfg["channel"], "stable")
        cfg = up.parse_conf("auto_install = yes\nchannel = weird\nnotify = 0 # off\n")
        self.assertEqual(cfg, dict(up.DEFAULTS, auto_install="on", notify="off"))
        self.assertEqual(up.parse_conf(up.render_conf(cfg)), cfg)

    def test_apply(self):
        cfg = up.parse_conf("")
        new, err = up.apply_setting(cfg, "channel", "testing", developer_mode=False)
        self.assertTrue(err)
        new, err = up.apply_setting(cfg, "channel", "testing", developer_mode=True)
        self.assertIsNone(err)
        self.assertEqual(new["channel"], "testing")
        self.assertTrue(up.apply_setting(cfg, "auto", "maybe", True)[1])
        self.assertTrue(up.apply_setting(cfg, "shell", "on", True)[1])
        self.assertEqual(up.apply_setting(cfg, "auto", "on", False)[0]["auto_install"], "on")

    def test_when_an_install_may_run(self):
        self.assertIsNone(up.may_install(20, True))
        self.assertIsNone(up.may_install(50, False))
        self.assertTrue(up.may_install(49, False))

    def test_try_boot(self):
        self.assertTrue(up.try_boot("root=x rw duo2.try=20261119-1 quiet\n"))
        self.assertFalse(up.try_boot("root=x rw duo2.trying=1 quiet"))
        self.assertFalse(up.try_boot(""))
        self.assertIsNone(up.may_install(None, False))

    def test_the_nightly_install(self):
        on = up.parse_conf("auto_install = on")
        self.assertIsNone(up.may_auto(on, True, True, 3))
        self.assertTrue(up.may_auto(up.parse_conf(""), True, True, 3))
        self.assertTrue(up.may_auto(on, False, True, 3))
        self.assertTrue(up.may_auto(on, True, False, 3))
        self.assertTrue(up.may_auto(on, True, True, 14))

    def test_daily_check_waits_for_wifi(self):
        cfg = up.parse_conf("")
        self.assertTrue(up.may_check(cfg, False, False))
        self.assertIsNone(up.may_check(cfg, True, False))
        self.assertIsNone(up.may_check(cfg, False, True))
        self.assertIsNone(up.may_check(up.parse_conf("check_on_mobile_data = on"), False, False))


class Classify(unittest.TestCase):
    def test_ours_and_debians(self):
        plan = {
            "install": [
                ["openduo-duo-wc", "0.1.0~dev+1", "0.1.0~dev+2", "OpenDuo:stable [arm64]"],
                ["libcamera0.7", "0.7.2-1~duo2.2", "0.7.2-1~duo2.3", "OpenDuo:stable [arm64]"],
                [
                    "openssl",
                    "3.5.1-1",
                    "3.5.1-1+deb13u1",
                    "Debian-Security:13/stable-security [arm64]",
                ],
                ["foo", None, "1.0", "Debian:13.2/stable [arm64]"],
            ],
            "remove": [["bar", "2.0"]],
        }
        c = up.classify(plan)
        self.assertEqual([i["name"] for i in c["ours"]], ["openduo-duo-wc", "libcamera0.7"])
        self.assertEqual([i["name"] for i in c["debian"]], ["openssl", "foo"])
        self.assertTrue(c["debian"][0]["security"])
        self.assertFalse(c["debian"][1]["security"])
        self.assertEqual(c["removed"], [{"name": "bar", "old": "2.0"}])


CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Fixed
- the dock

## [0.2.0] - 2027-01-10

### Added
- updates

## [0.1.0] - 2026-12-01

- first

[0.1.0]: https://example.org/
"""


class Changes(unittest.TestCase):
    def test_sections(self):
        s = up.changelog_sections(CHANGELOG)
        self.assertEqual([x["version"] for x in s], ["Unreleased", "0.2.0", "0.1.0"])
        self.assertEqual(s[1]["date"], "2027-01-10")
        self.assertEqual(s[2]["text"], "- first")
        self.assertEqual(len(up.changelog_sections(CHANGELOG, limit=1)), 1)

    def test_field_round_trip(self):
        text = "### Fixed\n- the dock\n\n- and this"
        field = up.encode_field(text)
        self.assertTrue(all(line.startswith(" ") for line in field.splitlines()))
        self.assertIn(" .", field.splitlines())
        stanza = "Package: openduo-release\nVersion: 2\n%s:\n%s\nDescription: x\n" % (
            up.CHANGES_FIELD,
            field,
        )
        self.assertEqual(up.decode_field(up.field_of(stanza, up.CHANGES_FIELD)), text)
        two = "Package: a\nVersion: 1\n\nPackage: a\nVersion: 2\nX: y\n"
        self.assertIn("X: y", up.stanza_for(two, "2"))
        self.assertEqual(up.stanza_for(two, "3"), "")

    def test_newer_sections(self):
        s = up.changelog_sections(CHANGELOG)
        names = lambda inst, cand: [x["version"] for x in up.newer_sections(s, inst, cand)]  # noqa: E731
        self.assertEqual(names("0.1.0", "0.2.0"), ["0.2.0"])
        self.assertEqual(names("0.1.0", "0.3.0-dev"), ["Unreleased", "0.2.0"])
        self.assertEqual(names("0.2.0", "0.2.0"), [])
        self.assertEqual(names("0.1.0-dev", "0.1.0"), ["0.2.0", "0.1.0"])
        self.assertEqual(up.os_version_of("0.1.0~dev+20261117001503.g5b008a3"), "0.1.0-dev")
        self.assertEqual(up.os_version_of("0.2.0+20270110120000.gabcdef1"), "0.2.0")

    def test_testing_sources(self):
        stable = (
            "Types: deb\nURIs: https://example.org/apt\nSuites: stable\nComponents: main\n"
            "Signed-By: /usr/share/keyrings/openduo-archive-keyring.pgp\n"
        )
        t = up.testing_sources(stable)
        self.assertIn("Suites: testing\n", t)
        self.assertNotIn("Suites: stable", t)
        self.assertIn("Signed-By: /usr/share/keyrings/openduo-archive-keyring.pgp", t)


class Jobs(unittest.TestCase):
    """duo2-updates status: what the page is told about a job."""

    NOW = 1_800_000_000

    def test_unit_running(self):
        # a Type=oneshot job is "activating" while it works
        for answer in ("active\n", "activating\n", "deactivating", "reloading"):
            self.assertTrue(up.unit_running(answer), answer)
        for answer in ("inactive\n", "failed\n", "", None):
            self.assertFalse(up.unit_running(answer), answer)

    def test_running_unit_is_a_running_job(self):
        rec = {"job": "install", "state": "running", "step": "download", "started": self.NOW - 30}
        self.assertEqual(up.job_view(rec, {"install": True, "check": False}, self.NOW), rec)

    def test_unit_started_before_its_first_record(self):
        old = {"job": "check", "state": "done", "finished": self.NOW - 3600}
        v = up.job_view(old, {"install": True, "check": False}, self.NOW)
        self.assertEqual(
            v, {"job": "install", "state": "running", "step": "starting", "started": self.NOW}
        )
        self.assertEqual(up.job_view(None, {"install": True}, self.NOW)["state"], "running")

    def test_stopped_unit_is_a_failure_the_page_shows(self):
        rec = {"job": "install", "state": "running", "step": "install", "started": self.NOW - 300}
        v = up.job_view(rec, {"install": False, "check": False}, self.NOW)
        self.assertEqual(
            (v["state"], v["finished"], v["error"]), ("failed", self.NOW, "the job stopped")
        )
        self.assertEqual(rec["state"], "running", "the record itself is not changed")

    def test_starting_record_waits_for_its_unit(self):
        rec = up.starting_record("install", self.NOW - 5)
        self.assertEqual(up.job_view(rec, {"install": False}, self.NOW)["state"], "running")
        late = up.job_view(rec, {"install": False}, self.NOW + up.START_GRACE)
        self.assertEqual(late["state"], "failed")

    def test_finished_jobs_as_they_are(self):
        done = {
            "job": "install",
            "state": "done",
            "finished": self.NOW - 10,
            "reboot_required": True,
        }
        self.assertEqual(up.job_view(done, {"install": False, "check": False}, self.NOW), done)
        self.assertIsNone(up.job_view(None, {"install": False}, self.NOW))

    def test_install_phases(self):
        # the headings duo2-update run prints, in its order
        lines = [
            "== apt-get update\n",
            "== the plan: 8 to upgrade (5 security), 0 new, 0 to remove\n",
            "== downloading\n",
            "== try out afterwards\n",
            "== snapshot (8 packages repacked: the way back)\n",
            "  duo2-snapshot: 20261117-105824-pre-update made in 55 s (267 MB)\n",
            "== apt-get dist-upgrade (log: /var/log/duo2-update/x.log)\n",
            "== after the upgrade\n",
            "  ok   dpkg --audit: clean\n",
        ]
        got = [p for p in map(up.install_phase, lines) if p]
        self.assertEqual(got, ["refresh", "download", "snapshot", "install", "verify"])


class Notify(unittest.TestCase):
    def test_argv(self):
        a = notify.notify_argv("Updates", "icon", "Sum", "Body", action="default")
        self.assertEqual(a[:3], ["busctl", "--user", "call"])
        self.assertIn("susssasa{sv}i", a)
        self.assertEqual(a[a.index("Body") + 1 : a.index("Body") + 4], ["2", "default", "Open"])
        self.assertEqual(notify.notify_argv("U", "i", "S", "B")[a.index("Body") + 1], "0")


if __name__ == "__main__":
    unittest.main()
