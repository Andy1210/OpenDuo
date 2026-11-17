"""The pen test tool's readout (touch/tools/pen-test.py): GTK gives the tilt
as -1..1 of -90..90 degrees, the readout says degrees, and only while the tip
touches (the library reports no tilt while hovering). Loaded with a stand-in
gi, so no GTK is needed.

    touch/tests/run.sh   (or: python3 -m unittest discover -s touch/tests)
"""

import importlib.util
import pathlib
import sys
import types
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools/pen-test.py"


def load_tool():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_: None
    repo = types.ModuleType("gi.repository")
    repo.Gdk = types.SimpleNamespace(
        ModifierType=types.SimpleNamespace(BUTTON2_MASK=1, BUTTON3_MASK=2)
    )
    repo.GLib = types.SimpleNamespace()
    repo.Gtk = types.SimpleNamespace(Grid=object, DrawingArea=object)
    gi.repository = repo
    saved = {k: sys.modules.get(k) for k in ("gi", "gi.repository")}
    sys.modules.update({"gi": gi, "gi.repository": repo})
    try:
        spec = importlib.util.spec_from_file_location("pen_test_tool", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class Tilt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_gtk_fraction_is_degrees(self):
        # 39.23 / 9.20 degrees (libinput on the phone, 2026-11-16) reach GTK
        # as 0.436 / 0.102; read as degrees they rounded to +0 / +0.
        x, y = self.tool.tilt_degrees({"xtilt": 39.23 / 90, "ytilt": 9.20 / 90})
        self.assertAlmostEqual(x, 39.23)
        self.assertAlmostEqual(y, 9.20)
        self.assertEqual(self.tool.tilt_degrees({"xtilt": -1.0, "ytilt": 1.0}), (-90.0, 90.0))

    def test_readout_while_touching(self):
        text = self.tool.tilt_text(
            {"xtilt": 39.23 / 90, "ytilt": -19.4 / 90, "pressure": 0.3}, True
        )
        self.assertEqual(text, "+39 / -19 deg")
        # A small tilt is not "-0".
        self.assertEqual(
            self.tool.tilt_text({"xtilt": -0.3 / 90, "ytilt": 0.0}, True), "+0 / +0 deg"
        )

    def test_no_tilt_while_hovering_or_without_axes(self):
        self.assertEqual(self.tool.tilt_text({"xtilt": 0.0, "ytilt": 0.0}, False), "-")
        self.assertEqual(self.tool.tilt_text({"pressure": 0.5}, True), "-")
        self.assertIsNone(self.tool.tilt_degrees({"xtilt": 0.2}))


if __name__ == "__main__":
    unittest.main()
