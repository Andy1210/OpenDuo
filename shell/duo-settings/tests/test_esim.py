import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duosettings import esim  # noqa: E402


class Esim(unittest.TestCase):
    def test_codes(self):
        self.assertTrue(esim.looks_like_code("LPA:1$rsp.example.com$QR-G-5C"))
        self.assertTrue(esim.looks_like_code(" 1$smdp.example.net:443$ABC "))
        self.assertFalse(esim.looks_like_code("rsp.example.com"))
        self.assertFalse(esim.looks_like_code("LPA:1$a b$c"))

    def test_mask(self):
        self.assertEqual(esim.mask("89000000000000000022"), "\u2022\u2022\u2022\u2022 0022")
        self.assertEqual(esim.mask(""), "")

    def test_parse(self):
        self.assertEqual(
            esim.parse('noise\n{"ok": true, "profiles": []}\n'), {"ok": True, "profiles": []}
        )
        self.assertEqual(esim.parse("")["ok"], False)
        self.assertEqual(esim.parse("[1]")["ok"], False)


if __name__ == "__main__":
    unittest.main()
