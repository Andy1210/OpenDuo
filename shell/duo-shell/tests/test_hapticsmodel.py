import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import hapticsmodel as m  # noqa: E402


class Notification(unittest.TestCase):
    def ev(self, category=None, urgency=m.URGENCY_NORMAL, hints=None, replaced=False, banners=True):
        return m.notification_event(category, urgency, hints, replaced, banners)

    def test_by_category(self):
        self.assertEqual(self.ev("im.received"), "message-new-instant")
        self.assertEqual(self.ev("email.arrived"), "message-new-email")
        self.assertEqual(self.ev("device.added"), "notification-new-generic")
        self.assertEqual(self.ev(None), "notification-new-generic")
        self.assertEqual(self.ev(None, urgency=m.URGENCY_CRITICAL), "alarm-clock-elapsed")

    def test_quiet_ones(self):
        self.assertIsNone(self.ev(urgency=m.URGENCY_LOW))
        self.assertIsNone(self.ev(replaced=True))
        self.assertIsNone(self.ev(hints={"suppress-sound": True}))
        self.assertIsNone(self.ev("call.incoming"))  # Calls rings itself

    def test_do_not_disturb_lets_critical_through(self):
        self.assertIsNone(self.ev("im.received", banners=False))
        self.assertEqual(self.ev(urgency=m.URGENCY_CRITICAL, banners=False), "alarm-clock-elapsed")


class Switch(unittest.TestCase):
    def test_toggle_remembers(self):
        self.assertEqual(m.toggled(0.8, 0.6), 0.0)
        self.assertEqual(m.toggled(0.0, 0.6), 0.6)
        self.assertEqual(m.toggled(0.0, 7), 1.0)
        self.assertEqual(m.toggled(0.0, "x"), m.DEFAULT_STRENGTH)
        self.assertTrue(m.is_on(0.1))
        self.assertFalse(m.is_on(0.0))

    def test_strength_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "duo-shell", "vibration.json")
            self.assertEqual(m.load_strength(path), m.DEFAULT_STRENGTH)
            m.save_strength(0.6, path)
            self.assertEqual(m.load_strength(path), 0.6)


class SoundModes(unittest.TestCase):
    def test_the_four_modes_are_two_settings(self):
        self.assertEqual(m.settings_for(m.SOUND_VIBRATION, 0.8), ("full", 0.8))
        self.assertEqual(m.settings_for(m.SOUND, 0.8), ("full", 0.0))
        self.assertEqual(m.settings_for(m.VIBRATION, 0.6), ("quiet", 0.6))
        self.assertEqual(m.settings_for(m.SILENT, 0.6), ("silent", 0.0))

    def test_round_trip(self):
        for mode in m.MODES:
            self.assertEqual(m.mode_of(*m.settings_for(mode, 1.0)), mode)

    def test_what_the_settings_say(self):
        # feedbackd's default (full, 1.0): sound and vibration
        self.assertEqual(m.mode_of("full", 1.0), m.SOUND_VIBRATION)
        # the old Vibration switch off in full: sound only
        self.assertEqual(m.mode_of("full", 0.0), m.SOUND)
        # quiet without strength vibrates with nothing: silent
        self.assertEqual(m.mode_of("quiet", 0.0), m.SILENT)
        self.assertEqual(m.mode_of("silent", 1.0), m.SILENT)
        self.assertEqual(m.mode_of("", 1.0), m.SOUND_VIBRATION)

    def test_the_vibration_switch_keeps_the_sound(self):
        self.assertEqual(m.with_vibration(m.SOUND_VIBRATION, False), m.SOUND)
        self.assertEqual(m.with_vibration(m.SOUND, True), m.SOUND_VIBRATION)
        self.assertEqual(m.with_vibration(m.VIBRATION, False), m.SILENT)
        self.assertEqual(m.with_vibration(m.SILENT, True), m.VIBRATION)

    def test_the_tile_goes_round(self):
        seen = [m.SOUND_VIBRATION]
        for _i in range(4):
            seen.append(m.next_mode(seen[-1]))
        self.assertEqual(
            seen, [m.SOUND_VIBRATION, m.SOUND, m.VIBRATION, m.SILENT, m.SOUND_VIBRATION]
        )
        self.assertEqual(m.next_mode("bogus"), m.SOUND_VIBRATION)

    def test_labels_and_icons_for_every_mode(self):
        for mode in m.MODES:
            self.assertTrue(m.MODE_LABELS[mode])
            self.assertTrue(m.MODE_ICONS[mode].endswith("-symbolic"))


if __name__ == "__main__":
    unittest.main()
