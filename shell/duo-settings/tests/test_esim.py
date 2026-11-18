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

    def test_qr_codes_and_their_parts(self):
        self.assertEqual(esim.normalize(" lpa:1$a.b$X\n"), "LPA:1$a.b$X")
        self.assertTrue(esim.looks_like_code("lpa:1$rsp.example.com$ABC"))
        self.assertFalse(esim.looks_like_code("https://example.com/x"))
        self.assertEqual(esim.server("LPA:1$rsp.example.com$ABC"), "rsp.example.com")
        self.assertEqual(esim.server("nonsense"), "")
        self.assertTrue(esim.needs_confirmation("LPA:1$a.b$X$$1"))
        self.assertFalse(esim.needs_confirmation("LPA:1$a.b$X"))

    def test_failure_text(self):
        self.assertIn("not confirmed", esim.failure_text({"error": "not confirmed"}))
        text = esim.failure_text(
            {"error": "No internet.", "step": "es9p_initiate_authentication", "detail": "x"}
        )
        self.assertTrue(text.startswith("No internet."))
        self.assertIn("(es9p_initiate_authentication: x)", text)
        self.assertEqual(esim.failure_text({}), "It did not work.")

    def test_progress(self):
        self.assertEqual(esim.progress_stage('{"progress": "confirmed"}')[0], 0.1)
        frac, text = esim.progress_stage('{"progress": "es10b_load_bound_profile_package"}')
        self.assertGreater(
            frac, esim.progress_stage('{"progress": "es9p_get_bound_profile_package"}')[0]
        )
        self.assertIn("Installing", text)
        for line in ('{"progress": "es9p_cancel_session"}', '{"ok": true}', "noise", ""):
            self.assertIsNone(esim.progress_stage(line), line)

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
