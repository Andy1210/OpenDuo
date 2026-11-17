import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import bing, wallpaper  # noqa: E402

PANELS = {"DSI-1": (0.0, 0.0, 538.0, 757.0), "DSI-2": (564.0, 0.0, 538.0, 757.0)}


class Geometry(unittest.TestCase):
    def test_outputs_become_panels(self):
        outs = [
            {"name": "DSI-1", "x": 0, "y": 0, "w": 538, "h": 757},
            {"name": "DSI-2", "x": 564, "y": 0, "w": 538, "h": 757},
            {"junk": 1},
        ]
        self.assertEqual(wallpaper.panels_from_outputs(outs), PANELS)

    def test_a_layout_from_before_a_rotation_does_not_fit(self):
        # duo-lock: its lock surface turned on its side (757 x 538) needs the
        # layout read again; an upright one fits within a pixel.
        self.assertTrue(wallpaper.panel_fits(PANELS, "DSI-1", 538, 757))
        self.assertTrue(wallpaper.panel_fits(PANELS, "DSI-2", 538.6, 756.4))
        self.assertFalse(wallpaper.panel_fits(PANELS, "DSI-1", 757, 538))
        self.assertFalse(wallpaper.panel_fits(PANELS, "DSI-3", 538, 757))
        self.assertFalse(wallpaper.panel_fits({}, "DSI-1", 538, 757))

    def test_bounding_box_holds_the_gap(self):
        self.assertEqual(wallpaper.bounding_box(PANELS), (0.0, 0.0, 1102.0, 757.0))

    def test_same_mode_covers_one_panel_centred(self):
        # a 4096 x 2048 picture on a 538 x 757 panel: the height decides
        rect, s = wallpaper.source_rect(4096, 2048, PANELS["DSI-1"], PANELS["DSI-1"])
        self.assertAlmostEqual(s, 757 / 2048)
        self.assertAlmostEqual(rect[3], 2048)
        self.assertAlmostEqual(rect[2], 538 / s)
        self.assertAlmostEqual(rect[0], (4096 - rect[2]) / 2)

    def test_span_is_continuous_over_the_gap(self):
        canvas = wallpaper.canvas_for("span", "DSI-1", PANELS)
        left, s = wallpaper.source_rect(4000, 2000, PANELS["DSI-1"], canvas)
        right, _ = wallpaper.source_rect(4000, 2000, PANELS["DSI-2"], canvas)
        # the right panel starts one panel + one gap (26 logical px) after the left one
        self.assertAlmostEqual(right[0] - left[0], 564 / s)
        gap_px = right[0] - (left[0] + left[2])
        self.assertAlmostEqual(gap_px, 26 / s)
        # a 2:1 picture on a 1102 x 757 box: the height decides, the width is cropped evenly
        self.assertAlmostEqual(left[0] + (right[0] + right[2] - left[0]) / 2 + 0 * s, 2000, delta=1)

    def test_span_without_gap_has_no_seam(self):
        panels = {"DSI-1": (0.0, 0.0, 538.0, 757.0), "DSI-2": (538.0, 0.0, 538.0, 757.0)}
        canvas = wallpaper.canvas_for("span", "DSI-1", panels)
        left, _ = wallpaper.source_rect(3000, 3000, panels["DSI-1"], canvas)
        right, _ = wallpaper.source_rect(3000, 3000, panels["DSI-2"], canvas)
        self.assertAlmostEqual(left[0] + left[2], right[0])

    def test_span_with_one_panel_is_same(self):
        one = {"DSI-1": PANELS["DSI-1"]}
        self.assertEqual(wallpaper.canvas_for("span", "DSI-1", one), PANELS["DSI-1"])

    def test_draw_rect_shows_the_part_render_panel_cuts(self):
        panels = {"DSI-1": (0.0, 0.0, 538.0, 757.0), "DSI-2": (564.0, 0.0, 538.0, 757.0)}
        img = (3840, 2160)
        for name in panels:
            (sx, sy, sw, sh), _s = wallpaper.source_rect(
                img[0], img[1], panels[name], wallpaper.canvas_for("span", name, panels)
            )
            x, y, w, h = wallpaper.draw_rect(name, panels, "span", (538, 757), img)
            k = w / img[0]
            self.assertAlmostEqual(h / img[1], k)
            # The cut's corners land on the widget's corners.
            self.assertAlmostEqual(x + sx * k, 0.0)
            self.assertAlmostEqual(y + sy * k, 0.0)
            self.assertAlmostEqual(sw * k, 538.0)
            self.assertAlmostEqual(sh * k, 757.0)
        # Turned: stacked, each panel its own part of the tall canvas.
        stacked = {"DSI-1": (0.0, 0.0, 757.0, 538.0), "DSI-2": (0.0, 564.0, 757.0, 538.0)}
        x1, y1, w1, _ = wallpaper.draw_rect("DSI-1", stacked, "span", (757, 538), img)
        x2, y2, w2, _ = wallpaper.draw_rect("DSI-2", stacked, "span", (757, 538), img)
        self.assertAlmostEqual(w1, w2)
        self.assertAlmostEqual(y1 - y2, 564.0)

    def test_draw_rect_without_the_layout_covers_the_panel(self):
        # Not caught up (no layout, or the old size): centred cover.
        x, y, w, h = wallpaper.draw_rect("DSI-1", {}, "span", (757, 538), (3840, 2160))
        self.assertAlmostEqual(w / 3840, h / 2160)
        self.assertAlmostEqual(x, (757 - w) / 2)
        self.assertAlmostEqual(y, (538 - h) / 2)
        self.assertGreaterEqual(w, 757 - 1e-9)
        self.assertGreaterEqual(h, 538 - 1e-9)
        old = {"DSI-1": (0.0, 0.0, 538.0, 757.0)}
        self.assertEqual(
            wallpaper.draw_rect("DSI-1", old, "span", (757, 538), (3840, 2160)), (x, y, w, h)
        )

    def test_tall_picture_crops_height(self):
        rect, s = wallpaper.source_rect(1000, 4000, PANELS["DSI-1"], PANELS["DSI-1"])
        self.assertAlmostEqual(rect[2], 1000)
        self.assertAlmostEqual(rect[1], (4000 - rect[3]) / 2)


