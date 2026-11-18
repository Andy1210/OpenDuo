import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duosettings import qr  # noqa: E402

UW = "/base/soc@0/cci@ac50000/i2c-bus@0/camera@10"
FRONT = "/base/soc@0/cci@ac50000/i2c-bus@1/camera@1a"


class Cameras(unittest.TestCase):
    def test_the_fixed_focus_ultra_wide_first(self):
        paths = [(FRONT, "front"), ("/x/unknown", "back"), (UW, "back"), ("/y", "external")]
        ranked = sorted(paths, key=lambda p: qr.camera_rank(*p))
        self.assertEqual(ranked[0][0], UW)
        self.assertEqual(ranked[-1][0], "/y")
        self.assertLess(qr.camera_rank(FRONT, "front"), qr.camera_rank("/x/unknown", "back"))

    def test_names(self):
        self.assertEqual(qr.camera_name(UW, "back", "imx481"), "Ultra-wide camera")
        self.assertEqual(qr.camera_name("/x", "back", "imx"), "Back camera")
        self.assertEqual(qr.camera_name("/x", "", "USB cam"), "USB cam")


class GreyRows(unittest.TestCase):
    def test_green_channel_rows_padded_to_four(self):
        # 3 x 2 RGB with a row stride of 12 (3 bytes of padding a row)
        px = bytes([1, 10, 2, 3, 11, 4, 5, 12, 6, 0, 0, 0, 7, 20, 8, 9, 21, 9, 9, 22, 9, 0, 0, 0])
        self.assertEqual(qr.gray_rows(px, 3, 2, 12, 3), bytes([10, 11, 12, 0, 20, 21, 22, 0]))

    def test_rgba(self):
        px = bytes([0, 50, 0, 255] * 4)
        self.assertEqual(qr.gray_rows(px, 4, 1, 16, 4), bytes([50] * 4))


if __name__ == "__main__":
    unittest.main()
