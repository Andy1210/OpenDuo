import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import locationmodel as m  # noqa: E402


class Decide(unittest.TestCase):
    def test_off_refuses_everyone(self):
        store = {"enabled": False, "apps": {"org.gnome.Maps": True}}
        self.assertEqual(m.decide(store, "org.gnome.Maps", m.EXACT), ("deny", m.NONE))
        self.assertEqual(m.max_level(store), m.NONE)

    def test_answers_and_asking(self):
        store = {"enabled": True, "apps": {"org.gnome.Maps": True, "evil": False}}
        self.assertEqual(m.decide(store, "org.gnome.Maps", m.CITY), ("allow", m.CITY))
        self.assertEqual(m.decide(store, "evil", m.CITY), ("deny", m.NONE))
        self.assertEqual(m.decide(store, "com.example.New", m.EXACT), ("ask", m.NONE))
        self.assertEqual(m.decide(store, "", m.EXACT), ("deny", m.NONE))
        self.assertEqual(m.max_level(store), m.EXACT)

    def test_answer_forget_switch(self):
        store = m.answer(m.DEFAULT, "a", True)
        self.assertEqual(store["apps"], {"a": True})
        self.assertEqual(m.forget(store, "a")["apps"], {})
        self.assertFalse(m.set_enabled(store, False)["enabled"])
        self.assertEqual(m.DEFAULT["apps"], {})  # untouched

    def test_portal_permissions(self):
        self.assertTrue(m.portal_granted(["EXACT", "1699999999"]))
        self.assertFalse(m.portal_granted(["NONE"]))
        self.assertIsNone(m.portal_granted([]))
        self.assertIsNone(m.portal_granted(None))


class File(unittest.TestCase):
    def test_round_trip_and_broken(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "location.json")
            self.assertEqual(m.load(path), {"enabled": True, "apps": {}})
            m.save({"enabled": False, "apps": {"x": True}}, path)
            self.assertEqual(m.load(path), {"enabled": False, "apps": {"x": True}})
            with open(path, "w") as f:
                f.write("not json")
            self.assertEqual(m.load(path), {"enabled": True, "apps": {}})


if __name__ == "__main__":
    unittest.main()
