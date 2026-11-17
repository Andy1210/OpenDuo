"""The home's encryption, its pure parts and the lock side against a
temporary store (no kernel, no root): python3 -m unittest discover -s tests
(from homecrypt/). The kernel side is tried in tests/dryrun.sh (a loop-mounted
ext4 in a privileged container)."""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(HERE)
sys.path.insert(0, TOP)

from duohomecrypt import fscrypt, keys, lockside, migrate  # noqa: E402
from duohomecrypt import store as st  # noqa: E402

FAST = {"n": 1 << 12, "r": 8, "p": 1}


def load_tool():
    path = os.path.join(TOP, "duo2-home-crypt")
    loader = importlib.machinery.SourceFileLoader("duo2_home_crypt", path)
    spec = importlib.util.spec_from_loader("duo2_home_crypt", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


tool = load_tool()


class Keys(unittest.TestCase):
    def setUp(self):
        self.kdf = keys.KDF
        keys.KDF = dict(FAST)

    def tearDown(self):
        keys.KDF = self.kdf

    def test_wrap_unwrap(self):
        master = keys.new_master_key()
        p = keys.wrap(master, "pin", "246813")
        self.assertEqual(keys.unwrap(p, "pin", "246813"), master)
        self.assertIsNone(keys.unwrap(p, "pin", "246812"))
        self.assertIsNone(keys.unwrap(p, "recovery", "246813"))  # the kind is in the tag
        self.assertNotIn(master.hex(), json.dumps(p))
        # a fresh salt every time: the same PIN wraps differently
        q = keys.wrap(master, "pin", "246813")
        self.assertNotEqual(p["salt"], q["salt"])
        self.assertNotEqual(p["wrapped"], q["wrapped"])
        # anything changed in the protector: nothing opens
        for field in ("salt", "wrapped", "tag"):
            bad = dict(p)
            raw = bytearray(keys._unb64(bad[field]))
            raw[0] ^= 1
            bad[field] = keys._b64(bytes(raw))
            self.assertIsNone(keys.unwrap(bad, "pin", "246813"), field)
        self.assertIsNone(keys.unwrap(dict(p, n=1 << 13), "pin", "246813"))
        self.assertIsNone(keys.unwrap(dict(p, n=1 << 30), "pin", "246813"))  # limits
        self.assertIsNone(keys.unwrap({"kdf": "pbkdf2"}, "pin", "246813"))

    def test_kdf_limits(self):
        self.assertTrue(keys.kdf_ok({"n": 1 << 15, "r": 8, "p": 1}))
        for bad in (
            {"n": 3000, "r": 8, "p": 1},
            {"n": 1 << 21, "r": 8, "p": 1},
            {"n": 1 << 20, "r": 8, "p": 1},  # 1 GiB of memory
            {"n": 1 << 15, "r": True, "p": 1},
            {"n": 1 << 15, "r": 8},
        ):
            self.assertFalse(keys.kdf_ok(bad), bad)

    def test_shipped_kdf_is_the_measured_one(self):
        self.assertEqual(self.kdf, {"n": 1 << 15, "r": 8, "p": 1})

    def test_recovery_key(self):
        d = keys.new_recovery_key()
        self.assertEqual(len(d), keys.RECOVERY_DIGITS)
        self.assertTrue(d.isdigit())
        shown = keys.format_recovery_key(d)
        self.assertEqual(shown.count("-"), keys.RECOVERY_DIGITS // keys.RECOVERY_GROUP - 1)
        self.assertEqual(keys.parse_recovery_key(shown), d)
        self.assertEqual(keys.parse_recovery_key(" ".join(shown.split("-"))), d)
        self.assertIsNone(keys.parse_recovery_key(d[:-1]))
        self.assertIsNone(keys.parse_recovery_key(d[:-1] + "x"))
        self.assertIsNone(keys.parse_recovery_key("１" * keys.RECOVERY_DIGITS))  # not ASCII
        self.assertIsNone(keys.parse_recovery_key("123456"))

    def test_key_file(self):
        master = keys.new_master_key()
        kf = keys.new_key_file("duo", "/home/duo", keys.key_identifier(master))
        kf = keys.protect(kf, master, "pin", "1234")
        rk = keys.new_recovery_key()
        kf = keys.protect(kf, master, "recovery", rk)
        again = keys.parse_key_file(keys.format_key_file(kf))
        self.assertEqual(keys.open_with(again, "1234"), (master, "pin"))
        self.assertEqual(keys.open_with(again, keys.format_recovery_key(rk)), (master, "recovery"))
        self.assertEqual(keys.open_with(again, "4321"), (None, None))
        self.assertEqual(keys.open_with(keys.without(again, "recovery"), rk), (None, None))
        for bad in ("", "[]", '{"format": 2}', json.dumps(dict(kf, identifier="00")),
                    json.dumps(dict(kf, protectors={"fingerprint": {}}))):  # fmt: skip
            with self.assertRaises(keys.BadKeyFile):
                keys.parse_key_file(bad)

    def test_identifier_is_the_kernels(self):
        # Checked against the kernel's FS_IOC_ADD_ENCRYPTION_KEY in
        # tests/dryrun.sh; here: 16 bytes, fixed for a key, different keys
        # different identifiers.
        a, b = bytes(range(64)), bytes(64)
        self.assertEqual(keys.key_identifier(a).hex(), "8699c2c53707405da5aba5ae4d8583c0")
        self.assertEqual(len(keys.key_identifier(a)), 16)
        self.assertEqual(keys.key_identifier(a), keys.key_identifier(a))
        self.assertNotEqual(keys.key_identifier(a), keys.key_identifier(b))

    def test_pending_key(self):
        master = keys.new_master_key()
        self.assertEqual(keys.parse_pending_key(keys.pending_key_text(master)), master)
        self.assertIsNone(keys.parse_pending_key("nonsense"))
        self.assertIsNone(keys.parse_pending_key(None))


class Kernel(unittest.TestCase):
    def test_ioctl_numbers(self):
        # include/uapi/linux/fscrypt.h and ext4.h, as the kernel computes them
        self.assertEqual(fscrypt.FS_IOC_SET_ENCRYPTION_POLICY, 0x800C6613)
        self.assertEqual(fscrypt.FS_IOC_GET_ENCRYPTION_POLICY_EX, 0xC0096616)
        self.assertEqual(fscrypt.FS_IOC_ADD_ENCRYPTION_KEY, 0xC0506617)
        self.assertEqual(fscrypt.FS_IOC_REMOVE_ENCRYPTION_KEY, 0xC0406618)
        self.assertEqual(fscrypt.FS_IOC_GET_ENCRYPTION_KEY_STATUS, 0xC080661A)
        self.assertEqual(fscrypt.EXT4_IOC_SET_TUNE_SB_PARAM, 0x40E8662E)
        self.assertEqual(fscrypt.EXT4_IOC_GET_TUNE_SB_PARAM, 0x80E8662D)

    def test_layouts(self):
        ident = bytes(range(16))
        p = fscrypt.pack_policy_v2(ident, "AES-256-XTS", "AES-256-HCTR2", 0x0B)
        self.assertEqual(p, bytes([2, 1, 10, 0x0B, 0, 0, 0, 0]) + ident)
        back = fscrypt.unpack_policy(struct.pack("<Q", 24) + p)
        self.assertEqual((back.contents, back.filenames, back.flags, back.identifier),
                         (1, 10, 0x0B, ident))  # fmt: skip
        self.assertEqual(fscrypt.key_spec(ident), struct.pack("<II", 2, 0) + ident + bytes(16))
        self.assertEqual(fscrypt.flags_value(["PAD_32", "IV_INO_LBLK_64"]), 0x0B)
        t = fscrypt.pack_tune_features(fscrypt.EXT4_FEATURE_COMPAT_STABLE_INODES,
                                       fscrypt.EXT4_FEATURE_INCOMPAT_ENCRYPT)  # fmt: skip
        self.assertEqual(len(t), 232)
        self.assertEqual(struct.unpack_from("<I", t, 0)[0], fscrypt.EXT4_TUNE_FL_EDIT_FEATURES)
        self.assertEqual(struct.unpack_from("<III", t, 76), (0x800, 0x10000, 0))
        with self.assertRaises(ValueError):
            fscrypt.key_spec(b"short")


class State(unittest.TestCase):
    def test_parse_format(self):
        self.assertEqual(st.parse_state("")["state"], "plain")
        s = st.parse_state("state = encrypting\nprogress = 140\nrecovery = yes\nuser = duo\n"
                           "detail = Waiting: the battery is at 12 %.\nstate2 = x\n")  # fmt: skip
        self.assertEqual((s["state"], s["progress"], s["recovery"]), ("encrypting", 100, "yes"))
        self.assertEqual(s["detail"], "Waiting: the battery is at 12 %.")
        self.assertEqual(st.parse_state(st.format_state(s)), s)
        self.assertEqual(st.parse_state("state = broken")["state"], "plain")
        self.assertEqual(st.MIGRATING["encrypting"], "encrypt")
        self.assertNotIn("encrypted", st.MIGRATING)


class MigrationParts(unittest.TestCase):
    def test_rsync_progress(self):
        self.assertEqual(migrate.rsync_percent("    123,456,789  42%   12.34MB/s    0:00:10"), 42)
        self.assertEqual(migrate.rsync_percent("  1,000 100%  1.00kB/s 0:00:00 (xfr#1)"), 100)
        self.assertIsNone(migrate.rsync_percent("sending incremental file list"))

    def test_power_and_space(self):
        self.assertIsNone(migrate.check_power(None))
        self.assertIsNone(migrate.check_power((12, True)))
        self.assertIsNone(migrate.check_power((30, False)))
        self.assertIn("12 %", migrate.check_power((12, False)))
        self.assertEqual(migrate.space_needed(0), migrate.SPARE_MIN)
        self.assertEqual(migrate.space_needed(100 << 30), (100 << 30) + (5 << 30))

    def test_side_paths(self):
        self.assertEqual(
            migrate.side_paths("/home/duo"), ("/home/.duo.openduo-new", "/home/.duo.openduo-old")
        )


class Tool(unittest.TestCase):
    def test_calls(self):
        self.assertFalse(tool.calls_up(""))
        self.assertFalse(tool.calls_up("No calls were found"))
        self.assertFalse(
            tool.calls_up("    /org/freedesktop/ModemManager1/Call/3 incoming (terminated)")
        )
        for state in ("ringing-in", "active", "dialing", "held", "waiting", "ringing-out"):
            out = (
                "Found 1 calls:\n\n    /org/freedesktop/ModemManager1/Call/4 incoming (%s)\n"
                % state
            )
            self.assertTrue(tool.calls_up(out), state)

    def test_tmpfs_at(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(
                "22 1 259:9 / / rw,noatime shared:1 - ext4 /dev/sda9 rw\n"
                "90 22 0:50 / /home/duo rw,nosuid,nodev shared:40 - tmpfs duo2-home-locked rw,size=262144k,mode=700,uid=1000,gid=1000\n"
                "91 22 0:51 / /home/someone rw shared:41 - tmpfs tmpfs rw\n"
            )
        try:
            self.assertTrue(tool.tmpfs_at("/home/duo", f.name))
            self.assertFalse(tool.tmpfs_at("/home/someone", f.name))  # not ours
            self.assertFalse(tool.tmpfs_at("/home/test", f.name))
        finally:
            os.unlink(f.name)

    def test_units(self):
        with open(os.path.join(TOP, "systemd", "duo2-home-boot.service")) as f:
            boot = f.read()
        self.assertIn("Before=systemd-user-sessions.service", boot)  # user@ is after it
        self.assertIn("TimeoutStartSec=infinity", boot)
        with open(os.path.join(TOP, "systemd", "user", "50-duo2-home-locked.conf")) as f:
            self.assertIn("ConditionPathExists=!/run/duo2-home/locked", f.read())
        self.assertIn(
            "user-db:user\nfile-db:/var/lib/duo2-de/alarms\n",
            tool.profile_text("/var/lib/duo2-de/alarms"),
        )


class LockSide(unittest.TestCase):
    """lockside against a store in a temporary directory."""

    def setUp(self):
        self.kdf = keys.KDF
        keys.KDF = dict(FAST)
        self.dir = tempfile.mkdtemp()
        self.paths = st.Paths(
            store=os.path.join(self.dir, "lock"), run=os.path.join(self.dir, "run"),
            de=os.path.join(self.dir, "de"),
        )  # fmt: skip
        os.makedirs(self.paths.run)

    def tearDown(self):
        keys.KDF = self.kdf
        shutil.rmtree(self.dir)

    def enable(self, recovery=True):
        return lockside.enable("duo", "/home/duo", "2468", recovery, self.paths,
                               check_home=False, supported=True)  # fmt: skip

    def test_enable_then_pin_and_recovery(self):
        with self.assertRaises(lockside.Refused):
            lockside.enable("duo", "/home/duo", "2468", True, self.paths, check_home=False,
                            supported=False)  # fmt: skip
        digits = self.enable()
        self.assertEqual(len(digits), keys.RECOVERY_DIGITS)
        s = st.state(self.paths)
        self.assertEqual((s["state"], s["recovery"], s["user"]), ("encrypt-pending", "yes", "duo"))
        for f in (self.paths.key_file, self.paths.pending):
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.paths.state_file).st_mode & 0o777, 0o644)
        with open(self.paths.state_file) as f:
            public = f.read()
        master = lockside.opens("2468", self.paths)
        self.assertIsNotNone(master)
        self.assertNotIn(keys.pending_key_text(master).strip(), public)
        self.assertEqual(lockside.opens(keys.format_recovery_key(digits), self.paths), master)
        self.assertIsNone(lockside.opens("1111", self.paths))
        kf = migrate.key_file(self.paths)
        self.assertEqual(bytes.fromhex(kf["identifier"]), keys.key_identifier(master))
        self.assertIn("IV_INO_LBLK_64", kf["policy"]["flags"])
        with self.assertRaises(lockside.Refused):
            self.enable()  # once
        # A new PIN wraps the same key; the old one stops working.
        lockside.rewrap(master, "1357", self.paths)
        self.assertEqual(lockside.opens("1357", self.paths), master)
        self.assertIsNone(lockside.opens("2468", self.paths))
        # A new recovery key: the old one stops working.
        d2 = lockside.new_recovery(master, self.paths)
        self.assertEqual(lockside.opens(d2, self.paths), master)
        self.assertIsNone(lockside.opens(digits, self.paths))

    def test_no_recovery_key(self):
        self.assertIsNone(self.enable(recovery=False))
        self.assertEqual(st.state(self.paths)["recovery"], "no")
        self.assertNotIn("recovery", migrate.key_file(self.paths)["protectors"])

    def test_cancel_and_disable(self):
        self.enable()
        master = lockside.opens("2468", self.paths)
        lockside.cancel(master, self.paths)
        self.assertEqual(st.state(self.paths)["state"], "plain")
        self.assertTrue(os.path.exists(self.paths.key_file))  # checks the PIN until forget()
        self.assertFalse(os.path.exists(self.paths.pending))
        lockside.forget(self.paths)
        self.assertFalse(os.path.exists(self.paths.key_file))
        with self.assertRaises(lockside.Refused):
            lockside.disable(master, self.paths)  # not encrypted
        self.enable()
        master = lockside.opens("2468", self.paths)
        s = st.state(self.paths)
        s["state"] = "encrypted"
        st.save_state(s, self.paths)
        os.unlink(self.paths.pending)
        lockside.disable(master, self.paths)
        self.assertEqual(st.state(self.paths)["state"], "decrypt-pending")
        self.assertEqual(keys.parse_pending_key(st.read(self.paths.pending)), master)
        lockside.cancel(master, self.paths)
        self.assertEqual(st.state(self.paths)["state"], "encrypted")
        self.assertFalse(os.path.exists(self.paths.pending))
        self.assertTrue(os.path.exists(self.paths.key_file))

    def test_after_unlock(self):
        self.enable()
        master = lockside.opens("2468", self.paths)
        self.assertFalse(lockside.after_unlock(master, self.paths, start=False))  # not locked
        self.assertFalse(os.path.exists(self.paths.unlock_key))
        st.touch(self.paths.locked)
        self.assertTrue(lockside.after_unlock(master, self.paths, start=False))
        self.assertEqual(os.stat(self.paths.unlock_key).st_mode & 0o777, 0o600)
        self.assertEqual(keys.parse_pending_key(st.read(self.paths.unlock_key)), master)

    def test_remove_overwrites(self):
        p = os.path.join(self.dir, "secret")
        with open(p, "w") as f:
            f.write("secret")
        st.remove(p)
        self.assertFalse(os.path.exists(p))
        st.remove(p)  # gone already: fine


if __name__ == "__main__":
    unittest.main()
