"""The ringtone (duosettings/ringtone.py), the voicemail number
(voicemail.py) and do not disturb's exceptions (dndprefs.py)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duosettings import dndprefs, ringtone, voicemail  # noqa: E402

FREEDESKTOP_INDEX = """[Sound Theme]
Name=Default
Directories=stereo

[stereo]
OutputProfile=stereo
"""


def sound_tree(root, themes):
    """themes: {name: (index text, [file names in stereo/])}"""
    for name, (index, files) in themes.items():
        d = os.path.join(root, "sounds", name, "stereo")
        os.makedirs(d)
        with open(os.path.join(root, "sounds", name, "index.theme"), "w") as f:
            f.write(index)
        for fn in files:
            open(os.path.join(d, fn), "w").close()


class Ringtone(unittest.TestCase):
    def test_choices(self):
        with tempfile.TemporaryDirectory() as d:
            sound_tree(
                d,
                {
                    "freedesktop": (
                        FREEDESKTOP_INDEX,
                        [
                            "phone-incoming-call.oga",
                            "bell.oga",
                            "alarm-clock-elapsed.oga",
                            "camera-shutter.oga",
                        ],
                    ),
                    "ocean": ("[Sound Theme]\nName=Ocean\n", ["phone-incoming-call.oga"]),
                    "broken": ("no group\n", []),
                },
            )
            os.makedirs(os.path.join(d, "sounds", "no-index"))
            out = ringtone.choices(
                dirs=[d], own="/home/x/.local/share/duo2/ringtones/own-ringtone.oga"
            )
            keys = [k for k, _l, _p in out]
            self.assertEqual(keys[0], "default")
            self.assertTrue(out[0][2].endswith("freedesktop/stereo/phone-incoming-call.oga"))
            self.assertIn("theme:ocean", keys)
            self.assertEqual(
                dict((k, l) for k, l, _p in out)["theme:ocean"], "Phone ringing (Ocean)"
            )
            self.assertIn("sound:freedesktop:bell", keys)
            self.assertIn("sound:freedesktop:alarm-clock-elapsed", keys)
            self.assertNotIn("sound:freedesktop:camera-shutter", keys)
            self.assertEqual(keys[-1], "own")

    def test_no_sound_theme(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                ringtone.choices(dirs=[d]), [("default", "Phone ringing (default)", None)]
            )

    def test_theme_file_and_switching_names(self):
        with tempfile.TemporaryDirectory() as d:
            name = ringtone.write_theme("/snd/bell.oga", "default", base=d)
            self.assertEqual(name, "duo2-ring-a")
            with open(os.path.join(ringtone.themes_dir(d), name + ".json")) as f:
                theme = json.load(f)
            # feedbackd's custom-theme form: the device theme with one entry replaced
            self.assertEqual(theme["name"], name)
            self.assertEqual(theme["parent-name"], "$device")
            self.assertEqual(
                theme["profiles"],
                [
                    {
                        "name": "full",
                        "feedbacks": [
                            {
                                "event-name": "phone-incoming-call",
                                "type": "Sound",
                                "file-name": "/snd/bell.oga",
                            }
                        ],
                    }
                ],
            )
            self.assertEqual(ringtone.current_path(name, base=d), "/snd/bell.oga")
            # the next change goes under the other name (feedbackd reloads on the key)
            name2 = ringtone.write_theme("/snd/x.oga", name, base=d)
            self.assertEqual(name2, "duo2-ring-b")
            ringtone.remove_themes(keep=name2, base=d)
            self.assertEqual(sorted(os.listdir(ringtone.themes_dir(d))), ["duo2-ring-b.json"])
            self.assertIsNone(ringtone.current_path("default", base=d))
            ringtone.remove_themes(base=d)
            self.assertEqual(os.listdir(ringtone.themes_dir(d)), [])

    def test_selected(self):
        opts = [("default", "D", "/a.oga"), ("sound:x:bell", "B", "/b.oga")]
        self.assertEqual(ringtone.selected(opts, None), 0)
        self.assertEqual(ringtone.selected(opts, "/b.oga"), 1)
        self.assertEqual(ringtone.selected(opts, "/gone.oga"), 0)

    def test_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "a.wav")
            with open(wav, "wb") as f:
                f.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
            ogg = os.path.join(d, "a.ogg")
            with open(ogg, "wb") as f:
                f.write(b"OggS" + b"\x00" * 24 + b"\x01vorbis" + b"\x00" * 10)
            opus = os.path.join(d, "a.opus")
            with open(opus, "wb") as f:
                f.write(b"OggS" + b"\x00" * 24 + b"OpusHead")
            mp3 = os.path.join(d, "a.mp3")
            with open(mp3, "wb") as f:
                f.write(b"ID3\x03\x00")
            self.assertFalse(ringtone.needs_conversion(wav))
            self.assertFalse(ringtone.needs_conversion(ogg))
            self.assertTrue(ringtone.needs_conversion(opus))
            self.assertTrue(ringtone.needs_conversion(mp3))
            self.assertTrue(ringtone.needs_conversion(os.path.join(d, "missing")))
        argv = ringtone.convert_argv("/in.mp3", "/out.oga")
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("libvorbis", argv)
        self.assertEqual(argv[argv.index("-t") + 1], str(ringtone.OWN_MAX_S))
        self.assertEqual(argv[-1], "/out.oga")

    def test_index_theme(self):
        self.assertEqual(ringtone.theme_label(FREEDESKTOP_INDEX, "x"), "Default")
        self.assertEqual(ringtone.theme_dirs(FREEDESKTOP_INDEX), ["stereo"])
        self.assertEqual(ringtone.theme_dirs("[Sound Theme]\nDirectories=a, b\n"), ["a", "b"])
        self.assertEqual(ringtone.theme_label("", "x"), "x")


class Voicemail(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(voicemail.clean("33 11"), "3311")
        self.assertEqual(voicemail.clean("+44 7700 900-311"), "+447700900311")
        self.assertEqual(voicemail.clean("*86#"), "*86#")
        for bad in ("", "abc", "12+3", "1" * 40):
            self.assertEqual(voicemail.clean(bad), "", bad)

    def test_suggestions(self):
        self.assertEqual([n for n, _w in voicemail.suggestions("26201")], ["3311", "4712"])
        self.assertEqual(voicemail.suggestions(" 26201 ")[0][0], "3311")
        self.assertEqual(voicemail.suggestions("21630"), [])
        self.assertEqual(voicemail.suggestions(""), [])

    def test_store_and_dial(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "voicemail.json")
            self.assertEqual(voicemail.load(path), "")
            voicemail.save("33 11", path)
            self.assertEqual(voicemail.load(path), "3311")
        self.assertEqual(voicemail.dial_argv("3311"), ["gnome-calls", "--dial", "3311"])
        self.assertEqual(voicemail.dial_argv(""), ["duo-settings", "sim"])


class DndPrefs(unittest.TestCase):
    def test_same_file_and_rules_as_the_shell(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "duo-shell"))
        from duoshell import dndmodel

        self.assertEqual(dndprefs.DEFAULTS, dndmodel.DEFAULTS)
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                self.assertEqual(dndprefs.config_path(), dndmodel.config_path())
                dndprefs.save({"repeat_callers": False, "contacts": True})
                self.assertEqual(dndmodel.load(), {"repeat_callers": False, "contacts": True})
            finally:
                del os.environ["XDG_CONFIG_HOME"]


if __name__ == "__main__":
    unittest.main()
