import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import wc  # noqa: E402


class Lines(unittest.TestCase):
    def test_lines_are_split_and_parsed(self):
        lines = wc.LineBuffer()
        self.assertEqual(lines.feed(b'{"event":"edge_swipe","out'), [])
        self.assertEqual(
            lines.feed(b'put":"DSI-1"}\n\n{"a":1}\nnot json\n{"b"'),
            [{"event": "edge_swipe", "output": "DSI-1"}, {"a": 1}],
        )
        self.assertEqual(lines.feed(b":2}\n"), [{"b": 2}])

    def test_a_runaway_line_is_dropped(self):
        lines = wc.LineBuffer(limit=16)
        self.assertEqual(lines.feed(b"x" * 64), [])
        self.assertEqual(lines.feed(b'{"c":3}\n'), [{"c": 3}])


if __name__ == "__main__":
    unittest.main()
