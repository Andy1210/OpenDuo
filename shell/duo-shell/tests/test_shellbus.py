"""The system bus remote's decisions (duoshell/shellbus.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duoshell import shellbus  # noqa: E402

OUTPUTS = {"DSI-1", "DSI-2"}


class TestDecide(unittest.TestCase):
    def test_root_opens_a_panel(self):
        self.assertEqual(
            shellbus.decide("OpenQuickMenu", ("DSI-2",), 0, OUTPUTS), ("open", "DSI-2", None)
        )

    def test_root_closes(self):
        self.assertEqual(shellbus.decide("CloseQuickMenu", (), 0, OUTPUTS), ("close", None, None))

    def test_only_root(self):
        for uid in (1000, 1001, 65534):
            for method, args in (("OpenQuickMenu", ("DSI-1",)), ("CloseQuickMenu", ())):
                action, output, error = shellbus.decide(method, args, uid, OUTPUTS)
                self.assertIsNone(action)
                self.assertEqual(error[0], "org.freedesktop.DBus.Error.AccessDenied")

    def test_unknown_output(self):
        for args in (("HDMI-A-1",), ("",), ()):
            action, _o, error = shellbus.decide("OpenQuickMenu", args, 0, OUTPUTS)
            self.assertIsNone(action)
            self.assertEqual(error[0], "org.freedesktop.DBus.Error.InvalidArgs")

    def test_unknown_method(self):
        action, _o, error = shellbus.decide("Reboot", (), 0, OUTPUTS)
        self.assertIsNone(action)
        self.assertEqual(error[0], "org.freedesktop.DBus.Error.UnknownMethod")

    def test_introspection_matches(self):
        from gi.repository import Gio

        iface = Gio.DBusNodeInfo.new_for_xml(shellbus.XML).interfaces[0]
        self.assertEqual(iface.name, shellbus.IFACE)
        self.assertEqual(sorted(m.name for m in iface.methods), ["CloseQuickMenu", "OpenQuickMenu"])


if __name__ == "__main__":
    unittest.main()
