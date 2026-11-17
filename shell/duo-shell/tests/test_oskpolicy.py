import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell.oskpolicy import History, button_hides, button_lit  # noqa: E402


class Lit(unittest.TestCase):
    def test_lit_on_its_panel_only(self):
        self.assertTrue(button_lit(True, False, "DSI-1", "DSI-1"))
        self.assertFalse(button_lit(True, False, "DSI-1", "DSI-2"))

    def test_hidden_is_not_lit(self):
        self.assertFalse(button_lit(False, False, "DSI-1", "DSI-1"))
        self.assertFalse(button_lit(None, False, None, "DSI-1"))

    def test_put_away_or_held_back_is_lit_nowhere(self):
        # squeekboard says visible (its start-up blink, or a tap beside the
        # field), duo-wc shows nothing: osk_away, no osk_output.
        self.assertFalse(button_lit(True, True, None, "DSI-1"))
        self.assertFalse(button_lit(True, True, None, "DSI-2"))

    def test_overview_open_lights_every_panel(self):
        self.assertTrue(button_lit(True, False, None, "DSI-1"))
        self.assertTrue(button_lit(True, False, None, "DSI-2"))


class Button(unittest.TestCase):
    def test_shown_here_hides(self):
        h = History()
        h.note(10.0, True, "DSI-1")
        self.assertTrue(button_hides(h, 20.0, "DSI-1"))
        self.assertFalse(button_hides(h, 20.0, "DSI-2"))

    def test_the_tap_moving_the_keyboard_here_does_not_count(self):
        # Shown on DSI-2; the tap on DSI-1's bar moved the focus, and the
        # keyboard with it, 0.1 s before the click: it was not shown here.
        h = History()
        h.note(10.0, True, "DSI-2")
        h.note(20.0, True, "DSI-1")
        self.assertFalse(button_hides(h, 20.1, "DSI-1"))

    def test_the_tap_bringing_it_up_does_not_count(self):
        # Hidden; the tap focused a terminal, which brought it up just now.
        h = History()
        h.note(10.0, False, None)
        h.note(20.05, True, "DSI-1")
        self.assertFalse(button_hides(h, 20.15, "DSI-1"))

    def test_nothing_known_shows(self):
        self.assertFalse(button_hides(History(), 5.0, "DSI-1"))

    def test_repeats_are_not_stored(self):
        h = History()
        h.note(1.0, True, "DSI-1")
        h.note(2.0, True, "DSI-1")
        self.assertEqual(len(h.items), 1)


if __name__ == "__main__":
    unittest.main()
