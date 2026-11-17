import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import overviewmodel as om  # noqa: E402

INDEX = {
    "foot.desktop": ("foot", "foot"),
    "org.gnome.TextEditor.desktop": (None, "gnome-text-editor"),
}
NAMES = {"foot.desktop": "Foot", "org.gnome.TextEditor.desktop": "Text Editor"}


def state(overview=True, dragged=None):
    return {
        "overview": overview,
        "windows": [
            {"id": 1, "app_id": "foot", "title": "alice@duo2: ~"},
            {"id": 3, "app_id": "org.gnome.TextEditor", "title": ""},
            {"id": 2, "app_id": "foot", "title": None},
        ],
        "overview_cards": [
            {
                "output": "DSI-1",
                "window": 3,
                "x": 16,
                "y": 170,
                "w": 245,
                "h": 30,
                "dragged": dragged == 3,
            },
            {
                "output": "DSI-1",
                "window": 1,
                "x": 277,
                "y": 170,
                "w": 245,
                "h": 30,
                "dragged": dragged == 1,
            },
            {
                "output": "DSI-2",
                "window": 2,
                "x": 107,
                "y": 120,
                "w": 323,
                "h": 30,
                "dragged": False,
            },
        ],
    }


class Labels(unittest.TestCase):
    def test_title_else_app_name(self):
        got = om.labels(state(), INDEX, NAMES)
        self.assertEqual(sorted(got), ["DSI-1", "DSI-2"])
        texts = {l.window: l.text for ls in got.values() for l in ls}
        self.assertEqual(texts, {1: "alice@duo2: ~", 3: "Text Editor", 2: "Foot"})
        first = got["DSI-1"][0]
        self.assertEqual((first.x, first.y, first.w, first.h), (16, 170, 245, 30))
        self.assertEqual(first.desktop_id, "org.gnome.TextEditor.desktop")

    def test_closed_overview_has_none(self):
        self.assertEqual(om.labels(state(overview=False), INDEX, NAMES), {})
        self.assertEqual(om.labels(None, INDEX), {})

    def test_a_carried_card_has_no_label(self):
        got = om.labels(state(dragged=3), INDEX, NAMES)
        self.assertEqual([l.window for l in got["DSI-1"]], [1])

    def test_closing_and_stuck_cards(self):
        st = state()
        st["overview_cards"][0]["closing"] = True
        st["overview_cards"][1]["closing"] = True
        st["overview_cards"][1]["stuck"] = True
        got = {l.window: l.state for ls in om.labels(st, INDEX, NAMES).values() for l in ls}
        self.assertEqual(got, {3: "closing", 1: "stuck", 2: None})
        self.assertNotEqual(
            om.signature(om.labels(st, INDEX, NAMES)),
            om.signature(om.labels(state(), INDEX, NAMES)),
        )

    def test_signature_changes_with_what_shows(self):
        a = om.signature(om.labels(state(), INDEX, NAMES))
        self.assertEqual(a, om.signature(om.labels(state(), INDEX, NAMES)))
        self.assertNotEqual(a, om.signature(om.labels(state(dragged=1), INDEX, NAMES)))


if __name__ == "__main__":
    unittest.main()
