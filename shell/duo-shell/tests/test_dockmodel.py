import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import dockmodel as dm  # noqa: E402

INDEX = {
    "foot.desktop": ("foot", "foot"),
    "org.gnome.TextEditor.desktop": (None, "gnome-text-editor"),
    "firefox-esr.desktop": ("firefox-esr", "firefox-esr"),
    "weird.desktop": (None, "weird-bin"),
}


class Match(unittest.TestCase):
    def test_by_id_class_or_executable(self):
        self.assertEqual(dm.match_app("foot", INDEX), "foot.desktop")
        self.assertEqual(
            dm.match_app("org.gnome.TextEditor", INDEX), "org.gnome.TextEditor.desktop"
        )
        self.assertEqual(dm.match_app("Firefox-ESR", INDEX), "firefox-esr.desktop")
        self.assertEqual(dm.match_app("weird-bin", INDEX), "weird.desktop")
        self.assertIsNone(dm.match_app("unknown", INDEX))
        self.assertIsNone(dm.match_app(None, INDEX))


class Signature(unittest.TestCase):
    def test_titles_and_order_of_one_apps_windows_do_not_count(self):
        a = [{"app_id": "foot", "title": "one"}, {"app_id": "foot", "title": "two"}]
        b = [{"app_id": "foot", "title": "three"}, {"app_id": "foot", "title": "one"}]
        pinned = ["foot.desktop"]
        self.assertEqual(
            dm.signature(dm.items(pinned, a, INDEX)), dm.signature(dm.items(pinned, b, INDEX))
        )

    def test_a_new_app_or_a_pin_counts(self):
        a = [{"app_id": "foot"}]
        b = [{"app_id": "foot"}, {"app_id": "org.gnome.TextEditor"}]
        self.assertNotEqual(
            dm.signature(dm.items([], a, INDEX)), dm.signature(dm.items([], b, INDEX))
        )
        self.assertNotEqual(
            dm.signature(dm.items([], a, INDEX)), dm.signature(dm.items(["foot.desktop"], a, INDEX))
        )


class Items(unittest.TestCase):
    def test_pinned_then_running(self):
        windows = [
            {"app_id": "foot"},
            {"app_id": "unknown"},
            {"app_id": "foot"},
            {"app_id": "org.gnome.TextEditor"},
        ]
        items = dm.items(["foot.desktop", "firefox-esr.desktop"], windows, INDEX)
        self.assertEqual(
            [(i.key(), i.pinned, i.running) for i in items],
            [
                ("foot.desktop", True, True),
                ("firefox-esr.desktop", True, False),
                # Running, newest window first, each app once.
                ("org.gnome.TextEditor.desktop", False, True),
                ("app:unknown", False, True),
            ],
        )
        self.assertEqual(items[0].app_id, "foot")

    def test_nothing_running(self):
        items = dm.items(["foot.desktop"], None, INDEX)
        self.assertEqual([(i.desktop_id, i.running) for i in items], [("foot.desktop", False)])


class Pinned(unittest.TestCase):
    def test_defaults_and_toggle(self):
        available = {"foot.desktop", "org.gnome.TextEditor.desktop", "x.desktop"}
        self.assertEqual(
            dm.default_pinned(available), ["foot.desktop", "org.gnome.TextEditor.desktop"]
        )
        self.assertEqual(dm.toggle_pin(["a", "b"], "a"), ["b"])
        self.assertEqual(dm.toggle_pin(["a"], "b"), ["a", "b"])

    def test_saved_list_survives_and_drops_removed_apps(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "dock.json")
            available = {"foot.desktop", "x.desktop"}
            self.assertEqual(dm.load_pinned(path, available), ["foot.desktop"])
            dm.save_pinned(path, ["x.desktop", "gone.desktop"])
            self.assertEqual(dm.load_pinned(path, available), ["x.desktop"])
            # An empty saved list stays empty (the user unpinned everything).
            dm.save_pinned(path, [])
            self.assertEqual(dm.load_pinned(path, available), [])


if __name__ == "__main__":
    unittest.main()


