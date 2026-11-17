import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import deskmodel as dk  # noqa: E402
from duoshell import dockmodel  # noqa: E402

LAPTOP = {"laptop": {"on": True, "desk": "DSI-1", "keys": "DSI-2"}}


class App:
    def __init__(self, name, *more):
        self.name = name
        self.fields = [name] + list(more)


APPS = [
    App("Text Editor", "gedit", "notes"),
    App("Files", "nautilus"),
    App("Terminal", "foot"),
    App("Firefox ESR", "web browser"),
    App("Fotos", "image viewer"),
    App("Settings"),
]


class WhereTheDeskIs(unittest.TestCase):
    def test_laptop_desk_and_external_displays_are_desks(self):
        self.assertEqual(dk.home_mode(LAPTOP, "DSI-1"), "desk")
        self.assertEqual(dk.home_mode(LAPTOP, "DSI-2"), "home")  # the keyboard panel keeps its home
        self.assertEqual(dk.home_mode({}, "DSI-1"), "home")
        self.assertEqual(dk.home_mode({}, "HDMI-A-1"), "desk")  # a TV, no laptop mode needed
        self.assertEqual(dk.home_mode(LAPTOP, "DP-1"), "desk")

    def test_no_status_bar_on_a_desk(self):
        self.assertFalse(dk.shows_bar(LAPTOP, "DSI-1"))
        self.assertTrue(dk.shows_bar(LAPTOP, "DSI-2"))
        self.assertTrue(dk.shows_bar({}, "DSI-1"))
        self.assertFalse(dk.shows_bar({}, "DP-1"))

    def test_laptop_mode_off_gives_the_panel_back(self):
        off = {"laptop": {"on": False, "desk": None, "keys": None}}
        self.assertEqual(dk.home_mode(off, "DSI-1"), "home")
        self.assertTrue(dk.shows_bar(off, "DSI-1"))


class AppMenu(unittest.TestCase):
    def test_empty_search_lists_everything_alphabetically(self):
        self.assertEqual(
            [a.name for a in dk.menu_apps(APPS, "")],
            ["Files", "Firefox ESR", "Fotos", "Settings", "Terminal", "Text Editor"],
        )

    def test_names_starting_with_the_query_come_first(self):
        self.assertEqual([a.name for a in dk.menu_apps(APPS, "te")], ["Terminal", "Text Editor"])
        # Terminal matches "f" by its executable (foot): after the names
        # that start with it.
        self.assertEqual(
            [a.name for a in dk.menu_apps(APPS, "f")], ["Files", "Firefox ESR", "Fotos", "Terminal"]
        )

    def test_keywords_and_accents(self):
        self.assertEqual([a.name for a in dk.menu_apps(APPS, "web")], ["Firefox ESR"])
        self.assertEqual([a.name for a in dk.menu_apps(APPS, "FÓT")], ["Fotos"])
        self.assertEqual(dk.menu_apps(APPS, "nothing like it"), [])

    def test_enter_starts_the_top_hit(self):
        self.assertEqual(dk.top_hit(APPS, "term").name, "Terminal")
        self.assertEqual(dk.top_hit(APPS, "foot").name, "Terminal")  # by its executable
        self.assertEqual(dk.top_hit(APPS, "").name, "Files")
        self.assertIsNone(dk.top_hit(APPS, "zzz"))


def win(id, app_id, minimized=False):
    return {"id": id, "app_id": app_id, "minimized": minimized, "placement": {"output": "DSI-1"}}


class TaskbarTaps(unittest.TestCase):
    def test_no_window_launches(self):
        self.assertEqual(dk.tap_action([win(1, "foot")], "org.gnome.TextEditor"), ("launch", None))
        self.assertEqual(dk.tap_action([], "foot"), ("launch", None))

    def test_the_front_window_is_set_aside_by_a_second_tap(self):
        windows = [win(1, "org.gnome.TextEditor"), win(2, "foot")]
        self.assertEqual(dk.tap_action(windows, "foot"), ("minimize", 2))
        self.assertEqual(dk.tap_action(windows, "org.gnome.TextEditor"), ("activate", 1))

    def test_a_window_set_aside_comes_back(self):
        windows = [win(1, "org.gnome.TextEditor"), win(2, "foot", minimized=True)]
        self.assertEqual(dk.front_window(windows), 1)
        self.assertEqual(dk.tap_action(windows, "foot"), ("activate", 2))
        self.assertEqual(dk.tap_action(windows, "org.gnome.TextEditor"), ("minimize", 1))

    def test_an_app_with_two_windows_brings_its_frontmost(self):
        windows = [win(1, "foot"), win(2, "org.gnome.TextEditor"), win(3, "foot")]
        self.assertEqual(dk.tap_action(windows, "foot"), ("minimize", 3))
        windows = [win(1, "foot"), win(3, "foot"), win(2, "org.gnome.TextEditor")]
        self.assertEqual(dk.tap_action(windows, "foot"), ("activate", 3))

    def test_all_set_aside(self):
        windows = [win(1, "foot", True), win(2, "foot", True)]
        self.assertIsNone(dk.front_window(windows))
        self.assertEqual(dk.tap_action(windows, "foot"), ("activate", 2))

    def test_the_front_apps_button_is_lit_and_the_row_follows(self):
        index = {"foot.desktop": ("foot", "foot")}
        windows = [win(1, "org.gnome.TextEditor"), win(2, "foot")]
        self.assertTrue(dk.is_front_app(windows, "foot"))
        self.assertFalse(dk.is_front_app(windows, "org.gnome.TextEditor"))
        items = dockmodel.items(["foot.desktop"], windows, index)
        swapped = [win(2, "foot"), win(1, "org.gnome.TextEditor")]
        self.assertNotEqual(
            dk.taskbar_signature(items, windows),
            dk.taskbar_signature(dockmodel.items(["foot.desktop"], swapped, index), swapped),
        )


class DeskMenu(unittest.TestCase):
    def test_leave_laptop_mode_only_on_its_desk(self):
        self.assertEqual(
            [a for a, _ in dk.desk_menu(LAPTOP, "DSI-1")], ["settings", "wallpaper", "leave_laptop"]
        )
        self.assertEqual([a for a, _ in dk.desk_menu({}, "HDMI-A-1")], ["settings", "wallpaper"])
        self.assertEqual([a for a, _ in dk.desk_menu(LAPTOP, "DP-1")], ["settings", "wallpaper"])

    def test_tray_battery(self):
        self.assertEqual(dk.tray_battery((57, "Discharging")), "57%")
        self.assertEqual(dk.tray_battery(None), "")
        self.assertEqual(dk.tray_battery((None, None)), "")


if __name__ == "__main__":
    unittest.main()
