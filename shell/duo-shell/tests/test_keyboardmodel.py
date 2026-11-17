import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import keyboardmodel as km  # noqa: E402

# The image's default (90_duo2-os.gschema.override).
DEFAULT = [("xkb", "hu"), ("ibus", "presage:en")]


class Layouts(unittest.TestCase):
    def test_the_system_list_as_xkb_layouts(self):
        self.assertEqual(km.layouts(DEFAULT), [("hu", ""), ("us", "")])
        self.assertEqual(
            km.layouts([("xkb", "de+nodeadkeys"), ("xkb", "us")]),
            [("de", "nodeadkeys"), ("us", "")],
        )
        # Word completion on the US layout next to the US layout: once.
        self.assertEqual(km.layouts([("xkb", "us"), ("ibus", "presage:en")]), [("us", "")])
        # Other input methods are no layouts; xkb has four groups.
        self.assertEqual(km.layouts([("ibus", "anthy"), ("xkb", "fr")]), [("fr", "")])
        five = [("xkb", n) for n in ("hu", "us", "de", "fr", "it")]
        self.assertEqual(len(km.layouts(five)), 4)
        self.assertEqual(km.layouts(None), [])
        self.assertEqual(km.layouts([("xkb",), "junk"]), [])

    def test_what_duo_wc_gets(self):
        self.assertEqual(km.request_args(DEFAULT), {"layouts": "hu,us", "variants": ""})
        self.assertEqual(
            km.request_args([("xkb", "de+nodeadkeys"), ("xkb", "hu")]),
            {"layouts": "de,hu", "variants": "nodeadkeys,"},
        )
        self.assertIsNone(km.request_args([]))

    def test_sent_only_when_duo_wc_differs(self):
        wc = {
            "keyboard": {
                "layouts": [{"layout": "hu", "variant": ""}, {"layout": "us", "variant": ""}],
                "active": 0,
            }
        }
        self.assertFalse(km.needs_push(DEFAULT, wc))
        self.assertTrue(
            km.needs_push(DEFAULT, {"keyboard": {"layouts": [{"layout": "us", "variant": ""}]}})
        )
        self.assertTrue(km.needs_push(DEFAULT, None))
        self.assertFalse(km.needs_push([], None))

    def test_a_picked_layout_is_added_once(self):
        self.assertEqual(km.with_added(DEFAULT, "de"), DEFAULT + [("xkb", "de")])
        self.assertEqual(
            km.with_added(DEFAULT, "de", "nodeadkeys"), DEFAULT + [("xkb", "de+nodeadkeys")]
        )
        self.assertIsNone(km.with_added(DEFAULT, "hu"))
        # US is there already: the word completion's layout.
        self.assertIsNone(km.with_added(DEFAULT, "us"))
        self.assertIsNone(km.with_added(DEFAULT, ""))
        self.assertIsNone(km.with_added(DEFAULT, "d e"))

    def test_names_in_their_own_language(self):
        self.assertEqual(km.name("hu"), "Magyar")
        self.assertEqual(km.name("de", "nodeadkeys"), "Deutsch (nodeadkeys)")
        self.assertEqual(km.name("xx"), "XX")


if __name__ == "__main__":
    unittest.main()
