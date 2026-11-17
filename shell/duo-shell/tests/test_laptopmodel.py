import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import laptopmodel as lm  # noqa: E402

# A Hungarian board as duo-wc sends it (the symbols from the keymap).
KEYS = [
    {
        "id": "esc",
        "x": 8.5,
        "y": 8.5,
        "w": 44.0,
        "h": 39.0,
        "label": "Esc",
        "shift": "",
        "altgr": "",
        "kind": "key",
    },
    {
        "id": "ae10",
        "x": 500.0,
        "y": 52.0,
        "w": 44.0,
        "h": 39.0,
        "label": "ö",
        "shift": "Ö",
        "altgr": "˝",
        "kind": "key",
    },
    {
        "id": "ae02",
        "x": 57.0,
        "y": 52.0,
        "w": 44.0,
        "h": 39.0,
        "label": "2",
        "shift": '"',
        "altgr": "ˇ",
        "kind": "key",
    },
    {
        "id": "ad01",
        "x": 82.0,
        "y": 96.0,
        "w": 44.0,
        "h": 39.0,
        "label": "q",
        "shift": "Q",
        "altgr": "\\",
        "kind": "key",
    },
    {
        "id": "ab04",
        "x": 230.0,
        "y": 184.0,
        "w": 44.0,
        "h": 39.0,
        "label": "v",
        "shift": "V",
        "altgr": "@",
        "kind": "key",
    },
    {
        "id": "shift_l",
        "x": 8.5,
        "y": 184.0,
        "w": 106.0,
        "h": 39.0,
        "label": "Shift",
        "shift": "",
        "altgr": "",
        "kind": "mod",
    },
    {
        "id": "space",
        "x": 190.0,
        "y": 228.0,
        "w": 230.0,
        "h": 39.0,
        "label": "",
        "shift": "",
        "altgr": "",
        "kind": "key",
    },
    {
        "id": "layout",
        "x": 480.0,
        "y": 228.0,
        "w": 44.0,
        "h": 39.0,
        "label": "HU",
        "shift": "",
        "altgr": "",
        "kind": "layout",
    },
    {
        "id": "left",
        "x": 600.0,
        "y": 228.0,
        "w": 44.0,
        "h": 39.0,
        "label": "←",
        "shift": "",
        "altgr": "",
        "kind": "key",
    },
    {
        "id": "exit",
        "x": 700.0,
        "y": 8.5,
        "w": 44.0,
        "h": 39.0,
        "label": "⌨",
        "shift": "",
        "altgr": "",
        "kind": "exit",
    },
]

MENU = {
    "rect": [100.0, 120.0, 500.0, 100.0],
    "items": [
        {
            "layout": "hu",
            "variant": "",
            "configured": True,
            "active": True,
            "x": 106,
            "y": 126,
            "w": 120,
            "h": 40,
        },
        {
            "layout": "us",
            "variant": "",
            "configured": True,
            "active": False,
            "x": 230,
            "y": 126,
            "w": 120,
            "h": 40,
        },
        {
            "layout": "de",
            "variant": "nodeadkeys",
            "configured": False,
            "active": False,
            "x": 354,
            "y": 126,
            "w": 120,
            "h": 40,
        },
    ],
}


def state(on=True, shift=False, caps=False, altgr=False, menu=None):
    lt = {
        "on": on,
        "desk": "DSI-1" if on else None,
        "keys": "DSI-2" if on else None,
        "shift": shift,
        "caps": caps,
        "altgr": altgr,
        "latched": [],
        "menu": menu,
        "layout": {
            "w": 757.0,
            "h": 538.0,
            "group": "hu",
            "iso": True,
            "keys": KEYS,
            "touchpad": [181.0, 307.0, 393.0, 221.0],
        }
        if on
        else None,
    }
    return {"laptop": lt, "lit": ["DSI-1", "DSI-2"]}


class Panels(unittest.TestCase):
    def test_panels_only_while_on(self):
        self.assertEqual(lm.keys_panel(state()), "DSI-2")
        self.assertEqual(lm.desk_panel(state()), "DSI-1")
        self.assertIsNone(lm.keys_panel(state(on=False)))
        self.assertIsNone(lm.keys_panel({}))
        self.assertIsNone(lm.keys_panel(None))
        self.assertEqual(lm.labels(state(on=False)), [])


