import os
import tempfile
import unittest

from duoshell import homemodel as hm


def apps(n, prefix="a"):
    return ["%s%d.desktop" % (prefix, i) for i in range(n)]


class TestPages(unittest.TestCase):
    def test_capacity_first_page_has_the_widgets(self):
        self.assertLess(hm.capacity(0), hm.capacity(1))
        self.assertEqual(hm.capacity(1), hm.capacity(5))

    def test_normalize_spills_and_drops(self):
        many = apps(hm.capacity(0) + 3)
        pages = hm.normalize([many])
        self.assertEqual(len(pages[0]), hm.capacity(0))
        self.assertEqual(pages[1], many[hm.capacity(0) :])
        # Duplicates and empty pages go; one page always stays.
        self.assertEqual(hm.normalize([["x", "y", "x"], [], ["y", "z"]]), [["x", "y"], ["z"]])
        self.assertEqual(hm.normalize([[], []]), [[]])
        self.assertEqual(hm.normalize([]), [[]])

    def test_move_within_and_across_pages_and_panels(self):
        layout = {"DSI-1": [["a", "b", "c"]], "DSI-2": [["d"]]}
        self.assertEqual(hm.move(layout, "c", "DSI-1", 0, 0)["DSI-1"], [["c", "a", "b"]])
        moved = hm.move(layout, "a", "DSI-2", 0, 1)
        self.assertEqual(moved["DSI-1"], [["b", "c"]])
        self.assertEqual(moved["DSI-2"], [["d", "a"]])
        # To a new page.
        self.assertEqual(hm.move(layout, "b", "DSI-1", 1, 0)["DSI-1"], [["a", "c"], ["b"]])
        # The original is untouched.
        self.assertEqual(layout["DSI-1"], [["a", "b", "c"]])

    def test_moving_onto_a_full_page_pushes_one_on(self):
        full = apps(hm.capacity(0))
        layout = {"DSI-1": [full, ["z"]]}
        out = hm.move(layout, "z", "DSI-1", 0, 0)["DSI-1"]
        self.assertEqual(out[0][0], "z")
        self.assertEqual(len(out[0]), hm.capacity(0))
        self.assertEqual(out[1], [full[-1]])

    def test_add_remove_find(self):
        layout = {"DSI-1": [["a"]], "DSI-2": [[]]}
        added = hm.add(layout, "DSI-2", "b")
        self.assertEqual(added["DSI-2"], [["b"]])
        self.assertIs(hm.add(added, "DSI-2", "b"), added)
        self.assertEqual(hm.find(added, "b"), ("DSI-2", 0, 0))
        self.assertIsNone(hm.find(added, "zz"))
        self.assertEqual(hm.remove(added, "a")["DSI-1"], [[]])
        full = {"DSI-1": [apps(hm.capacity(0))]}
        self.assertEqual(hm.add(full, "DSI-1", "new")["DSI-1"][1], ["new"])

    def test_load_save_defaults(self):
        available = {"org.gnome.TextEditor.desktop", "foot.desktop", "kept.desktop"}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "home.json")
            layout = hm.load(path, ["DSI-1", "DSI-2"], available)
            self.assertEqual(layout["DSI-1"], [["org.gnome.TextEditor.desktop"]])
            self.assertEqual(layout["DSI-2"], [["foot.desktop"]])
            hm.save(path, {"DSI-1": [["kept.desktop", "gone.desktop"]]})
            layout = hm.load(path, ["DSI-1", "DSI-2"], available)
            self.assertEqual(layout["DSI-1"], [["kept.desktop"]])
            self.assertEqual(layout["DSI-2"], [["foot.desktop"]])
            with open(path, "w") as f:
                f.write("not json")
            self.assertEqual(
                hm.load(path, ["DSI-1"], available)["DSI-1"], [["org.gnome.TextEditor.desktop"]]
            )

    def test_slots_and_edges(self):
        self.assertEqual(hm.slot_at(10, 10, 400, 500, 4, 5), 0)
        self.assertEqual(hm.slot_at(399, 499, 400, 500, 4, 5), 19)
        self.assertEqual(hm.slot_at(150, 120, 400, 500, 4, 5), 5)
        self.assertEqual(hm.slot_at(-50, 900, 400, 500, 4, 5), 16)
        self.assertEqual(hm.edge_flip(5, 500), -1)
        self.assertEqual(hm.edge_flip(495, 500), 1)
        self.assertEqual(hm.edge_flip(250, 500), 0)

    def test_grid_shapes_hold_the_pages(self):
        for landscape in (False, True):
            for page in (0, 1, 4):
                cols, rows = hm.grid_shape(page, landscape)
                self.assertGreaterEqual(cols * rows, hm.capacity(page), (page, landscape))
        self.assertEqual(hm.grid_shape(1), (hm.COLUMNS, hm.ROWS))
        # on its side three rows at most (757 x 538 logical)
        self.assertLessEqual(hm.grid_shape(1, True)[1], 3)
        self.assertLessEqual(hm.grid_shape(0, True)[1], 3)
        # the 7 x 3 grid's last cell is past the page's 20: a drop there is "last"
        cols, rows = hm.grid_shape(1, True)
        slot = hm.slot_at(cols * 100 - 1, rows * 100 - 1, cols * 100, rows * 100, cols, rows)
        self.assertEqual(slot, cols * rows - 1)
        out = hm.move({"DSI-2": [["a"], ["b", "c"]]}, "a", "DSI-2", 1, slot)
        self.assertEqual(out["DSI-2"][-1][-1], "a")


class TestCards(unittest.TestCase):
    def test_the_battery_card_opens_settings_on_its_battery_page(self):
        self.assertEqual(hm.CARDS["battery"], ("org.duo2os.Settings.desktop", "battery"))

    def test_each_card_action_is_in_its_desktop_file(self):
        # Duo Settings' desktop file is in this repository: the action the
        # card asks for must be declared there, or the tap opens the main page.
        import configparser

        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "..", "duo-settings", "org.duo2os.Settings.desktop")
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str
        ini.read(path, encoding="utf-8")
        for desktop_id, action in hm.CARDS.values():
            if desktop_id != "org.duo2os.Settings.desktop" or action is None:
                continue
            self.assertIn(action, ini["Desktop Entry"]["Actions"].split(";"))
            self.assertEqual(ini["Desktop Action %s" % action]["Exec"], "duo-settings %s" % action)


if __name__ == "__main__":
    unittest.main()
