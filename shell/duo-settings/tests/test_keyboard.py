"""Settings > Keyboard's pure part (python3 -m unittest discover -s tests)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import keyboard as kb  # noqa: E402

DEFAULT = [("xkb", "hu"), ("ibus", "presage:en")]


class KeyboardLayouts(unittest.TestCase):
    def test_rows(self):
        self.assertEqual(kb.describe(("xkb", "hu")), ("Magyar", None))
        self.assertEqual(kb.describe(("ibus", "presage:en")), ("English (US)", "English"))
        self.assertEqual(kb.describe(("xkb", "de+nodeadkeys")), ("Deutsch (nodeadkeys)", None))
        self.assertEqual(kb.layouts(DEFAULT), [("hu", ""), ("us", "")])

    def test_move_and_remove(self):
        self.assertEqual(kb.move(DEFAULT, 1, -1), [("ibus", "presage:en"), ("xkb", "hu")])
        self.assertIsNone(kb.move(DEFAULT, 0, -1))
        self.assertIsNone(kb.move(DEFAULT, 1, 1))
        self.assertEqual(kb.remove(DEFAULT, 0), [("ibus", "presage:en")])
        # The last layout stays.
        self.assertIsNone(kb.remove([("xkb", "hu")], 0))
        self.assertIsNone(kb.remove([("xkb", "hu"), ("ibus", "anthy")], 0))
        self.assertIsNone(kb.remove(DEFAULT, 5))

    def test_add(self):
        self.assertEqual(kb.add(DEFAULT, "de"), DEFAULT + [("xkb", "de")])
        self.assertIsNone(kb.add(DEFAULT, "hu"))
        # US is there: the word suggestions' layout.
        self.assertIsNone(kb.add(DEFAULT, "us"))
        offered = [k for k, _n in kb.addable(DEFAULT)]
        self.assertEqual(offered[:3], ["de", "gb", "es"])
        self.assertNotIn("hu", offered)
        self.assertNotIn("us", offered)
        full = [("xkb", k) for k in ("hu", "us", "de", "fr")]
        self.assertFalse(kb.can_add(full))
        self.assertEqual(kb.addable(full), [])
        self.assertIsNone(kb.add(full, "it"))


if __name__ == "__main__":
    unittest.main()
