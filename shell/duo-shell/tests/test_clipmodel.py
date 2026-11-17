import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import clipmodel as m  # noqa: E402


class Types(unittest.TestCase):
    def test_best_text_type(self):
        self.assertEqual(
            m.pick_text_type(["text/html", "text/plain", "text/plain;charset=utf-8"]),
            "text/plain;charset=utf-8",
        )
        self.assertEqual(m.pick_text_type(["UTF8_STRING", "STRING"]), "UTF8_STRING")
        self.assertEqual(
            m.pick_text_type(["text/plain;charset=iso-8859-1"]), "text/plain;charset=iso-8859-1"
        )
        self.assertIsNone(m.pick_text_type(["image/png", "text/uri-list"]))
        self.assertIsNone(m.pick_text_type(None))

    def test_decode_is_bounded_and_lenient(self):
        self.assertEqual(m.decode("Grüße 日本語".encode()), "Grüße 日本語")
        self.assertEqual(m.decode(b"\xff"), "�")
        self.assertEqual(len(m.decode(b"a" * (m.MAX_BYTES + 10))), m.MAX_BYTES)


class Callers(unittest.TestCase):
    def test_only_the_container(self):
        self.assertTrue(m.in_container("0::/lxc.payload.waydroid2/system.slice\n"))
        self.assertTrue(m.in_container("0::/lxc.payload.waydroid\n"))
        self.assertFalse(
            m.in_container("0::/user.slice/user-1000.slice/user@1000.service/app.slice/x.scope\n")
        )
        self.assertFalse(
            m.in_container(
                "0::/user.slice/lxc.payload.waydroidX-fake-name-in-a-scope\n".replace(
                    "lxc.payload.waydroidX-fake-name-in-a-scope", "app-lxc.payload.waydroid.scope"
                )
            )
        )
        self.assertFalse(m.in_container(""))
        # the user's own delegated subtree may name a cgroup like the container
        self.assertFalse(
            m.in_container(
                "0::/user.slice/user-1000.slice/user@1000.service/lxc.payload.waydroid2/x\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
