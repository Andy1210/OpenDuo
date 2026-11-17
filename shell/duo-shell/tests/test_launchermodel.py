import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import apps  # noqa: E402
from duoshell import launchermodel as lm


class Sections(unittest.TestCase):
    def test_letters_accents_and_others(self):
        self.assertEqual(lm.letter_of("firefox"), "F")
        self.assertEqual(lm.letter_of("Éditeur"), "E")
        self.assertEqual(lm.letter_of("  zoom"), "Z")
        self.assertEqual(lm.letter_of("2048"), "#")
        self.assertEqual(lm.letter_of(""), "#")
        self.assertEqual(lm.letter_of("Шахматы"), "#")

    def test_sections_in_index_order_sorted_inside(self):
        got = lm.sections(["Weather", "calculator", "Clocks", "2048", "Amberol", "Calendar"])
        self.assertEqual(
            got,
            [
                ("#", ["2048"]),
                ("A", ["Amberol"]),
                ("C", ["calculator", "Calendar", "Clocks"]),
                ("W", ["Weather"]),
            ],
        )

    def test_letter_under_the_finger(self):
        n = len(lm.INDEX)
        self.assertEqual(lm.letter_at(0, 270), "#")
        self.assertEqual(lm.letter_at(-40, 270), "#")
        self.assertEqual(lm.letter_at(269.9, 270), "Z")
        self.assertEqual(lm.letter_at(9999, 270), "Z")
        self.assertEqual(lm.letter_at(270 / n * 1.5, 270), "A")
        self.assertEqual(lm.letter_at(5, 0), "#")

    def test_a_missing_letter_jumps_to_the_next(self):
        present = ["A", "C", "F", "W"]
        self.assertEqual(lm.section_for("C", present), "C")
        self.assertEqual(lm.section_for("B", present), "C")
        self.assertEqual(lm.section_for("#", present), "A")
        self.assertEqual(lm.section_for("X", present), "W")
        self.assertIsNone(lm.section_for("A", []))


class View(unittest.TestCase):
    def test_read_write_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "launcher.conf")
            self.assertEqual(lm.read_view(path), "grid")
            lm.write_view("list", path)
            self.assertEqual(lm.read_view(path), "list")
            with open(path, "w") as f:
                f.write("view = sideways\n")
            self.assertEqual(lm.read_view(path), "grid")
            with self.assertRaises(ValueError):
                lm.write_view("tiles", path)


class ShortNames(unittest.TestCase):
    def test_the_shorter_name_under_an_icon(self):
        self.assertEqual(apps.short_name("Firefox ESR", "Firefox ESR Web Browser"), "Firefox ESR")
        self.assertEqual(apps.short_name("Text Editor", "Text Editor"), "Text Editor")
        self.assertEqual(apps.short_name(None, "Files"), "Files")
        self.assertEqual(apps.short_name("  ", "Files"), "Files")
        self.assertEqual(apps.short_name(None, None), "")
        self.assertEqual(
            apps.short_name("Chromium Web Browser", "Chromium Web Browser", "Web Browser"),
            "Chromium",
        )
        self.assertEqual(
            apps.short_name("Firefox ESR", "Firefox ESR Web Browser", "Web Browser"), "Firefox ESR"
        )
        # A name that is only the generic name stays.
        self.assertEqual(apps.short_name("Web Browser", None, "Web Browser"), "Web Browser")


class FrontOutputs(unittest.TestCase):
    def test_the_panels_a_window_came_to_the_front_on(self):
        msg = {"event": "window_front", "window": 11, "outputs": ["DSI-2"], "why": "opened"}
        self.assertEqual(lm.front_outputs(msg), ["DSI-2"])
        span = {"event": "window_front", "outputs": ["DSI-1", "DSI-2"]}
        self.assertEqual(lm.front_outputs(span), ["DSI-1", "DSI-2"])

    def test_anything_else_names_none(self):
        self.assertEqual(lm.front_outputs({"event": "window_front"}), [])
        self.assertEqual(lm.front_outputs({"outputs": "DSI-1"}), [])
        self.assertEqual(lm.front_outputs({"outputs": ["DSI-1", 3, ""]}), ["DSI-1"])
        self.assertEqual(lm.front_outputs(None), [])


if __name__ == "__main__":
    unittest.main()
