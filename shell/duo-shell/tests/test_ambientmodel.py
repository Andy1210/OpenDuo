import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import ambientmodel as am  # noqa: E402

LIT = ["DSI-1", "DSI-2"]


class AmbientPlanTest(unittest.TestCase):
    def test_nothing_without_an_ambient_screen(self):
        self.assertIsNone(am.plan({"lit": LIT}, "DSI-1"))
        self.assertIsNone(am.plan({"ambient": "peek", "lit": []}, "DSI-1"))
        self.assertIsNone(am.plan(None, "DSI-1"))

    BOTH = dict(am.DEFAULTS, peek_panels="both", glance_panels="both")

    def test_the_right_panel_by_default(self):
        for kind in ("peek", "glance"):
            st = {"ambient": kind, "lit": LIT}
            self.assertIsNone(am.plan(st, "DSI-1"))
            self.assertIsNotNone(am.plan(st, "DSI-2"))

    def test_peek_is_on_the_outer_edges(self):
        st = {"ambient": "peek", "lit": LIT}
        self.assertEqual(am.plan(st, "DSI-1", self.BOTH)["edge"], "left")
        self.assertEqual(am.plan(st, "DSI-2", self.BOTH)["edge"], "right")
        # one line flush with the outer edge, like the glance (owner 2026-11-07)
        p = am.plan(st, "DSI-2")
        self.assertEqual((p["style"], p["inset"], p["width"], p["turn"]), ("line", -4.0, 34, 270))
        # the older column layout on request
        c = am.plan(st, "DSI-2", dict(am.DEFAULTS, peek_style="column"))
        self.assertTrue(c["notifications"])
        self.assertEqual((c["turn"], c["width"]), (0, 72))

    def test_glance_is_one_line_in_the_sliver_next_to_the_hinge(self):
        st = {"ambient": "glance", "lit": LIT}
        self.assertEqual(am.plan(st, "DSI-1", self.BOTH)["edge"], "right")
        p = am.plan(st, "DSI-2")
        self.assertEqual(p["edge"], "left")
        self.assertFalse(p["notifications"])
        # The stock spine: ~34..54 px from the hinge-side edge, top to bottom.
        self.assertEqual((p["inset"], p["width"], p["along"], p["turn"]), (-2.3, 10, 28, 270))
        self.assertEqual(am.plan(st, "DSI-2", dict(am.DEFAULTS, glance_turn="90"))["turn"], 90)
        self.assertEqual(am.plan(st, "DSI-2", dict(am.DEFAULTS, glance_inset="6"))["inset"], 6)

    def test_the_ruler_bands(self):
        st = {"ambient": "glance-ruler", "lit": LIT}
        self.assertIsNone(am.plan(st, "DSI-1"))
        p = am.plan(st, "DSI-2")
        self.assertEqual(
            (p["kind"], p["edge"], p["band_px"], p["bands"]), ("ruler", "left", 16, 12)
        )
        bands = am.ruler_bands(p)
        self.assertEqual(bands[0][:2], (1, "red"))
        self.assertEqual((bands[0][3], bands[0][4]), (0, 16))
        self.assertEqual((bands[11][0], bands[11][4]), (12, 192))
        self.assertEqual(len({b[1] for b in bands}), 12)
        self.assertEqual(am.glance_text("12:48", 88), "12:48   88%")
        self.assertEqual(am.glance_text("12:48", None), "12:48")
        # the date after the time, its shorter forms, then the time alone
        self.assertEqual(
            am.strip_lines("12:48", ["Sonntag, 8. November 2026", "8. November", ""]),
            ["12:48   Sonntag, 8. November 2026", "12:48   8. November", "12:48"],
        )
        self.assertEqual(am.strip_lines("12:48", []), ["12:48"])

    def test_a_turned_panel_keeps_the_strip_on_the_glass(self):
        st = {"ambient": "peek", "lit": LIT, "rotation": {"DSI-1": 90, "DSI-2": 270}}
        self.assertEqual(am.plan(st, "DSI-1", self.BOTH)["edge"], "top")  # its left edge is up
        self.assertEqual(am.plan(st, "DSI-2", self.BOTH)["edge"], "top")  # its right edge is up

    def test_the_owner_can_pick_panels_and_widths(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ambient.conf")
            with open(path, "w") as f:
                f.write(
                    "glance_panels = DSI-2\nglance_width = 30 # thin\npeek_width = nonsense\n"
                    "peek_panels = both\n"
                )
            conf = am.read_conf(path)
        st = {"ambient": "glance", "lit": LIT}
        self.assertIsNone(am.plan(st, "DSI-1", conf))
        self.assertEqual(am.plan(st, "DSI-2", conf)["width"], 30)
        # a nonsense peek_width falls back to 72 (the column layout uses it)
        self.assertEqual(
            am.plan({"ambient": "peek", "lit": LIT}, "DSI-1", dict(conf, peek_style="column"))[
                "width"
            ],
            72,
        )


class NotesTest(unittest.TestCase):
    class Note:
        def __init__(self, n):
            self.n = n

        def icon(self):
            return ("name", "icon%d" % self.n)

    def test_nothing_without_notifications_or_when_off(self):
        self.assertEqual(am.notes_marker("icons", []), ("", []))
        self.assertEqual(am.notes_marker("count", None), ("", []))
        self.assertEqual(am.notes_marker("off", [self.Note(1)]), ("", []))

    def test_count_and_icons(self):
        notes = [self.Note(i) for i in range(6)]
        self.assertEqual(am.notes_marker("count", notes), ("   • 6", []))
        text, icons = am.notes_marker("icons", notes)
        self.assertEqual(text, "")
        self.assertEqual(len(icons), 4)

    def test_modes_in_the_plan(self):
        glance = am.plan({"ambient": "glance", "lit": LIT}, "DSI-2")
        peek = am.plan({"ambient": "peek", "lit": LIT}, "DSI-2")
        self.assertEqual((glance["notes"], glance["battery"]), ("count", "beside"))
        self.assertEqual((peek["notes"], peek["battery"]), ("icons", "inside"))
        conf = dict(am.DEFAULTS, glance_notifications="Icons", peek_notifications="bogus")
        self.assertEqual(
            am.plan({"ambient": "glance", "lit": LIT}, "DSI-2", conf)["notes"], "icons"
        )
        self.assertEqual(am.plan({"ambient": "peek", "lit": LIT}, "DSI-2", conf)["notes"], "icons")


class CallLineTest(unittest.TestCase):
    """The strips while a call rings: who calls, then the time, fitted to
    the strip (a fake measure: 5 px per character)."""

    @staticmethod
    def measure(text):
        return len(text) * 5

    def test_the_whole_line_when_it_fits(self):
        self.assertEqual(
            am.call_line("Anna  Nagy ", "12:34", 700, self.measure), "Anna Nagy   12:34"
        )

    def test_the_time_goes_before_the_name_is_cut(self):
        self.assertEqual(
            am.call_line("Unknown caller", "12:34", 100, self.measure), "Unknown caller"
        )
        self.assertEqual(
            am.call_line("+49 170 1234567", "12:34", 100, self.measure), "+49 170 1234567"
        )
        self.assertEqual(
            am.call_line("+49 170 1234567", "12:34", 200, self.measure), "+49 170 1234567   12:34"
        )

    def test_a_name_too_long_alone_is_cut_short(self):
        line = am.call_line("Anna Kovacs-Nagy", "12:34", 60, self.measure)
        self.assertEqual(line, "Anna Kovacs\u2026")
        self.assertLessEqual(self.measure(line), 60)
        self.assertEqual(
            am.call_line("Anna Kovacs-Nagy", "", 60, self.measure), "Anna Kovacs\u2026"
        )

    def test_nothing_overflows_however_small(self):
        for room in (5, 10, 23, 40, 64, 99, 130):
            for name in ("Anna Kovacs-Nagy", "+49 170 1234567", "Unknown caller", "X"):
                line = am.call_line(name, "12:34", room, self.measure)
                self.assertTrue(line)
                if self.measure(line[:1] + am.ELLIPSIS) <= room:
                    self.assertLessEqual(self.measure(line), room, (room, name, line))

    def test_the_room_along_the_strip_at_both_rotations(self):
        # The Duo 2's panel at scale 2.5: 538 x 757 logical upright,
        # 757 x 538 turned; the strip runs along the hinge-side (or outer)
        # edge, the panel's long side either way.
        for rotation, (w, h) in (
            (0, (538, 757)),
            (90, (757, 538)),
            (180, (538, 757)),
            (270, (757, 538)),
        ):
            st = {"ambient": "glance", "lit": LIT, "rotation": {"DSI-2": rotation}}
            plan = am.plan(st, "DSI-2")
            self.assertEqual(am.strip_room(plan, w, h), 757 - 2 * plan["along"], rotation)
            peek = am.plan(dict(st, ambient="peek"), "DSI-2")
            self.assertEqual(am.strip_room(peek, w, h), 757 - 2 * peek["along"], rotation)
