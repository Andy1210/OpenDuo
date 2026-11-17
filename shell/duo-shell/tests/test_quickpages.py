import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from duoshell import quickpages as qp  # noqa: E402

SETTINGS = os.path.join(HERE, "..", "..", "duo-settings", "duosettings")
QUICKMENU = os.path.join(HERE, "..", "duoshell", "quickmenu.py")


def settings_tags():
    """The page names `duo-settings PAGE` takes: Window.open_tag matches a
    page's class name, lower case, with or without its "Page"."""
    tags = set()
    for name in os.listdir(SETTINGS):
        if name.endswith(".py"):
            with open(os.path.join(SETTINGS, name)) as f:
                for cls in re.findall(r"^class (\w+)Page\(Page\):", f.read(), re.M):
                    tags.add(cls.lower())
    return tags


def tile_keys():
    """The keys the quick menu gives its tiles (Tile(..., "key"))."""
    with open(QUICKMENU) as f:
        text = f.read()
    return set(re.findall(r"self\.\w+,\s*\"(\w+)\",?\s*\)", text))


class Pages(unittest.TestCase):
    def test_the_owners_list(self):
        self.assertEqual(qp.page_for("hotspot"), "hotspot")
        self.assertEqual(qp.page_for("wifi"), "wifi")
        self.assertEqual(qp.page_for("bluetooth"), "bluetooth")
        self.assertEqual(qp.page_for("data"), "sim")
        self.assertEqual(qp.page_for("location"), "privacy")
        self.assertEqual(qp.page_for("dnd"), "notifications")
        self.assertEqual(qp.page_for("sound"), "sound")
        self.assertEqual(qp.page_for("rotate"), "rotation")
        self.assertEqual(qp.page_for("keyboard"), "keyboard")
        self.assertEqual(qp.page_for("gap"), "display")
        self.assertEqual(qp.page_for("panels"), "rotation")
        self.assertEqual(qp.page_for("laptop"), "laptop")
        self.assertEqual(qp.page_for("display"), "externaldisplay")
        # no page of their own: a long press is a tap
        for tile in ("torch", "airplane", "windows", None, "nope"):
            self.assertIsNone(qp.page_for(tile), tile)

    @unittest.skipUnless(os.path.isdir(SETTINGS), "no shell/duo-settings beside")
    def test_every_page_exists_in_settings(self):
        tags = settings_tags()
        self.assertIn("hotspot", tags)  # the parse works
        for tile, page in qp.TILE_PAGES.items():
            self.assertIn(page, tags, "%s -> %s" % (tile, page))

    def test_every_key_is_a_tile(self):
        keys = tile_keys()
        self.assertIn("wifi", keys)
        for tile in qp.TILE_PAGES:
            self.assertIn(tile, keys)

    def test_commandline(self):
        self.assertEqual(qp.commandline("duo-settings", "wifi"), "duo-settings wifi")
        self.assertEqual(
            qp.commandline("/opt/my settings/duo-settings", "sim"),
            "'/opt/my settings/duo-settings' sim",
        )
        for bad in ("", None, "wifi; reboot", "../x", "Wifi", "wifi page"):
            self.assertIsNone(qp.commandline("duo-settings", bad), bad)
        self.assertIsNone(qp.commandline(None, "wifi"))
        for page in qp.TILE_PAGES.values():
            self.assertEqual(qp.commandline("duo-settings", page), "duo-settings " + page)

    def test_swallow_click(self):
        self.assertFalse(qp.swallow_click(None, 100.0))
        self.assertTrue(qp.swallow_click(100.0, 100.4))
        self.assertFalse(qp.swallow_click(100.0, 100.0 + qp.HOLD_SWALLOW_S))
        self.assertFalse(qp.swallow_click(100.0, 99.0))  # (a clock that went back)


if __name__ == "__main__":
    unittest.main()
