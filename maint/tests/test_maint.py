"""duo2-update / duo2-snapshot / duo2-backup, the pure parts:
python3 -m unittest discover -s tests (from maint/)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duo2maint import aptplan, debver, system  # noqa: E402
from duo2maint import backup as bk
from duo2maint import snapshot as snap


class Versions(unittest.TestCase):
    def test_dpkg_order(self):
        cases = [  # a < b, as dpkg --compare-versions says
            ("1.0", "1.1"),
            ("1.0~rc1", "1.0"),
            ("1.0", "1.0+b1"),
            ("1.0-1", "1.0-2"),
            ("1.4.2-1", "1.4.2-1+deb13u1"),
            ("1.4.2-1+deb13u1", "1.4.2-1+duo2.1"),
            ("0.7.2-1~duo2.3", "0.7.2-1"),
            ("0.4.0-6", "0.7.2-1~duo2.3"),
            ("1:0.1", "2:0.0"),
            ("0:9", "1:1"),
            ("1.9", "1.10"),
            ("1.0a", "1.0b"),
            ("1.0", "1.0a"),
            ("1.0~", "1.0"),
            ("2.7.2-2", "2.7.2-2+deb13u1"),
            ("257.13-1~deb13u1", "257.13-1"),
            ("1.0.0", "1.0.0.1"),
        ]
        for a, b in cases:
            self.assertLess(debver.compare(a, b), 0, (a, b))
            self.assertGreater(debver.compare(b, a), 0, (b, a))
        self.assertEqual(debver.compare("1.0-1", "1.0-1"), 0)
        self.assertEqual(debver.compare("0:1.0", "1.0"), 0)
        self.assertEqual(debver.compare("1.00", "1.0"), 0)

    def test_local(self):
        self.assertTrue(debver.is_local("1.4.2-1+duo2.1"))
        self.assertTrue(debver.is_local("0.7.2-1~duo2.3"))
        self.assertFalse(debver.is_local("1.4.2-1+deb13u1"))
        self.assertEqual(debver.local_base("1.4.2-1+duo2.1"), "1.4.2-1")
        self.assertEqual(debver.local_base("0.7.2-1~duo2.3"), "0.7.2-1")


SIM = """NOTE: This is only a simulation!
Reading package lists...
Inst libz1 [1:1.3-1] (1:1.3-1+deb13u1 Debian-Security:13/stable-security [arm64])
Inst modemmanager [1.24.0-1+deb13u1] (1.24.0-1+deb13u2 Debian:13.3/stable [arm64]) []
Inst linux-image-6.12-arm64 (6.12.57-1 Debian:13.3/stable [arm64])
Inst pipewire [1.4.2-1+duo2.1] (1.4.2-1+deb13u1 Debian-Security:13/stable-security [arm64])
Remv oldthing [2.0-1]
Remv wpasupplicant [2:2.10-24]
Conf libz1 (1:1.3-1+deb13u1 Debian-Security:13/stable-security [arm64])
"""


class Plan(unittest.TestCase):
    def test_parse(self):
        p = aptplan.parse_simulation(SIM)
        self.assertEqual(
            [i[0] for i in p["install"]],
            ["libz1", "modemmanager", "linux-image-6.12-arm64", "pipewire"],
        )
        self.assertEqual(p["install"][0][1:3], ("1:1.3-1", "1:1.3-1+deb13u1"))
        self.assertIsNone(p["install"][2][1])
        self.assertTrue(aptplan.is_security(p["install"][0][3]))
        self.assertFalse(aptplan.is_security(p["install"][1][3]))
        self.assertEqual(p["remove"], [("oldthing", "2.0-1"), ("wpasupplicant", "2:2.10-24")])

    def test_review(self):
        p = aptplan.parse_simulation(SIM)
        inst = {"pipewire": "1.4.2-1+duo2.1", "libz1": "1:1.3-1"}
        refuse, note = aptplan.review(p, inst)
        text = "\n".join(refuse)
        self.assertIn("linux-image-6.12-arm64 would be installed", text)
        self.assertIn("pipewire 1.4.2-1+duo2.1 is our rebuild", text)
        self.assertIn("wpasupplicant would be removed (the phone needs it)", text)
        self.assertIn("oldthing would be removed (allow", text)
        refuse, note = aptplan.review(p, inst, allow_removals=True)
        self.assertNotIn("oldthing", "\n".join(refuse))
        self.assertIn("wpasupplicant", "\n".join(refuse))  # protected: never
        self.assertIn("new package: linux-image-6.12-arm64 6.12.57-1", note)

    def test_clean_plan(self):
        p = aptplan.parse_simulation(
            "Inst libz1 [1:1.3-1] (1:1.3-1+deb13u1 Debian-Security:13/stable-security [arm64])\n"
        )
        self.assertEqual(aptplan.review(p, {"libz1": "1:1.3-1"}), ([], []))

    def test_forbidden_patterns(self):
        for name in (
            "linux-image-arm64",
            "firmware-qcom-soc",
            "firmware-atheros",
            "systemd-boot",
            "grub-efi-arm64",
            "flash-kernel",
            "shim-signed",
        ):
            self.assertTrue(aptplan.matches(name, aptplan.FORBIDDEN), name)
        for name in (
            "systemd-boot-efi",
            "systemd-boot-tools",
            "linux-perf",
            "libfirmware-x",
            "systemd",
        ):
            self.assertFalse(aptplan.matches(name, aptplan.FORBIDDEN), name)

    def test_hints(self):
        h = aptplan.hints(["libqmi-glib5", "libgl1-mesa-dri", "systemd"])
        self.assertTrue(any(x.startswith("modem") for x in h))
        self.assertTrue(any(x.startswith("GPU") for x in h))
        self.assertTrue(any("re-executes" in x for x in h))
        self.assertEqual(aptplan.hints(["libfoo1"]), [])


POLICY = """pipewire:
  Installed: 1.4.2-1+duo2.1
  Candidate: 1.4.2-1+duo2.1
  Version table:
 *** 1.4.2-1+duo2.1 100
        100 /var/lib/dpkg/status
     1.4.2-1+deb13u1 500
        500 http://security.debian.org/debian-security trixie-security/main arm64 Packages
     1.4.2-1 500
        500 http://deb.debian.org/debian trixie/main arm64 Packages
