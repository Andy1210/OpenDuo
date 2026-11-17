import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import deskmodel, dockmodel  # noqa: E402
from duoshell import displaymodel as dm  # noqa: E402


def state(use="mirror", asking=False, **more):
    st = {
        "lit": ["DSI-1", "DSI-2", "DP-1"],
        "active_output": "DSI-2",
        "displays": {
            "on_connect": "ask",
            "mirror": "active",
            "connected": [
                {
                    "output": "DP-1",
                    "name": "TV",
                    "use": use,
                    "asking": asking,
                    "mirrors": ["DSI-2"] if use == "mirror" else [],
                }
            ],
        },
    }
    st.update(more)
    return st


class Uses(unittest.TestCase):
    def test_a_mirror_is_no_desk(self):
        self.assertTrue(dm.is_mirror(state("mirror"), "DP-1"))
        self.assertFalse(dockmodel.is_desk(state("mirror"), "DP-1"))
        self.assertEqual(deskmodel.home_mode(state("mirror"), "DP-1"), "home")
        self.assertIsNone(dockmodel.wanted(state("mirror"), "DP-1"))
        self.assertFalse(dockmodel.may_peek(state("mirror"), "DP-1"))

    def test_a_desk_display_is_a_desk(self):
        self.assertTrue(dockmodel.is_desk(state("desk"), "DP-1"))
        self.assertEqual(deskmodel.home_mode(state("desk"), "DP-1"), "desk")
        self.assertEqual(dockmodel.wanted(state("desk"), "DP-1"), "taskbar")

    def test_an_older_duo_wc_knows_only_desks(self):
        st = {"lit": ["DP-1"]}
        self.assertIsNone(dm.use_of(st, "DP-1"))
        self.assertTrue(dockmodel.is_desk(st, "DP-1"))
        self.assertIsNone(dm.tile(st))


class Asking(unittest.TestCase):
    def test_the_question_and_where(self):
        self.assertIsNone(dm.asking(state("mirror", asking=False)))
        c = dm.asking(state("mirror", asking=True))
        self.assertEqual(c["output"], "DP-1")
        self.assertEqual(dm.prompt_panel(state(asking=True)), "DSI-2")
        self.assertEqual(dm.prompt_panel(state(asking=True, active_output="DP-1")), "DSI-1")
        # Laptop mode: never on its keyboard panel.
        laptop = {"on": True, "desk": "DSI-2", "keys": "DSI-1"}
        self.assertEqual(
            dm.prompt_panel(state(asking=True, active_output="DSI-1", laptop=laptop)), "DSI-2"
        )
        self.assertIsNone(dm.prompt_panel({"lit": []}))

    def test_an_answer_and_remembering_it(self):
        self.assertEqual(
            dm.answer("DP-1", "desk", False), [("set_display", {"output": "DP-1", "mode": "desk"})]
        )
        self.assertEqual(
            dm.answer("DP-1", "mirror", True),
            [
                ("set_display", {"output": "DP-1", "mode": "mirror"}),
                ("set_external", {"on_connect": "mirror"}),
            ],
        )
        with self.assertRaises(ValueError):
            dm.answer("DP-1", "span", False)

    def test_the_signature_follows_the_question(self):
        self.assertIsNone(dm.signature(state(asking=False)))
        self.assertNotEqual(
            dm.signature(state(asking=True)),
            dm.signature(state(asking=True, active_output="DSI-1")),
        )


class Tile(unittest.TestCase):
    def test_only_while_a_display_is_connected(self):
        self.assertIsNone(dm.tile({"displays": {"connected": []}}))
        self.assertEqual(dm.tile(state("mirror")), (False, "Mirror"))
        self.assertEqual(dm.tile(state("desk")), (True, "Desktop"))

    def test_a_tap_switches(self):
        self.assertEqual(
            dm.toggle(state("mirror")), ("set_display", {"output": "DP-1", "mode": "desk"})
        )
        self.assertEqual(
            dm.toggle(state("desk")), ("set_display", {"output": "DP-1", "mode": "mirror"})
        )
        self.assertIsNone(dm.toggle({}))


if __name__ == "__main__":
    unittest.main()