class Choice(unittest.TestCase):
    def test_parse_and_default(self):
        self.assertEqual(
            wallpaper.parse("# c\nmode = span # x\nbing=on\n"), {"mode": "span", "bing": "on"}
        )
        r = wallpaper.resolve({})
        self.assertEqual(
            r, {"path": None, "mode": "same", "dim": wallpaper.DEFAULT_DIM, "caption": None}
        )

    def test_file_must_exist(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            r = wallpaper.resolve({"source": "file", "file": f.name, "mode": "span", "dim": "10"})
            self.assertEqual((r["path"], r["mode"], r["dim"]), (f.name, "span", 10))
        self.assertIsNone(
            wallpaper.resolve({"source": "file", "file": "/nonexistent/x.jpg"})["path"]
        )

    def test_bad_values(self):
        r = wallpaper.resolve({"mode": "zoom", "dim": "lots"})
        self.assertEqual((r["mode"], r["dim"]), ("same", wallpaper.DEFAULT_DIM))
        self.assertEqual(wallpaper.resolve({"dim": "900"})["dim"], wallpaper.MAX_DIM)

    def test_bing_replaces_the_choice_only_while_on(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "20261107_en-US.jpg"), "wb").write(b"x")
            json.dump({"file": "20261107_en-US.jpg"}, open(os.path.join(d, "current.json"), "w"))
            self.assertEqual(
                wallpaper.resolve({"bing": "on"}, d)["path"], os.path.join(d, "20261107_en-US.jpg")
            )
            self.assertIsNone(wallpaper.resolve({"bing": "off"}, d)["path"])

    def test_current_json_cannot_point_elsewhere(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("../x.jpg", "/etc/passwd", ".hidden", 5, None):
                json.dump({"file": name}, open(os.path.join(d, "current.json"), "w"))
                self.assertEqual(wallpaper.bing_current(d), (None, {}))

    def test_pixbuf_conversion(self):
        try:
            import gi

            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
        except (ImportError, ValueError):
            self.skipTest("no GdkPixbuf")
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 3, 2)
        pb.fill(0x11223300)
        w, h, data = wallpaper.pixbuf_to_bgrx(pb)
        self.assertEqual((w, h, len(data)), (3, 2, 24))
        self.assertEqual(data[:4], bytes([0x33, 0x22, 0x11, 0xFF]))
        self.assertEqual(data[-4:], bytes([0x33, 0x22, 0x11, 0xFF]))


SAMPLE = json.dumps(
    {
        "images": [
            {
                "startdate": "20261107",
                "fullstartdate": "202611062300",
                "urlbase": "/th?id=OHR.Example_EN-US1234567890",
                "url": "/th?id=OHR.Example_EN-US1234567890_1920x1080.jpg&rf=x&pid=hp",
                "copyright": "A place (© Someone)",
                "copyrightlink": "https://www.bing.com/search?q=x",
                "title": "Example\x07 title",
            }
        ]
    }
)