libcamera0.7:
  Installed: 0.7.2-1~duo2.3
  Candidate: 0.7.2-1~duo2.3
  Version table:
 *** 0.7.2-1~duo2.3 100
        100 /var/lib/dpkg/status
libcamera-ipa:
  Installed: 0.7.2-1~duo2.3
  Candidate: 0.7.2-1~duo2.3
  Version table:
 *** 0.7.2-1~duo2.3 100
        100 /var/lib/dpkg/status
     0.4.0-6 500
        500 http://deb.debian.org/debian trixie/main arm64 Packages
"""


class Rebuilds(unittest.TestCase):
    def test_policy(self):
        pol = aptplan.parse_policy(POLICY)
        self.assertEqual(pol["pipewire"]["installed"], "1.4.2-1+duo2.1")
        self.assertEqual(
            sorted(pol["pipewire"]["versions"]), ["1.4.2-1", "1.4.2-1+deb13u1", "1.4.2-1+duo2.1"]
        )
        self.assertEqual(
            sorted(aptplan.archive_versions(pol["pipewire"])), ["1.4.2-1", "1.4.2-1+deb13u1"]
        )
        self.assertEqual(aptplan.archive_versions(pol["libcamera0.7"]), [])

    def test_needed(self):
        need = aptplan.rebuilds_needed(aptplan.parse_policy(POLICY))
        self.assertEqual(need, [("pipewire", "1.4.2-1+duo2.1", "1.4.2-1", "1.4.2-1+deb13u1")])


class Files(unittest.TestCase):
    ALIASES = aptplan.usr_aliases({"/lib": "usr/lib", "/bin": "usr/bin"})

    def test_normalize(self):
        self.assertEqual(self.ALIASES, {"/lib/": "/usr/lib/", "/bin/": "/usr/bin/"})
        self.assertEqual(
            aptplan.normalize("./lib/firmware/qcom/a660_gmu.bin", self.ALIASES),
            "/usr/lib/firmware/qcom/a660_gmu.bin",
        )
        self.assertEqual(aptplan.normalize("/usr//share/x", self.ALIASES), "/usr/share/x")
        self.assertEqual(aptplan.normalize("./.hidden", {}), "/.hidden")

    def test_overwrites(self):
        on_disk = {
            "/usr/lib/firmware/qcom/a660_gmu.bin",
            "/usr/share/doc/x/README",
            "/usr/bin/tool",
            "/usr/share/alsa/ucm2/ours.conf",
        }
        owned = {"/lib/firmware/other.bin", "/usr/bin/tool"}
        new = {
            "firmware-qcom-soc": [
                "./usr/lib/firmware/qcom/a660_gmu.bin",
                "./usr/lib/firmware/qcom/new.bin",
            ],
            "tool": ["./bin/tool"],
            "alsa-ucm-conf": ["./usr/share/alsa/ucm2/ours.conf"],
        }
        out = aptplan.overwrites(new, owned, on_disk.__contains__, self.ALIASES)
        self.assertEqual(
            out,
            [
                ("alsa-ucm-conf", "/usr/share/alsa/ucm2/ours.conf", False),
                ("firmware-qcom-soc", "/usr/lib/firmware/qcom/a660_gmu.bin", True),
            ],
        )

    def test_verify(self):
        text = (
            "??5?????? c /etc/initramfs-tools/update-initramfs.conf\n"
            "??5??????   /usr/lib/waydroid/tools/helpers/lxc.py\n"
            "??5??????   /usr/share/foo/bar\nmissing   /usr/x\n"
        )
        changed = aptplan.parse_verify(text)
        self.assertEqual(
            changed[:2],
            [
                ("/etc/initramfs-tools/update-initramfs.conf", True),
                ("/usr/lib/waydroid/tools/helpers/lxc.py", False),
            ],
        )
        what = dict(aptplan.edits_replaced(changed))
        self.assertIn("kept", what["/etc/initramfs-tools/update-initramfs.conf"])
        self.assertIn("duo2-waydroid-patch", what["/usr/lib/waydroid/tools/helpers/lxc.py"])
        self.assertIn("REPLACED", what["/usr/share/foo/bar"])


class Snapshots(unittest.TestCase):
    def test_names(self):
        n = snap.name_for(0, "Pre Update!")
        self.assertTrue(n.endswith("-pre-update"))
        self.assertTrue(snap.is_name(n))
        self.assertFalse(snap.is_name("../etc"))
        self.assertFalse(snap.is_name("20261109-010101-x.partial"))
        names = ["20261101-000000", "20261103-000000-pre-update", "20261102-000000", "junk"]
        self.assertEqual(snap.to_prune(names, 2), ["20261101-000000"])
        self.assertEqual(
            snap.to_prune(names, 0), ["20261101-000000", "20261102-000000"]
        )  # one stays
        self.assertEqual(snap.to_prune(names, 5), [])

    def test_packages(self):
        tsv = "a\tarm64\t1.0\tii \nb\tall\t2.0\trc \nc\tarm64\t3.0\thi \nbroken\n"
        self.assertEqual(snap.parse_packages(tsv), {"a": ("arm64", "1.0"), "c": ("arm64", "3.0")})
        before = {"a": ("arm64", "1.0"), "c": ("arm64", "3.0"), "d": ("all", "1")}
        now = {"a": ("arm64", "1.1"), "c": ("arm64", "3.0"), "e": ("all", "2")}
        self.assertEqual(
            snap.changes(before, now),
            {"changed": [("a", "1.0", "1.1")], "added": [("e", "2")], "removed": [("d", "1")]},
        )
        self.assertEqual(snap.deb_name("libz1", "1:1.3-1", "arm64"), "libz1_1.3-1_arm64.deb")

    def test_sums(self):
        text = "%s  /boot/efi/a\n%s  /boot/efi/b\nnot a line\n" % ("a" * 64, "b" * 64)
        before = snap.parse_sums(text)
        now = dict(before, **{"/boot/efi/b": "c" * 64, "/boot/efi/new": "d" * 64})
        del now["/boot/efi/a"]
        self.assertEqual(
            snap.compare_sums(before, now),
            {"changed": ["/boot/efi/b"], "gone": ["/boot/efi/a"], "new": ["/boot/efi/new"]},
        )

    def test_protected_sums(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in (
                "boot/efi/duo2/Image",
                "usr/lib/firmware/qcom/a.bin",
                "usr/lib/modules/%s/modules.dep" % os.uname().release,
                "usr/lib/modules/other/x.ko",
            ):
                os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                with open(os.path.join(root, rel), "w") as f:
                    f.write(rel)
            sums = system.protected_sums(root)
            rels = sorted(os.path.relpath(p, root) for p in sums)
            self.assertEqual(
                rels,
                [
                    "boot/efi/duo2/Image",
                    "usr/lib/firmware/qcom/a.bin",
                    "usr/lib/modules/%s/modules.dep" % os.uname().release,
                ],
            )
            self.assertEqual(snap.parse_sums(system.sums_text(sums)), sums)


class Backup(unittest.TestCase):
    def cfg(self, **kw):
        c = bk.parse_conf("TARGET=backup@nas.lan:\n", gui_user="duo")
        c.update(kw)
        return c

    def test_conf(self):
        c = bk.parse_conf(
            "TARGET = 'u@h:/srv/duo2' # comment\nPORT=2222\nNOPE=1\ninclude_android=no\n",
            gui_user="duo",
        )
        self.assertEqual(
            (c["TARGET"], c["PORT"], c["INCLUDE_ANDROID"]), ("u@h:/srv/duo2", "2222", "no")
        )
        self.assertNotIn("NOPE", c)
        self.assertEqual(bk.check_conf(c), [])
        self.assertTrue(bk.check_conf(bk.parse_conf("")))
        for bad in ("u@h:/x;rm -rf /", "u@h", "u@h:$(id)", "-oProxyCommand=x:/a"):
            self.assertTrue(bk.check_conf(bk.parse_conf("TARGET=" + bad)), bad)
        self.assertEqual(bk.check_conf(bk.parse_conf("TARGET=nas:backups/duo2", "duo")), [])
        self.assertTrue(bk.check_conf(bk.parse_conf("TARGET=n:a\nPORT=99999")))

    def test_user(self):
        self.assertEqual(bk.parse_conf("", gui_user="duo")["USER"], "duo")
        self.assertEqual(bk.parse_conf("USER=someone\n", gui_user="duo")["USER"], "someone")
        self.assertIn("USER is not a user name", bk.check_conf(bk.parse_conf("TARGET=n:a\n")))

    def test_destination_and_argv(self):
        self.assertEqual(bk.destination("backup@nas.lan:"), "backup@nas.lan:current/")
        self.assertEqual(bk.destination("u@h:/srv/duo2/"), "u@h:/srv/duo2/current/")
        argv = bk.rsync_argv(self.cfg(), "2026-11-10_033000", filter_path="/f")
        self.assertEqual(argv[-1], "backup@nas.lan:current/")
        self.assertEqual(argv[-3:-1], ["/./home/duo", "/var/backups/duo2/backup/./staged"])
        self.assertIn("--backup-dir=/deleted/2026-11-10_033000", argv)
        self.assertIn("--filter=merge /f", argv)
        self.assertNotIn("--dry-run", argv)
        plain = bk.rsync_argv(
            self.cfg(RESTRICTED="no", INCLUDE_ANDROID="no", BWLIMIT_KIB="500"), "D", True
        )
        self.assertIn("--backup-dir=../deleted/D", plain)
        self.assertIn("--exclude=/home/duo/.local/share/waydroid/", plain)
        self.assertIn("--bwlimit=500", plain)
        self.assertIn("--dry-run", plain)
        ssh = argv[argv.index("-e") + 1]
        self.assertIn("StrictHostKeyChecking=yes", ssh)
        self.assertIn("BatchMode=yes", ssh)

    def test_when(self):
        c = self.cfg()
        self.assertEqual(bk.may_run(c, wifi=False, ac=True, forced=False), "not on WiFi")
        self.assertEqual(bk.may_run(c, wifi=True, ac=False, forced=False), "not charging")
        self.assertIsNone(bk.may_run(c, wifi=True, ac=True, forced=False))
        self.assertIsNone(bk.may_run(c, wifi=False, ac=False, forced=True))
        self.assertIsNone(
            bk.may_run(self.cfg(ONLY_ON_WIFI="no", ONLY_CHARGING="no"), False, False, False)
        )
        self.assertTrue(bk.on_ac([{"type": "USB", "online": "1"}]))
        self.assertTrue(bk.on_ac([{"type": "Battery", "status": "Full"}]))
        self.assertFalse(
            bk.on_ac([{"type": "USB", "online": "0"}, {"type": "Battery", "status": "Discharging"}])
        )

    def test_key_line_and_stats(self):
        line = bk.authorized_keys_line("ssh-ed25519 AAAAC3x duo2-backup@duo2\n", "/srv/backup/duo2")
        self.assertEqual(
            line,
            'command="rrsync -wo /srv/backup/duo2",restrict ssh-ed25519 AAAAC3x duo2-backup@duo2',
        )
        stats = bk.parse_stats(
            "Number of files: 1,234 (reg: 1,000)\nNumber of regular files transferred: 12\n"
            "Total file size: 5,000,000 bytes\nTotal bytes sent: 65,432\n"
        )
        self.assertEqual(
            stats,
            {"files": 1234, "transferred_files": 12, "total_size": 5000000, "sent_bytes": 65432},
        )


if __name__ == "__main__":
    unittest.main()
