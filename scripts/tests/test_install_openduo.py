"""scripts/install-openduo.sh against a pretend phone (tests/fakephone.py as
adb and fastboot): python3 -m unittest discover -s tests (from scripts/).

The whole install runs when the WOA Project's files are on this machine
(OPENDUO_TEST_WOA, or firmware/woa-tools + firmware/uefi/2611.01 of the main
checkout): their pinned sha256 cannot be faked. The dry run and the refusals
need none of them."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "install-openduo.sh")
SERIAL = "FAKE0DUO2X42"
OTHER = "OTHERPHONE77"
PROTECTED = ("modemst1", "modemst2", "fsg", "fsc", "persist", "sfpd")
OTHER_PARTS = (
    "devinfo",
    "misc",
    "boot_a",
    "boot_b",
    "modem_a",
    "dsp_a",
    "vendor_boot_a",
    "super",
    "rawdump",
)


def woa_dir():
    d = os.environ.get("OPENDUO_TEST_WOA")
    if d:
        return d
    try:
        common = subprocess.run(
            ["git", "-C", HERE, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
        ).stdout.strip()
    except OSError:
        return None
    root = os.path.dirname(common) if common else None
    if not root:
        return None
    files = {
        "parted": os.path.join(root, "firmware/woa-tools/parted"),
        "surfaceduo2-headless.img": os.path.join(
            root, "firmware/woa-tools/surfaceduo2-headless.img"
        ),
        "Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip": os.path.join(
            root,
            "firmware/uefi/2611.01/Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip",
        ),
    }
    return files if all(os.path.isfile(p) for p in files.values()) else None


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Installer(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t)
        self.phone = os.path.join(self.t, "phone")
        os.makedirs(os.path.join(self.phone, "blk"))
        for i, p in enumerate(PROTECTED + OTHER_PARTS):
            with open(os.path.join(self.phone, "blk", p), "wb") as f:
                f.write(bytes([i + 1]) * (4096 * (i + 1)))
        self.state = {
            "mode": "android",
            "serial": SERIAL,
            "unlocked": "yes",
            "product": "surfaceduo2",
            "others": [OTHER],
            "sda_sectors": 58191872,
            "sda": [
                {"num": 1, "name": "ssd", "start": 6, "end": 7},
                {"num": 2, "name": "persist", "start": 8, "end": 8199, "fs": "ext4"},
                {"num": 7, "name": "vm-data", "start": 89480, "end": 97835},
                {"num": 8, "name": "userdata", "start": 98048, "end": 58191866},
            ],
        }
        # persist lives on sda in the table and as a file under its name
        self.write_state()
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        for tool in ("adb", "fastboot"):
            os.symlink(os.path.join(HERE, "fakephone.py"), os.path.join(self.bin, tool))
        self.images = os.path.join(self.t, "release")
        os.makedirs(self.images)
        self.rootfs = os.path.join(self.images, "openduo-0.1.0-surface-duo2-rootfs.img")
        self.esp = os.path.join(self.images, "openduo-0.1.0-surface-duo2-esp.img")
        with open(self.rootfs, "wb") as f:
            f.write(b"ext4" * 50000)
        with open(self.esp, "wb") as f:
            f.write(b"FAT" * 30000)
        with open(os.path.join(self.images, "SHA256SUMS"), "w") as f:
            for p in (self.rootfs, self.esp):
                f.write("%s  %s\n" % (sha(p), os.path.basename(p)))
        self.backup = os.path.join(self.t, "backup")
        self.woa = os.path.join(self.t, "woa")
        os.makedirs(self.woa)

    def write_state(self):
        with open(os.path.join(self.phone, "state.json"), "w") as f:
            json.dump(self.state, f)

    def read_state(self):
        with open(os.path.join(self.phone, "state.json")) as f:
            return json.load(f)

    def log(self):
        p = os.path.join(self.phone, "log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()

    def run_installer(self, *args, env=None):
        env = dict(
            os.environ,
            FAKEPHONE=self.phone,
            ADB=os.path.join(self.bin, "adb"),
            FASTBOOT=os.path.join(self.bin, "fastboot"),
            XDG_CACHE_HOME=os.path.join(self.t, "cache"),
            **(env or {}),
        )
        cmd = [SCRIPT, "--backup", self.backup, "--images", self.images, "--woa", self.woa, *args]
        return subprocess.run(
            cmd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300
        )

    def use_woa(self):
        files = woa_dir()
        if not files:
            self.skipTest("the WOA Project's files are not on this machine (OPENDUO_TEST_WOA)")
        if isinstance(files, str):
            files = {n: os.path.join(files, n) for n in os.listdir(files)}
        for name, path in files.items():
            os.symlink(path, os.path.join(self.woa, name))

    def assert_never_other(self):
        for line in self.log():
            self.assertNotIn(OTHER, line)
            self.assertNotIn("kill-server", line)
            self.assertNotIn("start-server", line)

    # --- the dry run -----------------------------------------------------------

    def test_dry_run_runs_only_the_device_lists(self):
        r = self.run_installer("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        ran = {" ".join(line.split()[:2]) for line in self.log()}
        self.assertLessEqual(
            ran, {"adb devices", "adb version", "fastboot devices", "fastboot --version"}, ran
        )
        out = r.stdout
        for want in (
            "reboot bootloader",
            "getvar unlocked",
            "surfaceduo2-headless.img",
            "parted -m -s /dev/block/sda unit s print",
            "mkpart esp fat32",
            "mkpart linux ext4",
            "set ESPN esp on",
            "exec-in",
            "boot_a",
            "boot_b",
            "reboot",
            "curl",
        ):
            self.assertIn(want, out)
        self.assertNotIn(SERIAL, out + r.stderr)
        self.assertIn(SERIAL[:2] + "…" + SERIAL[-2:], out)
        self.assertFalse(os.path.exists(self.backup))
        self.assertEqual(self.read_state()["mode"], "android")
        self.assert_never_other()

    def test_dry_run_without_a_phone(self):
        self.state["mode"] = "off"
        self.write_state()
        r = self.run_installer("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no Surface Duo 2 connected", r.stdout)
        self.assert_never_other()

    # --- refusals --------------------------------------------------------------

    def test_no_phone(self):
        self.state["mode"] = "off"
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no Surface Duo 2 found", r.stderr)
        self.assert_never_other()

    def test_locked_bootloader_stops_before_anything(self):
        self.state["unlocked"] = "no"
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bootloader is locked", r.stderr)
        self.assertFalse([x for x in self.log() if " boot " in x or "exec-in" in x])
        self.assert_never_other()

    def test_installed_phone_needs_reinstall(self):
        self.state["sda"][-1:] = [
            {
                "num": 8,
                "name": "esp",
                "start": 98048,
                "end": 360191,
                "fs": "fat32",
                "flags": "boot, esp",
            },
            {"num": 9, "name": "linux", "start": 360192, "end": 58191866, "fs": "ext4"},
        ]
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reinstall", r.stderr)
        self.assertFalse([x for x in self.log() if "exec-in" in x])

    def test_a_product_that_is_not_a_duo2_is_left_alone(self):
        self.state["mode"] = "fastboot"
        self.state["product"] = "dreamlte"
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no Surface Duo 2 found", r.stderr)
        self.assertFalse([x for x in self.log() if " boot " in x])

    # --- USB permissions (Linux: no udev rule for the phone) --------------------

    def assert_udev_advice(self, text):
        self.assertIn("no permissions", text)
        self.assertIn("sudo sh " + os.path.join(os.path.dirname(SCRIPT), "udev-rules.sh"), text)
        self.assertIn("unplug the phone", text)
        self.assertIn("adb kill-server", text)
        # short lines: a long pasted line breaks in some terminals
        lines = text.splitlines()
        start = [i for i, x in enumerate(lines) if "lists the phone" in x][0]
        end = [i for i, x in enumerate(lines) if "kill-server" in x][0]
        for line in lines[start : end + 1]:
            if "udev-rules.sh" not in line:
                self.assertLessEqual(len(line), 76, line)

    def test_no_permission_in_fastboot(self):
        self.state["mode"] = "fastboot"
        self.state["noperm"] = ["fastboot"]
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assert_udev_advice(r.stderr)
        # never `fastboot -s no ...`: it would wait for ever
        self.assertEqual([x for x in self.log() if x.startswith("fastboot -s")], [])
        self.assert_never_other()

    def test_no_permission_in_the_recovery(self):
        self.state["mode"] = "fastboot"
        self.state["noperm"] = ["recovery"]
        self.write_state()
        self.use_woa()
        r = self.run_installer("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("adb lists the phone", r.stderr)
        self.assert_udev_advice(r.stderr)
        self.assertEqual(self.read_state()["mode"], "recovery")
        self.assertFalse([x for x in self.log() if " shell " in x or "exec-" in x])
        self.assert_never_other()

    def test_dry_run_names_the_udev_fix(self):
        self.state["mode"] = "fastboot"
        self.state["noperm"] = ["fastboot"]
        self.write_state()
        r = self.run_installer("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_udev_advice(r.stdout)

    # --- steps, times and progress -------------------------------------------------

    def test_progress_of_the_big_copies(self):
        """A 32 MiB root image (from its .xz) and a 32 MiB partition, copied
        slowly: both in the background, with progress lines."""
        self.use_woa()
        big = bytes(range(256)) * (32 * 4096)
        with open(self.rootfs, "wb") as f:
            f.write(big)
        rootfs_sha = sha(self.rootfs)
        subprocess.run(["xz", "-0", "-T1", self.rootfs], check=True)
        with open(os.path.join(self.images, "SHA256SUMS"), "w") as f:
            f.write("%s  %s\n" % (rootfs_sha, os.path.basename(self.rootfs)))
            f.write("%s  %s\n" % (sha(self.rootfs + ".xz"), os.path.basename(self.rootfs) + ".xz"))
            f.write("%s  %s\n" % (sha(self.esp), os.path.basename(self.esp)))
        with open(os.path.join(self.phone, "blk", "modem_a"), "wb") as f:
            f.write(b"M" * (32 << 20))
        self.state["slow"] = 0.08
        self.write_state()
        r = self.run_installer("--yes", env={"OPENDUO_PROGRESS_EVERY": "1"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout[-3000:])
        out = r.stdout
        for n in range(1, 7):
            self.assertIn("[%d/6] " % n, out)
        self.assertIn("(step 4 took ", out)
        self.assertIn("unpacking " + os.path.basename(self.rootfs), out)
        self.assertIn("copying modem_a (32 MiB)", out)
        self.assertRegex(out, r"ok   modem_a \(32 MiB in \d+ s, \d+ MB/s\)")
        self.assertRegex(out, r"written \(32 MiB in \d+ s, \d+ MB/s\)")
        self.assertRegex(out, r"\d+ (KiB|MiB) of 32 MiB \(\d+ %\), \d+ MB/s")
        self.assertIn("Done on this computer in ", out)
        st = self.read_state()
        linux = [p for p in st["sda"] if p["name"] == "linux"][0]
        self.assertEqual(sha(os.path.join(self.phone, "blk", "sda%d" % linux["num"])), rootfs_sha)
        with open(os.path.join(self.backup, "modem_a.img"), "rb") as f:
            self.assertEqual(f.read(), b"M" * (32 << 20))

    def test_ctrl_c_stops_a_copy_in_the_background(self):
        """A background copy ignores the terminal's Ctrl-C; the installer
        stops it, so nothing goes on reading or writing the phone."""
        self.use_woa()
        with open(os.path.join(self.phone, "blk", "modem_a"), "wb") as f:
            f.write(b"M" * (32 << 20))
        self.state["slow"] = 0.2
        self.write_state()
        env = dict(
            os.environ,
            FAKEPHONE=self.phone,
            ADB=os.path.join(self.bin, "adb"),
            FASTBOOT=os.path.join(self.bin, "fastboot"),
            XDG_CACHE_HOME=os.path.join(self.t, "cache"),
        )
        cmd = [SCRIPT, "--backup", self.backup, "--images", self.images, "--woa", self.woa, "--yes"]
        p = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        part = os.path.join(self.backup, "modem_a.img.part")
        for _ in range(300):
            if os.path.exists(part) and os.path.getsize(part) > 0:
                break
            time.sleep(0.1)
        os.killpg(p.pid, signal.SIGINT)  # what the terminal sends to its foreground group
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 130, err)
        self.assertIn("stopped", err)
        size = os.path.getsize(part)
        time.sleep(1.5)
        self.assertEqual(os.path.getsize(part), size, "the copy went on after Ctrl-C")
        self.assertLess(size, 32 << 20)
        self.assertFalse([x for x in self.log() if "exec-in" in x])

    # --- the whole install -----------------------------------------------------

    def test_install_from_android(self):
        self.use_woa()
        before = {p: sha(os.path.join(self.phone, "blk", p)) for p in PROTECTED}
        r = self.run_installer("--yes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout[-3000:])
        st = self.read_state()
        self.assertEqual(st["mode"], "off")
        parts = {p["name"]: p for p in st["sda"]}
        self.assertNotIn("userdata", parts)
        self.assertEqual((parts["esp"]["start"], parts["esp"]["end"]), (98048, 98048 + 262144 - 1))
        self.assertEqual(
            (parts["linux"]["start"], parts["linux"]["end"]), (98048 + 262144, 58191866)
        )
        self.assertIn("esp", parts["esp"].get("flags", ""))
        blk = os.path.join(self.phone, "blk")
        self.assertEqual(sha(os.path.join(blk, "sda%d" % parts["esp"]["num"])), sha(self.esp))
        self.assertEqual(sha(os.path.join(blk, "sda%d" % parts["linux"]["num"])), sha(self.rootfs))
        uefi = os.path.join(self.woa, "uefi_nosb.img")
        for s in ("a", "b"):
            self.assertEqual(sha(os.path.join(blk, "boot_" + s)), sha(uefi))
        for p in PROTECTED:
            self.assertEqual(sha(os.path.join(blk, p)), before[p], p)
        # the backup: every partition but userdata and super, checked
        with open(os.path.join(self.backup, "SHA256SUMS")) as f:
            sums = f.read()
        for p in PROTECTED + ("devinfo", "misc", "boot_a", "boot_b"):
            self.assertIn("  %s.img\n" % p, sums)
            self.assertEqual(
                sha(os.path.join(self.backup, p + ".img")),
                before.get(p) or sha(os.path.join(self.backup, p + ".img")),
            )
        self.assertNotIn("super.img", sums)
        self.assertTrue(os.path.exists(os.path.join(self.backup, "parted-sda.txt")))
        self.assertEqual(os.stat(self.backup).st_mode & 0o777, 0o700)
        self.assertNotIn(SERIAL, r.stdout + r.stderr)
        self.assert_never_other()
        for n in range(1, 7):
            self.assertIn("[%d/6] " % n, r.stdout)
        self.assertIn("Done on this computer in ", r.stdout)
        self.assertIn("Touch does not work before that restart", r.stdout)

    def test_files_given_as_links(self):
        """The release files and the WOA files may be symbolic links (put there
        by hand): sizes and checks follow them."""
        self.use_woa()
        uefi = os.path.join(self.t, "uefi_nosb.img")
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys, zipfile; open(sys.argv[3], 'wb').write("
                "zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))",
                os.path.join(
                    self.woa, "Surface.Duo.2.UEFI-v2611.01.Secure.Boot.Disabled.Fast.Boot.zip"
                ),
                "Surface Duo 2 UEFI (Secure Boot Disabled) (Fast Boot)/uefi_nosb.img",
                uefi,
            ],
            check=True,
        )
        link = os.path.join(self.woa, "uefi_nosb.img")
        if os.path.lexists(link):  # OPENDUO_TEST_WOA may bring one
            os.remove(link)
        os.symlink(uefi, link)
        links = os.path.join(self.t, "links")
        os.makedirs(links)
        for name in os.listdir(self.images):
            os.symlink(os.path.join(self.images, name), os.path.join(links, name))
        self.images = links
        r = self.run_installer("--yes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout[-3000:])
        st = self.read_state()
        blk = os.path.join(self.phone, "blk")
        linux = [p for p in st["sda"] if p["name"] == "linux"][0]
        self.assertEqual(sha(os.path.join(blk, "sda%d" % linux["num"])), sha(self.rootfs))
        for s in ("a", "b"):
            self.assertEqual(sha(os.path.join(blk, "boot_" + s)), sha(uefi))

    def test_a_second_run_resumes_the_backup(self):
        self.use_woa()
        self.state["unlocked"] = "yes"
        self.write_state()
        first = self.run_installer("--yes")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.state = self.read_state()
        self.state["mode"] = "recovery"
        self.write_state()
        again = self.run_installer("--yes", "--reinstall")
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout[-2000:])
        self.assertIn("(saved before)", again.stdout)
        # the Android boot images saved by the first run are still there
        self.assertIn("boot_a (the earlier backup is kept", again.stdout)
        with open(os.path.join(self.backup, "boot_a.img"), "rb") as f:
            self.assertEqual(
                f.read(),
                bytes([PROTECTED.__len__() + OTHER_PARTS.index("boot_a") + 1])
                * (4096 * (len(PROTECTED) + OTHER_PARTS.index("boot_a") + 1)),
            )


class UdevRules(unittest.TestCase):
    """scripts/udev-rules.sh with stand-ins for id, getent and udevadm."""

    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t)
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        self.rules = os.path.join(self.t, "rules.d", "51-surface-duo.rules")
        self.calls = os.path.join(self.t, "calls")

    def tool(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)

    def run_helper(self, *args, uid=0, plugdev=True):
        self.tool("id", "echo %d" % uid)
        self.tool("getent", "exit %d" % (0 if plugdev else 2))
        self.tool("udevadm", 'echo "udevadm $*" >> %s' % self.calls)
        env = dict(
            os.environ, PATH=self.bin + ":" + os.environ["PATH"], OPENDUO_UDEV_RULES=self.rules
        )
        helper = os.path.join(os.path.dirname(SCRIPT), "udev-rules.sh")
        return subprocess.run(["sh", helper, *args], env=env, capture_output=True, text=True)

    def test_writes_both_vendors_and_reloads(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.rules) as f:
            rules = [x for x in f.read().splitlines() if not x.startswith("#")]
        self.assertEqual(
            rules,
            [
                'SUBSYSTEM=="usb", ATTR{idVendor}=="%s", MODE="0660", GROUP="plugdev", '
                'TAG+="uaccess"' % v
                for v in ("045e", "18d1")
            ],
        )
        self.assertEqual(os.stat(self.rules).st_mode & 0o777, 0o644)
        with open(self.calls) as f:
            self.assertEqual(
                f.read().splitlines(),
                ["udevadm control --reload", "udevadm trigger --subsystem-match=usb"],
            )
        self.assertIn("unplug the phone", r.stdout)

    def test_no_plugdev_group(self):
        r = self.run_helper(plugdev=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.rules) as f:
            text = f.read()
        self.assertNotIn("GROUP", text)
        self.assertIn('MODE="0660", TAG+="uaccess"', text)

    def test_remove(self):
        self.run_helper()
        r = self.run_helper("--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.rules))

    def test_needs_root(self):
        r = self.run_helper(uid=1000)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo sh", r.stderr)
        self.assertFalse(os.path.exists(self.rules))


if __name__ == "__main__":
    unittest.main()