class Bing(unittest.TestCase):
    def test_parse(self):
        info = bing.parse_response(SAMPLE)
        self.assertEqual(info["date"], "20261107")
        self.assertEqual(info["urlbase"], "/th?id=OHR.Example_EN-US1234567890")
        self.assertEqual(info["title"], "Example title")
        self.assertEqual(info["copyright"], "A place (© Someone)")
        self.assertEqual(
            bing.image_urls(info["urlbase"])[0],
            "https://www.bing.com/th?id=OHR.Example_EN-US1234567890_UHD.jpg",
        )

    def test_parse_refuses_the_odd(self):
        for text in ("", "not json", "{}", '{"images": []}', '{"images": [{"startdate": "x"}]}'):
            self.assertIsNone(bing.parse_response(text))
        evil = json.loads(SAMPLE)
        evil["images"][0]["urlbase"] = "https://evil.example/x"
        self.assertIsNone(bing.parse_response(json.dumps(evil)))
        evil["images"][0]["urlbase"] = "/th?id=OHR.x/../../etc"
        self.assertIsNone(bing.parse_response(json.dumps(evil)))
        evil = json.loads(SAMPLE)
        evil["images"][0]["startdate"] = "../../1"
        self.assertIsNone(bing.parse_response(json.dumps(evil)))
        evil = json.loads(SAMPLE)
        evil["images"][0]["copyrightlink"] = "javascript:x"
        self.assertEqual(bing.parse_response(json.dumps(evil))["link"], "")

    def test_request_url(self):
        self.assertEqual(
            bing.request_url("hu-HU"),
            "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=hu-HU",
        )

    def test_markets(self):
        self.assertTrue(bing.valid_market("en-US"))
        for bad in ("en", "en_US", "EN-us", "en-US&x=1", None):
            self.assertFalse(bing.valid_market(bad))
        self.assertEqual(bing.market_from_locale("hu_HU.UTF-8"), "hu-HU")
        self.assertEqual(bing.market_from_locale("de_AT.UTF-8"), "de-DE")
        self.assertEqual(bing.market_from_locale("C"), "en-US")
        self.assertEqual(bing.market_from_locale(""), "en-US")

    def test_jpeg_check(self):
        good = b"\xff\xd8\xff" + b"0" * 40000 + b"\xff\xd9"
        self.assertTrue(bing.is_jpeg(good))
        self.assertFalse(bing.is_jpeg(good[:-2]))
        self.assertFalse(bing.is_jpeg(b"<html>" + b"0" * 40000))
        self.assertFalse(bing.is_jpeg(b"\xff\xd8\xff\xff\xd9"))

    def test_retention_keeps_the_newest_and_only_ours(self):
        names = ["2026110%d_en-US.jpg" % i for i in range(1, 9)] + [
            "current.json",
            "notes.txt",
            "mine.jpg",
        ]
        doomed = bing.to_delete(names, keep=5)
        self.assertEqual(
            sorted(doomed), ["20261101_en-US.jpg", "20261102_en-US.jpg", "20261103_en-US.jpg"]
        )
        self.assertEqual(
            sorted(bing.to_delete(names, keep=5, current="20261101_en-US.jpg")),
            ["20261102_en-US.jpg", "20261103_en-US.jpg"],
        )
        self.assertEqual(bing.to_delete(names[:3], keep=5), [])

    def test_fresh(self):
        self.assertTrue(bing.fresh({"market": "en-US", "fetched": 1000}, "en-US", 1000 + 3600))
        self.assertFalse(bing.fresh({"market": "en-US", "fetched": 1000}, "hu-HU", 1100))
        self.assertFalse(bing.fresh({"market": "en-US", "fetched": 1000}, "en-US", 1000 + 7 * 3600))
        self.assertFalse(bing.fresh({}, "en-US", 5))
        self.assertFalse(bing.fresh(None, "en-US", 5))


class BingCaption(unittest.TestCase):
    def test_caption_from_current_json(self):
        from duoshell import wallpaper as wp

        meta = {
            "title": "Info",
            "copyright": "A bison (© Someone)",
            "link": "https://www.bing.com/search?q=bison",
        }
        self.assertEqual(
            wp.bing_caption(meta),
            {
                "title": "",
                "text": "A bison (© Someone)",
                "link": "https://www.bing.com/search?q=bison",
            },
        )
        meta = {"title": "Grasslands", "copyright": "A bison", "link": "http://x"}
        self.assertEqual(
            wp.bing_caption(meta), {"title": "Grasslands", "text": "A bison", "link": ""}
        )
        self.assertIsNone(wp.bing_caption({"title": "Info"}))
        self.assertIsNone(wp.bing_caption(None))

    def test_resolve_names_the_caption_only_for_bing(self):
        import json
        import tempfile

        from duoshell import wallpaper as wp

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "20261107_en-US.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
            with open(os.path.join(d, "current.json"), "w") as f:
                json.dump(
                    {
                        "file": "20261107_en-US.jpg",
                        "copyright": "A bison",
                        "link": "https://example.org/",
                    },
                    f,
                )
            got = wp.resolve({"bing": "on"}, d)
            self.assertEqual(got["caption"]["text"], "A bison")
            self.assertIsNone(wp.resolve({"bing": "off"}, d)["caption"])


if __name__ == "__main__":
    unittest.main()