class DesktopTaskbarTest(unittest.TestCase):
    def test_a_taskbar_stands_on_a_lit_external_display(self):
        st = {"pose": "single DSI-1", "lit": ["DSI-1", "DP-1"], "overview": True}
        self.assertEqual(dm.wanted(st, "DP-1"), "taskbar")
        self.assertEqual(dm.wanted(st, "DSI-1"), "overview")
        self.assertIsNone(dm.wanted({"lit": ["DSI-1"]}, "DP-1"))
        self.assertIsNone(dm.wanted({"lit": ["DP-1"], "locked": True}, "DP-1"))
        self.assertIsNone(dm.wanted({"lit": ["DP-1"], "fullscreen": ["DP-1"]}, "DP-1"))

    def test_the_taskbar_lists_its_display_s_windows(self):
        windows = [
            {"app_id": "foot", "placement": {"output": "DP-1"}},
            {"app_id": "org.gnome.TextEditor", "placement": {"output": "DSI-2"}},
            {"app_id": "firefox-esr", "placement": "span"},
        ]
        self.assertEqual([w["app_id"] for w in dm.windows_on(windows, "DP-1")], ["foot"])
        self.assertEqual(len(dm.windows_on(windows, "DSI-1")), 3)
        self.assertEqual(dm.windows_on(None, "DP-1"), [])
        self.assertTrue(dm.is_external("HDMI-A-1") and not dm.is_external("DSI-2"))

    def test_laptop_mode_has_a_taskbar_on_its_desk_and_no_dock_on_its_keys(self):
        laptop = {"on": True, "desk": "DSI-1", "keys": "DSI-2"}
        st = {"pose": "dual", "lit": ["DSI-1", "DSI-2"], "laptop": laptop, "home": ["DSI-2"]}
        self.assertEqual(dm.wanted(st, "DSI-1"), "taskbar")
        self.assertIsNone(dm.wanted(st, "DSI-2"))
        self.assertFalse(dm.may_peek(st, "DSI-2"))
        self.assertTrue(dm.may_peek(st, "DSI-1"))
        self.assertTrue(
            dm.is_desk(st, "DSI-1") and dm.is_desk(st, "DP-1") and not dm.is_desk(st, "DSI-2")
        )
        # Not over a fullscreen window, not while locked.
        self.assertIsNone(dm.wanted(dict(st, fullscreen=["DSI-1"]), "DSI-1"))
        self.assertIsNone(dm.wanted(dict(st, locked=True), "DSI-1"))
        windows = [
            {"app_id": "foot", "placement": {"output": "DSI-1"}},
            {"app_id": "mpv", "placement": {"output": "DP-1"}},
        ]
        self.assertEqual([w["app_id"] for w in dm.windows_on(windows, "DSI-1", st)], ["foot"])
        # Off: the panels are panels again.
        off = dict(st, laptop={"on": False, "desk": None, "keys": None})
        self.assertIsNone(dm.wanted(off, "DSI-1"))
        self.assertEqual(dm.wanted(off, "DSI-2"), "home")
        self.assertEqual(len(dm.windows_on(windows, "DSI-1", off)), 2)


class DockShowsTest(unittest.TestCase):
    def test_no_standing_taskbar_in_span(self):
        st = {"pose": "span", "lit": ["DSI-1", "DSI-2"], "overview": False}
        self.assertIsNone(dm.wanted(st, "DSI-1"))
        self.assertTrue(dm.may_peek(st, "DSI-1"))

    def test_with_the_overview_on_lit_panels(self):
        st = {"pose": "dual", "lit": ["DSI-2"], "overview": True}
        self.assertEqual(dm.wanted(st, "DSI-2"), "overview")
        self.assertIsNone(dm.wanted(st, "DSI-1"))

    def test_never_over_a_fullscreen_window(self):
        st = {"pose": "span", "lit": ["DSI-1", "DSI-2"], "fullscreen": ["DSI-1", "DSI-2"]}
        self.assertFalse(dm.may_peek(st, "DSI-1"))
        st = {"pose": "dual", "lit": ["DSI-1", "DSI-2"], "fullscreen": ["DSI-2"]}
        self.assertTrue(dm.may_peek(st, "DSI-1"))
        self.assertFalse(dm.may_peek(st, "DSI-2"))
        self.assertFalse(dm.may_peek({"lit": []}, "DSI-1"))
        self.assertFalse(dm.may_peek(None, "DSI-1"))
