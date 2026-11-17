import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from duosettings import privacy  # noqa: E402


def load_helper():
    path = os.path.join(os.path.dirname(HERE), "duo-settings-helper")
    loader = importlib.machinery.SourceFileLoader("duo_settings_helper_privacy", path)
    spec = importlib.util.spec_from_loader("duo_settings_helper_privacy", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load_helper()


class Store(unittest.TestCase):
    def test_broken_or_missing_is_on_nobody_asked(self):
        self.assertEqual(privacy.parse_store("{"), {"enabled": True, "apps": {}})
        self.assertEqual(privacy.parse_store("[]"), {"enabled": True, "apps": {}})
        self.assertEqual(privacy.load_store("/nonexistent/x.json"), {"enabled": True, "apps": {}})

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "location.json")
            privacy.save_store({"enabled": False, "apps": {"b": True, "a": False}}, path)
            self.assertEqual(
                privacy.load_store(path), {"enabled": False, "apps": {"a": False, "b": True}}
            )


class Denied(unittest.TestCase):
    def test_the_helpers_file_reads_back(self):
        text = helper.geoclue_apps_text(
            ["org.gnome.Weather", "org.gnome.clocks", "org.gnome.Weather"]
        )
        self.assertEqual(privacy.parse_denied(text), {"org.gnome.Weather", "org.gnome.clocks"})
        self.assertEqual(text.count("[org.gnome.Weather]"), 1)

    def test_only_allowed_false_counts(self):
        text = "[a]\nallowed=true\n[b]\nallowed=false\n[c]\nsystem=false\n"
        self.assertEqual(privacy.parse_denied(text), {"b"})

    def test_ids_are_checked(self):
        self.assertTrue(helper.valid_app_ids(["org.gnome.Weather", "firefox-esr"]))
        self.assertTrue(helper.valid_app_ids([]))
        # A trailing newline would break the section header out of its line.
        self.assertFalse(helper.valid_app_ids(["firefox\n"]))
        self.assertFalse(helper.valid_app_ids(["a]\nallowed=true"]))
        for bad in (["a]\n[b"], ["../x"], ["a b"], [""], ["-x"]):
            self.assertFalse(helper.valid_app_ids(bad), bad)


class Rows(unittest.TestCase):
    def test_native_asked_and_portal_apps(self):
        infos = [
            ("org.gnome.Weather.desktop", "Weather", "Shows the local weather"),
            ("org.gnome.TextEditor.desktop", "Text Editor", None),
            ("org.gnome.clocks.desktop", "Clocks", "World clocks"),
        ]
        native = privacy.location_apps(infos)
        self.assertEqual(native, [("org.gnome.clocks", "Clocks"), ("org.gnome.Weather", "Weather")])
        rows = privacy.rows(
            {"enabled": True, "apps": {"com.example.Map": False}},
            {"org.gnome.clocks"},
            native,
            {"net.example.Run": True},
        )
        self.assertEqual(
            rows,
            [
                ("org.gnome.clocks", "Clocks", False, "native"),
                ("com.example.Map", "com.example.Map", False, "asked"),
                ("net.example.Run", "net.example.Run", True, "portal"),
                ("org.gnome.Weather", "Weather", True, "native"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