class Tile(unittest.TestCase):
    def test_the_quick_menu_tile(self):
        self.assertEqual(lm.tile(dict(state(), pose="dual")), (True, True, "On"))
        self.assertEqual(lm.tile(dict(state(on=False), pose="dual")), (True, False, "Off"))
        self.assertEqual(
            lm.tile(dict(state(on=False), pose="single DSI-1")), (False, False, "Needs both panels")
        )
        self.assertEqual(lm.tile({"pose": "dual"}), (False, False, "Not available"))
        self.assertEqual(lm.tile(None), (False, False, "Not available"))


class Labels(unittest.TestCase):
    def by_id(self, st):
        return {l.key_id: l for l in lm.labels(st)}

    def test_letters_follow_shift_and_caps(self):
        self.assertEqual(self.by_id(state())["ad01"].main, "q")
        self.assertEqual(self.by_id(state(shift=True))["ad01"].main, "Q")
        self.assertEqual(self.by_id(state(caps=True))["ad01"].main, "Q")
        # Shift with Caps Lock: lower case again, as xkb types it.
        self.assertEqual(self.by_id(state(shift=True, caps=True))["ad01"].main, "q")
        # The Hungarian letters on the digit row are letters too.
        self.assertEqual(self.by_id(state(shift=True))["ae10"].main, "Ö")
        self.assertEqual(self.by_id(state(caps=True))["ae10"].main, "Ö")

    def test_signs_show_both_the_shifted_one_big_with_shift(self):
        two = self.by_id(state())["ae02"]
        self.assertEqual((two.main, two.corner), ("2", '"'))
        two = self.by_id(state(shift=True))["ae02"]
        self.assertEqual((two.main, two.corner), ('"', "2"))
        # Caps Lock does not shift a digit.
        two = self.by_id(state(caps=True))["ae02"]
        self.assertEqual(two.main, "2")

    def test_the_altgr_symbol_small_and_big_with_altgr(self):
        v = self.by_id(state())["ab04"]
        self.assertEqual((v.main, v.third), ("v", "@"))
        v = self.by_id(state(altgr=True))["ab04"]
        self.assertEqual((v.main, v.third), ("@", ""))
        # A key with nothing on AltGr stays as it is.
        self.assertEqual(self.by_id(state(altgr=True))["esc"].main, "Esc")

    def test_a_tall_key_keeps_a_one_row_keys_text(self):
        tall = dict(KEYS[5], id="enter", label="Enter", kind="key", y=96.0, h=83.0)
        st = state()
        st["laptop"]["layout"]["keys"] = KEYS + [tall]
        by_id = {l.key_id: l for l in lm.labels(st)}
        self.assertAlmostEqual(
            by_id["enter"].h * by_id["enter"].size, by_id["shift_l"].h * by_id["shift_l"].size
        )

    def test_the_layout_key_shows_the_layout(self):
        key = self.by_id(state())["layout"]
        self.assertEqual((key.main, key.kind), ("HU", "layout"))

    def test_words_are_smaller_than_signs(self):
        labels = self.by_id(state())
        self.assertLess(labels["esc"].size, labels["left"].size)
        self.assertEqual(labels["shift_l"].main, "Shift")
        self.assertEqual(labels["exit"].corner, "×")
        # At the cap's place.
        self.assertEqual(
            (labels["ad01"].x, labels["ad01"].y, labels["ad01"].w, labels["ad01"].h),
            (82.0, 96.0, 44.0, 39.0),
        )

    def test_the_signature_changes_with_what_is_shown(self):
        self.assertEqual(lm.signature(state()), lm.signature(state()))
        self.assertNotEqual(lm.signature(state()), lm.signature(state(shift=True)))
        self.assertNotEqual(lm.signature(state()), lm.signature(state(menu=MENU)))
        self.assertIsNone(lm.signature(state(on=False)))


class Menu(unittest.TestCase):
    def test_the_list_names_the_layouts_in_their_own_language(self):
        names = [(l.main, l.kind) for l in lm.menu_labels(state(menu=MENU))]
        self.assertEqual(
            names,
            [
                ("Magyar", "menu-active"),
                ("English (US)", "menu"),
                ("+ Deutsch (nodeadkeys)", "menu-add"),
            ],
        )
        self.assertEqual(lm.menu_labels(state()), [])
        self.assertEqual(lm.menu_rect(state(menu=MENU)), (100.0, 120.0, 500.0, 100.0))

    def test_the_keys_under_the_list_have_no_label(self):
        ids = {l.key_id for l in lm.labels(state(menu=MENU))}
        # ab04 (y 184) is under the list (120..220); esc and the layout key not.
        self.assertNotIn("ab04", ids)
        self.assertIn("esc", ids)
        self.assertIn("layout", ids)


if __name__ == "__main__":
    unittest.main()
