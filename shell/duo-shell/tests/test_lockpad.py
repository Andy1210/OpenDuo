import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import lockpad  # noqa: E402


class Pin(unittest.TestCase):
    def test_hash_and_check(self):
        stored = lockpad.hash_pin("2468", salt=b"0123456789abcdef", rounds=1000)
        self.assertTrue(stored.startswith("pbkdf2-sha256$1000$"))
        self.assertTrue(lockpad.check_pin("2468", stored))
        self.assertFalse(lockpad.check_pin("2469", stored))
        self.assertFalse(lockpad.check_pin("2468", "garbage"))
        self.assertFalse(lockpad.check_pin("2468", "md5$1$00$00"))

    def test_valid(self):
        self.assertTrue(lockpad.valid_pin("1234"))
        self.assertFalse(lockpad.valid_pin("123"))
        self.assertFalse(lockpad.valid_pin("12a4"))
        self.assertFalse(lockpad.valid_pin("\u0661\u0662\u0663\u0664"))  # Arabic-Indic digits
        self.assertFalse(lockpad.valid_pin("12\u00b234"))  # a superscript two
        self.assertFalse(lockpad.valid_pin("1" * 13))

    def test_nothing_locks_by_default(self):
        self.assertFalse(lockpad.lock_on_blank({}))
        self.assertFalse(lockpad.lock_on_blank({"lock_on_blank": "on"}))
        self.assertFalse(lockpad.lock_on_blank({"pin": "x", "lock_on_blank": "off"}))
        self.assertFalse(lockpad.lock_on_blank({"pin": "x"}))
        self.assertTrue(lockpad.lock_on_blank({"pin": "x", "lock_on_blank": "on"}))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(lockpad.read_conf(os.path.join(d, "none.conf")), {})


class Keypad(unittest.TestCase):
    def test_layout_fits_and_hits(self):
        keys = lockpad.keypad(538, 757, 272)
        self.assertEqual(
            [k[0] for k in keys], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok"]
        )
        for key, cx, cy, r in keys:
            self.assertTrue(0 <= cx - r and cx + r <= 538, key)
            self.assertTrue(272 <= cy - r and cy + r <= 757, key)
            self.assertEqual(lockpad.key_at(keys, cx, cy), key)
        self.assertIsNone(lockpad.key_at(keys, 5, 5))


class Entry(unittest.TestCase):
    def setUp(self):
        self.entry = lockpad.Entry(lockpad.hash_pin("1357", rounds=1000))

    def type(self, keys, now=100.0):
        result = None
        for k in keys:
            result = self.entry.press(k, now)
        return result

    def test_right_pin_unlocks(self):
        self.assertEqual(self.type(["1", "3", "5", "7", "ok"]), "unlock")

    def test_backspace(self):
        self.assertEqual(self.type(["1", "3", "9", "back", "5", "7", "ok"]), "unlock")

    def test_wrong_then_wait(self):
        for _ in range(lockpad.TRIES_BEFORE_WAIT - 1):
            self.assertIsNone(self.type(["0", "0", "0", "0", "ok"]))
            self.assertEqual(self.entry.message, "wrong")
        self.assertIsNone(self.type(["0", "0", "0", "0", "ok"]))
        self.assertEqual(self.entry.message, "wait")
        # While waiting nothing is taken, not even the right PIN.
        self.assertIsNone(self.type(["1", "3", "5", "7", "ok"], now=110.0))
        self.assertEqual(
            self.type(["1", "3", "5", "7", "ok"], now=100.0 + lockpad.WAIT_S + 1), "unlock"
        )

    def test_no_pin_never_unlocks(self):
        entry = lockpad.Entry(None)
        for k in ["1", "2", "3", "4", "ok"]:
            result = entry.press(k, 0)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()


class SwipeLockTest(unittest.TestCase):
    def test_a_swipe_up_unlocks(self):
        self.assertTrue(lockpad.swipe_unlocks(5, -150, 757))
        self.assertTrue(lockpad.swipe_unlocks(-30, -100, 538))

    def test_short_sideways_or_down_does_not(self):
        self.assertFalse(lockpad.swipe_unlocks(0, -40, 757))  # a tap or a nudge
        self.assertFalse(lockpad.swipe_unlocks(200, -120, 757))  # more sideways than up
        self.assertFalse(lockpad.swipe_unlocks(0, 200, 757))  # down
        # 12 % of a tall panel is more than the 90 px minimum.
        self.assertFalse(lockpad.swipe_unlocks(0, -100, 1000))

    def test_lock_on_close_is_on_unless_switched_off(self):
        self.assertTrue(lockpad.lock_on_close({}))
        self.assertFalse(lockpad.lock_on_close({"lock_on_close": "off"}))


class RecoveryKeyEntry(unittest.TestCase):
    """With the home encrypted the keypad takes the recovery key too
    (homecrypt/: 36 digits), a PIN keypad stops at 12."""

    def test_length(self):
        answers = []
        entry = lockpad.Entry(None, verify=lambda text: answers.append(text) or ("ok", 0))
        for _ in range(40):
            entry.press("7", 0)
        self.assertEqual(len(entry.digits), lockpad.MAX_LEN)
        entry.press("back", 0)
        entry.max_len = lockpad.RECOVERY_LEN
        for _ in range(40):
            entry.press("3", 0)
        self.assertEqual(len(entry.digits), lockpad.RECOVERY_LEN)
        self.assertEqual(entry.press("ok", 0), "unlock")
        self.assertEqual(len(answers[0]), lockpad.RECOVERY_LEN)

    def test_same_length_as_homecrypt(self):
        import os
        import sys

        top = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "homecrypt"
        )
        if not os.path.isdir(top):
            self.skipTest("no homecrypt/ next to duo-shell")
        sys.path.insert(0, top)
        from duohomecrypt import keys

        self.assertEqual(lockpad.RECOVERY_LEN, keys.RECOVERY_DIGITS)
