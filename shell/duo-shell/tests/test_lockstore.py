import importlib.machinery
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from duoshell import lockpad  # noqa: E402
from duoshell import lockstore as ls


class Policy(unittest.TestCase):
    def test_parse_and_format(self):
        self.assertEqual(ls.parse_policy(""), ls.DEFAULT_POLICY)
        p = ls.parse_policy("pin = yes\nlock_on_blank = on\nlock_on_close = off\nbad = 1\n")
        self.assertEqual(p, {"pin": "yes", "lock_on_blank": "on", "lock_on_close": "off"})
        self.assertEqual(ls.parse_policy(ls.format_policy(p)), p)
        self.assertEqual(
            ls.parse_policy("pin = sure\nlock_on_close = maybe")["lock_on_close"], "on"
        )

    def test_rules(self):
        self.assertFalse(ls.lock_on_blank({"pin": "no", "lock_on_blank": "on"}))
        self.assertTrue(ls.lock_on_blank({"pin": "yes", "lock_on_blank": "on"}))
        self.assertTrue(ls.lock_on_close({}))
        self.assertTrue(ls.weakens("lock_on_close", "off", {"lock_on_close": "on"}))
        self.assertFalse(ls.weakens("lock_on_close", "on", {"lock_on_close": "off"}))
        self.assertFalse(ls.weakens("pin", "off", {"pin": "yes"}))

    def test_back_off(self):
        self.assertEqual(
            [ls.wait_after(n) for n in range(1, 10)], [0, 0, 0, 0, 30, 60, 120, 240, 480]
        )
        self.assertEqual(ls.wait_after(40), ls.MAX_WAIT_S)
        self.assertEqual(ls.parse_tries(ls.format_tries(7, 123.5)), (7, 123.5))
        self.assertEqual(ls.parse_tries("garbage\nfailures x"), (0, 0.0))


