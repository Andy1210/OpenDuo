"""The torch's pure parts (duoshell/torchmodel.py)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duoshell import torchmodel  # noqa: E402


def led(root, name, brightness, maximum=255):
    d = os.path.join(root, name)
    os.makedirs(d)
    for attr, v in (("brightness", brightness), ("max_brightness", maximum)):
        with open(os.path.join(d, attr), "w") as f:
            f.write("%s\n" % v)
    return d


class TestFind(unittest.TestCase):
    def test_flash_led(self):
        with tempfile.TemporaryDirectory() as d:
            led(d, "input3::capslock", 0, 1)
            want = led(d, "white:flash", 0)
            self.assertEqual(torchmodel.find(d), want)

    def test_torch_before_flash(self):
        with tempfile.TemporaryDirectory() as d:
            led(d, "white:flash", 0)
            want = led(d, "yellow:torch", 0)
            self.assertEqual(torchmodel.find(d), want)

    def test_none(self):
        with tempfile.TemporaryDirectory() as d:
            led(d, "mmc0::", 0)
            self.assertIsNone(torchmodel.find(d))
            self.assertIsNone(torchmodel.find(os.path.join(d, "missing")))


class TestRead(unittest.TestCase):
    def test_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = led(d, "white:flash", 255)
            st = torchmodel.read(p, access=lambda path, mode: True)
            self.assertEqual(st, {"brightness": 255, "max": 255, "writable": True})
            self.assertTrue(torchmodel.is_on(st))

    def test_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(torchmodel.read(None))
            self.assertIsNone(torchmodel.read(os.path.join(d, "white:flash")))
            p = led(d, "white:flash", 0, 0)  # max 0: nothing to light
            self.assertIsNone(torchmodel.read(p))
            p = led(d, "blue:flash", "x")
            self.assertIsNone(torchmodel.read(p))

    def test_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = led(d, "white:flash", 0)
            self.assertTrue(torchmodel.write(p, 255))
            self.assertEqual(torchmodel.read(p)["brightness"], 255)
            self.assertFalse(torchmodel.write(os.path.join(d, "gone"), 0))


class TestTile(unittest.TestCase):
    def st(self, b, writable=True):
        return {"brightness": b, "max": 255, "writable": writable}

    def test_states(self):
        self.assertEqual(torchmodel.tile(None), (False, False, "No flashlight"))
        self.assertEqual(torchmodel.tile(self.st(0)), (True, False, "Off"))
        self.assertEqual(torchmodel.tile(self.st(255)), (True, True, "On"))
        # lit by someone else, and the session may not switch it: shown lit
        self.assertEqual(torchmodel.tile(self.st(40, False)), (False, True, "No access"))

    def test_level(self):
        self.assertEqual(torchmodel.level(self.st(0), True), 255)
        self.assertEqual(torchmodel.level(self.st(255), False), 0)


if __name__ == "__main__":
    unittest.main()
