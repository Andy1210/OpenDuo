import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import apps  # noqa: E402


class Search(unittest.TestCase):
    FIELDS = ["Text Editor", "Edit text files", None, "gnome-text-editor", "Notepad", "write"]

    def test_every_word_must_match_somewhere(self):
        self.assertTrue(apps.matches("", self.FIELDS))
        self.assertTrue(apps.matches("text", self.FIELDS))
        self.assertTrue(apps.matches("  EDIT  note ", self.FIELDS))
        self.assertFalse(apps.matches("edit browser", self.FIELDS))

    def test_accents_and_case_fold(self):
        self.assertTrue(apps.matches("uberblick", ["Überblick"]))
        self.assertTrue(apps.matches("ÜBER", ["überblick"]))

    def test_names_that_start_with_the_query_come_first(self):
        self.assertEqual(apps.rank("te", "Text Editor"), 0)
        self.assertEqual(apps.rank("ed", "Text Editor"), 1)
        self.assertEqual(apps.rank("dit", "Text Editor"), 2)
        self.assertEqual(apps.rank("", "Anything"), 0)


class AndroidLabels(unittest.TestCase):
    def test_only_a_clash_gets_the_suffix(self):
        pairs = [
            ("org.gnome.Contacts.desktop", "Contacts"),
            ("waydroid.com.android.contacts.desktop", "Contacts"),
            ("waydroid.org.fdroid.fdroid.desktop", "F-Droid"),
            ("org.gnome.Calculator.desktop", "Calculator"),
        ]
        clashes = apps.android_clashes(pairs)
        self.assertEqual(clashes, {"contacts"})
        self.assertEqual(
            apps.label("waydroid.com.android.contacts.desktop", "Contacts", clashes),
            "Contacts (Android)",
        )
        self.assertEqual(apps.label("org.gnome.Contacts.desktop", "Contacts", clashes), "Contacts")
        self.assertEqual(
            apps.label("waydroid.org.fdroid.fdroid.desktop", "F-Droid", clashes), "F-Droid"
        )

    def test_case_and_accents_count_as_a_clash(self):
        self.assertEqual(
            apps.android_clashes([("a.desktop", "Café"), ("waydroid.x.desktop", "cafe")]), {"cafe"}
        )


if __name__ == "__main__":
    unittest.main()