def load_helper(store):
    path = os.path.join(HERE, "..", "duo-lock-helper")
    loader = importlib.machinery.SourceFileLoader("duo_lock_helper", path)
    spec = importlib.util.spec_from_loader("duo_lock_helper", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.ls.STORE = store
    module.ls.PIN_FILE = os.path.join(store, "pin")
    module.ls.POLICY_FILE = os.path.join(store, "policy")
    module.ls.TRIES_FILE = os.path.join(store, "tries")
    module.lockpad.ROUNDS = 1000
    return module


class Helper(unittest.TestCase):
    """The helper against a store in a temporary directory."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.dir.name, "duo2-lock")
        self.h = load_helper(self.store)
        self._hash = lockpad.hash_pin
        self.h.lockpad.hash_pin = lambda pin, salt=None, rounds=1000: self._hash(pin, salt, rounds)

    def tearDown(self):
        self.h.lockpad.hash_pin = self._hash
        self.dir.cleanup()

    def run_helper(self, args, stdin=""):
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch("sys.stdout", io.StringIO()) as out,
        ):
            code = self.h.main(args)
        return code, out.getvalue()

    def test_write_does_not_follow_a_planted_link(self):
        """migrate writes as root into a user's directory: a link left at the
        temporary name must not carry the write elsewhere."""
        target = os.path.join(self.dir.name, "victim")
        with open(target, "w") as f:
            f.write("untouched")
        path = os.path.join(self.dir.name, "lock.conf")
        os.symlink(target, path + ".new")
        self.h.write(path, "pin=x\n", 0o600)
        with open(target) as f:
            self.assertEqual(f.read(), "untouched")
        with open(path) as f:
            self.assertEqual(f.read(), "pin=x\n")

    def test_no_pin_then_set_verify_clear(self):
        self.assertEqual(self.run_helper(["verify"], "1234\n")[0], ls.NO_PIN)
        self.assertEqual(self.run_helper(["set"], "\n12\n")[0], ls.INVALID)
        self.assertEqual(self.run_helper(["set"], "\n2468\n")[0], ls.OK)
        self.assertEqual(os.stat(os.path.join(self.store, "pin")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(self.store, "policy")).st_mode & 0o777, 0o644)
        self.assertEqual(ls.read_policy(os.path.join(self.store, "policy"))["pin"], "yes")
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "1111\n")[0], ls.WRONG)
        # Changing or removing it needs the current one.
        self.assertEqual(self.run_helper(["set"], "1111\n1357\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["clear"], "1111\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["set"], "2468\n1357\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "1357\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["policy", "lock_on_blank", "on"])[0], ls.OK)
        self.assertEqual(self.run_helper(["clear"], "1357\n")[0], ls.OK)
        policy = ls.read_policy(os.path.join(self.store, "policy"))
        self.assertEqual((policy["pin"], policy["lock_on_blank"]), ("no", "off"))
        self.assertFalse(os.path.exists(os.path.join(self.store, "pin")))

    def test_back_off_is_the_helpers(self):
        self.run_helper(["set"], "\n2468\n")
        for _ in range(4):
            self.assertEqual(self.run_helper(["verify"], "0000\n")[0], ls.WRONG)
        code, out = self.run_helper(["verify"], "0000\n")
        self.assertEqual((code, out.split()[0]), (ls.WAIT, "wait"))
        # While waiting even the right PIN is not taken.
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.WAIT)

    def test_weakening_a_rule_needs_the_pin(self):
        self.assertEqual(self.run_helper(["policy", "lock_on_blank", "on"])[0], ls.INVALID)
        # No PIN: lock_on_close goes off freely.
        self.assertEqual(self.run_helper(["policy", "lock_on_close", "off"])[0], ls.OK)
        self.assertEqual(self.run_helper(["policy", "lock_on_close", "on"])[0], ls.OK)
        self.run_helper(["set"], "\n2468\n")
        self.assertEqual(self.run_helper(["policy", "lock_on_close", "off"], "1111\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["policy", "lock_on_close", "off"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["policy", "lock_on_close", "on"])[0], ls.OK)

    def test_migrate_only_takes_a_users_lock_conf(self):
        home = os.path.join(self.dir.name, "home", ".config", "duo-shell")
        os.makedirs(home)
        conf = os.path.join(home, "lock.conf")
        with open(conf, "w") as f:
            f.write("pin = %s\nlock_on_close = off\n" % self._hash("9753", None, 1000))
        if os.getuid() == 0:
            self.skipTest("run as a user (migrate refuses root's files)")
        self.assertEqual(self.run_helper(["migrate", "/etc/passwd"])[0], ls.INVALID)
        self.assertEqual(self.run_helper(["migrate", conf])[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "9753\n")[0], ls.OK)
        with open(conf) as f:
            self.assertNotIn("pin", f.read())
        self.assertEqual(ls.read_policy(os.path.join(self.store, "policy"))["lock_on_close"], "off")


class HomeEncrypted(Helper):
    """The helper with the home encrypted (homecrypt/): the key file checks
    the PIN, the recovery key works as the current PIN, the PIN cannot be
    removed, a PIN change re-wraps the key (duohomecrypt against the same
    temporary store)."""

    def setUp(self):
        super().setUp()
        h = self.h
        if h.lockside is None:
            self.skipTest("homecrypt/ is not next to duo-shell")
        self.paths = h.hs.Paths(
            store=self.store, run=os.path.join(self.dir.name, "run"), de=self.dir.name
        )
        os.makedirs(self.paths.run)
        self._saved = (h.hs.PATHS, h.hk.KDF, h.lockside.check_user_home, h.lockside.subprocess.call)
        h.hs.PATHS = self.paths
        h.hk.KDF = {"n": 1 << 12, "r": 8, "p": 1}
        h.lockside.check_user_home = lambda user, home: None
        self.started = []
        h.lockside.subprocess.call = lambda argv, **_kw: self.started.append(argv) or 0
        self.env = mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())})
        self.env.start()
        self.supported = mock.patch(
            "duohomecrypt.fscrypt.fs_supports_encryption", lambda *a, **k: True
        )
        self.supported.start()

    def tearDown(self):
        h = self.h
        if h.lockside is not None:
            h.hs.PATHS, h.hk.KDF, h.lockside.check_user_home, h.lockside.subprocess.call = (
                self._saved
            )
            self.env.stop()
            self.supported.stop()
        super().tearDown()

    def pin_file(self):
        with open(os.path.join(self.store, "pin")) as f:
            return f.read().strip()

    def enable(self):
        self.assertEqual(self.run_helper(["set"], "\n2468\n")[0], ls.OK)
        code, out = self.run_helper(["home", "enable", "recovery"], "2468\n")
        self.assertEqual(code, ls.OK, out)
        self.assertTrue(out.startswith("recovery "))
        return out.split()[1]

    def test_enable_and_the_pin(self):
        self.assertEqual(self.run_helper(["home", "enable", "recovery"], "2468\n")[0], ls.NO_PIN)
        recovery = self.enable()
        self.assertEqual(self.pin_file(), "homekey")  # no second copy of the PIN
        self.assertEqual(self.h.hs.state()["state"], "encrypt-pending")
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "2469\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["verify"], recovery + "\n")[0], ls.OK)
        # once only
        self.assertEqual(self.run_helper(["home", "enable", "norecovery"], "2468\n")[0], 6)
        # the PIN stays while the home is encrypted
        self.assertEqual(self.run_helper(["clear"], "2468\n")[0], self.h.ENCRYPTED)
        self.assertTrue(os.path.exists(os.path.join(self.store, "pin")))

    def test_forgotten_pin_and_the_recovery_key(self):
        recovery = self.enable()
        # the recovery key as the current PIN: a new PIN, the same key
        self.assertEqual(self.run_helper(["set"], "%s\n1357\n" % recovery)[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "1357\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.WRONG)
        self.assertEqual(self.pin_file(), "homekey")
        # a new recovery key: the old one stops working
        code, out = self.run_helper(["home", "recovery-key"], "1357\n")
        self.assertEqual(code, ls.OK)
        self.assertEqual(self.run_helper(["verify"], recovery + "\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["verify"], out.split()[1] + "\n")[0], ls.OK)

    def test_disable_and_cancel(self):
        self.enable()
        # not encrypted yet (waits for the start): only cancel
        self.assertEqual(self.run_helper(["home", "disable"], "2468\n")[0], 6)
        s = self.h.hs.state()
        s["state"] = "encrypted"
        self.h.hs.save_state(s)
        os.unlink(self.paths.pending)
        self.assertEqual(self.run_helper(["home", "disable"], "1111\n")[0], ls.WRONG)
        self.assertEqual(self.run_helper(["home", "disable"], "2468\n")[0], ls.OK)
        self.assertEqual(self.h.hs.state()["state"], "decrypt-pending")
        self.assertTrue(self.pin_file().startswith("pbkdf2-sha256$"))  # for after it
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["home", "cancel"], "2468\n")[0], ls.OK)
        self.assertEqual(self.h.hs.state()["state"], "encrypted")
        self.assertEqual(self.pin_file(), "homekey")

    def test_cancel_before_the_start_keeps_the_pin(self):
        recovery = self.enable()
        # the pin file gets the PIN's hash back: the recovery key is not enough
        self.assertEqual(self.run_helper(["home", "cancel"], recovery + "\n")[0], ls.INVALID)
        self.assertEqual(self.run_helper(["home", "cancel"], "2468\n")[0], ls.OK)
        self.assertEqual(self.h.hs.state()["state"], "plain")
        self.assertTrue(self.pin_file().startswith("pbkdf2-sha256$"))
        self.assertFalse(os.path.exists(self.paths.key_file))
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], recovery + "\n")[0], ls.WRONG)

    def test_a_failed_try_keeps_the_pin_working(self):
        """The boot job's encryption failed: plain again, the key file stays
        and still checks the PIN (the pin file says only "homekey")."""
        self.enable()
        os.unlink(self.paths.pending)
        s = self.h.hs.state()
        s.update(state="plain", detail="Not encrypted: not enough free space")
        self.h.hs.save_state(s)
        self.assertEqual(self.pin_file(), "homekey")
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "1111\n")[0], ls.WRONG)
        # a new PIN works the same way
        self.assertEqual(self.run_helper(["set"], "2468\n1357\n")[0], ls.OK)
        self.assertEqual(self.run_helper(["verify"], "1357\n")[0], ls.OK)
        # a new try replaces the key file
        code, _out = self.run_helper(["home", "enable", "norecovery"], "1357\n")
        self.assertEqual(code, ls.OK)
        self.assertEqual(self.h.hs.state()["state"], "encrypt-pending")
        # or the PIN goes, and the key file with it
        s = self.h.hs.state()
        s["state"] = "plain"
        self.h.hs.save_state(s)
        self.assertEqual(self.run_helper(["clear"], "1357\n")[0], ls.OK)
        self.assertFalse(os.path.exists(self.paths.key_file))
        self.assertEqual(self.run_helper(["verify"], "1357\n")[0], ls.NO_PIN)

    def test_first_unlock(self):
        self.enable()
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertEqual(self.started, [])  # not locked: nothing to hand on
        self.h.hs.touch(self.paths.locked)
        self.assertEqual(self.run_helper(["verify"], "2468\n")[0], ls.OK)
        self.assertTrue(os.path.exists(self.paths.unlock_key))
        self.assertIn("duo2-home-settle.service", self.started[0])
        os.unlink(self.paths.unlock_key)
        self.assertEqual(self.run_helper(["verify"], "1111\n")[0], ls.WRONG)
        self.assertFalse(os.path.exists(self.paths.unlock_key))

    def test_status_and_usage(self):
        import json

        code, out = self.run_helper(["home", "status"])
        self.assertEqual((code, json.loads(out)["state"]), (ls.OK, "plain"))
        for argv in (["home"], ["home", "enable"], ["home", "enable", "yes"], ["home", "x"]):
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(self.run_helper(argv)[0], ls.INVALID, argv)


class EntryVerify(unittest.TestCase):
    def test_entry_asks_the_helper(self):
        answers = [("wrong", 0), ("wait", 30), ("ok", 0)]
        entry = lockpad.Entry(None, verify=lambda pin: answers.pop(0))
        for k in "1234":
            entry.press(k, 0)
        self.assertIsNone(entry.press("ok", 0))
        self.assertEqual(entry.message, "wrong")
        for k in "1234":
            entry.press(k, 1)
        entry.press("ok", 1)
        self.assertEqual(entry.message, "wait")
        self.assertTrue(entry.waiting(10))
        for k in "1234":
            entry.press(k, 40)
        self.assertEqual(entry.press("ok", 40), "unlock")


if __name__ == "__main__":
    unittest.main()
