import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import volume  # noqa: E402


class Parse(unittest.TestCase):
    def test_wpctl_output(self):
        self.assertEqual(volume.parse_get_volume("Volume: 0.30\n"), (0.30, False))
        self.assertEqual(volume.parse_get_volume("Volume: 0.45 [MUTED]\n"), (0.45, True))
        self.assertIsNone(volume.parse_get_volume(""))
        self.assertIsNone(volume.parse_get_volume("Volume: nan\n"))
        self.assertIsNone(volume.parse_get_volume("Object not found\n"))


class Step(unittest.TestCase):
    def test_up_and_down_on_the_grid(self):
        self.assertEqual(volume.step(0.30, False, "up"), (0.35, False))
        self.assertEqual(volume.step(0.30, False, "down"), (0.25, False))
        self.assertEqual(volume.step(0.27, False, "up"), (0.30, False))
        self.assertEqual(volume.step(0.27, False, "down"), (0.25, False))

    def test_never_above_full(self):
        self.assertEqual(volume.step(0.98, False, "up"), (1.0, False))
        self.assertEqual(volume.step(1.0, False, "up"), (1.0, False))
        self.assertEqual(volume.step(1.4, False, "up"), (1.0, False))

    def test_the_bottom_mutes_and_up_unmutes(self):
        self.assertEqual(volume.step(0.05, False, "down"), (0.0, True))
        self.assertEqual(volume.step(0.0, True, "down"), (0.0, True))
        self.assertEqual(volume.step(0.0, True, "up"), (0.05, False))
        self.assertEqual(volume.step(0.40, True, "up"), (0.45, False))

    def test_mute_toggles(self):
        self.assertEqual(volume.step(0.40, False, "mute"), (0.40, True))
        self.assertEqual(volume.step(0.40, True, "mute"), (0.40, False))
        self.assertEqual(volume.step(0.0, True, "mute"), (0.05, False))

    def test_a_held_key_walks_the_whole_range(self):
        v, m = 0.30, False
        for _ in range(volume.REPEAT_MAX):
            v, m = volume.step(v, m, "down")
        self.assertEqual((v, m), (0.0, True))
        for _ in range(volume.REPEAT_MAX):
            v, m = volume.step(v, m, "up")
        self.assertEqual((v, m), (1.0, False))


class Commands(unittest.TestCase):
    def test_only_what_changed(self):
        self.assertEqual(volume.commands((0.30, False), (0.30, False)), [])
        self.assertEqual(
            volume.commands((0.30, False), (0.35, False)),
            [["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "0.35"]],
        )
        self.assertEqual(
            volume.commands((0.05, False), (0.0, True)),
            [
                ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "0.00"],
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
            ],
        )

    def test_icons(self):
        self.assertEqual(volume.icon_name(0.4, True), "audio-volume-muted-symbolic")
        self.assertEqual(volume.icon_name(0.0, False), "audio-volume-muted-symbolic")
        self.assertEqual(volume.icon_name(0.2, False), "audio-volume-low-symbolic")
        self.assertEqual(volume.icon_name(0.5, False), "audio-volume-medium-symbolic")
        self.assertEqual(volume.icon_name(0.9, False), "audio-volume-high-symbolic")
        self.assertEqual(volume.percent(0.35), 35)


if __name__ == "__main__":
    unittest.main()
