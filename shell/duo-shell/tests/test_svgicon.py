"""svgicon: symbolic icons from the icon theme drawn with plain cairo (the
lock screen, the glance strip). The path parser on made-up paths, the SVG
reader's refusals, and the theme's own icons when Adwaita is installed."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duoshell import svgicon  # noqa: E402


class FakeCtx:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name,) + a)


def svg(body, view="0 0 16 16"):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" width="16px" height="16px">%s</svg>'
        % (view, body)
    )


class PathParser(unittest.TestCase):
    def test_absolute_and_relative(self):
        ops = svgicon.parse_path("M 1 2 L 3 4 l 1 1 h 2 v -3 H 0 V 9 z")
        self.assertEqual(
            ops,
            [
                ("M", 1, 2),
                ("L", 3, 4),
                ("L", 4, 5),
                ("L", 6, 5),
                ("L", 6, 2),
                ("L", 0, 2),
                ("L", 0, 9),
                ("Z",),
            ],
        )

    def test_implicit_lines_after_move_and_close_returns(self):
        ops = svgicon.parse_path("m 1 1 2 0 0 2 z m 1 0 1 1")
        self.assertEqual(
            ops, [("M", 1, 1), ("L", 3, 1), ("L", 3, 3), ("Z",), ("M", 2, 1), ("L", 3, 2)]
        )

    def test_curves(self):
        ops = svgicon.parse_path("M0 0 c 1 0 2 1 2 2 s 1 2 2 2")
        self.assertEqual(ops[1], ("C", 1, 0, 2, 1, 2, 2))
        # S reflects the previous second control point (2, 1) around (2, 2)
        self.assertEqual(ops[2], ("C", 2, 3, 3, 4, 4, 4))
        q = svgicon.parse_path("M0 0 Q 3 3 6 0")[1]
        self.assertEqual(q[0], "C")
        self.assertAlmostEqual(q[1], 2)
        self.assertAlmostEqual(q[2], 2)
        self.assertEqual(q[5:], (6, 0))

    def test_numbers_without_spaces(self):
        ops = svgicon.parse_path("M10.5-2.25L.5.5")
        self.assertEqual(ops, [("M", 10.5, -2.25), ("L", 0.5, 0.5)])

    def test_refusals(self):
        for d in ("M 0 0 a 1 1 0 0 1 2 2", "0 0 L 1 1", "M 0", "M 0 0 X 1"):
            with self.assertRaises(svgicon.Unsupported, msg=d):
                svgicon.parse_path(d)


class Svg(unittest.TestCase):
    def test_reads_paths_and_skips_faded_ones(self):
        w, h, ops = svgicon.parse_svg(
            svg('<path d="M0 0 L 16 16"/><path opacity="0.35" d="M1 1 L 2 2"/>')
        )
        self.assertEqual((w, h), (16, 16))
        self.assertEqual(ops, [("M", 0, 0), ("L", 16, 16)])

    def test_viewbox_origin(self):
        _w, _h, ops = svgicon.parse_svg(svg('<path d="M 2 3 L 4 5"/>', view="2 3 16 16"))
        self.assertEqual(ops, [("M", 0, 0), ("L", 2, 2)])

    def test_unsupported_shapes(self):
        for body in (
            '<rect width="2" height="2"/>',
            '<g transform="scale(2)"><path d="M0 0"/></g>',
            "",
        ):
            with self.assertRaises(svgicon.Unsupported):
                svgicon.parse_svg(svg(body))

    def test_load_and_draw(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "actions"))
            with open(os.path.join(d, "actions", "x-symbolic.svg"), "w") as f:
                f.write(svg('<path d="M 0 0 L 16 0 L 16 8 z"/>'))
            with open(os.path.join(d, "actions", "bad-symbolic.svg"), "w") as f:
                f.write(svg('<circle r="3"/>'))
            icon = svgicon.load("x-symbolic", dirs=(d,))
            self.assertIsNone(svgicon.load("bad-symbolic", dirs=(d,)))
            self.assertIsNone(svgicon.load("missing-symbolic", dirs=(d,)))
            ctx = FakeCtx()
            svgicon.draw(ctx, icon, 100, 50, 32)  # 16 units -> 32 px, centred
            self.assertEqual(
                ctx.calls,
                [
                    ("new_path",),
                    ("move_to", 84, 34),
                    ("line_to", 116, 34),
                    ("line_to", 116, 50),
                    ("close_path",),
                    ("fill",),
                ],
            )

    def test_the_themes_icons(self):
        """The icons the lock and the glance strip use, from the installed
        Adwaita (skipped without it): all drawable here."""
        if not svgicon.find("call-start-symbolic"):
            self.skipTest("no Adwaita symbolic icons installed")
        for name in (
            "call-start-symbolic",
            "call-stop-symbolic",
            "edit-clear-symbolic",
            "audio-speakers-symbolic",
        ):
            icon = svgicon.load(name)
            self.assertIsNotNone(icon, name)
            self.assertEqual(icon[:2], (16, 16))


if __name__ == "__main__":
    unittest.main()
